"""Which URLs the passphrase gate still covers, and which it no longer does.

Issue #305's third design question, pinned. The split is stated once in
``common.public_paths`` and enforced by
``common.middleware.PublicPathPassphraseMiddleware``; these tests check the
statement and the enforcement separately, because a correct list that the
middleware ignores would be worse than no list at all.

Every request here goes through a client that has NOT answered the gate.
"""

from django.test import Client, TestCase

from common.gate import DEV_GATE_PASSPHRASE
from common.middleware import PublicPathPassphraseMiddleware
from common.public_paths import is_public_path


class PublicPathRuleTest(TestCase):
    """The rule itself, without a request."""

    def test_the_arena_and_its_whole_subtree_are_public(self):
        for path in (
            "/tarmar/battles/",
            "/tarmar/battles/setup/",
            "/tarmar/battles/12/",
            "/tarmar/battles/12/state/",
            "/tarmar/battles/12/seats/3/claim/",
        ):
            self.assertTrue(is_public_path(path), path)

    def test_the_sign_in_doors_are_public(self):
        for path in (
            "/tarmar/accounts/login/",
            "/tarmar/accounts/logout/",
            "/tarmar/accounts/register/",
        ):
            self.assertTrue(is_public_path(path), path)

    def test_everything_else_stays_gated(self):
        for path in (
            "/tarmar/",
            "/tarmar/about/",
            "/tarmar/characters/",
            "/tarmar/parties/",
            "/tarmar/reference/",
            "/tarmar/export/",
            "/tarmar/gm/",
            "/tarmar/gm/bestiary/",
            "/tarmar/gm/encounters/",
            "/tarmar/gm/ruins/",
            "/tarmar/accounts/profile/",
            "/tarmar/accounts/users/",
            "/admin/",
        ):
            self.assertFalse(is_public_path(path), path)

    def test_the_root_is_public_by_exact_match_only(self):
        """The front door is public because on the classic-arena host it IS
        the arena's door. That must not turn into a public prefix."""
        self.assertTrue(is_public_path("/"))
        self.assertFalse(is_public_path("/tarmar/"))
        self.assertFalse(is_public_path("/admin/login/"))

    def test_a_lookalike_prefix_is_not_public(self):
        self.assertFalse(is_public_path("/tarmar/battlesecret/"))
        self.assertFalse(is_public_path("/tarmar/accounts/login-as-admin/"))


class GateEnforcementTest(TestCase):
    """The middleware, asked by a client that has never seen the gate."""

    def setUp(self):
        # Deliberately not BaseTestCase: that one answers the gate in
        # setUp, which is exactly the thing under test here.
        self.client = Client()

    def test_the_arena_opens_with_no_passphrase(self):
        response = self.client.get("/tarmar/battles/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Battle Royale Simulator")
        self.assertNotContains(response, "Speak, friend, and enter.")

    def test_the_sign_in_page_opens_with_no_passphrase(self):
        for path in ("/tarmar/accounts/login/", "/tarmar/accounts/register/"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertNotContains(
                response, "Speak, friend, and enter.", msg_prefix=path
            )

    def test_the_gated_rooms_still_ask(self):
        for path in (
            "/tarmar/",
            "/tarmar/characters/",
            "/tarmar/parties/",
            "/tarmar/reference/",
            "/tarmar/gm/bestiary/",
            "/tarmar/gm/encounters/",
        ):
            response = self.client.get(path)
            self.assertContains(
                response, "Speak, friend, and enter.", msg_prefix=f"Path {path}"
            )

    def test_the_root_redirects_rather_than_gating(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/tarmar/")


class GateFlagSurvivesAPublicPathTest(TestCase):
    """Signing out must not throw a gated visitor back to the gate.

    ``logout()`` flushes the session, and speak_friend puts the gate flag
    back afterwards. Sign-out is a public path now, so the parent never
    runs for it — the subclass has to do that bookkeeping itself, or every
    logout would re-lock the whole site behind the passphrase.
    """

    def test_logout_keeps_the_passphrase_answered(self):
        client = Client()
        client.post("/gate/", {"passphrase": DEV_GATE_PASSPHRASE})
        self.assertTrue(client.session.get(PublicPathPassphraseMiddleware.SESSION_FLAG))
        client.post("/tarmar/accounts/logout/")
        response = client.get("/tarmar/about/")
        self.assertNotContains(response, "Speak, friend, and enter.")
