"""Answers to rules questions, read out of the code (issue #605).

The Game Master's GM refuses to state a rules number it was not given. That
rule is deliberate and expensive: #370 was the model inventing a survival
target -- "the campaigner track requires a survival roll of 3d6 <= 12" -- and
killing a character on a natural 18, which the rules make an automatic
survival. The prohibition in ``gm.engine``'s outcomes section exists so that
cannot happen again, and it is not relaxed here.

What it cost was the ordinary case. Asked "the campaigner survival roll is
3d6 and you have to get 12 or under, right?", the GM declined to say
anything at all, so a player kept a rule that is backwards.

The fix is provenance rather than permission. A ``rules_lookup`` action asks
this module, the answer comes back as a turn result like a dice roll, and the
model may then state the number because the server gave it to it. Every
sentence below is built from the constants and tables the rules actually use,
so an answer cannot drift from the code the way the model's memory did.
"""

from collections.abc import Callable

from characters.combat import MATRIX
from characters.constants import (
    CORE_ATTRIBUTE_FIELDS,
    MINOR_INJURY_ROLL_PENALTY,
    SEVERE_INJURY_BAND_TOP,
    SEVERE_INJURY_ROLL_PENALTY,
)
from characters.rules import CoinRoll, character_rules
from characters.skill_rules import AGING_ATTRIBUTES, skill_rules
from characters.skills import (
    age_for_aging_stage,
    normalize_skill_name,
    survival_rule_description,
    survival_targets,
    track_key_for,
)

#: The longest a subject may be, so a plan cannot smuggle prose in here.
MAX_SUBJECT_CHARS = 60

#: The longest the track a lookup narrows to may be (issue #938). No column
#: holds it: it is read against the survival tracks' names and, when it names
#: none, the general answer is given (#605), so it only needs room for a
#: track named loosely ("the campaigner track") and none for prose.
MAX_TRACK_CHARS = 40


class UnknownRule(LookupError):
    """No rule is filed under that subject; ``str()`` lists what is."""


def targets_written() -> str:
    """Every track's survivability target, in one line."""
    return ", ".join(
        f"{name} {target}" for name, target in sorted(survival_targets().items())
    )


def _survival(track: str = "") -> str:
    """The prior-experience survival check, for one track or in general."""
    survival_roll = skill_rules().survival_roll
    general = (
        f"Prior-experience survival: {survival_rule_description()} "
        f"A natural {survival_roll.automatic_survival_total} is never a death "
        f"and a natural {survival_roll.automatic_death_total} is never a "
        "survival."
    )
    if not track:
        return f"{general} Survivability targets: {targets_written()}."
    named = track_key_for(track)
    targets = survival_targets()
    if named not in targets:
        # A lookup always answers. The model phrases the narrowing field
        # however the question came out -- "base TN Piercing vs None" arrived
        # in it once -- and refusing left the GM narrating an apology at the
        # player instead of the rule (issue #605). The general answer carries
        # every target, so it answers the question regardless.
        return f"{general} Survivability targets: {targets_written()}."
    return (
        f"{general} The {named} track's survivability target is "
        f"{targets[named]}, so on {survival_roll.dice} plus the "
        f"best attribute modifier the character survives on "
        f"{targets[named]} or more."
    )


def _survival_targets(track: str = "") -> str:
    return _survival(track)


def _combat(weapon_class: str = "") -> str:
    """The attack matrix, from the table the code resolves attacks against.

    The whole table is twelve numbers, so it is given whole rather than a
    pairing at a time: a turn that has it can answer the follow-up without
    asking again.
    """
    opening = (
        "Attack: roll d20, add the to-hit bonus, and compare with the target "
        "number for the weapon class against the defender's armour; the "
        "attack hits on the target number or more."
    )
    named = weapon_class.strip().casefold()
    wanted = [weapon for weapon in MATRIX if not named or weapon.casefold() == named]
    if not wanted:
        # An unrecognised class means the whole table, for the same reason.
        wanted = list(MATRIX)
    rows = [
        f"{weapon} vs "
        + ", ".join(f"{armour} {target}" for armour, target in MATRIX[weapon].items())
        for weapon in wanted
    ]
    return f"{opening} " + "; ".join(rows) + "."


def signed(number: int) -> str:
    """``+2``, ``0`` or ``−3``, with the minus sign the prompt uses."""
    if number > 0:
        return f"+{number}"
    return str(number).replace("-", "−")


def _aging(_narrowing: str = "") -> str:
    """The aging schedule, from the canon's aging table (#807, #70).

    Only the ages where a total changes, each with the cumulative totals it
    carries, as aging.md's table gives them. A lookup rather than a line of
    the prompt: stated in the creation section, the tables drew the model
    into recording backgrounds nobody had stated (the 2026-09-29 board).
    """
    labels = {
        name: short for name, short in CORE_ATTRIBUTE_FIELDS if name != "charisma"
    }
    aging = skill_rules().aging
    steps: list[str] = []
    before: dict[str, int] | None = None
    for aging_stage in aging.stages:
        totals = aging_stage.penalties
        if totals != before and any(totals.values()):
            said = ", ".join(
                f"{labels[name]} {signed(totals[name])}"
                for name in AGING_ATTRIBUTES
                if totals[name]
            )
            steps.append(f"{age_for_aging_stage(aging_stage.stage)} {said}")
        before = totals
    age_rules = character_rules().age
    return (
        f"Aging, keyed to age from {age_rules.starting}, as cumulative lifetime "
        "totals, each replacing the last: "
        + "; ".join(steps)
        + "; after that STR, DEX and CON lose 1 more every "
        f"{age_rules.years_per_stage} years and INT and WIS every "
        f"{aging.stages_per_mental_step * age_rules.years_per_stage}. An aged "
        "score never goes below 0; "
        "play uses it, and only the literacy check and the weight from STR use "
        "the base score."
    )


def _origins(_narrowing: str = "") -> str:
    """What each Origin gives, from the canon's location table (#807, #59)."""
    rules = character_rules()
    rows = []
    for row in rules.location.rows:
        if row.literacy_modifier is None:
            rows.append(
                f"{row.label}: the Game Master decides what it gives and whether "
                "the character can read"
            )
            continue
        check = rules.literacy_roll.check_text(row.literacy_modifier)
        rows.append(f"{row.label}: {' or '.join(row.skills)}, literacy check {check}")
    return (
        "Origins: "
        + "; ".join(rows)
        + ". The literacy check decides whether a character can read at all; "
        "the Literacy skill is only how well."
    )


def _starting_coin(_narrowing: str = "") -> str:
    """Starting coin by wealth level, from the canon's wealth table (#807, #59)."""
    rows = []
    for row in character_rules().wealth.rows:
        coin = row.coin
        if isinstance(coin, CoinRoll):
            rows.append(f"{row.label} {coin.base} + {coin.dice_in_words}")
        elif coin is not None:
            rows.append(f"{row.label} {coin}")
        else:
            rows.append(f"{row.label}: the Game Master sets it")
    return "Starting coin, rolled with the wealth level, once: " + "; ".join(rows) + "."


def _skill_check(skill: str = "") -> str:
    """The skills glossary's check, and which attribute a skill rides on (#843).

    Built from the canon's skill check (``skill-rules.json``, issue #70),
    which :mod:`characters.skill_checks` rolls and whose default Governs
    table is held to the glossary by a drift test. ``skill`` names one skill;
    unnamed or unknown, every governing attribute is given.
    """
    # Imported here: gm.engine reads this module's subjects for its prompt,
    # and must import without Django (the offline CLI), which the check's
    # module needs for the character model.
    from characters.skill_checks import MAX_DIFFICULTY, governing_attribute

    checks = skill_rules().skill_check
    general = (
        f"Skill check: roll {checks.dice}, add the governing attribute's "
        "modifier (from its aged score) and the skill level, against a "
        f"difficulty the Game Master sets (0 to {MAX_DIFFICULTY}); a player "
        f"sets none. A natural {checks.natural_success} always succeeds and a "
        f"natural {checks.natural_failure} always fails; untrained, the "
        "modifier alone. Luck is not rolled as a check."
    )
    named = normalize_skill_name(skill) if skill.strip() else ""
    governing = governing_attribute(named) if named else None
    if governing is not None:
        return f"{general} {named.title()} rides on {governing.title()}."
    listed = ", ".join(
        f"{name} {attribute}"
        for name, attribute in sorted(checks.governing_attributes.items())
        if attribute is not None
    )
    return f"{general} Governing attributes: {listed}."


def _attribute_check(_narrowing: str = "") -> str:
    """The attribute check with no skill, 3d6 ≤ the attribute (#911).

    Built from the canon's attribute check (``skill-rules.json``, issue #70),
    which :func:`characters.skill_checks.attribute_check` rolls and bounds,
    and the injury table's bands, so the direction and the penalty are stated
    the way the action rolls them.
    """
    check = skill_rules().attribute_check
    limit = check.game_master_modifier_limit
    return (
        f"Attribute check with no skill: roll {check.dice}; it passes when the "
        "total is at or under the attribute's aged score. Injured, the score "
        "the dice must come in under is lowered: "
        f"{signed(MINOR_INJURY_ROLL_PENALTY)} with a pool down to half (but "
        f"above {SEVERE_INJURY_BAND_TOP}), {signed(SEVERE_INJURY_ROLL_PENALTY)} "
        f"with a pool at 1 to {SEVERE_INJURY_BAND_TOP}, the worse pool "
        "governing. The Game Master may state a total "
        f"({check.minimum} to {check.maximum}) rolled at his table, and set a "
        f"modifier ({signed(-limit)} to {signed(limit)}) on the score after any "
        "injury for the situation; a player sets neither. That modifier is not "
        "a survival save's penalty: a dying character's save is survival_save, "
        "which applies its own penalty."
    )


#: Subject to the function that answers it. The keys are what a plan may ask
#: for, and what the schema lists.
RULES: dict[str, Callable[[str], str]] = {
    "survival": _survival,
    "survival_targets": _survival_targets,
    "combat": _combat,
    "skill_check": _skill_check,
    "attribute_check": _attribute_check,
    "aging": _aging,
    "origins": _origins,
    "starting_coin": _starting_coin,
}


def subjects() -> list[str]:
    """Every subject a plan may look up, for the schema and the refusals."""
    return sorted(RULES)


def look_up(subject: str, track: str = "") -> str:
    """The rule filed under ``subject``, as a sentence the turn can hand on.

    Args:
        subject: One of :func:`subjects`.
        track: The track a survival question is about, when it names one;
            for ``skill_check``, the skill.

    Returns:
        The rule, built from the code that enforces it.

    Raises:
        UnknownRule: No rule is filed under that subject, or the track is not
            one the rules define.
    """
    named = subject.strip().casefold().replace(" ", "_").replace("-", "_")
    if named not in RULES:
        raise UnknownRule(
            f"no rule is filed under {subject!r}; the subjects are "
            + ", ".join(subjects())
            + ". A figure about a character -- skill points, XP, age, "
            "Fatigue -- is a record, not a rule: it is in the character "
            "list you were given, and this action does not read it"
        )
    return RULES[named](track)
