"""Template filters for experience-log display: survival rolls, track labels."""

from django import template

from characters.skills import track_label as track_label_for

register = template.Library()

# What the natural-roll overrides are called, by how many dice were rolled.
# The survival roll is 3d6 since issue #223; checks stored while it was 2d6
# still hold two dice, and they render as what they actually were -- a legacy
# [1, 1] is snake eyes, never relabelled as triple ones.
NATURAL_ROLL_NAMES: dict[int, tuple[str, str]] = {
    2: ("snake eyes", "boxcars"),
    3: ("triple ones", "triple sixes"),
}


def format_modifier(modifier: int) -> str:
    """Render a roll modifier with its own sign: ``+ 2``, ``- 2``, ``+ 0``.

    A malus is a subtraction, so it reads as one. Printing every modifier as
    ``+ {value}`` turned a penalty into the nonsense "+ -2" (issue #222).
    """
    sign = "-" if modifier < 0 else "+"
    return f"{sign} {abs(modifier)}"


@register.filter
def signed(value: int) -> str:
    """A modifier as the sheet's stat tiles show it: ``+2``, ``+0``, ``-3``.

    character_edit.html wrote ``{% if x >= 0 %}+{% endif %}{{ x }}`` out for
    each of its five signed readouts (#338 item 22). api.js's formatSigned is
    this filter's twin for the values character-edit.js repaints, so a tile
    reads the same before and after a live update.
    """
    return f"+{value}" if value >= 0 else str(value)


@register.filter
def survival_roll(check: dict) -> str:
    """Format a survival-check dict as ``3d6 [d1, d2, d3] + mod = total vs target``.

    The one place the survival-roll string is built (issue #175): the per-year
    experience history lines and the death card's fatal roll both come through
    here, and ``static/js/character-edit.js`` mirrors the format for the
    live-append path. The dice notation comes from the stored dice, so a check
    recorded under the old 2d6 rule still renders truthfully as 2d6.

    Args:
        check: A ``survival_check`` dict as ``simulate_experience_years``
            stores it (``dice``, ``modifier``, ``total``, ``target``).

    Returns:
        The formatted roll string, or an empty string for a missing check
        (legacy log entries predating per-year checks).
    """
    if not check:
        return ""
    faces = list(check["dice"])
    dice = ", ".join(str(face) for face in faces)
    formatted = (
        f"{len(faces)}d6 [{dice}] {format_modifier(check['modifier'])} "
        f"= {check['total']} vs {check['target']}"
    )
    # The lowest natural roll kills and the highest always survives regardless
    # of the target (see simulate_experience_years); without saying so, a fatal
    # roll that beats the target reads like the game cheated.
    death_name, survival_name = NATURAL_ROLL_NAMES.get(
        len(faces), ("the lowest roll", "the highest roll")
    )
    if check.get("auto_death"):
        return f"{formatted} ({death_name} — always fatal)"
    if check.get("auto_success"):
        return f"{formatted} ({survival_name} — always survives)"
    return formatted


@register.filter
def track_label(track: str) -> str:
    """Human label for an experience track value ("civil_service" -> "Civil Service")."""
    return track_label_for(track)
