"""The identity an unauthenticated arena visitor carries (issue #305).

The arena is playable without an account, so something other than a
``User`` has to answer "is this the person who made that battle?". That
something is a *guest key*: an opaque random token minted on demand and
kept in the visitor's session.

The key is deliberately NOT the session key itself. A session key rotates
(``login()`` cycles it, and so does any session flush), and it is a
credential for the whole site — writing it into a database column would
put a live credential in a row that gets joined, logged and dumped. A
separate token can be stored, indexed and compared freely, and it survives
the login rotation because Django's ``login()`` preserves session *data*
across ``cycle_key()`` — which is exactly what lets a guest's battles be
adopted into the account they register (``battle.adoption``).

What a guest key does NOT do is authenticate. It bounds *control* of rows
the same visitor created (their own battle, their own seat) and nothing
else: every read widening for anonymous visitors is decided by
``battle.access``, on the content of the battle, never on possession of a
key. Losing the key — a cleared cookie, an expired session — therefore
demotes a guest to a spectator of their own battle rather than locking
anyone out of anything they could otherwise reach.
"""

from __future__ import annotations

import secrets

from django.http import HttpRequest

#: Session field the guest key lives in.
GUEST_KEY_SESSION_FIELD = "arena_guest_key"

#: Bytes of entropy per key. 16 bytes (~22 url-safe characters) is the
#: same order as Django's own session keys and far past guessing range for
#: a token that only ever buys control of a throwaway battle.
GUEST_KEY_BYTES = 16


def peek_guest_key(request: HttpRequest) -> str:
    """This visitor's guest key, or ``""`` if they have never needed one.

    Read-only: safe on GET requests that must not write a session cookie
    (a crawler hitting the battle list should not be issued anything).
    """
    return request.session.get(GUEST_KEY_SESSION_FIELD, "") or ""


def ensure_guest_key(request: HttpRequest) -> str:
    """This visitor's guest key, minting and storing one if there is none.

    Called only where the visitor is actually taking ownership of
    something — opening a draft, claiming a seat — so browsing never
    issues a session cookie to someone who only looked.
    """
    key = peek_guest_key(request)
    if not key:
        key = secrets.token_urlsafe(GUEST_KEY_BYTES)
        request.session[GUEST_KEY_SESSION_FIELD] = key
    return key


def clear_guest_key(request: HttpRequest) -> None:
    """Forget the guest key (after its rows have been adopted by an account)."""
    request.session.pop(GUEST_KEY_SESSION_FIELD, None)
