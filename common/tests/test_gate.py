"""Tests for the passphrase-gate correctness coupling (issue #138).

speak_friend.PassphraseMiddleware hashes GATE_PASSPHRASE exactly as
configured but strips+lowercases every login attempt before hashing it for
comparison (see common/gate.py for the full explanation). These tests cover:

- normalize_passphrase() itself.
- A GATE_PASSPHRASE that originated as mixed case -- normalized the way
  tarmar_studio/settings.py normalizes it at import time -- authenticates
  end-to-end through the real speak_friend middleware, for any casing of the
  attempt.
- GateRateLimitMiddleware agrees with the gate about which attempts are
  wrong, including a wrong-case-but-would-have-been-right attempt.
- The gate offers GATE_HINT once an address's wrong guesses in the window
  reach GATE_HINT_AFTER_WRONG_GUESSES, and not before (issue #55).
"""

from typing import TYPE_CHECKING

from django.test import TestCase, override_settings

from common.gate import GATE_HINT, GATE_HINT_AFTER_WRONG_GUESSES, normalize_passphrase
from common.middleware import GateRateLimitMiddleware
from common.rate_limit import GUESS_WINDOW_SECONDS, current_count, too_many_attempts

if TYPE_CHECKING:
    # The test client's response type exists only in django-stubs.
    from django.test.client import _MonkeyPatchedWSGIResponse as TestResponse


class NormalizePassphraseTest(TestCase):
    def test_strips_and_lowercases(self):
        self.assertEqual(normalize_passphrase("  MixedCase123  "), "mixedcase123")

    def test_already_normalized_value_is_unchanged(self):
        self.assertEqual(normalize_passphrase("mellon2026"), "mellon2026")


@override_settings(GATE_PASSPHRASE=normalize_passphrase("Mellon2026"))
class MixedCaseConfigAuthenticatesTest(TestCase):
    """A GATE_PASSPHRASE that started out mixed case (then normalized by
    settings.py, the way ``GATE_PASSPHRASE = normalize_passphrase(...)``
    does at import time) must still let visitors in, with any casing of the
    passphrase -- not lock out the whole site.
    """

    def test_normalized_value_authenticates(self):
        response = self.client.post("/gate/", {"passphrase": "mellon2026"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    def test_original_mixed_case_still_authenticates(self):
        """Visitors don't need to know the configured value was normalized
        -- any casing of the same word works, because the gate normalizes
        attempts too."""
        response = self.client.post("/gate/", {"passphrase": "Mellon2026"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    def test_wrong_passphrase_still_rejected(self):
        response = self.client.post("/gate/", {"passphrase": "gandalf"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The doors remain shut.")


@override_settings(GATE_PASSPHRASE=normalize_passphrase("Mellon2026"))
class GateRateLimitAgreesWithGateTest(TestCase):
    """GateRateLimitMiddleware must count exactly the attempts the gate
    itself rejects -- not more, not fewer (issue #138)."""

    def _attempts_for_test_client_ip(self):
        return current_count("rate_limit:gate:127.0.0.1")

    def test_correct_attempt_not_counted(self):
        response = self.client.post("/gate/", {"passphrase": "mellon2026"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._attempts_for_test_client_ip(), 0)

    def test_wrong_case_but_would_have_been_right_attempt_not_counted(self):
        """A submission that differs only in case from the configured value
        is exactly what the gate accepts. It must not count as a wrong
        guess, even though a naive byte-for-byte comparison against the raw
        GATE_PASSPHRASE would see it as different."""
        response = self.client.post("/gate/", {"passphrase": "MELLON2026"})
        self.assertEqual(response.status_code, 302)  # the gate accepted it
        self.assertEqual(self._attempts_for_test_client_ip(), 0)

    def test_genuinely_wrong_attempt_is_counted(self):
        response = self.client.post("/gate/", {"passphrase": "gandalf"})
        self.assertEqual(response.status_code, 200)  # the gate rejected it
        self.assertEqual(self._attempts_for_test_client_ip(), 1)


class GateHintTest(TestCase):
    """The gate hints once a visitor has guessed wrong enough times.

    (Issue #55; the evidence is beside ``GATE_HINT`` in ``common/gate.py``.)
    The hint rides on the response to a wrong POST once the address's count
    in the window reaches ``GATE_HINT_AFTER_WRONG_GUESSES``, and on the 429
    once the guessing budget is spent. Every assertion here is against the
    constants, never the hint's text: the wording is Spencer's to change and
    changed once while this was being written.

    The counter is a ``RateLimitCounter`` row, which ``TestCase`` rolls back
    with everything else, so each test starts from zero wrong guesses.
    """

    GATE_URL = "/gate/"
    GATED_PAGE = "/tarmar/"
    WRONG_PASSPHRASE = "gandalf"
    REFUSAL = "The doors remain shut."
    LOCKOUT_HEADLINE = "The door has stopped answering."
    HINT_MARKUP = 'class="hint"'

    def _guess_wrong(self) -> "TestResponse":
        return self.client.post(self.GATE_URL, {"passphrase": self.WRONG_PASSPHRASE})

    def _guess_wrong_times(self, count: int) -> None:
        """Spend ``count`` wrong guesses, so the next response is the one under test."""
        for attempt in range(count):
            response = self._guess_wrong()
            self.assertEqual(response.status_code, 200, msg=f"prior guess {attempt}")

    def test_hint_threshold_is_reached_before_the_lockout(self):
        """A hint that only ever appears on the 429 page helps nobody still at
        the door. The two constants are declared in different modules (see the
        import-order note beside ``GATE_HINT_AFTER_WRONG_GUESSES``), so this is
        the one place their ordering is stated in code."""
        self.assertLess(
            GATE_HINT_AFTER_WRONG_GUESSES, GateRateLimitMiddleware.MAX_ATTEMPTS
        )

    def test_first_wrong_guess_is_refused_without_a_hint(self):
        response = self._guess_wrong()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.REFUSAL)
        self.assertNotContains(response, GATE_HINT)
        self.assertNotContains(response, self.HINT_MARKUP)

    def test_guess_one_short_of_the_threshold_has_no_hint(self):
        self._guess_wrong_times(GATE_HINT_AFTER_WRONG_GUESSES - 2)
        response = self._guess_wrong()
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, GATE_HINT)
        self.assertNotContains(response, self.HINT_MARKUP)

    def test_guess_at_the_threshold_is_refused_with_the_hint(self):
        """The GATE_HINT_AFTER_WRONG_GUESSES-th wrong guess: the response that
        turned the second visitor away now carries the hint under the refusal."""
        self._guess_wrong_times(GATE_HINT_AFTER_WRONG_GUESSES - 1)
        response = self._guess_wrong()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.REFUSAL)
        self.assertContains(response, GATE_HINT)

    def test_guess_past_the_threshold_keeps_the_hint(self):
        self._guess_wrong_times(GATE_HINT_AFTER_WRONG_GUESSES)
        response = self._guess_wrong()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, GATE_HINT)

    def test_last_guess_of_the_budget_is_still_the_gate_with_the_hint(self):
        """The limiter checks the count before charging, so the guess that
        spends the budget is still answered by the gate; only the one after
        it meets the 429."""
        self._guess_wrong_times(GateRateLimitMiddleware.MAX_ATTEMPTS - 1)
        response = self._guess_wrong()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.REFUSAL)
        self.assertContains(response, GATE_HINT)

    def test_lockout_carries_the_hint(self):
        self._guess_wrong_times(GateRateLimitMiddleware.MAX_ATTEMPTS)
        response = self._guess_wrong()
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, self.LOCKOUT_HEADLINE, status_code=429)
        self.assertContains(response, GATE_HINT, status_code=429)

    def test_reloading_the_gate_shows_no_hint(self):
        """The hint rides only on the response to a wrong POST. A GET of a gated
        page reads no counter, so it draws the gate with no hint and no hint
        markup to hide one in."""
        self._guess_wrong_times(GATE_HINT_AFTER_WRONG_GUESSES)
        response = self.client.get(self.GATED_PAGE)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="passphrase"')
        self.assertNotContains(response, GATE_HINT)
        self.assertNotContains(response, self.HINT_MARKUP)

    def test_a_lockout_with_no_hint_draws_none(self):
        """The account limiters (login, password reset, email change) answer
        through the same ``too_many_attempts`` and pass no hint; their 429 must
        not carry the gate's, nor an empty hint paragraph."""
        response = too_many_attempts("x", window_seconds=GUESS_WINDOW_SECONDS)
        self.assertEqual(response.status_code, 429)
        self.assertNotContains(response, GATE_HINT, status_code=429)
        self.assertNotContains(response, self.HINT_MARKUP, status_code=429)
