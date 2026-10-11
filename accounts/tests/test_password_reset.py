"""Self-service password reset (issue #929).

The login page offered no way back from a forgotten password and the reset URL
was a 404. These tests walk every step of the flow Django's four views now
serve under ``/tarmar/accounts/password_reset/``, as a visitor who has NOT
answered the passphrase gate: the sign-in page links here, and the emailed
link is opened in whatever browser the mail client picks.

Mail goes to Django's locmem backend, which the test runner installs, so
``mail.outbox`` is what would have been sent.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import Client, TestCase, override_settings

from accounts.models import User
from accounts.views import PasswordResetView
from common.models import RateLimitCounter
from common.public_paths import is_public_path
from common.test_helpers import TEST_PASSWORD

if TYPE_CHECKING:
    # The test client's response type exists only in django-stubs.
    from django.test.client import _MonkeyPatchedWSGIResponse as TestResponse

LOGIN_URL = "/tarmar/accounts/login/"
RESET_URL = "/tarmar/accounts/password_reset/"
DONE_URL = "/tarmar/accounts/password_reset/done/"
COMPLETE_URL = "/tarmar/accounts/password_reset/complete/"
NEW_PASSWORD = "LanternBridge42x"
ACCOUNT_EMAIL = "spencer@example.test"
#: The sentence the done page shows whatever address was typed.
SENT_SENTENCE = "If an account here has that email address"
CONFIRM_LINK = re.compile(
    r"(?P<scheme>https?)://(?P<host>[^/\s]+)"
    r"(?P<path>/tarmar/accounts/password_reset/[^/\s]+/[^/\s]+/)"
)


def sent_body() -> str:
    """The body of the one message sent so far."""
    return str(mail.outbox[0].body)


def without_csrf(page: bytes) -> bytes:
    """A page with its CSRF token blanked; the token is masked afresh per render."""
    return re.sub(rb'name="csrf-token" content="[^"]*"', b"", page)


class PasswordResetTestCase(TestCase):
    """A visitor outside the gate, and one account with a confirmed address.

    Confirmed, because since issue #931 the reset mails no other kind.
    """

    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(
            username="spencer",
            password=TEST_PASSWORD,
            email=ACCOUNT_EMAIL,
            email_verified=True,
        )

    def request_reset(
        self, email: str = ACCOUNT_EMAIL, *, secure: bool = False
    ) -> TestResponse:
        """POST the reset form, following nothing."""
        return self.client.post(RESET_URL, {"email": email}, secure=secure)

    def emailed_path(self) -> str:
        """The confirm path in the one message sent so far."""
        self.assertEqual(len(mail.outbox), 1)
        match = CONFIRM_LINK.search(sent_body())
        self.assertIsNotNone(match, mail.outbox[0].body)
        assert match is not None
        return match.group("path")


class LoginPageLinkTest(TestCase):
    def test_login_page_offers_the_reset(self) -> None:
        response = Client().get(LOGIN_URL)
        self.assertContains(
            response,
            f'<a href="{RESET_URL}" id="forgot-password-link">'
            "Forgot your password?</a>",
            html=True,
        )


class ResetIsOutsideTheGateTest(TestCase):
    def test_every_step_is_a_public_path(self) -> None:
        for path in (
            RESET_URL,
            DONE_URL,
            COMPLETE_URL,
            "/tarmar/accounts/password_reset/MQ/set-password/",
        ):
            self.assertTrue(is_public_path(path), path)

    def test_the_form_renders_without_the_passphrase(self) -> None:
        response = Client().get(RESET_URL)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/password_reset_form.html")
        self.assertNotContains(response, 'name="passphrase"')


class PasswordResetFormTest(PasswordResetTestCase):
    def test_get_renders_the_email_form(self) -> None:
        response = self.client.get(RESET_URL)
        self.assertContains(response, "<h1>Reset your password</h1>", html=True)
        self.assertContains(response, 'name="email"')
        self.assertContains(response, f'href="{LOGIN_URL}"')

    def test_a_known_address_gets_one_message_with_an_absolute_link(self) -> None:
        response = self.request_reset()
        self.assertRedirects(response, DONE_URL)

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [ACCOUNT_EMAIL])
        self.assertEqual(message.from_email, settings.DEFAULT_FROM_EMAIL)
        self.assertEqual(message.subject, "Reset your password at testserver")
        self.assertIn('the account "spencer"', str(message.body))
        match = CONFIRM_LINK.search(str(message.body))
        self.assertIsNotNone(match, message.body)
        assert match is not None
        self.assertEqual(match.group("scheme"), "http")
        self.assertEqual(match.group("host"), "testserver")

    def test_the_address_is_matched_without_regard_to_case(self) -> None:
        self.request_reset(ACCOUNT_EMAIL.upper())
        self.assertEqual(len(mail.outbox), 1)

    def test_the_link_takes_the_scheme_of_the_request(self) -> None:
        # Production sits behind nginx; gunicorn marks the request secure
        # from X-Forwarded-Proto, so the link must come out https.
        self.request_reset(secure=True)
        self.assertEqual(len(mail.outbox), 1)
        match = CONFIRM_LINK.search(sent_body())
        assert match is not None
        self.assertEqual(match.group("scheme"), "https")

    def test_an_unknown_address_sends_nothing_and_says_the_same(self) -> None:
        known = self.client.get(self.request_reset()["Location"])
        mail.outbox.clear()

        response = self.request_reset("nobody@example.test")
        self.assertRedirects(response, DONE_URL)
        self.assertEqual(mail.outbox, [])
        unknown = self.client.get(response["Location"])
        self.assertEqual(without_csrf(unknown.content), without_csrf(known.content))

    def test_an_account_without_an_address_is_not_found_by_a_blank_one(
        self,
    ) -> None:
        User.objects.create_user(username="no_email", password=TEST_PASSWORD)
        response = self.request_reset("")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")
        self.assertEqual(mail.outbox, [])

    def test_a_failed_send_is_logged_and_the_page_still_says_sent(self) -> None:
        with (
            patch(
                "django.core.mail.EmailMultiAlternatives.send",
                side_effect=ConnectionRefusedError("no mail server"),
            ),
            self.assertLogs("django.contrib.auth", level="ERROR") as logs,
        ):
            response = self.request_reset()
        self.assertRedirects(response, DONE_URL)
        self.assertIn("Failed to send password reset email", logs.output[0])


class PasswordResetRateLimitTest(PasswordResetTestCase):
    def test_requests_past_the_allowance_are_refused_and_send_nothing(
        self,
    ) -> None:
        for attempt in range(PasswordResetView.RESET_MAX_REQUESTS):
            response = self.request_reset()
            self.assertEqual(response.status_code, 302, msg=f"attempt {attempt}")
        sent = len(mail.outbox)

        response = self.request_reset()
        self.assertEqual(response.status_code, 429)
        self.assertContains(
            response, "Too many password reset requests", status_code=429
        )
        self.assertEqual(len(mail.outbox), sent)

    def test_the_form_page_itself_is_never_counted(self) -> None:
        for _ in range(PasswordResetView.RESET_MAX_REQUESTS + 1):
            self.assertEqual(self.client.get(RESET_URL).status_code, 200)
        self.assertEqual(self.request_reset().status_code, 302)


class PasswordResetDoneTest(TestCase):
    def test_done_page_says_sent_only_if_the_account_exists(self) -> None:
        response = Client().get(DONE_URL)
        self.assertTemplateUsed(response, "accounts/password_reset_done.html")
        self.assertContains(response, SENT_SENTENCE)
        self.assertContains(response, f'href="{RESET_URL}"')
        self.assertContains(response, f'href="{LOGIN_URL}"')


class PasswordResetConfirmTest(PasswordResetTestCase):
    def test_the_emailed_link_sets_the_new_password(self) -> None:
        self.request_reset()
        path = self.emailed_path()

        # Django moves the token out of the URL into the session first.
        response = self.client.get(path)
        set_password_path = response["Location"]
        self.assertTrue(set_password_path.endswith("/set-password/"))

        form_page = self.client.get(set_password_path)
        self.assertTemplateUsed(form_page, "accounts/password_reset_confirm.html")
        self.assertContains(form_page, "<h1>Choose a new password</h1>", html=True)
        self.assertContains(form_page, 'name="new_password1"')

        response = self.client.post(
            set_password_path,
            {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD},
        )
        self.assertRedirects(response, COMPLETE_URL)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))
        self.assertFalse(self.user.check_password(TEST_PASSWORD))
        self.assertTrue(
            Client().login(username="spencer", password=NEW_PASSWORD),
            "the new password must sign in",
        )

    def test_the_link_works_once(self) -> None:
        self.request_reset()
        path = self.emailed_path()
        set_password_path = self.client.get(path)["Location"]
        self.client.post(
            set_password_path,
            {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD},
        )

        response = Client().get(path)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="password-reset-invalid"')

    def test_a_weak_password_is_refused_on_the_page(self) -> None:
        self.request_reset()
        set_password_path = self.client.get(self.emailed_path())["Location"]
        response = self.client.post(
            set_password_path, {"new_password1": "short", "new_password2": "short"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "too short")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(TEST_PASSWORD))

    def test_an_invalid_token_shows_the_error_state(self) -> None:
        uidb64 = self.emailed_uid()
        response = self.client.get(
            f"/tarmar/accounts/password_reset/{uidb64}/not-a-real-token/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/password_reset_confirm.html")
        self.assertContains(response, "<h1>Reset link not valid</h1>", html=True)
        self.assertContains(response, f'href="{RESET_URL}"')
        self.assertNotContains(response, 'name="new_password1"')

    def test_an_unknown_user_shows_the_error_state(self) -> None:
        token = default_token_generator.make_token(self.user)
        response = self.client.get(f"/tarmar/accounts/password_reset/OTk5/{token}/")
        self.assertContains(response, 'id="password-reset-invalid"')

    def emailed_uid(self) -> str:
        self.request_reset()
        return self.emailed_path().split("/")[-3]


class PasswordResetCompleteTest(TestCase):
    def test_complete_page_points_at_sign_in(self) -> None:
        response = Client().get(COMPLETE_URL)
        self.assertTemplateUsed(response, "accounts/password_reset_complete.html")
        self.assertContains(response, "<h1>Password changed</h1>", html=True)
        self.assertContains(response, f'href="{LOGIN_URL}"')


class PasswordResetPerTargetLimitTest(PasswordResetTestCase):
    """Many sources must not be able to fill one inbox (#929 review)."""

    LIMIT = PasswordResetView.RESET_PER_EMAIL_MAX_SENDS

    def request_from(self, network_address: str, email: str) -> TestResponse:
        """POST the form as if from ``network_address``."""
        return self.client.post(
            RESET_URL, {"email": email}, REMOTE_ADDR=network_address
        )

    def test_one_address_gets_at_most_the_limit_from_many_sources(self) -> None:
        for source in range(self.LIMIT + 2):
            response = self.request_from(f"192.0.2.{source + 1}", ACCOUNT_EMAIL)
            # Never a 429: that would say this address is being asked about.
            self.assertRedirects(response, DONE_URL)
        self.assertEqual(len(mail.outbox), self.LIMIT)

    def test_the_count_ignores_case_and_surrounding_space(self) -> None:
        for spelling in (
            "SPENCER@example.test",
            " spencer@EXAMPLE.test",
            ACCOUNT_EMAIL,
        ):
            self.request_from("192.0.2.1", spelling)
        self.request_from("192.0.2.2", ACCOUNT_EMAIL)
        self.assertEqual(len(mail.outbox), self.LIMIT)

    def test_an_unregistered_address_is_charged_the_same_way(self) -> None:
        unknown = "nobody@example.test"
        for source in range(self.LIMIT):
            self.request_from(f"192.0.2.{source + 1}", unknown)
        target_counters = RateLimitCounter.objects.filter(
            key__startswith="rate_limit:password_reset:email_"
        )
        self.assertEqual(target_counters.count(), 1)
        counter = target_counters.get()
        self.assertEqual(counter.count, self.LIMIT)
        self.assertNotIn(unknown, counter.key)

    def test_once_tripped_the_page_is_the_same_page(self) -> None:
        for source in range(self.LIMIT):
            self.request_from(f"192.0.2.{source + 1}", ACCOUNT_EMAIL)
        normal = self.client.get(DONE_URL)
        tripped = self.request_from("198.51.100.1", ACCOUNT_EMAIL)
        self.assertRedirects(tripped, DONE_URL)
        self.assertEqual(
            without_csrf(self.client.get(tripped["Location"]).content),
            without_csrf(normal.content),
        )

    def test_other_addresses_are_unaffected(self) -> None:
        User.objects.create_user(
            username="other",
            password=TEST_PASSWORD,
            email="other@example.test",
            email_verified=True,
        )
        for source in range(self.LIMIT + 1):
            self.request_from(f"192.0.2.{source + 1}", ACCOUNT_EMAIL)
        mail.outbox.clear()
        self.request_from("192.0.2.50", "other@example.test")
        self.assertEqual(len(mail.outbox), 1)


class PasswordResetSiteBaseUrlTest(PasswordResetTestCase):
    @override_settings(SITE_BASE_URL="https://tarmar.example")
    def test_the_link_is_built_on_site_base_url_not_the_host_header(self) -> None:
        self.client.post(RESET_URL, {"email": ACCOUNT_EMAIL}, HTTP_HOST="testserver")
        match = CONFIRM_LINK.search(sent_body())
        assert match is not None
        self.assertEqual(match.group("scheme"), "https")
        self.assertEqual(match.group("host"), "tarmar.example")
        self.assertEqual(
            mail.outbox[0].subject, "Reset your password at tarmar.example"
        )
