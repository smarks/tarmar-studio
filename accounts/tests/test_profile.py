"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

from accounts.models import User
from accounts.views import EMAIL_FIELD_REFUSAL
from common.test_helpers import BaseTestCase


class ProfilePageTest(BaseTestCase):
    """Tests for editable profile page."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user(
            username="edituser",
            real_name="Original Name",
            email="original@example.com",
        )

    def test_profile_shows_editable_fields(self):
        """Profile page shows editable input fields."""
        self.login_as(self.user)
        response = self.client.get("/tarmar/accounts/profile/")
        self.assertContains(response, 'name="real_name"')
        self.assertContains(response, 'name="email"')
        self.assertContains(response, 'name="phone"')
        self.assertContains(response, 'name="discord"')

    def test_profile_shows_current_values(self):
        """Profile page shows current user values in fields."""
        self.login_as(self.user)
        response = self.client.get("/tarmar/accounts/profile/")
        self.assertContains(response, "Original Name")
        self.assertContains(response, "original@example.com")

    def test_non_admin_cannot_see_role_checkboxes(self):
        """Non-admin users see roles as text, not checkboxes."""
        self.login_as(self.user)
        response = self.client.get("/tarmar/accounts/profile/")
        self.assertNotContains(response, 'name="is_role_admin"')
        self.assertNotContains(response, 'name="is_dm"')
        self.assertNotContains(response, 'name="is_player"')
        self.assertContains(response, "Player")  # Displayed as text

    def test_admin_can_see_role_checkboxes(self):
        """Admin users see role checkboxes."""
        self.user.is_role_admin = True
        self.user.save()
        self.login_as(self.user)
        response = self.client.get("/tarmar/accounts/profile/")
        self.assertContains(response, 'name="is_role_admin"')
        self.assertContains(response, 'name="is_dm"')
        self.assertContains(response, 'name="is_player"')

    def test_profile_has_autosave_class(self):
        """Profile page has autosave class on input fields."""
        self.login_as(self.user)
        response = self.client.get("/tarmar/accounts/profile/")
        self.assertContains(response, 'class="autosave"')

    def test_profile_has_hamburger_menu(self):
        """Profile page has hamburger menu."""
        self.login_as(self.user)
        response = self.client.get("/tarmar/accounts/profile/")
        self.assertContains(response, 'id="menuToggle"')
        self.assertContains(response, 'id="menu"')


class ProfileAutoSaveTest(BaseTestCase):
    """Tests for profile auto-save API endpoint."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="autosaveuser", real_name="Original")

    def test_autosave_requires_login(self):
        """Auto-save endpoint requires authentication."""
        response = self.post_json(
            "/tarmar/accounts/profile/update-field/",
            {"field": "real_name", "value": "New Name"},
        )
        self.assertEqual(response.status_code, 302)

    def test_autosave_updates_profile_field(self):
        """Auto-save endpoint updates profile fields."""
        self.login_as(self.user)
        response = self.post_json(
            "/tarmar/accounts/profile/update-field/",
            {"field": "real_name", "value": "New Name"},
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.real_name, "New Name")

    def test_autosave_refuses_email(self):
        """Auto-save no longer writes the email address (issue #931).

        It changes only on its own page, with the password and a link to the
        new address; a plain POST from a signed-in browser must not be enough.
        """
        self.user.email = "original@example.com"
        self.user.save()
        self.login_as(self.user)
        response = self.post_json(
            "/tarmar/accounts/profile/update-field/",
            {"field": "email", "value": "new@example.com"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("its own page", response.json()["error"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "original@example.com")

    def test_autosave_rejects_invalid_phone(self):
        """Profile validators run on the AJAX edit path (phone regex)."""
        self.login_as(self.user)
        response = self.post_json(
            "/tarmar/accounts/profile/update-field/",
            {"field": "phone", "value": "not-a-phone!!"},
        )
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.phone, "not-a-phone!!")

    def test_autosave_rejects_invalid_email(self):
        """The address is refused before its format is looked at (#931).

        Format validation on this path is pinned on the fields it still
        writes: phone above, Discord below.
        """
        self.login_as(self.user)
        response = self.post_json(
            "/tarmar/accounts/profile/update-field/",
            {"field": "email", "value": "not-an-email"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], EMAIL_FIELD_REFUSAL)

    def test_autosave_rejects_invalid_discord(self):
        """Profile validators run on the AJAX edit path (Discord regex)."""
        self.login_as(self.user)
        response = self.post_json(
            "/tarmar/accounts/profile/update-field/",
            {"field": "discord", "value": "no spaces allowed!"},
        )
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.discord, "no spaces allowed!")

    def test_autosave_rejects_invalid_field(self):
        """Auto-save endpoint rejects invalid field names."""
        self.login_as(self.user)
        response = self.post_json(
            "/tarmar/accounts/profile/update-field/",
            {"field": "invalid_field", "value": "test"},
        )
        self.assertEqual(response.status_code, 400)

    def test_autosave_rejects_get_request(self):
        """Auto-save endpoint only accepts POST."""
        self.login_as(self.user)
        response = self.client.get("/tarmar/accounts/profile/update-field/")
        self.assertEqual(response.status_code, 405)

    def test_autosave_non_admin_cannot_change_roles(self):
        """Non-admin cannot change role fields via auto-save."""
        self.login_as(self.user)
        response = self.post_json(
            "/tarmar/accounts/profile/update-field/",
            {"field": "is_role_admin", "value": True},
        )
        self.assertEqual(response.status_code, 403)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_role_admin)

    def test_autosave_admin_can_change_roles(self):
        """Admin can change role fields via auto-save."""
        self.user.is_role_admin = True
        self.user.save()
        self.login_as(self.user)
        response = self.post_json(
            "/tarmar/accounts/profile/update-field/",
            {"field": "is_dm", "value": True},
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_dm)

    def test_autosave_returns_json(self):
        """Auto-save endpoint returns JSON response."""
        self.login_as(self.user)
        response = self.post_json(
            "/tarmar/accounts/profile/update-field/",
            {"field": "phone", "value": "1234567890"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

    def test_autosave_rejects_invalid_json(self):
        """Auto-save endpoint rejects invalid JSON."""
        self.login_as(self.user)
        response = self.client.post(
            "/tarmar/accounts/profile/update-field/",
            "not valid json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class ViewOtherUserProfileTest(BaseTestCase):
    """Tests for viewing and editing other users' profiles."""

    def setUp(self):
        super().setUp()
        self.admin = self.create_admin(real_name="Admin")
        self.dm = self.create_dm()
        self.target = self.create_user(username="targetuser", real_name="Target")

    def test_admin_can_view_other_user_profile(self):
        """Admins can view other users' profiles."""
        self.login_as(self.admin)
        response = self.client.get(f"/tarmar/accounts/profile/{self.target.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "targetuser")
        self.assertContains(response, "Target")

    def test_dm_can_view_other_user_profile(self):
        """DMs can view other users' profiles."""
        self.login_as(self.dm)
        response = self.client.get(f"/tarmar/accounts/profile/{self.target.pk}/")
        self.assertEqual(response.status_code, 200)

    def test_admin_can_edit_other_user_profile(self):
        """Admins can edit other users' profiles via auto-save."""
        self.login_as(self.admin)
        response = self.post_json(
            f"/tarmar/accounts/profile/{self.target.pk}/update-field/",
            {"field": "real_name", "value": "Updated Name"},
        )
        self.assertEqual(response.status_code, 200)
        self.target.refresh_from_db()
        self.assertEqual(self.target.real_name, "Updated Name")

    def test_admin_can_edit_other_user_roles(self):
        """Admins can edit other users' roles."""
        self.login_as(self.admin)
        response = self.post_json(
            f"/tarmar/accounts/profile/{self.target.pk}/update-field/",
            {"field": "is_dm", "value": True},
        )
        self.assertEqual(response.status_code, 200)
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_dm)

    def test_dm_cannot_edit_roles(self):
        """DMs cannot edit roles (only admins can)."""
        self.login_as(self.dm)
        response = self.post_json(
            f"/tarmar/accounts/profile/{self.target.pk}/update-field/",
            {"field": "is_role_admin", "value": True},
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_update_nonexistent_user_returns_403(self):
        """Admins updating non-existent user get permission denied (prevents enumeration)."""
        self.login_as(self.admin)
        response = self.post_json(
            "/tarmar/accounts/profile/99999/update-field/",
            {"field": "real_name", "value": "Whatever"},
        )
        # Returns 403 instead of 404 to prevent user enumeration
        self.assertEqual(response.status_code, 403)

    def test_hamburger_menu_shows_users_for_admin(self):
        """Hamburger menu shows Users link for admins."""
        self.login_as(self.admin)
        response = self.client.get("/tarmar/")
        self.assertContains(response, "/tarmar/accounts/users/")


class LastRoleAdminTest(BaseTestCase):
    """The site can never be left with no account that can grant roles.

    Issue #651: an admin's own profile renders the Admin checkbox, so unticking
    it cleared ``is_role_admin`` on the acting account. Every attempt to put it
    back was then refused (403) and the checkbox was gone from the page;
    Django's ``/admin/`` gates on ``is_staff``/``is_superuser``, which nothing
    here sets, so recovery needed shell or database access on the deploy host.
    """

    ROLE_URL = "/tarmar/accounts/profile/update-field/"

    def setUp(self):
        super().setUp()
        self.admin = self.create_admin()

    def test_the_only_admin_cannot_take_the_role_off_themselves(self):
        """The one-click lockout, refused with a reason the page can show."""
        self.login_as(self.admin)
        response = self.post_json(
            self.ROLE_URL, {"field": "is_role_admin", "value": False}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("only Admin", response.json()["error"])
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_role_admin)

    def test_the_last_admin_cannot_be_demoted_from_another_profile_either(self):
        """The same write, aimed through the other user's profile page."""
        other_admin = self.create_admin(username="secondadmin")
        self.login_as(other_admin)
        # Take the role off one of the two, which is allowed...
        first = self.post_json(
            f"/tarmar/accounts/profile/{self.admin.pk}/update-field/",
            {"field": "is_role_admin", "value": False},
        )
        self.assertEqual(first.status_code, 200)
        # ...and the removal that would leave the site with none is refused,
        # aimed through a profile page by primary key rather than at self.
        response = self.post_json(
            f"/tarmar/accounts/profile/{other_admin.pk}/update-field/",
            {"field": "is_role_admin", "value": False},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(User.objects.filter(is_role_admin=True).count(), 1)

    def test_an_admin_with_a_colleague_can_still_step_down(self):
        """The guard is about the last one, not about stepping down at all."""
        self.create_admin(username="secondadmin")
        self.login_as(self.admin)
        response = self.post_json(
            self.ROLE_URL, {"field": "is_role_admin", "value": False}
        )
        self.assertEqual(response.status_code, 200)
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_role_admin)

    def test_a_refused_demotion_is_audited(self):
        """A near-miss on the site's only Admin belongs on the record."""
        self.login_as(self.admin)
        with self.assertLogs("origami.audit", level="INFO") as captured:
            self.post_json(self.ROLE_URL, {"field": "is_role_admin", "value": False})
        logged = "\n".join(captured.output)
        self.assertIn("ROLE_CHANGE_REFUSED", logged)
        self.assertIn(self.admin.username, logged)

    def test_a_role_change_on_your_own_account_is_audited(self):
        """The library logs only changes made to somebody else (`if user_id`)."""
        self.login_as(self.admin)
        with self.assertLogs("origami.audit", level="INFO") as captured:
            response = self.post_json(self.ROLE_URL, {"field": "is_dm", "value": True})
        self.assertEqual(response.status_code, 200)
        logged = "\n".join(captured.output)
        self.assertIn("ROLE_CHANGE", logged)
        self.assertIn("is_dm", logged)
        self.assertIn("their own account", logged)

    def test_a_role_write_that_changes_nothing_is_not_audited(self):
        """Setting a role to what it already is leaves no record, as before."""
        self.login_as(self.admin)
        with self.assertNoLogs("origami.audit", level="INFO"):
            response = self.post_json(
                self.ROLE_URL, {"field": "is_role_admin", "value": True}
            )
        self.assertEqual(response.status_code, 200)

    def test_a_profile_field_still_saves_on_your_own_account(self):
        """The wrapper hands everything that is not a role write straight on."""
        self.login_as(self.admin)
        response = self.post_json(
            self.ROLE_URL, {"field": "real_name", "value": "Spencer"}
        )
        self.assertEqual(response.status_code, 200)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.real_name, "Spencer")
