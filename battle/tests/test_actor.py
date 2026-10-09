"""Tests for building an Actor from a user or a guest key."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from battle.access import Actor


class ActorForUserOrGuestTest(SimpleTestCase):
    def test_a_signed_in_user_never_carries_a_guest_key(self):
        user = SimpleNamespace(pk=7)
        actor = Actor.for_user_or_guest(user, "guest-key-123")
        self.assertIs(actor.user, user)
        self.assertEqual(actor.guest_key, "")
        self.assertFalse(actor.is_guest)

    def test_without_a_user_the_guest_key_is_kept(self):
        actor = Actor.for_user_or_guest(None, "guest-key-123")
        self.assertIsNone(actor.user)
        self.assertEqual(actor.guest_key, "guest-key-123")
        self.assertTrue(actor.is_guest)
