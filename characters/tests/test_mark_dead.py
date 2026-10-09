"""Tests for the DM mark-dead / revive endpoints (issue #24)."""

from characters.models import Character
from common.test_helpers import BaseTestCase


class MarkDeadAndReviveTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("char-owner")
        self.dm = self.create_dm("test-dm")
        self.character = Character.objects.create(
            user=self.owner,
            name="Doomed Hero",
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
        )

    # ---- mark_dead permission ----

    def test_non_dm_cannot_mark_dead(self):
        self.login_as(self.owner)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/mark-dead/",
            {"cause": "Anything"},
        )
        self.assertEqual(response.status_code, 403)
        self.character.refresh_from_db()
        self.assertTrue(self.character.is_alive)

    def test_dm_can_mark_dead_with_cause(self):
        self.login_as(self.dm)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/mark-dead/",
            {"cause": "Slain by goblin chief"},
        )
        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertFalse(self.character.is_alive)
        self.assertEqual(self.character.death_cause, "Slain by goblin chief")

    def test_mark_dead_requires_cause(self):
        self.login_as(self.dm)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/mark-dead/",
            {"cause": "  "},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("required", response.json()["error"])

    def test_mark_dead_caps_cause_at_max_length(self):
        self.login_as(self.dm)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/mark-dead/",
            {"cause": "x" * 201},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("200 characters", response.json()["error"])

    def test_mark_dead_rejects_already_dead(self):
        self.character.is_alive = False
        self.character.death_cause = "Old wound"
        self.character.save()
        self.login_as(self.dm)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/mark-dead/",
            {"cause": "Different cause"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("already dead", response.json()["error"])

    # ---- revive permission ----

    def test_dm_can_revive(self):
        self.character.is_alive = False
        self.character.death_cause = "Slain"
        self.character.save()
        self.login_as(self.dm)
        response = self.post_json(f"/tarmar/characters/{self.character.pk}/revive/", {})
        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertTrue(self.character.is_alive)
        self.assertEqual(self.character.death_cause, "")

    def test_non_dm_cannot_revive(self):
        self.character.is_alive = False
        self.character.save()
        self.login_as(self.owner)
        response = self.post_json(f"/tarmar/characters/{self.character.pk}/revive/", {})
        self.assertEqual(response.status_code, 403)

    def test_revive_rejects_already_alive(self):
        self.login_as(self.dm)
        response = self.post_json(f"/tarmar/characters/{self.character.pk}/revive/", {})
        self.assertEqual(response.status_code, 400)
        self.assertIn("already alive", response.json()["error"])

    # ---- UI surface ----

    def test_mark_dead_button_appears_for_dm_when_alive(self):
        self.login_as(self.dm)
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        self.assertContains(response, 'id="mark-dead-btn"')
        self.assertNotContains(response, 'id="revive-btn"')

    def test_revive_button_appears_for_dm_when_dead(self):
        self.character.is_alive = False
        self.character.death_cause = "Slain"
        self.character.save()
        self.login_as(self.dm)
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        self.assertContains(response, 'id="revive-btn"')
        self.assertNotContains(response, 'id="mark-dead-btn"')

    def test_owner_does_not_see_dm_buttons(self):
        self.login_as(self.owner)
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        self.assertNotContains(response, 'id="mark-dead-btn"')
        self.assertNotContains(response, 'id="revive-btn"')
