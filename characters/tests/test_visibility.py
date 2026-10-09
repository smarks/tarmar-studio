"""Public/private character visibility (issue #273).

A character is private by default: only its owner and DM/admin users can see
or touch it -- exactly the pre-flag behavior. Flipping it public widens READ
alone: every registered user may open the sheet, see the list row, and
export, but every write still goes through the owner/DM edit check.

The read rule has one statement, ``characters_visible_to``; the write rule
has one statement, ``common.helpers._can_edit_object``. These tests pin the
matrix (owner / other / DM / admin x public / private x read / edit), the
view-level enforcement on the sheet, list, exports, and the toggle endpoint,
and that battle seating semantics stay "visible and alive" (so players'
claimable seats remain their own characters only).
"""

import json
from types import SimpleNamespace

from battle.services import claimable_characters, seatable_characters
from characters.models import Character, characters_visible_to
from common.helpers import _can_edit_object
from common.test_helpers import BaseTestCase, create_character


class VisibilityMatrixTest(BaseTestCase):
    """The chokepoint pair: characters_visible_to and _can_edit_object."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("keeper")
        self.other = self.create_user("stranger")
        self.dm = self.create_dm("table-dm")
        self.admin = self.create_admin("table-admin")
        self.private_char = create_character(self.owner, name="Hidden Hero")
        self.public_char = create_character(
            self.owner, name="Famous Hero", is_public=True
        )
        self.deleted_public = create_character(
            self.owner, name="Gone Hero", is_public=True, is_deleted=True
        )

    def _visible_names(self, user):
        return set(characters_visible_to(user).values_list("name", flat=True))

    def test_owner_sees_own_private_and_public(self):
        self.assertEqual(
            self._visible_names(self.owner), {"Hidden Hero", "Famous Hero"}
        )

    def test_other_user_sees_public_only(self):
        self.assertEqual(self._visible_names(self.other), {"Famous Hero"})

    def test_dm_and_admin_see_everything_undeleted(self):
        for manager in (self.dm, self.admin):
            self.assertEqual(
                self._visible_names(manager),
                {"Hidden Hero", "Famous Hero"},
                f"for {manager.username}",
            )

    def test_soft_deleted_public_character_is_visible_to_nobody(self):
        for user in (self.owner, self.other, self.dm, self.admin):
            self.assertNotIn("Gone Hero", self._visible_names(user))

    def test_public_does_not_widen_the_edit_check(self):
        """The write rule ignores is_public: owner and DM/admin only."""
        for character in (self.private_char, self.public_char):
            self.assertTrue(
                _can_edit_object(SimpleNamespace(user=self.owner), character)
            )
            self.assertTrue(_can_edit_object(SimpleNamespace(user=self.dm), character))
            self.assertTrue(
                _can_edit_object(SimpleNamespace(user=self.admin), character)
            )
            self.assertFalse(
                _can_edit_object(SimpleNamespace(user=self.other), character),
                "a public character must not become editable by strangers",
            )

    def test_characters_default_private(self):
        """The schema default is the migration: existing rows land private."""
        character = create_character(self.other, name="Fresh Face")
        self.assertFalse(character.is_public)


class SheetAccessTest(BaseTestCase):
    """The sheet reads through the visibility rule and renders read-only
    for a viewer the edit check refuses."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("keeper")
        self.other = self.create_user("stranger")
        self.dm = self.create_dm("table-dm")
        self.private_char = create_character(self.owner, name="Hidden Hero")
        self.public_char = create_character(
            self.owner,
            name="Famous Hero",
            is_public=True,
            skills={"sword": 3},
            equipment=[{"item_id": "dagger", "quantity": 1}],
            free_skill_points=2,
        )

    def _sheet(self, character):
        return self.client.get(f"/tarmar/characters/{character.pk}/")

    def test_other_user_cannot_open_a_private_sheet(self):
        self.login_as(self.other)
        self.assertEqual(self._sheet(self.private_char).status_code, 404)

    def test_other_user_reads_a_public_sheet_without_edit_affordances(self):
        self.login_as(self.other)
        response = self._sheet(self.public_char)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Famous Hero", html)
        # No autosave inputs, no skill editing, no delete, no toggle, no
        # workbench roll button, no attack form -- reading only. (The
        # autosave.js script tag still loads; with no .autosave controls in
        # the page it binds nothing.)
        self.assertNotIn('class="autosave', html)
        self.assertNotIn("contenteditable", html)
        self.assertNotIn("delete-character-form", html)
        self.assertNotIn("visibility-toggle", html)
        self.assertNotIn("roll-years-btn", html)
        self.assertNotIn('id="attack-form"', html)
        self.assertNotIn("add-skill-btn", html)
        self.assertNotIn("add-equipment-btn", html)
        # The badge says why this page is readable at all.
        self.assertIn('id="visibility-badge"', html)
        self.assertIn("Public", html)
        # The config block tells the equipment renderer to draw static rows.
        self.assertIn('"canEdit": false', html)

    def test_owner_still_gets_the_editable_sheet_with_the_toggle(self):
        self.login_as(self.owner)
        html = self._sheet(self.public_char).content.decode()
        self.assertIn('class="autosave', html)
        self.assertIn("visibility-toggle", html)
        self.assertIn('"canEdit": true', html)

    def test_dm_gets_the_editable_sheet_for_a_private_character(self):
        self.login_as(self.dm)
        response = self._sheet(self.private_char)
        self.assertEqual(response.status_code, 200)
        self.assertIn('class="autosave', response.content.decode())

    def test_update_field_stays_forbidden_for_a_public_characters_reader(self):
        self.login_as(self.other)
        response = self.client.post(
            f"/tarmar/characters/{self.public_char.pk}/update-field/",
            data=json.dumps({"field": "strength", "value": 18}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.public_char.refresh_from_db()
        self.assertEqual(self.public_char.strength, 14)


class ListViewVisibilityTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("keeper")
        self.other = self.create_user("stranger")
        create_character(self.owner, name="Hidden Hero")
        create_character(self.owner, name="Famous Hero", is_public=True)
        create_character(self.other, name="My Own Hero")

    def test_regular_user_list_carries_own_plus_public(self):
        self.login_as(self.other)
        html = self.client.get("/tarmar/characters/").content.decode()
        self.assertIn("My Own Hero", html)
        self.assertIn("Famous Hero", html)
        self.assertNotIn("Hidden Hero", html)
        # The owner column names whose character the public row is.
        self.assertIn("keeper", html)
        # Both visibility badges render (own row private, public row public).
        self.assertIn(">Public</span>", html)
        self.assertIn(">Private</span>", html)

    def test_no_delete_button_on_someone_elses_public_row(self):
        self.login_as(self.other)
        html = self.client.get("/tarmar/characters/").content.decode()
        famous_pk = Character.objects.get(name="Famous Hero").pk
        own_pk = Character.objects.get(name="My Own Hero").pk
        self.assertNotIn(f"/tarmar/characters/{famous_pk}/delete/", html)
        self.assertIn(f"/tarmar/characters/{own_pk}/delete/", html)

    def test_show_deleted_does_not_leak_other_peoples_deleted_characters(self):
        create_character(self.owner, name="Gone Hero", is_public=True, is_deleted=True)
        create_character(self.other, name="My Gone Hero", is_deleted=True)
        self.login_as(self.other)
        html = self.client.get("/tarmar/characters/?show_deleted=1").content.decode()
        self.assertIn("My Gone Hero", html)
        self.assertNotIn("Gone Hero</a>", html)
        self.assertNotIn(">Gone Hero<", html)


class ExportVisibilityTest(BaseTestCase):
    """Exports are reads: public characters export for any registered user."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("keeper")
        self.other = self.create_user("stranger")
        self.private_char = create_character(self.owner, name="Hidden Hero")
        self.public_char = create_character(
            self.owner, name="Famous Hero", is_public=True
        )
        self.login_as(self.other)

    def test_public_markdown_and_json_export_for_another_user(self):
        for fmt in ("markdown", "json"):
            response = self.client.get(f"/tarmar/export/{self.public_char.pk}/{fmt}/")
            self.assertEqual(response.status_code, 200, fmt)

    def test_private_exports_stay_hidden_from_another_user(self):
        for fmt in ("markdown", "json", "pdf"):
            response = self.client.get(f"/tarmar/export/{self.private_char.pk}/{fmt}/")
            self.assertEqual(response.status_code, 404, fmt)


class SetVisibilityEndpointTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("keeper")
        self.other = self.create_user("stranger")
        self.dm = self.create_dm("table-dm")
        self.character = create_character(self.owner, name="Hidden Hero")

    def _post(self, character, is_public):
        return self.client.post(
            f"/tarmar/characters/{character.pk}/set-visibility/",
            data=json.dumps({"is_public": is_public}),
            content_type="application/json",
        )

    def test_owner_flips_public_and_back(self):
        self.login_as(self.owner)
        response = self._post(self.character, True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True, "is_public": True})
        self.character.refresh_from_db()
        self.assertTrue(self.character.is_public)

        response = self._post(self.character, False)
        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertFalse(self.character.is_public)

    def test_dm_may_flip_someone_elses_character(self):
        self.login_as(self.dm)
        self.assertEqual(self._post(self.character, True).status_code, 200)
        self.character.refresh_from_db()
        self.assertTrue(self.character.is_public)

    def test_another_user_may_not_flip_even_a_public_character(self):
        self.character.is_public = True
        self.character.save()
        self.login_as(self.other)
        self.assertEqual(self._post(self.character, False).status_code, 403)
        self.character.refresh_from_db()
        self.assertTrue(self.character.is_public)

    def test_anonymous_is_sent_to_login(self):
        self.assertEqual(self._post(self.character, True).status_code, 302)

    def test_get_is_refused(self):
        self.login_as(self.owner)
        response = self.client.get(
            f"/tarmar/characters/{self.character.pk}/set-visibility/"
        )
        self.assertEqual(response.status_code, 405)

    def test_non_boolean_payload_is_refused(self):
        self.login_as(self.owner)
        response = self._post(self.character, "yes")
        self.assertEqual(response.status_code, 400)
        self.character.refresh_from_db()
        self.assertFalse(self.character.is_public)

    def test_soft_deleted_character_is_not_reachable(self):
        self.character.is_deleted = True
        self.character.save()
        self.login_as(self.owner)
        self.assertEqual(self._post(self.character, True).status_code, 404)


class BattleSeatingVisibilityTest(BaseTestCase):
    """Seating semantics stay 'visible and alive' (deliberate for #273; the
    parties chain, #274/#275, is what will hand out seats for public
    characters). Players' lobby claims stay own-only either way."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("keeper")
        self.player = self.create_user("stranger")
        create_character(self.owner, name="Famous Hero", is_public=True)
        create_character(
            self.owner, name="Famous Corpse", is_public=True, is_alive=False
        )
        create_character(self.owner, name="Hidden Hero")
        create_character(self.player, name="My Own Hero")

    def test_seatable_is_the_visible_and_alive_set(self):
        names = set(seatable_characters(self.player).values_list("name", flat=True))
        self.assertEqual(names, {"My Own Hero", "Famous Hero"})

    def test_claimable_stays_own_characters_only(self):
        names = set(claimable_characters(self.player).values_list("name", flat=True))
        self.assertEqual(names, {"My Own Hero"})


class CreationVisibilityTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user("creator")
        self.login_as(self.user)

    def test_create_page_offers_the_choice_with_private_preselected(self):
        html = self.client.get("/tarmar/characters/create/").content.decode()
        self.assertIn("any registered user can view this character", html)
        self.assertIn('value="private" checked', html)
        self.assertNotIn('value="public" checked', html)

    def test_rolling_without_a_choice_creates_private(self):
        self.client.post(
            "/tarmar/characters/create/", {"method": "roll", "name": "Seen"}
        )
        self.assertFalse(Character.objects.get(user=self.user).is_public)

    def test_rolling_with_public_chosen_creates_public(self):
        self.client.post(
            "/tarmar/characters/create/",
            {"method": "roll", "name": "Seen", "visibility": "public"},
        )
        self.assertTrue(Character.objects.get(user=self.user).is_public)

    def test_point_buy_carries_the_choice_too(self):
        self.client.post(
            "/tarmar/characters/create/",
            {
                "method": "point-buy",
                "name": "Seen",
                "visibility": "public",
                "strength": 11,
                "dexterity": 11,
                "intelligence": 11,
                "wisdom": 11,
                "constitution": 11,
                "charisma": 10,
            },
        )
        self.assertTrue(Character.objects.get(user=self.user).is_public)


class AnonymousSeesNothingTest(BaseTestCase):
    """Public means public to accounts, not to the internet.

    Issue #305 lets an unauthenticated visitor reach the arena, which means
    this chokepoint can now be asked about ``AnonymousUser``. The answer is
    an empty queryset, and it is asserted here rather than only at the
    arena's door: the read rule has one statement, so the rule's own tests
    are where it has to be pinned.
    """

    def setUp(self):
        super().setUp()
        owner = self.create_user("keeper")
        create_character(owner, name="Hidden Hero")
        create_character(owner, name="Famous Hero", is_public=True)

    def test_an_anonymous_viewer_sees_no_character_public_or_private(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertEqual(list(characters_visible_to(AnonymousUser())), [])

    def test_a_registered_viewer_still_sees_the_public_one(self):
        stranger = self.create_user("stranger")
        names = [row.name for row in characters_visible_to(stranger)]
        self.assertEqual(names, ["Famous Hero"])
