"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

from django.test import TestCase

from accounts.forms import RegistrationForm
from common.test_helpers import TEST_PASSWORD, BaseTestCase


class UnitTestForms(TestCase):
    """Unit tests for forms."""

    def test_registration_form_valid(self):
        """Registration form should be valid with required fields."""
        from accounts.forms import RegistrationForm

        form = RegistrationForm(
            data={
                "username": "formuser",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
            }
        )
        self.assertTrue(form.is_valid())

    def test_registration_form_password_mismatch(self):
        """Registration form should be invalid with mismatched passwords."""
        from accounts.forms import RegistrationForm

        form = RegistrationForm(
            data={
                "username": "formuser",
                "password1": f"{TEST_PASSWORD}1",
                "password2": f"{TEST_PASSWORD}2",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("password2", form.errors)

    def test_registration_form_username_too_long(self):
        """Registration form should reject username over 25 chars."""
        from accounts.forms import RegistrationForm

        form = RegistrationForm(
            data={
                "username": "a" * 26,
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_registration_form_optional_fields(self):
        """Registration form should accept optional fields."""
        from accounts.forms import RegistrationForm

        form = RegistrationForm(
            data={
                "username": "optuser",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
                "real_name": "Optional User",
                "email": "opt@example.com",
                "phone": "1234567890",
                "discord": "optuser_disc",
                "preferred_contact": "email",
            }
        )
        self.assertTrue(form.is_valid())

    def test_registration_form_invalid_email(self):
        """Registration form should reject invalid email."""
        from accounts.forms import RegistrationForm

        form = RegistrationForm(
            data={
                "username": "emailuser",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
                "email": "not-an-email",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_registration_form_invalid_username_chars(self):
        """Registration form should reject usernames with invalid characters."""
        from accounts.forms import RegistrationForm

        form = RegistrationForm(
            data={
                "username": "bad@username!",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)
        self.assertIn(
            "letters, numbers, underscores, or hyphens", str(form.errors["username"][0])
        )


class PasswordRefusalsSitAtThePasswordTest(BaseTestCase):
    """A refusal belongs at the control that caused it (issue #711).

    ``UserCreationForm`` runs Django's password validators inside
    ``clean_password2``, so "This password is too short" rendered under
    Confirm Password -- a field that had been typed correctly -- while the
    Password field above it showed nothing.
    """

    def test_a_short_password_is_refused_at_the_password(self) -> None:
        form = RegistrationForm(
            data={"username": "shorty", "password1": "ab1", "password2": "ab1"}
        )

        self.assertFalse(form.is_valid())
        self.assertTrue(form.errors.get("password1"))
        self.assertFalse(form.errors.get("password2"))

    def test_two_different_entries_are_still_refused_at_the_second(self) -> None:
        """The second field is the one that disagrees with the first."""
        form = RegistrationForm(
            data={
                "username": "mismatcher",
                "password1": "a-long-enough-password",
                "password2": "a-different-long-password",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertTrue(form.errors.get("password2"))
        self.assertFalse(form.errors.get("password1"))
