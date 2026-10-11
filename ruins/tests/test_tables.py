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


SIDE_BY_SIDE = """\
## Job Table

| d4  | Needs   | d2  | Catch     |
| --- | ------- | --- | --------- |
| 1   | courier | 1   | a trap    |
| 2   | guard   | 2   | a rival   |
| 3   | finder  |     |           |
| 4   | thief   |     |           |

## Truth

| d6  | Truth Level   |
| --- | ------------- |
| 1−2 | Mostly true   |
| 3-4 | Missing parts |
| 5   | Exaggerated   |
| 6   | Planted       |

## Prices

| d3  | Prices | d4  | Why     |
| --- | ------ | --- | ------- |
| 1   | low    | 1   | ships   |
| 2–3 | high   | 2   | weather |
|     |        | 3   | war     |
|     |        | 4   | taxes   |

## Motivation

| d2  | Wants   | Hindered by |
| --- | ------- | ----------- |
| 1   | revenge | an oath     |
| 2   | wealth  | a debt      |
"""


class GmToolsShapeParserTests(SimpleTestCase):
    """The shapes the other GM-tools pages use (issue #809)."""

    def setUp(self):
        self.tables = parse_tables(SIDE_BY_SIDE)

    def test_side_by_side_dice_are_separate_tables_in_page_order(self):
        self.assertEqual(
            list(self.tables),
            ["Needs", "Catch", "Truth Level", "Prices", "Why", "Wants"],
        )
        self.assertEqual(self.tables["Needs"].die, "d4")
        self.assertEqual(self.tables["Catch"].rows, ("a trap", "a rival"))
        self.assertEqual(self.tables["Catch"].section, "Job Table")

    def test_ranged_faces_fill_every_face_they_cover(self):
        truth = self.tables["Truth Level"]
        self.assertEqual(truth.row_for_face(1), "Mostly true")
        self.assertEqual(truth.row_for_face(2), "Mostly true")
        self.assertEqual(truth.row_for_face(4), "Missing parts")
        self.assertEqual(self.tables["Prices"].rows, ("low", "high", "high"))

    def test_a_shorter_left_group_leaves_the_right_group_whole(self):
        self.assertEqual(self.tables["Why"].rows, ("ships", "weather", "war", "taxes"))

    def test_extra_columns_are_read_off_the_same_roll(self):
        wants = self.tables["Wants"]
        self.assertEqual(wants.extra_columns, ("Hindered by",))
        self.assertEqual(
            wants.cells_for_face(2), (("Wants", "wealth"), ("Hindered by", "a debt"))
        )
        self.assertEqual(
            self.tables["Needs"].cells_for_face(1), (("Needs", "courier"),)
        )

    def test_overlapping_range_is_rejected(self):
        broken = "| d3 | Lap |\n| --- | --- |\n| 1-2 | a |\n| 2-3 | b |\n"
        with self.assertRaisesRegex(ValueError, "repeats face 2"):
            parse_tables(broken)

    def test_ranged_gap_is_rejected(self):
        broken = "| d6 | Gap |\n| --- | --- |\n| 1-2 | a |\n| 4-6 | b |\n"
        with self.assertRaisesRegex(ValueError, "misnumbered"):
            parse_tables(broken)

    def test_ranged_overlap_with_a_single_face_is_rejected(self):
        broken = "| d4 | Lap |\n| --- | --- |\n| 1−3 | a |\n| 3 | b |\n| 4 | c |\n"
        with self.assertRaisesRegex(ValueError, "repeats face 3"):
            parse_tables(broken)

    def test_reversed_range_is_named(self):
        broken = "| d4 | Back |\n| --- | --- |\n| 4-2 | a |\n| 1 | b |\n"
        with self.assertRaisesRegex(ValueError, "reversed range “4-2”"):
            parse_tables(broken)

    def test_empty_result_cell_is_rejected_naming_table_and_face(self):
        broken = "| d2 | Blank |\n| --- | --- |\n| 1 | a |\n| 2 |  |\n"
        with self.assertRaisesRegex(
            ValueError, "“Blank” has an empty “Blank” cell on face “2”"
        ):
            parse_tables(broken)

    def test_empty_extra_column_cell_is_rejected(self):
        broken = "| d2 | Wants | Hindered by |\n| --- | --- | --- |\n"
        broken += "| 1 | a | x |\n| 2 | b |  |\n"
        with self.assertRaisesRegex(ValueError, "empty “Hindered by” cell on face “2”"):
            parse_tables(broken)

    def test_results_without_a_face_are_rejected(self):
        broken = "| d2 | Gap | d2 | Other |\n| --- | --- | --- | --- |\n"
        broken += "| 1 | a | 1 | x |\n| 2 | b | | y |\n"
        with self.assertRaisesRegex(ValueError, "row with no face"):
            parse_tables(broken)

    def test_a_face_that_is_not_a_number_is_rejected_in_a_later_group(self):
        broken = "| d2 | A | d2 | B |\n| --- | --- | --- | --- |\n"
        broken += "| 1 | a | one | x |\n| 2 | b | 2 | y |\n"
        with self.assertRaisesRegex(ValueError, "not a number or a range"):
            parse_tables(broken)

    def test_a_die_with_no_title_is_rejected(self):
        broken = "| d2 | A | d2 |\n| --- | --- | --- |\n| 1 | a | 1 |\n"
        with self.assertRaisesRegex(ValueError, "no table title"):
            parse_tables(broken)
