"""Equipment catalog for the character inventory system.

The items themselves come from the loaded canon's ``equipment.json``, read by
:mod:`characters.equipment_rules` the first time a function here is called,
never at import (issue #75): the catalog is canon data, and a module that read
it while importing would turn a canon without one into a crash instead of the
``characters.E003`` system check. The functions here are the catalog's readers
for the rest of the app and keep the shape they had when the catalog was a
fixture: an item is a dict of the fields the file holds.

Each item dict carries:
  - ``name``: human-readable label shown in the UI
  - ``category``: ``weapons`` | ``armor`` | ``shield`` | ``gear``
  - ``weight``: pounds, used by the encumbrance system
  - ``cost``: gold-piece equivalent
  - weapons add ``damage`` (dice string), ``str_req``, ``throwable``,
    ``two_handed``
  - armor and shields add ``stops`` and ``dex_penalty``

Per-character overrides (``to_hit_mod``, ``damage_mod``, ``stops_mod``,
``dex_mod`` and analogues) live on the character's equipment entry, NOT here;
this catalog is read-only.

Encumbrance thresholds come from the character's effective Strength:
``unencumbered ≤ STR``, ``light ≤ 1.5·STR``, ``medium ≤ 2·STR``,
``heavy ≤ 2.5·STR``; beyond that the character is overloaded. See
``calculate_encumbrance_level`` and ``get_movement_penalty`` for the
penalty/restriction table.
"""

import functools
from typing import Any

from reference.rules_file import derived_from_rules

from .equipment_rules import equipment_catalog

# What one item's own ``category`` may be, in the custom-item form's order and
# with its labels. Only shields differ from their group: the group is
# "shields", each item in it is a "shield" (see attack.py). The custom-item
# validator, the form's select and character-edit.js's branches all use these.
ITEM_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("gear", "Gear"),
    ("weapons", "Weapon"),
    ("armor", "Armor"),
    ("shield", "Shield"),
)


@derived_from_rules
@functools.cache
def catalog_by_group() -> dict[str, dict[str, dict[str, Any]]]:
    """The loaded catalog as the file holds it: each group's items by id.

    :meth:`characters.equipment_rules.EquipmentCatalog.as_dict` of the loaded
    catalog, built the first time it is asked for and held for the process;
    seating another catalog in a test forgets it
    (:func:`reference.rules_file.derived_from_rules`). The dicts are shared:
    callers read them and never change them.
    """
    return equipment_catalog().as_dict()


@derived_from_rules
@functools.cache
def _items_by_id() -> dict[str, dict[str, Any]]:
    """Every loaded item by id: :func:`catalog_by_group`'s groups merged in
    order, the same dict for an item in both (the file holds no id twice)."""
    merged: dict[str, dict[str, Any]] = {}
    for items in catalog_by_group().values():
        merged.update(items)
    return merged


def get_item(item_id: str) -> dict | None:
    """Get item data from catalog."""
    return _items_by_id().get(item_id)


def get_item_name(item_id: str) -> str:
    """Get item name, returns item_id if not found."""
    return equipment_catalog().name(item_id)


def get_weapon_class(item_id: str) -> str | None:
    """Return a weapon's combat class (see ``characters.combat``), or None.

    Ammunition and non-weapons have no class. Used by the d20 weapon-vs-armour
    resolution to look up the base Target Number.
    """
    return equipment_catalog().weapon_class(item_id)


def is_unusual_weapon(item_id: str) -> bool:
    """True for a catalog weapon in the Unusual group (subcategory ``unusual``).

    Unusual weapons are too unlike one another to share training: a Weapon
    skill tied to one transfers no bonus to or from any other weapon. See
    ``characters.combat.weapon_skill_bonus``.
    """
    return equipment_catalog().is_unusual_weapon(item_id)


def get_armour_tier(item_id: str) -> str | None:
    """Return an armour item's tier (None/Light/Medium/Heavy), or None.

    Unarmoured targets have no catalog item; callers default to ``"None"``.
    """
    return equipment_catalog().armour_tier(item_id)


def get_shield_bonus(item_id: str) -> int:
    """Return a shield's bonus to the defender's Target Number (0 if none)."""
    return equipment_catalog().shield_bonus(item_id)


def entry_category(item: dict) -> str:
    """An equipment entry's category: the catalog's, or a custom item's own."""
    catalog_item = get_item(item.get("item_id", ""))
    if catalog_item:
        return str(catalog_item.get("category", ""))
    return str(item.get("category", ""))


def is_worn_armour(item: dict) -> bool:
    """Whether an equipment entry is armour the character is wearing (#869).

    Worn armour is on the character; a pack is not, dropped or carried. So
    armour counts, for its stops, its DEX penalty, its movement restriction
    and the battle board's armour tier, only when it is equipped and not in
    the pack. Every reader of worn armour asks this, the way the shield in
    use and the weapon in hand are read only outside the pack (#804, #846).
    """
    return (
        entry_category(item) == "armor"
        and bool(item.get("equipped"))
        and not item.get("in_pack")
    )


def settle_worn_armour(item: dict) -> None:
    """Take equipped armour out of the pack, so the two flags never both hold.

    Putting armour on takes it out of the pack (#869, the rule #804 set for
    shields and #846 for weapons). A writer that stores an entry with both
    flags set (an import, the Games Master chat, an add with both boxes
    ticked) calls this, and the entry is stored as worn.

    Args:
        item: One equipment entry, changed in place.
    """
    if entry_category(item) == "armor" and item.get("equipped"):
        item["in_pack"] = False


# Encumbrance thresholds and penalties
ENCUMBRANCE_LEVELS = {
    "unencumbered": {"max_multiplier": 1.0, "penalty": 0, "restrictions": []},
    "light": {"max_multiplier": 1.5, "penalty": -1, "restrictions": []},
    "medium": {"max_multiplier": 2.0, "penalty": -2, "restrictions": ["run", "sprint"]},
    "heavy": {
        "max_multiplier": 2.5,
        "penalty": -4,
        "restrictions": ["jog", "run", "sprint"],
    },
    "overloaded": {
        "max_multiplier": float("inf"),
        "penalty": 0,
        "restrictions": ["jog", "run", "sprint"],
        "walk_limit": 1,
    },
}


# Lightest to heaviest; the first level whose max the carried weight fits under
# wins. "overloaded" has an infinite multiplier so it catches everything else.
ENCUMBRANCE_ORDER = ("unencumbered", "light", "medium", "heavy", "overloaded")


def calculate_encumbrance_level(carried_weight: float, strength: int) -> str:
    """Calculate encumbrance level based on carried weight and STR."""
    for level in ENCUMBRANCE_ORDER:
        if carried_weight <= strength * ENCUMBRANCE_LEVELS[level]["max_multiplier"]:
            return level
    return "overloaded"


def get_movement_penalty(encumbrance_level: str | None) -> int:
    """Get movement penalty for encumbrance level; 0 when the level is unknown."""
    if encumbrance_level is None:
        return 0
    return ENCUMBRANCE_LEVELS.get(encumbrance_level, {}).get("penalty", 0)


def get_movement_restrictions(encumbrance_level: str | None) -> list[str]:
    """Restricted movement modes for the level; none when the level is unknown."""
    if encumbrance_level is None:
        return []
    return ENCUMBRANCE_LEVELS.get(encumbrance_level, {}).get("restrictions", [])


def get_walk_limit(encumbrance_level: str) -> int | None:
    """Hex cap on Walk for encumbrance levels that impose one, else None.

    Only ``overloaded`` carries a ``walk_limit`` entry -- "Walk only, 1 hex
    max" (movement.md). Every other level walks at the normal gait formula.
    """
    return ENCUMBRANCE_LEVELS.get(encumbrance_level, {}).get("walk_limit")


# Canonical restricted-movement-mode order, lightest to heaviest gait. Matches
# the order used by ENCUMBRANCE_LEVELS["heavy"]["restrictions"] above.
MOVEMENT_MODE_ORDER = ("jog", "run", "sprint")

# Maps an armor's catalog ``movement_restriction`` code to the gaits it
# forbids. See ``reference/content/public-rules/equipment/armor-and-shields.md``:
# Leather Armor is "Cannot Sprint", Chainmail/Half-Plate/Plate/Fine Plate are
# "Cannot Run or Sprint".
ARMOR_MOVEMENT_RESTRICTION_MODES: dict[str, list[str]] = {
    "no_sprint": ["sprint"],
    "no_run_sprint": ["run", "sprint"],
}


def combine_movement_restrictions(*restriction_lists: list[str]) -> list[str]:
    """Merge restriction lists into their union, in canonical gait order.

    Restrictions from different sources (encumbrance, worn armor) are not
    additive counts, just a set of forbidden gaits — the most restrictive
    combination is simply the union of what each source forbids.
    """
    combined = set()
    for restrictions in restriction_lists:
        combined.update(restrictions)
    return [mode for mode in MOVEMENT_MODE_ORDER if mode in combined]


def _equipped_armor_restriction_codes(equipment: list[dict] | None):
    """Yield (movement_restriction code, display name) for each armour piece worn.

    Worn is :func:`is_worn_armour`: equipped and not in the pack (#869).

    Mirrors the catalog-vs-custom resolution in ``Character._sum_armor_field``:
    a catalog item's ``category`` comes from the catalog (the equipment entry
    itself need not repeat it), while a custom item (no catalog match) is read
    entirely from its own stored fields.
    """
    if not equipment:
        return
    for item in equipment:
        if not is_worn_armour(item):
            continue
        # A custom item (no catalog entry) carries its own fields.
        source = get_item(item.get("item_id", "")) or item
        code = source.get("movement_restriction")
        name = source.get("name")
        yield code, name or item.get("item_id", "Armor")


def get_armor_movement_restrictions(equipment: list[dict] | None) -> list[str]:
    """Restricted movement modes from currently equipped armor.

    Only armour worn counts (:func:`is_worn_armour`) — armour sitting
    unequipped in inventory, or in the pack, imposes no restriction (#869). Custom armor (no catalog entry) is read
    from its own stored ``movement_restriction`` field, matching how other
    per-item armor values (e.g. ``stops``) fall back to stored data.
    """
    restricted: set[str] = set()
    for code, _name in _equipped_armor_restriction_codes(equipment):
        if code:
            restricted.update(ARMOR_MOVEMENT_RESTRICTION_MODES.get(code, []))
    return [mode for mode in MOVEMENT_MODE_ORDER if mode in restricted]


def get_restrictive_armor_names(equipment: list[dict] | None) -> list[str]:
    """Names of equipped armor pieces that impose a movement restriction."""
    return [
        name
        for code, name in _equipped_armor_restriction_codes(equipment)
        if code in ARMOR_MOVEMENT_RESTRICTION_MODES
    ]


def describe_movement_restrictions(restrictions: list[str]) -> str:
    """Human-readable summary of restricted gaits, matching the rules doc wording."""
    if not restrictions:
        return ""
    if restrictions == ["sprint"]:
        return "Cannot Sprint"
    if restrictions == ["run", "sprint"]:
        return "Cannot Run or Sprint"
    if restrictions == ["jog", "run", "sprint"]:
        return "Cannot Jog, Run, or Sprint"
    return "Cannot " + ", ".join(mode.title() for mode in restrictions)
