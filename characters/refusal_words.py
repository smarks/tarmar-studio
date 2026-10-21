"""The words the characters app refuses in and marks a Game Master's call with
(issues #85, #97).

Content, not code (the house rule of 2026-10-17, issue #77): the reasons the
field table gives a player for each Game-Master-only value, the service
refusals that share them, and the phrase that marks a Game Master's override
on a history line, a check's line and the skill check's options live in
``config/characters-refusals.toml``, which Spencer edits with no Python.
settings.py reads it once at startup into ``settings.CHARACTERS_REFUSAL_WORDS``
through :func:`load_refusal_words`; :func:`common.wording.load_wording` checks
it there, so a missing key or a stray ``{name}`` stops the server rather than
reaching a player.

Moving a refusal's words here changes only what the player reads, never who is
refused: the field table's authors and the checks in front of each refusal
decide that.

settings.py imports this module while it is still loading, so nothing here
reads the settings at import time (``django.conf.settings`` is lazy).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from common.wording import load_wording, sentence


@dataclass(frozen=True)
class CommonWords:
    """Phrases more than one module uses."""

    the_game_masters_call: str = sentence()
    a_game_masters_call: str = sentence()


@dataclass(frozen=True)
class ServicesWords:
    """``characters.services``'s refusals: why a player may not set a value
    the field table gives a Game Master (issue #748)."""

    field_attribute: str = sentence("dice", "points")
    field_appearance_roll: str = sentence()
    field_fatigue_roll: str = sentence("dice")
    field_literacy_roll: str = sentence("dice")
    literacy_ruling_game_master_only: str = sentence()
    origin_ruling_game_master_only: str = sentence()
    dragonborn_game_master_only: str = sentence()
    field_provenance: str = sentence()
    field_wealth_level: str = sentence()
    field_starting_coin: str = sentence()
    field_total_xp: str = sentence()
    field_age: str = sentence()
    field_free_skill_points: str = sentence()
    field_death: str = sentence()
    pools_game_master_only: str = sentence()
    field_free_spell_points: str = sentence()
    field_magic_school_roll: str = sentence()
    field_magic_school: str = sentence()
    field_coin_adjustment: str = sentence()
    field_item_modifiers: str = sentence()
    roll_fixed_height: str = sentence()
    roll_fixed_weight: str = sentence()
    roll_fixed_origin: str = sentence()
    nobody_sets: str = sentence("field")


@dataclass(frozen=True)
class PurseWords:
    """``characters.purse``'s words (issue #136): how the purse prints, the
    history lines a coin movement writes, and its refusals."""

    held: str = sentence("coin", required=("coin",))
    consult_gm: str = sentence()
    held_and_unknown: str = sentence("held", "unknown", required=("held", "unknown"))
    not_enough: str = sentence("reason", "cost", "held", "unknown", required=("cost",))
    starting_coin_unknown: str = sentence()
    paid: str = sentence("coin", "reason", "purse", required=("coin",))
    received: str = sentence("coin", "reason", "purse", required=("coin",))
    label_reason: str = sentence()
    wealth_first: str = sentence()
    before_wealth_note: str = sentence()
    quantity_of: str = sentence("quantity", "name", required=("name",))
    given_by_game_master: str = sentence("item", required=("item",))
    custom_combat_item: str = sentence()
    custom_item_price: str = sentence()
    custom_item_more: str = sentence()
    an_item: str = sentence()


@dataclass(frozen=True)
class ConstantsWords:
    """``characters.constants``'s words (issue #136): the point-buy note and
    the name a character takes when nothing names it."""

    point_buy_over: str = sentence("spent", "off", "budget")
    point_buy_under: str = sentence("spent", "off", "budget")
    unnamed_character: str = sentence()


@dataclass(frozen=True)
class ViewsAccessWords:
    """``characters.views.access``'s refusal: a character that is not there,
    as the sheet's fetch-only endpoints answer it (issue #136)."""

    not_found: str = sentence()


@dataclass(frozen=True)
class CharactersRefusalWords:
    """The whole file: one table per module, and the phrases they share."""

    common: CommonWords
    services: ServicesWords
    purse: PurseWords
    constants: ConstantsWords
    views_access: ViewsAccessWords


def load_refusal_words(path: Path) -> CharactersRefusalWords:
    """The characters app's refusals, read from ``path``.

    Args:
        path: The TOML file, normally ``config/characters-refusals.toml`` or
            whatever ``CHARACTERS_REFUSAL_WORDS_FILE`` names.

    Returns:
        The words, one dataclass per table.

    Raises:
        ImproperlyConfigured: as :func:`common.wording.load_wording` does.
    """
    return load_wording(path, CharactersRefusalWords, "CHARACTERS_REFUSAL_WORDS_FILE")


def refusal_words() -> CharactersRefusalWords:
    """The loaded words (``settings.CHARACTERS_REFUSAL_WORDS``)."""
    return settings.CHARACTERS_REFUSAL_WORDS


def game_master_call_note(call: str) -> str:
    """What a check's or a cast's line ends with when a Game Master called it
    past the rules (issues #863, #840), or ``""`` when nothing was.

    Args:
        call: What the Game Master called the line past, as the line's
            ``game_master_call`` holds it.
    """
    if not call:
        return ""
    return f" ({refusal_words().common.a_game_masters_call}: {call})"
