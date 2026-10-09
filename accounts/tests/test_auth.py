"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

from accounts.models import User
from common.test_helpers import TEST_PASSWORD, BaseTestCase


class AuthenticationTest(BaseTestCase):
    def test_register_page_loads(self):
        """Verify registration page is accessible."""
        response = self.client.get("/tarmar/accounts/register/")
        self.assertEqual(response.status_code, 200)

    def test_login_page_loads(self):
        """Verify login page is accessible."""
        response = self.client.get("/tarmar/accounts/login/")
        self.assertEqual(response.status_code, 200)

    def test_register_user(self):
        """Verify user registration works."""
        response = self.client.post(
            "/tarmar/accounts/register/",
            {
                "username": "testuser",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="testuser").exists())

    def test_register_user_with_optional_fields(self):
        """Verify registration with optional fields."""
        response = self.client.post(
            "/tarmar/accounts/register/",
            {
                "username": "testuser2",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
                "real_name": "Test User",
                "email": "test@example.com",
                "phone": "1234567890",
                "discord": "testuser_disc",
                "preferred_contact": "email",
            },
        )
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username="testuser2")
        self.assertEqual(user.real_name, "Test User")
        self.assertEqual(user.email, "test@example.com")
        self.assertEqual(user.preferred_contact, "email")

    def test_username_max_length(self):
        """Verify username is limited to 25 characters."""
        response = self.client.post(
            "/tarmar/accounts/register/",
            {
                "username": "a" * 26,
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="a" * 26).exists())

    def test_username_unique(self):
        """Verify username must be unique."""
        self.create_user(username="existinguser")
        response = self.client.post(
            "/tarmar/accounts/register/",
            {
                "username": "existinguser",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(username="existinguser").count(), 1)

    def test_login(self):
        """Verify login works."""
        self.create_user(username="loginuser")
        response = self.client.post(
            "/tarmar/accounts/login/",
            {"username": "loginuser", "password": TEST_PASSWORD},
        )
        self.assertEqual(response.status_code, 302)

    def test_profile_requires_login(self):
        """Verify profile page requires authentication."""
        response = self.client.get("/tarmar/accounts/profile/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/tarmar/accounts/login/", response.url)

    def test_profile_accessible_when_logged_in(self):
        """Verify profile page is accessible when logged in."""
        user = self.create_user(username="profileuser")
        self.login_as(user)
        response = self.client.get("/tarmar/accounts/profile/")
        self.assertEqual(response.status_code, 200)


class AuthFlowTest(BaseTestCase):
    def test_register_logout_login_flow(self):
        """Test full flow: register new user, logout, then login successfully."""
        client = self.client

        # Step 1: Register a new user
        response = client.post(
            "/tarmar/accounts/register/",
            {
                "username": "flowuser",
                "password1": "flowpass123",
                "password2": "flowpass123",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="flowuser").exists())

        # Verify user is logged in after registration
        response = client.get("/tarmar/accounts/profile/")
        self.assertEqual(response.status_code, 200)

        # Step 2: Logout
        response = client.post("/tarmar/accounts/logout/")
        self.assertEqual(response.status_code, 302)
        # The front door routes per host (issue #269); on the default host
        # it lands on the tarmar home.
        self.assertEqual(response.url, "/")

        # Verify home page shows login link (user is logged out)
        response = client.get("/tarmar/")
        self.assertContains(response, "/tarmar/accounts/login/")
        self.assertNotContains(response, "/tarmar/accounts/logout/")

        # Verify user is logged out (profile requires login)
        response = client.get("/tarmar/accounts/profile/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/tarmar/accounts/login/", response.url)

        # Step 3: Login with the registered user
        response = client.post(
            "/tarmar/accounts/login/",
            {"username": "flowuser", "password": "flowpass123"},
        )
        self.assertEqual(response.status_code, 302)

        # Verify user is logged in again
        response = client.get("/tarmar/accounts/profile/")
        self.assertEqual(response.status_code, 200)
