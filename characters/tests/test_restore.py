"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

from characters.models import Character
from common.test_helpers import BaseTestCase


class RestoreCharacterTest(BaseTestCase):
    """Tests for restoring soft-deleted characters."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_user("dm")
        self.dm.is_dm = True
        self.dm.save()
        self.player = self.create_user("player")
        self.character = Character.objects.create(
            user=self.player,
            name="Deleted Char",
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
            is_deleted=True,
        )

    def test_dm_can_restore_character(self):
        """DM can restore a deleted character."""
        self.login_as(self.dm)
        response = self.client.post(
            f"/tarmar/characters/{self.character.pk}/restore/",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.character.refresh_from_db()
        self.assertFalse(self.character.is_deleted)
        self.assertIsNone(self.character.deleted_at)

    def test_owner_can_restore_their_own_character(self):
        """A player restores their own deleted character (#339 item 15)."""
        self.login_as(self.player)
        response = self.client.post(
            f"/tarmar/characters/{self.character.pk}/restore/",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertFalse(self.character.is_deleted)

    def test_player_cannot_restore_someone_elses_character(self):
        """Another player's deleted character stays deleted."""
        self.login_as(self.create_user("stranger"))
        response = self.client.post(
            f"/tarmar/characters/{self.character.pk}/restore/",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.character.refresh_from_db()
        self.assertTrue(self.character.is_deleted)

    def test_owner_is_offered_restore_on_the_character_list(self):
        """The list shows the owner a Restore button on their deleted row."""
        self.login_as(self.player)
        response = self.client.get("/tarmar/characters/?show_deleted=1")
        self.assertContains(
            response,
            f'data-restore-url="/tarmar/characters/{self.character.pk}/restore/"',
        )
        self.assertContains(response, 'id="restore-selected-btn"')

    def test_restore_requires_login(self):
        """Restore requires authentication."""
        response = self.client.post(
            f"/tarmar/characters/{self.character.pk}/restore/",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)

    def test_restore_not_found_for_non_deleted(self):
        """Cannot restore a character that isn't deleted."""
        self.character.is_deleted = False
        self.character.save()

        self.login_as(self.dm)
        response = self.client.post(
            f"/tarmar/characters/{self.character.pk}/restore/",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)


class DeletedCharactersViewTest(BaseTestCase):
    """Tests for the deleted characters list view."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_user("dm")
        self.dm.is_dm = True
        self.dm.save()
        self.player = self.create_user("player")

    def test_dm_can_view_deleted_characters(self):
        """DM can view deleted characters page."""
        self.login_as(self.dm)
        response = self.client.get("/tarmar/characters/deleted/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Deleted Characters")

    def test_the_character_list_links_managers_to_the_page(self):
        """The page was orphaned: nothing linked to it (issue #339, item 16)."""
        self.login_as(self.dm)
        response = self.client.get("/tarmar/characters/")
        self.assertContains(response, 'href="/tarmar/characters/deleted/"')

    def test_players_are_not_offered_the_link(self):
        self.login_as(self.player)
        response = self.client.get("/tarmar/characters/")
        self.assertNotContains(response, 'href="/tarmar/characters/deleted/"')

    def test_the_page_sits_under_characters_not_users(self):
        self.login_as(self.dm)
        response = self.client.get("/tarmar/characters/deleted/")
        self.assertEqual(response.context["active_page"], "characters")
        self.assertNotContains(response, "Back to Users")

    def test_player_denied(self):
        """Regular player gets 403 from deleted characters page."""
        self.login_as(self.player)
        response = self.client.get("/tarmar/characters/deleted/")
        self.assertEqual(response.status_code, 403)

    def test_deleted_characters_shown(self):
        """Deleted characters appear in the list."""
        Character.objects.create(
            user=self.player,
            name="Deleted One",
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
            is_deleted=True,
        )

        self.login_as(self.dm)
        response = self.client.get("/tarmar/characters/deleted/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Deleted One")

    def test_restore_button_carries_url_via_data_attribute(self):
        """Each row's Restore button gets its endpoint from {% url %}, not
        JS string-building (issue #112) — the button must carry the real,
        pk-specific restore URL so character-management.js never has to
        construct it itself."""
        character = Character.objects.create(
            user=self.player,
            name="Deleted One",
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
            is_deleted=True,
        )

        self.login_as(self.dm)
        response = self.client.get("/tarmar/characters/deleted/")
        self.assertContains(
            response,
            f'data-restore-url="/tarmar/characters/{character.pk}/restore/"',
        )
