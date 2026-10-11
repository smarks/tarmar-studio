"""``manage.py password_reset_link`` (issue #929).

The host operator's route for an account with no email address, or a site
whose mail is not working: print the same signed link the reset email carries.
"""

import re
from io import StringIO

from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.management import CommandError, call_command
from django.test import Client, TestCase, override_settings

from accounts.models import User
from accounts.password_reset import DEV_BASE_URL, default_base_url
from common.test_helpers import TEST_PASSWORD

NEW_PASSWORD = "LanternBridge42x"
CONFIRM_PATH_PREFIX = "/tarmar/accounts/password_reset/"
#: A reset link as the email and the command write it.
RESET_LINK = re.compile(
    rf"http://testserver(?P<path>{CONFIRM_PATH_PREFIX})"
    r"(?P<uidb64>[\w-]+)/(?P<token>[\w-]+)/"
)


def run_command(*arguments: str) -> tuple[str, str]:
    """Run the command and return what it wrote to stdout and stderr."""
    stdout = StringIO()
    stderr = StringIO()
    call_command("password_reset_link", *arguments, stdout=stdout, stderr=stderr)
    return stdout.getvalue(), stderr.getvalue()


class PasswordResetLinkCommandTest(TestCase):
    def setUp(self) -> None:
        # No email address: the case the command exists for.
        self.user = User.objects.create_user(username="spencer", password=TEST_PASSWORD)

    def test_unknown_username_is_refused_by_name(self) -> None:
        with self.assertRaisesMessage(
            CommandError, "No account with username 'nobody'."
        ):
            run_command("nobody")

    def test_inactive_account_is_refused(self) -> None:
        self.user.is_active = False
        self.user.save()
        with self.assertRaisesMessage(CommandError, "inactive"):
            run_command("spencer")

    def test_prints_only_the_link_on_stdout(self) -> None:
        stdout, stderr = run_command("spencer", "--base-url", "https://tarmar.test/")
        link = stdout.strip()
        self.assertEqual(stdout.count("\n"), 1)
        self.assertTrue(
            link.startswith(f"https://tarmar.test{CONFIRM_PATH_PREFIX}"), link
        )
        self.assertIn("expires", stderr)

    def test_never_prints_the_password_or_its_hash(self) -> None:
        stdout, stderr = run_command("spencer", "--base-url", "https://tarmar.test")
        for written in (stdout, stderr):
            self.assertNotIn(TEST_PASSWORD, written)
            self.assertNotIn(self.user.password, written)

    def test_sends_no_mail_and_changes_nothing(self) -> None:
        password_hash = self.user.password
        run_command("spencer", "--base-url", "https://tarmar.test")
        self.assertEqual(mail.outbox, [])
        self.user.refresh_from_db()
        self.assertEqual(self.user.password, password_hash)

    def test_the_printed_link_resets_the_password(self) -> None:
        stdout, _ = run_command("spencer", "--base-url", "http://testserver")
        path = stdout.strip().removeprefix("http://testserver")

        client = Client()
        set_password_path = client.get(path)["Location"]
        self.assertTrue(set_password_path.endswith("/set-password/"))
        response = client.post(
            set_password_path,
            {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD},
        )
        self.assertRedirects(response, f"{CONFIRM_PATH_PREFIX}complete/")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))

    def test_matches_the_link_the_email_would_carry(self) -> None:
        self.user.email = "spencer@example.test"
        # The reset mails only a confirmed address (issue #931).
        self.user.email_verified = True
        self.user.save()
        Client().post(f"{CONFIRM_PATH_PREFIX}", {"email": self.user.email})
        self.assertEqual(len(mail.outbox), 1)

        stdout, _ = run_command("spencer", "--base-url", "http://testserver")

        # The token carries the time in seconds, so the two links differ
        # when the calls fall in different seconds (issue #937): the same
        # address for the same account, each token one the account accepts.
        mailed = RESET_LINK.search(str(mail.outbox[0].body))
        printed = RESET_LINK.fullmatch(stdout.strip())
        assert mailed is not None and printed is not None
        self.assertEqual(printed["path"], mailed["path"])
        self.assertEqual(printed["uidb64"], mailed["uidb64"])
        for link in (printed, mailed):
            with self.subTest(token=link["token"]):
                self.assertTrue(
                    default_token_generator.check_token(self.user, link["token"])
                )

    @override_settings(SITE_BASE_URL="", DEBUG=False, ALLOWED_HOSTS=["*", ".x.test"])
    def test_production_without_a_concrete_host_asks_for_base_url(self) -> None:
        with self.assertRaisesMessage(CommandError, "Pass --base-url"):
            run_command("spencer")

    def test_a_base_url_without_a_scheme_is_refused(self) -> None:
        for bad in ("tarmar.test", "ftp://tarmar.test", "https://"):
            with self.subTest(base_url=bad):
                with self.assertRaisesMessage(CommandError, "is not a base URL"):
                    run_command("spencer", "--base-url", bad)

    @override_settings(SITE_BASE_URL="https://configured.test")
    def test_default_base_url_is_site_base_url_when_set(self) -> None:
        stdout, _ = run_command("spencer")
        self.assertTrue(stdout.startswith("https://configured.test/tarmar/"), stdout)


class DefaultBaseUrlTest(TestCase):
    @override_settings(SITE_BASE_URL="https://configured.test/")
    def test_site_base_url_wins_and_loses_its_trailing_slash(self) -> None:
        self.assertEqual(default_base_url(), "https://configured.test")

    @override_settings(
        SITE_BASE_URL="",
        DEBUG=False,
        ALLOWED_HOSTS=["*", ".origamisoftware.com", "tarmar.example", "x.test"],
    )
    def test_production_takes_the_first_concrete_allowed_host_over_https(
        self,
    ) -> None:
        self.assertEqual(default_base_url(), "https://tarmar.example")

    @override_settings(SITE_BASE_URL="", DEBUG=True, ALLOWED_HOSTS=["localhost"])
    def test_debug_uses_the_dev_server(self) -> None:
        self.assertEqual(default_base_url(), DEV_BASE_URL)

    @override_settings(SITE_BASE_URL="", DEBUG=False, ALLOWED_HOSTS=["*"])
    def test_no_concrete_host_in_production_gives_no_default(self) -> None:
        self.assertIsNone(default_base_url())
