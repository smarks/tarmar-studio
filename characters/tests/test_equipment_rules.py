"""The canon's equipment catalog and equipment rules files (issue #75).

Most cases read invented files, so they pin the loaders and the dataclasses'
validation rather than any canon's items or numbers. The default canon's real
files and the loaded canon's are read at the end; that the default files state
what the public pages state is the drift tests' business, and the Tarmar
canon's values are its own ``tests/expectations.json``'s.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

from django.core.checks import Error, run_checks
from django.test import SimpleTestCase

from characters.checks import (
    EQUIPMENT_CATALOG_CHECK_ID,
    EQUIPMENT_RULES_CHECK_ID,
    check_equipment_catalog,
    check_equipment_rules,
)
from characters.combat import validate_catalog_classes
from characters.equipment import ITEM_CATEGORIES
from characters.equipment_rules import (
    CATALOG_GROUPS,
    Armour,
    DefenderDodgeRules,
    EncumbranceLevel,
    EncumbranceRules,
    EquipmentCatalog,
    EquipmentCatalogError,
    EquipmentRules,
    EquipmentRulesError,
    GaitRules,
    Gear,
    Shield,
    Weapon,
    equipment_catalog,
    equipment_rules,
    load_equipment_catalog,
    load_equipment_rules,
)
from characters.tests.rules_fixtures import (
    DEFAULT_EQUIPMENT_CATALOG,
    DEFAULT_EQUIPMENT_RULES,
    seated_equipment_catalog,
    seated_equipment_rules,
)
from common.test_helpers import CANON_EXPECTATIONS, skip_unless_canon_expects
from reference.paths import (
    CANON_DIR_ENV,
    CANON_EQUIPMENT_CATALOG_FILE_NAME,
    CANON_EQUIPMENT_RULES_FILE_NAME,
    DEFAULT_CANON_DIR,
    EQUIPMENT_CATALOG_FILE,
    EQUIPMENT_RULES_FILE,
    REPO_ROOT,
)
from reference.rules_file import RulesFileError

#: A complete catalog with invented items, one of each kind the groups hold.
INVENTED_CATALOG: dict[str, Any] = {
    "weapons": {
        "glaive": {
            "name": "Glaive",
            "category": "weapons",
            "subcategory": "unusual",
            "damage": "2d4+1",
            "str_req": 9,
            "cost": 12,
            "weight": 6.5,
            "throwable": False,
            "two_handed": True,
            "weapon_class": "Flexible / Snare",
            "alt_class": "Piercing",
            "notes": "Reach.",
        },
        "pebbles": {
            "name": "Pebbles (10)",
            "category": "weapons",
            "subcategory": "ammunition",
            "damage": None,
            "str_req": None,
            "cost": 0.03,
            "weight": 1.0,
            "throwable": False,
            "two_handed": False,
        },
    },
    "armor": {
        "quilt": {
            "name": "Quilt",
            "category": "armor",
            "stops": 1,
            "dex_penalty": -1,
            "cost": 8,
            "weight": 9.0,
            "movement_restriction": "no_dash",
            "armour_tier": "Light",
        }
    },
    "shields": {
        "buckler": {
            "name": "Buckler",
            "category": "shield",
            "stops": 1,
            "dex_penalty": 0,
            "cost": 6,
            "weight": 3.0,
            "shield_bonus": 2,
        }
    },
    "gear": {
        "lantern": {
            "name": "Lantern",
            "category": "gear",
            "subcategory": "light",
            "cost": 0.5,
            "weight": 2.0,
        }
    },
    "rulings": [{"key": "invented", "reading": "An invented reading."}],
}

INVENTED_EQUIPMENT_CATALOG = EquipmentCatalog(
    weapons={
        "glaive": Weapon(
            identifier="glaive",
            name="Glaive",
            category="weapons",
            cost=12,
            weight=6.5,
            subcategory="unusual",
            damage="2d4+1",
            str_req=9,
            throwable=False,
            two_handed=True,
            weapon_class="Flexible / Snare",
            alt_class="Piercing",
            notes="Reach.",
        ),
        "pebbles": Weapon(
            identifier="pebbles",
            name="Pebbles (10)",
            category="weapons",
            cost=0.03,
            weight=1.0,
            subcategory="ammunition",
            damage=None,
            str_req=None,
            throwable=False,
            two_handed=False,
        ),
    },
    armour={
        "quilt": Armour(
            identifier="quilt",
            name="Quilt",
            category="armor",
            cost=8,
            weight=9.0,
            stops=1,
            dex_penalty=-1,
            movement_restriction="no_dash",
            armour_tier="Light",
        )
    },
    shields={
        "buckler": Shield(
            identifier="buckler",
            name="Buckler",
            category="shield",
            cost=6,
            weight=3.0,
            stops=1,
            dex_penalty=0,
            shield_bonus=2,
        )
    },
    gear={
        "lantern": Gear(
            identifier="lantern",
            name="Lantern",
            category="gear",
            cost=0.5,
            weight=2.0,
            subcategory="light",
        )
    },
)

#: A complete rules file with invented numbers and gaits.
INVENTED_RULES: dict[str, Any] = {
    "encumbrance": {
        "levels": [
            {
                "key": "free",
                "max_multiplier": 1,
                "penalty": 0,
                "forbidden_gaits": [],
                "walk_limit": None,
            },
            {
                "key": "laden",
                "max_multiplier": 3.5,
                "penalty": -2,
                "forbidden_gaits": ["dash"],
                "walk_limit": None,
            },
            {
                "key": "crushed",
                "max_multiplier": None,
                "penalty": -1,
                "forbidden_gaits": ["dash", "trot"],
                "walk_limit": 2,
            },
        ]
    },
    "armour_restriction_codes": {"no_dash": ["dash"]},
    "gaits": {
        "order": ["dash", "trot", "amble"],
        "base_distances": {"dash": 9, "trot": 5, "amble": 0},
    },
    "missile_classes": ["Flexible / Snare"],
    "defender_dodge": {"minimum": 1, "maximum": 6},
    "rulings": [{"key": "invented", "reading": "An invented reading."}],
}

INVENTED_EQUIPMENT_RULES = EquipmentRules(
    encumbrance=EncumbranceRules(
        levels=(
            EncumbranceLevel(
                key="free",
                max_multiplier=1,
                penalty=0,
                forbidden_gaits=(),
                walk_limit=None,
            ),
            EncumbranceLevel(
                key="laden",
                max_multiplier=3.5,
                penalty=-2,
                forbidden_gaits=("dash",),
                walk_limit=None,
            ),
            EncumbranceLevel(
                key="crushed",
                max_multiplier=None,
                penalty=-1,
                forbidden_gaits=("dash", "trot"),
                walk_limit=2,
            ),
        )
    ),
    armour_restriction_codes={"no_dash": ("dash",)},
    gaits=GaitRules(
        order=("dash", "trot", "amble"),
        base_distances={"dash": 9, "trot": 5, "amble": 0},
    ),
    missile_classes=frozenset({"Flexible / Snare"}),
    defender_dodge=DefenderDodgeRules(minimum=1, maximum=6),
)

#: A change made to a copy of an invented file.
Change = Callable[[dict[str, Any]], Any]


def copied(invented: dict[str, Any]) -> dict[str, Any]:
    """A deep copy of an invented file a test may change."""
    return json.loads(json.dumps(invented))


def group_of(catalog: EquipmentCatalog, identifier: str) -> str | None:
    """The key of the group holding ``identifier``, or ``None``."""
    return next(
        (group for group, items in catalog.groups.items() if identifier in items),
        None,
    )


def equipment_catalog_summary(
    catalog: EquipmentCatalog, identifiers: list[str]
) -> dict[str, Any]:
    """The catalog in the shape a canon's ``equipment_catalog`` expectation
    takes.

    Args:
        catalog: A loaded catalog.
        identifiers: The ids whose groups the expectation pins.

    Returns:
        The facts the expectation pins, as JSON would hold them.
    """
    torch = catalog.item("torch")
    return {
        "items_per_group": {
            group: len(items) for group, items in catalog.groups.items()
        },
        "torch_cost": torch.cost if torch else None,
        "item_groups": {
            identifier: group_of(catalog, identifier) for identifier in identifiers
        },
    }


def equipment_rules_summary(rules: EquipmentRules) -> dict[str, Any]:
    """The rules in the shape a canon's ``equipment_rules`` expectation takes.

    Args:
        rules: Loaded rules.

    Returns:
        The numbers the expectation pins, as JSON would hold them.
    """
    return {
        "encumbrance_levels": [
            [level.key, level.max_multiplier, level.penalty]
            for level in rules.encumbrance.levels
        ],
        "gait_base_distances": dict(rules.gaits.base_distances),
        "armour_restriction_codes": {
            code: list(gaits) for code, gaits in rules.armour_restriction_codes.items()
        },
        "missile_classes": sorted(rules.missile_classes),
        "defender_dodge": [rules.defender_dodge.minimum, rules.defender_dodge.maximum],
    }


class _FileTestCase(SimpleTestCase):
    """Loads changed copies of one invented file, for a subclass to refuse.

    Attributes:
        file_name: The file's name in a canon.
        invented: The invented file.
        error_class: The loader's refusal.
    """

    file_name: ClassVar[str]
    invented: ClassVar[dict[str, Any]]
    error_class: ClassVar[type[RulesFileError]]

    def setUp(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.rules_file = directory / self.file_name

    def write_text(self, text: str) -> Path:
        """Write ``text`` as the file and return its path."""
        self.rules_file.write_text(text, encoding="utf-8")
        return self.rules_file

    def load(self, stored: dict[str, Any]) -> object:
        """Load ``stored`` written as the file."""
        raise NotImplementedError

    def assert_changed_refused(self, change: Change, *expected_texts: str) -> None:
        """The invented file with ``change`` applied is refused, naming the
        file and each of ``expected_texts``."""
        stored = copied(self.invented)
        change(stored)
        with self.assertRaises(self.error_class) as caught:
            self.load(stored)
        message = str(caught.exception)
        self.assertIn(str(self.rules_file), message)
        for expected_text in expected_texts:
            self.assertIn(expected_text, message)

    def assert_each_refused(self, cases: tuple[tuple[Change, str, str], ...]) -> None:
        """Each change is refused, naming its field and its problem."""
        for change, field_path, problem in cases:
            with self.subTest(field_path=field_path, problem=problem):
                self.assert_changed_refused(change, field_path, problem)


class EquipmentCatalogFileTest(_FileTestCase):
    """The catalog loader reads the file strictly, naming the file and field."""

    file_name = CANON_EQUIPMENT_CATALOG_FILE_NAME
    invented = INVENTED_CATALOG
    error_class = EquipmentCatalogError

    def load(self, stored: dict[str, Any]) -> EquipmentCatalog:
        """Load ``stored`` written as the file."""
        return load_equipment_catalog(self.write_text(json.dumps(stored)))

    def test_a_full_file_loads_into_the_dataclasses(self) -> None:
        self.assertEqual(self.load(INVENTED_CATALOG), INVENTED_EQUIPMENT_CATALOG)

    def test_rulings_are_optional(self) -> None:
        stored = copied(INVENTED_CATALOG)
        del stored["rulings"]
        self.assertEqual(self.load(stored), INVENTED_EQUIPMENT_CATALOG)

    def test_the_error_is_a_rules_file_error(self) -> None:
        self.assertTrue(issubclass(EquipmentCatalogError, RulesFileError))

    def test_a_missing_file_is_refused(self) -> None:
        with self.assertRaises(EquipmentCatalogError) as caught:
            load_equipment_catalog(self.rules_file)
        self.assertIn(str(self.rules_file), str(caught.exception))
        self.assertIn("every canon must carry one", str(caught.exception))

    def test_a_key_repeated_in_one_object_is_refused(self) -> None:
        text = '{"gear": {}, ' + json.dumps(INVENTED_CATALOG)[1:]
        with self.assertRaises(EquipmentCatalogError) as caught:
            load_equipment_catalog(self.write_text(text))
        self.assertIn("is not valid JSON", str(caught.exception))
        self.assertIn("['gear']", str(caught.exception))

    def test_an_item_is_written_back_as_the_file_holds_it(self) -> None:
        catalog = self.load(INVENTED_CATALOG)
        for group, _label in CATALOG_GROUPS:
            for identifier, stored in INVENTED_CATALOG[group].items():
                with self.subTest(identifier=identifier):
                    catalog_item = catalog.item(identifier)
                    assert catalog_item is not None
                    self.assertEqual(catalog_item.as_dict(), stored)

    def test_ammunition_has_no_class_and_damage_is_not_parsed(self) -> None:
        stored = copied(INVENTED_CATALOG)
        stored["weapons"]["glaive"]["damage"] = "by STR"
        catalog = self.load(stored)
        self.assertEqual(catalog.weapons["glaive"].damage, "by STR")
        self.assertIsNone(catalog.weapons["pebbles"].weapon_class)
        self.assertIsNone(catalog.weapons["pebbles"].damage)

    def test_a_missing_key_is_refused(self) -> None:
        cases: tuple[tuple[Change, str], ...] = (
            (lambda stored: stored.pop("shields"), "shields"),
            (
                lambda stored: stored["weapons"]["pebbles"].pop("damage"),
                "weapons.pebbles.damage",
            ),
            (
                lambda stored: stored["armor"]["quilt"].pop("movement_restriction"),
                "armor.quilt.movement_restriction",
            ),
            (
                lambda stored: stored["shields"]["buckler"].pop("shield_bonus"),
                "shields.buckler.shield_bonus",
            ),
            (lambda stored: stored["gear"]["lantern"].pop("cost"), "gear.lantern.cost"),
        )
        for change, field_path in cases:
            with self.subTest(field_path=field_path):
                self.assert_changed_refused(change, f"{field_path} is missing")

    def test_an_unknown_key_is_refused(self) -> None:
        cases: tuple[tuple[Change, str], ...] = (
            (lambda stored: stored.update(potions={}), "potions"),
            (
                lambda stored: stored["weapons"]["glaive"].update(weapon_klass="x"),
                "weapons.glaive.weapon_klass",
            ),
            (
                lambda stored: stored["gear"]["lantern"].update(stops=1),
                "gear.lantern.stops",
            ),
            (
                lambda stored: stored["shields"]["buckler"].update(notes="Round."),
                "shields.buckler.notes",
            ),
        )
        for change, field_path in cases:
            with self.subTest(field_path=field_path):
                self.assert_changed_refused(
                    change, field_path, "is not a field this file takes"
                )

    def test_a_wrong_type_is_refused(self) -> None:
        self.assert_each_refused(
            (
                (
                    lambda stored: stored["gear"]["lantern"].update(cost=True),
                    "gear.lantern.cost",
                    "must be a finite number",
                ),
                (
                    lambda stored: stored["gear"]["lantern"].update(cost="0.5"),
                    "gear.lantern.cost",
                    "must be a finite number",
                ),
                (
                    lambda stored: stored["gear"]["lantern"].update(
                        weight=float("nan")
                    ),
                    "gear.lantern.weight",
                    "must be a finite number",
                ),
                (
                    lambda stored: stored["weapons"]["glaive"].update(damage=7),
                    "weapons.glaive.damage",
                    "a non-empty string or null",
                ),
                (
                    lambda stored: stored["weapons"]["glaive"].update(str_req=9.5),
                    "weapons.glaive.str_req",
                    "an integer or null",
                ),
                (
                    lambda stored: stored["weapons"]["glaive"].update(throwable=1),
                    "weapons.glaive.throwable",
                    "must be true or false",
                ),
                (
                    lambda stored: stored["weapons"]["glaive"].update(
                        weapon_class=None
                    ),
                    "weapons.glaive.weapon_class",
                    "a non-empty string",
                ),
                (
                    lambda stored: stored["gear"]["lantern"].update(notes=""),
                    "gear.lantern.notes",
                    "a non-empty string",
                ),
                (
                    lambda stored: stored["armor"]["quilt"].update(stops=1.0),
                    "armor.quilt.stops",
                    "must be an integer",
                ),
                (
                    lambda stored: stored.update(gear=[]),
                    "gear",
                    "must be an object",
                ),
            )
        )

    def test_each_refusal_of_an_item_names_it(self) -> None:
        self.assert_each_refused(
            (
                (
                    lambda stored: stored["gear"]["lantern"].update(cost=-0.5),
                    "gear.lantern:",
                    "cost must be at least 0, got -0.5",
                ),
                (
                    lambda stored: stored["weapons"]["pebbles"].update(weight=-1),
                    "weapons.pebbles:",
                    "weight must be at least 0, got -1",
                ),
                (
                    lambda stored: stored["armor"]["quilt"].update(dex_penalty=1),
                    "armor.quilt:",
                    "dex_penalty must be at most 0, got 1",
                ),
                (
                    lambda stored: stored["shields"]["buckler"].update(stops=-1),
                    "shields.buckler:",
                    "stops must be at least 0, got -1",
                ),
                (
                    lambda stored: stored["shields"]["buckler"].update(shield_bonus=-2),
                    "shields.buckler:",
                    "shield_bonus must be at least 0, got -2",
                ),
            )
        )

    def test_an_item_s_category_must_be_its_group_s(self) -> None:
        self.assert_each_refused(
            (
                (
                    lambda stored: stored["gear"]["lantern"].update(category="weapons"),
                    "gear.lantern:",
                    "category must be 'gear' for its group, got 'weapons'",
                ),
                (
                    lambda stored: stored["shields"]["buckler"].update(
                        category="shields"
                    ),
                    "shields.buckler:",
                    "category must be 'shield' for its group, got 'shields'",
                ),
            )
        )

    def test_the_groups_categories_are_the_custom_item_form_s(self) -> None:
        self.assertEqual(
            {item_class.CATEGORY for item_class in (Weapon, Armour, Shield, Gear)},
            set(dict(ITEM_CATEGORIES)),
        )

    def test_an_id_in_two_groups_is_refused(self) -> None:
        self.assert_changed_refused(
            lambda stored: stored["gear"].update(glaive=stored["gear"].pop("lantern")),
            "the groups:",
            "'glaive' is in both weapons and gear; an id names one item",
        )


class EquipmentCatalogLookupTest(SimpleTestCase):
    """The lookups answer as ``characters.equipment``'s functions do."""

    catalog = INVENTED_EQUIPMENT_CATALOG

    def test_an_item_is_found_by_id_in_any_group(self) -> None:
        self.assertEqual(
            list(self.catalog.all_items),
            ["glaive", "pebbles", "quilt", "buckler", "lantern"],
        )
        self.assertIs(self.catalog.item("buckler"), self.catalog.shields["buckler"])
        self.assertIsNone(self.catalog.item("anvil"))

    def test_the_groups_are_the_file_s_keys_in_the_picker_s_order(self) -> None:
        self.assertEqual(
            list(self.catalog.groups), [group for group, _label in CATALOG_GROUPS]
        )

    def test_weight_and_name_fall_back_for_an_unknown_id(self) -> None:
        self.assertEqual(self.catalog.weight("glaive"), 6.5)
        self.assertEqual(self.catalog.weight("anvil"), 0)
        self.assertEqual(self.catalog.name("quilt"), "Quilt")
        self.assertEqual(self.catalog.name("anvil"), "anvil")

    def test_items_are_found_by_their_stored_category(self) -> None:
        self.assertEqual(list(self.catalog.items_by_category("shield")), ["buckler"])
        self.assertEqual(self.catalog.items_by_category("shields"), {})

    def test_each_group_s_own_lookup_answers_for_its_items_only(self) -> None:
        self.assertEqual(self.catalog.weapon_class("glaive"), "Flexible / Snare")
        self.assertIsNone(self.catalog.weapon_class("pebbles"))
        self.assertIsNone(self.catalog.weapon_class("lantern"))
        self.assertTrue(self.catalog.is_unusual_weapon("glaive"))
        self.assertFalse(self.catalog.is_unusual_weapon("pebbles"))
        self.assertFalse(self.catalog.is_unusual_weapon("anvil"))
        self.assertEqual(self.catalog.armour_tier("quilt"), "Light")
        self.assertIsNone(self.catalog.armour_tier("buckler"))
        self.assertEqual(self.catalog.shield_bonus("buckler"), 2)
        self.assertEqual(self.catalog.shield_bonus("quilt"), 0)
        self.assertEqual(self.catalog.shield_bonus("anvil"), 0)

    def test_the_weapon_classes_are_every_weapon_s_own(self) -> None:
        # The glaive's alt_class "Piercing" is not among them.
        self.assertEqual(self.catalog.weapon_classes, frozenset({"Flexible / Snare"}))

    def test_an_item_filed_under_another_id_is_refused(self) -> None:
        with self.assertRaisesMessage(
            ValueError, "gear.torch carries the id 'lantern'"
        ):
            replace(self.catalog, gear={"torch": self.catalog.gear["lantern"]})


class EquipmentRulesFileTest(_FileTestCase):
    """The rules loader reads the file strictly, naming the file and field."""

    file_name = CANON_EQUIPMENT_RULES_FILE_NAME
    invented = INVENTED_RULES
    error_class = EquipmentRulesError

    def load(self, stored: dict[str, Any]) -> EquipmentRules:
        """Load ``stored`` written as the file."""
        return load_equipment_rules(self.write_text(json.dumps(stored)))

    def test_a_full_file_loads_into_the_dataclasses(self) -> None:
        self.assertEqual(self.load(INVENTED_RULES), INVENTED_EQUIPMENT_RULES)

    def test_rulings_are_optional(self) -> None:
        stored = copied(INVENTED_RULES)
        del stored["rulings"]
        self.assertEqual(self.load(stored), INVENTED_EQUIPMENT_RULES)

    def test_the_error_is_a_rules_file_error(self) -> None:
        self.assertTrue(issubclass(EquipmentRulesError, RulesFileError))

    def test_a_missing_file_is_refused(self) -> None:
        with self.assertRaises(EquipmentRulesError) as caught:
            load_equipment_rules(self.rules_file)
        self.assertIn(str(self.rules_file), str(caught.exception))
        self.assertIn("every canon must carry one", str(caught.exception))

    def test_a_missing_key_is_refused(self) -> None:
        cases: tuple[tuple[Change, str], ...] = (
            (lambda stored: stored.pop("missile_classes"), "missile_classes"),
            (
                lambda stored: stored["encumbrance"]["levels"][2].pop("walk_limit"),
                "encumbrance.levels[2].walk_limit",
            ),
            (
                lambda stored: stored["gaits"].pop("base_distances"),
                "gaits.base_distances",
            ),
            (
                lambda stored: stored["defender_dodge"].pop("maximum"),
                "defender_dodge.maximum",
            ),
        )
        for change, field_path in cases:
            with self.subTest(field_path=field_path):
                self.assert_changed_refused(change, f"{field_path} is missing")

    def test_an_unknown_key_is_refused(self) -> None:
        cases: tuple[tuple[Change, str], ...] = (
            (lambda stored: stored.update(gait_order=[]), "gait_order"),
            (
                lambda stored: stored["encumbrance"]["levels"][0].update(label="x"),
                "encumbrance.levels[0].label",
            ),
            (lambda stored: stored["gaits"].update(walk=4), "gaits.walk"),
        )
        for change, field_path in cases:
            with self.subTest(field_path=field_path):
                self.assert_changed_refused(
                    change, field_path, "is not a field this file takes"
                )

    def test_a_wrong_type_is_refused(self) -> None:
        self.assert_each_refused(
            (
                (
                    lambda stored: stored["encumbrance"]["levels"][1].update(
                        max_multiplier="3.5"
                    ),
                    "encumbrance.levels[1].max_multiplier",
                    "a finite number or null",
                ),
                (
                    lambda stored: stored["encumbrance"]["levels"][1].update(
                        max_multiplier=float("inf")
                    ),
                    "encumbrance.levels[1].max_multiplier",
                    "a finite number or null",
                ),
                (
                    lambda stored: stored["encumbrance"]["levels"][1].update(
                        forbidden_gaits=["dash", "dash"]
                    ),
                    "encumbrance.levels[1].forbidden_gaits",
                    "repeats an entry",
                ),
                (
                    lambda stored: stored["armour_restriction_codes"].update(
                        no_dash="dash"
                    ),
                    "armour_restriction_codes.no_dash",
                    "must be a list",
                ),
                (
                    lambda stored: stored["gaits"]["base_distances"].update(trot=5.5),
                    "gaits.base_distances.trot",
                    "must be an integer",
                ),
                (
                    lambda stored: stored["gaits"].update(order=["dash", "dash"]),
                    "gaits.order",
                    "repeats an entry",
                ),
                (
                    lambda stored: stored["defender_dodge"].update(minimum=False),
                    "defender_dodge.minimum",
                    "must be an integer",
                ),
            )
        )

    def test_each_refusal_of_the_rules_names_its_block(self) -> None:
        self.assert_each_refused(
            (
                (
                    lambda stored: stored["encumbrance"]["levels"][1].update(penalty=1),
                    "encumbrance.levels[1]:",
                    "penalty must be at most 0, got 1",
                ),
                (
                    lambda stored: stored["encumbrance"]["levels"][2].update(
                        walk_limit=-1
                    ),
                    "encumbrance.levels[2]:",
                    "walk_limit must be at least 0, got -1",
                ),
                (
                    lambda stored: stored["encumbrance"].update(levels=[]),
                    "encumbrance.levels:",
                    "has no levels",
                ),
                (
                    lambda stored: stored["encumbrance"]["levels"][1].update(
                        key="free"
                    ),
                    "encumbrance.levels:",
                    "repeats the key ['free']",
                ),
                (
                    lambda stored: stored["encumbrance"]["levels"][1].update(
                        max_multiplier=1
                    ),
                    "encumbrance.levels:",
                    "max_multiplier must rise: free allows 1 and laden 1",
                ),
                (
                    lambda stored: stored["encumbrance"]["levels"][0].update(
                        max_multiplier=None
                    ),
                    "encumbrance.levels:",
                    "free's max_multiplier is null, but only the heaviest level",
                ),
                (
                    lambda stored: stored["gaits"].update(order=[], base_distances={}),
                    "gaits:",
                    "order names no gait",
                ),
                (
                    lambda stored: stored["gaits"]["base_distances"].pop("amble"),
                    "gaits:",
                    "base_distances must give exactly the gaits of order",
                ),
                (
                    lambda stored: stored["gaits"]["base_distances"].update(trot=-1),
                    "gaits:",
                    "trot's base distance must be at least 0, got -1",
                ),
                (
                    lambda stored: stored["defender_dodge"].update(minimum=7),
                    "defender_dodge:",
                    "minimum 7 must be no more than maximum 6",
                ),
                (
                    lambda stored: stored.update(missile_classes=[]),
                    "the rules:",
                    "missile_classes names no class",
                ),
                (
                    lambda stored: stored["encumbrance"]["levels"][1].update(
                        forbidden_gaits=["gallop"]
                    ),
                    "the rules:",
                    "encumbrance.levels 'laden' forbids 'gallop', which is not a "
                    "gait a restriction may forbid: gaits.order but its slowest, "
                    "['trot', 'dash']",
                ),
                (
                    lambda stored: stored["armour_restriction_codes"].update(
                        no_trot=["trotting"]
                    ),
                    "the rules:",
                    "armour_restriction_codes 'no_trot' forbids 'trotting'",
                ),
                (
                    lambda stored: stored["encumbrance"]["levels"][2].update(
                        forbidden_gaits=["dash", "trot", "amble"]
                    ),
                    "the rules:",
                    "encumbrance.levels 'crushed' forbids 'amble', which is not a "
                    "gait a restriction may forbid",
                ),
                (
                    lambda stored: stored["armour_restriction_codes"].update(
                        no_amble=["amble"]
                    ),
                    "the rules:",
                    "armour_restriction_codes 'no_amble' forbids 'amble'",
                ),
                (
                    lambda stored: stored["encumbrance"]["levels"][0].update(
                        max_multiplier=0
                    ),
                    "encumbrance.levels[0]:",
                    "max_multiplier must be above 0, got 0",
                ),
                (
                    lambda stored: stored["encumbrance"]["levels"][0].update(
                        max_multiplier=-0.5
                    ),
                    "encumbrance.levels[0]:",
                    "max_multiplier must be above 0, got -0.5",
                ),
            )
        )


class EquipmentRulesValuesTest(SimpleTestCase):
    """What the loaded rules derive and look up, on invented values."""

    encumbrance = INVENTED_EQUIPMENT_RULES.encumbrance

    def test_a_weight_takes_the_first_level_whose_top_it_does_not_exceed(
        self,
    ) -> None:
        # Strength 10: free to 10 lb, laden to 35 lb, crushed past that.
        for carried_weight, level in (
            (0, "free"),
            (10, "free"),
            (10.5, "laden"),
            (35, "laden"),
            (35.01, "crushed"),
            (10**6, "crushed"),
        ):
            with self.subTest(carried_weight=carried_weight):
                self.assertEqual(self.encumbrance.level_for(carried_weight, 10), level)

    def test_with_no_catch_all_level_the_heaviest_takes_the_rest(self) -> None:
        capped = EncumbranceRules(
            levels=(
                *self.encumbrance.levels[:2],
                replace(self.encumbrance.levels[2], max_multiplier=5),
            )
        )
        self.assertEqual(capped.level_for(10**6, 10), "crushed")

    def test_a_level_s_penalty_restrictions_and_walk_limit(self) -> None:
        self.assertEqual(self.encumbrance.keys, ("free", "laden", "crushed"))
        self.assertEqual(self.encumbrance.penalty("laden"), -2)
        self.assertEqual(self.encumbrance.restrictions("crushed"), ["dash", "trot"])
        self.assertEqual(self.encumbrance.walk_limit("crushed"), 2)
        self.assertIsNone(self.encumbrance.walk_limit("laden"))

    def test_no_level_or_an_unknown_one_restricts_nothing(self) -> None:
        for key in (None, "buried"):
            with self.subTest(key=key):
                self.assertEqual(self.encumbrance.penalty(key), 0)
                self.assertEqual(self.encumbrance.restrictions(key), [])
                self.assertIsNone(self.encumbrance.walk_limit(key))

    def test_every_gait_but_the_slowest_is_restrictable_slowest_first(
        self,
    ) -> None:
        self.assertEqual(
            INVENTED_EQUIPMENT_RULES.gaits.restrictable_gaits, ("trot", "dash")
        )

    def test_levels_and_gaits_are_read_by_role_not_by_name(self) -> None:
        self.assertEqual(self.encumbrance.lightest.key, "free")
        self.assertEqual(self.encumbrance.heaviest.key, "crushed")
        self.assertIsNone(self.encumbrance.heaviest.max_multiplier)
        self.assertEqual(INVENTED_EQUIPMENT_RULES.gaits.slowest, "amble")


class DefaultEquipmentRulesEquivalenceTest(SimpleTestCase):
    """The default canon's rules are the Python constants they replace.

    Slice A of issue #75 only: slice C seats the rules and deletes the
    constants, and with them this test; the drift tests then hold the
    default file to the pages.
    """

    rules = DEFAULT_EQUIPMENT_RULES

    def test_the_encumbrance_levels_are_the_constants(self) -> None:
        from characters.equipment import ENCUMBRANCE_LEVELS, ENCUMBRANCE_ORDER

        self.assertEqual(self.rules.encumbrance.keys, ENCUMBRANCE_ORDER)
        for level in self.rules.encumbrance.levels:
            with self.subTest(level=level.key):
                constant = ENCUMBRANCE_LEVELS[level.key]
                max_multiplier = (
                    float("inf")
                    if level.max_multiplier is None
                    else level.max_multiplier
                )
                self.assertEqual(max_multiplier, constant["max_multiplier"])
                self.assertEqual(level.penalty, constant["penalty"])
                self.assertEqual(list(level.forbidden_gaits), constant["restrictions"])
                self.assertEqual(level.walk_limit, constant.get("walk_limit"))

    def test_the_armour_codes_and_gaits_are_the_constants(self) -> None:
        from characters.equipment import (
            ARMOR_MOVEMENT_RESTRICTION_MODES,
            MOVEMENT_MODE_ORDER,
        )
        from characters.movement import GAIT_BASE_DISTANCES, GAIT_ORDER

        self.assertEqual(
            {
                code: list(gaits)
                for code, gaits in self.rules.armour_restriction_codes.items()
            },
            ARMOR_MOVEMENT_RESTRICTION_MODES,
        )
        self.assertEqual(self.rules.gaits.order, GAIT_ORDER)
        self.assertEqual(dict(self.rules.gaits.base_distances), GAIT_BASE_DISTANCES)
        self.assertEqual(self.rules.gaits.restrictable_gaits, MOVEMENT_MODE_ORDER)

    def test_the_missile_classes_and_dodge_bounds_are_the_constants(self) -> None:
        from characters.attack import MAX_DEFENDER_DODGE, MIN_DEFENDER_DODGE
        from characters.weapon_skill import MISSILE_CLASSES

        self.assertEqual(self.rules.missile_classes, MISSILE_CLASSES)
        self.assertEqual(
            (self.rules.defender_dodge.minimum, self.rules.defender_dodge.maximum),
            (MIN_DEFENDER_DODGE, MAX_DEFENDER_DODGE),
        )


class DefaultEquipmentCatalogFixtureTest(SimpleTestCase):
    """The default canon's catalog is the fixture the code reads today.

    Slices A and B of issue #75 only: slice B seats the catalog and deletes
    ``characters/fixtures/equipment.json``, and with it this test.
    """

    def test_every_item_and_group_is_the_fixture_s(self) -> None:
        from characters.equipment import CATALOG_BY_GROUP, EQUIPMENT_CATALOG

        catalog = DEFAULT_EQUIPMENT_CATALOG
        self.assertEqual(
            {
                identifier: catalog_item.as_dict()
                for identifier, catalog_item in catalog.all_items.items()
            },
            EQUIPMENT_CATALOG,
        )
        self.assertEqual(list(catalog.all_items), list(EQUIPMENT_CATALOG))
        self.assertEqual(list(catalog.groups), list(CATALOG_BY_GROUP))
        for group, items in catalog.groups.items():
            with self.subTest(group=group):
                self.assertEqual(list(items), list(CATALOG_BY_GROUP[group]))


class CatalogClassesTest(SimpleTestCase):
    """Each weapon class and armour tier is one the combat matrix has."""

    def refusal(self, catalog: EquipmentCatalog) -> str:
        """The message :func:`validate_catalog_classes` refuses ``catalog``
        with."""
        with self.assertRaises(EquipmentCatalogError) as caught:
            validate_catalog_classes(catalog, EQUIPMENT_CATALOG_FILE)
        return str(caught.exception)

    def test_the_default_and_invented_catalogs_pass(self) -> None:
        for catalog in (DEFAULT_EQUIPMENT_CATALOG, INVENTED_EQUIPMENT_CATALOG):
            validate_catalog_classes(catalog, EQUIPMENT_CATALOG_FILE)

    def test_a_class_or_tier_the_matrix_lacks_is_refused(self) -> None:
        glaive = INVENTED_EQUIPMENT_CATALOG.weapons["glaive"]
        quilt = INVENTED_EQUIPMENT_CATALOG.armour["quilt"]
        cases = (
            (
                {"weapons": {"glaive": replace(glaive, weapon_class="Hooking")}},
                "weapons.glaive.weapon_class 'Hooking'",
            ),
            (
                {"weapons": {"glaive": replace(glaive, alt_class="Lassoing")}},
                "weapons.glaive.alt_class 'Lassoing'",
            ),
            (
                {"armour": {"quilt": replace(quilt, armour_tier="Soft")}},
                "armor.quilt.armour_tier 'Soft'",
            ),
        )
        for changes, field_text in cases:
            with self.subTest(field_text=field_text):
                message = self.refusal(replace(INVENTED_EQUIPMENT_CATALOG, **changes))
                self.assertIn(str(EQUIPMENT_CATALOG_FILE), message)
                self.assertIn(field_text, message)
                self.assertIn("the combat matrix has", message)

    def test_the_check_reports_it_under_e003(self) -> None:
        quilt = INVENTED_EQUIPMENT_CATALOG.armour["quilt"]
        catalog = replace(
            INVENTED_EQUIPMENT_CATALOG,
            armour={"quilt": replace(quilt, armour_tier="Soft")},
        )
        with patch("characters.checks.equipment_catalog", return_value=catalog):
            (message,) = check_equipment_catalog()
        self.assertEqual(message.id, EQUIPMENT_CATALOG_CHECK_ID)
        self.assertIn("armor.quilt.armour_tier 'Soft'", message.msg)


class CanonEquipmentFilesTest(SimpleTestCase):
    """The canons' own files load, and seated ones answer every caller."""

    def tearDown(self) -> None:
        equipment_catalog.cache_clear()
        equipment_rules.cache_clear()

    def test_the_default_canon_files_load(self) -> None:
        catalog = load_equipment_catalog(
            DEFAULT_CANON_DIR / CANON_EQUIPMENT_CATALOG_FILE_NAME
        )
        self.assertEqual(catalog, DEFAULT_EQUIPMENT_CATALOG)
        rules = load_equipment_rules(
            DEFAULT_CANON_DIR / CANON_EQUIPMENT_RULES_FILE_NAME
        )
        self.assertEqual(rules, DEFAULT_EQUIPMENT_RULES)

    def test_the_loaded_canon_files_load_once_per_process(self) -> None:
        equipment_catalog.cache_clear()
        equipment_rules.cache_clear()
        catalog, rules = equipment_catalog(), equipment_rules()
        self.assertEqual(catalog, load_equipment_catalog(EQUIPMENT_CATALOG_FILE))
        self.assertEqual(rules, load_equipment_rules(EQUIPMENT_RULES_FILE))
        self.assertIs(equipment_catalog(), catalog)
        self.assertIs(equipment_rules(), rules)

    def test_seated_files_answer_every_caller_then_go(self) -> None:
        with seated_equipment_catalog(INVENTED_EQUIPMENT_CATALOG):
            self.assertIs(equipment_catalog(), INVENTED_EQUIPMENT_CATALOG)
        self.assertEqual(
            equipment_catalog(), load_equipment_catalog(EQUIPMENT_CATALOG_FILE)
        )
        with seated_equipment_rules(INVENTED_EQUIPMENT_RULES):
            self.assertIs(equipment_rules(), INVENTED_EQUIPMENT_RULES)
        self.assertEqual(equipment_rules(), load_equipment_rules(EQUIPMENT_RULES_FILE))


class EquipmentChecksTest(SimpleTestCase):
    """``manage.py check`` refuses a canon whose files are refused or
    disagree."""

    def test_the_loaded_canon_passes(self) -> None:
        self.assertEqual(check_equipment_catalog(), [])
        self.assertEqual(check_equipment_rules(), [])

    def test_a_refused_file_is_a_registered_error_under_its_own_id(self) -> None:
        cases = (
            (
                "equipment_catalog",
                EquipmentCatalogError("equipment.json: gear is missing"),
                EQUIPMENT_CATALOG_CHECK_ID,
            ),
            (
                "equipment_rules",
                EquipmentRulesError("equipment-rules.json: gaits is missing"),
                EQUIPMENT_RULES_CHECK_ID,
            ),
        )
        for loader, refusal, check_id in cases:
            with self.subTest(check_id=check_id):
                with patch(f"characters.checks.{loader}", side_effect=refusal):
                    messages = run_checks()
                refused = [message for message in messages if message.id == check_id]
                self.assertEqual(len(refused), 1)
                self.assertIsInstance(refused[0], Error)
                self.assertEqual(refused[0].msg, str(refusal))
                self.assertIn("characters/equipment_rules.py", refused[0].hint or "")
                # The other file's check reports nothing it cannot check.
                other_ids = {EQUIPMENT_CATALOG_CHECK_ID, EQUIPMENT_RULES_CHECK_ID}
                other_ids.discard(check_id)
                self.assertEqual(
                    [message for message in messages if message.id in other_ids], []
                )

    def checked(
        self, catalog: EquipmentCatalog, rules: EquipmentRules
    ) -> dict[str, list[str]]:
        """Both checks' messages with ``catalog`` and ``rules`` loaded."""
        with (
            patch("characters.checks.equipment_catalog", return_value=catalog),
            patch("characters.checks.equipment_rules", return_value=rules),
        ):
            return {
                EQUIPMENT_CATALOG_CHECK_ID: [
                    message.msg for message in check_equipment_catalog()
                ],
                EQUIPMENT_RULES_CHECK_ID: [
                    message.msg for message in check_equipment_rules()
                ],
            }

    def test_the_invented_files_agree(self) -> None:
        self.assertEqual(
            self.checked(INVENTED_EQUIPMENT_CATALOG, INVENTED_EQUIPMENT_RULES),
            {EQUIPMENT_CATALOG_CHECK_ID: [], EQUIPMENT_RULES_CHECK_ID: []},
        )

    def test_an_armour_code_the_rules_do_not_give_is_refused(self) -> None:
        rules = replace(INVENTED_EQUIPMENT_RULES, armour_restriction_codes={})
        messages = self.checked(INVENTED_EQUIPMENT_CATALOG, rules)
        (message,) = messages[EQUIPMENT_CATALOG_CHECK_ID]
        self.assertIn(str(EQUIPMENT_CATALOG_FILE), message)
        self.assertIn(
            "armor.quilt.movement_restriction 'no_dash' is not a code "
            "equipment-rules.json's armour_restriction_codes gives []",
            message,
        )
        self.assertEqual(messages[EQUIPMENT_RULES_CHECK_ID], [])

    def test_a_missile_class_no_weapon_has_is_refused(self) -> None:
        rules = replace(
            INVENTED_EQUIPMENT_RULES,
            missile_classes=frozenset({"Flexible / Snare", "Piercing"}),
        )
        messages = self.checked(INVENTED_EQUIPMENT_CATALOG, rules)
        (message,) = messages[EQUIPMENT_RULES_CHECK_ID]
        self.assertIn(str(EQUIPMENT_RULES_FILE), message)
        self.assertIn(
            "missile_classes ['Piercing'] is no class a catalog weapon has "
            "['Flexible / Snare']",
            message,
        )
        self.assertEqual(messages[EQUIPMENT_CATALOG_CHECK_ID], [])


class TarmarEquipmentFilesTest(SimpleTestCase):
    """The Tarmar canon's files hold what its expectations pin.

    The values are the canon's: its ``tests/expectations.json`` carries them
    under ``equipment_catalog`` and ``equipment_rules``, and none appears here.
    """

    @skip_unless_canon_expects("equipment_catalog")
    def test_the_catalog_matches_the_canon_expectations(self) -> None:
        expected = CANON_EXPECTATIONS["equipment_catalog"]
        self.assertEqual(
            equipment_catalog_summary(
                load_equipment_catalog(EQUIPMENT_CATALOG_FILE),
                list(expected["item_groups"]),
            ),
            expected,
        )

    @skip_unless_canon_expects("equipment_rules")
    def test_the_rules_match_the_canon_expectations(self) -> None:
        self.assertEqual(
            equipment_rules_summary(load_equipment_rules(EQUIPMENT_RULES_FILE)),
            CANON_EXPECTATIONS["equipment_rules"],
        )


class EquipmentFilesReadOnlyWhenAskedTest(SimpleTestCase):
    """No import reads the canon's equipment files (issue #75).

    A module-level read would load a file while Django imports the URL
    configuration, so a canon without it would crash ``manage.py check``
    with a traceback instead of reporting ``characters.E003`` or ``E004``.
    Run out of process: this test runner has every module imported already.
    """

    def run_python(self, *arguments: str, canon_dir: Path | None = None) -> str:
        """Run ``python *arguments`` from the repository root; its output."""
        environment = {**os.environ, "DEBUG": "true"}
        if canon_dir is not None:
            environment[CANON_DIR_ENV] = str(canon_dir)
        completed = subprocess.run(
            [sys.executable, *arguments],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.stdout + completed.stderr

    def test_importing_the_url_configuration_reads_neither_file(self) -> None:
        script = (
            "import os, django\n"
            "os.environ.setdefault('DJANGO_SETTINGS_MODULE', "
            "'tarmar_studio.settings')\n"
            "django.setup()\n"
            "import tarmar_studio.urls, characters.urls\n"
            "from characters.equipment_rules import equipment_catalog, "
            "equipment_rules\n"
            "print('misses', equipment_catalog.cache_info().misses, "
            "equipment_rules.cache_info().misses)\n"
        )
        self.assertIn("misses 0 0", self.run_python("-c", script))

    def test_a_canon_without_a_file_is_a_check_error_not_a_crash(self) -> None:
        for file_name, check_id in (
            (CANON_EQUIPMENT_CATALOG_FILE_NAME, EQUIPMENT_CATALOG_CHECK_ID),
            (CANON_EQUIPMENT_RULES_FILE_NAME, EQUIPMENT_RULES_CHECK_ID),
        ):
            with self.subTest(file_name=file_name):
                canon_dir = (
                    Path(self.enterContext(tempfile.TemporaryDirectory())) / "canon"
                )
                shutil.copytree(DEFAULT_CANON_DIR, canon_dir)
                (canon_dir / file_name).unlink()
                output = self.run_python("manage.py", "check", canon_dir=canon_dir)
                self.assertIn(check_id, output)
                self.assertIn("is missing; every canon must carry one", output)
                self.assertNotIn("Traceback", output)
                # The other file's check has nothing to check it against.
                (other_check_id,) = {
                    EQUIPMENT_CATALOG_CHECK_ID,
                    EQUIPMENT_RULES_CHECK_ID,
                } - {check_id}
                self.assertNotIn(other_check_id, output)
