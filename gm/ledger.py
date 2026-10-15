"""The GM session's character ledger (issues #356, #571).

The model used to be the ledger: attributes, skill points, years, money and
equipment lived only in the conversation and the model did the sums in prose.
It got them wrong, agreed when corrected, and then got them wrong again.

The arithmetic is taken away from the model:

* a session's characters are ``characters.Character`` rows from their first
  change (issue #571, :mod:`gm.roster`), and every change a turn's plan asks
  for goes through the rules in :mod:`characters.services` (issue #590), so
  the model's only job is to *say what the player wants*;
* the numbers the Characters pane shows are a :class:`DraftLedger` snapshot
  of a character's field values, read through ``Character``'s own properties
  -- triangular skill costs, the XP thresholds, aging, Fatigue and Body all
  come from ``characters.skills`` and ``Character`` itself. The model reads
  the same records in each call's state block (:mod:`gm.turn_engine.state`).
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from characters.models import Character
from characters.rules import character_rules
from characters.skills import (
    calculate_attribute_modifier,
    calculate_skill_points_from_xp,
    format_skill_level,
)

from .character_sheet import ATTRIBUTE_LABELS

logger = logging.getLogger(__name__)

# The ``Character`` field names a ledger snapshot reads (issue #571: taken
# from the character's record). :func:`draft_character` feeds them straight
# back into ``Character(**values)``, so every rule, property and validator on
# the model applies unchanged.
DRAFT_FIELDS: tuple[str, ...] = (
    "name",
    "strength",
    "dexterity",
    "intelligence",
    "wisdom",
    "constitution",
    "charisma",
    "fatigue_roll",
    "provenance_class",
    "provenance_detail",
    "location",
    # Whether each part of the background is on the record (issues #749,
    # #754), so the ledger can say which were never rolled.
    "provenance_recorded",
    "location_recorded",
    "wealth_recorded",
    # The skill holding the Origin's grant (issue #754).
    "origin_skill",
    # The literacy check's record (issues #741, #742, #743): the roll, whether
    # one was ever made, which the Origin lock hangs on, and a Game Master's
    # ruling at "Consult GM".
    "literacy_roll",
    "literacy_rolled",
    "literacy_ruling",
    "wealth_level",
    "starting_coin",
    "skills",
    "free_skill_points",
    "free_spell_points",
    "equipment",
    "total_xp",
    "age",
    "experience_terms",
    "experience_log",
    "money_earned",
    "aging_str_penalty",
    "aging_dex_penalty",
    "aging_int_penalty",
    "aging_wis_penalty",
    "aging_con_penalty",
    "is_alive",
    "death_cause",
)

# The six core attributes come from gm.character_sheet's one table of
# (label, base field, effective field) -- the saved sheet and the draft ledger
# must name and order them identically or the panel reads as two designs.
ATTRIBUTE_FIELDS: tuple[tuple[str, str], ...] = tuple(
    (label, base_field) for label, base_field, _effective in ATTRIBUTE_LABELS
)


def draft_defaults() -> dict[str, Any]:
    """What an empty draft holds before the player has decided anything.

    Every value is ``Character``'s own field default; the attributes and the
    Fatigue roll have no default on the model and stay ``None`` until rolled,
    which is what lets the ledger say "not set yet" instead of inventing a
    number. The age is the loaded canon's starting age (issue #59), read when
    asked, so this is a function rather than a module constant.
    """
    return {
        "name": "",
        "strength": None,
        "dexterity": None,
        "intelligence": None,
        "wisdom": None,
        "constitution": None,
        "charisma": None,
        "fatigue_roll": None,
        "provenance_class": "commoner",
        "provenance_detail": "Laborer",
        "location": "Village",
        "provenance_recorded": True,
        "location_recorded": True,
        "wealth_recorded": True,
        "origin_skill": "",
        "literacy_roll": None,
        "literacy_rolled": False,
        "literacy_ruling": None,
        "wealth_level": "Moderate",
        "starting_coin": 100,
        "skills": {},
        "free_skill_points": 0,
        "free_spell_points": 0,
        "equipment": [],
        "total_xp": 0,
        "age": character_rules().age.starting,
        "experience_terms": 0,
        "experience_log": [],
        "money_earned": 0,
        "aging_str_penalty": 0,
        "aging_dex_penalty": 0,
        "aging_int_penalty": 0,
        "aging_wis_penalty": 0,
        "aging_con_penalty": 0,
        "is_alive": True,
        "death_cause": "",
    }


# Key on a draft holding what the conversation called the character before,
# or besides, its name -- "the brother" -- so a later "the brother is
# called Twain" still finds him (issue #390). Not one of DRAFT_FIELDS.
LABEL_KEY = "_label"

# Key on a draft holding skill points a Game Master spent past what the
# character earned (issue #404). ``free_skill_points`` cannot go below zero,
# so an override is recorded as a grant: the account stays true and the
# ledger and pane can say where the extra points came from. Not one of
# DRAFT_FIELDS; it travels with the character through the roster.
GAME_MASTER_SKILL_POINTS_KEY = "_game_master_skill_points"


@dataclass(frozen=True)
class AttributeLine:
    """One core attribute, as the ledger shows it."""

    label: str
    base: int | None
    effective: int | None
    modifier: int | None

    @property
    def is_set(self) -> bool:
        """Whether the player has actually rolled or bought this attribute."""
        return self.base is not None


@dataclass(frozen=True)
class SkillLine:
    """One skill on the draft: name, level (roman, per ``format_skill_level``), points."""

    name: str
    level_display: str
    points: int


@dataclass(frozen=True)
class EquipmentLine:
    """One item the draft is carrying."""

    name: str
    quantity: int


@dataclass(frozen=True)
class SkillPointAccount:
    """Where the draft's skill points came from and where they went.

    The issue's deliverable 3 asks for "free skill points remaining **and how
    they were earned**", because a bare total is exactly what the model used to
    argue about. Every figure here is derived from stored data through
    ``characters.skills``; none of it is a second copy of the rules.
    """

    from_experience_xp: int
    from_track_grants: int
    spent: int
    remaining: int
    # Points a Game Master spent past the earned total (issue #404).
    from_game_master: int = 0
    # Free points the breakdown cannot account for: the balance holds more
    # than everything listed above granted. An imported character carries
    # free points with no XP behind them, and then the parts do not add up.
    # Named rather than folded into a negative "spent" (issue #690).
    unexplained: int = 0

    @property
    def earned(self) -> int:
        """Every free skill point this draft has ever been granted."""
        return self.from_experience_xp + self.from_track_grants + self.from_game_master

    @property
    def adds_up(self) -> bool:
        """Whether the breakdown accounts for the balance (issue #690)."""
        return self.unexplained == 0


@dataclass(frozen=True)
class DraftLedger:
    """A read-only snapshot of the draft, for the panel and for the model.

    The chat page's Characters pane and the hidden ledger entry are both
    rendered from this one snapshot, so what the player sees and what the
    model is told cannot drift apart -- the same discipline
    :class:`gm.character_sheet.CharacterSheet` follows for saved characters.
    """

    name: str
    attributes: tuple[AttributeLine, ...]
    attributes_known: bool
    # The stored 2d6 roll, carried so the pools line can name what is actually
    # missing: Fatigue needs the roll *and* all six attributes, and telling a
    # player to roll 2d6 again when the roll is already in is a lie the model
    # will repeat (issue #374).
    fatigue_roll: int | None
    fatigue: int | None
    body: int | None
    mana: int
    skills: tuple[SkillLine, ...]
    skill_points: SkillPointAccount
    equipment: tuple[EquipmentLine, ...]
    total_xp: int
    level: int
    age: int
    experience_years: int
    experience_tracks: tuple[str, ...]
    money_earned: int
    # None for a Rich character until a Game Master sets it (issue #749).
    starting_coin: int | None
    wealth_level: str
    provenance_class: str
    provenance_detail: str
    location: str
    # Whether each part of the background was rolled or set (issue #754), and
    # the whole of it on one line with what is not rolled marked, read off
    # ``Character.background_summary`` as the state block reads it.
    provenance_recorded: bool
    location_recorded: bool
    wealth_recorded: bool
    background: str
    # The literacy check and its outcome in the sheet's own words, e.g.
    # "3d6 ≤ INT − 2, Literate (rolled 9 ≤ 12 − 2)" (issue #742): the line
    # ``CharacterSheet.literacy`` prints for a saved character, read off the
    # same property so the two panels cannot word it differently.
    literacy: str
    is_alive: bool
    death_cause: str

    @property
    def pools_pending(self) -> str:
        """What Fatigue and Body are still waiting for, or ``""`` when they are known.

        Fatigue is CON + WIS + INT + max(DEX, STR) + the 2d6 roll, so it needs
        every attribute *and* the roll. Read by both readers -- the model's
        block through :func:`_format_pools` and the panel's tooltip -- so
        neither can name a missing piece the other does not (issue #374).
        """
        if self.fatigue is not None:
            return ""
        missing = []
        if not self.attributes_known:
            missing.append("all six attributes")
        if self.fatigue_roll is None:
            missing.append("the 2d6 Fatigue roll")
        return " and ".join(missing)

    @classmethod
    def from_draft(cls, values: Mapping[str, Any]) -> "DraftLedger":
        """Snapshot a stored draft through ``Character``'s own properties.

        Args:
            values: The stored draft mapping (see :data:`DRAFT_FIELDS`).

        Returns:
            The ledger, with skills sorted by name so the rendering -- and
            therefore its hash -- does not depend on JSON key order.
        """
        character = draft_character(values)
        known = attributes_known(values)
        attributes = tuple(
            _attribute_line(character, label, base_field, effective_field, known)
            for label, base_field, effective_field in ATTRIBUTE_LABELS
        )
        pools_known = known and values.get("fatigue_roll") is not None
        skills = tuple(
            SkillLine(
                name=skill_name,
                level_display=format_skill_level(points, skill_name),
                points=points,
            )
            for skill_name, points in sorted((character.skills or {}).items())
        )
        # Resolved against the catalog rather than read off the stored
        # entry, which carries no name for a bought item (issue #718).
        equipment = tuple(
            EquipmentLine(name=row.name, quantity=row.quantity)
            for row in character.equipment_rows
        )
        return cls(
            name=character.name or "(unnamed)",
            attributes=attributes,
            attributes_known=known,
            fatigue_roll=character.fatigue_roll,
            fatigue=character.fatigue if pools_known else None,
            body=character.body if pools_known else None,
            mana=character.free_spell_points,
            skills=skills,
            skill_points=skill_point_account(
                character, int(values.get(GAME_MASTER_SKILL_POINTS_KEY, 0) or 0)
            ),
            equipment=equipment,
            total_xp=character.total_xp,
            level=character.level,
            age=character.age,
            experience_years=character.experience_years,
            experience_tracks=experience_tracks(character),
            money_earned=character.money_earned,
            starting_coin=character.starting_coin,
            wealth_level=character.wealth_level,
            provenance_class=character.provenance_class,
            provenance_detail=character.provenance_detail,
            location=character.location,
            provenance_recorded=character.provenance_recorded,
            location_recorded=character.location_recorded,
            wealth_recorded=character.wealth_recorded,
            background=character.background_summary,
            literacy=character.literacy_summary,
            is_alive=character.is_alive,
            death_cause=character.death_cause,
        )


def _attribute_line(
    character: Character,
    label: str,
    base_field: str,
    effective_field: str,
    known: bool,
) -> AttributeLine:
    """One attribute row, blank until every attribute has been decided.

    ``effective_*`` subtracts an aging penalty, so it can only be read once
    the base score exists; an undecided attribute reports ``None`` rather than
    a stand-in number the model might quote.
    """
    if not known:
        return AttributeLine(
            label=label,
            base=getattr(character, base_field),
            effective=None,
            modifier=None,
        )
    effective = getattr(character, effective_field)
    return AttributeLine(
        label=label,
        base=getattr(character, base_field),
        effective=effective,
        modifier=calculate_attribute_modifier(effective),
    )


def draft_character(values: Mapping[str, Any]) -> Character:
    """Rehydrate a stored draft as an **unsaved** :class:`Character`.

    This is the whole trick of this module. The returned object is a real
    ``Character``, so ``fatigue``, ``body``, ``level``, ``experience_years``,
    ``total_allocated_skill_points`` and ``apply_experience_run`` are the
    model's own -- there is no second implementation of any rule here. It is
    never saved and has no ``user``, so it can never appear in anyone's
    character list.

    Args:
        values: The stored draft mapping; missing keys take their default.

    Returns:
        An unsaved ``Character`` carrying the draft's values.
    """
    defaults = draft_defaults()
    fields = {field: values.get(field, defaults[field]) for field in DRAFT_FIELDS}
    return Character(**fields)


def draft_values(character: Character) -> dict[str, Any]:
    """The storable mapping for ``character`` -- the inverse of :func:`draft_character`."""
    return {field: getattr(character, field) for field in DRAFT_FIELDS}


def attributes_known(values: Mapping[str, Any]) -> bool:
    """Whether all six core attributes have been decided on this draft."""
    defaults = draft_defaults()
    return all(
        values.get(field, defaults[field]) is not None
        for _label, field in ATTRIBUTE_FIELDS
    )


def experience_tracks(character: Character) -> tuple[str, ...]:
    """The tracks this draft has played, in the order the runs were taken."""
    tracks: list[str] = []
    for entry in character.experience_log or []:
        if not isinstance(entry, dict):
            continue
        track = entry.get("track")
        if isinstance(track, str):
            tracks.append(track)
    return tuple(tracks)


def track_grant_points(character: Character) -> int:
    """Free skill points granted by "Skill Point" results on the track tables.

    Read back out of the experience log rather than stored separately, so it
    stays true to what the runs actually rolled.
    """
    total = 0
    for entry in character.experience_log or []:
        if not isinstance(entry, dict):
            continue
        gained = entry.get("skills_gained")
        if isinstance(gained, dict):
            total += int(gained.get("skill point", 0))
    return total


def skill_point_account(character: Character, granted: int = 0) -> SkillPointAccount:
    """Where the draft's free skill points came from, and how many are left.

    ``remaining`` is the authoritative balance ``Character.free_skill_points``
    carries -- maintained by ``apply_experience_run`` and decremented by
    :func:`apply_change` through the same arithmetic the workbench's allocate
    endpoint uses. The breakdown explains it; it never redefines it.

    ``spent`` is inferred from the rest -- what was granted, less what is
    left -- and the two sides are maintained independently:
    ``apply_experience_run`` adds the delta a run earned to the balance,
    while ``from_experience_xp`` here recomputes the total from ``total_xp``.
    A character whose free points did not come only from runs starting at
    zero XP therefore had them disagree, and an imported character with free
    points but no XP reported a negative number of points spent -- a figure
    that was not a count of anything (issue #690).

    So ``spent`` is never negative now, and ``unexplained`` carries the size
    of the disagreement when there is one: free points the breakdown cannot
    account for. Nothing is quietly corrected. ``remaining`` stays the
    authoritative balance either way, and the panel and the ledger entry say
    plainly when the parts do not add up rather than showing a figure that
    silently does not.
    """
    from_xp = calculate_skill_points_from_xp(character.total_xp)
    from_grants = track_grant_points(character)
    remaining = character.free_skill_points
    inferred = from_xp + from_grants + granted - remaining
    return SkillPointAccount(
        from_experience_xp=from_xp,
        from_track_grants=from_grants,
        spent=max(0, inferred),
        remaining=remaining,
        from_game_master=granted,
        unexplained=max(0, -inferred),
    )


def draft_identity(draft: Mapping[str, Any]) -> str:
    """How a draft is named where several are in progress (issue #390).

    Its name, with the label beside it when the conversation also calls it
    something else; the label alone before it has a name.
    """
    name = str(draft.get("name") or "").strip()
    label = str(draft.get(LABEL_KEY) or "").strip()
    if name and label and name.casefold() != label.casefold():
        return f"{name} ({label})"
    return name or label or "unnamed character"
