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
    ChannelDerivation,
    ChannelRules,
    ControlBand,
    MagicRules,
    PushRules,
    SpellEffectKind,
)

from battle.magic_rules import magic_rules

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


@contextmanager
def seated_rules(rules: MagicRules = INVENTED_RULES) -> Iterator[None]:
    """Seat ``rules`` as the loaded canon's for every caller, then forget them.

    Args:
        rules: The rules :func:`battle.magic_rules.magic_rules` answers with.
    """
    magic_rules.cache_clear()
    try:
        with patch("battle.magic_rules.load_magic_rules", return_value=rules):
            yield
    finally:
        magic_rules.cache_clear()
