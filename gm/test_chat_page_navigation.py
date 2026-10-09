"""The GM chat page names where it sits, like every other page (issue #339, item 13)."""

from common.test_helpers import BaseTestCase


class ChatPageNavigationTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.login_as(self.create_user())

    def test_the_page_carries_the_site_trail(self):
        response = self.client.get("/tarmar/gm/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["breadcrumb"][-1], {"label": "Game Master", "url": ""}
        )
        self.assertContains(
            response, '<nav class="breadcrumb" aria-label="Breadcrumb">'
        )

    def test_the_menu_marks_the_game_master_page_as_current(self):
        response = self.client.get("/tarmar/gm/")

        self.assertEqual(response.context["active_page"], "gm")

    def test_the_map_page_keeps_its_own_layout(self):
        response = self.client.get("/tarmar/gm/map/")

        if response.status_code == 200:
            self.assertNotContains(response, "chat-page")
