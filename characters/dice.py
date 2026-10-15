"""Dice rolling utilities for character creation and combat.

All randomness draws from a module-default :class:`common.rolling.Roller`
(issue #175) — the project's single roll service. The public functions here
keep their historic signatures; they are the character-creation-flavoured
face of that one roller.

Django-free on purpose: ``gm/engine.py`` (imported standalone by the offline
CLI) delegates its dice parsing here, so nothing in this module's import chain
may reach Django.
"""

# ``random`` is the patch target existing tests use
# (``patch("characters.dice.random.randint")``): the unseeded module-default
# Roller below draws through this same module object at call time, so those
# patches — and a global ``random.seed()`` — still steer every roll.
#
# The character-creation chain also accepts a caller-supplied ``roller``
# (issue #261, so a battle's roster is genuinely a function of its seed). A
# *seeded* Roller draws from its own private ``random.Random``, which those
# patches and ``random.seed()`` cannot reach — that is the whole point of
# passing one. Only callers that pass a seeded roller opt out; every call
# site that omits the argument keeps rolling through the module default, so
# the existing hooks are untouched.
import random  # noqa: F401
import re

from common.rolling import Roller, RollRecord, parse_dice_specification

from .constants import CORE_ATTRIBUTE_FIELDS, point_buy_budget_note
from .rules import CoinRoll, character_rules

# The module-default roller every helper below rolls through. Unseeded, so it
# draws from the module-global ``random`` (see ``common.rolling.Roller``).
_DEFAULT_ROLLER = Roller()


def roll_d6(roller: Roller | None = None) -> int:
    """Roll a single d6.

    Args:
        roller: Roll through this roller instead of the module default. A
            seeded one makes the result reproducible (see the module note).
    """
    return (roller or _DEFAULT_ROLLER).roll("1d6", purpose="d6").total


def roll_d20():
    """Roll a single d20."""
    return _DEFAULT_ROLLER.roll("1d20", purpose="d20").total


def roll_d100(roller: Roller | None = None) -> int:
    """Roll percentile dice (1-100).

    Args:
        roller: Roll through this roller instead of the module default.
    """
    return (roller or _DEFAULT_ROLLER).roll("1d100", purpose="d100").total


def roll_3d6():
    """Roll 3d6 and return the sum (3-18)."""
    return _DEFAULT_ROLLER.roll("3d6", purpose="3d6").total


def roll_3d6_logged(roller: Roller | None = None) -> tuple[int, str]:
    """Roll 3d6 and return (result, log_entry).

    Args:
        roller: Roll through this roller instead of the module default.
    """
    return roll_dice_logged("3d6", roller)


def roll_3d6_faces() -> tuple[int, int, int, int]:
    """Roll 3d6 and return (die1, die2, die3, total).

    The survival-check shape ``characters.skills`` uses since issue #223 moved
    the prior-experience survival roll from 2d6 to 3d6: every face is kept so
    the experience log can show the actual dice, not just their sum.
    """
    record = _DEFAULT_ROLLER.roll("3d6", purpose="3d6")
    first_die, second_die, third_die = record.faces
    return first_die, second_die, third_die, record.total


def roll_2d6_faces(roller: Roller | None = None) -> tuple[int, int, int]:
    """Roll 2d6 and return (die1, die2, total).

    The survival-check shape ``characters.skills`` uses: both faces are kept
    so the experience log can show the actual dice, not just their sum.

    Args:
        roller: Roll through this roller instead of the module default.
    """
    record = (roller or _DEFAULT_ROLLER).roll("2d6", purpose="2d6")
    first_die, second_die = record.faces
    return first_die, second_die, record.total


def roll_d6_logged(roller: Roller | None = None) -> tuple[int, str]:
    """Roll a plain d6 and return (result, log_entry). No exploding chain.

    Args:
        roller: Roll through this roller instead of the module default.
    """
    result = roll_d6(roller)
    log = f"1d6: {result}"
    return result, log


def roll_d20_logged():
    """Roll a single d20 and return (result, log_entry)."""
    result = roll_d20()
    return result, f"d20: {result}"


# The project's single dice-notation parser (issues #105 and #175) now lives
# in ``common.rolling``; this name is the historic import point weapon damage
# rolls and ``gm.engine.roll_dice`` (the GM's ``/roll``) come through, with
# the same bounds and the same error messages.
parse_dice_expression = parse_dice_specification


#: Reads a roll back out of the text the ``_logged`` helpers and
#: :mod:`characters.services` write, e.g. ``3d6: [5, 6, 1] = 12``,
#: ``1d8+1: [4] = 5`` (a canon's dice may carry a modifier, issue #59) or the
#: survival line's ``3d6 [4, 6, 2] = 12`` (the separator differs between the
#: two producers, so it is optional here).
#:
#: Actions other than ``roll`` report their dice in the text of a procedure
#: frame rather than in a ``gm_roll`` frame, so a reader that wants every die
#: the server rolled -- ``scripts/gm_behaviour_check.py`` does -- has to read
#: them out of prose. Keeping the pattern here, next to the writers, means the
#: two cannot drift apart unnoticed (issue #604).
DICE_IN_TEXT = re.compile(r"(\d*d\d+(?:[+-]\d+)?)\s*:?\s*\[([\d,\s]+)\]\s*=\s*(\d+)")


def roll_expression_record(
    expression: str, *, purpose: str = "expression", roller: Roller | None = None
) -> RollRecord:
    """Roll a dice expression and return the whole :class:`RollRecord`.

    The full-fidelity face of :func:`roll_expression`: the caller that needs
    the faces *and* what the roll was for (the GM asking the server to roll
    mid-turn, issue #363) gets the record the roll service already built,
    rather than a second helper rebuilding one beside it.

    Args:
        expression: ``NdS`` or ``NdS±M``, e.g. ``"2d6+3"``.
        purpose: What the roll is for; recorded verbatim on the record.
        roller: Roll through this roller instead of the module default. A
            seeded one makes the result reproducible (see the module note).

    Returns:
        The completed :class:`common.rolling.RollRecord`.

    Raises:
        ValueError: If the expression does not parse or is out of bounds.
    """
    return (roller or _DEFAULT_ROLLER).roll(expression, purpose=purpose)


def roll_expression(expression):
    """Roll a dice expression and return (individual_rolls, total).

    The modifier is folded into the total, not into the individual rolls, so a
    caller can print both the dice it got and what they came to.
    """
    record = roll_expression_record(expression)
    return list(record.faces), record.total


def roll_expression_logged(expression):
    """Roll a dice expression and return (rolls, total, log_entry).

    Three values rather than the ``(result, log)`` pair the other ``_logged``
    helpers return: an attack breakdown itemizes the individual dice, so the
    caller needs them alongside the total.
    """
    rolls, total = roll_expression(expression)
    dice = ", ".join(str(roll) for roll in rolls)
    log = f"{expression.strip().lower()}: [{dice}] = {total}"
    return rolls, total, log


def roll_demon_die():
    """
    Roll a demon die - an exploding d6.

    Roll until you get 2-5. Record 1s and 6s as a chain.
    Returns a string like "3", "1.4", "6.6.2", "1.1.1.5".
    """
    rolls = []
    while True:
        roll = roll_d6()
        rolls.append(roll)
        if 2 <= roll <= 5:
            break
    return ".".join(str(r) for r in rolls)


def roll_demon_die_logged(roller: Roller | None = None) -> tuple[str, str]:
    """Roll demon die and return (result, log_entry).

    Args:
        roller: Roll through this roller instead of the module default.
    """
    rolls = []
    log_parts = []
    while True:
        roll = roll_d6(roller)
        rolls.append(roll)
        log_parts.append(str(roll))
        if 2 <= roll <= 5:
            break
        log_parts.append(" -> explode -> ")
    result = ".".join(str(r) for r in rolls)
    log = f"Demon d6: {''.join(log_parts)} = {result}"
    return result, log


def parse_demon_die(roll_str):
    """
    Parse a demon die string into a list of integers.

    "1.1.3" -> [1, 1, 3]
    "4" -> [4]

    Returns [3] (average) for invalid input to prevent crashes.
    """
    if not roll_str or not isinstance(roll_str, str):
        return [3]  # Default to average roll

    try:
        parts = roll_str.strip().split(".")
        result = []
        for part in parts:
            val = int(part)
            if val < 1 or val > 6:
                return [3]  # Invalid die value, return default
            result.append(val)
        return result if result else [3]
    except (ValueError, AttributeError):
        return [3]  # Return average on parse failure


def demon_die_to_height_inches(roll_str):
    """
    Convert a demon die roll to height in inches.

    Base height from the FIRST roll (attributes.md's Height table -- issue
    #295), then each additional roll beyond that first one adds the canon's
    ``height.inches_per_extra_one`` for a 1 and ``inches_per_extra_six`` for
    a 6 (the public pages: -4" and +4"). A terminating 2-5 roll only stops
    the chain; it isn't itself a base or an adjustment unless it was the
    only roll.
    """
    height = character_rules().height
    rolls = parse_demon_die(roll_str)
    base_height = height.base_inches_by_face[rolls[0] - 1]

    # Count 1s and 6s in the chain beyond the first roll.
    adjustment = 0
    for r in rolls[1:]:
        if r == 1:
            adjustment += height.inches_per_extra_one
        elif r == 6:
            adjustment += height.inches_per_extra_six

    # Floor the result: a long all-1s chain (possible via an edited/imported
    # height_roll string) must not yield a negative or absurd height.
    return max(height.minimum_inches, base_height + adjustment)


def format_height(inches):
    """Format height in inches as feet'inches" string."""
    feet = inches // 12
    remaining_inches = inches % 12
    return f"{feet}'{remaining_inches}\""


def weight_roll_to_stones(roll_str):
    """
    Convert a base weight roll to stones.

    Rules: base weight is the player's choice within the canon's band, or a
    plain 1d6 roll mapped straight into it by ``weight.stones_by_face``. No
    exploding chain, no per-die adjustment. A stored value with a legacy
    demon-die chain (from before this was a plain 1d6) is parsed for its
    final digit only, so old character data still resolves sensibly.
    """
    rolls = parse_demon_die(roll_str)
    final_roll = rolls[-1]
    return character_rules().weight.stones_by_face[final_roll - 1]


def calculate_final_weight_lbs(roll_str, strength):
    """
    Calculate final weight in pounds.

    Final weight = Base weight + (STR / ``weight.strength_divisor``) stones,
    rounded down, at ``weight.pounds_per_stone`` pounds a stone.
    """
    weight = character_rules().weight
    base_stones = weight_roll_to_stones(roll_str)
    str_bonus_stones = strength // weight.strength_divisor
    total_stones = base_stones + str_bonus_stones
    return total_stones * weight.pounds_per_stone


def roll_dice_logged(
    specification: str, roller: Roller | None = None
) -> tuple[int, str]:
    """Roll a dice specification and return (total, log_entry).

    The log reads ``"2d6: [3, 4] = 7"``, the shape every ``_logged`` helper
    here writes and :data:`DICE_IN_TEXT` reads back.

    Args:
        specification: ``NdS`` or ``NdS±M``, as the canon's rules state it.
        roller: Roll through this roller instead of the module default.
    """
    record = (roller or _DEFAULT_ROLLER).roll(specification, purpose=specification)
    faces = ", ".join(str(face) for face in record.faces)
    return record.total, f"{specification}: [{faces}] = {record.total}"


def roll_fatigue_logged(roller: Roller | None = None) -> tuple[int, str]:
    """Roll the Fatigue roll on the canon's dice; return (total, log_entry).

    Args:
        roller: Roll through this roller instead of the module default.
    """
    return roll_dice_logged(character_rules().fatigue_roll.dice, roller)


def roll_attribute_logged(roller: Roller | None = None) -> tuple[int, str]:
    """Roll one core attribute on the canon's dice; return (total, log_entry).

    Args:
        roller: Roll through this roller instead of the module default.
    """
    return roll_dice_logged(character_rules().attribute_roll.dice, roller)


def roll_literacy_logged(roller: Roller | None = None) -> tuple[int, str]:
    """Roll the literacy roll on the canon's dice; return (total, log_entry).

    Args:
        roller: Roll through this roller instead of the module default.
    """
    return roll_dice_logged(character_rules().literacy_roll.dice, roller)


# Location effects come from the canon's ``location`` table: each origin's
# skills (the one granted first), the points the grant starts at, and the
# modifier on the INT check to be literate. "Consult GM" grants no skill and
# carries no published literacy modifier.
#
# Village reads "Street smarts OR survival (choose)". The location is rolled
# server-side during creation, after the player has already submitted the form,
# so there is nowhere to ask them at the moment of the choice. It grants the
# first listed skill and the player switches it with the rename-skill control
# on the sheet; the Location help text says so, and the canon records the
# reading as a ruling (issue #754: an Origin that changes moves its grant, and
# a Village character who took Survival holds the Village grant in it).


def location_skill_choices(location: str) -> tuple[str, ...]:
    """The skills an origin's grant may be held in, the granted one first.

    Args:
        location: A location table label, e.g. ``"Village"``.

    Returns:
        The skills; empty for an origin with no grant or a label the table
        does not have.
    """
    row = character_rules().location.row_labelled(location)
    return row.skills if row is not None else ()


def location_literacy_modifier(location: str) -> int | None:
    """The penalty an origin sets on the literacy roll.

    Args:
        location: A location table label.

    Returns:
        The modifier, or ``None`` where the table publishes none ("Consult
        GM", or a label the table does not have).
    """
    row = character_rules().location.row_labelled(location)
    return row.literacy_modifier if row is not None else None


def roll_location_logged(roller: Roller | None = None) -> tuple[str, str]:
    """
    Roll location (City, Village, Rural, or Consult GM).

    Args:
        roller: Roll through this roller instead of the module default.

    Returns (location, log_entry).
    """
    roll = roll_d100(roller)
    location = character_rules().location.row_for(roll).label
    log = f"Location: d100={roll} -> {location}"
    return location, log


def location_effects(location: str) -> tuple[dict[str, int], str]:
    """The skills a location grants a new character, plus a log line.

    Args:
        location: One of the location table's labels.

    Returns:
        ``(skills, log_entry)`` where ``skills`` maps the normalized skill name
        to its starting points (empty for "Consult GM"), and ``log_entry``
        records both the grant and the literacy-check modifier for the dice log.
    """
    # Deferred: characters.skills imports this module, so importing it at module
    # scope would be circular.
    from .skills import normalize_skill_name

    location_table = character_rules().location
    row = location_table.row_labelled(location)
    skill_name = row.granted_skill if row is not None else None

    if skill_name is None:
        return {}, f"Location effects: {location}"

    skill_points = location_table.skill_points
    skills = {normalize_skill_name(skill_name): skill_points}
    log = (
        f"Location effects: {skill_name} {skill_points} point, "
        f"{literacy_check_text(location)}"
    )
    return skills, log


def literacy_check_text(location: str) -> str:
    """The literacy check a location sets, as the dice log words it.

    The modifier is a penalty, so the log takes it off INT rather than
    printing a bare "+2" that reads like a bonus. The roll itself is spelled
    out on the sheet, not here: the log stays free of dice notation so the
    point-buy path's log carries none at all.
    """
    literacy_modifier = location_literacy_modifier(location)
    literacy = "Literacy check INT"
    if literacy_modifier:
        literacy = f"{literacy} - {literacy_modifier}"
    return literacy


def wealth_gives_coin(wealth_level: str) -> bool:
    """Whether the wealth table gives this level a coin figure (issue #749).

    Every level but the one whose coin is ``null`` in the canon does; on the
    public pages that is Rich, "Consult GM".
    """
    row = character_rules().wealth.row_labelled(wealth_level)
    return row is not None and row.coin is not None


def starting_coin_for_wealth(
    wealth_level: str, roller: Roller | None = None
) -> tuple[int | None, str]:
    """Starting coin for a wealth level, with a human-readable detail string.

    The canon's ``wealth`` table gives each level a fixed coin, a base plus
    dice (on the public pages, Merchant's 100 + d100), or ``null`` for the
    Game Master to set (Rich, "Consult GM", which gives ``None``). Single
    source for this rule (the character roller, the wealth roll on the sheet
    and a Game Master's first wealth setting).

    Args:
        wealth_level: One of the wealth table's labels.
        roller: Roll a level's dice through this roller instead of the
            module default. Ignored for the fixed levels, which roll nothing.

    Raises:
        KeyError: ``wealth_level`` is not a label of the wealth table.
    """
    row = character_rules().wealth.row_labelled(wealth_level)
    if row is None:
        raise KeyError(wealth_level)
    coin = row.coin
    if isinstance(coin, CoinRoll):
        # Percentile dice are rolled through roll_d100, the one d100 every
        # table roll here makes; the log writes them as it always has
        # ("100 + d100(57) = 157").
        if coin.is_percentile:
            bonus_roll = roll_d100(roller)
        else:
            record = (roller or _DEFAULT_ROLLER).roll(coin.dice, purpose=coin.dice)
            bonus_roll = record.total
        total = coin.base + bonus_roll
        return total, f"{coin.base} + {coin.dice_label}({bonus_roll}) = {total}"
    if coin is None:
        return None, "Consult GM"
    return coin, str(coin)


def roll_wealth_logged(roller: Roller | None = None) -> tuple[str, int | None, str]:
    """
    Roll wealth level and starting coin.

    Args:
        roller: Roll through this roller instead of the module default.

    Returns (wealth_level, starting_coin, log_entry).
    """
    roll = roll_d100(roller)
    wealth_level = character_rules().wealth.row_for(roll).label
    starting_coin, detail = starting_coin_for_wealth(wealth_level, roller)
    log = f"Wealth: d100={roll} -> {wealth_level}, starting coin = {detail}"
    return wealth_level, starting_coin, log


def roll_provenance_logged(roller: Roller | None = None) -> tuple[str, str, str]:
    """
    Roll provenance (social class and detail).

    The class table's labels name the table its detail is rolled on next
    (``characters.rules`` holds them to ``nobility``, ``merchant`` and
    ``commoner``).

    Args:
        roller: Roll through this roller instead of the module default.

    Returns (class, detail, log_entry).
    """
    rules = character_rules()
    class_roll = roll_d100(roller)
    social_class = rules.social_class.lookup(class_roll)
    detail_roll = roll_d100(roller)
    detail = rules.detail_table(social_class).lookup(detail_roll)
    log = (
        f"Provenance: d100={class_roll} -> {social_class.capitalize()}, "
        f"d100={detail_roll} -> {detail}"
    )
    return social_class, detail, log


def roll_physical_features(
    roller: Roller | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Roll appearance, height and weight: the dice nobody chooses.

    Height uses the exploding demon die (documented rule). Appearance and base
    weight are plain 1d6 rolls with no re-roll or chaining. The Games Master
    rolls these on the server the moment a character's record is created
    (issue #571), because code owns the dice.

    Args:
        roller: Roll through this roller instead of the module default.

    Returns:
        ``(stats, log_entries)``: the three roll fields and their log lines.
    """
    appearance_value, appearance_log = roll_d6_logged(roller)
    height_value, height_log = roll_demon_die_logged(roller)
    weight_value, weight_log = roll_d6_logged(roller)
    stats = {
        "appearance_roll": str(appearance_value),
        "height_roll": height_value,
        "weight_roll": str(weight_value),
    }
    log_entries = [
        f"Appearance: {appearance_log}",
        f"Height: {height_log}",
        f"Weight: {weight_log}",
    ]
    return stats, log_entries


def roll_secondary_character_stats(roller: Roller | None = None):
    """
    Roll everything on a new character except the six core attributes.

    Height, weight, appearance, fatigue, provenance, location and wealth are
    always rolled, whether the player rolled their core attributes or bought
    them with points. The rolled location also seeds the character's skills
    with the skill that location grants (see `location_effects`).

    Args:
        roller: Roll through this roller instead of the module default.

    Returns (stats_dict, log_entries).
    """
    log_entries: list[str] = []
    stats: dict = {}

    # Physical attributes. Height uses the exploding demon die (documented
    # rule). Appearance and base weight are plain 1d6 rolls with no re-roll
    # or chaining.
    physical, physical_log = roll_physical_features(roller)
    stats.update(physical)
    log_entries.extend(physical_log)

    fatigue_value, fatigue_log = roll_fatigue_logged(roller)
    stats["fatigue_roll"] = fatigue_value
    log_entries.append(f"Fatigue: {fatigue_log}")

    provenance_class, provenance_detail, provenance_log = roll_provenance_logged(roller)
    log_entries.append(provenance_log)

    location, location_log = roll_location_logged(roller)
    log_entries.append(location_log)

    location_skills, location_effects_log = location_effects(location)
    log_entries.append(location_effects_log)

    wealth_level, starting_coin, wealth_log = roll_wealth_logged(roller)
    log_entries.append(wealth_log)

    stats.update(
        {
            "provenance_class": provenance_class,
            "provenance_detail": provenance_detail,
            "location": location,
            "skills": location_skills,
            # Which skill holds the grant, so an Origin change moves exactly
            # that point (issue #754).
            "origin_skill": next(iter(location_skills), ""),
            "wealth_level": wealth_level,
            "starting_coin": starting_coin,
        }
    )
    return stats, log_entries


def roll_all_character_stats(roller: Roller | None = None):
    """
    Roll all stats for a new character, core attributes included, each on
    the canon's attribute dice (3d6 on the public pages).

    Args:
        roller: Roll every die through this roller instead of the module
            default. A seeded one makes the whole character reproducible —
            which is what lets a battle's roster be a function of its seed
            (issue #261).

    Returns a dict with all rolled values and a dice_log.
    """
    log_entries: list[str] = []
    stats: dict = {}

    # Core attributes, rolled in order on the canon's attribute dice.
    for field_name, label in CORE_ATTRIBUTE_FIELDS:
        value, entry_log = roll_attribute_logged(roller)
        stats[field_name] = value
        log_entries.append(f"{label}: {entry_log}")

    secondary_stats, secondary_log = roll_secondary_character_stats(roller)
    stats.update(secondary_stats)
    log_entries.extend(secondary_log)

    stats["dice_log"] = "\n".join(log_entries)
    return stats


def build_point_buy_character_stats(attributes):
    """
    Build a new character's stats from player-bought core attributes.

    Everything outside the six core attributes is rolled exactly as it is on the
    rolled path; only the core six come from `attributes`. The dice log records
    "point-buy" where a rolled character records its 3d6 chain, so the log stays
    line-per-stat for the readers that render it.

    Args:
        attributes: Mapping of core attribute field name to its bought value.
            Already validated by the caller.

    Returns:
        A dict shaped like `roll_all_character_stats`'s, including a dice_log.
    """
    log_entries: list[str] = []
    stats: dict = {}

    spent = 0
    for field_name, label in CORE_ATTRIBUTE_FIELDS:
        value = attributes[field_name]
        stats[field_name] = value
        spent += value
        log_entries.append(f"{label}: point-buy = {value}")

    # Only an admin or DM reaches here off budget (issue #299). The dice log is
    # where that belongs: a character built outside the 65 points should say so
    # for as long as it exists, not only in a warning whoever built it saw once
    # on the creation form.
    budget_note = point_buy_budget_note(spent)
    if budget_note is not None:
        log_entries.append(budget_note)

    secondary_stats, secondary_log = roll_secondary_character_stats()
    stats.update(secondary_stats)
    log_entries.extend(secondary_log)

    stats["dice_log"] = "\n".join(log_entries)
    return stats
