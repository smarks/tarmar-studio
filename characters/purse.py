"""Coin a character holds, and every movement of it (issue #757).

Creation gave a starting coin, each prior-experience year paid coin, and the
catalog priced every item, and none of the three met: buying gear cost
nothing. The purse is now one derived figure, ``Character.purse`` (starting
coin + earnings + ``coin_adjustment``), and :func:`move_coin` is the one
thing that changes it. Its callers:

* the sheet's catalog purchase and a raised quantity, which pay the catalog
  price (:func:`charge_for_catalog_item`);
* a custom item a player adds, which pays its stated cost
  (:func:`charge_for_custom_item`);
* a player's Games Master session adding an item, which is a purchase
  (``services.apply_equipment``), held to the same two charges;
* a Game Master's grant or removal of coin on the sheet.

Removing an item, or lowering its quantity, returns nothing (coordinator's
ruling on the #757 review, marked for Spencer: no page provides for resale);
a Game Master may give coin back. A Game Master's gifts, on the sheet or in
the Games Master chat, are free.
"""

import math
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from .constants import MAX_COIN_REASON_LENGTH, too_long
from .equipment import get_item
from .models import Character

#: Two decimal places: the catalog prices to the Minor MU (1/100 MU).
CENT = Decimal("0.01")

#: The most coin a Game Master moves in one step, on the sheet or in the
#: Games Master chat's set_coin.
MAX_COIN_MOVEMENT = Decimal(1_000_000)


class PurseError(ValueError):
    """A coin movement or purchase the rules refuse; ``str()`` says why."""


def _money(value: object) -> Decimal:
    return Decimal(str(value)).quantize(CENT)


def format_coin(amount: Decimal) -> str:
    """``12`` or ``12.50`` — whole MU print without the cents."""
    amount = _money(amount)
    return f"{amount:.0f}" if amount == amount.to_integral_value() else f"{amount:.2f}"


#: What the sheet says for a starting coin that is "Consult GM".
CONSULT_GM = "Consult GM"


def format_purse(character: Character) -> str:
    """The purse as the sheet, the export and the responses print it.

    ``"300 gp"``; while a Rich character's starting coin is "Consult GM"
    (wealth.md), ``"Consult GM"``, or ``"50 gp + Consult GM"`` once coin
    has been given meanwhile.
    """
    held = f"{format_coin(character.purse)} gp"
    if character.starting_coin_known:
        return held
    return CONSULT_GM if character.purse == 0 else f"{held} + {CONSULT_GM}"


def move_coin(character: Character, amount: Decimal, *, reason: str) -> str:
    """Add ``amount`` (negative to spend) to the character's purse.

    The rules this keeps, from ``reference/content/public-rules``:

        "Roll percentile dice ... | Level | Starting Coin |"
        (characters/wealth.md)

        "For every year of prior experience: ... Earn money based on skill
        level" (characters/prior-experience.md)

    and the Cost column of every table under ``equipment/``: gear is bought
    with that coin. A purse never goes below nothing — no page provides for
    debt — so a spend larger than the purse is refused, for a player and a
    Game Master alike; a Game Master who wants the character to have it
    grants the coin first, or gives the item free.

    A Rich character's starting coin is "Consult GM" (wealth.md): until a
    Game Master records it, it counts as no coin at all, so such a character
    buys only with what prior experience paid or a Game Master gives.

    Who may move coin in which direction is the caller's to decide: any
    editor may buy (nothing is ever refunded); only a Game Master grants or removes coin
    outright (``characters.views.mechanics.character_move_coin``).

    Args:
        character: The character, changed in memory and not saved.
        amount: Coin in MU; positive adds, negative spends.
        reason: What the movement was for, for the history line.

    Returns:
        The history line.

    Raises:
        PurseError: The purse cannot cover a spend.
    """
    amount = _money(amount)
    balance = character.purse
    if amount < 0 and balance + amount < 0:
        unknown = (
            ""
            if character.starting_coin_known
            else " (the starting coin is Consult GM until a Game Master records it)"
        )
        raise PurseError(
            f"Not enough coin: {reason} costs {format_coin(-amount)} gp and the "
            f"purse holds {format_coin(balance)} gp{unknown}"
        )
    character.coin_adjustment = _money(character.coin_adjustment) + amount
    verb = "paid" if amount < 0 else "received"
    return (
        f"{verb} {format_coin(abs(amount))} gp ({reason}); purse "
        f"{format_purse(character)}"
    )


def move_coin_for_reason(character: Character, amount: Decimal, *, reason: str) -> str:
    """A Game Master's coin given or taken for a stated reason.

    The sheet's coin control and the Games Master chat's ``set_coin`` both
    move coin through here, so the reason is held to its one limit on both,
    a refusal rather than a cut (issue #928). Who may call it is the
    caller's to decide, as for :func:`move_coin`.

    Args:
        character: The character, changed in memory and not saved.
        amount: Coin in MU; positive adds, negative takes.
        reason: What the Game Master gave or took it for.

    Returns:
        The history line.

    Raises:
        PurseError: A reason past :data:`MAX_COIN_REASON_LENGTH`, or a purse
            that cannot cover a take.
    """
    refusal = too_long("Reason", reason, MAX_COIN_REASON_LENGTH)
    if refusal:
        raise PurseError(refusal)
    return move_coin(character, amount, reason=reason)


#: Why a player cannot buy before the wealth roll (issue #851).
WEALTH_FIRST_REFUSAL = (
    "Roll wealth first: nothing is bought before the starting coin is rolled, "
    "since the roll replaces the coin a purchase would have spent"
)


def hold_purchase_to_wealth(character: Character, *, is_game_master: bool) -> None:
    """Refuse a player's purchase while the wealth roll is still to be made.

    The unrolled starting coin is a placeholder the wealth roll replaces
    (``services.apply_wealth_to_coin``), so gear bought from it stayed paid
    for out of a coin that no longer existed, and the purse went below
    nothing (issue #851). A player's import brings no gear for the same
    reason (#744). Players are held to it; a Game Master may buy for a
    character whenever they choose (the standing rule), and a Game Master's
    gift is not a purchase at all.

    Raises:
        PurseError: A player buying before the wealth roll.
    """
    if not is_game_master and not character.wealth_recorded:
        raise PurseError(WEALTH_FIRST_REFUSAL)


#: What a Game Master's purchase before the wealth roll is told (review of
#: #851): the placeholder coin it spends is replaced by the roll.
GAME_MASTER_BEFORE_WEALTH_NOTE = (
    "Bought before the wealth roll, from the placeholder starting coin: the "
    "roll replaces that coin, and this purchase stays paid for"
)


def purchase_before_wealth_note(character: Character, *, is_game_master: bool) -> str:
    """The line a Game Master's purchase before the wealth roll carries, or "".

    A player is refused (:func:`hold_purchase_to_wealth`); a Game Master may
    buy, and is told what it spends so the purse below nothing that can
    follow the roll is no surprise.
    """
    if is_game_master and not character.wealth_recorded:
        return GAME_MASTER_BEFORE_WEALTH_NOTE
    return ""


def catalog_price(item_id: str) -> Decimal:
    """One of a catalog item's price in MU; zero for an unpriced entry."""
    item = get_item(item_id) or {}
    cost = item.get("cost", 0)
    if not isinstance(cost, int | float):
        return Decimal(0)
    return _money(cost)


#: How a Game Master's gift of an item reads in the history.
GAME_MASTER_GIFT = "given by a Game Master"


def charge_for_catalog_item(
    character: Character, item_id: str, quantity: int, *, free: bool = False
) -> str:
    """Pay the catalog price for ``quantity`` of an item.

    Args:
        character: The buyer.
        item_id: The catalog id.
        quantity: How many are being bought.
        free: A Game Master's gift; the caller has checked who asked.

    Returns:
        The history line.

    Raises:
        PurseError: The purse cannot cover it.
    """
    name = (get_item(item_id) or {}).get("name", item_id)
    if free:
        return f"{quantity} × {name} {GAME_MASTER_GIFT}"
    return move_coin(
        character, -catalog_price(item_id) * quantity, reason=f"{quantity} × {name}"
    )


#: Custom items whose numbers reach combat: a weapon's damage, armour's and a
#: shield's Stops and DEX penalty (#845).
CUSTOM_COMBAT_CATEGORIES: tuple[str, ...] = ("weapons", "armor", "shield")

#: Why a player cannot add one (coordinator's ruling on the #757 review, #845).
CUSTOM_COMBAT_ITEM_REFUSAL = (
    "A custom weapon, armour or shield is a Game Master's to add: the catalog "
    "publishes every item's numbers, and nothing lets a player invent better ones"
)

#: Why a player's Games Master session cannot add a custom item it names no
#: price for (#927). The sheet's form always states one; a plan may not.
CUSTOM_ITEM_PRICE_REFUSAL = (
    "An item the catalog does not sell is bought at the price stated for it, "
    "and none was stated; only a Game Master gives one for nothing"
)

#: Why a player cannot make more of a custom item carried at no cost: a Game
#: Master's gift, or an item stored before items kept their cost (#927).
CUSTOM_ITEM_MORE_REFUSAL = (
    "This item is carried at no cost, so more of it is a Game Master's to give"
)


def charge_for_custom_item(
    character: Character,
    name: str,
    category: str,
    price: Decimal | None,
    quantity: int,
    *,
    is_game_master: bool,
    unpriced_refusal: str = CUSTOM_ITEM_PRICE_REFUSAL,
) -> str:
    """Hold a player's custom item to the rules; a Game Master's is free.

    The one rule for an item the catalog does not sell, on the sheet and in
    the Games Master chat (#927). A custom item carrying combat numbers is
    the Game Master's to add (#845); any other custom item a player adds is
    bought at the price stated for it (coordinator's rulings on the #757
    review), and a player buys nothing before the wealth roll (#851).

    Args:
        character: The buyer, changed in memory and not saved.
        name: The item's name, for the history line.
        category: The item's category; one of
            :data:`CUSTOM_COMBAT_CATEGORIES` carries combat numbers.
        price: The price of one in MU as stated, or None when none was.
        quantity: How many.
        is_game_master: Whether a Game Master is adding it.
        unpriced_refusal: What a player is told when ``price`` is None.

    Returns:
        The history line: what was paid, the Game Master's gift, or ``""``
        for a player's item stated to cost nothing.

    Raises:
        PurseError: A player's combat item, a player's item with no stated
            price, a purchase before the wealth roll, or a purse that cannot
            cover it.
    """
    if is_game_master:
        return f"{quantity} × {name} {GAME_MASTER_GIFT}"
    if category in CUSTOM_COMBAT_CATEGORIES:
        raise PurseError(CUSTOM_COMBAT_ITEM_REFUSAL)
    if price is None:
        raise PurseError(unpriced_refusal)
    cost = _money(price) * quantity
    if cost <= 0:
        return ""
    hold_purchase_to_wealth(character, is_game_master=False)
    return move_coin(character, -cost, reason=f"{quantity} × {name}")


def charge_for_more_of_custom_item(
    character: Character,
    item: Mapping[str, Any],
    quantity: int,
    *,
    is_game_master: bool,
) -> str:
    """Hold more of a custom item already carried to the rules (#927 review).

    More is bought at the cost the item is stored at, whatever price is
    stated for the new ones, as the sheet's quantity raise has always bought
    it. An item stored at no cost -- a Game Master's gift, or one stored
    before custom items kept their cost -- has nothing to buy more at, so
    more of it is the Game Master's to give; so is more of one carrying
    combat numbers (#845).

    Args:
        character: The buyer, changed in memory and not saved.
        item: The carried item, as stored.
        quantity: How many more.
        is_game_master: Whether a Game Master is adding them.

    Returns:
        The history line, as :func:`charge_for_custom_item` gives it.

    Raises:
        PurseError: Whatever :func:`charge_for_custom_item` refuses.
    """
    stored = item.get("cost")
    cost = (
        _money(stored)
        if isinstance(stored, int | float)
        and not isinstance(stored, bool)
        and math.isfinite(stored)
        else Decimal(0)
    )
    return charge_for_custom_item(
        character,
        str(item.get("name", "item")),
        str(item.get("category", "")),
        cost if cost > 0 else None,
        quantity,
        is_game_master=is_game_master,
        unpriced_refusal=CUSTOM_ITEM_MORE_REFUSAL,
    )
