"""The equipment vocabularies have one definition each (#338 item 21).

A catalog group ("shields") and an item's own category ("shield") are two
different lists that were typed into the template, the JavaScript and the
validator separately. characters/equipment_rules.py holds the groups and
characters/equipment.py the categories; these tests keep every consumer on
them. That every loaded item's category is its group's is the catalog
loader's check (``characters.equipment_rules``, issue #75).
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import reverse

from characters.equipment import ITEM_CATEGORIES
from characters.equipment_rules import CATALOG_GROUPS
from characters.equipment_service import build_custom_equipment_item
from common.test_helpers import BaseTestCase, create_character

ROOT = Path(settings.BASE_DIR)


class CustomItemValidationTest(SimpleTestCase):
    def test_each_listed_category_is_accepted(self):
        for category, _label in ITEM_CATEGORIES:
            with self.subTest(category=category):
                _item, _data, error = build_custom_equipment_item(
                    {"name": "Thing", "category": category}
                )
                self.assertIsNone(error)

    def test_a_group_name_is_not_an_item_category(self):
        _item, _data, error = build_custom_equipment_item(
            {"name": "Thing", "category": "shields"}
        )

        self.assertIsNotNone(error)


class EditPageOptionsTest(BaseTestCase):
    def test_both_pickers_list_the_vocabularies(self):
        owner = self.create_user("vocabulary-owner")
        character = create_character(owner)
        self.login_as(owner)

        html = self.client.get(
            reverse("characters:edit", args=[character.pk])
        ).content.decode()

        for group, label in CATALOG_GROUPS:
            with self.subTest(group=group):
                self.assertIn(f'<option value="{group}">{label}</option>', html)
        for category, label in ITEM_CATEGORIES:
            with self.subTest(category=category):
                self.assertIn(f'<option value="{category}">{label}</option>', html)

    def test_the_catalog_endpoint_is_the_grouped_catalog(self):
        owner = self.create_user("vocabulary-reader")
        self.login_as(owner)

        response = self.client.get(reverse("characters:equipment_catalog"))

        self.assertEqual(
            list(response.json()), [group for group, _label in CATALOG_GROUPS]
        )


class ScriptBranchesUseKnownCategoriesTest(SimpleTestCase):
    def test_every_category_the_editor_script_compares_against_is_known(self):
        script = (ROOT / "static" / "js" / "character-edit.js").read_text()
        compared = set(re.findall(r"category === '([a-z]+)'", script))
        known = {category for category, _label in ITEM_CATEGORIES}

        self.assertTrue(compared)
        self.assertLessEqual(compared, known)
        self.assertNotIn("['weapons', 'armor', 'shields', 'gear']", script)
