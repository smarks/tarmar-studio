"""The character sheet's heading and menu treatment (issue #160).

Two things the e2e suite asserts about the edit page were never true: the page
had no ``h1`` at all, and ``CharacterEditView.active_page`` was set to a value
(``"character_editor"``) that no menu entry matched, so nothing was ever marked
as the current page there.
"""

from characters.models import Character
from common.test_helpers import BaseTestCase

CHARACTER_DEFAULTS = {
    "strength": 12,
    "dexterity": 12,
    "intelligence": 12,
    "wisdom": 12,
    "constitution": 12,
    "charisma": 12,
    "appearance_roll": "3",
    "height_roll": "3",
    "weight_roll": "3",
    "fatigue_roll": 7,
}


class CharacterEditPageChromeTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.character = Character.objects.create(
            user=self.user, name="Aldwin the Bold", **CHARACTER_DEFAULTS
        )
        self.login_as(self.user)
        self.response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        self.html = self.response.content.decode()

    def test_the_page_has_a_heading(self):
        self.assertEqual(self.response.status_code, 200)
        self.assertIn("<h1", self.html)

    def test_the_heading_is_the_editable_name_field(self):
        """One heading, and it is the name — not a second decorative title."""
        self.assertEqual(self.html.count("<h1"), 1)
        self.assertIn(
            '<h1 class="editable-field name-field" data-field="name">', self.html
        )

    def test_the_menu_marks_the_character_editor_as_current(self):
        self.assertIn(">Character Editor</a>", self.html)
        self.assertIn(
            f'href="/tarmar/characters/{self.character.pk}/" class="submenu disabled"',
            self.html,
        )

    def test_the_menu_leaves_the_characters_link_usable(self):
        """`.disabled` sets pointer-events: none, so the way back must stay live."""
        self.assertIn('href="/tarmar/characters/" class="submenu"', self.html)

    def test_the_character_editor_entry_is_absent_from_other_pages(self):
        list_page = self.client.get("/tarmar/characters/").content.decode()
        self.assertNotIn("Character Editor", list_page)

    def test_no_raw_template_comment_leaks_into_the_page(self):
        """Django only honors single-line {# #} comments; a multi-line one
        renders literally (this bit the name-field heading once)."""
        self.assertNotIn("{#", self.html)
        self.assertNotIn("#}", self.html)
