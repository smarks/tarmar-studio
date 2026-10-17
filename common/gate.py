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

# The hints the gate offers once a visitor has guessed wrong
# GATE_HINT_AFTER_WRONG_GUESSES times (issue #55). The evidence, stated once
# here: of the only two outside visitors who found the gate in fifteen days,
# one needed three tries and the other gave up after three wrong guesses. The
# riddle is meant to be answered, not to turn people away. Each hint is sneaky
# (Spencer, 2026-10-16): none says the word. Each names the tongue or the
# door, because the single hint that preceded these, "in its own tongue", told
# nobody which tongue was meant (Spencer, 2026-10-17, issue #77). No hint
# carries an apostrophe or a quotation mark: Django autoescapes both, and the
# tests compare the rendered text with these literals. The wording of every
# one stays Spencer's to change. They are kept beside the dev passphrase
# because both describe the one door.
GATE_HINTS: tuple[str, ...] = (
    "The inscription you are reading is a translation. The door was made by Dwarves and lettered by Elves, and it answers only to Elvish.",
    "Gandalf sat before a door like this one for longer than he liked. The answer was in the question all along.",
    "Merry asked what the inscription meant by speak, friend, and enter. His question was the whole key.",
    "The door has already said the word. It wants it in Elvish.",
    "This is not a password. It is the riddle from the gates of Moria, and it has been answered before.",
    "Say friend, as a Grey-elf would have said it.",
)


def choose_gate_hint() -> str:
    """One of ``GATE_HINTS``, drawn at random.

    Several angles on one riddle, so a visitor who keeps guessing is shown a
    different way in each time rather than the same sentence again (Spencer,
    2026-10-17, issue #77). ``random`` is the right module here: a hint is
    not a secret, and nothing is protected by which one is drawn. The wording
    of every hint stays Spencer's to change.

    Returns:
        One hint, chosen uniformly from ``GATE_HINTS``.
    """
    return random.choice(GATE_HINTS)


# How many wrong guesses in a window before the hint is shown. Three, because
# that is where both visitors in the evidence above stood when one of them
# walked away. It must stay well under common.rate_limit.GUESS_MAX_ATTEMPTS so
# the hint arrives before the lockout does; a hint that only ever appears on
# the 429 page helps nobody still at the door. rate_limit is not imported here
# to say so in code: tarmar_studio/settings.py and scripts/verify_deployment.py
# import this module before Django's apps are loaded, and rate_limit imports
# common.models, which cannot be imported that early. middleware.py imports
# both and is where the two meet.
GATE_HINT_AFTER_WRONG_GUESSES = 3
