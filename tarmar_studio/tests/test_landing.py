"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

from common.test_helpers import BaseTestCase


class HomePageTest(BaseTestCase):
    def test_home_page_loads(self):
        """Verify home page is accessible."""
        response = self.client.get("/tarmar/")
        self.assertEqual(response.status_code, 200)

    def test_home_page_has_hero_image(self):
        """Verify home page displays hero image."""
        response = self.client.get("/tarmar/")
        self.assertContains(response, "tarmar01.jpeg")

    def test_home_page_has_login_link(self):
        """Verify home page has login link when not authenticated."""
        response = self.client.get("/tarmar/")
        self.assertContains(response, "/tarmar/accounts/login/")

    def test_home_page_has_register_link(self):
        """Verify home page always has register link."""
        response = self.client.get("/tarmar/")
        self.assertContains(response, "/tarmar/accounts/register/")

    def test_home_page_has_logout_when_authenticated(self):
        """Verify home page has logout link when authenticated."""
        user = self.create_user(username="homeuser")
        self.login_as(user)
        response = self.client.get("/tarmar/")
        self.assertContains(response, "/tarmar/accounts/logout/")

    def test_home_page_no_register_when_authenticated(self):
        """Verify home page hides register link when authenticated."""
        user = self.create_user(username="homeuser2")
        self.login_as(user)
        response = self.client.get("/tarmar/")
        self.assertNotContains(response, "/tarmar/accounts/register/")

    def test_home_page_no_login_when_authenticated(self):
        """Verify home page does not show login link when authenticated."""
        user = self.create_user(username="homeuser3")
        self.login_as(user)
        response = self.client.get("/tarmar/")
        self.assertNotContains(response, '"/tarmar/accounts/login/"')
