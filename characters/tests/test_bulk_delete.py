"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

from characters.models import Character
from common.test_helpers import BaseTestCase


class BulkDeleteCharactersTest(BaseTestCase):
    """Tests for bulk character deletion."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.char1 = Character.objects.create(
            user=self.user,
            name="Character 1",
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            appearance_roll="3",
            height_roll="4",
            weight_roll="3",
            fatigue_roll=7,
        )
        self.char2 = Character.objects.create(
            user=self.user,
            name="Character 2",
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            appearance_roll="3",
            height_roll="4",
            weight_roll="3",
            fatigue_roll=7,
        )

    def test_bulk_delete_success(self):
        """Can soft delete multiple characters at once."""
        self.login_as(self.user)
        response = self.post_json(
            "/tarmar/characters/bulk-delete/",
            {"ids": [self.char1.pk, self.char2.pk]},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["deleted"], 2)
        # Characters still exist but are marked as deleted
        self.assertEqual(Character.objects.filter(user=self.user).count(), 2)
        self.assertEqual(
            Character.objects.filter(user=self.user, is_deleted=False).count(), 0
        )
        self.char1.refresh_from_db()
        self.assertTrue(self.char1.is_deleted)
        self.assertIsNotNone(self.char1.deleted_at)

    def test_bulk_delete_single(self):
        """Can soft delete a single character via bulk endpoint."""
        self.login_as(self.user)
        response = self.post_json(
            "/tarmar/characters/bulk-delete/",
            {"ids": [self.char1.pk]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deleted"], 1)
        # One deleted, one not
        self.assertEqual(
            Character.objects.filter(user=self.user, is_deleted=False).count(), 1
        )

    def test_bulk_delete_only_own_characters(self):
        """Cannot delete other users' characters."""
        other_user = self.create_user("other")
        other_char = Character.objects.create(
            user=other_user,
            name="Other's Character",
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            appearance_roll="3",
            height_roll="4",
            weight_roll="3",
            fatigue_roll=7,
        )

        self.login_as(self.user)
        response = self.post_json(
            "/tarmar/characters/bulk-delete/",
            {"ids": [self.char1.pk, other_char.pk]},
        )

        self.assertEqual(response.status_code, 200)
        # Only deletes own character, not other user's
        self.assertEqual(response.json()["deleted"], 1)
        self.assertTrue(Character.objects.filter(pk=other_char.pk).exists())

    def test_dm_bulk_delete_other_users_characters(self):
        """A DM can bulk-delete characters owned by other users."""
        dm = self.create_user("dm")
        dm.is_dm = True
        dm.save()

        self.login_as(dm)
        response = self.post_json(
            "/tarmar/characters/bulk-delete/",
            {"ids": [self.char1.pk, self.char2.pk]},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["deleted"], 2)
        self.char1.refresh_from_db()
        self.char2.refresh_from_db()
        self.assertTrue(self.char1.is_deleted)
        self.assertTrue(self.char2.is_deleted)

    def test_dm_bulk_delete_reports_true_count(self):
        """The DM's reported count reflects only rows that actually matched."""
        dm = self.create_user("dm")
        dm.is_dm = True
        dm.save()
        # Already deleted -- shouldn't count again.
        self.char2.is_deleted = True
        self.char2.save()

        self.login_as(dm)
        response = self.post_json(
            "/tarmar/characters/bulk-delete/",
            {"ids": [self.char1.pk, self.char2.pk, 999999]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deleted"], 1)
        self.char1.refresh_from_db()
        self.assertTrue(self.char1.is_deleted)

    def test_dm_bulk_delete_logs_cross_user_deletes(self):
        """DM bulk-deleting other users' characters gets an audit-log entry
        each, matching the parity single delete_character already has
        (issue #162)."""
        dm = self.create_user("dm")
        dm.is_dm = True
        dm.save()

        self.login_as(dm)
        with self.assertLogs("origami.audit", level="INFO") as captured:
            response = self.post_json(
                "/tarmar/characters/bulk-delete/",
                {"ids": [self.char1.pk, self.char2.pk]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(captured.output), 2)
        for line in captured.output:
            self.assertIn("CHARACTER_DELETE", line)
            self.assertIn(f"user={dm.username}", line)
            self.assertIn(f"owned by user={self.user.username}", line)

    def test_bulk_delete_own_characters_does_not_log(self):
        """A user bulk-deleting their own characters isn't a DM action, so
        no cross-user audit entry is written."""
        self.login_as(self.user)
        with self.assertNoLogs("origami.audit", level="INFO"):
            response = self.post_json(
                "/tarmar/characters/bulk-delete/",
                {"ids": [self.char1.pk, self.char2.pk]},
            )

        self.assertEqual(response.status_code, 200)

    def test_non_dm_cannot_bulk_delete_others_characters(self):
        """A non-DM's bulk-delete of another user's characters reports 0, deletes nothing."""
        other_user = self.create_user("other2")
        other_char = Character.objects.create(
            user=other_user,
            name="Other2's Character",
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            appearance_roll="3",
            height_roll="4",
            weight_roll="3",
            fatigue_roll=7,
        )

        self.login_as(self.user)
        response = self.post_json(
            "/tarmar/characters/bulk-delete/",
            {"ids": [other_char.pk]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deleted"], 0)
        other_char.refresh_from_db()
        self.assertFalse(other_char.is_deleted)

    def test_bulk_delete_requires_login(self):
        """Bulk delete requires authentication."""
        response = self.client.post(
            "/tarmar/characters/bulk-delete/",
            {"ids": [self.char1.pk]},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)

    def test_bulk_delete_empty_ids(self):
        """Bulk delete with empty ids fails."""
        self.login_as(self.user)
        response = self.post_json(
            "/tarmar/characters/bulk-delete/",
            {"ids": []},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("No character IDs", response.json()["error"])

    def test_bulk_delete_invalid_json(self):
        """Bulk delete with invalid JSON fails."""
        self.login_as(self.user)
        response = self.client.post(
            "/tarmar/characters/bulk-delete/",
            "not valid json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid JSON", response.json()["error"])

    def test_bulk_delete_ignores_ids_that_are_not_primary_keys(self):
        """Issue #641: a non-integer member raised ValueError, so a 500.

        Reachable by any logged-in user with a hand-built body. What will not
        coerce to a primary key cannot name a row, so it is dropped and the
        ones that do name rows are still deleted.
        """
        self.login_as(self.user)

        response = self.post_json(
            "/tarmar/characters/bulk-delete/",
            {"ids": ["not-a-number", str(self.char1.pk), None, True]},
        )

        self.assertEqual(response.status_code, 200)
        self.char1.refresh_from_db()
        self.assertTrue(self.char1.is_deleted)
        self.char2.refresh_from_db()
        self.assertFalse(self.char2.is_deleted)

    def test_bulk_delete_refuses_a_body_naming_no_primary_key(self):
        self.login_as(self.user)

        response = self.post_json(
            "/tarmar/characters/bulk-delete/",
            {"ids": ["abc", {"pk": 1}]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("No character IDs", response.json()["error"])
