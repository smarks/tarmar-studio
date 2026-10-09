"""Tests for the Ollama HTTP client (issue #103).

``common/services/*`` was excluded from the coverage gate, so
``stream_chat``'s line-by-line JSONL parsing -- content extraction, blank
line skipping, and early exit on a ``done`` frame -- ran only against a real
Ollama server and was never covered by the test suite (every call site mocks
``ollama_client.stream_chat`` itself). These tests exercise that parsing
directly against a mocked ``httpx.stream``, plus the client's error and
timeout propagation, now that the exclusion has been lifted.
"""

import json
import time
from unittest.mock import MagicMock, patch

import httpx
from django.test import TestCase, override_settings

from common.services import ollama_client
from common.test_helpers import StallingOllama

# The suite's conftest replaces httpx.stream for every test; the deadline
# tests below need a real socket (issue #887), so they take the real one,
# captured at import, before any fixture runs.
REAL_STREAM = httpx.stream


def _fake_stream_response(lines: list[str], *, http_error: bool = False) -> MagicMock:
    """Build a fake httpx streaming response yielding the given raw lines."""
    resp = MagicMock()
    resp.iter_lines.return_value = iter(lines)
    if http_error:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _wire_stream(mock_stream: MagicMock, resp: MagicMock) -> None:
    """Make the ``httpx.stream(...)`` mock behave as a context manager.

    A bare MagicMock's ``__exit__`` returns a truthy MagicMock, which would
    suppress any exception raised inside the ``with`` block -- so it must be
    pinned to a falsy value explicitly.
    """
    mock_stream.return_value.__enter__.return_value = resp
    mock_stream.return_value.__exit__.return_value = False


class StreamChatTest(TestCase):
    @patch("common.services.ollama_client.httpx.stream")
    def test_yields_content_and_skips_blank_and_contentless_lines(self, mock_stream):
        lines = [
            "",  # blank line: skipped outright
            json.dumps({"message": {"content": "Hello"}}),
            json.dumps({"message": {}}),  # no content key: yields nothing
            json.dumps({"message": {"content": " world"}, "done": True}),
        ]
        _wire_stream(mock_stream, _fake_stream_response(lines))

        result = list(
            ollama_client.stream_chat("llama3", [{"role": "user", "content": "hi"}])
        )

        self.assertEqual(result, ["Hello", " world"])

    @patch("common.services.ollama_client.httpx.stream")
    def test_stops_at_done_without_consuming_further_lines(self, mock_stream):
        lines = [
            json.dumps({"message": {"content": "a"}, "done": True}),
            json.dumps({"message": {"content": "should never appear"}}),
        ]
        _wire_stream(mock_stream, _fake_stream_response(lines))

        result = list(ollama_client.stream_chat("llama3", []))

        self.assertEqual(result, ["a"])

    @patch("common.services.ollama_client.httpx.stream")
    def test_raises_on_http_error_status(self, mock_stream):
        _wire_stream(mock_stream, _fake_stream_response([], http_error=True))

        with self.assertRaises(httpx.HTTPStatusError):
            list(ollama_client.stream_chat("llama3", []))

    @patch("common.services.ollama_client.httpx.stream")
    def test_propagates_timeout(self, mock_stream):
        mock_stream.side_effect = httpx.TimeoutException("timed out")

        with self.assertRaises(httpx.TimeoutException):
            list(ollama_client.stream_chat("llama3", []))

    @patch("common.services.ollama_client.httpx.stream")
    def test_sends_expected_method_url_and_payload(self, mock_stream):
        _wire_stream(mock_stream, _fake_stream_response([]))
        messages = [{"role": "user", "content": "hi"}]

        list(
            ollama_client.stream_chat("llama3", messages, options={"temperature": 0.2})
        )

        args, kwargs = mock_stream.call_args
        self.assertEqual(args[0], "POST")
        self.assertTrue(args[1].endswith("/api/chat"))
        self.assertEqual(
            kwargs["json"],
            {
                "model": "llama3",
                "messages": messages,
                "stream": True,
                "options": {"temperature": 0.2},
            },
        )

    @patch("common.services.ollama_client.httpx.stream")
    def test_omits_options_key_when_not_provided(self, mock_stream):
        _wire_stream(mock_stream, _fake_stream_response([]))

        list(ollama_client.stream_chat("llama3", []))

        _, kwargs = mock_stream.call_args
        self.assertNotIn("options", kwargs["json"])

    @override_settings(OLLAMA_BASE_URL="http://example.com:11434/")
    @patch("common.services.ollama_client.httpx.stream")
    def test_base_url_trailing_slash_is_stripped(self, mock_stream):
        _wire_stream(mock_stream, _fake_stream_response([]))

        list(ollama_client.stream_chat("llama3", []))

        args, _ = mock_stream.call_args
        self.assertEqual(args[1], "http://example.com:11434/api/chat")

    @patch("common.services.ollama_client.httpx.stream")
    def test_sends_top_level_think_when_given(self, mock_stream):
        """``think`` is the API-level switch for a thinking model's reasoning
        (Ollama 0.9+); the GM passes ``False`` instead of a ``/no_think``
        prompt prefix (issue #324)."""
        _wire_stream(mock_stream, _fake_stream_response([]))

        list(ollama_client.stream_chat("llama3", [], think=False))

        _, kwargs = mock_stream.call_args
        self.assertIs(kwargs["json"]["think"], False)

    @patch("common.services.ollama_client.httpx.stream")
    def test_omits_format_when_not_given(self, mock_stream):
        """The streamed chat never asks for structured output: a schema-
        constrained reply is wanted whole, so the GM's foe list (issue #345)
        and the character extraction (#344) both go through ``chat()`` with
        ``response_format``. Nothing may put a ``format`` on this payload.
        """
        _wire_stream(mock_stream, _fake_stream_response([]))
        list(ollama_client.stream_chat("llama3", []))
        _args, kwargs = mock_stream.call_args
        self.assertNotIn("format", kwargs["json"])

    @patch("common.services.ollama_client.httpx.stream")
    def test_omits_think_when_not_given(self, mock_stream):
        _wire_stream(mock_stream, _fake_stream_response([]))

        list(ollama_client.stream_chat("llama3", []))

        _, kwargs = mock_stream.call_args
        self.assertNotIn("think", kwargs["json"])


class ChatTest(TestCase):
    """The non-streaming call a structured extraction makes (issue #344)."""

    @patch("common.services.ollama_client.httpx.post")
    def test_sends_schema_as_format_and_returns_the_content(self, mock_post):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"message": {"content": '{"age": 22}'}, "done": True}
        mock_post.return_value = resp
        schema = {"type": "object", "properties": {"age": {"type": "integer"}}}
        messages = [{"role": "user", "content": "hi"}]

        content = ollama_client.chat(
            "llama3",
            messages,
            options={"temperature": 0},
            think=False,
            response_format=schema,
        )

        self.assertEqual(content, '{"age": 22}')
        args, kwargs = mock_post.call_args
        self.assertTrue(args[0].endswith("/api/chat"))
        self.assertEqual(
            kwargs["json"],
            {
                "model": "llama3",
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0},
                "think": False,
                "format": schema,
            },
        )
        self.assertIs(kwargs["timeout"], ollama_client.CHAT_TIMEOUT)

    @patch("common.services.ollama_client.httpx.post")
    def test_omits_optional_fields_and_tolerates_a_bare_reply(self, mock_post):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"done": True}
        mock_post.return_value = resp

        self.assertEqual(ollama_client.chat("llama3", []), "")

        _, kwargs = mock_post.call_args
        self.assertEqual(
            kwargs["json"], {"model": "llama3", "messages": [], "stream": False}
        )

    @patch("common.services.ollama_client.httpx.post")
    def test_raises_on_http_error_status(self, mock_post):
        resp = MagicMock()
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
        mock_post.return_value = resp

        with self.assertRaises(httpx.HTTPStatusError):
            ollama_client.chat("llama3", [])


class BaseUrlTest(TestCase):
    """Every call takes an explicit origin, so a Django-free caller can use it."""

    def test_reads_the_environment_with_a_local_default(self):
        self.assertEqual(
            ollama_client.base_url_from_environ({}), ollama_client.DEFAULT_BASE_URL
        )
        self.assertEqual(
            ollama_client.base_url_from_environ({"OLLAMA_BASE_URL": "  "}),
            ollama_client.DEFAULT_BASE_URL,
        )
        self.assertEqual(
            ollama_client.base_url_from_environ({"OLLAMA_BASE_URL": "http://box:1/"}),
            "http://box:1/",
        )

    @override_settings(OLLAMA_BASE_URL="http://configured:11434")
    @patch("common.services.ollama_client.httpx.post")
    def test_explicit_base_url_wins_over_settings(self, mock_post):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"message": {"content": "ok"}}
        mock_post.return_value = resp

        ollama_client.chat("llama3", [])
        self.assertEqual(
            mock_post.call_args.args[0], "http://configured:11434/api/chat"
        )

        ollama_client.chat("llama3", [], base_url="http://cli:11434/")
        self.assertEqual(mock_post.call_args.args[0], "http://cli:11434/api/chat")


class ListModelsTest(TestCase):
    """``/api/tags``, so the CLI can say 'not pulled' rather than 'not running'."""

    @patch("common.services.ollama_client.httpx.get")
    def test_returns_the_model_names(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "models": [{"model": "qwen3:32b"}, {"model": "nomic-embed-text:latest"}]
        }
        mock_get.return_value = resp

        names = ollama_client.list_models(base_url="http://box:11434")

        self.assertEqual(names, ["qwen3:32b", "nomic-embed-text:latest"])
        self.assertEqual(mock_get.call_args.args[0], "http://box:11434/api/tags")

    @patch("common.services.ollama_client.httpx.get")
    def test_a_reply_without_models_is_an_empty_list(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {}
        mock_get.return_value = resp

        self.assertEqual(ollama_client.list_models(), [])


class ChatStructuredTest(TestCase):
    """The schema-constrained call, and the one fallback both GMs share."""

    SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}}}

    @staticmethod
    def _build_messages(*, schema_in_prompt: bool = False) -> list[dict[str, str]]:
        content = "schema in prompt" if schema_in_prompt else "plain"
        return [{"role": "user", "content": content}]

    @patch("common.services.ollama_client.chat")
    def test_sends_the_schema_as_response_format(self, mock_chat):
        mock_chat.return_value = '{"name": "Wren"}'

        reply = ollama_client.chat_structured(
            "llama3",
            self._build_messages,
            self.SCHEMA,
            options={"temperature": 0},
            base_url="http://box:11434",
        )

        self.assertEqual(reply, '{"name": "Wren"}')
        mock_chat.assert_called_once_with(
            "llama3",
            [{"role": "user", "content": "plain"}],
            options={"temperature": 0},
            think=False,
            response_format=self.SCHEMA,
            base_url="http://box:11434",
        )

    @patch("common.services.ollama_client.chat")
    def test_a_runner_that_refuses_format_gets_the_schema_in_the_prompt(
        self, mock_chat
    ):
        refusal = httpx.HTTPStatusError(
            "structured output is unavailable",
            request=MagicMock(),
            response=MagicMock(
                status_code=ollama_client.STRUCTURED_OUTPUT_UNAVAILABLE_STATUS
            ),
        )
        mock_chat.side_effect = [refusal, '{"name": "Wren"}']

        reply = ollama_client.chat_structured(
            "llama3", self._build_messages, self.SCHEMA
        )

        self.assertEqual(reply, '{"name": "Wren"}')
        second = mock_chat.call_args_list[1]
        self.assertEqual(
            second.args[1], [{"role": "user", "content": "schema in prompt"}]
        )
        self.assertNotIn("response_format", second.kwargs)

    @patch("common.services.ollama_client.chat")
    def test_any_other_status_propagates(self, mock_chat):
        mock_chat.side_effect = httpx.HTTPStatusError(
            "down", request=MagicMock(), response=MagicMock(status_code=500)
        )

        with self.assertRaises(httpx.HTTPStatusError):
            ollama_client.chat_structured("llama3", self._build_messages, self.SCHEMA)
        self.assertEqual(mock_chat.call_count, 1)


class StreamDeadlineTest(TestCase):
    """``stop_at`` hangs up a stream that stalls after its first tokens (#887).

    httpx's read timeout counts from each read, so a stalled read could block
    past any deadline by as long as the stream had already run. These run a
    real local server, because only a real socket shows the hang-up.
    """

    def test_a_stalled_stream_is_cut_off_at_its_deadline(self):
        with (
            patch.object(ollama_client.httpx, "stream", REAL_STREAM),
            StallingOllama(["A "], stall_seconds=10, first_token_after=0.3) as server,
        ):
            started = time.monotonic()
            said: list[str] = []
            with self.assertRaises(ollama_client.StreamDeadlinePassed):
                for token in ollama_client.stream_chat(
                    "model",
                    [],
                    base_url=server.base_url,
                    timeout=httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0),
                    stop_at=started + 1.0,
                ):
                    said.append(token)
            returned_after = time.monotonic() - started
            deadline = started + 1.0
            for _wait in range(50):
                if server.hung_up_at:
                    break
                time.sleep(0.02)

        self.assertEqual(said, ["A "])
        # Without the cut-off this read waits out its 5 s timeout.
        self.assertLess(returned_after, 1.5)
        self.assertGreaterEqual(returned_after, 0.95)
        # Ollama is told to stop: the server sees the connection hung up.
        self.assertEqual(len(server.hung_up_at), 1)
        self.assertLess(server.hung_up_at[0] - deadline, 0.5)

    def test_a_stream_that_ends_in_time_is_left_alone(self):
        with (
            patch.object(ollama_client.httpx, "stream", REAL_STREAM),
            StallingOllama(["A ", "recap."], stall_seconds=0) as server,
        ):
            said = list(
                ollama_client.stream_chat(
                    "model", [], base_url=server.base_url, stop_at=time.monotonic() + 5
                )
            )
        self.assertEqual(said, ["A ", "recap."])
        self.assertTrue(server.finished.is_set())

    def test_a_transport_error_before_the_deadline_is_not_called_one(self):
        # A connection the server drops is still the error it was.
        with (
            patch.object(ollama_client.httpx, "stream", REAL_STREAM),
            StallingOllama(["A "], stall_seconds=10) as server,
        ):
            tokens = ollama_client.stream_chat(
                "model", [], base_url=server.base_url, stop_at=time.monotonic() + 30
            )
            self.assertEqual(next(tokens), "A ")
            server.drop_connections()
            with self.assertRaises(httpx.TransportError) as raised:
                next(tokens)
        self.assertNotIsInstance(raised.exception, ollama_client.StreamDeadlinePassed)


class DeadlineCutterCancelTest(TestCase):
    """A cancelled cutter never cuts, even with its callback already due."""

    def test_a_cut_off_after_cancel_touches_nothing(self):
        connection = MagicMock()
        response = MagicMock()
        response.extensions = {"network_stream": MagicMock()}
        response.extensions["network_stream"].get_extra_info.return_value = connection
        cutter = ollama_client._DeadlineCutter(response, time.monotonic() + 60)
        cutter.cancel()
        # What a timer callback already running when cancel() came would do.
        cutter._cut_off()
        connection.shutdown.assert_not_called()
        self.assertFalse(cutter.fired.is_set())

    def test_an_uncancelled_cut_off_shuts_the_socket(self):
        connection = MagicMock()
        response = MagicMock()
        response.extensions = {"network_stream": MagicMock()}
        response.extensions["network_stream"].get_extra_info.return_value = connection
        cutter = ollama_client._DeadlineCutter(response, time.monotonic() + 60)
        cutter._cut_off()
        cutter.cancel()
        connection.shutdown.assert_called_once()
        self.assertTrue(cutter.fired.is_set())
