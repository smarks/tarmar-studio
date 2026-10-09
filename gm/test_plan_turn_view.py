"""The stream view running turns through the plan engine (issue #590)."""

import json
import time
from contextlib import ExitStack
from unittest.mock import Mock, patch

from django.test import override_settings

from common.test_helpers import BaseTestCase

from .engine import system_prompt_for
from .messages import build_interrupted_turn_message
from .models import GMEvent, GMSession, GMTurnRecord
from .retrieval import LEDGER_KIND, PROCEDURE_KIND, ROLL_KIND, SHEET_KIND
from .testing import a_mapped_place, add_character, recorded
from .tests import FakeMonotonicClock
from .turn_engine.model import Paused
from .views import PLAN_UNREADABLE_NOTICE, SSE_KEEPALIVE

STREAM_URL = "/tarmar/gm/stream/"

TWINS_PLAN = json.dumps(
    {
        "actions": [
            {
                "action": "record_attributes",
                "new_character": {"name": "Twain", "label": "the brother"},
                "all": 18,
            },
            {
                "action": "record_attributes",
                "new_character": {"name": "Thread"},
                "all": 18,
            },
        ]
    }
)


def frames(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: ") :])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def scripted(*replies, clock: FakeMonotonicClock | None = None):
    """A ``stream_chat`` stand-in answering each call with the next reply.

    Args:
        replies: One reply per model call, in order. A :class:`Paused` reply
            takes its stated time before answering.
        clock: A fake clock for a test that measures the turn. With one, a
            ``Paused`` reply moves that clock forward instead of sleeping,
            so the turn takes the time the fake model says it took rather
            than whatever a loaded machine allowed (issue #723).

    Returns:
        The ``stream_chat`` stand-in and the list its calls are recorded in.
    """
    calls = []

    def stream(model, messages, **kwargs):
        calls.append(messages)
        reply = replies[len(calls) - 1]
        if isinstance(reply, BaseException):
            raise reply
        pause = reply.seconds if isinstance(reply, Paused) else 0.0
        text = reply.reply if isinstance(reply, Paused) else reply

        def tokens():
            if clock is not None:
                clock.advance(pause)
            else:
                time.sleep(pause)
            yield from [text] if isinstance(text, str) else text

        return tokens()

    return stream, calls


class PlanTurnTestCase(BaseTestCase):
    """A signed-in Game Master and a way to play a turn with scripted replies."""

    def setUp(self):
        super().setUp()
        self.user = self.create_dm()
        self.login_as(self.user)

    def post(self, body: dict) -> tuple[str, list[dict]]:
        response = self.client.post(
            STREAM_URL, json.dumps(body), content_type="application/json"
        )
        self.assertTrue(response.streaming, getattr(response, "content", b""))
        text = b"".join(response.streaming_content).decode()
        return text, frames(text)

    def play(self, *replies, body=None, clock: FakeMonotonicClock | None = None):
        """Play one turn against ``replies``.

        Args:
            replies: What the model answers each call with.
            body: The request body, when the default opening message will
                not do.
            clock: A fake clock, for a test about how long the turn took.
                Given one, the view and the engine read it instead of
                ``time.monotonic``, so the only time that passes is the time
                a ``Paused`` reply says passed (issue #723).

        Returns:
            The raw SSE text, its frames, and the model calls made.
        """
        stream, calls = scripted(*replies, clock=clock)
        with ExitStack() as patches:
            patches.enter_context(
                patch(
                    "gm.turn_engine.model.ollama_client.stream_chat", side_effect=stream
                )
            )
            if clock is not None:
                view_time = Mock(wraps=time, monotonic=clock)
                patches.enter_context(patch("gm.views.time", view_time))
                patches.enter_context(patch("gm.turn_engine.engine.time", view_time))
            text, sent = self.post(
                body or {"message": "Twins called Twain and Thread, all 18s"}
            )
        return text, sent, calls


class PlanTurnViewTest(PlanTurnTestCase):
    def test_twins_are_planned_run_and_narrated(self):
        _text, sent, calls = self.play(TWINS_PLAN, ["Two figures ", "step forward."])

        statuses = [frame["status"] for frame in sent if "status" in frame]
        self.assertEqual(statuses[0], "planning")
        self.assertIn("recording attributes for Twain", statuses)
        self.assertEqual(statuses[-1], "writing")
        procedures = [frame["procedure"] for frame in sent if "procedure" in frame]
        self.assertEqual(len(procedures), 2)
        self.assertTrue(all(frame["ran"] for frame in procedures))
        done = sent[-1]
        self.assertTrue(done["done"])
        self.assertEqual(done["full_response"], "Two figures step forward.")
        self.assertEqual(len(done["procedures"]), 2)

        session = GMSession.objects.get(pk=done["session_id"])
        self.assertEqual(
            [character.name for character in session.attached_characters()],
            ["Twain", "Thread"],
        )
        self.assertEqual(
            [entry.get("kind") for entry in session.history],
            [None, PROCEDURE_KIND, PROCEDURE_KIND, None],
        )
        self.assertEqual(session.history[-1]["content"], "Two figures step forward.")

        plan_call, narrate_call = calls
        self.assertEqual(plan_call[0]["content"], system_prompt_for(True, session.mode))
        self.assertEqual(plan_call[:-1], narrate_call[:-1])
        self.assertTrue(
            plan_call[-1]["content"].endswith(
                "Latest message: Twins called Twain and Thread, all 18s"
            )
        )

        record = GMTurnRecord.objects.get(session=session)
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_OK)
        self.assertEqual(len(record.ledger_applied), 2)
        self.assertEqual(record.history_end, 4)
        kinds = list(GMEvent.objects.filter(turn=record).values_list("kind", flat=True))
        self.assertEqual(kinds.count("model_request"), 2)
        self.assertIn("narration", kinds)

    def test_the_model_reads_the_conversation_but_not_hidden_state_blocks(self):
        session = GMSession.objects.create(user=self.user, name="s", mode="play")
        session.history = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "SHEET", "kind": SHEET_KIND},
            {"role": "user", "content": "LEDGER", "kind": LEDGER_KIND},
            {
                "role": "user",
                "content": "chip",
                "kind": ROLL_KIND,
                "roll": {"line": "🎲"},
            },
            {"role": "assistant", "content": "Well met."},
        ]
        session.save()
        character = add_character(session, recorded("Gorn"))

        _text, sent, calls = self.play(
            '{"actions": []}',
            "Onward.",
            body={"session_id": session.pk, "message": "We ride"},
        )

        prefix = calls[0][:-1]
        self.assertEqual(
            [message["content"] for message in prefix[1:]], ["Hello", "Well met."]
        )
        self.assertIn(f"- id {character.pk}: Gorn", calls[0][-1]["content"])
        self.assertEqual(calls[1][:-1], prefix)
        self.assertNotIn("procedures", sent[-1])

    def test_a_player_is_refused_stated_scores_and_told_why(self):
        player = self.create_user(username="player")
        self.logout()
        self.login_as(player)

        _text, sent, calls = self.play(
            json.dumps(
                {
                    "actions": [
                        {
                            "action": "record_attributes",
                            "new_character": {"name": "Bob"},
                            "all": 18,
                        }
                    ]
                }
            ),
            "Bob must roll.",
        )

        (procedure,) = [frame["procedure"] for frame in sent if "procedure" in frame]
        self.assertFalse(procedure["ran"])
        self.assertIn("rolled, not stated", calls[1][-1]["content"])
        session = GMSession.objects.get(pk=sent[-1]["session_id"])
        self.assertEqual(list(session.attached_characters()), [])
        record = GMTurnRecord.objects.get(session=session)
        self.assertEqual(len(record.ledger_refused), 1)

    def test_dice_are_rolled_and_kept_as_chips(self):
        plan = json.dumps(
            {
                "actions": [
                    {
                        "action": "roll",
                        "dice": "2d6",
                        "purpose": "the patrol's reaction",
                    }
                ]
            }
        )

        _text, sent, _calls = self.play(
            plan,
            "The patrol shrugs.",
            body={"message": "Roll 2d6 for the patrol's reaction"},
        )

        rolls = [frame["gm_roll"] for frame in sent if "gm_roll" in frame]
        self.assertEqual(len(rolls), 1)
        self.assertTrue(
            rolls[0]["line"].startswith("🎲 GM rolls 2d6 for the patrol's reaction")
        )
        self.assertEqual(sent[-1]["gm_rolls"], rolls)
        session = GMSession.objects.get(pk=sent[-1]["session_id"])
        self.assertEqual(session.history[1]["kind"], ROLL_KIND)

    def test_an_aide_roll_for_a_table_fact_is_refused_not_run(self):
        """Issue #598: dice never decide what happened at the Game Master's table."""
        plan = json.dumps(
            {"actions": [{"action": "roll", "dice": "2d6", "purpose": "the guard"}]}
        )
        question = (
            "Last night my players killed the innkeeper at the Broken Wheel. "
            "Did the town guard see them do it?"
        )

        _text, sent, calls = self.play(
            plan, "I do not know; that is yours to say.", body={"message": question}
        )

        self.assertEqual([frame for frame in sent if "gm_roll" in frame], [])
        (procedure,) = [frame["procedure"] for frame in sent if "procedure" in frame]
        self.assertFalse(procedure["ran"])
        plan_call, narrate_call = calls
        session = GMSession.objects.get(pk=sent[-1]["session_id"])
        self.assertEqual(session.mode, "aide")
        self.assertEqual(plan_call[0]["content"], system_prompt_for(True, "aide"))
        self.assertEqual(plan_call[:-1], narrate_call[:-1])
        self.assertIn("Never plan a roll, or record anything", plan_call[-1]["content"])
        narrate = narrate_call[-1]["content"]
        self.assertIn("Not done: rolling dice, because", narrate)
        self.assertIn("dice do not decide what happened at his table", narrate)
        self.assertIn("say plainly that you do not know it", narrate)

    def test_a_play_turn_still_rolls_without_being_asked(self):
        session = GMSession.objects.create(user=self.user, name="s", mode="play")
        plan = json.dumps(
            {"actions": [{"action": "roll", "dice": "2d6", "purpose": "the guard"}]}
        )

        _text, sent, calls = self.play(
            plan,
            "The guard turns.",
            body={"message": "I slip past the guard", "session_id": session.pk},
        )

        self.assertEqual(len([frame for frame in sent if "gm_roll" in frame]), 1)
        plan_call, narrate_call = calls
        self.assertEqual(plan_call[0]["content"], system_prompt_for(True, "play"))
        self.assertEqual(plan_call[:-1], narrate_call[:-1])
        self.assertNotIn("Never plan a roll", plan_call[-1]["content"])
        self.assertNotIn("house rule", narrate_call[-1]["content"])

    def test_an_unreadable_plan_twice_runs_nothing_and_says_so(self):
        _text, sent, calls = self.play(
            "I will do it.", "Still prose.", "Say that again?"
        )

        self.assertEqual(len(calls), 3)
        self.assertIn(PLAN_UNREADABLE_NOTICE, sent[-1]["notices"])
        self.assertFalse(any("procedure" in frame for frame in sent))

    def test_a_slash_roll_is_narrated_without_planning(self):
        _text, sent, calls = self.play(
            "Seven it is.", body={"message": "2d6", "command": "roll"}
        )

        self.assertEqual(len(calls), 1)
        self.assertIn("The player rolled 2d6: [", calls[0][-1]["content"])
        self.assertEqual(sent[-1]["roll"]["expression"], "2d6")

    def test_a_retry_after_a_break_narrates_what_already_ran(self):
        session = GMSession.objects.create(user=self.user, name="s", mode="aide")
        session.history = [
            {"role": "user", "content": "Roll Fatigue."},
            {
                "role": "user",
                "content": "Gorn: Fatigue roll (2d6) rolled 3 and 4 = 7.",
                "kind": PROCEDURE_KIND,
                "procedure": {"lines": ["⚙️ x"]},
            },
            build_interrupted_turn_message(),
        ]
        session.save()

        _text, sent, calls = self.play(
            "Seven.",
            body={"session_id": session.pk, "message": "Roll Fatigue.", "retry": True},
        )

        self.assertEqual(len(calls), 1)
        self.assertIn(
            "Already done when this message was first sent: Gorn: Fatigue roll",
            calls[0][-1]["content"],
        )
        self.assertEqual(
            calls[0][-1]["content"].count("Latest message: Roll Fatigue."), 1
        )
        session.refresh_from_db()
        self.assertEqual(
            session.history[-1], {"role": "assistant", "content": "Seven."}
        )

    @patch("gm.views.GM_STREAM_KEEPALIVE_SECONDS", 0.05)
    def test_a_slow_plan_keeps_the_page_talking(self):
        text, sent, _calls = self.play(Paused(0.3, '{"actions": []}'), "Fine.")

        self.assertIn(SSE_KEEPALIVE, text)
        waiting = [frame for frame in sent if "elapsed" in frame]
        self.assertEqual(waiting[0]["status"], "planning")

    @override_settings(GM_STREAM_BUDGET_SECONDS=0.1)
    @patch("gm.views.GM_STREAM_KEEPALIVE_SECONDS", 0.02)
    def test_a_turn_out_of_time_keeps_what_ran_and_says_so(self):
        # The clock moves only when the fake model says it has, so both
        # twins are recorded before the narrate call spends the budget
        # however busy the machine is. Racing a real time.monotonic() here
        # made the assertion below fail as 1 != 2 under load (issue #723).
        _text, sent, _calls = self.play(
            TWINS_PLAN, Paused(0.4, "Too late."), clock=FakeMonotonicClock()
        )

        last = sent[-1]
        self.assertIn("error", last)
        self.assertIn("all six attributes recorded as 18", " ".join(last["notices"]))
        session = GMSession.objects.get(pk=last["session_id"])
        self.assertEqual(session.history[-1]["kind"], "interrupted")
        self.assertEqual(len(list(session.attached_characters())), 2)
        record = GMTurnRecord.objects.get(session=session)
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_TOO_SLOW)
        self.assertIn(
            "failed", GMEvent.objects.filter(turn=record).values_list("kind", flat=True)
        )

    def test_a_model_failure_is_reported(self):
        with self.assertLogs("gm.views", "ERROR"):
            _text, sent, _calls = self.play(ConnectionError("model gone"))

        self.assertEqual(sent[-1], {"error": "model gone"})
        record = GMTurnRecord.objects.get(user=self.user)
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_ERROR)
        self.assertIsNone(record.history_end)

    def test_a_grounding_step_that_fails_is_told_not_fatal(self):
        with (
            patch(
                "gm.services.pending_battle_entries", side_effect=RuntimeError("boom")
            ),
            self.assertLogs("gm.services", "ERROR") as logs,
        ):
            _text, sent, _calls = self.play(
                '{"actions": []}',
                "Onward.",
                body={
                    "session_id": GMSession.objects.create(user=self.user, name="s").pk,
                    "message": "go",
                },
            )
        self.assertIn("battle log could not be attached", sent[-1]["notices"][0])
        self.assertIn("RuntimeError", "\n".join(logs.output))

    def test_a_narration_naming_a_place_moves_the_map(self):
        _text, sent, _calls = self.play(
            '{"actions": []}', f"You reach {a_mapped_place()} at dusk."
        )
        self.assertIn("map", sent[-1])


class PlanTurnTranscriptTest(PlanTurnTestCase):
    """A plan turn's events are in its transcript, text and JSON (issue #590)."""

    def test_the_transcript_lists_the_turns_events(self):
        _text, sent, _calls = self.play(TWINS_PLAN, "Two figures step forward.")
        session_id = sent[-1]["session_id"]
        url = f"/tarmar/gm/sessions/{session_id}/transcript/"

        text = self.client.get(url).content.decode()
        self.assertIn("  events:", text)
        self.assertIn("state: planning", text)
        self.assertIn("model call plan: 1 messages before the last", text)
        self.assertIn(
            "model call narrate: 1 messages before the last (unchanged)", text
        )
        self.assertIn('model reply to plan:\n    | {"actions"', text)
        self.assertIn("action: Twain (the brother) is a new character", text)
        self.assertIn("narration saved (25 characters)", text)
        self.assertIn('plan: {"actions"', text)

        payload = json.loads(self.client.get(url, {"format": "json"}).content)
        events = payload["turns"][0]["events"]
        request = next(event for event in events if event["kind"] == "model_request")
        self.assertEqual(
            set(request["payload"]),
            {"call", "model", "prefix_messages", "last_message"},
        )
        self.assertTrue(
            request["payload"]["last_message"]["content"].endswith("all 18s")
        )

    def test_a_roll_event_reads_as_its_dice_line(self):
        plan = json.dumps({"actions": [{"action": "roll", "dice": "1d6"}]})
        _text, sent, _calls = self.play(plan, "Fine.", body={"message": "Roll 1d6"})
        text = self.client.get(
            f"/tarmar/gm/sessions/{sent[-1]['session_id']}/transcript/"
        ).content.decode()
        self.assertIn("roll: 🎲 GM rolls 1d6: [", text)


class AFailureWithNoMessageTest(PlanTurnTestCase):
    """A failure says something, however the exception was raised (#663).

    ``str()`` of an exception raised with no arguments is ``""``. The view
    streamed that verbatim, chat.js guarded its whole failure path behind
    ``if (data.error)``, and no done frame follows a failure -- so the stream
    ended, the thinking line stayed, and the player watched it for good while
    the server had the turn recorded as failed.
    """

    def test_the_stream_says_what_went_wrong(self):
        _text, sent, _calls = self.play(RuntimeError())

        self.assertEqual(sent[-1].get("error"), "RuntimeError")

    def test_the_record_keeps_the_same_reason(self):
        self.play(RuntimeError())

        record = GMTurnRecord.objects.latest("pk")
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_ERROR)
        self.assertEqual(record.error, "RuntimeError")

    def test_a_failure_that_does_say_why_is_unchanged(self):
        _text, sent, _calls = self.play(RuntimeError("the model is down"))

        self.assertEqual(sent[-1].get("error"), "the model is down")
