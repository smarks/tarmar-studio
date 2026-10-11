"""Tests for the Ollama HTTP client (issue #103).

``common/services/*`` was excluded from the coverage gate, so
``stream_chat``'s line-by-line JSONL parsing -- content extraction, blank
line skipping, and early exit on a ``done`` frame -- ran only against a real
Ollama server and was never covered by the test suite (every call site mocks
``ollama_client.stream_chat`` itself). These tests exercise that parsing
directly against a mocked ``open_stream``, plus the client's error and
timeout propagation, now that the exclusion has been lifted.
"""

import json
import threading
import time
from unittest.mock import MagicMock, patch

import httpx
from django.test import TestCase, override_settings

from common.services import ollama_client
from common.test_helpers import (
    ACT_ONCE_SET_SECONDS,
    STAND_IN_HOLD_SECONDS,
    STAND_IN_TIMEOUT,
    HandFiredDeadlines,
    StallingOllama,
)

# The suite's conftest replaces open_stream for every test; the deadline
# tests below need a real socket (issue #887), so they take the real one,
# captured at import, before any fixture runs.
REAL_OPEN_STREAM = ollama_client.open_stream


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
    """Make the ``open_stream(...)`` mock behave as a context manager.

    A bare MagicMock's ``__exit__`` returns a truthy MagicMock, which would
    suppress any exception raised inside the ``with`` block -- so it must be
    pinned to a falsy value explicitly.
    """
    mock_stream.return_value.__enter__.return_value = resp
    mock_stream.return_value.__exit__.return_value = False


class StreamChatTest(TestCase):
    @patch("common.services.ollama_client.open_stream")
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

    @patch("common.services.ollama_client.open_stream")
    def test_stops_at_done_without_consuming_further_lines(self, mock_stream):
        lines = [
            json.dumps({"message": {"content": "a"}, "done": True}),
            json.dumps({"message": {"content": "should never appear"}}),
        ]
        _wire_stream(mock_stream, _fake_stream_response(lines))

        result = list(ollama_client.stream_chat("llama3", []))

        self.assertEqual(result, ["a"])

    @patch("common.services.ollama_client.open_stream")
    def test_raises_on_http_error_status(self, mock_stream):
        _wire_stream(mock_stream, _fake_stream_response([], http_error=True))

        with self.assertRaises(httpx.HTTPStatusError):
            list(ollama_client.stream_chat("llama3", []))

    @patch("common.services.ollama_client.open_stream")
    def test_propagates_timeout(self, mock_stream):
        mock_stream.side_effect = httpx.TimeoutException("timed out")

        with self.assertRaises(httpx.TimeoutException):
            list(ollama_client.stream_chat("llama3", []))

    @patch("common.services.ollama_client.open_stream")
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

    @patch("common.services.ollama_client.open_stream")
    def test_omits_options_key_when_not_provided(self, mock_stream):
        _wire_stream(mock_stream, _fake_stream_response([]))

        list(ollama_client.stream_chat("llama3", []))

        _, kwargs = mock_stream.call_args
        self.assertNotIn("options", kwargs["json"])

    @override_settings(OLLAMA_BASE_URL="http://example.com:11434/")
    @patch("common.services.ollama_client.open_stream")
    def test_base_url_trailing_slash_is_stripped(self, mock_stream):
        _wire_stream(mock_stream, _fake_stream_response([]))

        list(ollama_client.stream_chat("llama3", []))

        args, _ = mock_stream.call_args
        self.assertEqual(args[1], "http://example.com:11434/api/chat")

    @patch("common.services.ollama_client.open_stream")
    def test_sends_top_level_think_when_given(self, mock_stream):
        """``think`` is the API-level switch for a thinking model's reasoning
        (Ollama 0.9+); the GM passes ``False`` instead of a ``/no_think``
        prompt prefix (issue #324)."""
        _wire_stream(mock_stream, _fake_stream_response([]))

        list(ollama_client.stream_chat("llama3", [], think=False))

        _, kwargs = mock_stream.call_args
        self.assertIs(kwargs["json"]["think"], False)

    @patch("common.services.ollama_client.open_stream")
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

    @patch("common.services.ollama_client.open_stream")
    def test_omits_think_when_not_given(self, mock_stream):
        _wire_stream(mock_stream, _fake_stream_response([]))

        list(ollama_client.stream_chat("llama3", []))

        _, kwargs = mock_stream.call_args
        self.assertNotIn("think", kwargs["json"])


def _fake_chat_response(reply: dict, *, http_error: bool = False) -> MagicMock:
    """A fake non-streaming answer, read over ``open_stream`` as ``chat`` reads it."""
    resp = _fake_stream_response([], http_error=http_error)
    resp.json.return_value = reply
    return resp


class ChatTest(TestCase):
    """The non-streaming call a structured extraction makes (issue #344).

    Read over a streamed response since issue #936, so its connection can be hung
    up at a deadline while the answer is read.
    """

    @patch("common.services.ollama_client.open_stream")
    def test_sends_schema_as_format_and_returns_the_content(self, mock_stream):
        resp = _fake_chat_response(
            {"message": {"content": '{"age": 22}'}, "done": True}
        )
        _wire_stream(mock_stream, resp)
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
        args, kwargs = mock_stream.call_args
        self.assertEqual(args[0], "POST")
        self.assertTrue(args[1].endswith("/api/chat"))
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
        resp.read.assert_called_once_with()

    @patch("common.services.ollama_client.open_stream")
    def test_omits_optional_fields_and_tolerates_a_bare_reply(self, mock_stream):
        _wire_stream(mock_stream, _fake_chat_response({"done": True}))

        self.assertEqual(ollama_client.chat("llama3", []), "")

        _, kwargs = mock_stream.call_args
        self.assertEqual(
            kwargs["json"], {"model": "llama3", "messages": [], "stream": False}
        )

    @patch("common.services.ollama_client.open_stream")
    def test_raises_on_http_error_status(self, mock_stream):
        _wire_stream(mock_stream, _fake_chat_response({}, http_error=True))

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
    @patch("common.services.ollama_client.open_stream")
    def test_explicit_base_url_wins_over_settings(self, mock_stream):
        _wire_stream(mock_stream, _fake_chat_response({"message": {"content": "ok"}}))

        ollama_client.chat("llama3", [])
        self.assertEqual(
            mock_stream.call_args.args[1], "http://configured:11434/api/chat"
        )

        ollama_client.chat("llama3", [], base_url="http://cli:11434/")
        self.assertEqual(mock_stream.call_args.args[1], "http://cli:11434/api/chat")


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
            stop_at=None,
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
    real local server, because only a real socket shows the hang-up. The
    deadline passes when the test says (issue #955), once the first token is
    in, rather than racing the stand-in on a loaded machine.
    """

    def test_a_stalled_stream_is_cut_off_at_its_deadline(self):
        hang_up = ollama_client.HangUp()
        token_read = threading.Event()
        with (
            patch.object(ollama_client, "open_stream", REAL_OPEN_STREAM),
            StallingOllama(["A "]) as server,
            HandFiredDeadlines() as deadlines,
        ):
            deadlines.pass_deadline_once(token_read)
            started = time.monotonic()
            said: list[str] = []
            with self.assertRaises(ollama_client.StreamDeadlinePassed):
                for token in ollama_client.stream_chat(
                    "model",
                    [],
                    base_url=server.base_url,
                    timeout=STAND_IN_TIMEOUT,
                    stop_at=started + 60,
                    hang_up=hang_up,
                ):
                    said.append(token)
                    token_read.set()
            self.assertTrue(server.closed.wait(5))

        self.assertEqual(said, ["A "])
        # The timer was armed for the time left, to hang this call up.
        (timer,) = deadlines.timers
        self.assertAlmostEqual(timer.due, started + 60, delta=1.0)
        self.assertTrue(timer.cancelled)
        self.assertTrue(hang_up.at_deadline)
        # Ollama is told to stop: the server sees the stalled stream hung up,
        # where without the cut-off the read would wait out its 30 s timeout.
        self.assertEqual(server.hang_ups, ["stall"])

    def test_a_stream_that_ends_in_time_is_left_alone(self):
        with (
            patch.object(ollama_client, "open_stream", REAL_OPEN_STREAM),
            StallingOllama(["A ", "recap."], stall=False) as server,
        ):
            said = list(
                ollama_client.stream_chat(
                    "model", [], base_url=server.base_url, stop_at=time.monotonic() + 60
                )
            )
        self.assertEqual(said, ["A ", "recap."])
        self.assertTrue(server.finished.is_set())

    def test_a_transport_error_before_the_deadline_is_not_called_one(self):
        # A connection the server drops is still the error it was.
        with (
            patch.object(ollama_client, "open_stream", REAL_OPEN_STREAM),
            StallingOllama(["A "]) as server,
        ):
            tokens = ollama_client.stream_chat(
                "model", [], base_url=server.base_url, stop_at=time.monotonic() + 60
            )
            self.assertEqual(next(tokens), "A ")
            server.drop_connections()
            with self.assertRaises(httpx.TransportError) as raised:
                next(tokens)
        self.assertNotIsInstance(raised.exception, ollama_client.StreamDeadlinePassed)


class StreamHangUpTest(TestCase):
    """A :class:`HangUp` ends a stalled read from another thread (#904).

    A turn that gives up on a call closes its reader, but a reader blocked in
    a read on a stalled stream would not notice until the read timeout, and
    the call held the model all that while.
    """

    def test_hanging_up_wakes_a_read_waiting_on_a_stalled_stream(self):
        hang_up = ollama_client.HangUp()
        with (
            patch.object(ollama_client, "open_stream", REAL_OPEN_STREAM),
            StallingOllama(["A "]) as server,
        ):
            tokens = ollama_client.stream_chat(
                "model",
                [],
                base_url=server.base_url,
                timeout=STAND_IN_TIMEOUT,
                hang_up=hang_up,
            )
            self.assertEqual(next(tokens), "A ")
            # From another thread, whether the next read has begun or not:
            # either way the hang-up is what ends it.
            asking = threading.Thread(target=hang_up.hang_up)
            asking.start()
            with self.assertRaises(ollama_client.StreamHungUp):
                next(tokens)
            asking.join(ACT_ONCE_SET_SECONDS)
            self.assertTrue(server.closed.wait(5))

        # Without the hang-up this read waits out its 30 s timeout, and the
        # server, having held the stream for nothing, records no hang-up.
        self.assertEqual(server.hang_ups, ["stall"])
        self.assertFalse(hang_up.at_deadline)

    def test_a_hang_up_asked_before_the_headers_is_carried_out_on_them(self):
        connection = MagicMock()
        response = MagicMock()
        response.extensions = {"network_stream": MagicMock()}
        response.extensions["network_stream"].get_extra_info.return_value = connection
        hang_up = ollama_client.HangUp()
        hang_up.hang_up()
        connection.shutdown.assert_not_called()
        hang_up._arm(response)
        connection.shutdown.assert_called_once()
        self.assertTrue(hang_up.fired.is_set())


def _connected_stream(connection: object) -> MagicMock:
    """A network stream as httpcore's connect trace event hands it over."""
    network_stream = MagicMock()
    network_stream.get_extra_info.return_value = connection
    return network_stream


class HangUpBeforeTheHeadersTest(TestCase):
    """A hang-up asked before the first token cuts the call at once (#935).

    Ollama sends its headers with the first token, so a hang-up that waited
    for them held the model through the prefill, and through any wait behind
    another call. The socket is taken from httpcore's ``trace`` extension
    when the connection is made instead.
    """

    TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

    def test_a_hang_up_during_the_prefill_cuts_the_call_at_once(self):
        hang_up = ollama_client.HangUp()
        outcome: list[object] = []
        ended = threading.Event()
        with (
            patch.object(ollama_client, "open_stream", REAL_OPEN_STREAM),
            StallingOllama(["A "], prefill=True) as server,
        ):
            tokens = ollama_client.stream_chat(
                "model",
                [],
                base_url=server.base_url,
                timeout=STAND_IN_TIMEOUT,
                hang_up=hang_up,
            )

            def read() -> None:
                try:
                    outcome.extend(tokens)
                except Exception as error:  # handed to the assertions below
                    outcome.append(error)
                ended.set()

            threading.Thread(target=read, daemon=True).start()
            self.assertTrue(server.requested.wait(5))
            hang_up.hang_up()
            self.assertTrue(ended.wait(STAND_IN_HOLD_SECONDS))
            self.assertTrue(server.closed.wait(5))

        # Before #935 the hang-up waited for the headers, which the stand-in
        # holds back until it is hung up on: the server saw no hang-up, and
        # the call ended only when it gave up.
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], ollama_client.StreamHungUp)
        self.assertEqual(server.hang_ups, ["prefill"])
        self.assertFalse(server.responding.is_set())
        self.assertFalse(hang_up.at_deadline)

    def test_a_hang_up_asked_before_the_call_sends_no_request(self):
        hang_up = ollama_client.HangUp()
        hang_up.hang_up()
        with (
            patch.object(ollama_client, "open_stream", REAL_OPEN_STREAM),
            StallingOllama(["A "], prefill=True) as server,
        ):
            with self.assertRaises(ollama_client.StreamHungUp):
                list(
                    ollama_client.stream_chat(
                        "model",
                        [],
                        base_url=server.base_url,
                        timeout=STAND_IN_TIMEOUT,
                        hang_up=hang_up,
                    )
                )
            # The connection's handler is done, so had a request gone out it
            # would have been read by now.
            self.assertTrue(server.closed.wait(5))

        # The socket is shut at connect, before the request is written, so
        # the model is never asked at all.
        self.assertFalse(server.requested.is_set())
        self.assertEqual(server.hang_ups, [])

    def test_the_socket_is_taken_at_connect(self):
        connection = MagicMock()
        hang_up = ollama_client.HangUp()
        bound = ollama_client._CallBound(timeout=self.TIMEOUT, hang_up=hang_up)
        bound.trace(
            "connection.connect_tcp.complete",
            {"return_value": _connected_stream(connection)},
        )
        bound.trace("http11.send_request_headers.started", {"request": MagicMock()})
        connection.shutdown.assert_not_called()
        hang_up.hang_up()
        connection.shutdown.assert_called_once()
        # The response's stream, when the headers come, changes nothing.
        later = MagicMock()
        response = MagicMock()
        response.extensions = {"network_stream": _connected_stream(later)}
        bound.arm(response)
        later.shutdown.assert_not_called()

    def test_a_hang_up_asked_before_the_connect_is_carried_out_on_it(self):
        connection = MagicMock()
        hang_up = ollama_client.HangUp()
        bound = ollama_client._CallBound(timeout=self.TIMEOUT, hang_up=hang_up)
        hang_up.hang_up()
        with self.assertLogs("common.services.ollama_client", "DEBUG") as logged:
            bound.trace(
                "connection.connect_tcp.complete",
                {"return_value": _connected_stream(connection)},
            )
        connection.shutdown.assert_called_once()
        self.assertTrue(hang_up.fired.is_set())
        self.assertIn("from connect", logged.output[0])

    def test_a_reused_connection_is_armed_on_the_headers(self):
        # httpcore hands over no socket for a request on a pooled connection
        # until its response begins; the hang-up waits for that, as before.
        hang_up = ollama_client.HangUp()
        bound = ollama_client._CallBound(timeout=self.TIMEOUT, hang_up=hang_up)
        hang_up.hang_up()
        with self.assertLogs("common.services.ollama_client", "DEBUG") as logged:
            bound.trace("http11.send_request_headers.started", {"request": MagicMock()})
            connection = MagicMock()
            response = MagicMock()
            response.extensions = {"network_stream": _connected_stream(connection)}
            bound.arm(response)
        connection.shutdown.assert_called_once()
        self.assertIn("No socket was taken", logged.output[0])
        self.assertIn("from the response headers", logged.output[1])

    def test_a_transport_with_no_socket_at_connect_waits_for_the_headers(self):
        hang_up = ollama_client.HangUp()
        bound = ollama_client._CallBound(timeout=self.TIMEOUT, hang_up=hang_up)
        hang_up.hang_up()
        with self.assertLogs("common.services.ollama_client", "DEBUG") as logged:
            bound.trace(
                "connection.connect_tcp.complete",
                {"return_value": _connected_stream(None)},
            )
        self.assertFalse(hang_up.fired.is_set())
        self.assertIn("no socket at connect", logged.output[0])
        # Nor any with the response: the hang-up says it has nothing to cut.
        response = MagicMock()
        response.extensions = {}
        with self.assertLogs("common.services.ollama_client", "WARNING") as warned:
            bound.arm(response)
        self.assertFalse(hang_up.fired.is_set())
        self.assertIn("no socket to shut down", warned.output[0])

    def test_a_call_with_no_hang_up_ignores_the_trace(self):
        network_stream = _connected_stream(MagicMock())
        bound = ollama_client._CallBound(timeout=self.TIMEOUT, hang_up=None)
        bound.trace("connection.connect_tcp.complete", {"return_value": network_stream})
        network_stream.get_extra_info.assert_not_called()

    @patch("common.services.ollama_client.open_stream")
    def test_both_calls_send_the_trace(self, mock_stream):
        _wire_stream(mock_stream, _fake_chat_response({"message": {"content": ""}}))
        list(ollama_client.stream_chat("m", [], stop_at=time.monotonic() + 5))
        streamed = mock_stream.call_args.kwargs["extensions"]["trace"]
        ollama_client.chat("m", [], stop_at=time.monotonic() + 5)
        answered = mock_stream.call_args.kwargs["extensions"]["trace"]
        for trace in (streamed, answered):
            self.assertIsInstance(trace.__self__, ollama_client._CallBound)


class StreamDeadlineBeforeTheResponseTest(TestCase):
    """``stop_at`` also bounds the wait for the first token (#904)."""

    @patch("common.services.ollama_client.open_stream")
    def test_the_read_timeout_is_held_to_the_time_left(self, mock_stream):
        _wire_stream(mock_stream, _fake_stream_response([]))
        list(ollama_client.stream_chat("m", [], stop_at=time.monotonic() + 2))
        read = mock_stream.call_args.kwargs["timeout"].read
        self.assertLessEqual(read, 2)
        self.assertGreater(read, 1)

    @patch("common.services.ollama_client.open_stream")
    def test_a_read_timeout_at_the_deadline_is_the_deadline(self, mock_stream):
        mock_stream.side_effect = httpx.ReadTimeout("no first token")
        with self.assertRaises(ollama_client.StreamDeadlinePassed):
            list(ollama_client.stream_chat("m", [], stop_at=time.monotonic() + 2))

    def test_a_capped_read_that_timed_out_is_the_deadline_whoever_hangs_up(self):
        # Issue #955: the read held to the deadline times out, and a turn
        # giving up at that same deadline hangs up before the error is
        # explained. The read's own bound ended the call, not the turn.
        hang_up, _connection = HangUpFinishTest.armed()
        bound = ollama_client._CallBound(
            timeout=ollama_client.CHAT_TIMEOUT,
            hang_up=hang_up,
            read_held_to_deadline=True,
        )
        hang_up.hang_up()
        explained = bound.explain(httpx.ReadTimeout("held to the deadline"))
        self.assertIsInstance(explained, ollama_client.StreamDeadlinePassed)

    def test_a_read_a_hang_up_ended_is_the_hang_up(self):
        # A hang-up ends a read with the connection's end, never a timeout.
        hang_up, _connection = HangUpFinishTest.armed()
        bound = ollama_client._CallBound(
            timeout=ollama_client.CHAT_TIMEOUT,
            hang_up=hang_up,
            read_held_to_deadline=True,
        )
        hang_up.hang_up()
        explained = bound.explain(httpx.RemoteProtocolError("peer closed"))
        self.assertIsInstance(explained, ollama_client.StreamHungUp)

    @patch("common.services.ollama_client.open_stream")
    def test_a_read_timeout_shorter_than_the_time_left_stays_one(self, mock_stream):
        # The caller's own read bound ran out first; the deadline did not.
        mock_stream.side_effect = httpx.ReadTimeout("stalled")
        with self.assertRaises(httpx.ReadTimeout) as raised:
            list(
                ollama_client.stream_chat(
                    "m",
                    [],
                    timeout=httpx.Timeout(connect=1.0, read=1.0, write=1.0, pool=1.0),
                    stop_at=time.monotonic() + 60,
                )
            )
        self.assertNotIsInstance(raised.exception, ollama_client.StreamDeadlinePassed)

    @patch("common.services.ollama_client.open_stream")
    def test_a_deadline_already_passed_sends_nothing(self, mock_stream):
        with self.assertRaises(ollama_client.StreamDeadlinePassed):
            list(ollama_client.stream_chat("m", [], stop_at=time.monotonic() - 1))
        mock_stream.assert_not_called()


class ChatDeadlineTest(TestCase):
    """``chat`` takes a ``stop_at`` too, and hangs up there (issue #936).

    ``/fight``'s foe calls were bounded only by the 300 s read timeout. These
    run a real local server, because only a real socket shows the hang-up.
    Two things end a call at its deadline: the timer's hang-up, and the read
    timeout held to the time left. Each is tested alone (issue #955); with
    both live, which came first was a matter of scheduling.
    """

    def test_an_answer_that_never_begins_is_hung_up_at_the_deadline(self):
        # Ollama sends a non-streaming answer's headers once it is written.
        with (
            patch.object(ollama_client, "open_stream", REAL_OPEN_STREAM),
            patch.object(ollama_client, "CHAT_TIMEOUT", STAND_IN_TIMEOUT),
            StallingOllama([], prefill=True) as server,
            HandFiredDeadlines() as deadlines,
        ):
            deadlines.pass_deadline_once(server.requested)
            with self.assertRaises(ollama_client.StreamDeadlinePassed) as raised:
                ollama_client.chat(
                    "model",
                    [],
                    base_url=server.base_url,
                    stop_at=time.monotonic() + 60,
                )
            self.assertTrue(server.closed.wait(5))

        # Hung up, not timed out: the read waits 30 s, and the server sees
        # the hang-up while it is still writing the answer.
        self.assertNotIsInstance(raised.exception.__cause__, httpx.ReadTimeout)
        self.assertEqual(server.hang_ups, ["prefill"])
        self.assertFalse(server.responding.is_set())

    def test_an_answer_that_never_begins_times_out_at_the_deadline(self):
        # The read timeout is held to the time left, so a call whose hang-up
        # never fires (a transport with no socket to shut) still ends there.
        with (
            patch.object(ollama_client, "open_stream", REAL_OPEN_STREAM),
            patch.object(ollama_client, "CHAT_TIMEOUT", STAND_IN_TIMEOUT),
            StallingOllama([], prefill=True) as server,
            HandFiredDeadlines(),
        ):
            stop_at = time.monotonic() + 1.0
            with self.assertRaises(ollama_client.StreamDeadlinePassed) as raised:
                ollama_client.chat(
                    "model", [], base_url=server.base_url, stop_at=stop_at
                )
            returned = time.monotonic()
            self.assertTrue(server.closed.wait(5))

        self.assertIsInstance(raised.exception.__cause__, httpx.ReadTimeout)
        self.assertGreaterEqual(returned, stop_at)
        self.assertEqual(server.hang_ups, ["prefill"])

    def test_an_answer_that_stalls_once_begun_is_hung_up_at_the_deadline(self):
        with (
            patch.object(ollama_client, "open_stream", REAL_OPEN_STREAM),
            patch.object(ollama_client, "CHAT_TIMEOUT", STAND_IN_TIMEOUT),
            StallingOllama(["{"]) as server,
            HandFiredDeadlines() as deadlines,
        ):
            deadlines.pass_deadline_once(server.responding)
            with self.assertRaises(ollama_client.StreamDeadlinePassed):
                ollama_client.chat(
                    "model",
                    [],
                    base_url=server.base_url,
                    stop_at=time.monotonic() + 60,
                )
            self.assertTrue(server.closed.wait(5))

        self.assertEqual(server.hang_ups, ["stall"])

    def test_an_answer_in_time_is_left_alone(self):
        with (
            patch.object(ollama_client, "open_stream", REAL_OPEN_STREAM),
            StallingOllama([], stall=False) as server,
        ):
            reply = ollama_client.chat(
                "model", [], base_url=server.base_url, stop_at=time.monotonic() + 60
            )
            self.assertTrue(server.closed.wait(5))
        self.assertEqual(reply, "")
        self.assertTrue(server.finished.is_set())
        self.assertEqual(server.hang_ups, [])

    @patch("common.services.ollama_client.open_stream")
    def test_a_deadline_already_passed_sends_nothing(self, mock_stream):
        with self.assertRaises(ollama_client.StreamDeadlinePassed):
            ollama_client.chat("m", [], stop_at=time.monotonic() - 1)
        mock_stream.assert_not_called()

    @patch("common.services.ollama_client.chat")
    def test_the_structured_fallback_shares_the_one_deadline(self, mock_chat):
        refusal = httpx.HTTPStatusError(
            "structured output is unavailable",
            request=MagicMock(),
            response=MagicMock(
                status_code=ollama_client.STRUCTURED_OUTPUT_UNAVAILABLE_STATUS
            ),
        )
        mock_chat.side_effect = [refusal, "{}"]

        ollama_client.chat_structured(
            "m", ChatStructuredTest._build_messages, {}, stop_at=1234.5
        )

        self.assertEqual(
            [call.kwargs["stop_at"] for call in mock_chat.call_args_list],
            [1234.5, 1234.5],
        )


class HangUpFinishTest(TestCase):
    """A finished hang-up never cuts, even with its deadline already due."""

    @staticmethod
    def armed() -> tuple[ollama_client.HangUp, MagicMock]:
        """A hang-up armed with a stand-in socket, as at the response headers."""
        connection = MagicMock()
        response = MagicMock()
        response.extensions = {"network_stream": MagicMock()}
        response.extensions["network_stream"].get_extra_info.return_value = connection
        hang_up = ollama_client.HangUp()
        hang_up._arm(response)
        return hang_up, connection

    def test_a_hang_up_after_the_stream_finished_touches_nothing(self):
        hang_up, connection = self.armed()
        hang_up._finish()
        # What a timer callback already running when the stream ended would do.
        hang_up._deadline_reached()
        hang_up.hang_up()
        connection.shutdown.assert_not_called()
        self.assertFalse(hang_up.fired.is_set())

    def test_a_hang_up_before_the_stream_finished_shuts_the_socket(self):
        hang_up, connection = self.armed()
        hang_up._deadline_reached()
        hang_up._finish()
        connection.shutdown.assert_called_once()
        self.assertTrue(hang_up.fired.is_set())
        self.assertTrue(hang_up.at_deadline)
