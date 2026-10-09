"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

from unittest.mock import patch

from django.urls import reverse

from characters.models import Character
from common.test_helpers import BaseTestCase, create_character


class CharacterViewsTest(BaseTestCase):
    """Tests for character views."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.other_user = self.create_user(username="otheruser")
        self.dm = self.create_dm()

    def test_character_list_requires_login(self):
        """Character list requires authentication."""
        response = self.client.get("/tarmar/characters/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/tarmar/accounts/login/", response.url)

    def test_character_list_shows_user_characters(self):
        """Character list shows only the user's characters."""
        self.login_as(self.user)
        Character.objects.create(
            user=self.user,
            name="My Character",
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
        Character.objects.create(
            user=self.other_user,
            name="Other Character",
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
        response = self.client.get("/tarmar/characters/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Character")
        self.assertNotContains(response, "Other Character")

    def test_character_create_redirects_to_edit(self):
        """Rolling from the create page creates a character and redirects to edit."""
        self.login_as(self.user)
        response = self.client.post(
            "/tarmar/characters/create/", {"method": "roll", "name": "Rolled"}
        )
        self.assertEqual(response.status_code, 302)  # Redirect to edit
        # Character should be created
        self.assertEqual(Character.objects.filter(user=self.user).count(), 1)
        character = Character.objects.get(user=self.user)
        self.assertIn(f"/tarmar/characters/{character.pk}/", response.url)

    def test_character_create_rolls_valid_stats(self):
        """Character create generates valid stats."""
        self.login_as(self.user)
        self.client.post(
            "/tarmar/characters/create/", {"method": "roll", "name": "Rolled"}
        )
        character = Character.objects.get(user=self.user)
        # Check stats are in valid ranges
        for stat in [
            character.strength,
            character.dexterity,
            character.intelligence,
            character.wisdom,
            character.constitution,
            character.charisma,
        ]:
            self.assertGreaterEqual(stat, 3)
            self.assertLessEqual(stat, 18)
        self.assertGreaterEqual(character.fatigue_roll, 2)
        self.assertLessEqual(character.fatigue_roll, 12)

    def test_character_create_with_auto_name(self):
        """A Game Master who leaves the name blank gets the account's name.

        A player must name the character now (issue #786); the Game Master's
        path through the page is what it was, auto-name included.
        """
        game_master = self.create_dm("auto-name-gm")
        self.login_as(game_master)
        self.client.post("/tarmar/characters/create/", {"method": "roll"})
        character = Character.objects.get(user=game_master)
        self.assertEqual(character.name, game_master.username)

    def test_character_edit_shows_stats(self):
        """Character edit page shows all stats."""
        self.login_as(self.user)
        character = Character.objects.create(
            user=self.user,
            name="Edit Test",
            strength=14,
            dexterity=12,
            intelligence=10,
            wisdom=13,
            constitution=15,
            charisma=8,
            appearance_roll="1.3",
            height_roll="6.4",
            weight_roll="3",
            fatigue_roll=7,
        )
        response = self.client.get(f"/tarmar/characters/{character.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edit Test")
        self.assertContains(response, "14")  # STR
        self.assertContains(response, "Fatigue")
        self.assertContains(response, "Body")

    def test_character_edit_owner_only(self):
        """Users cannot view/edit other users' characters."""
        other_character = Character.objects.create(
            user=self.other_user,
            name="Other Character",
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
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/characters/{other_character.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_dm_can_view_any_character(self):
        """DMs can view any character."""
        user_character = Character.objects.create(
            user=self.user,
            name="User Character",
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
        self.login_as(self.dm)
        response = self.client.get(f"/tarmar/characters/{user_character.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "User Character")

    def test_character_delete(self):
        """Character can be soft-deleted by owner."""
        self.login_as(self.user)
        character = Character.objects.create(
            user=self.user,
            name="To Delete",
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
        response = self.client.post(f"/tarmar/characters/{character.pk}/delete/")
        self.assertEqual(response.status_code, 302)  # Redirect
        # Character still exists but is marked as deleted
        character.refresh_from_db()
        self.assertTrue(character.is_deleted)
        self.assertIsNotNone(character.deleted_at)

    def test_character_delete_owner_only(self):
        """Users cannot delete other users' characters."""
        other_character = Character.objects.create(
            user=self.other_user,
            name="Other Character",
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
        self.login_as(self.user)
        response = self.client.post(f"/tarmar/characters/{other_character.pk}/delete/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Character.objects.filter(pk=other_character.pk).exists())

    def test_dm_can_delete_other_user_character(self):
        """DMs can soft-delete other users' characters."""
        other_character = Character.objects.create(
            user=self.user,
            name="User Character",
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
        self.login_as(self.dm)
        response = self.client.post(f"/tarmar/characters/{other_character.pk}/delete/")
        self.assertEqual(response.status_code, 302)
        other_character.refresh_from_db()
        self.assertIsNotNone(other_character.deleted_at)

    def test_character_edit_404s_for_soft_deleted(self):
        """A soft-deleted character's edit page 404s, even for its owner."""
        character = Character.objects.create(
            user=self.user,
            name="Gone",
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
            is_deleted=True,
        )
        self.login_as(self.user)
        response = self.client.get(f"/tarmar/characters/{character.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_character_edit_404s_for_soft_deleted_dm(self):
        """A soft-deleted character's edit page 404s for DMs too."""
        character = Character.objects.create(
            user=self.user,
            name="Gone",
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
            is_deleted=True,
        )
        self.login_as(self.dm)
        response = self.client.get(f"/tarmar/characters/{character.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_character_edit_accessible_after_restore(self):
        """Restoring a character makes its edit page reachable again."""
        character = Character.objects.create(
            user=self.user,
            name="Restored",
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
            is_deleted=True,
        )
        self.login_as(self.dm)
        restore_response = self.client.post(
            f"/tarmar/characters/{character.pk}/restore/",
            content_type="application/json",
        )
        self.assertEqual(restore_response.status_code, 200)
        response = self.client.get(f"/tarmar/characters/{character.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Restored")

    def test_hamburger_menu_has_characters_link(self):
        """Hamburger menu has Characters link when logged in."""
        self.login_as(self.user)
        response = self.client.get("/tarmar/")
        self.assertContains(response, "/tarmar/characters/")

    def test_character_edit_shows_dice_log(self):
        """Character edit page shows dice roll log."""
        self.login_as(self.user)
        character = Character.objects.create(
            user=self.user,
            name="Log Test",
            strength=14,
            dexterity=12,
            intelligence=10,
            wisdom=13,
            constitution=15,
            charisma=8,
            appearance_roll="3",
            height_roll="4",
            weight_roll="3",
            fatigue_roll=7,
            dice_log="STR: 4d6: [4, 3, 2, 1] -> keep [4, 3, 2], drop 1 = 9\nDEX: 4d6: [5, 4, 3, 2] -> keep [5, 4, 3], drop 2 = 12",
        )
        response = self.client.get(f"/tarmar/characters/{character.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dice Roll Log")
        self.assertContains(response, "STR: 4d6:")
        self.assertContains(response, "DEX: 4d6:")

    def test_character_creation_saves_dice_log(self):
        """Character creation should save the dice log."""
        self.login_as(self.user)
        self.client.post(
            "/tarmar/characters/create/", {"method": "roll", "name": "Rolled"}
        )
        character = Character.objects.get(user=self.user)
        self.assertTrue(len(character.dice_log) > 0)
        self.assertIn("STR:", character.dice_log)
        self.assertIn("DEX:", character.dice_log)

    def test_character_update_name(self):
        """Character name can be updated via AJAX."""
        self.login_as(self.user)
        character = Character.objects.create(
            user=self.user,
            name="Original Name",
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
        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "name", "value": "Updated Name"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        character.refresh_from_db()
        self.assertEqual(character.name, "Updated Name")

    def test_character_update_name_empty_rejected(self):
        """Empty character name is rejected."""
        self.login_as(self.user)
        character = Character.objects.create(
            user=self.user,
            name="Original Name",
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
        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "name", "value": "   "},
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)
        character.refresh_from_db()
        self.assertEqual(character.name, "Original Name")

    def test_character_update_name_to_a_taken_name_is_allowed(self):
        """Renaming onto another character's name is fine (issue #571)."""
        self.login_as(self.user)
        fields = {
            "user": self.user,
            "strength": 10,
            "dexterity": 10,
            "intelligence": 10,
            "wisdom": 10,
            "constitution": 10,
            "charisma": 10,
            "appearance_roll": "3",
            "height_roll": "3",
            "weight_roll": "3",
            "fatigue_roll": 7,
        }
        Character.objects.create(name="Taken Name", **fields)
        character = Character.objects.create(name="Original Name", **fields)
        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "name", "value": "Taken Name"},
        )
        self.assertEqual(response.status_code, 200)
        character.refresh_from_db()
        self.assertEqual(character.name, "Taken Name")

    def test_character_update_name_noop_same_name(self):
        """Renaming a character to its own current name is a harmless no-op."""
        self.login_as(self.user)
        character = Character.objects.create(
            user=self.user,
            name="Original Name",
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
        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "name", "value": "Original Name"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        character.refresh_from_db()
        self.assertEqual(character.name, "Original Name")

    def test_character_update_name_case_sensitive(self):
        """The unique constraint is case-sensitive, so a case-only rename is allowed."""
        self.login_as(self.user)
        Character.objects.create(
            user=self.user,
            name="Taken Name",
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
        character = Character.objects.create(
            user=self.user,
            name="Original Name",
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
        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "name", "value": "taken name"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        character.refresh_from_db()
        self.assertEqual(character.name, "taken name")

    def test_character_update_invalid_field_rejected(self):
        """Invalid field updates are rejected."""
        self.login_as(self.user)
        character = Character.objects.create(
            user=self.user,
            name="Test Character",
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
        # Try to update a field that doesn't exist or isn't editable
        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "user_id", "value": 999},
        )
        self.assertEqual(response.status_code, 400)

    def test_character_update_strength(self):
        """Character strength can be updated via AJAX."""
        # A rolled or awarded field: a Game Master sets it (issue #748).
        self.login_as(self.create_dm("update-gm"))
        character = Character.objects.create(
            user=self.user,
            name="Test Character",
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
        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "strength", "value": 18},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        # Should return updated derived values
        self.assertIn("fatigue", data)
        self.assertIn("body", data)
        self.assertIn("physical", data)
        self.assertIn("weight_lbs", data)
        character.refresh_from_db()
        self.assertEqual(character.strength, 18)

    def test_character_update_dexterity_restates_aim_and_dodge(self):
        """Issue #634: the two combat tiles went stale when DEX was edited."""
        # A rolled or awarded field: a Game Master sets it (issue #748).
        self.login_as(self.create_dm("update-gm"))
        character = Character.objects.create(
            user=self.user,
            name="Nimble",
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            fatigue_roll=7,
        )

        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "dexterity", "value": 18},
        )

        data = response.json()
        self.assertEqual(data["dex_modifier"], 4)
        self.assertEqual(data["dodge_modifier"], 4)

    def test_character_update_charisma_recalculates_mental(self):
        """Updating charisma returns the recalculated Mental attribute."""
        # A rolled or awarded field: a Game Master sets it (issue #748).
        self.login_as(self.create_dm("update-gm"))
        character = Character.objects.create(
            user=self.user,
            name="Mental Test",
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
        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "charisma", "value": 18},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertIn("mental", data)
        character.refresh_from_db()
        # WIS=10 + INT=10 + CHA=18 = 38; 38/2 = 19
        self.assertEqual(data["mental"], 19)
        self.assertEqual(character.mental, 19)

    def test_character_update_owner_only(self):
        """Users cannot update other users' characters."""
        other_character = Character.objects.create(
            user=self.other_user,
            name="Other Character",
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
        self.login_as(self.user)
        response = self.post_json(
            f"/tarmar/characters/{other_character.pk}/update-field/",
            {"field": "name", "value": "Hacked Name"},
        )
        self.assertEqual(response.status_code, 403)
        other_character.refresh_from_db()
        self.assertEqual(other_character.name, "Other Character")

    def test_dm_can_update_any_character(self):
        """DMs can update any character."""
        user_character = Character.objects.create(
            user=self.user,
            name="User Character",
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
        self.login_as(self.dm)
        response = self.post_json(
            f"/tarmar/characters/{user_character.pk}/update-field/",
            {"field": "name", "value": "DM Updated"},
        )
        self.assertEqual(response.status_code, 200)
        user_character.refresh_from_db()
        self.assertEqual(user_character.name, "DM Updated")

    def test_character_update_field_404s_for_soft_deleted(self):
        """A mutation endpoint 404s on a soft-deleted character and changes nothing."""
        character = Character.objects.create(
            user=self.user,
            name="Gone",
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
            is_deleted=True,
        )
        # Even the DM, who could otherwise edit any character, is blocked.
        self.login_as(self.dm)
        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "name", "value": "Hacked Name"},
        )
        self.assertEqual(response.status_code, 404)
        character.refresh_from_db()
        self.assertEqual(character.name, "Gone")
        self.assertTrue(character.is_deleted)

    def test_character_auto_naming(self):
        """A player's character takes the name they gave it (issue #786).

        This asserted the account's name, which was #786 itself: every
        character a player created was called after the account.
        """
        self.login_as(self.user)
        self.client.post(
            "/tarmar/characters/create/", {"method": "roll", "name": "Wren"}
        )
        character = Character.objects.get(user=self.user)
        self.assertEqual(character.name, "Wren")

    def test_character_auto_naming_does_not_suffix(self):
        """A second character given the same name keeps it (issue #571)."""
        self.login_as(self.user)
        for _ in range(2):
            self.client.post(
                "/tarmar/characters/create/", {"method": "roll", "name": "Wren"}
            )
        names = list(
            Character.objects.filter(user=self.user).values_list("name", flat=True)
        )
        self.assertEqual(names, ["Wren", "Wren"])

    def test_character_update_invalid_json(self):
        """Invalid JSON in request body returns error."""
        self.login_as(self.user)
        character = Character.objects.create(
            user=self.user,
            name="Test",
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
        response = self.client.post(
            f"/tarmar/characters/{character.pk}/update-field/",
            "not valid json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_character_update_json_array_rejected(self):
        """JSON array (not object) in request body returns error."""
        self.login_as(self.user)
        character = Character.objects.create(
            user=self.user,
            name="Test",
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
        response = self.client.post(
            f"/tarmar/characters/{character.pk}/update-field/",
            "[1, 2, 3]",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("Invalid JSON", data["error"])

    def test_character_update_invalid_number(self):
        """Non-numeric value for integer field returns error."""
        self.login_as(self.user)
        character = Character.objects.create(
            user=self.user,
            name="Test",
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
        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "strength", "value": "not a number"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_character_update_negative_number(self):
        """Negative value for integer field returns error."""
        self.login_as(self.user)
        character = Character.objects.create(
            user=self.user,
            name="Test",
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
        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "strength", "value": -5},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_character_update_height_roll(self):
        """Updating height_roll returns updated height_display."""
        self.login_as(self.user)
        character = Character.objects.create(
            user=self.user,
            name="Test",
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
        url = f"/tarmar/characters/{character.pk}/update-field/"
        # A player chooses one face; a chain is a roll's or a Game Master's
        # (issue #748).
        refused = self.post_json(url, {"field": "height_roll", "value": "6.6.4"})
        self.assertEqual(refused.status_code, 400)
        self.assertIn("one height face", refused.json()["error"])
        response = self.post_json(url, {"field": "height_roll", "value": "5"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(
            data["height_display"], Character(height_roll="5").height_display
        )
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("height-gm"))
        chained = self.post_json(url, {"field": "height_roll", "value": "6.6.4"})
        self.assertEqual(chained.status_code, 200)

    def test_character_update_wealth_level_sets_the_coin_once(self):
        """A Game Master's first wealth setting sets the coin from the table;
        re-selecting a level never rolls it again (issue #749, Spencer's
        ruling). This test used to pin the reroll: Moderate -> 100,
        Subsistence -> 10, Rich -> 0, Merchant -> a fresh 100 + d100."""
        self.login_as(self.create_dm("wealth-gm"))
        character = Character.objects.create(
            user=self.user,
            name="Test",
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
            wealth_recorded=False,
        )
        url = f"/tarmar/characters/{character.pk}/update-field/"

        with patch("characters.dice.roll_d100", return_value=57):
            data = self.post_json(
                url, {"field": "wealth_level", "value": "Merchant"}
            ).json()

        self.assertTrue(data["success"])
        self.assertEqual(data["starting_coin"], 157)
        character.refresh_from_db()
        self.assertTrue(character.wealth_recorded)
        self.assertIn(
            "Wealth: set to Merchant, starting coin = 100 + d100(57) = 157",
            character.dice_log,
        )

        for level in ("Moderate", "Merchant", "Subsistence", "Merchant"):
            data = self.post_json(url, {"field": "wealth_level", "value": level}).json()
            self.assertEqual(data["starting_coin"], 157, level)
        character.refresh_from_db()
        self.assertEqual(
            (character.wealth_level, character.starting_coin), ("Merchant", 157)
        )

    def test_a_first_rich_wealth_leaves_the_coin_to_the_game_master(self):
        """wealth.md gives Rich "Consult GM", not 0 (issue #749)."""
        self.login_as(self.create_dm("rich-gm"))
        character = create_character(self.user, wealth_recorded=False)

        data = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "wealth_level", "value": "Rich"},
        ).json()

        self.assertIsNone(data["starting_coin"])
        character.refresh_from_db()
        self.assertIsNone(character.starting_coin)
        page = self.client.get(reverse("characters:edit", args=[character.pk])).content
        self.assertIn(b'placeholder="Consult GM"', page)

    def test_character_update_total_xp_returns_level(self):
        """Updating total_xp returns the computed level."""
        # A rolled or awarded field: a Game Master sets it (issue #748).
        self.login_as(self.create_dm("update-gm"))
        character = Character.objects.create(
            user=self.user,
            name="XP Test",
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
            total_xp=0,
        )
        # Update XP to trigger level computation
        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "total_xp", "value": 100},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertIn("level", data)
        self.assertGreater(data["level"], 0)

    def test_character_update_string_field_too_long(self):
        """String fields reject values exceeding max length."""
        self.login_as(self.user)
        character = Character.objects.create(
            user=self.user,
            name="Length Test",
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
        # Name has max length of 100
        long_name = "x" * 101
        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "name", "value": long_name},
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("100 characters", data["error"])

    def test_character_update_integer_field_null_rejected(self):
        """Integer fields reject null values."""
        self.login_as(self.user)
        character = Character.objects.create(
            user=self.user,
            name="Null Test",
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
        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "strength", "value": None},
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("required", data["error"])

    def test_character_update_wealth_level_bogus_value_rejected(self):
        """A wealth_level outside the model's choices 400s instead of 500ing.

        Regression test for the bogus-wealth_level crash: previously this
        looked up the value in STARTING_COIN and raised an unhandled
        KeyError.
        """
        self.login_as(self.user)
        character = Character.objects.create(
            user=self.user,
            name="Wealth Choice Test",
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
            wealth_level="Moderate",
            starting_coin=100,
        )
        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "wealth_level", "value": "bogus"},
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("wealth_level", data["error"])
        character.refresh_from_db()
        self.assertEqual(character.wealth_level, "Moderate")
        self.assertEqual(character.starting_coin, 100)

    def test_character_update_location_bogus_value_rejected(self):
        """A location outside the model's choices is rejected with a 400."""
        self.login_as(self.user)
        character = Character.objects.create(
            user=self.user,
            name="Location Choice Test",
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
            location="Village",
        )
        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "location", "value": "Underwater Volcano"},
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("location", data["error"])
        character.refresh_from_db()
        self.assertEqual(character.location, "Village")

    def test_character_update_provenance_class_bogus_value_rejected(self):
        """A provenance_class outside the model's choices is rejected."""
        self.login_as(self.user)
        character = Character.objects.create(
            user=self.user,
            name="Provenance Choice Test",
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
            provenance_class="commoner",
        )
        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "provenance_class", "value": "space_pirate"},
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("provenance_class", data["error"])
        character.refresh_from_db()
        self.assertEqual(character.provenance_class, "commoner")

    def test_character_update_choice_fields_accept_valid_values(self):
        """Valid choice values for location and provenance_class still save."""
        self.login_as(self.user)
        character = Character.objects.create(
            user=self.user,
            name="Valid Choice Test",
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
            location="Village",
            provenance_class="commoner",
        )
        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "location", "value": "City"},
        )
        self.assertEqual(response.status_code, 200)
        character.refresh_from_db()
        self.assertEqual(character.location, "City")

        # Provenance is rolled; a Game Master may set it (issue #749).
        self.logout()  # switching users; keeps the site gate passed
        self.login_as(self.create_dm("choice-gm"))
        response = self.post_json(
            f"/tarmar/characters/{character.pk}/update-field/",
            {"field": "provenance_class", "value": "nobility"},
        )
        self.assertEqual(response.status_code, 200)
        character.refresh_from_db()
        self.assertEqual(character.provenance_class, "nobility")


# Skills System Tests
