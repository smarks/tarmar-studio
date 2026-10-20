"""Skills system logic for character experience and skill allocation.

The numbers are the loaded canon's ``skill-rules.json``, read through
:func:`characters.skill_rules.skill_rules` when a function needs them, never
at import (issue #70): the tracks and their d20 tables, the experience
ladder, the skill cost multipliers, the survival roll, the aging table and
the attribute modifier table. The procedures that use them stay here.
"""

import math
import random
import re
from collections.abc import Mapping
from itertools import pairwise
from pathlib import Path
from typing import Any

from common.number_words import number_in_words

from .constants import ATTRIBUTE_BONUS_FIELDS, CORE_ATTRIBUTE_FIELDS

# The survival roll's dice are the canon's (issue #70; 3d6 on the public
# pages since issue #223). This is the local name, and the patch target the
# tests use, for the roller that returns the faces the survival log stores.
from .dice import roll_d20, roll_survival_dice
from .rules import character_rules

# Re-exported: callers import it from here, as they did before it moved
# (issue #70).
from .skill_names import normalize_skill_name as normalize_skill_name
from .skill_names import skill_name_refusal as skill_name_refusal
from .skill_names import top_level_parentheses
from .skill_rules import (
    AGING_ATTRIBUTES,
    MENTAL_AGING_ATTRIBUTES,
    PHYSICAL_AGING_ATTRIBUTES,
    ExperienceLevel,
    SkillRules,
    SkillRulesError,
    SkillTrack,
    skill_rules,
)


def survival_rule_description() -> str:
    """The survival check written out in words, for the GM to read.

    For the GM to read rather than remember (issue #370: it told a player the
    campaigner track "requires a survival roll of 3d6 <= 12" and killed a
    character on an 18). It is *built* from the canon's survival roll and
    from the comparison ``simulate_experience_years`` actually makes, so the
    sentence cannot drift away from the code the way the model's memory did.
    It lives here, beside the rule, because this module is the single source
    of truth for the check.
    """
    survival_roll = skill_rules().survival_roll
    return (
        f"roll {survival_roll.dice}, add the character's best attribute "
        "modifier, and compare that total with the track's survivability "
        "target — the character survives when the total is greater than or "
        "equal to the target, so the roll must go HIGH. A natural "
        f"{survival_roll.automatic_survival_total} on the dice is an automatic "
        "survival whatever the target, and a natural "
        f"{survival_roll.automatic_death_total} is an automatic death."
    )


# How many years one run of the prior-experience workbench may play. One year
# is the default and the first-class case; the maximum is the upper bound on
# the "Years" input, generous enough for a whole career in a click and small
# enough that a single request stays bounded.
DEFAULT_RUN_YEARS = 1
MAX_EXPERIENCE_YEARS_PER_RUN = 20

# Skill levels cost a *triangular* number of points: level 1 costs 1 point,
# level 2 costs 2 more, level 3 costs 3 more, and so on, so the cumulative cost
# of level N is 1+2+...+N = N(N+1)/2 -> 1, 3, 6, 10, 15, 21, ...
# The formulas below are the source of truth and there is **no cap on skill
# level and no cap on points spent** (issue #146): allocation, import, and the
# experience simulator all price a skill the same way, so a term-built
# character's export re-imports. See reference/content/public-rules/characters/
# prior-experience/skills-glossary.md.
#
# Some skills are priced as a multiple of that: the canon's
# ``skill_cost.multipliers``. On the public pages **Arcane** (True Magic)
# costs double: level N costs 2xN more points, so reaching level N costs
# N(N+1) in total -> 2, 6, 12, 20, 30, 42, ... (issue #154). Every function
# that converts between points and levels takes the skill name so the
# exception is applied in exactly one place; passing no name means "an
# ordinary skill".

# The Arcane skill, as ``normalize_skill_name`` stores it: the key its cost
# multiplier is filed under and the skill Channel reads. The match is exact
# normalized equality, so "Arcane", "ARCANE", and "Arcane III" all hit it while
# a differently named skill that merely contains the word ("arcane lore") is an
# ordinary skill at ordinary cost. ``ArcaneSkillCostRuleTest`` guards the
# spelling against the rulebook's.
ARCANE_SKILL_NAME = "arcane"

# Points are uncapped by the rules but not by storage: a skill total has to stay
# a sane, JSON-serializable integer, and the level ladder is walked one level at
# a time on the client. This ceiling is a storage sanity bound, NOT a rules cap
# -- it sits far above any reachable career total (a term grants at most 4
# points to one skill), and allocation and import enforce the same number so
# anything allocatable stays importable.
MAX_SKILL_POINTS = 10_000

_ROMAN_NUMERALS: tuple[tuple[int, str], ...] = (
    (1000, "M"),
    (900, "CM"),
    (500, "D"),
    (400, "CD"),
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
)


def roman_numeral(number: int) -> str:
    """Render a positive integer as a Roman numeral; "" for zero or less."""
    if number < 1:
        return ""
    remaining = number
    rendered: list[str] = []
    for value, numeral in _ROMAN_NUMERALS:
        count, remaining = divmod(remaining, value)
        rendered.append(numeral * count)
    return "".join(rendered)


def skill_cost_multiplier(skill_name: str | None = None) -> int:
    """How many times the ordinary triangular cost ``skill_name`` charges.

    The canon's multiplier for the skill (2 for Arcane on the public pages),
    1 for every other skill and for ``None``, meaning the caller is asking
    about the ordinary ladder rather than a named skill.
    """
    if skill_name is None:
        return 1
    return skill_rules().skill_cost.multiplier_for(skill_name)


def points_for_level(level: int, skill_name: str | None = None) -> int:
    """Cumulative points to reach a skill level: ``level * (level + 1) / 2``.

    Uncapped — level 6 costs 21, level 10 costs 55. Levels below 1 cost
    nothing. Arcane charges double: 2, 6, 12, 20, ...
    """
    if level < 1:
        return 0
    return skill_cost_multiplier(skill_name) * level * (level + 1) // 2


def get_skill_level(points: int, skill_name: str | None = None) -> int:
    """Convert total points to a skill level — the inverse of the cost formula.

    Uncapped: the level is the largest ``N`` whose cumulative cost is at or
    below ``points``. For Arcane the cost is doubled, which is the same as
    reading the ordinary ladder at half the points (rounded down).
    """
    affordable = points // skill_cost_multiplier(skill_name)
    if affordable < 1:
        return 0
    # Exact integer inverse of N(N + 1) / 2 <= affordable.
    return (math.isqrt(8 * affordable + 1) - 1) // 2


def get_excess_points(points: int, skill_name: str | None = None) -> int:
    """Get points beyond the current level's cumulative cost."""
    return points - points_for_level(get_skill_level(points, skill_name), skill_name)


def format_skill_level(points: int, skill_name: str | None = None) -> str:
    """Format a skill's level for display, without its name.

    The one place a level becomes text: the edit screen prints it after the
    word "Level", and every JSON response that changes a skill carries the same
    string, so the ladder is never re-derived on the client.

    Examples (ordinary skill): 0 points -> "0", 1 -> "I", 2 -> "I (+1)",
    3 -> "II", 5 -> "II (+2)", 15 -> "V", 21 -> "VI" (levels are uncapped).
    Arcane costs double: 2 points -> "I", 5 -> "I (+3)", 6 -> "II".

    Level 0 renders as "0" so the "Level ..." label still reads as a level;
    leftover points always show as the same ``(+N)`` suffix.
    """
    level = get_skill_level(points, skill_name)
    excess = get_excess_points(points, skill_name)
    rendered = roman_numeral(level) if level > 0 else "0"
    if excess > 0:
        return f"{rendered} (+{excess})"
    return rendered


#: The track choice that lets the rules pick one of the canon's tracks: a UI
#: and plan option, not a track (issue #70 keeps it in code, beside the
#: choice that reads it).
RANDOM_TRACK_KEY = "random"
#: How the random choice is labelled wherever the tracks are listed.
RANDOM_TRACK_LABEL = "Random"


def track_keys() -> tuple[str, ...]:
    """The canon's prior-experience tracks, by key, in the canon's order."""
    return tuple(skill_rules().tracks.tracks)


#: What prose or a plan puts between a track's words: a space, an underscore,
#: a hyphen, or the slash the canon's labels use ("Hunter/Gatherer", issue #68).
TRACK_WORD_JOINER = r"[\s_/-]"


def track_key_for(said: str) -> str:
    """The track key a track named in prose or a plan stands for.

    "Hunter/Gatherer", "hunter-gatherer" and "Civil Service" are
    ``hunter_gatherer`` and ``civil_service``: the one reading the plan
    reader, the rules lookup and the behaviour check share (issue #68). Whether
    the key names a track is the caller's question.

    Args:
        said: The track as written.

    Returns:
        It case-folded, with each joiner between its words an underscore.
    """
    return re.sub(TRACK_WORD_JOINER, "_", said.strip().casefold())


def selectable_tracks() -> tuple[str, ...]:
    """Every track a player or a plan may name: the canon's, then random.

    The one list the plan schema the Games Master reads, the plan reader and
    the add-years endpoint check a named track against (issue #70).
    """
    return (*track_keys(), RANDOM_TRACK_KEY)


def track_labels() -> dict[str, str]:
    """Each selectable track's label, the random choice first.

    The one place they are spelled (the editor's select, the history run
    summaries, and the JS mirror all read these; issue #221 review caught
    "Civil_service" leaking raw values): the canon's labels, and the random
    choice's own.
    """
    return {
        RANDOM_TRACK_KEY: RANDOM_TRACK_LABEL,
        **{key: track.label for key, track in skill_rules().tracks.tracks.items()},
    }


def track_label(track: str) -> str:
    """The human label for a track value, humanized for unknown/legacy ones."""
    return track_labels().get(track, track.replace("_", " ").title())


def survival_targets() -> dict[str, int]:
    """Each track's survivability target, by key."""
    return {
        key: track.survival_target for key, track in skill_rules().tracks.tracks.items()
    }


def calculate_skill_points_from_xp(total_xp: int) -> int:
    """Total skill points earned from XP thresholds.

    Skill points are granted cumulatively as XP is earned: the climb to each
    level of the canon's ladder earns one point per that level's
    ``xp_per_skill_point`` (prior-experience.md: "at 3,000 XP you would have
    earned 2 skill points (at 1,000 and 2,000 XP)"). Past the top of the
    ladder the top level's rate carries on, so a Game Master who grants a
    character ten million XP sees the points keep coming -- the table gives
    the top level a rate, not a ceiling, and ``total_xp`` is directly
    editable far past it (issue #656).

    Args:
        total_xp: The character's lifetime XP.

    Returns:
        The skill points that much XP has earned.
    """
    if total_xp <= 0:
        return 0
    levels = skill_rules().experience.levels
    total_points = 0
    for lower, higher in pairwise(levels):
        if total_xp <= lower.minimum_xp:
            break
        bracket_xp = min(total_xp, higher.minimum_xp) - lower.minimum_xp
        total_points += bracket_xp // _rate_of(higher)
    top = levels[-1]
    if len(levels) > 1 and total_xp > top.minimum_xp:
        total_points += (total_xp - top.minimum_xp) // _rate_of(top)
    return total_points


def _rate_of(level: ExperienceLevel) -> int:
    """The XP per skill point on the climb to ``level``, past level 1.

    Raises:
        ValueError: ``level`` is level 1, which has no climb to rate; the
            ladder's own validation means no caller here asks for it.
    """
    if level.xp_per_skill_point is None:  # pragma: no cover - refused on load
        raise ValueError(f"level {level.level} has no XP per skill point")
    return level.xp_per_skill_point


def skill_budget_note(allocated: int, earned: int) -> str | None:
    """How far a character's spent skill points sit past what they earned.

    The skill-point counterpart of
    :func:`characters.constants.point_buy_budget_note`, written in the same
    shape so a character built past both budgets reads consistently. The
    workbench refuses the over-spend outright ("No free skill points
    available"); a DM building through the GM session is told the size of it
    instead (issue #352).

    Args:
        allocated: Points spent across the character's skills.
        earned: Points the character's experience actually earned.

    Returns:
        The sentence, or ``None`` when nothing was spent past the earned
        total.
    """
    if allocated <= earned:
        return None
    return (
        f"Skill points: {allocated} allocated — {allocated - earned} "
        f"over the {earned} earned from experience."
    )


def calculate_skill_points_in_range(xp_before: int, xp_after: int) -> int:
    """Calculate skill points earned when XP goes from xp_before to xp_after."""
    if xp_after <= xp_before:
        return 0
    return calculate_skill_points_from_xp(xp_after) - calculate_skill_points_from_xp(
        xp_before
    )


# Aging is the canon's ``aging`` table: aging *stage* -> TOTAL cumulative
# penalties at that stage, and how decline continues past the last tabulated
# stage (:meth:`characters.skill_rules.AgingRules.penalties_for`).
#
# A stage is the canon's ``age.years_per_stage`` years of life (four on the
# public pages) counted from its starting age (16), so stage N is reached at
# age ``16 + 4N`` -- 16, 20, 24, ... Before issue #221 the key was the
# experience *term* number, which came to the same thing only because a term
# was exactly four years; keying it to age makes the schedule identical
# whether a character buys four years in one click or one year at a time.


def aging_stage_for_age(age: int) -> int:
    """The aging stage a character of `age` has reached.

    Stage N spans the years from the starting age plus N stages (the
    canon's ``age``; on the public pages 16 + 4N): a character is stage 5
    from age 36 through 39, stage 6 from 40, and so on. Ages below the
    starting age give a negative stage, which carries no aging.
    """
    age_rules = character_rules().age
    return (age - age_rules.starting) // age_rules.years_per_stage


def age_for_aging_stage(stage: int) -> int:
    """The age at which `stage` begins -- the inverse of `aging_stage_for_age`."""
    age_rules = character_rules().age
    return age_rules.starting + age_rules.years_per_stage * stage


def get_aging_penalties_for_age(age: int) -> dict[str, int] | None:
    """The cumulative aging penalty totals a character of `age` carries.

    ``None`` before the canon's first stage with penalties; otherwise every
    aged attribute by field name, in the table's order (STR, DEX, INT, WIS,
    CON) whether the stage is tabulated or past the table.
    """
    return skill_rules().aging.penalties_for(aging_stage_for_age(age))


def next_aging_change_after_age(
    age: int, horizon_stage: int
) -> tuple[int, dict[str, int]] | None:
    """When the next aging hit lands, and what it costs.

    Walks forward from `age` to the first stage boundary whose cumulative
    totals differ from the ones the character carries now, and returns
    ``(age_at_that_boundary, non_zero_totals)``. Returns ``None`` when nothing
    changes before `horizon_stage` -- the caller says so in prose rather than
    precomputing an unbounded schedule (issue #163).

    Args:
        age: The character's current age.
        horizon_stage: The last aging stage to look ahead to.

    Returns:
        ``(age, totals)`` for the next change, or ``None`` past the horizon.
        The totals are in :data:`characters.skill_rules.AGING_ATTRIBUTES`'
        order, the order the workbench prints them in.
    """
    aging = skill_rules().aging
    current_totals = get_aging_penalties_for_age(age) or {}
    for stage in range(aging_stage_for_age(age) + 1, horizon_stage + 1):
        totals = aging.penalties_for(stage)
        if not totals or totals == current_totals:
            continue
        non_zero = {
            attribute: totals[attribute]
            for attribute in AGING_ATTRIBUTES
            if totals[attribute] != 0
        }
        if non_zero:
            return age_for_aging_stage(stage), non_zero
    return None


def aging_escalation_note(horizon_age: int) -> str:
    """What the workbench says of aging past its preview's horizon (issue #68).

    "Continues to escalate past age 96 (roughly -1 to STR/DEX/CON every four
    years, plus -1 to INT/WIS every eight)." The years are the canon's: a
    stage's length (``age.years_per_stage``) for the physical attributes and
    ``aging.stages_per_mental_step`` stages for the mental ones; the point a
    step is :meth:`characters.skill_rules.AgingRules.penalties_for`'s.

    Args:
        horizon_age: The age the preview stops at.

    Returns:
        The note, one sentence.
    """
    stage_years = character_rules().age.years_per_stage
    mental_years = stage_years * skill_rules().aging.stages_per_mental_step
    short_names = dict(CORE_ATTRIBUTE_FIELDS)
    physical = "/".join(short_names[name] for name in PHYSICAL_AGING_ATTRIBUTES)
    mental = "/".join(short_names[name] for name in MENTAL_AGING_ATTRIBUTES)
    years = "year" if stage_years == 1 else "years"
    return (
        f"Continues to escalate past age {horizon_age} (roughly -1 to {physical} "
        f"every {number_in_words(stage_years)} {years}, plus -1 to {mental} "
        f"every {number_in_words(mental_years)})."
    )


#: The glossary skills taken with a chosen part, recorded in the skill's own
#: name the way a Weapon skill records its weapon ("weapon (broadsword)",
#: ``characters.weapon_skill``): Maker Skill is "A **chosen craft** (smith,
#: carpenter, tailor...)", and Knowledge may be focused on "a field" (skills
#: glossary). Issue #808. "maker skill (smith)" is the Maker Skill, at the
#: Maker Skill's cost and governed by its attribute.
SPECIALISED_SKILLS: tuple[str, ...] = ("maker skill", "knowledge")


def split_specialised_skill(skill_name: str) -> tuple[str, str]:
    """``(glossary skill, chosen part)`` for a Maker Skill or Knowledge name.

    ``"maker skill (smith)"`` is ``("maker skill", "smith")``; a bare
    ``"knowledge"`` is ``("knowledge", "")``. Any other name comes back as
    ``(name, "")``: only :data:`SPECIALISED_SKILLS` carry a chosen part here
    (the Weapon skill's is its weapon, ``characters.weapon_skill``).

    Args:
        skill_name: A stored (normalised) skill name.
    """
    groups = top_level_parentheses(skill_name)
    if not groups:
        return skill_name, ""
    opening, closing = groups[-1]
    # The last group closes the name, after a space, and holds something. It
    # may hold parentheses of its own: "knowledge (history (old))".
    ends_the_name = closing == len(skill_name) - 1
    if ends_the_name and closing > opening + 1 and skill_name[:opening].endswith(" "):
        skill = skill_name[: opening - 1]
        if skill in SPECIALISED_SKILLS:
            return skill, skill_name[opening + 1 : closing].strip()
    return skill_name, ""


def glossary_skill(skill_name: str) -> str:
    """The glossary skill a stored skill name falls under (issues #768, #808).

    A Weapon skill, tied or not, is ``"weapon"``; a Maker Skill or Knowledge
    with its chosen part is the bare skill; anything else is its own name.
    The one answer to "which rule does this skill fall under", which a
    player's rename may never change.

    Args:
        skill_name: A stored (normalised) skill name.
    """
    from .weapon_skill import UNRECORDED_WEAPON_SKILL, is_weapon_skill

    if is_weapon_skill(skill_name):
        return UNRECORDED_WEAPON_SKILL
    return split_specialised_skill(skill_name)[0]


def validate_skill_names(rules: SkillRules, path: Path) -> None:
    """Refuse a canon skill name that is not a glossary skill in its own right.

    The checks' maps name each skill by the glossary skill a stored name falls
    under (:func:`glossary_skill`), and every track's ``money_skill`` and
    every cost multiplier must be one of them
    (:class:`characters.skill_rules.SkillRules`). A weapon's name ("hammer"
    falls under Weapon) or a chosen part ("maker skill (smith)" falls under
    Maker Skill) would never be found that way: a check, a cost or a year's
    pay would read the wrong skill without a word. Here rather than in
    :mod:`characters.skill_rules`, which this module imports and
    :func:`glossary_skill` needs; :func:`characters.checks.check_skill_rules`
    calls it, so a refusal is ``characters.E002`` (issue #70).

    Args:
        rules: The rules read from ``path``.
        path: The ``skill-rules.json`` they were read from, for the message.

    Raises:
        SkillRulesError: Some map key is not its own glossary skill.
    """
    skill_check = rules.skill_check
    refused = {
        f"skill_check.{field}.{name}": glossary_skill(name)
        for field, names in (
            ("governing_attributes", skill_check.governing_attributes),
            ("implied_governing_attributes", skill_check.implied_governing_attributes),
        )
        for name in names
        if glossary_skill(name) != name
    }
    if refused:
        listed = "; ".join(
            f"{field} falls under {under!r}" for field, under in refused.items()
        )
        raise SkillRulesError(
            f"{path}: {listed}: each must be a glossary skill in its own right"
        )


def glossary_skill_level(skills: dict[str, int] | None, skill_name: str) -> int:
    """The highest level held in one glossary skill, whatever its chosen part.

    The Craft track's income rides on "maker skill" (skill-tracks.md), and a
    Maker Skill recorded with its craft, "maker skill (smith)", is that skill
    (issue #808).

    Args:
        skills: Stored skills, ``{name: points}``.
        skill_name: A glossary skill, as :func:`glossary_skill` names it.
    """
    return max(
        (
            get_skill_level(int(points or 0), name)
            for name, points in (skills or {}).items()
            if glossary_skill(name) == skill_name
        ),
        default=0,
    )


def specialised_skill_for_granted(skills: dict[str, int], granted: str) -> str:
    """Where a prior-experience grant of Maker Skill or Knowledge lands.

    The glossary: "Roll a skill you already have and the point goes toward
    its next level." A character whose Maker Skill is "maker skill (smith)"
    already has the Maker Skill, so a rolled Maker Skill goes there; with
    several chosen parts, to the one with the most points (the rule
    ``weapon_skill_for_granted`` applies to Weapon skills, #753). With none
    it lands on the bare skill, for the player to name its part.

    Args:
        skills: The character's stored skills.
        granted: The normalised name the track rolled.
    """
    if granted not in SPECIALISED_SKILLS:
        return granted
    chosen = sorted(
        name
        for name in skills
        if name != granted and split_specialised_skill(name)[0] == granted
    )
    if not chosen or granted in skills:
        return granted
    return max(chosen, key=lambda name: int(skills.get(name, 0) or 0))


def is_special_experience_result(normalized_skill: str) -> bool:
    """Whether a normalized roll result is special rather than a real skill.

    A track's d20 table can roll an attribute bonus ("+1 str"/"+1 dex"/
    "+1 con") or a bonus point ("skill point"/"spell point") instead of a
    skill -- see the "Special / mechanical entries" table in reference/
    content/public-rules/characters/prior-experience/skills-glossary.md.
    Both ``simulate_experience_years`` (which must not add these to
    ``character.skills``) and ``granted_skill_names`` (which must not suggest
    them as skills) need the same check, so it lives here once.
    """
    return normalized_skill.startswith("+1 ") or normalized_skill in (
        "skill point",
        "spell point",
    )


def granted_skill_names() -> list[str]:
    """Every real (non-special) skill name any track's d20 table can grant.

    Flattened from the canon's track tables, normalized, deduplicated, and
    sorted -- the single source of truth the character editor's add-skill
    type-ahead reads from (issue #224), so its suggestion list can never drift
    from what the prior-experience generator actually rolls.
    """
    normalized_names = {
        normalize_skill_name(raw)
        for track in skill_rules().tracks.tracks.values()
        for raw in track.skills
    }
    return sorted(
        name for name in normalized_names if not is_special_experience_result(name)
    )


def calculate_attribute_modifier(value: int) -> int:
    """Modifier for an attribute value, per the canon's attributes table.

    The published table (``reference/content/public-rules/characters/
    attributes.md``) is flat 0 across 8-13, then changes by 1 per point outside
    that band:

        3 -> -5, 4 -> -4, 5 -> -3, 6 -> -2, 7 -> -1,
        8 through 13 -> 0,
        14 -> +1, 15 -> +2, 16 -> +3, 17 -> +4, 18 -> +5

    The table prints 3 to 18 and nothing past either end, though a score can
    leave that range: a Laborer's "+1 STR" and a Game Master's figure raise it
    to 30, and aging lowers it towards 0. Issue #806: a score outside the table
    takes the modifier of the nearest printed row, -5 below 3 and +5 above 18,
    as attributes.md now says. It used to continue the slope the rules never
    state (19 -> +6 ... 30 -> +17, 0 -> -8). Coordinator's ruling under the
    standing rule (the table is the rule; nothing past it is invented);
    Spencer may overrule. The table and the ruling are the canon's
    (``attribute_modifier``, issue #70).
    """
    return skill_rules().attribute_modifier.modifier_for(value)


def get_best_modifier(
    strength: int,
    dexterity: int,
    intelligence: int,
    wisdom: int,
    constitution: int,
    charisma: int,
) -> int:
    """Get the best modifier from all six attributes."""
    modifiers = [
        calculate_attribute_modifier(strength),
        calculate_attribute_modifier(dexterity),
        calculate_attribute_modifier(intelligence),
        calculate_attribute_modifier(wisdom),
        calculate_attribute_modifier(constitution),
        calculate_attribute_modifier(charisma),
    ]
    return max(modifiers)


def get_skill_for_year(track: str, position: int) -> str:
    """
    Get the skill at a 1-based position in a track (position 1 = first skill).

    Positions past the end of the 20-entry table clamp to the last skill. This
    is purely defensive — the simulator never asks for one, because a d20 roll
    cannot exceed the table length.

    Raises:
        ValueError: ``track`` is not one of the canon's tracks. It used to
            fall back to Laborer, a track a canon need not have; every caller
            checks the track first (issue #70).
    """
    table = _track(track).skills
    if position > len(table):
        return table[-1]

    # Position 1 is index 0
    return table[position - 1]


def _track(track: str) -> SkillTrack:
    """The canon's track ``track``.

    Raises:
        ValueError: The canon has no such track.
    """
    tracks = skill_rules().tracks.tracks
    if track not in tracks:
        raise ValueError(f"Unknown track: {track}")
    return tracks[track]


def get_random_track() -> str:
    """Get a random track (all tracks are unrestricted)."""
    return random.choice(track_keys())


def _effective_attributes_at_age(
    attributes: dict[str, int], age: int
) -> dict[str, int]:
    """The attributes a character of `age` actually rolls with.

    Aging is a pure function of age (issue #221): the cumulative penalty totals
    for the age's stage come off the base scores, floored at 0, exactly the way
    ``Character.effective_strength`` and its siblings compute them. Charisma
    has no aging penalty, so it passes through.
    """
    penalties = get_aging_penalties_for_age(age) or {}
    return {
        name: max(0, value + penalties.get(name, 0))
        for name, value in attributes.items()
    }


def survival_dice_expression(check: Mapping[str, Any]) -> str:
    """The dice a stored survival check was rolled on, e.g. ``"3d6"``.

    A check records its dice since the canon names them (issue #70). One
    stored before that was rolled on d6s, two of them before issue #223 and
    three after, so its own faces say how many.

    Args:
        check: A ``survival_check`` dict as :func:`simulate_experience_years`
            stores it.
    """
    expression = check.get("dice_expression")
    if isinstance(expression, str) and expression:
        return expression
    return f"{len(check.get('dice') or ())}d6"


def simulate_experience_years(
    years_requested: int,
    years_completed: int,
    current_age: int,
    track: str,
    strength: int,
    dexterity: int,
    intelligence: int,
    wisdom: int,
    constitution: int,
    charisma: int,
    starting_skills: dict[str, int] | None = None,
) -> dict:
    """Simulate a run of `years_requested` years in `track`, year by year.

    A run is however many years the player asked for -- one is the default and
    the common case (issue #221). Each year rolls its own skill (d20) and its
    own survival check, and the run stops the moment a survival check fails.

    The six attributes are the character's **base** scores; aging is applied
    inside, from the age each year starts at, so the survival modifier tracks
    the character's real, current attributes through the run (issue #222). A
    "+1 STR"/"+1 DEX"/"+1 CON" result raises the running score immediately, so
    it counts from the *next* year onward -- which makes four one-year runs and
    one four-year run land on exactly the same modifiers.

    `starting_skills` is the character's skill-points dict (skill name ->
    points) as of the start of the run; it is read, never mutated. Money is
    recalculated each year from skill levels *as of that point in the run* --
    a point rolled in an earlier year (or this year's own roll, since it lands
    before that year's pay is figured) counts immediately.

    Args:
        years_requested: How many years to play, at least 1.
        years_completed: Years of prior experience the character already has,
            so this run's years carry on the career numbering.
        current_age: The character's age at the start of the run.
        track: The track key, e.g. ``"merchant"``.
        strength, dexterity, intelligence, wisdom, constitution, charisma:
            Base (un-aged) attribute scores.
        starting_skills: Skill-points dict at the start of the run.

    Returns a dict with:
    - years_requested: int (what was asked for; ``len(years)`` is what played)
    - track: str
    - years: list of year results (each with skill, XP, money, survival check)
    - skills_gained: dict of skill -> points gained
    - xp_awarded: int (sum of yearly XP based on track)
    - money_earned: int (sum of yearly money based on skill levels)
    - survival_check: dict (final year's survival check)
    - aging: cumulative penalty totals if the run crossed an aging stage, else
      None
    - alive: bool
    """
    track_rules = _track(track)
    xp_per_year = track_rules.xp_per_year
    survival_target = track_rules.survival_target
    money_multiplier = track_rules.money_multiplier
    survival_roll = skill_rules().survival_roll

    # Running skill-points snapshot, seeded from the character's skills at the
    # start of the run and updated after each year's roll -- so a later year's
    # pay in this run reflects skill points gained earlier in it.
    running_skills: dict[str, int] = dict(starting_skills or {})

    # Running base attributes, raised in place by "+1 STR"/"+1 DEX"/"+1 CON"
    # results as they are rolled (issue #222). The cap matches the one
    # ``_apply_attribute_bonus`` enforces when the run is written back.
    running_attributes: dict[str, int] = {
        "strength": strength,
        "dexterity": dexterity,
        "intelligence": intelligence,
        "wisdom": wisdom,
        "constitution": constitution,
        "charisma": charisma,
    }

    result = {
        "years_requested": years_requested,
        "track": track,
        "years": [],
        "skills_gained": {},
        "xp_awarded": 0,
        "money_earned": 0,
        "survival_check": None,
        "aging": None,
        "alive": True,
    }

    max_skill_index = len(track_rules.skills)
    aging_at_run_start = get_aging_penalties_for_age(current_age)

    for year_offset in range(years_requested):
        year_in_career = years_completed + year_offset + 1
        age_at_year_start = current_age + year_offset
        age = age_at_year_start + 1

        # The survival modifier is recomputed every year from the attributes
        # the character actually has at the start of it -- base scores raised
        # by earlier years' gains, lowered by whatever aging they have reached
        # (issue #222). It used to be computed once per term, so a term's later
        # years rolled on stale numbers.
        effective = _effective_attributes_at_age(running_attributes, age_at_year_start)
        best_mod = get_best_modifier(
            effective["strength"],
            effective["dexterity"],
            effective["intelligence"],
            effective["wisdom"],
            effective["constitution"],
            effective["charisma"],
        )

        roll = roll_d20()

        # Get skill from track table (use roll to pick from table, capped at table length)
        skill_index = min(roll, max_skill_index)
        skill_raw = get_skill_for_year(track, skill_index)
        skill = normalize_skill_name(skill_raw)

        # A roll can land on a real skill or a special result ("+1 STR",
        # "skill point", "spell point"). Only real skills feed skill levels
        # (and thus money) -- specials never enter character.skills either
        # (see _apply_experience_run), so they must not count here.
        if is_special_experience_result(skill):
            attribute_field = ATTRIBUTE_BONUS_FIELDS.get(skill[3:].strip())
            if attribute_field:
                running_attributes[attribute_field] = min(
                    character_rules().attributes.maximum,
                    running_attributes[attribute_field] + 1,
                )
        else:
            # A chosen Maker Skill or Knowledge already held takes the point
            # (issue #808), as apply_experience_run records it.
            skill = specialised_skill_for_granted(running_skills, skill)
            running_skills[skill] = running_skills.get(skill, 0) + 1

        # Calculate money for this year from skill levels as of this point in
        # the run (floored at level 1: everyone earns at least the track's
        # base rate, even with no skills -- see survivability.md). A track
        # whose pay rides on one skill (Craft's Maker Skill on the public
        # pages) reads that skill's level; the rest read the highest held.
        # Levels are read per skill, not per point total, because Arcane's
        # points buy levels at half the rate (issue #154).
        if track_rules.money_skill is not None:
            skill_level_for_money = glossary_skill_level(
                running_skills, track_rules.money_skill
            )
        else:
            skill_level_for_money = max(
                (
                    get_skill_level(pts, running_name)
                    for running_name, pts in running_skills.items()
                ),
                default=0,
            )
        year_money = money_multiplier * max(1, skill_level_for_money)

        # Survival check for this year: the canon's dice + best modifier >=
        # target (3d6 on the public pages since issue #223).
        *faces, roll_total = roll_survival_dice()
        survival_total = roll_total + best_mod
        auto_success = roll_total == survival_roll.automatic_survival_total
        auto_death = roll_total == survival_roll.automatic_death_total

        if auto_death:
            survival_success = False
        elif auto_success:
            survival_success = True
        else:
            survival_success = survival_total >= survival_target

        survival_check = {
            "dice": faces,
            "dice_expression": survival_roll.dice,
            "roll": roll_total,
            "modifier": best_mod,
            "total": survival_total,
            "target": survival_target,
            "success": survival_success,
            "auto_success": auto_success,
            "auto_death": auto_death,
        }

        year_result = {
            "year": year_in_career,
            "age": age,
            "skill": skill_raw,  # Keep original for display
            "skill_normalized": skill,
            "roll": roll,
            "xp_awarded": xp_per_year,
            "money_earned": year_money,
            "survival_check": survival_check,
        }
        result["years"].append(year_result)

        # Accumulate totals
        result["xp_awarded"] += xp_per_year
        result["money_earned"] += year_money
        result["survival_check"] = survival_check  # The run's last check

        # Track points gained (normalized names)
        result["skills_gained"][skill] = result["skills_gained"].get(skill, 0) + 1

        # Check for death - stop processing further years
        if not survival_success:
            result["alive"] = False
            break

    # Aging is keyed to age, so a run reports it only when the years just
    # played moved the totals -- otherwise the totals the character already
    # carries are still the right ones and the log stays quiet rather than
    # announcing "aging penalties applied" for a run that changed nothing.
    #
    # Read from the age the run actually reached, not from the years asked
    # for: a run that ended in death used to return before this block, so its
    # log said nothing about the aging those years earned (issue #654).
    age_reached = result["years"][-1]["age"] if result["years"] else current_age
    aging_at_run_end = get_aging_penalties_for_age(age_reached)
    if (
        aging_at_run_end is not None
        and aging_at_run_end != aging_at_run_start
        and any(aging_at_run_end.values())
    ):
        result["aging"] = aging_at_run_end

    return result


def skill_rows(skills: dict[str, int] | None) -> list[dict]:
    """The character's skills as the sheet lists them, in their stored order.

    One source for the page's skills list and the add-years and allocate
    responses (issue #338 item 3), so a live update and a reload show the same
    rows in the same order. The level string comes from the ladder here, never
    from the browser (issues #146, #154).
    """
    # Imported here: weapon_skill reads this module's ladder at import time.
    from .weapon_skill import needs_weapon_tie

    return [
        {
            "name": skill_name,
            "points": points,
            "level": get_skill_level(points, skill_name),
            "display": format_skill_level(points, skill_name),
            # A Weapon skill the attack resolver cannot read yet (#753).
            "needs_weapon_tie": needs_weapon_tie(skill_name),
        }
        for skill_name, points in (skills or {}).items()
    ]
