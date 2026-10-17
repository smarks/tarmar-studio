"""Movement gaits and the movement modifier (issues #143 and #75).

``reference/content/public-rules/combat/action-options/movement.md`` defines:

    Movement Modifier: CON modifier + STR modifier + DEX modifier

    Sprint  18 + modifier
    Run     12 + modifier
    Jog      7 + modifier
    Walk     4 + modifier

("Walk (slow)" and "Stand Still" are fixed distances with no modifier and are
not gaits in the sense used here.)

The gaits and their base distances are the loaded canon's
``equipment-rules.json`` (:attr:`characters.equipment_rules.EquipmentRules.gaits`,
issue #75), read when a function here runs: their names are the canon's, and
the code reads them by order, fastest first, never by name. The default
canon's are the table above, which ``MovementGaitRuleTest``
(characters/tests/test_rules_consistency.py) holds it to. The movement
modifier's shape, CON + STR + DEX, and the floor at 0 stay code.

The attribute modifiers themselves come from ``characters.skills.
calculate_attribute_modifier`` -- the same table used everywhere else an
attribute becomes a bonus/penalty.

Encumbrance's own movement penalty further reduces every gait's distance, and
encumbrance and armour restrictions (``characters.equipment.
combine_movement_restrictions``) can forbid a gait outright regardless of the
computed number -- a restricted gait has no distance at all, not a reduced
one. A level with a walk limit (the default canon's Overloaded: "Walk only, 1
hex max") caps the slowest gait at that many hexes instead of its formula.

``characters.models.Character`` wires the pieces above together (effective
attributes, encumbrance, armor) and exposes the result as sheet-ready
properties; this module holds the pure math so it can be tested and read
without a database.
"""

from collections.abc import Collection

from .equipment_rules import equipment_rules


def movement_modifier(con_modifier: int, str_modifier: int, dex_modifier: int) -> int:
    """Movement Modifier: CON modifier + STR modifier + DEX modifier."""
    return con_modifier + str_modifier + dex_modifier


def gait_distance(gait: str, modifier: int, movement_penalty: int = 0) -> int:
    """Hex distance for one gait: base + movement modifier + movement penalty.

    The base is the loaded canon's for ``gait``. Floored at 0 -- a character
    never moves a negative distance no matter how unfavorable the combined
    modifier is.

    Raises:
        KeyError: ``gait`` is not one of the loaded canon's gaits.
    """
    base_distance = equipment_rules().gaits.base_distances[gait]
    return max(0, base_distance + modifier + movement_penalty)


def compute_gaits(
    modifier: int,
    movement_penalty: int,
    restrictions: Collection[str],
    walk_limit: int | None = None,
) -> dict[str, int | None]:
    """Map each gait, fastest first, to its hex distance, or ``None`` if unusable.

    A gait forbidden by ``restrictions`` (from encumbrance or worn armor) maps
    to ``None`` -- restricted gaits have no distance to show, "n/a" on the
    sheet, not a reduced number. ``walk_limit`` overrides the slowest gait's
    computed number when given (Overloaded's "Walk only, 1 hex max"); it takes
    priority over the formula but not over a restriction, since the slowest
    gait is never one a level or an armour forbids, only capped.
    """
    gaits = equipment_rules().gaits
    distances: dict[str, int | None] = {}
    for gait in gaits.order:
        if gait in restrictions:
            distances[gait] = None
        elif gait == gaits.slowest and walk_limit is not None:
            distances[gait] = walk_limit
        else:
            distances[gait] = gait_distance(gait, modifier, movement_penalty)
    return distances
