"""Issue #958: no character is younger than the starting age.

Every character starts at the canon's starting age (16 on the public pages)
and ages only forward, so the floor is on the column
(``characters.models.validate_starting_age``), and the importer, the chat's
extraction schema and the sheet's writers all read the one rule.
"""

import random

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from characters.import_service import (
    CharacterImportError,
    held_to_player,
    save_imported_character,
    validate_import_payload,
)
from characters.models import (
    CHARACTER_FIELDS,
    character_int_field_minimum,
    starting_age,
)
from characters.skills import ALL_TRACKS, simulate_experience_years
from common.test_helpers import BaseTestCase, create_character
from gm.character_schema import character_json_schema, extraction_to_import_payload

from .test_import import _build_payload

BELOW_STARTING_AGE_REFUSAL = (
    f"Import failed: age: Value must be {starting_age()} or more"
)


class AgeFloorTest(BaseTestCase):
    """The model, the file import and the chat's extraction share one floor."""

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm("age-floor-dm")

    def test_the_model_refuses_an_age_below_the_starting_age(self):
        character = create_character(self.game_master, age=starting_age() - 1)
        self.assertIn("age", self._field_errors(character))
        character.age = starting_age()
        self.assertNotIn("age", self._field_errors(character))

    @staticmethod
    def _field_errors(character) -> dict[str, list[str]]:
        """What the model's own field checks refuse, as the admin runs them."""
        try:
            character.clean_fields()
        except ValidationError as refused:
            return refused.message_dict
        return {}

    def test_the_importers_floor_is_the_starting_age(self):
        self.assertEqual(character_int_field_minimum("age"), starting_age())

    def test_the_file_import_refuses_fifteen_and_keeps_sixteen(self):
        with self.assertRaises(CharacterImportError) as refused:
            validate_import_payload(
                _build_payload(overrides={"age": starting_age() - 1})
            )
        self.assertEqual(str(refused.exception), BELOW_STARTING_AGE_REFUSAL)

        imported = held_to_player(
            validate_import_payload(_build_payload(overrides={"age": starting_age()})),
            is_game_master=True,
            importer=self.game_master,
        )
        character = save_imported_character(self.game_master, imported)
        self.assertEqual(character.age, starting_age())

    def test_the_chats_extraction_refuses_fifteen_and_keeps_sixteen(self):
        payload = extraction_to_import_payload(
            {"name": "Extracted", "age": starting_age() - 1}
        )
        with self.assertRaises(CharacterImportError) as refused:
            validate_import_payload(payload)
        self.assertEqual(str(refused.exception), BELOW_STARTING_AGE_REFUSAL)

        payload["data"]["age"] = starting_age()
        self.assertEqual(validate_import_payload(payload).fields["age"], starting_age())


class AgeFloorSchemaTest(SimpleTestCase):
    """The schema the chat's model is steered by floors age at the constant."""

    def test_both_schemas_floor_age_at_the_starting_age(self):
        for for_game_master in (False, True):
            with self.subTest(for_game_master=for_game_master):
                field = character_json_schema(for_game_master=for_game_master)[
                    "properties"
                ]["age"]
                self.assertEqual(field["minimum"], starting_age())
                self.assertEqual(field["minimum"], character_int_field_minimum("age"))


class NoSheetPathLowersTheAgeTest(SimpleTestCase):
    """The sheet's writers of ``age`` only ever move it forward.

    The sweep for #958 found two: a run of prior experience
    (``Character.apply_experience_run``, which takes the run's last year)
    and the import above. The aging columns are read from the age, never
    written to it, and no single-field edit, a Game Master's included,
    takes the age.
    """

    def test_no_single_field_edit_takes_the_age(self):
        self.assertNotIn("age", CHARACTER_FIELDS)

    def test_every_year_of_a_run_ends_older_than_it_started(self):
        """Death included: a run that stops early still ends a year on."""
        attributes = dict.fromkeys(
            ("strength", "dexterity", "intelligence", "wisdom", "constitution"), 3
        )
        deaths = 0
        for track in ALL_TRACKS:
            for seed in range(20):
                with self.subTest(track=track, seed=seed):
                    random.seed(seed)
                    result = simulate_experience_years(
                        years_requested=4,
                        years_completed=0,
                        current_age=starting_age(),
                        track=track,
                        charisma=3,
                        **attributes,
                    )
                    ages = [year["age"] for year in result["years"]]
                    self.assertEqual(
                        ages,
                        list(range(starting_age() + 1, starting_age() + 1 + len(ages))),
                    )
                    deaths += not result["alive"]
        self.assertGreater(deaths, 0, "no run died, so the early stop went untested")
