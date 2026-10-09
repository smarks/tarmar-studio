"""Tests for the equipment_bulk_remove endpoint and surrounding UI (issue #28)."""

from characters.models import Character
from common.test_helpers import BaseTestCase


class EquipmentBulkRemoveTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user("equip-owner")
        self.character = Character.objects.create(
            user=self.user,
            name="Inventory Test",
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
            equipment=[
                {"item_id": "dagger", "quantity": 1},
                {"item_id": "rapier", "quantity": 1},
                {"item_id": "broadsword", "quantity": 1},
                {"item_id": "backpack", "quantity": 1},
            ],
        )

    def _post_bulk_remove(self, indices):
        return self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/bulk-remove/",
            {"indices": indices},
        )

    def test_removes_multiple_items_in_one_call(self):
        self.login_as(self.user)
        response = self._post_bulk_remove([0, 2])
        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        remaining_ids = [item["item_id"] for item in self.character.equipment]
        self.assertEqual(remaining_ids, ["rapier", "backpack"])
        self.assertEqual(response.json()["removed"], 2)

    def test_handles_duplicate_indices_idempotently(self):
        self.login_as(self.user)
        response = self._post_bulk_remove([1, 1, 3])
        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        remaining_ids = [item["item_id"] for item in self.character.equipment]
        # Index 1 (rapier) removed once, index 3 (backpack) removed once.
        self.assertEqual(remaining_ids, ["dagger", "broadsword"])
        self.assertEqual(response.json()["removed"], 2)

    def test_rejects_out_of_range_index(self):
        self.login_as(self.user)
        response = self._post_bulk_remove([0, 99])
        self.assertEqual(response.status_code, 400)
        self.character.refresh_from_db()
        # All-or-nothing: the in-range removal didn't happen either.
        self.assertEqual(len(self.character.equipment), 4)

    def test_rejects_non_list_indices(self):
        self.login_as(self.user)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/bulk-remove/",
            {"indices": "not-a-list"},
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_empty_indices(self):
        self.login_as(self.user)
        response = self._post_bulk_remove([])
        self.assertEqual(response.status_code, 400)

    def test_rejects_non_owner(self):
        other = self.create_user("other-user")
        self.login_as(other)
        response = self._post_bulk_remove([0])
        self.assertEqual(response.status_code, 403)

    def test_dm_can_bulk_remove_on_other_user_character(self):
        dm = self.create_dm("equip-dm")
        self.login_as(dm)
        response = self._post_bulk_remove([0])
        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertEqual(len(self.character.equipment), 3)


class EquipmentControlsRenderTest(BaseTestCase):
    """The filter/sort/bulk-delete controls render on the character edit page."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user("controls-user")
        self.character = Character.objects.create(
            user=self.user,
            name="Controls Test",
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
        )

    def test_controls_bar_present(self):
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        body = response.content.decode()
        self.assertIn('id="equipment-filter"', body)
        self.assertIn('id="equipment-sort"', body)
        self.assertIn('id="equipment-bulk-delete"', body)
        # Sort options expose name, weight, category — match issue plan.
        for option_value in ("name", "weight_asc", "weight_desc", "category"):
            self.assertIn(f'value="{option_value}"', body)
