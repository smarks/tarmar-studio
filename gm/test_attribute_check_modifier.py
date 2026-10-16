"""A Game Master's situational modifier on an attribute check (#953).

Spencer's ruling on the #911 open question: an attribute check (3d6 against
the aged score, injury lowering the target) takes a modifier on the target
-- a situational call, not a survival save's penalty -- that only a Game
Master sets, applied after the injury penalty, named on the line as his
call and kept in the character's history. A player's is refused on both paths a plan reaches the
check by: the plan reader, which asks the model again, and the check's own
service, which the executor reaches with a plan the reader did not see.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from characters.skill_rules import skill_rules
from common.test_helpers import create_character

from . import rules_lookup
from .engine import MODE_PLAY, system_prompt_for
from .test_aide_roll_condition import hurt_to
from .test_injured_roll_direction import ROLL_3D6, three_dice
from .test_turn_engine_executor import ExecutorTestCase, plan
from .turn_engine import events
from .turn_engine.plan import PLAYER_ATTRIBUTE_CHECK_ERROR, PlannedAction


class AGameMasterSetsTheModifierTest(ExecutorTestCase):
    game_master = True
    mode = MODE_PLAY

    def setUp(self):
        super().setUp()
        self.dain = create_character(self.user, name="Dain", constitution=12)
        self.session.characters.add(self.dain)

    def check(self, **fields) -> dict:
        return {
            "action": "attribute_check",
            "character_id": self.dain.pk,
            "attribute": "constitution",
            **fields,
        }

    def run_check(self, total: int, **fields):
        with patch(ROLL_3D6, return_value=three_dice(total)):
            outcome, yielded, _model, _turn = self.play(plan(self.check(**fields)))
        rejected = [event for event in yielded if event.kind == events.PLAN_REJECTED]
        self.assertEqual(rejected, [])
        (result,) = outcome.results
        self.assertTrue(result.ran, result.error)
        return result

    def test_a_negative_modifier_lowers_the_target_and_is_kept(self):
        result = self.run_check(8, modifier=-5)
        line = (
            "constitution check, 3d6: [8] = 8 > 7 "
            "(CON 12 -5 modifier set by a Game Master): fails"
        )
        self.assertIn(line, result.result)
        self.dain.refresh_from_db()
        self.assertEqual(self.dain.history[-1]["field"], "attribute_check")
        self.assertEqual(self.dain.history[-1]["new"], line)

    def test_a_positive_modifier_raises_the_target(self):
        result = self.run_check(14, modifier=2)
        self.assertIn(
            "= 14 ≤ 14 (CON 12 +2 modifier set by a Game Master): passes",
            result.result,
        )

    def test_it_comes_after_the_injury_penalty(self):
        fatigue = self.dain.fatigue
        assert fatigue is not None
        hurt_to(self.dain, fatigue_left=fatigue // 2)
        result = self.run_check(7, modifier=-4)
        self.assertIn(
            "= 7 ≤ 7 (CON 12 -1 injured -4 modifier set by a Game Master): passes",
            result.result,
        )

    def test_the_plan_holds_it_to_its_bound(self):
        limit = skill_rules().attribute_check.game_master_modifier_limit
        past = limit + 1
        with patch(ROLL_3D6, return_value=three_dice(10)):
            outcome, yielded, _model, _turn = self.play(
                plan(self.check(modifier=-past)), replan=plan(self.check())
            )
        (rejected,) = [event for event in yielded if event.kind == events.PLAN_REJECTED]
        self.assertIn(
            f"modifier must be a whole number from -{limit} to {limit}",
            " ".join(rejected.payload["errors"]),
        )
        self.assertNotIn("modifier", outcome.results[0].result)

    def test_the_event_log_keeps_the_modifier(self):
        action = PlannedAction(
            action="attribute_check",
            character_id=self.dain.pk,
            attribute="constitution",
            modifier=-5,
        )
        self.assertEqual(action.as_payload()["modifier"], -5)
        self.assertNotIn(
            "modifier", PlannedAction(action="attribute_check").as_payload()
        )


class APlayerSetsNoModifierTest(ExecutorTestCase):
    game_master = False
    mode = MODE_PLAY

    def setUp(self):
        super().setUp()
        self.dain = create_character(self.user, name="Dain", constitution=12)
        self.session.characters.add(self.dain)
        self.history_before = list(self.dain.history or [])

    def test_the_plan_reader_refuses_it_and_the_plain_check_rolls(self):
        check = {
            "action": "attribute_check",
            "character_id": self.dain.pk,
            "attribute": "constitution",
        }
        with patch(ROLL_3D6, return_value=three_dice(10)):
            outcome, yielded, _model, _turn = self.play(
                plan({**check, "modifier": 3}), replan=plan(check)
            )
        (rejected,) = [event for event in yielded if event.kind == events.PLAN_REJECTED]
        self.assertIn(
            PLAYER_ATTRIBUTE_CHECK_ERROR.format(index=1), rejected.payload["errors"]
        )
        self.assertIn("modifier", PLAYER_ATTRIBUTE_CHECK_ERROR)
        (result,) = outcome.results
        self.assertTrue(result.ran, result.error)
        self.assertIn("3d6: [10] = 10 ≤ 12 (CON 12): passes", result.result)

    def test_the_service_refuses_it_and_writes_nothing(self):
        for modifier in (-5, 5):
            with self.subTest(modifier=modifier):
                result = self.executor().run(
                    PlannedAction(
                        action="attribute_check",
                        character_id=self.dain.pk,
                        attribute="constitution",
                        modifier=modifier,
                    ),
                    lambda _event: None,
                )
                self.assertFalse(result.ran)
                self.assertIn(
                    "Only a Game Master can set a modifier or state the dice",
                    result.error,
                )
                self.dain.refresh_from_db()
                self.assertEqual(list(self.dain.history or []), self.history_before)


class TheRulesSayWhoSetsItTest(SimpleTestCase):
    """The plan schema and the rules lookup name the modifier as the GM's."""

    def test_the_schema_offers_it_to_the_game_master_alone(self):
        for is_game_master in (True, False):
            with self.subTest(is_game_master=is_game_master):
                self.assertIn(
                    'Only the Game Master\'s carries a "total": 11 rolled at his '
                    'table or a "modifier": -5 on the attribute.',
                    system_prompt_for(is_game_master, MODE_PLAY),
                )

    def test_the_lookup_gives_the_bound_and_the_order(self):
        limit = skill_rules().attribute_check.game_master_modifier_limit
        said = rules_lookup.look_up("attribute_check")
        self.assertIn(
            f"set a modifier ({rules_lookup.signed(-limit)} "
            f"to {rules_lookup.signed(limit)}) on the score "
            "after any injury for the situation; a player sets neither.",
            said,
        )
        # The survival save's penalty is survival_save's, never this one's.
        self.assertIn(
            "not a survival save's penalty: a dying character's save is "
            "survival_save, which applies its own penalty.",
            said,
        )
        self.assertNotIn("CON − 5", said)

    def test_the_skill_checks_difficulty_is_stated_the_same_way(self):
        from characters.skill_checks import MAX_DIFFICULTY

        said = rules_lookup.look_up("skill_check")
        self.assertIn(
            f"a difficulty the Game Master sets (0 to {MAX_DIFFICULTY}); a player "
            "sets none.",
            said,
        )
