"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

from accounts.models import User
from common.test_helpers import BaseTestCase


class AuthorizationOrderTest(BaseTestCase):
    """Tests for authorization check ordering."""

    def setUp(self):
        super().setUp()
        self.player = self.create_player()
        self.target = self.create_user(username="targetuser")

    def test_player_cannot_update_other_user_profile(self):
        """Players should get 403 when trying to update another user's profile."""
        self.login_as(self.player)
        response = self.post_json(
            f"/tarmar/accounts/profile/{self.target.pk}/update-field/",
            {"field": "real_name", "value": "Hacked Name"},
        )
        self.assertEqual(response.status_code, 403)
        # Verify target user was not modified
        self.target.refresh_from_db()
        self.assertNotEqual(self.target.real_name, "Hacked Name")

    def test_player_cannot_delete_other_user(self):
        """Players should get 403 when trying to delete another user."""
        self.login_as(self.player)
        response = self.client.post(f"/tarmar/accounts/users/{self.target.pk}/delete/")
        self.assertEqual(response.status_code, 403)
        # Verify target user still exists
        self.assertTrue(User.objects.filter(pk=self.target.pk).exists())
