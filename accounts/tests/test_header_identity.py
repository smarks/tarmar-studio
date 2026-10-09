"""Header identity block (issue #200).

Every page's header shows, beside the hamburger, who is signed in: the
username, a badge for each role the account holds, and when this session
began. The block renders only for authenticated users — anonymous visitors
get the header exactly as it was.
"""

from django.template.defaultfilters import date as date_filter
from django.utils import timezone

from common.test_helpers import BaseTestCase

PROFILE_URL = "/tarmar/accounts/profile/"
LOGIN_URL = "/tarmar/accounts/login/"


class HeaderIdentityTest(BaseTestCase):
    """The identity block next to the hamburger menu."""

    def test_anonymous_header_has_no_identity_block(self):
        """Anonymous visitors see no identity block at all."""
        response = self.client.get(LOGIN_URL)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "header-identity")
        self.assertNotContains(response, "role-badge")

    def test_username_shown_next_to_hamburger(self):
        """The signed-in username appears in the identity block."""
        player = self.create_player()
        self.login_as(player)
        response = self.client.get(PROFILE_URL)
        self.assertContains(response, 'class="header-identity"')
        self.assertContains(
            response,
            '<span class="identity-username">playeruser</span>',
            html=True,
        )

    def test_single_role_user_gets_exactly_one_badge(self):
        """A player-only account shows the Player badge and no other."""
        player = self.create_player()
        self.login_as(player)
        response = self.client.get(PROFILE_URL)
        content = response.content.decode()
        self.assertContains(
            response, '<span class="role-badge">Player</span>', html=True
        )
        self.assertEqual(content.count("role-badge"), 1)

    def test_multi_role_user_shows_all_badges(self):
        """An account holding all three roles gets all three badges."""
        everything = self.create_user(
            "everyrole", is_role_admin=True, is_dm=True, is_player=True
        )
        self.login_as(everything)
        response = self.client.get(PROFILE_URL)
        for badge in ("Admin", "DM", "Player"):
            self.assertContains(
                response,
                f'<span class="role-badge">{badge}</span>',
                html=True,
            )
        self.assertEqual(response.content.decode().count("role-badge"), 3)

    def test_signed_in_since_shows_this_sessions_login_time(self):
        """The block reads "Signed in since <last_login>".

        Django refreshes ``last_login`` during login, before any page
        renders, so for the viewing user the raw value IS the moment the
        current session began — which is exactly what the label claims.
        """
        player = self.create_player()
        self.login_as(player)
        player.refresh_from_db()
        expected = date_filter(timezone.localtime(player.last_login), "M d, Y H:i")
        response = self.client.get(PROFILE_URL)
        self.assertContains(response, "Signed in since")
        self.assertContains(response, expected)
