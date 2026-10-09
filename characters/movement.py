"""Movement gaits and the movement modifier (issue #143).

``reference/content/public-rules/combat/action-options/movement.md`` defines:

    Movement Modifier: CON modifier + STR modifier + DEX modifier

    Sprint  18 + modifier
    Run     12 + modifier
    Jog      7 + modifier
    Walk     4 + modifier

("Walk (slow)" and "Stand Still" are fixed distances with no modifier and are
not gaits in the sense used here.)

The attribute modifiers themselves come from ``characters.skills.
calculate_attribute_modifier`` -- the same table used everywhere else an
attribute becomes a bonus/penalty.

Encumbrance's own movement penalty (``characters.equipment.
get_movement_penalty``) further reduces every gait's distance, and
encumbrance/armor restrictions (``characters.equipment.
get_movement_restrictions`` / ``get_armor_movement_restrictions``) can forbid
a gait outright regardless of the computed number -- a restricted gait has no
distance at all, not a reduced one. The Overloaded tier ignores the Walk
formula entirely and caps it at ``characters.equipment.
ENCUMBRANCE_LEVELS["overloaded"]["walk_limit"]`` hexes: "Walk only, 1 hex max".

``characters.models.Character`` wires the pieces above together (effective
attributes, encumbrance, armor) and exposes the result as sheet-ready
properties; this module holds the pure math so it can be tested and read
without a database.
"""

# Base hex distance for each gait before the movement modifier is added.
# Fastest to slowest, matching the order of the rules table.
# MovementGaitRuleTest (characters/tests/test_rules_consistency.py) ties these
# numbers to movement.md so an edit to one without the other is caught.
GAIT_BASE_DISTANCES: dict[str, int] = {
    "sprint": 18,
    "run": 12,
    "jog": 7,
    "walk": 4,
}

# Canonical gait order, fastest to slowest.
GAIT_ORDER = ("sprint", "run", "jog", "walk")


def movement_modifier(con_modifier: int, str_modifier: int, dex_modifier: int) -> int:
    """Movement Modifier: CON modifier + STR modifier + DEX modifier."""
    return con_modifier + str_modifier + dex_modifier


def gait_distance(gait: str, modifier: int, movement_penalty: int = 0) -> int:
    """Hex distance for one gait: base + movement modifier + movement penalty.

    Floored at 0 -- a character never moves a negative distance no matter how
    unfavorable the combined modifier is.
    """
    return max(0, GAIT_BASE_DISTANCES[gait] + modifier + movement_penalty)


def compute_gaits(
    modifier: int,
    movement_penalty: int,
    restrictions: list[str],
    walk_limit: int | None = None,
) -> dict[str, int | None]:
    """Map each gait to its hex distance, or ``None`` if the gait is unusable.

    A gait forbidden by ``restrictions`` (from encumbrance or worn armor) maps
    to ``None`` -- restricted gaits have no distance to show, "n/a" on the
    sheet, not a reduced number. ``walk_limit`` overrides Walk's computed
    number when given (Overloaded's "Walk only, 1 hex max"); it takes
    priority over the formula but not over a restriction, since Walk itself
    is never one of the restricted gaits, only capped.
    """
    gaits: dict[str, int | None] = {}
    for gait in GAIT_ORDER:
        if gait in restrictions:
            gaits[gait] = None
        elif gait == "walk" and walk_limit is not None:
            gaits[gait] = walk_limit
        else:
            gaits[gait] = gait_distance(gait, modifier, movement_penalty)
    return gaits
