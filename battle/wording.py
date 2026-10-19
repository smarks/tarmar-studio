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

import string
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, get_type_hints

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

#: The field metadata key naming the placeholders a sentence may use.
PLACEHOLDERS = "placeholders"


def _sentence(*placeholders: str) -> Any:
    """A wording field whose sentence may use ``placeholders``."""
    return field(metadata={PLACEHOLDERS: frozenset(placeholders)})


@dataclass(frozen=True)
class CommonWords:
    """Words both rulings use."""

    game_master: str = _sentence("name")
    and_word: str = _sentence()
    unknown_figure: str = _sentence()


@dataclass(frozen=True)
class HthAgreementWords:
    """The HTH-by-agreement ruling's words (#867)."""

    heading: str = _sentence()
    game_master_only: str = _sentence()
    none_marked: str = _sentence()
    whole_battle_line: str = _sentence()
    pairs_line: str = _sentence("pairs")
    pair_names: str = _sentence("first", "second")
    log_battle_marked: str = _sentence("by")
    log_battle_lifted: str = _sentence("by")
    log_pair_marked: str = _sentence("by", "pair")
    log_pair_lifted: str = _sentence("by", "pair")
    mark_whole_battle: str = _sentence()
    lift_whole_battle: str = _sentence()
    lift_pair: str = _sentence()
    mark_pair: str = _sentence()
    first_of_pair: str = _sentence()
    second_of_pair: str = _sentence()
    refuse_scope: str = _sentence()
    refuse_pair: str = _sentence()
    refuse_pair_missing: str = _sentence()
    refuse_same_side: str = _sentence("first", "second")
    refuse_classic: str = _sentence()


@dataclass(frozen=True)
class MagicBoundsWords:
    """The magic-bounds ruling's words (#37)."""

    heading: str = _sentence()
    channel: str = _sentence()
    push_cap: str = _sentence()
    counter_bounds: str = _sentence()
    game_master_only: str = _sentence()
    none_lifted: str = _sentence("bounds")
    lifting_line: str = _sentence("bound", "name", "by")
    log_lifted: str = _sentence("by", "bounds", "name")
    log_held: str = _sentence("by", "name", "bounds")
    past_by_ruling: str = _sentence("bounds")
    figure_label: str = _sentence()
    lift_button: str = _sentence()
    hold_again: str = _sentence()
    refuse_bounds: str = _sentence("bounds")
    refuse_bounds_list: str = _sentence()
    refuse_combatant: str = _sentence()
    refuse_classic: str = _sentence()


@dataclass(frozen=True)
class RulingsWording:
    """The whole file: one table per ruling, and the words they share."""

    common: CommonWords
    hth_agreement: HthAgreementWords
    magic_bounds: MagicBoundsWords


def _table(path: Path, name: str, raw: Any, words_type: type) -> Any:
    """One table of the file as its dataclass, or ``ImproperlyConfigured``."""
    where = f"BATTLE_RULINGS_WORDING_FILE {path} [{name}]"
    if not isinstance(raw, dict):
        raise ImproperlyConfigured(f"{where} is missing or is not a table")
    known = {word_field.name: word_field for word_field in fields(words_type)}
    unknown = sorted(set(raw) - set(known))
    missing = sorted(set(known) - set(raw))
    if unknown or missing:
        raise ImproperlyConfigured(
            f"{where}: unknown keys {unknown}, missing keys {missing}"
        )
    for key, word_field in known.items():
        value = raw[key]
        if not isinstance(value, str) or not value.strip():
            raise ImproperlyConfigured(f"{where} {key} is not a sentence")
        allowed = word_field.metadata[PLACEHOLDERS]
        # A trial fill with every name the sentence may use catches what a
        # Game Master would otherwise meet: a name it may not use, a stray or
        # unbalanced brace, a positional {} or a nested {name:{spec}}.
        try:
            value.format(**dict.fromkeys(allowed, ""))
        except (KeyError, IndexError, ValueError, AttributeError) as error:
            raise ImproperlyConfigured(
                f"{where} {key} cannot be filled ({error!r}); it may use only "
                f"{sorted(allowed)}"
            ) from error
        # An empty trial value fills shapes the real ones break on ("{by:>{name}}"
        # pads to a width read from a name), so each field must be a bare name:
        # no format spec, no conversion, no attribute or index.
        for _text, field_name, spec, conversion in string.Formatter().parse(value):
            if field_name is None:
                continue
            if spec or conversion or "." in field_name or "[" in field_name:
                raise ImproperlyConfigured(
                    f"{where} {key} has a field that is not a bare name: "
                    f"{{{field_name}{'!' + conversion if conversion else ''}"
                    f"{':' + spec if spec else ''}}}; write {{name}} alone"
                )
    return words_type(**raw)


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
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ImproperlyConfigured(
            f"BATTLE_RULINGS_WORDING_FILE {path} cannot be read ({error}); the "
            "battle's rulings need their words (issues #867, #37)."
        ) from error
    tables = get_type_hints(RulingsWording)
    unknown = sorted(set(raw) - set(tables))
    if unknown:
        raise ImproperlyConfigured(
            f"BATTLE_RULINGS_WORDING_FILE {path}: unknown tables {unknown}"
        )
    return RulingsWording(
        **{
            name: _table(path, name, raw.get(name), words_type)
            for name, words_type in tables.items()
        }
    )


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
