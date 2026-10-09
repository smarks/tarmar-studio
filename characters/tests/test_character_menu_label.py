"""The menu does not offer an editor to someone who can only read (issue #339, item 34).

Opening another player's public character showed "Character Editor" in the
menu, though the page renders read-only for that viewer. The entry reads
"Character Sheet" for them; the owner still sees "Character Editor".
"""

from django.urls import reverse

from common.test_helpers import BaseTestCase, create_character


class CharacterMenuLabelTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("sheet_owner")
        self.character = create_character(self.owner, name="Open Book", is_public=True)
        self.url = reverse("characters:edit", args=[self.character.pk])

    def test_a_reader_sees_character_sheet(self):
        self.login_as(self.create_user("sheet_reader"))

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_edit"])
        self.assertContains(response, ">Character Sheet</a>")
        self.assertNotContains(response, ">Character Editor</a>")

    def test_the_owner_sees_character_editor(self):
        self.login_as(self.owner)

        response = self.client.get(self.url)

        self.assertTrue(response.context["can_edit"])
        self.assertContains(response, ">Character Editor</a>")
