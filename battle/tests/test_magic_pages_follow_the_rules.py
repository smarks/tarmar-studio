"""The default canon's magic pages state the numbers in its magic-rules.json.

``reference/default_canon/magic-mechanics/`` settles five mechanics for the
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
  (the ``push`` block).

``channel.md`` states no number and the default canon's ``channel`` block is
null, so there is nothing to pin.

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
    BorrowingRules,
    CastingFailureRules,
    CounterspellRules,
    PushRules,
    RunawayRules,
)

from characters.constants import CORE_ATTRIBUTE_FIELDS
from reference.paths import DEFAULT_CANON_DIR

from .magic_fixtures import DEFAULT_MAGIC_RULES

MAGIC_MECHANICS_DIR = DEFAULT_CANON_DIR / "magic-mechanics"
BORROWING_PAGE = MAGIC_MECHANICS_DIR / "borrowing.md"
RUNAWAY_PAGE = MAGIC_MECHANICS_DIR / "runaway.md"
MANA_POOL_PAGE = MAGIC_MECHANICS_DIR / "mana-pool.md"
COUNTERSPELL_PAGE = MAGIC_MECHANICS_DIR / "counterspell.md"
PUSH_PAGE = MAGIC_MECHANICS_DIR / "push.md"

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
FAILURE_NAME = r"A failed Debt Roll is a (\w+),"
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

PUSH_CONTROL_ROLL = r"Control Roll \((\d+d\d+) against (\w+)\)"
PUSH_AUTOMATIC_RUNAWAY = r"or roll an? (\d+), and the spell slips"


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
        "failure_label": search(FAILURE_NAME, page).group(1),
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
        "failure_label": rules.failure.label,
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

    def test_the_channel_page_has_nothing_to_pin(self) -> None:
        """channel.md states no number because the default canon has no Channel."""
        self.assertIsNone(DEFAULT_MAGIC_RULES.channel)
        channel_page = read_page(MAGIC_MECHANICS_DIR / "channel.md")
        self.assertIsNone(re.search(r"\d", channel_page.prose))


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
                ("is a Backlash", "is a Setback"),
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
