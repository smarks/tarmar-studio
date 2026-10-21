"""Views for the GM app — chat page and SSE streaming endpoints."""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from functools import partial
from typing import NamedTuple

import httpx
from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import ListView, TemplateView

from battle.access import Actor, is_dm
from battle.services import BARE_HANDED_KEY
from battle.views import arena_context
from characters.constants import too_long
from characters.models import Character
from common.breadcrumbs import BreadcrumbMixin, step, trail
from common.decorators import rate_limit
from common.helpers import (
    _json_error,
    _parse_json_body,
    column_length,
    database_integer,
    wants_json,
)
from common.mixins import ActivePageMixin
from common.pagination import query_without
from common.pickers import asked_for, picker_body_response
from common.services.ollama_client import HangUp, StreamDeadlinePassed
from reference.rendering import render_reference_markdown

from . import roster
from .attach import attach_refusal
from .battle_report import fight_verdict, filed_verdict
from .character_sheet import CharacterSheet, sheets_for
from .fight import FightError, fight_in_progress, start_fight
from .hand_records import latest_hand_edits, may_edit_by_hand
from .history_pages import history_page, resolved_threads_page
from .ledger import DraftLedger
from .luck import luck_panel
from .maps import MAP_AREAS, MapScope, detect_place, party_place
from .messages import (
    INTERRUPTED_BY_BUDGET,
    INTERRUPTED_BY_CONNECTION,
    PLAYER_ROLL_KEY,
    TYPED_KEY,
    as_typed,
    build_interrupted_turn_message,
    cut_short_notice,
    resumes_interrupted_turn,
    stored_roll_message,
)
from .models import (
    MODE_DESCRIPTIONS,
    MODE_LABELS,
    MODE_OPENINGS,
    MODE_PLACEHOLDERS,
    PLACEHOLDER_SESSION_NAME,
    CampaignState,
    GMRecordEdit,
    GMReviewRequest,
    GMSession,
    GMTurnRecord,
    SceneEntity,
    StoryThread,
    SupplyItem,
    name_from_history,
)
from .pickers import (
    SESSION_PICKER_SOURCES,
    attachable_picker,
    play_session_picker,
    session_picker,
    sessions_holding_character,
)
from .retrieval import (
    PROCEDURE_KIND,
    RECAP_KIND,
    ROLL_KIND,
    ChatMessage,
    is_hidden_message,
    is_played_entry,
    is_procedure_message,
    is_reader_only,
    is_roll_message,
)
from .services import (
    build_rules_lookup_message,
    build_state_request_message,
    default_mode_for_user,
    fold_needed,
    folded_for_model,
    get_gm_model,
    get_gm_options,
    get_system_message,
    ground_turn_in_battle,
    ground_turn_in_canon,
    load_rules,
    messages_for_model,
    mode_for_user,
    roll_dice,
    save_fold,
    write_fold,
    write_session_recap,
)
from .text_limits import GM_TEXT_LIMITS
from .transcript import render_transcript_text, transcript_filename, transcript_payload
from .turn_engine import events as turn_events
from .turn_engine import history as turn_history
from .turn_engine.engine import TurnBudgetExceeded, TurnEngine, TurnOutcome, TurnRequest
from .turn_engine.events import DatabaseSink, TurnEvent
from .turn_engine.executor import RecordsExecutor
from .turn_engine.model import OllamaModel
from .turn_engine.model_queue import TURNS_AHEAD_KEY, ModelQueueWait
from .turn_engine.policy import TurnPolicy
from .turn_engine.state import (
    CarriedRecap,
    active_rulings,
    carried_recap,
    continue_campaign,
    file_carried_recap,
    previous_campaign,
    recap_pending,
    write_carried_recap,
)
from .turn_record import (
    TurnRecorder,
    claim_turn,
    expire_stale_running_turns,
    running_turn_for,
    turns_ahead_of,
)
from .unarmed import unarmed_notice

logger = logging.getLogger(__name__)

# The ``kind`` the chat template renders as a chip rather than a message
# (issue #363). It exists only on the reader-facing entries
# :func:`_chip_entries` builds; nothing with this kind is ever stored.
DICE_LINE_KIND = "dice"
#: The page's marker for where a long session was folded (issue #791). Built
#: for the page alone, never stored; the model reads the recap itself.
FOLD_RECAP_KIND = "fold_recap"

# Upper bound on a single player message before it is appended to history and
# sent to the model — guards against prompt-injection padding and worker DoS.
MAX_GM_MESSAGE_CHARS = 4000

# How long a session name may be: the model's own column width, read off the
# field rather than restated, so the page, the endpoint and the database
# cannot disagree about where a rename is refused.
SESSION_NAME_MAX_STORED = column_length(GMSession, "name")

#: The frame key carrying the whole working line while a turn waits behind
#: other tables' turns (issue #64); chat.js shows it in place of "The Game
#: Master is working".
BUSY_FRAME_KEY = "busy"

#: How much of its budget a turn must have spent behind other tables' turns
#: for the queue to be named as why it ran out, when it was no longer waiting
#: at the end (issue #64). A turn still waiting when the budget ran out is
#: always told it was the queue.
QUEUE_BLAMED_SHARE = 0.5


def queue_status_line(turns_ahead: int, seconds: int) -> str:
    """The working line for a turn waiting behind other turns (issue #64).

    Args:
        turns_ahead: How many turns are ahead of it; at least one.
        seconds: How long the turn has been going.

    Returns:
        The line, from the settings' wording.
    """
    if turns_ahead == 1:
        return settings.GM_QUEUE_STATUS_ONE_AHEAD.format(seconds=seconds)
    return settings.GM_QUEUE_STATUS_MANY_AHEAD.format(
        turns_ahead=turns_ahead, seconds=seconds
    )


def too_slow_message(queue: ModelQueueWait, budget_seconds: float) -> str:
    """What the player reads when a turn outruns its budget (issue #64).

    The queue is named when the turn was still waiting behind other tables'
    turns as the budget ran out, or had spent at least
    :data:`QUEUE_BLAMED_SHARE` of it waiting behind them; the player is then
    told not to resend straight away, which would only join the end of the
    same line. Otherwise the turn was slow on its own, and the ordinary
    message stands.

    Args:
        queue: The turn's waits behind other turns.
        budget_seconds: The turn's budget.
    """
    # The budget is checked between silences, so the wait can run a moment
    # past it; the player is never told it waited longer than it had.
    waited = min(queue.waited(), budget_seconds)
    if queue.still_waiting or waited >= budget_seconds * QUEUE_BLAMED_SHARE:
        return settings.GM_STREAM_QUEUED_TOO_LONG_MESSAGE.format(
            waited_seconds=round(waited), budget_seconds=round(budget_seconds)
        )
    # A template like every message setting, so it is filled in too (with no
    # fields): a doubled brace in it reads as one, as it does in the others.
    return settings.GM_STREAM_TOO_SLOW_MESSAGE.format()


def _waiting_frame(payload: dict) -> dict:
    """A ``waiting`` event as the page reads it.

    A turn waiting behind other turns carries the whole line to show
    (issue #64); any other silence carries the status and seconds alone.
    """
    frame = dict(payload)
    turns_ahead = frame.pop(TURNS_AHEAD_KEY, 0)
    if turns_ahead:
        frame[BUSY_FRAME_KEY] = queue_status_line(
            turns_ahead, int(frame.get("elapsed", 0))
        )
    return frame


# What the player reads when a turn or a fight fails on an exception nobody
# wrote for them (issue #13). The exception's own text can carry file paths,
# SQL, the model host's address or internal state, so it goes to the log with
# its traceback and the turn's record and the page get this sentence instead.
# The sentence is never empty, so the page's ``if (data.error)`` check always
# shows it (issue #663).
GM_SERVER_ERROR_MESSAGE = (
    "The Game Master ran into an error on the server and could not finish. "
    "It has been logged; send your message again in a moment."
)


# A second message to a session whose turn is still running (issue #515).
TURN_ALREADY_RUNNING_ERROR = (
    "The Game Master is still answering your last message in this session. "
    "Its reply will appear here when it is ready; send this one after that."
)
# /fight while a message is still being answered (issue #836).
FIGHT_WHILE_TURN_RUNNING = (
    "The Game Master is still answering your last message in this session. "
    "Start the fight once the reply has appeared."
)
#: How a fight's turn record is marked (issue #836).
FIGHT_COMMAND = "fight"
#: /fight when the model cannot be asked who the party faces (review of #841).
FIGHT_MODEL_UNREACHABLE = (
    "The Game Master could not be reached to choose who you face. Nothing was "
    "started; try the fight again in a moment."
)
# A Retry of a turn that in fact finished after its page lost the stream
# (issue #515). Running it again would play its procedures twice.
TURN_ALREADY_ANSWERED_ERROR = (
    "That message was already answered while the page was away. Reload the "
    "page to see the reply."
)


def _get_gm_session(session_id, user) -> tuple[GMSession | None, JsonResponse | None]:
    """Look up a GMSession by id, scoped to ``user``.

    Returns ``(session, None)`` on success or ``(None, error_response)`` when
    ``session_id`` doesn't resolve to one of the user's sessions. Every caller
    of this helper is a JSON endpoint, so both failure modes come back as a
    clean JSON response rather than an uncaught exception: a malformed id
    (``ValueError`` for a non-numeric string like ``"abc"``, ``TypeError`` for
    a JSON array/object, ``OverflowError`` for ``1e999``, which JSON reads as
    infinity and Django's lookup does not catch -- issue #767) is a 400, and a
    well-formed but nonexistent id — the ``Http404`` that ``get_object_or_404``
    raises — is a JSON 404 instead of Django's HTML 404 page (issue #165).
    """
    try:
        return get_object_or_404(GMSession, pk=session_id, user=user), None
    except (ValueError, TypeError, OverflowError):
        return None, _json_error("Invalid session_id")
    except Http404:
        return None, _json_error("GM session not found", status=404)


#: What a Game Master's own characters are grouped under in the picker.
OWN_CHARACTERS_GROUP = "Your characters"


@dataclass(frozen=True)
class AttachableGroup:
    """One owner's characters in the attach picker (issue #759).

    Attributes:
        owner: The group's label: :data:`OWN_CHARACTERS_GROUP`, a player's
            username, or ``""`` for a player's picker, which has one owner and
            needs no label.
        characters: The owner's characters, by name.
    """

    owner: str
    characters: list[Character]


def _attachable_groups(user, characters: list[Character]) -> list[AttachableGroup]:
    """``characters`` grouped by owner for the picker, the viewer's own first.

    ``characters`` is one page of the picker, newest first (#864); each group
    keeps that order, and the other owners' groups follow by username.
    """
    if not is_dm(user):
        return [AttachableGroup("", characters)] if characters else []
    own = [character for character in characters if character.user_id == user.pk]
    groups = [AttachableGroup(OWN_CHARACTERS_GROUP, own)] if own else []
    by_owner: dict[str, list[Character]] = {}
    for character in characters:
        if character.user_id != user.pk:
            by_owner.setdefault(character.user.username, []).append(character)
    groups += [AttachableGroup(owner, by_owner[owner]) for owner in sorted(by_owner)]
    return groups


def _get_attachable_character(
    request: HttpRequest, character_id
) -> tuple[Character | None, JsonResponse | None]:
    """Look up a character ``request.user`` may attach to one of their sessions.

    Same contract as :func:`_get_gm_session`. A character the user cannot
    edit (``_can_edit_object``: owner, or DM/admin) and a soft-deleted one
    are both a JSON 404 -- the endpoint never confirms that a foreign or
    deleted row exists. A dead character is a 400: the picker only lists the
    living, and the GM cannot play the dead.
    """
    try:
        character = get_object_or_404(Character, pk=character_id, is_deleted=False)
    except (ValueError, TypeError, OverflowError):
        return None, _json_error("Invalid character_id")
    except Http404:
        return None, _json_error("Character not found", status=404)
    # The rule itself lives in gm.attach, so the controls that offer an
    # attach (the sheet's and the home page's, #827/#828) ask the same one.
    refusal = attach_refusal(request.user, character)
    if refusal is not None:
        return None, _json_error(refusal.message, status=refusal.status)
    return character, None


def _session_characters_payload(session: GMSession) -> dict:
    """The JSON both character endpoints answer with.

    It carries the Battle tab's bare-handed line as it stands after the
    change, since the page shows an attach or a detach in place (issue #15)
    and that line names the attached characters who carry no weapon.
    """
    return {
        "ok": True,
        "session_id": session.pk,
        "characters": [
            {"id": character.pk, "name": character.name}
            for character in session.attached_characters()
        ],
        BARE_HANDED_KEY: unarmed_notice(session),
    }


class PanelDraft(NamedTuple):
    """A character this session built, as the Characters pane shows it.

    Attributes:
        heading: Its name, or name and label when several are being built.
        ledger: What only a character being built shows: the skill-point
            account and what it still needs (issue #356).
        character: The record.
    """

    heading: str
    ledger: DraftLedger
    character: Character

    @property
    def sheet(self) -> CharacterSheet:
        """The sheet every character in the pane is shown from (issue #848)."""
        return CharacterSheet.from_character(self.character)


def _draft_ledgers(session: GMSession | None) -> list[PanelDraft]:
    """The characters this session built, as the Characters pane shows them.

    Read from the same character records the model's state block is built
    from (issues #571, #590), so the pane and the GM cannot disagree about a
    number.

    Returns:
        ``(heading, ledger, character)`` for each; empty when the session
        built nobody.
    """
    entries = roster.built_here(session)
    return [
        PanelDraft(heading, ledger, entry.character)
        for (heading, ledger), entry in zip(
            roster.ledger_headings(entries), entries, strict=True
        )
    ]


def _campaign_panel_context(
    user, session: GMSession | None, resolved_since: StoryThread | None = None
) -> dict:
    """What the Campaign tab renders, for the page and its live fragment.

    Read from the same records the model is handed in its state block
    (issue #579), so the pane and the GM cannot disagree about where the
    party is or what state a thing is in. Resolved threads are kept and
    shown folded away: what the party settled is part of the campaign, but
    it should not crowd out what is still hanging.

    The table's rulings live here with the other table records (issue
    #789), and a Game Master gets the controls that add, correct, retire,
    resolve and remove them by hand (issues #789, #790); each record set by
    hand says who set it.

    The resolved threads are the latest page of them, with a control that
    loads the page above (issue #972): a long campaign settles more threads
    than the tab can usefully list at once. ``resolved_since`` is the first
    thread of the earliest page a reader has loaded, so a refetched tab keeps
    the pages they loaded (issue #982).
    """
    if session is None:
        return {"campaign_session": None}
    marks = latest_hand_edits(session)
    resolved_page = resolved_threads_page(session, since=resolved_since)
    _hand_marked(resolved_page.threads, GMRecordEdit.THREAD, marks)
    return {
        "can_edit_records": may_edit_by_hand(user),
        # Each hand-set text's maxlength, the column's own (issue #938).
        "gm_text_limits": GM_TEXT_LIMITS,
        "campaign_rulings": _hand_marked(
            active_rulings(session), GMRecordEdit.RULING, marks
        ),
        "campaign_session": session,
        "campaign_state": CampaignState.objects.filter(session=session).first(),
        "campaign_supplies": list(SupplyItem.objects.filter(session=session)),
        "campaign_people": _hand_marked(
            list(SceneEntity.objects.filter(session=session, kind=SceneEntity.PERSON)),
            GMRecordEdit.PERSON,
            marks,
        ),
        "campaign_things": _hand_marked(
            list(SceneEntity.objects.filter(session=session, kind=SceneEntity.THING)),
            GMRecordEdit.THING,
            marks,
        ),
        "campaign_threads": _hand_marked(
            list(StoryThread.objects.filter(session=session, resolved=False)),
            GMRecordEdit.THREAD,
            marks,
        ),
        # The page of them the tab lists, and the page itself for its count
        # and its "Show earlier" control (issue #972).
        "campaign_resolved": resolved_page.threads,
        "campaign_resolved_page": resolved_page,
    }


def _hand_marked(rows: list, kind: str, marks: dict) -> list:
    """``rows``, each carrying ``hand_mark``: who last set it by hand, or ``""``.

    The pane says which records the Game Master set himself (issues #789,
    #790), so a hand edit is never taken for one the model made.
    """
    for row in rows:
        row.hand_mark = marks.get((kind, row.pk), "")
    return rows


def _characters_panel_context(user, session: GMSession | None) -> dict:
    """What the Characters tab renders, for the page and its live fragment.

    The attached characters' sheets, the picker of the user's playable
    characters not yet attached, and the characters this session is building
    (issues #341, #356, #571), each from the same snapshot the model is given,
    so a disagreement between the GM's prose and the truth is visible in the
    pane rather than argued about. Characters this session built are listed
    from their ledgers, so they are left out of the sheets.
    """
    attached = list(session.attached_characters()) if session else []
    built_ids = {entry.character.pk for entry in roster.built_here(session)}
    # A page at a time, newest first, with a search that reaches every page
    # (#864); a Game Master's page is grouped by owner (#759).
    picker = attachable_picker(user, session, attached)
    groups = _attachable_groups(user, picker.rows)
    return {
        "attachable_picker": picker,
        "panel_characters": sheets_for(
            character
            for character in attached
            if character.pk not in built_ids and character.is_complete
        ),
        "attachable_groups": groups,
        "attachable_characters": [
            character for group in groups for character in group.characters
        ],
        "panel_drafts": _draft_ledgers(session),
        # Luck, once per session per level (issue #861).
        "panel_luck": luck_panel(session),
    }


def _finished_turn_map(session_id: int | None, narration: str) -> dict | None:
    """Where the party is after a turn, for the Map tab to follow (#343, #579).

    The party's own record, which the turn may just have changed; only a
    session that has recorded no place at all falls back to reading the
    narration for one. The done frame carries it, and so does the answer a
    page that lost the stream reads the finished turn from (issue #89).

    Args:
        session_id: The session the turn saved into.
        narration: The turn's reply.

    Returns:
        The map payload, or None when neither names a place.
    """
    party_map = _party_map_payload(session_id)
    if party_map is not None:
        return party_map
    detected_place = detect_place(narration)
    return detected_place.frame_payload() if detected_place is not None else None


def _story_map_payload(history: list[ChatMessage]) -> dict[str, str] | None:
    """The ``map`` payload the latest GM reply naming a place would carry."""
    for message in reversed(history):
        # A notice the app filed (issue #871) is not a GM reply naming a place.
        if message.get("role") != "assistant" or is_reader_only(message):
            continue
        detected_place = detect_place(message.get("content", ""))
        if detected_place is not None:
            return detected_place.frame_payload()
    return None


def _party_map_payload(session_id: int | None) -> dict[str, object] | None:
    """The ``map`` payload for where the party's record puts them (issue #579).

    The record beats the prose. A reply that mentions Majahelm in passing
    used to drag the Map tab there while the party sat in a well under
    Farport; the party is where ``move_party`` last put them, and a place
    the gazetteer never named is reported unmapped rather than passed over
    in silence.

    Args:
        session_id: The session to read, or None when there is none yet.

    Returns:
        The payload, or None when the session has recorded no place.
    """
    if session_id is None:
        return None
    state = CampaignState.objects.filter(session_id=session_id).first()
    if state is None:
        return None
    where = party_place(state.place, state.place_key)
    return None if where is None else where.frame_payload()


def _visible_history(session: GMSession) -> list[ChatMessage]:
    """The messages of a session a reader sees.

    Neither the system prompt nor the blocks filed for the model alone --
    canon excerpts, character sheets, battle log entries -- are anyone's
    to read.

    Args:
        session: The session to read.

    Returns:
        The visible messages, oldest first.
    """
    visible: list[ChatMessage] = []
    # Where a long session was folded (issue #791): the recap the model reads
    # in place of the entries before this point, shown where they end.
    fold_at = session.summary_covers if session.summary else 0
    recap_set_by = (
        GMRecordEdit.objects.filter(session=session, record_kind=GMRecordEdit.RECAP)
        .select_related("user")
        .last()
        if fold_at
        else None
    )
    for index, message in enumerate(session.history):
        if fold_at and index == fold_at:
            visible.append(_fold_marker(session, recap_set_by))
        if message.get("role") == "system":
            continue
        if is_roll_message(message) or is_procedure_message(message):
            # A die the GM asked the server to roll (issue #363), or a rules
            # procedure the server ran for it (issue #370), is hidden as a
            # *message* -- its text is written to the model -- but its dice and
            # its verdict are the player's to see, so a reloaded session shows
            # the same chips the turn showed live rather than losing them.
            visible.extend(_chip_entries(message))
            continue
        if is_hidden_message(message):
            continue
        if message.get(TYPED_KEY):
            # A /roll (issue #838): the line the player typed, then the chip
            # the turn showed live, from the dice the server stored.
            visible.append({**message, "content": as_typed(message)})
            line = (message.get(PLAYER_ROLL_KEY) or {}).get("line")
            if line:
                visible.append(
                    {"role": "assistant", "kind": DICE_LINE_KIND, "content": str(line)}
                )
            continue
        visible.append(message)
    if fold_at and fold_at >= len(session.history):
        visible.append(_fold_marker(session, recap_set_by))
    return visible


def _panel_verdict(session: GMSession | None) -> str | None:
    """How the session's fight ended, for the Battle pane (issue #785).

    The notice filed for this ending, else the verdict read from the battle:
    a fight that ended before notices were filed still says how it went.
    ``None`` while it goes on or when there is none.
    """
    if session is None or session.battle is None:
        return None
    notice = filed_verdict(session)
    return str(notice["content"]) if notice else fight_verdict(session.battle)


def _battle_panel_context(
    user: AbstractBaseUser | AnonymousUser, session: GMSession | None
) -> dict[str, object]:
    """Everything ``gm/_panel_battle.html`` needs, wherever it is rendered.

    The chat page's Battle tab (issue #345) and the tab's live fragment
    (``gm:battle_panel``, issue #89) render the partial from this one
    context: the session's battle drawn with the battle app's own arena
    partial, from the same context the battle page builds, so the board
    beside the chat is the board.

    Args:
        user: The viewer, who decides what the arena offers.
        session: The session whose fight the tab shows, or None for a page
            opened without one.

    Returns:
        The battle, how it ended, whether it is still on, and the arena's
        own context when there is a battle.
    """
    battle = session.battle if session is not None else None
    return {
        "panel_battle": battle,
        # How it ended, once it has (issue #785): the notice filed for this
        # ending, so the pane says what the chat says.
        "panel_verdict": _panel_verdict(session),
        "battle_live": session is not None and fight_in_progress(session),
        **(
            arena_context(battle, Actor.for_user_or_guest(user, ""))
            if battle is not None
            else {}
        ),
    }


def _fold_marker(session: GMSession, set_by: GMRecordEdit | None) -> ChatMessage:
    """The recap a fold left, as the chat shows it at the fold (issue #791)."""
    return {
        "role": "recap",
        "kind": FOLD_RECAP_KIND,
        "content": session.summary,
        "covers": session.summary_covers,
        "set_by": set_by.line if set_by is not None else "",
    }


def _chip_entries(message: ChatMessage) -> list[ChatMessage]:
    """A stored roll or procedure entry as the chips the page renders.

    Args:
        message: A history entry marked with ``gm.retrieval.ROLL_KIND`` or
            ``gm.retrieval.PROCEDURE_KIND``.

    Returns:
        One entry per chip line, each rendered by the chat template as a
        ``.dice-roll`` element. Every line comes from the payload the outcome
        was filed with -- never from a re-roll, never from a recomputed
        verdict, never from the model's prose.
    """
    roll = message.get("roll") or {}
    procedure = message.get("procedure") or {}
    lines = [str(line) for line in procedure.get("lines", [])] or [
        str(roll.get("line", ""))
    ]
    return [
        {"role": "assistant", "kind": DICE_LINE_KIND, "content": line}
        for line in lines
        if line
    ]


def _map_panel_context(
    session: GMSession | None, history: list[ChatMessage]
) -> dict[str, object]:
    """Everything ``gm/_panel_map.html`` needs, wherever it is rendered.

    The chat page's Map tab and the standalone map page (issue #351) render
    the same partial from this one context, so the picker, the detector and
    the images cannot disagree between them.

    Args:
        session: The session the map belongs to, or None for a page opened
            without one.
        history: The session's visible messages, newest last; the last GM
            reply naming a charted place decides the map opened on.

    Returns:
        The ``map_areas`` registry and the ``map_config`` block the tab's
        script reads.
    """
    return {
        "map_areas": MAP_AREAS,
        # The map the tab opens on when nothing else picks one: the regional
        # chart, whichever canon is loaded (issue #918).
        "map_default_slug": next(
            (area.slug for area in MAP_AREAS if area.scope is MapScope.REGION), None
        ),
        "map_config": {
            "sessionId": session.pk if session else None,
            # Where the party's record puts them (issue #579), which the tab
            # opens on ahead of anything the prose named.
            "partyMap": _party_map_payload(session.pk if session else None),
            "storyMap": _story_map_payload(history),
        },
    }


class GMMapView(LoginRequiredMixin, TemplateView):
    """The Map panel on its own page, full window (issue #351).

    Where the panel's Map pane pops out to. It renders the very same
    partial the pane does, from the same registry, so there is one map
    surface in the codebase and not two. ``?session=<pk>`` names the
    session whose story the map follows (and whose remembered chart the
    page shares, both being keyed on the session in the browser's storage);
    ``?map=<slug>`` is read by the script, which opens on that chart.
    """

    template_name = "gm/map.html"
    login_url = "login"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        session_id = self.request.GET.get("session")
        session = None
        history: list[ChatMessage] = []
        if session_id:
            try:
                session = GMSession.objects.filter(
                    pk=session_id, user=self.request.user
                ).first()
            except (ValueError, TypeError):
                # A junk ?session= is a stale link, not a reason to fail:
                # the page opens on the maps with no story to follow.
                session = None
            if session is not None:
                history = _visible_history(session)
        context.update(
            {
                "active_session": session,
                "map_standalone": True,
                **_map_panel_context(session, history),
            }
        )
        return context


class GMChatView(BreadcrumbMixin, ActivePageMixin, LoginRequiredMixin, TemplateView):
    # The trail and the menu's you-are-here mark every other page has had
    # since issue #189 (issue #339, item 13).
    template_name = "gm/chat.html"
    login_url = "login"
    active_page = "gm"
    breadcrumb_label = "Game Master"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Every session, not the newest twenty (issue #339, item 12). The
        # cap silently hid a player's older games from the only list that
        # reaches them, and the sidebar is a scrolling column that can hold
        # them. If this ever needs bounding it wants a pager on the page,
        # not a limit nothing tells the reader about.
        session_id = self.request.GET.get("session")
        active_session = None
        running = None
        history: list[ChatMessage] = []

        # A turn this page's session is still running (issue #515): the page
        # was reloaded, or came back after the stream dropped. It says so and
        # waits for the reply, which is saved whether or not anyone watched.
        #
        # Only for a page that names a session. A turn sent as the first
        # message of a new session keeps session_id NULL for the whole of its
        # run, so a session-less lookup matches it by nothing but "this
        # player has one running", and a second tab opened to start a
        # different game was handed the first tab's turn: read-only until it
        # landed, then navigated into the first tab's session. That page
        # remembers its own turn instead, per tab, and asks after it by id
        # (issue #650).
        #
        # Asked before the session is read (issues #880, #910). A turn saves
        # its history and only then finishes its record, so asking in this
        # order always finds one or the other. Read the other way round, a
        # turn that saved and finished between the two reads left a page
        # with neither: the session from before the save, and no running
        # turn to wait for, so the turn's dice and reply never appeared. The
        # row itself is the read that counts: its history, name and recap
        # are all loaded with it, so fetching it before asking (as #880's
        # fix still did) kept the race. running_turn_for filters on the
        # owner, so asking before the ownership check reveals nothing.
        if session_id:
            try:
                requested_pk = int(session_id)
            except ValueError:
                raise Http404("No GMSession matches the given query.")
            running = running_turn_for(self.request.user, requested_pk)
            active_session = get_object_or_404(
                GMSession, pk=requested_pk, user=self.request.user
            )
            history = _visible_history(active_session)

        # The mode indicator (issue #353), which is now also the control
        # that changes the mode (issue #372). It is read from the same value
        # the stream endpoint sends the model -- the session's own column, or
        # for a session that does not exist yet, the mode the last switch put
        # in the query string -- so the page can never claim one mode while
        # the model is being run in the other.
        mode = mode_for_user(
            active_session.mode if active_session else self.request.GET.get("mode"),
            self.request.user,
        )
        # The rail and its small-screen menu, a page at a time, most
        # recently played first, with a search that reaches every page; the
        # open session is listed whatever page it is on (#864).
        rail_picker = session_picker(
            self.request.user, "rail", active_session=active_session
        )
        context.update(
            {
                "session_rail_picker": rail_picker,
                "session_menu_picker": session_picker(
                    self.request.user, "menu", active_session=active_session
                ),
                # The sessions the rail shows, as one list, for what reads
                # the page's sessions rather than the picker.
                "sessions": [*rail_picker.pinned, *rail_picker.rows],
                "active_session": active_session,
                # The name field's own limit, from the column (item 10).
                "session_name_max_length": SESSION_NAME_MAX_STORED,
                # The recap correction's maxlength, the fold's own (#938).
                "gm_text_limits": GM_TEXT_LIMITS,
                # Every visible entry, for what reads the conversation
                # (the map below, the empty state); the chat renders the
                # latest page of turns, with a control for the page above
                # (issue #972).
                "history": history,
                "history_page": history_page(history),
                "is_game_master": is_dm(self.request.user),
                # Mode is a separate axis from the role above: the role says
                # what this person may do, the mode says how the model
                # behaves, and anyone may change their own (issue #372).
                "session_mode": mode,
                "session_mode_label": MODE_LABELS[mode],
                "session_mode_description": MODE_DESCRIPTIONS[mode],
                "session_mode_placeholder": MODE_PLACEHOLDERS[mode],
                "session_mode_opening": MODE_OPENINGS[mode],
                "session_mode_options": [
                    {
                        "value": value,
                        "label": label,
                        "description": MODE_DESCRIPTIONS[value],
                        "placeholder": MODE_PLACEHOLDERS[value],
                        "opening": MODE_OPENINGS[value],
                        "selected": value == mode,
                    }
                    for value, label in MODE_LABELS.items()
                ],
                # A new session may continue the last one's campaign (issue
                # #761): offered on the new-session page from the records
                # themselves, and only ever done when asked.
                "campaign_offer": (
                    previous_campaign(self.request.user)
                    if active_session is None
                    else None
                ),
                # Which story a continued session opened with, so the page
                # can say whether it is the whole of the last session.
                "carried_recap": (
                    carried_recap(active_session)
                    if active_session is not None and active_session.continued_from
                    else None
                ),
                "continued_recap_pending": recap_pending(active_session),
                # The Characters tab (issues #341, #356, #571), from the one
                # builder the live panel fragment uses too.
                **_characters_panel_context(self.request.user, active_session),
                # The Campaign tab (issue #579): where the party is, who
                # and what is in play, and what is still open.
                **_campaign_panel_context(self.request.user, active_session),
                # The Map tab (issue #343): every map the picker offers, and
                # the map the story last named so a reloaded page opens on
                # the same chart the last reply chose. The standalone map
                # page (issue #351) renders the same partial from the same
                # helper.
                **_map_panel_context(active_session, history),
                # The Battle tab (issue #345), from the one builder its live
                # fragment uses too (issue #89).
                **_battle_panel_context(self.request.user, active_session),
                # What gm-panel-characters.js needs, in its own json_script
                # block so chat.js's #chat-config keeps only its own keys.
                # gm-panel-battle.js reads the token and session id from the
                # same block; its endpoint rides on the fight form.
                "panel_config": {
                    "attachUrl": reverse("gm:characters_attach"),
                    "detachUrl": reverse("gm:characters_detach"),
                    # The Characters tab refetches itself while a turn runs
                    # (issue #571), so a character the GM just changed shows
                    # without a reload.
                    "charactersPanelUrlTemplate": reverse(
                        "gm:characters_panel", args=[0]
                    ).replace("/0/", "/{session_id}/"),
                    # The Campaign tab refetches the same way (issue #579),
                    # so a thing the GM just moved shows without a reload.
                    "campaignPanelUrlTemplate": reverse(
                        "gm:campaign_panel", args=[0]
                    ).replace("/0/", "/{session_id}/"),
                    # The Battle tab is asked for again when the board
                    # changes under it (issue #89), rather than the page.
                    "battlePanelUrlTemplate": reverse(
                        "gm:battle_panel", args=[0]
                    ).replace("/0/", "/{session_id}/"),
                    "sessionId": active_session.pk if active_session else None,
                },
                # Everything chat.js needs from Django, in one json_script
                # block (issue #63). It replaces the last inline <script> on
                # this page, which declared these as bare globals.
                "chat_config": {
                    # The mode a session-creating turn should open in
                    # (issue #372); ignored by the server once the session
                    # exists and carries its own.
                    "mode": mode,
                    "streamUrl": reverse("gm:stream"),
                    # Where the page asks after a turn it lost sight of, and the
                    # turn still running when it loaded (issue #515).
                    "turnUrlTemplate": reverse("gm:turn", args=[0]).replace(
                        "/0/", "/{turn_id}/"
                    ),
                    # Where the page asks for the conversation as stored, from
                    # a turn on, to show it without a reload (issue #89).
                    "historyUrlTemplate": reverse("gm:history_page", args=[0]).replace(
                        "/0/", "/{session_id}/"
                    ),
                    "runningTurn": (
                        {"id": running.pk, "message": running.user_message}
                        if running is not None
                        else None
                    ),
                    "saveUrl": reverse("gm:save"),
                    "deleteUrl": reverse("gm:delete"),
                    "sessionId": active_session.pk if active_session else None,
                    # The Transcript button's download (issue #389), as a pattern
                    # the page fills in with the session it is on *now* (issue
                    # #436). A frozen URL left a session this page created with
                    # nothing to download until the page was reloaded.
                    "transcriptUrlTemplate": reverse("gm:transcript", args=[0]).replace(
                        "/0/", "/{session_id}/"
                    ),
                    # Where "Send to Claude" posts (issue #402); only a Game Master gets
                    # one, and the endpoint checks the account again regardless.
                    "reviewUrl": (
                        reverse("gm:reviews") if is_dm(self.request.user) else None
                    ),
                },
            }
        )
        return context


def _persist_turns(
    session: GMSession | None,
    user,
    history: list[ChatMessage],
    prior_message_count: int,
    mode: str,
) -> int:
    """Save this turn's new history entries; returns the session id.

    Strips system messages before persisting. An existing session is
    re-fetched under a row lock and only the entries past
    ``prior_message_count`` are appended, so a concurrent send to the same
    session can't be lost (and unrelated columns aren't rewritten); with no
    session yet, one is created holding the whole conversation.

    Args:
        session: The session being appended to, or ``None`` to create one.
        user: Its owner.
        history: This turn's messages, system message included.
        prior_message_count: How many non-system entries were already stored.
        mode: The mode this turn ran in, written onto a session being created
            so the next turn reads back the mode this one used (issue #372).
            Ignored for an existing session, whose mode only ``gm_mode``
            changes.
    """
    non_system = [m for m in history if m.get("role") != "system"]
    if session is None:
        new_session = GMSession(
            name=name_from_history(non_system), user=user, mode=mode
        )
        new_session.history = non_system
        new_session.save()
        return new_session.pk
    with transaction.atomic():
        locked = GMSession.objects.select_for_update().get(pk=session.pk)
        locked.history = locked.history + non_system[prior_message_count:]
        fields = ["history_json", "updated_at"]
        # A session still carrying the placeholder gets named after its own
        # opening line, the moment there is one (issue #339, item 12). Only
        # the placeholder is replaced, so a session anybody has named — by
        # /save or by editing the name on the page — keeps that name.
        if locked.name in ("", PLACEHOLDER_SESSION_NAME):
            derived = name_from_history(locked.history)
            if derived != locked.name:
                locked.name = derived
                fields.append("name")
        locked.save(update_fields=fields)
        return locked.pk


def _stored_length(history: list[ChatMessage]) -> int:
    """How many entries ``history`` holds once saved: system messages never are."""
    return sum(1 for message in history if message.get("role") != "system")


def _rejected_turn(recorder: TurnRecorder, message: str) -> JsonResponse:
    """Record a turn the server refused before it began, and refuse it (issue #389)."""
    recorder.finish(GMTurnRecord.OUTCOME_REJECTED, error=message)
    return _json_error(message)


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


# How long a GM stream may go without sending anything before it sends a
# keep-alive (issue #415). A turn can sit silent for tens of seconds while the
# model reads a long prompt before its first token, and a proxy or browser
# that sees an idle connection may close it. An SSE comment line is ignored by
# every client, the page's included, and costs a few bytes.
GM_STREAM_KEEPALIVE_SECONDS = 10.0

#: What the page shows while a continued session's first turn writes the
#: last session's recap (issue #761).
CATCHING_UP_STATUS = "reading back over the last session"
SSE_KEEPALIVE = ": keep-alive\n\n"

#: How long past ``GM_RECAP_BUDGET_SECONDS`` a continued session's first turn
#: waits for a recap whose call was hung up at the deadline to hand back what
#: it has, before carrying the fold recap without it (issue #936).
CATCH_UP_GRACE_SECONDS = 1.0

#: What the page shows while a long session's older turns are folded into a
#: recap before the turn is planned (issue #783).
FOLDING_STATUS = "reading back over the story so far"


#: The templates that write one session's entry in the rail and in the
#: small-screen menu: for the page load, for every search, and for a session a
#: turn creates.
SESSION_RAIL_ROW_TEMPLATE = "gm/_session_rail_row.html"
SESSION_MENU_OPTION_TEMPLATE = "gm/_session_menu_option.html"


def session_entry_html(template: str, session: GMSession) -> str:
    """One session's entry, written by the template that writes all of them.

    The page puts it into the rail (or the menu) when a turn creates the
    session, so the entry (its ids, its labels, its message count, its date)
    is the one the server writes for every other and nothing in the page
    copies it (issue #62).

    Args:
        template: :data:`SESSION_RAIL_ROW_TEMPLATE` or
            :data:`SESSION_MENU_OPTION_TEMPLATE`.
        session: The session the page is showing, so its entry is the active one.

    Returns:
        The ``<li>`` or ``<option>`` as HTML.
    """
    return render_to_string(template, {"s": session, "active_session": session})


def _with_session_id(
    frame: dict, session: GMSession | None, *, creating: bool = False
) -> dict:
    """``frame`` carrying the session's id once there is one (issue #410).

    A turn's first procedure or roll can create the session, and the id used
    to arrive only on the done frame. A page whose connection broke after that
    retried into a brand-new session and played the years again.

    Args:
        frame: The frame to add to.
        session: The session, or ``None`` before the turn has made one.
        creating: Whether this turn is the one that makes the session, so the
            page has no entry for it yet. Only then does the frame carry the
            session's rail row and menu option; a later turn's frames would
            only repeat what the page already holds.
    """
    if session is not None:
        frame["session_id"] = session.pk
        # And what it is called (issue #704). Sending the first message on a
        # blank page creates a session, and the rail on the left went on
        # showing only "+ New" until the next full page load -- a player who
        # then pressed "+ New" could not see that the first session had been
        # kept, and the panel still read "A new session takes its name from
        # your first message" about something that had happened. The name is
        # the server's, derived in gm.models.name_from_history, so the page
        # never re-derives it.
        frame["session_name"] = session.name
        if creating:
            # The entries are the rail's and the menu's own templates', so the
            # page never builds one (issue #62). They ride every frame of the
            # creating turn because the first to name the session may be a
            # procedure's, and a turn that then breaks has already made it.
            frame["session_row_html"] = session_entry_html(
                SESSION_RAIL_ROW_TEMPLATE, session
            )
            frame["session_menu_option_html"] = session_entry_html(
                SESSION_MENU_OPTION_TEMPLATE, session
            )
    return frame


@login_required
@require_POST
@rate_limit("gm_stream", max_requests=30, window_seconds=60)
def gm_stream(request: HttpRequest) -> StreamingHttpResponse | JsonResponse:
    """SSE endpoint: stream a GM response."""
    data, error_response = _parse_json_body(request)
    if error_response:
        return error_response
    session_id = data.get("session_id")
    user_message = data.get("message", "")
    if not isinstance(user_message, str):
        return _json_error("message must be a string")
    user_message = user_message.strip()
    command = data.get("command")

    # Bound user input before it is appended to history / sent to the model.
    refusal = too_long("Message", user_message, MAX_GM_MESSAGE_CHARS)
    if refusal:
        return _json_error(refusal)

    # Load or create session (scoped to the requesting user)
    if session_id:
        session, err = _get_gm_session(session_id, request.user)
        if err:
            return err
        assert session is not None
        # A copy: the session's cached list is what the fold reads later. A
        # session that has grown past a share of the window has its older
        # turns folded into a recap (issue #579), but only once this turn is
        # claimed and recorded, inside its stream (issue #783): folding here
        # made a refused second message pay for a whole recap on the shared
        # model, with no record of it and nothing sent on the connection.
        history = list(session.history)
    else:
        session = None
        history = []

    # How this turn is answered (issue #372). An existing session carries its
    # own mode; the turn that creates one takes the mode the page was showing,
    # which is the role's default until the person changes it. A mode from the
    # body is safe to honour because a mode grants nothing: it says how the
    # model behaves, never what may be written down. What may be written down
    # is decided a line below, off the account, exactly as before (issue #352).
    mode = (
        session.mode
        if session is not None
        else mode_for_user(data.get("mode"), request.user)
    )

    roll_outcome: tuple[list[int], int] | None = None
    ordinary_turn = False
    resumed = False
    # What the model reads in place of the stored message, for a command
    # whose prompt the server writes (issue #758).
    command_prompt: str | None = None

    # Ensure the GM's own system prompt is first. The test is by identity, not
    # by role: any bookkeeping entry carries a ``kind``, and one of them --
    # the folded session's recap -- used to sit at index 0 wearing the
    # ``system`` role, which this guard read as "the prompt is already here".
    # Every turn of every folded session then went out with no prompt at all,
    # the plan schema included (issue #631).
    stored_entries = len(history)
    stored_from = 0
    if not history or history[0].get("role") != "system" or history[0].get("kind"):
        # The authority half of the prompt follows the signed-in user's role
        # (issue #352); it is never read from the message being sent. Neither
        # half is persisted with the session, so a role change or a mode
        # switch takes effect on the next turn.
        history.insert(0, get_system_message(request.user, mode))
        stored_from = 1

    # Count the turns already stored, so on save we append only the new turns to
    # the session's *current* history rather than overwriting it with this
    # (possibly stale) snapshot — avoids losing a concurrent send's turns.
    prior_message_count = sum(1 for m in history if m.get("role") != "system")

    # Opened before any branch can end the turn, so every way out of it is
    # recorded -- including the ones that save no history (issue #389).
    recorder = TurnRecorder(
        user=request.user,
        session_id=session.pk if session is not None else None,
        command=(
            command[: GMTurnRecord.MAX_COMMAND_CHARS]
            if isinstance(command, str)
            else ""
        ),
        mode=mode,
        is_game_master=is_dm(request.user),
        user_message=user_message,
        history_start=prior_message_count,
        system_prompt=str(history[0].get("content", "")),
        model=get_gm_model(),
        options=get_gm_options(),
    )

    # One turn at a time per session (issue #515). A turn now runs to the end
    # even after its page leaves, so a second message sent meanwhile -- from a
    # reloaded page, another tab, or a Retry -- would interleave with it.
    already_running = claim_turn(recorder)
    if already_running is not None:
        recorder.finish(GMTurnRecord.OUTCOME_REJECTED, error=TURN_ALREADY_RUNNING_ERROR)
        return JsonResponse(
            {"error": TURN_ALREADY_RUNNING_ERROR, "running_turn": already_running.pk},
            status=409,
        )

    # Handle special commands. /state and /rules store the line the player
    # typed and hand the model the prompt the server writes for it, for this
    # turn only. Stored, the rules prompt was the player's message after a
    # reload and went to the model again on every later turn -- the whole
    # rules tree, about 20,000 tokens a turn (issue #758).
    if command == "state":
        history.append({"role": "user", "content": "/state"})
        command_prompt = build_state_request_message()["content"]
    elif command == "rules" and user_message:
        rules_text = load_rules()
        if rules_text:
            history.append({"role": "user", "content": f"/rules {user_message}"})
            command_prompt = build_rules_lookup_message(user_message, rules_text)[
                "content"
            ]
        else:
            return _rejected_turn(recorder, "Rules directory not found")
    elif command == "roll":
        try:
            rolls, total = roll_dice(user_message)
        except ValueError as e:
            return _rejected_turn(recorder, str(e))
        roll_outcome = (rolls, total)
        # Stored as typed, with the prompt the model has always read kept as
        # its content: later turns go on from those faces (issue #838).
        history.append(stored_roll_message(user_message, rolls, total))
    elif (
        data.get("retry") is True
        and user_message
        and resumes_interrupted_turn(history, user_message)
    ):
        # A retry of a turn that broke off after the server had run its
        # procedures or rolls (issue #410). The message, what was run for it
        # and a note saying so are stored already; appending the message a
        # second time would read to the model as a second request.
        logger.info("Retrying a GM turn that broke off in session %s", session_id)
        resumed = True
    elif (
        data.get("retry") is True
        and session is not None
        and _already_answered(session, user_message, recorder.record_id)
    ):
        # The turn this Retry is for ran to the end after its page lost the
        # stream (issue #515). Sending it again would play it twice.
        return _rejected_turn(recorder, TURN_ALREADY_ANSWERED_ERROR)
    elif user_message:
        history.append({"role": "user", "content": user_message})
        ordinary_turn = True
    else:
        return _rejected_turn(recorder, "No message provided")

    # The plan → run → narrate loop (issue #590).
    return _plan_turn(
        request,
        PlannedTurn(
            session=session,
            history=history,
            mode=mode,
            recorder=recorder,
            prior_message_count=prior_message_count,
            user_message=user_message,
            ordinary_turn=ordinary_turn,
            resumed=resumed,
            roll_outcome=roll_outcome,
            command_prompt=command_prompt,
            stored_from=stored_from,
            stored_entries=stored_entries,
        ),
    )


# What the player is told when the model's plan could not be read twice over
# (issue #590): nothing was recorded, and the narration says so too.
PLAN_UNREADABLE_NOTICE = (
    "The Game Master could not work out what to record or roll for that "
    "message, so nothing was changed. Try asking again in other words."
)


@dataclass
class PlannedTurn:
    """What :func:`gm_stream` settled before handing a turn to the engine."""

    session: GMSession | None
    history: list[ChatMessage]
    mode: str
    recorder: TurnRecorder
    prior_message_count: int
    user_message: str
    ordinary_turn: bool
    resumed: bool
    roll_outcome: tuple[list[int], int] | None
    #: What the model reads for the latest message instead of what was
    #: stored, when a command's prompt is the server's (issue #758).
    command_prompt: str | None = None
    #: Where the session's stored entries sit in ``history``, and how many
    #: there are: the part a fold may shorten for the model (issue #783).
    stored_from: int = 0
    stored_entries: int = 0


def _plan_turn(request: HttpRequest, turn: PlannedTurn) -> StreamingHttpResponse:
    """Run a turn through the plan → run → narrate engine (issue #590).

    The view grounds the turn (canon, the battle log), hands the engine the
    history the model reads, and relays the engine's events as the frames
    the page already understands: ``status``, keep-alives, ``procedure`` and
    ``gm_roll`` chips, ``token``, then ``done`` or ``error``. Everything the
    turn decides happens in the engine and the executor.
    """
    history = turn.history
    recorder = turn.recorder
    session = turn.session
    # Settled by ground(), inside the stream, once any fold has run: the
    # canon and battle grounding read the history the model will see, as
    # they did when the fold ran before the turn (issue #783).
    grounding_notices: list[str] = []
    known_results: tuple[str, ...] = ()
    latest = ""
    policy = TurnPolicy.for_turn(
        turn.mode,
        is_game_master=is_dm(session.user if session is not None else request.user),
        latest=None,
    )
    # How many stored entries the fold took out of what the model reads; the
    # turn record counts entries of the stored history, which keeps them all.
    folded_away = 0
    # Whether this turn is the one that makes the session: the page has no
    # rail row or menu option for it until this turn's frames bring them.
    creating_session = session is None

    def ground() -> None:
        """File the canon and battle log, and read what the turn answers."""
        nonlocal known_results, latest, policy
        notices: list[str] = []
        if turn.ordinary_turn:
            notices.append(ground_turn_in_canon(history, turn.user_message) or "")
        notices.append(ground_turn_in_battle(history, session) or "")
        grounding_notices.extend(notice for notice in notices if notice)
        latest_index = turn_history.latest_message_index(history)
        if turn.resumed:
            known_results = turn_history.results_filed_after(history, latest_index)
        elif turn.roll_outcome is not None:
            rolls, total = turn.roll_outcome
            faces = ", ".join(str(face) for face in rolls)
            known_results = (
                f"The player rolled {turn.user_message}: [{faces}] = {total}.",
            )
        latest = (
            turn.command_prompt
            if turn.command_prompt is not None
            else str(history[latest_index].get("content", ""))
        )
        policy = TurnPolicy.for_turn(
            turn.mode,
            is_game_master=is_dm(session.user if session is not None else request.user),
            latest=latest,
        )

    def ensure_session() -> GMSession:
        nonlocal session
        if session is None:
            session = GMSession.objects.create(
                user=request.user, name=PLACEHOLDER_SESSION_NAME, mode=turn.mode
            )
            recorder.attach_session(session.pk)
        return session

    # A continued session's first turn writes the last session's recap
    # before anything else (issue #761), so the model reads it on this turn.
    catching_up = recap_pending(session)
    previous = session.continued_from if catching_up and session is not None else None
    prior_count = turn.prior_message_count

    def commit(narration: str) -> int:
        history.append({"role": "assistant", "content": narration})
        return _persist_turns(session, request.user, history, prior_count, turn.mode)

    def engine_request() -> TurnRequest:
        answering = turn_history.latest_message_index(history)
        return TurnRequest(
            policy=policy,
            prefix=messages_for_model(turn_history.model_history(history[:answering])),
            latest=latest,
            commit=commit,
            plan=turn.ordinary_turn,
            known_results=known_results,
        )

    def catch_up():
        """Write and file the carried recap, with keep-alives while it is written.

        Bounded by ``GM_RECAP_BUDGET_SECONDS`` (issue #936): the call is hung
        up at that deadline, a recap cut there is carried as the fold recap
        like any recap the model failed to write (a turn that then fails
        tries the whole one again), and the turn goes ahead on its own clock.
        Nothing here waits on a recap it has given up on.
        """
        nonlocal prior_count
        assert session is not None and previous is not None
        yield _sse({"status": CATCHING_UP_STATUS})
        fallback = (
            CarriedRecap(previous.summary, whole=False) if previous.summary else None
        )
        recap_deadline = time.monotonic() + float(settings.GM_RECAP_BUDGET_SECONDS)
        # write_carried_recap makes one model call, which this one hang-up
        # serves.
        hang_up = HangUp()
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            writing = pool.submit(
                write_carried_recap,
                previous,
                partial(write_session_recap, deadline=recap_deadline, hang_up=hang_up),
                settings.GM_NUM_CTX,
            )
            given_up_at = recap_deadline + CATCH_UP_GRACE_SECONDS
            while True:
                try:
                    recap = writing.result(
                        timeout=min(
                            GM_STREAM_KEEPALIVE_SECONDS,
                            max(given_up_at - time.monotonic(), 0.0),
                        )
                    )
                    break
                except FutureTimeout:
                    if time.monotonic() >= given_up_at:
                        # The call is cut at the deadline; a worker that has
                        # still not let go is not waited on.
                        logger.warning(
                            "Continuation recap for session %s passed the %gs "
                            "budget; carried the fold recap",
                            session.pk,
                            settings.GM_RECAP_BUDGET_SECONDS,
                        )
                        recap = fallback
                        break
                    yield SSE_KEEPALIVE
                except Exception:
                    # A bug rather than a model failure (write_carried_recap
                    # already carries the fold recap when the call fails):
                    # logged, and the fold recap carried as what it is.
                    logger.exception(
                        "Continuation recap failed for session %s", session.pk
                    )
                    recap = fallback
                    break
        finally:
            # A recap given up on above leaves no call holding the model and
            # no thread waiting on it. A page that leaves does not end the
            # call: _RunToCompletion drains this stream to the end, so the
            # recap's deadline is what bounds it; only a stream closed
            # outright (GeneratorExit) is hung up early here.
            hang_up.hang_up()
            pool.shutdown(wait=False, cancel_futures=True)
        if recap is None:
            return
        filed = file_carried_recap(session, recap)
        # Replacing a fallback an earlier try filed leaves the count alone.
        opening = history[1] if len(history) > 1 else {}
        if opening.get("kind") == RECAP_KIND:
            history[1] = filed
        else:
            history.insert(1, filed)
            prior_count += 1

    def fold(
        sink: DatabaseSink,
        started_at: float,
        budget_seconds: float,
        queue: ModelQueueWait,
    ):
        """Fold a long session's older turns, inside the turn (issue #783).

        Run after the turn is claimed and recorded, on the turn's clock, with
        a status line and keep-alives while the recap is written, and filed
        as an event of the turn so ``gm_transcript`` shows it. The recap is
        written off this thread -- it is one model call and touches no rows
        -- and kept under a lock on the session.

        The call is not streamed, so it has no first token to tell its wait
        in the model's line from its writing: like an engine call before its
        first token, every keep-alive looks at the line, and the player is
        told while turns are ahead of this one (issue #64).

        Raises:
            TurnBudgetExceeded: The recap outran the turn's budget; the
                session is left as it was.
        """
        nonlocal prior_count, folded_away
        assert session is not None
        stored_until = turn.stored_from + turn.stored_entries
        stored = history[turn.stored_from : stored_until]
        if fold_needed(session, stored):
            yield _sse({"status": FOLDING_STATUS})
            covered_before = session.summary_covers
            began = time.monotonic()
            # write_fold makes one model call, which this one hang-up serves.
            hang_up = HangUp()
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                # Bounded by the turn's budget, so a recap the turn gives up
                # on stops rather than holding the model (review of #783).
                queue.call_started()
                writing = pool.submit(
                    write_fold,
                    session,
                    stored,
                    deadline=started_at + budget_seconds,
                    hang_up=hang_up,
                )
                while True:
                    try:
                        written, covered = writing.result(
                            timeout=GM_STREAM_KEEPALIVE_SECONDS
                        )
                        break
                    except FutureTimeout:
                        if time.monotonic() - started_at > budget_seconds:
                            raise TurnBudgetExceeded from None
                        yield SSE_KEEPALIVE
                        was_waiting = queue.still_waiting
                        turns_ahead = queue.look()
                        # Said while turns are ahead, and taken back to the
                        # fold's own status once they are not.
                        if turns_ahead or was_waiting:
                            yield _sse(
                                _waiting_frame(
                                    {
                                        "status": FOLDING_STATUS,
                                        "elapsed": int(time.monotonic() - started_at),
                                        TURNS_AHEAD_KEY: turns_ahead,
                                    }
                                )
                            )
            finally:
                # Never wait on a recap the turn has given up on, nor leave
                # its call holding the model: a recap that ended is left
                # alone, and one still running -- a turn closed outright
                # (GeneratorExit) mid-fold -- is hung up at once (#935).
                hang_up.hang_up()
                pool.shutdown(wait=False, cancel_futures=True)
            kept = covered != covered_before and save_fold(session, written, covered)
            sink.record(
                TurnEvent(
                    turn_events.FOLD,
                    {
                        "kept": kept,
                        "covered_before": covered_before,
                        "covered": session.summary_covers,
                        "summary_chars": len(session.summary),
                        "seconds": round(time.monotonic() - began, 1),
                    },
                )
            )
            # A recap that ended at the deadline -- written, or given up on
            # by its own bound -- leaves the turn no time to plan in. Until
            # then it may still have been waiting in the line, so it is
            # served only once it is known to have ended inside the budget.
            if time.monotonic() - started_at > budget_seconds:
                raise TurnBudgetExceeded
            queue.served()
        folded = folded_for_model(session, stored)
        if folded != stored:
            history[turn.stored_from : stored_until] = folded
            folded_away = len(stored) - len(folded)
            prior_count -= folded_away

    def event_stream():
        # The turn's own clock starts after any catch-up, which is not the
        # turn's time to spend (third review of #761).
        if catching_up:
            yield from catch_up()
        budget_seconds = float(settings.GM_STREAM_BUDGET_SECONDS)
        started_at = time.monotonic()
        assert recorder.record_id is not None
        sink = DatabaseSink(recorder.record_id)
        # Queue time counts against the budget (issue #64): see
        # docs/architecture.md, "A turn waiting for the model".
        queue = ModelQueueWait(
            turns_ahead=partial(turns_ahead_of, recorder.record_id),
            clock=time.monotonic,
        )
        ran_rolls: list[dict] = []
        ran_procedures: list[dict] = []
        applied: list[str] = []
        refused: list[str] = []
        # A continued session's first turn has nothing of its own to fold:
        # nobody has played it yet (issue #761).
        if session is not None and not catching_up:
            try:
                yield from fold(sink, started_at, budget_seconds, queue)
            except TurnBudgetExceeded:
                logger.warning(
                    "GM session %s fold passed the %gs budget; told the player",
                    session.pk,
                    budget_seconds,
                )
                too_slow_error = too_slow_message(queue, budget_seconds)
                recorder.finish(GMTurnRecord.OUTCOME_TOO_SLOW, error=too_slow_error)
                yield _sse({"error": too_slow_error})
                return
        ground()
        filed_before_reply = len(history)
        executor = RecordsExecutor(
            policy,
            session,
            ensure_session,
            request.user,
            turn_id=recorder.record_id,
        )
        engine = TurnEngine(
            OllamaModel(name=get_gm_model(), options=get_gm_options()),
            sink,
            executor,
            started_at=started_at,
            budget_seconds=budget_seconds,
            keepalive_seconds=GM_STREAM_KEEPALIVE_SECONDS,
            queue=queue,
        )

        def record_failed_turn(
            outcome: str, error: str, opening: str, kept: list[str]
        ) -> None:
            saved = False
            try:
                filed = history[filed_before_reply:]
                if any(
                    entry.get("kind") in (PROCEDURE_KIND, ROLL_KIND) for entry in filed
                ):
                    history.append(build_interrupted_turn_message(opening))
                    _persist_turns(
                        session,
                        request.user,
                        history,
                        prior_count,
                        turn.mode,
                    )
                    saved = True
            finally:
                recorder.finish(
                    outcome,
                    error=error,
                    notices=[*grounding_notices, *kept],
                    gm_rolls=ran_rolls,
                    procedures=ran_procedures,
                    ledger_applied=applied,
                    ledger_refused=refused,
                    history_end=(
                        _stored_length(history) + folded_away if saved else None
                    ),
                )

        running = engine.run(engine_request())
        try:
            while True:
                try:
                    event = next(running)
                except StopIteration as stop:
                    outcome: TurnOutcome = stop.value
                    break
                if event.kind == turn_events.TOKEN:
                    yield _sse({"token": event.payload["token"]})
                elif event.kind == turn_events.STATUS:
                    yield _sse({"status": event.payload["status"]})
                elif event.kind == turn_events.WAITING:
                    yield SSE_KEEPALIVE
                    yield _sse(_waiting_frame(event.payload))
                elif event.kind == turn_events.ACTION and "frame" in event.payload:
                    frame = event.payload["frame"]
                    ran_procedures.append(frame)
                    (applied if frame.get("ran") else refused).append(
                        str(frame.get("note") or frame.get("error") or "")
                    )
                    history.append(
                        turn_history.procedure_entry(event.payload["result"], frame)
                    )
                    yield _sse(
                        _with_session_id(
                            {"procedure": frame}, session, creating=creating_session
                        )
                    )
                elif event.kind == turn_events.ROLL:
                    frame = dict(event.payload)
                    ran_rolls.append(frame)
                    history.append(turn_history.roll_entry(frame))
                    yield _sse(
                        _with_session_id(
                            {"gm_roll": frame}, session, creating=creating_session
                        )
                    )
        except TurnBudgetExceeded:
            logger.warning(
                "GM plan turn passed its %gs budget after %.1fs; told the player",
                budget_seconds,
                time.monotonic() - started_at,
            )
            kept_notice = cut_short_notice(ran_procedures, ran_rolls)
            kept = [kept_notice] if kept_notice else []
            too_slow_error = too_slow_message(queue, budget_seconds)
            record_failed_turn(
                GMTurnRecord.OUTCOME_TOO_SLOW,
                too_slow_error,
                INTERRUPTED_BY_BUDGET,
                kept,
            )
            too_slow: dict = {"error": too_slow_error}
            if kept:
                too_slow["notices"] = kept
            yield _sse(_with_session_id(too_slow, session, creating=creating_session))
            return
        except Exception:
            logger.exception("GM plan turn failed")
            # The player is told a fixed sentence, never the exception's text,
            # which can carry paths, SQL or the model host (issue #13). The
            # record keeps the same sentence, since ``gm:turn`` hands the
            # record's error to the page that lost the stream; the engine's
            # failed event and the log keep the real reason.
            record_failed_turn(
                GMTurnRecord.OUTCOME_ERROR,
                GM_SERVER_ERROR_MESSAGE,
                INTERRUPTED_BY_CONNECTION,
                [],
            )
            yield _sse({"error": GM_SERVER_ERROR_MESSAGE})
            return

        turn_notices = [
            *grounding_notices,
            *([PLAN_UNREADABLE_NOTICE] if outcome.plan_unreadable else []),
        ]
        done_data: dict = {
            "done": True,
            "full_response": outcome.narration,
            "session_id": outcome.session_id,
        }
        # The rail's entry for a session created by this very turn (#704).
        if outcome.session_id:
            named = GMSession.objects.filter(pk=outcome.session_id).first()
            if named is not None:
                _with_session_id(done_data, named, creating=creating_session)
                # And for one that already existed: the rail's count and the
                # name the first reply gave it, which the page otherwise kept
                # at "New Session · 0 msgs" until a reload (issue #829).
                done_data["message_count"] = named.message_count
                done_data["message_count_label"] = named.message_count_label
        if turn_notices:
            done_data["notices"] = turn_notices
        if ran_rolls:
            done_data["gm_rolls"] = ran_rolls
        if ran_procedures:
            done_data["procedures"] = ran_procedures
        if turn.roll_outcome is not None:
            rolls, total = turn.roll_outcome
            done_data["roll"] = {
                "expression": turn.user_message,
                "rolls": ", ".join(str(face) for face in rolls),
                "total": total,
            }
        # Where the party is, from the record this turn may just have
        # changed (issue #579). Only a session that has recorded no place at
        # all falls back to reading the narration for one.
        finished_map = _finished_turn_map(outcome.session_id, outcome.narration)
        if finished_map is not None:
            done_data["map"] = finished_map
        recorder.finish(
            GMTurnRecord.OUTCOME_OK,
            session_id=outcome.session_id,
            reply=outcome.narration,
            history_end=_stored_length(history) + folded_away,
            gm_rolls=ran_rolls,
            procedures=ran_procedures,
            ledger_applied=applied,
            ledger_refused=refused,
            notices=turn_notices,
        )
        yield _sse(done_data)

    return _turn_response(event_stream(), recorder)


def _sse_response(events) -> StreamingHttpResponse:
    """Wrap an event generator as an unbuffered SSE response."""
    resp = StreamingHttpResponse(events, content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp


class _RunToCompletion:
    """A started turn's frames, with the rest of the turn run if the page leaves.

    Issue #515: an iPhone that locked or lost signal mid-turn closed the
    connection, and the turn died with it -- nothing saved. The WSGI server
    reports a closed connection by calling ``close()`` on the response once
    a write fails (gunicorn's gthread worker does so in a ``finally``), and
    Django hands that call to this object. Rather than closing the turn's
    generator, which would end the turn, ``close()`` pulls it to the end and
    discards the frames nobody is left to read. The turn therefore saves its
    reply, procedures, rolls, ledger and record exactly as a watched one does,
    and procedures and rolls run once, in the one generator that ever ran
    them. The budget is unchanged: it is checked inside the turn, so an
    unwatched turn that overruns ends as a watched one does.

    It runs on the request's own thread, which the turn held anyway while it
    streamed, so it takes no more of a worker than a turn always did. The
    database connection is the request's too, and Django closes it only after
    the response's closers have run.
    """

    def __init__(self, frames):
        self._frames = frames

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._frames)

    def close(self) -> None:
        try:
            for _frame in self._frames:
                pass
        except Exception:
            # Django's response.close() ignores what its closers raise, so the
            # traceback is logged here or nowhere.
            logger.exception("GM turn failed while finishing after its page left")
            raise


def _turn_response(events, recorder: TurnRecorder) -> StreamingHttpResponse:
    """The SSE response of a started turn, which runs to the end (issue #515).

    The ``X-GM-Turn`` header names the turn, so a page that loses the stream
    can ask after it at ``gm:turn`` instead of reporting a failure.
    """
    resp = _sse_response(_RunToCompletion(events))
    if recorder.record_id is not None:
        resp["X-GM-Turn"] = str(recorder.record_id)
    return resp


def _already_answered(
    session: GMSession, user_message: str, current_turn_id: int | None
) -> bool:
    """Whether the session's last turn already answered ``user_message`` (issue #515).

    A page that lost its stream may still offer Retry for a turn the server
    went on to finish. Refused turns are passed over, so a refused Retry does
    not hide the answered turn from the next one.
    """
    last = (
        GMTurnRecord.objects.filter(session=session)
        .exclude(pk=current_turn_id)
        .exclude(outcome=GMTurnRecord.OUTCOME_REJECTED)
        # A fight started since answers no message (issue #836).
        .exclude(command=FIGHT_COMMAND)
        .order_by("-started_at", "-pk")
        .first()
    )
    return (
        last is not None
        and last.outcome == GMTurnRecord.OUTCOME_OK
        and last.user_message == user_message
    )


@login_required
@require_GET
def gm_turn(request: HttpRequest, turn_id: int) -> JsonResponse:
    """Where a turn stands, for a page that lost sight of it (issue #515).

    Returns ``state`` -- ``running``, ``finished`` or ``failed`` -- with the
    session the turn saved into and, for a failed turn, why. Only the turn's
    own player may ask.

    A finished turn's answer also carries what its done frame would have
    (issue #89): the session's name and message count, and where the party
    is, so the page shows the turn in place, as a streamed one is, rather
    than reloading.
    """
    expire_stale_running_turns()
    record = GMTurnRecord.objects.filter(pk=turn_id, user=request.user).first()
    if record is None:
        return _json_error("GM turn not found", status=404)
    if record.outcome == GMTurnRecord.OUTCOME_RUNNING:
        state = "running"
    elif record.outcome == GMTurnRecord.OUTCOME_OK:
        state = "finished"
    else:
        state = "failed"
    answer: dict[str, object] = {
        "state": state,
        "session_id": record.session_id,
        "error": record.error,
    }
    saved_into = (
        GMSession.objects.filter(pk=record.session_id, user=request.user).first()
        if state == "finished" and record.session_id is not None
        else None
    )
    if saved_into is not None:
        answer["session_name"] = saved_into.name
        answer["message_count"] = saved_into.message_count
        answer["message_count_label"] = saved_into.message_count_label
        finished_map = _finished_turn_map(saved_into.pk, record.reply)
        if finished_map is not None:
            answer["map"] = finished_map
    return JsonResponse(answer)


@login_required
@require_POST
def gm_save(request: HttpRequest) -> JsonResponse:
    """Rename a GM session.

    An empty name is refused rather than quietly ignored (issue #339,
    item 10): the chat page's name field auto-saves on blur, so "ignored"
    would leave an empty box on screen over a session that still had its
    old name, which is the field lying about what is stored.
    """
    data, error_response = _parse_json_body(request)
    if error_response:
        return error_response
    session_id = data.get("session_id")
    name = data.get("name", "")
    if not isinstance(name, str):
        return _json_error("name must be a string")
    name = name.strip()

    if not session_id:
        return _json_error("No session_id")
    if not name:
        return _json_error("A session needs a name")
    refusal = too_long("Name", name, SESSION_NAME_MAX_STORED)
    if refusal:
        return _json_error(refusal)

    session, err = _get_gm_session(session_id, request.user)
    if err:
        return err
    assert session is not None
    session.name = name
    session.save(update_fields=["name", "updated_at"])

    return JsonResponse({"ok": True, "name": session.name})


@login_required
@require_POST
def gm_mode(request: HttpRequest) -> HttpResponse:
    """Change how the Game Master behaves in this session (issue #372).

    The control on the chat page posts an ordinary form here: ``mode``, and
    ``session_id`` when there is a session yet. A plain form post (the page
    without JavaScript) is answered with a redirect back to the chat page,
    rendered from the stored mode. The page's script posts the same form
    (``common.helpers.wants_json``) and is answered with the mode as stored,
    ``{"ok": true, "mode": ...}``, which it paints the page from in place
    (issue #89): a reload threw away the earlier turns the reader had loaded.
    Either way there is no banner to dismiss and nothing on screen that a
    reload could contradict.

    Anyone may change their own session's mode. It confers nothing: the
    Game Master's authority is read off the account on every turn and is
    untouched by this endpoint (issue #352).

    An unrecognised mode is normalised to this account's default rather than
    refused; the only failure this reports is a session that is not the
    requesting user's, which ``_get_gm_session`` already answers.
    """
    mode = mode_for_user(request.POST.get("mode"), request.user)
    session_id = request.POST.get("session_id")
    chat_url = reverse("gm:chat")
    scripted = wants_json(request)
    if not session_id:
        # No session exists yet, so there is no row to write the choice on.
        # It rides in the query string until the first message creates one,
        # which is state the page renders from and a reload preserves.
        if scripted:
            return JsonResponse({"ok": True, "mode": mode})
        return redirect(f"{chat_url}?mode={mode}")

    session, error = _get_gm_session(session_id, request.user)
    if error:
        return error if scripted else redirect(chat_url)
    assert session is not None
    # Keeps the conversation and files a hidden note at the seam; see
    # GMSession.switch_mode for why the history is not thrown away.
    session.switch_mode(mode)
    if scripted:
        return JsonResponse({"ok": True, "mode": session.mode})
    return redirect(f"{chat_url}?session={session.pk}")


@login_required
@require_POST
@rate_limit("gm_continue", max_requests=10, window_seconds=60)
def gm_continue(request: HttpRequest) -> HttpResponseRedirect:
    """Open a new session that continues one of the owner's campaigns (#761).

    The control on the new-session page posts an ordinary form here naming
    the session it offered. The records are copied by
    :func:`gm.turn_engine.state.continue_campaign`, and the answer is a
    redirect to the new session, so what the reader sees is the page
    rendered from the copied rows: the Campaign tab listing them, and a line
    saying which session this one continues.

    Continuing is the owner's own business and sets no value a rule governs
    -- every copied row was written by a plan action under the policy of the
    session it came from -- so a player and a Game Master may each continue
    their own sessions and nobody may continue another's.

    A second press, or a reload of the form, lands on the continuation the
    first one opened while it is still unplayed and the source has not been
    played since, rather than opening another. The copy runs with the source
    row locked, so a double click cannot open two. No model is called here:
    the recap is written on the new session's first turn, which streams with
    keep-alives, where a form post would sit silent past the proxy's timeout
    (second review of #761).
    """
    chat_url = reverse("gm:chat")
    previous, error = _get_gm_session(request.POST.get("session_id"), request.user)
    if error:
        return redirect(chat_url)
    assert previous is not None
    waiting = _unplayed_continuation(previous)
    if waiting is not None:
        return redirect(f"{chat_url}?session={waiting.pk}")
    with transaction.atomic():
        locked = GMSession.objects.select_for_update().get(pk=previous.pk)
        waiting = _unplayed_continuation(locked)
        session = waiting or continue_campaign(locked, None)
    return redirect(f"{chat_url}?session={session.pk}")


def _unplayed_continuation(previous: GMSession) -> GMSession | None:
    """A continuation of ``previous`` nobody has played, if it is still current.

    Current means opened after ``previous`` was last played: a continuation
    left from before is stale, since its copies miss what happened since.
    """
    for waiting in previous.continuations.filter(
        user=previous.user, created_at__gte=previous.updated_at
    ).order_by("-created_at"):
        if not any(is_played_entry(entry) for entry in waiting.history):
            return waiting
    return None


@login_required
@require_POST
def gm_delete(request: HttpRequest) -> JsonResponse:
    """Delete a GM session."""
    data, error_response = _parse_json_body(request)
    if error_response:
        return error_response
    session_id = data.get("session_id")

    if not session_id:
        return _json_error("No session_id")

    session, err = _get_gm_session(session_id, request.user)
    if err:
        return err
    assert session is not None
    session.delete()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def gm_characters_attach(request: HttpRequest) -> JsonResponse:
    """Attach a character to a GM session (issue #341).

    Body: ``{"session_id": <id or null>, "character_id": <id>}``. With no
    ``session_id`` a session is created first, so a player can pick their
    characters before the first message; the answer carries the session id
    either way, plus the attached characters after the change. Attaching an
    already-attached character is a no-op that still answers ``ok``.
    """
    data, error_response = _parse_json_body(request)
    if error_response:
        return error_response
    character, error = _get_attachable_character(request, data.get("character_id"))
    if error:
        return error
    assert character is not None

    session_id = data.get("session_id")
    if session_id:
        session, error = _get_gm_session(session_id, request.user)
        if error:
            return error
        assert session is not None
    else:
        session = GMSession.objects.create(
            name=PLACEHOLDER_SESSION_NAME,
            user=request.user,
            mode=default_mode_for_user(request.user),
        )
    session.characters.add(character)
    return JsonResponse(_session_characters_payload(session))


def _own_session_or_none(request: HttpRequest, raw_id: str) -> GMSession | None:
    """The asker's session named by ``raw_id``; None for none; 404 otherwise."""
    if not raw_id:
        return None
    if not raw_id.isdigit():
        raise Http404("No GMSession matches the given query.")
    return get_object_or_404(GMSession, pk=int(raw_id), user=request.user)


@login_required
@require_GET
def gm_session_picker(request: HttpRequest, view: str) -> HttpResponse:
    """One page of the session rail or its menu (issue #864), for picker.js.

    The asker's own sessions only. ``?active=`` names the open session,
    which stays listed and marked.
    """
    if view not in SESSION_PICKER_SOURCES:
        raise Http404("No such picker")
    active_session = _own_session_or_none(request, request.GET.get("active", ""))
    query, page_number = asked_for(request)
    picker = session_picker(
        request.user,
        view,
        active_session=active_session,
        query=query,
        page_number=page_number,
    )
    return picker_body_response(request, picker, {"active_session": active_session})


@login_required
@require_GET
def gm_play_session_picker(request: HttpRequest) -> HttpResponse:
    """One page of the sheet's "Take <name> to" session picker (issue #973).

    Guarded as ``gm_characters_attach``, the endpoint the picker's form posts
    to: ``?character=`` must be a character the asker may attach
    (``_get_attachable_character``, the same refusal and status), and the
    sessions are the asker's own.
    """
    character, error = _get_attachable_character(
        request, request.GET.get("character", "")
    )
    if error:
        return error
    assert character is not None
    query, page_number = asked_for(request)
    picker = play_session_picker(
        request.user, character, query=query, page_number=page_number
    )
    return picker_body_response(
        request,
        picker,
        {
            "character": character,
            "holding": sessions_holding_character(request.user, character),
        },
    )


@login_required
@require_GET
def gm_attach_picker(request: HttpRequest) -> HttpResponse:
    """One page of the Characters tab's attach picker (issue #864).

    What ``gm_characters_attach`` would take from the asker
    (``attachable_characters``), less what ``?session=`` (the asker's own)
    already holds.
    """
    session = _own_session_or_none(request, request.GET.get("session", ""))
    attached = list(session.attached_characters()) if session else []
    query, page_number = asked_for(request)
    picker = attachable_picker(
        request.user, session, attached, query=query, page_number=page_number
    )
    groups = _attachable_groups(request.user, picker.rows)
    return picker_body_response(
        request,
        picker,
        {
            "attachable_groups": groups,
            "attachable_characters": [
                character for group in groups for character in group.characters
            ],
        },
    )


@login_required
@require_GET
def gm_characters_panel(request: HttpRequest, session_id: int) -> HttpResponse:
    """The Characters tab's contents for one of the user's sessions (issue #571).

    The page refetches this while a turn runs -- when a procedure frame or the
    done frame arrives -- so a character the GM just started or changed shows
    in the pane as it happens. The same template and context as the page
    render, so the fragment cannot drift from a reload.
    """
    session = get_object_or_404(GMSession, pk=session_id, user=request.user)
    return render(
        request,
        "gm/_panel_characters_body.html",
        _characters_panel_context(request.user, session),
    )


@login_required
@require_GET
def gm_battle_panel(request: HttpRequest, session_id: int) -> HttpResponse:
    """The Battle tab for one of the user's sessions (issue #89).

    The page asks for it when the board changes under it -- a fight started
    from the page, a restart made elsewhere, a ruling posted from the tab --
    and puts it in place of the tab it shows, so the rest of the page, the
    earlier turns the reader loaded among it, stays as it is. The same
    template and context as the page render, so the fragment cannot drift
    from a reload.
    """
    session = get_object_or_404(GMSession, pk=session_id, user=request.user)
    return render(
        request,
        "gm/_panel_battle.html",
        {"active_session": session, **_battle_panel_context(request.user, session)},
    )


@login_required
def gm_campaign_panel(request: HttpRequest, session_id: int) -> HttpResponse:
    """The Campaign tab's contents for one session (issue #579).

    Refetched while a turn runs, like the Characters tab, so a thing the GM
    just moved or an NPC it just met shows in the pane as it happens. The
    same template and context as the page render, so the fragment cannot
    drift from a reload.

    ``?resolved_from=<thread id>`` is the first resolved thread of the earliest
    page the reader has loaded (issue #982): the tab then lists every resolved
    thread from it on, rather than the latest page alone, so the refetch does
    not close the pages they opened. It is a view preference, not a rule, so
    any reader of the page may send it; a thread that is not this session's
    is ignored, as no cursor, and a value that is not a thread id is refused.
    """
    session = get_object_or_404(GMSession, pk=session_id, user=request.user)
    resolved_since = None
    if "resolved_from" in request.GET:
        try:
            resolved_from = database_integer(request.GET["resolved_from"])
        except ValueError:
            return HttpResponseBadRequest("resolved_from must be the id of a thread")
        resolved_since = StoryThread.objects.filter(
            session=session, pk=resolved_from
        ).first()
    return render(
        request,
        "gm/_panel_campaign_body.html",
        _campaign_panel_context(request.user, session, resolved_since),
    )


@login_required
@require_POST
def gm_characters_detach(request: HttpRequest) -> JsonResponse:
    """Detach a character from a GM session (issue #341).

    Body: ``{"session_id": <id>, "character_id": <id>}``. Only the link is
    checked -- a character that has died or been deleted since it was
    attached can still be detached -- and detaching one that is not attached
    is a no-op that still answers ``ok``.
    """
    data, error_response = _parse_json_body(request)
    if error_response:
        return error_response
    session_id = data.get("session_id")
    if not session_id:
        return _json_error("No session_id")
    session, error = _get_gm_session(session_id, request.user)
    if error:
        return error
    assert session is not None
    try:
        character = session.characters.filter(pk=data.get("character_id")).first()
    except (ValueError, TypeError, OverflowError):
        return _json_error("Invalid character_id")
    if character is not None:
        session.characters.remove(character)
    return JsonResponse(_session_characters_payload(session))


@login_required
@require_POST
@rate_limit("gm_fight", max_requests=10, window_seconds=60)
def gm_fight(request: HttpRequest) -> JsonResponse:
    """Put the session's fight on the hex board (issue #345).

    Body: ``{"session_id": <id>, "foes": "<optional text>"}``. The attached
    living characters fight for the player; ``foes`` names the other side
    (``"two gray wolves, a dire wolf"``) or, left empty, the model is asked
    who the party faces. Answers with the new battle's id and the two
    rosters; the page reloads on the session and the Battle tab shows the
    board. Every way the fight cannot start is a 400 in the player's words.
    """
    data, error_response = _parse_json_body(request)
    if error_response:
        return error_response
    session_id = data.get("session_id")
    if not session_id:
        return _json_error("Attach a character or send a message first")
    session, error = _get_gm_session(session_id, request.user)
    if error:
        return error
    assert session is not None
    foes_text = data.get("foes", "")
    if not isinstance(foes_text, str):
        return _json_error("foes must be a string")
    refusal = too_long("Foes", foes_text, MAX_GM_MESSAGE_CHARS)
    if refusal:
        return _json_error(refusal)
    # The fight takes the session's one-turn claim like a message does
    # (issues #515, #836). Started while a turn was running, its opening
    # lines landed in the history and the turn then saved a reply written
    # without knowing the fight had begun; and a turn sent while the model
    # was still naming the foes would do the same the other way round.
    recorder = TurnRecorder(
        user=request.user,
        session_id=session.pk,
        command=FIGHT_COMMAND,
        mode=session.mode,
        is_game_master=is_dm(request.user),
        user_message=f"/fight {foes_text.strip()}".strip(),
        history_start=len(session.history),
        # The foe call sends the session's history and no system prompt.
        system_prompt="",
        model=get_gm_model(),
        options=get_gm_options(),
    )
    already_running = claim_turn(recorder)
    if already_running is not None:
        recorder.finish(GMTurnRecord.OUTCOME_REJECTED, error=FIGHT_WHILE_TURN_RUNNING)
        return JsonResponse(
            {"error": FIGHT_WHILE_TURN_RUNNING, "running_turn": already_running.pk},
            status=409,
        )
    try:
        # One deadline for every foe call, the fallback and the second ask
        # included (issue #936). /fight is a plain POST that sends nothing
        # until it answers, so its budget sits below nginx's 60 s
        # proxy_read_timeout, where an SSE turn's keep-alives need not.
        started = start_fight(
            session,
            foes_text=foes_text,
            stop_at=time.monotonic() + float(settings.GM_FIGHT_BUDGET_SECONDS),
        )
    except FightError as fight_error:
        recorder.finish(GMTurnRecord.OUTCOME_REJECTED, error=str(fight_error))
        return _json_error(str(fight_error))
    except httpx.HTTPError as model_error:
        # The model could not be asked who the party faces -- down, or
        # answering with an error. A player's /fight always asks it (#841),
        # so this is the player's to read in words, not a 500.
        logger.warning("GM fight: the foe call failed: %s", model_error)
        # The record says what the player was told: ``gm:turn`` reads it back
        # to the player, and the model error names the model host (#13).
        recorder.finish(GMTurnRecord.OUTCOME_ERROR, error=FIGHT_MODEL_UNREACHABLE)
        return _json_error(FIGHT_MODEL_UNREACHABLE, status=503)
    except StreamDeadlinePassed as out_of_time:
        logger.warning("GM fight: the foe call passed its deadline: %s", out_of_time)
        recorder.finish(GMTurnRecord.OUTCOME_TOO_SLOW, error=str(out_of_time))
        # When the foe call ran out of its deadline (#936); worded not to
        # invite a resend into the model's line (issue #64).
        return _json_error(settings.GM_FIGHT_TOO_SLOW_MESSAGE.format(), status=504)
    except Exception:
        # Django logs the traceback of what is raised; the record, which
        # ``gm:turn`` reads back to the player, gets the fixed sentence (#13).
        recorder.finish(GMTurnRecord.OUTCOME_ERROR, error=GM_SERVER_ERROR_MESSAGE)
        raise
    stored = session.history
    recorder.finish(
        GMTurnRecord.OUTCOME_OK,
        reply=str(stored[-1].get("content", "")) if stored else "",
        history_end=len(stored),
    )
    return JsonResponse(
        {
            "ok": True,
            "session_id": session.pk,
            "battle_id": started.battle.pk,
            "battle_name": started.battle.name,
            "party": [character.name for character in started.party],
            "foes": [{"name": foe.name, "count": foe.count} for foe in started.foes],
            "dropped": started.dropped,
        }
    )


@login_required
@require_GET
def gm_transcript(request: HttpRequest, session_id: int) -> HttpResponse:
    """Download one of the requesting user's sessions whole (issue #389).

    Plain text by default, for reading and pasting into a bug report;
    ``?format=json`` for tools. Only the session's owner can download it: a
    transcript carries every hidden entry, including the system prompt.
    """
    session = get_object_or_404(GMSession, pk=session_id, user=request.user)
    if request.GET.get("format") == "json":
        body = json.dumps(
            transcript_payload(session), indent=2, ensure_ascii=False, default=str
        )
        response = HttpResponse(body, content_type="application/json; charset=utf-8")
        extension = "json"
    else:
        response = HttpResponse(
            render_transcript_text(session), content_type="text/plain; charset=utf-8"
        )
        extension = "txt"
    filename = transcript_filename(session, extension)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# A reviewer reads the note as guidance, so it stays a sentence or two.
MAX_REVIEW_NOTE_CHARS = 2000


@login_required
@require_POST
@rate_limit("gm_review", max_requests=20, window_seconds=3600)
def gm_review_create(request: HttpRequest) -> JsonResponse:
    """Send one of the Game Master's sessions to Claude for review (issue #402).

    The session is frozen into the request now -- the same payload the
    transcript download carries -- so the review reads what the Game Master
    was looking at, however play carries on. Game Masters and admins only:
    a review runs a model on the LLM host, and reads every hidden entry.
    """
    if not is_dm(request.user):
        return _json_error(
            "Only a Game Master can send a session to Claude.", status=403
        )
    data, error_response = _parse_json_body(request)
    if error_response:
        return error_response
    session, error = _get_gm_session(data.get("session_id"), request.user)
    if error:
        return error
    assert session is not None
    note = data.get("note", "")
    if not isinstance(note, str):
        return _json_error("note must be a string")
    review = GMReviewRequest.objects.create(
        session=session,
        requested_by=request.user,
        note=note.strip()[:MAX_REVIEW_NOTE_CHARS],
        snapshot=transcript_payload(session),
    )
    return JsonResponse(
        {
            "ok": True,
            "review_id": review.pk,
            "url": reverse("gm:review", args=[review.pk]),
        }
    )


@login_required
@require_GET
def gm_review_detail(request: HttpRequest, review_id: int) -> HttpResponse:
    """One review: what was asked, where it stands, and Claude's findings (issue #402).

    Rendered from the stored review, so reloading is how a waiting page learns
    the findings arrived -- nothing polls and nothing appears on a timer. The
    findings are model-written markdown and go through the reference pages'
    sanitising renderer.
    """
    review = get_object_or_404(GMReviewRequest, pk=review_id, requested_by=request.user)
    other_reviews = (
        GMReviewRequest.objects.filter(
            session=review.session, requested_by=request.user
        ).exclude(pk=review.pk)
        if review.session_id
        else GMReviewRequest.objects.none()
    )
    return render(
        request,
        "gm/review.html",
        {
            "review": review,
            "findings_html": render_reference_markdown(review.findings)
            if review.findings
            else "",
            "other_reviews": other_reviews,
        },
    )


# The reviews list reads a screenful at a time; a Game Master sends a handful.
REVIEWS_PER_PAGE = 25


class GMReviewListView(BreadcrumbMixin, ActivePageMixin, LoginRequiredMixin, ListView):
    """Every review this Game Master has sent, and where each stands (issue #572).

    A GET lists them, newest first, filtered by ``?status=`` (see
    :data:`GMReviewRequest.FILTER_CHOICES`) so a filtered view is a link that
    can be shared. A POST to the same address sends a session for review
    (:func:`gm_review_create`), as it did before the list existed.

    Access is the review pages' rule: Game Masters only, as for sending one,
    and only the reviews they sent, as for reading one -- so every row's link
    opens. Rendered from the database; while a listed review is still open,
    ``gm-reviews.js`` polls :func:`gm_review_statuses` and reloads the page
    when one changes.
    """

    template_name = "gm/review_list.html"
    context_object_name = "reviews"
    paginate_by = REVIEWS_PER_PAGE
    login_url = "login"
    active_page = "gm"
    breadcrumb_label = "Claude's reviews"

    def get(self, request, *args, **kwargs):
        if not is_dm(request.user):
            raise PermissionDenied("Only a Game Master can see Claude's reviews.")
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return gm_review_create(request)

    def status_filter(self) -> str:
        choice = self.request.GET.get("status", GMReviewRequest.FILTER_ALL)
        if choice not in GMReviewRequest.FILTER_CHOICES:
            return GMReviewRequest.FILTER_ALL
        return choice

    def get_queryset(self):
        return (
            GMReviewRequest.filtered(self.status_filter())
            .filter(requested_by=self.request.user)
            .select_related("session", "requested_by")
            .defer("snapshot", "findings")
        )

    def get_breadcrumb(self, context):
        return trail(step("Game Master", reverse("gm:chat")), step("Claude's reviews"))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["preserved_query"] = query_without(self.request, "page")
        context["status_filter"] = self.status_filter()
        context["filter_choices"] = [
            (value, label)
            for value, (label, _statuses) in GMReviewRequest.FILTER_CHOICES.items()
        ]
        context["open_review_ids"] = [
            review.pk for review in context["reviews"] if review.is_open
        ]
        return context


@login_required
@require_GET
def gm_review_statuses(request: HttpRequest) -> JsonResponse:
    """Where each of ``?ids=1,2,3`` stands, for the reviews list's poll (issue #572).

    Only the caller's own reviews answer; an id that is not theirs, or not a
    number, is left out, and the list page reloads when what it gets differs
    from what it rendered.
    """
    if not is_dm(request.user):
        return _json_error("Only a Game Master can see Claude's reviews.", status=403)
    review_ids = []
    for part in request.GET.get("ids", "").split(","):
        # isdigit() let "²" through to int(), and a number past SQLite's
        # integer raised out of pk__in (#767): both are left out.
        try:
            review_ids.append(database_integer(part.strip()))
        except ValueError:
            continue
    statuses = dict(
        GMReviewRequest.objects.filter(
            pk__in=review_ids, requested_by=request.user
        ).values_list("pk", "status")
    )
    return JsonResponse(
        {"reviews": {str(review_id): value for review_id, value in statuses.items()}}
    )
