"""What bounds a guest's writes to the arena (issue #305).

Battle creation used to be DM-only, so it was bounded by the account list.
Opening the arena makes it an unauthenticated write, and an unauthenticated
write needs its own ceiling. Two of them, because they stop different
things:

- **A rate**, per client IP: how fast new battles may be started from one
  place. This is the one that stops a script. It is a fixed window on
  ``common.rate_limit`` — the same atomic counter the passphrase gate and
  the login throttle use, so there is one rate-limit mechanism in the
  project rather than three.
- **A live cap**, per guest session: how many unfinished battles one
  visitor may have going at once. This is the one that stops the slow
  accumulation a rate limit permits, and it is what the issue asks for by
  name. It counts only *unfinished* battles from the last
  ``GUEST_LIVE_WINDOW_HOURS``, so it drains on its own: a guest who
  abandons three battles is not shut out of the arena forever, and one who
  finishes their battles is never blocked at all.

Both are asked as one question — :func:`guest_creation_blocker` — which
returns the sentence to show the visitor, or ``None``. The battle list
renders the disabled button from that same sentence and the create endpoint
refuses on it, so what the page says and what the server does cannot drift.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from common.helpers import client_ip
from common.rate_limit import charge, current_count

from .models import Battle, BattleDraft

#: How many battles one IP may start per window, and how long the window is.
GUEST_BATTLES_PER_WINDOW = 5
GUEST_BATTLE_WINDOW_SECONDS = 60 * 60

#: How many unfinished battles one guest session may hold, and how far back
#: "unfinished" is counted.
GUEST_LIVE_BATTLE_CAP = 3
GUEST_LIVE_WINDOW_HOURS = 24

#: The statuses that still count as a battle in progress.
LIVE_STATUSES = (
    Battle.STATUS_LOBBY,
    Battle.STATUS_SETUP,
    Battle.STATUS_PAUSED,
    Battle.STATUS_AWAITING,
)


#: The start of every guest-battle rate key; the client IP follows it. The
#: e2e suite resets these keys between guest tests (issue #882), since every
#: Playwright page is 127.0.0.1.
GUEST_RATE_KEY_PREFIX = "rate_limit:guest-battle:"


def _rate_key(request) -> str:
    return f"{GUEST_RATE_KEY_PREFIX}{client_ip(request)}"


def live_guest_battles(guest_key: str) -> int:
    """Unfinished battles this guest session holds inside the live window."""
    if not guest_key:
        return 0
    since = timezone.now() - timedelta(hours=GUEST_LIVE_WINDOW_HOURS)
    battles = Battle.objects.filter(
        guest_key=guest_key, status__in=LIVE_STATUSES, created_at__gte=since
    ).count()
    drafts = BattleDraft.objects.filter(
        guest_key=guest_key, created_by__isnull=True, created_at__gte=since
    ).count()
    return battles + drafts


def guest_creation_blocker(request, guest_key: str) -> str | None:
    """Why this guest may not open a new battle right now, or ``None``.

    Read-only — asking never charges anything. :func:`charge_guest_creation`
    is the write, and it happens once a battle actually opens.
    """
    if live_guest_battles(guest_key) >= GUEST_LIVE_BATTLE_CAP:
        return (
            f"You already have {GUEST_LIVE_BATTLE_CAP} battles going as a "
            "guest. Finish one — or sign in, and your battles are kept "
            "under your account instead of counted against this limit."
        )
    if current_count(_rate_key(request)) >= GUEST_BATTLES_PER_WINDOW:
        return (
            f"That is {GUEST_BATTLES_PER_WINDOW} new battles in an hour from "
            "this connection, which is as many as guests get. Try again "
            "later, or sign in."
        )
    return None


def charge_guest_creation(request) -> None:
    """Count one guest battle against this IP's window."""
    charge(_rate_key(request), GUEST_BATTLE_WINDOW_SECONDS)
