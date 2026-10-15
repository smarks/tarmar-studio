"""Skills system logic for character experience and skill allocation."""

import math
import random
import re

from .constants import (
    ATTRIBUTE_BONUS_FIELDS,
    strip_control_characters,
)

# The survival roll's dice. It moved from 2d6 to 3d6 in issue #223 -- same
# per-track targets, a much narrower spread -- so this is the local name (and
# the patch target the tests use) for the roller that returns the faces the
# survival log stores.
from .dice import roll_3d6_faces as roll_survival_dice
from .dice import roll_d20
from .rules import character_rules

# Survival roll shape (issue #223). Three dice, so the natural-roll overrides
# are triple ones and triple sixes rather than 2d6's snake eyes and boxcars.
SURVIVAL_DICE_COUNT = 3
SURVIVAL_AUTO_DEATH_ROLL = 3
SURVIVAL_AUTO_SURVIVE_ROLL = 18

# The survival check written out in words, for the GM to read rather than
# remember (issue #370: it told a player the campaigner track "requires a
# survival roll of 3d6 <= 12" and killed a character on an 18). It is *built*
# from the constants above and from the comparison
# ``simulate_experience_years`` actually makes, so the sentence cannot drift
# away from the code the way the model's memory did. It lives here, beside the
# rule, because this module is the single source of truth for the check.
SURVIVAL_RULE_DESCRIPTION = (
    f"roll {SURVIVAL_DICE_COUNT}d6, add the character's best attribute "
    "modifier, and compare that total with the track's survivability target — "
    "the character survives when the total is greater than or equal to the "
    f"target, so the roll must go HIGH. A natural {SURVIVAL_AUTO_SURVIVE_ROLL} "
    "on the dice is an automatic survival whatever the target, and a natural "
    f"{SURVIVAL_AUTO_DEATH_ROLL} is an automatic death."
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
# One skill is priced differently. **Arcane** (True Magic) costs double: level N
# costs 2xN more points, so reaching level N costs N(N+1) in total -> 2, 6, 12,
# 20, 30, 42, ... (issue #154). Every function that converts between points and
# levels takes the skill name so the exception is applied in exactly one place;
# passing no name means "an ordinary skill".

# The doubled skill, as ``normalize_skill_name`` stores it. The match is exact
# normalized equality, so "Arcane", "ARCANE", and "Arcane III" all hit it while
# a differently named skill that merely contains the word ("arcane lore") is an
# ordinary skill at ordinary cost. ``ArcaneSkillCostRuleTest`` guards the
# spelling against the rulebook's.
ARCANE_SKILL_NAME = "arcane"
ARCANE_COST_MULTIPLIER = 2

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

    2 for Arcane, 1 for every other skill (and for ``None``, meaning the
    caller is asking about the ordinary ladder rather than a named skill).
    """
    if skill_name is None:
        return 1
    if normalize_skill_name(skill_name) == ARCANE_SKILL_NAME:
        return ARCANE_COST_MULTIPLIER
    return 1


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


# Track data: XP per year, survivability target, money multiplier
# Money is multiplied by highest skill level (or maker skill for craft track)
# Human display labels for the tracks -- the one place they are spelled
# (the editor's select, the history run summaries, and the JS mirror all
# read these; issue #221 review caught "Civil_service" leaking raw values).
TRACK_LABELS: dict[str, str] = {
    "random": "Random",
    "merchant": "Merchant",
    "campaigner": "Campaigner",
    "laborer": "Laborer",
    "magic": "Magic",
    "underworld": "Underworld",
    "civil_service": "Civil Service",
    "craft": "Craft",
    "hunter_gatherer": "Hunter/Gatherer",
}


def track_label(track: str) -> str:
    """The human label for a track value, humanized for unknown/legacy ones."""
    return TRACK_LABELS.get(track, track.replace("_", " ").title())


TRACK_DATA: dict[str, dict[str, int | str]] = {
    "merchant": {"xp_per_year": 750, "survivability": 4, "money_multiplier": 100},
    "campaigner": {"xp_per_year": 1200, "survivability": 9, "money_multiplier": 200},
    "laborer": {"xp_per_year": 500, "survivability": 2, "money_multiplier": 25},
    "magic": {"xp_per_year": 900, "survivability": 9, "money_multiplier": 75},
    "underworld": {"xp_per_year": 1000, "survivability": 8, "money_multiplier": 125},
    "civil_service": {"xp_per_year": 750, "survivability": 4, "money_multiplier": 75},
    "craft": {
        "xp_per_year": 600,
        "survivability": 3,
        "money_multiplier": 75,
        "money_skill": "maker",
    },
    "hunter_gatherer": {"xp_per_year": 800, "survivability": 6, "money_multiplier": 25},
}

# Backward compatibility: extract survival targets from TRACK_DATA
SURVIVAL_TARGETS: dict[str, int] = {
    track: int(data["survivability"]) for track, data in TRACK_DATA.items()
}

# Experience level thresholds (level -> minimum XP required)
EXPERIENCE_LEVELS: dict[int, int] = {
    1: 0,
    2: 2000,
    3: 4000,
    4: 8000,
    5: 16000,
    6: 32000,
    7: 64000,
    8: 128000,
    9: 256000,
    10: 512000,
}

# XP per skill point at each level (used for cumulative skill point calculation)
SKILL_POINTS_PER_LEVEL: dict[int, int] = {
    2: 1000,
    3: 1334,
    4: 2000,
    5: 3200,
    6: 5334,
    7: 9143,
    8: 16000,
    9: 28445,
    10: 51200,
}


def get_experience_level(total_xp: int) -> int:
    """Get experience level (1-10) from total XP."""
    for level in range(10, 0, -1):
        if total_xp >= EXPERIENCE_LEVELS[level]:
            return level
    return 1


#: The last level the published table prints. Level 10's row gives a rate
#: ("Skill Points Every 51,200") rather than a terminus, and the page says
#: skill points are granted cumulatively as XP is earned without naming a
#: stop, so that rate keeps running past 512,000 XP rather than the grant
#: ending there (issue #656).
TOP_EXPERIENCE_LEVEL = 10


def calculate_skill_points_from_xp(total_xp: int) -> int:
    """Total skill points earned from XP thresholds.

    Skill points are granted cumulatively as XP is earned; the rate is the
    one the level bracket names. Past the top of the published table the
    level-10 rate carries on, so a Game Master who grants a character ten
    million XP sees the points keep coming -- the table gives level 10 a
    rate, not a ceiling, and ``total_xp`` is directly editable far past it.

    Args:
        total_xp: The character's lifetime XP.

    Returns:
        The skill points that much XP has earned.
    """
    if total_xp <= 0:
        return 0

    total_points = 0

    # Process each level bracket
    for level in range(2, TOP_EXPERIENCE_LEVEL + 1):
        level_start = EXPERIENCE_LEVELS[level - 1] if level > 1 else 0
        level_end = EXPERIENCE_LEVELS[level]
        xp_per_point = SKILL_POINTS_PER_LEVEL[level]

        # How much XP in this bracket?
        if total_xp <= level_start:
            break

        bracket_xp = min(total_xp, level_end) - level_start
        if bracket_xp > 0:
            points_in_bracket = bracket_xp // xp_per_point
            total_points += points_in_bracket

    # Past the table's last row, level 10's rate continues (issue #656).
    top_of_table = EXPERIENCE_LEVELS[TOP_EXPERIENCE_LEVEL]
    if total_xp > top_of_table:
        total_points += (total_xp - top_of_table) // SKILL_POINTS_PER_LEVEL[
            TOP_EXPERIENCE_LEVEL
        ]

    return total_points


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


# All tracks are now unrestricted - no attribute requirements

# Aging table: aging *stage* -> TOTAL cumulative penalties at that stage.
#
# A stage is the canon's ``age.years_per_stage`` years of life (four on the
# public pages) counted from its starting age (16), so stage N is reached at
# age ``16 + 4N`` -- 16, 20, 24, ... Decline starts at
# stage 5 (age 36). Before issue #221 the key was the experience *term*
# number, which came to the same thing only because a term was exactly four
# years; keying it to age makes the schedule identical whether a character
# buys four years in one click or one year at a time.
AGING_TABLE: dict[int, dict[str, int]] = {
    4: {
        "strength": 0,
        "dexterity": 0,
        "intelligence": 0,
        "wisdom": 0,
        "constitution": 0,
    },
    5: {
        "strength": -1,
        "dexterity": -1,
        "intelligence": 0,
        "wisdom": 0,
        "constitution": 0,
    },
    6: {
        "strength": -1,
        "dexterity": -1,
        "intelligence": 0,
        "wisdom": 0,
        "constitution": -1,
    },
    7: {
        "strength": -1,
        "dexterity": -1,
        "intelligence": 0,
        "wisdom": 0,
        "constitution": -1,
    },
    8: {
        "strength": -1,
        "dexterity": -1,
        "intelligence": 0,
        "wisdom": 0,
        "constitution": -1,
    },
    9: {
        "strength": -1,
        "dexterity": -1,
        "intelligence": 0,
        "wisdom": 0,
        "constitution": -1,
    },
    10: {
        "strength": -1,
        "dexterity": -1,
        "intelligence": 0,
        "wisdom": 0,
        "constitution": -1,
    },
    11: {
        "strength": -1,
        "dexterity": -1,
        "intelligence": 0,
        "wisdom": 0,
        "constitution": -1,
    },
    12: {
        "strength": -1,
        "dexterity": -1,
        "intelligence": 0,
        "wisdom": 0,
        "constitution": -1,
    },
    13: {
        "strength": -2,
        "dexterity": -2,
        "intelligence": -1,
        "wisdom": -1,
        "constitution": -2,
    },
}

# AGING_TABLE stops at stage 13 (age 68), but decline doesn't stop there.
# Stages 14+ escalate by formula instead of a longer table: every stage past
# 13 adds a further -1 to STR/DEX/CON, and every second stage past 13 (15, 17,
# 19, ...) adds a further -1 to INT/WIS. Uncapped -- see reference/content/
# public-rules/characters/prior-experience/aging.md.
_LAST_TABULATED_AGING_STAGE = max(AGING_TABLE)  # 13

# Past the table, INT and WIS lose 1 every this many stages, STR, DEX and CON
# every stage. Read by the escalation below and by the rules lookup that
# states it (``gm.rules_lookup``).
STAGES_PER_MENTAL_AGING_STEP = 2

# The first stage that carries any penalty at all.
FIRST_AGING_STAGE = 5


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


def get_aging_penalties(stage_number: int) -> dict[str, int] | None:
    """Return the cumulative aging penalty totals for aging stage `stage_number`.

    Stages before 5 have no aging (``None``). Stages 4-13 come straight from
    ``AGING_TABLE``, unchanged. Stages past the table's last entry escalate by
    formula so aging never freezes for very old characters.
    """
    if stage_number < FIRST_AGING_STAGE:
        return None
    if stage_number in AGING_TABLE:
        return AGING_TABLE[stage_number]
    if stage_number > _LAST_TABULATED_AGING_STAGE:
        stages_past_table = stage_number - _LAST_TABULATED_AGING_STAGE
        last = AGING_TABLE[_LAST_TABULATED_AGING_STAGE]
        physical_penalty = last["strength"] - stages_past_table
        mental_penalty = last["intelligence"] - (
            stages_past_table // STAGES_PER_MENTAL_AGING_STEP
        )
        return {
            "strength": physical_penalty,
            "dexterity": physical_penalty,
            "constitution": physical_penalty,
            "intelligence": mental_penalty,
            "wisdom": mental_penalty,
        }
    return None


def get_aging_penalties_for_age(age: int) -> dict[str, int] | None:
    """The cumulative aging penalty totals a character of `age` carries."""
    return get_aging_penalties(aging_stage_for_age(age))


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
    """
    current_totals = get_aging_penalties_for_age(age) or {}
    for stage in range(aging_stage_for_age(age) + 1, horizon_stage + 1):
        totals = get_aging_penalties(stage)
        if not totals or totals == current_totals:
            continue
        non_zero = {attr: value for attr, value in totals.items() if value != 0}
        if non_zero:
            return age_for_aging_stage(stage), non_zero
    return None


# Skill tracks: maps track name to a list of 20 skills, one per d20 roll
# position (index 0 = a roll of 1). A d20 cannot exceed 20, so every position
# is reachable and there is no overflow entry.
SKILL_TRACKS: dict[str, list[str]] = {
    "merchant": [
        "Weapon",
        "Weapon Parry",
        "Coins",
        "Deceit",
        "Persuasion",
        "Sleight of Hand",
        "Literacy",
        "Street Smarts",
        "Weather/Path",
        "Morale",
        "Diplomacy",
        "Logistics",
        "Barter",
        "Bribery",
        "Appraise",
        "Perception",
        "Knowledge",
        "Bluff",
        "Luck",
        "Alert",
    ],
    "campaigner": [
        "Weapon Parry",
        "Weapon",
        "Weapon Parry",
        "Weapon",
        "Weapon Parry",
        "Weapon",
        "Weapon Parry",
        "Tracker",
        "Medic",
        "Hunting",
        "Coins",
        "Literacy",
        "Knowledge",
        "Alert",
        "Alert",
        "Perception",
        "Stealth",
        "Barter",
        "Weather/Path",
        "Survival",
    ],
    "laborer": [
        "Weapon",
        "Weapon Parry",
        "Coins",
        "Literacy",
        "Knowledge",
        "Maker Skill",
        "Maker Skill",
        "Portage",
        "Portage",
        "+1 STR",
        "+1 DEX",
        "+1 CON",
        "+1 STR",
        "+1 DEX",
        "+1 CON",
        "Gamble",
        "Miner",
        "Sailor",
        "Luck",
        "Herb Lore",
    ],
    "magic": [
        "Deceit",
        "Persuasion",
        "Literacy",
        "Literacy",
        "Sleight of Hand",
        "Perception",
        "Skill Point",
        "Spell Point",
        "Spell Point",
        "Spell Point",
        "Coins",
        "Medic",
        "Herb Lore",
        "Knowledge",
        "Knowledge",
        "Bluff",
        "Diplomacy",
        "Enchanting",
        "Enchanting",
        "Alert",
    ],
    "underworld": [
        "Weapon",
        "Weapon Parry",
        "Weapon",
        "Weapon Parry",
        "Deceit",
        "Persuasion",
        "Persuasion",
        "Literacy",
        "Street Smarts",
        "Stealth",
        "Sleight of Hand",
        "Bribery",
        "Appraise",
        "Perception",
        "Poison",
        "Gamble",
        "Bluff",
        "Diplomacy",
        "Coins",
        "Alert",
    ],
    "civil_service": [
        "Weapon",
        "Deceit",
        "Persuasion",
        "Detect Truth",
        "Detect Truth",
        "Knowledge",
        "Knowledge",
        "Weather/Path",
        "Diplomacy",
        "Logistics",
        "Barter",
        "Bribery",
        "Coins",
        "Coins",
        "Literacy",
        "Literacy",
        "Contracts/Law",
        "Appraise",
        "Street Smarts",
        "Sleight of Hand",
    ],
    "craft": [
        "Weapon",
        "Weapon Parry",
        "Enchanting",
        "Enchanting",
        "Arts",
        "Herb Lore",
        "Herb Lore",
        "Literacy",
        "Logistics",
        "Appraise",
        "Knowledge",
        "Coins",
        "Maker Skill",
        "Maker Skill",
        "Maker Skill",
        "Maker Skill",
        "Maker Skill",
        "Appraise",
        "Street Smarts",
        "Barter",
    ],
    "hunter_gatherer": [
        "Weapon",
        "Weapon Parry",
        "Weapon",
        "Weapon Parry",
        "Archery",
        "Archery",
        "Archery",
        "Tracker",
        "Tracker",
        "Herb Lore",
        "Herb Lore",
        "Knowledge",
        "Survival",
        "Alert",
        "Alert",
        "Poison",
        "Stealth",
        "Perception",
        "Hunting",
        "Hunting",
    ],
}

# All available tracks
ALL_TRACKS: list[str] = list(SKILL_TRACKS.keys())


# The three patterns normalize_skill_name applies. A skill name comes from a
# player's request, an imported file (up to 256 KB, checked against the
# 60-character limit only after it is normalized) or the model, so each runs
# in linear time (issue #11). Two things keep it so. No two repeated parts
# take the same characters: the bonus's text starts on a non-space the
# whitespace before it cannot take, and a run of whitespace is tried only
# from its first character (the lookbehinds), not again from each character
# inside it. And the parenthesis patterns only see the name up to its last
# ")": past it, every "(" scanned to the end of the name and failed, so a
# name of many unclosed "(+1 " was scanned once per "(".
_BONUS_IN_PARENTHESES = re.compile(r"\(\+?\d+\s+(\S[^)\n]*|[^\S\n])\)")
_PARENTHESES = re.compile(r"(?<!\s)\s*\([^)]*\)")
_ROMAN_NUMERAL_SUFFIX = re.compile(r"(?<!\s)\s+[ivx]+\s*$", re.IGNORECASE)


def normalize_skill_name(raw_name: str) -> str:
    """
    Normalize a skill name for storage.

    Rules:
    - Convert to lowercase
    - Drop control characters (they have no place in a skill name and would
      travel into the page as a data-skill attribute value)
    - Strip parenthetical modifiers like (+1 to hit)
    - Remove trailing Roman numerals (I, II, III, IV, V)
    - Collapse whitespace

    Quotes and apostrophes are deliberately left alone -- "wolf's bane" is a
    legitimate skill name -- and the templates escape them for both text and
    attribute contexts.

    Examples:
    - "Sword" -> "sword"
    - "Sword (+1 to hit)" -> "sword to hit"
    - "Shield Parry II" -> "shield parry"
    - "  Navigation  " -> "navigation"
    """
    name = strip_control_characters(raw_name).lower()

    # Remove parenthetical modifiers but keep content like "to hit"
    # Match patterns like "(+1 to hit)" and extract "to hit" part. Every match
    # ends on a ")", so nothing past the last one can match.
    closed = name[: name.rfind(")") + 1]
    paren_match = _BONUS_IN_PARENTHESES.search(closed)
    if paren_match:
        suffix = paren_match.group(1)
        name = _PARENTHESES.sub("", closed) + name[len(closed) :]
        name = f"{name.strip()} {suffix}"

    # Remove trailing Roman numerals (case-insensitive since we lowercased)
    name = _ROMAN_NUMERAL_SUFFIX.sub("", name)

    # Collapse whitespace
    name = " ".join(name.split())

    return name


#: The glossary skills taken with a chosen part, recorded in the skill's own
#: name the way a Weapon skill records its weapon ("weapon (broadsword)",
#: ``characters.weapon_skill``): Maker Skill is "A **chosen craft** (smith,
#: carpenter, tailor...)", and Knowledge may be focused on "a field" (skills
#: glossary). Issue #808. "maker skill (smith)" is the Maker Skill, at the
#: Maker Skill's cost and governed by its attribute.
SPECIALISED_SKILLS: tuple[str, ...] = ("maker skill", "knowledge")

_SPECIALISED_NAME = re.compile(r"^(?P<skill>.+?) \((?P<detail>[^()]+)\)$")


def split_specialised_skill(skill_name: str) -> tuple[str, str]:
    """``(glossary skill, chosen part)`` for a Maker Skill or Knowledge name.

    ``"maker skill (smith)"`` is ``("maker skill", "smith")``; a bare
    ``"knowledge"`` is ``("knowledge", "")``. Any other name comes back as
    ``(name, "")``: only :data:`SPECIALISED_SKILLS` carry a chosen part here
    (the Weapon skill's is its weapon, ``characters.weapon_skill``).

    Args:
        skill_name: A stored (normalised) skill name.
    """
    match = _SPECIALISED_NAME.match(skill_name)
    if match and match.group("skill") in SPECIALISED_SKILLS:
        return match.group("skill"), match.group("detail").strip()
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

    Flattened from ``SKILL_TRACKS``, normalized, deduplicated, and sorted --
    the single source of truth the character editor's add-skill type-ahead
    reads from (issue #224), so its suggestion list can never drift from what
    the prior-experience generator actually rolls.
    """
    normalized_names = {
        normalize_skill_name(raw) for table in SKILL_TRACKS.values() for raw in table
    }
    return sorted(
        name for name in normalized_names if not is_special_experience_result(name)
    )


#: The attribute table's first and last printed rows (attributes.md).
MODIFIER_TABLE_LOWEST_SCORE = 3
MODIFIER_TABLE_HIGHEST_SCORE = 18


def calculate_attribute_modifier(value: int) -> int:
    """Modifier for an attribute value, per the Tarmar attributes table.

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
    Spencer may overrule.
    """
    value = min(max(value, MODIFIER_TABLE_LOWEST_SCORE), MODIFIER_TABLE_HIGHEST_SCORE)
    if value <= 7:
        return value - 8
    if value <= 13:
        return 0
    return value - 13


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
    """
    if track not in SKILL_TRACKS:
        track = "laborer"  # Fallback

    table = SKILL_TRACKS[track]
    if position > len(table):
        return table[-1]

    # Position 1 is index 0
    return table[position - 1]


def get_random_track() -> str:
    """Get a random track (all tracks are unrestricted)."""
    return random.choice(ALL_TRACKS)


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
    track_data = TRACK_DATA.get(track, TRACK_DATA["laborer"])
    xp_per_year = track_data["xp_per_year"]
    survival_target = track_data["survivability"]
    money_multiplier = track_data["money_multiplier"]
    uses_maker_skill = track_data.get("money_skill") == "maker"

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

    track_table = SKILL_TRACKS.get(track, SKILL_TRACKS["laborer"])
    max_skill_index = len(track_table)
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

        # Levels are read per skill, not per point total, because Arcane's
        # points buy levels at half the rate (issue #154).
        highest_skill_level = max(
            (
                get_skill_level(pts, running_name)
                for running_name, pts in running_skills.items()
            ),
            default=0,
        )
        maker_skill_level = glossary_skill_level(running_skills, "maker skill")

        # Calculate money for this year from skill levels as of this point in
        # the run (floored at level 1: everyone earns at least the track's
        # base rate, even with no skills -- see survivability.md).
        skill_level_for_money = (
            maker_skill_level if uses_maker_skill else highest_skill_level
        )
        year_money = money_multiplier * max(1, skill_level_for_money)

        # Survival check for this year: 3d6 + best modifier >= target
        # (issue #223 -- same targets, three dice).
        first_die, second_die, third_die, roll_total = roll_survival_dice()
        survival_total = roll_total + best_mod
        auto_success = roll_total == SURVIVAL_AUTO_SURVIVE_ROLL
        auto_death = roll_total == SURVIVAL_AUTO_DEATH_ROLL

        if auto_death:
            survival_success = False
        elif auto_success:
            survival_success = True
        else:
            survival_success = survival_total >= survival_target

        survival_check = {
            "dice": [first_die, second_die, third_die],
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
