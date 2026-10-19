"""Template filters for a Game Master's rulings on a battle (issue #37)."""

from django import template

from battle.magic_bounds import event_past_by_ruling

register = template.Library()


@register.filter
def past_by_ruling(payload: dict | None) -> str:
    """Beneath a cast or counter that went past a lifted bound, the words for
    it (:func:`battle.magic_bounds.event_past_by_ruling`); ``""`` otherwise."""
    return event_past_by_ruling(payload)
