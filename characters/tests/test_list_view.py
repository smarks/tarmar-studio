"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

from characters.models import Character
from common.test_helpers import BaseTestCase


class CharacterListViewManagerTest(BaseTestCase):
    """Tests for DM/Admin character list management features."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_user("dm")
        self.dm.is_dm = True
        self.dm.save()
        self.player = self.create_user("player")

    def test_dm_sees_all_characters(self):
        """DM sees all characters on the characters list page."""
        self.login_as(self.dm)
        response = self.client.get("/tarmar/characters/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Characters")
        self.assertTrue(response.context["is_manager"])

    def test_player_sees_only_own_characters(self):
        """Regular player only sees their own characters."""
        # Create characters for both users
        Character.objects.create(
            user=self.player,
            name="Player Char",
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
        Character.objects.create(
            user=self.dm,
            name="DM Char",
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
        self.login_as(self.player)
        response = self.client.get("/tarmar/characters/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Player Char")
        self.assertNotContains(response, "DM Char")
        self.assertNotIn("is_manager", response.context)

    def test_dm_sees_active_and_deleted_characters_with_filter(self):
        """DM sees both active and deleted characters when show_deleted is enabled."""
        Character.objects.create(
            user=self.player,
            name="Active Char",
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
            is_deleted=False,
        )
        Character.objects.create(
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

        self.login_as(self.dm)
        # By default, deleted characters are hidden
        response = self.client.get("/tarmar/characters/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active Char")
        self.assertNotContains(response, "Deleted Char")

        # With show_deleted=1, both are visible
        response = self.client.get("/tarmar/characters/?show_deleted=1")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active Char")
        self.assertContains(response, "Deleted Char")

    def test_dm_can_filter_by_user(self):
        """DM can filter characters by user."""
        Character.objects.create(
            user=self.player,
            name="Player Char",
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
        Character.objects.create(
            user=self.dm,
            name="DM Char",
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
        self.login_as(self.dm)
        response = self.client.get(f"/tarmar/characters/?user={self.player.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Player Char")
        self.assertNotContains(response, "DM Char")

    def test_delete_and_restore_buttons_use_url_tag_not_string_building(self):
        """Row action buttons carry a real {% url %}-rendered endpoint for
        deleteCharacter()/restoreCharacter() (issue #112) instead of the JS
        helper string-building "/tarmar/characters/<id>/..." itself. Since
        #338 item 19 the endpoints ride on data attributes rather than inline
        onclick handlers. The bulk-restore loop, which restores an arbitrary
        set of ids picked at runtime, uses a rendered URL template with a
        substitutable pk."""
        active = Character.objects.create(
            user=self.player,
            name="Active Char",
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
            is_deleted=False,
        )
        deleted = Character.objects.create(
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

        self.login_as(self.dm)
        response = self.client.get("/tarmar/characters/?show_deleted=1")
        body = response.content.decode()

        self.assertIn(
            f'data-id="{active.pk}" '
            f'data-delete-url="/tarmar/characters/{active.pk}/delete/"',
            body,
        )
        self.assertIn(
            f'data-id="{deleted.pk}" '
            f'data-restore-url="/tarmar/characters/{deleted.pk}/restore/"',
            body,
        )
        # Placeholder-pk template used by the bulk-restore loop, which
        # substitutes the real id client-side for each selected row.
        self.assertIn('data-restore-url-template="/tarmar/characters/0/restore/"', body)
        # Same placeholder-pk approach for the post-import redirect, which
        # only learns the new character's id from the JSON response.
        self.assertIn('data-edit-url-template="/tarmar/characters/0/"', body)

    def test_deleted_character_name_is_not_a_dead_link(self):
        """CharacterEditView's get_queryset excludes soft-deleted characters
        (404, matching every mutation endpoint -- issue #150), so linking a
        deleted row's name to the edit page dead-ends. The list should render
        it as plain text instead; the way back in is the Restore button
        (already conditional on is_deleted/is_manager) sitting right next to
        it, not the name itself (issue #163)."""
        active = Character.objects.create(
            user=self.player,
            name="Active Char",
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
            is_deleted=False,
        )
        deleted = Character.objects.create(
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

        self.login_as(self.dm)
        response = self.client.get("/tarmar/characters/?show_deleted=1")
        body = response.content.decode()

        # Active character's name is still a link to the edit page.
        self.assertIn(
            f'<a href="/tarmar/characters/{active.pk}/">Active Char</a>', body
        )
        # Deleted character's name is plain text -- no <a> around it, and no
        # link anywhere in the response points at its edit URL.
        self.assertNotIn(
            f'<a href="/tarmar/characters/{deleted.pk}/">Deleted Char</a>', body
        )
        self.assertNotIn(f'href="/tarmar/characters/{deleted.pk}/">', body)
        self.assertIn("Deleted Char", body)

    def test_list_page_loads_api_js_before_its_consumers(self):
        """
        character-management.js and character-list.js call postWithCsrf as a
        bare global that api.js publishes (issue #111). base.html includes
        api.js in <head>, while the consumer <script> tags sit in the
        extra_js block and parse later. Reverse that order and every delete,
        restore and import on this page throws ReferenceError. (The page's own
        handlers moved from inline scripts into character-list.js, #338.)
        """
        Character.objects.create(
            user=self.player,
            name="Order Check",
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
        self.login_as(self.dm)
        body = self.client.get("/tarmar/characters/").content.decode()

        self.assertIn("js/api.js", body)
        self.assertLess(
            body.index("js/api.js"),
            body.index("js/character-management.js"),
            "api.js must be <script>-included before character-management.js",
        )
        self.assertLess(
            body.index("js/character-management.js"),
            body.index("js/character-list.js"),
            "character-list.js uses character-management.js, so it loads after",
        )


class CharacterListPaginationTest(BaseTestCase):
    """CharacterListView paginates at 50 and preserves filter query params."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm("paginator-dm")

    def _make_characters(self, owner, count):
        for index in range(count):
            Character.objects.create(
                user=owner,
                name=f"Char{index:04d}",
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

    def test_page_size_is_fifty(self):
        self._make_characters(self.dm, 55)
        self.login_as(self.dm)
        response = self.client.get("/tarmar/characters/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_paginated"])
        self.assertEqual(len(response.context["characters"]), 50)
        self.assertEqual(response.context["page_obj"].paginator.num_pages, 2)

    def test_second_page_returns_remaining(self):
        self._make_characters(self.dm, 55)
        self.login_as(self.dm)
        response = self.client.get("/tarmar/characters/?page=2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["characters"]), 5)

    def test_pagination_links_preserve_show_deleted_filter(self):
        self._make_characters(self.dm, 55)
        self.login_as(self.dm)
        response = self.client.get("/tarmar/characters/?show_deleted=1")
        body = response.content.decode()
        # The "Next" link should carry show_deleted=1 forward.
        self.assertIn("show_deleted=1&amp;page=2", body)

    def test_pagination_links_preserve_user_filter(self):
        player = self.create_player("filterable-player")
        self._make_characters(player, 55)
        self.login_as(self.dm)
        response = self.client.get(f"/tarmar/characters/?user={player.pk}")
        body = response.content.decode()
        self.assertIn(f"user={player.pk}&amp;page=2", body)

    def test_pagination_links_strip_old_page_param(self):
        self._make_characters(self.dm, 55)
        self.login_as(self.dm)
        response = self.client.get("/tarmar/characters/?page=2")
        body = response.content.decode()
        # Previous link should be page=1, not contain a stale page= entry from input.
        self.assertIn("page=1", body)
        self.assertNotIn("page=2&amp;page=", body)


class CharacterListOwnerFilterTest(BaseTestCase):
    """The manager list's ?user= filter takes whatever the URL bar holds."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm("filter-dm")
        self.player = self.create_user("filter-player")
        self.login_as(self.dm)

    def test_a_non_numeric_owner_filter_is_ignored_rather_than_a_500(self):
        """Issue #641: qs.filter(user_id="abc") raised out of the view."""
        response = self.client.get("/tarmar/characters/?user=abc")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["filter_user_id"], "abc")

    def test_a_numeric_owner_filter_still_filters(self):
        mine = Character.objects.create(user=self.player, name="Theirs")
        Character.objects.create(user=self.dm, name="Mine")

        response = self.client.get(f"/tarmar/characters/?user={self.player.pk}")

        self.assertEqual(list(response.context["characters"]), [mine])
