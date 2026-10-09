"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

from characters.models import Character
from common.test_helpers import BaseTestCase


class CharacterModelTest(BaseTestCase):
    """Tests for Character model."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.character = Character.objects.create(
            user=self.user,
            name="Test Character",
            strength=14,
            dexterity=12,
            intelligence=10,
            wisdom=13,
            constitution=15,
            charisma=8,
            appearance_roll="3",
            height_roll="4",
            weight_roll="3",
            fatigue_roll=7,
        )

    def test_character_str(self):
        """Character string representation."""
        self.assertEqual(str(self.character), "Test Character (testuser)")

    def test_fatigue_calculation(self):
        """Fatigue should be CON + WIS + INT + max(DEX,STR) + roll."""
        # CON=15 + WIS=13 + INT=10 + max(12,14)=14 + roll=7 = 59
        self.assertEqual(self.character.fatigue, 59)

    def test_body_calculation(self):
        """Body should be 2/3 of fatigue, rounded up."""
        import math

        expected = math.ceil(59 * 2 / 3)
        self.assertEqual(self.character.body, expected)

    def test_physical_calculation(self):
        """Physical should be half of effective STR+DEX+CON, rounded up."""
        import math

        # STR=14 + DEX=12 + CON=15 = 41; 41/2 = 20.5 -> ceil 21
        expected = math.ceil((14 + 12 + 15) / 2)
        self.assertEqual(self.character.physical, expected)

    def test_mental_calculation(self):
        """Mental should be half of effective WIS+INT+CHA, rounded up."""
        import math

        # WIS=13 + INT=10 + CHA=8 = 31; 31/2 = 15.5 -> ceil 16
        expected = math.ceil((13 + 10 + 8) / 2)
        self.assertEqual(self.character.mental, expected)

    def test_physical_uses_effective_attributes(self):
        """Physical should apply aging penalties to STR/DEX/CON."""
        import math

        self.character.aging_str_penalty = 4
        self.character.aging_dex_penalty = 2
        self.character.aging_con_penalty = 5
        # effective STR=10, DEX=10, CON=10 = 30; 30/2 = 15
        expected = math.ceil((10 + 10 + 10) / 2)
        self.assertEqual(self.character.physical, expected)

    def test_mental_uses_effective_attributes(self):
        """Mental should apply aging penalties to WIS/INT (CHA has none)."""
        import math

        self.character.aging_wis_penalty = 3
        self.character.aging_int_penalty = 2
        # effective WIS=10, INT=8, CHA=8 = 26; 26/2 = 13
        expected = math.ceil((10 + 8 + 8) / 2)
        self.assertEqual(self.character.mental, expected)

    def test_fatigue_uses_effective_attributes(self):
        """Fatigue should apply aging penalties (pools shrink as attributes age)."""
        self.character.aging_con_penalty = 5
        self.character.aging_wis_penalty = 3
        self.character.aging_int_penalty = 2
        self.character.aging_str_penalty = 4
        self.character.aging_dex_penalty = 6
        # effective CON=10, WIS=10, INT=8, DEX=6, STR=10 -> max(DEX,STR)=10
        # 10 + 10 + 8 + 10 + roll=7 = 45
        self.assertEqual(self.character.fatigue, 45)

    def test_body_uses_effective_attributes(self):
        """Body should derive from the aging-penalized fatigue value."""
        import math

        self.character.aging_con_penalty = 5
        self.character.aging_wis_penalty = 3
        self.character.aging_int_penalty = 2
        self.character.aging_str_penalty = 4
        self.character.aging_dex_penalty = 6
        expected = math.ceil(45 * 2 / 3)
        self.assertEqual(self.character.body, expected)

    def test_height_display(self):
        """height_display should return formatted height."""
        self.assertEqual(self.character.height_display, "5'8\"")

    def test_weight_lbs(self):
        """weight_lbs should include STR bonus."""
        # Base "3" = 10 stones, STR 14 = +7 stones = 17 stones = 238 lbs
        self.assertEqual(self.character.weight_lbs, 17 * 14)

    def test_weight_uses_the_attribute_the_rule_names(self):
        """Weight is figured from STR, not effective STR (issues #691, #685).

        attributes.md writes it as "Final weight = Base weight + (STR / 2)
        stones", and the corpus says where it means the aged score instead:
        derived-pools.md spells out that "'Effective' means after aging
        penalties are applied". This page does not, so plain Strength it is.
        The code used to pass effective_strength, and a character lost seven
        pounds on their thirty-sixth birthday.
        """
        self.character.age = 68  # aging.md: STR -2 at 68
        self.character.save()
        self.assertEqual(self.character.effective_strength, 12)
        # Base "3" = 10 stones, STR 14 = +7 stones = 17 stones = 238 lbs,
        # unchanged by the aging that took effective STR to 12.
        self.assertEqual(self.character.weight_lbs, 17 * 14)

    def test_highest_skill_level_is_uncapped(self):
        """Issue #146: 21 points is level 6, not a level clamped at 5."""
        self.character.skills = {"sword": 21, "shield": 3}
        self.assertEqual(self.character.highest_skill_level, 6)

    def test_highest_skill_level_prices_arcane_double(self):
        """Issue #154: Arcane's points buy half as much level as any other
        skill's, so the highest level is read per skill, not per point total."""
        self.character.skills = {"arcane": 20}
        self.assertEqual(self.character.highest_skill_level, 4)

        # A plain skill with the same points still reaches level 5, and the
        # higher of the two is the one reported.
        self.character.skills = {"arcane": 20, "sword": 20}
        self.assertEqual(self.character.highest_skill_level, 5)
