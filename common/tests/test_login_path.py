"""The login path is declared once and settings take it from there."""

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import reverse

from common.public_paths import LOGIN_PATH, is_public_path


class LoginPathTest(SimpleTestCase):
    def test_matches_the_routed_login_view(self):
        self.assertEqual(reverse("login"), LOGIN_PATH)

    def test_settings_send_visitors_to_it(self):
        self.assertEqual(settings.LOGIN_URL, LOGIN_PATH)
        self.assertEqual(settings.LOGOUT_REDIRECT_URL, LOGIN_PATH)

    def test_it_is_outside_the_passphrase_gate(self):
        self.assertTrue(is_public_path(LOGIN_PATH))
