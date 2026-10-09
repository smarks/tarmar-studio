"""A view exception under DEBUG=False must leave a traceback on stderr.

Django logs every 500 to the ``django.request`` logger, but its default
configuration only prints that record when DEBUG is on (and mails ADMINS,
unset here, when it is off). Production runs gunicorn with
``--error-logfile -`` under systemd, so a record that reaches a stderr handler
lands in the journal and one that does not vanishes -- which is what happened
while chasing the deploy-window 500 in issue #328 (see #329).
"""

import io
import logging
import tempfile

from django.http import HttpRequest, HttpResponse
from django.test import TestCase, override_settings
from django.urls import include, path

from common.gate import DEV_GATE_PASSPHRASE

EXPLODE_MESSAGE = "deliberate server error for the logging test"


def explode(request: HttpRequest) -> HttpResponse:
    """Raise, so the request handler takes its 500 path."""
    raise RuntimeError(EXPLODE_MESSAGE)


urlpatterns = [
    path("explode/", explode),
    path("", include("tarmar_studio.urls")),
]


@override_settings(ROOT_URLCONF=__name__, DEBUG=False)
class ServerErrorLoggingTest(TestCase):
    """The configured ``django.request`` handler writes the traceback."""

    def setUp(self) -> None:
        self.client.raise_request_exception = False
        self.client.post("/gate/", {"passphrase": DEV_GATE_PASSPHRASE})

    def capture_stderr_handlers(self, logger_name: str) -> io.StringIO:
        """Point every stream handler on ``logger_name`` at a buffer.

        ``logging.StreamHandler`` binds ``sys.stderr`` when it is built, so
        redirecting stderr after settings are loaded would not see its output;
        swapping the handler's stream does.

        The stream is assigned rather than set with ``setStream``, which
        flushes the stream it replaces. Under ``pytest -n auto`` the stderr
        the handler bound at settings-load time is a capture file that a
        worker has since closed, so that flush raises ``ValueError: I/O
        operation on closed file`` and the test fails for a reason that has
        nothing to do with what it checks (issues #584, #589). Assigning
        touches neither stream.
        """
        logger = logging.getLogger(logger_name)
        stream_handlers = [
            handler
            for handler in logger.handlers
            if isinstance(handler, logging.StreamHandler)
        ]
        self.assertTrue(
            stream_handlers, f"{logger_name} has no stream handler configured"
        )
        buffer = io.StringIO()
        for handler in stream_handlers:
            original_stream = handler.stream
            handler.stream = buffer
            self.addCleanup(setattr, handler, "stream", original_stream)
        return buffer

    def test_view_exception_is_logged_with_its_traceback(self) -> None:
        with self.assertLogs("django.request", level="ERROR") as logs:
            response = self.client.get("/explode/")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(len(logs.records), 1)
        self.assertIsNotNone(logs.records[0].exc_info)

    def test_view_exception_traceback_reaches_stderr_handler(self) -> None:
        # Not combined with assertLogs above: that helper swaps the logger's
        # handlers out for its own while it is active, so the configured
        # handler would never see the record.
        captured = self.capture_stderr_handlers("django.request")
        response = self.client.get("/explode/")
        self.assertEqual(response.status_code, 500)

        written = captured.getvalue()
        self.assertIn(
            "[ERROR] django.request: Internal Server Error: /explode/", written
        )
        self.assertIn("Traceback (most recent call last):", written)
        self.assertIn(f"RuntimeError: {EXPLODE_MESSAGE}", written)

    def test_capture_leaves_a_closed_original_stream_alone(self) -> None:
        """The helper must not flush the stream it replaces (issues #584, #589).

        This is the failure the parallel runs saw, made deterministic: a
        handler whose stream is a closed file, as a worker's captured stderr
        can be. ``setStream`` flushes it and raises ``ValueError: I/O
        operation on closed file``; assigning does not. A real file, because
        a closed ``StringIO`` flushes without complaint and would prove
        nothing.
        """
        closed_stream = tempfile.TemporaryFile("w+")
        closed_stream.close()
        handler = logging.StreamHandler(closed_stream)
        logger = logging.getLogger("django.request")
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)

        captured = self.capture_stderr_handlers("django.request")

        self.assertIs(handler.stream, captured)

    def test_error_loggers_do_not_fall_through_to_djangos_console(self) -> None:
        """Handled here, not re-emitted by Django's DEBUG-only console handler."""
        for logger_name in ("django.request", "django.security"):
            with self.subTest(logger=logger_name):
                logger = logging.getLogger(logger_name)
                self.assertFalse(logger.propagate)
                self.assertEqual(logger.level, logging.ERROR)
                self.assertTrue(
                    any(
                        isinstance(handler, logging.StreamHandler)
                        for handler in logger.handlers
                    )
                )
