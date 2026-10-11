"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

from django.test import TestCase

from characters.models import Character
from common.test_helpers import BaseTestCase, mark_gate_passed


class EquipmentCatalogTest(TestCase):
    """Tests for equipment catalog data."""

    def test_catalog_has_weapons(self):
        """Catalog has weapon entries."""
        from characters.equipment import WEAPONS

        self.assertIn("broadsword", WEAPONS)
        self.assertEqual(WEAPONS["broadsword"]["damage"], "2d6")
        self.assertEqual(WEAPONS["broadsword"]["weight"], 5.0)

    def test_catalog_has_armor(self):
        """Catalog has armor entries."""
        from characters.equipment import ARMOR

        self.assertIn("chainmail", ARMOR)
        self.assertEqual(ARMOR["chainmail"]["stops"], 3)
        self.assertEqual(ARMOR["chainmail"]["dex_penalty"], -3)

    def test_catalog_has_shields(self):
        """Catalog has shield entries."""
        from characters.equipment import SHIELDS

        self.assertIn("large_shield", SHIELDS)
        self.assertEqual(SHIELDS["large_shield"]["stops"], 2)

    def test_catalog_has_gear(self):
        """Catalog has gear entries."""
        from characters.equipment import GEAR

        self.assertIn("backpack", GEAR)
        self.assertEqual(GEAR["backpack"]["weight"], 2.0)

    def test_get_item_returns_item(self):
        """get_item returns item data."""
        from characters.equipment import get_item

        item = get_item("broadsword")
        self.assertIsNotNone(item)
        assert item is not None  # Type narrowing for pyright
        self.assertEqual(item["name"], "Broadsword")

    def test_get_item_returns_none_for_missing(self):
        """get_item returns None for missing items."""
        from characters.equipment import get_item

        self.assertIsNone(get_item("nonexistent_item"))

    def test_encumbrance_calculation(self):
        """Encumbrance level calculation."""
        from characters.equipment import calculate_encumbrance_level

        self.assertEqual(calculate_encumbrance_level(10, 15), "unencumbered")
        self.assertEqual(calculate_encumbrance_level(15, 15), "unencumbered")
        self.assertEqual(calculate_encumbrance_level(20, 15), "light")
        self.assertEqual(calculate_encumbrance_level(25, 15), "medium")
        self.assertEqual(calculate_encumbrance_level(35, 15), "heavy")
        self.assertEqual(calculate_encumbrance_level(40, 15), "overloaded")


class EquipmentModelTest(BaseTestCase):
    """Tests for equipment model properties."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.character = Character.objects.create(
            user=self.user,
            name="Test Character",
            strength=15,
            dexterity=12,
            intelligence=10,
            wisdom=13,
            constitution=14,
            charisma=8,
            appearance_roll="3",
            height_roll="4",
            weight_roll="3",
            fatigue_roll=7,
            equipment=[],
        )

    def test_empty_equipment_weight(self):
        """Empty equipment has zero weight."""
        self.assertEqual(self.character.total_equipment_weight, 0.0)
        self.assertEqual(self.character.carried_weight, 0.0)

    def test_equipment_weight_calculation(self):
        """Equipment weight is calculated correctly."""
        self.character.equipment = [
            {"item_id": "broadsword", "quantity": 1, "in_pack": False},
            {"item_id": "backpack", "quantity": 1, "in_pack": False},
        ]
        self.character.save()
        # Broadsword: 5.0 lbs, Backpack: 2.0 lbs
        self.assertEqual(self.character.total_equipment_weight, 7.0)

    def test_quantity_multiplies_weight(self):
        """Quantity multiplies item weight."""
        self.character.equipment = [
            {"item_id": "torch", "quantity": 5, "in_pack": True},
        ]
        self.character.save()
        # Torch: 1.0 lbs * 5 = 5.0 lbs
        self.assertEqual(self.character.total_equipment_weight, 5.0)

    def test_pack_weight_calculation(self):
        """Pack weight only includes in_pack items."""
        self.character.equipment = [
            {"item_id": "broadsword", "quantity": 1, "in_pack": False},
            {"item_id": "bedroll", "quantity": 1, "in_pack": True},
        ]
        self.character.save()
        self.assertEqual(self.character.pack_weight, 5.0)  # Bedroll only

    def test_carried_weight_with_pack_dropped(self):
        """Carried weight excludes pack when dropped."""
        self.character.equipment = [
            {"item_id": "broadsword", "quantity": 1, "in_pack": False},
            {"item_id": "bedroll", "quantity": 1, "in_pack": True},
        ]
        self.character.pack_dropped = True
        self.character.save()
        # Total: 5 + 5 = 10, Pack: 5, Carried = 10 - 5 = 5
        self.assertEqual(self.character.carried_weight, 5.0)

    def test_encumbrance_level_property(self):
        """Encumbrance level is calculated from carried weight and STR."""
        # STR 15, unencumbered up to 15 lbs
        self.assertEqual(self.character.encumbrance_level, "unencumbered")

        self.character.equipment = [
            {"item_id": "chainmail", "quantity": 1, "in_pack": False},
        ]
        self.character.save()
        # Chainmail: 30 lbs, STR 15 -> medium (22.5 < 30 <= 30)
        self.assertEqual(self.character.carried_weight, 30.0)
        self.assertEqual(self.character.encumbrance_level, "medium")

    def test_armor_stops_calculation(self):
        """Armor stops are summed from equipped armor."""
        self.character.equipment = [
            {"item_id": "chainmail", "quantity": 1, "in_pack": False, "equipped": True},
            {"item_id": "small_shield", "quantity": 1, "in_pack": False},
        ]
        self.character.save()
        # Chainmail: 3 stops (equipped), Small shield: 1 stop
        self.assertEqual(self.character.armor_stops, 4)

    def test_armor_stops_with_modifier(self):
        """Armor stops include modifier."""
        self.character.equipment = [
            {
                "item_id": "chainmail",
                "quantity": 1,
                "in_pack": False,
                "stops_mod": 1,
                "equipped": True,
            },
        ]
        self.character.save()
        self.assertEqual(self.character.armor_stops, 4)  # 3 + 1

    def test_armor_dex_penalty(self):
        """Armor DEX penalty is summed from equipped armor."""
        self.character.equipment = [
            {"item_id": "chainmail", "quantity": 1, "in_pack": False, "equipped": True},
            {"item_id": "large_shield", "quantity": 1, "in_pack": False},
        ]
        self.character.save()
        # Chainmail: -3 (equipped), Large shield: -1
        self.assertEqual(self.character.armor_dex_penalty, -4)

    def test_unequipped_armor_not_counted(self):
        """Unequipped armor doesn't contribute to stats."""
        self.character.equipment = [
            {
                "item_id": "chainmail",
                "quantity": 1,
                "in_pack": False,
                "equipped": False,
            },
            {"item_id": "small_shield", "quantity": 1, "in_pack": False},
        ]
        self.character.save()
        # Only shield counts (1 stop, 0 dex penalty)
        self.assertEqual(self.character.armor_stops, 1)
        self.assertEqual(self.character.armor_dex_penalty, 0)

    def test_weapon_modifiers(self):
        """Weapon modifiers are summed."""
        self.character.equipment = [
            {
                "item_id": "broadsword",
                "quantity": 1,
                "in_pack": False,
                "to_hit_mod": 2,
                "damage_mod": 1,
                "critical_mod": -1,
            },
        ]
        self.character.save()
        self.assertEqual(self.character.weapon_to_hit_mod, 2)
        self.assertEqual(self.character.weapon_damage_mod, 1)
        self.assertEqual(self.character.weapon_critical_mod, -1)

    def test_custom_weapon_modifiers_count_in_totals(self):
        """Custom weapon modifiers count toward totals (issue #135)."""
        self.character.equipment = [
            {
                "item_id": "custom",
                "name": "Magic Dagger",
                "category": "weapons",
                "quantity": 1,
                "in_pack": False,
                "to_hit_mod": 5,
                "damage_mod": 3,
            },
        ]
        self.character.save()
        self.assertEqual(self.character.weapon_to_hit_mod, 5)
        self.assertEqual(self.character.weapon_damage_mod, 3)

    def test_custom_gear_item_mod_fields_not_counted(self):
        """A custom gear item with stray mod fields doesn't count as a weapon."""
        self.character.equipment = [
            {
                "item_id": "custom",
                "name": "Lucky Charm",
                "category": "gear",
                "quantity": 1,
                "in_pack": False,
                "to_hit_mod": 5,
                "damage_mod": 3,
            },
        ]
        self.character.save()
        self.assertEqual(self.character.weapon_to_hit_mod, 0)
        self.assertEqual(self.character.weapon_damage_mod, 0)

    def test_movement_restrictions_none_when_unarmored_and_unencumbered(self):
        """No armor, no encumbrance -> no movement restriction."""
        self.assertEqual(self.character.movement_restrictions, [])
        self.assertEqual(self.character.movement_restriction_label, "")

    def test_movement_restrictions_leather_cannot_sprint(self):
        """Leather armor alone restricts sprinting only (issue #128)."""
        # A very strong character, so 16 lbs of leather stays unencumbered and
        # the restriction below comes from the armor, not the carried weight.
        self.character.strength = 25
        self.character.equipment = [
            {
                "item_id": "leather_armor",
                "quantity": 1,
                "in_pack": False,
                "equipped": True,
            },
        ]
        self.character.save()
        self.assertEqual(self.character.encumbrance_level, "unencumbered")
        self.assertEqual(self.character.movement_restrictions, ["sprint"])
        self.assertEqual(
            self.character.movement_restriction_label, "Cannot Sprint (Leather Armor)"
        )

    def test_movement_restrictions_plate_cannot_run_or_sprint(self):
        """Plate armor alone restricts running and sprinting (issue #128)."""
        # Strong enough that 55 lbs of plate stays unencumbered.
        self.character.strength = 60
        self.character.equipment = [
            {
                "item_id": "plate_armor",
                "quantity": 1,
                "in_pack": False,
                "equipped": True,
            },
        ]
        self.character.save()
        self.assertEqual(self.character.encumbrance_level, "unencumbered")
        self.assertEqual(self.character.movement_restrictions, ["run", "sprint"])
        self.assertEqual(
            self.character.movement_restriction_label,
            "Cannot Run or Sprint (Plate Armor)",
        )

    def test_movement_restrictions_combine_encumbrance_and_armor(self):
        """The most restrictive combination of encumbrance and armor applies."""
        self.character.equipment = [
            # Leather (sprint only) plus enough gear to reach medium
            # encumbrance (run + sprint) at STR 15.
            {
                "item_id": "leather_armor",
                "quantity": 1,
                "in_pack": False,
                "equipped": True,
            },
            {
                "item_id": "custom",
                "name": "Lead weights",
                "quantity": 1,
                "weight": 10,
                "in_pack": False,
            },
        ]
        self.character.save()
        self.assertEqual(self.character.carried_weight, 26.0)
        self.assertEqual(self.character.encumbrance_level, "medium")
        # Medium encumbrance already forbids run+sprint, a superset of what
        # leather forbids alone -- the union still comes out to run+sprint.
        self.assertEqual(self.character.movement_restrictions, ["run", "sprint"])

    def test_movement_restrictions_unequipped_armor_imposes_nothing(self):
        """Armor sitting in inventory, not worn, doesn't restrict movement."""
        self.character.strength = 60  # stays unencumbered under the plate
        self.character.equipment = [
            {
                "item_id": "plate_armor",
                "quantity": 1,
                "in_pack": False,
                "equipped": False,
            },
        ]
        self.character.save()
        self.assertEqual(self.character.movement_restrictions, [])
        self.assertEqual(self.character.movement_restriction_label, "")


class EquipmentViewsTest(BaseTestCase):
    """Tests for equipment view endpoints."""

    def as_game_master(self):
        """A custom weapon, armour or shield is a Game Master's to add (#845)."""
        self.logout()
        self.login_as(self.create_dm())

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.character = Character.objects.create(
            user=self.user,
            name="Test Character",
            strength=15,
            dexterity=12,
            intelligence=10,
            wisdom=13,
            constitution=14,
            charisma=8,
            appearance_roll="3",
            height_roll="4",
            weight_roll="3",
            fatigue_roll=7,
            equipment=[],
            # Catalog items are bought (issue #757); these tests are about
            # the inventory, so the purse covers anything they add. The
            # purchases themselves are tested in test_purse.py.
            starting_coin=100_000,
        )
        self.login_as(self.user)

    def test_equipment_catalog_requires_login(self):
        """Equipment catalog requires authentication."""
        self.logout()
        response = self.client.get("/tarmar/characters/equipment/catalog/")
        self.assertEqual(response.status_code, 302)

    def test_equipment_catalog_returns_data(self):
        """Equipment catalog returns all categories."""
        response = self.client.get("/tarmar/characters/equipment/catalog/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("weapons", data)
        self.assertIn("armor", data)
        self.assertIn("shields", data)
        self.assertIn("gear", data)

    def test_add_equipment(self):
        """Add equipment to character."""
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "broadsword"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["equipment"]), 1)
        self.assertEqual(data["equipment"][0]["item_id"], "broadsword")

    def test_add_equipment_with_quantity(self):
        """Add equipment with quantity."""
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "torch", "quantity": 5, "in_pack": True},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["equipment"][0]["quantity"], 5)
        self.assertTrue(data["equipment"][0]["in_pack"])

    def test_add_equipment_invalid_item(self):
        """Adding invalid item fails."""
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "nonexistent"},
        )
        self.assertEqual(response.status_code, 400)

    def test_equipment_responses_carry_the_attack_weapon_choices(self):
        """Add, update and remove all return the Attack Resolution panel's
        weapons, so the page refreshes the select without a reload (issue
        #339, item 24). The list is the same one the edit page renders."""
        from characters.views.characters import attack_weapon_choices

        base = f"/tarmar/characters/{self.character.pk}/equipment"
        added = self.post_json(f"{base}/add/", {"item_id": "backpack"}).json()
        self.assertEqual(added["attack_weapons"], [])
        added = self.post_json(f"{base}/add/", {"item_id": "saber"}).json()
        self.assertEqual(
            added["attack_weapons"],
            [
                {
                    "index": 1,
                    "item_id": "saber",
                    "name": "Saber",
                    "weapon_class": "Striking",
                    "damage": "2d6-2",
                    # Added for #846: the only weapon carried is in hand.
                    "in_hand": True,
                }
            ],
        )
        added = self.post_json(f"{base}/add/", {"item_id": "club"}).json()
        self.assertEqual(added["attack_gm_adjudicated_weapons"], ["Club"])

        updated = self.post_json(f"{base}/update/", {"index": 0, "quantity": 2}).json()
        self.character.refresh_from_db()
        weapons, gm_adjudicated = attack_weapon_choices(self.character)
        self.assertEqual(updated["attack_weapons"], weapons)
        self.assertEqual(updated["attack_gm_adjudicated_weapons"], gm_adjudicated)

        removed = self.post_json(f"{base}/remove/", {"index": 0}).json()
        self.assertEqual(
            [
                (weapon["index"], weapon["item_id"])
                for weapon in removed["attack_weapons"]
            ],
            [(0, "saber")],
        )

    def test_update_equipment_quantity(self):
        """Update equipment quantity."""
        self.character.equipment = [
            {"item_id": "torch", "quantity": 1, "in_pack": False, "notes": ""}
        ]
        self.character.save()

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/update/",
            {"index": 0, "quantity": 10},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["equipment"][0]["quantity"], 10)

    def test_update_equipment_in_pack(self):
        """Update equipment in_pack status."""
        self.character.equipment = [
            {"item_id": "bedroll", "quantity": 1, "in_pack": False, "notes": ""}
        ]
        self.character.save()

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/update/",
            {"index": 0, "in_pack": True},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["equipment"][0]["in_pack"])

    def test_update_weapon_modifiers(self):
        """Update weapon modifiers."""
        # Item modifiers are a Game Master's grant (issue #752).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("equipment-gm"))
        self.character.equipment = [
            {"item_id": "broadsword", "quantity": 1, "in_pack": False, "notes": ""}
        ]
        self.character.save()

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/update/",
            {"index": 0, "to_hit_mod": 2, "damage_mod": 1},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["weapon_to_hit_mod"], 2)
        self.assertEqual(data["weapon_damage_mod"], 1)

    def test_remove_equipment(self):
        """Remove equipment from character."""
        self.character.equipment = [
            {"item_id": "broadsword", "quantity": 1, "in_pack": False, "notes": ""},
            {"item_id": "torch", "quantity": 5, "in_pack": True, "notes": ""},
        ]
        self.character.save()

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/remove/",
            {"index": 0},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["equipment"]), 1)
        self.assertEqual(data["equipment"][0]["item_id"], "torch")

    def test_toggle_pack(self):
        """Toggle pack dropped status."""
        self.character.equipment = [
            {"item_id": "bedroll", "quantity": 1, "in_pack": True, "notes": ""}
        ]
        self.character.save()

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/toggle-pack/",
            {},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["pack_dropped"])
        self.assertEqual(data["carried_weight"], 0.0)  # Pack items excluded

    def test_encumbrance_in_response(self):
        """Response includes encumbrance data."""
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "chainmail"},
        )
        data = response.json()
        self.assertIn("encumbrance_level", data)
        self.assertIn("movement_penalty", data)
        self.assertIn("carried_weight", data)
        self.assertIn("armor_stops", data)
        self.assertIn("movement_modifier", data)
        self.assertIn("gait_readout", data)

    def test_the_response_restates_aim_and_dodge(self):
        """Issue #634: equipping armour moves both, and neither was carried.

        Chainmail's DEX penalty drags combat DEX down, so the two tiles the
        page shows beside "Armor DEX Penalty" must move with it rather than
        keep the numbers the template rendered at page load.
        """
        before = self.character.dex_modifier

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "chainmail", "equipped": True},
        )

        data = response.json()
        self.assertEqual(data["dex_modifier"], self.reloaded().dex_modifier)
        self.assertEqual(data["dodge_modifier"], self.reloaded().dodge_modifier)
        self.assertNotEqual(data["armor_dex_penalty"], 0)
        self.assertLess(data["dex_modifier"], before)

    def reloaded(self) -> Character:
        """This test case's character, read back from the database."""
        return Character.objects.get(pk=self.character.pk)

    def test_cannot_edit_other_user_equipment(self):
        """Cannot modify another user's character equipment."""
        other_user = self.create_user(username="other")
        other_char = Character.objects.create(
            user=other_user,
            name="Other Char",
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

        response = self.post_json(
            f"/tarmar/characters/{other_char.pk}/equipment/add/",
            {"item_id": "broadsword"},
        )
        self.assertEqual(response.status_code, 403)

    def test_add_custom_item(self):
        """Add a custom item to character."""
        self.as_game_master()
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {
                "is_custom": True,
                "name": "Magic Sword",
                "category": "weapons",
                "weight": 3.5,
                "cost": 500,
                "damage": "2d8",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["equipment"][0]["item_id"], "custom")
        self.assertEqual(data["equipment"][0]["name"], "Magic Sword")
        self.assertEqual(data["equipment"][0]["weight"], 3.5)

    def test_custom_item_weight_calculation(self):
        """Custom item weight is included in totals."""
        self.character.equipment = [
            {
                "item_id": "custom",
                "name": "Heavy Thing",
                "category": "gear",
                "weight": 25.0,
                "quantity": 2,
                "in_pack": False,
            }
        ]
        self.character.save()
        self.assertEqual(self.character.total_equipment_weight, 50.0)

    def test_equip_armor_exclusivity(self):
        """Only one armor can be equipped at a time."""
        # Add first armor and equip it
        self.character.equipment = [
            {"item_id": "chainmail", "quantity": 1, "in_pack": False, "equipped": True}
        ]
        self.character.save()

        # Add second armor and equip it
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "plate_armor", "equipped": True},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # First armor should be unequipped
        self.assertFalse(data["equipment"][0]["equipped"])
        # Second armor should be equipped
        self.assertTrue(data["equipment"][1]["equipped"])

    def test_update_armor_equipped_unequips_others(self):
        """Equipping armor via update unequips other armor."""
        self.character.equipment = [
            {"item_id": "chainmail", "quantity": 1, "in_pack": False, "equipped": True},
            {
                "item_id": "leather_armor",
                "quantity": 1,
                "in_pack": False,
                "equipped": False,
            },
        ]
        self.character.save()

        # Equip the second armor
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/update/",
            {"index": 1, "equipped": True},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # First armor unequipped, second equipped
        self.assertFalse(data["equipment"][0]["equipped"])
        self.assertTrue(data["equipment"][1]["equipped"])

    def test_add_custom_item_empty_name_fails(self):
        """Custom item requires a name."""
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"is_custom": True, "name": "", "category": "gear", "weight": 1.0},
        )
        self.assertEqual(response.status_code, 400)

    def test_add_custom_item_name_too_long_fails(self):
        """Custom item name cannot exceed 100 characters."""
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"is_custom": True, "name": "x" * 101, "category": "gear", "weight": 1.0},
        )
        self.assertEqual(response.status_code, 400)

    def test_add_custom_item_non_string_name_fails_cleanly(self):
        """A non-string name (e.g. a JSON number) returns 400, not a 500.

        Regression test for issue #148: name used to be validated with a bare
        ``.strip()`` that assumed a string; it's now coerced via ``str()``
        before validation, so a number/list/etc. produces a clean 400 instead
        of an AttributeError.
        """
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"is_custom": True, "name": 123, "category": "gear", "weight": 1.0},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["equipment"][0]["name"], "123")

    def test_add_custom_item_invalid_category_fails(self):
        """Custom item requires valid category."""
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"is_custom": True, "name": "Test", "category": "invalid", "weight": 1.0},
        )
        self.assertEqual(response.status_code, 400)

    def test_add_custom_item_invalid_weight_fails(self):
        """Custom item weight must be valid."""
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"is_custom": True, "name": "Test", "category": "gear", "weight": "abc"},
        )
        self.assertEqual(response.status_code, 400)

    def test_add_custom_item_weight_too_high_fails(self):
        """Custom item weight cannot exceed MAX_EQUIPMENT_WEIGHT_LBS (#938)."""
        from characters.constants import MAX_EQUIPMENT_WEIGHT_LBS

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {
                "is_custom": True,
                "name": "Test",
                "category": "gear",
                "weight": MAX_EQUIPMENT_WEIGHT_LBS + 1,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(str(MAX_EQUIPMENT_WEIGHT_LBS), response.json()["error"])

    def test_add_custom_item_invalid_cost_fails(self):
        """Custom item cost must be valid."""
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {
                "is_custom": True,
                "name": "Test",
                "category": "gear",
                "weight": 1.0,
                "cost": "abc",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_add_custom_item_negative_cost_fails(self):
        """Custom item cost cannot be negative."""
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {
                "is_custom": True,
                "name": "Test",
                "category": "gear",
                "weight": 1.0,
                "cost": -10,
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_add_custom_weapon_with_modifiers(self):
        """Custom weapon can have combat modifiers."""
        # Item modifiers are a Game Master's grant (issue #752).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("equipment-gm"))
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {
                "is_custom": True,
                "name": "Magic Dagger",
                "category": "weapons",
                "weight": 1.0,
                "damage": "1d4+2",
                "to_hit_mod": 2,
                "damage_mod": 1,
                "critical_mod": 1,
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["equipment"][0]["to_hit_mod"], 2)
        self.assertEqual(data["equipment"][0]["damage_mod"], 1)

    def test_add_custom_weapon_modifiers_count_in_totals(self):
        """Custom weapon modifiers are reflected in response totals (issue #135)."""
        # Item modifiers are a Game Master's grant (issue #752).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("equipment-gm"))
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {
                "is_custom": True,
                "name": "Magic Dagger",
                "category": "weapons",
                "weight": 1.0,
                "damage": "1d4+2",
                "to_hit_mod": 5,
                "damage_mod": 3,
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["weapon_to_hit_mod"], 5)
        self.assertEqual(data["weapon_damage_mod"], 3)

    def test_update_custom_weapon_modifiers(self):
        """Editing a custom weapon's modifiers works and re-totals (issue #135)."""
        # Item modifiers are a Game Master's grant (issue #752).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("equipment-gm"))
        self.character.equipment = [
            {
                "item_id": "custom",
                "name": "Magic Dagger",
                "category": "weapons",
                "quantity": 1,
                "in_pack": False,
                "notes": "",
                "to_hit_mod": 1,
                "damage_mod": 0,
            },
        ]
        self.character.save()

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/update/",
            {"index": 0, "to_hit_mod": 5, "damage_mod": 3},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["equipment"][0]["to_hit_mod"], 5)
        self.assertEqual(data["equipment"][0]["damage_mod"], 3)
        self.assertEqual(data["weapon_to_hit_mod"], 5)
        self.assertEqual(data["weapon_damage_mod"], 3)

    def test_update_custom_weapon_modifier_invalid_fails(self):
        """Editing a custom weapon's modifiers validates like catalog weapons."""
        # Item modifiers are a Game Master's grant (issue #752).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("equipment-gm"))
        self.character.equipment = [
            {
                "item_id": "custom",
                "name": "Magic Dagger",
                "category": "weapons",
                "quantity": 1,
                "in_pack": False,
                "notes": "",
            },
        ]
        self.character.save()

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/update/",
            {"index": 0, "to_hit_mod": 15},
        )
        self.assertEqual(response.status_code, 400)

    def test_add_custom_weapon_modifier_too_high_fails(self):
        """Custom weapon modifiers cannot exceed +/- 10."""
        # Item modifiers are a Game Master's grant (issue #752).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("equipment-gm"))
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {
                "is_custom": True,
                "name": "OP Sword",
                "category": "weapons",
                "weight": 1.0,
                "to_hit_mod": 15,
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_add_custom_armor_with_stats(self):
        """Custom armor can have stops and dex penalty."""
        self.as_game_master()
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {
                "is_custom": True,
                "name": "Magic Armor",
                "category": "armor",
                "weight": 20.0,
                "stops": 4,
                "dex_penalty": -2,
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["equipment"][0]["stops"], 4)
        self.assertEqual(data["equipment"][0]["dex_penalty"], -2)

    def test_add_custom_armor_stops_too_high_fails(self):
        """Custom armor stops cannot exceed 10."""
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {
                "is_custom": True,
                "name": "OP Armor",
                "category": "armor",
                "weight": 20.0,
                "stops": 15,
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_add_custom_armor_dex_penalty_invalid_fails(self):
        """Custom armor dex penalty must be 0 to -10."""
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {
                "is_custom": True,
                "name": "Bad Armor",
                "category": "armor",
                "weight": 20.0,
                "dex_penalty": 5,  # Should be negative or 0
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_add_custom_armor_modifier_too_high_fails(self):
        """Custom armor modifiers cannot exceed +/- 10."""
        # Item modifiers are a Game Master's grant (issue #752).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("equipment-gm"))
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {
                "is_custom": True,
                "name": "OP Armor",
                "category": "armor",
                "weight": 20.0,
                "stops_mod": 15,
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_add_catalog_item_invalid_quantity_fails(self):
        """Catalog item quantity must be at least 1."""
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "torch", "quantity": 0},
        )
        self.assertEqual(response.status_code, 400)

    def test_add_catalog_item_quantity_too_high_fails(self):
        """Catalog item quantity cannot exceed MAX_EQUIPMENT_QUANTITY (#938)."""
        from characters.constants import MAX_EQUIPMENT_QUANTITY

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "torch", "quantity": MAX_EQUIPMENT_QUANTITY + 1},
        )
        self.assertEqual(response.status_code, 400)

    def test_add_catalog_item_invalid_quantity_type_fails(self):
        """Catalog item quantity must be a number."""
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "torch", "quantity": "abc"},
        )
        self.assertEqual(response.status_code, 400)

    def test_add_catalog_item_notes_too_long_fails(self):
        """Catalog item notes cannot exceed 200 characters."""
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "torch", "notes": "x" * 201},
        )
        self.assertEqual(response.status_code, 400)

    def test_add_catalog_weapon_with_modifiers(self):
        """Catalog weapon can have combat modifiers."""
        # Item modifiers are a Game Master's grant (issue #752).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("equipment-gm"))
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "broadsword", "to_hit_mod": 1, "damage_mod": 2},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["equipment"][0].get("to_hit_mod"), 1)
        self.assertEqual(data["equipment"][0].get("damage_mod"), 2)

    def test_add_catalog_weapon_modifier_too_high_fails(self):
        """Catalog weapon modifiers cannot exceed +/- 10."""
        # Item modifiers are a Game Master's grant (issue #752).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("equipment-gm"))
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "broadsword", "to_hit_mod": 15},
        )
        self.assertEqual(response.status_code, 400)

    def test_add_catalog_weapon_modifier_invalid_type_fails(self):
        """Catalog weapon modifiers must be numbers."""
        # Item modifiers are a Game Master's grant (issue #752).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("equipment-gm"))
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "broadsword", "to_hit_mod": "abc"},
        )
        self.assertEqual(response.status_code, 400)

    def test_add_catalog_armor_with_modifiers(self):
        """Catalog armor can have modifiers."""
        # Item modifiers are a Game Master's grant (issue #752).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("equipment-gm"))
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "chainmail", "stops_mod": 1, "dex_mod": 1},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["equipment"][0].get("stops_mod"), 1)
        self.assertEqual(data["equipment"][0].get("dex_mod"), 1)

    def test_add_catalog_armor_modifier_too_high_fails(self):
        """Catalog armor modifiers cannot exceed +/- 10."""
        # Item modifiers are a Game Master's grant (issue #752).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("equipment-gm"))
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "chainmail", "stops_mod": 15},
        )
        self.assertEqual(response.status_code, 400)

    def test_add_catalog_armor_modifier_invalid_type_fails(self):
        """Catalog armor modifiers must be numbers."""
        # Item modifiers are a Game Master's grant (issue #752).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("equipment-gm"))
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "chainmail", "stops_mod": "abc"},
        )
        self.assertEqual(response.status_code, 400)

    def test_update_equipment_invalid_index_fails(self):
        """Update with invalid index fails."""
        self.character.equipment = [
            {"item_id": "torch", "quantity": 1, "in_pack": False, "notes": ""}
        ]
        self.character.save()

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/update/",
            {"index": 5, "quantity": 10},
        )
        self.assertEqual(response.status_code, 400)

    def test_update_equipment_negative_index_fails(self):
        """Update with negative index fails."""
        self.character.equipment = [
            {"item_id": "torch", "quantity": 1, "in_pack": False, "notes": ""}
        ]
        self.character.save()

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/update/",
            {"index": -1, "quantity": 10},
        )
        self.assertEqual(response.status_code, 400)

    def test_update_equipment_invalid_quantity_fails(self):
        """Update with invalid quantity fails."""
        self.character.equipment = [
            {"item_id": "torch", "quantity": 1, "in_pack": False, "notes": ""}
        ]
        self.character.save()

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/update/",
            {"index": 0, "quantity": 0},
        )
        self.assertEqual(response.status_code, 400)

    def test_update_equipment_notes_too_long_fails(self):
        """Update with notes too long fails."""
        self.character.equipment = [
            {"item_id": "torch", "quantity": 1, "in_pack": False, "notes": ""}
        ]
        self.character.save()

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/update/",
            {"index": 0, "notes": "x" * 201},
        )
        self.assertEqual(response.status_code, 400)

    def test_update_armor_modifiers(self):
        """Update armor modifiers works."""
        # Item modifiers are a Game Master's grant (issue #752).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("equipment-gm"))
        self.character.equipment = [
            {"item_id": "chainmail", "quantity": 1, "in_pack": False, "notes": ""}
        ]
        self.character.save()

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/update/",
            {"index": 0, "stops_mod": 2, "dex_mod": 1},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["equipment"][0].get("stops_mod"), 2)

    def test_update_armor_modifier_invalid_fails(self):
        """Update armor with invalid modifier fails."""
        # Item modifiers are a Game Master's grant (issue #752).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("equipment-gm"))
        self.character.equipment = [
            {"item_id": "chainmail", "quantity": 1, "in_pack": False, "notes": ""}
        ]
        self.character.save()

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/update/",
            {"index": 0, "stops_mod": 15},
        )
        self.assertEqual(response.status_code, 400)

    def test_update_weapon_modifier_invalid_fails(self):
        """Update weapon with invalid modifier fails."""
        # Item modifiers are a Game Master's grant (issue #752).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("equipment-gm"))
        self.character.equipment = [
            {"item_id": "broadsword", "quantity": 1, "in_pack": False, "notes": ""}
        ]
        self.character.save()

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/update/",
            {"index": 0, "to_hit_mod": 15},
        )
        self.assertEqual(response.status_code, 400)

    def test_remove_equipment_invalid_index_fails(self):
        """Remove with invalid index fails."""
        self.character.equipment = [
            {"item_id": "torch", "quantity": 1, "in_pack": False, "notes": ""}
        ]
        self.character.save()

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/remove/",
            {"index": 5},
        )
        self.assertEqual(response.status_code, 400)

    def test_remove_equipment_missing_index_fails(self):
        """Remove without index fails."""
        self.character.equipment = [
            {"item_id": "torch", "quantity": 1, "in_pack": False, "notes": ""}
        ]
        self.character.save()

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/remove/",
            {},
        )
        self.assertEqual(response.status_code, 400)

    def test_add_equipment_marks_character_played(self):
        """Adding equipment marks character as played."""
        self.character.is_played = False
        self.character.save()

        self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "torch"},
        )

        self.character.refresh_from_db()
        self.assertTrue(self.character.is_played)

    def test_update_equipment_marks_character_played(self):
        """Updating equipment marks character as played."""
        self.character.equipment = [
            {"item_id": "torch", "quantity": 1, "in_pack": False, "notes": ""}
        ]
        self.character.is_played = False
        self.character.save()

        self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/update/",
            {"index": 0, "quantity": 5},
        )

        self.character.refresh_from_db()
        self.assertTrue(self.character.is_played)

    def test_remove_equipment_marks_character_played(self):
        """Removing equipment marks character as played."""
        self.character.equipment = [
            {"item_id": "torch", "quantity": 1, "in_pack": False, "notes": ""}
        ]
        self.character.is_played = False
        self.character.save()

        self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/remove/",
            {"index": 0},
        )

        self.character.refresh_from_db()
        self.assertTrue(self.character.is_played)

    def test_toggle_pack_marks_character_played(self):
        """Toggling pack marks character as played."""
        self.character.is_played = False
        self.character.save()

        self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/toggle-pack/",
            {},
        )

        self.character.refresh_from_db()
        self.assertTrue(self.character.is_played)

    def test_dm_adding_equipment_does_not_mark_played(self):
        """A DM editing someone else's equipment doesn't flip their "Played"
        status (issue #91): character_update_field has always drawn this
        line -- "played" means the owner touched the sheet, not a DM fixing
        it up -- but the equipment endpoints skipped the same ownership
        check and set is_played unconditionally. Mirrors
        test_add_equipment_marks_character_played, but as the DM."""
        dm = self.create_dm()
        self.character.is_played = False
        self.character.save()

        # setUp already logged in as self.user; Client.login() replaces the
        # session outright (it doesn't just add auth keys to the existing
        # one), which drops the passphrase-gate flag BaseTestCase.setUp set --
        # re-apply it exactly as setUp does, or the gate intercepts this
        # second login with its own 200 page before the view ever runs.
        self.login_as(dm)
        mark_gate_passed(self.client)

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "torch"},
        )
        self.assertEqual(response.status_code, 200)

        self.character.refresh_from_db()
        self.assertFalse(self.character.is_played)

    def test_add_custom_weapon_invalid_modifier_defaults_zero(self):
        """Custom weapon with non-numeric modifier defaults to zero."""
        # Item modifiers are a Game Master's grant (issue #752).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("equipment-gm"))
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {
                "is_custom": True,
                "name": "Test Sword",
                "category": "weapons",
                "weight": 3.0,
                "to_hit_mod": "abc",  # Invalid - should default to 0
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["equipment"][0]["to_hit_mod"], 0)

    def test_add_custom_armor_invalid_stops_defaults_zero(self):
        """Custom armor with non-numeric stops defaults to zero."""
        self.as_game_master()
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {
                "is_custom": True,
                "name": "Test Armor",
                "category": "armor",
                "weight": 20.0,
                "stops": "not_a_number",  # Invalid - should default to 0
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["equipment"][0]["stops"], 0)

    def test_add_custom_armor_invalid_dex_penalty_defaults_zero(self):
        """Custom armor with non-numeric dex_penalty defaults to zero."""
        self.as_game_master()
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {
                "is_custom": True,
                "name": "Test Armor",
                "category": "armor",
                "weight": 20.0,
                "dex_penalty": [],  # Invalid type - should default to 0
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["equipment"][0]["dex_penalty"], 0)

    def test_add_custom_armor_invalid_stops_mod_defaults_zero(self):
        """Custom armor with non-numeric stops_mod defaults to zero."""
        # Item modifiers are a Game Master's grant (issue #752).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("equipment-gm"))
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {
                "is_custom": True,
                "name": "Test Armor",
                "category": "armor",
                "weight": 20.0,
                "stops_mod": {},  # Invalid type - should default to 0
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["equipment"][0]["stops_mod"], 0)

    def test_equip_armor_unequips_custom_armor(self):
        """Adding equipped armor unequips existing custom armor."""
        self.character.equipment = [
            {
                "item_id": "custom",
                "name": "Custom Plate",
                "category": "armor",
                "weight": 30.0,
                "quantity": 1,
                "in_pack": False,
                "equipped": True,
            }
        ]
        self.character.save()

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "chainmail", "equipped": True},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Custom armor should be unequipped
        self.assertFalse(data["equipment"][0]["equipped"])
        # New armor should be equipped
        self.assertTrue(data["equipment"][1]["equipped"])

    def test_equipment_add_invalid_json_fails(self):
        """Equipment add with invalid JSON body fails."""
        response = self.client.post(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            data="not valid json{",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_equipment_update_invalid_json_fails(self):
        """Equipment update with invalid JSON body fails."""
        response = self.client.post(
            f"/tarmar/characters/{self.character.pk}/equipment/update/",
            data="{invalid",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
