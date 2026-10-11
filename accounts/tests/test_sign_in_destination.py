"""A sign-in must end somewhere the visitor can actually get to (issue #694).

``/tarmar/battles/`` is public and offers "Sign in -- either one keeps the
battles you have going here". Following the site menu's Login link, which
carries no ``?next=``, sent a correct password to ``LOGIN_REDIRECT_URL`` --
the front door -- and the front door is behind the passphrase gate. The
visitor was answered with "Speak, friend, and enter", with nothing saying the
sign-in had worked, nothing saying a second secret existed, and no way back.

These tests hold the three parts of the answer: an explicit ``next`` still
wins, a gateless visitor lands on a public page, and a visitor who has already
answered the passphrase keeps the ordinary landing -- the player's journal
since issue #952.
"""

from django.test import TestCase
from django.test.client import Client

from accounts.models import User
from common.middleware import GATE_SESSION_FLAG

LOGIN_URL = "/tarmar/accounts/login/"
REGISTER_URL = "/tarmar/accounts/register/"
BATTLES_URL = "/tarmar/battles/"
PASSWORD = "gate-trap-password-1"


class SignInDestinationTest(TestCase):
    """Where a completed sign-in puts a visitor who has not passed the gate."""

    def setUp(self) -> None:
        # Deliberately NOT BaseTestCase: that sets the gate flag for every
        # test, which is exactly the condition this class has to vary.
        self.client = Client()
        self.user = User.objects.create_user(username="gatetrap", password=PASSWORD)

    def _sign_in(self, **extra: str):
        return self.client.post(
            LOGIN_URL, {"username": self.user.username, "password": PASSWORD, **extra}
        )

    def test_a_sign_in_with_no_next_lands_somewhere_public(self) -> None:
        response = self._sign_in()

        self.assertRedirects(
            response, BATTLES_URL, msg_prefix="a fresh sign-in met the gate"
        )

    def test_an_explicit_next_still_wins(self) -> None:
        """The arena's own Sign in links carry one, and it must keep working."""
        response = self._sign_in(next="/tarmar/battles/1/")

        self.assertEqual(response["Location"], "/tarmar/battles/1/")

    def test_a_visitor_who_has_passed_the_gate_lands_on_the_journal(self) -> None:
        session = self.client.session
        session[GATE_SESSION_FLAG] = True
        session.save()

        response = self._sign_in()

        self.assertEqual(response["Location"], "/tarmar/journal/")

    def test_registering_also_lands_somewhere_public(self) -> None:
        """Registration is public for the arena's sake; its landing was not."""
        response = self.client.post(
            REGISTER_URL,
            {
                "username": "brandnew",
                "password1": "brand-new-password-1",
                "password2": "brand-new-password-1",
            },
        )

        self.assertRedirects(
            response, BATTLES_URL, msg_prefix="a new account met the gate"
        )


class GateAcknowledgesASignedInVisitorTest(TestCase):
    """Reaching the gate signed in should say so, not stare back (issue #694)."""

    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(username="atthedoor", password=PASSWORD)

    def test_the_gate_names_the_signed_in_account_and_offers_a_way_back(self) -> None:
        self.client.force_login(self.user)

        response = self.client.get("/tarmar/")

        self.assertContains(response, "Signed in as")
        self.assertContains(response, "atthedoor")
        self.assertContains(response, BATTLES_URL)

    def test_a_signed_out_visitor_is_told_none_of_that(self) -> None:
        response = self.client.get("/tarmar/")

        self.assertNotContains(response, "Signed in as")

    def test_the_passphrase_field_has_a_label(self) -> None:
        """Its accessible name was the placeholder, "..." (#716, low finding)."""
        response = self.client.get("/tarmar/")

        self.assertContains(response, 'for="gate-passphrase"')
        self.assertContains(response, 'id="gate-passphrase"')
