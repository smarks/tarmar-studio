"""The host-aware front door (issue #269, unification phase 5).

On a classic-arena host the front door IS the arena: the root URL lands on
the battle list, login without a ``next`` returns there, and the menu is
arena-shaped. The default host keeps the studio's landing and nav; a sign-in
there lands on the player's journal (issue #952), while the root still opens
onto the public front page. One source of truth decides which is which -- the
same ``THEME_HOSTS`` mapping the theme reads -- so a host cannot wear one
door's skin and open onto the other's rooms.
"""

from django.test import override_settings

from common.test_helpers import TEST_PASSWORD, BaseTestCase

ARENA_HOST = "melee.example.com"

arena_settings = override_settings(
    ALLOWED_HOSTS=["testserver", ARENA_HOST],
    THEME_HOSTS={ARENA_HOST: "classic-arena"},
)


@arena_settings
class FrontDoorLandingTest(BaseTestCase):
    """Where the root URL sends a visitor, per host."""

    def test_default_host_root_still_lands_on_the_studio_home(self):
        response = self.client.get("/")
        self.assertRedirects(response, "/tarmar/")

    def test_arena_host_root_lands_on_the_battle_list(self):
        self.login_as(self.create_user())
        response = self.client.get("/", HTTP_HOST=ARENA_HOST)
        self.assertRedirects(response, "/tarmar/battles/")

    def test_arena_host_root_signed_out_lands_in_the_arena(self):
        """The arena needs no account since issue #305, so the anonymous
        chain now ends *at* the battle list rather than at a login door."""
        response = self.client.get("/", HTTP_HOST=ARENA_HOST, follow=True)
        chain = [url for url, status in response.redirect_chain]
        self.assertEqual(chain, ["/tarmar/battles/"])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Battle Royale Simulator")


@arena_settings
class LoginLandsPerHostTest(BaseTestCase):
    """Login, logout, and register return the visitor to their own door."""

    def _login(self, host):
        self.create_user()
        return self.client.post(
            "/tarmar/accounts/login/",
            {"username": "testuser", "password": TEST_PASSWORD},
            follow=True,
            HTTP_HOST=host,
        )

    def test_login_on_default_host_lands_on_the_journal(self):
        """Issue #952: the journal, not the front page, after signing in."""
        response = self._login("testserver")
        self.assertEqual(response.request["PATH_INFO"], "/tarmar/journal/")

    def test_the_default_host_root_stays_the_front_page_once_signed_in(self):
        """Signing in moves where a login lands, not where the root opens."""
        self._login("testserver")
        response = self.client.get("/")
        self.assertRedirects(response, "/tarmar/")

    def test_an_explicit_next_still_wins_on_the_default_host(self):
        self.create_user()
        response = self.client.post(
            "/tarmar/accounts/login/?next=/tarmar/characters/",
            {"username": "testuser", "password": TEST_PASSWORD},
        )
        self.assertRedirects(response, "/tarmar/characters/")

    def test_an_offsite_next_is_ignored_and_the_journal_is_used(self):
        self.create_user()
        response = self.client.post(
            "/tarmar/accounts/login/?next=https://elsewhere.example.com/",
            {"username": "testuser", "password": TEST_PASSWORD},
        )
        self.assertRedirects(response, "/tarmar/journal/")

    def test_a_signed_in_visit_to_login_goes_to_the_journal(self):
        """The login page sends an account already signed in on, too."""
        self.login_as(self.create_user())
        response = self.client.get("/tarmar/accounts/login/")
        self.assertRedirects(response, "/tarmar/journal/")

    def test_login_on_arena_host_lands_on_the_battle_list(self):
        response = self._login(ARENA_HOST)
        self.assertEqual(response.request["PATH_INFO"], "/tarmar/battles/")

    def test_an_explicit_next_still_wins_on_the_arena_host(self):
        self.create_user()
        response = self.client.post(
            "/tarmar/accounts/login/?next=/tarmar/characters/",
            {"username": "testuser", "password": TEST_PASSWORD},
            HTTP_HOST=ARENA_HOST,
        )
        self.assertRedirects(response, "/tarmar/characters/")

    def test_logout_returns_to_the_front_door_not_the_studio(self):
        """Logout targets the root, which routes per host -- on the arena
        that means back toward the arena, never a hard-coded /tarmar/."""
        self.login_as(self.create_user())
        response = self.client.post("/tarmar/accounts/logout/", HTTP_HOST=ARENA_HOST)
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_register_on_arena_host_lands_on_the_battle_list(self):
        response = self.client.post(
            "/tarmar/accounts/register/",
            {
                "username": "newfighter",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
            },
            follow=True,
            HTTP_HOST=ARENA_HOST,
        )
        self.assertEqual(response.request["PATH_INFO"], "/tarmar/battles/")

    def test_register_on_default_host_lands_on_the_journal(self):
        response = self.client.post(
            "/tarmar/accounts/register/",
            {
                "username": "newscribe",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
            },
            follow=True,
        )
        self.assertEqual(response.request["PATH_INFO"], "/tarmar/journal/")

    def test_register_with_an_offsite_next_ignores_it_and_lands_on_the_journal(
        self,
    ):
        response = self.client.post(
            "/tarmar/accounts/register/?next=https://elsewhere.example.com/",
            {
                "username": "newwanderer",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
            },
        )
        self.assertRedirects(response, "/tarmar/journal/")

    def test_register_with_an_explicit_next_goes_there(self):
        response = self.client.post(
            "/tarmar/accounts/register/?next=/tarmar/characters/",
            {
                "username": "newwriter",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
            },
        )
        self.assertRedirects(response, "/tarmar/characters/")

    def test_register_on_arena_host_with_next_goes_there(self):
        response = self.client.post(
            "/tarmar/accounts/register/?next=/tarmar/battles/new/",
            {
                "username": "newchallenger",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
            },
            HTTP_HOST=ARENA_HOST,
        )
        self.assertEqual(response["Location"], "/tarmar/battles/new/")


@arena_settings
class ArenaNavTest(BaseTestCase):
    """The menu is arena-shaped on the arena host, studio-shaped elsewhere.

    Assertions pin menu link *hrefs*, which appear nowhere else on the pages
    fetched here: /tarmar/about/ is a plain prose page on both hosts.
    """

    PAGE = "/tarmar/about/"

    def test_arena_menu_leads_with_battles_and_hides_the_studio_rooms(self):
        self.login_as(self.create_player())
        response = self.client.get(self.PAGE, HTTP_HOST=ARENA_HOST)
        self.assertContains(response, 'href="/tarmar/battles/"')
        self.assertNotContains(response, 'href="/tarmar/characters/"')
        self.assertNotContains(response, 'href="/tarmar/reference/"')
        self.assertNotContains(response, 'href="/tarmar/gm/"')

    def test_arena_menu_keeps_profile_logout_and_a_quiet_studio_crosslink(self):
        self.login_as(self.create_player())
        response = self.client.get(self.PAGE, HTTP_HOST=ARENA_HOST)
        self.assertContains(response, 'href="/tarmar/accounts/profile/"')
        self.assertContains(response, 'form="logout-form"')
        # The cross-link to the studio: quiet, but present.
        self.assertContains(response, 'href="/tarmar/">Tarmar Studio</a>')

    def test_arena_menu_gives_the_dm_user_admin_but_a_player_nothing_extra(self):
        self.login_as(self.create_dm())
        response = self.client.get(self.PAGE, HTTP_HOST=ARENA_HOST)
        self.assertContains(response, 'href="/tarmar/accounts/users/"')

        self.logout()
        self.login_as(self.create_player())
        response = self.client.get(self.PAGE, HTTP_HOST=ARENA_HOST)
        self.assertNotContains(response, 'href="/tarmar/accounts/users/"')

    def test_arena_menu_signed_out_offers_register_and_login(self):
        response = self.client.get(self.PAGE, HTTP_HOST=ARENA_HOST)
        self.assertContains(response, 'href="/tarmar/accounts/register/"')
        self.assertContains(response, 'href="/tarmar/accounts/login/"')

    def test_default_host_menu_is_untouched(self):
        self.login_as(self.create_player())
        response = self.client.get(self.PAGE)
        self.assertContains(response, 'href="/tarmar/characters/"')
        self.assertContains(response, 'href="/tarmar/reference/"')
        self.assertContains(response, 'href="/tarmar/gm/"')
        self.assertNotContains(response, 'href="/tarmar/">Tarmar Studio</a>')


@arena_settings
class ArenaLandingBreadcrumbTest(BaseTestCase):
    """The battle list is the arena's front page, and a front page shows no
    trail -- the same convention the studio home already follows. On the
    default host the list keeps its Tarmar-rooted trail."""

    def test_no_breadcrumb_on_the_arena_front_page(self):
        self.login_as(self.create_user())
        response = self.client.get("/tarmar/battles/", HTTP_HOST=ARENA_HOST)
        self.assertNotContains(response, 'class="breadcrumb"')

    def test_default_host_battle_list_keeps_its_trail(self):
        self.login_as(self.create_user())
        response = self.client.get("/tarmar/battles/")
        self.assertContains(response, 'class="breadcrumb"')
