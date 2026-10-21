"""Tests for the offline CLI Game Master (issue #113).

RULES_DIR used to point at a deleted `rules/` tree, so the CLI could never
start (`load_rules()` always hit the "not found" branch and called
`sys.exit(1)`). These tests pin the fixed path -- the same canonical rules
tree the web GM defaults to (`gm.engine.DEFAULT_RULES_DIR`) -- and prove the
CLI entrypoint actually runs far enough to load real rules content, without
requiring a live Ollama connection.
"""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import httpx

from cli import gm as cli_gm
from common.services.ollama_client import STRUCTURED_OUTPUT_UNAVAILABLE_STATUS
from common.test_helpers import fake_embedder, write_fake_corpus
from gm import retrieval
from gm.engine import (
    DEFAULT_RULES_DIR,
    MODE_AIDE,
    MODE_PLAY,
    aide_system_prompt,
    dm_play_system_prompt,
    dm_system_prompt,
    normalise_mode,
    section_dm_authority,
    system_prompt,
)
from gm.model_config import GMModelConfig
from gm.retrieval import CANON_CONTEXT_PREAMBLE, CANON_KIND, build_index


class RulesDirTest(TestCase):
    """RULES_DIR must resolve to the current rules location, not the deleted one."""

    def test_rules_dir_matches_canonical_default(self):
        """CLI's RULES_DIR is the same path the web GM's settings.RULES_DIR
        defaults to -- one source of truth, not a second hardcoded copy."""
        self.assertEqual(cli_gm.RULES_DIR, DEFAULT_RULES_DIR)

    def test_rules_dir_points_at_public_rules(self):
        """Sanity check on the actual path segment, so a future refactor that
        silently changes DEFAULT_RULES_DIR to something wrong still fails
        this test even though it would keep matching the (now wrong) constant."""
        parts = cli_gm.RULES_DIR.parts[-3:]
        self.assertEqual(parts, ("reference", "content", "public-rules"))

    def test_rules_dir_exists_on_disk(self):
        """The old `rules/` tree was deleted; the replacement must actually exist."""
        self.assertTrue(cli_gm.RULES_DIR.is_dir())

    def test_load_rules_loads_nonempty_content(self):
        """load_rules() reads real markdown files from the fixed directory
        instead of hitting the missing-directory sys.exit(1) path."""
        rules_text = cli_gm.load_rules()
        self.assertIsInstance(rules_text, str)
        self.assertGreater(len(rules_text), 0)
        # Loader tags each file's content with a `--- <relative path> ---`
        # header (see gm.engine.load_rules_from_dir) -- confirms real files
        # were read, not just an empty join.
        self.assertIn("---", rules_text)
        self.assertIn(".md", rules_text)


class MainEntrypointTest(TestCase):
    """Run the CLI's real entrypoint far enough to prove rules load end-to-end."""

    def test_main_starts_and_loads_rules_without_live_ollama(self):
        """main() should get past the Ollama check and rules loading -- the
        two steps that ran before the interactive loop -- then exit cleanly
        on the very first (mocked) prompt. No live model or network access is
        used: the model listing and rich's Prompt.ask() are both mocked."""
        with (
            patch("cli.gm.ollama_client.list_models", return_value=[cli_gm.MODEL]),
            patch("cli.gm.Prompt.ask", side_effect=EOFError),
        ):
            # Raises nothing and does not sys.exit(1) -- before the fix this
            # died inside load_rules() with "Rules directory not found".
            cli_gm.main()


class ModelConfigTest(TestCase):
    """The CLI reads GM_MODEL / GM_NUM_CTX / GM_TEMPERATURE like the web GM
    (issue #324) -- no custom ``tarmar-gm`` model, no Modelfile."""

    def test_model_comes_from_the_shared_config(self):
        expected = GMModelConfig.from_environ()
        self.assertEqual(cli_gm.GM_CONFIG, expected)
        self.assertEqual(cli_gm.MODEL, expected.model)

    def test_system_message_is_the_engine_prompt(self):
        self.assertEqual(
            cli_gm.build_system_message("ignored"),
            {"role": "system", "content": system_prompt()},
        )

    def test_the_offline_session_plays_as_a_player_by_default(self):
        """No login offline, so no role: the player prompt unless asked (#352)."""
        self.assertFalse(cli_gm.RUNNING_AS_GAME_MASTER)

    def test_the_game_master_variable_switches_both_prompt_and_schema(self):
        # Both constants are read from the environment at import, and the
        # mode's default follows the DM flag (issue #372), so the pair is
        # patched together the way the environment would have set it.
        with (
            patch.object(cli_gm, "RUNNING_AS_GAME_MASTER", True),
            patch.object(cli_gm, "RUNNING_MODE", MODE_AIDE),
        ):
            self.assertEqual(
                cli_gm.build_system_message("ignored")["content"], dm_system_prompt()
            )

    def test_the_mode_variable_lets_a_game_master_play(self):
        """Issue #372's offline half: authority without the aide's manner."""
        with (
            patch.object(cli_gm, "RUNNING_AS_GAME_MASTER", True),
            patch.object(cli_gm, "RUNNING_MODE", MODE_PLAY),
        ):
            self.assertEqual(
                cli_gm.build_system_message("ignored")["content"],
                dm_play_system_prompt(),
            )

    def test_a_player_choosing_aide_mode_gains_no_authority(self):
        """The two axes stay separate offline as well (issues #352, #372)."""
        with (
            patch.object(cli_gm, "RUNNING_AS_GAME_MASTER", False),
            patch.object(cli_gm, "RUNNING_MODE", MODE_AIDE),
        ):
            sent = cli_gm.build_system_message("ignored")["content"]
        self.assertEqual(sent, aide_system_prompt())
        self.assertNotIn(section_dm_authority(), sent)

    def test_the_mode_variable_is_named_in_one_place(self):
        self.assertEqual(cli_gm.GM_MODE_ENV, "TARMAR_GM_MODE")

    def test_an_unset_mode_follows_the_role(self):
        """``TARMAR_GM_DM=true`` alone still runs the aide, as it always did."""
        self.assertEqual(normalise_mode(None, is_game_master=True), MODE_AIDE)
        self.assertEqual(normalise_mode(None, is_game_master=False), MODE_PLAY)

    def test_the_game_master_variable_switches_the_extraction_schema_too(self):
        history = [{"role": "user", "content": "Vashti has STR 22."}]
        with (
            patch.object(cli_gm, "RUNNING_AS_GAME_MASTER", True),
            patch(
                "cli.gm.ollama_client.chat", return_value='{"name": "Vashti"}'
            ) as mock_chat,
        ):
            cli_gm.extract_character_payload(history)
        sent = mock_chat.call_args.kwargs["response_format"]
        self.assertEqual(sent["properties"]["strength"]["maximum"], 30)

    def test_the_environment_variable_is_named_in_one_place(self):
        self.assertEqual(cli_gm.GAME_MASTER_ENV, "TARMAR_GM_DM")

    def test_missing_model_says_pull_not_create(self):
        with (
            patch("cli.gm.ollama_client.list_models", return_value=["other"]),
            patch("cli.gm.console.print") as mock_print,
            self.assertRaises(SystemExit),
        ):
            cli_gm.check_ollama()
        printed = " ".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertIn(f"ollama pull {cli_gm.MODEL}", printed)
        self.assertNotIn("ollama create", printed)

    def test_stream_response_sends_options_and_think_false(self):
        history = [{"role": "user", "content": "hello"}]
        with (
            patch(
                "cli.gm.ollama_client.stream_chat", return_value=iter(["Well ", "met."])
            ) as mock_stream,
            patch("cli.gm.Spinner"),
        ):
            response = cli_gm.stream_response(history)

        self.assertEqual(response, "Well met.")
        mock_stream.assert_called_once_with(
            cli_gm.MODEL,
            history,
            options=cli_gm.GM_CONFIG.options(),
            think=False,
            base_url=cli_gm.OLLAMA_BASE_URL,
        )


class RetrievalTest(TestCase):
    """An ordinary CLI turn is grounded in canon excerpts (issues #325, #331).

    ``stream_response`` files the excerpt block into the history it is given,
    just before the player's message, so /save keeps it and the model's prompt
    prefix stays cacheable; readers never see it.
    """

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name) / "corpus"
        root.mkdir()
        write_fake_corpus(root)
        self.index_path = Path(self._temp.name) / "gm-index.json"
        build_index(
            corpus_root=root,
            index_path=self.index_path,
            embed_model="fake-model",
            base_url="http://unused",
            embedder=fake_embedder,
        )

    def tearDown(self):
        self._temp.cleanup()

    @staticmethod
    def _fake_chat(*args, **kwargs):
        return iter(["A dragon stirs."])

    @staticmethod
    def _fake_embed_texts(texts, *, model, base_url):
        return fake_embedder(texts)

    def _retrieval_on(self, **overrides):
        values = {
            "INDEX_PATH": self.index_path,
            "RETRIEVAL_ENABLED": True,
            "RETRIEVAL_TOP_K": 1,
        }
        values.update(overrides)
        return patch.multiple(cli_gm, **values)

    def test_player_turn_sends_and_files_the_excerpt_block(self):
        history = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "Tell me about the dragon"},
        ]
        with (
            self._retrieval_on(),
            patch.object(retrieval, "embed_texts", self._fake_embed_texts),
            patch(
                "cli.gm.ollama_client.stream_chat", side_effect=self._fake_chat
            ) as mock_chat,
        ):
            response = cli_gm.stream_response(history, query="Tell me about the dragon")

        self.assertEqual(response, "A dragon stirs.")
        sent = mock_chat.call_args.args[1]
        self.assertEqual(
            [message["role"] for message in sent], ["system", "user", "user"]
        )
        self.assertTrue(sent[1]["content"].startswith(CANON_CONTEXT_PREAMBLE))
        self.assertIn("### the-world.md — The World", sent[1]["content"])
        self.assertEqual(set(sent[1]), {"role", "content"}, "bookkeeping stripped")
        self.assertEqual(sent[2], history[-1])
        self.assertEqual(len(history), 3, "the block is filed into the history")
        self.assertEqual(history[1]["kind"], CANON_KIND)
        self.assertEqual(history[1]["content"], sent[1]["content"])

    def test_prefix_is_stable_and_seen_chunks_are_not_repeated(self):
        history = [{"role": "system", "content": "sys"}]
        sent_per_turn: list[list[dict[str, str]]] = []
        with (
            self._retrieval_on(),
            patch.object(retrieval, "embed_texts", self._fake_embed_texts),
            patch(
                "cli.gm.ollama_client.stream_chat", side_effect=self._fake_chat
            ) as mock_chat,
        ):
            for text in ["the dragon", "the emperor", "a sword"]:
                history.append({"role": "user", "content": text})
                response = cli_gm.stream_response(history, query=text)
                history.append({"role": "assistant", "content": response})
                sent_per_turn.append(mock_chat.call_args.args[1])

        for previous, current in zip(sent_per_turn, sent_per_turn[1:], strict=False):
            self.assertEqual(current[: len(previous)], previous)
        canon_per_turn = [
            sum(1 for m in sent if CANON_CONTEXT_PREAMBLE in m["content"])
            for sent in sent_per_turn
        ]
        self.assertEqual(canon_per_turn, [1, 1, 2], "the emperor chunk was already in")
        self.assertEqual(cli_gm.visible_message_count(history), 7)

    def test_command_turns_and_disabled_retrieval_send_history_as_is(self):
        history = [{"role": "user", "content": "dragon"}]
        with (
            self._retrieval_on(),
            patch.object(retrieval, "embed_texts") as mock_embed,
            patch(
                "cli.gm.ollama_client.stream_chat", side_effect=self._fake_chat
            ) as mock_chat,
        ):
            cli_gm.stream_response(history)  # a /roll, /state or /rules turn
        mock_embed.assert_not_called()
        self.assertEqual(mock_chat.call_args.args[1], history)

        with (
            self._retrieval_on(RETRIEVAL_ENABLED=False),
            patch.object(retrieval, "embed_texts") as mock_embed,
            patch(
                "cli.gm.ollama_client.stream_chat", side_effect=self._fake_chat
            ) as mock_chat,
        ):
            cli_gm.stream_response(history, query="dragon")
        mock_embed.assert_not_called()
        self.assertEqual(mock_chat.call_args.args[1], history)
        self.assertEqual(len(history), 1)

    def test_missing_index_warns_and_still_answers(self):
        history = [{"role": "user", "content": "dragon"}]
        with (
            self._retrieval_on(INDEX_PATH=Path(self._temp.name) / "absent.json"),
            patch(
                "cli.gm.ollama_client.stream_chat", side_effect=self._fake_chat
            ) as mock_chat,
            self.assertLogs("gm.retrieval", level="WARNING") as logs,
        ):
            response = cli_gm.stream_response(history, query="dragon")
        self.assertEqual(response, "A dragon stirs.")
        self.assertIn("GM index not found", logs.output[0])
        self.assertEqual(mock_chat.call_args.args[1], history)

    def test_saved_sessions_keep_canon_entries_but_counts_skip_them(self):
        history = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "x", "kind": CANON_KIND, "chunk_ids": ["a"]},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "well met"},
        ]
        saves_dir = Path(self._temp.name) / "saves"
        with (
            patch.object(cli_gm, "SAVES_DIR", saves_dir),
            patch("cli.gm.console.print") as mock_print,
        ):
            cli_gm.save_session("cache-test", history, "rules")
            loaded = cli_gm.load_session("cache-test")
            cli_gm.list_saves()
        self.assertEqual(loaded, history, "/load restores the excerpt blocks")
        printed = " ".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertIn("(3 messages,", printed)
        self.assertIn("-- 3 messages,", printed)

    def test_a_saved_session_names_its_instant_with_the_offset(self):
        """saved_at was the machine's naive clock, which names no moment once
        the file is read in another zone (issue #144)."""
        saves_dir = Path(self._temp.name) / "saves"
        before = datetime.now().astimezone()
        with (
            patch.object(cli_gm, "SAVES_DIR", saves_dir),
            patch("cli.gm.console.print"),
        ):
            saved = cli_gm.save_session("instant-test", [], "rules")
        after = datetime.now().astimezone()

        saved_at = json.loads(saved.read_text(encoding="utf-8"))["saved_at"]
        self.assertRegex(saved_at, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")
        instant = datetime.fromisoformat(saved_at)
        self.assertIsNotNone(instant.utcoffset())
        self.assertLessEqual(before - timedelta(seconds=1), instant)
        self.assertLessEqual(instant, after)

    def test_max_tokens_knob_reads_the_environment(self):
        self.assertEqual(cli_gm.RETRIEVAL_MAX_TOKENS, 900)


class CharacterCommandTest(TestCase):
    """``/character`` writes the file the site's importer accepts (issue #344)."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.saves_dir = Path(self._temp.name)
        self._patch_saves = patch.object(cli_gm, "SAVES_DIR", self.saves_dir)
        self._patch_saves.start()

    def tearDown(self):
        self._patch_saves.stop()
        self._temp.cleanup()

    @staticmethod
    def _refusal() -> httpx.HTTPStatusError:
        """What Ollama's MLX runner answers to any ``format`` (issue #344)."""
        return httpx.HTTPStatusError(
            "structured output is unavailable",
            request=MagicMock(),
            response=MagicMock(status_code=STRUCTURED_OUTPUT_UNAVAILABLE_STATUS),
        )

    def test_sends_the_schema_and_writes_the_importable_document(self):
        from characters.constants import CHARACTER_EXPORT_FORMAT
        from gm.character_schema import (
            EXTRACTION_TEMPERATURE,
            character_json_schema,
            extraction_prompt,
        )

        history = [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": "a hunter named Wren"},
        ]
        extracted = {
            "name": "Wren",
            "strength": 12,
            "skills": [{"name": "Bow", "points": 2}],
        }
        with (
            patch(
                "cli.gm.ollama_client.chat", return_value=json.dumps(extracted)
            ) as mock_chat,
            patch("cli.gm.Spinner"),
            patch.object(cli_gm.console, "print"),
        ):
            written = cli_gm.save_character_file(history, "")

        model, messages = mock_chat.call_args.args
        kwargs = mock_chat.call_args.kwargs
        self.assertEqual(model, cli_gm.MODEL)
        self.assertIs(kwargs["think"], False)
        self.assertEqual(kwargs["response_format"], character_json_schema())
        self.assertEqual(kwargs["options"]["temperature"], EXTRACTION_TEMPERATURE)
        self.assertEqual(kwargs["base_url"], cli_gm.OLLAMA_BASE_URL)
        self.assertEqual(messages[-1]["content"], extraction_prompt())
        self.assertEqual(messages[0]["content"], system_prompt())

        assert written is not None
        self.assertEqual(written, self.saves_dir / "character_Wren.json")
        document = json.loads(written.read_text(encoding="utf-8"))
        self.assertEqual(document["_format"], CHARACTER_EXPORT_FORMAT)
        self.assertEqual(document["data"]["name"], "Wren")
        self.assertEqual(document["data"]["skills"], {"bow": 2})

    def test_a_runner_without_structured_output_gets_the_schema_in_the_prompt(self):
        """The fallback is the shared client's, not a second copy here."""
        from gm.character_schema import SCHEMA_IN_PROMPT_PREAMBLE

        with (
            patch(
                "cli.gm.ollama_client.chat",
                side_effect=[self._refusal(), '```json\n{"name": "Wren"}\n```'],
            ) as mock_chat,
            patch("cli.gm.Spinner"),
            patch.object(cli_gm.console, "print"),
        ):
            written = cli_gm.save_character_file([], "")

        assert written is not None
        self.assertEqual(json.loads(written.read_text())["data"]["name"], "Wren")
        first, second = mock_chat.call_args_list
        self.assertIsNotNone(first.kwargs["response_format"])
        self.assertNotIn("response_format", second.kwargs)
        self.assertIn(SCHEMA_IN_PROMPT_PREAMBLE, second.args[1][-1]["content"])

    def test_other_ollama_errors_propagate(self):
        error = httpx.HTTPStatusError(
            "down", request=MagicMock(), response=MagicMock(status_code=500)
        )
        with (
            patch("cli.gm.ollama_client.chat", side_effect=error),
            patch("cli.gm.Spinner"),
            patch.object(cli_gm.console, "print"),
        ):
            with self.assertRaises(httpx.HTTPStatusError):
                cli_gm.save_character_file([], "")

    def test_explicit_name_names_the_file(self):
        with (
            patch("cli.gm.ollama_client.chat", return_value='{"name": "Wren"}'),
            patch("cli.gm.Spinner"),
            patch.object(cli_gm.console, "print"),
        ):
            written = cli_gm.save_character_file([], "my hunter")
        self.assertEqual(written, self.saves_dir / "character_my_hunter.json")

    def test_a_non_json_answer_writes_nothing(self):
        with (
            patch("cli.gm.ollama_client.chat", return_value="Wren is a hunter."),
            patch("cli.gm.Spinner"),
            patch.object(cli_gm.console, "print") as mock_print,
        ):
            written = cli_gm.save_character_file([], "")
        self.assertIsNone(written)
        self.assertEqual(list(self.saves_dir.iterdir()), [])
        printed = " ".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertIn("not valid JSON", printed)


class CLIGMReplyTest(TestCase):
    """The offline GM answers as a plain conversation (issue #590).

    It keeps no character records, so it runs no plan and rolls nothing on
    the model's behalf: a turn is one call, and the player rolls with /roll.
    """

    def replies(self, *texts):
        """A fake ``stream_chat`` handing back one scripted reply per call."""
        calls: list[list[dict[str, str]]] = []

        def stream_chat(_model=None, messages=None, **kwargs):
            calls.append(list(messages or kwargs.get("messages") or []))
            reply = texts[len(calls) - 1]
            if isinstance(reply, BaseException):
                raise reply
            return iter(reply if isinstance(reply, list) else [reply])

        return stream_chat, calls

    def test_thinking_is_never_printed(self):
        chat, _calls = self.replies(["<think>hmm</think>", "The gate opens."])
        with (
            patch("cli.gm.ollama_client.stream_chat", side_effect=chat),
            patch("cli.gm.Spinner"),
        ):
            response = cli_gm.stream_response([{"role": "user", "content": "hi"}])
        self.assertEqual(response, "The gate opens.")

    def test_a_failed_call_is_reported_and_answers_nothing(self):
        chat, _calls = self.replies(ConnectionError("gone"))
        with (
            patch("cli.gm.ollama_client.stream_chat", side_effect=chat),
            patch("cli.gm.Spinner"),
            patch.object(cli_gm.console, "print") as mock_print,
        ):
            response = cli_gm.stream_response([{"role": "user", "content": "hi"}])
        self.assertEqual(response, "")
        printed = " ".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertIn("Error from Ollama: gone", printed)

    def test_a_reply_with_nothing_visible_still_ends_the_spinner(self):
        chat, _calls = self.replies(["<think>only thinking</think>"])
        with (
            patch("cli.gm.ollama_client.stream_chat", side_effect=chat),
            patch("cli.gm.Spinner") as spinner,
        ):
            response = cli_gm.stream_response([{"role": "user", "content": "hi"}])
        self.assertEqual(response, "")
        spinner.return_value.stop.assert_called_once()

    def test_a_turn_without_dice_still_costs_one_call(self):
        chat, calls = self.replies("They walk past without a word.")
        with (
            patch("cli.gm.ollama_client.stream_chat", side_effect=chat),
            patch("cli.gm.Spinner"),
        ):
            response = cli_gm.stream_response([{"role": "user", "content": "hi"}])
        self.assertEqual(response, "They walk past without a word.")
        self.assertEqual(len(calls), 1)
