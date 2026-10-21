"""A backwards direction cannot turn an injury penalty into a bonus (#911).

Batch h (#886) applied the injury table's -1/-2 to a character's free-form
``roll`` in the direction the plan named: ``"over"`` took the point off the
total, ``"under"`` added it. A 3d6 ≤ CON roll planned ``"over"`` therefore
counted a point *lower*, which on a roll-under is a point *better*: the
penalty became a bonus, and no check in code could see it, because a
free-form roll carries no rule that says which way it is read.

The fix routes every roll against a score through an action whose direction
is the rule's: ``skill_check`` (2d6 roll-over), ``survival_save`` (3d6 ≤
CON) and ``attribute_check`` (3d6 ≤ the attribute). A free-form ``roll``
carries no injury penalty, because nothing it rolls is a check the character
makes, and no direction the model writes changes any total.
"""

from unittest.mock import patch

from characters.refusal_words import game_master_call_note
from common.test_helpers import create_character

from .engine import MODE_AIDE, MODE_PLAY
from .refusal_words import refusal_words
from .test_aide_roll_condition import hurt_to
from .test_turn_engine_executor import ExecutorTestCase, plan
from .turn_engine import events
from .turn_engine.plan import PLAYER_ATTRIBUTE_CHECK_ERROR
from .turn_engine.policy import TurnPolicy

#: Where the attribute check's dice come from (the canon's, 3d6 on the
#: pages), for a fixed total.
ROLL_3D6 = "characters.skill_checks.roll_dice_logged"


def three_dice(total: int) -> tuple[int, str]:
    """A 3d6 result of ``total``, as ``roll_dice_logged`` returns one."""
    return total, f"3d6: [{total}] = {total}"


class InjuredCharacterTestCase(ExecutorTestCase):
    """A player's character at -1 to all rolls."""

    game_master = False
    mode = MODE_PLAY

    def setUp(self):
        super().setUp()
        self.dain = create_character(self.user, name="Dain", constitution=12)
        self.session.characters.add(self.dain)
        fatigue = self.dain.fatigue
        assert fatigue is not None
        hurt_to(self.dain, fatigue_left=fatigue // 2)

    def run_plan(self, *actions: dict):
        outcome, yielded, _model, _turn = self.play(plan(*actions))
        rejected = [event for event in yielded if event.kind == events.PLAN_REJECTED]
        return outcome, rejected


class ABackwardsDirectionIsNoBonusTest(InjuredCharacterTestCase):
    def test_a_roll_under_planned_over_does_not_count_lower(self):
        # The issue's case: 3d6 that must come in at or under CON, planned
        # "over". Before the fix it counted as total - 1, a point better.
        outcome, _rejected = self.run_plan(
            {
                "action": "roll",
                "dice": "3d6",
                "purpose": "a constitution check",
                "character_id": self.dain.pk,
                "direction": "over",
            }
        )
        (result,) = outcome.results
        self.assertTrue(result.ran, result.error)
        (frame,) = result.rolls
        self.assertNotIn("counts_as", frame)
        self.assertNotIn("injury_penalty", frame)
        self.assertNotIn("counts as", result.result)
        self.assertEqual(
            frame["line"],
            f"🎲 Dain rolls 3d6 for a constitution check: [{frame['rolls']}] "
            f"= {frame['total']}",
        )

    def test_no_direction_the_model_writes_changes_a_free_form_roll(self):
        lines = []
        for direction in ("over", "under", ""):
            with (
                self.subTest(direction=direction),
                patch("common.rolling.random.randint", return_value=4),
            ):
                action = {"action": "roll", "dice": "3d6", "character_id": self.dain.pk}
                if direction:
                    action["direction"] = direction
                outcome, rejected = self.run_plan(action)
                self.assertEqual(rejected, [])
                (frame,) = outcome.results[0].rolls
                self.assertEqual(frame["total"], 12)
                lines.append(frame["line"])
        self.assertEqual(len(set(lines)), 1, lines)

    def test_the_constitution_check_takes_the_penalty_as_a_penalty(self):
        # 12 against CON 12 passes unhurt; at -1 it has to come in at 11.
        with patch(ROLL_3D6, return_value=three_dice(12)):
            outcome, rejected = self.run_plan(
                {
                    "action": "attribute_check",
                    "character_id": self.dain.pk,
                    "attribute": "constitution",
                    # Ignored: the rule decides which way the check reads.
                    "direction": "over",
                }
            )
        self.assertEqual(rejected, [])
        (result,) = outcome.results
        self.assertTrue(result.ran, result.error)
        self.assertIn("constitution check, 3d6: [12] = 12 > 11", result.result)
        self.assertIn("(CON 12 -1 injured): fails", result.result)

    def test_a_check_inside_the_lowered_target_passes(self):
        with patch(ROLL_3D6, return_value=three_dice(11)):
            outcome, _rejected = self.run_plan(
                {
                    "action": "attribute_check",
                    "character_id": self.dain.pk,
                    "attribute": "constitution",
                }
            )
        result = outcome.results[0]
        self.assertTrue(result.ran, result.error)
        self.assertIn("= 11 ≤ 11 (CON 12 -1 injured): passes", result.result)
        self.dain.refresh_from_db()
        self.assertEqual(self.dain.history[-1]["field"], "attribute_check")

    def test_a_player_states_no_total(self):
        check = {
            "action": "attribute_check",
            "character_id": self.dain.pk,
            "attribute": "constitution",
        }
        with patch(ROLL_3D6, return_value=three_dice(10)):
            outcome, yielded, _model, _turn = self.play(
                plan({**check, "total": 3}), replan=plan(check)
            )
        rejected = [event for event in yielded if event.kind == events.PLAN_REJECTED]
        self.assertEqual(len(rejected), 1)
        self.assertIn(
            PLAYER_ATTRIBUTE_CHECK_ERROR.format(index=1),
            rejected[0].payload["errors"],
        )
        # The check the player asked for still rolls, with the server's dice.
        (result,) = outcome.results
        self.assertTrue(result.ran, result.error)
        self.assertIn("3d6: [10] = 10 ≤ 11", result.result)

    def test_an_unconscious_characters_check_is_refused_for_a_player(self):
        hurt_to(self.dain, fatigue_left=0)
        outcome, _rejected = self.run_plan(
            {
                "action": "attribute_check",
                "character_id": self.dain.pk,
                "attribute": "constitution",
            }
        )
        result = outcome.results[0]
        self.assertFalse(result.ran)
        self.assertIn("unconscious", result.error)


class AGameMasterOverridesTest(ExecutorTestCase):
    game_master = True
    mode = MODE_PLAY

    def setUp(self):
        super().setUp()
        self.dain = create_character(self.user, name="Dain", wisdom=10)
        self.session.characters.add(self.dain)

    def test_a_game_master_may_state_his_tables_total(self):
        outcome, *_rest = self.play(
            plan(
                {
                    "action": "attribute_check",
                    "character_id": self.dain.pk,
                    "attribute": "wisdom",
                    "total": 10,
                }
            )
        )
        result = outcome.results[0]
        self.assertTrue(result.ran, result.error)
        self.assertIn(
            "wisdom check, 3d6 stated by a Game Master = 10 ≤ 10 (WIS 10): passes",
            result.result,
        )

    def test_a_game_master_may_call_an_unconscious_characters_check(self):
        hurt_to(self.dain, fatigue_left=0)
        with patch(ROLL_3D6, return_value=three_dice(9)):
            outcome, *_rest = self.play(
                plan(
                    {
                        "action": "attribute_check",
                        "character_id": self.dain.pk,
                        "attribute": "wisdom",
                    }
                )
            )
        result = outcome.results[0]
        self.assertTrue(result.ran, result.error)
        self.assertIn(game_master_call_note("unconscious"), result.result)


class TheAideRollsTheCheckOnlyWhenAskedTest(ExecutorTestCase):
    game_master = True
    mode = MODE_AIDE

    def setUp(self):
        super().setUp()
        self.gorn = create_character(self.user, name="Gorn", strength=14)
        self.session.characters.add(self.gorn)

    def check(self) -> dict:
        return {
            "action": "attribute_check",
            "character_id": self.gorn.pk,
            "attribute": "strength",
        }

    def test_an_unasked_check_is_refused(self):
        self.policy = TurnPolicy.for_turn(
            MODE_AIDE, is_game_master=True, latest="How is Gorn after the fall?"
        )
        outcome, *_rest = self.play(plan(self.check()))
        self.assertEqual(
            outcome.results[0].error, refusal_words().policy.aide_unasked_roll
        )

    def test_a_check_asked_for_by_name_runs(self):
        for asked in ("Give Gorn a strength check", "an attribute check for Gorn"):
            with self.subTest(asked=asked):
                self.policy = TurnPolicy.for_turn(
                    MODE_AIDE, is_game_master=True, latest=asked
                )
                outcome, *_rest = self.play(plan(self.check()))
                result = outcome.results[0]
                self.assertTrue(result.ran, result.error)
