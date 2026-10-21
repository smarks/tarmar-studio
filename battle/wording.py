"""The words a Game Master's rulings on a battle are shown in (issues #867, #37).

Content, not code (the house rule of 2026-10-17, issue #77): the panels'
headings and buttons, the battle-log lines, the refusals and the turn menu's
lines (#90), and the battle log's other lines, the roster's notes and who
decides for a figure (#136) live in ``config/battle-rulings.toml``, which
Spencer edits with no
Python, read once at startup into ``settings.BATTLE_RULINGS_WORDING`` by
:func:`load_rulings_wording`. A missing or unknown key, a value that is not
text, and a ``{name}`` a sentence may not use all stop the server then, not in
front of a player.

Each table of the file is one dataclass below and each key one field, so code
and templates read a sentence as an attribute (``words().magic_bounds.heading``)
and no key is spelled twice. A field's metadata names the ``{placeholders}`` its
sentence may use; a sentence may leave any of them out, except the names a field requires.

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
    or_word: str = sentence()
    unknown_figure: str = sentence()


@dataclass(frozen=True)
class TurnMenuWords:
    """The turn menu's words both profiles' lines use (#90)."""

    the_weapon: str = sentence("weapon")
    bare_hands: str = sentence()
    with_odds: str = sentence("line", "odds", required=("line", "odds"))
    no_nearer: str = sentence("move", "aimed")
    ends_from: str = sentence("move", "distance", "aimed")
    standing: str = sentence("reason", required=("reason",))
    stand: str = sentence("reason", required=("reason",))
    reach: str = sentence("move", "hexes", required=("move",))
    no_attack_charge: str = sentence("aimed", "reason", required=("reason",))
    attack: str = sentence("aimed", "weapon")
    shoot: str = sentence("aimed", "weapon")
    last_shot: str = sentence("aimed", "weapon")
    cast: str = sentence("spell")
    cast_at: str = sentence("spell", "aimed")
    ready: str = sentence("weapon")
    ready_another: str = sentence()
    change_to: str = sentence("weapon")
    change_weapons: str = sentence()
    dropped: str = sentence("weapon")
    pick_up: str = sentence("weapon")
    stand_up: str = sentence()
    under_one: str = sentence()
    about: str = sentence("damage")


@dataclass(frozen=True)
class TarmarMenuWords:
    """A Tarmar figure's turn menu's own words (#788, #56, #57, #90)."""

    odds: str = sentence("faces", "sides", "dice", "average")
    odds_armoured: str = sentence("faces", "sides", "dice", "stops", "average")
    clear_gait: str = sentence("gait")
    nearest_enemy: str = sentence()
    run: str = sentence("hexes", "aimed")
    sprint: str = sentence("hexes", "aimed")
    fatigue: str = sentence("fatigue")
    fatigue_if_moving: str = sentence("fatigue")
    charge: str = sentence("aimed", "gait", "hexes", "weapon")
    dodge_guards: str = sentence()
    defend_guards: str = sentence()
    dodge: str = sentence("guards", required=("guards",))
    dodge_standing: str = sentence("line", "standing", required=("line", "standing"))
    dodge_toward: str = sentence("line", "aimed", required=("line",))
    defend: str = sentence("guards", required=("guards",))
    drop: str = sentence("gait", "hexes")
    leaves_hand: str = sentence("weapon")
    press_again: str = sentence("spell")
    press_again_at: str = sentence("spell", "aimed")
    pushing: str = sentence("line", "pushed", required=("line",))
    push_cost: str = sentence("total", "level", "pushed", "paid", required=("paid",))
    cast_cost: str = sentence("mana", "paid", required=("paid",))
    paid_on_success: str = sentence()
    paid_on_success_or_total: str = sentence("totals")
    borrowing: str = sentence("line", "owed", required=("line",))
    borrowing_cost: str = sentence("cost", "owed", required=("cost",))
    pressing_cost: str = sentence("cost", required=("cost",))
    anchor: str = sentence()
    self_anchor: str = sentence("spell", "dice", "attribute")
    helper_anchor: str = sentence("name", "spell", "dice", "attribute")
    helper_anchor_cost: str = sentence("mana")
    helper_caught: str = sentence("cost", "spell", required=("cost",))
    counter: str = sentence("name", "mana")
    counter_cost: str = sentence("mana", "name")
    an_enemy_caster: str = sentence()
    step_away: str = sentence()
    step_away_stuck: str = sentence("line", "stuck", required=("line", "stuck"))
    no_step_away: str = sentence("threat")
    grapple: str = sentence("aimed")
    close_strike: str = sentence("aimed", "weapon")
    draw_dagger: str = sentence()
    break_away: str = sentence()
    break_away_stuck: str = sentence("line", "stuck", required=("line", "stuck"))
    break_away_steps: str = sentence("line", required=("line",))
    struggle_free: str = sentence("aimed")
    hold_still: str = sentence("aimed")
    squeeze: str = sentence("aimed")
    maintain: str = sentence("aimed")
    release: str = sentence("aimed")
    pick_up_any: str = sentence()
    yielded: str = sentence("line", required=("line",))
    yielded_blocked: str = sentence("line", required=("line",))
    cost_past_ruling: str = sentence("cost", "past", required=("cost", "past"))


@dataclass(frozen=True)
class ClassicMenuWords:
    """A classic Melee figure's turn menu's own words (#890, #56, #57, #90)."""

    move_word: str = sentence()
    no_foe_in_reach: str = sentence("reason", "weapon", required=("reason",))
    charge: str = sentence("aimed", "hexes", "weapon")
    charge_another: str = sentence("aimed", "hexes", "struck", "weapon")
    charge_grapple: str = sentence("aimed", "hexes", "grapple")
    charge_grapple_another: str = sentence("aimed", "hexes", "struck", "grapple")
    grapple_words: str = sentence()
    shift_attack: str = sentence("aimed", "weapon")
    odds: str = sentence("needed", "dice", "chance")
    odds_damage: str = sentence("odds", "damage", "average", required=("odds",))
    odds_damage_armoured: str = sentence(
        "odds", "damage", "stops", "average", required=("odds",)
    )
    charge_dice: str = sentence("damage", "extra", required=("damage",))
    charge_dice_armoured: str = sentence("damage", "extra", required=("damage",))
    move: str = sentence("hexes")
    move_toward: str = sentence("hexes", "aimed")
    half_move: str = sentence("hexes")
    half_move_toward: str = sentence("hexes", "aimed")
    dodge: str = sentence("how", "dice", "plain_dice", required=("how",))
    dodge_moving: str = sentence("hexes")
    dodge_moving_toward: str = sentence("hexes", "aimed")
    shift_defend: str = sentence("dice", "plain_dice")
    pick_up_any: str = sentence()
    crawl: str = sentence("hexes")
    disengage: str = sentence()
    hth_attack: str = sentence("aimed", "grapple")
    cast_cost: str = sentence("strength")
    go_prone: str = sentence()
    kneel: str = sentence()
    do_nothing: str = sentence()
    pass_turn: str = sentence()


@dataclass(frozen=True)
class TurnPanelWords:
    """The turn panel's own words, around the server's lines (#94): the
    prompt, each option's cost and the AI's pick, the push, borrow and counter
    controls, the cast stage, the hex picker and the buttons."""

    waiting: str = sentence("name", required=("name",))
    waiting_played: str = sentence("name", "player", required=("name",))
    ai_choose: str = sentence()
    your_move: str = sentence("name", "turn", required=("name",))
    options_group: str = sentence()
    ai_pick: str = sentence()
    advice: str = sentence("score", "rationale", required=("rationale",))
    show_scoring: str = sentence()
    hide_scoring: str = sentence()
    costs: str = sentence("cost", required=("cost",))
    costs_nothing: str = sentence()
    cast_option: str = sentence()
    casts_ready_one: str = sentence()
    casts_ready: str = sentence("count")
    ready_borrowing: str = sentence("ready", "count", required=("ready",))
    cast_advice: str = sentence("pool", "ready", required=("pool", "ready"))
    spells_group: str = sentence()
    pool: str = sentence("pool_label", "pool", "max_pool")
    pool_owing: str = sentence("pool_label", "pool", "max_pool", "owed")
    stage_pool: str = sentence("pool", required=("pool",))
    not_castable: str = sentence()
    stand_down: str = sentence()
    push_label: str = sentence()
    push_label_channel: str = sentence("channel")
    push_none: str = sentence()
    push_amount: str = sentence("mana")
    push_group: str = sentence("spell")
    borrow_label: str = sentence()
    borrow_amount: str = sentence("borrowed")
    borrow_pushed: str = sentence("pushed", "borrowed")
    borrow_group: str = sentence("spell")
    the_caster: str = sentence()
    counter_label: str = sentence("caster")
    counter_amount: str = sentence("mana")
    counter_group: str = sentence("caster")
    presses_again: str = sentence()
    moving_to: str = sentence("hex")
    pick_hex: str = sentence("count")
    leave_hex: str = sentence()
    confirm: str = sentence()


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
class ServicesWords:
    """``battle.services``'s words that are not refusals (issue #136): the
    battle log's lines, the notes a fight's roster and seats carry, and a cast
    option's cost."""

    records_withheld: str = sentence("name", "reason", required=("reason",))
    withheld_other_players: str = sentence()
    withheld_not_game_master: str = sentence()
    a_character: str = sentence()
    pools_taken: str = sentence("pools", required=("pools",))
    figure_pools: str = sentence(
        "name", "fatigue_was", "fatigue", "body_was", "body", "mana_was", "mana"
    )
    figure_owed: str = sentence("owed_was", "owed")
    stays_dead: str = sentence("names", required=("names",))
    stay_dead: str = sentence("names", required=("names",))
    stays_off: str = sentence("names", required=("names",))
    stay_off: str = sentence("names", required=("names",))
    seat_taken: str = sentence("game_master", "name", "owner")
    seat_returned: str = sentence("name", "owner")
    leaves_fight: str = sentence("name", "owner")
    team_victorious: str = sentence("team", "names")
    victorious: str = sentence("name")
    stalemate: str = sentence("turns", "names")
    re_armed: str = sentence("names", required=("names",))
    no_spell_crosses: str = sentence("names", required=("names",))
    reach_no_spell: str = sentence("names", required=("names",))
    unarmed_one: str = sentence("names", required=("names",))
    unarmed: str = sentence("names", required=("names",))
    unarmed_attached: str = sentence("unarmed", required=("unarmed",))
    bare_handed_one: str = sentence("names", required=("names",))
    bare_handed: str = sentence("names", required=("names",))
    chosen_by: str = sentence("owner", "rationale", required=("rationale",))
    open_seat: str = sentence("number")
    cast_cost: str = sentence("school", "level")


@dataclass(frozen=True)
class ControlWords:
    """``battle.control``'s words (issue #136): who decides for a figure, the
    roster card's line about it, and the log line a change of mode writes."""

    ai_owner: str = sentence()
    guest_owner: str = sentence()
    you: str = sentence()
    their_owners: str = sentence()
    automatic: str = sentence()
    manual: str = sentence()
    manual_line: str = sentence("owner")
    automatic_line: str = sentence("ai")
    every_combatant_follows: str = sentence("mode", "decided_by")
    battle_default: str = sentence("mode", "decided_by")
    follows_again: str = sentence("name", "mode", "decided_by")
    played_by_hand: str = sentence("name", "decided_by")
    hands_to_ai: str = sentence("name", "ai")


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
    services: ServicesWords
    control: ControlWords


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
