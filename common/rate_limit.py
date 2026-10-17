"""Fixed-window rate-limit counters, atomic at the SQL level (issue #161).

Both of tarmar-studio's own rate limiters -- the passphrase gate
(``common.middleware.GateRateLimitMiddleware``) and the login throttle
(``accounts.views.LoginView``) -- used to keep their counters in
``CACHES["default"]`` (``DatabaseCache``). That backend inherits
``BaseCache.incr()``, which is get-then-set in Python: not atomic, so a
concurrent burst of requests can lose increments and undercount an attacker.
``origami_common.decorators.rate_limit`` has the same shape and is out of
scope here (it lives in a separate library).

This module replaces the cache-based counter with ``common.models.
RateLimitCounter`` and does every increment/decrement through a single
``UPDATE ... SET count = count +/- 1`` (an ``F()`` expression), which the
database executes as one indivisible statement -- there's no read of the
current Python value for a second caller to race against.

The window is fixed, not rolling: ``expires_at`` is set once, when a window
opens (the first charge against a key, or the first charge after the
previous window elapsed), and every charge inside that window updates
``count`` alone. ``cache.set(key, value, window_seconds)`` -- the old
gate-limiter pattern -- pushes the expiry forward on every single call, so
a failure at t=290 relights the clock all the way out to t=590 instead of
the window closing at t=300. ``origami_common`` moved off that pattern for
its own rate_limit decorator (#311); this gives tarmar-studio's two
home-grown limiters the same fix.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone

from common.models import RateLimitCounter

# The guessing budget both of this project's own limiters allow. They guard
# the same kind of thing -- a secret an attacker can only find by trying
# spellings of it (the gate passphrase, a user's password) -- so they have
# always allowed the same ten tries in the same five minutes, and were fixed
# together when that window turned out to be rolling (issue #161). Declared
# here so the pair cannot drift apart by accident; either class may still set
# its own value deliberately by overriding the attribute.
GUESS_MAX_ATTEMPTS = 10
GUESS_WINDOW_SECONDS = 300


def too_many_attempts(
    message: str,
    *,
    window_seconds: int,
    request: HttpRequest | None = None,
    headline: str = "The door has stopped answering.",
    hint: str = "",
) -> HttpResponse:
    """The 429 a spent guessing budget answers with.

    ``Retry-After`` carries the window in seconds, so a client is told when
    the budget reopens rather than left to guess.

    The body used to be that sentence and nothing else -- a bare
    ``HttpResponse``, so the browser drew it in Times New Roman on white with
    no header, no footer, no styling and no link anywhere (issue #697). A
    visitor who mistyped a password a few times was dropped out of the site:
    Back returned to a form that would refuse again, and nothing named the
    wait or pointed at the arena, which needs no account at all. It renders
    through ``templates/429.html`` now, the way the 403 page does, and the
    wait is stated in minutes.

    Args:
        message: What to say in the body.
        window_seconds: The limiter's fixed window, sent as ``Retry-After``
            and shown to the reader rounded up to whole minutes.
        request: The request being refused, when there is one. Only a context
            for the template; the response is the same without it.
        headline: The line above the message, so the gate and the sign-in can
            each say what has stopped answering.
        hint: A line of help shown under the message, or empty for none. The
            gate passes one of ``settings.GATE_HINTS`` (issue #55): a visitor who
            spent ``GUESS_MAX_ATTEMPTS`` guesses on the riddle has not
            understood it, and the page that turns them away is the last
            chance to say so. The login limiter passes nothing; there is no
            hint to give about a password.

    Returns:
        A 429 carrying the rendered page and a ``Retry-After`` header.
    """
    wait_minutes = max(1, -(-window_seconds // 60))
    body = render_to_string(
        "429.html",
        {
            "message": message,
            "headline": headline,
            "wait_minutes": wait_minutes,
            "hint": hint,
        },
        request=request,
    )
    response = HttpResponse(body, status=429)
    response["Retry-After"] = str(window_seconds)
    return response


def current_count(key: str) -> int:
    """The live count for ``key``, or 0 if there is no open window.

    Read-only -- callers use this for the "already at the limit?" check
    that has to happen before deciding whether to charge anything.
    """
    now = timezone.now()
    counter = RateLimitCounter.objects.filter(key=key, expires_at__gt=now).first()
    return counter.count if counter is not None else 0


def charge(key: str, window_seconds: int) -> int:
    """Atomically add one to the fixed-window counter for ``key``.

    Returns the new count. If ``key`` has no open window (never charged, or
    its previous window has elapsed), this call opens a fresh one at count 1
    with a new ``window_seconds`` expiry; otherwise it adds one to the
    existing count and leaves the expiry alone.

    Two callers racing to open the very same window both have to be counted
    -- neither may silently clobber the other's charge. ``get_or_create()``
    is what makes that safe: it tries ``create()`` and, if a concurrent
    caller's row wins the race, catches the resulting ``IntegrityError`` and
    retries as a plain ``get()`` -- so exactly one row is ever created no
    matter how many callers arrive at once, and every caller that lost that
    race still holds a real row to increment via ``F()`` below, rather than
    a set of unconditional field values that would stomp back over it.
    ``select_for_update()`` additionally serializes the "is my row's window
    already closed?" check against any other writer, on backends that
    support row locks (a no-op on SQLite, the test database -- see
    characters/tests/test_locking.py for the project's existing note on
    that gap).
    """
    now = timezone.now()
    with transaction.atomic():
        counter, created = RateLimitCounter.objects.select_for_update().get_or_create(
            key=key,
            defaults={
                "count": 1,
                "expires_at": now + timedelta(seconds=window_seconds),
            },
        )
        if created:
            # Opportunistic cull, piggybacked on the one path that's already
            # paying for a write: unlike DatabaseCache (MAX_ENTRIES=20000,
            # culls itself), this table has no size cap of its own, so a
            # distributed guessing run leaving one expired row per attacker
            # IP would otherwise grow it forever. expires_at is indexed, so
            # this is a plain indexed range delete, not a table scan.
            RateLimitCounter.objects.filter(expires_at__lte=now).delete()
            return 1

        if counter.expires_at <= now:
            # The window we found is closed. Replace it with a fresh one
            # rather than adding to a count that belongs to a guessing run
            # that's already over.
            counter.count = 1
            counter.expires_at = now + timedelta(seconds=window_seconds)
            counter.save(update_fields=["count", "expires_at"])
            return 1

        RateLimitCounter.objects.filter(pk=key).update(count=F("count") + 1)
        return RateLimitCounter.objects.get(pk=key).count


def release(key: str) -> int:
    """Refund one charge against ``key`` (undo a ``charge()``).

    Used by the login limiter, which charges *before* checking the
    password and refunds the charge when the login turns out to be genuine
    -- a real login is not a guess, so it should not count toward the
    brute-force limit. Returns the count remaining; deletes the row and
    returns 0 once nothing is left charged in the window, so a clean login
    leaves no trace of one at all. Never extends or otherwise touches
    ``expires_at`` -- a refund must not affect the window's fixed close.

    A no-op (returns 0) if the window has already elapsed or the count is
    already 0 -- refunding into an expired or empty window has nothing to
    refund.
    """
    now = timezone.now()
    with transaction.atomic():
        updated = RateLimitCounter.objects.filter(
            key=key, expires_at__gt=now, count__gt=0
        ).update(count=F("count") - 1)
        if not updated:
            return 0
        remaining = RateLimitCounter.objects.get(pk=key).count
        if remaining <= 0:
            RateLimitCounter.objects.filter(pk=key).delete()
            return 0
        return remaining
