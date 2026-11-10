"""The Content-Security-Policy every response carries (issues #177, #178).

The policy is ``settings.CONTENT_SECURITY_POLICY``, sent by Django's own
``ContentSecurityPolicyMiddleware`` as ``SECURE_CSP`` (enforced) or, with
``CSP_ENFORCED=false``, as ``SECURE_CSP_REPORT_ONLY``. These tests hold the
directives the review asked for (#178) in place, pin that ``img-src`` names no
outside host (what closes #177's image leak), that ``script-src`` admits no
inline script except by nonce, and that the gate's own pages carry the header
as the views' pages do (the #176 lesson, applied to this header).
"""

import re

from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase

# django-stubs has no stub for Django 6.0's csp module yet.
from django.utils.csp import CSP  # type: ignore[reportMissingImports]

from common.gate import DEV_GATE_PASSPHRASE, GATE_PATH
from common.middleware import GateRateLimitMiddleware
from common.test_helpers import BaseTestCase, mark_gate_passed

ENFORCED_HEADER = str(CSP.HEADER_ENFORCE)
REPORT_ONLY_HEADER = str(CSP.HEADER_REPORT_ONLY)
GATED_PAGE = "/tarmar/"
PUBLIC_PAGE = "/tarmar/accounts/login/"
WRONG_PASSPHRASE = "gandalf"
#: A source that names another host; none of these may appear anywhere.
OUTSIDE_HOST = re.compile(r"(?:https?:)?//")
NONCE_IN_HEADER = re.compile(r"'nonce-([A-Za-z0-9+/=_-]+)'")
NONCE_IN_SCRIPT_TAG = re.compile(r'<script nonce="([^"]+)"')


def directives(header: str) -> dict[str, list[str]]:
    """The header's directives, each to its sources."""
    parsed: dict[str, list[str]] = {}
    for clause in header.split(";"):
        name, _, sources = clause.strip().partition(" ")
        parsed[name] = sources.split()
    return parsed


class PolicyDirectivesTest(SimpleTestCase):
    """What the configured policy says, read as the browser will."""

    def setUp(self) -> None:
        super().setUp()
        self.policy = settings.CONTENT_SECURITY_POLICY

    def test_the_review_s_directives_are_set(self):
        self.assertEqual(self.policy["frame-ancestors"], [CSP.NONE])
        self.assertEqual(self.policy["object-src"], [CSP.NONE])
        self.assertEqual(self.policy["base-uri"], [CSP.SELF])
        self.assertEqual(self.policy["form-action"], [CSP.SELF])
        self.assertEqual(self.policy["default-src"], [CSP.SELF])

    def test_scripts_run_from_this_site_or_by_nonce_only(self):
        self.assertEqual(self.policy["script-src"], [CSP.SELF, CSP.NONCE])
        self.assertNotIn(CSP.UNSAFE_INLINE, self.policy["script-src"])
        self.assertNotIn(CSP.UNSAFE_EVAL, self.policy["script-src"])

    def test_images_fonts_and_fetches_come_from_this_site_only(self):
        """``img-src 'self'`` is what stops a model- or player-written image
        from reporting a reader's address to another host (issue #177)."""
        for directive in ("img-src", "font-src", "connect-src"):
            with self.subTest(directive=directive):
                self.assertEqual(self.policy[directive], [CSP.SELF])

    def test_no_directive_names_an_outside_host(self):
        for directive, sources in self.policy.items():
            for source in sources:
                with self.subTest(directive=directive, source=source):
                    self.assertIsNone(OUTSIDE_HOST.search(str(source)))

    def test_styles_still_allow_inline_attributes_for_now(self):
        """Documented in settings.py: ``style="..."`` attributes cannot carry a
        nonce, and the templates and scripts still write some thirty of them.
        This pins the exception so it is lifted on purpose, not by accident."""
        self.assertEqual(self.policy["style-src"], [CSP.SELF, CSP.UNSAFE_INLINE])

    def test_enforced_by_default_and_the_switch_moves_the_whole_policy(self):
        self.assertTrue(settings.CSP_ENFORCED)
        self.assertIs(settings.SECURE_CSP, self.policy)
        self.assertIsNone(settings.SECURE_CSP_REPORT_ONLY)


class PolicyHeaderTest(TestCase):
    """The header on the responses a visitor meets, inside and outside the gate."""

    def _assert_enforced_header(self, response, what: str) -> dict[str, list[str]]:
        header = response.headers.get(ENFORCED_HEADER)
        self.assertIsNotNone(
            header, f"{what} answered {response.status_code} without it"
        )
        assert header is not None
        self.assertNotIn(REPORT_ONLY_HEADER, response.headers)
        parsed = directives(header)
        self.assertEqual(parsed["frame-ancestors"], ["'none'"])
        self.assertEqual(parsed["img-src"], ["'self'"])
        return parsed

    def test_a_view_s_page(self):
        self._assert_enforced_header(self.client.get(PUBLIC_PAGE), "the sign-in page")

    def test_the_gate_form_the_refusal_and_the_lockout(self):
        """The gate answers these itself; the header middleware stands outside
        it so they carry the header too (the order check in common.middleware)."""
        self._assert_enforced_header(self.client.get(GATED_PAGE), "the gate form")
        refused = self.client.post(GATE_PATH, {"passphrase": WRONG_PASSPHRASE})
        self._assert_enforced_header(refused, "the wrong-passphrase page")
        for _attempt in range(GateRateLimitMiddleware.MAX_ATTEMPTS):
            self.client.post(GATE_PATH, {"passphrase": WRONG_PASSPHRASE})
        locked = self.client.post(GATE_PATH, {"passphrase": WRONG_PASSPHRASE})
        self.assertEqual(locked.status_code, 429)
        self._assert_enforced_header(locked, "the 429")

    def test_the_csrf_refusal(self):
        refused = Client(enforce_csrf_checks=True).post(
            GATE_PATH, {"passphrase": DEV_GATE_PASSPHRASE}
        )
        self.assertEqual(refused.status_code, 403)
        self._assert_enforced_header(refused, "the CSRF 403")

    def test_a_page_with_no_inline_script_sends_no_nonce(self):
        """The nonce is minted only when a template asks for it; the gate page
        has no inline script, so its script-src is 'self' alone."""
        parsed = self._assert_enforced_header(
            self.client.get(GATED_PAGE), "the gate form"
        )
        self.assertEqual(parsed["script-src"], ["'self'"])


class NonceTest(BaseTestCase):
    """The nonce in the header is the one on the page's inline scripts."""

    def test_base_html_s_menu_script_carries_the_header_s_nonce(self):
        mark_gate_passed(self.client)
        response = self.client.get(GATED_PAGE)
        self.assertEqual(response.status_code, 200)
        header = response.headers[ENFORCED_HEADER]
        in_header = NONCE_IN_HEADER.search(header)
        self.assertIsNotNone(in_header, f"no nonce in {header}")
        assert in_header is not None
        on_page = set(NONCE_IN_SCRIPT_TAG.findall(response.content.decode()))
        self.assertEqual(on_page, {in_header.group(1)})

    def test_the_404_page_s_script_carries_the_nonce(self):
        """The error handler renders through the request, so the stars script
        runs under the policy rather than being refused."""
        self.client.post(GATE_PATH, {"passphrase": DEV_GATE_PASSPHRASE})
        response = self.client.get("/tarmar/no-such-page/")
        self.assertEqual(response.status_code, 404)
        in_header = NONCE_IN_HEADER.search(response.headers[ENFORCED_HEADER])
        assert in_header is not None
        self.assertIn(
            f'<script nonce="{in_header.group(1)}">', response.content.decode()
        )

    def test_every_nonce_is_fresh(self):
        first = NONCE_IN_HEADER.search(
            self.client.get(PUBLIC_PAGE).headers[ENFORCED_HEADER]
        )
        second = NONCE_IN_HEADER.search(
            self.client.get(PUBLIC_PAGE).headers[ENFORCED_HEADER]
        )
        assert first is not None and second is not None
        self.assertNotEqual(first.group(1), second.group(1))
