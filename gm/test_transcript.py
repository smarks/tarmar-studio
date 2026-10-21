"""Turn records and the session transcript (issue #389).

The transcript exists because a model cannot be trusted to repeat a
conversation, and the turn records exist because the history cannot hold
anything but messages. These tests pin both halves: that every way out of a
turn leaves a record, and that the download reproduces what the server stored,
hidden entries marked as hidden.
"""

import datetime
import json
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import override_settings

from common.test_helpers import BaseTestCase
from gm.engine import dm_system_prompt, system_prompt
from gm.models import GMPromptSnapshot, GMSession, GMTurnRecord
from gm.retrieval import CANON_KIND, PROCEDURE_KIND
from gm.testing import add_character, gm_replies
from gm.transcript import (
    CHIP,
    HIDDEN,
    VISIBLE,
    Block,
    render_transcript_text,
    transcript_blocks,
)
from gm.turn_record import TurnRecorder, prompt_hash
from gm.views import GM_SERVER_ERROR_MESSAGE

STREAM_URL = "/tarmar/gm/stream/"


def _post_turn(client, body: dict) -> list[dict]:
    """Send one turn and return the SSE frames it produced."""
    response = client.post(
        STREAM_URL, json.dumps(body), content_type="application/json"
    )
    if not hasattr(response, "streaming_content"):
        return [json.loads(response.content)]
    raw = b"".join(response.streaming_content).decode()
    return [json.loads(chunk[len("data: ") :]) for chunk in raw.split("\n\n") if chunk]


def _recorder(**overrides) -> TurnRecorder:
    values = {
        "user": None,
        "session_id": None,
        "command": "",
        "mode": "play",
        "is_game_master": False,
        "user_message": "hello",
        "history_start": 0,
        "system_prompt": "PROMPT",
        "model": "test-model",
        "options": {"num_ctx": 1024},
    }
    values.update(overrides)
    return TurnRecorder(**values)


class TurnRecordStreamTest(BaseTestCase):
    """Every way a turn ends leaves exactly one record."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    @override_settings(GM_MODEL="test-model", GM_NUM_CTX=1024, GM_TEMPERATURE=0.5)
    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_completed_turn_records_what_it_ran_with_and_what_it_saved(self, stream):
        stream.side_effect = gm_replies(["Hello ", "there"])

        frames = _post_turn(self.client, {"message": "Hi"})

        record = GMTurnRecord.objects.get()
        session = GMSession.objects.get()
        self.assertEqual(frames[-1]["session_id"], session.pk)
        self.assertEqual(record.session, session)
        self.assertEqual(record.user, self.user)
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_OK)
        self.assertEqual(record.mode, "play")
        self.assertFalse(record.is_game_master)
        self.assertEqual(record.command, "")
        self.assertEqual(record.user_message, "Hi")
        self.assertEqual(record.reply, "Hello there")
        self.assertEqual(record.model, "test-model")
        self.assertEqual(record.options, {"num_ctx": 1024, "temperature": 0.5})
        self.assertEqual(record.history_start, 0)
        self.assertEqual(record.history_end, len(session.history))
        self.assertEqual(record.prompt.text, system_prompt())
        self.assertEqual(record.prompt.prompt_hash, prompt_hash(system_prompt()))
        self.assertEqual(record.error, "")

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_the_same_prompt_is_stored_once_across_turns(self, stream):
        stream.side_effect = lambda *args, **kwargs: iter(["ok"])
        _post_turn(self.client, {"message": "One"})
        session = GMSession.objects.get()
        _post_turn(self.client, {"message": "Two", "session_id": session.pk})

        self.assertEqual(GMTurnRecord.objects.count(), 2)
        self.assertEqual(GMPromptSnapshot.objects.count(), 1)
        second = list(GMTurnRecord.objects.order_by("pk"))[-1]
        self.assertEqual(second.history_start, 2)
        self.assertEqual(second.session, session)

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_game_master_turn_records_authority_and_the_aide_prompt(self, stream):
        self.logout()
        self.login_as(self.create_admin())
        stream.side_effect = gm_replies(["Answered."])

        _post_turn(self.client, {"message": "What does a longsword cost?"})

        record = GMTurnRecord.objects.get()
        self.assertTrue(record.is_game_master)
        self.assertEqual(record.mode, "aide")
        self.assertEqual(record.prompt.text, dm_system_prompt())

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_failed_turn_is_recorded_even_though_it_saves_no_history(self, stream):
        stream.side_effect = RuntimeError("ollama exploded")

        frames = _post_turn(self.client, {"message": "Are you there?"})

        self.assertEqual(frames[-1], {"error": GM_SERVER_ERROR_MESSAGE})
        record = GMTurnRecord.objects.get()
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_ERROR)
        # The record holds what the player was told (#13); the engine's
        # failed event keeps the real reason for whoever debugs the turn.
        self.assertEqual(record.error, GM_SERVER_ERROR_MESSAGE)
        failed = record.events.get(kind="failed")
        self.assertEqual(failed.payload["error"], "ollama exploded")
        self.assertEqual(record.user_message, "Are you there?")
        self.assertIsNone(record.history_end)
        self.assertIsNone(record.session)
        self.assertFalse(GMSession.objects.exists())

    @override_settings(GM_STREAM_BUDGET_SECONDS=0)
    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_turn_that_runs_out_of_time_is_recorded(self, stream):
        stream.side_effect = gm_replies(["slow ", "reply"])

        frames = _post_turn(self.client, {"message": "Take your time"})

        self.assertIn("error", frames[-1])
        record = GMTurnRecord.objects.get()
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_TOO_SLOW)
        self.assertEqual(record.error, frames[-1]["error"])
        self.assertIsNone(record.history_end)

    def test_a_turn_refused_before_it_starts_is_recorded(self):
        frames = _post_turn(self.client, {"message": "   "})

        self.assertEqual(frames[-1]["error"], "No message provided")
        record = GMTurnRecord.objects.get()
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_REJECTED)
        self.assertEqual(record.error, "No message provided")

    def test_a_bad_roll_expression_is_recorded_as_rejected(self):
        _post_turn(self.client, {"message": "banana", "command": "roll"})

        record = GMTurnRecord.objects.get()
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_REJECTED)
        self.assertEqual(record.command, "roll")
        self.assertNotEqual(record.error, "")

    @patch("gm.views.load_rules", return_value="")
    def test_a_rules_lookup_with_no_rules_is_recorded_as_rejected(self, _rules):
        _post_turn(self.client, {"message": "How far can I move?", "command": "rules"})

        record = GMTurnRecord.objects.get()
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_REJECTED)
        self.assertEqual(record.error, "Rules directory not found")

    def test_character_with_no_session_is_recorded_as_rejected(self):
        _post_turn(self.client, {"command": "character"})

        record = GMTurnRecord.objects.get()
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_REJECTED)
        self.assertEqual(record.command, "character")

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_a_page_that_disconnects_mid_turn_is_recorded(self, stream):
        stream.side_effect = gm_replies(["one ", "two ", "three"])
        response = self.client.post(
            STREAM_URL, json.dumps({"message": "Hi"}), content_type="application/json"
        )
        frames = iter(response.streaming_content)
        next(frames)

        response.close()

        # The turn runs to the end without its page (issue #515), and its one
        # record says so.
        record = GMTurnRecord.objects.get()
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_OK)
        self.assertEqual(record.reply, "one two three")
        self.assertEqual(record.error, "")


class TurnRecorderTest(BaseTestCase):
    def test_finishing_twice_writes_one_record(self):
        recorder = _recorder()

        recorder.finish(GMTurnRecord.OUTCOME_OK)
        recorder.finish(GMTurnRecord.OUTCOME_ERROR, error="late")

        self.assertEqual(GMTurnRecord.objects.get().outcome, GMTurnRecord.OUTCOME_OK)

    def test_a_record_that_cannot_be_written_is_logged_and_does_not_raise(self):
        recorder = _recorder()
        with patch.object(
            GMTurnRecord.objects, "create", side_effect=RuntimeError("disk full")
        ):
            with self.assertLogs("gm.turn_record", level="ERROR") as logs:
                recorder.finish(GMTurnRecord.OUTCOME_OK)

        self.assertIn("Could not record GM turn", logs.output[0])
        self.assertFalse(GMTurnRecord.objects.exists())


class TranscriptBlocksTest(BaseTestCase):
    """How records are laid against the history they wrote."""

    def _record(self, start: int, end: int | None) -> GMTurnRecord:
        return GMTurnRecord(
            outcome="ok", mode="play", history_start=start, history_end=end
        )

    def test_entries_sit_inside_the_turn_that_wrote_them(self):
        history = [{"role": "user", "content": str(n)} for n in range(4)]
        first, second = self._record(0, 2), self._record(2, 4)

        blocks = transcript_blocks(history, [first, second])

        self.assertEqual(
            [(block.kind, block.index, block.number) for block in blocks],
            [
                ("turn_start", -1, 1),
                ("entry", 0, 0),
                ("entry", 1, 0),
                ("turn_end", -1, 1),
                ("turn_start", -1, 2),
                ("entry", 2, 0),
                ("entry", 3, 0),
                ("turn_end", -1, 2),
            ],
        )

    def test_a_turn_that_saved_nothing_sits_where_it_happened(self):
        history = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ]

        blocks = transcript_blocks(history, [self._record(0, 2), self._record(2, None)])

        self.assertEqual(
            [block.kind for block in blocks],
            ["turn_start", "entry", "entry", "turn_end", "turn_start", "turn_end"],
        )

    def test_entries_no_record_owns_stay_in_order(self):
        history = [{"role": "user", "content": str(n)} for n in range(3)]

        blocks = transcript_blocks(history, [self._record(1, 2)])

        self.assertEqual(
            [(block.kind, block.index) for block in blocks],
            [
                ("entry", 0),
                ("turn_start", -1),
                ("entry", 1),
                ("turn_end", -1),
                ("entry", 2),
            ],
        )
        self.assertIsInstance(blocks[0], Block)


class TranscriptDownloadTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)
        self.session = GMSession.objects.create(
            user=self.user, name="The Broken Wheel", mode="aide"
        )
        self.session.history = [
            {
                "role": "user",
                "content": "Canon: the Broken Wheel is an inn.",
                "kind": CANON_KIND,
                "chunk_ids": ["inns-1"],
            },
            {"role": "user", "content": "I push open the door."},
            {
                "role": "user",
                "content": "[Server procedure — five years ran]",
                "kind": PROCEDURE_KIND,
            },
            {"role": "assistant", "content": "The common room falls quiet."},
        ]
        self.session.save()
        add_character(
            self.session, {"name": "Wren", "strength": 18}, label="the ranger"
        )
        snapshot = GMPromptSnapshot.objects.create(
            prompt_hash=prompt_hash("THE PROMPT TEXT"), text="THE PROMPT TEXT"
        )
        GMTurnRecord.objects.create(
            session=self.session,
            user=self.user,
            outcome=GMTurnRecord.OUTCOME_OK,
            mode="aide",
            is_game_master=True,
            model="qwen-test",
            options={"num_ctx": 2048},
            prompt=snapshot,
            app_version="abc1234",
            user_message="I push open the door.",
            reply="The common room falls quiet.",
            history_start=0,
            history_end=4,
            procedures=[{"label": "5 years on the campaigner track"}],
            ledger_applied=["named Wren"],
            ledger_refused=["cannot spend 99 points"],
            notices=["The canon index is missing."],
        )
        GMTurnRecord.objects.create(
            session=self.session,
            user=self.user,
            outcome=GMTurnRecord.OUTCOME_ERROR,
            mode="aide",
            prompt=snapshot,
            user_message="a message that was lost",
            history_start=4,
            history_end=None,
            error="ollama exploded",
        )
        self.url = f"/tarmar/gm/sessions/{self.session.pk}/transcript/"

    def test_the_text_download_carries_every_entry_and_every_turn(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertRegex(
            response["Content-Disposition"],
            rf'^attachment; filename="gm-session-{self.session.pk}-the-broken-wheel-\d{{8}}-\d{{6}}\.txt"$',
        )
        body = response.content.decode()
        self.assertIn(f"Session {self.session.pk}: The Broken Wheel", body)
        self.assertIn("Mode now: aide", body)
        self.assertIn(f"[#0] USER · canon · {HIDDEN}", body)
        self.assertIn('data: {"chunk_ids": ["inns-1"]}', body)
        self.assertIn(f"[#1] USER · message · {VISIBLE}", body)
        self.assertIn(f"[#2] USER · procedure · {CHIP}", body)
        self.assertIn("The common room falls quiet.", body)
        self.assertIn("TURN 1 ·", body)
        self.assertIn("mode aide · Game Master authority · command (message)", body)
        self.assertIn('model qwen-test · options {"num_ctx": 2048}', body)
        self.assertIn("app abc1234", body)
        self.assertIn("ledger applied: named Wren", body)
        self.assertIn("ledger refused: cannot spend 99 points", body)
        self.assertIn("notice shown: The canon index is missing.", body)
        self.assertIn('procedure: {"label": "5 years on the campaigner track"}', body)
        self.assertIn("TURN 2 ·", body)
        self.assertIn("player sent: a message that was lost", body)
        self.assertIn("nothing from this turn was saved to the session history", body)
        self.assertIn("error: ollama exploded", body)
        self.assertIn('"strength": 18', body)
        # The prompt both turns shared appears once, naming both.
        self.assertEqual(body.count("THE PROMPT TEXT"), 1)
        self.assertIn("· turns 1, 2", body)
        # In reading order: the first turn opens before its first entry, and
        # the failed turn comes after the last saved one.
        self.assertLess(body.index("TURN 1 ·"), body.index("[#0]"))
        self.assertLess(body.index("[#3]"), body.index("TURN 2 ·"))

    def test_the_json_download_is_the_same_transcript_as_data(self):
        response = self.client.get(self.url, {"format": "json"})

        self.assertEqual(response["Content-Type"], "application/json; charset=utf-8")
        self.assertTrue(response["Content-Disposition"].endswith('.json"'))
        payload = json.loads(response.content)
        self.assertEqual(payload["session"]["id"], self.session.pk)
        self.assertEqual(
            [entry["visibility"] for entry in payload["history"]],
            [HIDDEN, VISIBLE, CHIP, VISIBLE],
        )
        self.assertEqual(
            [turn["outcome"] for turn in payload["turns"]], ["ok", "error"]
        )
        self.assertEqual(
            payload["turns"][0]["ledger_refused"], ["cannot spend 99 points"]
        )
        (character,) = payload["characters"]
        self.assertEqual(
            (character["name"], character["strength"], character["label"]),
            ("Wren", 18, "the ranger"),
        )
        self.assertFalse(character["complete"])
        self.assertEqual(list(payload["prompts"].values()), ["THE PROMPT TEXT"])

    def test_nobody_else_can_download_it(self):
        self.logout()
        self.login_as(self.create_user(username="someoneelse"))

        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_it_needs_a_login(self):
        self.logout()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)

    def test_a_session_from_before_turn_records_still_exports_its_history(self):
        GMTurnRecord.objects.all().delete()

        body = self.client.get(self.url).content.decode()

        self.assertIn("I push open the door.", body)
        self.assertIn(
            "(no turn records: this session predates detailed recording)", body
        )
        self.assertNotIn("TURN 1", body)

    def test_the_chat_page_gives_the_download_pattern_with_or_without_a_session(self):
        """A session this page starts needs its transcript too (issue #436).

        The URL used to be built only when the page loaded with a session, so
        a session started from a fresh page had none until a reload.
        """
        with_session = self.client.get(f"/tarmar/gm/?session={self.session.pk}")
        without_session = self.client.get("/tarmar/gm/")

        for response in (with_session, without_session):
            template = response.context["chat_config"]["transcriptUrlTemplate"]
            self.assertEqual(
                template.replace("{session_id}", str(self.session.pk)), self.url
            )
        self.assertContains(with_session, 'id="gm-transcript-button"')

    def test_each_stamp_names_its_zone(self):
        """The transcript's instants go through the one formatter a
        downloaded file writes instants with (#123), each with its zone."""
        created = datetime.datetime(2026, 10, 15, 2, 50, 11, tzinfo=datetime.UTC)
        updated = datetime.datetime(2026, 10, 16, 8, 5, 0, tzinfo=datetime.UTC)
        started = datetime.datetime(2026, 10, 16, 8, 4, 30, tzinfo=datetime.UTC)
        exported = datetime.datetime(2026, 10, 20, 23, 40, tzinfo=datetime.UTC)
        GMSession.objects.filter(pk=self.session.pk).update(
            created_at=created, updated_at=updated
        )
        GMTurnRecord.objects.filter(session=self.session).update(started_at=started)
        self.session.refresh_from_db()

        with patch("django.utils.timezone.now", return_value=exported):
            lines = render_transcript_text(self.session).splitlines()

        self.assertIn("Created: 2026-10-15 02:50:11 UTC", lines)
        self.assertIn("Updated: 2026-10-16 08:05:00 UTC", lines)
        self.assertTrue(
            any(
                line.startswith("Exported: 2026-10-20 23:40:00 UTC by app")
                for line in lines
            )
        )
        self.assertTrue(
            any(
                line.startswith("TURN 1 · 2026-10-16 08:04:30 UTC · ") for line in lines
            )
        )


class TranscriptCommandTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.session = GMSession.objects.create(
            user=self.create_user(), name="Over ssh"
        )
        self.session.history = [{"role": "user", "content": "Hello from the table."}]
        self.session.save()

    def test_it_prints_the_text_transcript(self):
        out = StringIO()

        call_command("gm_transcript", self.session.pk, stdout=out)

        self.assertIn("GM SESSION TRANSCRIPT", out.getvalue())
        self.assertIn("Hello from the table.", out.getvalue())

    def test_it_prints_json_on_request(self):
        out = StringIO()

        call_command("gm_transcript", self.session.pk, "--format", "json", stdout=out)

        self.assertEqual(json.loads(out.getvalue())["session"]["name"], "Over ssh")

    def test_an_unknown_session_is_an_error(self):
        with self.assertRaisesMessage(CommandError, "No GM session 999999"):
            call_command("gm_transcript", 999999, stdout=StringIO())
