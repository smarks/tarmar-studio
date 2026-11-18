"""Migration 0030: two schools leave the school roll (tarmar-studio #208).

Counter and Arcane/True Magic are no longer schools a character rolls or
chooses. A character holding one has the school cleared and the roll kept, so
the player chooses again from the list the roll opened, and the history says
why. Every other character is left as it was.
"""

import importlib

from django.apps import apps as django_apps

from characters.magic import choose_school, rolled_school_row, schools_to_choose
from characters.models import Character
from common.test_helpers import BaseTestCase, create_character

MIGRATION = importlib.import_module(
    "characters.migrations.0030_schools_the_roll_no_longer_names"
)

#: A school the migration leaves alone.
KEPT_SCHOOL = "Elemental"


class RemovedSchoolsMigrationTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("school-migrated")

    def migrated(self, **fields: object) -> Character:
        """A character with ``fields``, read back after the migration ran."""
        character = create_character(self.owner, **fields)
        MIGRATION.clear_removed_schools(django_apps, None)
        return Character.objects.get(pk=character.pk)

    def test_the_removed_schools_are_the_two_named(self):
        self.assertEqual(MIGRATION.REMOVED_SCHOOLS, ("Counter", "Arcane/True Magic"))

    def test_a_removed_school_is_cleared_and_its_roll_kept(self):
        for school in MIGRATION.REMOVED_SCHOOLS:
            with self.subTest(school=school):
                character = self.migrated(magic_school=school, magic_school_roll=88)
                self.assertEqual(character.magic_school, "")
                self.assertEqual(character.magic_school_roll, 88)

    def test_the_history_says_which_school_and_why(self):
        earlier = {"ts": "t", "field": "name", "old": "A", "new": "B", "by": "someone"}
        character = self.migrated(
            magic_school="Counter", magic_school_roll=88, history=[earlier]
        )
        self.assertEqual(character.history[0], earlier)
        (line,) = character.history[1:]
        self.assertEqual(
            (line["field"], line["old"], line["by"]),
            ("magic_school", "Counter", "system"),
        )
        self.assertEqual(
            line["new"],
            "unchosen: Counter is no longer a school a character rolls or chooses "
            "(tarmar-studio #208); the school roll is kept, so choose again from "
            "the list it opened",
        )
        self.assertTrue(line["ts"])

    def test_the_history_keeps_its_last_fifty_entries(self):
        full = [
            {"ts": "t", "field": "name", "old": "", "new": str(index), "by": "someone"}
            for index in range(50)
        ]
        character = self.migrated(magic_school="Arcane/True Magic", history=full)
        self.assertEqual(len(character.history), 50)
        self.assertEqual(character.history[0]["new"], "1")
        self.assertEqual(character.history[-1]["old"], "Arcane/True Magic")

    def test_the_player_then_chooses_again_from_the_list_the_roll_opened(self):
        character = self.migrated(magic_school="Counter", magic_school_roll=88)
        opened = rolled_school_row(character)
        assert opened is not None, "the loaded canon rolls a caster's school"
        offered = schools_to_choose(character, is_game_master=False)
        self.assertEqual(offered, opened.schools)
        self.assertTrue(offered)
        choose_school(character, offered[0], is_game_master=False)
        self.assertEqual(character.magic_school, offered[0])

    def test_a_removed_school_with_no_roll_is_cleared_for_the_player_to_roll(self):
        character = self.migrated(magic_school="Counter", magic_school_roll=None)
        self.assertEqual(character.magic_school, "")
        self.assertIsNone(character.magic_school_roll)
        self.assertEqual(schools_to_choose(character, is_game_master=False), ())

    def test_every_other_character_is_left_as_it_was(self):
        for school, roll in ((KEPT_SCHOOL, 12), ("", 88), ("", None)):
            with self.subTest(school=school, roll=roll):
                character = self.migrated(magic_school=school, magic_school_roll=roll)
                self.assertEqual(character.magic_school, school)
                self.assertEqual(character.magic_school_roll, roll)
                self.assertEqual(character.history, [])

    def test_running_it_again_changes_nothing(self):
        character = self.migrated(magic_school="Counter", magic_school_roll=88)
        MIGRATION.clear_removed_schools(django_apps, None)
        again = Character.objects.get(pk=character.pk)
        self.assertEqual(again.history, character.history)
        self.assertEqual(again.magic_school, "")
