"""Django's admin sign-in spends the site's login budget (issue #173).

``/admin/login/`` took unlimited password guesses while the site's own
sign-in refused after ten failures per address in five minutes. Both doors
now go through ``accounts.login_limit.limit_failed_logins`` with one counter
per address, so these tests hold the admin's door to exactly what
``test_login_rate_limit`` holds the site's to, and pin that the budget is
shared rather than doubled. The lockout's words are read from the loaded
configuration (``[login]`` in ``config/common-refusals.toml``), never typed
here.
"""

from django.contrib import admin
from django.test import TestCase
from django.urls import reverse

from accounts.admin_site import LimitedAdminSite
from accounts.login_limit import (
    FAILED_LOGIN_MAX_ATTEMPTS,
    FAILED_LOGIN_WINDOW_SECONDS,
    login_failures_key,
)
from accounts.models import User
from common.models import RateLimitCounter
from common.refusal_words import refusal_words
from common.test_helpers import TEST_CLIENT_ADDRESS, TEST_PASSWORD, mark_gate_passed

WRONG_PASSWORD = "not-the-password"
#: The counter key the test client's address charges, at either door.
LOGIN_FAILURES_KEY = login_failures_key(TEST_CLIENT_ADDRESS)


class AdminLoginRateLimitTest(TestCase):
    """Guesses at ``/admin/login/`` are counted and refused like the site's."""

    def setUp(self) -> None:
        super().setUp()
        self.staff = User.objects.create_user(
            "keeper", password=TEST_PASSWORD, is_staff=True, is_superuser=True
        )
        self.admin_login_url = reverse("admin:login")
        self.admin_index_url = reverse("admin:index")
        self.site_login_url = reverse("login")
        mark_gate_passed(self.client)

    def _admin_attempt(self, password: str = TEST_PASSWORD):
        return self.client.post(
            self.admin_login_url,
            {
                "username": self.staff.username,
                "password": password,
                "next": self.admin_index_url,
            },
        )

    def _site_attempt(self, password: str = TEST_PASSWORD):
        return self.client.post(
            self.site_login_url,
            {"username": self.staff.username, "password": password},
        )

    def _wrong_admin_guesses(self, count: int) -> None:
        for attempt in range(count):
            response = self._admin_attempt(WRONG_PASSWORD)
            self.assertEqual(response.status_code, 200, msg=f"attempt {attempt}")

    def _assert_locked_out(self, response) -> None:
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], str(FAILED_LOGIN_WINDOW_SECONDS))
        self.assertContains(
            response, refusal_words().login.lockout_headline, status_code=429
        )
        self.assertContains(
            response, refusal_words().login.lockout_message, status_code=429
        )

    def test_the_default_admin_site_is_the_limited_one(self):
        """``admin.site`` must resolve to the limited class, or every
        ``@admin.register`` would feed a site this limiter never sees."""
        self.assertIsInstance(admin.site, LimitedAdminSite)

    def test_wrong_passwords_at_the_admin_door_are_counted(self):
        self._wrong_admin_guesses(3)
        self.assertEqual(RateLimitCounter.objects.get(pk=LOGIN_FAILURES_KEY).count, 3)

    def test_the_budget_spent_at_the_admin_door_is_refused_with_the_sites_429(self):
        self._wrong_admin_guesses(FAILED_LOGIN_MAX_ATTEMPTS)
        self._assert_locked_out(self._admin_attempt(WRONG_PASSWORD))

    def test_a_locked_out_address_is_refused_even_with_the_right_password(self):
        """The evidence in #173: fifteen wrong guesses, then the right one
        answered 302 to /admin/. Now the right one is refused too."""
        self._wrong_admin_guesses(FAILED_LOGIN_MAX_ATTEMPTS)

        self.assertEqual(self._admin_attempt().status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_genuine_admin_sign_in_leaves_no_counter_row(self):
        response = self._admin_attempt()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.admin_index_url)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertFalse(
            RateLimitCounter.objects.filter(pk=LOGIN_FAILURES_KEY).exists()
        )

    def test_the_admin_and_site_doors_spend_one_budget(self):
        """Half the guesses at each door: the next guess at either is refused.
        Two counters would have given an attacker twenty."""
        half = FAILED_LOGIN_MAX_ATTEMPTS // 2
        self._wrong_admin_guesses(half)
        for attempt in range(FAILED_LOGIN_MAX_ATTEMPTS - half):
            response = self._site_attempt(WRONG_PASSWORD)
            self.assertEqual(response.status_code, 200, msg=f"site attempt {attempt}")

        self._assert_locked_out(self._admin_attempt(WRONG_PASSWORD))
        self._assert_locked_out(self._site_attempt(WRONG_PASSWORD))

    def test_viewing_the_admin_form_costs_nothing(self):
        """The methods Django's CSRF check calls safe only read; none is a guess."""
        for _view in range(FAILED_LOGIN_MAX_ATTEMPTS + 1):
            self.assertEqual(self.client.get(self.admin_login_url).status_code, 200)
            self.assertEqual(self.client.head(self.admin_login_url).status_code, 200)
            self.assertIn(
                self.client.options(self.admin_login_url).status_code, (200, 405)
            )
        self.assertFalse(
            RateLimitCounter.objects.filter(pk=LOGIN_FAILURES_KEY).exists()
        )

    def test_any_other_method_is_a_counted_guess(self):
        """A PUT or a PATCH carries no form Django reads, but it is still a
        request at the door, and the guarantee must not rest on
        ``request.POST`` being empty for it (review of #173): each is charged,
        and the budget they spend refuses the right password afterwards."""
        for attempt in range(FAILED_LOGIN_MAX_ATTEMPTS):
            method = self.client.put if attempt % 2 == 0 else self.client.patch
            response = method(self.admin_login_url, data="", content_type="text/plain")
            # Django's FormView answers a PUT as a POST (an empty, failed
            # form) and a PATCH with 405; both are counted all the same.
            self.assertIn(response.status_code, (200, 405), msg=f"attempt {attempt}")
            self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(
            RateLimitCounter.objects.get(pk=LOGIN_FAILURES_KEY).count,
            FAILED_LOGIN_MAX_ATTEMPTS,
        )

        self._assert_locked_out(self._admin_attempt())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_the_form_is_still_the_admins_own(self):
        """The limiter wraps the parent's view; it does not replace it."""
        response = self.client.get(self.admin_login_url)
        self.assertContains(response, 'name="username"')
        self.assertContains(response, "Log in")
        self.assertEqual(
            response["Cache-Control"],
            "max-age=0, no-cache, no-store, must-revalidate, private",
        )
