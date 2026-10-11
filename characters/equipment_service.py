"""Equipment item building, validation, and inventory rules (issue #90).

The equipment endpoints (``characters/views/equipment.py``) all share the same
few pieces of business logic -- how a catalog or custom item is validated and
assembled, how a bounded index or modifier is coerced, and the "only one
armor equipped at a time" rule. Centralizing them here means
``equipment_update``'s re-derivation of an item's category/modifiers can't
drift from what the two builders below already encode.
"""

import math
from collections.abc import Mapping
from typing import Any

from django.http import JsonResponse

from common.helpers import _json_error, _require_field, whole_number

from .constants import (
    DAMAGE_DICE_PATTERN,
    ITEM_MODIFIER_FIELDS,
    MAX_ARMOR_DEX_PENALTY_MAGNITUDE,
    MAX_ARMOR_STOPS,
    MAX_EQUIPMENT_DAMAGE_LENGTH,
    MAX_EQUIPMENT_MODIFIER,
    MAX_EQUIPMENT_NAME_LENGTH,
    MAX_EQUIPMENT_NOTES_LENGTH,
    MAX_EQUIPMENT_QUANTITY,
    MAX_EQUIPMENT_WEIGHT_LBS,
    strip_control_characters,
    too_long,
)
from .equipment import ITEM_CATEGORIES, get_item


def validate_modifier(data, field, target, default=None):
    """Read `field` from `data` as a bounded integer modifier and store it on `target`.

    Catalog path (``default=None``): an absent field is skipped — returns
    ``(None, None)`` and leaves `target` untouched; a non-integer value returns
    ``(None, error_response)``.

    Custom-item path (``default`` set, e.g. ``0``): an absent or non-integer
    field stores `default` on `target` and returns ``(default, None)``.

    Either way, an out-of-range value returns ``(None, error_response)``.
    """
    value, refusal = read_modifier(data, field, target, default)
    return value, (_json_error(refusal) if refusal else None)


def read_modifier(
    data: Mapping[str, Any], field: str, target: dict, default: int | None = None
) -> tuple[int | None, str]:
    """:func:`validate_modifier` with the refusal as text, ``""`` for none."""
    if field not in data:
        if default is not None:
            target[field] = default
            return default, ""
        return None, ""
    try:
        val = whole_number(data[field])
    except ValueError:
        if default is not None:
            target[field] = default
            return default, ""
        return None, f"Invalid {field}"
    if abs(val) > MAX_EQUIPMENT_MODIFIER:
        return None, (
            f"{field} must be between "
            f"-{MAX_EQUIPMENT_MODIFIER} and +{MAX_EQUIPMENT_MODIFIER}"
        )
    target[field] = val
    return val, ""


def item_modifiers_named(data: dict) -> list[str]:
    """The item-modifier fields an equipment request names (issue #752).

    Args:
        data: The request body.

    Returns:
        Those of ``constants.ITEM_MODIFIER_FIELDS`` present, in that order.
    """
    return [field for field in ITEM_MODIFIER_FIELDS if field in data]


def validate_equipment_index(index, equipment):
    """Coerce `index` to a valid position in `equipment`.

    Returns ``(int_index, None)`` on success or ``(0, error_response)`` on
    failure — a sentinel 0 rather than None, so callers (which check the error
    first) get a non-Optional int.
    """
    try:
        index = whole_number(index)
    except ValueError:
        return 0, _json_error("Invalid index")
    if index < 0 or index >= len(equipment):
        return 0, _json_error("Invalid item index")
    return index, None


def unequip_all_armor(equipment_list, skip_index=None):
    """Set equipped=False on every armor item in `equipment_list`.

    `skip_index`, if set, is left alone (used when caller is the one being
    equipped and shouldn't unequip itself).
    """
    for i, item in enumerate(equipment_list):
        if skip_index is not None and i == skip_index:
            continue
        item_data = get_item(item.get("item_id", ""))
        category = item_data.get("category") if item_data else item.get("category")
        if category == "armor":
            item["equipped"] = False


#: The categories a character holds in hand, one item of each at a time
#: (READY WEAPON in action-options.md, issues #804 and #846).
HELD_CATEGORIES: tuple[str, ...] = ("weapons", "shield")


def ready_in_hand(equipment_list: list[dict], index: int, category: str) -> None:
    """Put the item at ``index`` in hand, slinging away the one it replaces.

    READY WEAPON: "Re-sling current, ready new weapon/shield". The item comes
    out of the pack to be held, and every other item of the same category
    stops being the chosen one.

    Args:
        equipment_list: The character's equipment, changed in place.
        index: The item being readied.
        category: Its category, one of :data:`HELD_CATEGORIES`.
    """
    for other_index, item in enumerate(equipment_list):
        if other_index == index:
            continue
        item_data = get_item(item.get("item_id", ""))
        other_category = (
            item_data.get("category") if item_data else item.get("category")
        )
        if other_category == category:
            item["equipped"] = False
    equipment_list[index]["equipped"] = True
    equipment_list[index]["in_pack"] = False


def build_custom_equipment_item(data) -> tuple[dict, dict, JsonResponse | None]:
    """Validate and build a custom equipment item from request data.

    Returns ``(equipment_item, item_data, None)`` on success or
    ``(None, None, error_response)`` on validation failure.
    """
    equipment_item, refusal = custom_equipment_item(data)
    if refusal:
        return {}, {}, _json_error(refusal)
    return equipment_item, {"category": equipment_item["category"]}, None


def custom_equipment_item(data: Mapping[str, Any]) -> tuple[dict, str]:
    """Validate and build a custom item, for the sheet and the chat alike.

    The Games Master chat's ``add_equipment`` builds its custom items here
    (#927), so a name, a category and a custom item's combat numbers are held
    to the one set of bounds the sheet keeps.

    Args:
        data: The item as the sheet's form would post it.

    Returns:
        ``(equipment_item, "")``, quantity 1, or ``({}, refusal)`` saying why
        the item is refused.
    """
    # Control characters are dropped rather than rejected: they are never typed
    # on purpose, and the field otherwise stays wide open (quotes, apostrophes
    # and parentheses are all legitimate in an item name). See issue #120.
    name = strip_control_characters(str(data.get("name", "")))
    if not name:
        return {}, "Custom item name required"
    # Refused, never cut short, on every path that builds one (issue #928):
    # the chat's add_equipment reads its name at the plan's ceiling and is
    # held to this one here.
    refusal = too_long("Name", name, MAX_EQUIPMENT_NAME_LENGTH)
    if refusal:
        return {}, refusal
    notes = strip_control_characters(str(data.get("notes", "")))
    refusal = too_long("Notes", notes, MAX_EQUIPMENT_NOTES_LENGTH)
    if refusal:
        return {}, refusal

    category = data.get("category", "gear")
    # A list is unhashable, so ``in`` the dict raised TypeError (#767).
    if not isinstance(category, str) or category not in dict(ITEM_CATEGORIES):
        return {}, "Invalid category"

    try:
        weight = float(data.get("weight", 0))
        # NaN compares false with everything, so it would pass the range
        # check below; infinity is not a weight either.
        if not math.isfinite(weight) or weight < 0 or weight > MAX_EQUIPMENT_WEIGHT_LBS:
            return {}, f"Weight must be 0-{MAX_EQUIPMENT_WEIGHT_LBS} lbs"
    except (ValueError, TypeError):
        return {}, "Invalid weight"

    try:
        cost = float(data.get("cost", 0))
        if not math.isfinite(cost):
            return {}, "Invalid cost"
        if cost < 0:
            return {}, "Cost cannot be negative"
    except (ValueError, TypeError):
        return {}, "Invalid cost"

    equipment_item = {
        "item_id": "custom",
        "name": name,
        "category": category,
        "weight": weight,
        "cost": cost,
        "quantity": 1,
        "in_pack": bool(data.get("in_pack", False)),
        "notes": notes,
    }

    if category == "weapons":
        raw_damage = str(data.get("damage", "1d6"))
        if len(raw_damage) > MAX_EQUIPMENT_DAMAGE_LENGTH or not (
            DAMAGE_DICE_PATTERN.match(raw_damage)
        ):
            return {}, "Damage must look like '1d6', '2d8+1', or '1d10-2'"
        equipment_item["damage"] = raw_damage
        for field in ("to_hit_mod", "damage_mod", "critical_mod"):
            _, refusal = read_modifier(data, field, equipment_item, default=0)
            if refusal:
                return {}, refusal
    elif category in ("armor", "shield"):
        try:
            stops = whole_number(data.get("stops", 0))
            if stops < 0 or stops > MAX_ARMOR_STOPS:
                return {}, f"Stops must be 0-{MAX_ARMOR_STOPS}"
            equipment_item["stops"] = stops
        except ValueError:
            equipment_item["stops"] = 0

        try:
            dex_penalty = whole_number(data.get("dex_penalty", 0))
            if dex_penalty > 0 or dex_penalty < -MAX_ARMOR_DEX_PENALTY_MAGNITUDE:
                return (
                    {},
                    f"DEX penalty must be 0 to -{MAX_ARMOR_DEX_PENALTY_MAGNITUDE}",
                )
            equipment_item["dex_penalty"] = dex_penalty
        except ValueError:
            equipment_item["dex_penalty"] = 0

        for field in ("stops_mod", "dex_mod"):
            _, refusal = read_modifier(data, field, equipment_item, default=0)
            if refusal:
                return {}, refusal

    return equipment_item, ""


def build_catalog_equipment_item(data) -> tuple[dict, dict, JsonResponse | None]:
    """Validate and build a catalog equipment item from request data.

    Returns ``(equipment_item, item_data, None)`` on success or
    ``(None, None, error_response)`` on validation failure.
    """
    item_id, err = _require_field(data, "item_id", "Item ID required")
    if err:
        return {}, {}, err

    item_data = get_item(str(item_id))
    if not item_data:
        return {}, {}, _json_error(f"Unknown item: {item_id}")

    quantity = data.get("quantity", 1)
    notes = strip_control_characters(str(data.get("notes", "")))

    try:
        quantity = whole_number(quantity)
        if quantity < 1:
            return {}, {}, _json_error("Quantity must be at least 1")
        if quantity > MAX_EQUIPMENT_QUANTITY:
            return {}, {}, _json_error("Quantity too large")
    except ValueError:
        return {}, {}, _json_error("Invalid quantity")

    refusal = too_long("Notes", notes, MAX_EQUIPMENT_NOTES_LENGTH)
    if refusal:
        return {}, {}, _json_error(refusal)

    equipment_item = {
        "item_id": str(item_id),
        "quantity": quantity,
        "in_pack": bool(data.get("in_pack", False)),
        "notes": notes,
    }

    if item_data.get("category") == "weapons":
        for field in ("to_hit_mod", "damage_mod", "critical_mod"):
            _, err = validate_modifier(data, field, equipment_item)
            if err:
                return {}, {}, err
    if item_data.get("category") in ("armor", "shield"):
        for field in ("stops_mod", "dex_mod"):
            _, err = validate_modifier(data, field, equipment_item)
            if err:
                return {}, {}, err

    return equipment_item, item_data, None
