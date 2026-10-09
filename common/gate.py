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
