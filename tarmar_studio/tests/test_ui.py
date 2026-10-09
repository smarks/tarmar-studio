"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

from common.test_helpers import BaseTestCase


class UITest(BaseTestCase):
    """UI tests for templates and page elements."""

    def test_home_page_has_hamburger_menu(self):
        """Home page should have hamburger menu button."""
        response = self.client.get("/tarmar/")
        self.assertContains(response, 'id="menuToggle"')
        self.assertContains(response, 'id="menu"')

    def test_home_page_has_logo(self):
        """Home page should have logo."""
        response = self.client.get("/tarmar/")
        self.assertContains(response, "Tarmar Studio")

    def test_home_page_has_favicon(self):
        """Home page should have favicon link."""
        response = self.client.get("/tarmar/")
        self.assertContains(response, "favicon.svg")

    def test_login_page_has_form(self):
        """Login page should have username and password fields."""
        response = self.client.get("/tarmar/accounts/login/")
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')
        self.assertContains(response, 'type="submit"')

    def test_register_page_has_all_fields(self):
        """Register page should have all registration fields."""
        response = self.client.get("/tarmar/accounts/register/")
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password1"')
        self.assertContains(response, 'name="password2"')
        self.assertContains(response, 'name="real_name"')
        self.assertContains(response, 'name="email"')
        self.assertContains(response, 'name="phone"')
        self.assertContains(response, 'name="discord"')
        self.assertContains(response, 'name="preferred_contact"')

    def test_register_page_has_preferred_contact_dropdown(self):
        """Register page should have preferred contact dropdown."""
        response = self.client.get("/tarmar/accounts/register/")
        self.assertContains(response, 'id="id_preferred_contact"')
        self.assertContains(response, "<select")

    def test_profile_page_has_hamburger_menu(self):
        """Profile page should have hamburger menu."""
        user = self.create_user(username="uiuser")
        self.login_as(user)
        response = self.client.get("/tarmar/accounts/profile/")
        self.assertContains(response, 'id="menuToggle"')
        self.assertContains(response, 'id="menu"')

    def test_profile_displays_username(self):
        """Profile page should display the username."""
        user = self.create_user(username="displayuser")
        self.login_as(user)
        response = self.client.get("/tarmar/accounts/profile/")
        self.assertContains(response, "displayuser")

    def test_profile_displays_optional_fields_when_set(self):
        """Profile page should display optional fields when they have values."""
        user = self.create_user(
            username="fulluser",
            real_name="Full User",
            email="full@example.com",
            phone="1234567890",
            discord="fulluser_disc",
        )
        self.login_as(user)
        response = self.client.get("/tarmar/accounts/profile/")
        self.assertContains(response, "Full User")
        self.assertContains(response, "full@example.com")
        self.assertContains(response, "1234567890")
        self.assertContains(response, "fulluser_disc")

    def test_login_page_has_register_link(self):
        """Login page should have link to register."""
        response = self.client.get("/tarmar/accounts/login/")
        self.assertContains(response, "/tarmar/accounts/register/")

    def test_register_page_has_login_link(self):
        """Register page should have link to login."""
        response = self.client.get("/tarmar/accounts/register/")
        self.assertContains(response, "/tarmar/accounts/login/")
