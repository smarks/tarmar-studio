"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from django.test import TestCase

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# The checkout's real dotenv. Nothing in this module writes it: other processes
# using the checkout -- Playwright's global setup, a developer's runserver --
# read it while pytest runs, and a staged DATABASE_URL there sent them down the
# production branch mid-run (issue #471).
CHECKOUT_DOTENV_PATH = BASE_DIR / ".env"

# Every variable the mail settings read (issue #929). A subprocess that checks
# what settings make of the environment starts without any of them, so a
# developer's shell cannot decide the answer.
MAIL_ENVIRONMENT_KEYS = (
    "EMAIL_BACKEND",
    "EMAIL_HOST",
    "EMAIL_PORT",
    "EMAIL_HOST_USER",
    "EMAIL_HOST_PASSWORD",
    "EMAIL_USE_TLS",
    "EMAIL_FILE_PATH",
    "DEFAULT_FROM_EMAIL",
    "SITE_BASE_URL",
)
# The sender DEPLOY.md documents for the host.
PRODUCTION_SENDER = "tarmar@roots.origamisoftware.com"


def read_checkout_dotenv():
    """Return the checkout's .env as bytes, or None when there is none."""
    if CHECKOUT_DOTENV_PATH.exists():
        return CHECKOUT_DOTENV_PATH.read_bytes()
    return None


class DotenvStagingMixin:
    """Stage a dotenv for one test in a private temp dir.

    Every test that needs a .env goes through this. Reading whatever the
    developer happens to have instead makes the suite pass or fail by machine:
    CI checks out clean and has no .env, so such a test is green there and red
    only locally -- the wrong way round for a failure to be discovered, and
    what made #298 block commits rather than a build.

    The file lives outside the checkout and reaches settings through
    TARMAR_DOTENV_PATH, so each test has its own copy: concurrent xdist workers
    cannot see each other's, and nothing else reading BASE_DIR/.env sees any.
    """

    def stage_dotenv(self, body):
        """Write `body` to a fresh temp dotenv, removed again on cleanup.

        Args:
            body: The complete .env contents this test needs.

        Returns:
            The path written, for a test that reads it back.
        """
        staging_dir = Path(tempfile.mkdtemp(prefix="tarmar-dotenv-"))
        self.addCleanup(shutil.rmtree, staging_dir)
        dotenv_path = staging_dir / ".env"
        dotenv_path.write_text(body)
        self.staged_dotenv_path = dotenv_path
        return dotenv_path

    def stage_empty_dotenv_unless_staged(self):
        """Neutralise the ambient .env for a test that did not stage its own.

        A subprocess started with DEBUG=true *does* load a dotenv, so a
        developer whose BASE_DIR/.env sets DATABASE_URL would send settings
        down the production branch and the import dies on `dj_database_url`, a
        prod-only dependency. That has nothing to do with what such a test is
        checking, so it starts from an empty file (issue #298).
        """
        if getattr(self, "staged_dotenv_path", None) is None:
            self.stage_dotenv("")

    def subprocess_environment(self):
        """Return os.environ with settings pointed at this test's dotenv.

        Stages an empty dotenv first if the test has not staged one, so a
        subprocess never falls back to the checkout's real file.
        """
        self.stage_empty_dotenv_unless_staged()
        env = dict(os.environ)
        env["TARMAR_DOTENV_PATH"] = str(self.staged_dotenv_path)
        # Avoid pytest auto-skipping checks inside the subprocess.
        env.pop("PYTEST_CURRENT_TEST", None)
        return env


class SecuritySettingsTest(TestCase):
    """Tests for security settings."""

    def test_debug_defaults_to_false(self):
        """DEBUG should default to False when env var not set."""
        import os

        # Save current value
        original = os.environ.get("DEBUG")
        # Remove env var
        if "DEBUG" in os.environ:
            del os.environ["DEBUG"]

        # Re-evaluate the expression
        debug_value = os.environ.get("DEBUG", "False").lower() == "true"
        self.assertFalse(debug_value)

        # Restore original
        if original is not None:
            os.environ["DEBUG"] = original

    def test_session_cookie_httponly_enabled(self):
        """SESSION_COOKIE_HTTPONLY should be True."""
        from django.conf import settings

        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)

    def test_session_cookie_samesite_set(self):
        """SESSION_COOKIE_SAMESITE should be Lax."""
        from django.conf import settings

        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")

    def test_csrf_cookie_httponly_enabled(self):
        """CSRF_COOKIE_HTTPONLY should be True."""
        from django.conf import settings

        self.assertTrue(settings.CSRF_COOKIE_HTTPONLY)

    def test_csrf_cookie_samesite_set(self):
        """CSRF_COOKIE_SAMESITE should be Lax."""
        from django.conf import settings

        self.assertEqual(settings.CSRF_COOKIE_SAMESITE, "Lax")

    def test_trust_proxy_ip_header_defaults_to_off(self):
        """Trusting forwarded headers must be opted into, never inherited."""
        from django.conf import settings

        self.assertIs(settings.TRUST_PROXY_IP_HEADER, False)

    def test_trusted_proxy_ips_defaults_to_loopback(self):
        """nginx proxies to gunicorn over loopback; nothing else is a proxy."""
        from django.conf import settings

        self.assertEqual(settings.TRUSTED_PROXY_IPS, frozenset({"127.0.0.1", "::1"}))

    def test_client_ip_middleware_runs_first(self):
        """Ordering is load-bearing, not cosmetic.

        The REMOTE_ADDR rewrite has to land before anything that reads it --
        origami_common's rate_limit decorator and the gate limiter both do --
        so every layer agrees on who the caller is.
        """
        from django.conf import settings

        self.assertEqual(
            settings.MIDDLEWARE[0], "common.middleware.TrustedProxyClientIPMiddleware"
        )

    def test_secret_key_uses_environment_variable(self):
        """SECRET_KEY should be configurable via environment variable."""
        import os

        # Test that the environment variable mechanism works
        test_key = "test-secret-key-for-testing"
        original = os.environ.get("SECRET_KEY")

        os.environ["SECRET_KEY"] = test_key
        # Re-evaluate the expression used in settings
        key_value = os.environ.get(
            "SECRET_KEY", "django-insecure-dev-only-key-do-not-use-in-production"
        )
        self.assertEqual(key_value, test_key)

        # Restore original
        if original is not None:
            os.environ["SECRET_KEY"] = original
        else:
            del os.environ["SECRET_KEY"]

    def test_allowed_hosts_from_environment(self):
        """ALLOWED_HOSTS should be configurable via comma-separated env var."""
        import os

        original = os.environ.get("ALLOWED_HOSTS")

        # Test parsing comma-separated hosts
        os.environ["ALLOWED_HOSTS"] = "example.com, www.example.com ,api.example.com"
        hosts = [
            host.strip()
            for host in os.environ.get("ALLOWED_HOSTS", "").split(",")
            if host.strip()
        ]
        self.assertEqual(hosts, ["example.com", "www.example.com", "api.example.com"])

        # Restore original
        if original is not None:
            os.environ["ALLOWED_HOSTS"] = original
        else:
            del os.environ["ALLOWED_HOSTS"]

    def test_allowed_hosts_empty_by_default(self):
        """ALLOWED_HOSTS should be empty when env var not set."""
        import os

        original = os.environ.get("ALLOWED_HOSTS")

        # Remove env var
        if "ALLOWED_HOSTS" in os.environ:
            del os.environ["ALLOWED_HOSTS"]

        hosts = [
            host.strip()
            for host in os.environ.get("ALLOWED_HOSTS", "").split(",")
            if host.strip()
        ]
        self.assertEqual(hosts, [])

        # Restore original
        if original is not None:
            os.environ["ALLOWED_HOSTS"] = original


class EnvExampleTest(TestCase):
    """Guards against .env.example lines that silently defeat a fallback.

    settings.py reads GATE_PASSPHRASE via os.environ.get(..., "mellon"). An
    uncommented `GATE_PASSPHRASE=` line is present-but-empty once load_dotenv
    reads it, so os.environ.get sees a real (empty) value and never reaches
    the "mellon" fallback -- speak_friend then accepts an empty submission
    (issue #149). It must stay commented out with an example value.
    """

    def test_gate_passphrase_is_commented_out_in_env_example(self):
        from pathlib import Path

        env_example_path = Path(__file__).parent.parent.parent / ".env.example"
        for line in env_example_path.read_text().splitlines():
            if line.startswith("GATE_PASSPHRASE="):
                self.fail(
                    "GATE_PASSPHRASE must stay commented out in .env.example "
                    f"(an uncommented empty value overrides the dev fallback "
                    f"and disables the gate): found {line!r}"
                )


class ProductionSettingsFailFastTest(DotenvStagingMixin, TestCase):
    """Settings must refuse to load in production with insecure defaults."""

    def _import_settings(self, env_overrides):
        """Re-execute settings.py in a clean subprocess with the given env.

        "Clean" has to include the dotenv, not just the environment:
        see :meth:`stage_empty_dotenv_unless_staged`.
        """
        env = self.subprocess_environment()
        for key in ("SECRET_KEY", "ALLOWED_HOSTS", "DEBUG", *MAIL_ENVIRONMENT_KEYS):
            env.pop(key, None)
        env.update(env_overrides)

        # tarmar_studio/tests/test_settings.py -> tarmar_studio/settings.py
        settings_path = BASE_DIR / "tarmar_studio" / "settings.py"
        return subprocess.run(
            [sys.executable, str(settings_path)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_fails_when_secret_key_missing_in_production(self):
        result = self._import_settings({"DEBUG": "False", "ALLOWED_HOSTS": "x.test"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SECRET_KEY", result.stderr)

    def test_fails_when_secret_key_is_dev_sentinel_in_production(self):
        result = self._import_settings(
            {
                "DEBUG": "False",
                "ALLOWED_HOSTS": "x.test",
                "SECRET_KEY": "django-insecure-dev-only-key-do-not-use-in-production",
            }
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SECRET_KEY", result.stderr)

    def test_fails_when_allowed_hosts_missing_in_production(self):
        result = self._import_settings(
            {"DEBUG": "False", "SECRET_KEY": "real-prod-key-12345"}
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ALLOWED_HOSTS", result.stderr)

    def test_fails_when_default_from_email_missing_in_production(self):
        # Issue #929: without a sender, a reset message is refused or goes
        # out from webmaster@localhost, and the failure is only logged.
        result = self._import_settings(
            {
                "DEBUG": "False",
                "SECRET_KEY": "real-prod-key-12345",
                "ALLOWED_HOSTS": "example.com",
            }
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DEFAULT_FROM_EMAIL must be set", result.stderr)

    def test_passes_with_real_secret_key_and_hosts_in_production(self):
        result = self._import_settings(
            {
                "DEBUG": "False",
                "SECRET_KEY": "real-prod-key-12345",
                "ALLOWED_HOSTS": "example.com",
                "DEFAULT_FROM_EMAIL": PRODUCTION_SENDER,
            }
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_production_on_the_default_canon_logs_a_warning(self):
        # Issue #921: production without TARMAR_CANON_DIR serves the generic
        # default canon, which only this warning would reveal.
        default_canon = BASE_DIR / "reference" / "default_canon"
        result = self._import_settings(
            {
                "DEBUG": "False",
                "SECRET_KEY": "real-prod-key-12345",
                "ALLOWED_HOSTS": "example.com",
                "DEFAULT_FROM_EMAIL": PRODUCTION_SENDER,
                "TARMAR_CANON_DIR": str(default_canon),
            }
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("TARMAR_CANON_DIR names the default canon", result.stderr)
        self.assertIn("generic default canon", result.stderr)

    def test_production_on_a_tarmar_canon_logs_nothing_about_it(self):
        canon_directory = Path(tempfile.mkdtemp(prefix="tarmar-canon-"))
        self.addCleanup(shutil.rmtree, canon_directory)
        (canon_directory / "canon.json").write_text('{"name": "tarmar"}')
        result = self._import_settings(
            {
                "DEBUG": "False",
                "SECRET_KEY": "real-prod-key-12345",
                "ALLOWED_HOSTS": "example.com",
                "DEFAULT_FROM_EMAIL": PRODUCTION_SENDER,
                "TARMAR_CANON_DIR": str(canon_directory),
            }
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertNotIn("default canon", result.stderr)

    def test_a_development_process_is_not_warned(self):
        default_canon = BASE_DIR / "reference" / "default_canon"
        result = self._import_settings(
            {"DEBUG": "true", "TARMAR_CANON_DIR": str(default_canon)}
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertNotIn("default canon", result.stderr)

    def test_dev_fallback_still_works_in_debug(self):
        result = self._import_settings({"DEBUG": "true"})
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def _import_settings_as_test_run(self, dotenv_body):
        """Import settings in a subprocess that looks like a test run.

        Stages `dotenv_body` as the dotenv settings loads and returns whatever
        DATABASE_URL the imported settings left in the subprocess environment.
        """
        self.stage_dotenv(dotenv_body)

        env = self.subprocess_environment()
        env.pop("DATABASE_URL", None)
        program = (
            "import sys, os;"
            "sys.argv = ['manage.py', 'test'];"
            "import tarmar_studio.settings;"
            "print(os.environ.get('DATABASE_URL', ''))"
        )
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return result.stdout.strip()

    def test_dotenv_is_not_read_during_a_test_run(self):
        """The suite must not inherit the host's .env.

        deploy.sh runs pytest on the deployment host without exporting .env --
        "the test step above must NOT see this env" -- because that file holds
        the production DATABASE_URL. Reading it off disk anyway put the whole
        suite on the production database, which has no privilege to create a
        test database, and the deploy failed with 581 errors.
        """
        seen = self._import_settings_as_test_run(
            "DATABASE_URL=postgres://nobody@localhost:5432/should_not_be_used\n"
        )
        self.assertEqual(seen, "")

    def test_dotenv_cannot_satisfy_the_production_checks(self):
        """A .env left on a production host must not defeat the fail-fast.

        settings.py loads BASE_DIR/.env so local development does not need env
        prefixes on every command. That file is read before the checks above
        run, so without care it would supply the very values those checks
        exist to demand. A real DEBUG=False must make the file irrelevant.
        """
        dotenv_path = self.stage_dotenv("ALLOWED_HOSTS=sneaky.test\n")
        # The file really does offer the value the checks demand -- that is the
        # whole scenario, and staging it makes the scenario the same on every
        # machine rather than borrowing the developer's file (issue #298).
        self.assertIn("ALLOWED_HOSTS", dotenv_path.read_text())

        # ALLOWED_HOSTS is absent from the real environment passed in here, so
        # the only way the subprocess could find one is by reading that file.
        result = self._import_settings(
            {"DEBUG": "False", "SECRET_KEY": "real-prod-key-12345"}
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ALLOWED_HOSTS", result.stderr)

    def test_dotenv_is_still_read_for_local_development(self):
        """The convenience the .env loading exists for still works."""
        # Used to skip whenever a developer had a .env, so the convenience this
        # loading exists for went unverified on exactly the machines that rely
        # on it. Staging restores their file afterwards, so there is nothing to
        # protect it from (issue #298).
        self.stage_dotenv(
            "SECRET_KEY=from-the-dotenv-file-12345\nALLOWED_HOSTS=example.test\n"
            f"DEFAULT_FROM_EMAIL={PRODUCTION_SENDER}\n"
        )

        # Nothing is passed in the environment, so settings can only satisfy
        # the production checks by reading the file.
        result = self._import_settings({})
        self.assertEqual(result.returncode, 0, msg=result.stderr)


class DotenvStagingTest(DotenvStagingMixin, TestCase):
    """The staging helper and the settings override it relies on.

    This is what makes the suite safe to run beside anything else using the
    checkout. If staging ever reaches BASE_DIR/.env again, a concurrent
    Playwright run or runserver loads a test's DATABASE_URL (issue #471), so it
    is pinned here rather than trusted.
    """

    def _stage_and_finish(self, body):
        """Run one throwaway test that stages `body`, so its cleanups fire.

        Returns:
            What was observed while the dotenv was staged: the staged path, its
            contents, and the checkout's .env bytes (None when absent).
        """
        from unittest import TestCase as PlainTestCase
        from unittest import TestResult

        observed = {}

        class Staging(DotenvStagingMixin, PlainTestCase):
            def runTest(inner):
                staged_path = inner.stage_dotenv(body)
                observed["path"] = staged_path
                observed["contents"] = staged_path.read_text()
                observed["checkout_dotenv"] = read_checkout_dotenv()

        result = TestResult()
        Staging().run(result)
        self.assertTrue(result.wasSuccessful(), msg=result.errors)
        return observed

    def _settings_dotenv_path(self, env):
        """Import settings in a subprocess and return its DOTENV_PATH.

        The subprocess looks like a test run, so settings resolves the path
        but loads nothing from it, whatever this machine's .env holds.
        """
        program = (
            "import sys;"
            "sys.argv = ['manage.py', 'test'];"
            "import tarmar_studio.settings as settings;"
            "print(settings.DOTENV_PATH)"
        )
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return Path(result.stdout.strip())

    def test_staging_leaves_the_checkouts_dotenv_byte_for_byte_unchanged(self):
        """BASE_DIR/.env is identical, or still absent, during and after."""
        before = read_checkout_dotenv()

        observed = self._stage_and_finish(
            "DATABASE_URL=postgres://nobody@localhost:5432/should_not_be_used\n"
        )

        self.assertEqual(observed["checkout_dotenv"], before)
        self.assertEqual(read_checkout_dotenv(), before)
        self.assertNotEqual(observed["path"], CHECKOUT_DOTENV_PATH)
        self.assertFalse(observed["path"].resolve().is_relative_to(BASE_DIR))

    def test_the_staged_dotenv_is_written_then_removed_again(self):
        observed = self._stage_and_finish("ALLOWED_HOSTS=staged.test\n")

        self.assertEqual(observed["contents"], "ALLOWED_HOSTS=staged.test\n")
        self.assertFalse(observed["path"].exists())
        self.assertFalse(observed["path"].parent.exists())

    def test_settings_reads_the_checkouts_dotenv_without_an_override(self):
        """Production behaviour: no TARMAR_DOTENV_PATH means BASE_DIR/.env."""
        env = {k: v for k, v in os.environ.items() if k != "TARMAR_DOTENV_PATH"}
        self.assertEqual(self._settings_dotenv_path(env), CHECKOUT_DOTENV_PATH)

        env["TARMAR_DOTENV_PATH"] = ""
        self.assertEqual(self._settings_dotenv_path(env), CHECKOUT_DOTENV_PATH)

    def test_settings_follows_the_override_to_the_staged_dotenv(self):
        staged_path = self.stage_dotenv("")
        self.assertEqual(
            self._settings_dotenv_path(self.subprocess_environment()), staged_path
        )


class GMStreamBudgetSettingTest(DotenvStagingMixin, TestCase):
    """The GM turn budget's default, and the environment's power over it.

    Issue #554: character-creation turns measured 171-249 s on production
    against a 180 s budget, so the default is 300 s. A deploy host that wants
    another number still sets ``GM_STREAM_BUDGET_SECONDS`` and is obeyed --
    the default is what a checkout with no .env runs, never a ceiling.
    """

    def _budget(self, env):
        """Import settings in a subprocess and return GM_STREAM_BUDGET_SECONDS."""
        program = (
            "import sys;"
            "sys.argv = ['manage.py', 'test'];"
            "import tarmar_studio.settings as settings;"
            "print(settings.GM_STREAM_BUDGET_SECONDS)"
        )
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return float(result.stdout.strip())

    def test_the_default_is_five_minutes(self):
        env = self.subprocess_environment()
        env.pop("GM_STREAM_BUDGET_SECONDS", None)

        self.assertEqual(self._budget(env), 300.0)

    def test_the_environment_still_overrides_it(self):
        env = self.subprocess_environment()
        env["GM_STREAM_BUDGET_SECONDS"] = "420"

        self.assertEqual(self._budget(env), 420.0)


class PinnedDiceSettingTest(DotenvStagingMixin, TestCase):
    """E2E_DICE_SEEDING, the browser tests' pinned dice (issue #959).

    It lets a request choose its own dice, so a process without DEBUG must not
    load with it, and only a process with it carries the middleware.
    """

    PRODUCTION = {
        "DEBUG": "False",
        "SECRET_KEY": "real-prod-key-12345",
        "ALLOWED_HOSTS": "example.com",
        "DEFAULT_FROM_EMAIL": PRODUCTION_SENDER,
    }

    def import_settings(
        self, env_overrides: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        """Import settings in a clean subprocess; print whether it pins dice."""
        env = self.subprocess_environment()
        for key in (
            "SECRET_KEY",
            "ALLOWED_HOSTS",
            "DEBUG",
            "E2E_DICE_SEEDING",
            *MAIL_ENVIRONMENT_KEYS,
        ):
            env.pop(key, None)
        env.update(env_overrides)
        program = (
            "import tarmar_studio.settings as s\n"
            "print('PINNED', 'common.middleware.PinnedDiceMiddleware' in s.MIDDLEWARE)\n"
        )
        return subprocess.run(
            [sys.executable, "-c", program],
            cwd=BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_production_refuses_to_start_with_it(self):
        for spelling in ("true", "True", "TRUE"):
            with self.subTest(spelling=spelling):
                result = self.import_settings(
                    {**self.PRODUCTION, "E2E_DICE_SEEDING": spelling}
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("E2E_DICE_SEEDING", result.stderr)
                self.assertIn("refused unless DEBUG=true", result.stderr)

    def test_production_without_it_carries_no_middleware(self):
        result = self.import_settings(self.PRODUCTION)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("PINNED False", result.stdout)

    def test_debug_without_it_carries_no_middleware(self):
        result = self.import_settings({"DEBUG": "true"})
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("PINNED False", result.stdout)

    def test_debug_with_it_installs_the_middleware(self):
        result = self.import_settings({"DEBUG": "true", "E2E_DICE_SEEDING": "true"})
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("PINNED True", result.stdout)


class MailSettingsTest(DotenvStagingMixin, TestCase):
    """What settings make of the mail variables (issue #929)."""

    PRODUCTION = {
        "DEBUG": "False",
        "SECRET_KEY": "real-prod-key-12345",
        "ALLOWED_HOSTS": "example.com",
        "DEFAULT_FROM_EMAIL": PRODUCTION_SENDER,
    }

    def resolved_mail_settings(
        self, env_overrides: dict[str, str]
    ) -> dict[str, object]:
        """Import settings in a clean subprocess; return its mail values."""
        env = self.subprocess_environment()
        for key in ("SECRET_KEY", "ALLOWED_HOSTS", "DEBUG", *MAIL_ENVIRONMENT_KEYS):
            env.pop(key, None)
        env.update(env_overrides)
        program = (
            "import json\n"
            "import tarmar_studio.settings as s\n"
            "print(json.dumps({name: getattr(s, name, None) for name in ("
            "'EMAIL_BACKEND', 'EMAIL_HOST', 'EMAIL_PORT', 'EMAIL_HOST_USER', "
            "'EMAIL_HOST_PASSWORD', 'EMAIL_USE_TLS', 'EMAIL_TIMEOUT', "
            "'EMAIL_FILE_PATH', 'DEFAULT_FROM_EMAIL', 'SITE_BASE_URL')}))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_production_defaults_to_smtp_on_the_local_postfix(self):
        resolved = self.resolved_mail_settings(self.PRODUCTION)
        self.assertEqual(
            resolved["EMAIL_BACKEND"], "django.core.mail.backends.smtp.EmailBackend"
        )
        self.assertEqual(resolved["EMAIL_HOST"], "localhost")
        self.assertEqual(resolved["EMAIL_PORT"], 25)
        self.assertEqual(resolved["EMAIL_HOST_USER"], "")
        self.assertEqual(resolved["EMAIL_HOST_PASSWORD"], "")
        self.assertIs(resolved["EMAIL_USE_TLS"], False)
        self.assertEqual(resolved["EMAIL_TIMEOUT"], 10)
        self.assertEqual(resolved["DEFAULT_FROM_EMAIL"], PRODUCTION_SENDER)
        self.assertEqual(resolved["SITE_BASE_URL"], "")
        self.assertIsNone(resolved["EMAIL_FILE_PATH"])

    def test_debug_defaults_to_the_console_and_a_local_sender(self):
        resolved = self.resolved_mail_settings({"DEBUG": "true"})
        self.assertEqual(
            resolved["EMAIL_BACKEND"],
            "django.core.mail.backends.console.EmailBackend",
        )
        self.assertEqual(resolved["DEFAULT_FROM_EMAIL"], "tarmar@localhost")

    def test_every_value_comes_from_the_environment(self):
        resolved = self.resolved_mail_settings(
            {
                **self.PRODUCTION,
                "EMAIL_HOST": "smtp.example.com",
                "EMAIL_PORT": "587",
                "EMAIL_HOST_USER": "relay-user",
                "EMAIL_HOST_PASSWORD": "relay-secret",
                "EMAIL_USE_TLS": "true",
                "SITE_BASE_URL": "https://tarmar.example/",
            }
        )
        self.assertEqual(resolved["EMAIL_HOST"], "smtp.example.com")
        self.assertEqual(resolved["EMAIL_PORT"], 587)
        self.assertEqual(resolved["EMAIL_HOST_USER"], "relay-user")
        self.assertEqual(resolved["EMAIL_HOST_PASSWORD"], "relay-secret")
        self.assertIs(resolved["EMAIL_USE_TLS"], True)
        self.assertEqual(resolved["SITE_BASE_URL"], "https://tarmar.example")

    def test_an_explicit_backend_wins_in_either_mode(self):
        file_backend = "django.core.mail.backends.filebased.EmailBackend"
        for mode in (self.PRODUCTION, {"DEBUG": "true"}):
            with self.subTest(debug=mode["DEBUG"]):
                resolved = self.resolved_mail_settings(
                    {
                        **mode,
                        "EMAIL_BACKEND": file_backend,
                        "EMAIL_FILE_PATH": "/tmp/tarmar-mail",
                    }
                )
                self.assertEqual(resolved["EMAIL_BACKEND"], file_backend)
                self.assertEqual(resolved["EMAIL_FILE_PATH"], "/tmp/tarmar-mail")

    def test_a_malformed_site_base_url_refuses_to_start(self):
        for bad in (
            "tarmar.example",
            "ftp://tarmar.example",
            "https://",
            "https://tarmar.example/tarmar",
            "https://tarmar.example?x=1",
            "https://tarmar.example#top",
        ):
            for mode in (self.PRODUCTION, {"DEBUG": "true"}):
                with self.subTest(site_base_url=bad, debug=mode["DEBUG"]):
                    env = self.subprocess_environment()
                    for key in (
                        "SECRET_KEY",
                        "ALLOWED_HOSTS",
                        "DEBUG",
                        *MAIL_ENVIRONMENT_KEYS,
                    ):
                        env.pop(key, None)
                    env.update({**mode, "SITE_BASE_URL": bad})
                    result = subprocess.run(
                        [sys.executable, "-c", "import tarmar_studio.settings"],
                        cwd=BASE_DIR,
                        env=env,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"SITE_BASE_URL is {bad!r}", result.stderr)
                    self.assertIn("no path, query or fragment", result.stderr)

    def test_a_trailing_slash_on_site_base_url_is_accepted(self):
        resolved = self.resolved_mail_settings(
            {**self.PRODUCTION, "SITE_BASE_URL": "http://tarmar.example:8000/"}
        )
        self.assertEqual(resolved["SITE_BASE_URL"], "http://tarmar.example:8000")

    def test_env_example_documents_every_mail_variable(self):
        documented = (BASE_DIR / ".env.example").read_text()
        for key in MAIL_ENVIRONMENT_KEYS:
            with self.subTest(key=key):
                self.assertIn(f"{key}=", documented)


class RepositoryUrlSettingsTest(DotenvStagingMixin, TestCase):
    """What settings make of the footer's repository addresses (issue #914)."""

    def resolved_repository_urls(self, env_overrides: dict[str, str]) -> list[str]:
        """Import settings in a clean subprocess; return the two addresses."""
        env = self.subprocess_environment()
        for key in ("SECRET_KEY", "ALLOWED_HOSTS", "DEBUG"):
            env.pop(key, None)
        for key in ("REPOSITORY_PUBLIC_URL", "REPOSITORY_PRIVATE_URL"):
            env.pop(key, None)
        env.update({**MailSettingsTest.PRODUCTION, **env_overrides})
        program = (
            "import tarmar_studio.settings as s\n"
            "print(s.REPOSITORY_PUBLIC_URL)\n"
            "print(s.REPOSITORY_PRIVATE_URL)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return result.stdout.strip().splitlines()[-2:]

    def test_the_defaults_are_the_public_snapshot_and_the_private_history(self):
        self.assertEqual(
            self.resolved_repository_urls({}),
            [
                "https://github.com/smarks/tarmar-studio",
                "https://github.com/smarks/tarmar-studio-private",
            ],
        )

    def test_the_environment_overrides_both_and_trailing_slashes_drop(self):
        self.assertEqual(
            self.resolved_repository_urls(
                {
                    "REPOSITORY_PUBLIC_URL": "https://example.test/public/",
                    "REPOSITORY_PRIVATE_URL": "https://example.test/private/",
                }
            ),
            ["https://example.test/public", "https://example.test/private"],
        )

    def test_a_malformed_address_refuses_to_start(self):
        for variable in ("REPOSITORY_PUBLIC_URL", "REPOSITORY_PRIVATE_URL"):
            for bad in (
                "github.com/smarks/tarmar-studio",
                "ftp://github.com/smarks/tarmar-studio",
                "https://",
                "https://github.com/smarks/tarmar-studio?tab=readme",
                "https://github.com/smarks/tarmar-studio#top",
                "",
            ):
                with self.subTest(variable=variable, value=bad):
                    env = self.subprocess_environment()
                    for key in ("SECRET_KEY", "ALLOWED_HOSTS", "DEBUG"):
                        env.pop(key, None)
                    env.update({**MailSettingsTest.PRODUCTION, variable: bad})
                    result = subprocess.run(
                        [sys.executable, "-c", "import tarmar_studio.settings"],
                        cwd=BASE_DIR,
                        env=env,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"{variable} is {bad!r}", result.stderr)
                    self.assertIn("with no query or fragment", result.stderr)

    def test_an_address_with_a_path_is_accepted(self):
        self.assertEqual(
            self.resolved_repository_urls(
                {
                    "REPOSITORY_PUBLIC_URL": "https://git.example.test/team/repo",
                    "REPOSITORY_PRIVATE_URL": "http://git.example.test:3000/a/b/",
                }
            ),
            ["https://git.example.test/team/repo", "http://git.example.test:3000/a/b"],
        )

    def test_env_example_documents_both_with_the_defaults(self):
        documented = (BASE_DIR / ".env.example").read_text()
        self.assertIn(
            "# REPOSITORY_PUBLIC_URL=https://github.com/smarks/tarmar-studio\n",
            documented,
        )
        self.assertIn(
            "# REPOSITORY_PRIVATE_URL="
            "https://github.com/smarks/tarmar-studio-private\n",
            documented,
        )
