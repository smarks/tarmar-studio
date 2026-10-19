"""The six core attributes are listed once and reused in order."""

import ast
import os
from collections import Counter
from pathlib import Path

from django.test import SimpleTestCase

from bestiary.generation import _VARIED_STATS
from bestiary.models import Beast
from character_export.export_utils import EXPORTABLE_FIELDS
from characters.admin import CharacterAdmin
from characters.constants import CHARACTER_ATTRIBUTE_FIELDS
from characters.models import (
    CHARACTER_FIELDS,
    CHARACTER_INT_FIELD_MAX,
    CHARACTER_INT_FIELD_MIN,
    MAX_SMALLINT_FIELD_VALUE,
    MIN_ATTRIBUTE_VALUE,
    Character,
)
from characters.services import FIELD_RULES, FieldAuthor
from characters.views import _CREATION_STAT_FIELDS
from gm.character_schema import character_fields
from gm.character_sheet import ATTRIBUTE_LABELS
from gm.ledger import DRAFT_FIELDS, draft_defaults
from tarmar_studio.tests.repository_scan import SKIPPED_DIRECTORY_NAMES

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

SHEET_ORDER = (
    "strength",
    "dexterity",
    "intelligence",
    "wisdom",
    "constitution",
    "charisma",
)


class CharacterAttributeFieldsTest(SimpleTestCase):
    def test_names_the_six_model_fields_in_sheet_order(self):
        self.assertEqual(CHARACTER_ATTRIBUTE_FIELDS, SHEET_ORDER)
        for field in CHARACTER_ATTRIBUTE_FIELDS:
            Character._meta.get_field(field)
            Beast._meta.get_field(field)

    def test_creation_export_and_variation_lists_keep_their_order(self):
        self.assertEqual(_CREATION_STAT_FIELDS[:6], SHEET_ORDER)
        self.assertEqual(EXPORTABLE_FIELDS[:7], ("name", *SHEET_ORDER))
        self.assertEqual(_VARIED_STATS, (*SHEET_ORDER, "movement", "stops"))


class BroaderFieldListsKeepTheirOrderTest(SimpleTestCase):
    """The schemas that contain the six attributes read them from the one tuple.

    Each list's order is behaviour (the admin's columns, the draft ledger's
    field order), so the six stay where the literal lists had them.
    """

    def test_the_admin_columns_keep_their_order(self):
        self.assertEqual(
            CharacterAdmin.list_display,
            (
                "name",
                "user",
                "strength",
                "dexterity",
                "intelligence",
                "wisdom",
                "constitution",
                "charisma",
                "is_deleted",
                "created_at",
            ),
        )

    def test_the_editable_fields_hold_the_six(self):
        self.assertTrue(set(SHEET_ORDER) <= CHARACTER_FIELDS)

    def test_the_integer_bounds_cover_the_six_in_order(self):
        self.assertEqual(tuple(CHARACTER_INT_FIELD_MAX)[:6], SHEET_ORDER)
        self.assertEqual(tuple(CHARACTER_INT_FIELD_MIN)[:6], SHEET_ORDER)
        for name in SHEET_ORDER:
            self.assertEqual(CHARACTER_INT_FIELD_MAX[name], MAX_SMALLINT_FIELD_VALUE)
            self.assertEqual(CHARACTER_INT_FIELD_MIN[name], MIN_ATTRIBUTE_VALUE)

    def test_the_draft_ledger_keeps_its_field_order(self):
        self.assertEqual(
            DRAFT_FIELDS[:8],
            ("name", *SHEET_ORDER, "fatigue_roll"),
        )
        self.assertEqual(
            tuple(draft_defaults())[:8],
            ("name", *SHEET_ORDER, "fatigue_roll"),
        )
        defaults = draft_defaults()
        self.assertEqual([defaults[name] for name in SHEET_ORDER], [None] * 6)

    def test_the_six_are_a_game_masters_to_set_in_the_field_table(self):
        names = tuple(FIELD_RULES)
        start = names.index(SHEET_ORDER[0])
        self.assertEqual(names[start : start + 6], SHEET_ORDER)
        for name in SHEET_ORDER:
            self.assertIs(FIELD_RULES[name].author, FieldAuthor.GAME_MASTER)


#: The sites that name all six attributes on purpose: each is keyed by its
#: file and the function or assigned name that holds it, and the number is how
#: many six-name literals that holder is allowed. A further literal, in another
#: file, another holder or the same holder, is not exempt.
SITES_THAT_NAME_ALL_SIX = {
    # The source tuple itself.
    "characters/constants.py::CORE_ATTRIBUTE_FIELDS": 1,
    # Six formulas, one per attribute.
    "bestiary/translation.py::translate": 1,
    # Six values, one per name.
    "characters/skills.py::simulate_experience_years": 1,
}
SKIPPED_SOURCE_DIRECTORY_NAMES = frozenset({"migrations", "tests", "e2e"})
CONTAINER_NODES = (ast.Dict, ast.Tuple, ast.List, ast.Set)


def _strings_in(node: ast.AST) -> set[str]:
    """Every string literal anywhere under ``node``: keys, values and labels."""
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }


def _names_all_six(node: ast.AST) -> bool:
    return set(SHEET_ORDER) <= _strings_in(node)


class _SixNameSites(ast.NodeVisitor):
    """The smallest containers whose contents name all six attributes.

    A container counts when the strings anywhere beneath it (keys, values,
    labels, nested pairs) include all six names. The smallest such container is
    the site, so the tuple around it is not reported as well.
    """

    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.holders: list[str] = []
        self.found: list[str] = []

    def _inside(self, name: str, node: ast.AST) -> None:
        self.holders.append(name)
        self.generic_visit(node)
        self.holders.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._inside(node.name, node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._inside(node.name, node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._inside(node.name, node)

    def visit_Assign(self, node: ast.Assign) -> None:
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if names and not self.holders:
            self._inside(names[0], node)
        else:
            self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and not self.holders:
            self._inside(node.target.id, node)
        else:
            self.generic_visit(node)

    def _visit_container(self, node: ast.AST) -> None:
        smaller_site = any(
            isinstance(child, CONTAINER_NODES) and _names_all_six(child)
            for child in ast.walk(node)
            if child is not node
        )
        if _names_all_six(node) and not smaller_site:
            self.found.append(
                f"{self.relative_path}::{'.'.join(self.holders) or '<module>'}"
            )
        self.generic_visit(node)

    visit_Dict = _visit_container  # noqa: N815
    visit_Tuple = _visit_container  # noqa: N815
    visit_List = _visit_container  # noqa: N815
    visit_Set = _visit_container  # noqa: N815


def _sites_naming_all_six_attributes() -> Counter[str]:
    """How many non-test containers name all six, by ``path::holder``."""
    found = []
    for directory, subdirectories, filenames in os.walk(REPOSITORY_ROOT):
        subdirectories[:] = [
            name
            for name in subdirectories
            if not name.startswith(".")
            and name not in SKIPPED_DIRECTORY_NAMES
            and name not in SKIPPED_SOURCE_DIRECTORY_NAMES
        ]
        for filename in filenames:
            path = Path(directory) / filename
            if path.suffix != ".py" or filename.startswith("test_"):
                continue
            visitor = _SixNameSites(path.relative_to(REPOSITORY_ROOT).as_posix())
            visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
            found.extend(visitor.found)
    return Counter(found)


class NoListRestatesTheSixTest(SimpleTestCase):
    """A list or table naming all six attributes builds them from the one tuple."""

    def test_no_container_spells_all_six_but_the_listed_sites(self):
        self.assertEqual(
            {
                site: count
                for site, count in _sites_naming_all_six_attributes().items()
                if count != SITES_THAT_NAME_ALL_SIX.get(site, 0)
            },
            {},
            "build these from CHARACTER_ATTRIBUTE_FIELDS or CORE_ATTRIBUTE_FIELDS "
            "(splat, dict.fromkeys or a comprehension) instead of spelling the "
            "six names (issue #72)",
        )

    def test_every_listed_site_still_names_them(self):
        # A site that no longer spells the six leaves the list, so the list
        # cannot carry an exemption nothing needs.
        found = _sites_naming_all_six_attributes()
        self.assertEqual(
            SITES_THAT_NAME_ALL_SIX,
            {site: found[site] for site in SITES_THAT_NAME_ALL_SIX},
        )


class SheetAndSchemaKeepTheirOrderTest(SimpleTestCase):
    """The GM sheet's attribute lines and the extraction schema read the table."""

    def test_the_sheet_labels_keep_their_order(self):
        self.assertEqual(
            ATTRIBUTE_LABELS,
            (
                ("STR", "strength", "effective_strength"),
                ("DEX", "dexterity", "effective_dexterity"),
                ("INT", "intelligence", "effective_intelligence"),
                ("WIS", "wisdom", "effective_wisdom"),
                ("CON", "constitution", "effective_constitution"),
                ("CHR", "charisma", "effective_charisma"),
            ),
        )

    def test_every_effective_property_exists_and_charisma_is_unaged(self):
        for _label, _field, effective in ATTRIBUTE_LABELS:
            self.assertTrue(hasattr(Character, effective), effective)
        self.assertEqual(Character(charisma=11).effective_charisma, 11)

    def test_the_extraction_schema_lists_the_six_in_order_with_their_labels(self):
        for for_game_master in (False, True):
            fields = character_fields(for_game_master=for_game_master)
            names = [field.name for field in fields]
            self.assertEqual(names[0], "name")
            self.assertEqual(tuple(names[1:7]), SHEET_ORDER)
            self.assertEqual(names[7], "appearance_roll")
            for field, label in zip(
                fields[1:7], ("STR", "DEX", "INT", "WIS", "CON", "CHR"), strict=True
            ):
                self.assertTrue(
                    field.description.startswith(f"{label} as rolled or bought"),
                    field.description,
                )
