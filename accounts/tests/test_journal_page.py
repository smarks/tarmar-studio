"""The signed-in player's journal, on its own page (issues #827, #924, #952).

#827 put a block on the signed-in home page, rendered from the database: the
player's characters (each playable one with a way to the Games Master), their
latest Games Master sessions and their battles, with a first step for a
player who has none. #924 moved it to a page of its own, reached from the site
menu, so the front page is the same for everyone. #952 named it the journal,
at ``/tarmar/journal/``, kept its old address as a permanent redirect, and
made a sign-in on the default host land there.
"""

import re

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.views import player_journal
from battle.models import Battle, BattleCombatant
from common.test_helpers import BaseTestCase, create_character
from gm.models import GMSession

HOME_URL = "/tarmar/"
JOURNAL_URL = "/tarmar/journal/"
#: The page's address before #952.
OLD_TABLE_URL = "/tarmar/table/"
ARENA_HOST = "melee.example.com"
MENU_ENTRY = f'<a href="{JOURNAL_URL}" class="submenu">Journal</a>'


def menu_of(html: str) -> str:
    """The site menu's markup, cut out of a whole page.

    Args:
        html: A rendered page.

    Returns:
        Everything inside ``<nav class="menu" id="menu">``.
    """
    start = html.index('<nav class="menu" id="menu">')
    return html[start : html.index("</nav>", start)]


def main_of(html: str) -> str:
    """The page's ``<main>`` element, without the header and menu around it.

    Args:
        html: A rendered page.

    Returns:
        The ``<main>`` element's markup.
    """
    start = html.index("<main ")
    return html[start : html.index("</main>", start)]


class JournalPageAccessTest(BaseTestCase):
    """Who reaches the journal page, what it is given, and where it is linked."""

    def setUp(self) -> None:
        super().setUp()
        self.player = self.create_user("journal-reader")

    def test_a_signed_out_visitor_is_sent_to_sign_in_and_back(self) -> None:
        response = self.client.get(JOURNAL_URL)

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={JOURNAL_URL}",
            fetch_redirect_response=False,
        )

    def test_a_player_is_given_the_journal_player_journal_builds(self) -> None:
        create_character(self.player, name="Wren")
        GMSession.objects.create(user=self.player, name="First night")
        Battle.objects.create(name="Made here", seed=1, created_by=self.player)
        self.login_as(self.player)

        response = self.client.get(JOURNAL_URL)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/journal.html")
        self.assertTemplateUsed(response, "accounts/_journal.html")
        self.assertEqual(
            response.context["journal"], player_journal(response.wsgi_request)
        )
        self.assertEqual(response.context["active_page"], "journal")
        self.assertEqual(
            response.context["breadcrumb"],
            [
                {"label": "Tarmar", "url": HOME_URL},
                {"label": "Your journal", "url": ""},
            ],
        )

    def test_the_old_address_is_a_permanent_redirect_to_the_journal(self) -> None:
        self.login_as(self.player)

        response = self.client.get(OLD_TABLE_URL)

        self.assertEqual(response.status_code, 301)
        self.assertRedirects(response, JOURNAL_URL, status_code=301)

    def test_the_old_address_still_sends_a_signed_out_visitor_to_sign_in(
        self,
    ) -> None:
        response = self.client.get(OLD_TABLE_URL, follow=True)

        self.assertEqual(
            response.redirect_chain,
            [(JOURNAL_URL, 301), (f"{reverse('login')}?next={JOURNAL_URL}", 302)],
        )

    def test_the_page_carries_the_journal_names(self) -> None:
        """Every id, class, heading and title says journal (issue #952)."""
        self.login_as(self.player)

        html = self.client.get(JOURNAL_URL).content.decode()

        self.assertIn("<title>Your journal", html)
        self.assertIn(
            '<section class="journal" id="journal" aria-labelledby="journal-heading">',
            html,
        )
        self.assertIn('<h1 id="journal-heading">Your journal</h1>', html)
        self.assertIn('class="journal-parts"', html)
        self.assertIn('class="journal-part"', html)
        self.assertIn('id="journal-first-character"', html)
        self.assertIn('class="journal-empty"', html)
        self.assertNotIn("your-table", html)
        self.assertNotIn("Your table", html)
        self.assertNotIn("Your Table", html)

    def test_the_front_page_carries_no_journal_signed_in_or_out(self) -> None:
        signed_out = self.client.get(HOME_URL)
        self.login_as(self.player)
        signed_in = self.client.get(HOME_URL)

        for response in (signed_out, signed_in):
            self.assertNotIn("journal", response.context)
            self.assertNotIn('id="journal"', response.content.decode())
            self.assertTemplateNotUsed(response, "accounts/_journal.html")
        self.assertEqual(
            main_of(signed_in.content.decode()), main_of(signed_out.content.decode())
        )

    def test_the_menu_offers_the_journal_only_once_signed_in(self) -> None:
        signed_out = menu_of(self.client.get(HOME_URL).content.decode())
        self.login_as(self.player)
        signed_in = menu_of(self.client.get(HOME_URL).content.decode())
        on_the_page = menu_of(self.client.get(JOURNAL_URL).content.decode())

        self.assertNotIn(JOURNAL_URL, signed_out)
        self.assertIn(MENU_ENTRY, signed_in)
        # Beside Characters and Parties, after them.
        self.assertLess(signed_in.index(">Parties</a>"), signed_in.index(MENU_ENTRY))
        self.assertIn(
            f'<a href="{JOURNAL_URL}" class="submenu disabled">Journal</a>',
            on_the_page,
        )

    @override_settings(
        ALLOWED_HOSTS=["testserver", ARENA_HOST],
        THEME_HOSTS={ARENA_HOST: "classic-arena"},
    )
    def test_the_arena_menu_shows_no_journal_as_it_shows_no_characters(self) -> None:
        self.login_as(self.player)

        menu = menu_of(
            self.client.get(
                reverse("battle:list"), HTTP_HOST=ARENA_HOST
            ).content.decode()
        )

        self.assertNotIn(reverse("characters:list"), menu)
        self.assertNotIn(JOURNAL_URL, menu)


class JournalPageTest(BaseTestCase):
    """What the journal page lists."""

    def setUp(self) -> None:
        super().setUp()
        self.player = self.create_user("journal-player")
        self.other = self.create_user("journal-other")

    def journal_html(self) -> str:
        self.login_as(self.player)
        response = self.client.get(JOURNAL_URL)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_a_new_player_is_offered_a_first_character(self) -> None:
        html = self.journal_html()

        self.assertIn('id="journal"', html)
        self.assertIn(
            f'href="{reverse("characters:create")}" class="button" '
            'id="journal-first-character"',
            html,
        )
        self.assertIn("Create your first character", html)
        self.assertIn("No Game Master sessions yet", html)
        self.assertIn("No battles yet", html)

    def test_the_players_own_characters_are_listed_with_their_sheets(self) -> None:
        wren = create_character(self.player, name="Wren")
        create_character(self.other, name="Stranger", is_public=True)
        create_character(self.player, name="Buried", is_deleted=True)

        html = self.journal_html()

        self.assertIn(
            f'<a href="{reverse("characters:edit", args=[wren.pk])}">Wren</a>', html
        )
        self.assertNotIn("Stranger", html)
        self.assertNotIn("Buried", html)
        self.assertNotIn("Create your first character", html)

    def test_only_a_playable_living_character_offers_the_games_master(self) -> None:
        ready = create_character(self.player, name="Ready")
        create_character(self.player, name="Unfinished", fatigue_roll=None)
        create_character(self.player, name="Fallen", is_alive=False)

        html = self.journal_html()

        offered = set(re.findall(r'data-character-id="(\d+)"', html))
        self.assertEqual(offered, {str(ready.pk)})
        self.assertIn("Incomplete", html)
        self.assertIn("Deceased", html)

    def test_a_long_roster_links_to_the_whole_list(self) -> None:
        for number in range(8):
            create_character(self.player, name=f"Hero {number}")

        html = self.journal_html()

        self.assertEqual(html.count('class="journal-character"'), 6)
        self.assertIn("All 8 characters", html)
        self.assertIn(f'href="{reverse("characters:list")}"', html)

    def test_the_three_latest_sessions_are_listed(self) -> None:
        sessions = [
            GMSession.objects.create(user=self.player, name=f"Session {number}")
            for number in range(4)
        ]
        GMSession.objects.create(user=self.other, name="Not yours")

        html = self.journal_html()

        chat_url = reverse("gm:chat")
        for session in sessions[1:]:
            self.assertIn(f'href="{chat_url}?session={session.pk}"', html)
        self.assertNotIn(f"?session={sessions[0].pk}", html)
        self.assertNotIn("Not yours", html)

    def test_battles_the_player_made_or_sits_in_are_listed(self) -> None:
        made = Battle.objects.create(name="Made here", seed=1, created_by=self.player)
        seated = Battle.objects.create(
            name="Seated here", seed=2, created_by=self.other
        )
        BattleCombatant.objects.create(
            battle=seated, display_name="Wren", player=self.player
        )
        Battle.objects.create(name="Somebody else's", seed=3, created_by=self.other)
        Battle.objects.create(
            name="Cleared away", seed=4, created_by=self.player, is_deleted=True
        )

        html = self.journal_html()

        self.assertIn(f'href="{reverse("battle:detail", args=[made.pk])}"', html)
        self.assertIn(f'href="{reverse("battle:detail", args=[seated.pk])}"', html)
        self.assertNotIn("Somebody else", html)
        self.assertNotIn("Cleared away", html)

    def test_the_journal_page_carries_nothing_of_the_front_page(self) -> None:
        html = self.journal_html()

        self.assertNotIn('class="home-nav"', html)
        self.assertNotIn('class="get-started"', html)
        self.assertNotIn("tarmar01.jpeg", html)

    def test_the_front_page_keeps_its_public_content_for_everyone(self) -> None:
        """The links and the getting-started list stay, signed in or out."""
        self.login_as(self.player)
        signed_in = self.client.get(HOME_URL).content.decode()
        self.logout()
        signed_out = self.client.get(HOME_URL).content.decode()

        for page in (signed_in, signed_out):
            self.assertIn('class="home-nav"', page)
            self.assertIn('class="get-started"', page)
            self.assertIn("tarmar01.jpeg", page)


class JournalPageQueryCountTest(BaseTestCase):
    """The roster's Play buttons cost no query per character (review of #827)."""

    def queries_for(self, character_count: int) -> int:
        player = self.create_user(f"counted-{character_count}")
        for number in range(character_count):
            create_character(player, name=f"Hero {number}")
        self.logout()
        self.login_as(player)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(JOURNAL_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content.decode().count("data-character-id"), character_count
        )
        return len(captured.captured_queries)

    def test_one_character_and_six_cost_the_same(self) -> None:
        self.assertEqual(self.queries_for(6), self.queries_for(1))
