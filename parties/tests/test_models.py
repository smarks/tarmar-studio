"""Party model rules (issue #274): the permission chokepoints, membership
uniqueness, the addable-characters rule, and the experience summary.

The power matrix under test, per Spencer's rulings:

- Any registered user creates parties (view-level; see test_views).
- The party owner (or DM/admin) manages: edits the party, adds any
  character they can read, removes any member.
- A character's owner can ALWAYS withdraw their character — from any
  party, whatever the character's visibility or deletion state.
"""

from django.db import IntegrityError

from common.test_helpers import BaseTestCase, create_character
from parties.models import (
    Party,
    PartyMembership,
    addable_characters,
    can_manage_party,
    can_remove_membership,
)


class PartyPermissionMatrixTest(BaseTestCase):
    """can_manage_party / can_remove_membership across every role."""

    def setUp(self):
        super().setUp()
        self.party_owner = self.create_user("bandleader")
        self.character_owner = self.create_user("keeper")
        self.other = self.create_user("stranger")
        self.dm = self.create_dm("table-dm")
        self.admin = self.create_admin("table-admin")
        self.party = Party.objects.create(
            name="The Grey Company", owner=self.party_owner
        )
        self.character = create_character(
            self.character_owner, name="Famous Hero", is_public=True
        )
        self.membership = PartyMembership.objects.create(
            party=self.party, character=self.character, added_by=self.party_owner
        )

    def test_manage_matrix(self):
        self.assertTrue(can_manage_party(self.party_owner, self.party))
        self.assertTrue(can_manage_party(self.dm, self.party))
        self.assertTrue(can_manage_party(self.admin, self.party))
        self.assertFalse(can_manage_party(self.character_owner, self.party))
        self.assertFalse(can_manage_party(self.other, self.party))

    def test_remove_matrix(self):
        """Party manager or the character's owner; nobody else."""
        for allowed in (self.party_owner, self.dm, self.admin, self.character_owner):
            self.assertTrue(
                can_remove_membership(allowed, self.membership), allowed.username
            )
        self.assertFalse(can_remove_membership(self.other, self.membership))

    def test_withdraw_wins_for_a_private_and_even_deleted_character(self):
        """Ownership needs no visibility: the owner of a character that went
        private — or was soft-deleted — can still pull it out."""
        self.character.is_public = False
        self.character.is_deleted = True
        self.character.save()
        self.assertTrue(can_remove_membership(self.character_owner, self.membership))
        self.assertFalse(can_remove_membership(self.other, self.membership))

    def test_membership_is_unique_per_party_and_character(self):
        with self.assertRaises(IntegrityError):
            PartyMembership.objects.create(
                party=self.party, character=self.character, added_by=self.party_owner
            )

    def test_character_may_join_many_parties(self):
        second = Party.objects.create(name="The Red Band", owner=self.other)
        PartyMembership.objects.create(party=second, character=self.character)
        self.assertEqual(self.character.party_memberships.count(), 2)

    def test_membership_survives_privacy_flip_and_soft_delete(self):
        self.character.is_public = False
        self.character.is_deleted = True
        self.character.save()
        self.assertTrue(PartyMembership.objects.filter(pk=self.membership.pk).exists())


class AddableCharactersTest(BaseTestCase):
    """The add rule IS the read rule: own + public for a regular user,
    everything undeleted for a DM/admin."""

    def setUp(self):
        super().setUp()
        self.party_owner = self.create_user("bandleader")
        self.other = self.create_user("stranger")
        self.admin = self.create_admin("table-admin")
        self.own_private = create_character(self.party_owner, name="My Secret")
        self.own_public = create_character(
            self.party_owner, name="My Star", is_public=True
        )
        self.foreign_public = create_character(
            self.other, name="Their Star", is_public=True
        )
        self.foreign_private = create_character(self.other, name="Their Secret")
        self.deleted_public = create_character(
            self.other, name="Gone Star", is_public=True, is_deleted=True
        )

    def _names(self, user):
        return set(addable_characters(user).values_list("name", flat=True))

    def test_regular_owner_may_add_own_plus_public(self):
        self.assertEqual(
            self._names(self.party_owner), {"My Secret", "My Star", "Their Star"}
        )

    def test_admin_may_add_anything_undeleted(self):
        self.assertEqual(
            self._names(self.admin),
            {"My Secret", "My Star", "Their Star", "Their Secret"},
        )

    def test_soft_deleted_characters_are_never_addable(self):
        for user in (self.party_owner, self.other, self.admin):
            self.assertNotIn("Gone Star", self._names(user))


class ExperienceSummaryTest(BaseTestCase):
    """The model property #276's hook generator will consume."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("bandleader")
        self.party = Party.objects.create(name="The Grey Company", owner=self.owner)

    def _add(self, name, total_xp, **overrides):
        character = create_character(
            self.owner, name=name, total_xp=total_xp, **overrides
        )
        PartyMembership.objects.create(party=self.party, character=character)
        return character

    def test_empty_roster_summarizes_to_none(self):
        self.assertIsNone(self.party.experience_summary)

    def test_average_and_range_over_member_levels(self):
        self._add("Novice", 0)  # level 1
        self._add("Veteran", 4000)  # level 3
        self._add("Champion", 16000)  # level 5
        summary = self.party.experience_summary
        assert summary is not None
        self.assertEqual(summary["count"], 3)
        self.assertEqual(summary["min_level"], 1)
        self.assertEqual(summary["max_level"], 5)
        self.assertAlmostEqual(summary["average_level"], 3.0)

    def test_soft_deleted_members_do_not_count(self):
        self._add("Novice", 0)
        self._add("Ghost", 512000, is_deleted=True)  # level 10, out of play
        summary = self.party.experience_summary
        assert summary is not None
        self.assertEqual(summary["count"], 1)
        self.assertEqual(summary["max_level"], 1)

    def test_all_deleted_roster_summarizes_to_none(self):
        self._add("Ghost", 0, is_deleted=True)
        self.assertIsNone(self.party.experience_summary)

    def test_private_members_count_like_public_ones(self):
        self._add("Secret Champion", 32000, is_public=False)  # level 6
        summary = self.party.experience_summary
        assert summary is not None
        self.assertEqual(summary["max_level"], 6)


class PartyOrderingTest(BaseTestCase):
    def test_parties_list_newest_first(self):
        owner = self.create_user("bandleader")
        first = Party.objects.create(name="First", owner=owner)
        second = Party.objects.create(name="Second", owner=owner)
        self.assertEqual(list(Party.objects.all()), [second, first])

    def test_str_forms(self):
        owner = self.create_user("bandleader")
        party = Party.objects.create(name="The Grey Company", owner=owner)
        character = create_character(owner, name="Famous Hero")
        membership = PartyMembership.objects.create(party=party, character=character)
        self.assertEqual(str(party), "The Grey Company")
        self.assertEqual(str(membership), "Famous Hero in The Grey Company")
