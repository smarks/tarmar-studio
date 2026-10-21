"""The words a Game Master's rulings on a battle are shown in (issues #867, #37).

Content, not code (the house rule of 2026-10-17, issue #77): the panels'
headings and buttons, the battle-log lines, the refusals and the turn menu's
lines (#90) live in ``config/battle-rulings.toml``, which Spencer edits with no
Python, read once at startup into ``settings.BATTLE_RULINGS_WORDING`` by
:func:`load_rulings_wording`. A missing or unknown key, a value that is not
text, and a ``{name}`` a sentence may not use all stop the server then, not in
front of a player.

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

from common.message_templates import MessageTemplateError, require_bare_fields

#: The field metadata key naming the placeholders a sentence may use.
PLACEHOLDERS = "placeholders"

#: The field metadata key naming the placeholders a sentence must use (#90):
#: those whose loss would drop a whole line, a cost or a ruling the sentence
#: carries ({line}, {cost}, {past}, {odds} and their kin). A name whose loss
#: drops only a detail ({move} in no_nearer and ends_from, {average},
#: {weapon}, {grapple}, {aimed}, {name}) stays optional, as every other
#: placeholder is.
REQUIRED = "required"


def _sentence(*placeholders: str, required: tuple[str, ...] = ()) -> Any:
    """A wording field whose sentence may use ``placeholders`` and must use
    ``required``, which are among them."""
    return field(
        metadata={
            PLACEHOLDERS: frozenset(placeholders),
            REQUIRED: frozenset(required),
        }
    )


@dataclass(frozen=True)
class CommonWords:
    """Words both rulings use."""

    game_master: str = _sentence("name")
    and_word: str = _sentence()
    or_word: str = _sentence()
    unknown_figure: str = _sentence()


@dataclass(frozen=True)
class TurnMenuWords:
    """The turn menu's words both profiles' lines use (#90)."""

    the_weapon: str = _sentence("weapon")
    bare_hands: str = _sentence()
    with_odds: str = _sentence("line", "odds", required=("line", "odds"))
    no_nearer: str = _sentence("move", "aimed")
    ends_from: str = _sentence("move", "distance", "aimed")
    standing: str = _sentence("reason", required=("reason",))
    stand: str = _sentence("reason", required=("reason",))
    reach: str = _sentence("move", "hexes", required=("move",))
    no_attack_charge: str = _sentence("aimed", "reason", required=("reason",))
    attack: str = _sentence("aimed", "weapon")
    shoot: str = _sentence("aimed", "weapon")
    last_shot: str = _sentence("aimed", "weapon")
    cast: str = _sentence("spell")
    cast_at: str = _sentence("spell", "aimed")
    ready: str = _sentence("weapon")
    ready_another: str = _sentence()
    change_to: str = _sentence("weapon")
    change_weapons: str = _sentence()
    dropped: str = _sentence("weapon")
    pick_up: str = _sentence("weapon")
    stand_up: str = _sentence()
    under_one: str = _sentence()
    about: str = _sentence("damage")


@dataclass(frozen=True)
class TarmarMenuWords:
    """A Tarmar figure's turn menu's own words (#788, #56, #57, #90)."""

    odds: str = _sentence("faces", "sides", "dice", "average")
    odds_armoured: str = _sentence("faces", "sides", "dice", "stops", "average")
    clear_gait: str = _sentence("gait")
    nearest_enemy: str = _sentence()
    run: str = _sentence("hexes", "aimed")
    sprint: str = _sentence("hexes", "aimed")
    fatigue: str = _sentence("fatigue")
    fatigue_if_moving: str = _sentence("fatigue")
    charge: str = _sentence("aimed", "gait", "hexes", "weapon")
    dodge_guards: str = _sentence()
    defend_guards: str = _sentence()
    dodge: str = _sentence("guards", required=("guards",))
    dodge_standing: str = _sentence("line", "standing", required=("line", "standing"))
    dodge_toward: str = _sentence("line", "aimed", required=("line",))
    defend: str = _sentence("guards", required=("guards",))
    drop: str = _sentence("gait", "hexes")
    leaves_hand: str = _sentence("weapon")
    press_again: str = _sentence("spell")
    press_again_at: str = _sentence("spell", "aimed")
    pushing: str = _sentence("line", "pushed", required=("line",))
    push_cost: str = _sentence("total", "level", "pushed", "paid", required=("paid",))
    cast_cost: str = _sentence("mana", "paid", required=("paid",))
    paid_on_success: str = _sentence()
    paid_on_success_or_total: str = _sentence("totals")
    borrowing: str = _sentence("line", "owed", required=("line",))
    borrowing_cost: str = _sentence("cost", "owed", required=("cost",))
    pressing_cost: str = _sentence("cost", required=("cost",))
    anchor: str = _sentence()
    self_anchor: str = _sentence("spell", "dice", "attribute")
    helper_anchor: str = _sentence("name", "spell", "dice", "attribute")
    helper_anchor_cost: str = _sentence("mana")
    helper_caught: str = _sentence("cost", "spell", required=("cost",))
    counter: str = _sentence("name", "mana")
    counter_cost: str = _sentence("mana", "name")
    an_enemy_caster: str = _sentence()
    step_away: str = _sentence()
    step_away_stuck: str = _sentence("line", "stuck", required=("line", "stuck"))
    no_step_away: str = _sentence("threat")
    grapple: str = _sentence("aimed")
    close_strike: str = _sentence("aimed", "weapon")
    draw_dagger: str = _sentence()
    break_away: str = _sentence()
    break_away_stuck: str = _sentence("line", "stuck", required=("line", "stuck"))
    break_away_steps: str = _sentence("line", required=("line",))
    struggle_free: str = _sentence("aimed")
    hold_still: str = _sentence("aimed")
    squeeze: str = _sentence("aimed")
    maintain: str = _sentence("aimed")
    release: str = _sentence("aimed")
    pick_up_any: str = _sentence()
    yielded: str = _sentence("line", required=("line",))
    yielded_blocked: str = _sentence("line", required=("line",))
    cost_past_ruling: str = _sentence("cost", "past", required=("cost", "past"))


@dataclass(frozen=True)
class ClassicMenuWords:
    """A classic Melee figure's turn menu's own words (#890, #56, #57, #90)."""

    move_word: str = _sentence()
    no_foe_in_reach: str = _sentence("reason", "weapon", required=("reason",))
    charge: str = _sentence("aimed", "hexes", "weapon")
    charge_another: str = _sentence("aimed", "hexes", "struck", "weapon")
    charge_grapple: str = _sentence("aimed", "hexes", "grapple")
    charge_grapple_another: str = _sentence("aimed", "hexes", "struck", "grapple")
    grapple_words: str = _sentence()
    shift_attack: str = _sentence("aimed", "weapon")
    odds: str = _sentence("needed", "dice", "chance")
    odds_damage: str = _sentence("odds", "damage", "average", required=("odds",))
    odds_damage_armoured: str = _sentence(
        "odds", "damage", "stops", "average", required=("odds",)
    )
    charge_dice: str = _sentence("damage", "extra", required=("damage",))
    charge_dice_armoured: str = _sentence("damage", "extra", required=("damage",))
    move: str = _sentence("hexes")
    move_toward: str = _sentence("hexes", "aimed")
    half_move: str = _sentence("hexes")
    half_move_toward: str = _sentence("hexes", "aimed")
    dodge: str = _sentence("how", "dice", "plain_dice", required=("how",))
    dodge_moving: str = _sentence("hexes")
    dodge_moving_toward: str = _sentence("hexes", "aimed")
    shift_defend: str = _sentence("dice", "plain_dice")
    pick_up_any: str = _sentence()
    crawl: str = _sentence("hexes")
    disengage: str = _sentence()
    hth_attack: str = _sentence("aimed", "grapple")
    cast_cost: str = _sentence("strength")
    go_prone: str = _sentence()
    kneel: str = _sentence()
    do_nothing: str = _sentence()
    pass_turn: str = _sentence()


@dataclass(frozen=True)
class TurnPanelWords:
    """The turn panel's own words, around the server's lines (#94): the
    prompt, each option's cost and the AI's pick, the push, borrow and counter
    controls, the cast stage, the hex picker and the buttons."""

    waiting: str = _sentence("name", required=("name",))
    waiting_played: str = _sentence("name", "player", required=("name",))
    ai_choose: str = _sentence()
    your_move: str = _sentence("name", "turn", required=("name",))
    options_group: str = _sentence()
    ai_pick: str = _sentence()
    advice: str = _sentence("score", "rationale", required=("rationale",))
    show_scoring: str = _sentence()
    hide_scoring: str = _sentence()
    costs: str = _sentence("cost", required=("cost",))
    costs_nothing: str = _sentence()
    cast_option: str = _sentence()
    casts_ready_one: str = _sentence()
    casts_ready: str = _sentence("count")
    ready_borrowing: str = _sentence("ready", "count", required=("ready",))
    cast_advice: str = _sentence("pool", "ready", required=("pool", "ready"))
    spells_group: str = _sentence()
    pool: str = _sentence("pool_label", "pool", "max_pool")
    pool_owing: str = _sentence("pool_label", "pool", "max_pool", "owed")
    stage_pool: str = _sentence("pool", required=("pool",))
    not_castable: str = _sentence()
    stand_down: str = _sentence()
    push_label: str = _sentence()
    push_label_channel: str = _sentence("channel")
    push_none: str = _sentence()
    push_amount: str = _sentence("mana")
    push_group: str = _sentence("spell")
    borrow_label: str = _sentence()
    borrow_amount: str = _sentence("borrowed")
    borrow_pushed: str = _sentence("pushed", "borrowed")
    borrow_group: str = _sentence("spell")
    the_caster: str = _sentence()
    counter_label: str = _sentence("caster")
    counter_amount: str = _sentence("mana")
    counter_group: str = _sentence("caster")
    presses_again: str = _sentence()
    moving_to: str = _sentence("hex")
    pick_hex: str = _sentence("count")
    leave_hex: str = _sentence()
    confirm: str = _sentence()


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
    turn_menu: TurnMenuWords
    tarmar_menu: TarmarMenuWords
    classic_menu: ClassicMenuWords
    turn_panel: TurnPanelWords
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
        # An empty trial value fills shapes the real ones break on, and the
        # page fills some of these itself (#94): each field is a bare name.
        try:
            require_bare_fields(f"{where} {key}", value)
        except MessageTemplateError as error:
            raise ImproperlyConfigured(str(error)) from error
        used = {
            field_name
            for _text, field_name, _spec, _conversion in string.Formatter().parse(value)
        }
        dropped = sorted(word_field.metadata[REQUIRED] - used)
        if dropped:
            raise ImproperlyConfigured(
                f"{where} {key} must use {dropped}: each carries another sentence"
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
