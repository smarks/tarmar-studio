"""The stacked-card table layout on phones is defined once (#338 item 15).

The users list and the character list each carried the same ~40-line media
block that turns a table into labelled cards. It lives in base.css as
``table.table-stack`` now; these tests keep a page sheet from growing a copy.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
CSS_DIRECTORY = ROOT / "static" / "css"

# reference.css stacks rendered markdown tables at its own breakpoint with a
# different card (a fixed label column, zebra rows), so it is not a copy.
OWN_STACKED_LAYOUT = {"reference.css"}


def _without_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


class TableStackTest(SimpleTestCase):
    def test_base_defines_the_stacked_card_layout(self):
        base = _without_comments((CSS_DIRECTORY / "base.css").read_text())

        self.assertIn("table.table-stack td[data-label]::before", base)
        self.assertIn("table.table-stack thead", base)

    def test_no_other_page_sheet_labels_cells_from_data_label(self):
        sheets = sorted(CSS_DIRECTORY.glob("*.css"))
        self.assertGreater(len(sheets), 10)
        for sheet in sheets:
            if sheet.name == "base.css" or sheet.name in OWN_STACKED_LAYOUT:
                continue
            with self.subTest(sheet=sheet.name):
                self.assertNotIn(
                    "attr(data-label)", _without_comments(sheet.read_text())
                )

    def test_the_stacked_tables_ask_for_it(self):
        for template, table_class in (
            ("accounts/templates/accounts/users_list.html", "users-table"),
            ("characters/templates/characters/character_list.html", "character-table"),
        ):
            with self.subTest(template=template):
                self.assertIn(
                    f'<table class="{table_class} table-stack">',
                    (ROOT / template).read_text(),
                )
