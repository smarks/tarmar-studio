"""A started GM turn is not lost because the page went away (issue #515).

Production lost two turns from an iPhone whose page dropped the stream after
11 s and 27 s: nothing reached the session. A turn now runs to the end on the
server whether or not the page is still listening, is saved exactly as a
finished turn is, and a page that comes back finds it -- or finds it still
running, and cannot send into the session until it lands.
"""

import json
import time
from datetime import timedelta
from unittest.mock import Mock, patch

from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from common.test_helpers import BaseTestCase

from . import views
from .models import PLACEHOLDER_SESSION_NAME, GMSession, GMTurnRecord
from .retrieval import INTERRUPTED_KIND, PROCEDURE_KIND
from .testing import gm_replies
from .tests import FakeMonotonicClock, SlowFakeStream
from .turn_record import (
    RUNNING_TURN_GRACE_SECONDS,
    STALE_RUNNING_TURN_ERROR,
    TurnRecorder,
    claim_turn,
    expire_stale_running_turns,
)

STREAM_URL = "/tarmar/gm/stream/"
# A plan whose one action changes a character, so the turn has a chip to keep.
PLAN = json.dumps(
    {"actions": [{"action": "roll_attributes", "new_character": {"name": "Wren"}}]}
)
REPLY = "You came through the year."


def _data_frames(chunks):
    for chunk in chunks:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        for line in text.splitlines():
            if line.startswith("data: "):
                yield json.loads(line[len("data: ") :])


class TurnSurvivesDisconnectTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def _open(self, body: dict):
        response = self.client.post(
            STREAM_URL, json.dumps(body), content_type="application/json"
        )
        return response, iter(response.streaming_content)

    def _read_until(self, chunks, key: str) -> dict:
        for frame in _data_frames(chunks):
            if key in frame:
                return frame
        self.fail(f"the stream ended without a {key!r} frame")

    @staticmethod
    def _spoken(session: GMSession) -> list[str]:
        return [entry["content"] for entry in session.history if not entry.get("kind")]

    @staticmethod
    def _kinds(session: GMSession) -> list[str | None]:
        return [entry.get("kind") for entry in session.history]

    def _drop_after_procedure(self, stream) -> int:
        """Play a turn whose page leaves right after its procedure ran."""
        stream.side_effect = gm_replies(REPLY, plans=[PLAN])
        response, chunks = self._open({"message": "Roll Fatigue."})
        frame = self._read_until(chunks, "procedure")
        response.close()
        return frame["session_id"]

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_turn_whose_page_leaves_still_saves_its_reply_and_procedures(
        self, stream
    ):
        session_id = self._drop_after_procedure(stream)

        session = GMSession.objects.get(pk=session_id)
        self.assertEqual(self._spoken(session), ["Roll Fatigue.", REPLY])
        self.assertEqual(self._kinds(session).count(PROCEDURE_KIND), 1)
        self.assertNotIn(INTERRUPTED_KIND, self._kinds(session))
        record = GMTurnRecord.objects.get()
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_OK)
        self.assertEqual(record.reply, REPLY)
        self.assertEqual(len(record.procedures), 1)
        self.assertEqual(record.history_end, len(session.history))

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_turn_whose_page_leaves_before_anything_streamed_is_saved(self, stream):
        stream.side_effect = gm_replies(["The gate ", "is shut."])

        response, _chunks = self._open({"message": "I knock"})
        response.close()

        session = GMSession.objects.get()
        self.assertEqual(self._spoken(session), ["I knock", "The gate is shut."])

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_the_running_turn_names_the_session_a_procedure_created(self, stream):
        stream.side_effect = gm_replies(REPLY, plans=[PLAN])
        response, chunks = self._open({"message": "Roll Fatigue."})
        frame = self._read_until(chunks, "procedure")

        record = GMTurnRecord.objects.get()
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_RUNNING)
        self.assertEqual(record.session_id, frame["session_id"])
        page = self.client.get(f"/tarmar/gm/?session={frame['session_id']}")
        self.assertEqual(
            page.context["chat_config"]["runningTurn"],
            {"id": record.pk, "message": "Roll Fatigue."},
        )
        response.close()

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_coming_back_does_not_run_the_procedures_again(self, stream):
        session_id = self._drop_after_procedure(stream)

        page = self.client.get(f"/tarmar/gm/?session={session_id}")
        self.assertIsNone(page.context["chat_config"]["runningTurn"])
        self.assertContains(page, REPLY)
        record = GMTurnRecord.objects.get()
        state = self.client.get(f"/tarmar/gm/turns/{record.pk}/").json()
        self.assertEqual(
            state, {"state": "finished", "session_id": session_id, "error": ""}
        )

        # A stale page's Retry of the same message is refused, not replayed.
        retry = self.client.post(
            STREAM_URL,
            json.dumps(
                {"session_id": session_id, "message": "Roll Fatigue.", "retry": True}
            ),
            content_type="application/json",
        )
        self.assertEqual(retry.status_code, 400)
        self.assertEqual(retry.json()["error"], views.TURN_ALREADY_ANSWERED_ERROR)
        self.assertEqual(stream.call_count, 2)
        session = GMSession.objects.get(pk=session_id)
        self.assertEqual(self._kinds(session).count(PROCEDURE_KIND), 1)
        self.assertEqual(self._spoken(session), ["Roll Fatigue.", REPLY])

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_second_message_while_a_turn_runs_is_refused(self, stream):
        session = GMSession.objects.create(user=self.user, name="Keep", mode="play")
        stream.side_effect = gm_replies(["You push ", "the door."], "Never.")
        first, chunks = self._open({"session_id": session.pk, "message": "Push"})
        self._read_until(chunks, "token")
        running = GMTurnRecord.objects.get(outcome=GMTurnRecord.OUTCOME_RUNNING)

        second = self.client.post(
            STREAM_URL,
            json.dumps({"session_id": session.pk, "message": "Pull"}),
            content_type="application/json",
        )

        self.assertEqual(second.status_code, 409)
        self.assertEqual(
            second.json(),
            {"error": views.TURN_ALREADY_RUNNING_ERROR, "running_turn": running.pk},
        )
        refused = GMTurnRecord.objects.get(user_message="Pull")
        self.assertEqual(refused.outcome, GMTurnRecord.OUTCOME_REJECTED)
        list(chunks)
        first.close()
        session.refresh_from_db()
        self.assertEqual(self._spoken(session), ["Push", "You push the door."])
        # One turn: its plan and its narration.
        self.assertEqual(stream.call_count, 2)

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_new_session_turn_is_never_refused(self, stream):
        stream.side_effect = gm_replies("One.", "Two.")
        first, chunks = self._open({"message": "Begin"})
        self._read_until(chunks, "token")

        second, second_chunks = self._open({"message": "Begin again"})
        list(second_chunks)
        list(chunks)
        first.close()

        self.assertEqual(GMSession.objects.count(), 2)

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_turn_left_running_by_a_dead_worker_does_not_block_the_session(
        self, stream
    ):
        session = GMSession.objects.create(user=self.user, name="Keep", mode="play")
        stale = GMTurnRecord.objects.create(
            session=session,
            user=self.user,
            outcome=GMTurnRecord.OUTCOME_RUNNING,
            mode="play",
            user_message="Lost",
            started_at=timezone.now() - timedelta(hours=1),
        )
        stream.side_effect = gm_replies(["Welcome back."])

        response, chunks = self._open({"session_id": session.pk, "message": "Hello"})
        list(chunks)

        stale.refresh_from_db()
        self.assertEqual(stale.outcome, GMTurnRecord.OUTCOME_ERROR)
        self.assertEqual(stale.error, STALE_RUNNING_TURN_ERROR)
        state = self.client.get(f"/tarmar/gm/turns/{stale.pk}/").json()
        self.assertEqual(state["state"], "failed")
        session.refresh_from_db()
        self.assertEqual(self._spoken(session), ["Hello", "Welcome back."])

    @override_settings(GM_RECAP_BUDGET_SECONDS=100, GM_STREAM_BUDGET_SECONDS=200)
    def test_a_turn_still_writing_its_recap_is_not_taken_for_dead(self):
        """Issue #936: a continued session's first turn spends both budgets.

        Its recap runs before its own clock starts, so a turn is taken to have
        died only once both, and the grace, have passed.
        """
        session = GMSession.objects.create(user=self.user, name="Keep", mode="play")
        limit = 100 + 200 + RUNNING_TURN_GRACE_SECONDS

        def running_since(seconds: float) -> GMTurnRecord:
            return GMTurnRecord.objects.create(
                session=session,
                user=self.user,
                outcome=GMTurnRecord.OUTCOME_RUNNING,
                mode="play",
                user_message="Still here",
                started_at=timezone.now() - timedelta(seconds=seconds),
            )

        alive = running_since(limit - 30)
        dead = running_since(limit + 30)

        expire_stale_running_turns()

        alive.refresh_from_db()
        dead.refresh_from_db()
        self.assertEqual(alive.outcome, GMTurnRecord.OUTCOME_RUNNING)
        self.assertEqual(dead.outcome, GMTurnRecord.OUTCOME_ERROR)

    def test_a_turn_can_only_be_asked_after_by_its_owner(self):
        stranger = self.create_user(username="stranger")
        record = GMTurnRecord.objects.create(
            user=stranger, outcome=GMTurnRecord.OUTCOME_RUNNING, mode="play"
        )

        response = self.client.get(f"/tarmar/gm/turns/{record.pk}/")

        self.assertEqual(response.status_code, 404)

    def test_claiming_a_turn_twice_for_one_session_refuses_the_second(self):
        session = GMSession.objects.create(user=self.user, name="Keep", mode="play")

        def recorder() -> TurnRecorder:
            return TurnRecorder(
                user=self.user,
                session_id=session.pk,
                command="",
                mode="play",
                is_game_master=False,
                user_message="Hi",
                history_start=0,
                system_prompt="prompt",
                model="fake",
                options={},
            )

        first = recorder()
        self.assertIsNone(claim_turn(first))
        running = claim_turn(recorder())
        self.assertIsNotNone(running)
        assert running is not None
        self.assertEqual(running.pk, first.record_id)


@override_settings(GM_STREAM_BUDGET_SECONDS=0.05)
class DisconnectedTurnBudgetTest(BaseTestCase):
    """A turn finishing without its page still ends at the budget (issue #515)."""

    def setUp(self):
        super().setUp()
        self.login_as(self.create_user())

    def test_an_overrun_after_the_page_left_saves_nothing_truncated(self):
        clock = FakeMonotonicClock()
        slow = SlowFakeStream(
            before=["The dust ", "settles"],
            stall_seconds=0.4,
            # The reader asks for one token past the stall before the budget
            # check sees it, as in GMStreamBudgetTest; the one after that must
            # never be generated.
            after=["a-late-token", "a-later-token"],
            clock=clock,
        )
        view_time = Mock(wraps=time, monotonic=clock)
        with (
            patch("gm.turn_engine.model.ollama_client.stream_chat", slow),
            patch("gm.views.time", view_time),
            patch("gm.turn_engine.engine.time", view_time),
            self.assertLogs("gm.views", level="WARNING") as logs,
        ):
            response = self.client.post(
                STREAM_URL,
                json.dumps({"message": "What is behind the door?"}),
                content_type="application/json",
            )
            chunks = iter(response.streaming_content)
            first = next(_data_frames(chunks))
            response.close()

        # The stall comes while the turn plans, so nothing was written yet.
        self.assertEqual(first, {"status": "planning"})
        self.assertIn("passed its 0.05s budget", "\n".join(logs.output))
        self.assertNotIn("a-later-token", slow.emitted)
        self.assertTrue(slow.closed)
        self.assertEqual(GMSession.objects.count(), 0)
        record = GMTurnRecord.objects.get()
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_TOO_SLOW)
        self.assertEqual(record.error, settings.GM_STREAM_TOO_SLOW_MESSAGE)


class BlankPageDoesNotAdoptAnotherTabsTurnTest(BaseTestCase):
    """A session-less turn belongs to the tab that sent it (issue #650).

    A turn that is the first message of a new session keeps ``session_id``
    NULL for the whole of its run, so a lookup for "this player's running
    session-less turn" matches it by nothing that ties it to a page. A second
    tab opened to start a different game was handed the first tab's turn: it
    set ``isStreaming``, disabled Send, and on completion navigated into the
    other tab's session. The two states disagreed as well -- ``claim_turn``
    never refuses a session-less turn, so the server would have allowed the
    second new game the page would not offer.
    """

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def running_turn(self, session_id: int | None) -> GMTurnRecord:
        """A turn recorded as running, with or without a session."""
        recorder = TurnRecorder(
            user=self.user,
            session_id=session_id,
            command="",
            mode="play",
            is_game_master=False,
            user_message="I open the iron box",
            history_start=0,
            system_prompt="the rules",
            model="test",
            options={},
        )
        recorder.start()
        return GMTurnRecord.objects.get(pk=recorder.record_id)

    def test_a_blank_page_is_offered_no_running_turn(self):
        self.running_turn(None)

        page = self.client.get("/tarmar/gm/")

        self.assertIsNone(page.context["chat_config"]["runningTurn"])
        self.assertIsNone(page.context["chat_config"]["sessionId"])

    def test_the_page_may_still_start_a_new_game(self):
        """The page now offers what the server would already have allowed."""
        record = self.running_turn(None)

        second = claim_turn(
            TurnRecorder(
                user=self.user,
                session_id=None,
                command="",
                mode="play",
                is_game_master=False,
                user_message="I start a different game",
                history_start=0,
                system_prompt="the rules",
                model="test",
                options={},
            )
        )

        self.assertIsNone(second)
        self.assertEqual(GMTurnRecord.objects.filter(session__isnull=True).count(), 2)
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_RUNNING)

    def test_a_session_page_still_waits_for_its_own_running_turn(self):
        """What issue #515 built this for is untouched."""
        session = GMSession.objects.create(user=self.user, name="s")
        record = self.running_turn(session.pk)

        page = self.client.get(f"/tarmar/gm/?session={session.pk}")

        self.assertEqual(
            page.context["chat_config"]["runningTurn"],
            {"id": record.pk, "message": "I open the iron box"},
        )

    def test_the_page_can_still_ask_after_a_session_less_turn_by_id(self):
        """How the tab that started it picks it up again."""
        record = self.running_turn(None)

        state = self.client.get(f"/tarmar/gm/turns/{record.pk}/").json()

        self.assertEqual(state["state"], "running")
        self.assertIsNone(state["session_id"])


class ReloadAsTheTurnLandsTest(BaseTestCase):
    """A reload that lands as the turn saves shows it, one way or the other.

    Issue #880: the page read the session's history and then asked whether a
    turn was running. A turn saves its history and then finishes its record,
    so one that did both between those two reads left the page with the
    history from before the save and no running turn to wait for: the turn's
    dice chips and reply never appeared until another reload.

    Issue #910: #880's fix moved the history read after the question, but the
    session row -- its history among it -- was still fetched before asking.
    A turn that landed between that fetch and the question left the page
    with the session as it was before the save, still named "New Session",
    and nothing to wait for.
    """

    # The running-turn helper of the class above, not its tests.
    running_turn = BlankPageDoesNotAdoptAnotherTabsTurnTest.running_turn

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def test_a_turn_that_lands_between_the_reads_is_waited_for(self):
        session = GMSession.objects.create(user=self.user, name="s")
        session.history = [{"role": "user", "content": "I open the iron box"}]
        session.save()
        record = self.running_turn(session.pk)
        real_history = views._visible_history

        def the_turn_lands_meanwhile(read_session):
            seen = real_history(read_session)
            # What the stream does next: save the reply, then finish.
            landed = GMSession.objects.get(pk=read_session.pk)
            landed.history = [*landed.history, {"role": "assistant", "content": REPLY}]
            landed.save()
            GMTurnRecord.objects.filter(pk=record.pk).update(
                outcome=GMTurnRecord.OUTCOME_OK
            )
            return seen

        with patch.object(views, "_visible_history", the_turn_lands_meanwhile):
            page = self.client.get(f"/tarmar/gm/?session={session.pk}")

        # The page read the history before the save, so it must wait for the
        # turn; it is told the turn was running when it looked.
        self.assertEqual(
            page.context["chat_config"]["runningTurn"],
            {"id": record.pk, "message": "I open the iron box"},
        )
        self.assertNotContains(page, REPLY)

    def test_a_turn_that_landed_before_the_reads_is_in_the_history(self):
        session = GMSession.objects.create(user=self.user, name="s")
        session.history = [
            {"role": "user", "content": "I open the iron box"},
            {"role": "assistant", "content": REPLY},
        ]
        session.save()
        record = self.running_turn(session.pk)
        GMTurnRecord.objects.filter(pk=record.pk).update(
            outcome=GMTurnRecord.OUTCOME_OK
        )

        page = self.client.get(f"/tarmar/gm/?session={session.pk}")

        self.assertIsNone(page.context["chat_config"]["runningTurn"])
        self.assertContains(page, REPLY)

    def test_a_turn_that_lands_as_the_page_asks_is_in_the_history(self):
        """The page fetched the session, then the turn landed, then it asked.

        Issue #910: the row was read before the question, so the page showed
        the session from before the save and had no running turn to wait for.
        """
        session = GMSession.objects.create(
            user=self.user, name=PLACEHOLDER_SESSION_NAME
        )
        record = self.running_turn(session.pk)
        real_running_turn_for = views.running_turn_for

        def the_turn_lands_first(user, session_id: int) -> GMTurnRecord | None:
            # What the stream does at the end: save the exchange, named after
            # its opening line, then finish the record.
            landed = GMSession.objects.get(pk=session_id)
            landed.history = [
                {"role": "user", "content": "I open the iron box"},
                {"role": "assistant", "content": REPLY},
            ]
            landed.name = "I open the iron box"
            landed.save()
            GMTurnRecord.objects.filter(pk=record.pk).update(
                outcome=GMTurnRecord.OUTCOME_OK
            )
            return real_running_turn_for(user, session_id)

        with patch.object(views, "running_turn_for", the_turn_lands_first):
            page = self.client.get(f"/tarmar/gm/?session={session.pk}")

        # Nothing is running when the page asks, so the session it shows must
        # be the saved one: the reply, under the name the save gave it.
        self.assertIsNone(page.context["chat_config"]["runningTurn"])
        self.assertContains(page, REPLY)
        self.assertEqual(page.context["active_session"].name, "I open the iron box")
