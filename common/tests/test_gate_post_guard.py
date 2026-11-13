"""What stands in front of the gate's POST, and what its pages carry (#175, #176).

Three facts about the passphrase gate that are decided by ``settings.MIDDLEWARE``
and the limiter in front of it:

- A POST to the gate is checked for a CSRF token before the passphrase is
  compared, and a POST refused there is not counted as a guess (issue #175).
- Only a POST to the gate's own path, ``GATE_PATH``, is a guess: a
  ``passphrase`` field posted anywhere else charges nothing (issue #175).
- The gate's own responses -- the form, the refusal, the 429 and the CSRF
  403 -- carry ``X-Frame-Options`` like every view's page (issue #176).

The form's action is ``GATE_PATH`` too, so the spelling the limiter counts
is the one the browser posts to.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, SimpleTestCase, TestCase

from common.gate import (
    CSP_MIDDLEWARE,
    DEV_GATE_PASSPHRASE,
    FRAME_OPTIONS_MIDDLEWARE,
    GATE_LIMITER_MIDDLEWARE,
    GATE_MIDDLEWARE,
    GATE_PATH,
)
from common.middleware import (
    GATE_SESSION_FLAG,
    GateRateLimitMiddleware,
    gate_counter_key,
    require_gate_middleware_order,
)
from common.rate_limit import current_count
from common.test_helpers import TEST_CLIENT_ADDRESS

GATED_PAGE = "/tarmar/"
WRONG_PASSPHRASE = "gandalf"
#: The gate limiter's counter key for the test client's address.
GATE_COUNTER_KEY = gate_counter_key(TEST_CLIENT_ADDRESS)
#: Public paths a passphrase field used to be counted on (issue #175).
NOT_THE_GATE = ("/tarmar/battles/", "/tarmar/accounts/login/", GATED_PAGE)
FRAME_OPTIONS_HEADER = "X-Frame-Options"
CSRF_FIELD = "csrfmiddlewaretoken"


class GatePostCsrfTest(TestCase):
    """The gate's POST meets Django's CSRF check first (issue #175)."""

    def setUp(self) -> None:
        super().setUp()
        self.client = Client(enforce_csrf_checks=True)

    def _token_from_the_gate_page(self) -> str:
        """The token the gate form carries, as a browser would send it back."""
        self.client.get(GATED_PAGE)
        return self.client.cookies[settings.CSRF_COOKIE_NAME].value

    def test_the_right_passphrase_without_a_token_is_refused_and_opens_nothing(self):
        response = self.client.post(GATE_PATH, {"passphrase": DEV_GATE_PASSPHRASE})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.client.session.get(GATE_SESSION_FLAG, False))
        self.assertContains(
            self.client.get(GATED_PAGE), 'name="passphrase"', msg_prefix="still gated"
        )

    def test_a_refused_post_is_not_a_guess(self):
        """Its passphrase was never read, so it spends nothing: a hostile page
        posting ten tokenless forms cannot lock the visitor's address out."""
        for _attempt in range(GateRateLimitMiddleware.MAX_ATTEMPTS + 1):
            response = self.client.post(GATE_PATH, {"passphrase": WRONG_PASSPHRASE})
            self.assertEqual(response.status_code, 403)
        self.assertEqual(current_count(GATE_COUNTER_KEY), 0)

    def test_a_wrong_token_is_refused_before_the_passphrase_is_read(self):
        self._token_from_the_gate_page()
        response = self.client.post(
            GATE_PATH, {"passphrase": DEV_GATE_PASSPHRASE, CSRF_FIELD: "forged"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.client.session.get(GATE_SESSION_FLAG, False))

    def test_the_form_as_rendered_still_opens_the_gate(self):
        """The browser's own round trip: the cookie and the token the gate
        page sets go back with the passphrase, and the gate opens."""
        token = self._token_from_the_gate_page()
        response = self.client.post(
            GATE_PATH, {"passphrase": DEV_GATE_PASSPHRASE, CSRF_FIELD: token}
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.client.session.get(GATE_SESSION_FLAG, False))

    def test_a_wrong_passphrase_with_a_token_is_still_a_counted_guess(self):
        token = self._token_from_the_gate_page()
        response = self.client.post(
            GATE_PATH, {"passphrase": WRONG_PASSPHRASE, CSRF_FIELD: token}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The doors remain shut.")
        self.assertEqual(current_count(GATE_COUNTER_KEY), 1)


class GateLimiterCountsOnlyTheGateTest(TestCase):
    """A passphrase field posted anywhere but the gate is no guess (#175)."""

    def test_a_passphrase_posted_elsewhere_charges_nothing(self):
        for path in NOT_THE_GATE:
            with self.subTest(path=path):
                for _attempt in range(GateRateLimitMiddleware.MAX_ATTEMPTS + 1):
                    self.client.post(path, {"passphrase": WRONG_PASSPHRASE})
                self.assertEqual(current_count(GATE_COUNTER_KEY), 0)

    def test_the_gate_still_opens_after_a_flood_of_passphrases_elsewhere(self):
        """The #175 lockout: ten passphrases posted to a public page used to
        answer the right passphrase at the gate with a 429."""
        for _attempt in range(GateRateLimitMiddleware.MAX_ATTEMPTS):
            self.client.post(NOT_THE_GATE[0], {"passphrase": WRONG_PASSPHRASE})
        response = self.client.post(GATE_PATH, {"passphrase": DEV_GATE_PASSPHRASE})
        self.assertEqual(response.status_code, 302)

    def test_the_form_posts_to_the_path_the_limiter_counts(self):
        response = self.client.get(GATED_PAGE)
        self.assertContains(response, f'action="{GATE_PATH}"')


class GatePagesCarryFrameOptionsTest(TestCase):
    """Every response the gate answers itself has the header (issue #176)."""

    def _assert_denies_framing(self, response, what: str) -> None:
        self.assertEqual(
            response.headers.get(FRAME_OPTIONS_HEADER),
            settings.X_FRAME_OPTIONS,
            msg=f"{what} answered {response.status_code} without the header",
        )

    def test_the_gate_form(self):
        self._assert_denies_framing(self.client.get(GATED_PAGE), "the gate form")

    def test_the_refusal_of_a_wrong_passphrase(self):
        response = self.client.post(GATE_PATH, {"passphrase": WRONG_PASSPHRASE})
        self.assertEqual(response.status_code, 200)
        self._assert_denies_framing(response, "the wrong-passphrase page")

    def test_the_lockout(self):
        for _attempt in range(GateRateLimitMiddleware.MAX_ATTEMPTS):
            self.client.post(GATE_PATH, {"passphrase": WRONG_PASSPHRASE})
        response = self.client.post(GATE_PATH, {"passphrase": WRONG_PASSPHRASE})
        self.assertEqual(response.status_code, 429)
        self._assert_denies_framing(response, "the 429")

    def test_the_csrf_refusal(self):
        response = Client(enforce_csrf_checks=True).post(
            GATE_PATH, {"passphrase": DEV_GATE_PASSPHRASE}
        )
        self.assertEqual(response.status_code, 403)
        self._assert_denies_framing(response, "the CSRF 403")

    def test_a_view_still_has_it(self):
        """The move must not have cost the pages that already had the header."""
        self._assert_denies_framing(self.client.get("/"), "the front door")


class GateMiddlewareOrderTest(SimpleTestCase):
    """The order the gate's parts must keep, and the refusal when they don't."""

    #: The gate's parts, outermost first, as the settings must list them.
    IN_ORDER = (
        CSP_MIDDLEWARE,
        FRAME_OPTIONS_MIDDLEWARE,
        GATE_LIMITER_MIDDLEWARE,
        GATE_MIDDLEWARE,
    )

    def test_the_settings_keep_the_order(self):
        require_gate_middleware_order(settings.MIDDLEWARE)
        positions = [settings.MIDDLEWARE.index(name) for name in self.IN_ORDER]
        self.assertEqual(positions, sorted(positions))

    def test_a_header_middleware_inside_the_gate_is_refused(self):
        """Either header middleware listed after the gate (issues #176, #178)."""
        for inside in (FRAME_OPTIONS_MIDDLEWARE, CSP_MIDDLEWARE):
            with self.subTest(inside=inside):
                disordered = [name for name in self.IN_ORDER if name != inside]
                disordered.append(inside)
                with self.assertRaises(ImproperlyConfigured) as refusal:
                    require_gate_middleware_order(disordered)
                self.assertIn("#176", str(refusal.exception))
                self.assertIn("#178", str(refusal.exception))

    def test_the_limiter_behind_the_gate_is_refused(self):
        with self.assertRaises(ImproperlyConfigured):
            require_gate_middleware_order(
                [
                    CSP_MIDDLEWARE,
                    FRAME_OPTIONS_MIDDLEWARE,
                    GATE_MIDDLEWARE,
                    GATE_LIMITER_MIDDLEWARE,
                ]
            )

    def test_a_missing_part_is_refused_and_named(self):
        with self.assertRaises(ImproperlyConfigured) as refusal:
            require_gate_middleware_order(
                [CSP_MIDDLEWARE, FRAME_OPTIONS_MIDDLEWARE, GATE_MIDDLEWARE]
            )
        self.assertIn(GATE_LIMITER_MIDDLEWARE, str(refusal.exception))
