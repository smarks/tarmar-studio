"""Project-wide pytest configuration.

Two things here: the unit suite must not talk to Ollama, and it must not
inherit the git or deploy variables of whatever process started it.
"""

import os

import httpx
import pytest

from common.services import ollama_client

UNREACHABLE = "Ollama is not reachable from the unit suite"

INHERITED_VARIABLE_PREFIXES: tuple[str, ...] = ("GIT_", "TARMAR_")

# Inputs a run is meant to inherit despite the prefix. ``TARMAR_CANON_DIR``
# chooses which canon every dm-rules reader serves (issue #917); the suite has
# to be runnable against an outside canon, so it is configuration, not a stray
# deploy variable.
PRESERVED_VARIABLE_NAMES: frozenset[str] = frozenset({"TARMAR_CANON_DIR"})


def remove_inherited_environment_variables() -> list[str]:
    """Delete every inherited ``GIT_*`` and ``TARMAR_*`` name from ``os.environ``.

    Names in :data:`PRESERVED_VARIABLE_NAMES` are kept.

    Returns:
        The names removed, in the order ``os.environ`` listed them.
    """
    removed_variable_names = [
        name
        for name in list(os.environ)
        if name.startswith(INHERITED_VARIABLE_PREFIXES)
        and name not in PRESERVED_VARIABLE_NAMES
    ]
    for variable_name in removed_variable_names:
        del os.environ[variable_name]
    return removed_variable_names


# Done at import as well as in the fixture below, because conftest is imported
# before collection imports any test module, and so before a module-level
# statement in one of them could start a subprocess.
remove_inherited_environment_variables()


@pytest.fixture(autouse=True, scope="session")
def without_inherited_environment_variables() -> None:
    """Keep the surrounding process's git and deploy variables out of the suite.

    Git exports ``GIT_DIR`` to its hooks -- and ``GIT_INDEX_FILE``,
    ``GIT_WORK_TREE`` and ``GIT_PREFIX`` from a linked worktree -- so the suite
    the pre-commit hook runs inherits them, as does every subprocess a test
    starts. They take precedence over both ``git -C <dir>`` and the process's
    working directory, so a throwaway repository built under ``tmp_path`` acts
    on the repository being committed instead. On 2026-09-25 that put
    ``core.bare=true`` and a test identity into Spencer's own checkout, staged a
    stub ``manage.py`` in its index, and authored six real commits as the
    identity ``scripts/test_gate.py`` gives its temporary checkout (#725).

    ``TARMAR_*`` goes for the second reason ``deploy.sh`` gave: it runs this
    suite as its verification step, having re-executed itself with
    ``TARMAR_DEPLOY_PULLED=1`` and ``TARMAR_DEPLOY_LOCKED=1`` exported, and
    inherited those told a throwaway deploy it had already pulled (#576). The
    other ``TARMAR_*`` names ``deploy.sh`` and the settings read -- profile,
    app directory, dotenv path -- are inputs the tests set for themselves, so
    clearing an ambient one only makes a run more repeatable.

    Three modules used to carry a copy of this fixture each, added one at a
    time as each was bitten. Nothing is restored afterwards: the variables were
    never meant for this process, and a session fixture's teardown runs after
    the last test anyway.
    """
    remove_inherited_environment_variables()


def _refuse(*args, **kwargs):
    """Stand in for an outbound Ollama call the way an absent server would."""
    raise httpx.ConnectError(UNREACHABLE)


@pytest.fixture(autouse=True)
def ollama_is_unreachable(monkeypatch):
    """Fail every un-mocked Ollama call immediately instead of over 10 seconds.

    A GM turn used to make a second, structured call through
    `ollama_client.chat` on every `/tarmar/gm/stream/` request. The GM view
    tests mocked `stream_chat` for the reply but not that second call, so
    roughly thirty of them were opening a real socket to
    `localhost:11434` and sitting on `CHAT_TIMEOUT`'s ten-second connect
    budget before the caller's `except Exception` swallowed the failure. Ten
    seconds each is most of the suite's wall time.

    Worse than slow, it made the result depend on the machine: a developer
    with Ollama running got a live model deciding what the ledger recorded,
    where CI got a refused connection. Same test, two different code paths.

    Patching `httpx` inside the client (rather than `chat`/`stream_chat`
    themselves) keeps every existing mock working: the client's own tests
    patch `common.services.ollama_client.httpx.stream` and `.post`, and any
    test that mocks a level above this never reaches it. A test that wants a
    live call back can monkeypatch these two names itself.
    """
    monkeypatch.setattr(ollama_client.httpx, "post", _refuse)
    monkeypatch.setattr(ollama_client.httpx, "stream", _refuse)
