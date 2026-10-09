"""Parser tests against the real markdown, plus the drift guard (issue #194).

The reference page is the single source of truth for the tables; the drift
guard asserts the page still publishes exactly the tables the generator rolls,
so a rename or deletion there fails here instead of silently changing output.
"""

from django.test import SimpleTestCase

from ruins.generation import EXPECTED_TABLE_TITLES, ORIGIN_TABLE
from ruins.tables import TABLES_MARKDOWN_PATH, load_tables, parse_tables


class DriftGuardTests(SimpleTestCase):
    def test_markdown_publishes_exactly_the_expected_tables(self):
        tables = parse_tables(TABLES_MARKDOWN_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            set(tables),
            set(EXPECTED_TABLE_TITLES),
            "dungeons-ruins.md tables drifted from ruins.generation",
        )

    def test_every_table_is_complete_and_rollable(self):
        for table in load_tables().values():
            self.assertGreaterEqual(table.die_sides, 2)
            self.assertEqual(len(table.rows), table.die_sides)
            self.assertTrue(all(row for row in table.rows), table.title)

    def test_load_tables_reads_the_reference_page(self):
        origin = load_tables()[ORIGIN_TABLE]
        self.assertEqual(origin.die, "d8")
        self.assertEqual(origin.section, "Ruin Origin")
        self.assertEqual(origin.row_for_face(1), origin.rows[0])


SYNTHETIC = """\
# Sample

Some prose.

## First Section

| d4  | Alpha |
| --- | ----- |
| 1   | one   |
| 2   | two   |
| 3   | three |
| 4   | four  |

| Name | Value |
| ---- | ----- |
| 7    | not a dice table |

## Second Section

| d2  | Beta |
| --- | ---- |
| 1   | yes  |
| 2   | no   |
"""


class ParserTests(SimpleTestCase):
    def test_parses_tables_with_sections_and_ignores_non_dice_tables(self):
        tables = parse_tables(SYNTHETIC)
        self.assertEqual(set(tables), {"Alpha", "Beta"})
        alpha = tables["Alpha"]
        self.assertEqual(alpha.section, "First Section")
        self.assertEqual(alpha.die_sides, 4)
        self.assertEqual(alpha.rows, ("one", "two", "three", "four"))
        self.assertEqual(tables["Beta"].section, "Second Section")

    def test_row_for_face_rejects_out_of_range_faces(self):
        alpha = parse_tables(SYNTHETIC)["Alpha"]
        self.assertEqual(alpha.row_for_face(4), "four")
        with self.assertRaises(ValueError):
            alpha.row_for_face(0)
        with self.assertRaises(ValueError):
            alpha.row_for_face(5)

    def test_missing_face_is_rejected(self):
        broken = "| d3 | Gap |\n| --- | --- |\n| 1 | a |\n| 3 | c |\n"
        with self.assertRaisesRegex(ValueError, "misnumbered"):
            parse_tables(broken)

    def test_extra_face_is_rejected(self):
        broken = "| d2 | Over |\n| --- | --- |\n| 1 | a |\n| 2 | b |\n| 3 | c |\n"
        with self.assertRaisesRegex(ValueError, "misnumbered"):
            parse_tables(broken)

    def test_repeated_face_is_rejected(self):
        broken = "| d2 | Twice |\n| --- | --- |\n| 1 | a |\n| 1 | b |\n"
        with self.assertRaisesRegex(ValueError, "repeats face"):
            parse_tables(broken)

    def test_duplicate_table_title_is_rejected(self):
        broken = (
            "| d2 | Same |\n| --- | --- |\n| 1 | a |\n| 2 | b |\n\n"
            "| d2 | Same |\n| --- | --- |\n| 1 | c |\n| 2 | d |\n"
        )
        with self.assertRaisesRegex(ValueError, "Duplicate table title"):
            parse_tables(broken)

    def test_single_cell_and_stray_rows_are_ignored(self):
        text = "| lonely |\n| 5 | orphan row with no table |\n"
        self.assertEqual(parse_tables(text), {})
