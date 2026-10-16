import functools
import json
import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest import skip
from unittest.mock import patch

import httpx
from django.test import Client, TestCase

from accounts.models import User
from common.middleware import PublicPathPassphraseMiddleware
from common.rolling import Roller, RollRecord
from reference.paths import CANON_TESTS_DIR_NAME, DM_RULES_DIR

# What the Tarmar-only tests expect of the loaded canon (issue #921). The
# names they pin are the canon's secrets, so the canon supplies them in this
# file rather than this repository spelling them out; the default canon has
# no such file, and a test that needs a key skips without it (but fails when
# the file is there and the key is not: see skip_unless_canon_expects).
CANON_EXPECTATIONS_FILE = DM_RULES_DIR / CANON_TESTS_DIR_NAME / "expectations.json"


def load_canon_expectations(path: Path = CANON_EXPECTATIONS_FILE) -> dict[str, Any]:
    """The loaded canon's test expectations, or none when it has no file.

    Args:
        path: The canon's ``tests/expectations.json``.

    Returns:
        The file's object; empty when the file is absent.

    Raises:
        ValueError: The file is not a JSON object.
    """
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must hold a JSON object")
    return data


CANON_EXPECTATIONS: dict[str, Any] = load_canon_expectations()


def skip_unless_canon_expects(
    key: str, path: Path = CANON_EXPECTATIONS_FILE
) -> Callable[[Any], Any]:
    """Skip a test when the loaded canon has no expectations file at all.

    A canon without ``tests/expectations.json`` (the default canon) skips the
    test. A canon with the file must supply ``key``: when it does not, the
    test fails rather than skips (#922), because a Tarmar-only test that
    quietly skips under the Tarmar canon checks nothing.

    Args:
        key: The expectation the test reads from :data:`CANON_EXPECTATIONS`.
        path: The canon's ``tests/expectations.json``.

    Returns:
        A decorator for a test method or a test case class.

    Raises:
        ValueError: The file is not a JSON object.
    """
    if not path.is_file():
        return skip(
            f"the loaded canon has no {path.name} to supply {key!r} "
            "(the Tarmar canon's does)"
        )
    if key in load_canon_expectations(path):
        return lambda test: test
    message = f"{path} supplies no {key!r}, which this test needs"

    def failing(test_method: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(test_method)
        def fail_for_the_missing_key(self: Any, *args: Any, **kwargs: Any) -> None:
            self.fail(message)

        return fail_for_the_missing_key

    def decorate(test: Any) -> Any:
        if isinstance(test, type):
            for name in dir(test):
                if name.startswith("test"):
                    setattr(test, name, failing(getattr(test, name)))
            return test
        return failing(test)

    return decorate


# The password every test user is created with. Strong enough to pass the
# project's password validators when a test drives the real signup form.
TEST_PASSWORD = "GalleonMist7q"


def mark_gate_passed(client: Client) -> None:
    """Mark a test client's session as past the passphrase gate.

    Logging in or out flushes the session, so tests that do either re-plant
    the flag afterwards.

    Args:
        client: The Django test client whose session to mark.
    """
    session = client.session
    session[PublicPathPassphraseMiddleware.SESSION_FLAG] = True
    session.save()


def logged_exception(record: logging.LogRecord) -> BaseException:
    """The exception a log record was written with, traceback and all (#13).

    A view that keeps an exception's text from the page must still log it;
    this is how a test reads back what was logged.

    Args:
        record: One of the records ``assertLogs`` captured.

    Returns:
        The exception logged with ``record``.

    Raises:
        AssertionError: The record was logged without an exception.
    """
    if record.exc_info is None or record.exc_info[1] is None:
        raise AssertionError(f"logged without a traceback: {record.getMessage()}")
    return record.exc_info[1]


# Required fields for a test Beast, a plain four-legged biter. ``make_beast``
# below is the shared factory; modules whose tests depend on particular stats
# pass them as overrides.
BEAST_DEFAULTS = {
    "name": "Test Wolf",
    "slug": "test-wolf",
    "category": "mammals",
    "description": "A test wolf.",
    "strength": 12,
    "dexterity": 14,
    "intelligence": 5,
    "wisdom": 5,
    "constitution": 12,
    "charisma": 4,
    "fatigue_pool": 30,
    "body_pool": 20,
    "movement": 12,
    "attacks": [{"name": "Bite", "damage": "1d6+1", "weapon_class": "Piercing"}],
}


def make_beast(*, save: bool = True, **overrides):
    """Build a bestiary Beast for tests from ``BEAST_DEFAULTS``.

    Args:
        save: Create the row when True; return an unsaved instance when False.
        **overrides: Any ``Beast`` field, overriding or adding to the defaults.

    Returns:
        The ``bestiary.models.Beast``, saved unless ``save`` is False.
    """
    from bestiary.models import Beast

    fields = {**BEAST_DEFAULTS, **overrides}
    fields["attacks"] = [dict(attack) for attack in fields["attacks"]]
    if not save:
        return Beast(**fields)
    return Beast.objects.create(**fields)


# The ten keyword arguments every hand-built test Character repeats, in the
# model's field order. `create_character` below is the shared factory; new test
# modules should call it rather than restating these.
CHARACTER_DEFAULTS = {
    "name": "Test Character",
    "strength": 14,
    "dexterity": 12,
    "intelligence": 10,
    "wisdom": 13,
    "constitution": 15,
    "charisma": 8,
    "appearance_roll": "3",
    "height_roll": "4",
    "weight_roll": "3",
    "fatigue_roll": 7,
}


class FacesRoller(Roller):
    """A roller whose every roll lands on the faces given, whatever the dice.

    For a test that needs a check or a save to show particular dice: the
    record keeps the dice asked for and its purpose, with the faces given and
    their sum as the total.
    """

    def __init__(self, *faces: int) -> None:
        super().__init__(seed=0)
        self.faces_to_give = faces

    def roll(
        self,
        specification: str,
        *,
        purpose: str,
        modifier: int = 0,
        target_number: int | None = None,
        outcome: str | None = None,
    ) -> RollRecord:
        """Roll ``specification`` as :class:`Roller` does, on the faces given."""
        record = super().roll(
            specification,
            purpose=purpose,
            modifier=modifier,
            target_number=target_number,
            outcome=outcome,
        )
        return replace(record, faces=self.faces_to_give, total=sum(self.faces_to_give))


def create_character(user, **overrides):
    """Create a Character for tests, with sane defaults for the required fields.

    Args:
        user: The owning ``accounts.User``.
        **overrides: Any ``Character`` field, overriding ``CHARACTER_DEFAULTS``
            or adding optional fields (``skills``, ``equipment``, ...).

    Returns:
        The saved ``characters.models.Character``.
    """
    from characters.models import Character

    fields = {**CHARACTER_DEFAULTS, **overrides}
    return Character.objects.create(user=user, **fields)


class BaseTestCase(TestCase):
    """Base test class with common setup and helper methods.

    Deliberately does NOT call ``cache.clear()`` here (issue #109). That was
    filed back when ``CACHES["default"]`` was the process-local LocMemCache,
    which really did carry rate-limit counters from one test into the next.
    Since issue #139, the default cache is DatabaseCache, whose table lives
    in the same database as every model -- and every test here runs as a
    ``django.test.TestCase``, which wraps ``setUp``, the test body, and
    ``tearDown`` in one transaction that gets rolled back afterwards. Any
    ``cache.set()`` a test makes (including a rate-limit decorator's counter)
    rolls back with everything else, so it cannot survive into the next
    test -- within a class or across classes. Verified empirically; see the
    regression test at
    ``accounts.tests.test_login_rate_limit.LoginRateLimitIsolationRegressionTest``.

    This guarantee is specific to ``TestCase``'s rollback, not to Django
    tests generally: ``TransactionTestCase`` isolates by flushing tables
    instead, and ``flush`` only truncates tables backed by a Django model.
    The cache table is created via ``createcachetable`` inside a raw
    ``RunPython`` migration (``common/migrations/0001_create_cache_table.py``),
    so it has no model and ``flush`` leaves it alone -- a
    ``TransactionTestCase`` subclass would need to clear the cache itself.
    No test in this repo currently subclasses ``TransactionTestCase``; if one
    starts to and touches a rate-limited endpoint, clear the cache in its own
    ``setUp``.
    """

    def setUp(self):
        self.client = Client()
        # Bypass passphrase gate for all tests by default
        mark_gate_passed(self.client)

    def create_user(self, username="testuser", password=TEST_PASSWORD, **kwargs):
        """Create a user with the given attributes."""
        return User.objects.create_user(username=username, password=password, **kwargs)

    def create_admin(self, username="adminuser", password=TEST_PASSWORD, **kwargs):
        """Create an admin user."""
        return self.create_user(username, password, is_role_admin=True, **kwargs)

    def create_dm(self, username="dmuser", password=TEST_PASSWORD, **kwargs):
        """Create a DM user."""
        return self.create_user(username, password, is_dm=True, **kwargs)

    def create_player(self, username="playeruser", password=TEST_PASSWORD, **kwargs):
        """Create a player user."""
        return self.create_user(username, password, is_player=True, **kwargs)

    def login_dm(self):
        """Create a DM user, log in as them, and return them."""
        dm = self.create_dm()
        self.login_as(dm)
        return dm

    def login_as(self, user, password=TEST_PASSWORD):
        """Login as the given user."""
        self.client.login(username=user.username, password=password)

    def logout(self):
        """Logout but preserve passphrase gate authentication."""
        self.client.logout()
        mark_gate_passed(self.client)

    def post_json(self, url, data):
        """POST JSON data to a URL."""
        return self.client.post(url, json.dumps(data), content_type="application/json")


# --- GM retrieval fakes (issue #325) ---
# A tiny, deterministic "embedding": word counts over a fixed vocabulary, so a
# query about dragons lands on the dragon page and nowhere else. Shared by the
# gm and cli retrieval tests; no test may reach a real embedding model.
FAKE_EMBED_VOCABULARY = ("dragon", "emperor", "sword", "mana")


def fake_embedder(texts: Sequence[str]) -> list[list[float]]:
    """Bag-of-words vectors over :data:`FAKE_EMBED_VOCABULARY`, one per input."""
    vectors: list[list[float]] = []
    for text in texts:
        words = text.lower().split()
        vectors.append([float(words.count(word)) for word in FAKE_EMBED_VOCABULARY])
    return vectors


def write_fake_corpus(root: Path) -> None:
    """Lay down a three-file canon corpus plus a non-canon proposals page."""
    (root / "public-rules" / "combat").mkdir(parents=True)
    (root / "proposals").mkdir()
    (root / "the-world.md").write_text(
        "# The World\n\nA short opening.\n\n"
        "## Dragons\n\nThe dragon sleeps. Every dragon dreams of dragon gold.\n\n"
        "## The Emperor\n\nThe emperor rules. The emperor never sleeps.\n",
        encoding="utf-8",
    )
    (root / "public-rules" / "combat" / "weapons.md").write_text(
        "# Weapons\n\nA sword is a sword. Pick a sword.\n\n"
        "### Two-handed\n\nA bigger sword.\n",
        encoding="utf-8",
    )
    (root / "public-rules" / "magic.md").write_text(
        "# Magic\n\nMana is spent to cast. Mana returns with rest.\n",
        encoding="utf-8",
    )
    (root / "proposals" / "new-idea.md").write_text(
        "# Proposal\n\nA dragon emperor with a mana sword. Not canon.\n",
        encoding="utf-8",
    )


# How long the stand-in below holds a response open for the client to hang
# up before it gives up and closes the connection itself. Only a hang-up that
# failed ever reaches it, and giving up is not recorded as a hang-up, so the
# test that hit it fails rather than hangs (issue #955).
STAND_IN_HOLD_SECONDS = 10.0

# Timeouts for a call to the stand-in. The read outlasts the hold, so a held
# response ends by a hang-up or by the stand-in giving up, never by a read
# timeout racing either one (issue #955).
STAND_IN_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)


class StallingOllama:
    """A local stand-in for Ollama that holds its response open for a hang-up.

    Issue #887: httpx's read timeout counts from each read, so only a real
    socket shows whether a stalled stream is cut off. The server answers
    ``/api/chat`` in up to three stages:

    - with ``prefill``, nothing at all, headers included, until the client
      hangs up. Like Ollama, it sends its headers only with the first token
      (issue #935), and a non-streaming answer's only once it is written
      (issue #936).
    - the headers and ``tokens``, one NDJSON line each.
    - with ``stall``, nothing more until the client hangs up; without it,
      the end of the response.

    No stage waits on the clock (issue #955). A held response ends only when
    the client hangs up (or after :data:`STAND_IN_HOLD_SECONDS`, when it gives
    up), so what a test sees never depends on how quickly a loaded machine
    reaches a point in time. Its events say where it has got to.

    Use as a context manager; ``base_url`` is where it listens. The suite's
    conftest replaces ``ollama_client.open_stream`` for every test, so a test
    that talks to this server patches back the real one, captured at import.

    Attributes:
        requested: Set once a request has been read: the prefill has begun.
        responding: Set once the headers and tokens have gone out.
        hang_ups: The hold each hang-up came in, ``"prefill"`` or ``"stall"``.
        finished: Set once a response has gone out in full.
        closed: Set once a connection's handler is done, so what it recorded
            is final.
    """

    def __init__(
        self, tokens: Sequence[str], *, stall: bool = True, prefill: bool = False
    ):
        self.requested = threading.Event()
        self.responding = threading.Event()
        self.hang_ups: list[str] = []
        self.finished = threading.Event()
        self.closed = threading.Event()
        self.dropping = threading.Event()
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: object) -> None:
                """Keep the test output quiet."""

            def handle(self) -> None:
                try:
                    super().handle()
                finally:
                    stand_in.closed.set()

            def _send_line(self, payload: dict) -> None:
                line = (json.dumps(payload) + "\n").encode()
                self.wfile.write(f"{len(line):x}\r\n".encode() + line + b"\r\n")
                self.wfile.flush()

            def _hold(self, stage: str) -> None:
                """Send nothing until the client hangs up; record it in ``stage``."""
                self.close_connection = True
                self.connection.settimeout(0.02)
                until = time.monotonic() + STAND_IN_HOLD_SECONDS
                while time.monotonic() < until:
                    if stand_in.dropping.is_set():
                        # Hang up mid-response, as a failing server would.
                        return
                    try:
                        if self.connection.recv(1) == b"":
                            break
                    except TimeoutError:
                        continue
                    except OSError:
                        break
                else:
                    # No hang-up came: give up, and record nothing.
                    return
                stand_in.hang_ups.append(stage)

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers["Content-Length"]))
                stand_in.requested.set()
                if prefill:
                    self._hold("prefill")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for token in tokens:
                    self._send_line({"message": {"content": token}, "done": False})
                stand_in.responding.set()
                if stall:
                    self._hold("stall")
                    return
                self._send_line({"message": {"content": ""}, "done": True})
                self.wfile.write(b"0\r\n\r\n")
                stand_in.finished.set()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def drop_connections(self) -> None:
        """Have a stalled response hang up on the client from the server's side."""
        self.dropping.set()

    def __enter__(self) -> "StallingOllama":
        self._thread.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()


# How long a helper thread waits for its event before giving up, and how
# long a test waits for that thread to end.
ACT_ONCE_SET_SECONDS = 5.0


def act_once_set(
    event: threading.Event, action: Callable[[], None]
) -> threading.Thread:
    """Run ``action`` on a thread of its own once ``event`` is set.

    The thread gives up after :data:`ACT_ONCE_SET_SECONDS` without acting, so
    a test whose event never comes fails rather than leaves it waiting.

    Args:
        event: What the thread waits for, such as a stand-in's ``responding``.
        action: What it then does, such as hanging a call up.

    Returns:
        The started thread, for the test to join.
    """

    def act() -> None:
        if event.wait(ACT_ONCE_SET_SECONDS):
            action()

    thread = threading.Thread(target=act)
    thread.start()
    return thread


class _HandFiredTimer:
    """What :class:`HandFiredDeadlines` arms in place of a ``threading.Timer``.

    Attributes:
        interval: The time left the timer was armed with.
        due: The ``time.monotonic()`` it was armed to fire at: the call's
            ``stop_at``, give or take the moment between the two readings.
    """

    def __init__(self, interval: float, function: Callable[[], None]) -> None:
        self.interval = interval
        self.due = time.monotonic() + interval
        self.function = function
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


class HandFiredDeadlines:
    """A call's ``stop_at`` passes when the test says so, not when the clock does.

    The Ollama client arms a ``threading.Timer`` for a call's ``stop_at``.
    A real one fires whenever a loaded machine gets round to it, so a test
    that also waits on the stand-in above to reach a stage is a race between
    the two; it lost under full-suite load (issue #955). Inside this context
    ``threading.Timer`` is replaced, for the whole process, by a timer that
    records what it was armed with and fires only on :meth:`pass_deadline`.
    Nothing but the client arms a ``Timer`` in the code these tests run.

    Give the call a ``stop_at`` well beyond the test's own length, so the
    read timeout the client holds to the time left cannot end the call first.

    Attributes:
        timers: Every timer armed, in order.
    """

    def __init__(self) -> None:
        self.timers: list[_HandFiredTimer] = []
        self._helpers: list[threading.Thread] = []
        self._patch = patch("threading.Timer", self._arm)

    def _arm(self, interval: float, function: Callable[[], None]) -> _HandFiredTimer:
        timer = _HandFiredTimer(interval, function)
        self.timers.append(timer)
        return timer

    def pass_deadline(self) -> None:
        """Fire every timer started and not cancelled, as the clock would."""
        for timer in self.timers:
            if timer.started and not timer.cancelled:
                timer.function()

    def pass_deadline_once(self, event: threading.Event) -> None:
        """Pass the deadline from another thread once ``event`` is set.

        The thread is joined when the context ends, so a test that failed
        leaves nothing behind.
        """
        self._helpers.append(act_once_set(event, self.pass_deadline))

    def __enter__(self) -> "HandFiredDeadlines":
        self._patch.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self._patch.stop()
        for helper in self._helpers:
            helper.join(ACT_ONCE_SET_SECONDS)
