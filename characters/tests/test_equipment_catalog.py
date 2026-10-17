"""Unit tests for pure functions in characters.equipment and characters.constants.

These run quickly with no DB. Integration tests for character views/models
live in this same package under test_views.py, test_models.py, etc.
"""

from django.test import SimpleTestCase

from characters.constants import (
    CHARACTER_NAME_PATTERN,
    DAMAGE_DICE_PATTERN,
    MAX_EQUIPMENT_MODIFIER,
)
from characters.equipment import (
    calculate_encumbrance_level,
    combine_movement_restrictions,
    describe_movement_restrictions,
    get_armor_movement_restrictions,
    get_item,
    get_item_name,
    get_movement_penalty,
    get_movement_restrictions,
    get_restrictive_armor_names,
)
from characters.equipment_rules import equipment_catalog
from characters.rules import load_character_rules
from reference.paths import CANON_CHARACTER_RULES_FILE_NAME, DEFAULT_CANON_DIR


class EquipmentCatalogTests(SimpleTestCase):
    def test_get_item_returns_none_for_unknown(self):
        self.assertIsNone(get_item("__nope__"))

    def test_an_unknown_item_weighs_nothing(self):
        self.assertEqual(equipment_catalog().weight("__nope__"), 0)

    def test_get_item_name_unknown_returns_id(self):
        self.assertEqual(get_item_name("__nope__"), "__nope__")

    def test_catalog_items_have_required_keys(self):
        # Every catalog entry needs name, category, weight, cost.
        for item_id in equipment_catalog().all_items:
            item = get_item(item_id)
            with self.subTest(item_id=item_id):
                assert item is not None
                for key in ("name", "category", "weight", "cost"):
                    self.assertIn(key, item, f"{item_id} missing {key}")

    def test_items_by_category_filters_correctly(self):
        catalog = equipment_catalog()
        weapons = catalog.items_by_category("weapons")
        for item_id, item in weapons.items():
            self.assertEqual(item.category, "weapons", item_id)
        # Categories cover all items.
        seen = sum(
            len(catalog.items_by_category(category))
            for category in ("weapons", "armor", "shield", "gear")
        )
        self.assertEqual(seen, len(catalog.all_items))


class EncumbranceTests(SimpleTestCase):
    def test_unencumbered_at_str_weight(self):
        self.assertEqual(calculate_encumbrance_level(10.0, 10), "unencumbered")

    def test_light_above_str(self):
        # STR 10 -> light is 10 < w <= 15.
        self.assertEqual(calculate_encumbrance_level(11.0, 10), "light")
        self.assertEqual(calculate_encumbrance_level(15.0, 10), "light")

    def test_medium_threshold(self):
        # STR 10 -> medium is 15 < w <= 20.
        self.assertEqual(calculate_encumbrance_level(20.0, 10), "medium")

    def test_heavy_threshold(self):
        # STR 10 -> heavy is 20 < w <= 25.
        self.assertEqual(calculate_encumbrance_level(25.0, 10), "heavy")

    def test_overloaded(self):
        self.assertEqual(calculate_encumbrance_level(100.0, 10), "overloaded")

    def test_movement_penalty_matches_level(self):
        self.assertEqual(get_movement_penalty("unencumbered"), 0)
        self.assertEqual(get_movement_penalty("light"), -1)
        self.assertEqual(get_movement_penalty("medium"), -2)
        self.assertEqual(get_movement_penalty("heavy"), -4)

    def test_movement_restrictions(self):
        self.assertEqual(get_movement_restrictions("unencumbered"), [])
        self.assertIn("sprint", get_movement_restrictions("medium"))
        self.assertIn("sprint", get_movement_restrictions("heavy"))
        self.assertIn("sprint", get_movement_restrictions("overloaded"))

    def test_unknown_level_safe_defaults(self):
        self.assertEqual(get_movement_penalty("__nope__"), 0)
        self.assertEqual(get_movement_restrictions("__nope__"), [])


class ArmorMovementRestrictionTests(SimpleTestCase):
    """Armor-derived movement restrictions (issue #128)."""

    def test_no_equipment_is_unrestricted(self):
        self.assertEqual(get_armor_movement_restrictions([]), [])
        self.assertEqual(get_armor_movement_restrictions(None), [])

    def test_leather_restricts_sprint_only(self):
        equipment = [
            {"item_id": "leather_armor", "category": "armor", "equipped": True}
        ]
        self.assertEqual(get_armor_movement_restrictions(equipment), ["sprint"])

    def test_chainmail_restricts_run_and_sprint(self):
        equipment = [{"item_id": "chainmail", "category": "armor", "equipped": True}]
        self.assertEqual(get_armor_movement_restrictions(equipment), ["run", "sprint"])

    def test_plate_restricts_run_and_sprint(self):
        equipment = [{"item_id": "plate_armor", "category": "armor", "equipped": True}]
        self.assertEqual(get_armor_movement_restrictions(equipment), ["run", "sprint"])

    def test_unequipped_armor_imposes_nothing(self):
        equipment = [{"item_id": "plate_armor", "category": "armor", "equipped": False}]
        self.assertEqual(get_armor_movement_restrictions(equipment), [])

    def test_armor_without_equipped_key_defaults_to_unequipped(self):
        equipment = [{"item_id": "plate_armor", "category": "armor"}]
        self.assertEqual(get_armor_movement_restrictions(equipment), [])

    def test_cloth_armor_has_no_restriction(self):
        equipment = [{"item_id": "cloth_armor", "category": "armor", "equipped": True}]
        self.assertEqual(get_armor_movement_restrictions(equipment), [])

    def test_non_armor_equipment_ignored(self):
        equipment = [{"item_id": "broadsword", "category": "weapons", "equipped": True}]
        self.assertEqual(get_armor_movement_restrictions(equipment), [])

    def test_custom_armor_reads_own_movement_restriction_field(self):
        equipment = [
            {
                "item_id": "custom",
                "category": "armor",
                "equipped": True,
                "movement_restriction": "no_run_sprint",
            }
        ]
        self.assertEqual(get_armor_movement_restrictions(equipment), ["run", "sprint"])

    def test_combine_movement_restrictions_unions_and_orders(self):
        self.assertEqual(
            combine_movement_restrictions(["sprint"], ["run", "sprint"]),
            ["run", "sprint"],
        )
        self.assertEqual(
            combine_movement_restrictions(["jog", "run", "sprint"], ["sprint"]),
            ["jog", "run", "sprint"],
        )
        self.assertEqual(combine_movement_restrictions([], []), [])

    def test_describe_movement_restrictions(self):
        self.assertEqual(describe_movement_restrictions([]), "")
        self.assertEqual(describe_movement_restrictions(["sprint"]), "Cannot Sprint")
        self.assertEqual(
            describe_movement_restrictions(["run", "sprint"]), "Cannot Run or Sprint"
        )
        self.assertEqual(
            describe_movement_restrictions(["jog", "run", "sprint"]),
            "Cannot Jog, Run, or Sprint",
        )

    def test_get_restrictive_armor_names(self):
        equipment = [
            {"item_id": "chainmail", "category": "armor", "equipped": True},
            {"item_id": "cloth_armor", "category": "armor", "equipped": True},
            {"item_id": "small_shield", "category": "shield", "equipped": True},
        ]
        self.assertEqual(get_restrictive_armor_names(equipment), ["Chainmail"])


class ConstantsTests(SimpleTestCase):
    def test_max_attribute_value(self):
        # The default canon's ceiling, as the public attributes page states it.
        default_rules = load_character_rules(
            DEFAULT_CANON_DIR / CANON_CHARACTER_RULES_FILE_NAME
        )
        self.assertEqual(default_rules.attributes.maximum, 30)

    def test_max_modifier_magnitude(self):
        self.assertEqual(MAX_EQUIPMENT_MODIFIER, 10)

    def test_damage_dice_pattern_accepts_valid(self):
        for s in ("1d6", "2d8", "3d10+1", "1d10-2", "10d100", "1d10+99"):
            self.assertIsNotNone(DAMAGE_DICE_PATTERN.match(s), s)

    def test_damage_dice_pattern_rejects_invalid(self):
        for s in ("999d999", "d6", "1d", "1x6", "abc", "1d6+", "1d6 ", " 1d6"):
            self.assertIsNone(DAMAGE_DICE_PATTERN.match(s), s)

    def test_character_name_pattern_accepts_typical(self):
        for s in ("Aragorn", "Bilbo Baggins", "O'Connor", "Eve-1", "Foo.Bar_2"):
            self.assertIsNotNone(CHARACTER_NAME_PATTERN.match(s), s)

    def test_character_name_pattern_rejects_dangerous(self):
        # Emoji and control chars are blocked.
        for s in ("Hero \U0001f600", "Hero\n", "Hero\t"):
            self.assertIsNone(CHARACTER_NAME_PATTERN.match(s), repr(s))
