"""Issue #939: the importer holds a value to the rule the sheet holds it to.

The sheet builds a custom item through ``custom_equipment_item``, which holds
its damage to a dice pattern and its Stops and DEX penalty to ranges; the
importer checked the damage's length alone, so a file brought in a damage of
"lots". The same sweep found the height and weight die rolls held to a
length on import and to the die-roll rule on the sheet, and a player's
import keeping a height chain no player may choose.
"""

import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from character_export.export_utils import generate_character_json
from characters.constants import MAX_ARMOR_DEX_PENALTY_MAGNITUDE, MAX_ARMOR_STOPS
from characters.equipment import ITEM_CATEGORIES
from characters.import_service import (
    CharacterImportError,
    held_to_player,
    save_imported_character,
    validate_import_payload,
)
from characters.models import Character
from common.test_helpers import BaseTestCase, create_character
from gm.character_schema import extraction_to_import_payload

from .test_import import _build_payload


def _custom(**fields: object) -> dict:
    """A custom item as the sheet stores one, with ``fields`` over it."""
    return {
        "item_id": "custom",
        "name": "Odd thing",
        "category": "gear",
        "weight": 1.0,
        "cost": 0.0,
        "quantity": 1,
        "in_pack": False,
        "notes": "",
    } | fields


class CustomItemHeldToTheSheetsRuleTest(BaseTestCase):
    """A custom item is held to the sheet's builder, on every import."""

    def _validate(self, item: dict) -> None:
        validate_import_payload(_build_payload(overrides={"equipment": [item]}))

    def test_what_the_sheet_refuses_the_import_refuses(self):
        for label, item, wording in (
            (
                "damage not dice",
                _custom(category="weapons", damage="lots"),
                "Damage must look like",
            ),
            (
                "damage past the pattern",
                _custom(category="weapons", damage="999d999"),
                "Damage must look like",
            ),
            (
                "stops over",
                _custom(category="armor", stops=MAX_ARMOR_STOPS + 1),
                f"Stops must be 0-{MAX_ARMOR_STOPS}",
            ),
            (
                "stops under",
                _custom(category="shield", stops=-1),
                f"Stops must be 0-{MAX_ARMOR_STOPS}",
            ),
            (
                "dex penalty positive",
                _custom(category="armor", dex_penalty=1),
                "DEX penalty must be",
            ),
            (
                "dex penalty too deep",
                _custom(
                    category="armor",
                    dex_penalty=-MAX_ARMOR_DEX_PENALTY_MAGNITUDE - 1,
                ),
                "DEX penalty must be",
            ),
            ("category unknown", _custom(category="spaceship"), "Invalid category"),
            ("category a list", _custom(category=["armor"]), "Invalid category"),
            ("cost negative", _custom(cost=-5), "Cost cannot be negative"),
            ("name blank", _custom(name=""), "Custom item name required"),
            (
                "an id the catalog does not know",
                _custom(item_id="no-such-item", category="armor", stops=99),
                "Stops must be",
            ),
        ):
            with self.subTest(label):
                with self.assertRaises(CharacterImportError) as refused:
                    self._validate(item)
                self.assertTrue(
                    str(refused.exception).startswith("Equipment item 0: "),
                    str(refused.exception),
                )
                self.assertIn(wording, str(refused.exception))

    def test_a_number_not_stored_as_the_builder_stores_it_is_refused(self):
        """The builder reads a form and coerces; a stored item is typed."""
        for field_name, item in (
            ("stops", _custom(category="armor", stops="3")),
            ("stops", _custom(category="armor", stops=None)),
            ("dex_penalty", _custom(category="armor", dex_penalty="-1")),
            ("cost", _custom(cost="abc")),
            ("cost", _custom(cost=None)),
            ("weight", _custom(weight=None)),
            ("to_hit_mod", _custom(category="weapons", to_hit_mod=None)),
            ("equipped", _custom(equipped="yes")),
            ("in_pack", _custom(in_pack=1)),
            # #928's limit loop takes a damage that is not text first.
            ("damage", _custom(category="weapons", damage=6)),
            ("damage_mod", _custom(category="weapons", damage_mod="1")),
            ("critical_mod", _custom(category="weapons", critical_mod=1.5)),
            ("stops_mod", _custom(category="armor", stops_mod=None)),
            ("dex_mod", _custom(category="armor", dex_mod=True)),
            ("cost", _custom(cost=float("nan"))),
            ("cost", _custom(cost=float("inf"))),
            ("weight", _custom(weight=float("-inf"))),
        ):
            with self.subTest(field_name, value=item[field_name]):
                with self.assertRaises(CharacterImportError) as refused:
                    self._validate(item)
                self.assertEqual(
                    str(refused.exception),
                    f"Equipment item 0 has an invalid {field_name}",
                )

    def test_what_the_sheet_accepts_the_import_accepts(self):
        accepted = [
            _custom(category="weapons", damage=damage)
            for damage in ("1d6", "2d8+1", "1d10-2", "12d100+999")
        ]
        accepted += [
            _custom(category=category, stops=stops, dex_penalty=dex_penalty)
            for category in ("armor", "shield")
            for stops, dex_penalty in (
                (0, 0),
                (MAX_ARMOR_STOPS, -MAX_ARMOR_DEX_PENALTY_MAGNITUDE),
            )
        ]
        accepted += [_custom(category=category) for category, _ in ITEM_CATEGORIES]
        for item in accepted:
            with self.subTest(item=item):
                self._validate(item)

    def test_a_catalog_entry_is_read_from_the_catalog(self):
        """A catalog item's numbers are the catalog's, whatever it carries."""
        self._validate({"item_id": "dagger", "quantity": 1, "damage": "lots"})

    def test_the_chats_extraction_is_held_to_the_same_rule(self):
        """The chat's ``/character`` file goes through the same importer."""
        payload = extraction_to_import_payload(
            {"name": "Extracted", "equipment": [{"name": "Odd thing"}]}
        )
        validate_import_payload(payload)
        payload["data"]["equipment"][0].update(category="weapons", damage="lots")
        with self.assertRaises(CharacterImportError) as refused:
            validate_import_payload(payload)
        self.assertIn("Damage must look like", str(refused.exception))


class CustomItemRoundTripTest(BaseTestCase):
    """An item the sheet stores comes back from its export unchanged."""

    def test_a_game_masters_custom_items_round_trip(self):
        game_master = self.create_dm("round-trip-dm")
        character = create_character(game_master, name="Kit", equipment=[])
        self.login_as(game_master)
        for item in (
            {"name": "Odd blade", "category": "weapons", "damage": "2d8+1"},
            {"name": "Hide", "category": "armor", "stops": 4, "dex_penalty": -2},
            {"name": "Buckler", "category": "shield", "stops": 1},
            {"name": "Trinket", "category": "gear", "weight": 0.5, "cost": 3},
        ):
            response = self.post_json(
                reverse("characters:equipment_add", args=[character.pk]),
                {"is_custom": True} | item,
            )
            self.assertEqual(response.status_code, 200, response.content)
        character.refresh_from_db()
        self.assertEqual(len(character.equipment), 4)

        upload = SimpleUploadedFile(
            "character.json",
            generate_character_json(character).encode("utf-8"),
            content_type="application/json",
        )
        response = self.client.post("/tarmar/characters/import/", {"file": upload})

        self.assertEqual(response.status_code, 200, response.content.decode()[:400])
        imported = Character.objects.get(pk=response.json()["character_id"])
        self.assertEqual(imported.equipment, character.equipment)


class DieRollsHeldToTheSheetsRuleTest(BaseTestCase):
    """Height and weight are die faces on the sheet and on import (#805)."""

    def test_a_roll_no_die_makes_is_refused(self):
        for field_name, value, wording in (
            ("height_roll", "tall", "Height must be die faces"),
            ("weight_roll", "9", "Weight must be die faces"),
        ):
            with self.subTest(field_name):
                with self.assertRaises(CharacterImportError) as refused:
                    validate_import_payload(
                        _build_payload(overrides={field_name: value})
                    )
                self.assertIn(wording, str(refused.exception))

    def test_a_game_master_keeps_a_chain_and_a_player_does_not(self):
        """A chain is the dice's or a Game Master's (#748); a file is not dice."""
        payload = _build_payload(overrides={"height_roll": "6.6.2"})
        for is_game_master, height in ((True, "6.6.2"), (False, "")):
            with self.subTest(is_game_master=is_game_master):
                imported = held_to_player(
                    validate_import_payload(json.loads(json.dumps(payload))),
                    is_game_master=is_game_master,
                )
                character = save_imported_character(
                    self.create_user(f"importer-{is_game_master}"), imported
                )
                self.assertEqual(character.height_roll, height)
                if not is_game_master:
                    self.assertIn("height roll 6.6.2", character.import_notice)

    def test_a_player_keeps_the_face_they_chose(self):
        imported = held_to_player(
            validate_import_payload(_build_payload(overrides={"height_roll": "5"})),
            is_game_master=False,
        )
        self.assertEqual(imported.fields["height_roll"], "5")
        self.assertNotIn("height roll", imported.fields["import_notice"])
