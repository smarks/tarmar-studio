"""The GM's own dice: plan action, roll service, dice line, reload (#363, #590).

Nothing here reaches Ollama. Every die is either patched at
``common.rolling``'s randomness source or rolled through a seeded
:class:`common.rolling.Roller`, so the suite proves the roll path without a
runner and without a real random number.
"""

import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from common.rolling import Roller
from common.test_helpers import BaseTestCase

from .engine import SECTION_OUTCOMES, roll_dice_record, section_character_creation
from .roll_service import (
    RollRequest,
    RollResolution,
    dice_line,
    resolve_roll_request,
    resolve_roll_series,
    roll_frame_payload,
)
from .testing import gm_replies
from .turn_engine.messages import PLANNING_GUIDANCE


def frames(response) -> list[dict]:
    """Every SSE payload of a streamed response, in order."""
    body = b"".join(response.streaming_content).decode()
    return [
        json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")
    ]


class ResolveRollRequestTest(SimpleTestCase):
    def test_a_roll_goes_through_the_project_roll_service(self):
        with patch("common.rolling.random.randint", side_effect=[4, 3]):
            resolution = resolve_roll_request(
                RollRequest(expression="2d6", purpose="reaction")
            )
        assert resolution.record is not None
        self.assertTrue(resolution.rolled)
        self.assertEqual(resolution.record.faces, (4, 3))
        self.assertEqual(resolution.record.total, 7)
        self.assertEqual(resolution.record.purpose, "reaction")

    def test_a_modifier_lands_on_the_record_not_on_the_faces(self):
        with patch("common.rolling.random.randint", side_effect=[11]):
            resolution = resolve_roll_request(RollRequest(expression="1d20+3"))
        assert resolution.record is not None
        self.assertEqual(resolution.record.faces, (11,))
        self.assertEqual(resolution.record.modifier, 3)
        self.assertEqual(resolution.record.total, 14)

    def test_a_seeded_roller_reproduces_the_same_faces(self):
        """Fidelity: the GM's dice replay like every other die in the project."""
        first = roll_dice_record("3d6", purpose="reaction")
        seeded = Roller(seed=1234).roll("3d6", purpose="reaction")
        again = Roller(seed=1234).roll("3d6", purpose="reaction")
        self.assertEqual(seeded, again)
        self.assertEqual(len(first.faces), 3)

    def test_an_unrollable_expression_yields_an_error_and_no_number(self):
        resolution = resolve_roll_request(RollRequest(expression="two sixes"))
        self.assertIsNone(resolution.record)
        self.assertFalse(resolution.rolled)
        self.assertTrue(resolution.error)

    def test_an_out_of_bounds_expression_is_refused_by_the_roll_service(self):
        resolution = resolve_roll_request(RollRequest(expression="9999d6"))
        self.assertIsNone(resolution.record)
        self.assertTrue(resolution.error)


class RollSeriesTest(SimpleTestCase):
    """#376: one marker, several separate rolls -- never one larger pool."""

    def test_a_count_gives_that_many_independent_rolls(self):
        with patch("common.rolling.random.randint", side_effect=[1, 2, 3, 4, 5, 6]):
            resolutions = resolve_roll_series(
                RollRequest(expression="1d6", purpose="attributes", times=6)
            )
        self.assertEqual(len(resolutions), 6)
        totals = [r.record.total for r in resolutions if r.record is not None]
        self.assertEqual(totals, [1, 2, 3, 4, 5, 6])

    def test_each_resolution_carries_a_single_roll_request(self):
        with patch("common.rolling.random.randint", return_value=3):
            resolutions = resolve_roll_series(RollRequest(expression="1d6", times=3))
        self.assertEqual({r.request.times for r in resolutions}, {1})

    def test_no_count_is_still_exactly_one_roll(self):
        with patch("common.rolling.random.randint", return_value=3):
            self.assertEqual(len(resolve_roll_series(RollRequest(expression="1d6"))), 1)


class RollLineTest(SimpleTestCase):
    def rolled(self, expression="2d6", faces=(4, 3), **kwargs) -> RollResolution:
        with patch("common.rolling.random.randint", side_effect=list(faces)):
            return resolve_roll_request(RollRequest(expression=expression, **kwargs))

    def test_the_label_reads_as_a_phrase(self):
        self.assertEqual(
            RollRequest(expression="2d6", purpose="reaction", roller="Grishak").label,
            "2d6 for reaction (Grishak)",
        )

    def test_the_dice_line_shows_the_faces_and_never_recomputes(self):
        self.assertEqual(
            dice_line(self.rolled(purpose="reaction")),
            "🎲 GM rolls 2d6 for reaction: [4, 3] = 7",
        )
        self.assertEqual(
            dice_line(self.rolled(roller="Grishak")), "🎲 Grishak rolls 2d6: [4, 3] = 7"
        )

    def test_a_failed_dice_line_says_no_dice_were_rolled(self):
        line = dice_line(resolve_roll_request(RollRequest(expression="nope")))
        self.assertIn("no dice were rolled", line)

    def test_the_frame_payload_mirrors_the_record(self):
        payload = roll_frame_payload(self.rolled(purpose="reaction"))
        self.assertEqual(payload["expression"], "2d6")
        self.assertEqual(payload["rolls"], "4, 3")
        self.assertEqual(payload["total"], 7)
        self.assertEqual(payload["purpose"], "reaction")
        self.assertIn("line", payload)

    def test_a_failed_frame_payload_carries_the_error_and_no_total(self):
        payload = roll_frame_payload(resolve_roll_request(RollRequest(expression="x")))
        self.assertNotIn("total", payload)
        self.assertTrue(payload["error"])


class DicePromptTest(SimpleTestCase):
    """Drift guards on what the prompt and plan say about dice (#363, #376)."""

    def test_it_forbids_the_disclaimer_that_started_this(self):
        self.assertIn("Never say you cannot roll dice", SECTION_OUTCOMES)
        self.assertIn("never ask the player or the Game", SECTION_OUTCOMES)
        self.assertIn("Never invent a roll, a total or an outcome", SECTION_OUTCOMES)

    def test_a_roll_asked_for_is_planned_now(self):
        self.assertIn(
            "When the message asks for a roll, plan it now", PLANNING_GUIDANCE
        )
        self.assertIn("never waits on a target number", PLANNING_GUIDANCE)

    def test_character_creation_says_whose_dice_the_attributes_are(self):
        self.assertIn(
            "rolled for the player, never by the player", section_character_creation()
        )


@override_settings(GM_RETRIEVAL_ENABLED=False)
class GMRollTurnTest(BaseTestCase):
    """A turn whose plan rolls dice, and the player's own /roll."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def post(self, body: dict, stream) -> list[dict]:
        with patch(
            "gm.turn_engine.model.ollama_client.stream_chat", side_effect=stream
        ):
            return frames(
                self.client.post(
                    "/tarmar/gm/stream/",
                    json.dumps(body),
                    content_type="application/json",
                )
            )

    def test_the_narration_is_told_the_real_result_and_the_page_keeps_it(self):
        plan = json.dumps(
            {"actions": [{"action": "roll", "dice": "2d6", "purpose": "reaction"}]}
        )
        stream = gm_replies("They pass.", plans=[plan])
        with patch("common.rolling.random.randint", side_effect=[4, 3]):
            payloads = self.post({"message": "The patrol spots me"}, stream)

        (roll,) = [frame["gm_roll"] for frame in payloads if "gm_roll" in frame]
        self.assertEqual(roll["line"], "🎲 GM rolls 2d6 for reaction: [4, 3] = 7")
        self.assertIn(
            "GM rolls 2d6 for reaction: [4, 3] = 7", stream.calls[1][-1]["content"]
        )
        session_id = payloads[-1]["session_id"]
        page = self.client.get(f"/tarmar/gm/?session={session_id}")
        self.assertContains(page, "dice-roll")
        self.assertContains(page, "= 7")

    def test_an_unrollable_request_tells_the_player_and_invents_nothing(self):
        plan = json.dumps({"actions": [{"action": "roll", "dice": "0d6"}]})
        stream = gm_replies("No dice fell.", plans=[plan])
        payloads = self.post({"message": "Roll a lot"}, stream)

        (roll,) = [frame["gm_roll"] for frame in payloads if "gm_roll" in frame]
        self.assertIn("no dice were rolled", roll["line"])
        self.assertNotIn("total", roll)

    def test_the_players_own_roll_command_is_untouched(self):
        with patch("common.rolling.random.randint", side_effect=[4, 3, 6]):
            payloads = self.post(
                {"message": "3d6", "command": "roll"}, gm_replies("A fine roll.")
            )
        done = [f for f in payloads if f.get("done")][0]
        self.assertEqual(
            done["roll"], {"expression": "3d6", "rolls": "4, 3, 6", "total": 13}
        )
        self.assertNotIn("gm_rolls", done)
