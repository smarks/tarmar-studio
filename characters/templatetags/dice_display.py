"""Template filters for experience-log display: survival rolls, track labels."""

from django import template

from characters.skill_rules import AGING_ATTRIBUTES
from characters.skills import survival_dice_expression
from characters.skills import track_label as track_label_for
from common.rolling import dice_total_range

register = template.Library()

# What the dice's lowest and highest totals are called, by the dice rolled. The
# survival roll is 3d6 since issue #223; checks stored while it was 2d6 still
# hold two dice, and they render as what they actually were -- a legacy [1, 1]
# is snake eyes, never relabelled as triple ones. A canon's automatic totals
# need not be the dice's ends (issue #70): a natural that is neither, or the
# ends of dice not named here, is said as the number it was.
NATURAL_ROLL_NAMES: dict[str, tuple[str, str]] = {
    "2d6": ("snake eyes", "boxcars"),
    "3d6": ("triple ones", "triple sixes"),
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
    expression = survival_dice_expression(check)
    dice = ", ".join(str(face) for face in check["dice"])
    formatted = (
        f"{expression} [{dice}] {format_modifier(check['modifier'])} "
        f"= {check['total']} vs {check['target']}"
    )
    # The automatic death total kills and the automatic survival total always
    # survives regardless of the target (see simulate_experience_years);
    # without saying so, a fatal roll that beats the target reads like the
    # game cheated.
    if check.get("auto_death"):
        return f"{formatted} ({_natural_name(expression, check, 0)} — always fatal)"
    if check.get("auto_success"):
        return f"{formatted} ({_natural_name(expression, check, 1)} — always survives)"
    return formatted


def _natural_name(expression: str, check: dict, end: int) -> str:
    """What an automatic survival roll is called: its faces' name, or its total.

    Args:
        expression: The dice the check was rolled on, e.g. ``"3d6"``.
        check: The stored survival check, whose ``roll`` is the dice total.
        end: 0 for the dice's lowest total (death), 1 for the highest.

    Returns:
        "triple ones" and the like when the roll is that end of dice named in
        :data:`NATURAL_ROLL_NAMES`, otherwise "a natural N".
    """
    roll = check.get("roll")
    names = NATURAL_ROLL_NAMES.get(expression)
    if roll is None:  # an entry stored without its total: name the end
        return (names or ("the lowest roll", "the highest roll"))[end]
    if names is not None and roll == dice_total_range(expression)[end]:
        return names[end]
    return f"a natural {roll}"


@register.filter
def track_label(track: str) -> str:
    """Human label for an experience track value ("civil_service" -> "Civil Service")."""
    return track_label_for(track)


@register.filter
def in_aging_order(penalties: dict[str, int]) -> list[tuple[str, int]]:
    """A run's aging penalties in the aging table's order (STR, DEX, INT, WIS, CON).

    The experience log stores each run's totals as a dict, whose key order
    depended on the stage until issue #70: a run past the table stored STR,
    DEX, CON, INT, WIS. Printing them in one fixed order makes every run, old
    or new, read the way the workbench and the table do. Any other key a
    legacy entry holds comes after, as stored.

    Args:
        penalties: A run's ``aging`` totals, by attribute field name.
    """
    ordered = [
        (name, penalties[name]) for name in AGING_ATTRIBUTES if name in penalties
    ]
    return ordered + [
        (name, value)
        for name, value in penalties.items()
        if name not in AGING_ATTRIBUTES
    ]
