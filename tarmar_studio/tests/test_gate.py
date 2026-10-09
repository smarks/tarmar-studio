"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

from django.test import TestCase

from common.gate import DEV_GATE_PASSPHRASE


class PassphraseGateTest(TestCase):
    """Tests for the passphrase gate middleware."""

    def test_gate_renders_for_unauthenticated(self):
        """Unauthenticated requests see the gate page.

        Asked of ``/tarmar/`` rather than ``/``: since issue #305 the root
        is a public redirect (on the classic-arena host it is the arena's
        own front door), so the gate a signed-out visitor meets is the one
        on the destination.
        """
        response = self.client.get("/tarmar/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tarmar Studio")
        self.assertContains(response, "Speak, friend, and enter.")

    def test_correct_passphrase_authenticates(self):
        """Correct passphrase grants access."""
        response = self.client.post("/gate/", {"passphrase": DEV_GATE_PASSPHRASE})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")
        # Follow redirect — should see actual home page, not gate
        response = self.client.get("/", follow=True)
        self.assertNotContains(response, "Speak, friend, and enter.")

    def test_wrong_passphrase_shows_error(self):
        """Wrong passphrase shows error and stays on gate."""
        response = self.client.post("/gate/", {"passphrase": "gandalf"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The doors remain shut.")

    def test_passphrase_case_insensitive(self):
        """Passphrase is case-insensitive."""
        response = self.client.post("/gate/", {"passphrase": "Mellon"})
        self.assertEqual(response.status_code, 302)

    def test_session_persists(self):
        """Once authenticated, session persists across requests."""
        self.client.post("/gate/", {"passphrase": DEV_GATE_PASSPHRASE})
        response = self.client.get("/", follow=True)
        self.assertNotContains(response, "Speak, friend, and enter.")

    def test_health_check_exempt(self):
        """Health check endpoint bypasses the gate."""
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_gate_blocks_all_paths(self):
        """Gate blocks all non-exempt paths when unauthenticated.

        The sign-in door left this list with issue #305 — see
        ``common.tests.test_public_paths`` for the split it moved to.
        """
        for path in ["/admin/login/", "/tarmar/characters/", "/tarmar/about/"]:
            response = self.client.get(path)
            self.assertContains(
                response, "Speak, friend, and enter.", msg_prefix=f"Path {path}"
            )

    def test_empty_passphrase_rejected(self):
        """Empty passphrase is rejected."""
        response = self.client.post("/gate/", {"passphrase": ""})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The doors remain shut.")
