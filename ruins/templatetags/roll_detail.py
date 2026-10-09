"""Template tag: the die-and-face trace after a generated result (#338 item 8).

The ruin and hook generators show which die was rolled and what came up next
to each table result, as ``<span class="roll-detail">d8 → 5</span>``. That
span was written out six times. ``{% roll_detail roll %}`` is it, once.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django import template
from django.utils.html import format_html
from django.utils.safestring import SafeString

register = template.Library()


@register.simple_tag
def roll_detail(roll: Mapping[str, Any] | Any) -> SafeString:
    """Render the die-and-face trace for one table roll.

    Args:
        roll: A generated roll, as the generators' results carry it: a mapping
            (or object) with ``die`` (such as ``"d8"``) and ``face`` (the value
            that came up).

    Returns:
        ``<span class="roll-detail">die → face</span>``, both values escaped.
    """
    if isinstance(roll, Mapping):
        die, face = roll.get("die", ""), roll.get("face", "")
    else:
        die, face = getattr(roll, "die", ""), getattr(roll, "face", "")
    return format_html('<span class="roll-detail">{} &rarr; {}</span>', die, face)
