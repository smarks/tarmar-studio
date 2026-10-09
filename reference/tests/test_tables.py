"""Tests for the rules-table annotation that replaces horizontal scrolling."""

from django.test import SimpleTestCase

from common.test_helpers import BaseTestCase
from reference.tables import annotate_tables


class AnnotateTablesTest(SimpleTestCase):
    """Unit tests for reference.tables.annotate_tables."""

    def test_adds_column_count_to_table(self):
        html = "<table><thead><tr><th>A</th><th>B</th></tr></thead>"
        html += "<tbody><tr><td>1</td><td>2</td></tr></tbody></table>"
        self.assertIn('<table data-columns="2">', annotate_tables(html))

    def test_labels_each_cell_with_its_column(self):
        html = "<table><thead><tr><th>Year</th><th>Merchant</th></tr></thead>"
        html += "<tbody><tr><td>1</td><td>Weapon</td></tr></tbody></table>"
        result = annotate_tables(html)
        self.assertIn('<td data-label="Year">1</td>', result)
        self.assertIn('<td data-label="Merchant">Weapon</td>', result)

    def test_labels_restart_on_every_row(self):
        html = "<table><thead><tr><th>Year</th><th>Track</th></tr></thead><tbody>"
        html += "<tr><td>1</td><td>Merchant</td></tr>"
        html += "<tr><td>2</td><td>Laborer</td></tr></tbody></table>"
        result = annotate_tables(html)
        self.assertIn('<td data-label="Year">2</td>', result)
        self.assertIn('<td data-label="Track">Laborer</td>', result)

    def test_header_markup_is_stripped_from_the_label(self):
        html = "<table><thead><tr><th><strong>XP</strong> Required</th></tr></thead>"
        html += "<tbody><tr><td>2000</td></tr></tbody></table>"
        result = annotate_tables(html)
        self.assertIn('<td data-label="XP Required">2000</td>', result)

    def test_header_entities_are_decoded_in_the_label(self):
        html = "<table><thead><tr><th>Coin &amp; Kit</th></tr></thead>"
        html += "<tbody><tr><td>5</td></tr></tbody></table>"
        result = annotate_tables(html)
        self.assertIn('data-label="Coin &amp; Kit"', result)

    def test_header_numeric_character_references_are_decoded(self):
        html = "<table><thead><tr><th>Penalty &#8722;1</th></tr></thead>"
        html += "<tbody><tr><td>STR</td></tr></tbody></table>"
        result = annotate_tables(html)
        self.assertIn('data-label="Penalty −1"', result)
        self.assertIn("&#8722;", result)

    def test_valueless_attributes_survive_the_round_trip(self):
        html = "<table><thead><tr><th>A</th></tr></thead>"
        html += "<tbody><tr><td nowrap>x</td></tr></tbody></table>"
        result = annotate_tables(html)
        self.assertIn("<td nowrap ", result)
        self.assertIn('data-label="A"', result)

    def test_quotes_in_a_header_are_escaped(self):
        html = '<table><thead><tr><th>The "Old" Way</th></tr></thead>'
        html += "<tbody><tr><td>x</td></tr></tbody></table>"
        result = annotate_tables(html)
        self.assertIn("&quot;Old&quot;", result)
        self.assertNotIn('data-label="The "Old" Way"', result)

    def test_extra_cells_beyond_the_header_are_left_unlabelled(self):
        html = "<table><thead><tr><th>Only</th></tr></thead>"
        html += "<tbody><tr><td>a</td><td>b</td></tr></tbody></table>"
        result = annotate_tables(html)
        self.assertIn('<td data-label="Only">a</td>', result)
        self.assertIn("<td>b</td>", result)

    def test_empty_header_produces_no_label(self):
        html = "<table><thead><tr><th></th><th>B</th></tr></thead>"
        html += "<tbody><tr><td>a</td><td>b</td></tr></tbody></table>"
        result = annotate_tables(html)
        self.assertIn("<td>a</td>", result)
        self.assertIn('<td data-label="B">b</td>', result)

    def test_existing_cell_attributes_are_preserved(self):
        html = "<table><thead><tr><th>A</th></tr></thead>"
        html += '<tbody><tr><td colspan="2">x</td></tr></tbody></table>'
        result = annotate_tables(html)
        self.assertIn('colspan="2"', result)
        self.assertIn('data-label="A"', result)

    def test_content_outside_tables_is_unchanged(self):
        html = "<h1>Skill Tracks</h1><p>Roll a d20.</p><hr /><br />"
        self.assertEqual(annotate_tables(html), html)

    def test_table_without_headers_still_gets_a_column_count(self):
        html = "<table><tbody><tr><td>a</td></tr></tbody></table>"
        result = annotate_tables(html)
        self.assertIn('<table data-columns="0">', result)
        self.assertIn("<td>a</td>", result)

    def test_nested_tables_keep_their_own_labels(self):
        inner = "<table><thead><tr><th>Inner</th></tr></thead>"
        inner += "<tbody><tr><td>i</td></tr></tbody></table>"
        html = "<table><thead><tr><th>Outer</th></tr></thead>"
        html += f"<tbody><tr><td>{inner}</td></tr></tbody></table>"
        result = annotate_tables(html)
        self.assertIn('data-label="Inner"', result)
        self.assertIn('data-label="Outer"', result)


class ReferenceTableRenderingTest(BaseTestCase):
    """The rendered reference page shows whole tables, not scrolling ones."""

    SKILL_TRACKS_URL = (
        "/tarmar/reference/public-rules/characters/prior-experience/skill-tracks/"
    )

    def test_skill_tracks_table_reports_nine_columns(self):
        response = self.client.get(self.SKILL_TRACKS_URL)
        self.assertContains(response, '<table data-columns="9">')

    def test_skill_tracks_cells_carry_their_track_name(self):
        response = self.client.get(self.SKILL_TRACKS_URL)
        self.assertContains(response, 'data-label="Hunter/Gatherer"')
        self.assertContains(response, 'data-label="Year"')

    def test_tables_are_not_wrapped_in_a_scrolling_container(self):
        response = self.client.get(self.SKILL_TRACKS_URL)
        self.assertNotContains(response, '<div class="table-wrap"><table>')
