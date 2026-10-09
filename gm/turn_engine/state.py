"""The state block both of a turn's model calls open their last message with (#590).

It says who sent the latest message, lists the session's characters by
record id, with their key figures, as the records hold them now, and lists
the house rules the Game Master has ruled for this table (issue #600). It is
rebuilt from the rows every time it is sent, so it is never stale and never
accumulates in the history the way the old hidden sheet and ledger blocks did.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.db.models import Q

from characters.constants import CORE_ATTRIBUTE_FIELDS
from characters.magic import known_spells
from characters.models import Character
from characters.purse import format_purse
from characters.skill_checks import governing_attribute, injury_penalty
from characters.skills import format_skill_level
from characters.weapon_skill import is_weapon_skill

from .. import roster, summary
from ..character_sheet import pools_line
from ..models import (
    PLACEHOLDER_SESSION_NAME,
    CampaignState,
    GMEvent,
    GMSession,
    SceneEntity,
    StoryThread,
    SupplyItem,
    TableRuling,
)
from ..retrieval import (
    RECAP_KIND,
    ChatMessage,
    estimate_message_tokens,
    is_played_entry,
)
from . import events
from .policy import SETTLED_REFUSALS, TurnPolicy

#: The heading above the character list.
ROSTER_HEADING = "Session characters:"

#: What the list says when the session has no characters.
NO_CHARACTERS = "- none yet"

#: The heading above the table's rulings, left out when there are none.
RULINGS_HEADING = "Rulings at this table:"

#: Headings for the campaign records, each left out when it holds nothing
#: (issue #579).
WHERE_HEADING = "Where the party is:"
SUPPLIES_HEADING = "Carried between them (change one with adjust_supplies):"
PEOPLE_HEADING = "People met:"
THINGS_HEADING = (
    "Things in play (where a thing's record and a person's description "
    "disagree, the thing's record is right):"
)
THREADS_HEADING = "Still open:"

#: The heading above what this session already tried and was refused.
REFUSED_HEADING = "Already refused in this session, do not plan again:"

#: How many distinct refusals the block carries. Enough to break a loop,
#: few enough that the block stays short.
MAX_REFUSALS_LISTED = 5

#: How far back to look for them.
REFUSAL_LOOKBACK = 80

#: The most lines any one campaign section puts in the block (issue #647).
#:
#: Nothing bounded these. The block is rebuilt whole for both of a turn's
#: model calls and rides in the call's *last* message, appended after the
#: context guard has run, so the guard -- which only ever drops canon
#: excerpts from the prefix -- never sees it, and the fold's budget never
#: measured it. A SceneEntity line can carry a name and four 200-character
#: details, and record_npc/record_object are prompted to be used "the first
#: time it is named", so the list grows with the campaign: at 150 recorded
#: things the block alone was larger than the whole default window, and the
#: model server truncated it silently -- the failure folding exists to
#: prevent.
MAX_CAMPAIGN_LINES_LISTED = 12


#: The topics a ledger figure can be about, as this module names them. A
#: topic is a running total the record maintains -- the kind of number the
#: model used to work out in its head -- not a rolled score (issue #622).
SKILL_POINTS_TOPIC = "skill points"
EXPERIENCE_POINTS_TOPIC = "xp"
LEVEL_TOPIC = "level"
EXPERIENCE_YEARS_TOPIC = "years of experience"
AGE_TOPIC = "age"
FATIGUE_TOPIC = "fatigue"
BODY_TOPIC = "body"


@dataclass(frozen=True)
class LedgerFigure:
    """One running total a record holds, ready to be quoted (issue #622).

    Attributes:
        topic: What the figure is about, one of the ``*_TOPIC`` constants.
        value: The number the record holds, read straight off it.
        phrase: The figure written out as :func:`key_figures` writes it,
            e.g. ``"8 free skill points"``.
    """

    topic: str
    value: int
    phrase: str


def ledger_figures(character: Character) -> tuple[LedgerFigure, ...]:
    """The running totals ``character``'s record holds, each as one phrase.

    The same numbers :func:`key_figures` writes into the state block, kept
    apart from the prose so the server can quote one back when a reply that
    was asked for it states none (issue #622). Nothing here is computed: each
    value is a field of the record.

    Args:
        character: The record to read.

    Returns:
        One figure per total the record has. Fatigue and Body are left out
        until they have been rolled.
    """
    figures = [
        LedgerFigure(
            SKILL_POINTS_TOPIC,
            character.free_skill_points,
            f"{character.free_skill_points} free skill point"
            f"{'' if character.free_skill_points == 1 else 's'}",
        ),
        LedgerFigure(
            EXPERIENCE_POINTS_TOPIC, character.total_xp, f"XP {character.total_xp}"
        ),
        LedgerFigure(LEVEL_TOPIC, character.level, f"level {character.level}"),
        LedgerFigure(
            EXPERIENCE_YEARS_TOPIC,
            character.experience_years,
            f"{character.experience_years} year"
            f"{'' if character.experience_years == 1 else 's'} of experience",
        ),
        LedgerFigure(AGE_TOPIC, character.age, f"age {character.age}"),
    ]
    # What the record holds now (issue #751): after a fight, the pools it
    # left, with the maximum beside a pool that is down.
    for topic, label, current, maximum in (
        (FATIGUE_TOPIC, "Fatigue", character.current_fatigue, character.fatigue),
        (BODY_TOPIC, "Body", character.current_body, character.body),
    ):
        if current is None or maximum is None:
            continue
        phrase = f"{label} {current}" + (f" of {maximum}" if current != maximum else "")
        figures.append(LedgerFigure(topic, current, phrase))
    return tuple(figures)


@dataclass(frozen=True)
class RosterLine:
    """One of the session's characters, as the state block lists it.

    Attributes:
        character_id: The record's id, which plans name it by.
        name: The record's name.
        label: What the conversation calls it, when that differs.
        figures: The key figures, already written out.
        ledger: The same running totals one by one, for a reply that has to
            quote one back (issue #622).
        injured: Whether the injury table's -1 or -2 applies to the
            character's rolls now (``characters.skill_checks.injury_penalty``),
            so a player's roll for them must say which way it is read (#886).
    """

    character_id: int
    name: str
    label: str
    figures: str
    ledger: tuple[LedgerFigure, ...] = ()
    injured: bool = False

    @property
    def called(self) -> str:
        """The name, with the label beside it when the two differ."""
        if self.label and self.label.casefold() != self.name.casefold():
            return f"{self.name} ({self.label})"
        return self.name

    @property
    def line(self) -> str:
        """``- id 41: Twain (the brother) — figures``."""
        return f"- id {self.character_id}: {self.called} — {self.figures}"


#: The most skills, items or spells one character's line lists (issue #771):
#: the first in the sheet's order are kept and the rest counted. The same
#: bound the campaign sections are held to (issue #647), because the line
#: rides in the last message of both of a turn's calls, where the context
#: guard cannot reach it.
MAX_SHEET_ENTRIES_LISTED = MAX_CAMPAIGN_LINES_LISTED


def _listed(entries: Sequence[str]) -> str:
    """``entries`` joined, capped at :data:`MAX_SHEET_ENTRIES_LISTED`."""
    kept = ", ".join(entries[:MAX_SHEET_ENTRIES_LISTED])
    more = len(entries) - MAX_SHEET_ENTRIES_LISTED
    return f"{kept}, and {more} more" if more > 0 else kept


def _attribute(character: Character, name: str, short: str) -> str:
    """One attribute: its base score, and the aged score play uses when it
    differs (issue #771; aging.md). Base scores are for creation-time checks,
    effective ones for play, so the model is given both."""
    base = getattr(character, name)
    if base is None:
        return f"{short} not recorded"
    effective = getattr(character, f"effective_{name}", base)
    if effective is not None and effective != base:
        return f"{short} {base} (aged {effective})"
    return f"{short} {base}"


#: The skills an attack reads besides the Weapon skill itself.
_ATTACK_SKILLS = frozenset({"weapon parry", "archery", "brawling"})


def _skill_listing_order(skill: str) -> tuple[int, str]:
    """Where a skill comes in the state block's capped list (review of #771).

    The skills an attack reads first (the Weapon skill, tied or not, Weapon
    Parry, Archery, Brawling), then those a skill check reads (every skill
    with a governing attribute), then the rest; by name within each. A cap
    that kept the first twelve by name cut the Weapon skills, whose names
    sort late, from a long list -- the skills a fight turns on.
    """
    if is_weapon_skill(skill) or skill in _ATTACK_SKILLS:
        return 0, skill
    if governing_attribute(skill) is not None:
        return 1, skill
    return 2, skill


def sheet_parts(character: Character) -> list[str]:
    """What the sheet holds beyond the figures, for the model (issue #771).

    Skills with their levels, what the character carries, the coin held now
    and the spells known: the Game Master's panel had all of these and the
    model none, so "what does her Stealth add?" or "can she afford a horse?"
    were answered from nothing. Each list is capped (issue #647). A part the
    character has nothing in is left out, except the purse, which is a
    figure even when it is empty (issue #843).
    """
    parts: list[str] = []
    held = [
        skill
        for skill, points in (character.skills or {}).items()
        if isinstance(points, int) and points > 0
    ]
    skills = [
        f"{skill} {format_skill_level(character.skills[skill], skill)}"
        for skill in sorted(held, key=_skill_listing_order)
    ]
    if skills:
        parts.append(f"skills: {_listed(skills)}")
    carried = []
    for row in character.equipment_rows:
        said = row.name + (f" ×{row.quantity}" if row.quantity != 1 else "")
        if row.equipped:
            said += " (equipped)"
        carried.append(said)
    if carried:
        parts.append(f"carries: {_listed(carried)}")
    parts.append(f"purse: {format_purse(character)}")
    spells = [
        f"{spell.name} ({spell.mastery_display})" for spell in known_spells(character)
    ]
    if spells:
        parts.append(f"spells: {_listed(spells)}")
    return parts


def key_figures(character: Character) -> str:
    """The figures a turn needs about ``character``, in one line.

    Attributes (with the aged score beside a base one age has lowered),
    Fatigue and Body (or what they still wait for), age, experience, XP and
    level, free skill points, skills, gear, coin and spells, whether they can
    read, and whether they live. Every number is read off the record; nothing
    is added up here that the model then has to trust. The literacy part is
    the sheet's own line (``Character.literacy_summary``), so the model is
    told whether a character can read in the words the player sees (issue
    #742). It appears once the check has been rolled or ruled on: listed as
    "Not rolled", it drew the model into announcing the pending check in
    replies about something else, at the cost of what it was asked to say.
    """
    attributes = ", ".join(
        _attribute(character, name, short) for name, short in CORE_ATTRIBUTE_FIELDS
    )
    pools = pools_line(character)
    living = "alive" if character.is_alive else "dead"
    if not character.is_alive and character.death_cause:
        living = f"dead ({character.death_cause})"
    parts = [
        attributes,
        pools,
        # What start_character rolled, or what is still unrolled (#754).
        f"background: {character.background_summary}",
        f"age {character.age}",
        f"{character.experience_years} year{'' if character.experience_years == 1 else 's'} of experience",
        f"XP {character.total_xp} (level {character.level})",
        f"{character.free_skill_points} free skill points",
        *sheet_parts(character),
        living,
    ]
    decided = (
        character.literacy_rolled
        or character.literacy_roll is not None
        or character.literacy_ruling is not None
    )
    if decided:
        parts.insert(-1, f"literacy: {character.literacy_summary}")
    if character.missing_parts:
        parts.append(", ".join(character.missing_parts))
    return "; ".join(parts)


def roster_lines(session: GMSession | None) -> list[RosterLine]:
    """Every character attached to ``session``, oldest attachment first."""
    return [
        RosterLine(
            character_id=entry.character.pk,
            name=entry.character.name,
            label=entry.label,
            figures=key_figures(entry.character),
            ledger=ledger_figures(entry.character),
            injured=bool(injury_penalty(entry.character)),
        )
        for entry in roster.roster(session)
    ]


def active_rulings(session: GMSession | None) -> list[TableRuling]:
    """The rulings ``session``'s table plays by now, oldest first."""
    if session is None:
        return []
    return list(TableRuling.objects.filter(session=session, active=True))


def ruling_lines(session: GMSession | None) -> list[str]:
    """The active rulings as the state block lists them."""
    return [ruling.line for ruling in active_rulings(session)]


def campaign_lines(session: GMSession | None) -> list[str]:
    """Where the party is and when, as the state block says it."""
    if session is None:
        return []
    state = CampaignState.objects.filter(session=session).first()
    return state.lines if state is not None else []


def _recent_lines(found: Any, *, newest_first: Sequence[str]) -> list[str]:
    """At most :data:`MAX_CAMPAIGN_LINES_LISTED` rows' lines, oldest first.

    The cap keeps the newest rows -- the ones the table is using -- and says
    how many older ones it left out, so the model is never told a partial
    list is the whole record (issue #647). Within the kept window the lines
    stay in the order they were recorded, which is how they read.

    Args:
        found: The rows for one section, in their recorded order.
        newest_first: The ordering that puts the most recent row first.

    Returns:
        The lines, with a closing line when the section was trimmed.
    """
    total = found.count()
    if total <= MAX_CAMPAIGN_LINES_LISTED:
        return [row.line for row in found]
    kept = list(found.order_by(*newest_first)[:MAX_CAMPAIGN_LINES_LISTED])
    kept.reverse()
    return [
        *(row.line for row in kept),
        f"- ... and {total - MAX_CAMPAIGN_LINES_LISTED} older ones, not listed here",
    ]


def supply_lines(session: GMSession | None) -> list[str]:
    """What the party carries, one line per thing, oldest first (issue #579).

    Capped at :data:`MAX_CAMPAIGN_LINES_LISTED`, newest kept (issue #647).
    """
    if session is None:
        return []
    return _recent_lines(
        SupplyItem.objects.filter(session=session),
        newest_first=("-updated_at", "-pk"),
    )


def entity_lines(session: GMSession | None, kind: str) -> list[str]:
    """The people met, or the things in play, oldest first.

    Capped at :data:`MAX_CAMPAIGN_LINES_LISTED`, newest kept (issue #647).
    """
    if session is None:
        return []
    return _recent_lines(
        SceneEntity.objects.filter(session=session, kind=kind),
        newest_first=("-updated_at", "-pk"),
    )


def open_thread_ids(session: GMSession | None) -> list[int]:
    """Every thread this session still has open, by id (issue #627).

    Uncapped, unlike :func:`thread_lines`: this is what a plan's
    ``resolve_thread`` is held to, and an older thread the block no longer
    lists is still one the party may finish.
    """
    if session is None:
        return []
    return list(
        StoryThread.objects.filter(session=session, resolved=False).values_list(
            "pk", flat=True
        )
    )


def person_names(session: GMSession | None) -> list[str]:
    """The names of every person this session has recorded (issue #649).

    Uncapped, unlike :func:`entity_lines`: this decides whose record a
    question is about, and an older NPC the message names is exactly the
    case the cap would hide.
    """
    if session is None:
        return []
    return list(
        SceneEntity.objects.filter(
            session=session, kind=SceneEntity.PERSON
        ).values_list("name", flat=True)
    )


def thread_lines(session: GMSession | None) -> list[str]:
    """The threads still open, oldest first.

    Resolved threads are kept as records but left out here: the model needs
    to know what is still hanging, and a list that grows with everything the
    party ever did would crowd out what does. Capped at
    :data:`MAX_CAMPAIGN_LINES_LISTED`, newest kept (issue #647).
    """
    if session is None:
        return []
    return _recent_lines(
        StoryThread.objects.filter(session=session, resolved=False),
        newest_first=("-created_at", "-pk"),
    )


@dataclass(frozen=True)
class CampaignBlock:
    """Everything the campaign holds, ready for the state block (issue #579).

    Read fresh every turn like the roster, so the narration cannot drift
    from it the way it drifted from the transcript: session 36's iron box
    was on a ledge, then chained, then fused to the rock, with nothing in
    between that would have changed it.
    """

    where: Sequence[str] = ()
    supplies: Sequence[str] = ()
    people: Sequence[str] = ()
    things: Sequence[str] = ()
    threads: Sequence[str] = ()
    #: The recorded people by name, uncapped and unformatted. Not part of
    #: the block: it is how the narration tells a question about one of them
    #: from a question about the player's own character (issue #649).
    person_names: Sequence[str] = ()
    #: Every open thread's id, uncapped. Not part of the block either: it is
    #: what read_plan holds resolve_thread to (issue #627). Uncapped on
    #: purpose -- the listed threads are capped, and closing an older one the
    #: block did not list is a right answer, not an invented id.
    open_thread_ids: Sequence[int] = ()

    @classmethod
    def of(cls, session: GMSession | None) -> "CampaignBlock":
        """Read it from the records."""
        return cls(
            where=campaign_lines(session),
            supplies=supply_lines(session),
            people=entity_lines(session, SceneEntity.PERSON),
            things=entity_lines(session, SceneEntity.THING),
            threads=thread_lines(session),
            person_names=person_names(session),
            open_thread_ids=open_thread_ids(session),
        )

    @property
    def sections(self) -> list[tuple[str, Sequence[str]]]:
        """Each heading with its lines, in the order the block writes them."""
        return [
            (WHERE_HEADING, self.where),
            (SUPPLIES_HEADING, self.supplies),
            (PEOPLE_HEADING, self.people),
            (THINGS_HEADING, self.things),
            (THREADS_HEADING, self.threads),
        ]

    def __bool__(self) -> bool:
        return any(lines for _heading, lines in self.sections)


# --------------------------------------------------------------------------
# Continuing a campaign in a new session (issue #761).
# --------------------------------------------------------------------------

#: How the carried recap opens a continued session's conversation.
CARRIED_RECAP_PREFIX = "The campaign so far, from the last session:"

#: How it opens when only the last session's fold recap could be carried, so
#: neither the model nor the page takes it for the whole story.
CARRIED_FOLD_RECAP_PREFIX = (
    "The campaign up to the last session's most recent recap (its final "
    "turns are not in it):"
)


@dataclass(frozen=True)
class CarriedRecap:
    """The story a continued session opens with (issue #761).

    Attributes:
        text: The recap.
        whole: True when it covers the whole of the last session; False when
            it is that session's fold recap, which stops at its last fold.
    """

    text: str
    whole: bool

    @property
    def prefix(self) -> str:
        """How the opening entry introduces it."""
        return CARRIED_RECAP_PREFIX if self.whole else CARRIED_FOLD_RECAP_PREFIX


def write_carried_recap(
    previous: GMSession, ask_model: Callable[[str], str], num_ctx: int
) -> CarriedRecap | None:
    """Write the story ``previous`` hands on, with one model call (#761).

    Written over the whole of ``previous`` by the fold's own summariser,
    since its fold recap stops where its last fold did and the page promises
    the story so far. Should that call fail, the fold recap is carried and
    said to be one; a session that never folded then carries none.

    Args:
        previous: The session being continued.
        ask_model: Sends a prompt and returns the reply, as the fold's does.
        num_ctx: The model's context window, which bounds the request.

    Returns:
        The recap to carry, or None when there is none.
    """
    written = summary.recap_of_whole_session(
        previous.history,
        previous_summary=previous.summary,
        already_folded=previous.summary_covers,
        ask_model=ask_model,
        num_ctx=num_ctx,
    )
    if written:
        return CarriedRecap(text=written, whole=True)
    if previous.summary:
        return CarriedRecap(text=previous.summary, whole=False)
    return None


def recap_pending(session: GMSession | None) -> bool:
    """Whether ``session`` continues a campaign whose recap is still to write.

    The recap is written on the continued session's first turn, which
    streams with keep-alives, rather than in the form post that opened it
    (second review of #761): a continuation nobody has played, that carries
    no recap yet, and whose source still exists.
    """
    if session is None or session.continued_from_id is None:
        return False
    history = session.history
    if any(is_played_entry(entry) for entry in history):
        return False
    # A turn that failed after filing only the fold-recap fallback leaves
    # the session unplayed; the whole-session recap is tried again on the
    # next turn rather than given up on (third review of #761).
    carried = carried_recap(session)
    return carried is None or not carried.whole


def file_carried_recap(session: GMSession, recap: CarriedRecap) -> ChatMessage:
    """Open ``session``'s stored conversation with ``recap``, and return the entry.

    Written under a lock on the row, at the front of whatever the session
    already holds -- in place of a fallback an earlier try filed -- and made
    the session's recap so far, so its first fold takes it in.
    """
    entry = carried_recap_message(recap)
    with transaction.atomic():
        locked = GMSession.objects.select_for_update().get(pk=session.pk)
        held = locked.history
        if held and carried_recap(locked) is not None:
            held = held[1:]
        locked.history = [entry, *held]
        locked.summary = recap.text
        locked.save(update_fields=["history_json", "summary"])
    return entry


def carried_recap(session: GMSession) -> CarriedRecap | None:
    """The recap ``session`` was opened with, read back from its first entry."""
    history = session.history
    opening = history[0] if history else {}
    if opening.get("kind") != RECAP_KIND:
        return None
    content = str(opening.get("content", ""))
    for whole, prefix in (
        (True, CARRIED_RECAP_PREFIX),
        (False, CARRIED_FOLD_RECAP_PREFIX),
    ):
        if content.startswith(f"{prefix}\n"):
            return CarriedRecap(text=content[len(prefix) + 1 :], whole=whole)
    return None


@dataclass(frozen=True)
class CampaignCarry:
    """What a session holds that a new session could continue from (#761).

    Every count is of rows the plan actions wrote, so the offer on the page
    and the copy it makes read the same records.

    Attributes:
        session: The session the records belong to.
        has_place: Whether it recorded where and when the party is.
        people: People met.
        things: Things in play.
        threads: Threads, open or resolved.
        supplies: Things carried.
        rulings: The table's active house rules.
        recap: The session's fold recap, or ``""`` when it never folded.
    """

    session: GMSession
    has_place: bool
    people: int
    things: int
    threads: int
    supplies: int
    rulings: int
    recap: str

    @classmethod
    def of(cls, session: GMSession) -> "CampaignCarry":
        """Count what ``session`` holds."""
        entities = SceneEntity.objects.filter(session=session)
        return cls(
            session=session,
            has_place=CampaignState.objects.filter(session=session).exists(),
            people=entities.filter(kind=SceneEntity.PERSON).count(),
            things=entities.filter(kind=SceneEntity.THING).count(),
            threads=StoryThread.objects.filter(session=session).count(),
            supplies=SupplyItem.objects.filter(session=session).count(),
            rulings=TableRuling.objects.filter(session=session, active=True).count(),
            recap=session.summary,
        )

    def __bool__(self) -> bool:
        return bool(
            self.has_place
            or self.people
            or self.things
            or self.threads
            or self.supplies
            or self.rulings
        )

    @property
    def parts(self) -> list[str]:
        """What would carry over, in words, for the offer on the page."""
        counted = (
            (self.people, "person", "people"),
            (self.things, "thing", "things"),
            (self.threads, "thread", "threads"),
            (self.supplies, "thing carried", "things carried"),
            (self.rulings, "table ruling", "table rulings"),
        )
        said = ["where and when the party is"] if self.has_place else []
        said += [
            f"{count} {one if count == 1 else many}"
            for count, one, many in counted
            if count
        ]
        if self.recap:
            said.append("the story so far")
        return said


def previous_campaign(user: Any) -> CampaignCarry | None:
    """The session a new one of ``user``'s could continue, if any (#761).

    The most recently used of their sessions that has been played and holds
    campaign records. A session with no records has nothing a new one could
    carry, and one nobody has played yet -- a continuation opened and left --
    would only offer the copies it already holds, so both are passed over.
    """
    holding_records = (
        GMSession.objects.filter(user=user)
        .filter(
            Q(campaign__isnull=False)
            | Q(entities__isnull=False)
            | Q(threads__isnull=False)
            | Q(supplies__isnull=False)
            | Q(rulings__active=True)
        )
        .distinct()
        .order_by("-updated_at")
    )
    for session in holding_records:
        if any(is_played_entry(entry) for entry in session.history):
            return CampaignCarry.of(session)
    return None


def carried_recap_message(recap: CarriedRecap) -> ChatMessage:
    """The last session's recap, as the entry that opens the new one.

    Marked as a recap, so it is hidden from the reader, never names the
    session, and ``gm_stream`` still puts the system prompt in front of it
    (issue #631).
    """
    return {
        "role": "user",
        "kind": RECAP_KIND,
        "content": f"{recap.prefix}\n{recap.text}",
    }


def continue_campaign(previous: GMSession, recap: CarriedRecap | None) -> GMSession:
    """Open a new session carrying ``previous``'s campaign records (#761).

    Every record is copied, not moved or shared: the older session stays
    exactly as it was played, and the new one changes its copies through
    the same plan actions as any session. Nothing is carried unless the
    owner asks, which is the one way this is ever called.

    What carries: where and when the party is, what they carry, the people
    and things in play, every thread (a resolved one is what stops the
    narration reopening it), and the table's active rulings. Rows keep the
    turn that recorded them and their timestamps, so where a ruling came from
    stays readable and the state block's cap keeps the same newest rows.

    The story carries as ``recap`` (:func:`write_carried_recap`), the
    opening entry of the new conversation. It is also the new session's
    recap so far, so the first time this session folds it writes a recap
    that takes the old one in rather than losing it.

    Args:
        previous: The session to continue, already checked to be the
            caller's.
        recap: The story to open with, or None to carry the records alone.

    Returns:
        The new session.
    """
    with transaction.atomic():
        session = GMSession.objects.create(
            user=previous.user,
            name=PLACEHOLDER_SESSION_NAME,
            mode=previous.mode,
            continued_from=previous,
            summary=recap.text if recap is not None else "",
        )
        if recap is not None:
            session.history = [carried_recap_message(recap)]
            session.save(update_fields=["history_json"])
        place = CampaignState.objects.filter(session=previous).first()
        if place is not None:
            CampaignState.objects.create(
                session=session,
                place=place.place,
                place_key=place.place_key,
                day=place.day,
                time_of_day=place.time_of_day,
                updated_at=place.updated_at,
            )
        SupplyItem.objects.bulk_create(
            SupplyItem(
                session=session,
                name=item.name,
                amount=item.amount,
                note=item.note,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in SupplyItem.objects.filter(session=previous)
        )
        SceneEntity.objects.bulk_create(
            SceneEntity(
                session=session,
                kind=entity.kind,
                name=entity.name,
                where=entity.where,
                wants=entity.wants,
                condition=entity.condition,
                notes=entity.notes,
                created_at=entity.created_at,
                updated_at=entity.updated_at,
            )
            for entity in SceneEntity.objects.filter(session=previous)
        )
        StoryThread.objects.bulk_create(
            StoryThread(
                session=session,
                text=thread.text,
                opened_turn=thread.opened_turn,
                resolved=thread.resolved,
                resolution=thread.resolution,
                resolved_at=thread.resolved_at,
                created_at=thread.created_at,
            )
            for thread in StoryThread.objects.filter(session=previous)
        )
        TableRuling.objects.bulk_create(
            TableRuling(
                session=session,
                topic=ruling.topic,
                text=ruling.text,
                turn=ruling.turn,
                created_at=ruling.created_at,
            )
            for ruling in TableRuling.objects.filter(session=previous, active=True)
        )
    return session


def _recent_action_payloads(
    session: GMSession, is_game_master: bool | None
) -> list[Any]:
    """The last :data:`REFUSAL_LOOKBACK` action events' payloads, newest first.

    Keyed by role (issue #835): a refusal is only remembered for turns taken
    with the authority the account has now. The role is read off the account
    every turn and an administrator can grant or withdraw it at any time, so
    "only a Game Master may state scores", settled while the account was a
    player, is not a settled answer once it is a Game Master -- and a Game
    Master's refusals say nothing about what the same account may do as a
    player.

    Args:
        session: The session.
        is_game_master: The role to remember refusals for; ``None`` counts
            every turn whatever its role, which is what an estimate of the
            block's size wants.
    """
    found = GMEvent.objects.filter(turn__session=session, kind=events.ACTION)
    if is_game_master is not None:
        found = found.filter(turn__is_game_master=is_game_master)
    return list(
        found.order_by("-id").values_list("payload", flat=True)[:REFUSAL_LOOKBACK]
    )


def refusal_lines(
    session: GMSession | None, *, is_game_master: bool | None = None
) -> list[str]:
    """What this session already tried and was told no, newest first.

    A refusal whose reason cannot change -- a player may not state their own
    attributes -- was invisible to the next turn, so the model planned the
    same action again, and again. One session spent all five of its turns
    that way and recorded nothing (issue #614). Listing them tells the model
    what it has already learned the hard way.

    Distinct by action and reason: the same refusal three times is one line.

    Args:
        session: The session, or ``None`` before there is one.
        is_game_master: Only refusals from turns taken with this authority
            (issue #835); ``None`` for every turn.
    """
    if session is None:
        return []
    recent = _recent_action_payloads(session, is_game_master)
    said: dict[tuple[str, str], None] = {}
    for payload in recent:
        frame = (payload or {}).get("frame") or {}
        error = str(frame.get("error") or "")
        if frame.get("ran") or not error:
            continue
        said.setdefault((str(frame.get("kind") or ""), error), None)
        if len(said) >= MAX_REFUSALS_LISTED:
            break
    return [f"- {kind}: {error}" for kind, error in said]


def settled_refusals(
    session: GMSession | None, *, is_game_master: bool | None = None
) -> dict[str, str]:
    """Actions this session was refused for a reason that cannot change.

    Maps the action to the reason. Only refusals in
    :data:`gm.turn_engine.policy.SETTLED_REFUSALS`: a refusal that turned on
    what the message said may not hold next turn, and must not be held
    against a plan (issue #614). Those reasons turn on the account's role, so
    only turns taken with the role it has now count (issue #835).

    Args:
        session: The session, or ``None`` before there is one.
        is_game_master: The authority the turn asking has; ``None`` for
            every turn.
    """
    settled: dict[str, str] = {}
    if session is None:
        return settled
    recent = _recent_action_payloads(session, is_game_master)
    for payload in recent:
        frame = (payload or {}).get("frame") or {}
        error = str(frame.get("error") or "")
        kind = str(frame.get("kind") or "")
        if frame.get("ran") or error not in SETTLED_REFUSALS or not kind:
            continue
        settled.setdefault(kind, error)
    return settled


def state_block(
    policy: TurnPolicy,
    lines: Sequence[RosterLine],
    rulings: Sequence[str] = (),
    campaign: CampaignBlock | None = None,
    refused: Sequence[str] = (),
) -> str:
    """Who is speaking, the characters, the rulings, and the campaign.

    Args:
        policy: The turn's policy, which says whether the Game Master sent it.
        lines: The roster, from :func:`roster_lines`.
        rulings: The active rulings, from :func:`ruling_lines`. The section is
            left out when there are none.
        campaign: Where the party is, who and what is in play, and what is
            still open, from :meth:`CampaignBlock.of` (issue #579). Each of
            its sections is left out when it holds nothing.
        refused: What this session already tried and was refused, from
            :func:`refusal_lines` (issue #614). Left out when there is
            nothing.

    Returns:
        The block, deterministic for the same speaker and records.
    """
    listed = "\n".join(line.line for line in lines) or NO_CHARACTERS
    block = f"{policy.speaker_line}\n{ROSTER_HEADING}\n{listed}"
    if rulings:
        block += f"\n{RULINGS_HEADING}\n" + "\n".join(rulings)
    for heading, said in (campaign or CampaignBlock()).sections:
        if said:
            block += f"\n{heading}\n" + "\n".join(said)
    if refused:
        block += f"\n{REFUSED_HEADING}\n" + "\n".join(refused)
    return block


def state_block_tokens(session: GMSession | None) -> int:
    """What this session's state block is expected to cost, in tokens.

    Read before a turn is built, so the fold's budget can count it (issue
    #647). It used to count the history alone, while the block -- which is
    rebuilt whole for both of a turn's model calls and rides in the call's
    last message, where the context guard cannot reach it -- grew without
    bound beside it. A long campaign therefore pushed the one message that
    carries the plan schema past the window.

    The speaker line and the surrounding wording are a few dozen tokens on
    top of this; the figure is for deciding whether to fold, not for
    accounting.

    Args:
        session: The session, or ``None`` before there is one.

    Returns:
        The estimated token cost of the block's own lines.
    """
    campaign = CampaignBlock.of(session)
    said = "\n".join(
        [
            *(line.line for line in roster_lines(session)),
            *ruling_lines(session),
            *(one for _heading, lines in campaign.sections for one in lines),
            *refusal_lines(session),
        ]
    )
    return estimate_message_tokens({"role": "user", "content": said})
