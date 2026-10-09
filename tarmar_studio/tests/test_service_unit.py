"""Guards on the gunicorn worker settings in ``tarmar.service.template``.

Issue #361: the unit ran gunicorn with the default sync worker and no
``--timeout``, so the arbiter's 30-second default doubled as a hard cap on
request duration and killed the GM's SSE worker mid-stream. The fix lives in a
file no Python test would otherwise touch, so these tests render the template
the way ``deploy.sh`` does and assert on the result.

Rendering rather than grepping the raw file is the point: it also proves every
placeholder ``deploy.sh`` substitutes is one this template actually uses, so a
renamed placeholder cannot ship a unit with ``__PORT__`` still in it.
"""

import re
import shlex
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
UNIT_TEMPLATE = REPO_ROOT / "tarmar.service.template"
DEPLOY_SCRIPT = REPO_ROOT / "deploy.sh"

# The substitutions deploy.sh's render_unit() makes, with stand-in values.
PLACEHOLDERS = {
    "__COLOUR__": "blue",
    "__PORT__": "9066",
    "__RELEASE_DIR__": "/home/sam/dev/tarmar-studio-blue",
    "__APP_DIR__": "/home/sam/dev/tarmar-studio",
}


def render_unit() -> str:
    """The unit file as deploy.sh installs it, for the blue colour."""
    rendered = UNIT_TEMPLATE.read_text(encoding="utf-8")
    for placeholder, value in PLACEHOLDERS.items():
        rendered = rendered.replace(placeholder, value)
    return rendered


def gunicorn_arguments() -> list[str]:
    """The rendered ExecStart's gunicorn command line, as a token list."""
    rendered = render_unit()
    match = re.search(r"^ExecStart=(.*?)(?=^\w+=)", rendered, re.MULTILINE | re.DOTALL)
    assert match is not None, "no ExecStart= line in the rendered unit"
    # systemd's line continuations are the shell's, so shlex reads them.
    return shlex.split(match.group(1).replace("\\\n", " "))


def flag_value(flag: str) -> str:
    """The value following ``flag`` on the rendered gunicorn command line."""
    arguments = gunicorn_arguments()
    return arguments[arguments.index(flag) + 1]


class RenderedUnitTest(SimpleTestCase):
    def test_no_placeholder_survives_rendering(self):
        self.assertNotIn("__", render_unit())

    def test_runs_gunicorn_on_the_colours_port(self):
        arguments = gunicorn_arguments()
        self.assertIn("gunicorn", arguments)
        self.assertEqual(flag_value("--bind"), "127.0.0.1:9066")


class WorkerSettingsTest(SimpleTestCase):
    """The three flags that keep a long GM stream alive (issue #361)."""

    def test_uses_a_threaded_worker_not_the_sync_default(self):
        """Only a non-sync worker heartbeats while a request is in flight.

        Under the sync worker the arbiter cannot tell a long stream from a
        hung process, so it kills both.
        """
        self.assertEqual(flag_value("--worker-class"), "gthread")

    def test_gives_each_worker_enough_threads_for_concurrent_streams(self):
        """A GM turn holds its thread for minutes; short page views must not
        queue behind the players who have one open."""
        self.assertGreaterEqual(int(flag_value("--threads")), 4)

    def test_timeout_is_set_explicitly_and_stays_a_liveness_check(self):
        """Explicit, and small enough to still catch a wedged worker.

        gthread decouples --timeout from request duration, so there is no
        reason to inflate it to cover slow streams -- doing so is exactly what
        would delay detection of a genuinely hung worker.
        """
        timeout = int(flag_value("--timeout"))
        self.assertGreaterEqual(timeout, 30)
        self.assertLessEqual(timeout, 120)


class DeployScriptTest(SimpleTestCase):
    """deploy.sh must read the flags back out of systemd after the restart.

    A template edit is not proof the running unit changed, so the deploy
    checks ``systemctl cat`` and fails loudly if the colour it just restarted
    is still on the old ExecStart.
    """

    def test_deploy_checks_the_running_unit_for_the_worker_flags(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("systemctl cat", script)
        self.assertIn("assert_worker_flags", script)
        for flag in ("--worker-class", "--threads", "--timeout"):
            self.assertIn(flag, script)
