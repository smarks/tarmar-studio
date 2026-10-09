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
"""

from django.test import TestCase, override_settings

from common.gate import normalize_passphrase
from common.rate_limit import current_count


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
