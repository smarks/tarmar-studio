"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

from accounts.models import User
from common.test_helpers import BaseTestCase


class UsersListTest(BaseTestCase):
    """Tests for users list view."""

    def setUp(self):
        super().setUp()
        self.admin = self.create_admin()
        self.dm = self.create_dm()
        self.player = self.create_player()

    def test_users_list_requires_login(self):
        """Users list requires authentication."""
        response = self.client.get("/tarmar/accounts/users/")
        self.assertEqual(response.status_code, 302)

    def test_users_list_denied_for_player(self):
        """Players cannot access users list."""
        self.login_as(self.player)
        response = self.client.get("/tarmar/accounts/users/")
        self.assertEqual(response.status_code, 403)

    def test_users_list_accessible_by_admin(self):
        """Admins can access users list."""
        self.login_as(self.admin)
        response = self.client.get("/tarmar/accounts/users/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Users")

    def test_users_list_accessible_by_dm(self):
        """DMs can access users list."""
        self.login_as(self.dm)
        response = self.client.get("/tarmar/accounts/users/")
        self.assertEqual(response.status_code, 200)

    def test_users_list_shows_all_users(self):
        """Users list shows all users."""
        self.login_as(self.admin)
        response = self.client.get("/tarmar/accounts/users/")
        self.assertContains(response, "adminuser")
        self.assertContains(response, "dmuser")
        self.assertContains(response, "playeruser")

    def test_users_list_has_hamburger_menu(self):
        """Users list has hamburger menu."""
        self.login_as(self.admin)
        response = self.client.get("/tarmar/accounts/users/")
        self.assertContains(response, 'id="menuToggle"')

    def test_delete_button_carries_url_via_data_attribute(self):
        """Each row's Delete button gets its endpoint from {% url %}, not
        JS string concatenation (issue #112) — the click handler reads
        data-delete-url instead of building '/accounts/users/<id>/delete/'
        itself."""
        self.login_as(self.admin)
        response = self.client.get("/tarmar/accounts/users/")
        self.assertContains(
            response,
            f'data-delete-url="/tarmar/accounts/users/{self.dm.pk}/delete/"',
        )


class DeleteUserTest(BaseTestCase):
    """Tests for delete user functionality."""

    def setUp(self):
        super().setUp()
        self.admin = self.create_admin()
        self.dm = self.create_dm()
        self.target = self.create_user(username="targetuser")

    def test_delete_requires_login(self):
        """Delete user requires authentication."""
        response = self.client.post(f"/tarmar/accounts/users/{self.target.pk}/delete/")
        self.assertEqual(response.status_code, 302)

    def test_delete_denied_for_player(self):
        """Players cannot delete users."""
        player = self.create_player()
        self.login_as(player)
        response = self.client.post(f"/tarmar/accounts/users/{self.target.pk}/delete/")
        self.assertEqual(response.status_code, 403)

    def test_admin_can_delete_user(self):
        """Admins can delete users."""
        self.login_as(self.admin)
        response = self.client.post(f"/tarmar/accounts/users/{self.target.pk}/delete/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(pk=self.target.pk).exists())

    def test_dm_can_delete_user(self):
        """DMs can delete users."""
        self.login_as(self.dm)
        response = self.client.post(f"/tarmar/accounts/users/{self.target.pk}/delete/")
        self.assertEqual(response.status_code, 200)

    def test_cannot_delete_self(self):
        """Users cannot delete themselves."""
        self.login_as(self.admin)
        response = self.client.post(f"/tarmar/accounts/users/{self.admin.pk}/delete/")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())


class UsersListLastLoginColumnTest(BaseTestCase):
    """The "Last login" column added to the users table (issue #200)."""

    def setUp(self):
        super().setUp()
        self.admin = self.create_admin()
        self.never_logged_in = self.create_player("ghostuser")

    def test_last_login_column_header_present(self):
        """The table gains a "Last login" column."""
        self.login_as(self.admin)
        response = self.client.get("/tarmar/accounts/users/")
        self.assertContains(response, "<th>Last login</th>", html=True)

    def test_last_login_rendered_humanized(self):
        """A user who has logged in shows the timestamp, site-style, as a
        progressive-enhancement <time> element (issue #225) -- the UTC
        fallback text is the same "site-style" formatting as before; the
        viewer's browser reformats it to local time on load
        (static/js/local-time.js)."""
        from django.template.defaultfilters import date as date_filter
        from django.utils import timezone

        self.login_as(self.admin)
        self.admin.refresh_from_db()
        expected = date_filter(timezone.localtime(self.admin.last_login), "M d, Y H:i")
        response = self.client.get("/tarmar/accounts/users/")
        self.assertContains(
            response,
            f'<td class="last-login" data-label="Last login">'
            f'<time class="local-time" datetime="{self.admin.last_login.isoformat()}" '
            f'data-local-time-style="datetime">{expected}</time></td>',
            html=True,
        )

    def test_never_logged_in_shows_em_dash(self):
        """A user with no login on record gets an em dash, not a blank."""
        self.login_as(self.admin)
        response = self.client.get("/tarmar/accounts/users/")
        self.assertIsNone(User.objects.get(username="ghostuser").last_login)
        self.assertContains(
            response,
            '<td class="last-login" data-label="Last login">—</td>',
            html=True,
        )


class DeletingARoleAdminTest(BaseTestCase):
    """Only a role admin may delete an account that holds the Admin role.

    Issue #632: ``origami_auth.views.delete_user`` checks ``can_manage_users``,
    which a DM satisfies, while the role fields themselves are guarded by
    ``is_role_admin``. A DM could therefore not tick or untick anybody's Admin
    box but could delete the account holding it, and on a site with one role
    admin — the normal shape here — nothing in the app could appoint a
    replacement afterwards.
    """

    def setUp(self):
        super().setUp()
        self.admin = self.create_admin()
        self.dm = self.create_dm()
        self.player = self.create_player()

    def test_a_dm_cannot_delete_a_role_admin(self):
        """The hole in the guard: a DM aiming at the only Admin account."""
        self.login_as(self.dm)
        response = self.client.post(f"/tarmar/accounts/users/{self.admin.pk}/delete/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_a_refused_deletion_is_audited(self):
        """The attempt is on the record, named, like the deletions that run."""
        self.login_as(self.dm)
        with self.assertLogs("origami.audit", level="INFO") as captured:
            self.client.post(f"/tarmar/accounts/users/{self.admin.pk}/delete/")
        logged = "\n".join(captured.output)
        self.assertIn("USER_DELETE_REFUSED", logged)
        self.assertIn(self.dm.username, logged)
        self.assertIn(self.admin.username, logged)

    def test_a_dm_can_still_delete_an_account_with_no_admin_role(self):
        """The guard is about the Admin role, not about DMs deleting at all."""
        self.login_as(self.dm)
        response = self.client.post(f"/tarmar/accounts/users/{self.player.pk}/delete/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(pk=self.player.pk).exists())

    def test_a_role_admin_can_delete_another_role_admin(self):
        """An account that can revoke the role can remove the account too."""
        other_admin = self.create_admin(username="secondadmin")
        self.login_as(self.admin)
        response = self.client.post(f"/tarmar/accounts/users/{other_admin.pk}/delete/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(pk=other_admin.pk).exists())

    def test_the_users_list_offers_a_dm_no_delete_button_for_an_admin(self):
        """The page renders what the endpoint will accept, and nothing more."""
        self.login_as(self.dm)
        response = self.client.get("/tarmar/accounts/users/")
        self.assertNotContains(
            response,
            f'data-delete-url="/tarmar/accounts/users/{self.admin.pk}/delete/"',
        )
        self.assertContains(
            response,
            f'data-delete-url="/tarmar/accounts/users/{self.player.pk}/delete/"',
        )

    def test_the_users_list_still_offers_an_admin_every_delete_button(self):
        """A role admin sees the button on an Admin row, because it works."""
        self.login_as(self.admin)
        response = self.client.get("/tarmar/accounts/users/")
        self.assertContains(
            response,
            f'data-delete-url="/tarmar/accounts/users/{self.dm.pk}/delete/"',
        )
