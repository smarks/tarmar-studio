"""The default canon's magic pages state the numbers in its magic-rules.json.

``reference/default_canon/magic-mechanics/`` settles seven mechanics for the
battle board in hand-written sentences, and
``reference/default_canon/magic-rules.json`` holds the same numbers as data
(issues #42 and #47):

- ``borrowing.md``: the Debt cap, the Debt Roll's dice, attribute, penalty and
  automatic failure, the failure's name, and that mana handed back pays the
  Debt first (the ``borrowing`` block);
- ``runaway.md``: the later-turn drain, where the rest comes off, the death
  line, both Anchor Rolls, what a helper pays and risks, and the effect bonus
  per turn (the ``runaway`` block);
- ``mana-pool.md``: the casting roll's failing total, the totals that spend
  the mana anyway, and the total that starts a Runaway (the
  ``casting_failure`` block);
- ``counterspell.md``: what a defender may commit, the opposed roll, the bonus
  per mana and who wins a tie (the ``counterspell`` block);
- ``push.md``: the Control Roll and the total that always starts a Runaway
  (the ``push`` block);
- ``channel.md``: one kind of magic for every spell, its Channel per level held
  in the spell and the amount added, and the Channel of a caster who holds no
  level; and the second kind a True cast or a working reads (tarmar-studio
  #208), its skill, its Channel per level and the amount added (the
  ``channel`` block);
- ``counterspell.md`` also: the bonus per level of familiarity
  (tarmar-studio #208);
- ``true-magic.md``: the tier cap, the craft turns, each working's effect,
  the interruption roll and its bands, the working's roll, and the drain of
  a casting that goes off and of one that does not (the ``true_magic`` block,
  tarmar-studio #208).

They have no shared source: the rules-as-data prose of issue #68 builds the
sheet's and the Games Master prompt's sentences from the loaded canon, but
these pages are plain markdown files that nothing generates. Editing the file
without the page, or the page without the file, would leave a rule printed one
way and played another. These tests parse each sentence's numbers and compare
them with the default canon's loaded block, whichever canon the server loads.

Each ``*_claims_of_page`` / ``*_claims_of_rules`` pair builds the same
dictionary from the two sides, so a mismatch names the claim that differs. A
sentence the parser cannot find fails with its page and pattern, rather than
passing for having nothing to compare.
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from django.test import SimpleTestCase
from tarmar_engine.magic import (
    UNRECORDED_SKILL_LEVEL,
    BorrowingRules,
    CastingEnding,
    CastingFailureRules,
    ChannelRules,
    CounterspellRules,
    PushRules,
    RunawayRules,
    SpellEffectKind,
    TrueMagicRules,
)

from battle.magic_rules import CASTING_DICE
from characters.constants import CORE_ATTRIBUTE_FIELDS
from reference.paths import DEFAULT_CANON_DIR

from .magic_fixtures import DEFAULT_MAGIC_RULES

MAGIC_MECHANICS_DIR = DEFAULT_CANON_DIR / "magic-mechanics"
BORROWING_PAGE = MAGIC_MECHANICS_DIR / "borrowing.md"
RUNAWAY_PAGE = MAGIC_MECHANICS_DIR / "runaway.md"
MANA_POOL_PAGE = MAGIC_MECHANICS_DIR / "mana-pool.md"
COUNTERSPELL_PAGE = MAGIC_MECHANICS_DIR / "counterspell.md"
PUSH_PAGE = MAGIC_MECHANICS_DIR / "push.md"
CHANNEL_PAGE = MAGIC_MECHANICS_DIR / "channel.md"
TRUE_MAGIC_PAGE = MAGIC_MECHANICS_DIR / "true-magic.md"

#: Attribute abbreviations (as ``magic-rules.json`` spells them) to the name
#: the pages print.
ATTRIBUTE_NAMES = {
    abbreviation: field_name.capitalize()
    for field_name, abbreviation in CORE_ATTRIBUTE_FIELDS
}

#: How the pages word "this many times the original cost".
MULTIPLIER_WORDS = {"its": 1, "twice": 2, "three times": 3}

#: The engine's Counterspell gains one point per ``mana_per_bonus`` mana
#: (``CounterspellRules.bonus_for``); the pages print that point as "+1".
ENGINE_COUNTERSPELL_BONUS_STEP = 1

#: A markdown link, kept as its text.
MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")

#: The sentences the pages state, as patterns over the page with its bold
#: markers and link targets removed. Each group is one claim.
BORROWING_CAP = r"up to (\d+) owed in all"
DEBT_ROLL = (
    r"(\d+d\d+) against (\w+), less (\d+) for each point owed;"
    r" a (\d+) always fails"
)
DEBT_AUTOMATIC_BAND = r"always fails, whatever the margin, as an? (\w+)"
DEBT_BAND = (
    r"by (\d+)(?: (?:or|to) (\d+)| or more), an? (\w+),"
    r" costing (?:nothing|(\d+) (\w+) for each point owed)"
    r"( but starting a Runaway)?"
)
REFUNDS_PAY_DEBT_FIRST = "pays the Debt before it refills the pool"

RUNAWAY_DRAIN = r"drains (its|twice|three times) original casting cost plus (\d+)"
RUNAWAY_OVERFLOW = r"the rest comes off (\w+); at (\d+) (\w+) the caster dies"
OPENING_ANCHOR = r"Anchor Roll \((\d+d\d+) against (\w+)\)"
SELF_ANCHOR = r"Anchor Roll, (\d+d\d+) against (\w+) less"
HELPER_ANCHOR = (
    r"they pay (twice|three times) the original cost in mana whatever happens"
    r" and roll (\d+d\d+) against (\w+)"
)
HELPER_FAILURE = "if that fails they are caught in a Runaway of the same spell"
RUNAWAY_GROWTH = r"adds \+(\d+) per turn it ran"

CASTING_FLOOR = r"a natural (\d+) or more fails the spell"
CASTING_MANA_LOST = (
    r"except on a (\d+) or a (\d+), which spend it anyway;"
    r" a (\d+) also starts a Runaway"
)

COUNTERSPELL_COMMITMENT = r"committing (\d+) to (\d+) mana"
COUNTERSPELL_ROLL = r"both sides roll (\d+d\d+) plus (\w+):"
COUNTERSPELL_CASTER_BONUS = r"the caster adds \+(\d+) for every (\d+) mana"
COUNTERSPELL_DEFENDER_BONUS = r"the defender \+(\d+) for every (\d+) mana committed"
COUNTERSPELL_TIE = r"a tie goes to the (defender|caster)"
COUNTERSPELL_FAMILIARITY = r"each side adds \+(\d+) for each level of familiarity"

PUSH_CONTROL_ROLL = r"Control Roll \((\d+d\d+) against (\w+)\)"
PUSH_AUTOMATIC_RUNAWAY = r"or roll an? (\d+), and the spell slips"

CHANNEL_ONE_KIND = "every spell is of one kind of magic"
CHANNEL_DERIVATION = (
    r"Channel for a spell is (\d+) for each level they hold in (that spell|\w+),"
    r" plus (\d+)\."
)
#: How ``channel.md`` names the level a Channel reads when it is the spell's own.
CHANNEL_SPELL_LEVEL = "that spell"
CHANNEL_WITH_NO_LEVEL = r"holds no level in the spell still has a Channel of (\d+)"
#: The kind of magic a True cast or a working reads: its Channel per level,
#: the general skill the level is held in, and the amount added.
CHANNEL_SKILL_KIND = (
    r"a second kind of magic instead, read from the caster's (\w+):"
    r" a Channel of (\d+) for each level they hold in the (\w+) skill,"
    r" (?:with nothing added|plus (\d+)),"
)


TRUE_TIER_CAP = (
    r"A working's tier is at most (\d+) for each level the caster holds in (\w+),"
)
TRUE_CRAFT_TURNS = r"Its craft takes (\d+) turns? for each tier"
TRUE_WORKINGS = r"The workings are: ([^.]*)\."
TRUE_ROLLED_EFFECT = (
    r"^(\w+), (\d+d\d+(?:\+\d+)?) (damage|healing) to (\w+) per tier(.*)$"
)
TRUE_NARRATED_EFFECT = r"^(\w+), a narrated effect with no dice(.*)$"
TRUE_UNTARGETED = "needs no Dexterity roll to hit"
TRUE_IGNORES_ARMOUR = "ignores armour"
TRUE_NEEDS_GRANT = "crafted only at a tier the Game Master has granted"
TRUE_INTERRUPTION = (
    r"interruption roll of (\d+d\d+) against (\w+) less (\d+) for each mana gathered"
)
TRUE_BANDS = r"Failing it loses the working: ([^.]*)\."
TRUE_BAND = r"by (\d+) (?:to (\d+)|or more) it is an? (\w+)(, a Runaway)?"
TRUE_AUTOMATIC = r"a natural (\d+) is always an? (\w+)"
TRUE_WORKING_ROLL = (
    r"casting roll of (\d+d\d+) against (\w+) plus (\d+) for each (\w+) level"
)
TRUE_DRAIN = (
    r"A working that goes off drains (\d+) (\w+) for each point of its mana; a "
    r"True cast drains (\d+) (\w+) for each point pushed"
)
TRUE_LOST_DRAIN = (
    r"(\d+) (\w+) a point when a hit interrupts it, (\d+) (\w+) a point when it "
    r"is countered, and nothing any other way"
)


class Page(NamedTuple):
    """A canon page as the parser reads it."""

    name: str
    prose: str


def read_page(path: Path) -> Page:
    """The page with its bold markers and link targets dropped, lines joined."""
    text = path.read_text(encoding="utf-8").replace("**", "")
    return Page(path.name, " ".join(MARKDOWN_LINK.sub(r"\1", text).split()))


def search(pattern: str, page: Page) -> re.Match[str]:
    """The match for a sentence the page must state.

    Raises:
        AssertionError: The page no longer states it in the form the test
            parses, so the sentence needs the test updated with it.
    """
    match = re.search(pattern, page.prose)
    assert match is not None, f"{page.name} no longer states: {pattern}"
    return match


def states(phrase: str, page: Page, *, expected: bool) -> bool:
    """Whether the page states ``phrase``, for a claim the file makes a flag.

    A page that must state the phrase (``expected``) and does not fails with
    the page and phrase named; when the file says the flag is off, the phrase
    is simply reported present or absent.
    """
    if expected:
        search(re.escape(phrase), page)
        return True
    return phrase in page.prose


def debt_bands_of_page(page: Page) -> list[tuple[object, ...]]:
    """The Debt Roll's failure bands as a ``borrowing.md`` page states them:
    each band's margins, label, cost per point owed and its pool (both
    ``None`` for a band costing nothing), and whether it starts a Runaway.
    """
    search(DEBT_BAND, page)
    return [
        (
            int(low),
            int(high) if high else None,
            label,
            int(cost) if cost else None,
            pool or None,
            bool(runaway),
        )
        for low, high, label, cost, pool, runaway in re.findall(DEBT_BAND, page.prose)
    ]


def borrowing_claims_of_page(page: Page, rules: BorrowingRules) -> dict[str, object]:
    """What a ``borrowing.md`` page says about the Debt Roll.

    ``rules`` only decides whether a flag's phrase must be present.
    """
    debt_roll = search(DEBT_ROLL, page)
    return {
        "max_debt": int(search(BORROWING_CAP, page).group(1)),
        "debt_dice": debt_roll.group(1),
        "debt_attribute": debt_roll.group(2),
        "penalty_per_debt": int(debt_roll.group(3)),
        "automatic_failure_total": int(debt_roll.group(4)),
        "automatic_band": search(DEBT_AUTOMATIC_BAND, page).group(1),
        "failure_bands": debt_bands_of_page(page),
        "refunds_pay_debt_first": states(
            REFUNDS_PAY_DEBT_FIRST, page, expected=rules.refunds_pay_debt_first
        ),
    }


def borrowing_claims_of_rules(rules: BorrowingRules) -> dict[str, object]:
    """The same claims, from the loaded ``borrowing`` block."""
    (attribute,) = rules.debt_attributes
    (automatic_failure_total,) = rules.automatic_failure_totals
    return {
        "max_debt": rules.max_debt,
        "debt_dice": rules.debt_dice,
        "debt_attribute": ATTRIBUTE_NAMES[attribute],
        "penalty_per_debt": rules.penalty_per_debt,
        "automatic_failure_total": automatic_failure_total,
        "automatic_band": rules.automatic_band().label,
        "failure_bands": [
            (
                band.lowest_margin,
                band.highest_margin,
                band.label,
                band.cost_per_debt or None,
                band.cost_pool.capitalize() if band.cost_pool else None,
                band.is_runaway,
            )
            for band in rules.failure_bands
        ],
        "refunds_pay_debt_first": rules.refunds_pay_debt_first,
    }


def runaway_claims_of_page(page: Page, rules: RunawayRules) -> dict[str, object]:
    """What a ``runaway.md`` page says about the later turns and the anchors.

    ``rules`` only decides whether a flag's phrase must be present.
    """
    drain = search(RUNAWAY_DRAIN, page)
    overflow = search(RUNAWAY_OVERFLOW, page)
    opening_anchor = search(OPENING_ANCHOR, page)
    self_anchor = search(SELF_ANCHOR, page)
    helper = search(HELPER_ANCHOR, page)
    return {
        "drain_multiplier_of_cost": MULTIPLIER_WORDS[drain.group(1)],
        "drain_flat": int(drain.group(2)),
        "overflow_pool": overflow.group(1),
        "death_multiplier_of_max": int(overflow.group(2)),
        "death_pool": overflow.group(3),
        "opening_anchor_dice": opening_anchor.group(1),
        "opening_anchor_attribute": opening_anchor.group(2),
        "self_anchor_dice": self_anchor.group(1),
        "self_anchor_attribute": self_anchor.group(2),
        "helper_cost_multiplier_of_cost": MULTIPLIER_WORDS[helper.group(1)],
        "helper_anchor_dice": helper.group(2),
        "helper_anchor_attribute": helper.group(3),
        "helper_failure_catches_helper": states(
            HELPER_FAILURE, page, expected=rules.helper_failure_catches_helper
        ),
        "effect_bonus_per_turn": int(search(RUNAWAY_GROWTH, page).group(1)),
    }


def runaway_claims_of_rules(rules: RunawayRules) -> dict[str, object]:
    """The same claims, from the loaded ``runaway`` block."""
    return {
        "drain_multiplier_of_cost": rules.drain_multiplier_of_cost,
        "drain_flat": rules.drain_flat,
        "overflow_pool": rules.overflow_pool.capitalize(),
        "death_multiplier_of_max": rules.death_multiplier_of_max,
        "death_pool": rules.overflow_pool.capitalize(),
        "opening_anchor_dice": rules.self_anchor.dice,
        "opening_anchor_attribute": ATTRIBUTE_NAMES[rules.self_anchor.attribute],
        "self_anchor_dice": rules.self_anchor.dice,
        "self_anchor_attribute": ATTRIBUTE_NAMES[rules.self_anchor.attribute],
        "helper_cost_multiplier_of_cost": rules.helper_cost_multiplier_of_cost,
        "helper_anchor_dice": rules.helper_anchor.dice,
        "helper_anchor_attribute": ATTRIBUTE_NAMES[rules.helper_anchor.attribute],
        "helper_failure_catches_helper": rules.helper_failure_catches_helper,
        "effect_bonus_per_turn": rules.effect_bonus_per_turn,
    }


def casting_failure_claims_of_page(
    page: Page, rules: CastingFailureRules
) -> dict[str, object]:
    """What a ``mana-pool.md`` page says about a casting roll's high totals."""
    mana_lost = search(CASTING_MANA_LOST, page)
    return {
        "fumble_floor": int(search(CASTING_FLOOR, page).group(1)),
        "mana_lost_totals": [int(mana_lost.group(1)), int(mana_lost.group(2))],
        "runaway_total": int(mana_lost.group(3)),
    }


def casting_failure_claims_of_rules(rules: CastingFailureRules) -> dict[str, object]:
    """The same claims, from the loaded ``casting_failure`` block."""
    return {
        "fumble_floor": rules.fumble_floor,
        "mana_lost_totals": sorted(rules.mana_lost_totals),
        "runaway_total": rules.runaway_total,
    }


def counterspell_claims_of_page(
    page: Page, rules: CounterspellRules
) -> dict[str, object]:
    """What a ``counterspell.md`` page says about the duel's numbers."""
    commitment = search(COUNTERSPELL_COMMITMENT, page)
    roll = search(COUNTERSPELL_ROLL, page)
    caster_bonus = search(COUNTERSPELL_CASTER_BONUS, page)
    defender_bonus = search(COUNTERSPELL_DEFENDER_BONUS, page)
    return {
        "defender_min_mana": int(commitment.group(1)),
        "defender_max_mana": int(commitment.group(2)),
        "opposed_dice": roll.group(1),
        "opposed_attribute": roll.group(2),
        "caster_bonus_step": int(caster_bonus.group(1)),
        "caster_mana_per_bonus": int(caster_bonus.group(2)),
        "defender_bonus_step": int(defender_bonus.group(1)),
        "defender_mana_per_bonus": int(defender_bonus.group(2)),
        "ties_go_to_caster": search(COUNTERSPELL_TIE, page).group(1) == "caster",
        "familiarity_per_level": int(search(COUNTERSPELL_FAMILIARITY, page).group(1)),
    }


def counterspell_claims_of_rules(rules: CounterspellRules) -> dict[str, object]:
    """The same claims, from the loaded ``counterspell`` block."""
    return {
        "defender_min_mana": rules.defender_min_mana,
        "defender_max_mana": rules.defender_max_mana,
        "opposed_dice": rules.opposed_dice,
        "opposed_attribute": ATTRIBUTE_NAMES[rules.opposed_attribute],
        "caster_bonus_step": ENGINE_COUNTERSPELL_BONUS_STEP,
        "caster_mana_per_bonus": rules.mana_per_bonus,
        "defender_bonus_step": ENGINE_COUNTERSPELL_BONUS_STEP,
        "defender_mana_per_bonus": rules.mana_per_bonus,
        "ties_go_to_caster": rules.ties_go_to_caster,
        "familiarity_per_level": rules.familiarity_per_level,
    }


def push_claims_of_page(page: Page, rules: PushRules) -> dict[str, object]:
    """What a ``push.md`` page says about the Control Roll."""
    control_roll = search(PUSH_CONTROL_ROLL, page)
    return {
        "control_dice": control_roll.group(1),
        "control_attribute": control_roll.group(2),
        "automatic_runaway_totals": [
            int(search(PUSH_AUTOMATIC_RUNAWAY, page).group(1))
        ],
    }


def push_claims_of_rules(rules: PushRules) -> dict[str, object]:
    """The same claims, from the loaded ``push`` block."""
    return {
        "control_dice": rules.control_dice,
        "control_attribute": ATTRIBUTE_NAMES[rules.control_attribute],
        "automatic_runaway_totals": sorted(rules.automatic_runaway_totals),
    }


def channel_claims_of_page(page: Page, rules: ChannelRules) -> dict[str, object]:
    """What a ``channel.md`` page says about how Channel derives.

    The page words one kind of magic for every spell and a second, read from
    a general skill, for a True cast or a working (tarmar-studio #208), so a
    block that adds a further kind or sorts a spell into one needs the page
    and this parser rewritten with it.
    """
    derivation = search(CHANNEL_DERIVATION, page)
    skill_kind = search(CHANNEL_SKILL_KIND, page)
    return {
        "one_kind_for_every_spell": states(CHANNEL_ONE_KIND, page, expected=True),
        "reads_the_spell_level": derivation.group(2) == CHANNEL_SPELL_LEVEL,
        "multiplier": int(derivation.group(1)),
        "offset": int(derivation.group(3)),
        "channel_with_no_level": int(search(CHANNEL_WITH_NO_LEVEL, page).group(1)),
        "skill_kind_skills": [
            skill_kind.group(1).casefold(),
            skill_kind.group(3).casefold(),
        ],
        "skill_kind_multiplier": int(skill_kind.group(2)),
        "skill_kind_offset": int(skill_kind.group(4) or 0),
    }


def channel_claims_of_rules(rules: ChannelRules) -> dict[str, object]:
    """The same claims, from the loaded ``channel`` block: the default kind is
    every spell's, and the one other kind is the one read from a skill."""
    spell_kind = rules.default_kind
    (skill_kind,) = (kind for kind in rules.derivations if kind != spell_kind)
    assert spell_kind is not None, "the default canon gives every spell one kind"
    derivation = rules.derivations[spell_kind]
    skill_derivation = rules.derivations[skill_kind]
    return {
        "one_kind_for_every_spell": not rules.kind_by_spell,
        "reads_the_spell_level": derivation.source_skill is None,
        "multiplier": derivation.multiplier,
        "offset": derivation.offset,
        "channel_with_no_level": derivation.channel_at(UNRECORDED_SKILL_LEVEL),
        # The page names the skill twice, and both must be the block's.
        "skill_kind_skills": [skill_derivation.source_skill] * 2,
        "skill_kind_multiplier": skill_derivation.multiplier,
        "skill_kind_offset": skill_derivation.offset,
    }


def working_effects_of_page(page: Page) -> list[tuple[object, ...]]:
    """Each working the page lists: its label, dice per tier, kind, pool, and
    for damage whether it must be aimed and whether armour stops it, and
    whether it needs a Game Master's grant.

    The page lists rolled effects and narrated ones with no dice, the two this
    canon has; a narrated effect with dice needs the page and this parser
    rewritten with it.
    """
    effects: list[tuple[object, ...]] = []
    for item in search(TRUE_WORKINGS, page).group(1).split("; "):
        rolled = re.match(TRUE_ROLLED_EFFECT, item)
        if rolled is not None:
            label, dice, kind, pool, rest = rolled.groups()
            damage = kind == "damage"
            effects.append(
                (
                    label,
                    dice,
                    kind,
                    pool.casefold(),
                    TRUE_UNTARGETED not in rest if damage else None,
                    TRUE_IGNORES_ARMOUR in rest if damage else None,
                    TRUE_NEEDS_GRANT in rest,
                )
            )
            continue
        narrated = re.match(TRUE_NARRATED_EFFECT, item)
        assert narrated is not None, f"{page.name} lists a working as: {item}"
        label, rest = narrated.groups()
        effects.append((label, None, None, None, None, None, TRUE_NEEDS_GRANT in rest))
    return effects


def true_magic_claims_of_page(page: Page, rules: TrueMagicRules) -> dict[str, object]:
    """What a ``true-magic.md`` page says about workings and their drain."""
    tier_cap = search(TRUE_TIER_CAP, page)
    interruption = search(TRUE_INTERRUPTION, page)
    automatic = search(TRUE_AUTOMATIC, page)
    working_roll = search(TRUE_WORKING_ROLL, page)
    drain = search(TRUE_DRAIN, page)
    lost_drain = search(TRUE_LOST_DRAIN, page)
    return {
        "tiers_per_level": int(tier_cap.group(1)),
        "skills": [
            tier_cap.group(2).casefold(),
            working_roll.group(4).casefold(),
        ],
        "turns_per_tier": int(search(TRUE_CRAFT_TURNS, page).group(1)),
        "effects": working_effects_of_page(page),
        "interruption_dice": interruption.group(1),
        "interruption_attribute": interruption.group(2),
        "interruption_penalty_per_mana": int(interruption.group(3)),
        "interruption_bands": [
            (
                int(lowest),
                int(highest) if highest else None,
                label,
                bool(runaway),
            )
            for lowest, highest, label, runaway in re.findall(
                TRUE_BAND, search(TRUE_BANDS, page).group(1)
            )
        ],
        "automatic_runaway": (int(automatic.group(1)), automatic.group(2)),
        "working_roll_dice": working_roll.group(1),
        "working_roll_attribute": working_roll.group(2),
        "working_roll_bonus_per_level": int(working_roll.group(3)),
        "drain": [
            (int(drain.group(1)), drain.group(2).casefold()),
            (int(drain.group(3)), drain.group(4).casefold()),
        ],
        "lost_drain": [
            (int(lost_drain.group(1)), lost_drain.group(2).casefold()),
            (int(lost_drain.group(3)), lost_drain.group(4).casefold()),
        ],
    }


def true_magic_claims_of_rules(
    rules: TrueMagicRules, true_skill: str
) -> dict[str, object]:
    """The same claims, from the loaded ``true_magic`` block.

    Args:
        rules: The block.
        true_skill: The skill its Channel kind reads (``channel``'s
            derivation for ``rules.channel_kind``), which the page names
            twice.
    """
    interruption = rules.interruption
    runaway_bands = [band for band in interruption.failure_bands if band.is_runaway]
    (automatic_total,) = interruption.automatic_runaway_totals
    return {
        "tiers_per_level": rules.tiers_per_level,
        "skills": [true_skill] * 2,
        "turns_per_tier": rules.turns_per_tier,
        "effects": [
            (
                effect.label,
                effect.dice_per_tier,
                effect.kind.value if effect.kind else None,
                effect.pool if effect.kind else None,
                effect.targeted if effect.kind is SpellEffectKind.DAMAGE else None,
                effect.ignores_armour
                if effect.kind is SpellEffectKind.DAMAGE
                else None,
                effect.needs_grant,
            )
            for effect in rules.effects
        ],
        "interruption_dice": interruption.roll.dice,
        "interruption_attribute": ATTRIBUTE_NAMES[interruption.roll.attribute],
        "interruption_penalty_per_mana": interruption.penalty_per_mana,
        "interruption_bands": [
            (band.lowest_margin, band.highest_margin, band.label, band.is_runaway)
            for band in interruption.failure_bands
        ],
        "automatic_runaway": (automatic_total, runaway_bands[0].label),
        "working_roll_dice": CASTING_DICE,
        "working_roll_attribute": ATTRIBUTE_NAMES[rules.roll_attribute],
        "working_roll_bonus_per_level": rules.roll_bonus_per_level,
        "drain": [(rules.drain_per_mana, rules.drain_pool)] * 2,
        "lost_drain": [
            (rules.lost_drain_per_mana.get(ending, 0), rules.drain_pool)
            for ending in (CastingEnding.INTERRUPTED, CastingEnding.COUNTERED)
        ],
    }


def default_true_magic() -> tuple[TrueMagicRules, str]:
    """The default canon's ``true_magic`` block and the skill it reads."""
    rules = DEFAULT_MAGIC_RULES.true_magic
    channel = DEFAULT_MAGIC_RULES.channel
    assert rules is not None, "the default canon offers True Magic"
    assert channel is not None, "True Magic needs Channel"
    true_skill = channel.derivations[rules.channel_kind].source_skill
    assert true_skill is not None, "True Magic's Channel reads a skill"
    return rules, true_skill


class MagicPagesFollowTheRulesFileTest(SimpleTestCase):
    """Each hand-written paragraph states what the default canon's file holds."""

    def test_borrowing_page_matches_the_borrowing_block(self) -> None:
        rules = DEFAULT_MAGIC_RULES.borrowing
        assert rules is not None, "the default canon offers Borrowing"
        self.assertEqual(
            borrowing_claims_of_page(read_page(BORROWING_PAGE), rules),
            borrowing_claims_of_rules(rules),
        )

    def test_runaway_page_matches_the_runaway_block(self) -> None:
        rules = DEFAULT_MAGIC_RULES.runaway
        assert rules is not None, "the default canon runs a Runaway"
        self.assertEqual(
            runaway_claims_of_page(read_page(RUNAWAY_PAGE), rules),
            runaway_claims_of_rules(rules),
        )

    def test_mana_pool_page_matches_the_casting_failure_block(self) -> None:
        rules = DEFAULT_MAGIC_RULES.casting_failure
        assert rules is not None, (
            "the default canon fails a casting roll on high totals"
        )
        self.assertEqual(
            casting_failure_claims_of_page(read_page(MANA_POOL_PAGE), rules),
            casting_failure_claims_of_rules(rules),
        )

    def test_counterspell_page_matches_the_counterspell_block(self) -> None:
        rules = DEFAULT_MAGIC_RULES.counterspell
        assert rules is not None, "the default canon offers Counterspell"
        self.assertEqual(
            counterspell_claims_of_page(read_page(COUNTERSPELL_PAGE), rules),
            counterspell_claims_of_rules(rules),
        )

    def test_push_page_matches_the_push_block(self) -> None:
        rules = DEFAULT_MAGIC_RULES.push
        assert rules is not None, "the default canon offers Push"
        self.assertEqual(
            push_claims_of_page(read_page(PUSH_PAGE), rules),
            push_claims_of_rules(rules),
        )

    def test_channel_page_matches_the_channel_block(self) -> None:
        rules = DEFAULT_MAGIC_RULES.channel
        assert rules is not None, "the default canon bounds a casting by Channel"
        self.assertEqual(
            channel_claims_of_page(read_page(CHANNEL_PAGE), rules),
            channel_claims_of_rules(rules),
        )

    def test_true_magic_page_matches_the_true_magic_block(self) -> None:
        rules, true_skill = default_true_magic()
        self.assertEqual(
            true_magic_claims_of_page(read_page(TRUE_MAGIC_PAGE), rules),
            true_magic_claims_of_rules(rules, true_skill),
        )


class MagicPageDriftIsCaughtTest(SimpleTestCase):
    """Changing a number on a page must fail the comparison above.

    Each case swaps one fragment of the real page for another and checks that
    the claims read from the edited page no longer equal the file's. The
    fragment is asserted present first, so rewording the page cannot leave a
    case comparing a page with itself.
    """

    def assert_edits_are_caught[Rules](
        self,
        path: Path,
        rules: Rules,
        claims_of_page: Callable[[Page, Rules], dict[str, object]],
        expected_claims: dict[str, object],
        edits: tuple[tuple[str, str], ...],
    ) -> None:
        page = read_page(path)
        for old_fragment, new_fragment in edits:
            with self.subTest(old_fragment=old_fragment, new_fragment=new_fragment):
                self.assertIn(old_fragment, page.prose, f"{page.name} changed")
                edited = page._replace(
                    prose=page.prose.replace(old_fragment, new_fragment, 1)
                )
                self.assertNotEqual(claims_of_page(edited, rules), expected_claims)

    def test_borrowing_page_edits_are_caught(self) -> None:
        rules = DEFAULT_MAGIC_RULES.borrowing
        assert rules is not None
        self.assert_edits_are_caught(
            BORROWING_PAGE,
            rules,
            borrowing_claims_of_page,
            borrowing_claims_of_rules(rules),
            (
                ("up to 4 owed", "up to 5 owed"),
                ("2d6 against", "3d6 against"),
                ("against Wisdom", "against Intelligence"),
                ("less 2 for each", "less 3 for each"),
                ("a 12 always fails", "a 11 always fails"),
                ("as an Unravel", "as a Flicker"),
                ("by 3 to 5", "by 3 to 6"),
                ("a Fray, costing 2", "a Fray, costing 3"),
                ("costing 1 Body", "costing 1 Fatigue"),
                ("by 6 to 8, a Backlash", "by 6 to 9, a Backlash"),
                ("by 9 or more, an Unravel", "by 9 or more, a Setback"),
                (" but starting a Runaway", ""),
            ),
        )

    def test_runaway_page_edits_are_caught(self) -> None:
        rules = DEFAULT_MAGIC_RULES.runaway
        assert rules is not None
        self.assert_edits_are_caught(
            RUNAWAY_PAGE,
            rules,
            runaway_claims_of_page,
            runaway_claims_of_rules(rules),
            (
                ("drains its original", "drains twice original"),
                ("cost plus 1", "cost plus 2"),
                ("comes off Body", "comes off Fatigue"),
                ("at 0 Body", "at 1 Body"),
                ("at 0 Body", "at 0 Mana"),
                ("(3d6 against Wisdom)", "(2d6 against Wisdom)"),
                ("(3d6 against Wisdom)", "(3d6 against Intelligence)"),
                ("Anchor Roll, 3d6 against", "Anchor Roll, 2d6 against"),
                (
                    "Anchor Roll, 3d6 against Wisdom",
                    "Anchor Roll, 3d6 against Charisma",
                ),
                ("pay twice the original", "pay three times the original"),
                ("2d10 against Intelligence", "3d10 against Intelligence"),
                ("2d10 against Intelligence", "2d10 against Wisdom"),
                ("adds +2 per turn", "adds +3 per turn"),
            ),
        )

    def test_mana_pool_page_edits_are_caught(self) -> None:
        rules = DEFAULT_MAGIC_RULES.casting_failure
        assert rules is not None
        self.assert_edits_are_caught(
            MANA_POOL_PAGE,
            rules,
            casting_failure_claims_of_page,
            casting_failure_claims_of_rules(rules),
            (
                ("a natural 13 or more", "a natural 12 or more"),
                ("except on a 13 or a 14", "except on a 12 or a 14"),
                ("except on a 13 or a 14", "except on a 13 or a 15"),
                ("a 14 also starts", "a 15 also starts"),
            ),
        )

    def test_counterspell_page_edits_are_caught(self) -> None:
        rules = DEFAULT_MAGIC_RULES.counterspell
        assert rules is not None
        self.assert_edits_are_caught(
            COUNTERSPELL_PAGE,
            rules,
            counterspell_claims_of_page,
            counterspell_claims_of_rules(rules),
            (
                ("committing 1 to 5 mana", "committing 2 to 5 mana"),
                ("committing 1 to 5 mana", "committing 1 to 6 mana"),
                ("roll 2d10 plus", "roll 3d10 plus"),
                ("2d10 plus Intelligence", "2d10 plus Wisdom"),
                ("the caster adds +1 for every 2", "the caster adds +2 for every 2"),
                ("the caster adds +1 for every 2", "the caster adds +1 for every 3"),
                ("defender +1 for every 2", "defender +1 for every 3"),
                ("defender +1 for every 2", "defender +2 for every 2"),
                ("a tie goes to the defender", "a tie goes to the caster"),
                (
                    "each side adds +2 for each level",
                    "each side adds +3 for each level",
                ),
            ),
        )

    def test_push_page_edits_are_caught(self) -> None:
        rules = DEFAULT_MAGIC_RULES.push
        assert rules is not None
        self.assert_edits_are_caught(
            PUSH_PAGE,
            rules,
            push_claims_of_page,
            push_claims_of_rules(rules),
            (
                ("(3d6 against Intelligence)", "(2d6 against Intelligence)"),
                ("(3d6 against Intelligence)", "(3d6 against Wisdom)"),
                ("or roll an 18,", "or roll a 17,"),
            ),
        )

    def test_channel_page_edits_are_caught(self) -> None:
        rules = DEFAULT_MAGIC_RULES.channel
        assert rules is not None
        self.assert_edits_are_caught(
            CHANNEL_PAGE,
            rules,
            channel_claims_of_page,
            channel_claims_of_rules(rules),
            (
                ("is 2 for each level", "is 3 for each level"),
                ("they hold in that spell,", "they hold in Sword,"),
                ("in that spell, plus 6.", "in that spell, plus 5."),
                ("still has a Channel of 6,", "still has a Channel of 5,"),
                ("a Channel of 3 for each level", "a Channel of 4 for each level"),
                ("read from the caster's Arcane:", "read from the caster's Sword:"),
                ("they hold in the Arcane skill,", "they hold in the Sword skill,"),
                ("Arcane skill, with nothing added,", "Arcane skill, plus 1,"),
            ),
        )

    def test_true_magic_page_edits_are_caught(self) -> None:
        rules, true_skill = default_true_magic()
        self.assert_edits_are_caught(
            TRUE_MAGIC_PAGE,
            rules,
            true_magic_claims_of_page,
            true_magic_claims_of_rules(rules, true_skill),
            (
                ("at most 2 for each level", "at most 3 for each level"),
                ("holds in Arcane,", "holds in Sword,"),
                ("takes 3 turns for each tier", "takes 2 turns for each tier"),
                ("Sear, 1d8 damage", "Sear, 1d6 damage"),
                ("damage to Fatigue per tier", "damage to Body per tier"),
                ("which needs no Dexterity roll", "which needs the Dexterity roll"),
                ("and ignores armour", "and is stopped by armour"),
                ("Knit, 1d4+1 healing", "Knit, 1d4+2 healing"),
                ("healing to Body per tier", "healing to Fatigue per tier"),
                ("Knit,", "Weld,"),
                (", crafted only at a tier the Game Master has granted", ""),
                ("2d10 against Wisdom less", "2d8 against Wisdom less"),
                ("2d10 against Wisdom less", "2d10 against Intelligence less"),
                ("less 4 for each mana", "less 3 for each mana"),
                ("by 1 to 10 it is", "by 1 to 9 it is"),
                ("by 11 or more", "by 12 or more"),
                ("it is a Breakaway, a Runaway;", "it is a Breakaway;"),
                ("it is a Scatter;", "it is a Scatter, a Runaway;"),
                ("a natural 20 is always", "a natural 19 is always"),
                ("is always a Breakaway", "is always a Scatter"),
                ("3d6 against Wisdom plus", "2d6 against Wisdom plus"),
                ("against Wisdom plus 5", "against Intelligence plus 5"),
                ("plus 5 for each Arcane level", "plus 4 for each Arcane level"),
                ("for each Arcane level", "for each Sword level"),
                ("goes off drains 8 Fatigue", "goes off drains 7 Fatigue"),
                ("goes off drains 8 Fatigue", "goes off drains 8 Body"),
                ("True cast drains 8 Fatigue", "True cast drains 9 Fatigue"),
                ("6 Fatigue a point when a hit", "5 Fatigue a point when a hit"),
                ("7 Fatigue a point when it is", "8 Fatigue a point when it is"),
                ("7 Fatigue a point when it is", "7 Body a point when it is"),
            ),
        )

    def test_a_flag_whose_phrase_is_reworded_names_the_phrase(self) -> None:
        borrowing = DEFAULT_MAGIC_RULES.borrowing
        runaway = DEFAULT_MAGIC_RULES.runaway
        assert borrowing is not None and runaway is not None
        borrowing_page = read_page(BORROWING_PAGE)
        runaway_page = read_page(RUNAWAY_PAGE)
        with self.assertRaisesRegex(AssertionError, "borrowing.md no longer states"):
            borrowing_claims_of_page(
                borrowing_page._replace(
                    prose=borrowing_page.prose.replace("pays the Debt", "settles")
                ),
                borrowing,
            )
        with self.assertRaisesRegex(AssertionError, "runaway.md no longer states"):
            runaway_claims_of_page(
                runaway_page._replace(
                    prose=runaway_page.prose.replace("if that fails", "if it fails")
                ),
                runaway,
            )

    def test_a_page_that_drops_the_sentence_is_refused(self) -> None:
        rules = DEFAULT_MAGIC_RULES.borrowing
        assert rules is not None
        page = read_page(BORROWING_PAGE)
        edited = page._replace(
            prose=page.prose.replace("up to 4 owed in all", "without limit")
        )
        with self.assertRaisesRegex(AssertionError, "borrowing.md no longer states"):
            borrowing_claims_of_page(edited, rules)
