"""Party pages and roster endpoints (issue #274): auth, the add/remove/
withdraw powers as HTTP, and the visibility-aware roster rendering."""

import json

from django.urls import reverse
from django.utils.html import escape

from common.test_helpers import (
    TEST_PASSWORD,
    BaseTestCase,
    create_character,
    mark_gate_passed,
)
from parties.models import Party, PartyMembership
from parties.refusal_words import refusal_words as parties_words


class PartyViewTestCase(BaseTestCase):
    """Shared cast: a party owner, a character owner, a bystander, roles."""

    def setUp(self):
        super().setUp()
        self.party_owner = self.create_user("bandleader")
        self.character_owner = self.create_user("keeper")
        self.other = self.create_user("stranger")
        self.dm = self.create_dm("table-dm")
        self.admin = self.create_admin("table-admin")
        self.party = Party.objects.create(
            name="The Grey Company",
            description="Sellswords of the north road.",
            owner=self.party_owner,
        )
        self.public_char = create_character(
            self.character_owner, name="Famous Hero", is_public=True
        )
        self.private_char = create_character(self.character_owner, name="Hidden Hero")

    def login(self, user, password=TEST_PASSWORD):
        """Sign ``user`` in, keeping the passphrase gate satisfied.

        Switching users mid-test flushes the session (Django's login does,
        when the user changes), which would drop the gate flag BaseTestCase
        planted and turn the next request into a gate page — so it is
        re-planted after every login.
        """
        self.client.login(username=user.username, password=password)
        mark_gate_passed(self.client)

    def detail_url(self):
        return reverse("parties:detail", kwargs={"pk": self.party.pk})

    def add(self, character, added_by=None):
        return PartyMembership.objects.create(
            party=self.party,
            character=character,
            added_by=added_by or self.party_owner,
        )


class PartyListViewTest(PartyViewTestCase):
    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(reverse("parties:list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_any_registered_user_sees_every_party(self):
        Party.objects.create(name="The Red Band", owner=self.other)
        self.login(self.character_owner)
        response = self.client.get(reverse("parties:list"))
        self.assertContains(response, "The Grey Company")
        self.assertContains(response, "The Red Band")
        self.assertContains(response, "bandleader")

    def test_member_counts_are_listed(self):
        self.add(self.public_char)
        self.login(self.other)
        response = self.client.get(reverse("parties:list"))
        self.assertContains(response, "The Grey Company")
        self.assertEqual(
            [party.member_count for party in response.context["parties"]], [1]
        )


class PartyCreateEditViewTest(PartyViewTestCase):
    def test_any_registered_user_creates_a_party_and_owns_it(self):
        self.login(self.other)
        response = self.client.post(
            reverse("parties:create"),
            {"name": "The Red Band", "description": "Upstarts."},
        )
        party = Party.objects.get(name="The Red Band")
        self.assertRedirects(
            response, reverse("parties:detail", kwargs={"pk": party.pk})
        )
        self.assertEqual(party.owner, self.other)

    def test_anonymous_cannot_create(self):
        response = self.client.get(reverse("parties:create"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_blank_name_is_rejected(self):
        self.login(self.other)
        response = self.client.post(reverse("parties:create"), {"name": ""})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Party.objects.filter(owner=self.other).exists())

    def edit_url(self):
        return reverse("parties:edit", kwargs={"pk": self.party.pk})

    def update_field(self, field, value):
        return self.client.post(
            reverse("parties:update_field", kwargs={"pk": self.party.pk}),
            json.dumps({"field": field, "value": value}),
            content_type="application/json",
        )

    def test_edit_page_autosaves_instead_of_a_save_button(self):
        """Item 17 (#339): the edit page's fields autosave; no Save button."""
        self.login(self.party_owner)
        response = self.client.get(self.edit_url())
        self.assertContains(response, 'data-field="name"')
        self.assertContains(response, 'data-field="description"')
        self.assertContains(
            response,
            f"updateUrl: '/tarmar/parties/{self.party.pk}/update-field/'",
        )
        self.assertNotContains(response, ">Save</button>")
        self.assertNotContains(response, '<form method="post" class="party-form">')

    def test_owner_autosaves_name_and_description(self):
        self.login(self.party_owner)
        response = self.update_field("name", "The Greyer Company")
        self.assertEqual(
            response.json(), {"success": True, "value": "The Greyer Company"}
        )
        self.assertTrue(
            self.update_field("description", "Older now.").json()["success"]
        )
        self.party.refresh_from_db()
        self.assertEqual(self.party.name, "The Greyer Company")
        self.assertEqual(self.party.description, "Older now.")

    def test_blank_name_is_refused_with_a_reason(self):
        self.login(self.party_owner)
        response = self.update_field("name", "")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])
        self.assertTrue(response.json()["error"])
        self.party.refresh_from_db()
        self.assertEqual(self.party.name, "The Grey Company")

    def test_overlong_name_is_refused(self):
        self.login(self.party_owner)
        response = self.update_field("name", "x" * 101)
        self.assertEqual(response.status_code, 400)

    def test_unknown_field_and_bad_bodies_are_refused(self):
        self.login(self.party_owner)
        self.assertEqual(self.update_field("owner", "stranger").status_code, 400)
        self.assertEqual(self.update_field("name", 7).status_code, 400)
        url = reverse("parties:update_field", kwargs={"pk": self.party.pk})
        for body in ("not json", "[1, 2]"):
            response = self.client.post(url, body, content_type="application/json")
            self.assertEqual(response.status_code, 400)

    def test_non_owner_gets_404_on_edit(self):
        self.login(self.other)
        self.assertEqual(self.client.get(self.edit_url()).status_code, 404)
        self.assertEqual(self.update_field("name", "Hijacked").status_code, 404)
        self.party.refresh_from_db()
        self.assertEqual(self.party.name, "The Grey Company")

    def test_admin_and_dm_may_edit_any_party(self):
        for manager in (self.admin, self.dm):
            self.login(manager)
            self.assertEqual(self.client.get(self.edit_url()).status_code, 200)
            self.assertTrue(
                self.update_field("name", f"Renamed by {manager.username}").json()[
                    "success"
                ]
            )


class DisbandPartyTest(PartyViewTestCase):
    """Item 18 (#339): disband is a soft delete for the owner or a manager."""

    def disband(self):
        return self.client.post(
            reverse("parties:disband", kwargs={"pk": self.party.pk})
        )

    def restore(self):
        return self.client.post(
            reverse("parties:restore", kwargs={"pk": self.party.pk})
        )

    def test_owner_disbands_and_members_characters_are_untouched(self):
        membership = self.add(self.public_char)
        self.login(self.party_owner)
        response = self.disband()
        self.assertRedirects(response, reverse("parties:list"))
        self.party.refresh_from_db()
        self.assertTrue(self.party.is_deleted)
        self.assertIsNotNone(self.party.deleted_at)
        self.assertTrue(PartyMembership.objects.filter(pk=membership.pk).exists())
        self.public_char.refresh_from_db()
        self.assertFalse(self.public_char.is_deleted)

    def test_dm_and_admin_disband_any_party(self):
        for manager in (self.dm, self.admin):
            self.party.is_deleted = False
            self.party.save()
            self.login(manager)
            self.assertEqual(self.disband().status_code, 302)
            self.party.refresh_from_db()
            self.assertTrue(self.party.is_deleted)

    def test_bystander_and_member_owner_may_not_disband(self):
        self.add(self.public_char)
        for user in (self.other, self.character_owner):
            self.login(user)
            self.assertEqual(self.disband().status_code, 404)
        self.party.refresh_from_db()
        self.assertFalse(self.party.is_deleted)

    def test_get_is_refused(self):
        self.login(self.party_owner)
        url = reverse("parties:disband", kwargs={"pk": self.party.pk})
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_disbanded_party_leaves_the_list_and_its_pages(self):
        self.login(self.party_owner)
        self.disband()
        listing = self.client.get(reverse("parties:list"))
        self.assertNotIn(self.party, listing.context["parties"])
        self.assertEqual(self.client.get(self.detail_url()).status_code, 404)
        edit_url = reverse("parties:edit", kwargs={"pk": self.party.pk})
        self.assertEqual(self.client.get(edit_url).status_code, 404)
        add_url = reverse("parties:add_member", kwargs={"pk": self.party.pk})
        response = self.client.post(add_url, {"character_id": self.public_char.pk})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.disband().status_code, 404)

    def test_disbanded_parties_are_listed_only_for_those_who_may_restore(self):
        self.login(self.party_owner)
        self.disband()
        owner_listing = self.client.get(reverse("parties:list"))
        self.assertContains(owner_listing, "Disbanded parties")
        self.assertContains(
            owner_listing, f'action="/tarmar/parties/{self.party.pk}/restore/"'
        )
        self.login(self.dm)
        self.assertContains(
            self.client.get(reverse("parties:list")), "Disbanded parties"
        )
        self.login(self.other)
        self.assertNotContains(
            self.client.get(reverse("parties:list")), "Disbanded parties"
        )

    def test_restore_puts_the_party_back(self):
        self.login(self.party_owner)
        self.disband()
        response = self.restore()
        self.assertRedirects(response, self.detail_url())
        self.party.refresh_from_db()
        self.assertFalse(self.party.is_deleted)
        self.assertIsNone(self.party.deleted_at)

    def test_restore_is_refused_to_a_bystander_and_for_an_active_party(self):
        self.login(self.party_owner)
        self.assertEqual(self.restore().status_code, 404)
        self.disband()
        self.login(self.other)
        self.assertEqual(self.restore().status_code, 404)

    def test_detail_offers_disband_with_a_confirmation_to_managers_only(self):
        self.login(self.party_owner)
        response = self.client.get(self.detail_url())
        self.assertContains(response, 'data-confirm="Disband The Grey Company?')
        self.login(self.other)
        self.assertNotContains(self.client.get(self.detail_url()), "Disband")


class AddMemberTest(PartyViewTestCase):
    def post_add(self, character_id):
        return self.client.post(
            reverse("parties:add_member", kwargs={"pk": self.party.pk}),
            {"character_id": character_id},
        )

    def test_owner_adds_a_public_character_immediately(self):
        """No acceptance step: the ruling is the character joins on the post."""
        self.login(self.party_owner)
        response = self.post_add(self.public_char.pk)
        self.assertRedirects(response, self.detail_url())
        membership = self.party.memberships.get()
        self.assertEqual(membership.character, self.public_char)
        self.assertEqual(membership.added_by, self.party_owner)

    def test_owner_adds_their_own_private_character(self):
        own_private = create_character(self.party_owner, name="My Secret")
        self.login(self.party_owner)
        self.post_add(own_private.pk)
        self.assertTrue(self.party.memberships.filter(character=own_private).exists())

    def test_someone_elses_private_character_is_a_404(self):
        self.login(self.party_owner)
        self.assertEqual(self.post_add(self.private_char.pk).status_code, 404)
        self.assertFalse(self.party.memberships.exists())

    def test_soft_deleted_character_is_a_404(self):
        gone = create_character(
            self.character_owner, name="Gone Hero", is_public=True, is_deleted=True
        )
        self.login(self.party_owner)
        self.assertEqual(self.post_add(gone.pk).status_code, 404)

    def test_admin_adds_any_character_to_any_party(self):
        self.login(self.admin)
        response = self.post_add(self.private_char.pk)
        self.assertRedirects(response, self.detail_url())

    def test_non_manager_may_not_add(self):
        self.login(self.character_owner)
        response = self.post_add(self.public_char.pk)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.party.memberships.exists())

    def test_duplicate_add_re_renders_with_notice(self):
        self.add(self.public_char)
        self.login(self.party_owner)
        response = self.post_add(self.public_char.pk)
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            escape(
                parties_words().views.already_in_party.format(
                    name=self.public_char.name
                )
            ),
            status_code=400,
        )
        self.assertEqual(self.party.memberships.count(), 1)

    def test_non_numeric_character_id_is_a_400(self):
        self.login(self.party_owner)
        self.assertEqual(self.post_add("banana").status_code, 400)

    def test_get_is_refused(self):
        self.login(self.party_owner)
        response = self.client.get(
            reverse("parties:add_member", kwargs={"pk": self.party.pk})
        )
        self.assertEqual(response.status_code, 405)

    def test_anonymous_is_sent_to_login(self):
        response = self.post_add(self.public_char.pk)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)


class RemoveMemberTest(PartyViewTestCase):
    def setUp(self):
        super().setUp()
        self.membership = self.add(self.public_char)

    def post_remove(self, membership_id):
        return self.client.post(
            reverse("parties:remove_member", kwargs={"pk": self.party.pk}),
            {"membership_id": membership_id},
        )

    def test_party_owner_removes_a_member(self):
        self.login(self.party_owner)
        response = self.post_remove(self.membership.pk)
        self.assertRedirects(response, self.detail_url())
        self.assertFalse(self.party.memberships.exists())

    def test_character_owner_withdraws_from_a_party_they_do_not_manage(self):
        self.login(self.character_owner)
        response = self.post_remove(self.membership.pk)
        self.assertRedirects(response, self.detail_url())
        self.assertFalse(self.party.memberships.exists())

    def test_withdraw_wins_even_after_the_character_went_private(self):
        self.public_char.is_public = False
        self.public_char.save()
        self.login(self.character_owner)
        self.post_remove(self.membership.pk)
        self.assertFalse(self.party.memberships.exists())

    def test_withdraw_wins_even_for_a_soft_deleted_character(self):
        self.public_char.is_deleted = True
        self.public_char.save()
        self.login(self.character_owner)
        self.post_remove(self.membership.pk)
        self.assertFalse(self.party.memberships.exists())

    def test_dm_and_admin_remove_from_any_party(self):
        self.membership.delete()
        for manager in (self.dm, self.admin):
            membership = self.add(self.public_char)
            self.login(manager)
            self.post_remove(membership.pk)
            self.assertFalse(self.party.memberships.exists(), manager.username)

    def test_bystander_may_not_remove(self):
        self.login(self.other)
        response = self.post_remove(self.membership.pk)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(self.party.memberships.exists())

    def test_unknown_membership_is_a_404(self):
        self.login(self.party_owner)
        self.assertEqual(self.post_remove(999999).status_code, 404)

    def test_membership_of_another_party_is_a_404_here(self):
        second = Party.objects.create(name="The Red Band", owner=self.other)
        foreign = PartyMembership.objects.create(
            party=second, character=self.public_char
        )
        self.login(self.party_owner)
        self.assertEqual(self.post_remove(foreign.pk).status_code, 404)

    def test_non_numeric_membership_id_is_a_400(self):
        self.login(self.party_owner)
        self.assertEqual(self.post_remove("banana").status_code, 400)


class RosterRenderingTest(PartyViewTestCase):
    """Visibility-aware rows: full card through characters_visible_to,
    name-only past it — the party owner included."""

    def setUp(self):
        super().setUp()
        self.add(self.public_char)
        self.add(self.private_char)  # seeded directly: was addable via admin

    def test_visible_member_renders_the_full_card(self):
        self.login(self.other)
        response = self.client.get(self.detail_url())
        self.assertContains(
            response, reverse("characters:edit", kwargs={"pk": self.public_char.pk})
        )
        self.assertContains(response, "Famous Hero")
        self.assertContains(response, "level 1")

    def test_invisible_member_renders_name_only(self):
        """The name shows; the link, owner, and stats do not."""
        self.login(self.other)
        response = self.client.get(self.detail_url())
        self.assertContains(response, "Hidden Hero")
        self.assertContains(response, "Sheet not visible to you")
        self.assertNotContains(
            response, reverse("characters:edit", kwargs={"pk": self.private_char.pk})
        )

    def test_party_owner_sees_name_only_for_a_member_that_went_private(self):
        """No snapshot of add-time visibility: the owner's view follows the
        character's CURRENT visibility, so a member that went private (and
        isn't theirs) collapses to name-only for them too."""
        self.login(self.party_owner)
        response = self.client.get(self.detail_url())
        self.assertContains(response, "Hidden Hero")
        self.assertContains(response, "Sheet not visible to you")
        self.assertNotContains(
            response, reverse("characters:edit", kwargs={"pk": self.private_char.pk})
        )

    def test_soft_deleted_member_renders_name_only_for_everyone(self):
        self.public_char.is_deleted = True
        self.public_char.save()
        for viewer in (self.party_owner, self.character_owner, self.dm):
            self.login(viewer)
            response = self.client.get(self.detail_url())
            self.assertContains(response, "Famous Hero", msg_prefix=viewer.username)
            self.assertNotContains(
                response,
                reverse("characters:edit", kwargs={"pk": self.public_char.pk}),
                msg_prefix=viewer.username,
            )

    def test_dm_sees_the_private_member_in_full(self):
        self.login(self.dm)
        response = self.client.get(self.detail_url())
        self.assertContains(
            response, reverse("characters:edit", kwargs={"pk": self.private_char.pk})
        )

    def test_character_owner_gets_withdraw_button_and_no_manage_controls(self):
        self.login(self.character_owner)
        response = self.client.get(self.detail_url())
        self.assertContains(response, "Withdraw")
        self.assertNotContains(response, "Remove")
        self.assertNotContains(response, "Add Members")
        self.assertNotContains(
            response, reverse("parties:edit", kwargs={"pk": self.party.pk})
        )

    def test_party_owner_gets_remove_buttons_edit_link_and_the_shelf(self):
        self.login(self.party_owner)
        response = self.client.get(self.detail_url())
        self.assertContains(response, "Remove")
        self.assertContains(response, "Add Members")
        self.assertContains(
            response, reverse("parties:edit", kwargs={"pk": self.party.pk})
        )

    def test_bystander_reads_the_roster_with_no_buttons(self):
        self.login(self.other)
        response = self.client.get(self.detail_url())
        self.assertNotContains(response, "Withdraw")
        self.assertNotContains(response, "Remove")
        self.assertNotContains(response, "Add Members")

    def test_shelf_offers_addable_characters_but_not_seated_ones(self):
        mine = create_character(self.party_owner, name="My Second")
        self.login(self.party_owner)
        response = self.client.get(self.detail_url())
        shelf = list(response.context["addable_characters"])
        self.assertIn(mine, shelf)
        self.assertNotIn(self.public_char, shelf)
        self.assertNotIn(self.private_char, shelf)

    def test_experience_summary_renders(self):
        self.login(self.other)
        response = self.client.get(self.detail_url())
        self.assertContains(response, "2 members")
        self.assertContains(response, "average level 1.0")

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        mark_gate_passed(self.client)  # logout flushed the gate
        response = self.client.get(self.detail_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)
