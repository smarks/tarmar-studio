"""The error pages share one base, and a refused account gets a real 403 page.

Issue #339, item 14: a player opening a Game Master page got Django's bare
"403 Forbidden", because there was no 403 template. Issue #338, item 23: the
404 and 500 pages each carried their own copy of the same reset, layout and
link styles. All three now extend ``error_base.html``.
"""

from django.template.loader import render_to_string
from django.test import override_settings

from common.test_helpers import BaseTestCase

GAME_MASTER_PAGE = "/tarmar/characters/deleted/"


class ForbiddenPageTest(BaseTestCase):
    def test_a_player_refused_a_game_master_page_gets_the_403_page(self):
        self.login_as(self.create_user())

        response = self.client.get(GAME_MASTER_PAGE)

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "403.html")
        self.assertTemplateUsed(response, "error_base.html")
        self.assertContains(response, "You shall not pass.", status_code=403)
        self.assertContains(response, 'href="/"', status_code=403)
        self.assertNotContains(
            response, "<title>403 Forbidden</title>", status_code=403
        )


class NotFoundPageTest(BaseTestCase):
    @override_settings(DEBUG=False)
    def test_the_404_page_still_renders_through_the_shared_base(self):
        self.login_as(self.create_user())

        response = self.client.get("/tarmar/no-such-page-anywhere/")

        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, "404.html")
        self.assertTemplateUsed(response, "error_base.html")
        self.assertContains(
            response, "Not all those who wander are lost.", status_code=404
        )
        self.assertContains(response, "/tarmar/no-such-page-anywhere/", status_code=404)


class ServerErrorPageTest(BaseTestCase):
    def test_the_500_page_renders_with_no_context(self):
        # Django's server_error view renders it with an empty context, so it
        # must not need a request or any context processor.
        html = render_to_string("500.html")

        self.assertIn("A Balrog of Morgoth.", html)
        self.assertIn("<title>500 — Tarmar Studio</title>", html)
        self.assertIn("text-shadow: 0 0 20px", html)
