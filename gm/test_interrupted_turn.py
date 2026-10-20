"""A turn that breaks off can be retried without running anything twice.

Issue #410. A procedure changes the draft the moment it runs, but a turn was
stored only when it finished, so a turn that broke off after the years were
played left the draft ahead of the history. Retrying then played the years
again.

Since issue #515 a page that drops the connection no longer breaks the turn:
the server runs it to the end and saves it. These tests hold the page's drop
at each point it can go to that, and exercise #410's retry path with the way a
turn still breaks off on the server -- the model failing part-way.
"""

import json
from unittest.mock import patch

from common.test_helpers import BaseTestCase

from .messages import build_interrupted_turn_message, resumes_interrupted_turn
from .models import GMSession, GMTurnRecord
from .retrieval import INTERRUPTED_KIND, PROCEDURE_KIND
from .testing import gm_replies
from .views import GM_SERVER_ERROR_MESSAGE

STREAM_URL = "/tarmar/gm/stream/"
# A plan whose one action changes a character before any reply is written.
PLAN = json.dumps(
    {"actions": [{"action": "roll_attributes", "new_character": {"name": "Wren"}}]}
)


def _data_frames(chunks):
    """Every SSE data frame in ``chunks``, decoded, as they arrive."""
    for chunk in chunks:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        for line in text.splitlines():
            if line.startswith("data: "):
                yield json.loads(line[len("data: ") :])


class InterruptedTurnTest(BaseTestCase):
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

    def _stored_session(self) -> GMSession:
        session = GMSession.objects.create(user=self.user, name="Keep", mode="play")
        return session

    @staticmethod
    def _spoken(session: GMSession) -> list[str]:
        return [entry["content"] for entry in session.history if not entry.get("kind")]

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_drop_before_anything_streamed_still_saves_the_turn(self, stream):
        stream.side_effect = gm_replies(["Hello."])
        session = self._stored_session()

        response, _chunks = self._open({"session_id": session.pk, "message": "Hi"})
        response.close()

        session.refresh_from_db()
        self.assertEqual(self._spoken(session), ["Hi", "Hello."])

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_drop_after_text_streamed_still_saves_the_reply(self, stream):
        stream.side_effect = gm_replies(["You push ", "the door."])
        session = self._stored_session()

        response, chunks = self._open({"session_id": session.pk, "message": "Push"})
        self._read_until(chunks, "token")
        response.close()

        session.refresh_from_db()
        self.assertEqual(self._spoken(session), ["Push", "You push the door."])

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_drop_after_a_procedure_finishes_the_turn_once(self, stream):
        stream.side_effect = gm_replies("The year is done.", plans=[PLAN])

        response, chunks = self._open({"message": "Roll Fatigue."})
        frame = self._read_until(chunks, "procedure")
        response.close()

        session = GMSession.objects.get(pk=frame["session_id"])
        kinds = [entry.get("kind") for entry in session.history]
        self.assertEqual(
            self._spoken(session),
            ["Roll Fatigue.", "The year is done."],
        )
        self.assertEqual(kinds.count(PROCEDURE_KIND), 1)
        self.assertNotIn(INTERRUPTED_KIND, kinds)

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_model_failure_after_a_procedure_keeps_what_it_ran(self, stream):
        stream.side_effect = gm_replies(RuntimeError("model went away"), plans=[PLAN])

        response, chunks = self._open({"message": "Roll Fatigue."})
        frames = list(_data_frames(chunks))

        self.assertEqual(frames[-1]["error"], GM_SERVER_ERROR_MESSAGE)
        session = GMSession.objects.get()
        self.assertEqual(session.history[-1]["kind"], INTERRUPTED_KIND)
        # The turn's record keeps what already ran (issue #539).
        record = GMTurnRecord.objects.get()
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_ERROR)
        self.assertEqual(len(record.procedures), 1)
        self.assertEqual(len(record.ledger_applied), 1)
        self.assertEqual(record.history_end, len(session.history))

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_procedure_that_makes_the_session_brings_its_rail_entries(self, stream):
        """A turn that breaks after the first frame has still made the session (#62).

        The procedure's frame is the first to name it, so the rail's row and
        the menu's option ride it: the page needs no later frame to show the
        session the turn made.
        """
        stream.side_effect = gm_replies(RuntimeError("model went away"), plans=[PLAN])

        _response, chunks = self._open({"message": "Roll Fatigue."})
        frames = list(_data_frames(chunks))

        # The failure comes after the procedure's frame, and names no session
        # of its own: the page already holds the entries from the one before.
        naming = [frame for frame in frames if "session_id" in frame]
        self.assertEqual([next(iter(frame)) for frame in naming], ["procedure"])
        self.assertEqual(frames[-1]["error"], GM_SERVER_ERROR_MESSAGE)
        session = GMSession.objects.get()
        for frame in naming:
            self.assertIn(f'data-id="{session.pk}"', frame["session_row_html"])
            self.assertIn(f'value="{session.pk}"', frame["session_menu_option_html"])

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_retry_into_the_session_sends_no_entries(self, stream):
        stream.side_effect = gm_replies(
            RuntimeError("model went away"), "You came through the year.", plans=[PLAN]
        )
        _response, chunks = self._open({"message": "Roll Fatigue."})
        session_id = self._read_until(chunks, "procedure")["session_id"]
        list(chunks)

        _retry, retry_chunks = self._open(
            {"session_id": session_id, "message": "Roll Fatigue.", "retry": True}
        )
        frames = list(_data_frames(retry_chunks))

        self.assertTrue(frames[-1]["done"])
        for frame in frames:
            self.assertNotIn("session_row_html", frame)
            self.assertNotIn("session_menu_option_html", frame)

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_retry_carries_on_from_what_already_ran(self, stream):
        stream.side_effect = gm_replies(
            RuntimeError("model went away"), "You came through the year.", plans=[PLAN]
        )
        response, chunks = self._open({"message": "Roll Fatigue."})
        session_id = self._read_until(chunks, "procedure")["session_id"]
        list(chunks)

        retry, retry_chunks = self._open(
            {"session_id": session_id, "message": "Roll Fatigue.", "retry": True}
        )
        list(retry_chunks)

        self.assertEqual(
            stream.call_count, 3, "the retry must not start a new procedure"
        )
        session = GMSession.objects.get(pk=session_id)
        self.assertEqual(
            self._spoken(session), ["Roll Fatigue.", "You came through the year."]
        )
        kinds = [entry.get("kind") for entry in session.history]
        self.assertEqual(kinds.count(PROCEDURE_KIND), 1)
        (_model, messages), _options = stream.call_args_list[2]
        self.assertIn(
            "Already done when this message was first sent: Wren",
            messages[-1]["content"],
        )
        self.assertIn("Narrate this turn from the results", messages[-1]["content"])

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_retry_runs_no_plan_its_narration_carries(self, stream):
        """The resumed turn's actions stood; a leaked plan is not a second go.

        Issue #782: the narrate message of a resumed turn repeats the original
        request, and a narration that answered it with a plan ran that plan,
        so Wren was started (or her years played) a second time.
        """
        leaked = "Wren again. " + PLAN
        stream.side_effect = gm_replies(
            RuntimeError("model went away"), leaked, plans=[PLAN]
        )
        response, chunks = self._open({"message": "Make Wren."})
        session_id = self._read_until(chunks, "procedure")["session_id"]
        list(chunks)

        retry, retry_chunks = self._open(
            {"session_id": session_id, "message": "Make Wren.", "retry": True}
        )
        frames = list(_data_frames(retry_chunks))

        self.assertEqual(stream.call_count, 3, "one narration, no follow-up")
        self.assertFalse([frame for frame in frames if "procedure" in frame])
        session = GMSession.objects.get(pk=session_id)
        self.assertEqual(
            [character.name for character in session.attached_characters()],
            ["Wren"],
        )
        kinds = [entry.get("kind") for entry in session.history]
        self.assertEqual(kinds.count(PROCEDURE_KIND), 1)

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_retry_of_a_different_message_is_an_ordinary_turn(self, stream):
        stream.side_effect = gm_replies(
            RuntimeError("model went away"), "The road is quiet.", plans=[PLAN]
        )
        response, chunks = self._open({"message": "Roll Fatigue."})
        session_id = self._read_until(chunks, "procedure")["session_id"]
        list(chunks)

        retry, retry_chunks = self._open(
            {"session_id": session_id, "message": "Where am I?", "retry": True}
        )
        list(retry_chunks)

        session = GMSession.objects.get(pk=session_id)
        self.assertEqual(
            self._spoken(session),
            ["Roll Fatigue.", "Where am I?", "The road is quiet."],
        )


class ResumesInterruptedTurnTest(BaseTestCase):
    def test_only_the_same_message_after_the_note_resumes(self):
        history = [
            {"role": "user", "content": "Roll Fatigue."},
            {"role": "user", "content": "[procedure]", "kind": PROCEDURE_KIND},
            build_interrupted_turn_message(),
        ]

        self.assertTrue(resumes_interrupted_turn(history, "Roll Fatigue."))
        self.assertFalse(resumes_interrupted_turn(history, "Something else."))
        self.assertFalse(resumes_interrupted_turn(history[:-1], "Roll Fatigue."))
        self.assertFalse(resumes_interrupted_turn([], "Roll Fatigue."))
