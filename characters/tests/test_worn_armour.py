"""Armour counts only when worn: equipped and out of the pack (issue #869).

Every reader of worn armour used to read ``equipped`` alone, so armour a
player had ticked both "equipped" and "in pack" kept its stops, DEX penalty,
movement restriction and battle tier in a dropped pack. The sweep covers each
reader (the sheet's sums, the movement restriction, the battle board's tier)
and each writer (the sheet's update and add, an import, the migration that
settles rows already stored).
"""

import importlib
import json

from django.apps import apps as django_apps
from django.urls import reverse

from battle.services import _armour_tier
from characters.equipment import (
    get_armor_movement_restrictions,
    is_worn_armour,
    settle_worn_armour,
)
from characters.import_service import validate_import_payload
from characters.models import Character
from common.test_helpers import BaseTestCase, create_character

MIGRATION = importlib.import_module(
    "characters.migrations.0023_worn_armour_out_of_the_pack"
)

PLATE_WORN_AND_PACKED = {
    "item_id": "plate_armor",
    "quantity": 1,
    "equipped": True,
    "in_pack": True,
}


class WornArmourReadersTest(BaseTestCase):
    """Each reader asks the one question: equipped and not in the pack."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("armour-owner")

    def test_armour_in_a_dropped_pack_counts_for_nothing(self):
        character = create_character(
            self.owner, equipment=[dict(PLATE_WORN_AND_PACKED)], pack_dropped=True
        )
        self.assertEqual(character.armor_stops, 0)
        self.assertEqual(character.armor_dex_penalty, 0)
        self.assertEqual(character.armor_stops_without_modifiers, 0)
        self.assertEqual(character.combat_dexterity, character.effective_dexterity)
        self.assertEqual(get_armor_movement_restrictions(character.equipment), [])
        self.assertEqual(_armour_tier(character), "None")

    def test_armour_in_a_carried_pack_is_not_worn_either(self):
        character = create_character(
            self.owner, equipment=[dict(PLATE_WORN_AND_PACKED)], pack_dropped=False
        )
        self.assertEqual(character.armor_stops, 0)
        self.assertEqual(_armour_tier(character), "None")

    def test_worn_armour_still_counts_everywhere(self):
        character = create_character(
            self.owner,
            equipment=[{"item_id": "plate_armor", "quantity": 1, "equipped": True}],
            pack_dropped=True,
        )
        self.assertEqual(character.armor_stops, 5)
        self.assertEqual(character.armor_dex_penalty, -5)
        self.assertEqual(
            get_armor_movement_restrictions(character.equipment), ["run", "sprint"]
        )
        self.assertEqual(_armour_tier(character), "Heavy")

    def test_custom_armour_is_read_the_same_way(self):
        custom = {
            "item_id": "custom",
            "name": "Bone mail",
            "category": "armor",
            "stops": 3,
            "dex_penalty": -1,
            "equipped": True,
            "in_pack": True,
        }
        self.assertFalse(is_worn_armour(custom))
        character = create_character(self.owner, equipment=[custom])
        self.assertEqual(character.armor_stops, 0)
        self.assertTrue(is_worn_armour(dict(custom, in_pack=False)))

    def test_settling_takes_worn_armour_out_and_leaves_the_rest(self):
        armour = dict(PLATE_WORN_AND_PACKED)
        settle_worn_armour(armour)
        self.assertEqual((armour["equipped"], armour["in_pack"]), (True, False))
        packed_armour = {"item_id": "chainmail", "in_pack": True}
        settle_worn_armour(packed_armour)
        self.assertTrue(packed_armour["in_pack"])
        rope = {"item_id": "rope_50ft", "equipped": True, "in_pack": True}
        settle_worn_armour(rope)
        self.assertTrue(rope["in_pack"])


class WornArmourWritersTest(BaseTestCase):
    """The sheet never stores armour both worn and packed; a player is held to it."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("armour-player")
        self.game_master = self.create_dm("armour-gm")
        self.character = create_character(self.owner)
        self.login_as(self.owner)

    def url(self, name: str) -> str:
        return reverse(f"characters:{name}", args=[self.character.pk])

    def carry(self, item: dict, *, pack_dropped: bool = False) -> None:
        self.character.equipment = [item]
        self.character.pack_dropped = pack_dropped
        self.character.save()

    def test_packing_worn_armour_takes_it_off(self):
        self.carry({"item_id": "chainmail", "quantity": 1, "equipped": True})
        data = self.post_json(
            self.url("equipment_update"), {"index": 0, "in_pack": True}
        ).json()
        self.assertTrue(data["equipment"][0]["in_pack"])
        self.assertFalse(data["equipment"][0]["equipped"])
        self.assertEqual(data["armor_stops"], 0)

    def test_putting_packed_armour_on_takes_it_out_of_the_pack(self):
        self.carry({"item_id": "chainmail", "quantity": 1, "in_pack": True})
        data = self.post_json(
            self.url("equipment_update"), {"index": 0, "equipped": True}
        ).json()
        self.assertTrue(data["equipment"][0]["equipped"])
        self.assertFalse(data["equipment"][0]["in_pack"])
        self.assertEqual(data["armor_stops"], 3)

    def test_a_player_cannot_put_on_armour_from_a_dropped_pack(self):
        self.carry(
            {"item_id": "chainmail", "quantity": 1, "in_pack": True},
            pack_dropped=True,
        )
        response = self.post_json(
            self.url("equipment_update"), {"index": 0, "equipped": True}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("dropped pack", response.json()["error"])
        self.character.refresh_from_db()
        self.assertFalse(self.character.equipment[0].get("equipped"))

    def test_a_game_master_may_put_it_on_from_a_dropped_pack(self):
        self.carry(
            {"item_id": "chainmail", "quantity": 1, "in_pack": True},
            pack_dropped=True,
        )
        self.logout()
        self.login_as(self.game_master)
        data = self.post_json(
            self.url("equipment_update"), {"index": 0, "equipped": True}
        ).json()
        self.assertFalse(data["equipment"][0]["in_pack"])
        self.assertEqual(data["armor_stops"], 3)

    def test_taking_armour_off_leaves_it_where_it_was(self):
        self.carry({"item_id": "chainmail", "quantity": 1, "equipped": True})
        data = self.post_json(
            self.url("equipment_update"), {"index": 0, "equipped": False}
        ).json()
        self.assertFalse(data["equipment"][0]["equipped"])
        self.assertFalse(data["equipment"][0].get("in_pack", False))

    def test_armour_added_worn_and_packed_is_stored_worn(self):
        self.logout()
        self.login_as(self.game_master)
        data = self.post_json(
            self.url("equipment_add"),
            {"item_id": "chainmail", "equipped": True, "in_pack": True, "free": True},
        ).json()
        added = data["equipment"][-1]
        self.assertEqual((added["equipped"], added["in_pack"]), (True, False))

    def test_an_import_settles_armour_marked_both(self):
        imported = validate_import_payload(
            {
                "_format": "tarmar-character",
                "_version": 1,
                "data": {"name": "Packed Plate", "equipment": [PLATE_WORN_AND_PACKED]},
            }
        )
        (armour,) = imported.fields["equipment"]
        self.assertEqual((armour["equipped"], armour["in_pack"]), (True, False))


class WornArmourMigrationTest(BaseTestCase):
    """The migration settles stored rows as worn, so nothing in play changes."""

    def test_it_clears_in_pack_on_equipped_armour_only(self):
        owner = self.create_user("armour-migrated")
        stored = [
            dict(PLATE_WORN_AND_PACKED),
            {"item_id": "leather_armor", "quantity": 1, "in_pack": True},
            {"item_id": "rope_50ft", "quantity": 1, "in_pack": True},
            {
                "item_id": "custom",
                "name": "Bone mail",
                "category": "armor",
                "equipped": True,
                "in_pack": True,
            },
        ]
        character = create_character(owner, equipment=json.loads(json.dumps(stored)))
        MIGRATION.take_worn_armour_out_of_the_pack(django_apps, None)
        character = Character.objects.get(pk=character.pk)
        self.assertEqual(
            [item.get("in_pack") for item in character.equipment],
            [False, True, True, False],
        )
        self.assertEqual(character.armor_stops, 5)
