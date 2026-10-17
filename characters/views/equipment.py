"""Equipment endpoints: catalog, add/update/remove/bulk-remove, pack toggle.

Split out of the monolithic ``characters/views.py`` (issue #90). Item
building/validation and the "only one armor equipped" rule live in
``characters/equipment_service.py``; these views stay thin: parse the
request, make the change through ``characters.services.changing`` (one
locked row, one history line), and shape the response.
"""

from collections.abc import Callable
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST

from common.decorators import rate_limit
from common.helpers import (
    _json_error,
    _parse_json_body,
    _require_field,
    whole_number,
)

from .. import services
from ..constants import (
    MAX_EQUIPMENT_NOTES_LENGTH,
    MAX_EQUIPMENT_QUANTITY,
    strip_control_characters,
    too_long,
)
from ..equipment import catalog_by_group, get_item, settle_worn_armour
from ..equipment_service import (
    HELD_CATEGORIES,
    build_catalog_equipment_item,
    build_custom_equipment_item,
    item_modifiers_named,
    ready_in_hand,
    unequip_all_armor,
    validate_equipment_index,
    validate_modifier,
)
from ..models import Character
from ..purse import (
    PurseError,
    catalog_price,
    charge_for_catalog_item,
    charge_for_custom_item,
    charge_for_more_of_custom_item,
    format_coin,
    format_purse,
    hold_purchase_to_wealth,
    purchase_before_wealth_note,
)
from .access import ChangeRefused, answer_change, may_edit
from .characters import (
    _log_character_event,
    attack_weapon_choices,
    movement_response,
    sheet_repaint,
)


def _log_equipment_change(request, character, action: str) -> None:
    """Audit-log equipment mutations performed on someone else's character."""
    _log_character_event(request, character, "EQUIPMENT_" + action)


def _charge_for_custom_item(
    request, character: Character, equipment_item: dict
) -> list[str]:
    """Hold a player's custom item to the rules; a Game Master's is free.

    The rule is :func:`characters.purse.charge_for_custom_item`, which the
    Games Master chat's ``add_equipment`` is held to as well (#927). The
    sheet's form always states a cost, 0 when left blank.

    Returns:
        The history lines for the purse.

    Raises:
        PurseError: A player adding a combat item, or a purse that cannot
            cover the stated cost.
    """
    line = charge_for_custom_item(
        character,
        equipment_item["name"],
        str(equipment_item.get("category", "")),
        Decimal(str(equipment_item.get("cost", 0) or 0)),
        1,
        is_game_master=request.user.can_manage_users,
    )
    return [line] if line else []


def _note_purchase_before_wealth(
    request, character: Character, coin_lines: list[str], notices: list[str]
) -> None:
    """Mark a Game Master's purchase before the wealth roll (review of #851).

    The line goes on the purchase's own history entry and on the page, where
    it stays until dismissed.
    """
    note = purchase_before_wealth_note(
        character, is_game_master=request.user.can_manage_users
    )
    if note and coin_lines:
        coin_lines[-1] = f"{coin_lines[-1]}; {note}"
        notices.append(note)


def _get_equipment_response(character: Character) -> dict:
    """Build standard equipment response with encumbrance data.

    It also carries the Attack Resolution panel's weapon choices, so an
    equipment change refreshes that select without a reload (issue #339,
    item 24); and the encumbrance, movement and combat tiles
    (:func:`~characters.views.characters.movement_response`).

    What any change moves elsewhere on the sheet (:func:`sheet_repaint`,
    issue #792) is not in it: the view adds that once, at the top of its
    response. A run of years nests this payload under ``movement``, and a
    copy here rendered the "Play with the Game Master" slot, its session
    queries and every partial beside it twice (issue #933).

    Args:
        character: The saved character.
    """
    attack_weapons, gm_adjudicated = attack_weapon_choices(character)
    return {
        "attack_weapons": attack_weapons,
        "attack_gm_adjudicated_weapons": gm_adjudicated,
        "success": True,
        "equipment": character.equipment,
        "pack_dropped": character.pack_dropped,
        # Which rows hold the weapon in hand and the shield in use (issues
        # #804, #846): the list marks them, the tiles read them.
        "weapon_in_hand_index": character.weapon_in_hand_index,
        "shield_in_use_index": character.shield_in_use_index,
        # Why a carried shield does not count, when a two-handed weapon is in
        # hand; the shield's row says so (review of #804).
        "shield_idle_reason": character.shield_idle_reason,
        **movement_response(character),
        # The running balance beside the picker (issue #757), and the Wealth
        # table's Spent / granted line a purchase moves with it.
        "purse": format_purse(character),
        "coin_adjustment": format_coin(character.coin_adjustment),
    }


#: The equipment edits kept in the character's change history. Quantity,
#: notes and pack edits autosave as they are typed and would crowd out
#: everything else.
_HISTORY_ACTIONS = ("ADD", "REMOVE", "BULK_REMOVE", "MODIFIERS")


def _hold_item_modifiers(request, data: dict) -> list[str]:
    """The item modifiers a request names, refused for a player (issue #752).

    The rules give equipment no modifiers (``services.ITEM_MODIFIER_RULE``);
    a Game Master may grant them. Called inside the locked edit, after the
    edit right is checked, so a stranger still gets the 403.

    Raises:
        ChangeRefused: A player's request names one.
    """
    named = item_modifiers_named(data)
    if named and not services.can_set_item_modifiers(
        is_game_master=request.user.can_manage_users
    ):
        raise ChangeRefused(_json_error(services.ITEM_MODIFIER_RULE.reason))
    return named


def _stamp_item_modifiers(request, item: dict, named: list[str]) -> str | None:
    """Record who set an item's modifiers, and the history line saying so.

    The sheet shows the name beside the modifiers, so a Game Master's grant
    carries its provenance (issue #752).

    Returns:
        The audit/history action, or None when no modifier was named.
    """
    if not named:
        return None
    item["modifiers_set_by"] = request.user.username
    values = ", ".join(f"{field} {int(item.get(field, 0)):+d}" for field in named)
    name = get_item(item.get("item_id", "")) or {}
    called = name.get("name") or item.get("name") or item.get("item_id", "item")
    return f"MODIFIERS {called}: {values}"


def _change_equipment(
    request,
    pk: int,
    action: str,
    edit: Callable[[Character], str | None],
    extra: Callable[[str], dict] | None = None,
    coin_lines: list[str] | None = None,
) -> HttpResponse:
    """Make one equipment edit through ``characters.services`` and answer it.

    Args:
        request: The request; its user must be allowed to edit the character.
        pk: The character's primary key.
        action: The audit event's suffix; "" for an edit that is not audited.
        edit: Changes the locked character's equipment, raising
            ``ChangeRefused`` to leave it untouched. May return a fuller
            action for the audit line.
        extra: Extra response fields, built from the action ``edit`` returned.
        coin_lines: Filled by ``edit`` with what the purse paid (issue
            #757); each is kept in the change history.
    """

    def run() -> HttpResponse:
        with services.changing(
            pk, actor=request.user, authorize=may_edit(request)
        ) as change:
            try:
                logged = edit(change.character) or action
            except PurseError as refusal:
                raise ChangeRefused(_json_error(str(refusal))) from refusal
            if logged.split(" ", 1)[0] in _HISTORY_ACTIONS:
                change.note("equipment", "", logged.lower().replace("_", " "))
            for line in coin_lines or ():
                change.note("purse", "", line)
        if action:
            _log_equipment_change(request, change.character, logged)
        response = {
            **_get_equipment_response(change.character),
            **sheet_repaint(change.character, request.user),
        }
        if extra is not None:
            response.update(extra(logged))
        return JsonResponse(response)

    return answer_change(run, json_not_found=False)


@login_required
def equipment_catalog(request):
    """Return the loaded canon's equipment catalog for the UI picker."""
    return JsonResponse(catalog_by_group())


@login_required
@require_POST
@rate_limit("equipment_add", max_requests=60, window_seconds=60)
def equipment_add(request, pk):
    """Add an item from the catalog or a custom item to character's equipment."""
    data, err = _parse_json_body(request)
    if err:
        return err

    coin_lines: list[str] = []
    notices: list[str] = []
    # Only a Game Master gives an item for nothing (issue #757).
    free = bool(data.get("free")) and request.user.can_manage_users

    # Lock the row for the read-modify-write so concurrent equipment edits can't
    # lose an item (mirrors the term/skill writes).
    def edit(character: Character) -> str | None:
        named = _hold_item_modifiers(request, data)
        is_custom = data.get("is_custom", False)
        if is_custom:
            equipment_item, item_data, err = build_custom_equipment_item(data)
        else:
            equipment_item, item_data, err = build_catalog_equipment_item(data)
        if err:
            raise ChangeRefused(err)
        if is_custom:
            coin_lines.extend(
                _charge_for_custom_item(request, character, equipment_item)
            )
        else:
            # A catalog item is bought at the catalog's price (issue #757),
            # and a player buys nothing before the wealth roll (#851).
            if not free and catalog_price(equipment_item["item_id"]) > 0:
                hold_purchase_to_wealth(
                    character, is_game_master=request.user.can_manage_users
                )
            coin_lines.append(
                charge_for_catalog_item(
                    character,
                    equipment_item["item_id"],
                    equipment_item["quantity"],
                    free=free,
                )
            )
            if not free and catalog_price(equipment_item["item_id"]) > 0:
                _note_purchase_before_wealth(request, character, coin_lines, notices)

        # Get current equipment list
        current_equipment = list(character.equipment or [])  # Make a copy

        # Handle armor equipping - only one armor can be equipped at a time
        if item_data.get("category") == "armor":
            equipped = data.get("equipped", False)
            equipment_item["equipped"] = bool(equipped)
            if equipped:
                unequip_all_armor(current_equipment)
                # Worn, so not in the pack, whatever the request ticked (#869).
                settle_worn_armour(equipment_item)

        stamped = _stamp_item_modifiers(request, equipment_item, named)
        # Add to equipment list
        current_equipment.append(equipment_item)
        character.equipment = current_equipment
        return stamped and f"ADD {stamped}"

    return _change_equipment(
        request,
        pk,
        "ADD",
        edit,
        extra=lambda _action: {"notice": " ".join(notices)} if notices else {},
        coin_lines=coin_lines,
    )


@login_required
@require_POST
@rate_limit("equipment_update", max_requests=120, window_seconds=60)
def equipment_update(request, pk):
    """Update an equipment item (quantity, in_pack, notes, modifiers)."""
    data, err = _parse_json_body(request)
    if err:
        return err

    requested_index, err = _require_field(data, "index", "Item index required")
    if err:
        return err

    coin_lines: list[str] = []
    notices: list[str] = []
    is_game_master = request.user.can_manage_users

    # Lock the row for the read-modify-write so concurrent equipment edits can't
    # clobber each other (mirrors the term/skill writes).
    def edit(character: Character) -> str | None:
        named = _hold_item_modifiers(request, data)
        current_equipment = character.equipment or []

        index, index_error = validate_equipment_index(
            requested_index, current_equipment
        )
        if index_error:
            raise ChangeRefused(index_error)

        item = current_equipment[index]
        item_data = get_item(item.get("item_id", ""))
        # Category, falling back to the item's own stored category for custom
        # items (no catalog entry) -- mirrors the armor/shield handling below.
        item_category = item_data.get("category") if item_data else item.get("category")

        # A player takes nothing out of a dropped pack, whether by unpacking it
        # or by readying it; a Game Master may, since where the pack lies is
        # his to rule (coordinator's ruling on the #804 review). The stored
        # state decides, not what this same request writes.
        # Putting armour on takes it out of the pack as well (#869).
        drawing = ("in_pack" in data and not data["in_pack"]) or (
            item_category in (*HELD_CATEGORIES, "armor") and bool(data.get("equipped"))
        )
        if (
            drawing
            and item.get("in_pack")
            and character.pack_dropped
            and not is_game_master
        ):
            raise ChangeRefused(
                _json_error("It is in the dropped pack: pick the pack up first")
            )

        # Update quantity if provided
        if "quantity" in data:
            try:
                quantity = whole_number(data["quantity"])
                if quantity < 1:
                    raise ChangeRefused(_json_error("Quantity must be at least 1"))
                if quantity > MAX_EQUIPMENT_QUANTITY:
                    raise ChangeRefused(_json_error("Quantity too large"))
            except ValueError:
                raise ChangeRefused(_json_error("Invalid quantity")) from None
            held = int(item.get("quantity", 1) or 1)
            # More of a catalog item is bought at its price; fewer returns
            # nothing, as removing does (issue #757).
            if item_data and quantity > held:
                # More is a purchase: none before the wealth roll (#851).
                if catalog_price(str(item.get("item_id", ""))) > 0:
                    hold_purchase_to_wealth(character, is_game_master=is_game_master)
                coin_lines.append(
                    charge_for_catalog_item(
                        character, str(item.get("item_id", "")), quantity - held
                    )
                )
                if catalog_price(str(item.get("item_id", ""))) > 0:
                    _note_purchase_before_wealth(
                        request, character, coin_lines, notices
                    )
            elif quantity > held:
                # More of a custom item is held to the chat's stacking rule
                # (#927): bought at its stored cost, and a Game Master's to
                # give when it has none or carries combat numbers (#845).
                line = charge_for_more_of_custom_item(
                    character, item, quantity - held, is_game_master=is_game_master
                )
                if line:
                    coin_lines.append(line)
            item["quantity"] = quantity

        # Update in_pack if provided. A weapon or shield slung into the pack
        # is no longer the one in hand (issues #804, #846), and armour packed
        # away is no longer worn (#869).
        if "in_pack" in data:
            item["in_pack"] = bool(data["in_pack"])
            if item["in_pack"] and item_category in (*HELD_CATEGORIES, "armor"):
                item["equipped"] = False

        # Update notes if provided
        if "notes" in data:
            notes = strip_control_characters(str(data["notes"]))
            refusal = too_long("Notes", notes, MAX_EQUIPMENT_NOTES_LENGTH)
            if refusal:
                raise ChangeRefused(_json_error(refusal))
            item["notes"] = notes

        # Update weapon modifiers
        if item_category == "weapons":
            for field in ("to_hit_mod", "damage_mod", "critical_mod"):
                _, err = validate_modifier(data, field, item)
                if err:
                    raise ChangeRefused(err)

        # Update armor/shield modifiers
        if item_category in ("armor", "shield"):
            for field in ("stops_mod", "dex_mod"):
                _, err = validate_modifier(data, field, item)
                if err:
                    raise ChangeRefused(err)

        # Handle armor equipping - only one armor can be equipped at a time
        if item_category == "armor" and "equipped" in data:
            new_equipped = bool(data["equipped"])
            if new_equipped and not item.get("equipped", False):
                unequip_all_armor(current_equipment, skip_index=index)
            item["equipped"] = new_equipped
            # Armour put on comes out of the pack (#869).
            settle_worn_armour(item)

        # The weapon in hand and the shield in use are the player's choice,
        # one of each (READY WEAPON; issues #804, #846). Readying one draws it
        # from the pack and slings the other away.
        # A Game Master may ready anything; a player's shield needs a free
        # hand. Readying a two-handed weapon beside a shield is not refused:
        # the shield's row says why it no longer counts (shield_idle_reason).
        if item_category in HELD_CATEGORIES and data.get("equipped"):
            if (
                item_category == "shield"
                and character.weapon_in_hand_is_two_handed
                and not is_game_master
            ):
                raise ChangeRefused(
                    _json_error(
                        "The weapon in hand takes both hands: ready a one-handed "
                        "weapon first"
                    )
                )
            ready_in_hand(current_equipment, index, item_category)

        stamped = _stamp_item_modifiers(request, item, named)
        current_equipment[index] = item
        character.equipment = current_equipment
        return stamped

    return _change_equipment(
        request,
        pk,
        "UPDATE",
        edit,
        extra=lambda _action: {"notice": " ".join(notices)} if notices else {},
        coin_lines=coin_lines,
    )


@login_required
@require_POST
@rate_limit("equipment_remove", max_requests=60, window_seconds=60)
def equipment_remove(request, pk):
    """Remove an equipment item by index."""
    data, err = _parse_json_body(request)
    if err:
        return err

    requested_index, err = _require_field(data, "index", "Item index required")
    if err:
        return err

    # Lock the row for the read-modify-write so a concurrent equipment edit
    # can't be lost (mirrors the term/skill writes).
    def edit(character: Character) -> str | None:
        current_equipment = character.equipment or []

        index, index_error = validate_equipment_index(
            requested_index, current_equipment
        )
        if index_error:
            raise ChangeRefused(index_error)

        # Remove item. Nothing is returned for it (issue #757).
        current_equipment.pop(index)
        character.equipment = current_equipment
        return None

    return _change_equipment(request, pk, "REMOVE", edit)


@login_required
@require_POST
@rate_limit("equipment_bulk_remove", max_requests=20, window_seconds=60)
def equipment_bulk_remove(request, pk):
    """Remove multiple equipment items by index in a single round-trip."""
    data, err = _parse_json_body(request)
    if err:
        return err

    indices = data.get("indices")
    if not isinstance(indices, list) or not indices:
        return _json_error("indices must be a non-empty list")

    # Lock the row for the read-modify-write so a concurrent equipment edit
    # can't be lost (mirrors the term/skill writes).
    def edit(character: Character) -> str | None:
        current_equipment = list(character.equipment or [])

        # Coerce + bounds-check each index up-front so the mutation is all-or-nothing.
        cleaned: set[int] = set()
        for raw in indices:
            index, err = validate_equipment_index(raw, current_equipment)
            if err:
                raise ChangeRefused(err)
            cleaned.add(index)

        # Remove in reverse so earlier indices stay valid as we pop.
        for index in sorted(cleaned, reverse=True):
            current_equipment.pop(index)

        character.equipment = current_equipment
        return f"BULK_REMOVE count={len(cleaned)}"

    return _change_equipment(
        request,
        pk,
        "BULK_REMOVE",
        edit,
        extra=lambda action: {"removed": int(action.rsplit("=", 1)[1])},
    )


@login_required
@require_POST
@rate_limit("equipment_toggle", max_requests=30, window_seconds=60)
def equipment_toggle_pack(request, pk):
    """Toggle pack dropped status."""

    # Lock the row: the toggle reads the current flag and writes its negation,
    # so concurrent toggles would otherwise cancel out (mirrors the other
    # equipment writes).
    def edit(character: Character) -> str | None:
        character.pack_dropped = not character.pack_dropped
        return None

    return _change_equipment(request, pk, "", edit)
