"""d20 combat resolution — thin re-export of the shared ``tarmar_rules`` core.

The d20 attack-resolution system specified in
``reference/content/proposals/d20-combat-resolution-spec.md`` lives in the
shared ``tarmar_rules`` package (https://github.com/smarks/tarmar-rules,
pinned in ``pyproject.toml``), which ``melee`` also consumes for its Tarmar
profile — one source, so the two games cannot drift. The extraction happened
in #239 for the primitives the package then implemented faithfully, and
completed in #243 once tarmar-rules v0.3.0 caught up to the #119 skill-bonus
ruling and the integrated §7 resolution from #105/#101.

What remains genuinely local:

1. **Catalog adapters** — :func:`weapon_skill_bonus` and :func:`to_hit_bonus`
   speak this repo's weapon ids; they resolve ids to ``weapon_class`` strings
   and Unusual flags via ``characters.equipment`` and delegate the math. One
   catalog-semantic edge lives here by design: two *different* unclassed
   items share no training, which the package (having no catalog) cannot
   know.
(The **§8 Hybrid-armour display helpers** — :func:`hybrid_bypass_applies`
and :func:`applied_armour_stops`, decompositions of ``damage_after_armour``
the attack panel reads to *show* the rule — used to be the second local
item; #240 milestone 2 folded the duplicate onto ``tarmar_engine.resolution``
and they are re-exported from there.)

Everything else — the matrix, modifiers, ``skill_bonus`` and the same-class
transfer, ``resolve_attack`` with the §7 naturals, :data:`FUMBLE_TABLE` and
:func:`fumble_result`, and every constant — is imported from the package.
The drift-guard tests (``test_combat.py``,
``test_rules_consistency.CritFumbleRuleTest``) now guard the package through
this module.

Resolution is roll-over: ``d20 + to-hit bonus >= Target Number`` is a hit. A
natural 20 always hits (and crits); a natural 1 always misses (and fumbles).
"""

from pathlib import Path

# The §8 Hybrid decomposition helpers were duplicated here and in
# tarmar_engine.resolution until #240 milestone 2 folded them onto one
# source; damage_after_armour is tarmar_rules' own, re-exported through the
# same seam. All three are in __all__.
from tarmar_engine.resolution import (
    applied_armour_stops,
    damage_after_armour,
    hybrid_bypass_applies,
)
from tarmar_rules import (
    ARMOUR_TIERS,
    CRIT_DAMAGE_ROLLS,
    DIE_FACES,
    FUMBLE_DIE_SIDES,
    FUMBLE_TABLE,
    HEAVY_CLASSES,
    MATRIX,
    SAME_CLASS_LEVEL_DIVISOR,
    SEVERE_CRIT_DAMAGE_ROLLS,
    SKILL_BONUS_PER_LEVEL,
    dex_modifier,
    dodge_modifier,
    hit_probability,
    resolve_attack,
    same_class_skill_bonus,
    skill_bonus,
    strength_fit_penalty,
    target_number,
)
from tarmar_rules import fumble_table_lookup as fumble_result
from tarmar_rules import weapon_skill_bonus as _package_weapon_skill_bonus

from .equipment import get_weapon_class, is_unusual_weapon
from .equipment_rules import EquipmentCatalog, EquipmentCatalogError

__all__ = [
    "ARMOUR_TIERS",
    "CRIT_DAMAGE_ROLLS",
    "DIE_FACES",
    "FUMBLE_DIE_SIDES",
    "FUMBLE_TABLE",
    "HEAVY_CLASSES",
    "MATRIX",
    "SAME_CLASS_LEVEL_DIVISOR",
    "SEVERE_CRIT_DAMAGE_ROLLS",
    "SKILL_BONUS_PER_LEVEL",
    "applied_armour_stops",
    "damage_after_armour",
    "dex_modifier",
    "dodge_modifier",
    "fumble_result",
    "hit_probability",
    "hybrid_bypass_applies",
    "resolve_attack",
    "same_class_skill_bonus",
    "skill_bonus",
    "strength_fit_penalty",
    "target_number",
    "to_hit_bonus",
    "validate_catalog_classes",
    "weapon_skill_bonus",
]


def validate_catalog_classes(catalog: EquipmentCatalog, path: Path) -> None:
    """Refuse a weapon class or armour tier the combat matrix does not have.

    The weapon-against-armour matrix (:data:`MATRIX`, rows by weapon class,
    columns by :data:`ARMOUR_TIERS`) is the rules package's, and the catalog
    the canon's, so the catalog's loader cannot see a class or tier with no
    row or column: an attack with that weapon, or on that armour, would find
    no Target Number. Here rather than in :mod:`characters.equipment_rules`,
    which stays free of the rules package;
    :func:`characters.checks.check_equipment_catalog` calls it, so a refusal
    is ``characters.E003`` (issue #75).

    Args:
        catalog: The catalog read from ``path``.
        path: The ``equipment.json`` it was read from, for the message.

    Raises:
        EquipmentCatalogError: Some weapon's ``weapon_class`` or
            ``alt_class``, or some armour's ``armour_tier``, is not the
            matrix's.
    """
    refused = [
        f"weapons.{identifier}.{field} {weapon_class!r}"
        for identifier, weapon in catalog.weapons.items()
        for field, weapon_class in (
            ("weapon_class", weapon.weapon_class),
            ("alt_class", weapon.alt_class),
        )
        if weapon_class is not None and weapon_class not in MATRIX
    ] + [
        f"armor.{identifier}.armour_tier {armour.armour_tier!r}"
        for identifier, armour in catalog.armour.items()
        if armour.armour_tier not in ARMOUR_TIERS
    ]
    if refused:
        raise EquipmentCatalogError(
            f"{path}: {', '.join(refused)} is not a weapon class "
            f"{list(MATRIX)} or armour tier {list(ARMOUR_TIERS)} the combat "
            "matrix has"
        )


def weapon_skill_bonus(
    skill_level: int,
    *,
    skill_weapon_id: str | None = None,
    attack_weapon_id: str | None = None,
) -> int:
    """To-hit bonus a Weapon skill gives when swinging a particular weapon.

    Catalog-aware adapter over ``tarmar_rules.weapon_skill_bonus`` (which
    speaks weapon-class strings because the package has no catalog): resolves
    both catalog ids to their ``weapon_class`` and Unusual flags, then
    delegates. Passing either id as ``None`` means "the skill is for the
    weapon in hand" and yields the full bonus — what plain
    :func:`to_hit_bonus` callers get.
    """
    if skill_weapon_id is None or attack_weapon_id is None:
        return skill_bonus(skill_level)
    trained_class = get_weapon_class(skill_weapon_id)
    attack_class = get_weapon_class(attack_weapon_id)
    # Catalog semantics the package can't know: two DIFFERENT unclassed items
    # (a class of None means "not a weapon the matrix knows") share no
    # training. The package reads a None class as "unknown, credit in full",
    # which is right only for the same weapon.
    if skill_weapon_id != attack_weapon_id and (
        trained_class is None or attack_class is None
    ):
        return 0
    return _package_weapon_skill_bonus(
        skill_level,
        skill_weapon_class=trained_class,
        attack_weapon_class=attack_class,
        same_weapon=skill_weapon_id == attack_weapon_id,
        unusual=is_unusual_weapon(skill_weapon_id)
        or is_unusual_weapon(attack_weapon_id),
    )


def to_hit_bonus(
    *,
    effective_dexterity: int,
    skill_level: int,
    effective_strength: int,
    str_req: int | None,
    situational: int = 0,
    skill_weapon_id: str | None = None,
    attack_weapon_id: str | None = None,
) -> int:
    """Total bonus added to the attacker's d20 (§2).

    Composed from the package's primitives plus the catalog-aware
    :func:`weapon_skill_bonus` adapter — identical math to
    ``tarmar_rules.to_hit_bonus``, with this repo's id-to-class resolution.
    """
    return (
        dex_modifier(effective_dexterity)
        + weapon_skill_bonus(
            skill_level,
            skill_weapon_id=skill_weapon_id,
            attack_weapon_id=attack_weapon_id,
        )
        + strength_fit_penalty(effective_strength, str_req)
        + situational
    )


# The §8 Hybrid-armour helpers are re-exported near the top of the module
# (see the module docstring's local-remainder list, item 2): the identical
# decompositions used to live here and were folded onto the package's copy
# in #240 milestone 2.
