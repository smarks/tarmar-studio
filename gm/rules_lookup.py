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
from characters.constants import CORE_ATTRIBUTE_FIELDS
from characters.dice import (
    LOCATION_LITERACY_MODIFIER,
    LOCATION_SKILL_CHOICES,
    STARTING_COIN,
    WEALTH_TABLE,
)
from characters.skills import (
    AGING_TABLE,
    SURVIVAL_AUTO_DEATH_ROLL,
    SURVIVAL_AUTO_SURVIVE_ROLL,
    SURVIVAL_DICE_COUNT,
    SURVIVAL_RULE_DESCRIPTION,
    SURVIVAL_TARGETS,
    age_for_aging_stage,
    normalize_skill_name,
)

#: The longest a subject may be, so a plan cannot smuggle prose in here.
MAX_SUBJECT_CHARS = 60


class UnknownRule(LookupError):
    """No rule is filed under that subject; ``str()`` lists what is."""


def targets_written() -> str:
    """Every track's survivability target, in one line."""
    return ", ".join(
        f"{name} {target}" for name, target in sorted(SURVIVAL_TARGETS.items())
    )


def _survival(track: str = "") -> str:
    """The prior-experience survival check, for one track or in general."""
    general = (
        f"Prior-experience survival: {SURVIVAL_RULE_DESCRIPTION} "
        f"A natural {SURVIVAL_AUTO_SURVIVE_ROLL} is never a death and a "
        f"natural {SURVIVAL_AUTO_DEATH_ROLL} is never a survival."
    )
    if not track:
        return f"{general} Survivability targets: {targets_written()}."
    named = track.strip().casefold().replace(" ", "_")
    if named not in SURVIVAL_TARGETS:
        # A lookup always answers. The model phrases the narrowing field
        # however the question came out -- "base TN Piercing vs None" arrived
        # in it once -- and refusing left the GM narrating an apology at the
        # player instead of the rule (issue #605). The general answer carries
        # every target, so it answers the question regardless.
        return f"{general} Survivability targets: {targets_written()}."
    return (
        f"{general} The {named} track's survivability target is "
        f"{SURVIVAL_TARGETS[named]}, so on {SURVIVAL_DICE_COUNT}d6 plus the "
        f"best attribute modifier the character survives on "
        f"{SURVIVAL_TARGETS[named]} or more."
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
    """The aging schedule, from :data:`characters.skills.AGING_TABLE` (#807).

    Only the ages where a total changes, each with the cumulative totals it
    carries, as aging.md's table gives them. A lookup rather than a line of
    the prompt: stated in the creation section, the tables drew the model
    into recording backgrounds nobody had stated (the 2026-09-29 board).
    """
    labels = {
        name: short for name, short in CORE_ATTRIBUTE_FIELDS if name != "charisma"
    }
    steps: list[str] = []
    before: dict[str, int] | None = None
    for stage in sorted(AGING_TABLE):
        totals = AGING_TABLE[stage]
        if totals != before and any(totals.values()):
            said = ", ".join(
                f"{labels[name]} {signed(value)}"
                for name, value in totals.items()
                if value
            )
            steps.append(f"{age_for_aging_stage(stage)} {said}")
        before = totals
    return (
        "Aging, keyed to age from 16, as cumulative lifetime totals, each "
        "replacing the last: "
        + "; ".join(steps)
        + "; after that STR, DEX and CON lose 1 more every 4 years and INT and "
        "WIS every 8. An aged score never goes below 0; play uses it, and only "
        "the literacy check and the weight from STR use the base score."
    )


def _origins(_narrowing: str = "") -> str:
    """What each Origin gives, from ``characters.dice`` (#807)."""
    rows = []
    for origin, skills in LOCATION_SKILL_CHOICES.items():
        modifier = LOCATION_LITERACY_MODIFIER[origin]
        check = "3d6 ≤ INT" + (f" − {modifier}" if modifier else "")
        rows.append(f"{origin}: {' or '.join(skills)}, literacy check {check}")
    return (
        "Origins: "
        + "; ".join(rows)
        + "; Consult GM: the Game Master decides what it gives and whether the "
        "character can read. The literacy check decides whether a character "
        "can read at all; the Literacy skill is only how well."
    )


def _starting_coin(_narrowing: str = "") -> str:
    """Starting coin by wealth level, from ``characters.dice`` (#807)."""
    rows = []
    for _threshold, level in WEALTH_TABLE:
        coin = STARTING_COIN.get(level)
        if coin is not None:
            rows.append(f"{level} {coin}")
        elif level == "Merchant":
            rows.append(f"{level} 100 + percentile dice")
        else:
            rows.append(f"{level}: the Game Master sets it")
    return "Starting coin, rolled with the wealth level, once: " + "; ".join(rows) + "."


def _skill_check(skill: str = "") -> str:
    """The skills glossary's check, and which attribute a skill rides on (#843).

    Built from :mod:`characters.skill_checks`, which rolls the check and whose
    Governs table is held to the glossary by a drift test. ``skill`` names
    one skill; unnamed or unknown, every governing attribute is given.
    """
    # Imported here: gm.engine reads this module's subjects for its prompt,
    # and must import without Django (the offline CLI), which the check's
    # module needs for the character model.
    from characters.skill_checks import (
        GOVERNING_ATTRIBUTES,
        NATURAL_FAILURE,
        NATURAL_SUCCESS,
        governing_attribute,
    )

    general = (
        "Skill check: roll 2d6, add the governing attribute's modifier (from "
        "its aged score) and the skill level, against a difficulty the Game "
        f"Master sets. A natural {NATURAL_SUCCESS} always succeeds and a "
        f"natural {NATURAL_FAILURE} always fails; untrained, the modifier "
        "alone. Luck is not rolled as a check."
    )
    named = normalize_skill_name(skill) if skill.strip() else ""
    governing = governing_attribute(named) if named else None
    if governing is not None:
        return f"{general} {named.title()} rides on {governing.title()}."
    listed = ", ".join(
        f"{name} {attribute}"
        for name, attribute in sorted(GOVERNING_ATTRIBUTES.items())
        if attribute is not None
    )
    return f"{general} Governing attributes: {listed}."


#: Subject to the function that answers it. The keys are what a plan may ask
#: for, and what the schema lists.
RULES: dict[str, Callable[[str], str]] = {
    "survival": _survival,
    "survival_targets": _survival_targets,
    "combat": _combat,
    "skill_check": _skill_check,
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
