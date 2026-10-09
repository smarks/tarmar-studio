"""The combatant name rule is the character name rule plus parentheses."""

from django.test import SimpleTestCase

from battle.views import COMBATANT_NAME_PATTERN
from characters.constants import CHARACTER_NAME_PATTERN


class CombatantNamePatternTest(SimpleTestCase):
    def test_accepts_every_character_name_and_numbered_duplicates(self):
        for name in ("Aelric", "Mary-Jane O'Neil", "Sir Bob Jr.", "Ødegård"):
            with self.subTest(name=name):
                self.assertTrue(CHARACTER_NAME_PATTERN.match(name))
                self.assertTrue(COMBATANT_NAME_PATTERN.match(name))
        self.assertTrue(COMBATANT_NAME_PATTERN.match("Cougar (2)"))
        self.assertFalse(CHARACTER_NAME_PATTERN.match("Cougar (2)"))

    def test_rejects_what_neither_allows(self):
        for name in ("<script>", "semi;colon", "line\nbreak"):
            with self.subTest(name=name):
                self.assertFalse(CHARACTER_NAME_PATTERN.match(name))
                self.assertFalse(COMBATANT_NAME_PATTERN.match(name))
