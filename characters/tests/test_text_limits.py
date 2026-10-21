"""One limit per free-text field, held on the sheet and by the importer (#928).

The chat's side is ``gm.test_text_limits``. Here: the sheet refuses one
character past each limit with its usual error rather than cutting the text
short, and the importer refuses what the sheet would.
"""

import json

from django.urls import reverse

from character_export.export_utils import generate_character_json
from characters import services
from characters.constants import (
    MAX_COIN_REASON_LENGTH,
    MAX_DEATH_CAUSE_LENGTH,
    MAX_EQUIPMENT_DAMAGE_LENGTH,
    MAX_EQUIPMENT_NOTES_LENGTH,
    MAX_SKILL_NAME_LENGTH,
)
from characters.import_service import (
    CharacterImportError,
    held_to_player,
    save_imported_character,
    validate_import_payload,
)
from characters.models import Character
from common.test_helpers import (
    BaseTestCase,
    create_character,
    limit_refusal_parts,
    too_long_refusal,
)


class SheetTextLimitsTest(BaseTestCase):
    """A Game Master's sheet, so only the length can be what refuses."""

    def setUp(self):
        super().setUp()
        self.dm = self.login_dm()
        self.character = create_character(
            self.dm, name="Wren", starting_coin=100, free_skill_points=2
        )

    def post(self, name: str, data: dict):
        return self.post_json(
            reverse(f"characters:{name}", args=[self.character.pk]), data
        )

    def assert_refused(self, response, reason: str) -> None:
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn(reason, response.json()["error"])

    def test_a_skill_at_the_limit_is_allocated(self):
        response = self.post("allocate_skill", {"skill": "q" * MAX_SKILL_NAME_LENGTH})

        self.assertEqual(response.status_code, 200, response.content)

    def test_a_skill_one_past_the_limit_is_refused(self):
        response = self.post(
            "allocate_skill", {"skill": "q" * (MAX_SKILL_NAME_LENGTH + 1)}
        )

        self.assert_refused(
            response, f"Skill name must be {MAX_SKILL_NAME_LENGTH} characters"
        )
        self.character.refresh_from_db()
        self.assertEqual(self.character.skills, {})

    def test_a_skill_is_refused_by_its_received_length_not_its_stored_one(self):
        # Issue #52: the name is held to the limit before it is normalized, as
        # the Games Master chat holds it, so a long crafted name is refused at
        # the limit rather than read in full. This one would store as
        # "sword to hit", which fits, so only the received length refuses it.
        padded = "Sword (+1 to hit" + " " * MAX_SKILL_NAME_LENGTH + ")"
        self.assertGreater(len(padded), MAX_SKILL_NAME_LENGTH)
        self.character.skills = {"riding": 1}
        self.character.save()

        requests = {
            "allocate_skill": {"skill": padded},
            "rename_skill": {"old_name": "riding", "new_name": padded},
            "skill_check": {"skill": padded},
        }
        for name, body in requests.items():
            with self.subTest(name):
                self.assert_refused(
                    self.post(name, body),
                    f"Skill name must be {MAX_SKILL_NAME_LENGTH} characters",
                )
        self.character.refresh_from_db()
        self.assertEqual(self.character.skills, {"riding": 1})

    def test_edge_whitespace_does_not_count_toward_the_limit(self):
        spaced = "q" * (MAX_SKILL_NAME_LENGTH - 2) + "   "
        self.character.skills = {"riding": 1}
        self.character.save()

        for name, body in {
            "allocate_skill": {"skill": spaced},
            "skill_check": {"skill": spaced},
            "rename_skill": {"old_name": "riding", "new_name": spaced},
        }.items():
            with self.subTest(name):
                response = self.post(name, body)
                self.assertNotIn(
                    limit_refusal_parts()[-1],
                    response.content.decode(),
                    response.content,
                )

    def test_a_held_name_past_the_limit_can_still_be_renamed(self):
        # A record from before #928 may hold one; renaming it is the repair.
        long_name = "q" * (MAX_SKILL_NAME_LENGTH + 10)
        Character.objects.filter(pk=self.character.pk).update(skills={long_name: 2})

        response = self.post(
            "rename_skill", {"old_name": long_name, "new_name": "riding"}
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.character.refresh_from_db()
        self.assertEqual(self.character.skills, {"riding": 2})

    def test_a_skill_renamed_past_the_limit_is_refused(self):
        self.character.skills = {"riding": 1}
        self.character.save()

        response = self.post(
            "rename_skill",
            {"old_name": "riding", "new_name": "q" * (MAX_SKILL_NAME_LENGTH + 1)},
        )

        self.assert_refused(
            response, f"Skill name must be {MAX_SKILL_NAME_LENGTH} characters"
        )
        self.character.refresh_from_db()
        self.assertEqual(self.character.skills, {"riding": 1})

    def test_custom_item_notes_past_the_limit_are_refused_not_cut(self):
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {
                "is_custom": True,
                "name": "Heirloom ring",
                "category": "gear",
                "weight": 0,
                "notes": "n" * (MAX_EQUIPMENT_NOTES_LENGTH + 1),
            },
        )

        self.assert_refused(
            response, f"Notes must be {MAX_EQUIPMENT_NOTES_LENGTH} characters"
        )
        self.character.refresh_from_db()
        self.assertEqual(self.character.equipment, [])

    def test_a_coin_reason_past_the_limit_is_refused_not_cut(self):
        response = self.post(
            "move_coin",
            {"amount": "5", "reason": "r" * (MAX_COIN_REASON_LENGTH + 1)},
        )

        self.assert_refused(
            response, f"Reason must be {MAX_COIN_REASON_LENGTH} characters"
        )
        self.character.refresh_from_db()
        self.assertEqual(self.character.coin_adjustment, 0)

    def test_the_inputs_carry_the_servers_limits(self):
        page = self.client.get(
            reverse("characters:edit", args=[self.character.pk])
        ).content.decode()

        self.assertIn(f'id="new-skill-name" maxlength="{MAX_SKILL_NAME_LENGTH}"', page)
        self.assertIn(
            f'id="custom-notes" maxlength="{MAX_EQUIPMENT_NOTES_LENGTH}"', page
        )


class DeathCauseLimitTest(BaseTestCase):
    def test_a_cause_past_the_limit_is_refused_not_cut(self):
        character = create_character(self.create_dm(), name="Gorn")

        with self.assertRaisesMessage(
            services.CharacterChangeError,
            too_long_refusal("Cause", MAX_DEATH_CAUSE_LENGTH),
        ):
            services.apply_death(character, "c" * (MAX_DEATH_CAUSE_LENGTH + 1))
        self.assertTrue(character.is_alive)


class ImportTextLimitsTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.character = create_character(self.dm, name="Gorn")

    def payload(self, **fields) -> dict:
        document = json.loads(generate_character_json(self.character))
        document["data"].update(fields)
        return document

    def import_as_game_master(self, document: dict) -> Character:
        imported = validate_import_payload(document)
        return save_imported_character(
            self.dm, held_to_player(imported, is_game_master=True)
        )

    def test_a_skill_at_the_limit_imports(self):
        skill = "q" * MAX_SKILL_NAME_LENGTH

        back = self.import_as_game_master(self.payload(skills={skill: 3}))

        self.assertEqual(back.skills, {skill: 3})

    def test_a_skill_one_past_the_limit_is_refused(self):
        document = self.payload(skills={"q" * (MAX_SKILL_NAME_LENGTH + 1): 3})

        with self.assertRaisesMessage(
            CharacterImportError,
            too_long_refusal("Skill name", MAX_SKILL_NAME_LENGTH),
        ):
            self.import_as_game_master(document)

    def test_a_skill_is_refused_by_its_received_length_not_its_stored_one(self):
        # Issue #52: this would store as "sword to hit", which fits.
        padded = "Sword (+1 to hit" + " " * MAX_SKILL_NAME_LENGTH + ")"

        with self.assertRaisesMessage(
            CharacterImportError,
            too_long_refusal("Skill name", MAX_SKILL_NAME_LENGTH),
        ):
            self.import_as_game_master(self.payload(skills={padded: 3}))

    def test_the_origin_skill_holds_any_skill_name(self):
        skill = "q" * MAX_SKILL_NAME_LENGTH

        back = self.import_as_game_master(
            self.payload(skills={skill: 1}, origin_skill=skill)
        )

        self.assertEqual(back.origin_skill, skill)

    def test_a_custom_damage_past_the_limit_is_refused(self):
        document = self.payload(
            equipment=[
                {
                    "item_id": "custom",
                    "name": "Starblade",
                    "category": "weapons",
                    "quantity": 1,
                    "damage": "1" * (MAX_EQUIPMENT_DAMAGE_LENGTH + 1),
                }
            ]
        )

        with self.assertRaisesMessage(
            CharacterImportError, "Equipment item 0 has an invalid damage"
        ):
            self.import_as_game_master(document)
