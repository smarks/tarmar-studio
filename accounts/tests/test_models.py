"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

from accounts.models import User
from common.test_helpers import TEST_PASSWORD, BaseTestCase


class FieldLimitsTest(BaseTestCase):
    """Negative tests to verify field length limits are enforced."""

    def setUp(self):
        super().setUp()
        self.valid_data = {
            "username": "validuser",
            "password1": "SecurePass123!",
            "password2": "SecurePass123!",
        }

    def _register(self, **overrides):
        data = {**self.valid_data, **overrides}
        return self.client.post("/tarmar/accounts/register/", data)

    def test_username_over_25_chars_rejected(self):
        """Username over 25 characters should be rejected."""
        response = self._register(username="a" * 26)
        self.assertEqual(response.status_code, 200)  # Form re-rendered with error
        self.assertFalse(User.objects.filter(username="a" * 26).exists())

    def test_username_at_25_chars_accepted(self):
        """Username at exactly 25 characters should be accepted."""
        response = self._register(username="a" * 25)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="a" * 25).exists())

    def test_password_over_128_chars_rejected(self):
        """Password over 128 characters should be rejected."""
        long_pass = "a" * 129
        response = self._register(password1=long_pass, password2=long_pass)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="validuser").exists())

    def test_password_at_128_chars_accepted(self):
        """Password at exactly 128 characters should be accepted."""
        long_pass = "a" * 128
        response = self._register(
            username="passuser", password1=long_pass, password2=long_pass
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="passuser").exists())

    def test_real_name_over_100_chars_rejected(self):
        """Real name over 100 characters should be rejected."""
        response = self._register(username="nameuser", real_name="a" * 101)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="nameuser").exists())

    def test_real_name_at_100_chars_accepted(self):
        """Real name at exactly 100 characters should be accepted."""
        response = self._register(username="nameuser2", real_name="a" * 100)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="nameuser2").exists())

    def test_email_over_254_chars_rejected(self):
        """Email over 254 characters should be rejected."""
        long_email = "a" * 243 + "@example.com"  # 255 chars
        response = self._register(username="emailuser", email=long_email)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="emailuser").exists())

    def test_email_at_254_chars_accepted(self):
        """Email at exactly 254 characters should be accepted."""
        long_email = "a" * 242 + "@example.com"  # 254 chars
        response = self._register(username="emailuser2", email=long_email)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="emailuser2").exists())

    def test_phone_over_20_chars_rejected(self):
        """Phone over 20 characters should be rejected."""
        response = self._register(username="phoneuser", phone="1" * 21)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="phoneuser").exists())

    def test_phone_at_20_chars_accepted(self):
        """Phone at exactly 20 characters should be accepted."""
        response = self._register(username="phoneuser2", phone="1" * 20)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="phoneuser2").exists())

    def test_discord_over_32_chars_rejected(self):
        """Discord over 32 characters should be rejected (Discord limit)."""
        response = self._register(username="discorduser", discord="a" * 33)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="discorduser").exists())

    def test_discord_at_32_chars_accepted(self):
        """Discord at exactly 32 characters should be accepted (Discord limit)."""
        response = self._register(username="discorduser2", discord="a" * 32)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="discorduser2").exists())


class RolesTest(BaseTestCase):
    """Tests for user roles."""

    def test_new_user_has_player_role(self):
        """New registered user should have player role by default."""
        self.client.post(
            "/tarmar/accounts/register/",
            {
                "username": "newplayer",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
            },
        )
        user = User.objects.get(username="newplayer")
        self.assertTrue(user.is_player)
        self.assertFalse(user.is_dm)
        self.assertFalse(user.is_role_admin)

    def test_user_can_have_multiple_roles(self):
        """User can have multiple roles."""
        user = self.create_user(
            username="multirole", is_player=True, is_dm=True, is_role_admin=True
        )
        user.refresh_from_db()
        self.assertTrue(user.is_player)
        self.assertTrue(user.is_dm)
        self.assertTrue(user.is_role_admin)

    def test_user_can_have_single_role(self):
        """User can have only one role."""
        user = self.create_user(
            username="singlerole", is_player=False, is_dm=True, is_role_admin=False
        )
        user.refresh_from_db()
        self.assertFalse(user.is_player)
        self.assertTrue(user.is_dm)
        self.assertFalse(user.is_role_admin)

    def test_profile_displays_roles(self):
        """Profile page should display user roles."""
        user = self.create_user(username="roleuser")
        self.login_as(user)
        response = self.client.get("/tarmar/accounts/profile/")
        self.assertContains(response, "Roles")
        self.assertContains(response, "Player")


class UnitTestModels(BaseTestCase):
    """Unit tests for models."""

    def test_user_str(self):
        """User string representation should be username."""
        user = self.create_user(username="struser")
        self.assertEqual(str(user), "struser")

    def test_user_default_roles(self):
        """New user should have correct default roles."""
        user = self.create_user(username="defaultrole")
        self.assertTrue(user.is_player)
        self.assertFalse(user.is_dm)
        self.assertFalse(user.is_role_admin)

    def test_user_contact_choices(self):
        """User model should have correct contact choices."""
        expected_choices = [
            ("email", "Email"),
            ("phone", "Phone"),
            ("discord", "Discord"),
        ]
        self.assertEqual(User.CONTACT_CHOICES, expected_choices)

    def test_user_preferred_contact_display(self):
        """User should return correct preferred contact display."""
        user = self.create_user(username="prefuser", preferred_contact="email")
        self.assertEqual(user.get_preferred_contact_display(), "Email")

    def test_contact_choices_template_tag(self):
        """contact_choices template tag returns User.CONTACT_CHOICES."""
        from accounts.templatetags.account_tags import contact_choices

        self.assertEqual(contact_choices(), User.CONTACT_CHOICES)
