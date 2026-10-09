"""Every paginated list shows its pages (issue #339, item 29).

The users list, party list and deleted-characters page each declare
paginate_by = 50 but rendered no page links, so anything past the fiftieth
entry could not be reached. They share the character list's pager now.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase
from django.urls import reverse

from characters.models import Character
from common.css_contrast import stylesheet, stylesheet_names
from common.test_helpers import BaseTestCase, create_character
from parties.models import Party

#: Repository root, for reading the templates that include the pager.
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent


class ListPaginationTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.create_admin()
        self.login_as(self.admin)

    def assert_pager(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Page 1 of 2")
        self.assertContains(response, 'rel="next"')

    def test_the_party_list_shows_its_pages(self):
        Party.objects.bulk_create(
            Party(name=f"Company {number}", owner=self.admin) for number in range(51)
        )

        self.assert_pager(self.client.get(reverse("parties:list")))

    def test_the_users_list_shows_its_pages(self):
        for number in range(51):
            self.create_user(f"reader{number:02d}")

        self.assert_pager(self.client.get(reverse("users_list")))

    def test_the_deleted_characters_page_shows_its_pages(self):
        owner = self.create_user("many_deleted")
        for number in range(51):
            character = create_character(owner, name=f"Gone {number}")
            Character.objects.filter(pk=character.pk).update(is_deleted=True)

        self.assert_pager(self.client.get(reverse("characters:deleted")))

    def test_a_short_list_shows_no_pager(self):
        response = self.client.get(reverse("parties:list"))

        self.assertNotContains(response, 'class="pagination"')


class ThePagerIsStyledOnEveryListThatIncludesItTest(SimpleTestCase):
    """The pager's look belongs to the shared partial, not to one page (#735).

    ``templates/_pagination.html`` is included by six templates across five
    apps, and the rules that make ``.page-current`` a filled chip lived in
    ``character-list.css``, which one of those six loads. On the other five --
    the bestiary catalog twice, the party list, the deleted-characters page,
    the users list and the GM review list -- the pager rendered as bare text
    with bare links: no chip, no fill, no padding.

    ``base.html`` links ``base.css`` on every page in the app, so that is the
    one sheet every paginated list is guaranteed to have. These hold the rules
    there and nowhere else: a second copy in a page sheet is the same defect
    waiting to be reintroduced, and a shared partial with two stylists has no
    single source of truth for how it looks.
    """

    def included_by(self) -> list[Path]:
        """Every template that includes ``_pagination.html``.

        Returns:
            Paths of the templates, in name order, at least five of them.
        """
        templates = sorted(
            path
            for path in REPOSITORY_ROOT.glob("**/templates/**/*.html")
            if "_pagination.html" in path.read_text()
            and path.name != "_pagination.html"
        )
        self.assertGreater(
            len(templates), 4, "the pager is meant to be a shared partial"
        )
        return templates

    def test_the_pager_is_included_from_more_than_one_app(self):
        """The premise: a partial used by one app could live in that app's sheet."""
        apps = {
            path.relative_to(REPOSITORY_ROOT).parts[0] for path in self.included_by()
        }

        self.assertGreater(len(apps), 1, sorted(apps))

    def test_base_css_makes_the_current_page_a_filled_chip(self):
        rules = re.findall(
            r"\.pagination\s+\.page-current\s*\{([^}]*)\}", stylesheet("base.css")
        )

        self.assertEqual(len(rules), 2, "the chip's padding rule and its fill rule")
        self.assertIn("--colour-accent-teal", "".join(rules))

    def test_no_page_sheet_keeps_a_second_copy_of_the_pagers_rules(self):
        names = stylesheet_names()
        self.assertGreater(len(names), 10)
        for name in names:
            if name == "base.css":
                continue
            with self.subTest(sheet=name):
                self.assertNotIn(
                    ".pagination",
                    stylesheet(name),
                    f"{name} styles the shared pager; base.css owns it (#735)",
                )
