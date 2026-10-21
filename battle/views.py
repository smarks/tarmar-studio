"""Battle simulator views.

Since issue #250 authenticated players get in beside the DM — to find open
lobbies on the list page, claim and ready a seat in an open battle, and,
once seated, follow the battle and choose their own combatant's turn
actions. The spectator flow completes the picture: every battle is
*readable* by every signed-in user — arena, roster, event log, who the turn
waits on — while what a viewer may *change* is enforced endpoint by
endpoint and never by hiding buttons alone.

Issue #305 takes the last door off: the arena is playable with no account
at all. Two things changed in this module for it, and neither is a
loosening of who may touch what.

First, control moved from *role* to *ownership*. Creation and battle
control (advance, restart, rename, start) used to test ``can_manage_users``
— which, once anyone can start a battle, would have meant nobody could run
the battle they had just made. They now ask ``battle.access.can_control``:
the DM runs everything, and everyone else runs what they made. Nobody
gained a say over anybody else's battle.

Second, identity is an ``Actor`` rather than a ``User``, because half of
the visitors no longer have one. Every ownership comparison goes through
``battle.access`` for a reason worth remembering: ``seat.player_id ==
user.pk`` is ``None == None`` for an anonymous viewer looking at an
unclaimed seat.

What a guest may not do — seat a saved character, import a party, keep a
character — is refused here *and* said on the page, at the control it
applies to, with the way out (``guest_notes`` below). A limit a visitor
discovers by a control quietly not being there is a bug.
"""

import functools
import json
import logging
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from itertools import groupby

from django.contrib.auth.views import redirect_to_login
from django.db import transaction
from django.db.models import BooleanField, Count, Exists, Max, OuterRef, Q, Value
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import capfirst
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import DetailView, ListView

from bestiary.generation import DIFFICULTY_BANDS
from bestiary.models import CATEGORY_CHOICES, Beast
from characters.constants import CHARACTER_NAME_CHARACTERS, too_long
from common.breadcrumbs import BreadcrumbMixin, step, trail
from common.helpers import (
    _json_error,
    _parse_json_body,
    column_length,
    request_theme,
    whole_number,
)
from common.mixins import ActivePageMixin
from common.pagination import PAGE_SIZE, query_without
from common.pickers import asked_for, picker_body_response
from common.seeds import InvalidSeed, parse_seed

from . import agreement, control, magic_bounds, profiles
from .access import (
    Actor,
    actor_of,
    can_control,
    claimed_seat_q,
    commands_foes,
    decides_for,
    deletable_battles,
    deleted_battles,
    detail_visible_ids,
    listable_battles,
    own_drafts,
    readable_battles,
    seat_is_held,
)
from .adaptation import NO_MODELLED_ATTACK_NOTE, beast_attack
from .generation import ARCHETYPES
from .limits import charge_guest_creation, guest_creation_blocker
from .models import SEED_RANGE, Battle, BattleCombatant, BattleDraft, BattleEvent
from .persistence import (
    CLASSIC_PROFILE,
    PROFILE_CHOICES,
    TARMAR_PROFILE,
    storable_profile,
)
from .pickers import SETUP_PICKER_KINDS, claim_picker, setup_picker
from .refusal_words import refusal_words
from .refusals import BattleRefusal
from .services import (
    BARE_HANDED_KEY,
    MAX_COMBATANTS,
    MAX_TEAM_NAME,
    MIN_COMBATANTS,
    StalePools,
    add_draft_entry,
    add_draft_team,
    advance_turn,
    bare_handed_line,
    claim_seat,
    dead_seats,
    held_by_another,
    import_party_into_draft,
    record_player_action,
    release_seat,
    remove_draft_entry,
    remove_draft_team,
    rename_combatant,
    resnapshot_pools,
    restart_battle,
    results_recorded,
    return_seat,
    runs_the_seats,
    seatable_characters,
    set_battle_control_mode,
    set_combatant_control_mode,
    set_entry_team,
    set_hth_agreement,
    set_magic_bounds_lifted,
    set_seat_ready,
    stale_pools,
    start_battle,
    start_draft,
    take_seat,
    withdrawn_from_session,
)
from .wording import game_master_name, name_of, words

#: The setup form's seed bound: the battle's own range (``SEED_RANGE``).
MAX_SEED = SEED_RANGE

#: The battle page's template, named once. ``BattleDetailView`` renders it
#: and so does :func:`_detail_with_error`, which re-renders the page under a
#: refused post (#339, item 2).
BATTLE_DETAIL_TEMPLATE = "battle/battle_detail.html"

#: The spellings a checkbox or a form-posted flag arrives as. A browser sends
#: "on" for a checked box, script tends to send "1" or "true", and all three
#: mean the same thing to every endpoint that reads one.
TRUTHY_FORM_VALUES = frozenset({"1", "true", "on"})


def _form_flag(value) -> bool:
    """Whether a form or JSON field carries a yes.

    Args:
        value: The raw field, of any type; ``None`` and absent both read as
            no.
    """
    return str(value or "").strip().lower() in TRUTHY_FORM_VALUES


def _actor_and_readable_battle(
    request, pk, *, claim: bool = False
) -> tuple[Actor, Battle]:
    """This request's actor and the battle at ``pk``, or raise ``Http404``.

    Every battle endpoint starts here: identify the viewer, then look the
    battle up *through* what that viewer may read, so a battle they may not
    open is indistinguishable from one that does not exist.

    Args:
        request: The request.
        pk: The battle's primary key, from the URL.
        claim: Passed to :func:`battle.access.actor_of` -- mint a guest key
            for a visitor who does not have one yet. Only the endpoints that
            let a guest take an action need it.

    Returns:
        The actor, and the battle they may read.
    """
    actor = actor_of(request, claim=claim)
    return actor, get_object_or_404(readable_battles(actor), pk=pk)


def _control_denied(actor: Actor, battle: Battle) -> JsonResponse | None:
    """The 403 to return when ``actor`` may not run ``battle``, else ``None``.

    The rule is :func:`battle.access.can_control` -- the DM runs everything,
    everyone else runs what they made -- which this does not restate.
    """
    if can_control(actor, battle):
        return None
    return _json_error(refusal_words().views.control_denied, status=403)


def _wants_json(request) -> bool:
    """True for an XHR caller, false for a plain form post.

    The control endpoints serve both: the battle page posts forms (the panel
    renders from stored state, so a reload is the whole update), while the
    same endpoints answer in the battle payload shape for anything driving
    them from script.
    """
    return request.headers.get("X-Requested-With") == "XMLHttpRequest" or (
        request.content_type or ""
    ).startswith("application/json")


logger = logging.getLogger(__name__)


def _internal_errors_logged(
    *, json_only: bool = False
) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    """Keep a service's internal ``ValueError`` text off the page (issue #39).

    A battle view catches :class:`~battle.refusals.BattleRefusal` around its
    service call and shows the player those words. Any other ``ValueError``
    is an internal check failing, and its text is not the player's to read.
    A script caller gets ``refusal_words().views.request_failed`` as JSON, with
    the error logged here with its traceback, as the advance answers (#13).
    A form post gets the error raised on, so Django logs it once with its
    traceback and renders the site's own error page.

    Args:
        json_only: The endpoint answers only JSON, so its error is JSON even
            for a post with no JSON body (a bodyless ``postJson``).

    Returns:
        The decorator.
    """

    def decorate(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @functools.wraps(view)
        def guarded(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            try:
                return view(request, *args, **kwargs)
            except ValueError:
                if not (json_only or _wants_json(request)):
                    raise
                logger.exception("Battle endpoint %s failed", request.path)
                return _json_error(refusal_words().views.request_failed, status=500)

        return guarded

    return decorate


class BattleListView(
    BreadcrumbMixin,
    ActivePageMixin,
    ListView,
):
    """Everyone's way in — now including people with no account (#305).

    Every row carries the action its viewer has a right to: Join an open
    lobby, Play a battle they hold a seat in, Watch anything else. The seat
    annotations feed the lobby rows' "N of M seats claimed" state;
    ``viewer_seated`` picks the verb.

    What a viewer is *offered* here is narrower than what they may open
    (``battle.access.listable_battles`` says why: battle names are free
    text and creation is now an unauthenticated write, so the list is not a
    public wall). A guest sees the battles they made; a link still opens
    anything ``readable_battles`` allows.
    """

    breadcrumb_label = "Battles"

    def get_breadcrumb(self, context):
        # On a classic-arena host this list IS the front page, and a front
        # page shows no trail -- the convention the studio home already
        # follows. The check reads the theme, never the hostname (#269).
        if request_theme(self.request).landing_url_name == "battle:list":
            return []
        return super().get_breadcrumb(context)

    model = Battle
    template_name = "battle/battle_list.html"
    context_object_name = "battles"
    active_page = "battles"
    # Paged like the other lists (#795): every battle ever made, deleted
    # ones aside, is on this list.
    paginate_by = PAGE_SIZE

    #: ``?show=`` values the status filter offers, and the queryset each
    #: names. One table, so the links, the <select> and the filter itself
    #: cannot drift apart (#339, item 5).
    SHOW_ALL = "all"
    SHOW_LOBBY = "lobby"
    SHOW_RUNNING = "running"
    SHOW_OVER = "over"
    SHOW_DELETED = "deleted"
    SHOW_CHOICES = [
        (SHOW_ALL, "All battles"),
        (SHOW_LOBBY, "Open lobbies"),
        (SHOW_RUNNING, "Under way"),
        (SHOW_OVER, "Finished"),
        (SHOW_DELETED, "Deleted"),
    ]
    SHOW_FILTERS = {
        SHOW_LOBBY: Q(status=Battle.STATUS_LOBBY),
        SHOW_RUNNING: Q(
            status__in=[
                Battle.STATUS_SETUP,
                Battle.STATUS_PAUSED,
                Battle.STATUS_AWAITING,
            ]
        ),
        SHOW_OVER: Q(status__in=[Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE]),
    }

    def show_filter(self) -> str:
        """Which ``?show=`` bucket this request asked for, defaulting to all."""
        requested = (self.request.GET.get("show") or "").strip().lower()
        valid = {value for value, _label in self.SHOW_CHOICES}
        return requested if requested in valid else self.SHOW_ALL

    def get_queryset(self):
        actor = actor_of(self.request)
        open_seats = Q(combatants__is_open_seat=True)
        show = self.show_filter()
        # The deleted bucket is a different queryset, not a filter over the
        # live one: a soft-deleted battle is off the list by construction,
        # and only whoever may put it back is shown it.
        battles = (
            deleted_battles(actor)
            if show == self.SHOW_DELETED
            else listable_battles(actor)
        )
        status_filter = self.SHOW_FILTERS.get(show)
        if status_filter is not None:
            battles = battles.filter(status_filter)
        return battles.annotate(
            seat_total=Count("combatants", filter=open_seats),
            seat_claimed=Count(
                "combatants", filter=open_seats & claimed_seat_q("combatants__")
            ),
            viewer_seated=Exists(
                BattleCombatant.objects.filter(
                    actor.seat_filter(), battle=OuterRef("pk")
                )
            ),
            # Whether this viewer runs the row's battle (#339, item 6). The
            # verb column used to ask ``user.can_manage_users`` alone, so a
            # player looking at the battle they made themselves was offered
            # "Watch" — the one verb that is never true of an owner.
            viewer_owns=Exists(deletable_battles(actor).filter(pk=OuterRef("pk")))
            if actor.user is not None or actor.guest_key
            else Value(False, output_field=BooleanField()),
        ).order_by("-created_at", "-pk")
        # Stated, not left to Meta.ordering: the Count annotations group the
        # query, and a grouped query drops the model's default order, so the
        # paged list came back in no order at all -- the newest battle could
        # land on the last page, and a row could repeat or go missing
        # between pages (#795).

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        actor = actor_of(self.request)
        # The status filter (#339, item 5): which bucket is showing, and the
        # links to the others. Part of the page, read back off the URL, so a
        # reload and a shared link land on the same list.
        context["show"] = self.show_filter()
        context["show_choices"] = self.SHOW_CHOICES
        # The pager's links keep the status filter (#795).
        context["list_query"] = query_without(self.request, "page")
        # The list page carries only the way in (#271): a New battle button
        # and any drafts still on the desk — the setup itself has a page of
        # its own. Everyone gets both now, guests included (#305).
        context["drafts"] = own_drafts(actor)
        context["is_guest"] = actor.is_guest
        context["guest_keep_note"] = refusal_words().guest_notes.keep
        # Why the New battle button is disabled, when it is. Rendered from
        # the same sentence the create endpoint refuses on, so the page
        # cannot promise something the server will decline.
        context["creation_blocker"] = (
            guest_creation_blocker(self.request, actor.guest_key)
            if actor.is_guest
            else None
        )
        return context


# ------------------------------------------------------------------- setup


def _own_draft_or_404(request, pk) -> BattleDraft:
    """This actor's draft, or 404 — drafts are private to whoever opened one.

    Ownership, not role (#305): a draft belongs to the account that opened
    it, or to the guest session that did. A DM has no special read here —
    somebody else's half-built muster is nobody's business.
    """
    actor = actor_of(request)
    return get_object_or_404(own_drafts(actor), pk=pk)


#: How long a battle's name may be: its column's own length, which the terms
#: field's maxlength and the refusal below both read (issue #938).
BATTLE_NAME_MAX_LENGTH = column_length(Battle, "name")


def _clean_draft_name(value) -> str:
    name = str(value or "").strip()
    refusal = too_long("Name", name, BATTLE_NAME_MAX_LENGTH)
    if refusal:
        raise BattleRefusal(refusal)
    return name


def _clean_draft_seed(value) -> int | None:
    try:
        seed = parse_seed(value or "")
    except InvalidSeed:
        raise BattleRefusal(refusal_words().views.seed_not_whole_number) from None
    if seed is None:
        return None
    if not 0 <= seed < MAX_SEED:
        raise BattleRefusal(
            refusal_words().views.seed_out_of_range.format(highest=MAX_SEED - 1)
        )
    return seed


def _clean_draft_profile(value) -> str:
    profile = str(value or TARMAR_PROFILE).strip()
    if profile not in dict(PROFILE_CHOICES):
        raise BattleRefusal(refusal_words().views.unknown_rules_profile)
    return profile


def _clean_draft_control_mode(value) -> str:
    """The battle-level control mode a setup form named (#308)."""
    return control.clean_mode(value or control.AUTOMATIC)


#: The draft fields the terms stage edits, with their validators. Each
#: validator returns the value to store, raising BattleRefusal with a
#: user-facing message otherwise. One table serves both writers — the
#: autosave endpoint and the start-time merge — so they cannot drift.
DRAFT_FIELD_CLEANERS = {
    "name": _clean_draft_name,
    "seed": _clean_draft_seed,
    "rules_profile": _clean_draft_profile,
    "control_mode": _clean_draft_control_mode,
    "auto_start": _form_flag,
    # A Game Master's choice that the battle counts (#750, #751); stored for a
    # player too, and ignored when their battle is made (draft_create_kwargs).
    "writes_to_records": _form_flag,
}


def _apply_draft_terms(draft: BattleDraft, data) -> None:
    """Store the terms fields present in ``data`` on the draft.

    Raises:
        BattleRefusal: with the first field's user-facing message.
    """
    changed = []
    for field, cleaner in DRAFT_FIELD_CLEANERS.items():
        if field in data:
            setattr(draft, field, cleaner(data.get(field)))
            changed.append(field)
    if changed:
        # Its own savepoint: a save that finds the row gone raises NotUpdated,
        # and inside a caller's transaction that failed write would otherwise
        # leave the whole transaction unusable (#832).
        with transaction.atomic():
            draft.save(update_fields=[*changed, "updated_at"])


def _beast_muster_note(beast: Beast) -> str:
    """The muster row's one line about a beast: what it is, and how it fights.

    A beast whose catalog entry names no rollable damage is fought with the
    published Bare-Handed Damage table instead, and the muster says so
    (#645). It used to print only the category and the ST, so a DM adding a
    Troll had nothing on screen telling them the figure they were about to
    seat would not hit like the Troll on the catalog page.
    """
    line = f"{beast.get_category_display()} — ST {beast.strength}"
    if beast_attack(beast).substituted:
        return f"{line} — {NO_MODELLED_ATTACK_NOTE}"
    return line


def _muster_rows(draft: BattleDraft, actor: Actor) -> list[dict]:
    """The draft's roster entries dressed for the muster-roll ledger.

    Character entries resolve to live rows *as this user may currently see
    them* — one that died or was deleted since it was added comes back
    flagged ``stale`` so the page can say why the battle will not start.
    """
    roster = draft.roster_json or []
    slugs = [entry.get("slug") for entry in roster if entry.get("kind") == "beast"]
    beasts = {beast.slug: beast for beast in Beast.objects.filter(slug__in=slugs)}
    character_ids = [
        entry.get("character_id")
        for entry in roster
        if entry.get("kind") == "character"
    ]
    seatable = (
        seatable_characters(actor.user).select_related("user").in_bulk(character_ids)
    )
    rows = []
    for index, entry in enumerate(roster):
        kind = entry.get("kind")
        row = {
            "index": index,
            "kind": kind,
            "stale": False,
            "edit_target": None,
            "team": entry.get("team", ""),
        }
        if kind == "fighter":
            row["label"] = entry.get("archetype", "").title()
            row["note"] = "Generated fighter — rolled when the battle starts"
        elif kind == "beast":
            beast = beasts.get(entry.get("slug"))
            row["label"] = beast.name if beast else entry.get("slug", "?")
            row["note"] = (
                _beast_muster_note(beast) if beast else "No longer in the catalog"
            )
            row["stale"] = beast is None
        elif kind == "random_beast":
            row["label"] = "Random beast"
            filters = [
                label
                for label in (
                    dict(CATEGORY_CHOICES).get(entry.get("category", "")),
                    entry.get("difficulty", "").title() or "",
                )
                if label
            ]
            row["note"] = (
                f"Drawn from the catalog — {', '.join(filters)}"
                if filters
                else "Drawn from the whole catalog"
            )
        elif kind == "character":
            character = seatable.get(entry.get("character_id"))
            if character is None:
                row["label"] = "Unavailable character"
                row["note"] = "Died or was deleted since you added it — strike it"
                row["stale"] = True
            else:
                row["label"] = character.name
                owner = (
                    "your character"
                    if actor.user is not None and character.user_id == actor.user.pk
                    else f"{character.user.username}'s character"
                )
                row["note"] = f"Saved {owner} — snapshotted when the battle starts"
                row["edit_target"] = character.pk
        else:
            row["label"] = "Open seat"
            row["note"] = "A player claims it in the lobby"
        rows.append(row)
    return rows


@dataclass(frozen=True)
class CatalogBeastGroup:
    """One ``<optgroup>`` of the muster's beast picker (issue #706).

    Attributes:
        label: The category's display name.
        beasts: Its beasts, alphabetical.
    """

    label: str
    beasts: list[Beast]


def _catalog_beast_groups() -> list[CatalogBeastGroup]:
    """The catalog's beasts grouped by category, categories alphabetical.

    The picker was a single flat ``<select>`` of all 239, so choosing a beast
    you already had in mind meant scrolling past every ant. Grouping is the
    whole fix: a browser's own dropdown lets you jump between optgroups, and
    it costs no JavaScript and no second request (issue #706).

    Returns:
        One group per category that has at least one catalog beast.
    """
    grouped: dict[str, list[Beast]] = {}
    for beast in Beast.objects.filter(is_generated=False).order_by("name"):
        grouped.setdefault(beast.get_category_display(), []).append(beast)
    return [
        CatalogBeastGroup(label=label, beasts=beasts)
        for label, beasts in sorted(grouped.items())
    ]


def _setup_context(
    request,
    draft: BattleDraft,
    error: str | None = None,
    notice: str | None = None,
    error_field: str | None = None,
) -> dict:
    """Everything the setup page renders.

    Args:
        request: The request being answered.
        draft: The battle draft being set up.
        error: A refusal to show, or None.
        notice: A notice to show, or None.
        error_field: The terms field ``error`` belongs beside, when it is
            about one field rather than the page (issue #711). The template
            renders the sentence under that field and focuses it, instead of
            leaving the reader to find an empty box a screen and a half
            further down.

    Returns:
        The template context.
    """
    actor = actor_of(request)
    rows = _muster_rows(draft, actor)
    total = len(rows)
    teams = list(draft.teams_json or [])
    # The muster roll grouped for display (#275): one section per team in
    # ledger order, then the free-for-all pool. With no teams the pool is
    # the whole roll and renders headerless, exactly as before teams.
    muster_groups = [
        {
            "team": team,
            "rows": [row for row in rows if row["team"] == team],
        }
        for team in teams
    ]
    free_rows = [row for row in rows if row["team"] not in teams or not row["team"]]
    blockers = []
    if total < MIN_COMBATANTS:
        blockers.append(
            f"Add at least {MIN_COMBATANTS} to the muster — it holds {total} "
            f"{'entry' if total == 1 else 'entries'} so far"
        )
    if any(row["stale"] for row in rows):
        blockers.append("Strike the unavailable entries first")
    setup_url = reverse("battle:setup", args=[draft.pk])
    return {
        "error_field": error_field,
        # A resumed draft that already has a name is called by it (issue
        # #339, item 27); only an unnamed one is still a "New battle".
        "breadcrumb": trail(
            step("Battles", reverse("battle:list")), step(draft.name or "New battle")
        ),
        "active_page": "battles",
        "draft": draft,
        "muster_rows": rows,
        "teams": teams,
        "muster_groups": muster_groups,
        "free_rows": free_rows,
        # Parties are public objects (#274): every party is offered, and the
        # import itself skips the members this user cannot see. A guest is
        # offered none — a party is a saved team, and saving needs an
        # account (#305) — and is told so rather than shown an empty shelf.
        # A page at a time, newest first, with a search that reaches every
        # page (#864).
        "party_picker": setup_picker(draft, actor, "parties"),
        "is_guest": actor.is_guest,
        # The "This battle counts" choice is a Game Master's (#750, #751).
        "is_dm": actor.is_dm,
        "guest_notes": refusal_words().guest_notes,
        "notice": notice,
        "muster_total": total,
        "min_combatants": MIN_COMBATANTS,
        "max_combatants": MAX_COMBATANTS,
        # The two text inputs' maxlength, the server's own (issue #938).
        "battle_name_max_length": BATTLE_NAME_MAX_LENGTH,
        "team_name_max_length": MAX_TEAM_NAME,
        "muster_full": total >= MAX_COMBATANTS,
        "blockers": blockers,
        "error": error,
        "archetypes": ARCHETYPES,
        "catalog_beast_groups": _catalog_beast_groups(),
        "beast_categories": CATEGORY_CHOICES,
        "difficulty_bands": list(DIFFICULTY_BANDS),
        "rules_profiles": PROFILE_CHOICES,
        # The battle-level control default this muster will be created with
        # (#308); per-combatant modes are set from the battle page.
        "control_modes": control.MODE_CHOICES,
        # The saved-character shelf, a page at a time, newest first, with a
        # search that reaches every page (#864).
        "shelf_picker": setup_picker(draft, actor, "characters"),
        "setup_url": setup_url,
        "max_seed": MAX_SEED - 1,
        "has_open_seats": any(row["kind"] == "open_seat" for row in rows),
    }


SETUP_TEMPLATE = "battle/battle_setup.html"

# Where a setup action leaves the notice it wants shown on the page it
# redirects to (issue #339, item 20). Keyed by draft, so two drafts open in
# one browser never show each other's notices; read once by battle_setup.
SETUP_NOTICE_SESSION_KEY = "battle_setup_notice:{pk}"


@require_POST
def battle_setup_create(request):
    """Open a fresh draft and land the visitor on its setup page.

    Open to everyone since #305, which makes this the app's one
    unauthenticated write. ``battle.limits`` bounds it — a per-IP rate and
    a cap on how many unfinished battles one guest session may hold — and
    refuses with the same sentence the list page renders on the disabled
    button, so the page and the server always agree about why.

    The draft opens on the rules the *door* stands for (#332): the arena
    host proposes classic Melee, every other host proposes Tarmar. Which
    host is which is decided once, in ``THEME_HOSTS``, and read here through
    the theme rather than by parsing the hostname a second time. It is only
    the starting value of the setup page's dropdown -- a DM still picks
    either profile at either door.
    """
    actor = actor_of(request, claim=True)
    if actor.is_guest:
        blocker = guest_creation_blocker(request, actor.guest_key)
        if blocker:
            return _json_error(blocker, status=429)
        charge_guest_creation(request)
    draft = BattleDraft.objects.create(
        created_by=actor.user,
        guest_key=actor.guest_key,
        rules_profile=storable_profile(request_theme(request).default_rules_profile),
    )
    return redirect("battle:setup", pk=draft.pk)


def battle_setup(request, pk):
    """The staged setup page: terms, muster roll, march (#270/#271)."""
    draft = _own_draft_or_404(request, pk)
    notice = request.session.pop(SETUP_NOTICE_SESSION_KEY.format(pk=draft.pk), None)
    return render(
        request, SETUP_TEMPLATE, _setup_context(request, draft, notice=notice)
    )


@require_GET
def battle_setup_picker(request, pk, kind: str) -> HttpResponse:
    """One page of the setup page's shelf or party picker (issue #864).

    What ``picker.js`` asks for as the reader types or turns a page: the
    picker's body for ``?q=`` and ``?page=``, from the same rows the setup
    page offers and its add and import endpoints accept. The draft is the
    asker's own or a 404, as on the page.
    """
    draft = _own_draft_or_404(request, pk)
    if kind not in SETUP_PICKER_KINDS:
        raise Http404("No such picker")
    query, page_number = asked_for(request)
    actor = actor_of(request)
    picker = setup_picker(draft, actor, kind, query=query, page_number=page_number)
    rows = _muster_rows(draft, actor)
    return picker_body_response(
        request,
        picker,
        {
            "draft": draft,
            "setup_url": reverse("battle:setup", args=[draft.pk]),
            "muster_full": len(rows) >= MAX_COMBATANTS,
            "user": request.user,
        },
    )


def _draft_gone_is_404(view):
    """Answer 404 when the draft goes while the request is under way (#832).

    A request that found its draft, then lost it to an overlapping press of
    "Begin the battle" or "Discard", hears what one arriving after it would:
    the draft is not there. The draft services raise ``DoesNotExist`` once
    they reload the row (``battle.services.holds_draft``); saving the terms
    onto a row already deleted raises ``NotUpdated``.
    """

    @functools.wraps(view)
    def guarded(request, pk):
        try:
            return view(request, pk)
        except (BattleDraft.DoesNotExist, BattleDraft.NotUpdated):
            raise Http404(refusal_words().views.draft_gone) from None

    return guarded


@require_POST
@_internal_errors_logged(json_only=True)
def battle_setup_update(request, pk):
    """Autosave one terms field (name, seed, rules profile, auto-start).

    A draft that is not this visitor's, or no longer exists, is a JSON 404
    rather than the HTML one, so the autosave can say why the save failed.
    That includes a draft deleted between loading and saving it (#465).
    """
    try:
        draft = _own_draft_or_404(request, pk)
    except Http404:
        return _json_error(refusal_words().views.draft_gone, status=404)
    data, err = _parse_json_body(request)
    if err:
        return err
    field = data.get("field")
    # A list is unhashable, so ``in`` the dict raised TypeError (#767).
    if not isinstance(field, str) or field not in DRAFT_FIELD_CLEANERS:
        return _json_error(refusal_words().views.unknown_setup_field)
    try:
        _apply_draft_terms(draft, {field: data.get("value")})
    except BattleRefusal as field_error:
        return _json_error(str(field_error))
    except BattleDraft.NotUpdated:
        return _json_error(refusal_words().views.draft_gone, status=404)
    return JsonResponse({"success": True, "value": getattr(draft, field)})


@require_POST
@_draft_gone_is_404
@_internal_errors_logged()
def battle_setup_add(request, pk):
    """Add one muster entry (fighter, beast, random beast, character, seat)."""
    draft = _own_draft_or_404(request, pk)
    character_id_raw = (request.POST.get("character_id") or "").strip()
    character_id = None
    if character_id_raw:
        try:
            character_id = int(character_id_raw)
        except ValueError:
            return render(
                request,
                SETUP_TEMPLATE,
                _setup_context(
                    request, draft, refusal_words().views.numeric_character_id
                ),
                status=400,
            )
    actor = actor_of(request)
    if actor.is_guest and (request.POST.get("kind") or "").strip() == "character":
        return render(
            request,
            SETUP_TEMPLATE,
            _setup_context(
                request, draft, refusal_words().guest_notes.saved_characters
            ),
            status=403,
        )
    try:
        add_draft_entry(
            draft,
            actor.user,
            kind=(request.POST.get("kind") or "").strip(),
            archetype=(request.POST.get("archetype") or "").strip(),
            slug=(request.POST.get("slug") or "").strip(),
            category=(request.POST.get("category") or "").strip(),
            difficulty=(request.POST.get("difficulty") or "").strip(),
            character_id=character_id,
            team=(request.POST.get("team") or "").strip(),
        )
    except BattleRefusal as add_error:
        return render(
            request,
            SETUP_TEMPLATE,
            _setup_context(request, draft, str(add_error)),
            status=400,
        )
    return redirect("battle:setup", pk=draft.pk)


@require_POST
@_draft_gone_is_404
@_internal_errors_logged()
def battle_setup_remove(request, pk):
    """Strike one muster entry, by roster position."""
    draft = _own_draft_or_404(request, pk)
    try:
        index = int(request.POST.get("index") or "")
    except ValueError:
        return render(
            request,
            SETUP_TEMPLATE,
            _setup_context(request, draft, refusal_words().views.numeric_entry_index),
            status=400,
        )
    try:
        remove_draft_entry(draft, index)
    except BattleRefusal as remove_error:
        return render(
            request,
            SETUP_TEMPLATE,
            _setup_context(request, draft, str(remove_error)),
            status=400,
        )
    return redirect("battle:setup", pk=draft.pk)


@require_POST
@_draft_gone_is_404
@_internal_errors_logged()
def battle_setup_import_party(request, pk):
    """Import a party as a new team seeded with its visible members (#275).

    What could not join is said in a notice that stays on the page (house
    rule: nothing transient) — members hidden from this user, dead ones,
    and ones already on the muster are skipped and counted, never smuggled.
    """
    draft = _own_draft_or_404(request, pk)
    actor = actor_of(request)
    if actor.is_guest:
        return render(
            request,
            SETUP_TEMPLATE,
            _setup_context(request, draft, refusal_words().guest_notes.parties),
            status=403,
        )
    try:
        party_id = int(request.POST.get("party_id") or "")
    except ValueError:
        return render(
            request,
            SETUP_TEMPLATE,
            _setup_context(request, draft, refusal_words().views.numeric_party_id),
            status=400,
        )
    try:
        result = import_party_into_draft(draft, actor.user, party_id)
    except BattleRefusal as import_error:
        return render(
            request,
            SETUP_TEMPLATE,
            _setup_context(request, draft, str(import_error)),
            status=400,
        )
    skipped = []
    if result["hidden"]:
        skipped.append(f"{result['hidden']} not visible to you")
    if result["fallen"]:
        skipped.append(f"{result['fallen']} dead or gone")
    if result["already_mustered"]:
        skipped.append(f"{result['already_mustered']} already on the muster")
    notice = (
        f"{result['team']} joins as a team — "
        f"{result['imported']} member{'s' if result['imported'] != 1 else ''} "
        f"mustered"
    )
    if skipped:
        notice += f" ({', '.join(skipped)} — skipped)"
    # Redirect like every other setup action (issue #339, item 20). Rendering
    # the page at this POST address meant a refresh offered to post the
    # import again. The notice rides the session to the page it lands on.
    request.session[SETUP_NOTICE_SESSION_KEY.format(pk=draft.pk)] = notice
    return redirect("battle:setup", pk=draft.pk)


@require_POST
@_draft_gone_is_404
@_internal_errors_logged()
def battle_setup_team_add(request, pk):
    """Name a new, empty team on the draft's ledger."""
    draft = _own_draft_or_404(request, pk)
    try:
        # team_name, not name: the setup page's terms form owns "name" for
        # the battle itself, and the two must not shadow each other.
        add_draft_team(draft, request.POST.get("team_name") or "")
    except BattleRefusal as team_error:
        return render(
            request,
            SETUP_TEMPLATE,
            _setup_context(request, draft, str(team_error)),
            status=400,
        )
    return redirect("battle:setup", pk=draft.pk)


@require_POST
@_draft_gone_is_404
@_internal_errors_logged()
def battle_setup_team_remove(request, pk):
    """Disband one team; its entries return to the free-for-all pool."""
    draft = _own_draft_or_404(request, pk)
    try:
        remove_draft_team(draft, request.POST.get("team_name") or "")
    except BattleRefusal as team_error:
        return render(
            request,
            SETUP_TEMPLATE,
            _setup_context(request, draft, str(team_error)),
            status=400,
        )
    return redirect("battle:setup", pk=draft.pk)


@require_POST
@_draft_gone_is_404
@_internal_errors_logged()
def battle_setup_entry_team(request, pk):
    """Move one muster entry onto a team (or back to the free pool)."""
    draft = _own_draft_or_404(request, pk)
    try:
        index = int(request.POST.get("index") or "")
    except ValueError:
        return render(
            request,
            SETUP_TEMPLATE,
            _setup_context(request, draft, refusal_words().views.numeric_entry_index),
            status=400,
        )
    try:
        set_entry_team(draft, index, (request.POST.get("team") or "").strip())
    except BattleRefusal as move_error:
        return render(
            request,
            SETUP_TEMPLATE,
            _setup_context(request, draft, str(move_error)),
            status=400,
        )
    return redirect("battle:setup", pk=draft.pk)


@require_POST
@_internal_errors_logged()
def battle_setup_start(request, pk):
    """Compile the draft into a battle and go to it.

    The terms inputs belong to this form (the ``form`` attribute), so the
    submitted values are merged into the draft first — a name typed and
    immediately followed by the Begin press is never lost to an autosave
    race, and the flow works before any JavaScript runs.
    """
    draft = _own_draft_or_404(request, pk)
    terms = request.POST.dict()
    # An unchecked checkbox submits nothing; at start time absence IS the
    # value, so make it explicit rather than keeping a stale True.
    terms.setdefault("auto_start", "")
    terms.setdefault("writes_to_records", "")
    try:
        _apply_draft_terms(draft, terms)
        battle = start_draft(draft)
    except (BattleDraft.DoesNotExist, BattleDraft.NotUpdated):
        # The overlapping press (#832): another press of "Begin the battle"
        # started this draft while this one waited on it. That press built
        # the battle; this one goes to the list the battle is on, newest
        # first, rather than to a 404 about a setup that did what was asked.
        return redirect("battle:list")
    except BattleRefusal as start_error:
        return render(
            request,
            SETUP_TEMPLATE,
            _setup_context(
                request,
                draft,
                str(start_error),
                error_field=getattr(start_error, "field", None),
            ),
            status=400,
        )
    except Beast.DoesNotExist:
        return render(
            request,
            SETUP_TEMPLATE,
            _setup_context(request, draft, refusal_words().services.no_such_beast),
            status=400,
        )
    return redirect("battle:detail", pk=battle.pk)


@require_POST
def battle_setup_discard(request, pk):
    """Throw the draft away and go back to the battles list."""
    draft = _own_draft_or_404(request, pk)
    draft.delete()
    return redirect("battle:list")


def event_turns(events) -> list[dict]:
    """Events grouped by turn, in the order given: ``[{turn, events}, ...]``.

    The one grouping the battle page's log and the endpoints' turn fragments
    are both rendered from (#504).
    """
    return [
        {"turn": turn, "events": list(turn_events)}
        for turn, turn_events in groupby(events, key=lambda event: event.turn)
    ]


def rendered_turns(events) -> list[dict]:
    """Each turn these events touch, rendered by ``battle/_turn_group.html``.

    ``[{"turn": 3, "html": "<section class=\"turn-group\" ...>"}, ...]``.
    battle.js inserts these rather than building event markup itself, so an
    event appended live is the markup a reload renders (#504).
    """
    return [
        {
            "turn": group["turn"],
            "html": render_to_string("battle/_turn_group.html", {"group": group}),
        }
        for group in event_turns(events)
    ]


def serialize_event(event) -> dict:
    return {
        "turn": event.turn,
        "phase": event.phase,
        "sequence": event.sequence,
        "event_type": event.event_type,
        "actor": event.actor,
        "payload": event.payload,
        "message": event.message,
    }


def seat_summaries(battle, actor: Actor) -> list[dict]:
    """Every combatant's seat-facing facts, as the lobby and roster show them.

    ``is_you`` is relative to the requesting actor, so the browser can
    render "your" affordances (ready toggle, action prompt) without knowing
    ids.

    ``kind`` means "what sits here", and since #305 it cannot be read off
    ``character_id`` any more: a guest's fighter has no ``Character`` row,
    so the old fall-through would have called a claimed seat *open* and
    offered it to the next passer-by. It is decided by the claim itself —
    a seat is ``open`` only while genuinely unclaimed.
    """
    summaries = []
    # The side nobody sits in is commanded by whoever runs the battle, or in
    # a Games Master session's fight by a Game Master only (#762).
    controller = commands_foes(actor, battle)
    for combatant in battle.combatants.select_related("player", "character"):
        claimed = seat_is_held(combatant)
        if combatant.beast_id:
            kind = "beast"
        elif combatant.is_open_seat and not claimed:
            kind = "open"
        else:
            kind = "character"
        summaries.append(
            {
                "combatant_id": combatant.pk,
                "display_name": combatant.display_name,
                "is_open_seat": combatant.is_open_seat,
                "kind": kind,
                "is_saved_character": combatant.is_saved_character,
                "player": control.seat_holder_label(combatant),
                "is_you": actor.holds_seat(combatant),
                "is_ready": combatant.is_ready,
                # Empty = free-for-all. A seat's team survives its claim
                # (#275), so the lobby can say which side a seat joins.
                "team": combatant.team,
                # Control as a setting (#308). ``control_mode`` is the row's
                # own answer — empty when it follows the battle — and
                # ``effective_control_mode`` is what that resolves to, so
                # nothing downstream has to re-implement the precedence
                # rule. ``control_line`` is the sentence the roster card
                # shows; ``you_decide`` says whether this viewer is the one
                # who would be asked.
                "control_mode": combatant.control_mode,
                "effective_control_mode": control.effective_mode(battle, combatant),
                "controller": control.controller_label(battle, combatant),
                "control_line": control.mode_line(battle, combatant),
                "you_decide": decides_for(actor, battle, combatant),
                # Who may change this combatant's mode (#339, item 3). The
                # same two-sided answer ``combatant_control`` enforces:
                # whoever runs the battle, and the player sitting in the
                # seat — which is what "I have had enough, let the AI fight
                # my figure" needs. It used to say "the controller", so the
                # payload disagreed with the endpoint.
                "can_set_control": controller or actor.holds_seat(combatant),
                # Renaming follows the same line (#762): whoever runs the
                # battle may rename a figure, but in a Games Master session's
                # fight the player renames only their own; battle.js offers
                # the click on exactly these.
                "can_rename": controller or actor.holds_seat(combatant),
                # A character's owner plays it; a Game Master takes an absent
                # owner's seat and the owner or he hands it back (#853).
                # Empty where this viewer has no such control.
                **seat_hand_offs(battle, combatant, actor),
            }
        )
    return summaries


def seat_hand_offs(battle, combatant, actor: Actor) -> dict[str, str]:
    """The take-seat and return-seat endpoints this viewer may use (#853).

    Worked out here, beside the seat summary, so ``battle.js`` renders a
    button only where :func:`battle.services.take_seat` and
    :func:`battle.services.return_seat` would accept the post; the services
    hold the same rule, so a hidden button is never the only guard.

    Returns:
        ``take_seat_url`` and ``return_seat_url``, each ``""`` when not
        offered.
    """
    offered = {"take_seat_url": "", "return_seat_url": ""}
    character = combatant.character if combatant.character_id else None
    if character is None or actor.user is None or battle.is_over:
        return offered
    owner_id = character.user_id
    # The fight's own Game Master only (the #853 review), and never for a
    # character its owner withdrew from the session (#871 review).
    runner = runs_the_seats(battle, actor.user)
    if (
        runner
        and seat_is_held(combatant)
        and combatant.player_id != actor.user.pk
        and not withdrawn_from_session(battle, character)
    ):
        offered["take_seat_url"] = reverse(
            "battle:seat-take", args=[battle.pk, combatant.pk]
        )
    if held_by_another(combatant) and (runner or actor.user.pk == owner_id):
        offered["return_seat_url"] = reverse(
            "battle:seat-return", args=[battle.pk, combatant.pk]
        )
    return offered


def pending_payload(battle, actor: Actor) -> dict | None:
    """The awaited decision, dressed for display: who, for whom, the menu.

    ``candidates`` and ``magic`` are only included for the player being
    awaited (and the DM, who may hand the choice to the AI) — other viewers
    see who is up, not their options and not a caster's hand. ``magic`` is
    the cast stage's data (pool, known spells, why an unavailable one is
    unavailable) and is ``None`` for a combatant who knows no spells.
    """
    pending = battle.pending_input_json or {}
    if not pending:
        return None
    seat = (
        battle.combatants.select_related("player")
        .filter(pk=pending.get("combatant_id"))
        .first()
    )
    if seat is None:
        return None
    # Ownership, not seat-holding (#308): a manual combatant nobody is
    # sitting in belongs to whoever runs the battle, and they are the one
    # being asked. ``holds_seat`` alone would show them a prompt addressed
    # to nobody.
    is_you = decides_for(actor, battle, seat)
    payload = {
        "combatant_id": seat.pk,
        "turn": pending.get("turn"),
        "display_name": seat.display_name,
        "player": control.seat_holder_label(seat),
        "is_you": is_you,
        "candidates": [],
        "magic": None,
    }
    if is_you or commands_foes(actor, battle):
        payload["candidates"] = pending.get("candidates", [])
        profile = profiles.for_battle(battle)
        payload["magic"] = profile.caster_magic(
            profile.load(battle.state_json), seat.pk
        )
    return payload


def advance_control(battle, actor: Actor) -> dict | None:
    """What the Advance button says and whether it may be pressed (#308).

    Computed here, from stored state, rather than worked out in the browser
    when something happens: in a mixed battle the button is not a plain
    "go" — it runs the automatic combatants and stops at each manual one,
    so between presses it has to say *whose* move the battle is sitting on.
    A viewer who cannot run the battle gets ``None``: there is no button.

    Two things follow from computing it server-side. A reload says exactly
    what the page said before it (the readout is state, not an event), and
    the label lives beside the rule that produces it instead of being
    reconstructed from a status string in ``battle.js``.
    """
    if not can_control(actor, battle) or battle.status == Battle.STATUS_LOBBY:
        return None
    if battle.status == Battle.STATUS_FINISHED:
        return {"label": "Battle over", "enabled": False, "waiting_on": ""}
    if battle.status == Battle.STATUS_STALEMATE:
        return {"label": "Stalemate", "enabled": False, "waiting_on": ""}
    moved = stale_pools(battle)
    if moved:
        # The records moved under an unfinished fight (issue #842): it does
        # not go on until a Game Master takes the pools from the records.
        # Said on the page from stored state, so a reload says it too.
        return {
            "label": "Pools changed on the records",
            "enabled": False,
            "waiting_on": "",
            "stale": refusal_words().services.stale_pools.format(
                names=", ".join(moved)
            ),
            "resnapshot_url": (
                reverse("battle:resnapshot", args=[battle.pk]) if actor.is_dm else ""
            ),
        }
    if battle.status == Battle.STATUS_AWAITING:
        pending = battle.pending_input_json or {}
        awaited = battle.combatants.filter(pk=pending.get("combatant_id")).first()
        name = awaited.display_name if awaited else "a player"
        return {
            "label": f"Waiting on {name}'s move",
            "enabled": False,
            "waiting_on": name,
        }
    return {"label": "Advance turn", "enabled": True, "waiting_on": ""}


def battle_payload(
    battle, actor: Actor, events, request: HttpRequest | None = None
) -> dict:
    """The one response shape every battle mutation and poll answers in.

    The view model is built for *this* viewer (#302): which combatants'
    full attributes it carries is decided here, from the actor, so a poll
    can never hand a spectator numbers the page would not have rendered.
    """
    advance = advance_control(battle, actor)
    return {
        "status": battle.status,
        "turn": battle.turn_number,
        "view": profiles.view_of(battle, detail_ids=detail_visible_ids(actor, battle)),
        "events": [serialize_event(event) for event in events],
        # The same events as the log shows them, one fragment per turn (#504).
        "turns_html": rendered_turns(events),
        "seats": seat_summaries(battle, actor),
        "pending": pending_payload(battle, actor),
        "control_mode": battle.control_mode,
        "advance": advance,
        # Whether the fight is still on (#868): a Games Master panel's Fight
        # form repaints from this when the board applies a response.
        "live": not battle.is_over,
        # Who fights, or would fight, bare-handed (#844): the figures while
        # the fight is on, the session's attached characters once it is over,
        # repainted with the board so the line follows either.
        BARE_HANDED_KEY: bare_handed_line(battle),
        # Why a record-writing fight will not go on, and the Game Master's
        # control, rendered once for every response (#842): a poll while a
        # player's move is awaited shows it too, and "" clears it.
        "stale_html": stale_pools_html(advance, request),
        # A Game Master's rulings on the battle (#867, #37): the records, so
        # a JSON caller reads back what it changed, and each panel rendered
        # once, keyed by its element id, which battle.js puts in its place.
        "rulings": {
            "hth_agreement": battle.hth_agreement_json,
            "magic_bounds_lifted": battle.magic_bounds_lifted_json,
        },
        "rulings_html": ruling_panels_html(battle, actor, request),
    }


#: The ruling panels' element ids, named once for the templates (through
#: the panel dicts), the payload and battle.js (which reads them as the
#: payload's keys).
HTH_AGREEMENT_PANEL_ID = "battle-hth-agreement"
MAGIC_BOUNDS_PANEL_ID = "battle-magic-bounds"

#: The names the ruling partials read their panel by.
HTH_AGREEMENT_CONTEXT = "hth_agreement"
MAGIC_BOUNDS_CONTEXT = "magic_bounds"

#: The arena context's name for the turn panel's words (#94), which
#: battle/_arena.html renders as #battle-turn-panel-words.
TURN_PANEL_WORDS_CONTEXT = "turn_panel_words"


def ruling_panels_html(
    battle, actor: Actor, request: HttpRequest | None
) -> dict[str, str]:
    """Each ruling's panel as the page renders it, keyed by its element id.

    ``""`` for a panel the battle does not show. The partials are the page's
    own, so the page carries one copy of their markup; a form's ``next`` is
    the endpoint's path here, and battle.js points it back at the page.
    """
    rendered = {}
    for panel_id, template, name, panel in (
        (
            HTH_AGREEMENT_PANEL_ID,
            "battle/_hth_agreement.html",
            HTH_AGREEMENT_CONTEXT,
            hth_agreement_panel(battle, actor),
        ),
        (
            MAGIC_BOUNDS_PANEL_ID,
            "battle/_magic_bounds.html",
            MAGIC_BOUNDS_CONTEXT,
            magic_bounds_panel(battle, actor),
        ),
    ):
        rendered[panel_id] = (
            ""
            if panel is None
            else render_to_string(
                template, {name: panel, "battle": battle}, request=request
            )
        )
    return rendered


def stale_pools_html(advance: dict | None, request: HttpRequest | None) -> str:
    """The stale-pools notice as the page renders it, or ``""`` (#842)."""
    if not advance or not advance.get("stale"):
        return ""
    return render_to_string(
        "battle/_stale_pools.html", {"advance": advance}, request=request
    )


#: How many of a battle's most recent turns the log renders on load (#662).
#: A plain six-fighter battle run to its natural end took 83 turns and wrote
#: 1,834 events; a battle may hold twelve combatants, and STALEMATE_TURNS
#: only ends fights in which *nothing* happens, so the ceiling is several
#: times that. Every one of those rows used to be fetched and rendered for
#: every viewer on every load, including the spectators the replay is meant
#: to cost nothing.
LOG_TURN_WINDOW = 10


def windowed_events(battle, *, show_all: bool) -> tuple[list, int]:
    """The events the log renders, and how many earlier turns it held back.

    The window is the last :data:`LOG_TURN_WINDOW` turns. Which end of it a
    reader is on is a URL, not a button that forgets itself — ``?log=all``
    on the battle page opens the whole log and survives a reload, the way
    the rest of this page's state does.

    Args:
        battle: The battle whose log is being rendered.
        show_all: Render every event, however many turns that is.

    Returns:
        ``(events, earlier_turns)`` — the rows to render, and the number of
        distinct turns *before* the window, which is zero for a battle short
        enough to fit in it (and is reported whether or not the whole log is
        being shown, so the page can offer the way back).
    """
    latest = battle.events.aggregate(latest=Max("turn"))["latest"]
    if latest is None:
        return [], 0
    first_shown = latest - LOG_TURN_WINDOW + 1
    earlier_turns = (
        battle.events.filter(turn__lt=first_shown)
        .values_list("turn", flat=True)
        .distinct()
        .count()
    )
    if show_all or not earlier_turns:
        return list(battle.events.all()), earlier_turns
    return list(battle.events.filter(turn__gte=first_shown)), earlier_turns


def arena_context(battle, actor: Actor, *, show_all_turns: bool = False) -> dict:
    """The template context the arena partial renders a started battle from.

    ``battle/_arena_advance.html``, ``battle/_arena_restart.html`` and
    ``battle/_arena.html`` -- and the ``battle.js`` that drives them --
    read exactly these names, so the one
    dict serves both the battle page and the GM chat's Battle tab (#345):
    the board beside the chat is built from the same view model, seats,
    pending menu and advance readout as the page, never a second copy.

    Args:
        battle: The battle to render.
        actor: The viewer, which decides whose attributes the view carries
            and whether the controls are offered at all.
        show_all_turns: Render the whole event log rather than the last
            :data:`LOG_TURN_WINDOW` turns of it (#662).
    """
    events, earlier_turns = windowed_events(battle, show_all=show_all_turns)
    return {
        "battle": battle,
        "event_turns": event_turns(events),
        # The log window (#662), and the two links that move between its
        # ends. Both point at the battle page, because that is where a whole
        # log belongs — following one from the GM chat's Battle tab opens it
        # there rather than reloading the conversation.
        "log_turn_window": LOG_TURN_WINDOW,
        "earlier_turns": earlier_turns,
        "showing_all_turns": show_all_turns,
        "full_log_url": f"{reverse('battle:detail', args=[battle.pk])}?log=all",
        "windowed_log_url": reverse("battle:detail", args=[battle.pk]),
        # What the arena and roster panels draw, in the one shape both rules
        # profiles answer in (#251). json_script serializes it, so the browser
        # parses exactly what the advance endpoint returns thereafter.
        "view_json": profiles.view_of(
            battle, detail_ids=detail_visible_ids(actor, battle)
        ),
        # Battles predating the turn-0 snapshot cannot be rewound (#188).
        "can_restart": bool(battle.initial_state_json),
        # The disabled Restart button says why, in the refusal's own words.
        "no_starting_snapshot": refusal_words().services.no_starting_snapshot,
        # Restart and the control desk command the side nobody sits in: in a
        # Games Master session's fight, a Game Master only (#762). Whether a
        # fight that has marked a record may be restarted is the endpoint's
        # to answer (``services.restart_battle``), and it says why.
        "commands_foes": commands_foes(actor, battle),
        # Restart is offered only to someone the endpoint would let restart:
        # a fight whose results are already on a character's record is a
        # Game Master's to restart (``services.results_recorded``, #750).
        "restart_offered": commands_foes(actor, battle)
        and (actor.is_dm or not results_recorded(battle)),
        # Who runs this battle (#305): its DM, or whoever made it. The
        # template agrees with the endpoints rather than deciding anything.
        "can_control": can_control(actor, battle),
        "seats": seat_summaries(battle, actor),
        "pending": pending_payload(battle, actor),
        # The turn panel's own words (#94): config/battle-rulings.toml's
        # [turn_panel], once per page, since they cannot change while the
        # server runs, so battle.js prints no wording of its own.
        TURN_PANEL_WORDS_CONTEXT: asdict(words().turn_panel),
        "advance": advance_control(battle, actor),
        HTH_AGREEMENT_CONTEXT: hth_agreement_panel(battle, actor),
        MAGIC_BOUNDS_CONTEXT: magic_bounds_panel(battle, actor),
    }


def hth_agreement_panel(battle, actor: Actor) -> dict | None:
    """What the page shows of the "HTH by agreement" ruling (#867), or None.

    Rendered from the battle's record, the one source of truth: the line
    every viewer reads, and, for the fight's Game Master
    (:func:`battle.services.runs_the_seats`, the only one who may change it),
    the marked pairs and the combatants a new pair is chosen from. The words
    are configuration (``battle.wording``). A classic battle has no such
    ruling and gets nothing.

    Args:
        battle: The battle.
        actor: The viewer.
    """
    if battle.rules_profile != TARMAR_PROFILE:
        return None
    record = agreement.Agreement.of(battle)
    rows = list(battle.combatants.order_by("pk"))
    names = {row.pk: row.display_name for row in rows}
    hth_words = words().hth_agreement
    return {
        "panel_id": HTH_AGREEMENT_PANEL_ID,
        "record": json.dumps(battle.hth_agreement_json, sort_keys=True),
        "words": hth_words,
        "and_word": words().common.and_word,
        "line": agreement.describe(record, names) or hth_words.none_marked,
        "whole_battle": record.whole_battle,
        "pairs": [
            {
                "first": first,
                "second": second,
                "names": agreement.pair_names((first, second), names),
            }
            for first, second in sorted(record.pairs)
        ],
        "can_rule": runs_the_seats(battle, actor.user),
        "combatants": [{"id": row.pk, "name": row.display_name} for row in rows],
    }


def magic_bounds_panel(battle, actor: Actor) -> dict | None:
    """What the page shows of the magic-bounds ruling (#37), or None.

    Rendered from the battle's record, the one source of truth: every
    lifting, with who lifted it and when, for every viewer; and, for the
    fight's Game Master (:func:`battle.services.runs_the_seats`, the only one
    who may change it), the forms, offering only the bounds the loaded canon
    imposes (:func:`battle.magic_bounds.offered_bounds`). The words are
    configuration (``battle.wording``). A classic battle, and a Tarmar
    battle under a canon that imposes none of the bounds and with nothing
    lifted, get nothing.

    Args:
        battle: The battle.
        actor: The viewer.
    """
    if battle.rules_profile != TARMAR_PROFILE:
        return None
    record = magic_bounds.LiftedBounds.of(battle)
    offered = magic_bounds.offered_bounds()
    if not offered and not record.liftings:
        return None
    rows = list(battle.combatants.order_by("pk"))
    names = {row.pk: row.display_name for row in rows}
    bounds_words = words().magic_bounds
    return {
        "panel_id": MAGIC_BOUNDS_PANEL_ID,
        "record": json.dumps(battle.magic_bounds_lifted_json, sort_keys=True),
        "words": bounds_words,
        "none_lifted_line": magic_bounds.none_lifted_line(),
        "liftings": [
            {
                "combatant_id": lifting.combatant_id,
                "bound": lifting.bound,
                "line": bounds_words.lifting_line.format(
                    bound=capfirst(magic_bounds.bound_label(lifting.bound)),
                    name=name_of(names, lifting.combatant_id),
                    by=game_master_name(lifting.by),
                ),
                "at": datetime.fromisoformat(lifting.at),
            }
            for lifting in sorted(record.liftings, key=magic_bounds.order)
        ],
        "can_rule": bool(offered) and runs_the_seats(battle, actor.user),
        "combatants": [{"id": row.pk, "name": row.display_name} for row in rows],
        "bounds": [
            {"key": bound, "label": capfirst(magic_bounds.bound_label(bound))}
            for bound in offered
        ],
        "fields": {
            "combatant": magic_bounds.COMBATANT_FIELD,
            "bounds": magic_bounds.BOUNDS_FIELD,
            "lifted": magic_bounds.LIFTED_FIELD,
        },
    }


class BattleDetailView(
    BreadcrumbMixin,
    ActivePageMixin,
    DetailView,
):
    """The battle page: lobby while open, arena and log once started.

    Visible to every signed-in user (#250). What the page *offers* depends
    on the viewer: the DM gets the controls, a seated player their own turn
    prompts, and everyone else watches — same arena, roster, and event log,
    no controls (each mutation endpoint enforces that server-side; the
    template merely agrees). A finished battle is the same page with its
    complete event log: the log is server-rendered from the ``BattleEvent``
    rows on every load, so the replay costs spectators nothing and is
    deliberately kept open to them.
    """

    def get_breadcrumb(self, context):
        return trail(
            step("Battles", reverse("battle:list")),
            step(context["battle"].name),
        )

    model = Battle
    template_name = BATTLE_DETAIL_TEMPLATE
    context_object_name = "battle"
    active_page = "battles"

    def get_queryset(self):
        return readable_battles(actor_of(self.request))

    def get(self, request, *args, **kwargs):
        """Send a signed-out visitor to sign in when that would let them in.

        A battle holding a private character is kept from anonymous viewers
        (``readable_battles``), but every signed-in user may read every
        battle (#250). So for a signed-out visitor, a battle that exists and
        is closed to them is a login redirect with ``?next=`` back here
        (issue #339, item 32), not a dead-end 404. The trade-off: the
        redirect tells an anonymous visitor that a battle with this id
        exists -- never its name, roster or anything on the page -- where a
        404 would not. A signed-in reader never hits this branch, and an id
        with no battle behind it is still a 404 for everyone.
        """
        try:
            return super().get(request, *args, **kwargs)
        except Http404:
            if (
                not request.user.is_authenticated
                and Battle.objects.filter(pk=kwargs.get("pk")).exists()
            ):
                return redirect_to_login(request.get_full_path())
            raise

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        battle = context["battle"]
        actor = actor_of(self.request)
        # The arena's own context (#345): what the arena partial and
        # battle.js need, shared with the GM chat's Battle tab.
        # ``?log=all`` opens the whole event log rather than its last few
        # turns (#662) — a URL, so a reload shows what the reader chose.
        context.update(
            arena_context(
                battle,
                actor,
                show_all_turns=self.request.GET.get("log") == "all",
            )
        )
        context["combatants"] = battle.combatants.select_related("character", "beast")
        context["is_dm"] = actor.is_dm
        context["is_guest"] = actor.is_guest
        context["guest_notes"] = refusal_words().guest_notes
        # The control desk (#308): the battle's default, and how many
        # combatants are ignoring it. Both are read off stored state, so
        # the panel says the same thing on a reload as it did before one.
        context["control_modes"] = control.MODE_CHOICES
        context["combatant_modes"] = control.COMBATANT_MODE_CHOICES
        context["control_overrides"] = sum(
            1 for seat in context["seats"] if seat["control_mode"]
        )
        context["manual_count"] = sum(
            1
            for seat in context["seats"]
            if seat["effective_control_mode"] == control.MANUAL
        )
        context["my_seat"] = battle.combatants.filter(actor.seat_filter()).first()
        # Whether the lobby can still offer anyone a seat (issue #339, item
        # 25): the lede used to invite a claim when none was left.
        context["has_open_seat"] = any(
            seat["kind"] == "open" for seat in context["seats"]
        )
        # The seat this viewer sits in, as the seat summary the page renders
        # every other seat from (#339, item 3) — so the "hand my figure to
        # the AI" control on a started battle reads its combatant's mode and
        # control line from the same source the DM's desk does.
        # Every seat this viewer sits in: a Games Master session's fight seats
        # the whole party to its player, who gets a card per figure (#762;
        # this used to take the first and drop the rest).
        context["my_seat_summaries"] = [
            seat
            for seat in context["seats"]
            if seat["is_you"] and seat["can_set_control"]
        ]
        # Deleting a battle (#339, item 5): the same people who may run it.
        context["can_delete"] = context["can_control"]
        # A viewer with no role in a started battle is watching (#250): the
        # header says so, and no control affordance renders for them.
        context["is_spectator"] = (
            not context["can_control"]
            and context["my_seat"] is None
            and battle.status != Battle.STATUS_LOBBY
        )
        if battle.status == Battle.STATUS_LOBBY:
            # The muster names the rules being joined (#251): under classic
            # rules what a player brings is rebuilt at the start, and the
            # lobby is the last point at which that is news they can act on.
            # Read from the constant rather than matched as a literal in the
            # template, so the profile name lives in one place.
            context["is_classic"] = battle.rules_profile == CLASSIC_PROFILE
            # Seats whose characters have died since the claim hold the start.
            context["blocking_seats"] = dead_seats(battle)
            context["archetypes"] = ARCHETYPES
            # What a claim may bring (#270): your own living, undeleted
            # characters — the same chokepoint claim_seat enforces — a page
            # at a time, newest first, with a search (#864).
            context["claim_picker"] = claim_picker(battle, actor)
        return context


@require_GET
def battle_claim_picker(request, pk) -> HttpResponse:
    """One page of a lobby seat's claim picker (issue #864).

    The viewer's own living characters (``claimable_characters``, what
    ``claim_seat`` accepts), searched by name, for a battle the viewer may
    read. A guest has none.
    """
    actor = actor_of(request)
    battle = get_object_or_404(readable_battles(actor), pk=pk)
    query, page_number = asked_for(request)
    picker = claim_picker(battle, actor, query=query, page_number=page_number)
    return picker_body_response(request, picker, {"battle": battle})


def _detail_with_error(request, battle: Battle, message: str, *, status: int = 400):
    """Re-render the battle page with ``message`` standing on it (#339, item 2).

    Every lobby control — claim, release, ready, start — is a plain HTML
    form, so a refusal answered with ``_json_error`` replaced the whole page
    with a line of raw JSON and stranded the player there. The setup page
    already answers a refused post by re-rendering itself with the reason in
    context; the lobby now does the same. The message is part of the page,
    not a banner on a timer, so a reload shows the muster rather than the
    complaint and nothing vanishes while it is being read.

    Args:
        request: The refused POST.
        battle: The battle whose page to render.
        message: The reason, in the player's words.
        status: HTTP status for the response.

    Returns:
        The rendered battle page, carrying ``error``.
    """
    view = BattleDetailView()
    view.setup(request, pk=battle.pk)
    view.object = battle
    context = view.get_context_data(object=battle)
    context["error"] = message
    return render(request, BATTLE_DETAIL_TEMPLATE, context, status=status)


def _refuse(request, battle: Battle, message: str, *, status: int = 400):
    """Refuse a control post the way its caller asked to be answered.

    The control endpoints serve both the page's plain forms and script; a
    form post gets the page back with the reason on it, and an XHR caller
    keeps the JSON error shape it parses (#339, item 2).
    """
    if _wants_json(request):
        return _json_error(message, status=status)
    return _detail_with_error(request, battle, message, status=status)


def _back_or_detail(request, battle: Battle):
    """Where a form post on a battle lands: the ``next`` it names, when that
    is a page on this site (the GM chat's Battle tab posts from the chat),
    else the battle page."""
    back = str(request.POST.get("next") or "")
    if back and url_has_allowed_host_and_scheme(
        back, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(back)
    return redirect("battle:detail", pk=battle.pk)


@require_POST
def battle_advance(request, pk):
    actor, battle = _actor_and_readable_battle(request, pk)
    denied = _control_denied(actor, battle)
    if denied is not None:
        return denied
    try:
        battle, events = advance_turn(battle.pk)
    except StalePools as stale:
        # Not a failure: the rule refusing (issue #842), in its own words,
        # with the readout and the notice the page would render on a reload
        # -- the Game Master's control included -- so a page that was open
        # when the records moved gets them without reloading.
        advance = advance_control(battle, actor)
        return JsonResponse(
            {
                "error": str(stale),
                "advance": advance,
                "stale_html": stale_pools_html(advance, request),
            },
            status=409,
        )
    except BattleRefusal as refusal:
        # A rule refusing, like the other battle views' refusals: its words,
        # as a 400 (the lobby's, BattleInLobby). Only refusals are caught, not
        # ValueError at large, since the engine's own ValueErrors are
        # internal text (issues #13, #39).
        return _json_error(str(refusal))
    except Exception:  # noqa: BLE001 — logged with its traceback, never shown
        logger.exception("Advancing battle %s failed", pk)
        return _json_error(refusal_words().views.request_failed, status=500)
    return JsonResponse(battle_payload(battle, actor, events, request))


@require_POST
@_internal_errors_logged()
def battle_control(request, pk):
    """Set the battle's default control mode (#308), for whoever runs it.

    ``apply_to_all`` is the "set them all to X" affordance: it clears every
    per-combatant override so the whole roster follows the battle again.
    A form post lands back on the page — the panel is rendered from stored
    state, so the reload *is* the update; an XHR caller gets the usual
    battle payload.
    """
    actor, battle = _actor_and_readable_battle(request, pk)
    if not can_control(actor, battle):
        return _refuse(
            request, battle, refusal_words().views.control_denied, status=403
        )
    if not commands_foes(actor, battle):
        return _refuse(
            request,
            battle,
            refusal_words().views.foes_belong_to_game_master,
            status=403,
        )
    data, err = (
        _parse_json_body(request) if _wants_json(request) else (request.POST, None)
    )
    if err:
        return err
    try:
        battle, _event = set_battle_control_mode(
            battle.pk,
            str(data.get("control_mode") or ""),
            apply_to_all=_form_flag(data.get("apply_to_all")),
        )
    except BattleRefusal as mode_error:
        return _refuse(request, battle, str(mode_error))
    if _wants_json(request):
        return JsonResponse(battle_payload(battle, actor, [], request))
    return redirect("battle:detail", pk=battle.pk)


@require_POST
@_internal_errors_logged()
def battle_hth_agreement(request, pk):
    """Mark or unmark the battle, or a pair, "HTH by agreement" (#867).

    The fight's Game Master only (:func:`battle.services.runs_the_seats`),
    here and in the service it calls. A form post lands back where it came
    from (the battle page, or the GM chat's Battle tab), whose panel renders
    from the battle's record; an XHR caller gets the battle payload. ``scope`` is "battle" or
    "pair"; ``agreed`` marks or unmarks; a pair names ``first`` and
    ``second``.
    """
    actor, battle = _actor_and_readable_battle(request, pk)
    hth_words = words().hth_agreement
    if not runs_the_seats(battle, actor.user):
        return _refuse(request, battle, hth_words.game_master_only, status=403)
    data, err = (
        _parse_json_body(request) if _wants_json(request) else (request.POST, None)
    )
    if err:
        return err
    scope = str(data.get("scope") or "")
    pair = None
    if scope == "pair":
        try:
            pair = (whole_number(data.get("first")), whole_number(data.get("second")))
        except ValueError:
            return _refuse(request, battle, hth_words.refuse_pair_missing)
    elif scope != "battle":
        return _refuse(request, battle, hth_words.refuse_scope)
    try:
        battle, _event = set_hth_agreement(
            battle.pk,
            actor,
            agreed=_form_flag(data.get("agreed")),
            whole_battle=scope == "battle",
            pair=pair,
        )
    except BattleRefusal as ruling_error:
        return _refuse(request, battle, str(ruling_error))
    if _wants_json(request):
        return JsonResponse(battle_payload(battle, actor, [], request))
    return _back_or_detail(request, battle)


@require_POST
@_internal_errors_logged()
def battle_magic_bounds(request, pk):
    """Lift, or hold again, magic bounds for one figure (#37).

    The fight's Game Master only (:func:`battle.services.runs_the_seats`),
    here and in the service it calls. A form post lands back where it came
    from (the battle page, or the GM chat's Battle tab), whose panel renders
    from the battle's record; an XHR caller gets the battle payload, which
    carries the record. The fields are :data:`battle.magic_bounds.COMBATANT_FIELD`,
    :data:`~battle.magic_bounds.BOUNDS_FIELD` (repeated in a form, a list in
    JSON) and :data:`~battle.magic_bounds.LIFTED_FIELD`.
    """
    actor, battle = _actor_and_readable_battle(request, pk)
    bounds_words = words().magic_bounds
    if not runs_the_seats(battle, actor.user):
        return _refuse(request, battle, bounds_words.game_master_only, status=403)
    if _wants_json(request):
        data, err = _parse_json_body(request)
        if err:
            return err
        bounds = data.get(magic_bounds.BOUNDS_FIELD)
        if not isinstance(bounds, list):
            return _refuse(request, battle, bounds_words.refuse_bounds_list)
    else:
        data = request.POST
        bounds = request.POST.getlist(magic_bounds.BOUNDS_FIELD)
    try:
        combatant_id = whole_number(data.get(magic_bounds.COMBATANT_FIELD))
    except ValueError:
        return _refuse(request, battle, bounds_words.refuse_combatant)
    try:
        battle, _event = set_magic_bounds_lifted(
            battle.pk,
            actor,
            combatant_id=combatant_id,
            bounds=[str(bound) for bound in bounds],
            lifted=_form_flag(data.get(magic_bounds.LIFTED_FIELD)),
        )
    except BattleRefusal as ruling_error:
        return _refuse(request, battle, str(ruling_error))
    if _wants_json(request):
        return JsonResponse(battle_payload(battle, actor, [], request))
    return _back_or_detail(request, battle)


@require_POST
@_internal_errors_logged()
def combatant_control(request, pk, combatant_id):
    """Set one combatant's control mode, or hand it back to the battle (#308).

    Mid-battle by design: a player who has had enough gives their figure to
    the AI without giving up the seat, and whoever runs the battle can take
    one back. The change lands in the battle log, because a figure that
    starts behaving differently mid-fight otherwise has nothing explaining
    why.
    """
    actor, battle = _actor_and_readable_battle(request, pk)
    combatant = battle.combatants.filter(pk=combatant_id).first()
    if combatant is None:
        return _json_error(refusal_words().services.no_such_combatant, status=404)
    # Whoever runs the battle sets any combatant's mode; a seated player
    # sets their own, which is what "hand my fighter to the AI" needs. In a
    # Games Master session's fight the foes are the Game Master's (#762).
    if not (commands_foes(actor, battle) or actor.holds_seat(combatant)):
        if can_control(actor, battle):
            return _refuse(
                request,
                battle,
                refusal_words().views.foes_belong_to_game_master,
                status=403,
            )
        return _refuse(
            request,
            battle,
            refusal_words().views.combatant_control_denied,
            status=403,
        )
    data, err = (
        _parse_json_body(request) if _wants_json(request) else (request.POST, None)
    )
    if err:
        return err
    try:
        battle, _combatant, _event = set_combatant_control_mode(
            battle.pk, combatant.pk, str(data.get("control_mode") or "")
        )
    except BattleRefusal as mode_error:
        return _refuse(request, battle, str(mode_error))
    if _wants_json(request):
        return JsonResponse(battle_payload(battle, actor, [], request))
    return redirect("battle:detail", pk=battle.pk)


@require_POST
def battle_delete(request, pk):
    """Take a battle off the lists, keeping the fight itself (#339, item 5).

    A soft delete, exactly as ``characters.views.delete_character`` does it:
    the flag and the moment go on, the row and its whole event log stay, and
    :func:`battle_restore` puts it back. A battle is a replayable record and
    a GM session may point at one, so "clear this off my list" must not be
    the same act as destroying it.

    Whoever runs the battle may delete it (its DM, or the account or guest
    that made it). Deleting an already-deleted battle is a 404 rather than a
    second write, which is also what stops a double-submit from moving
    ``deleted_at``.
    """
    actor = actor_of(request)
    battle = get_object_or_404(deletable_battles(actor), pk=pk, is_deleted=False)
    battle.is_deleted = True
    battle.deleted_at = timezone.now()
    battle.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
    logger.info(
        "BATTLE_DELETE: battle=%s (pk=%s) soft-deleted",
        battle.name,
        battle.pk,
    )
    return redirect("battle:list")


@require_POST
def battle_restore(request, pk):
    """Put a soft-deleted battle back on the lists (#339, item 5).

    The same people who may delete a battle may restore it. Deliberately
    wider than ``characters``' manager-only restore: a player who can hide
    their own battle and then cannot get it back has been handed a one-way
    door dressed as a tidy-up.
    """
    actor = actor_of(request)
    battle = get_object_or_404(deletable_battles(actor), pk=pk, is_deleted=True)
    battle.is_deleted = False
    battle.deleted_at = None
    battle.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
    return redirect("battle:detail", pk=battle.pk)


@require_POST
@_internal_errors_logged(json_only=True)
def battle_restart(request, pk):
    """Replay from turn 0 with the same roster and seed (issue #188).

    Answers in the advance endpoint's shape — status, turn, state, events —
    so the page re-renders through exactly the path an advance takes. The
    event list is empty, because restart clears the log, but for the line
    naming who stays dead (#854).

    ``empty_log_html`` is the no-turns notice from ``battle/_log_empty.html``,
    the partial ``_arena.html`` renders for a battle with no events, so the
    log a restart leaves behind is the one a reload shows (#508).
    """
    actor, battle = _actor_and_readable_battle(request, pk)
    denied = _control_denied(actor, battle)
    if denied is not None:
        return denied
    if not commands_foes(actor, battle):
        return _json_error(refusal_words().views.foes_belong_to_game_master, status=403)
    try:
        battle = restart_battle(battle.pk, is_game_master=actor.is_dm)
    except BattleRefusal as restart_error:
        return _json_error(str(restart_error))
    # The one line a restart writes -- who stays dead, because the record
    # says so (#854) -- comes back with it, so the log says why a figure is
    # still down without a reload.
    payload = battle_payload(
        battle,
        actor,
        list(
            battle.events.filter(
                payload__has_any_keys=["laid_dead", "withdrawn_kept_off"]
            ).order_by("sequence")
        ),
        request,
    )
    payload["empty_log_html"] = render_to_string(
        "battle/_log_empty.html", {"can_control": can_control(actor, battle)}
    )
    return JsonResponse(payload)


@require_POST
@_internal_errors_logged()
def battle_resnapshot(request, pk):
    """A Game Master takes a fight's pools from the character records (#842).

    The control the stale-pools notice offers. A form post goes back to the
    page it came from (the battle page, or the Games Master chat whose Battle
    tab holds the board) when that is a page of this site; an XHR caller
    gets the battle payload.
    """
    actor, battle = _actor_and_readable_battle(request, pk)
    denied = _control_denied(actor, battle)
    if denied is not None:
        return denied
    try:
        battle = resnapshot_pools(battle.pk, is_game_master=actor.is_dm)
    except BattleRefusal as refusal:
        return _refuse(request, battle, str(refusal))
    if _wants_json(request):
        return JsonResponse(battle_payload(battle, actor, [], request))
    return _back_or_detail(request, battle)


# Single source of truth for how long a combatant name may be: the model.
COMBATANT_NAME_MAX_LENGTH = column_length(BattleCombatant, "display_name")

# characters.constants.CHARACTER_NAME_PATTERN plus parentheses, which
# duplicated beast names ("Cougar (2)") already carry.
COMBATANT_NAME_PATTERN = re.compile(
    rf"\A[{CHARACTER_NAME_CHARACTERS}()]+\Z", re.UNICODE
)


@require_POST
@_internal_errors_logged(json_only=True)
def battle_rename(request, pk):
    """Rename one combatant: row, state_json, future event names.

    The battle's DM or its maker (#305).
    """
    actor, battle = _actor_and_readable_battle(request, pk)
    denied = _control_denied(actor, battle)
    if denied is not None:
        return denied
    data, err = _parse_json_body(request)
    if err:
        return err
    combatant_id_raw = data.get("combatant_id")
    if combatant_id_raw is None:
        return _json_error(refusal_words().views.numeric_combatant_id)
    try:
        combatant_id = whole_number(combatant_id_raw)
    except ValueError:
        return _json_error(refusal_words().views.numeric_combatant_id)
    if not commands_foes(actor, battle):
        seat = battle.combatants.filter(pk=combatant_id).first()
        if not actor.holds_seat(seat):
            return _json_error(
                refusal_words().views.foes_belong_to_game_master, status=403
            )
    name = data.get("name") or ""
    # A list has no strip(); it raised AttributeError, a 500 (#767).
    if not isinstance(name, str):
        return _json_error(refusal_words().views.combatant_needs_name)
    name = name.strip()
    if not name:
        return _json_error(refusal_words().views.combatant_needs_name)
    refusal = too_long("Name", name, COMBATANT_NAME_MAX_LENGTH)
    if refusal:
        return _json_error(refusal)
    if not COMBATANT_NAME_PATTERN.match(name):
        return _json_error(refusal_words().views.name_characters)
    try:
        battle, combatant = rename_combatant(battle.pk, combatant_id, name)
    except BattleRefusal as rename_error:
        return _json_error(str(rename_error))
    return JsonResponse(
        {
            "success": True,
            "name": combatant.display_name,
            "view": profiles.view_of(
                battle, detail_ids=detail_visible_ids(actor, battle)
            ),
        }
    )


@require_POST
@_internal_errors_logged()
def battle_start(request, pk):
    """Close the lobby and start the battle: its DM or its maker (#305)."""
    actor, battle = _actor_and_readable_battle(request, pk)
    if not can_control(actor, battle):
        return _detail_with_error(
            request,
            battle,
            refusal_words().views.start_denied,
            status=403,
        )
    try:
        start_battle(battle.pk)
    except BattleRefusal as start_error:
        return _detail_with_error(request, battle, str(start_error))
    return redirect("battle:detail", pk=pk)


@require_POST
@_internal_errors_logged()
def seat_claim(request, pk, combatant_id):
    """Claim one open seat, bringing or generating a character.

    Open to anyone who can read the battle, guests included (#305) — a
    guest may only generate, which ``services.claim_seat`` refuses rather
    than silently ignores, and the lobby says so beside the control.
    """
    actor, battle = _actor_and_readable_battle(request, pk, claim=True)
    character_id_raw = (request.POST.get("character_id") or "").strip()
    character_id = None
    if character_id_raw:
        try:
            character_id = int(character_id_raw)
        except ValueError:
            return _detail_with_error(
                request, battle, refusal_words().views.numeric_character_id
            )
    archetype = (request.POST.get("archetype") or "").strip()
    try:
        claim_seat(
            battle.pk,
            combatant_id,
            actor.user,
            guest_key=actor.guest_key,
            character_id=character_id,
            archetype=archetype,
        )
    except BattleRefusal as claim_error:
        return _detail_with_error(request, battle, str(claim_error))
    return redirect("battle:detail", pk=pk)


@require_POST
@_internal_errors_logged()
def seat_release(request, pk, combatant_id):
    """Give a claimed seat back to the lobby (its holder, or any DM)."""
    actor, battle = _actor_and_readable_battle(request, pk)
    try:
        release_seat(battle.pk, combatant_id, actor.user, guest_key=actor.guest_key)
    except BattleRefusal as release_error:
        return _detail_with_error(request, battle, str(release_error))
    return redirect("battle:detail", pk=pk)


@require_POST
@_internal_errors_logged()
def seat_ready(request, pk, combatant_id):
    """Set your seat's ready flag; may auto-start the battle (see services)."""
    actor, battle = _actor_and_readable_battle(request, pk)
    ready = _form_flag(request.POST.get("ready"))
    try:
        set_seat_ready(
            battle.pk,
            combatant_id,
            actor.user,
            guest_key=actor.guest_key,
            ready=ready,
        )
    except BattleRefusal as ready_error:
        return _detail_with_error(request, battle, str(ready_error))
    return redirect("battle:detail", pk=pk)


def _seat_hand_off(
    request,
    pk: int,
    combatant_id: int,
    hand_off: Callable[..., tuple[BattleCombatant, BattleEvent | None]],
) -> HttpResponse:
    """Run a seat hand-off and answer in the battle payload's shape.

    Only ``battle.js`` posts these (the roster card's button), so the answer
    is always JSON, as the advance endpoint's is: the roster that comes back
    says who plays the figure now, and the log's line comes with it.
    """
    actor, battle = _actor_and_readable_battle(request, pk)
    # A guest is refused by the service itself, in that service's words.
    try:
        _seat, event = hand_off(battle.pk, combatant_id, actor.user)
    except BattleRefusal as refusal:
        return _json_error(str(refusal), status=403)
    battle.refresh_from_db()
    return JsonResponse(
        battle_payload(battle, actor, [event] if event is not None else [], request)
    )


@require_POST
@_internal_errors_logged(json_only=True)
def seat_take(request, pk, combatant_id):
    """A Game Master takes the seat of a character's absent owner (#853)."""
    return _seat_hand_off(
        request,
        pk,
        combatant_id,
        lambda battle_id, seat_id, user: take_seat(
            battle_id, seat_id, game_master=user
        ),
    )


@require_POST
@_internal_errors_logged(json_only=True)
def seat_return(request, pk, combatant_id):
    """Hand a taken seat back to the character's owner (#853)."""
    return _seat_hand_off(
        request,
        pk,
        combatant_id,
        lambda battle_id, seat_id, user: return_seat(battle_id, seat_id, user=user),
    )


@require_POST
@_internal_errors_logged(json_only=True)
def battle_act(request, pk):
    """The awaited player's turn choice (or the DM handing it to the AI).

    Records the choice and resumes the paused turn; answers in the advance
    endpoint's shape so the page applies it exactly like an advance.

    ``combatant_id`` and ``turn`` are required and name the pause the choice
    was composed for (#644): the service refuses a submission that names a
    different one rather than applying it to whoever is awaited now.
    ``push_mana`` (optional, 0 when absent) picks a pushed cast the menu
    offered (#960), ``borrow`` (optional, false when absent) a borrowing
    cast (tarmar-engine #28), ``anchor_target`` (optional, null when
    absent) the combatant whose Runaway an anchor takes back (tarmar-engine
    #30), and ``counter_mana`` (optional, null when absent) the mana a
    counter commits against the caster ``target_id`` names (tarmar-engine
    #31).
    """
    actor, battle = _actor_and_readable_battle(request, pk)
    data, err = _parse_json_body(request)
    if err:
        return err
    as_ai = bool(data.get("ai"))
    try:
        combatant_id = whole_number(data["combatant_id"])
    except (KeyError, ValueError):
        return _json_error(refusal_words().views.choice_combatant_id)
    try:
        turn = whole_number(data["turn"])
    except (KeyError, ValueError):
        return _json_error(refusal_words().views.choice_turn)
    target_id_raw = data.get("target_id")
    target_id = None
    if target_id_raw is not None:
        try:
            target_id = whole_number(target_id_raw)
        except ValueError:
            return _json_error(refusal_words().views.numeric_target_id)
    try:
        push_mana = whole_number(data.get("push_mana") or 0)
    except ValueError:
        return _json_error(refusal_words().views.numeric_push_mana)
    borrow = data.get("borrow", False)
    if not isinstance(borrow, bool):
        return _json_error(refusal_words().views.borrow_true_or_false)
    anchor_target_raw = data.get("anchor_target")
    anchor_target = None
    if anchor_target_raw is not None:
        try:
            anchor_target = whole_number(anchor_target_raw)
        except ValueError:
            return _json_error(refusal_words().views.numeric_anchor_target)
    counter_mana_raw = data.get("counter_mana")
    counter_mana = None
    if counter_mana_raw is not None:
        try:
            counter_mana = whole_number(counter_mana_raw)
        except ValueError:
            return _json_error(refusal_words().views.numeric_counter_mana)
    destination_raw = data.get("destination")
    destination = None
    if destination_raw is not None:
        if not isinstance(destination_raw, list) or len(destination_raw) != 2:
            return _json_error(refusal_words().views.destination_is_hex)
        try:
            destination = (
                whole_number(destination_raw[0]),
                whole_number(destination_raw[1]),
            )
        except ValueError:
            return _json_error(refusal_words().views.destination_is_hex)
    try:
        battle, events = record_player_action(
            battle.pk,
            actor.user,
            guest_key=actor.guest_key,
            combatant_id=combatant_id,
            turn=turn,
            letter=str(data.get("letter") or ""),
            target_id=target_id,
            spell_key=str(data.get("spell_key") or ""),
            push_mana=push_mana,
            borrow=borrow,
            anchor_target=anchor_target,
            counter_mana=counter_mana,
            destination=destination,
            as_ai=as_ai,
        )
    except BattleRefusal as act_error:
        return _json_error(str(act_error))
    return JsonResponse(battle_payload(battle, actor, events, request))


@require_GET
def battle_state(request, pk):
    """Poll endpoint: the battle's current shape, plus events after a marker.

    ``?after=<sequence>`` returns only events later than that battle-global
    sequence number, so followers append incrementally; omitted, no events
    are included (the page already rendered them server-side).
    """
    actor, battle = _actor_and_readable_battle(request, pk)
    after_raw = (request.GET.get("after") or "").strip()
    events = []
    if after_raw:
        try:
            after = int(after_raw)
        except ValueError:
            return _json_error(refusal_words().views.numeric_after_marker)
        events = list(battle.events.filter(sequence__gt=after))
    return JsonResponse(battle_payload(battle, actor, events, request))
