"""Invented magic rules, and seating them as the loaded canon's (issue #960).

Every number here is invented; the Tarmar values live only in the private
canon. :func:`seated_rules` patches the one loader every caller reads
through (:func:`battle.magic_rules.magic_rules`), so a turn, its menu, the
seat and the cast stage all see the same rules, under either canon.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import patch

from tarmar_engine.magic import (
    BorrowingFailure,
    BorrowingRules,
    CastingFailureRules,
    ChannelDerivation,
    ChannelRules,
    ControlBand,
    CounterspellRules,
    MagicRules,
    PushRules,
    RollUnderRule,
    RunawayRules,
    SpellEffectKind,
)

from battle.magic_rules import (
    CASTING_DICE,
    SchoolRow,
    SchoolTable,
    load_magic_rules,
    magic_rules,
    magic_schools,
)
from common.rolling import parse_dice_specification
from reference.paths import CANON_MAGIC_RULES_FILE_NAME, DEFAULT_CANON_DIR
from reference.rules_file import forget_values_derived_from_rules

#: The default canon's magic rules: the numbers its magic-mechanics pages
#: state, whichever canon is loaded.
DEFAULT_MAGIC_RULES: MagicRules = load_magic_rules(
    DEFAULT_CANON_DIR / CANON_MAGIC_RULES_FILE_NAME
)

#: The kind of magic every spell belongs to under the invented Channel.
STUDIED_KIND = "studied"

#: Push with a two-mana cap, and a Channel of twice the spell's recorded level.
INVENTED_RULES = MagicRules(
    casting_success_tiers=(),
    push=PushRules(
        control_dice="3d6",
        control_attribute="INT",
        penalty_per_mana=1,
        base_cost_counts_as_invested=False,
        failure_bands=(
            ControlBand(
                key="waver",
                label="Waver",
                is_runaway=False,
                lowest_margin=1,
                highest_margin=2,
                spell_takes_effect=True,
                push_bonus_applies=False,
            ),
            ControlBand(
                key="bolt",
                label="Bolt",
                is_runaway=True,
                lowest_margin=3,
                highest_margin=None,
                spell_takes_effect=False,
                push_bonus_applies=False,
            ),
        ),
        automatic_runaway_totals=frozenset(),
        effect_bonus_per_mana_by_kind={SpellEffectKind.DAMAGE: 1},
        max_push_mana=2,
    ),
    channel=ChannelRules(
        derivations={
            STUDIED_KIND: ChannelDerivation(source_skill=None, multiplier=2, offset=0)
        },
        default_kind=STUDIED_KIND,
    ),
)


#: :data:`INVENTED_RULES` with Borrowing (tarmar-engine #28): a 2d6 debt roll
#: under WIS less two per point owed, a natural 12 failing it whatever the
#: target, a non-Runaway failure, and a cap of three owed.
BORROWING_RULES = replace(
    INVENTED_RULES,
    borrowing=BorrowingRules(
        debt_dice="2d6",
        debt_attributes=("WIS",),
        penalty_per_debt=2,
        failure=BorrowingFailure(
            key="slip", label="Slip of the purse", is_runaway=False
        ),
        automatic_failure_totals=frozenset({12}),
        max_debt=3,
    ),
)


#: :data:`INVENTED_RULES` with Runaway's later turns (tarmar-engine #30): a
#: drain of twice the original cost and 1 more a turn, moving to Body once the
#: mana is gone, death at minus the Body maximum, a 2d8 self anchor against
#: WIS, a 2d10 helper anchor against INT at twice the original cost, a failed
#: helper caught, and a growth of 2 a turn.
RUNAWAY_RULES = replace(
    INVENTED_RULES,
    runaway=RunawayRules(
        drain_multiplier_of_cost=2,
        drain_flat=1,
        overflow_pool="body",
        death_multiplier_of_max=1,
        self_anchor=RollUnderRule(dice="2d8", attribute="WIS"),
        helper_anchor=RollUnderRule(dice="2d10", attribute="INT"),
        helper_cost_multiplier_of_cost=2,
        helper_failure_catches_helper=True,
        effect_bonus_per_turn=2,
    ),
)


#: :data:`INVENTED_RULES` with Counterspell (tarmar-engine #31): both sides
#: roll 2d6 plus INT, +1 per 2 mana invested, ties to the defender, and a
#: defender commits 1 to 3 mana.
COUNTER_RULES = replace(
    INVENTED_RULES,
    counterspell=CounterspellRules(
        opposed_dice="2d6",
        opposed_attribute="INT",
        mana_per_bonus=2,
        ties_go_to_caster=False,
        defender_min_mana=1,
        defender_max_mana=3,
    ),
)


#: Casting failure totals (tarmar-engine #46): a natural 12 or more fails
#: whatever the attribute, a 12 or a 13 spends the mana anyway, and a 13 starts
#: a Runaway. Not the default canon's, so a test seating these proves the
#: seating rather than the loaded canon.
CASTING_FAILURE = CastingFailureRules(
    fumble_floor=12, mana_lost_totals=frozenset({12, 13}), runaway_total=13
)

#: :data:`RUNAWAY_RULES` with :data:`CASTING_FAILURE`, so the Runaway a cast
#: starts has its later turns.
CASTING_FAILURE_RULES = replace(RUNAWAY_RULES, casting_failure=CASTING_FAILURE)


def rules_with_channel(
    *, multiplier: int = 2, source_skills: tuple[str, ...] = ()
) -> MagicRules:
    """:data:`INVENTED_RULES` with another Channel.

    Args:
        multiplier: Channel per recorded spell level, for every spell.
        source_skills: General skills a further kind of magic each reads.
    """
    derivations = {
        STUDIED_KIND: ChannelDerivation(
            source_skill=None, multiplier=multiplier, offset=0
        )
    }
    for skill in source_skills:
        derivations[f"read from {skill}"] = ChannelDerivation(
            source_skill=skill, multiplier=1, offset=0
        )
    return replace(
        INVENTED_RULES,
        channel=ChannelRules(derivations=derivations, default_kind=STUDIED_KIND),
    )


def casting_faces(natural_total: int) -> list[int]:
    """Faces of the casting roll's dice summing to ``natural_total``.

    Lets a test throw a total it read from the loaded canon's rules without
    writing that total down.

    Args:
        natural_total: The sum wanted.

    Returns:
        One face per die, highest first, summing to ``natural_total``.

    Raises:
        ValueError: No faces of the casting dice sum to it.
    """
    dice, sides, _ = parse_dice_specification(CASTING_DICE)
    if not dice <= natural_total <= dice * sides:
        raise ValueError(f"no {dice}d{sides} faces sum to {natural_total}")
    faces: list[int] = []
    remaining = natural_total
    for dice_left in range(dice, 0, -1):
        face = min(sides, remaining - (dice_left - 1))
        faces.append(face)
        remaining -= face
    return faces


@contextmanager
def seated_rules(rules: MagicRules = INVENTED_RULES) -> Iterator[None]:
    """Seat ``rules`` as the loaded canon's for every caller, then forget them.

    Every value built from the loaded rules is forgotten too, before and
    after: the Games Master's magic section is built from them once and held
    for the process (``gm.engine.section_magic``, issue #48), so a seated test
    reads it built from the seated rules, and no later test does.

    Args:
        rules: The rules :func:`battle.magic_rules.magic_rules` answers with.
    """
    magic_rules.cache_clear()
    forget_values_derived_from_rules()
    try:
        with patch("battle.magic_rules.load_magic_rules", return_value=rules):
            yield
    finally:
        magic_rules.cache_clear()
        forget_values_derived_from_rules()


#: An invented school table (issue #18): every school the engine files a
#: spell under, a row naming one school outright, and a school with no spell.
INVENTED_SCHOOLS = SchoolTable(
    rows=(
        SchoolRow(up_to=40, label="Usual", schools=("Elemental", "Mending")),
        SchoolRow(up_to=60, label="Guarded", schools=("Protection",)),
        SchoolRow(up_to=100, label="Rarer", schools=("Passage", "Control", "Sky")),
    )
)


@contextmanager
def seated_schools(table: SchoolTable | None = INVENTED_SCHOOLS) -> Iterator[None]:
    """Seat ``table`` as the loaded canon's school table, then forget it.

    Args:
        table: What :func:`battle.magic_rules.magic_schools` answers with;
            ``None`` for a canon that gates no spell by school.
    """
    magic_schools.cache_clear()
    try:
        with patch("battle.magic_rules.load_magic_schools", return_value=table):
            yield
    finally:
        magic_schools.cache_clear()
