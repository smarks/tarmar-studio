"""The words the Games Master app refuses a request in (issue #97).

Content, not code (the house rule of 2026-10-17, issue #77): the refusals the
app's views, hand edits, fights, Luck, withdrawals, a turn's plan policy and
its executor answer with live in
``config/gm-refusals.toml``, one table per module, which Spencer edits with no
Python. settings.py reads it once at startup into ``settings.GM_REFUSAL_WORDS``
through :func:`load_refusal_words`; :func:`common.wording.load_wording` checks it
there, so a missing key or a stray ``{name}`` stops the server rather than
reaching a player.

Moving a refusal's words here changes only what the player reads, never who is
refused: the condition in front of each refusal decides that.

settings.py imports this module while it is still loading, so nothing here
reads the settings at import time (``django.conf.settings`` is lazy).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from common.wording import load_wording, sentence


@dataclass(frozen=True)
class HandRecordsWords:
    """``gm.hand_records``'s refusals: a Game Master setting records by hand."""

    game_master_only: str = sentence()
    recap_while_turn_running: str = sentence()
    not_text: str = sentence("label")
    needed: str = sentence("label")
    ruling_not_recorded: str = sentence()
    no_such_ruling: str = sentence()
    no_such_entity: str = sentence()
    entity_kind: str = sentence()
    entity_already_recorded: str = sentence("name")
    entity_recorded_as_another_row: str = sentence("name")
    no_such_thread: str = sentence()
    thread_already_resolved: str = sentence("thread")
    thread_still_open: str = sentence("thread")
    not_folded_yet: str = sentence()
    label_topic: str = sentence()
    label_rule: str = sentence()
    label_name: str = sentence()
    label_where: str = sentence()
    label_wants: str = sentence()
    label_condition: str = sentence()
    label_notes: str = sentence()
    label_thread: str = sentence()
    label_resolution: str = sentence()
    label_recap: str = sentence()
    ruling_corrected: str = sentence("old", "new", "text")
    entity_recorded: str = sentence("line", required=("line",))
    entity_corrected: str = sentence("line", required=("line",))
    entity_removed: str = sentence("name")
    thread_opened: str = sentence("thread", "text")
    thread_corrected: str = sentence("thread", "text")
    thread_closed: str = sentence("thread", "text")
    thread_reopened: str = sentence("thread", "text")
    recap_corrected: str = sentence()


@dataclass(frozen=True)
class PolicyWords:
    """``gm.turn_engine.policy``'s refusals: a plan asking for what its turn
    may not do."""

    game_master_only: str = sentence()
    house_rule: str = sentence()
    literacy_ruling: str = sentence()
    literacy_ruling_origin: str = sentence("origin", required=("origin",))
    player_stated_scores: str = sentence()
    player_stated_fatigue: str = sentence()
    game_master_records: str = sentence()
    aide_unasked_roll: str = sentence()


@dataclass(frozen=True)
class RepeatedRefusalWords:
    """What ``gm.turn_engine.policy`` tells the model when a plan repeats a
    refusal already settled in the session, and what to do instead (issue
    #136). Written for the model, not a player; kept apart from
    :class:`PolicyWords`, whose keys are exactly the policy's reasons."""

    already_refused: str = sentence("action", "reason", required=("reason",))
    with_remedy: str = sentence("said", "remedy", required=("said", "remedy"))
    something_else: str = sentence("said", required=("said",))
    instead_record_attributes: str = sentence()
    instead_rule_literacy: str = sentence()
    instead_roll_fatigue: str = sentence()
    instead_origins: str = sentence("origins", required=("origins",))


@dataclass(frozen=True)
class BattleReportWords:
    """``gm.battle_report``'s words: how the player's notice of a session
    fight's end opens, and its verdict for each side (issues #785, #136)."""

    fight_over: str = sentence()
    after_restart: str = sentence()
    foes_victorious: str = sentence("names")
    party_victorious: str = sentence("names")


@dataclass(frozen=True)
class ViewsWords:
    """``gm.views``'s refusals and failures: the chat, the fight command, the
    session's own endpoints and Claude's reviews."""

    server_error: str = sentence()
    turn_already_running: str = sentence()
    fight_while_turn_running: str = sentence()
    fight_model_unreachable: str = sentence()
    turn_already_answered: str = sentence()
    plan_unreadable: str = sentence()
    invalid_session_id: str = sentence()
    session_not_found: str = sentence()
    invalid_character_id: str = sentence()
    character_not_found: str = sentence()
    message_not_text: str = sentence()
    no_message: str = sentence()
    rules_not_found: str = sentence()
    turn_not_found: str = sentence()
    name_not_text: str = sentence()
    no_session_id: str = sentence()
    session_needs_name: str = sentence()
    resolved_from_not_thread: str = sentence()
    attach_or_send: str = sentence()
    foes_not_text: str = sentence()
    send_to_claude_game_master_only: str = sentence()
    note_not_text: str = sentence()
    reviews_game_master_only: str = sentence()


@dataclass(frozen=True)
class ViewsRecordsWords:
    """``gm.views_records``'s refusals: a hand edit's request."""

    change_one_of: str = sentence("changes")


@dataclass(frozen=True)
class ViewsHistoryWords:
    """``gm.views_history``'s refusals: paging back through a session."""

    before_not_turn: str = sentence()
    since_not_turn: str = sentence()
    before_not_thread: str = sentence()


@dataclass(frozen=True)
class FightWords:
    """``gm.fight``'s refusals: taking a session's fight to the board."""

    name_a_foe: str = sentence()
    could_not_say_who: str = sentence()
    found_nobody: str = sentence()
    no_foes_in_bestiary: str = sentence()
    bestiary_empty: str = sentence()
    not_a_calling: str = sentence("calling")
    fight_under_way: str = sentence()
    other_players_characters: str = sentence()
    attach_living_character: str = sentence()
    field_full: str = sentence("most")
    no_foes: str = sentence()


@dataclass(frozen=True)
class ExecutorWords:
    """``gm.turn_engine.executor``'s refusals: an action of a plan that will
    not run against the records."""

    not_yours: str = sentence()
    no_such_character: str = sentence("id")
    duplicate_character_name: str = sentence("name", "id")
    not_created: str = sentence("ref")
    no_character_named: str = sentence()
    character_gone: str = sentence()
    not_carrying: str = sentence("item")
    already_recorded_as: str = sentence("name", "kind")
    no_open_thread: str = sentence("id")
    no_active_ruling: str = sentence("id")


@dataclass(frozen=True)
class WithdrawWords:
    """``gm.withdraw``'s refusals: withdrawing a character from a session."""

    owner_only: str = sentence()
    session_id_not_id: str = sentence()


@dataclass(frozen=True)
class LuckWords:
    """``gm.luck``'s refusals: spending a level's Luck in a session."""

    not_in_session: str = sentence()
    no_luck: str = sentence("name")
    luck_spent: str = sentence("name", "level")
    character_id_not_id: str = sentence()


@dataclass(frozen=True)
class AttachWords:
    """``gm.attach``'s refusals: a character that may not join a session.
    One the user may not attach is ``[views] character_not_found``."""

    dead: str = sentence()
    incomplete: str = sentence("missing")


@dataclass(frozen=True)
class GmRefusalWords:
    """The whole file: one table per module."""

    hand_records: HandRecordsWords
    policy: PolicyWords
    repeated_refusal: RepeatedRefusalWords
    battle_report: BattleReportWords
    views: ViewsWords
    views_records: ViewsRecordsWords
    views_history: ViewsHistoryWords
    fight: FightWords
    executor: ExecutorWords
    withdraw: WithdrawWords
    luck: LuckWords
    attach: AttachWords


def load_refusal_words(path: Path) -> GmRefusalWords:
    """The Games Master app's refusals, read from ``path``.

    Args:
        path: The TOML file, normally ``config/gm-refusals.toml`` or whatever
            ``GM_REFUSAL_WORDS_FILE`` names.

    Returns:
        The words, one dataclass per table.

    Raises:
        ImproperlyConfigured: as :func:`common.wording.load_wording` does.
    """
    return load_wording(path, GmRefusalWords, "GM_REFUSAL_WORDS_FILE")


def refusal_words() -> GmRefusalWords:
    """The loaded words (``settings.GM_REFUSAL_WORDS``)."""
    return settings.GM_REFUSAL_WORDS
