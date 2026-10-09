"""Thin HTTP client for the Ollama REST API.

The one place this project speaks to Ollama over HTTP. The offline CLI used
to keep a second client of its own (the ``ollama`` package), which meant the
same conversation could go out in two different shapes -- and did: the CLI
had hand-rolled its own copy of the structured-output fallback below. Every
call here takes an explicit ``base_url`` so a caller without Django settings
can use it (issue #337).
"""

import json
import logging
import os
import socket
import threading
import time
from collections.abc import Callable, Generator, Mapping
from typing import Any

import httpx
from django.conf import settings

# One timeout for both calls: a structured extraction prefill over a long
# session is as slow as a streamed reply's first token.
CHAT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)

# Where Ollama listens, and the variable that says otherwise. Declared here
# rather than in settings.py so the offline CLI, which runs without Django,
# reads the same name and the same default (issue #337).
BASE_URL_ENV = "OLLAMA_BASE_URL"
DEFAULT_BASE_URL = "http://localhost:11434"

# Ollama's HTTP status when the loaded runner cannot constrain output (the
# MLX runner answers 501 "structured output is unavailable" to any ``format``,
# as of 0.33.3). :func:`chat_structured` then sends the schema in the prompt.
STRUCTURED_OUTPUT_UNAVAILABLE_STATUS = 501

logger = logging.getLogger(__name__)


def base_url_from_environ(environ: Mapping[str, str] | None = None) -> str:
    """The Ollama origin from ``OLLAMA_BASE_URL``, or the local default."""
    source = os.environ if environ is None else environ
    return source.get(BASE_URL_ENV, "").strip() or DEFAULT_BASE_URL


def _base_url(base_url: str | None) -> str:
    """The origin to call: the caller's, or ``settings.OLLAMA_BASE_URL``.

    Django callers pass nothing and get the configured setting. The offline
    CLI has no settings to read, so it passes its own -- which is why every
    call here takes an explicit ``base_url`` rather than reaching for Django
    unconditionally.
    """
    origin = settings.OLLAMA_BASE_URL if base_url is None else base_url
    return origin.rstrip("/")


def chat_payload(
    model: str,
    messages: list[dict[str, str]],
    *,
    stream: bool,
    options: dict[str, Any] | None,
    think: bool | None,
    response_format: dict[str, Any] | None,
) -> dict[str, Any]:
    """The ``/api/chat`` request body every call here sends.

    ``think`` maps to the top-level ``think`` field (Ollama 0.9+), which
    switches a thinking model's reasoning on or off server-side; ``None``
    leaves the field out so the model's default applies. ``response_format``
    is a JSON schema object sent as Ollama's ``format`` field (structured
    outputs): the server constrains generation so the reply's ``content`` is
    a JSON document matching the schema. ``None`` leaves the field out.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }
    if options:
        payload["options"] = options
    if think is not None:
        payload["think"] = think
    if response_format is not None:
        payload["format"] = response_format
    return payload


class StreamDeadlinePassed(TimeoutError):
    """A stream cut off at its ``stop_at`` while a read was waiting (#887)."""


class _DeadlineCutter:
    """Hangs up a streaming response's connection at a deadline (issue #887).

    httpx applies a read timeout to each read, not to the whole response, so
    a stream that stalls after its first tokens blocks one read for the whole
    read timeout from the moment of the stall, past any deadline the caller
    had. A timer on its own thread shuts the socket down at the deadline:
    ``shutdown`` (unlike ``close``) wakes a read blocked on the socket in
    another thread, which then fails, and the server sees the hang-up, which
    is how Ollama is told to stop.

    ``cancel`` and the cut-off take one lock, and a cancelled cutter never
    cuts: ``Timer.cancel`` cannot stop a callback already running, which
    could otherwise shut down the socket after the stream ended on its own
    (review of #887).
    """

    def __init__(self, response: httpx.Response, stop_at: float):
        self.fired = threading.Event()
        self._response = response
        self._lock = threading.Lock()
        self._done = False
        self._timer = threading.Timer(
            max(0.0, stop_at - time.monotonic()), self._cut_off
        )
        self._timer.daemon = True
        self._timer.start()

    def _cut_off(self) -> None:
        with self._lock:
            if self._done:
                return
            network_stream = self._response.extensions.get("network_stream")
            if network_stream is None:
                logger.warning(
                    "A stream past its deadline has no connection to cut off"
                )
                return
            connection = network_stream.get_extra_info("socket")
            if connection is None:
                logger.warning(
                    "A stream past its deadline exposes no socket to cut off"
                )
                return
            self.fired.set()
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError as already_closed:
                # The peer closed the socket as the deadline came; there is
                # nothing left to cut off.
                logger.debug(
                    "Deadline cut-off found the stream closed: %s", already_closed
                )

    def cancel(self) -> None:
        """Stop the timer for good; the stream ended on its own.

        Once this returns, the socket is never shut down by this cutter.
        """
        with self._lock:
            self._done = True
        self._timer.cancel()


def stream_chat(
    model: str,
    messages: list[dict[str, str]],
    *,
    options: dict[str, Any] | None = None,
    think: bool | None = None,
    base_url: str | None = None,
    timeout: httpx.Timeout | None = None,
    stop_at: float | None = None,
) -> Generator[str]:
    """Stream chat tokens from Ollama. Yields content strings as they arrive.

    See :func:`chat_payload` for ``think`` and :func:`_base_url` for
    ``base_url``. ``timeout`` replaces :data:`CHAT_TIMEOUT` for a call that
    must end sooner, such as a recap bounded by a turn's budget. Closing the
    generator closes the connection, which is how Ollama is told to stop.

    Args:
        stop_at: A ``time.monotonic()`` at which the connection is hung up
            even while a read is waiting on a stalled stream (issue #887).
            It is armed once the response's headers arrive; the wait for
            them is bounded by ``timeout``'s read.

    Raises:
        StreamDeadlinePassed: The stream was cut off at ``stop_at``.
    """
    payload = chat_payload(
        model,
        messages,
        stream=True,
        options=options,
        think=think,
        response_format=None,
    )

    with httpx.stream(
        "POST",
        f"{_base_url(base_url)}/api/chat",
        json=payload,
        timeout=CHAT_TIMEOUT if timeout is None else timeout,
    ) as resp:
        resp.raise_for_status()
        cutter = None if stop_at is None else _DeadlineCutter(resp, stop_at)
        try:
            for line in resp.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                content = data.get("message", {}).get("content", "")
                if content:
                    yield content
                if data.get("done"):
                    return
        except httpx.TransportError as cut:
            if cutter is not None and cutter.fired.is_set():
                raise StreamDeadlinePassed(
                    "the stream was cut off at its deadline"
                ) from cut
            raise
        finally:
            if cutter is not None:
                cutter.cancel()


def chat(
    model: str,
    messages: list[dict[str, str]],
    *,
    options: dict[str, Any] | None = None,
    think: bool | None = None,
    response_format: dict[str, Any] | None = None,
    base_url: str | None = None,
) -> str:
    """One non-streaming chat call; returns the reply's ``content``.

    Used where the whole answer is needed before anything can happen with
    it -- a structured extraction (issue #344) is parsed as JSON, not shown
    token by token. See :func:`chat_payload` for ``think`` and
    ``response_format``, and :func:`_base_url` for ``base_url``.
    """
    payload = chat_payload(
        model,
        messages,
        stream=False,
        options=options,
        think=think,
        response_format=response_format,
    )
    resp = httpx.post(
        f"{_base_url(base_url)}/api/chat", json=payload, timeout=CHAT_TIMEOUT
    )
    resp.raise_for_status()
    return str(resp.json().get("message", {}).get("content", ""))


def list_models(*, base_url: str | None = None) -> list[str]:
    """Every model name Ollama reports from ``GET /api/tags``.

    Used to tell "the server is down" apart from "the server is up but has
    not pulled this model", which are different things to tell a person.

    Args:
        base_url: See :func:`_base_url`.

    Returns:
        The model names, e.g. ``["qwen3:32b", "nomic-embed-text:latest"]``.

    Raises:
        httpx.HTTPError: On a transport failure or non-2xx response.
    """
    response = httpx.get(f"{_base_url(base_url)}/api/tags", timeout=CHAT_TIMEOUT)
    response.raise_for_status()
    entries = response.json().get("models", [])
    return [str(entry.get("model", "")) for entry in entries]


def chat_structured(
    model: str,
    build_messages: Callable[..., list[dict[str, str]]],
    schema: dict[str, Any],
    *,
    options: dict[str, Any] | None = None,
    base_url: str | None = None,
) -> str:
    """Ask the model for schema-constrained JSON, and return its raw reply.

    Non-streaming, reasoning off, ``format`` = ``schema``. A runner that
    refuses ``format`` (see
    :data:`STRUCTURED_OUTPUT_UNAVAILABLE_STATUS`) is asked once more with the
    schema written into the prompt instead. This is the one place that
    fallback lives: the web GM's ``/character`` (issue #344), its ledger's
    per-turn intent read (issue #356) and the offline CLI's ``/character``
    all go through it, so they cannot drift apart.

    Args:
        model: The model name as Ollama knows it.
        build_messages: Builds the call's messages; called with
            ``schema_in_prompt=True`` on the fallback path.
        schema: The JSON schema to constrain the answer to.
        options: The ``options`` object for the call.
        base_url: See :func:`_base_url`.

    Returns:
        The model's reply, unparsed -- each caller owns how it reads and how
        it reports a malformed one.

    Raises:
        httpx.HTTPStatusError: On any non-2xx other than the runner saying it
            cannot constrain output.
    """
    try:
        return chat(
            model,
            build_messages(),
            options=options,
            think=False,
            response_format=schema,
            base_url=base_url,
        )
    except httpx.HTTPStatusError as error:
        if error.response.status_code != STRUCTURED_OUTPUT_UNAVAILABLE_STATUS:
            raise
        logger.info(
            "Model %s takes no structured output; sending the schema in the prompt",
            model,
        )
        return chat(
            model,
            build_messages(schema_in_prompt=True),
            options=options,
            think=False,
            base_url=base_url,
        )
