"""Consolidation tests for the single roll service (issue #175).

``characters/dice.py`` keeps its public API but draws all randomness from a
module-default :class:`common.rolling.Roller`; ``characters.skills`` and
``gm.engine`` reach the same path. Pure logic — ``SimpleTestCase`` throughout.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from common.rolling import Roller, parse_dice_specification


class DiceModuleDelegationTest(SimpleTestCase):
    def test_parse_dice_expression_is_the_shared_parser(self):
        from characters import dice

        self.assertIs(dice.parse_dice_expression, parse_dice_specification)

    def test_roll_expression_draws_from_the_module_default_roller(self):
        from characters import dice

        expected = [
            list(Roller(seed=5).roll("3d6", purpose="expression").faces)
            for _ in range(3)
        ]
        with patch.object(dice, "_DEFAULT_ROLLER", Roller(seed=5)):
            rolled = [dice.roll_expression("3d6")[0] for _ in range(3)]
        self.assertNotEqual(rolled, expected)  # sequences advance per roll...

        with patch.object(dice, "_DEFAULT_ROLLER", Roller(seed=5)):
            replayed = [dice.roll_expression("3d6")[0] for _ in range(3)]
        self.assertEqual(rolled, replayed)  # ...but the same seed replays them

    def test_single_die_helpers_draw_from_the_module_default_roller(self):
        from characters import dice

        with patch.object(dice, "_DEFAULT_ROLLER", Roller(seed=8)):
            rolled = (dice.roll_d6(), dice.roll_d20(), dice.roll_d100())
        with patch.object(dice, "_DEFAULT_ROLLER", Roller(seed=8)):
            replayed = (dice.roll_d6(), dice.roll_d20(), dice.roll_d100())
        self.assertEqual(rolled, replayed)

    def test_roll_2d6_faces_returns_both_faces_and_the_total(self):
        from characters.dice import roll_2d6_faces

        with patch("characters.dice.random.randint", side_effect=[3, 4]):
            self.assertEqual(roll_2d6_faces(), (3, 4, 7))

    def test_the_survival_roll_returns_every_face_and_the_total(self):
        from characters.dice import roll_survival_dice
        from characters.tests.rules_fixtures import seated_skill_rules

        with seated_skill_rules():  # 3d6, the public pages' survival roll
            with patch("characters.dice.random.randint", side_effect=[3, 4, 6]):
                self.assertEqual(roll_survival_dice(), (3, 4, 6, 13))


class SkillsRollConsolidationTest(SimpleTestCase):
    def test_skills_survival_roller_is_the_dice_module_implementation(self):
        from characters import dice, skills

        self.assertIs(skills.roll_survival_dice, dice.roll_survival_dice)

    def test_survival_check_still_records_individual_faces(self):
        from characters.skills import simulate_experience_years

        with (
            patch("characters.skills.roll_survival_dice", return_value=(6, 5, 4, 15)),
            patch("characters.skills.roll_d20", return_value=10),
        ):
            run = simulate_experience_years(
                1, 4, 20, "merchant", 10, 10, 10, 10, 10, 10
            )
        self.assertEqual(run["years"][0]["survival_check"]["dice"], [6, 5, 4])
