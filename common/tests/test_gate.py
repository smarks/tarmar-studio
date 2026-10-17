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
- The gate offers one of GATE_HINTS, drawn at random per response (issue
  #77), once an address's wrong guesses in the window reach
  GATE_HINT_AFTER_WRONG_GUESSES, and not before (issue #55).
"""

import html
import re
from typing import TYPE_CHECKING
from unittest.mock import patch

from django.http import HttpResponse
from django.test import TestCase, override_settings

from common.gate import (
    GATE_HINT_AFTER_WRONG_GUESSES,
    GATE_HINTS,
    choose_gate_hint,
    normalize_passphrase,
)
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

    (Issue #55; the evidence is beside ``GATE_HINTS`` in ``common/gate.py``.)
    A hint rides on the response to a wrong POST once the address's count
    in the window reaches ``GATE_HINT_AFTER_WRONG_GUESSES``, and on the 429
    once the guessing budget is spent. Which hint is drawn at random from
    ``GATE_HINTS`` for every response (issue #77), so every assertion here is
    against the tuple, never a literal: the wording is Spencer's to change
    and has changed twice while these tests have existed.

    The counter is a ``RateLimitCounter`` row, which ``TestCase`` rolls back
    with everything else, so each test starts from zero wrong guesses.
    """

    GATE_URL = "/gate/"
    GATED_PAGE = "/tarmar/"
    WRONG_PASSPHRASE = "gandalf"
    REFUSAL = "The doors remain shut."
    LOCKOUT_HEADLINE = "The door has stopped answering."
    HINT_MARKUP = 'class="hint"'
    #: The one paragraph both gate.html and 429.html draw a hint in.
    HINT_PARAGRAPH = re.compile(r'<p class="hint">(.*?)</p>')
    #: Draws for the variety test below; see its docstring for the odds.
    DRAWS_TO_SEE_VARIETY = 60

    def _guess_wrong(self) -> "TestResponse":
        return self.client.post(self.GATE_URL, {"passphrase": self.WRONG_PASSPHRASE})

    def _guess_wrong_times(self, count: int) -> None:
        """Spend ``count`` wrong guesses, so the next response is the one under test."""
        for attempt in range(count):
            response = self._guess_wrong()
            self.assertEqual(response.status_code, 200, msg=f"prior guess {attempt}")

    def _shown_hint(self, response: "TestResponse | HttpResponse") -> str:
        """The hint paragraph's text, unescaped, or a failure if there is none."""
        match = self.HINT_PARAGRAPH.search(response.content.decode())
        self.assertIsNotNone(match, "no hint paragraph in the response")
        assert match is not None  # for the type checker; the assertion above holds
        return html.unescape(match.group(1))

    def _assert_no_hint(self, response: "TestResponse | HttpResponse") -> None:
        self.assertNotContains(
            response, self.HINT_MARKUP, status_code=response.status_code
        )
        for hint in GATE_HINTS:
            self.assertNotContains(response, hint, status_code=response.status_code)

    def test_hint_threshold_is_reached_before_the_lockout(self):
        """A hint that only ever appears on the 429 page helps nobody still at
        the door. The two constants are declared in different modules (see the
        import-order note beside ``GATE_HINT_AFTER_WRONG_GUESSES``), so this is
        the one place their ordering is stated in code."""
        self.assertLess(
            GATE_HINT_AFTER_WRONG_GUESSES, GateRateLimitMiddleware.MAX_ATTEMPTS
        )

    def test_every_hint_is_usable_as_written(self):
        """Each hint is rendered through Django's autoescape and compared with
        its literal here and in the browser spec, so none may carry an
        apostrophe or a quotation mark (the constraint stated beside
        ``GATE_HINTS``); and a blank or repeated hint would be a draw that
        tells the visitor nothing new (issue #77)."""
        self.assertEqual(len(set(GATE_HINTS)), len(GATE_HINTS))
        for hint in GATE_HINTS:
            with self.subTest(hint=hint):
                self.assertTrue(hint.strip())
                self.assertNotIn("'", hint)
                self.assertNotIn('"', hint)

    def test_chooser_draws_from_the_hints(self):
        self.assertIn(choose_gate_hint(), GATE_HINTS)

    def test_first_wrong_guess_is_refused_without_a_hint(self):
        response = self._guess_wrong()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.REFUSAL)
        self._assert_no_hint(response)

    def test_guess_one_short_of_the_threshold_has_no_hint(self):
        self._guess_wrong_times(GATE_HINT_AFTER_WRONG_GUESSES - 2)
        response = self._guess_wrong()
        self.assertEqual(response.status_code, 200)
        self._assert_no_hint(response)

    def test_guess_at_the_threshold_is_refused_with_a_hint(self):
        """The GATE_HINT_AFTER_WRONG_GUESSES-th wrong guess: the response that
        turned the second visitor away now carries a hint under the refusal."""
        self._guess_wrong_times(GATE_HINT_AFTER_WRONG_GUESSES - 1)
        response = self._guess_wrong()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.REFUSAL)
        self.assertIn(self._shown_hint(response), GATE_HINTS)

    def test_guess_past_the_threshold_keeps_a_hint(self):
        self._guess_wrong_times(GATE_HINT_AFTER_WRONG_GUESSES)
        response = self._guess_wrong()
        self.assertEqual(response.status_code, 200)
        self.assertIn(self._shown_hint(response), GATE_HINTS)

    def test_the_gate_shows_the_hint_the_chooser_drew(self):
        """The middleware must take its hint from ``choose_gate_hint()``, not
        from a fixed string, and draw once per response from the whole tuple
        (issue #77). ``common.gate.random`` is the stdlib module itself, so
        the patch replaces ``random.choice`` globally for the block, not only
        for ``common.gate``. That reach was checked: nothing else on the path
        of a POST to the gate draws from ``random`` (the project's other users,
        ``common/rolling.py`` and the battle generator, are not reached)."""
        self._guess_wrong_times(GATE_HINT_AFTER_WRONG_GUESSES - 1)
        with patch(
            "common.gate.random.choice", return_value=GATE_HINTS[-1]
        ) as mocked_choice:
            response = self._guess_wrong()
        mocked_choice.assert_called_once_with(GATE_HINTS)
        self.assertEqual(self._shown_hint(response), GATE_HINTS[-1])

    def test_the_lockout_shows_the_hint_the_chooser_drew(self):
        """The 429 path draws the same way; the patch's reach is as noted on
        the gate test above."""
        self._guess_wrong_times(GateRateLimitMiddleware.MAX_ATTEMPTS)
        with patch(
            "common.gate.random.choice", return_value=GATE_HINTS[0]
        ) as mocked_choice:
            response = self._guess_wrong()
        mocked_choice.assert_called_once_with(GATE_HINTS)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self._shown_hint(response), GATE_HINTS[0])

    def test_repeated_guessing_is_shown_more_than_one_hint(self):
        """The real chooser, not a mock, across many responses: the gate's
        refusals up to the budget and the 429s after it both draw, and the
        hints seen must not all be the same one (issue #77). The odds of a
        false failure are len(GATE_HINTS) * (1 / len(GATE_HINTS)) **
        DRAWS_TO_SEE_VARIETY, negligible for any tuple of two or more: this
        test is not flaky, and a failure means the draw is broken. The mocked
        tests above pin the wiring; this one pins that the wiring is fed by a
        draw."""
        self.assertGreater(
            len(GATE_HINTS), 1, "issue #77 asks for a selection of hints"
        )
        self._guess_wrong_times(GATE_HINT_AFTER_WRONG_GUESSES - 1)
        hints_seen = set()
        for _draw in range(self.DRAWS_TO_SEE_VARIETY):
            response = self._guess_wrong()
            self.assertIn(response.status_code, (200, 429))
            hints_seen.add(self._shown_hint(response))
        self.assertGreater(len(hints_seen), 1)
        self.assertTrue(hints_seen <= set(GATE_HINTS))

    def test_last_guess_of_the_budget_is_still_the_gate_with_a_hint(self):
        """The limiter checks the count before charging, so the guess that
        spends the budget is still answered by the gate; only the one after
        it meets the 429."""
        self._guess_wrong_times(GateRateLimitMiddleware.MAX_ATTEMPTS - 1)
        response = self._guess_wrong()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.REFUSAL)
        self.assertIn(self._shown_hint(response), GATE_HINTS)

    def test_lockout_carries_a_hint(self):
        self._guess_wrong_times(GateRateLimitMiddleware.MAX_ATTEMPTS)
        response = self._guess_wrong()
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, self.LOCKOUT_HEADLINE, status_code=429)
        self.assertIn(self._shown_hint(response), GATE_HINTS)

    def test_reloading_the_gate_shows_no_hint(self):
        """The hint rides only on the response to a wrong POST. A GET of a gated
        page reads no counter, so it draws the gate with no hint and no hint
        markup to hide one in."""
        self._guess_wrong_times(GATE_HINT_AFTER_WRONG_GUESSES)
        response = self.client.get(self.GATED_PAGE)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="passphrase"')
        self._assert_no_hint(response)

    def test_a_lockout_with_no_hint_draws_none(self):
        """The account limiters (login, password reset, email change) answer
        through the same ``too_many_attempts`` and pass no hint; their 429 must
        not carry the gate's, nor an empty hint paragraph."""
        response = too_many_attempts("x", window_seconds=GUESS_WINDOW_SECONDS)
        self.assertEqual(response.status_code, 429)
        self._assert_no_hint(response)
