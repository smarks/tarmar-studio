"""A first message's refused action keeps the session it created (issue #908).

A Game Master's first message has no session yet; the first action that needs
one creates it. That creation ran inside the action's own transaction, so an
action refused after it -- a character name the name rule refuses (#870),
"Twin 1 (Male)" -- rolled the new session back with the refused writes. The
executor and the view kept the unsaved instance: the turn's commit found no
session and raised out of the stream, and the turn record, attached to the
vanished session, stayed "running".
"""

import json

from gm.test_plan_turn_view import PlanTurnTestCase

from .models import GMSession, GMTurnRecord


def plan(*actions: dict) -> str:
    return json.dumps({"actions": list(actions)})


ALL_18 = {
    name: 18
    for name in (
        "strength",
        "dexterity",
        "intelligence",
        "wisdom",
        "constitution",
        "charisma",
    )
}


class ARefusedFirstActionKeepsTheSessionTest(PlanTurnTestCase):
    def assert_the_turn_ends_with_its_session(self, sent: list[dict]) -> GMSession:
        errors = [frame["error"] for frame in sent if "error" in frame]
        self.assertEqual(errors, [])
        done = sent[-1]
        self.assertTrue(done.get("done"), sent[-3:])
        session = GMSession.objects.get(pk=done["session_id"])
        turn = GMTurnRecord.objects.get(session=session)
        self.assertNotEqual(turn.outcome, "running")
        return session

    def test_a_refused_character_name_is_a_refusal_not_a_crash(self):
        _text, sent, _calls = self.play(
            plan(
                {
                    "action": "record_attributes",
                    "new_character": {"name": "Twin 1 (Male)", "label": "male twin"},
                    "scores": ALL_18,
                },
                {"action": "roll_fatigue", "new_character_ref": "Twin 1 (Male)"},
            ),
            ["Nothing was made."],
            body={"message": "Twins, all 18s: Twin 1 (Male) and Twin 2 (Female)"},
        )
        session = self.assert_the_turn_ends_with_its_session(sent)
        procedures = [frame["procedure"] for frame in sent if "procedure" in frame]
        self.assertEqual([step["ran"] for step in procedures], [False, False])
        self.assertIn("Name may contain", procedures[0]["error"])
        self.assertEqual(list(session.attached_characters()), [])

    def test_a_refused_campaign_action_keeps_the_session_too(self):
        # The same class on another path: a first message whose action is
        # refused after it asked for the session (spending torches the party
        # does not carry).
        _text, sent, _calls = self.play(
            plan({"action": "adjust_supplies", "item": "torches", "spent": True}),
            ["The torches are already gone."],
            body={"message": "We burn the last of our torches."},
        )
        self.assert_the_turn_ends_with_its_session(sent)
        procedures = [frame["procedure"] for frame in sent if "procedure" in frame]
        self.assertEqual([step["ran"] for step in procedures], [False])
