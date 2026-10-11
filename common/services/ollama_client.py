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
from collections.abc import Callable, Generator, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
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

# httpcore's ``trace`` events (httpcore 1.0) that carry a newly connected
# network stream as their ``return_value``: the earliest a call's socket can
# be had. A request sent over a reused pooled connection fires neither, and
# httpcore hands its stream over only with the response (issue #935).
CONNECTED_TRACE_EVENTS = frozenset(
    {
        "connection.connect_tcp.complete",
        "connection.connect_unix_socket.complete",
    }
)
# Fired as a request's headers go out, over a fresh connection or a reused one.
REQUEST_SENT_TRACE_EVENT = "http11.send_request_headers.started"

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
    """A call cut off at its ``stop_at`` (#887).

    Raised by :func:`stream_chat` and, since issue #936, by :func:`chat`,
    which reads its one JSON answer over the same kind of connection.
    """


class StreamHungUp(ConnectionError):
    """A stream its caller hung up on with :meth:`HangUp.hang_up` (issue #904)."""


class HangUp:
    """Hangs up one call's connection from another thread.

    httpx applies a read timeout to each read, not to the whole response, so
    a stream that stalls after its first tokens blocks one read for the whole
    read timeout from the moment of the stall, past any deadline the caller
    had (issue #887), and a caller that gives up on it cannot wake the thread
    blocked in that read either (issue #904). Shutting the socket down
    (unlike closing it) wakes a read blocked on it in another thread, which
    then fails, and the server sees the hang-up, which is how Ollama is told
    to stop.

    :func:`stream_chat` and :func:`chat` (which reads its one answer over a
    streamed response, issue #936) arm it with the call's socket as soon as
    the transport hands one over, and finish it when the response ends.
    Ollama sends its headers only with the first token, so the socket is
    taken when the connection is made (issue #935): a call hung up while the
    prompt is prefilled, or while it waits behind another call, is cut off
    at once rather than when the first token comes. A request sent over a
    reused connection offers no socket until its response headers, and is
    armed then. :meth:`hang_up` may be called from any thread at any time:
    before the call is armed it is remembered and carried out the moment it
    is, and once the stream has ended it does nothing. A request and the
    stream's end take one lock, so a stream that ended on its own is never
    cut afterwards (review of #887). One instance serves one call.

    Attributes:
        fired: Set once the socket has been shut down.
        at_deadline: Whether the hang-up was the call's ``stop_at`` passing,
            rather than a caller asking.
    """

    def __init__(self) -> None:
        self.fired = threading.Event()
        self.at_deadline = False
        self._lock = threading.Lock()
        self._armed = False
        self._connection: socket.socket | None = None
        self._wanted = False
        self._done = False

    def hang_up(self) -> None:
        """Hang up now, or as soon as the response begins."""
        self._request(at_deadline=False)

    def _deadline_reached(self) -> None:
        self._request(at_deadline=True)

    def _request(self, *, at_deadline: bool) -> None:
        with self._lock:
            if self._done or self._wanted:
                return
            self._wanted = True
            self.at_deadline = at_deadline
            if self._armed:
                self._shut_down()

    def _connected(self, network_stream: Any) -> None:
        """Take a new connection's socket, before the request goes out."""
        connection = network_stream.get_extra_info("socket")
        if connection is None:
            logger.debug(
                "The transport offers no socket at connect; the hang-up waits "
                "for the response headers"
            )
            return
        self._take(connection, "connect")

    def _request_sent(self) -> None:
        """Note a request that went out with no socket taken yet.

        That is a reused connection, or a new one whose transport offered no
        socket at connect.
        """
        with self._lock:
            armed = self._armed
        if not armed:
            logger.debug(
                "No socket was taken before the request went out; the hang-up "
                "waits for the response headers"
            )

    def _arm(self, response: httpx.Response) -> None:
        """Take the response's socket, unless one was taken at connect."""
        network_stream = response.extensions.get("network_stream")
        connection = (
            None if network_stream is None else network_stream.get_extra_info("socket")
        )
        self._take(connection, "the response headers")

    def _take(self, connection: socket.socket | None, stage: str) -> None:
        """Arm with ``connection``; hang up at once if already asked to.

        The first socket taken is kept: the response's, when one was taken
        at connect, is the same socket.
        """
        with self._lock:
            if self._armed:
                return
            self._armed = True
            self._connection = connection
            logger.debug("The hang-up holds the call's socket from %s", stage)
            if self._wanted and not self._done:
                self._shut_down()

    def _shut_down(self) -> None:
        """Shut the socket down; the caller holds the lock."""
        if self._connection is None:
            logger.warning("A stream to hang up exposes no socket to shut down")
            return
        self.fired.set()
        try:
            self._connection.shutdown(socket.SHUT_RDWR)
        except OSError as already_closed:
            # The peer closed the socket as the hang-up came; there is
            # nothing left to cut off.
            logger.debug("Hang-up found the stream closed: %s", already_closed)

    def _finish(self) -> None:
        """The stream ended; from now on nothing hangs it up."""
        with self._lock:
            self._done = True


@dataclass
class _CallBound:
    """One call's ``stop_at`` and hang-up, set up before the request is sent.

    Shared by :func:`stream_chat` and :func:`chat` so both hold the model to
    the same bound (issue #936).

    Attributes:
        timeout: The timeout to send with: the caller's, with the read held
            to the time left when there is a deadline.
        hang_up: The hang-up the call arms with its socket, or ``None`` for
            a call with neither a deadline nor a caller's hang-up.
        read_held_to_deadline: Whether the read timeout was shortened to the
            time left, so a read timeout means the deadline passed.
        timer: Hangs the call up when the deadline passes.

    The bound is not exact before the connection is made: the connect and
    pool timeouts (10 s each in :data:`CHAT_TIMEOUT`) are not held to the
    deadline, and the hang-up has no socket until the connection is made,
    so a call can outlive ``stop_at`` by about 10 s while it connects. Over
    a reused connection the socket comes only with the response headers;
    every call here opens its own connection, so none is reused.
    """

    timeout: httpx.Timeout
    hang_up: HangUp | None
    read_held_to_deadline: bool = False
    timer: threading.Timer | None = None

    def trace(self, event: str, info: Mapping[str, Any]) -> None:
        """httpcore's ``trace`` extension: arm the hang-up at connect (#935).

        Args:
            event: httpcore's event name, such as
                ``connection.connect_tcp.complete``.
            info: The event's details; a connect's ``return_value`` is the
                new network stream.
        """
        if self.hang_up is None:
            return
        if event in CONNECTED_TRACE_EVENTS:
            self.hang_up._connected(info["return_value"])
        elif event == REQUEST_SENT_TRACE_EVENT:
            self.hang_up._request_sent()

    def arm(self, response: httpx.Response) -> None:
        """Hand the hang-up the response's socket, if it has none yet."""
        if self.hang_up is not None:
            self.hang_up._arm(response)

    def explain(self, cut: httpx.TransportError) -> Exception | None:
        """What ``cut`` was, when this bound caused it; ``None`` otherwise.

        A read held to the deadline that timed out is the deadline, whatever
        the hang-up says by the time this runs. A hang-up wakes a read with
        the connection's end, never with a timeout, so a ``ReadTimeout`` is
        the read's own bound running out; a caller who hangs up between that
        and this, as a turn giving up at the same deadline does, did not end
        the call, and the error says what did (issue #955).
        """
        if self.read_held_to_deadline and isinstance(cut, httpx.ReadTimeout):
            return StreamDeadlinePassed("a read waited past the deadline")
        if self.hang_up is not None and self.hang_up.fired.is_set():
            if self.hang_up.at_deadline:
                return StreamDeadlinePassed("the call was cut off at its deadline")
            return StreamHungUp("the call was hung up on")
        return None

    def finish(self) -> None:
        """The call ended; nothing hangs it up from now on."""
        if self.hang_up is not None:
            self.hang_up._finish()
        if self.timer is not None:
            self.timer.cancel()


def _bound_call(
    timeout: httpx.Timeout, stop_at: float | None, hang_up: HangUp | None
) -> _CallBound:
    """Hold a call to ``stop_at``: the read timeout, and a hang-up there.

    Raises:
        StreamDeadlinePassed: ``stop_at`` has already passed; nothing is sent.
    """
    if stop_at is None:
        return _CallBound(timeout=timeout, hang_up=hang_up)
    if hang_up is None:
        hang_up = HangUp()
    left = stop_at - time.monotonic()
    if left <= 0:
        raise StreamDeadlinePassed("the deadline passed before the call was made")
    read_held_to_deadline = timeout.read is None or timeout.read > left
    if read_held_to_deadline:
        timeout = httpx.Timeout(
            connect=timeout.connect,
            read=left,
            write=timeout.write,
            pool=timeout.pool,
        )
    timer = threading.Timer(left, hang_up._deadline_reached)
    timer.daemon = True
    timer.start()
    return _CallBound(
        timeout=timeout,
        hang_up=hang_up,
        read_held_to_deadline=read_held_to_deadline,
        timer=timer,
    )


@contextmanager
def open_stream(
    method: str,
    url: str,
    *,
    json: dict[str, Any],
    timeout: httpx.Timeout,
    extensions: dict[str, Any],
) -> Iterator[httpx.Response]:
    """Send one request on a connection of its own and stream its response.

    The one place a call here opens a connection, and the name the suite's
    conftest replaces so no test reaches a real Ollama. What
    ``httpx.stream`` does, which takes no request ``extensions``, so it
    cannot carry httpcore's ``trace`` (issue #935). A client per call, as
    ``httpx.stream`` made, so no call reuses another's connection.

    Args:
        method: The HTTP method.
        url: The URL to send it to.
        json: The JSON request body.
        timeout: The call's timeouts.
        extensions: The request's extensions, such as ``trace``.

    Yields:
        The response, its body not yet read.
    """
    with (
        httpx.Client(timeout=timeout) as client,
        client.stream(method, url, json=json, extensions=extensions) as response,
    ):
        yield response


def stream_chat(
    model: str,
    messages: list[dict[str, str]],
    *,
    options: dict[str, Any] | None = None,
    think: bool | None = None,
    base_url: str | None = None,
    timeout: httpx.Timeout | None = None,
    stop_at: float | None = None,
    hang_up: HangUp | None = None,
) -> Generator[str]:
    """Stream chat tokens from Ollama. Yields content strings as they arrive.

    See :func:`chat_payload` for ``think`` and :func:`_base_url` for
    ``base_url``. ``timeout`` replaces :data:`CHAT_TIMEOUT` for a call that
    must end sooner. Closing the generator closes the connection, which is
    how Ollama is told to stop.

    Args:
        stop_at: A ``time.monotonic()`` past which the call does not hold the
            model. Its connection is hung up there, during the prefill
            before the first token (issue #935) or while a read is waiting
            on a stalled stream (issue #887), and the read timeout is held
            to the time left as well.
        hang_up: A :class:`HangUp` another thread can use to end the call at
            any time, such as a turn that has given up on it (issue #904),
            including while the prompt is still prefilled (issue #935).

    Raises:
        StreamDeadlinePassed: ``stop_at`` passed: before the call, while its
            headers were awaited, or while the stream was read.
        StreamHungUp: ``hang_up`` was used while a read was waiting.
    """
    payload = chat_payload(
        model,
        messages,
        stream=True,
        options=options,
        think=think,
        response_format=None,
    )
    bound = _bound_call(CHAT_TIMEOUT if timeout is None else timeout, stop_at, hang_up)
    try:
        with open_stream(
            "POST",
            f"{_base_url(base_url)}/api/chat",
            json=payload,
            timeout=bound.timeout,
            extensions={"trace": bound.trace},
        ) as resp:
            resp.raise_for_status()
            bound.arm(resp)
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
        explained = bound.explain(cut)
        if explained is None:
            raise
        raise explained from cut
    finally:
        bound.finish()


def chat(
    model: str,
    messages: list[dict[str, str]],
    *,
    options: dict[str, Any] | None = None,
    think: bool | None = None,
    response_format: dict[str, Any] | None = None,
    base_url: str | None = None,
    stop_at: float | None = None,
) -> str:
    """One non-streaming chat call; returns the reply's ``content``.

    Used where the whole answer is needed before anything can happen with
    it -- a structured extraction (issue #344) is parsed as JSON, not shown
    token by token. See :func:`chat_payload` for ``think`` and
    ``response_format``, and :func:`_base_url` for ``base_url``.

    The answer is still read over a streamed response, so the connection can
    be hung up at ``stop_at`` while its body is read, as :func:`stream_chat`
    does. Ollama sends a non-streaming answer's headers only once the whole
    answer is written; the connection is hung up at ``stop_at`` while that
    wait goes on too, since the socket is taken at connect (issue #935).

    Args:
        stop_at: A ``time.monotonic()`` past which the call does not hold the
            model; see :func:`stream_chat`.

    Raises:
        StreamDeadlinePassed: ``stop_at`` passed before or during the call.
        httpx.HTTPStatusError: On a non-2xx answer.
    """
    payload = chat_payload(
        model,
        messages,
        stream=False,
        options=options,
        think=think,
        response_format=response_format,
    )
    bound = _bound_call(CHAT_TIMEOUT, stop_at, None)
    try:
        with open_stream(
            "POST",
            f"{_base_url(base_url)}/api/chat",
            json=payload,
            timeout=bound.timeout,
            extensions={"trace": bound.trace},
        ) as resp:
            bound.arm(resp)
            resp.read()
    except httpx.TransportError as cut:
        explained = bound.explain(cut)
        if explained is None:
            raise
        raise explained from cut
    finally:
        bound.finish()
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
    stop_at: float | None = None,
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
        stop_at: One ``time.monotonic()`` deadline for the call and its
            fallback together, so a runner that refuses ``format`` does not
            double the time the model is held (issue #936).

    Returns:
        The model's reply, unparsed -- each caller owns how it reads and how
        it reports a malformed one.

    Raises:
        httpx.HTTPStatusError: On any non-2xx other than the runner saying it
            cannot constrain output.
        StreamDeadlinePassed: ``stop_at`` passed.
    """
    try:
        return chat(
            model,
            build_messages(),
            options=options,
            think=False,
            response_format=schema,
            base_url=base_url,
            stop_at=stop_at,
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
            stop_at=stop_at,
        )
