"""Shared logic for the passphrase gate.

speak_friend.PassphraseMiddleware (a git dependency -- see pyproject.toml)
hashes the *configured* passphrase (settings.GATE_PASSPHRASE) exactly as
given, but strips and lowercases every login *attempt* before hashing it for
comparison (see ``PassphraseMiddleware.__call__`` in
``speak_friend/middleware.py``). A submission can therefore only ever match a
passphrase that is already itself stripped and lowercased -- a mixed-case
GATE_PASSPHRASE can never authenticate anyone (tarmar-studio issue #138).

``tarmar_studio/settings.py`` normalizes GATE_PASSPHRASE with
``normalize_passphrase()`` at load time, so the value speak_friend hashes is
already in the only form it can ever match. ``GateRateLimitMiddleware`` (in
this package's ``middleware.py``) uses the same ``normalize_passphrase()`` so
its notion of "correct" always agrees with what speak_friend will actually
accept.

This coupling is load-bearing: it depends on speak_friend's exact
normalize-the-attempt-not-the-config behavior. If that behavior changes
upstream, this function -- and the settings.py checks it powers -- need to
change with it.
"""

import random
from pathlib import Path

# settings.py imports this module while it is itself still loading, so the
# settings object must not be read at import time here. ``django.conf.settings``
# is lazy: importing it costs nothing, and only an attribute read (the one in
# choose_gate_hint, at request time) makes it load, by which point settings.py
# has long finished. That is what makes this import safe in both directions.
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from common.config_text import config_lines


def normalize_passphrase(value: str) -> str:
    """Normalize a passphrase the way speak_friend normalizes login attempts."""
    return value.strip().lower()


# The dev/default passphrase. speak_friend needs *a* configured value, and the
# same one has to be known to the gate middleware's fallback and to the
# deployment verifier that types it into the form, so it is declared here
# rather than three times over. It is public knowledge (it is in this repo)
# and exists only so a local checkout comes up gated but usable; every
# deployment sets GATE_PASSPHRASE. Already in the normalized form
# :func:`normalize_passphrase` produces, so it can never be the value that
# locks a site out (issue #138).
DEV_GATE_PASSPHRASE = "mellon"

# The gate offers a hint once a visitor has guessed wrong
# settings.GATE_HINT_AFTER_WRONG_GUESSES times (issue #55). The evidence,
# stated once here: of the only two outside visitors who found the gate in
# fifteen days, one needed three tries and the other gave up after three wrong
# guesses. The riddle is meant to be answered, not to turn people away.
#
# The hints themselves are configuration, not code: config/gate-hints.txt,
# loaded into settings.GATE_HINTS at startup by load_gate_hints() below. They
# were a tuple here until Spencer ruled otherwise (2026-10-17, issue #77):
# strings that are content belong in configuration, and a literal belongs in
# code only when it is used once. They are snarky on purpose, embarrassing the
# asker for not knowing the lore, and sneaky still: none says the word. Their
# wording is his to change, in that file, with no Python.


def load_gate_hints(path: Path) -> tuple[str, ...]:
    """The gate's hints, read from ``path``.

    One hint per line; blank lines and lines starting with ``#`` are ignored,
    so the file can carry its own header. Called once, by settings.py, so a
    bad file stops the server at startup rather than at a visitor's first
    wrong guess (issue #77): the gate is the first thing an outsider meets,
    and a hint that fails to load there would fail in front of them.

    Args:
        path: The hints file, normally ``config/gate-hints.txt`` or whatever
            ``GATE_HINTS_FILE`` names.

    Returns:
        The hints in file order, stripped, as an immutable tuple.

    Raises:
        ImproperlyConfigured: If the file cannot be read (missing, unreadable,
            or a path through a file rather than a directory) or yields no
            hints.
    """
    try:
        hints = config_lines(path)
    except OSError as error:
        raise ImproperlyConfigured(
            f"GATE_HINTS_FILE {path} cannot be read ({error}); the passphrase "
            "gate needs its hints (issues #55, #77)."
        ) from error
    if not hints:
        raise ImproperlyConfigured(
            f"GATE_HINTS_FILE {path} holds no hints: every line is blank or a "
            "comment (issues #55, #77)."
        )
    return hints


def choose_gate_hint() -> str:
    """One of ``settings.GATE_HINTS``, drawn at random.

    Several angles on one riddle, so a visitor who keeps guessing is shown a
    different way in each time rather than the same sentence again (Spencer,
    2026-10-17, issue #77). ``random`` is the right module here: a hint is
    not a secret, and nothing is protected by which one is drawn.

    Returns:
        One hint, chosen uniformly from ``settings.GATE_HINTS``.
    """
    return random.choice(settings.GATE_HINTS)
