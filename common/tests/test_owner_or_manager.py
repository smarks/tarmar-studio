"""Tests for the shared owner-or-manager rule."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from common.helpers import is_owner_or_manager


class IsOwnerOrManagerTest(SimpleTestCase):
    def setUp(self):
        self.owner = SimpleNamespace(username="owner", can_manage_users=False)
        self.stranger = SimpleNamespace(username="stranger", can_manage_users=False)
        self.manager = SimpleNamespace(username="manager", can_manage_users=True)

    def test_the_owner_may(self):
        self.assertTrue(is_owner_or_manager(self.owner, self.owner))

    def test_a_dm_or_admin_may(self):
        self.assertTrue(is_owner_or_manager(self.manager, self.owner))

    def test_anyone_else_may_not(self):
        self.assertFalse(is_owner_or_manager(self.stranger, self.owner))
