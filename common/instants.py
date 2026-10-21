"""Instants written into a downloaded file, with their zone named (#123).

A file a visitor downloads -- a character export, a GM session transcript --
is read away from the page, so the browser's local-time pass
(``static/js/local-time.js``) never reaches it. Every instant such a file
carries is written here: an aware value in the active zone (``TIME_ZONE``,
UTC, unless a request activated another), with that zone's name after it,
so a reader cannot take it for their own clock.
"""

from __future__ import annotations

import datetime

from django.utils import timezone

#: How a file writes an instant: date, clock and the zone's name.
LABELLED_INSTANT_FORMAT = "%Y-%m-%d %H:%M:%S %Z"


def labelled_instant(value: datetime.datetime | None = None) -> str:
    """``value`` in the active zone, with the zone named.

    Args:
        value: An aware instant; ``None`` is now.

    Returns:
        The instant as :data:`LABELLED_INSTANT_FORMAT` writes it, for
        example ``"2026-10-20 23:40:00 UTC"``.

    Raises:
        ValueError: ``value`` is naive, so names no instant; Django's
            ``localtime`` refuses it.
    """
    return timezone.localtime(value).strftime(LABELLED_INSTANT_FORMAT)
