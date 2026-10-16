"""Folding a long session's older turns into a recap (issue #579).

The Game Master's session 36 reached 49,300 tokens of a 65,536 window with
all 260 messages still being sent: the context guard drops canon excerpts and
never a player or GM turn, so once the excerpts were gone it would have
handed the model server a conversation larger than the window and let it
truncate whatever it liked, including the system prompt.
"""

import threading
import time
from unittest.mock import Mock, patch

import httpx
from django.conf import settings
from django.test import SimpleTestCase, override_settings

from common.services import ollama_client
from common.services.ollama_client import HangUp, StreamHungUp
from common.test_helpers import (
    ACT_ONCE_SET_SECONDS,
    STAND_IN_HOLD_SECONDS,
    BaseTestCase,
    HandFiredDeadlines,
    StallingOllama,
    act_once_set,
)
from gm import summary as folding
from gm import views
from gm.engine import section_plan_schema, system_prompt_for
from gm.models import GMEvent, GMSession, GMTurnRecord
from gm.retrieval import CANON_KIND, RECAP_KIND
from gm.services import RecapOutOfTime, save_fold, write_fold, write_session_recap
from gm.test_plan_turn_view import PlanTurnTestCase
from gm.turn_engine import events as turn_events

# conftest replaces ollama_client.open_stream for every test; the stalled-stream
# tests need a real socket (issue #887), so they take the real one, captured
# at import.
REAL_OPEN_STREAM = ollama_client.open_stream


def turns(count: int, *, words: int = 30) -> list[dict]:
    """A stored conversation: alternating turns, and no system message.

    The shape ``gm.views._persist_turns`` saves. It strips every system entry
    before writing, so a ``GMSession.history`` cannot hold one -- which is
    why this helper does not produce one. It used to, and every fold test
    then measured a history that does not occur: the one case they all agreed
    was safe, the recap landing at index 0 with no system message above it,
    was the case production was actually in (issue #631).
    """
    body = " ".join(["word"] * words)
    return [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"{index} {body}",
        }
        for index in range(count)
    ]


def turns_led_by_a_system_message(count: int, *, words: int = 30) -> list[dict]:
    """``turns`` with a system message in front, as an offline caller builds it."""
    return [{"role": "system", "content": "The rules."}, *turns(count, words=words)]


class FoldPointTest(SimpleTestCase):
    def test_a_short_session_is_never_folded(self):
        self.assertIsNone(folding.fold_point(turns(6)))

    def test_the_recent_turns_are_kept_whole(self):
        history = turns(200)

        cut = folding.fold_point(history)

        self.assertIsNotNone(cut)
        assert cut is not None
        self.assertEqual(len(history) - cut, folding.KEEP_RECENT_ENTRIES)

    def test_a_leading_system_message_is_never_folded(self):
        history = turns_led_by_a_system_message(200)

        folded = folding.folded_history(
            history,
            summary="what happened",
            folded_through=folding.fold_point(history) or 0,
        )

        self.assertEqual(folded[0], history[0])
        self.assertIn("what happened", folded[1]["content"])

    def test_a_stored_history_folds_to_a_recap_that_is_not_a_system_message(self):
        """What production folds: the recap must not pass for the GM's prompt.

        ``gm_stream`` puts the system prompt in front of whatever it is
        given, and decides whether it is needed by looking at the first
        entry. A recap wearing the ``system`` role told it the prompt was
        already there (issue #631).
        """
        history = turns(200)

        folded = folding.folded_history(
            history,
            summary="what happened",
            folded_through=folding.fold_point(history) or 0,
        )

        self.assertNotEqual(folded[0]["role"], "system")
        self.assertEqual(folded[0]["kind"], RECAP_KIND)
        self.assertIn("what happened", folded[0]["content"])
        self.assertFalse(any(entry.get("role") == "system" for entry in folded))

    def test_folding_shortens_the_conversation(self):
        history = turns(200)
        cut = folding.fold_point(history) or 0

        folded = folding.folded_history(history, summary="a recap", folded_through=cut)

        self.assertLess(len(folded), len(history))
        self.assertEqual(len(folded), 1 + folding.KEEP_RECENT_ENTRIES)
        self.assertLess(folding.history_tokens(folded), folding.history_tokens(history))

    def test_the_last_turn_survives_folding(self):
        """Whatever else goes, the message being answered stays."""
        history = turns(200)

        folded = folding.folded_history(
            history, summary="a recap", folded_through=folding.fold_point(history) or 0
        )

        self.assertEqual(folded[-1], history[-1])

    def test_a_session_with_no_recap_is_unchanged(self):
        history = turns(200)

        self.assertEqual(
            folding.folded_history(history, summary="", folded_through=0), history
        )


class WhenToFoldTest(SimpleTestCase):
    def test_a_small_session_is_left_alone(self):
        self.assertFalse(folding.should_fold(turns(10), num_ctx=65536))

    def test_a_session_past_the_share_of_the_window_is_folded(self):
        history = turns(400, words=120)

        self.assertGreater(
            folding.history_tokens(history),
            folding.usable_budget(65536) * folding.FOLD_AT_FRACTION,
        )
        self.assertTrue(folding.should_fold(history, num_ctx=65536))

    def test_folding_happens_before_the_guard_runs_out_of_room(self):
        """The recap is not what rescues a turn already at the edge."""
        history = turns(400, words=120)

        self.assertLess(
            folding.history_tokens(history)
            if folding.should_fold(history, num_ctx=65536)
            else 0,
            folding.usable_budget(65536),
        )


class WritingTheRecapTest(SimpleTestCase):
    def test_the_model_is_asked_for_story_and_told_to_leave_figures_out(self):
        asked: list[str] = []

        folding.fold(
            turns(200),
            previous_summary="",
            already_folded=0,
            ask_model=lambda prompt: asked.append(prompt) or "They crossed the moor.",
        )

        self.assertIn("Story only", asked[0])
        self.assertIn("held as records", asked[0])

    def test_the_recap_and_its_reach_come_back(self):
        history = turns(200)

        written, covered = folding.fold(
            history,
            previous_summary="",
            already_folded=0,
            ask_model=lambda _prompt: "They crossed the moor and lost the horse.",
        )

        self.assertEqual(written, "They crossed the moor and lost the horse.")
        self.assertEqual(covered, folding.fold_point(history))

    def test_the_earlier_recap_is_folded_into_the_new_one(self):
        asked: list[str] = []

        folding.fold(
            turns(300),
            previous_summary="They left Farport.",
            already_folded=20,
            ask_model=lambda prompt: asked.append(prompt) or "A longer story.",
        )

        self.assertIn("They left Farport.", asked[0])

    def test_a_failed_recap_leaves_the_session_as_it_was(self):
        """Losing the past is worse than sending a long conversation."""

        def refuse(_prompt: str) -> str:
            raise RuntimeError("the model is down")

        written, covered = folding.fold(
            turns(200),
            previous_summary="what came before",
            already_folded=8,
            ask_model=refuse,
        )

        self.assertEqual(written, "what came before")
        self.assertEqual(covered, 8)

    def test_an_empty_recap_is_refused(self):
        written, covered = folding.fold(
            turns(200),
            previous_summary="before",
            already_folded=8,
            ask_model=lambda _p: "   ",
        )

        self.assertEqual((written, covered), ("before", 8))

    def test_a_recap_is_capped(self):
        written, _covered = folding.fold(
            turns(200),
            previous_summary="",
            already_folded=0,
            ask_model=lambda _p: "x" * 10_000,
        )

        self.assertEqual(len(written), folding.MAX_SUMMARY_CHARS)

    def test_nothing_to_fold_changes_nothing(self):
        written, covered = folding.fold(
            turns(6),
            previous_summary="before",
            already_folded=0,
            ask_model=lambda _p: "new",
        )

        self.assertEqual((written, covered), ("before", 0))

    def test_bookkeeping_entries_are_not_read_to_the_summariser(self):
        """A canon excerpt is not part of the story."""
        history = turns(200)
        history.insert(5, {"role": "user", "content": "AN EXCERPT", "kind": CANON_KIND})
        asked: list[str] = []

        folding.fold(
            history,
            previous_summary="",
            already_folded=0,
            ask_model=lambda prompt: asked.append(prompt) or "recap",
        )

        self.assertNotIn("AN EXCERPT", asked[0])


#: What a fold keeps whole after the entries it covers.
KEEP_AFTER = folding.KEEP_RECENT_ENTRIES


class FoldingASessionTest(PlanTurnTestCase):
    """The wiring: a long session is folded before its turn is planned.

    These drove ``fold_long_session`` directly until the fold moved into the
    turn (issue #783); they now play the turn, as the session does.
    """

    def session_of(self, history: list[dict]) -> GMSession:
        session = GMSession.objects.create(user=self.user, name="long", mode="play")
        session.history = history
        session.save()
        return session

    def turn_on(self, session: GMSession, **recap) -> list[dict]:
        """Play one turn; returns what the plan call sent the model."""
        with patch("gm.services.write_session_recap", **recap):
            _text, sent, calls = self.play(
                '{"actions": []}',
                ["The road goes on."],
                body={"session_id": session.pk, "message": "We walk east"},
            )
        self.assertTrue(sent[-1].get("done"), sent[-1])
        return calls[0]

    def test_a_short_session_is_sent_whole_and_costs_no_call(self):
        session = self.session_of(turns(10))
        asked = Mock()

        sent = self.turn_on(session, new=asked)

        asked.assert_not_called()
        self.assertIn(turns(10)[0]["content"], [message["content"] for message in sent])
        session.refresh_from_db()
        self.assertEqual(session.summary, "")

    def test_a_long_session_is_folded_and_the_recap_kept(self):
        session = self.session_of(turns(400, words=120))

        sent = self.turn_on(session, return_value="They crossed the moor.")

        session.refresh_from_db()
        self.assertEqual(session.summary, "They crossed the moor.")
        self.assertGreater(session.summary_covers, 0)
        contents = [message["content"] for message in sent]
        self.assertNotIn(turns(400, words=120)[0]["content"], contents)
        self.assertIn("They crossed the moor.", contents[1])

    def test_the_stored_history_is_never_shortened(self):
        """Folding changes what the model sees, never what was said."""
        before = turns(400, words=120)
        session = self.session_of(before)

        self.turn_on(session, return_value="a recap")

        session.refresh_from_db()
        self.assertEqual(session.history[: len(before)], before)

    def test_a_second_fold_of_the_same_history_asks_nothing(self):
        """Nothing new past the recap's reach: no second call (unchanged rule).

        Driven through the fold's parts rather than a second turn: a turn
        adds two entries, which moves the fold point and is a new fold.
        """
        session = self.session_of(turns(400, words=120))
        self.turn_on(session, return_value="a recap")
        session.refresh_from_db()
        stored = session.history[: session.summary_covers + KEEP_AFTER]
        again = Mock(return_value="another")

        with patch("gm.services.write_session_recap", new=again):
            written, covered = write_fold(session, stored)

        again.assert_not_called()
        self.assertEqual((written, covered), (session.summary, session.summary_covers))

    def test_a_failed_recap_still_sends_the_turn(self):
        session = self.session_of(turns(400, words=120))

        sent = self.turn_on(session, side_effect=RuntimeError("down"))

        self.assertIn(
            turns(400, words=120)[0]["content"],
            [message["content"] for message in sent],
        )
        session.refresh_from_db()
        self.assertEqual(session.summary, "")

    def test_the_folded_conversation_fits_the_window(self):
        """The point of the exercise."""
        session = self.session_of(turns(400, words=120))

        sent = self.turn_on(session, return_value="a recap")

        self.assertLess(
            folding.history_tokens(sent), folding.usable_budget(settings.GM_NUM_CTX)
        )


class TheSystemPromptSurvivesAFoldTest(PlanTurnTestCase):
    """A folded session still tells the model how to write a plan (issue #631).

    The bug this guards was live in production: because a stored history
    never holds a system message, the recap landed at index 0, ``gm_stream``
    read its ``system`` role as the GM's prompt already being in place, and
    from the first fold onwards every turn of that session went to the model
    with no prompt -- no authority, no mode, and no plan schema, while still
    being asked for "a JSON plan in the format your instructions give".
    """

    def play_a_folded_session(self) -> tuple[GMSession, list[list[dict]]]:
        """Play one ordinary turn on a session long enough to have folded."""
        session = GMSession.objects.create(user=self.user, name="long", mode="play")
        session.history = turns(400, words=120)
        session.save()
        with patch(
            "gm.services.write_session_recap", return_value="They crossed the moor."
        ):
            _text, _sent, calls = self.play(
                '{"actions": []}',
                ["The road goes on."],
                body={"session_id": session.pk, "message": "We walk east"},
            )
        return session, calls

    def test_the_plan_schema_reaches_the_model_after_a_fold(self):
        session, calls = self.play_a_folded_session()
        expected_prompt = system_prompt_for(True, session.mode)

        for name, call in zip(("plan", "narrate"), calls, strict=True):
            with self.subTest(call=name):
                self.assertEqual(call[0]["role"], "system")
                self.assertEqual(call[0]["content"], expected_prompt)
                self.assertIn(section_plan_schema(), call[0]["content"])

    def test_the_recap_follows_the_prompt_and_is_the_only_other_preamble(self):
        session, calls = self.play_a_folded_session()

        plan_call = calls[0]
        self.assertEqual(
            [
                message["content"]
                for message in plan_call
                if message["role"] == "system"
            ],
            [system_prompt_for(True, session.mode)],
        )
        self.assertTrue(plan_call[1]["content"].startswith(folding.SUMMARY_PREFIX))
        self.assertIn("They crossed the moor.", plan_call[1]["content"])

    def test_the_turn_record_stores_the_prompt_and_not_the_recap(self):
        session, _calls = self.play_a_folded_session()

        record = GMTurnRecord.objects.filter(session=session).latest("pk")
        self.assertIsNotNone(record.prompt)
        assert record.prompt is not None
        self.assertEqual(record.prompt.text, system_prompt_for(True, session.mode))

    def test_the_recap_is_never_written_into_the_stored_history(self):
        session, _calls = self.play_a_folded_session()

        session.refresh_from_db()
        self.assertFalse(
            any(entry.get("kind") == RECAP_KIND for entry in session.history)
        )
        self.assertFalse(
            any(entry.get("role") == "system" for entry in session.history)
        )


class TheFoldRunsInsideTheTurnTest(PlanTurnTestCase):
    """A long session folds after its turn is claimed and recorded (#783).

    ``gm_stream`` used to fold as its first act: a full model call made
    before the turn record existed, before the one-turn claim and before
    the stream and its keep-alives began. A second message sent while a
    turn was running paid for a whole recap on the shared model and wrote
    the session's summary from an unlocked copy, then was refused 409; a
    slow fold sent nothing on the connection; and no turn record said the
    fold had happened.
    """

    def long_session(self) -> GMSession:
        session = GMSession.objects.create(user=self.user, name="long", mode="play")
        session.history = turns(400, words=120)
        session.save()
        return session

    def test_a_refused_second_message_folds_nothing(self):
        session = self.long_session()
        running = GMTurnRecord.objects.create(
            user=self.user, session=session, outcome=GMTurnRecord.OUTCOME_RUNNING
        )

        with patch("gm.services.write_session_recap") as recap:
            response = self.client.post(
                "/tarmar/gm/stream/",
                {"session_id": session.pk, "message": "hello"},
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["running_turn"], running.pk)
        recap.assert_not_called()
        session.refresh_from_db()
        self.assertEqual((session.summary, session.summary_covers), ("", 0))

    def test_the_fold_is_a_status_line_and_an_event_of_the_turn(self):
        session = self.long_session()

        with patch("gm.services.write_session_recap", return_value="A recap."):
            _text, sent, _calls = self.play(
                '{"actions": []}',
                ["The road goes on."],
                body={"session_id": session.pk, "message": "We walk east"},
            )

        statuses = [frame["status"] for frame in sent if "status" in frame]
        self.assertEqual(statuses[0], views.FOLDING_STATUS)
        self.assertIn("planning", statuses)
        record = GMTurnRecord.objects.get(session=session)
        fold = GMEvent.objects.get(turn=record, kind=turn_events.FOLD)
        session.refresh_from_db()
        self.assertTrue(fold.payload["kept"])
        self.assertEqual(fold.payload["covered_before"], 0)
        self.assertEqual(fold.payload["covered"], session.summary_covers)
        self.assertEqual(fold.payload["summary_chars"], len("A recap."))
        self.assertEqual(fold.seq, 1, "the fold is the turn's first event")

    def test_the_turn_record_counts_entries_of_the_stored_history(self):
        """So the transcript hands the turn its own entries, fold or no fold."""
        session = self.long_session()
        stored = len(session.history)

        with patch("gm.services.write_session_recap", return_value="A recap."):
            self.play(
                '{"actions": []}',
                ["The road goes on."],
                body={"session_id": session.pk, "message": "We walk east"},
            )

        session.refresh_from_db()
        record = GMTurnRecord.objects.get(session=session)
        self.assertEqual(record.history_start, stored)
        self.assertEqual(record.history_end, len(session.history))
        self.assertEqual(
            [entry["content"] for entry in session.history[record.history_start :]],
            ["We walk east", "The road goes on."],
        )

    def test_a_slow_fold_keeps_the_connection_alive(self):
        session = self.long_session()

        def slow_recap(prompt: str, **_bounds) -> str:
            time.sleep(0.2)
            return "A recap."

        with (
            patch("gm.views.GM_STREAM_KEEPALIVE_SECONDS", 0.05),
            patch("gm.services.write_session_recap", side_effect=slow_recap),
        ):
            text, _sent, _calls = self.play(
                '{"actions": []}',
                ["The road goes on."],
                body={"session_id": session.pk, "message": "We walk east"},
            )

        folding_at = text.index(views.FOLDING_STATUS)
        planning_at = text.index('"planning"')
        self.assertIn(views.SSE_KEEPALIVE, text[folding_at:planning_at])

    def test_a_fold_past_the_budget_ends_the_turn_and_changes_nothing(self):
        session = self.long_session()
        before = list(session.history)

        def slow_recap(prompt: str, **_bounds) -> str:
            time.sleep(0.3)
            return "A recap."

        with (
            override_settings(GM_STREAM_BUDGET_SECONDS=0.1),
            patch("gm.views.GM_STREAM_KEEPALIVE_SECONDS", 0.05),
            patch("gm.services.write_session_recap", side_effect=slow_recap),
        ):
            _text, sent, calls = self.play(
                body={"session_id": session.pk, "message": "We walk east"},
            )

        self.assertEqual(sent[-1]["error"], views.GM_STREAM_TOO_SLOW_MESSAGE)
        self.assertEqual(calls, [], "the turn never reached the model")
        session.refresh_from_db()
        self.assertEqual(session.history, before)
        self.assertEqual(session.summary_covers, 0)
        record = GMTurnRecord.objects.get(session=session)
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_TOO_SLOW)

    def test_a_turn_closed_outright_mid_fold_hangs_the_recap_up_at_once(self):
        # Issue #935: the fold's call had no hang-up, so a turn closed
        # outright (GeneratorExit) left it holding the model until its first
        # token or the turn's deadline. A real socket shows the hang-up.
        session = self.long_session()
        with StallingOllama(["A "], prefill=True) as server:
            started: list = []
            with (
                patch("common.services.ollama_client.open_stream", REAL_OPEN_STREAM),
                patch("gm.views.GM_STREAM_KEEPALIVE_SECONDS", 0.05),
                # The turn's own generator, unwrapped: _RunToCompletion would
                # drain it to the end rather than close it.
                patch(
                    "gm.views._RunToCompletion",
                    side_effect=lambda events: started.append(events) or iter(()),
                ),
                override_settings(OLLAMA_BASE_URL=server.base_url),
            ):
                self.client.post(
                    "/tarmar/gm/stream/",
                    {"session_id": session.pk, "message": "We walk east"},
                    content_type="application/json",
                )
                (turn,) = started
                for frame in turn:
                    if views.FOLDING_STATUS in frame:
                        break
                # The fold is asked for after its status; a keep-alive says
                # the turn is waiting on it.
                self.assertEqual(next(turn), views.SSE_KEEPALIVE)
                self.assertTrue(server.requested.wait(5))
                closing = time.monotonic()
                turn.close()
                close_took = time.monotonic() - closing
                self.assertTrue(server.closed.wait(5))

        # Before #935 the hang-up waited for the headers, which the stand-in
        # holds back until it is hung up on: the server saw no hang-up.
        # Not held until the stand-in gives up on the hang-up.
        self.assertLess(close_took, STAND_IN_HOLD_SECONDS / 2)
        self.assertEqual(server.hang_ups, ["prefill"])

    def test_a_short_session_sends_no_fold_status(self):
        session = GMSession.objects.create(user=self.user, name="short", mode="play")
        session.history = turns(6)
        session.save()

        with patch("gm.services.write_session_recap") as recap:
            _text, sent, _calls = self.play(
                '{"actions": []}',
                ["Fine."],
                body={"session_id": session.pk, "message": "Hello"},
            )

        recap.assert_not_called()
        statuses = [frame["status"] for frame in sent if "status" in frame]
        self.assertNotIn(views.FOLDING_STATUS, statuses)
        self.assertFalse(GMEvent.objects.filter(kind=turn_events.FOLD).exists())


class SavingAFoldTest(BaseTestCase):
    """A fold's recap is written under the session's lock (#783)."""

    def test_a_fold_from_an_older_copy_does_not_replace_a_newer_one(self):
        session = GMSession.objects.create(user=self.create_user(), name="s")
        stale = GMSession.objects.get(pk=session.pk)
        GMSession.objects.filter(pk=session.pk).update(
            summary="The newer recap.", summary_covers=40
        )

        kept = save_fold(stale, "The older recap.", 30)

        self.assertFalse(kept)
        session.refresh_from_db()
        self.assertEqual(
            (session.summary, session.summary_covers), ("The newer recap.", 40)
        )
        self.assertEqual(
            (stale.summary, stale.summary_covers), ("The newer recap.", 40)
        )

    def test_a_fold_that_reaches_further_is_kept(self):
        session = GMSession.objects.create(
            user=self.create_user(), name="s", summary="old", summary_covers=10
        )

        self.assertTrue(save_fold(session, "new", 30))

        session.refresh_from_db()
        self.assertEqual((session.summary, session.summary_covers), ("new", 30))


class ARecapTheTurnGaveUpOnStopsTest(PlanTurnTestCase):
    """The fold's model call ends when its turn stops waiting (review of #783).

    The model server answers one request at a time, so a recap left running
    after its turn ran out of time held the next turn behind it.
    """

    def test_a_deadline_already_past_asks_nothing(self):
        client = Mock()
        with patch("gm.services.ollama_client", client):
            with self.assertRaises(RecapOutOfTime):
                write_session_recap("recap it", deadline=time.monotonic() - 1)

        client.stream_chat.assert_not_called()

    def test_the_read_timeout_is_held_to_the_deadline(self):
        client = Mock()
        client.stream_chat.side_effect = lambda *_args, **_kwargs: (
            token for token in ("A ", "recap.")
        )
        with patch("gm.services.ollama_client", client):
            said = write_session_recap("recap it", deadline=time.monotonic() + 5)

        self.assertEqual(said, "A recap.")
        self.assertLessEqual(client.stream_chat.call_args.kwargs["timeout"].read, 5)

    def test_a_prefill_that_never_answers_is_out_of_time(self):
        def silent(*_args, **_kwargs):
            raise httpx.ReadTimeout("no first token")
            yield ""  # pragma: no cover - a generator that never yields

        client = Mock()
        client.stream_chat.side_effect = silent
        with patch("gm.services.ollama_client", client):
            with self.assertRaises(RecapOutOfTime):
                write_session_recap("recap it", deadline=time.monotonic() + 5)

    def test_a_recap_that_stalls_after_its_first_token_ends_at_the_deadline(self):
        # Issue #887: the read timeout was set once, from the time left at the
        # start, and counts from each read, so a stream that stalled after its
        # first token held the model past the deadline by as long as it ran.
        # The deadline passes when the test says, once the stream has begun,
        # rather than racing the stand-in on a loaded machine (issue #955).
        with (
            StallingOllama(["A "]) as server,
            HandFiredDeadlines() as deadlines,
            patch("common.services.ollama_client.open_stream", REAL_OPEN_STREAM),
            override_settings(OLLAMA_BASE_URL=server.base_url),
        ):
            deadlines.pass_deadline_once(server.responding)
            with self.assertRaises(RecapOutOfTime):
                write_session_recap("recap it", deadline=time.monotonic() + 60)
            self.assertTrue(server.closed.wait(5))

        # Before the fix nothing hung the stalled read up short of its 60 s
        # timeout: the stand-in gave up first and recorded no hang-up.
        self.assertEqual(server.hang_ups, ["stall"])

    def test_a_recap_given_up_on_is_hung_up_at_once(self):
        # Issue #936: the catch-up recap's caller hangs it up when it stops
        # waiting, rather than leaving the call to run to its deadline.
        hang_up = HangUp()
        with StallingOllama(["A "]) as server:
            with (
                patch("common.services.ollama_client.open_stream", REAL_OPEN_STREAM),
                override_settings(OLLAMA_BASE_URL=server.base_url),
            ):
                asking = act_once_set(server.responding, hang_up.hang_up)
                with self.assertRaises(StreamHungUp):
                    write_session_recap(
                        "recap it", deadline=time.monotonic() + 30, hang_up=hang_up
                    )
                asking.join(ACT_ONCE_SET_SECONDS)
                self.assertTrue(server.closed.wait(5))

        self.assertEqual(server.hang_ups, ["stall"])

    def test_a_recap_given_up_on_during_its_prefill_is_hung_up_at_once(self):
        # Issue #935: before its first token, when Ollama sends the headers,
        # the hang-up had no socket and the recap held the model until then.
        hang_up = HangUp()
        with StallingOllama(["A "], prefill=True) as server:
            with (
                patch("common.services.ollama_client.open_stream", REAL_OPEN_STREAM),
                override_settings(OLLAMA_BASE_URL=server.base_url),
            ):
                # Once the request is in, so the hang-up lands in the prefill
                # rather than before the connection is made.
                asking = act_once_set(server.requested, hang_up.hang_up)
                with self.assertRaises(StreamHungUp):
                    write_session_recap(
                        "recap it", deadline=time.monotonic() + 30, hang_up=hang_up
                    )
                asking.join(ACT_ONCE_SET_SECONDS)
                self.assertTrue(server.closed.wait(5))

        # Before #935 the hang-up waited for the headers, which the stand-in
        # holds back until it is hung up on: the server saw no hang-up.
        self.assertEqual(server.hang_ups, ["prefill"])
        self.assertFalse(server.responding.is_set())

    def test_the_stream_is_closed_when_the_turn_runs_out(self):
        closed = threading.Event()

        def slow_tokens(*_args, **_kwargs):
            try:
                while True:
                    time.sleep(0.05)
                    yield "word "
            finally:
                closed.set()

        session = GMSession.objects.create(user=self.user, name="long", mode="play")
        session.history = turns(400, words=120)
        session.save()
        # The services module's client only: the turn's own calls go through
        # the engine's, which the test's scripted replies answer.
        client = Mock()
        client.stream_chat.side_effect = slow_tokens
        with (
            override_settings(GM_STREAM_BUDGET_SECONDS=0.2),
            patch("gm.views.GM_STREAM_KEEPALIVE_SECONDS", 0.05),
            patch("gm.services.ollama_client", client),
        ):
            _text, sent, _calls = self.play(
                body={"session_id": session.pk, "message": "We walk east"},
            )

        self.assertEqual(sent[-1]["error"], views.GM_STREAM_TOO_SLOW_MESSAGE)
        self.assertTrue(closed.wait(2), "the recap's stream was left running")
        session.refresh_from_db()
        self.assertEqual(session.summary_covers, 0)
