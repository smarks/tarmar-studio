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
- The gate offers one of settings.GATE_HINTS, drawn at random per response
  (issue #77), once an address's wrong guesses in the window reach
  settings.GATE_HINT_AFTER_WRONG_GUESSES, and not before (issue #55).
- load_gate_hints() reads the hints file the settings name, and the shipped
  config/gate-hints.txt gives the answer away to nobody (issue #77).
"""

import html
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils.html import escape

from common.gate import (
    DEV_GATE_PASSPHRASE,
    choose_gate_hint,
    load_gate_hints,
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


#: Hints for the tests below, standing in for config/gate-hints.txt so the
#: tests do not depend on what the file says (issue #77). One carries an
#: apostrophe, which Django autoescapes on the way out.
TEST_GATE_HINTS = (
    "Read the door again. It's in Elvish.",
    "Gandalf got this one on a bad night.",
    "The hobbit asked the right question.",
)
TEST_HINT_THRESHOLD = 3


@override_settings(
    GATE_HINTS=TEST_GATE_HINTS, GATE_HINT_AFTER_WRONG_GUESSES=TEST_HINT_THRESHOLD
)
class GateHintTest(TestCase):
    """The gate hints once a visitor has guessed wrong enough times.

    (Issue #55; the evidence is in ``common/gate.py``.) A hint rides on the
    response to a wrong POST once the address's count in the window reaches
    ``settings.GATE_HINT_AFTER_WRONG_GUESSES``, and on the 429 once the
    guessing budget is spent. Which hint is drawn at random from
    ``settings.GATE_HINTS`` for every response (issue #77). Both settings are
    overridden here with fixed values, and every assertion reads them back
    through ``settings``, so the tests pin that the middleware reads its
    configuration and never what the shipped file happens to say.

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
        """Neither the hint markup nor any configured hint, as autoescaped, is on the page."""
        self.assertNotContains(
            response, self.HINT_MARKUP, status_code=response.status_code
        )
        for hint in settings.GATE_HINTS:
            self.assertNotContains(
                response, escape(hint), status_code=response.status_code
            )

    def test_chooser_draws_from_the_configured_hints(self):
        self.assertIn(choose_gate_hint(), settings.GATE_HINTS)

    def test_first_wrong_guess_is_refused_without_a_hint(self):
        response = self._guess_wrong()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.REFUSAL)
        self._assert_no_hint(response)

    def test_guess_one_short_of_the_threshold_has_no_hint(self):
        self._guess_wrong_times(settings.GATE_HINT_AFTER_WRONG_GUESSES - 2)
        response = self._guess_wrong()
        self.assertEqual(response.status_code, 200)
        self._assert_no_hint(response)

    def test_guess_at_the_threshold_is_refused_with_a_hint(self):
        """The GATE_HINT_AFTER_WRONG_GUESSES-th wrong guess: the response that
        turned the second visitor away now carries a hint under the refusal."""
        self._guess_wrong_times(settings.GATE_HINT_AFTER_WRONG_GUESSES - 1)
        response = self._guess_wrong()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.REFUSAL)
        self.assertIn(self._shown_hint(response), settings.GATE_HINTS)

    def test_guess_past_the_threshold_keeps_a_hint(self):
        self._guess_wrong_times(settings.GATE_HINT_AFTER_WRONG_GUESSES)
        response = self._guess_wrong()
        self.assertEqual(response.status_code, 200)
        self.assertIn(self._shown_hint(response), settings.GATE_HINTS)

    def test_the_gate_shows_the_hint_the_chooser_drew(self):
        """The middleware must take its hint from ``choose_gate_hint()``, not
        from a fixed string, and draw once per response from the whole
        configured tuple (issue #77). ``common.gate.random`` is the stdlib
        module itself, so the patch replaces ``random.choice`` globally for the
        block, not only for ``common.gate``. That reach was checked: nothing
        else on the path of a POST to the gate draws from ``random`` (the
        project's other users, ``common/rolling.py`` and the battle generator,
        are not reached). The hint drawn here is the one with the apostrophe,
        so this also pins that autoescape and ``html.unescape`` round-trip."""
        self._guess_wrong_times(settings.GATE_HINT_AFTER_WRONG_GUESSES - 1)
        with patch(
            "common.gate.random.choice", return_value=settings.GATE_HINTS[0]
        ) as mocked_choice:
            response = self._guess_wrong()
        mocked_choice.assert_called_once_with(settings.GATE_HINTS)
        self.assertEqual(self._shown_hint(response), settings.GATE_HINTS[0])
        self.assertContains(response, escape(settings.GATE_HINTS[0]))

    def test_the_lockout_shows_the_hint_the_chooser_drew(self):
        """The 429 path draws the same way; the patch's reach is as noted on
        the gate test above."""
        self._guess_wrong_times(GateRateLimitMiddleware.MAX_ATTEMPTS)
        with patch(
            "common.gate.random.choice", return_value=settings.GATE_HINTS[-1]
        ) as mocked_choice:
            response = self._guess_wrong()
        mocked_choice.assert_called_once_with(settings.GATE_HINTS)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self._shown_hint(response), settings.GATE_HINTS[-1])

    def test_repeated_guessing_is_shown_more_than_one_hint(self):
        """The real chooser, not a mock, across many responses: the gate's
        refusals up to the budget and the 429s after it both draw, and the
        hints seen must not all be the same one (issue #77). The odds of a
        false failure are len(settings.GATE_HINTS) * (1 / len(settings.GATE_HINTS))
        ** DRAWS_TO_SEE_VARIETY, negligible for any tuple of two or more: this
        test is not flaky, and a failure means the draw is broken. The mocked
        tests above pin the wiring; this one pins that the wiring is fed by a
        draw."""
        self.assertGreater(
            len(settings.GATE_HINTS), 1, "issue #77 asks for a selection of hints"
        )
        self._guess_wrong_times(settings.GATE_HINT_AFTER_WRONG_GUESSES - 1)
        hints_seen = set()
        for _draw in range(self.DRAWS_TO_SEE_VARIETY):
            response = self._guess_wrong()
            self.assertIn(response.status_code, (200, 429))
            hints_seen.add(self._shown_hint(response))
        self.assertGreater(len(hints_seen), 1)
        self.assertTrue(hints_seen <= set(settings.GATE_HINTS))

    def test_last_guess_of_the_budget_is_still_the_gate_with_a_hint(self):
        """The limiter checks the count before charging, so the guess that
        spends the budget is still answered by the gate; only the one after
        it meets the 429."""
        self._guess_wrong_times(GateRateLimitMiddleware.MAX_ATTEMPTS - 1)
        response = self._guess_wrong()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.REFUSAL)
        self.assertIn(self._shown_hint(response), settings.GATE_HINTS)

    def test_lockout_carries_a_hint(self):
        self._guess_wrong_times(GateRateLimitMiddleware.MAX_ATTEMPTS)
        response = self._guess_wrong()
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, self.LOCKOUT_HEADLINE, status_code=429)
        self.assertIn(self._shown_hint(response), settings.GATE_HINTS)

    def test_reloading_the_gate_shows_no_hint(self):
        """The hint rides only on the response to a wrong POST. A GET of a gated
        page reads no counter, so it draws the gate with no hint and no hint
        markup to hide one in."""
        self._guess_wrong_times(settings.GATE_HINT_AFTER_WRONG_GUESSES)
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


class LoadGateHintsTest(SimpleTestCase):
    """``load_gate_hints()`` and the configuration as shipped (issue #77).

    The hints are a text file Spencer edits with no Python, so the loader's
    reading of that file is what stands between an edit and the page: which
    lines count, what a bad file does at startup, and that the file in the
    repo gives the answer away to nobody.
    """

    def _scratch_directory(self) -> Path:
        """An empty temporary directory, removed when the test ends."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    def _hints_file(self, content: str) -> Path:
        """A temporary hints file holding ``content``, removed when the test ends."""
        path = self._scratch_directory() / "gate-hints.txt"
        path.write_text(content, encoding="utf-8")
        return path

    def test_comments_and_blank_lines_are_ignored_and_order_is_kept(self):
        """A ``#`` only comments at the start of a line; inside a hint it is
        content, as an apostrophe is. Surrounding whitespace is dropped."""
        path = self._hints_file(
            "# The header the shipped file carries.\n"
            "\n"
            "  First, with a trailing space. \n"
            "Second, with an apostrophe: it's content.\n"
            "   \n"
            "Third, keeping its inner # mark.\n"
            "# A trailing comment.\n"
        )
        self.assertEqual(
            load_gate_hints(path),
            (
                "First, with a trailing space.",
                "Second, with an apostrophe: it's content.",
                "Third, keeping its inner # mark.",
            ),
        )

    def test_a_missing_file_refuses_to_start_and_names_the_path(self):
        path = self._scratch_directory() / "missing.txt"
        with self.assertRaises(ImproperlyConfigured) as refusal:
            load_gate_hints(path)
        self.assertIn(str(path), str(refusal.exception))

    def test_a_path_through_a_file_refuses_to_start_and_names_the_path(self):
        """Not every unreadable path is a missing one: a path that runs through
        a file is refused by the OS with another error, and the loader must
        turn that into the same named refusal rather than let it escape (found
        while the #77 loader tests were written). The OS's own wording is not
        asserted; only that the path is named."""
        path = self._hints_file("a hint") / "deeper.txt"
        with self.assertRaises(ImproperlyConfigured) as refusal:
            load_gate_hints(path)
        self.assertIn(str(path), str(refusal.exception))

    def test_a_file_of_only_comments_refuses_to_start(self):
        path = self._hints_file("# nothing but\n\n# comments\n")
        with self.assertRaises(ImproperlyConfigured) as refusal:
            load_gate_hints(path)
        self.assertIn(str(path), str(refusal.exception))

    def test_the_shipped_file_is_what_the_settings_hold(self):
        """``settings.GATE_HINTS`` is the loader's reading of
        ``settings.GATE_HINTS_FILE``, not a copy that could drift from it."""
        self.assertEqual(load_gate_hints(settings.GATE_HINTS_FILE), settings.GATE_HINTS)

    def test_the_shipped_hints_are_a_selection_that_keeps_the_secret(self):
        """At least two distinct, non-empty hints (issue #77 asks for a
        selection), and none of them says the word: a hint that contained the
        dev passphrase, in any casing, would open the dev gate for anyone who
        guessed wrong GATE_HINT_AFTER_WRONG_GUESSES times. The check is against
        ``DEV_GATE_PASSPHRASE``, the one value the shipped file could leak."""
        shipped_hints = load_gate_hints(settings.GATE_HINTS_FILE)
        self.assertGreaterEqual(len(shipped_hints), 2)
        self.assertEqual(len(set(shipped_hints)), len(shipped_hints))
        for hint in shipped_hints:
            with self.subTest(hint=hint):
                self.assertTrue(hint.strip())
                self.assertNotIn(DEV_GATE_PASSPHRASE.casefold(), hint.casefold())

    def test_hint_threshold_is_reached_before_the_lockout(self):
        """A hint that only ever appears on the 429 page helps nobody still at
        the door. The threshold is a setting and the budget a constant in
        ``common.rate_limit``, which ``settings.py`` cannot import while it is
        loading (its comment says so), so this is the one place the two are
        held together in code. It reads the shipped setting, not the override
        the gate tests use."""
        self.assertLess(
            settings.GATE_HINT_AFTER_WRONG_GUESSES, GateRateLimitMiddleware.MAX_ATTEMPTS
        )
