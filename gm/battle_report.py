"""Following a hex-board fight from the GM session (issue #345).

Once ``/fight`` has put a battle on the board (:mod:`gm.fight`), the board
is played through the battle app's own endpoints -- advance, act, restart
-- which know nothing about the chat. What the model needs from it is the
log: every roll and outcome, so it narrates the fight that actually
happened rather than one it imagined. This module carries that log across.

It travels the way the canon excerpts (#331) and character sheets (#341)
do: as hidden ``kind: "battle"`` history entries, filed in front of the
player's message on their next turn, stripped to ``role``/``content`` on
the wire, skipped by every reader-facing view, and only ever *added* so
the model-visible prefix stays stable and Ollama's prompt cache holds.
One entry per completed turn of the battle, and -- once the battle is over
-- one closing summary saying who fell and what every pool has left.

Each entry remembers how far along the battle's event log it reached
(``battle_sequence``, plus a mark of the last line filed), so the next turn
files only what is new. A restart clears the battle's log and numbers it
afresh; the mark catches that, and the model is told the earlier entries
are void before the replayed turns follow.

Nothing here writes to a character. The board writes each wound and death
to the character's record itself, the moment the turn that dealt it
commits (``battle.services.fight_reports``, issues #750 and #751), so the
record never waits on the player's next message: a restart or a detach in
between used to leave a character dead on the board alive on their sheet.
The model reads the pools the record holds from the session's character
list, as it reads every other figure; this module only tells it the story.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import groupby

from django.db import transaction

from battle import profiles
from battle.models import Battle, BattleEvent

from .models import GMSession
from .retrieval import (
    BATTLE_KIND,
    FIGHT_VERDICT_KIND,
    ChatMessage,
    is_battle_message,
)

logger = logging.getLogger(__name__)

LOG_PREAMBLE = "Battle log"
SUMMARY_PREAMBLE = "Battle over"
RESTART_TEXT = (
    "Battle — the fight on the board was restarted from turn 0. Every "
    "earlier 'Battle log' entry for it is void; the replayed turns follow."
)

#: The statuses in which a battle has nothing more to file after its summary.
TERMINAL_STATUSES = frozenset({Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE})

# A mark is a content hash, sixteen hex digits like the sheet hash.
MARK_LENGTH = 16


@dataclass(frozen=True)
class FiledPosition:
    """How far a battle's log has been filed into a session.

    Attributes:
        sequence: The battle sequence the last entry filed reached.
        mark: The mark of the log line it reached ("" for none).
        ended: Whether the last entry filed is a closing summary: the run
            of the fight the model last heard about is over. A restart's
            entries follow an ending, so an ending filed before them does
            not count (issue #897).
    """

    sequence: int
    mark: str
    ended: bool


def event_mark(event: BattleEvent) -> str:
    """A hash identifying one log line, to notice when a restart replaced it."""
    return hashlib.sha256(f"{event.turn}:{event.message}".encode()).hexdigest()[
        :MARK_LENGTH
    ]


def battle_entry(
    content: str,
    *,
    battle: Battle,
    sequence: int,
    turn: int,
    mark: str = "",
    ended: bool = False,
) -> ChatMessage:
    """Build one hidden battle entry. ``user`` role, as the other hidden kinds."""
    entry: ChatMessage = {
        "role": "user",
        "content": content,
        "kind": BATTLE_KIND,
        "battle_id": battle.pk,
        "battle_sequence": sequence,
        "battle_turn": turn,
        "battle_mark": mark,
    }
    if ended:
        entry["battle_ended"] = True
    return entry


def filed_position(
    history: Sequence[ChatMessage], battle_id: int
) -> FiledPosition | None:
    """Where the session's entries for ``battle_id`` reach, or ``None`` if none."""
    last: ChatMessage | None = None
    for message in history:
        if is_battle_message(message) and message.get("battle_id") == battle_id:
            last = message
    if last is None:
        return None
    return FiledPosition(
        sequence=int(last.get("battle_sequence", 0)),
        mark=str(last.get("battle_mark", "")),
        ended=bool(last.get("battle_ended")),
    )


def restarted_since(battle: Battle, position: FiledPosition) -> bool:
    """True when the line last filed is no longer in the battle's log.

    A restart deletes the log and numbers the replay from one again, so the
    event at the filed sequence is either gone or a different line.
    """
    if not position.mark:
        return False
    event = battle.events.filter(sequence=position.sequence).first()
    return event is None or event_mark(event) != position.mark


def event_lines(event: BattleEvent) -> list[str]:
    """One event's lines as the model reads them.

    The event's own message, and beneath a decision that withheld casts past
    the caster's Channel (issue #960) each reason, in the engine's words, as
    the battle log prints them (``battle/_event.html``). The casting success
    tier, the Control Roll and the debt roll (tarmar-engine #28) are events
    of their own, narrated by the engine with the labels the canon's rules
    give them.

    Args:
        event: A battle log event.
    """
    withheld = (event.payload or {}).get("withheld", [])
    return [f"- {event.message}", *(f"  - {reason}" for reason in withheld)]


def turn_block(battle: Battle, turn: int, events: Sequence[BattleEvent]) -> str:
    """One turn's log lines as the model reads them."""
    lines = "\n".join(line for event in events for line in event_lines(event))
    return f"{LOG_PREAMBLE} — {battle.name}, turn {turn}:\n{lines}"


def _pool_text(pool: dict) -> str:
    return f"{str(pool['label']).capitalize()} {pool['current']}/{pool['maximum']}"


def combatant_line(combatant: dict) -> str:
    """``name: state; pools`` for one figure of the closing summary."""
    if not combatant["alive"]:
        state = "dead"
    elif not combatant["conscious"]:
        state = "unconscious"
    else:
        state = "standing"
    pools = ", ".join(_pool_text(pool) for pool in combatant["pools"])
    wounds = sum(
        max(0, int(pool["maximum"]) - int(pool["current"]))
        for pool in combatant["pools"]
        if pool["key"] == "body"
    )
    wounded = f" (wounds: {wounds} body)" if wounds else ""
    return f"{combatant['name']}: {state}; {pools}{wounded}"


#: The engine's victory line for a team: "<team> stands victorious — ...".
VICTORY_SUFFIX = " stands victorious"


def _session_team_phrases() -> dict[str, str]:
    """How the verdict names a session fight's two sides (issue #785).

    The engine writes "<team> stands victorious", which reads "Foes stands
    victorious" for the side a session fight calls Foes. Imported here rather
    than at the top: :mod:`gm.fight` imports this module.
    """
    from .fight import FOE_TEAM, PARTY_TEAM

    return {
        FOE_TEAM: "The foes stand victorious",
        PARTY_TEAM: "The party stands victorious",
    }


def closing_line(battle: Battle) -> str:
    """The victory or stalemate line the engine wrote, or a plain fallback.

    A session fight's side is named in words that agree with it (issue
    #785): "The foes stand victorious — ...", not "Foes stands ...". Any
    other team's line is the engine's as written.
    """
    verdict = (
        battle.events.filter(event_type="info")
        .filter(payload__has_any_keys=["winner", "winner_team", "stalemate"])
        .order_by("-sequence")
        .first()
    )
    if verdict is None:
        return f"The battle ended ({battle.get_status_display().lower()})"
    team = str((verdict.payload or {}).get("winner_team") or "")
    phrase = _session_team_phrases().get(team)
    prefix = f"{team}{VICTORY_SUFFIX}"
    if phrase is not None and verdict.message.startswith(prefix):
        return phrase + verdict.message[len(prefix) :]
    return verdict.message


def summary_block(battle: Battle) -> str:
    """The closing summary: the verdict, then every figure by team."""
    view = profiles.view_of(battle)
    lines = [f"{SUMMARY_PREAMBLE} — {battle.name}: {closing_line(battle)}."]
    for team, group in groupby(
        sorted(view["combatants"], key=lambda item: (item["team"], item["name"])),
        key=lambda item: item["team"],
    ):
        lines.append(f"{team or 'Unaligned'}:")
        lines.extend(f"- {combatant_line(combatant)}" for combatant in group)
    lines.append(
        "Deaths and the Fatigue and Body each character has left are already "
        "on their records; the session's character list carries them from here."
    )
    return "\n".join(lines)


#: What a notice for an ending after a restart opens with (review of #785).
AFTER_RESTART = "After the restart, the fight is over"
FIGHT_OVER = "The fight is over"


def fight_verdict(battle: Battle, *, after_restart: bool = False) -> str | None:
    """How a finished session fight ended, for the player (issue #785).

    The verdict, then each of the party's figures as the board left them --
    standing, unconscious or dead, and the pools they have left, which the
    board has already written to their records. ``None`` while the battle is
    still being fought.

    Args:
        battle: The battle, read as it stands.
        after_restart: Whether an earlier ending of this battle was already
            told; the notice then says the fight was played again.
    """
    if battle.status not in TERMINAL_STATUSES:
        return None
    from .fight import PARTY_TEAM

    view = profiles.view_of(battle)
    party = sorted(
        (item for item in view["combatants"] if item["team"] == PARTY_TEAM),
        key=lambda item: item["name"],
    )
    opening = AFTER_RESTART if after_restart else FIGHT_OVER
    lines = [f"{opening}: {closing_line(battle)}."]
    lines.extend(f"{combatant_line(combatant)}." for combatant in party)
    return "\n".join(lines)


def ending_of(battle: Battle) -> int | None:
    """Which ending of ``battle`` this is: the id of its last log line.

    A restart deletes the log, and the replay writes new rows, so each run's
    ending is a row of its own even when it reads the same as the last --
    which a content mark (``battle_mark``) cannot tell apart. ``None`` for a
    battle with no log.
    """
    return (
        battle.events.order_by("-sequence", "-pk").values_list("pk", flat=True).first()
    )


def is_verdict_for(message: ChatMessage, battle_id: int) -> bool:
    """Whether ``message`` is a notice of how ``battle_id`` ended, any run."""
    return (
        message.get("kind") == FIGHT_VERDICT_KIND
        and message.get("battle_id") == battle_id
    )


def notice_key(message: ChatMessage) -> str:
    """One notice among a battle's: ``"<battle id>:<ending>"``, as the page marks it."""
    return f"{message.get('battle_id')}:{message.get('battle_ending')}"


def filed_verdict(session: GMSession) -> ChatMessage | None:
    """The chat's notice of how the session's fight last ended, if filed.

    Read only: nothing here writes (issue #785, review). ``None`` when the
    session has no fight, its fight goes on, or no notice was filed for this
    ending -- a fight that ended before notices were filed has none, and none
    is backfilled.
    """
    battle = session.battle
    if battle is None or battle.status not in TERMINAL_STATUSES:
        return None
    ending = ending_of(battle)
    for message in session.history:
        if (
            is_verdict_for(message, battle.pk)
            and message.get("battle_ending") == ending
        ):
            return message
    return None


def file_fight_verdict(session: GMSession) -> ChatMessage | None:
    """Put the notice of how the session's fight ended into its chat (#785).

    Called when the turn that ends the fight has committed
    (:func:`file_verdicts_for_ended_battle`), so the notice lands where the
    fight ended in the conversation, the moment the board says so. One notice
    per ending (review of #785): a fight the Game Master restarts and plays
    to an end again gets a second, saying so, and the first stays where it
    was. Under a lock on the session, so a turn saving at the same moment
    keeps both. It stays in the chat like any message.

    Returns:
        The notice for this ending, whether filed now or before; ``None``
        when the session has no battle or its battle is still being fought.
    """
    battle = session.battle
    if battle is None or battle.status not in TERMINAL_STATUSES:
        return None
    ending = ending_of(battle)
    with transaction.atomic():
        locked = GMSession.objects.select_for_update().get(pk=session.pk)
        history = locked.history
        earlier = [m for m in history if is_verdict_for(m, battle.pk)]
        for message in earlier:
            if message.get("battle_ending") == ending:
                return message
        text = fight_verdict(battle, after_restart=bool(earlier))
        assert text is not None  # the status was checked above
        notice: ChatMessage = {
            "role": "assistant",
            "kind": FIGHT_VERDICT_KIND,
            "content": text,
            "battle_id": battle.pk,
            "battle_ending": ending,
        }
        locked.history = [*history, notice]
        locked.save(update_fields=["history_json", "updated_at"])
    session.refresh_from_db()
    return notice


def file_verdicts_for_ended_battle(sender: object, battle: Battle, **kwargs) -> None:
    """File the notice into every session whose fight ``battle`` is (#785).

    The receiver of :data:`battle.signals.battle_ended`, sent by the advance
    path once the ending turn has committed. A battle nobody's session holds
    -- one set up on the battle page -- files nothing.

    A failure here is logged with its traceback and goes no further (review
    of #785): the turn has committed and its fight is over on the board and
    on the records, and an advance answering 500 for a notice would tell the
    player the turn failed when it did not. The session then has no notice
    for this ending; the Battle pane still shows how it ended, from the
    battle.
    """
    for session in GMSession.objects.filter(battle=battle):
        try:
            file_fight_verdict(session)
        except Exception:
            logger.exception(
                "Could not file the notice of how battle %s ended into GM "
                "session %s; the turn stands",
                battle.pk,
                session.pk,
            )


def pending_battle_entries(
    history: Sequence[ChatMessage], session: GMSession
) -> list[ChatMessage]:
    """Everything the session's battle has done that the model has not seen.

    One entry per completed turn since the last filed line (after a restart
    notice, when the log was replaced), then the closing summary once the
    battle is over -- filed once for each run of the fight. Empty when there
    is no battle, nothing new, or the summary is already filed and the fight
    has not been restarted since; a Game Master's restart of a finished
    fight files its notice, the replay's turns and the replay's own ending
    (issue #897). The characters' records were written by the board as each
    turn committed.
    """
    battle = session.battle
    if battle is None:
        return []
    position = filed_position(history, battle.pk)
    if (
        position is not None
        and position.ended
        and not restarted_since(battle, position)
    ):
        return []
    entries: list[ChatMessage] = []
    since = 0
    if position is not None:
        if restarted_since(battle, position):
            entries.append(
                battle_entry(RESTART_TEXT, battle=battle, sequence=0, turn=0)
            )
        else:
            since = position.sequence
    events = list(battle.events.filter(sequence__gt=since).order_by("sequence"))
    last_event: BattleEvent | None = None
    for turn, turn_events in groupby(events, key=lambda event: event.turn):
        lines: list[BattleEvent] = list(turn_events)
        closing: BattleEvent = lines[-1]
        last_event = closing
        entries.append(
            battle_entry(
                turn_block(battle, turn, lines),
                battle=battle,
                sequence=closing.sequence,
                turn=turn,
                mark=event_mark(closing),
            )
        )
    if battle.status in TERMINAL_STATUSES:
        if last_event is not None:
            closing_sequence, closing_mark = last_event.sequence, event_mark(last_event)
        else:
            closing_sequence = since
            closing_mark = position.mark if position is not None else ""
        entries.append(
            battle_entry(
                summary_block(battle),
                battle=battle,
                sequence=closing_sequence,
                turn=battle.turn_number,
                mark=closing_mark,
                ended=True,
            )
        )
    return entries
