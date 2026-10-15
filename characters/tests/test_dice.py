"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from characters.dice import (
    parse_dice_expression,
    roll_d20_logged,
    roll_expression,
    roll_expression_logged,
)


class DiceRollingTest(TestCase):
    """Tests for dice rolling utilities."""

    def test_roll_3d6_range(self):
        """roll_3d6 should return values between 3 and 18."""
        from characters.dice import roll_3d6

        for _ in range(100):
            result = roll_3d6()
            self.assertGreaterEqual(result, 3)
            self.assertLessEqual(result, 18)

    def test_roll_demon_die_format(self):
        """roll_demon_die should return a dot-separated string."""
        from characters.dice import roll_demon_die

        for _ in range(100):
            result = roll_demon_die()
            self.assertIsInstance(result, str)
            # Should be valid format: numbers separated by dots
            parts = result.split(".")
            self.assertTrue(all(p.isdigit() for p in parts))
            # Last roll should be 2-5 (terminating roll)
            self.assertIn(int(parts[-1]), [2, 3, 4, 5])

    def test_parse_demon_die(self):
        """parse_demon_die should convert string to list of ints."""
        from characters.dice import parse_demon_die

        self.assertEqual(parse_demon_die("3"), [3])
        self.assertEqual(parse_demon_die("1.4"), [1, 4])
        self.assertEqual(parse_demon_die("6.6.2"), [6, 6, 2])
        self.assertEqual(parse_demon_die("1.1.1.5"), [1, 1, 1, 5])

    def test_demon_die_to_height_inches(self):
        """demon_die_to_height_inches should calculate correct heights.

        Per attributes.md's Height table (issue #295): the FIRST roll sets
        the base height from the canon's height table. Each additional 1 in the chain
        subtracts 4" and each additional 6 adds 4". The final 2-5 roll only
        stops the chain -- it is not itself a base or an adjustment unless
        it was the only roll.
        """
        from characters.dice import demon_die_to_height_inches

        # Base heights (no chain)
        self.assertEqual(demon_die_to_height_inches("2"), 60)  # 5'0"
        self.assertEqual(demon_die_to_height_inches("3"), 64)  # 5'4"
        self.assertEqual(demon_die_to_height_inches("4"), 68)  # 5'8"
        self.assertEqual(demon_die_to_height_inches("5"), 72)  # 6'0"

        # A single leading 1/6 IS the base -- the reroll that stops the
        # chain doesn't itself add or subtract anything.
        self.assertEqual(demon_die_to_height_inches("1.3"), 56)  # 4'8"
        self.assertEqual(demon_die_to_height_inches("6.4"), 76)  # 6'4"

        # Extra 1s/6s beyond the first roll adjust the base by 4" each.
        self.assertEqual(demon_die_to_height_inches("1.1.2"), 52)  # 4'8" - 4"
        self.assertEqual(demon_die_to_height_inches("6.6.5"), 80)  # 6'4" + 4"

        # attributes.md / issue #295's own worked examples.
        self.assertEqual(demon_die_to_height_inches("6.2"), 76)  # 6'4"
        self.assertEqual(demon_die_to_height_inches("6.6.2"), 80)  # 6'8"
        self.assertEqual(demon_die_to_height_inches("1.1.3"), 52)  # 4'8" - 4" = 4'4"

    def test_leading_six_chain_never_shorter_than_plain_five(self):
        """A chain led by the tallest face must never derive a height below
        a plain (non-exploding) roll of 5.

        Regression for #295: the old final-roll-as-base reading let a
        leading 6 read as *shorter* than average (e.g. "6.2" == 5'4",
        under a plain roll of 4's 5'8", let alone a plain 5).
        """
        from characters.dice import demon_die_to_height_inches

        plain_five = demon_die_to_height_inches("5")
        for roll_str in ("6.2", "6.3", "6.4", "6.5", "6.6.2", "6.6.6.4"):
            with self.subTest(roll_str=roll_str):
                self.assertGreaterEqual(
                    demon_die_to_height_inches(roll_str), plain_five
                )

    def test_format_height(self):
        """format_height should format inches as feet'inches\"."""
        from characters.dice import format_height

        self.assertEqual(format_height(60), "5'0\"")
        self.assertEqual(format_height(64), "5'4\"")
        self.assertEqual(format_height(72), "6'0\"")
        self.assertEqual(format_height(75), "6'3\"")

    def test_calculate_final_weight_lbs(self):
        """calculate_final_weight_lbs should include STR bonus."""
        from characters.dice import calculate_final_weight_lbs

        # Base weight roll "3" = 10 stones, STR 10 = +5 stones = 15 stones = 210 lbs
        self.assertEqual(calculate_final_weight_lbs("3", 10), 15 * 14)
        # Base weight roll "2" = 9 stones, STR 16 = +8 stones = 17 stones = 238 lbs
        self.assertEqual(calculate_final_weight_lbs("2", 16), 17 * 14)

    def test_weight_roll_to_stones(self):
        """Base weight is a plain 1d6 mapped straight into the 8-13 stone band."""
        from characters.dice import weight_roll_to_stones

        self.assertEqual(weight_roll_to_stones("1"), 8)
        self.assertEqual(weight_roll_to_stones("2"), 9)
        self.assertEqual(weight_roll_to_stones("3"), 10)
        self.assertEqual(weight_roll_to_stones("4"), 11)
        self.assertEqual(weight_roll_to_stones("5"), 12)
        self.assertEqual(weight_roll_to_stones("6"), 13)

    def test_calculate_final_weight_ignores_legacy_chain_adjustment(self):
        """A legacy demon-die-chain weight_roll (from before this was a plain

        1d6) is parsed for its final digit only - chained 1s/6s no longer
        add or subtract stones.
        """
        from characters.dice import calculate_final_weight_lbs

        # "1.3" -> final roll 3 -> 10 stones (no -1 adjustment), STR 10 = +5 = 15
        self.assertEqual(calculate_final_weight_lbs("1.3", 10), 15 * 14)
        # "6.4" -> final roll 4 -> 11 stones (no +1 adjustment), STR 10 = +5 = 16
        self.assertEqual(calculate_final_weight_lbs("6.4", 10), 16 * 14)
        # "1.1.3" -> final roll 3 -> 10 stones (no -2 adjustment), STR 10 = +5 = 15
        self.assertEqual(calculate_final_weight_lbs("1.1.3", 10), 15 * 14)
        # "6.6.4" -> final roll 4 -> 11 stones (no +2 adjustment), STR 10 = +5 = 16
        self.assertEqual(calculate_final_weight_lbs("6.6.4", 10), 16 * 14)

    def test_roll_all_character_stats(self):
        """roll_all_character_stats should return all required fields."""
        from characters.dice import roll_all_character_stats

        stats = roll_all_character_stats()
        required_fields = [
            "strength",
            "dexterity",
            "intelligence",
            "wisdom",
            "constitution",
            "charisma",
            "appearance_roll",
            "height_roll",
            "weight_roll",
            "fatigue_roll",
            "provenance_class",
            "provenance_detail",
            "location",
            "wealth_level",
            "starting_coin",
            "dice_log",
        ]
        for field in required_fields:
            self.assertIn(field, stats)

    def test_roll_3d6_logged(self):
        """roll_3d6_logged should return result and log."""
        from characters.dice import roll_3d6_logged

        result, log = roll_3d6_logged()
        self.assertGreaterEqual(result, 3)
        self.assertLessEqual(result, 18)
        self.assertIn("3d6:", log)

    def test_roll_dice_logged_writes_what_dice_in_text_reads_back(self):
        """A canon's dice may carry a modifier (issue #59); the log line the
        roll writes is still read back whole, modifier and all."""
        from characters.dice import DICE_IN_TEXT, roll_dice_logged
        from common.rolling import Roller

        for specification in ("2d6", "1d8+1", "3d6-2"):
            with self.subTest(specification=specification):
                total, log = roll_dice_logged(specification, Roller(seed=59))
                ((expression, faces, read_total),) = DICE_IN_TEXT.findall(log)
                self.assertEqual(expression, specification)
                self.assertEqual(int(read_total), total)
                self.assertTrue(faces.strip())

    def test_roll_demon_die_logged(self):
        """roll_demon_die_logged should return result and log."""
        from characters.dice import roll_demon_die_logged

        result, log = roll_demon_die_logged()
        self.assertIsInstance(result, str)
        self.assertIn("Demon d6:", log)

    def test_roll_d6_logged(self):
        """roll_d6_logged should return a plain 1-6 result with no chain."""
        from characters.dice import roll_d6_logged

        for _ in range(100):
            result, log = roll_d6_logged()
            self.assertGreaterEqual(result, 1)
            self.assertLessEqual(result, 6)
            self.assertIn("1d6:", log)
            self.assertNotIn(".", log)

    def test_appearance_and_weight_rolls_are_plain_d6(self):
        """Appearance and base weight are plain 1d6: no re-roll, no chain.

        A rolled 1 or 6 must stand (roll_all_character_stats must not use
        the exploding demon die for these two fields - that's Height-only).
        """
        from characters.dice import roll_all_character_stats

        for _ in range(50):
            stats = roll_all_character_stats()
            self.assertNotIn(".", stats["appearance_roll"])
            self.assertIn(int(stats["appearance_roll"]), range(1, 7))
            self.assertNotIn(".", stats["weight_roll"])
            self.assertIn(int(stats["weight_roll"]), range(1, 7))

    def test_dice_log_contains_all_rolls(self):
        """dice_log should contain entries for all rolled stats."""
        from characters.dice import roll_all_character_stats

        stats = roll_all_character_stats()
        log = stats["dice_log"]
        self.assertIn("STR:", log)
        self.assertIn("DEX:", log)
        self.assertIn("INT:", log)
        self.assertIn("WIS:", log)
        self.assertIn("CON:", log)
        self.assertIn("CHR:", log)
        self.assertIn("Appearance:", log)
        self.assertIn("Height:", log)
        self.assertIn("Weight:", log)
        self.assertIn("Fatigue:", log)
        self.assertIn("Provenance:", log)
        self.assertIn("Location:", log)
        self.assertIn("Wealth:", log)

    def test_lookup_table(self):
        """A percentile table's lookup returns the row a roll lands on."""
        from characters.rules import RollTable, TableRow

        table = RollTable(
            rows=(TableRow(10, "first"), TableRow(20, "second"), TableRow(100, "third"))
        )
        self.assertEqual(table.lookup(5), "first")
        self.assertEqual(table.lookup(10), "first")
        self.assertEqual(table.lookup(15), "second")
        self.assertEqual(table.lookup(100), "third")

    def test_provenance_classes(self):
        """The default canon's provenance tables give the published details."""
        from .rules_fixtures import DEFAULT_CHARACTER_RULES

        nobility = DEFAULT_CHARACTER_RULES.nobility
        merchant = DEFAULT_CHARACTER_RULES.merchant
        commoner = DEFAULT_CHARACTER_RULES.commoner

        # Test specific lookups
        self.assertEqual(nobility.lookup(1), "Monarch")
        self.assertEqual(nobility.lookup(50), "Baron")
        self.assertEqual(merchant.lookup(50), "Retail")
        self.assertEqual(merchant.lookup(96), "Specialty")
        self.assertEqual(commoner.lookup(50), "Laborer")
        self.assertEqual(commoner.lookup(80), "Crafts")

        # Verify all details exist
        self.assertIn("Knights and Warrior Nobility", nobility.labels)
        self.assertIn("Wholesale", merchant.labels)
        self.assertIn("Crafts", commoner.labels)

    def test_roll_provenance_logged_all_classes(self):
        """roll_provenance_logged should handle all social classes."""
        from unittest.mock import patch

        from characters.dice import roll_provenance_logged

        # Test nobility (roll 1-10)
        with patch("characters.dice.roll_d100", side_effect=[5, 50]):
            social_class, detail, log = roll_provenance_logged()
            self.assertEqual(social_class, "nobility")
            self.assertEqual(detail, "Baron")
            self.assertIn("Nobility", log)

        # Test merchant (roll 11-30)
        with patch("characters.dice.roll_d100", side_effect=[20, 80]):
            social_class, detail, log = roll_provenance_logged()
            self.assertEqual(social_class, "merchant")
            self.assertEqual(detail, "Wholesale")
            self.assertIn("Merchant", log)

        # Test commoner (roll 31-100)
        with patch("characters.dice.roll_d100", side_effect=[50, 80]):
            social_class, detail, log = roll_provenance_logged()
            self.assertEqual(social_class, "commoner")
            self.assertEqual(detail, "Crafts")
            self.assertIn("Commoner", log)

    def test_roll_location_logged(self):
        """roll_location_logged should return valid locations."""
        from unittest.mock import patch

        from characters.dice import roll_location_logged

        # Test City (1-20)
        with patch("characters.dice.roll_d100", return_value=10):
            location, log = roll_location_logged()
            self.assertEqual(location, "City")
            self.assertIn("City", log)

        # Test Village (21-70)
        with patch("characters.dice.roll_d100", return_value=50):
            location, log = roll_location_logged()
            self.assertEqual(location, "Village")

        # Test Rural (71-99)
        with patch("characters.dice.roll_d100", return_value=80):
            location, log = roll_location_logged()
            self.assertEqual(location, "Rural")

        # Test Consult GM (100)
        with patch("characters.dice.roll_d100", return_value=100):
            location, log = roll_location_logged()
            self.assertEqual(location, "Consult GM")

    def test_roll_wealth_logged(self):
        """roll_wealth_logged should return valid wealth levels and starting coin."""
        from unittest.mock import patch

        from characters.dice import roll_wealth_logged

        # Test Subsistence (0-15)
        with patch("characters.dice.roll_d100", return_value=10):
            wealth_level, starting_coin, log = roll_wealth_logged()
            self.assertEqual(wealth_level, "Subsistence")
            self.assertEqual(starting_coin, 10)

        # Test Moderate (16-70)
        with patch("characters.dice.roll_d100", return_value=50):
            wealth_level, starting_coin, log = roll_wealth_logged()
            self.assertEqual(wealth_level, "Moderate")
            self.assertEqual(starting_coin, 100)

        # Test Merchant (71-95) - has bonus roll
        with patch("characters.dice.roll_d100", side_effect=[80, 50]):
            wealth_level, starting_coin, log = roll_wealth_logged()
            self.assertEqual(wealth_level, "Merchant")
            self.assertEqual(starting_coin, 150)  # 100 + 50
            self.assertIn("100 + d100", log)

        # Test Rich (96-100)
        with patch("characters.dice.roll_d100", return_value=100):
            wealth_level, starting_coin, log = roll_wealth_logged()
            self.assertEqual(wealth_level, "Rich")
            # "Consult GM": no number for a Game Master to overwrite (#749).
            self.assertIsNone(starting_coin)


class DiceExpressionTest(SimpleTestCase):
    """``NdM±K`` parsing/rolling — the project's one dice-notation parser.

    ``gm.engine.roll_dice`` delegates here (issue #105), so these bounds and
    error messages are the ones the GM's ``/roll`` command enforces too.
    """

    def test_parse_returns_count_sides_and_modifier(self):
        self.assertEqual(parse_dice_expression("3d6"), (3, 6, 0))
        self.assertEqual(parse_dice_expression("2d6+3"), (2, 6, 3))
        self.assertEqual(parse_dice_expression("1d6-1"), (1, 6, -1))

    def test_parse_tolerates_surrounding_space_and_case(self):
        self.assertEqual(parse_dice_expression("  2D6+2 "), (2, 6, 2))

    def test_parse_rejects_junk(self):
        for expression in ("invalid", "", "d6", "3d", "3d6+", "3d6 fireball"):
            with self.subTest(expression=expression):
                with self.assertRaises(ValueError):
                    parse_dice_expression(expression)

    def test_parse_rejects_out_of_range_counts_and_sides(self):
        for expression in ("0d6", "101d6", "1d1", "1d1001", "999999999d6"):
            with self.subTest(expression=expression):
                with self.assertRaises(ValueError):
                    parse_dice_expression(expression)

    def test_roll_expression_returns_each_die_and_the_total(self):
        rolls, total = roll_expression("3d6")
        self.assertEqual(len(rolls), 3)
        self.assertTrue(all(1 <= roll <= 6 for roll in rolls))
        self.assertEqual(total, sum(rolls))

    def test_roll_expression_applies_the_modifier(self):
        with patch("characters.dice.random.randint", side_effect=[4, 5]):
            rolls, total = roll_expression("2d6-2")
        self.assertEqual(rolls, [4, 5])
        self.assertEqual(total, 7)

    def test_roll_expression_logged_spells_out_the_dice(self):
        with patch("characters.dice.random.randint", side_effect=[4, 5]):
            rolls, total, log = roll_expression_logged("2d6-2")
        self.assertEqual((rolls, total), ([4, 5], 7))
        self.assertEqual(log, "2d6-2: [4, 5] = 7")

    def test_roll_d20_logged_returns_the_face_and_a_log_line(self):
        with patch("characters.dice.random.randint", return_value=14):
            result, log = roll_d20_logged()
        self.assertEqual(result, 14)
        self.assertEqual(log, "d20: 14")
