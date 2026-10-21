"""The words the battle app refuses a request in (issues #97, #86).

Content, not code (the house rule of 2026-10-17, issue #77): every sentence a
battle service, view or control module answers a refused request with lives in
``config/battle-refusals.toml``, one table per module, which Spencer edits with
no Python. settings.py reads it once at startup into ``settings.BATTLE_REFUSAL_WORDS``
through :func:`load_refusal_words`; :func:`common.wording.load_wording` checks
it there, so a missing key or a stray ``{name}`` stops the server rather than
reaching a player. The words of the Game Master's rulings on a battle are
``battle.wording``'s, in ``config/battle-rulings.toml``.

Moving a refusal's words here changes only what the player reads, never who is
refused: the condition in front of each ``raise`` decides that.

settings.py imports this module while it is still loading, so nothing here
reads the settings at import time (``django.conf.settings`` is lazy).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from common.wording import load_wording, sentence


@dataclass(frozen=True)
class ServicesWords:
    """``battle.services``'s refusals: setup, muster, lobby, seats, the fight."""

    guest_saved_characters: str = sentence()
    character_twice: str = sentence()
    not_seatable_characters: str = sentence("ids")
    combatant_count: str = sentence("fewest", "most", "total")
    team_needs_name: str = sentence()
    no_such_team: str = sentence()
    no_such_muster_entry: str = sentence()
    no_such_party: str = sentence()
    party_has_no_members: str = sentence("party")
    nobody_in_party_can_join: str = sentence("party", "hidden", "fallen", "already")
    no_room_for_party: str = sentence("party", "joining", "mustered", "most")
    muster_full: str = sentence("most")
    unknown_archetype: str = sentence()
    no_such_beast: str = sentence()
    unknown_beast_category: str = sentence()
    unknown_difficulty_band: str = sentence()
    not_a_seatable_character: str = sentence()
    already_on_muster: str = sentence("name")
    unknown_roster_entry_kind: str = sentence()
    battle_needs_name: str = sentence()
    choose_character_or_archetype: str = sentence()
    no_guest_session: str = sentence()
    guest_own_character: str = sentence()
    not_open_for_joining: str = sentence()
    already_hold_a_seat: str = sentence()
    seat_already_taken: str = sentence()
    not_your_living_character: str = sentence()
    not_an_occupied_seat: str = sentence()
    release_seat_player_or_dm: str = sentence()
    not_your_seat: str = sentence()
    died_since_claiming: str = sentence("name")
    already_started: str = sentence()
    every_player_ready: str = sentence()
    too_few_combatants: str = sentence("fewest")
    results_recorded: str = sentence()
    stale_pools: str = sentence("names")
    resnapshot_game_master_only: str = sentence()
    not_started: str = sentence()
    pools_already_held: str = sentence()
    no_starting_snapshot: str = sentence()
    no_such_combatant: str = sentence()
    take_seat_game_master_only: str = sentence()
    withdrawn_seat: str = sentence("name")
    return_seat_owner_or_game_master: str = sentence()
    no_seat_to_return: str = sentence("name")
    no_seat_to_take: str = sentence("name")
    already_play_seat: str = sentence("name")
    owner_already_plays: str = sentence("owner", "name")
    battle_over: str = sentence()
    no_owner_to_stand_in_for: str = sentence()
    no_input_pending: str = sentence()
    stale_choice: str = sentence(
        "chosen_name", "chosen_turn", "awaited_name", "awaited_turn"
    )
    stale_choice_unknown_figure: str = sentence()
    hand_choice_to_ai_denied: str = sentence()
    no_pending_menu: str = sentence()
    not_your_combatant: str = sentence()
    option_not_on_menu: str = sentence()
    option_cannot_be_carried_out: str = sentence("option")
    destination_not_marked: str = sentence("option", "name", "hex")
    unnamed_option: str = sentence()
    name_taken: str = sentence()
    still_in_lobby: str = sentence()


@dataclass(frozen=True)
class ViewsWords:
    """``battle.views``'s refusals: who may act, and malformed requests."""

    control_denied: str = sentence()
    foes_belong_to_game_master: str = sentence()
    seed_out_of_range: str = sentence("highest")
    unknown_rules_profile: str = sentence()
    draft_gone: str = sentence()
    unknown_setup_field: str = sentence()
    numeric_entry_index: str = sentence()
    numeric_party_id: str = sentence()
    combatant_control_denied: str = sentence()
    numeric_combatant_id: str = sentence()
    combatant_needs_name: str = sentence()
    name_characters: str = sentence()
    start_denied: str = sentence()
    choice_combatant_id: str = sentence()
    choice_turn: str = sentence()
    numeric_target_id: str = sentence()
    numeric_push_mana: str = sentence()
    borrow_true_or_false: str = sentence()
    numeric_anchor_target: str = sentence()
    numeric_counter_mana: str = sentence()
    destination_is_hex: str = sentence()
    numeric_after_marker: str = sentence()


@dataclass(frozen=True)
class GuestNotes:
    """What a guest is told at the control a limit applies to.

    The templates render these beside a disabled control, and the setup views
    refuse a guest's post with the same words.
    """

    saved_characters: str = sentence()
    claim_character: str = sentence()
    parties: str = sentence()
    keep: str = sentence()


@dataclass(frozen=True)
class ControlWords:
    """``battle.control``'s refusals: a control mode that is not one."""

    battle_mode: str = sentence()
    combatant_mode: str = sentence()


@dataclass(frozen=True)
class BattleRefusalWords:
    """The whole file: one table per module, and the guests' notes."""

    services: ServicesWords
    views: ViewsWords
    guest_notes: GuestNotes
    control: ControlWords


def load_refusal_words(path: Path) -> BattleRefusalWords:
    """The battle app's refusals, read from ``path``.

    Args:
        path: The TOML file, normally ``config/battle-refusals.toml`` or
            whatever ``BATTLE_REFUSAL_WORDS_FILE`` names.

    Returns:
        The words, one dataclass per table.

    Raises:
        ImproperlyConfigured: as :func:`common.wording.load_wording` does.
    """
    return load_wording(path, BattleRefusalWords, "BATTLE_REFUSAL_WORDS_FILE")


def refusal_words() -> BattleRefusalWords:
    """The loaded words (``settings.BATTLE_REFUSAL_WORDS``)."""
    return settings.BATTLE_REFUSAL_WORDS
