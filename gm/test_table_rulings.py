"""A Game Master's house rules as recorded table rulings (issue #600)."""

import json

from django.urls import reverse

from common.test_helpers import BaseTestCase

from .engine import system_prompt_for
from .models import GMEvent, GMSession, GMTurnRecord, TableRuling
from .refusal_words import refusal_words
from .test_plan_turn_view import PlanTurnTestCase
from .testing import run_actions
from .turn_engine import events
from .turn_engine.executor import RecordsExecutor
from .turn_engine.messages import RULINGS_APPLY
from .turn_engine.plan import read_plan
from .turn_engine.policy import TurnPolicy
from .turn_engine.state import RULINGS_HEADING, ruling_lines

CRIT_QUESTION = (
    "At my table a natural 20 just crits — no confirmation roll. A player has "
    "rolled a natural 20 with a sword. What damage does he take?"
)

CRIT_RULE = "a natural 20 just crits, with no confirmation roll"
CRIT_LINE_START = "- critical hits (ruling "


def record_crits(topic: str = "critical hits", rule: str = CRIT_RULE) -> dict:
    return {"action": "record_house_rule", "topic": topic, "rule": rule}


def plan_of(*actions: dict) -> str:
    return json.dumps({"actions": list(actions)})


def rulings_section(content: str) -> str:
    """The state block's rulings, as a call's last message carries them."""
    start = content.index(RULINGS_HEADING)
    return content[start : content.index("\n\n", start)]


class RulingActionsTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm()
        self.session = GMSession.objects.create(user=self.game_master, name="s")

    def test_a_game_master_records_a_ruling_and_its_event(self):
        policy = TurnPolicy.for_turn("aide", is_game_master=True)
        reading = read_plan(plan_of(record_crits()), roster_ids=set(), policy=policy)
        executor = RecordsExecutor(
            policy, self.session, lambda: self.session, self.game_master
        )
        recorded = []

        (result,) = [
            executor.run(action, recorded.append) for action in reading.actions
        ]

        ruling = TableRuling.objects.get(session=self.session)
        self.assertEqual(
            (ruling.topic, ruling.text, ruling.active),
            ("critical hits", CRIT_RULE, True),
        )
        self.assertTrue(result.ran)
        self.assertIn(
            f"Recorded the ruling on critical hits (ruling {ruling.pk})", result.result
        )
        assert result.procedure is not None
        self.assertEqual(
            result.procedure["lines"], [f"📜 Table ruling, critical hits: {CRIT_RULE}"]
        )
        self.assertEqual(
            [(event.kind, event.payload.get("change")) for event in recorded],
            [(events.RULING, "recorded"), (events.ACTION, None)],
        )
        self.assertEqual(
            executor.rulings(), [f"- critical hits (ruling {ruling.pk}): {CRIT_RULE}"]
        )

    def test_a_players_ruling_is_refused_and_told_why(self):
        (result,) = run_actions(self.session, record_crits(), is_game_master=False)

        self.assertFalse(result.ran)
        self.assertEqual(
            result.result,
            f"Not done: recording a house rule, because {refusal_words().policy.house_rule}.",
        )
        self.assertFalse(TableRuling.objects.exists())

    def test_a_player_may_not_retire_one_either(self):
        run_actions(self.session, record_crits())
        ruling = TableRuling.objects.get()

        (result,) = run_actions(
            self.session,
            {"action": "retire_house_rule", "ruling_id": ruling.pk},
            is_game_master=False,
        )

        self.assertIn(refusal_words().policy.house_rule, result.result)
        self.assertTrue(TableRuling.objects.get().active)

    def test_a_second_ruling_on_the_topic_supersedes_the_first(self):
        run_actions(self.session, record_crits())
        first = TableRuling.objects.get()

        (result,) = run_actions(
            self.session,
            record_crits("Critical  Hits", "a natural 19 or 20 crits"),
        )

        first.refresh_from_db()
        self.assertFalse(first.active)
        self.assertIsNotNone(first.retired_at)
        second = TableRuling.objects.get(active=True)
        self.assertEqual(second.text, "a natural 19 or 20 crits")
        self.assertIn(f"it replaces ruling {first.pk}: {CRIT_RULE}", result.result)
        self.assertEqual(
            ruling_lines(self.session),
            [f"- Critical Hits (ruling {second.pk}): a natural 19 or 20 crits"],
        )

    def test_rulings_on_other_topics_stand_side_by_side(self):
        run_actions(
            self.session,
            record_crits(),
            record_crits("fumbles", "a natural 1 drops the weapon"),
        )

        self.assertEqual(TableRuling.objects.filter(active=True).count(), 2)

    def test_retiring_removes_a_ruling_from_the_state(self):
        run_actions(self.session, record_crits())
        ruling = TableRuling.objects.get()

        (result,) = run_actions(
            self.session, {"action": "retire_house_rule", "ruling_id": ruling.pk}
        )

        self.assertTrue(result.ran)
        self.assertEqual(ruling_lines(self.session), [])
        self.assertIn("Retired the ruling on critical hits", result.result)

    def test_retiring_a_ruling_the_table_does_not_have_is_refused(self):
        (result,) = run_actions(
            self.session, {"action": "retire_house_rule", "ruling_id": 999}
        )

        self.assertFalse(result.ran)
        self.assertIn(
            refusal_words().executor.no_active_ruling.format(id=999), result.result
        )

    def test_a_malformed_ruling_is_not_a_plan(self):
        policy = TurnPolicy.for_turn("aide", is_game_master=True)
        for action in (
            {"action": "record_house_rule", "topic": "crits"},
            {"action": "record_house_rule", "topic": "x" * 61, "rule": "r"},
            {"action": "retire_house_rule", "ruling_id": "crits"},
        ):
            with self.subTest(action=action):
                self.assertFalse(
                    read_plan(plan_of(action), roster_ids=set(), policy=policy).ok
                )


class RulingTurnViewTest(PlanTurnTestCase):
    def test_the_crit_question_records_the_ruling_and_the_narration_answers_by_it(self):
        _text, sent, calls = self.play(
            plan_of(record_crits()),
            "No confirmation: it is a crit.",
            body={"message": CRIT_QUESTION},
        )

        (procedure,) = [frame["procedure"] for frame in sent if "procedure" in frame]
        self.assertTrue(procedure["ran"])
        plan_call, narrate_call = calls
        session = GMSession.objects.get(pk=sent[-1]["session_id"])
        self.assertEqual(session.mode, "aide")
        self.assertEqual(plan_call[0]["content"], system_prompt_for(True, "aide"))
        self.assertEqual(plan_call[:-1], narrate_call[:-1])
        self.assertIn("record it with record_house_rule", plan_call[-1]["content"])
        self.assertNotIn(RULINGS_HEADING, plan_call[-1]["content"])
        narrate = narrate_call[-1]["content"]
        ruling = TableRuling.objects.get(session=session)
        self.assertIn(
            f"{RULINGS_HEADING}\n- critical hits (ruling {ruling.pk}): {CRIT_RULE}",
            narrate,
        )
        self.assertIn(RULINGS_APPLY, narrate)
        self.assertNotIn(CRIT_RULE, plan_call[0]["content"])

        record = GMTurnRecord.objects.get(session=session)
        self.assertEqual(ruling.turn, record)
        ruling_events = GMEvent.objects.filter(turn=record, kind=events.RULING)
        self.assertEqual(
            [event.payload["change"] for event in ruling_events], ["recorded"]
        )
        transcript = self.client.get(
            reverse("gm:transcript", args=[session.pk])
        ).content.decode()
        self.assertIn(
            f"ruling recorded: critical hits (ruling {ruling.pk}): {CRIT_RULE}",
            transcript,
        )

    def test_later_calls_list_the_rulings_identically_in_plan_and_narrate(self):
        session = GMSession.objects.create(user=self.user, name="s", mode="aide")
        run_actions(session, record_crits())

        _text, _sent, calls = self.play(
            plan_of(),
            "Triple damage.",
            body={
                "message": "What does a crit with a sword do?",
                "session_id": session.pk,
            },
        )

        plan_call, narrate_call = calls
        self.assertEqual(plan_call[:-1], narrate_call[:-1])
        plan_rulings = rulings_section(plan_call[-1]["content"])
        self.assertIn(CRIT_LINE_START, plan_rulings)
        self.assertEqual(plan_rulings, rulings_section(narrate_call[-1]["content"]))
        self.assertIn(RULINGS_APPLY, narrate_call[-1]["content"])

    def test_a_retired_ruling_leaves_the_state_block(self):
        session = GMSession.objects.create(user=self.user, name="s", mode="aide")
        run_actions(session, record_crits())
        ruling = TableRuling.objects.get()

        _text, _sent, calls = self.play(
            plan_of({"action": "retire_house_rule", "ruling_id": ruling.pk}),
            "Back to the book.",
            body={"message": "Forget my crit rule.", "session_id": session.pk},
        )

        plan_call, narrate_call = calls
        self.assertIn(CRIT_LINE_START, plan_call[-1]["content"])
        self.assertNotIn(RULINGS_HEADING, narrate_call[-1]["content"])
        self.assertNotIn(RULINGS_APPLY, narrate_call[-1]["content"])
        self.assertIn(
            "Retired the ruling on critical hits", narrate_call[-1]["content"]
        )

    def test_play_mode_sees_the_rulings_too(self):
        session = GMSession.objects.create(user=self.user, name="s", mode="play")
        run_actions(session, record_crits())

        _text, _sent, calls = self.play(
            plan_of(),
            "Your blade bites deep.",
            body={
                "message": "I swing and roll a natural 20.",
                "session_id": session.pk,
            },
        )

        plan_call, narrate_call = calls
        self.assertEqual(plan_call[0]["content"], system_prompt_for(True, "play"))
        self.assertEqual(plan_call[:-1], narrate_call[:-1])
        self.assertIn(CRIT_LINE_START, plan_call[-1]["content"])
        self.assertIn(CRIT_LINE_START, narrate_call[-1]["content"])
        self.assertIn(RULINGS_APPLY, narrate_call[-1]["content"])

    def test_a_player_cannot_set_a_ruling_through_a_turn(self):
        player = self.create_user(username="player")
        self.logout()
        self.login_as(player)

        _text, sent, calls = self.play(
            plan_of(record_crits()),
            "That is for your Game Master.",
            body={"message": "At my table a natural 20 just crits."},
        )

        (procedure,) = [frame["procedure"] for frame in sent if "procedure" in frame]
        self.assertFalse(procedure["ran"])
        self.assertIn(refusal_words().policy.house_rule, calls[1][-1]["content"])
        self.assertFalse(TableRuling.objects.exists())

    def test_the_panel_lists_the_tables_rulings(self):
        # With the table's other records in the Campaign tab since #789; this
        # read the Characters tab, where issue #789 found them out of place.
        session = GMSession.objects.create(user=self.user, name="s", mode="aide")
        empty = self.client.get(reverse("gm:campaign_panel", args=[session.pk]))
        self.assertContains(empty, "No rulings at this table yet.")
        self.assertNotContains(empty, CRIT_RULE)

        run_actions(session, record_crits())

        panel = self.client.get(reverse("gm:campaign_panel", args=[session.pk]))
        self.assertContains(panel, "Table rulings")
        self.assertContains(panel, CRIT_RULE)
        characters = self.client.get(reverse("gm:characters_panel", args=[session.pk]))
        self.assertNotContains(characters, "Table rulings")
