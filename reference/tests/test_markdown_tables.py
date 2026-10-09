"""Tests for the shared markdown pipe-table parsing helpers."""

from django.test import SimpleTestCase

from reference.markdown_tables import (
    is_separator_row,
    split_table_row,
    table_rows_under_heading,
)


class SplitTableRowTest(SimpleTestCase):
    def test_strips_outer_pipes_and_cell_whitespace(self):
        self.assertEqual(split_table_row("  | a |  b c | d|  "), ["a", "b c", "d"])

    def test_keeps_empty_cells(self):
        self.assertEqual(split_table_row("| a |  | c |"), ["a", "", "c"])


class IsSeparatorRowTest(SimpleTestCase):
    def test_dashes_and_alignment_colons_are_a_separator(self):
        self.assertTrue(is_separator_row(split_table_row("|---|:--:| --: |")))

    def test_a_row_with_text_is_not_a_separator(self):
        self.assertFalse(is_separator_row(["---", "1"]))


class TableRowsUnderHeadingTest(SimpleTestCase):
    MARKDOWN = (
        "# Other\n| x | y |\n|---|---|\n| 0 | 0 |\n\n"
        "# Target\n\nIntro text.\n\n| Roll | Result |\n|:---|---:|\n"
        "| 1 | Goblin |\n| 2 | Ogre |\n\nAfter the table.\n| 9 | stray |\n"
    )

    def test_reads_the_table_after_the_heading_only(self):
        self.assertEqual(
            table_rows_under_heading(self.MARKDOWN, "# Target"),
            [["Roll", "Result"], ["1", "Goblin"], ["2", "Ogre"]],
        )

    def test_missing_heading_raises(self):
        with self.assertRaises(IndexError):
            table_rows_under_heading(self.MARKDOWN, "# Absent")
