"""The signed-in home page shows the player's own table (issue #827).

After signing in, ``/tarmar/`` used to show the same hero and link row as the
signed-out page and nothing that belonged to the player. It now carries a
"Your table" block rendered from the database: the player's characters (each
playable one with a way to the Games Master), their latest Games Master
sessions and their battles, with a first step for a player who has none.
"""

import re

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from battle.models import Battle, BattleCombatant
from common.test_helpers import BaseTestCase, create_character
from gm.models import GMSession

HOME_URL = "/tarmar/"


class HomeTableTest(BaseTestCase):
    """What the "Your table" block lists, and for whom."""

    def setUp(self) -> None:
        super().setUp()
        self.player = self.create_user("table-player")
        self.other = self.create_user("table-other")

    def home_html(self) -> str:
        self.login_as(self.player)
        response = self.client.get(HOME_URL)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_a_signed_out_visitor_gets_no_table(self) -> None:
        html = self.client.get(HOME_URL).content.decode()

        self.assertNotIn('id="your-table"', html)

    def test_a_new_player_is_offered_a_first_character(self) -> None:
        html = self.home_html()

        self.assertIn('id="your-table"', html)
        self.assertIn(
            f'href="{reverse("characters:create")}" class="button" '
            'id="your-table-first-character"',
            html,
        )
        self.assertIn("Create your first character", html)
        self.assertIn("No Game Master sessions yet", html)
        self.assertIn("No battles yet", html)

    def test_the_players_own_characters_are_listed_with_their_sheets(self) -> None:
        wren = create_character(self.player, name="Wren")
        create_character(self.other, name="Stranger", is_public=True)
        create_character(self.player, name="Buried", is_deleted=True)

        html = self.home_html()

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

        html = self.home_html()

        offered = set(re.findall(r'data-character-id="(\d+)"', html))
        self.assertEqual(offered, {str(ready.pk)})
        self.assertIn("Incomplete", html)
        self.assertIn("Deceased", html)

    def test_a_long_roster_links_to_the_whole_list(self) -> None:
        for number in range(8):
            create_character(self.player, name=f"Hero {number}")

        html = self.home_html()

        self.assertEqual(html.count('class="your-table-character"'), 6)
        self.assertIn("All 8 characters", html)
        self.assertIn(f'href="{reverse("characters:list")}"', html)

    def test_the_three_latest_sessions_are_listed(self) -> None:
        sessions = [
            GMSession.objects.create(user=self.player, name=f"Session {number}")
            for number in range(4)
        ]
        GMSession.objects.create(user=self.other, name="Not yours")

        html = self.home_html()

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

        html = self.home_html()

        self.assertIn(f'href="{reverse("battle:detail", args=[made.pk])}"', html)
        self.assertIn(f'href="{reverse("battle:detail", args=[seated.pk])}"', html)
        self.assertNotIn("Somebody else", html)
        self.assertNotIn("Cleared away", html)

    def test_the_public_page_below_is_unchanged(self) -> None:
        """The links and the getting-started list stay for everyone."""
        signed_in = self.home_html()
        self.logout()
        signed_out = self.client.get(HOME_URL).content.decode()

        for page in (signed_in, signed_out):
            self.assertIn('class="home-nav"', page)
            self.assertIn('class="get-started"', page)
            self.assertIn("tarmar01.jpeg", page)


class HomeTableQueryCountTest(BaseTestCase):
    """The roster's Play buttons cost no query per character (review of #827)."""

    def queries_for(self, character_count: int) -> int:
        player = self.create_user(f"counted-{character_count}")
        for number in range(character_count):
            create_character(player, name=f"Hero {number}")
        self.logout()
        self.login_as(player)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(HOME_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content.decode().count("data-character-id"), character_count
        )
        return len(captured.captured_queries)

    def test_one_character_and_six_cost_the_same(self) -> None:
        self.assertEqual(self.queries_for(6), self.queries_for(1))
