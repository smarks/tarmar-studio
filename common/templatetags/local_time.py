"""Template tag: render a timestamp for progressive-enhancement local time.

Every user-facing timestamp on the site (issue #225) -- "Signed in since" in
the header, character created/deleted dates, battle listings, user
last-login -- is a real instant: a ``DateTimeField`` (``last_login``,
``created_at``, ``updated_at``, ``deleted_at``), stored and rendered
server-side in UTC. Django's ``TIME_ZONE`` stays ``"UTC"`` (settings.py) so
the database, the ORM, and any log line agree on one clock. But a visitor
reading "Signed in since ... 14:32" wants their own local time, not the
server's.

``{% local_time %}`` is the server-side half of that: it renders a
``<time datetime="...full ISO-8601 UTC...">`` element whose visible text is
the UTC value formatted per ``format_string`` (Django's ``date`` filter
syntax) -- a complete, correctly labeled fallback if JavaScript never runs.
``static/js/local-time.js`` is the other half: on load, it rewrites every
such element's text to the viewer's own local zone via
``Intl.DateTimeFormat``.

Even the date-only call sites (``character_list.html``, ``character_edit.
html``) route through this tag rather than being left alone: ``created_at``
and ``deleted_at`` are moments, not calendar dates, so a character created
just after UTC midnight can read as "yesterday" for a visitor west of UTC --
that shift matters regardless of whether the display shows a time of day.
``style="date"`` keeps the *display* date-only (no viewer-local clock time
shown) while still moving the calendar date itself into the viewer's zone.
"""

from __future__ import annotations

import datetime

from django import template
from django.template.defaultfilters import date as format_date
from django.utils.html import format_html
from django.utils.timezone import is_naive, make_aware

register = template.Library()


@register.simple_tag
def local_time(
    value: datetime.datetime | None,
    format_string: str = "M d, Y H:i",
    style: str = "datetime",
) -> str:
    """Render ``value`` as a ``<time>`` element the browser reformats locally.

    Args:
        value: The instant to render. Naive values are treated as UTC (this
            project runs with ``USE_TZ = True`` and ``TIME_ZONE = "UTC"``, so
            in practice every value reaching this tag is already aware --
            the naive branch is a defensive fallback, not the expected path).
        format_string: A Django ``date`` filter format string, used for the
            UTC fallback text shown when JavaScript never runs. Match
            whatever the call site rendered directly before adopting this
            tag, so the no-JS experience is unchanged.
        style: ``"datetime"`` (default) or ``"date"``. Read by
            ``static/js/local-time.js`` to decide whether the viewer-local
            re-render includes a time of day. ``"date"`` still shifts the
            calendar date into the viewer's zone -- it only suppresses the
            clock-time portion of the *enhanced* display.

    Returns:
        Safe HTML for one ``<time class="local-time" datetime="...">``
        element, or ``""`` for a ``None`` value (mirrors how
        ``{{ value|date:"..." }}`` on ``None`` silently renders nothing).
    """
    if value is None:
        return ""
    if is_naive(value):
        value = make_aware(value, datetime.UTC)
    return format_html(
        '<time class="local-time" datetime="{}" data-local-time-style="{}">{}</time>',
        value.isoformat(),
        style,
        format_date(value, format_string),
    )
