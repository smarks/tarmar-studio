"""The aide's rolls answer to the character's condition (issue #886).

Split from #840 and #839. injury-thresholds-death.md: "Down to 1/2 (but above
5) — −1 to all rolls; Between 1 and 5 — −2 to all rolls", and a character
past minus half a pool makes a survival save each turn. The sheet's attack
panel and skill check took the penalty; the aide's ``roll`` action rolled a
wounded character's dice as if unhurt, and the aide could not roll a
survival save at all.
"""

import json
import math
from unittest.mock import patch

from characters.models import Character
from characters.services import SURVIVAL_SAVE_ALREADY_ROLLED, SURVIVAL_SAVE_FIELD
from common.rolling import RollRecord
from common.test_helpers import create_character

from .engine import MODE_AIDE, MODE_PLAY
from .roll_service import RollRequest, RollResolution, dice_line, roll_frame_payload
from .test_turn_engine_executor import ExecutorTestCase, plan
from .turn_engine import events
from .turn_engine.plan import PLAYER_INJURED_ROLL_ERROR, read_plan
from .turn_engine.policy import ACTION_NAMES, AIDE_UNASKED_ROLL_REFUSAL, TurnPolicy


def hurt_to(character: Character, *, fatigue_left: int) -> None:
    """Leave ``character`` with ``fatigue_left`` Fatigue and Body untouched."""
    fatigue = character.fatigue
    assert fatigue is not None
    character.fatigue_damage = fatigue - fatigue_left
    character.save()


def dying(character: Character) -> None:
    """Body at minus half its starting value: a survival save is due."""
    body = character.body
    assert body is not None
    character.body_damage = body + math.ceil(body / 2)
    character.save()


def rolled(total: int, request: RollRequest) -> RollResolution:
    """``request`` as though its dice came to ``total``."""
    return RollResolution(
        request=request,
        record=RollRecord(
            purpose="gm",
            specification=request.expression,
            faces=(total,),
            modifier=0,
            total=total,
            target_number=None,
            outcome=None,
        ),
    )


class TheLineNamesThePenaltyTest(ExecutorTestCase):
    """What an injured character's dice line says, each way it can be read."""

    def test_over_loses_the_point_and_under_counts_it_higher(self):
        for direction, counted in (("over", 9), ("under", 11)):
            with self.subTest(direction=direction):
                request = RollRequest(
                    "3d6", roller="Dain", injury_penalty=-1, direction=direction
                )
                line = dice_line(rolled(10, request))
                self.assertTrue(
                    line.endswith(
                        f", -1 injured on a roll {direction}: counts as {counted}"
                    ),
                    line,
                )
                payload = roll_frame_payload(rolled(10, request))
                self.assertEqual(
                    (payload["total"], payload["injury_penalty"], payload["counts_as"]),
                    (10, -1, counted),
                )

    def test_no_direction_names_the_penalty_and_applies_nothing(self):
        request = RollRequest("3d6", roller="Dain", injury_penalty=-2)
        line = dice_line(rolled(10, request))
        self.assertIn("= 10; injured, -2 to all rolls, not applied", line)
        payload = roll_frame_payload(rolled(10, request))
        self.assertNotIn("counts_as", payload)
        self.assertEqual(payload["total"], 10)

    def test_an_unhurt_roll_is_unchanged(self):
        request = RollRequest("3d6", roller="Dain", direction="under")
        self.assertEqual(dice_line(rolled(10, request)), "🎲 Dain rolls 3d6: [10] = 10")
        self.assertNotIn("injury_penalty", roll_frame_payload(rolled(10, request)))


class ThePlanSaysWhichWayTest(ExecutorTestCase):
    def read(self, *actions: dict):
        return read_plan(
            json.dumps({"actions": list(actions)}),
            roster_ids={41},
            policy=TurnPolicy.for_turn(MODE_PLAY, is_game_master=False),
        )

    def test_direction_is_over_or_under(self):
        reading = self.read(
            {"action": "roll", "dice": "3d6", "character_id": 41, "direction": "Under"}
        )
        self.assertEqual(reading.errors, ())
        self.assertEqual(reading.actions[0].direction, "under")
        self.assertEqual(reading.actions[0].as_payload()["direction"], "under")
        refused = self.read(
            {"action": "roll", "dice": "3d6", "character_id": 41, "direction": "up"}
        )
        self.assertTrue(refused.errors)

    def test_survival_save_is_an_action_on_a_listed_character(self):
        self.assertEqual(ACTION_NAMES[-1], "survival_save")
        reading = self.read({"action": "survival_save", "character_id": 41})
        self.assertEqual(reading.errors, ())
        self.assertFalse(reading.actions[0].refused)
        self.assertTrue(self.read({"action": "survival_save"}).errors)


class PlayersRollTest(ExecutorTestCase):
    game_master = False
    mode = MODE_PLAY

    def setUp(self):
        super().setUp()
        self.dain = create_character(self.user, name="Dain", constitution=12)
        self.session.characters.add(self.dain)

    def roll(self, **fields):
        outcome, *_rest = self.play(
            plan(
                {
                    "action": "roll",
                    "dice": "3d6",
                    "character_id": self.dain.pk,
                    **fields,
                }
            )
        )
        return outcome.results[0]

    def test_an_injured_characters_roll_takes_the_penalty(self):
        fatigue = self.dain.fatigue
        assert fatigue is not None
        hurt_to(self.dain, fatigue_left=fatigue // 2)
        result = self.roll(direction="under")
        self.assertTrue(result.ran, result.error)
        (frame,) = result.rolls
        self.assertEqual(frame["injury_penalty"], -1)
        self.assertEqual(frame["counts_as"], frame["total"] + 1)
        self.assertIn("counts as", result.result)

    def test_a_players_injured_roll_with_no_direction_is_planned_again(self):
        # The #886 review: with no direction the penalty was only named, so an
        # injured player's roll escaped it. The model is asked again.
        fatigue = self.dain.fatigue
        assert fatigue is not None
        hurt_to(self.dain, fatigue_left=fatigue // 2)
        outcome, yielded, model, _turn = self.play(
            plan({"action": "roll", "dice": "3d6", "character_id": self.dain.pk}),
            replan=plan(
                {
                    "action": "roll",
                    "dice": "3d6",
                    "character_id": self.dain.pk,
                    "direction": "under",
                }
            ),
        )
        rejected = [event for event in yielded if event.kind == events.PLAN_REJECTED]
        self.assertEqual(len(rejected), 1)
        self.assertIn(
            PLAYER_INJURED_ROLL_ERROR.format(index=1), rejected[0].payload["errors"]
        )
        (result,) = outcome.results
        self.assertTrue(result.ran, result.error)
        self.assertEqual(result.rolls[0]["counts_as"], result.rolls[0]["total"] + 1)

    def test_an_unhurt_players_roll_needs_no_direction(self):
        result = self.roll()
        self.assertTrue(result.ran, result.error)
        self.assertNotIn("injury_penalty", result.rolls[0])

    def test_an_unconscious_characters_roll_is_refused_for_a_player(self):
        hurt_to(self.dain, fatigue_left=0)
        result = self.roll(direction="over")
        self.assertFalse(result.ran)
        self.assertIn("unconscious", result.error)

    def test_a_player_rolls_one_survival_save_for_each_state_of_the_wounds(self):
        dying(self.dain)
        with patch(
            "characters.services.roll_3d6_logged",
            return_value=(9, "3d6: [3, 3, 3] = 9"),
        ):
            outcome, *_rest = self.play(
                plan({"action": "survival_save", "character_id": self.dain.pk})
            )
            first = outcome.results[0]
            outcome, *_rest = self.play(
                plan({"action": "survival_save", "character_id": self.dain.pk})
            )
            second = outcome.results[0]
        self.assertTrue(first.ran, first.error)
        self.assertIn("survival save, 3d6: [3, 3, 3] = 9 ≤ 12", first.result)
        self.assertIn("survives this turn", first.result)
        self.assertFalse(second.ran)
        self.assertEqual(second.error, SURVIVAL_SAVE_ALREADY_ROLLED)
        self.dain.refresh_from_db()
        self.assertTrue(self.dain.is_alive)
        self.assertEqual(self.dain.history[-1]["field"], SURVIVAL_SAVE_FIELD)


class GameMastersRollTest(ExecutorTestCase):
    game_master = True
    mode = MODE_PLAY

    def setUp(self):
        super().setUp()
        self.dain = create_character(self.user, name="Dain", constitution=12)
        self.session.characters.add(self.dain)

    def test_a_game_master_may_call_an_unconscious_characters_roll(self):
        hurt_to(self.dain, fatigue_left=0)
        outcome, *_rest = self.play(
            plan({"action": "roll", "dice": "3d6", "character_id": self.dain.pk})
        )
        result = outcome.results[0]
        self.assertTrue(result.ran, result.error)
        self.assertIn("called by the Game Master: unconscious", result.result)

    def test_a_game_masters_roll_with_no_direction_runs_and_names_the_penalty(self):
        fatigue = self.dain.fatigue
        assert fatigue is not None
        hurt_to(self.dain, fatigue_left=fatigue // 2)
        outcome, yielded, _model, _turn = self.play(
            plan({"action": "roll", "dice": "3d6", "character_id": self.dain.pk})
        )
        self.assertFalse(
            [event for event in yielded if event.kind == events.PLAN_REJECTED]
        )
        result = outcome.results[0]
        self.assertTrue(result.ran, result.error)
        self.assertIn("-1 to all rolls, not applied", result.result)

    def test_the_dead_roll_nothing_for_anyone(self):
        self.dain.is_alive = False
        self.dain.save()
        outcome, *_rest = self.play(
            plan({"action": "roll", "dice": "3d6", "character_id": self.dain.pk})
        )
        self.assertFalse(outcome.results[0].ran)
        self.assertIn("dead", outcome.results[0].error)

    def test_a_game_master_may_call_another_save_and_a_failure_is_a_death(self):
        dying(self.dain)
        with patch(
            "characters.services.roll_3d6_logged",
            return_value=(9, "3d6: [3, 3, 3] = 9"),
        ):
            self.play(plan({"action": "survival_save", "character_id": self.dain.pk}))
        with patch(
            "characters.services.roll_3d6_logged",
            return_value=(17, "3d6: [6, 6, 5] = 17"),
        ):
            outcome, *_rest = self.play(
                plan({"action": "survival_save", "character_id": self.dain.pk})
            )
        result = outcome.results[0]
        self.assertTrue(result.ran, result.error)
        self.assertIn("fails, and dies", result.result)
        self.dain.refresh_from_db()
        self.assertFalse(self.dain.is_alive)


class AnAideRollsNoDiceUnaskedTest(ExecutorTestCase):
    """#906: a Game Master's aide rolled a check or a save nobody asked for."""

    game_master = True
    mode = MODE_AIDE

    def setUp(self):
        super().setUp()
        self.policy = TurnPolicy.for_turn(
            MODE_AIDE,
            is_game_master=True,
            latest="How is Gorn doing after the fall?",
        )
        self.gorn = create_character(self.user, name="Gorn", skills={"stealth": 3})
        self.session.characters.add(self.gorn)

    def test_a_check_a_save_and_a_roll_are_refused_and_record_nothing(self):
        dying(self.gorn)
        before = len(Character.objects.get(pk=self.gorn.pk).history)
        outcome, *_rest = self.play(
            plan(
                {
                    "action": "skill_check",
                    "character_id": self.gorn.pk,
                    "skill": "stealth",
                },
                {"action": "survival_save", "character_id": self.gorn.pk},
                {"action": "roll", "dice": "3d6", "character_id": self.gorn.pk},
            )
        )
        self.assertEqual(
            [(result.ran, result.error) for result in outcome.results],
            [(False, AIDE_UNASKED_ROLL_REFUSAL)] * 3,
        )
        self.assertEqual(len(Character.objects.get(pk=self.gorn.pk).history), before)
