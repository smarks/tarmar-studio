"""Tests for the single roll service (issue #175).

``common.rolling`` is pure Python — these tests run under ``SimpleTestCase``
and never touch the database.
"""

import random
from unittest.mock import patch

from django.test import SimpleTestCase

from common.rolling import (
    Roller,
    RollRecord,
    dice_total_range,
    parse_dice_specification,
    pinned_dice,
    standard_dice_specification,
)


class ParseDiceSpecificationTest(SimpleTestCase):
    """The shared ``NdS`` / ``NdS±M`` parser."""

    def test_parses_count_sides_and_modifier(self):
        self.assertEqual(parse_dice_specification("2d6+3"), (2, 6, 3))
        self.assertEqual(parse_dice_specification("2d6-2"), (2, 6, -2))
        self.assertEqual(parse_dice_specification("3d6"), (3, 6, 0))

    def test_normalizes_case_and_whitespace(self):
        self.assertEqual(parse_dice_specification("  1D20 "), (1, 20, 0))

    def test_rejects_junk(self):
        for specification in ("invalid", "", "d6", "3d", "3d6+", "3d6 fireball"):
            with self.subTest(specification=specification):
                with self.assertRaises(ValueError):
                    parse_dice_specification(specification)

    def test_rejects_out_of_range_counts_and_sides(self):
        for specification in ("0d6", "101d6", "1d1", "1d1001", "999999999d6"):
            with self.subTest(specification=specification):
                with self.assertRaises(ValueError):
                    parse_dice_specification(specification)

    def test_error_messages_match_the_historic_wording(self):
        """The GM's /roll surfaces these strings verbatim (issue #105)."""
        with self.assertRaises(ValueError) as caught:
            parse_dice_specification("nonsense")
        self.assertEqual(str(caught.exception), "Invalid dice expression: nonsense")

        with self.assertRaises(ValueError) as caught:
            parse_dice_specification("0d6")
        self.assertEqual(str(caught.exception), "Dice expression out of range: 0d6")


class DiceSpecificationFormTest(SimpleTestCase):
    """The standard spelling of a specification, and the totals it can roll."""

    def test_every_spelling_of_one_roll_has_one_standard_form(self):
        for specification, standard in (
            ("2d6", "2d6"),
            (" 2D6 ", "2d6"),
            ("02d06", "2d6"),
            ("2d6+0", "2d6"),
            ("1d100+05", "1d100+5"),
            ("3d6-2", "3d6-2"),
        ):
            with self.subTest(specification=specification):
                self.assertEqual(standard_dice_specification(specification), standard)

    def test_a_specification_that_does_not_parse_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Invalid dice expression"):
            standard_dice_specification("2x6")

    def test_the_total_range_runs_from_all_ones_to_all_top_faces(self):
        self.assertEqual(dice_total_range("2d6"), (2, 12))
        self.assertEqual(dice_total_range("3d6-2"), (1, 16))
        self.assertEqual(dice_total_range("1d100+100"), (101, 200))


class RollRecordTest(SimpleTestCase):
    def test_is_frozen(self):
        record = RollRecord(
            purpose="attack",
            specification="1d20",
            faces=(14,),
            modifier=0,
            total=14,
            target_number=None,
            outcome=None,
        )
        with self.assertRaises(AttributeError):
            record.total = 99  # type: ignore[misc]


class RollerTest(SimpleTestCase):
    def test_roll_returns_faces_summing_to_total_minus_modifier(self):
        record = Roller(seed=1).roll("3d6+2", purpose="damage")
        self.assertEqual(len(record.faces), 3)
        self.assertTrue(all(1 <= face <= 6 for face in record.faces))
        self.assertEqual(record.modifier, 2)
        self.assertEqual(record.total, sum(record.faces) + 2)
        self.assertEqual(record.purpose, "damage")
        self.assertEqual(record.specification, "3d6+2")

    def test_same_seed_reproduces_the_same_faces(self):
        first = Roller(seed=42).roll("3d6", purpose="check")
        second = Roller(seed=42).roll("3d6", purpose="check")
        self.assertEqual(first.faces, second.faces)

    def test_keyword_modifier_adds_to_the_specification_modifier(self):
        record = Roller(seed=7).roll("2d6+3", purpose="survival", modifier=-1)
        self.assertEqual(record.modifier, 2)
        self.assertEqual(record.total, sum(record.faces) + 2)

    def test_target_number_and_outcome_are_recorded(self):
        record = Roller(seed=3).roll(
            "1d20", purpose="attack", target_number=15, outcome="hit"
        )
        self.assertEqual(record.target_number, 15)
        self.assertEqual(record.outcome, "hit")

    def test_specification_is_normalized_on_the_record(self):
        record = Roller(seed=3).roll(" 1D20 ", purpose="attack")
        self.assertEqual(record.specification, "1d20")

    def test_on_roll_callback_sees_every_record(self):
        captured: list[RollRecord] = []
        roller = Roller(seed=9, on_roll=captured.append)
        first = roller.roll("1d6", purpose="a")
        second = roller.roll("2d6", purpose="b")
        self.assertEqual(captured, [first, second])

    def test_invalid_specification_raises_and_fires_no_callback(self):
        captured: list[RollRecord] = []
        roller = Roller(seed=9, on_roll=captured.append)
        with self.assertRaises(ValueError):
            roller.roll("bogus", purpose="a")
        self.assertEqual(captured, [])

    def test_unseeded_roller_draws_from_the_module_global_random(self):
        """An unseeded Roller must respect ``random.seed()`` and test patches.

        Existing tests patch ``characters.dice.random.randint`` — the module
        object shared by every ``import random`` — so the unseeded roller has
        to draw through the module at call time rather than a private
        ``random.Random()`` instance.
        """
        with patch.object(random, "randint", side_effect=[4, 5]):
            record = Roller().roll("2d6", purpose="check")
        self.assertEqual(record.faces, (4, 5))

    def test_seeded_roller_ignores_the_module_global_random(self):
        expected = Roller(seed=11).roll("2d6", purpose="check").faces
        with patch.object(random, "randint", side_effect=AssertionError):
            record = Roller(seed=11).roll("2d6", purpose="check")
        self.assertEqual(record.faces, expected)


class PinnedDiceTest(SimpleTestCase):
    """``pinned_dice``: the browser tests' seed for one request (issue #959)."""

    def test_an_unseeded_roll_inside_the_block_draws_from_the_seed(self):
        seeded = random.Random(3)
        expected = tuple(seeded.randint(1, 6) for _ in range(3))
        with pinned_dice(3):
            record = Roller().roll("3d6", purpose="survival")
        self.assertEqual(record.faces, expected)

    def test_each_block_starts_the_seed_afresh(self):
        with pinned_dice(3):
            first = Roller().roll("3d6", purpose="survival").faces
        with pinned_dice(3):
            second = Roller().roll("3d6", purpose="survival").faces
        self.assertEqual(first, second)

    def test_a_roller_with_its_own_seed_keeps_it(self):
        expected = Roller(seed=11).roll("2d6", purpose="check").faces
        with pinned_dice(3):
            record = Roller(seed=11).roll("2d6", purpose="check")
        self.assertEqual(record.faces, expected)

    def test_the_pin_ends_with_the_block_even_when_it_raises(self):
        with self.assertRaises(RuntimeError):
            with pinned_dice(3):
                raise RuntimeError("the view failed")
        with patch.object(random, "randint", side_effect=[4, 5]):
            record = Roller().roll("2d6", purpose="check")
        self.assertEqual(record.faces, (4, 5))
