"""The words a Game Master's rulings on a battle are shown in (issues #867, #37).

Content, not code (the house rule of 2026-10-17, issue #77): the panels'
headings and buttons, the battle-log lines and the refusals live in
``config/battle-rulings.toml``, which Spencer edits with no Python, read once at
startup into ``settings.BATTLE_RULINGS_WORDING`` by :func:`load_rulings_wording`.
A missing or unknown key, a value that is not text, and a ``{name}`` a sentence
may not use all stop the server then, not in front of a player.

Each table of the file is one dataclass below and each key one field, so code
and templates read a sentence as an attribute (``words().magic_bounds.heading``)
and no key is spelled twice. A field's metadata names the ``{placeholders}`` its
sentence may use; a sentence may leave any of them out.

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
    """Words both rulings use."""

    game_master: str = sentence("name")
    and_word: str = sentence()
    unknown_figure: str = sentence()


@dataclass(frozen=True)
class HthAgreementWords:
    """The HTH-by-agreement ruling's words (#867)."""

    heading: str = sentence()
    game_master_only: str = sentence()
    none_marked: str = sentence()
    whole_battle_line: str = sentence()
    pairs_line: str = sentence("pairs")
    pair_names: str = sentence("first", "second")
    log_battle_marked: str = sentence("by")
    log_battle_lifted: str = sentence("by")
    log_pair_marked: str = sentence("by", "pair")
    log_pair_lifted: str = sentence("by", "pair")
    mark_whole_battle: str = sentence()
    lift_whole_battle: str = sentence()
    lift_pair: str = sentence()
    mark_pair: str = sentence()
    first_of_pair: str = sentence()
    second_of_pair: str = sentence()
    refuse_scope: str = sentence()
    refuse_pair: str = sentence()
    refuse_pair_missing: str = sentence()
    refuse_same_side: str = sentence("first", "second")
    refuse_classic: str = sentence()


@dataclass(frozen=True)
class MagicBoundsWords:
    """The magic-bounds ruling's words (#37)."""

    heading: str = sentence()
    channel: str = sentence()
    push_cap: str = sentence()
    counter_bounds: str = sentence()
    game_master_only: str = sentence()
    none_lifted: str = sentence("bounds")
    lifting_line: str = sentence("bound", "name", "by")
    log_lifted: str = sentence("by", "bounds", "name")
    log_held: str = sentence("by", "name", "bounds")
    past_by_ruling: str = sentence("bounds")
    figure_label: str = sentence()
    lift_button: str = sentence()
    hold_again: str = sentence()
    refuse_bounds: str = sentence("bounds")
    refuse_bounds_list: str = sentence()
    refuse_combatant: str = sentence()
    refuse_classic: str = sentence()


@dataclass(frozen=True)
class RulingsWording:
    """The whole file: one table per ruling, and the words they share."""

    common: CommonWords
    hth_agreement: HthAgreementWords
    magic_bounds: MagicBoundsWords


def load_rulings_wording(path: Path) -> RulingsWording:
    """The rulings' words, read from ``path``.

    Args:
        path: The TOML file, normally ``config/battle-rulings.toml`` or
            whatever ``BATTLE_RULINGS_WORDING_FILE`` names.

    Returns:
        The words, one dataclass per table.

    Raises:
        ImproperlyConfigured: The file cannot be read or is not TOML; a table
            is missing, or has an unknown or a missing key; a value is not
            text; or a sentence uses a ``{name}`` it may not.
    """
    return load_wording(path, RulingsWording, "BATTLE_RULINGS_WORDING_FILE")


def words() -> RulingsWording:
    """The loaded words (``settings.BATTLE_RULINGS_WORDING``)."""
    return settings.BATTLE_RULINGS_WORDING


def name_of(names: dict[int, str], combatant_id: int) -> str:
    """A figure's name, or the words for one no longer in the battle."""
    return names.get(combatant_id, words().common.unknown_figure)


def listed(items: list[str]) -> str:
    """Items in prose: "A", "A and B", "A, B and C"."""
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} {words().common.and_word} {items[-1]}"


def game_master_name(username: str) -> str:
    """How the page and the log name the Game Master who made a ruling."""
    return words().common.game_master.format(name=username)
