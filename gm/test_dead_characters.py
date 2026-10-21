"""The Games Master chat refuses a dead character's records (issue #932).

Every chat action that writes a character goes through the sheet's services,
whose guard refuses the dead (``characters.services.changing``); the refusal
reaches the ledger as a refused line naming revival. These hold every action
a plan may name to that, for a player's session and a Game Master's, and fail
when a new action is added without a decision about the dead.
"""

from typing import Any

from characters.models import Character
from characters.services import CharacterChangeError
from common.test_helpers import create_character

from .engine import MODE_PLAY
from .luck import LUCK_SKILL, spend_luck
from .models import SessionCharacter
from .refusal_words import refusal_words
from .test_turn_engine_executor import ExecutorTestCase, plan
from .turn_engine.policy import (
    ACTION_NAMES,
    CAMPAIGN_ACTIONS,
    HOUSE_RULE_ACTIONS,
)
from .withdraw import withdraw

#: The words every refusal of the dead carries.
DEAD_WORDS = "is dead; a Game Master must bring them back to life first"

#: Each character action, with what it needs besides the character's id.
ACTION_FIELDS: dict[str, dict[str, Any]] = {
    "record_attributes": {"all": 12},
    "roll_attributes": {},
    "roll_fatigue": {},
    "roll_literacy": {},
    "rule_literacy": {"literate": True},
    "roll_background": {},
    "play_years": {"track": "campaigner", "years": 1},
    "spend_skill_points": {"skill": "stealth", "points": 1},
    "add_equipment": {"item": "rope", "quantity": 1},
    "roll": {"dice": "1d6", "purpose": "to listen"},
    "rename_character": {"name": "Wren the Lost"},
    "set_background": {"wealth": "rich"},
    "revive_character": {},
    "remove_character": {},
    "mark_dead": {"cause": "again"},
    "award_xp": {"xp": 100},
    "set_coin": {"change": 5},
    "skill_check": {"skill": "stealth"},
    "survival_save": {},
    "attribute_check": {"attribute": "strength"},
}

#: The actions that write nothing to a character, or ask before acting.
NOT_ON_A_CHARACTER = CAMPAIGN_ACTIONS | HOUSE_RULE_ACTIONS | {"rules_lookup", "ask_gm"}

#: What each action does to a dead character other than the shared refusal:
#: a Game Master's own revival, the name no rule governs, a removal from the
#: session, mark-dead's own refusal and a roll's (the dead roll nothing).
NOT_THE_SHARED_REFUSAL: dict[str, str] = {
    "rename_character": "runs",
    "remove_character": "runs",
    "revive_character": "runs",
    "mark_dead": "already dead",
    "roll": "is dead and makes no rolls",
}

#: What only a Game Master asks for, and which of the policy's refusals (a
#: key of ``config/gm-refusals.toml``'s [policy] table) answers a player's plan
#: for it whoever the character is (issues #403, #743, #802), before any record
#: is read.
GAME_MASTER_ONLY: dict[str, str] = {
    "revive_character": "game_master_only",
    "remove_character": "game_master_only",
    "mark_dead": "game_master_records",
    "award_xp": "game_master_records",
    "set_coin": "game_master_records",
    "record_attributes": "player_stated_scores",
    "rule_literacy": "literacy_ruling",
}


class DeadCharacterActionsTest(ExecutorTestCase):
    game_master = True

    def setUp(self) -> None:
        super().setUp()
        self.wren = self.make_wren()

    def make_wren(self) -> Character:
        """A dead character in the session, with points, skills and coin."""
        wren = create_character(
            self.user,
            name="Wren",
            is_alive=False,
            death_cause="Fell from the tower",
            free_skill_points=5,
            skills={"stealth": 1},
            starting_coin=100,
            body_damage=2,
        )
        self.session.characters.add(wren)
        return wren

    def run_on_wren(self, name: str) -> Any:
        outcome, *_rest = self.play(
            plan({"action": name, "character_id": self.wren.pk, **ACTION_FIELDS[name]})
        )
        return outcome.results[0]

    def test_every_action_is_decided(self) -> None:
        self.assertEqual(set(ACTION_FIELDS) | NOT_ON_A_CHARACTER, set(ACTION_NAMES))

    def expected(self, name: str) -> str:
        if not self.game_master and name in GAME_MASTER_ONLY:
            return getattr(refusal_words().policy, GAME_MASTER_ONLY[name])
        return NOT_THE_SHARED_REFUSAL.get(name, DEAD_WORDS)

    def test_each_action_on_the_dead(self) -> None:
        for name in ACTION_FIELDS:
            with self.subTest(action=name):
                # A fresh one each time: a revival or a removal must not
                # change what the next action meets.
                self.wren = self.make_wren()
                before = Character.objects.filter(pk=self.wren.pk).values().get()
                result = self.run_on_wren(name)
                expected = self.expected(name)
                if expected == "runs":
                    self.assertTrue(result.ran, result.error)
                    continue
                self.assertFalse(result.ran, result.result)
                self.assertIn(expected, result.error)
                # The ledger's line for it is a refusal, in those words.
                self.assertIn(f"because {result.error}", result.procedure["lines"][0])
                self.assertEqual(
                    Character.objects.filter(pk=self.wren.pk).values().get(), before
                )

    def test_a_revived_character_takes_the_rules_again(self) -> None:
        revived = self.run_on_wren("revive_character")
        if not self.game_master:
            # A player's plan may not revive (#403), so the death stands.
            self.assertFalse(revived.ran)
            self.assertFalse(self.run_on_wren("spend_skill_points").ran)
            self.wren.refresh_from_db()
            self.assertFalse(self.wren.is_alive)
            return
        self.assertTrue(revived.ran, revived.error)
        result = self.run_on_wren("spend_skill_points")
        self.assertTrue(result.ran, result.error)
        self.wren.refresh_from_db()
        self.assertEqual(self.wren.skills["stealth"], 2)


class PlayerDeadCharacterActionsTest(DeadCharacterActionsTest):
    game_master = False
    mode = MODE_PLAY


class DeadCharacterSessionPathsTest(ExecutorTestCase):
    """The two session writes outside a plan: Luck, and withdrawing."""

    game_master = True

    def setUp(self) -> None:
        super().setUp()
        self.player = self.create_user("wren-owner")
        self.wren = create_character(
            self.player,
            name="Wren",
            is_alive=False,
            death_cause="Fell from the tower",
            skills={LUCK_SKILL: 1},
        )
        self.session.characters.add(self.wren)

    def test_luck_is_refused_and_no_use_is_spent(self) -> None:
        for is_game_master in (False, True):
            with self.subTest(is_game_master=is_game_master):
                with self.assertRaisesMessage(CharacterChangeError, DEAD_WORDS):
                    spend_luck(
                        self.session, self.wren.pk, is_game_master=is_game_master
                    )
                self.assertFalse(
                    SessionCharacter.objects.filter(
                        session=self.session, character=self.wren, luck_spent__gt=0
                    ).exists()
                )

    def test_its_owner_may_still_withdraw_it(self) -> None:
        self.assertTrue(withdraw(self.wren, self.session.pk, self.player))
        self.assertNotIn(self.wren, self.session.characters.all())
        self.wren.refresh_from_db()
        self.assertEqual(self.wren.history[-1]["new"], "withdrawn")
