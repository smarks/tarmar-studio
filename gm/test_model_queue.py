"""A turn waiting behind other tables' turns for the model (issue #64).

Ollama on the production host answers one request at a time. A turn sent
while others were being answered sat on "The Game Master is working:
planning" with nothing to say it was waiting its turn, and a turn the line
held past its budget was told the model had gone cold and to send again,
which only put another turn in the line.
"""

import json
import time
from collections.abc import Iterator
from datetime import timedelta
from unittest.mock import Mock, patch

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from common.test_helpers import BaseTestCase

from . import views
from .models import GMSession, GMTurnRecord
from .test_summary import turns
from .test_turn_engine import engine_for, request, run_turn
from .tests import FakeMonotonicClock
from .turn_engine import events
from .turn_engine.engine import TurnBudgetExceeded
from .turn_engine.model import FakeModel, Paused
from .turn_engine.model_queue import TURNS_AHEAD_KEY, ModelQueueWait
from .turn_record import RUNNING_TURN_GRACE_SECONDS, turns_ahead_of

STREAM_URL = "/tarmar/gm/stream/"

#: Fixed wording, so the tests pin the code to its settings and not to the
#: shipped defaults.
ONE_AHEAD = "Busy with one other table ({seconds}s)"
MANY_AHEAD = "Busy: {turns_ahead} turns ahead ({seconds}s)"
COLD = "The model was slow."
QUEUED = "Waited {waited_seconds} of {budget_seconds}s behind other tables."


def queue_with(clock: FakeMonotonicClock, *ahead_at_each_look: int) -> ModelQueueWait:
    """A queue wait whose looks find these counts, in order."""
    counts = iter(ahead_at_each_look)
    return ModelQueueWait(turns_ahead=lambda: next(counts), clock=clock)


class ModelQueueWaitTest(SimpleTestCase):
    """What counts as time spent waiting behind other turns."""

    def test_silences_with_turns_ahead_are_counted(self):
        clock = FakeMonotonicClock()
        queue = queue_with(clock, 2, 1)
        queue.call_started()

        clock.advance(10)
        self.assertEqual(queue.look(), 2)
        clock.advance(10)
        self.assertEqual(queue.look(), 1)

        self.assertEqual(queue.waited(), 20)
        self.assertTrue(queue.still_waiting)

    def test_a_silence_with_nobody_ahead_is_not_counted(self):
        clock = FakeMonotonicClock()
        queue = queue_with(clock, 0)
        queue.call_started()

        clock.advance(10)
        self.assertEqual(queue.look(), 0)

        self.assertEqual(queue.waited(), 0)
        self.assertFalse(queue.still_waiting)

    def test_a_call_still_waiting_is_counted_up_to_now(self):
        clock = FakeMonotonicClock()
        queue = queue_with(clock, 1)
        queue.call_started()
        clock.advance(10)
        queue.look()

        clock.advance(4)

        self.assertEqual(queue.waited(), 14)

    def test_once_served_the_model_working_is_not_waiting(self):
        clock = FakeMonotonicClock()
        queue = queue_with(clock, 1)
        queue.call_started()
        clock.advance(10)
        queue.look()
        queue.served()

        clock.advance(100)

        self.assertEqual(queue.waited(), 10)
        self.assertFalse(queue.still_waiting)

    def test_each_call_waits_afresh_and_the_waits_add_up(self):
        clock = FakeMonotonicClock()
        queue = queue_with(clock, 1, 3)
        queue.call_started()
        clock.advance(10)
        queue.look()
        queue.served()
        clock.advance(50)

        queue.call_started()
        clock.advance(10)
        queue.look()

        self.assertEqual(queue.waited(), 20)
        self.assertEqual(queue.ahead, 3)


class TurnsAheadOfTest(BaseTestCase):
    """The app's own view of the line: running turns claimed earlier."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("waiting")
        self.other = self.create_user("other-table")

    def running(self, user, **fields) -> GMTurnRecord:
        return GMTurnRecord.objects.create(
            user=user, outcome=GMTurnRecord.OUTCOME_RUNNING, mode="play", **fields
        )

    def test_counts_running_turns_claimed_before_this_one(self):
        self.running(self.other)
        self.running(self.player)
        mine = self.running(self.player)

        self.assertEqual(turns_ahead_of(mine.pk), 2)

    def test_leaves_out_finished_later_and_dead_turns(self):
        finished = self.running(self.other)
        finished.outcome = GMTurnRecord.OUTCOME_OK
        finished.save()
        dead_for = (
            settings.GM_RECAP_BUDGET_SECONDS
            + settings.GM_STREAM_BUDGET_SECONDS
            + RUNNING_TURN_GRACE_SECONDS
            + 60
        )
        self.running(
            self.other, started_at=timezone.now() - timedelta(seconds=dead_for)
        )
        mine = self.running(self.player)
        self.running(self.other)

        self.assertEqual(turns_ahead_of(mine.pk), 0)


@override_settings(
    GM_QUEUE_STATUS_ONE_AHEAD=ONE_AHEAD,
    GM_QUEUE_STATUS_MANY_AHEAD=MANY_AHEAD,
    GM_STREAM_TOO_SLOW_MESSAGE=COLD,
    GM_STREAM_QUEUED_TOO_LONG_MESSAGE=QUEUED,
)
class QueueWordingTest(SimpleTestCase):
    """The working line and the timeout message, from the settings' wording."""

    def test_one_turn_ahead(self):
        self.assertEqual(
            views.queue_status_line(1, 40), "Busy with one other table (40s)"
        )

    def test_several_turns_ahead_says_how_many(self):
        self.assertEqual(views.queue_status_line(3, 40), "Busy: 3 turns ahead (40s)")

    def test_a_waiting_frame_with_turns_ahead_carries_the_line(self):
        frame = views._waiting_frame(
            {"status": "planning", "elapsed": 20, TURNS_AHEAD_KEY: 2}
        )

        self.assertEqual(
            frame,
            {
                "status": "planning",
                "elapsed": 20,
                views.BUSY_FRAME_KEY: "Busy: 2 turns ahead (20s)",
            },
        )

    def test_a_waiting_frame_with_nobody_ahead_is_unchanged(self):
        frame = views._waiting_frame({"status": "planning", "elapsed": 20})

        self.assertEqual(frame, {"status": "planning", "elapsed": 20})

    def test_a_turn_cut_while_still_waiting_is_told_it_was_the_queue(self):
        clock = FakeMonotonicClock()
        queue = queue_with(clock, 1)
        queue.call_started()
        clock.advance(290)
        queue.look()
        clock.advance(12)

        # Counted to the moment of the cut, and never past the budget.
        self.assertEqual(
            views.too_slow_message(queue, 300),
            "Waited 300 of 300s behind other tables.",
        )

    def test_a_turn_that_spent_half_its_budget_waiting_is_told_so(self):
        clock = FakeMonotonicClock()
        queue = queue_with(clock, 2)
        queue.call_started()
        clock.advance(150)
        queue.look()
        queue.served()
        clock.advance(160)

        self.assertEqual(
            views.too_slow_message(queue, 300),
            "Waited 150 of 300s behind other tables.",
        )

    def test_a_turn_that_was_slow_on_its_own_keeps_the_ordinary_message(self):
        clock = FakeMonotonicClock()
        queue = queue_with(clock, 1)
        queue.call_started()
        clock.advance(10)
        queue.look()
        queue.served()
        clock.advance(300)

        self.assertEqual(views.too_slow_message(queue, 300), COLD)


class ShippedQueueWordingTest(SimpleTestCase):
    """The default wording says what the issue asked it to."""

    def test_the_line_says_the_game_master_is_busy_with_another_table(self):
        self.assertEqual(
            views.queue_status_line(1, 5),
            "The Game Master is busy with another table: a turn started before "
            "yours is still running (5s so far)",
        )

    def test_the_count_is_stated_as_what_is_known_not_a_place_in_line(self):
        """Review of #64: the count is per turn, Ollama's line is per call."""
        line = views.queue_status_line(3, 5)

        self.assertIn("3 turns started before yours are still running", line)
        self.assertNotIn("ahead of yours", line)

    def test_the_queued_timeout_names_the_queue_and_does_not_invite_a_resend(self):
        clock = FakeMonotonicClock()
        queue = queue_with(clock, 1)
        queue.call_started()
        clock.advance(300)
        queue.look()

        message = views.too_slow_message(queue, 300)

        self.assertIn("busy with other tables", message)
        self.assertIn("300 of its 300 seconds", message)
        self.assertNotIn("cold", message)
        self.assertNotIn("normally much faster", message)

    def test_a_fight_cut_at_its_deadline_does_not_invite_a_resend_into_the_line(
        self,
    ):
        message = settings.GM_FIGHT_TOO_SLOW_MESSAGE.format()

        self.assertNotIn("again in a moment", message)
        self.assertIn("busy with other tables", message)
        self.assertIn("give it a minute", message)


class NarrationStallsAfterItsFirstWords:
    """A model that plans at once, then falls silent mid-narration."""

    name = "fake-model"
    options: dict = {}

    def __init__(self) -> None:
        self.calls = 0

    def stream(self, messages, *, stop_at=None) -> Iterator[str]:
        self.calls += 1
        if self.calls == 1:
            return iter(['{"actions": []}'])
        return self._narration()

    def _narration(self) -> Iterator[str]:
        yield "The door "
        time.sleep(0.3)
        yield "opens."


class EngineWaitsInLineTest(SimpleTestCase):
    """The engine reports the line while a call waits, and only then."""

    def test_a_silent_call_with_turns_ahead_says_how_many(self):
        model = FakeModel([Paused(0.3, '{"actions": []}'), "Fine."])
        queue = ModelQueueWait(turns_ahead=lambda: 2)

        yielded, _outcome = run_turn(
            engine_for(model, keepalive_seconds=0.05, queue=queue), request()
        )

        waiting = [event for event in yielded if event.kind == events.WAITING]
        self.assertTrue(waiting)
        self.assertTrue(
            all(event.payload[TURNS_AHEAD_KEY] == 2 for event in waiting),
            waiting,
        )
        self.assertGreater(queue.waited(), 0)

    def test_a_silence_after_the_first_token_is_not_the_line(self):
        queue = ModelQueueWait(turns_ahead=lambda: 2)
        engine = engine_for(
            NarrationStallsAfterItsFirstWords(), keepalive_seconds=0.05, queue=queue
        )

        yielded, outcome = run_turn(engine, request())

        waiting = [event for event in yielded if event.kind == events.WAITING]
        self.assertTrue(waiting)
        self.assertFalse(
            [event for event in waiting if TURNS_AHEAD_KEY in event.payload]
        )
        self.assertEqual(queue.waited(), 0)
        self.assertEqual(outcome.narration, "The door opens.")

    def test_queue_time_counts_against_the_budget(self):
        """The budget decision of issue #64: waiting in line is not free.

        A turn held behind other turns past its budget ends there, as any
        other turn that outruns it does, and its wait is what was counted.
        """
        model = FakeModel([Paused(0.5, "late")])
        queue = ModelQueueWait(turns_ahead=lambda: 1)
        engine = engine_for(
            model, budget_seconds=0.15, keepalive_seconds=0.02, queue=queue
        )

        with self.assertRaises(TurnBudgetExceeded):
            run_turn(engine, request())

        self.assertTrue(queue.still_waiting)
        self.assertGreater(queue.waited(), 0)


class LineThatNeverMoves:
    """A model call held in Ollama's line: nothing comes back for a while.

    The fake clock is the turn's, so the budget runs out after the steps the
    test chose, however loaded the machine is; the real pause in each step
    is what lets the engine's keep-alive fall silent and look at the line.
    """

    STEP_SECONDS = 40.0
    STEPS = 5
    REAL_PAUSE_SECONDS = 0.1

    def __init__(self, clock: FakeMonotonicClock) -> None:
        self.clock = clock

    def __call__(self, *args: object, **kwargs: object) -> Iterator[str]:
        return self._tokens()

    def _tokens(self) -> Iterator[str]:
        for _step in range(self.STEPS):
            self.clock.advance(self.STEP_SECONDS)
            time.sleep(self.REAL_PAUSE_SECONDS)
        yield "too late"


@override_settings(
    GM_STREAM_BUDGET_SECONDS=100,
    GM_QUEUE_STATUS_ONE_AHEAD=ONE_AHEAD,
    GM_QUEUE_STATUS_MANY_AHEAD=MANY_AHEAD,
    GM_STREAM_TOO_SLOW_MESSAGE=COLD,
    GM_STREAM_QUEUED_TOO_LONG_MESSAGE=QUEUED,
)
class StreamWhileWaitingInLineTest(BaseTestCase):
    """The stream a player reads while the model's line holds their turn."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("waiting")
        self.login_as(self.player)

    def post(self) -> list[dict]:
        clock = FakeMonotonicClock()
        view_time = Mock(wraps=time, monotonic=clock)
        with (
            patch(
                "gm.turn_engine.model.ollama_client.stream_chat",
                LineThatNeverMoves(clock),
            ),
            patch("gm.views.time", view_time),
            patch("gm.turn_engine.engine.time", view_time),
            patch("gm.views.GM_STREAM_KEEPALIVE_SECONDS", 0.02),
            self.assertLogs("gm.views", level="WARNING"),
        ):
            response = self.client.post(
                STREAM_URL,
                json.dumps({"message": "What is behind the door?"}),
                content_type="application/json",
            )
            content = b"".join(response.streaming_content).decode()
        return [
            json.loads(line[len("data: ") :])
            for line in content.splitlines()
            if line.startswith("data: ")
        ]

    def test_behind_another_table_the_player_is_told_so_and_why_it_ended(self):
        GMTurnRecord.objects.create(
            user=self.create_user("other-table"),
            outcome=GMTurnRecord.OUTCOME_RUNNING,
            mode="play",
        )

        frames = self.post()

        busy = [frame for frame in frames if views.BUSY_FRAME_KEY in frame]
        self.assertTrue(busy, frames)
        for frame in busy:
            self.assertEqual(
                frame[views.BUSY_FRAME_KEY], ONE_AHEAD.format(seconds=frame["elapsed"])
            )
        self.assertEqual(
            frames[-1], {"error": "Waited 100 of 100s behind other tables."}
        )
        record = GMTurnRecord.objects.get(user=self.player)
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_TOO_SLOW)
        self.assertEqual(record.error, frames[-1]["error"])

    def test_with_nobody_ahead_a_silence_is_the_ordinary_slow_turn(self):
        frames = self.post()

        self.assertFalse([frame for frame in frames if views.BUSY_FRAME_KEY in frame])
        self.assertEqual(frames[-1], {"error": COLD})


class FoldThatWaitsInLine:
    """``write_fold`` held in the model's line for ``steps`` of the turn's clock.

    It touches no rows, as the real one does not; ``on_return`` runs just
    before it returns.
    """

    REAL_PAUSE_SECONDS = 0.1

    def __init__(
        self,
        clock: FakeMonotonicClock,
        steps: int,
        step_seconds: float,
        on_return=None,
    ) -> None:
        self.clock = clock
        self.steps = steps
        self.step_seconds = step_seconds
        self.on_return = on_return

    def __call__(self, session, history, *, deadline=None, hang_up=None):
        for _step in range(self.steps):
            self.clock.advance(self.step_seconds)
            time.sleep(self.REAL_PAUSE_SECONDS)
        if self.on_return is not None:
            self.on_return()
        return "", session.summary_covers


@override_settings(
    GM_STREAM_BUDGET_SECONDS=100,
    GM_QUEUE_STATUS_ONE_AHEAD=ONE_AHEAD,
    GM_QUEUE_STATUS_MANY_AHEAD=MANY_AHEAD,
    GM_STREAM_TOO_SLOW_MESSAGE=COLD,
    GM_STREAM_QUEUED_TOO_LONG_MESSAGE=QUEUED,
)
class FoldWhileWaitingInLineTest(BaseTestCase):
    """The fold's model call is on the turn's clock, and waits in line too.

    Review of #64: it never looked at the line, so a fold held there past the
    budget was blamed on a cold model, and a fold's wait was left out of the
    count when the turn ran out later, in planning.
    """

    def setUp(self):
        super().setUp()
        self.player = self.create_user("waiting")
        self.login_as(self.player)
        self.session = GMSession.objects.create(
            user=self.player, name="long", mode="play", history=turns(400, words=120)
        )
        GMTurnRecord.objects.create(
            user=self.create_user("other-table"),
            outcome=GMTurnRecord.OUTCOME_RUNNING,
            mode="play",
        )

    def post(self, clock: FakeMonotonicClock, fold, stream_chat) -> list[dict]:
        view_time = Mock(wraps=time, monotonic=clock)
        with (
            patch("gm.views.write_fold", fold),
            patch("gm.turn_engine.model.ollama_client.stream_chat", stream_chat),
            patch("gm.views.time", view_time),
            patch("gm.turn_engine.engine.time", view_time),
            patch("gm.views.GM_STREAM_KEEPALIVE_SECONDS", 0.02),
            self.assertLogs("gm.views", level="WARNING"),
        ):
            response = self.client.post(
                STREAM_URL,
                json.dumps({"session_id": self.session.pk, "message": "We walk"}),
                content_type="application/json",
            )
            content = b"".join(response.streaming_content).decode()
        return [
            json.loads(line[len("data: ") :])
            for line in content.splitlines()
            if line.startswith("data: ")
        ]

    def test_a_fold_held_in_line_past_the_budget_names_the_queue(self):
        clock = FakeMonotonicClock()
        fold = FoldThatWaitsInLine(clock, steps=5, step_seconds=40)

        frames = self.post(clock, fold, LineThatNeverMoves(clock))

        self.assertEqual(frames[0], {"status": views.FOLDING_STATUS})
        busy = [frame for frame in frames if views.BUSY_FRAME_KEY in frame]
        self.assertTrue(busy, frames)
        self.assertEqual(busy[0]["status"], views.FOLDING_STATUS)
        self.assertEqual(
            frames[-1], {"error": "Waited 100 of 100s behind other tables."}
        )

    def test_a_folds_wait_counts_when_the_turn_runs_out_later(self):
        clock = FakeMonotonicClock()
        # 80 of the budget's 100 s in line, then the other table's turn is
        # done, and planning, with nobody ahead, runs the clock out. The
        # line is counted here rather than in the database: the fold runs on
        # its own thread, which the test database will not take a write from.
        line = {"turns_ahead": 1}

        def other_table_done() -> None:
            line["turns_ahead"] = 0

        fold = FoldThatWaitsInLine(
            clock, steps=2, step_seconds=40, on_return=other_table_done
        )

        with patch("gm.views.turns_ahead_of", lambda record_id: line["turns_ahead"]):
            frames = self.post(clock, fold, LineThatNeverMoves(clock))

        self.assertEqual(frames[-1]["error"], "Waited 80 of 100s behind other tables.")

    def test_a_slow_fold_with_nobody_ahead_keeps_the_ordinary_message(self):
        clock = FakeMonotonicClock()
        fold = FoldThatWaitsInLine(clock, steps=5, step_seconds=40)

        with patch("gm.views.turns_ahead_of", lambda record_id: 0):
            frames = self.post(clock, fold, LineThatNeverMoves(clock))

        self.assertFalse([frame for frame in frames if views.BUSY_FRAME_KEY in frame])
        self.assertEqual(frames[-1]["error"], COLD)
