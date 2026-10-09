"""Guards for the stylesheet extraction of issue #93.

The design system used to live in an inline ``<style>`` block in
``templates/base.html``, with each page appending its own rules through
``{% block extra_styles %}``. Those blocks are now ``static/css/*.css`` files
linked with ``{% static %}``. What these tests protect is the thing that made
the move risky: the order the browser parses the rules in, and the fact that
every sheet a template names actually exists on disk.
"""

import re
from pathlib import Path

from django.contrib.staticfiles import finders
from django.test import TestCase

from characters.models import Character
from common.test_helpers import BaseTestCase

STYLESHEET_HREF_PATTERN = re.compile(
    r"""<link[^>]*rel=["']stylesheet["'][^>]*href=["']([^"']+)["']"""
)
MAIN_TAG_PATTERN = re.compile(r"<main([^>]*)>")


def stylesheet_hrefs(response) -> list[str]:
    """Every stylesheet URL in the response, in document order."""
    return STYLESHEET_HREF_PATTERN.findall(response.content.decode())


class BaseStylesheetTest(BaseTestCase):
    """base.css is linked on every page, ahead of anything a page adds."""

    def test_base_stylesheet_is_linked(self):
        response = self.client.get("/tarmar/")
        self.assertEqual(stylesheet_hrefs(response)[0], "/static/css/base.css")

    def test_base_stylesheet_precedes_page_stylesheets(self):
        user = self.create_admin(username="cascadeadmin")
        self.login_as(user)
        response = self.client.get("/tarmar/accounts/users/")
        hrefs = stylesheet_hrefs(response)
        self.assertEqual(hrefs, ["/static/css/base.css", "/static/css/users-list.css"])

    def test_character_list_width_follows_the_viewer_through_a_class(self):
        """A manager's list is wider than a player's, by base.css's page widths.

        The width used to be an inline ``main { max-width: ... }`` chosen by a
        template tag, which had to keep parsing after base.css to win. The
        template now names one of base.css's ``main.page-width-*`` classes
        (issue #338 item 24), which wins wherever it loads; both branches of
        the ``{% if is_manager %}`` are exercised.
        """
        cases = (
            (self.create_player(username="cascadeplayer"), "page-width-800"),
            (self.create_admin(username="cascademanager"), "page-width-1000"),
        )
        for user, expected_class in cases:
            with self.subTest(username=user.username):
                # Logging a second user in flushes the session; this helper
                # clears it and puts the passphrase gate flag back.
                self.logout()
                self.login_as(user)
                response = self.client.get("/tarmar/characters/")
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                self.assertIn(f'<main class="{expected_class}">', html)
                self.assertNotIn("max-width", html.split("<main", 1)[0])


class ReferenceStylesheetTest(BaseTestCase):
    """The reference pages share one sheet and add one of their own."""

    def test_reference_index_links_shared_then_page_sheet(self):
        response = self.client.get("/tarmar/reference/")
        self.assertEqual(
            stylesheet_hrefs(response),
            [
                "/static/css/base.css",
                "/static/css/reference-shared.css",
                "/static/css/reference.css",
            ],
        )

    def test_reference_search_links_shared_then_page_sheet(self):
        response = self.client.get("/tarmar/reference/search/?q=sword")
        self.assertEqual(
            stylesheet_hrefs(response),
            [
                "/static/css/base.css",
                "/static/css/reference-shared.css",
                "/static/css/reference-search.css",
            ],
        )

    def test_reference_pages_main_carries_only_its_own_classes(self):
        """Why splitting the shared run past the page's ``main`` rule is safe.

        The reference pages' ``display: flex; gap: 2rem`` layout sits after the
        shared sheet, which only matters if some shared rule can win the same
        property on the same element. Since issue #338 item 24 that rule is
        scoped to the page's own class (``main.reference-page``,
        ``main.reference-search-page``) and the width comes from base.css's
        ``page-width-*`` class. No shared selector names either, so, as when
        ``<main>`` carried no attributes at all, none can match it.
        """
        shared = (
            Path(__file__).resolve().parents[2]
            / "static"
            / "css"
            / "reference-shared.css"
        ).read_text()
        cases = (
            ("/tarmar/reference/", "reference-page page-width-1200"),
            (
                "/tarmar/reference/search/?q=sword",
                "reference-search-page page-width-1000",
            ),
        )
        for url, expected_classes in cases:
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                main_tag = MAIN_TAG_PATTERN.search(html)
                self.assertIsNotNone(main_tag, "page rendered no <main> element")
                assert main_tag is not None  # narrows the type for pyright
                self.assertEqual(
                    main_tag.group(1).strip(), f'class="{expected_classes}"'
                )
                for class_name in expected_classes.split():
                    self.assertNotIn(class_name, shared)


class CharacterStylesheetTest(BaseTestCase):
    """The character pages each link their own extracted sheet."""

    def setUp(self):
        super().setUp()
        self.user = self.create_player(username="sheetplayer")
        self.login_as(self.user)

    def test_character_edit_links_its_sheet(self):
        character = Character.objects.create(
            user=self.user,
            name="Stylesheet Subject",
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
        )
        response = self.client.get(f"/tarmar/characters/{character.pk}/")
        self.assertEqual(
            stylesheet_hrefs(response),
            ["/static/css/base.css", "/static/css/character-edit.css"],
        )

    def test_character_create_links_its_sheet(self):
        response = self.client.get("/tarmar/characters/create/")
        self.assertEqual(
            stylesheet_hrefs(response),
            ["/static/css/base.css", "/static/css/character-create.css"],
        )


class AccountStylesheetTest(BaseTestCase):
    """The account pages each link their own extracted sheet."""

    def test_home_links_its_sheet(self):
        response = self.client.get("/tarmar/")
        self.assertEqual(
            stylesheet_hrefs(response),
            ["/static/css/base.css", "/static/css/home.css"],
        )

    def test_profile_links_its_sheet(self):
        user = self.create_player(username="profileplayer")
        self.login_as(user)
        response = self.client.get("/tarmar/accounts/profile/")
        self.assertEqual(
            stylesheet_hrefs(response),
            ["/static/css/base.css", "/static/css/profile.css"],
        )


class StylesheetFilesExistTest(TestCase):
    """Every sheet the templates name resolves through the staticfiles finders."""

    def test_every_extracted_stylesheet_is_findable(self):
        for name in (
            "base.css",
            "chat.css",
            "character-create.css",
            "character-edit.css",
            "character-list.css",
            "home.css",
            "profile.css",
            "reference-search.css",
            "reference-shared.css",
            "reference.css",
            "users-list.css",
        ):
            with self.subTest(stylesheet=name):
                self.assertIsNotNone(finders.find(f"css/{name}"))
