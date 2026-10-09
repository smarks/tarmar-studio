"""The login throttle must bound guessing, not ordinary use (issue #160).

origami-auth rate-limits every login POST, success and failure alike. With the
shared DatabaseCache from issue #139 that counter is now one site-wide, restart-
surviving bucket per client address, so ten *successful* logins from one address
locked it out for five minutes. ``accounts.views.LoginView`` charges only
failures; these tests hold both halves of that in place.
"""

from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from accounts import views as accounts_views
from common.models import RateLimitCounter
from common.test_helpers import TEST_PASSWORD, BaseTestCase

LOGIN_URL = "/tarmar/accounts/login/"
PASSWORD = TEST_PASSWORD
WRONG_PASSWORD = "not-the-password"
ATTEMPTS_BEFORE_BLOCK = 10
LOGIN_FAILURES_KEY = "rate_limit:login_failures:ip_127.0.0.1"


class LoginRateLimitTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()

    def _attempt(self, password=PASSWORD):
        return self.client.post(
            LOGIN_URL, {"username": self.user.username, "password": password}
        )

    def _exhaust(self):
        for attempt in range(ATTEMPTS_BEFORE_BLOCK):
            response = self._attempt(WRONG_PASSWORD)
            self.assertEqual(response.status_code, 200, msg=f"attempt {attempt}")

    def _assert_signed_in(self, response, msg=""):
        """A 302 alone is not proof: assert the session really holds a user.

        The view reaches Django's ``post`` past origami-auth's decorated one.
        If that MRO hop ever broke and redirected without authenticating, the
        status code would not notice.
        """
        self.assertEqual(response.status_code, 302, msg=msg)
        self.assertIn("_auth_user_id", self.client.session, msg=msg)

    def test_a_successful_login_leaves_no_trace_of_the_counter_row(self):
        """release() deletes the row once nothing is left charged in it, so
        a clean login leaves no trace of a window at all -- not just a
        current_count() of 0 (which an expired-but-not-yet-culled row would
        also read as), but no row in the table at all."""
        self._assert_signed_in(self._attempt())
        self.assertFalse(
            RateLimitCounter.objects.filter(pk=LOGIN_FAILURES_KEY).exists()
        )

    def test_a_long_run_of_successful_logins_is_never_throttled(self):
        """The e2e suite's shape: one address, many real logins, no lockout."""
        for attempt in range(ATTEMPTS_BEFORE_BLOCK * 2 + 1):
            self._assert_signed_in(self._attempt(), msg=f"attempt {attempt}")
            self.logout()

    def test_failed_logins_still_get_throttled(self):
        self._exhaust()

        response = self._attempt(WRONG_PASSWORD)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "300")

    def test_a_success_does_not_wipe_the_failure_count(self):
        """Guessing must not be reset by owning one valid account."""
        for _ in range(ATTEMPTS_BEFORE_BLOCK - 1):
            self._attempt(WRONG_PASSWORD)

        self._assert_signed_in(self._attempt())
        self.logout()

        self.assertEqual(self._attempt(WRONG_PASSWORD).status_code, 200)
        self.assertEqual(self._attempt(WRONG_PASSWORD).status_code, 429)

    def test_a_locked_out_address_is_refused_even_with_the_right_password(self):
        self._exhaust()

        self.assertEqual(self._attempt().status_code, 429)

    def test_one_address_cannot_lock_out_another(self):
        self._exhaust()
        self.assertEqual(self._attempt(WRONG_PASSWORD).status_code, 429)

        elsewhere = self.client.post(
            LOGIN_URL,
            {"username": self.user.username, "password": PASSWORD},
            REMOTE_ADDR="198.51.100.7",
        )
        self._assert_signed_in(elsewhere)


class LoginRateLimitExpiryTest(BaseTestCase):
    """The counter can expire mid-attempt; neither end may 500 (issue #160).

    The charge is taken before the password is checked and refunded after, so
    the window can close in between. Both halves have to survive their entry
    vanishing underneath them.
    """

    def setUp(self):
        super().setUp()
        self.user = self.create_user()

    def _attempt(self, password=PASSWORD):
        return self.client.post(
            LOGIN_URL, {"username": self.user.username, "password": password}
        )

    def test_a_charge_on_a_window_that_expired_before_this_request_opens_a_fresh_one(
        self,
    ):
        RateLimitCounter.objects.create(
            key=LOGIN_FAILURES_KEY,
            count=9,
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        self.assertEqual(self._attempt(WRONG_PASSWORD).status_code, 200)

        self.assertEqual(RateLimitCounter.objects.get(pk=LOGIN_FAILURES_KEY).count, 1)

    def test_a_refund_that_lands_after_the_window_expired_still_signs_you_in(self):
        """The window can elapse between the charge and the refund too.

        Patches ``accounts.views.release`` -- the call the view makes after
        confirming the login -- so that, just before it runs for real, the
        counter's window is pushed into the past from underneath it. That
        reproduces "the window closed while the password was being
        checked" without needing the 300-second wait, and without knowing
        anything about how release() itself is implemented.
        """
        real_release = accounts_views.release

        def expire_then_release(key, *args, **kwargs):
            RateLimitCounter.objects.filter(pk=key).update(
                expires_at=timezone.now() - timedelta(seconds=1)
            )
            return real_release(key, *args, **kwargs)

        with patch.object(accounts_views, "release", side_effect=expire_then_release):
            response = self._attempt()

        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)


class LoginRateLimitIsolationRegressionTest(BaseTestCase):
    """Rate-limit counters must not leak from one test into the next (#109).

    Unlike the classes above, ``setUp`` here does NOT call ``cache.clear()``.
    That is the point: it proves the isolation actually relied on comes from
    ``TestCase``'s per-test transaction rollback (see the comment on
    ``BaseTestCase.setUp`` in common/test_helpers.py), not from a manual
    clear. Method names are prefixed with a run order because
    ``unittest`` runs test methods in alphabetical order by default -- the
    ordering has to be guaranteed for "test one runs before test two" to mean
    anything.
    """

    def setUp(self):
        super().setUp()
        self.user = self.create_user()

    def _attempt(self, password=PASSWORD):
        return self.client.post(
            LOGIN_URL, {"username": self.user.username, "password": password}
        )

    def test_1_first_test_exhausts_the_limit_and_gets_blocked(self):
        for attempt in range(ATTEMPTS_BEFORE_BLOCK):
            response = self._attempt(WRONG_PASSWORD)
            self.assertEqual(response.status_code, 200, msg=f"attempt {attempt}")

        self.assertEqual(self._attempt(WRONG_PASSWORD).status_code, 429)

    def test_2_second_test_starts_with_a_clean_counter(self):
        # If test_1's counter had bled into this test, this first attempt --
        # a different user, but the same client IP the counter keys on --
        # would already come back 429 instead of 200.
        self.assertEqual(self._attempt(WRONG_PASSWORD).status_code, 200)
