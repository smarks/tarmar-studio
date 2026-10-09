import functools
import json
import threading
import time
from collections.abc import Callable, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest import skip

from django.test import Client, TestCase

from accounts.models import User
from common.middleware import PublicPathPassphraseMiddleware
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


class StallingOllama:
    """A local stand-in for Ollama whose stream stalls after its first tokens.

    Issue #887: httpx's read timeout counts from each read, so only a real
    socket shows whether a stalled stream is cut off at a deadline. The
    server answers ``/api/chat`` with ``tokens`` (one NDJSON line each), then
    either finishes (``stall_seconds`` of 0) or sends nothing for
    ``stall_seconds`` while watching for the client to hang up, and records
    when it did.

    Use as a context manager; ``base_url`` is where it listens, and
    ``real_stream`` must be the unpatched ``httpx.stream`` (the suite's
    conftest replaces it for every test, so a test module captures it at
    import).
    """

    def __init__(
        self,
        tokens: Sequence[str],
        *,
        stall_seconds: float,
        first_token_after: float = 0.0,
    ):
        self.hung_up_at: list[float] = []
        self.finished = threading.Event()
        self.dropping = threading.Event()
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: object) -> None:
                """Keep the test output quiet."""

            def _send_line(self, payload: dict) -> None:
                line = (json.dumps(payload) + "\n").encode()
                self.wfile.write(f"{len(line):x}\r\n".encode() + line + b"\r\n")
                self.wfile.flush()

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                time.sleep(first_token_after)
                for token in tokens:
                    self._send_line({"message": {"content": token}, "done": False})
                if not stall_seconds:
                    self._send_line({"message": {"content": ""}, "done": True})
                    self.wfile.write(b"0\r\n\r\n")
                    stand_in.finished.set()
                    return
                self.connection.settimeout(0.02)
                until = time.monotonic() + stall_seconds
                while time.monotonic() < until:
                    if stand_in.dropping.is_set():
                        # Hang up mid-response, as a failing server would.
                        self.close_connection = True
                        return
                    try:
                        if self.connection.recv(1) == b"":
                            break
                    except TimeoutError:
                        continue
                    except OSError:
                        break
                else:
                    stand_in.finished.set()
                    return
                stand_in.hung_up_at.append(time.monotonic())

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
