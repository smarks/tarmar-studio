"""Coin a character holds, and every movement of it (issue #757).

Creation gave a starting coin, each prior-experience year paid coin, and the
catalog priced every item, and none of the three met: buying gear cost
nothing. The purse is now one derived figure, ``Character.purse`` (starting
coin + earnings + ``coin_adjustment``), and :func:`move_coin` is the one
thing that changes it. Its callers:

* the sheet's catalog purchase and a raised quantity, which pay the catalog
  price (:func:`charge_for_catalog_item`);
* a custom item a player adds, which pays its stated cost;
* a player's Games Master session adding an item, which is a purchase
  (``services.apply_equipment``);
* a Game Master's grant or removal of coin on the sheet.

Removing an item, or lowering its quantity, returns nothing (coordinator's
ruling on the #757 review, marked for Spencer: no page provides for resale);
a Game Master may give coin back. A Game Master's gifts, on the sheet or in
the Games Master chat, are free.
"""

from decimal import Decimal

from .equipment import get_item
from .models import Character

#: Two decimal places: the catalog prices to the Minor MU (1/100 MU).
CENT = Decimal("0.01")

#: The most coin a Game Master moves in one step, on the sheet or in the
#: Games Master chat's set_coin.
MAX_COIN_MOVEMENT = Decimal(1_000_000)


class PurseError(ValueError):
    """A coin movement the rules refuse; ``str()`` says why, for the player."""


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
        return f"{quantity} × {name} given by a Game Master"
    return move_coin(
        character, -catalog_price(item_id) * quantity, reason=f"{quantity} × {name}"
    )
