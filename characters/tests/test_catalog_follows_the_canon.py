"""Every reader of the equipment catalog follows the loaded canon (issue #75).

The catalog is the loaded canon's ``equipment.json``, read through
:func:`characters.equipment_rules.equipment_catalog`. Each case seats an
invented catalog whose ids, names, weights and classes the default canon does
not have, so a reader still holding the old fixture, or a value it built at
import, would answer with the default canon's items and fail.
"""

from django.test import SimpleTestCase
from django.urls import reverse

from battle.generation import _lighter_weapons
from battle.services import _weapon_skill_levels
from characters import combat
from characters.equipment import catalog_by_group, get_item
from characters.equipment_rules import (
    Armour,
    EquipmentCatalog,
    Gear,
    Shield,
    Weapon,
)
from characters.import_service import CharacterImportError, validate_import_payload
from characters.models import Character
from characters.services import CharacterChangeError, catalog_item_id
from characters.skills import points_for_level
from characters.tests.rules_fixtures import (
    DEFAULT_EQUIPMENT_CATALOG,
    seated_equipment_catalog,
)
from characters.weapon_skill import (
    record_weapon_skill,
    trained_weapon_id,
    weapon_choices,
)
from common.test_helpers import BaseTestCase, create_character
from gm.character_schema import extraction_to_import_payload


def _weapon(identifier: str, name: str, weight: float, weapon_class: str) -> Weapon:
    """An invented one-handed weapon of ``weapon_class``."""
    return Weapon(
        identifier=identifier,
        name=name,
        category="weapons",
        cost=7,
        weight=weight,
        subcategory="knives_swords",
        damage="1d6+1",
        str_req=9,
        throwable=False,
        two_handed=False,
        weapon_class=weapon_class,
    )


#: A catalog none of whose ids or names the default canon has.
INVENTED_CATALOG = EquipmentCatalog(
    weapons={
        "moonblade": _weapon("moonblade", "Moonblade", 3.5, "Striking"),
        "sunblade": _weapon("sunblade", "Sunblade", 4.0, "Striking"),
        "starpick": _weapon("starpick", "Starpick", 1.5, "Piercing"),
    },
    armour={
        "reed_vest": Armour(
            identifier="reed_vest",
            name="Reed Vest",
            category="armor",
            cost=20,
            weight=8.0,
            stops=1,
            dex_penalty=-1,
            movement_restriction=None,
            armour_tier="Light",
        )
    },
    shields={
        "kite_board": Shield(
            identifier="kite_board",
            name="Kite Board",
            category="shield",
            cost=9,
            weight=5.0,
            stops=1,
            dex_penalty=0,
            shield_bonus=2,
        )
    },
    gear={
        "lodestone": Gear(
            identifier="lodestone",
            name="Lodestone",
            category="gear",
            cost=0.25,
            weight=16.0,
            subcategory="special",
        )
    },
)


class SeatedCatalogTest(SimpleTestCase):
    """Seats :data:`INVENTED_CATALOG` for every test of the class."""

    def setUp(self) -> None:
        self.enterContext(seated_equipment_catalog(INVENTED_CATALOG))

    def test_the_invented_ids_are_not_the_default_canon_s(self) -> None:
        self.assertFalse(
            set(INVENTED_CATALOG.all_items) & set(DEFAULT_EQUIPMENT_CATALOG.all_items)
        )


class ReadersFollowTheSeatedCatalogTest(SeatedCatalogTest):
    def test_an_item_is_read_from_the_seated_file(self) -> None:
        self.assertEqual(
            get_item("lodestone"), INVENTED_CATALOG.gear["lodestone"].as_dict()
        )
        self.assertIsNone(get_item("broadsword"))
        self.assertEqual(catalog_by_group(), INVENTED_CATALOG.as_dict())

    def test_weight_and_encumbrance_come_from_the_seated_weights(self) -> None:
        character = Character(
            strength=10,
            equipment=[{"item_id": "lodestone", "quantity": 1, "in_pack": False}],
        )
        self.assertEqual(character.carried_weight, 16.0)
        # STR 10: unencumbered to 10 lb, light to 15, medium to 20.
        self.assertEqual(character.encumbrance_level, "medium")

    def test_the_weapon_classes_are_the_seated_file_s(self) -> None:
        # Two different weapons of one class share half the training; a
        # weapon of another class, or one the seated file lacks, shares none.
        self.assertEqual(
            combat.weapon_skill_bonus(
                4, skill_weapon_id="moonblade", attack_weapon_id="sunblade"
            ),
            combat.skill_bonus(4) // 2,
        )
        self.assertEqual(
            combat.weapon_skill_bonus(
                4, skill_weapon_id="moonblade", attack_weapon_id="broadsword"
            ),
            0,
        )

    def test_a_weapon_skill_is_tied_to_a_seated_weapon(self) -> None:
        self.assertEqual(trained_weapon_id("weapon (moonblade)"), "moonblade")
        self.assertEqual(trained_weapon_id("starpick"), "starpick")
        self.assertIsNone(trained_weapon_id("weapon (broadsword)"))
        self.assertEqual(
            [choice["item_id"] for choice in weapon_choices()],
            ["moonblade", "starpick", "sunblade"],
        )

    def test_a_name_is_matched_to_a_seated_id(self) -> None:
        self.assertEqual(catalog_item_id("moonblade"), "moonblade")
        self.assertEqual(catalog_item_id(" Kite Board "), "kite_board")
        self.assertEqual(catalog_item_id("Broadsword"), "")

    def test_an_untied_weapon_skill_is_tied_to_a_seated_weapon(self) -> None:
        character = Character(skills={"weapon": 4})
        line = record_weapon_skill(
            character, "weapon", "moonblade", is_game_master=False
        )
        self.assertEqual(line, "weapon tied to Moonblade: now weapon (moonblade)")
        self.assertEqual(character.skills, {"weapon (moonblade)": 4})
        with self.assertRaisesMessage(CharacterChangeError, "Unknown weapon"):
            record_weapon_skill(
                Character(skills={"weapon": 4}),
                "weapon",
                "broadsword",
                is_game_master=False,
            )

    def test_a_weapon_steps_down_to_a_lighter_seated_one_of_its_class(
        self,
    ) -> None:
        # The sunblade's class is the moonblade's, which weighs less; the
        # starpick is lighter still but of another class.
        self.assertEqual(_lighter_weapons("sunblade", 10), ["moonblade"])
        self.assertEqual(_lighter_weapons("moonblade", 10), [])

    def test_the_board_prices_every_seated_weapon(self) -> None:
        character = Character(
            skills={"weapon (moonblade)": points_for_level(4, "weapon (moonblade)")}
        )
        levels = _weapon_skill_levels(character)
        # The trained weapon in full, its classmate in part, the other class
        # not at all, and no default canon weapon.
        self.assertEqual(set(levels), {"moonblade", "sunblade"})
        self.assertGreater(levels["moonblade"], levels["sunblade"])

    def test_the_game_master_s_extraction_and_the_importer_read_it(self) -> None:
        payload = extraction_to_import_payload(
            {"name": "Seated", "equipment": [{"name": "Moonblade"}, {"name": "Dagger"}]}
        )
        moonblade, dagger = payload["data"]["equipment"]
        self.assertEqual(moonblade["item_id"], "moonblade")
        self.assertEqual(dagger["item_id"], "custom")
        # A catalog item's numbers are the catalog's, whatever it carries.
        moonblade.update(category="weapons", damage="lots")
        validate_import_payload(payload)
        # The default canon's dagger is a custom item here, held to the rule.
        dagger.update(category="weapons", damage="lots")
        with self.assertRaises(CharacterImportError):
            validate_import_payload(payload)


class TheSheetServesTheSeatedCatalogTest(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_equipment_catalog(INVENTED_CATALOG))

    def test_the_catalog_endpoint_serves_the_seated_file(self) -> None:
        self.login_as(self.create_user("seated-catalog-reader"))

        response = self.client.get(reverse("characters:equipment_catalog"))

        self.assertEqual(response.json(), INVENTED_CATALOG.as_dict())

    def test_the_attack_panel_lists_the_seated_shields(self) -> None:
        owner = self.create_user("seated-shield-reader")
        character = create_character(
            owner, equipment=[{"item_id": "moonblade", "quantity": 1}]
        )
        self.login_as(owner)

        response = self.client.get(reverse("characters:edit", args=[character.pk]))

        self.assertEqual(
            response.context["attack_shields"],
            [{"item_id": "kite_board", "name": "Kite Board"}],
        )
