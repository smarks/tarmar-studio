"""Players are held to the rules; a Game Master may go past them (issue #404).

Spencer's rule, 2026-09-13: the GM enforces the rules exactly for players, and
a human with the Game Master role may override constraints. These tests hold
both halves on the two rules a character change decides -- the score a stated
attribute may take, and spending past the skill points a character earned --
through the rules in :mod:`characters.services` that every Games Master action
runs (issue #590). Refusals that guard sense rather than rules stay for all.
"""

import json
import time
from unittest.mock import patch

from django.test import SimpleTestCase

from characters import services
from characters.services import CharacterChangeError
from common.test_helpers import BaseTestCase

from . import roster
from .engine import MODE_AIDE
from .ledger import (
    GAME_MASTER_SKILL_POINTS_KEY,
    DraftLedger,
    draft_character,
    draft_defaults,
    draft_values,
    skill_point_account,
)
from .models import GMSession, SessionCharacter
from .testing import character_values, session_with_character
from .turn_engine.engine import TurnEngine, TurnRequest
from .turn_engine.events import MemorySink
from .turn_engine.executor import RecordsExecutor
from .turn_engine.model import FakeModel
from .turn_engine.policy import TurnPolicy

DIE = patch("characters.skills.roll_survival_dice", return_value=(1, 1, 1, 3))
FIRST_SKILL = patch("characters.skills.roll_d20", return_value=1)
ATTRIBUTES = (
    "strength",
    "dexterity",
    "intelligence",
    "wisdom",
    "constitution",
    "charisma",
)


def _with_points(free: int):
    """A character with every attribute set and ``free`` skill points to spend."""
    character = draft_character(draft_defaults())
    for name in ATTRIBUTES:
        setattr(character, name, 12)
    character.free_skill_points = free
    return character


class AttributeRangeTest(SimpleTestCase):
    def test_a_player_is_held_to_three_to_eighteen(self):
        for value in (2, 19, 22):
            with self.subTest(value=value):
                with self.assertRaisesMessage(
                    CharacterChangeError, "must be between 3 and 18 for a player"
                ):
                    services.apply_recorded_attributes(
                        _with_points(0), "strength", value
                    )

    def test_a_player_within_the_rules_is_recorded_plainly(self):
        character = _with_points(0)
        note = services.apply_recorded_attributes(character, "all", 18)

        self.assertEqual(character.charisma, 18)
        self.assertEqual(note, "all six attributes recorded as 18")

    def test_a_game_master_may_go_past_them_and_the_note_says_so(self):
        character = _with_points(0)
        note = services.apply_recorded_attributes(
            character, "strength", 22, is_game_master=True
        )

        self.assertEqual(character.strength, 22)
        self.assertEqual(
            note,
            "strength recorded as 22 (outside the 3-18 a player may set: the Game Master's call)",
        )

    def test_nobody_can_set_more_than_a_character_can_hold(self):
        with self.assertRaisesMessage(
            CharacterChangeError, "the most a character can hold"
        ):
            services.apply_recorded_attributes(
                _with_points(0), "strength", 31, is_game_master=True
            )


class SkillPointsTest(SimpleTestCase):
    def test_a_player_cannot_spend_past_the_points_earned(self):
        with self.assertRaisesMessage(
            CharacterChangeError,
            "Only 2 free skill point(s) available; 5 were asked for",
        ):
            services.apply_skill_points(_with_points(2), "sword", 5)

    def test_a_game_masters_overspend_is_recorded_as_a_grant(self):
        character = _with_points(2)
        note, granted = services.apply_skill_points(
            character, "sword", 5, is_game_master=True
        )

        self.assertEqual(character.free_skill_points, 0)
        self.assertEqual(character.skills["sword"], 5)
        self.assertEqual(granted, 3)
        self.assertIn("3 past the points earned, granted by the Game Master", note)

    def test_the_account_stays_true_and_names_the_grant(self):
        # The two free points are earned the ordinary way, by 2000 XP, so the
        # account has something to explain them with. Each figure is pinned
        # to a literal: asserting ``spent == earned - remaining`` restated
        # the ledger's own formula, so a wrong ``earned`` or ``remaining``
        # moved both sides together and passed (issue #799).
        character = _with_points(2)
        character.total_xp = 2000
        _note, granted = services.apply_skill_points(
            character, "sword", 5, is_game_master=True
        )

        ledger = DraftLedger.from_draft(
            {**draft_values(character), GAME_MASTER_SKILL_POINTS_KEY: granted}
        )
        self.assertEqual(ledger.skill_points.from_experience_xp, 2)
        self.assertEqual(ledger.skill_points.from_game_master, 3)
        self.assertEqual(ledger.skill_points.earned, 5)
        self.assertEqual(ledger.skill_points.remaining, 0)
        self.assertEqual(ledger.skill_points.spent, 5)
        self.assertEqual(ledger.skill_points.unexplained, 0)

    def test_a_ledger_with_no_grant_names_none(self):
        ledger = DraftLedger.from_draft(draft_values(_with_points(4)))
        self.assertEqual(ledger.skill_points.from_game_master, 0)


class DeadCharacterYearsTest(SimpleTestCase):
    def _dead(self):
        character = _with_points(0)
        with FIRST_SKILL, DIE:
            services.apply_experience_years(character, "campaigner", 1)
        return character

    def test_years_for_the_dead_are_refused_for_everyone(self):
        with self.assertRaisesMessage(CharacterChangeError, "is dead"):
            services.apply_experience_years(self._dead(), "campaigner", 1)

    def test_a_game_master_is_told_the_override(self):
        with self.assertRaisesMessage(
            CharacterChangeError, "bring them back to life first"
        ):
            services.apply_experience_years(
                self._dead(), "campaigner", 1, is_game_master=True
            )


class AuthorityFromTheAccountTest(BaseTestCase):
    """The authority a turn runs with comes from the session owner's account."""

    def _play(self, user, actions: list[dict], **values):
        session = session_with_character(
            user, {**draft_defaults(), "name": "Kael", **values}, name="Authority"
        )
        character_id = session.attached_characters().get().pk
        for action in actions:
            action.setdefault("character_id", character_id)
        policy = TurnPolicy.for_turn(
            MODE_AIDE, is_game_master=user.can_manage_users or user.is_dm
        )
        engine = TurnEngine(
            FakeModel([json.dumps({"actions": actions}), "Done."]),
            MemorySink(),
            RecordsExecutor(policy, session, lambda: session, user),
            started_at=time.monotonic(),
            budget_seconds=60,
        )
        running = engine.run(
            TurnRequest(
                policy=policy, prefix=[], latest="go", commit=lambda text: session.pk
            )
        )
        while True:
            try:
                next(running)
            except StopIteration as stop:
                return session, stop.value

    def test_a_game_masters_stated_score_goes_past_the_rules(self):
        session, outcome = self._play(
            self.create_admin(),
            [{"action": "record_attributes", "scores": {"strength": 22}}],
        )

        self.assertTrue(outcome.results[0].ran, outcome.results[0].error)
        self.assertEqual(character_values(session)["strength"], 22)
        self.assertIn("the Game Master's call", outcome.results[0].note)

    def test_a_players_stated_score_is_refused(self):
        session, outcome = self._play(
            self.create_user(),
            [{"action": "record_attributes", "scores": {"strength": 12}}],
        )

        self.assertFalse(outcome.results[0].ran)
        self.assertIsNone(character_values(session)["strength"])

    def test_a_game_masters_overspend_is_kept_on_the_session(self):
        user = self.create_dm()
        session, outcome = self._play(
            user,
            [
                {"action": "record_attributes", "all": 12},
                {"action": "spend_skill_points", "skill": "sword", "points": 2},
                {"action": "spend_skill_points", "skill": "bow", "points": 1},
            ],
        )

        self.assertTrue(all(result.ran for result in outcome.results))
        entry = GMSession.objects.get(pk=session.pk).session_characters.get()
        self.assertEqual(entry.game_master_skill_points, 3)

    def test_a_players_session_cannot_start_a_skill_the_glossary_does_not_name(
        self,
    ):
        """The chat held to the sheet's rule (issue #902, after #862).

        Whatever name the model writes, a player's free point goes only into a
        skill the glossary names or one the character holds, and the ledger
        says why the rest were not done.
        """
        cases = (
            ("brawling", services.ALLOCATION_REFUSED_REASON),
            ("sword", services.ALLOCATION_REFUSED_REASON),
            ("Knowledge (History)", services.KNOWLEDGE_FIELD_REFUSED_REASON),
        )
        session, outcome = self._play(
            self.create_user(),
            [
                {"action": "spend_skill_points", "skill": name, "points": 1}
                for name, _reason in cases
            ],
            free_skill_points=3,
        )

        for result, (name, reason) in zip(outcome.results, cases, strict=True):
            with self.subTest(name=name):
                self.assertFalse(result.ran)
                self.assertEqual(result.error, reason)
                self.assertIsNotNone(result.procedure)
                self.assertEqual(
                    result.procedure["lines"][-1],
                    f"⚙️ Not done: spending skill points for Kael, because {reason}",
                )
        values = character_values(session)
        self.assertEqual(values["skills"], {})
        self.assertEqual(values["free_skill_points"], 3)

    def test_a_players_session_starts_what_the_glossary_names(self):
        session, outcome = self._play(
            self.create_user(),
            [
                {"action": "spend_skill_points", "skill": "Broadsword", "points": 1},
                {"action": "spend_skill_points", "skill": "Perception", "points": 1},
            ],
            free_skill_points=3,
        )

        self.assertTrue(all(result.ran for result in outcome.results))
        self.assertNotIn(
            services.GAME_MASTER_STARTED_SKILL_NOTE, outcome.results[0].note
        )
        values = character_values(session)
        self.assertEqual(values["skills"], {"broadsword": 1, "perception": 1})
        self.assertEqual(values["free_skill_points"], 1)

    def test_a_game_masters_session_may_start_any_skill_and_says_so(self):
        session, outcome = self._play(
            self.create_dm(),
            [
                {"action": "spend_skill_points", "skill": "brawling", "points": 1},
                {
                    "action": "spend_skill_points",
                    "skill": "Knowledge (History)",
                    "points": 1,
                },
                {"action": "spend_skill_points", "skill": "Broadsword", "points": 1},
            ],
            free_skill_points=3,
        )

        unnamed, knowledge_field, glossary = outcome.results
        # One wording for either reason a player is refused (review of #902):
        # it must not call a Knowledge field a skill the glossary lacks.
        self.assertEqual(
            services.GAME_MASTER_STARTED_SKILL_NOTE,
            "started by the Game Master: a skill only a Game Master may start",
        )
        for result, stored in (
            (unnamed, "brawling"),
            (knowledge_field, "knowledge (history)"),
        ):
            with self.subTest(stored=stored):
                self.assertTrue(result.ran, result.error)
                self.assertIn(
                    f"{stored} {services.GAME_MASTER_STARTED_SKILL_NOTE}",
                    result.note,
                )
        self.assertTrue(glossary.ran, glossary.error)
        self.assertNotIn(services.GAME_MASTER_STARTED_SKILL_NOTE, glossary.note)
        self.assertEqual(
            character_values(session)["skills"],
            {"brawling": 1, "knowledge (history)": 1, "broadsword": 1},
        )


class TheSkillPointBreakdownNeverGoesNegativeTest(SimpleTestCase):
    """``spent`` was inferred from two sides kept apart (issue #690).

    ``from_experience_xp`` is recomputed from ``total_xp`` while ``remaining``
    is a running balance ``apply_experience_run`` adds deltas to. A character
    whose free points did not all come from runs starting at zero XP had the
    two disagree, and an imported character with points but no XP behind them
    reported a negative number of points spent -- a figure that counts
    nothing, shown in the Characters pane as though it did.
    """

    def test_free_points_with_no_xp_behind_them_are_named_not_negated(self):
        account = skill_point_account(_with_points(4))

        self.assertEqual(account.spent, 0)
        self.assertEqual(account.unexplained, 4)
        self.assertFalse(account.adds_up)

    def test_a_breakdown_that_adds_up_says_there_is_nothing_unexplained(self):
        character = _with_points(2)
        _note, granted = services.apply_skill_points(
            character, "sword", 5, is_game_master=True
        )

        account = skill_point_account(character, granted=granted)

        self.assertEqual(account.remaining, 0)
        self.assertEqual(account.spent, account.earned - account.remaining)
        self.assertEqual(account.unexplained, 0)
        self.assertTrue(account.adds_up)

    def test_the_balance_itself_is_never_touched(self):
        """Whatever the parts say, remaining is what the record holds."""
        character = _with_points(4)

        self.assertEqual(skill_point_account(character).remaining, 4)


class GrantedPointsAddUpTest(BaseTestCase):
    """Two grants in one session are two grants (issue #689).

    ``grant_skill_points`` read the total off the row it had just fetched and
    wrote that plus the new points in a separate UPDATE. Two grants that
    interleave -- two turns of one session resolved at once, or a retried
    request -- both read the old total, and the second write threw the first
    away. The recorded grant then under-counted, and ``skill_point_account``
    reported a ``spent`` figure too high by exactly the lost grant.
    """

    def test_two_grants_reading_the_same_row_both_count(self):
        session = session_with_character(self.create_dm(), {"name": "Wren"})
        character = session.attached_characters().get()
        stale = SessionCharacter.objects.get(session=session, character=character)

        # Both calls are handed the same row as it was before either wrote,
        # which is what two overlapping requests each get.
        with patch.object(
            SessionCharacter.objects, "get_or_create", return_value=(stale, False)
        ):
            roster.grant_skill_points(session, character, 3)
            roster.grant_skill_points(session, character, 4)

        stale.refresh_from_db()
        self.assertEqual(stale.game_master_skill_points, 7)

    def test_one_grant_is_still_one_grant(self):
        session = session_with_character(self.create_dm(), {"name": "Wren"})
        character = session.attached_characters().get()

        roster.grant_skill_points(session, character, 5)

        self.assertEqual(
            SessionCharacter.objects.get(
                session=session, character=character
            ).game_master_skill_points,
            5,
        )
