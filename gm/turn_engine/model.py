"""The turn engine's port to the language model (issue #590).

The engine asks a :class:`ModelPort` for a stream of tokens and nothing else,
so tests drive whole turns with :class:`FakeModel` and never reach Ollama.
:class:`OllamaModel` is the production adapter: the GM model and options from
settings, reasoning switched off per request.
"""

import logging
import queue
import threading
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from common.services import ollama_client

Messages = list[dict[str, str]]

logger = logging.getLogger(__name__)


class ModelPort(Protocol):
    """Something that answers a list of chat messages with a stream of tokens."""

    @property
    def name(self) -> str:
        """The model's name, for the event log."""
        ...

    @property
    def options(self) -> dict[str, Any]:
        """The options sent with every call, for the event log."""
        ...

    def stream(
        self, messages: Messages, *, stop_at: float | None = None
    ) -> Iterator[str]:
        """The reply to ``messages``, token by token.

        Args:
            messages: The chat messages to answer.
            stop_at: A ``time.monotonic()`` past which the call must not hold
                the model; it ends with a ``TimeoutError`` there. The
                returned iterator may also offer ``hang_up()``, which ends
                the call from another thread (issue #904);
                :meth:`KeepAliveTokens.close` uses it.
        """
        ...


class _HangUpTokens:
    """An Ollama call's tokens, with a way to hang up on them (issue #904).

    A generator can only be closed by the thread running it, and a read
    blocked on a stalled stream does not return until the read timeout, so
    the thread that gave up on the call hangs up the connection instead,
    which wakes the read and tells Ollama to stop.
    """

    def __init__(self, tokens: Iterable[str], hang_up: ollama_client.HangUp) -> None:
        self._source = tokens
        self._tokens = iter(tokens)
        self._hang_up = hang_up

    def __iter__(self) -> Iterator[str]:
        return self

    def __next__(self) -> str:
        return next(self._tokens)

    def close(self) -> None:
        """Close the stream; only the thread reading it may do this.

        ``stream_chat`` gives a generator, but a stand-in for it may give any
        iterable, which has nothing to close.
        """
        close = getattr(self._source, "close", None)
        if close is not None:
            close()

    def hang_up(self) -> None:
        """Hang up the connection; safe from any thread, at any time."""
        self._hang_up.hang_up()


@dataclass
class OllamaModel:
    """The GM model on Ollama, streamed with thinking switched off."""

    name: str
    options: dict[str, Any] = field(default_factory=dict)

    def stream(
        self, messages: Messages, *, stop_at: float | None = None
    ) -> Iterator[str]:
        hang_up = ollama_client.HangUp()
        return _HangUpTokens(
            ollama_client.stream_chat(
                self.name,
                messages,
                options=self.options,
                think=False,
                stop_at=stop_at,
                hang_up=hang_up,
            ),
            hang_up,
        )


@dataclass(frozen=True)
class Paused:
    """A scripted reply that waits before its first token, like a cold model."""

    seconds: float
    reply: str | Sequence[str]


def _paused(pause: Paused) -> Iterator[str]:
    time.sleep(pause.seconds)
    yield from [pause.reply] if isinstance(pause.reply, str) else pause.reply


@dataclass
class FakeModel:
    """A model that answers each call with the next scripted reply.

    Attributes:
        replies: One reply per call, in order. A reply is a string (sent as
            one token), a list of tokens, a :class:`Paused` reply, or an
            exception to raise.
        requests: Every message list the model was sent, in order.
    """

    replies: Sequence[str | Sequence[str] | Paused | BaseException]
    name: str = "fake-model"
    options: dict[str, Any] = field(default_factory=dict)
    requests: list[Messages] = field(default_factory=list)
    #: Each call's ``stop_at``, in order; a scripted reply has no
    #: connection to hang up, so the fake only records it.
    stops: list[float | None] = field(default_factory=list)

    def stream(
        self, messages: Messages, *, stop_at: float | None = None
    ) -> Iterator[str]:
        index = len(self.requests)
        self.requests.append([dict(message) for message in messages])
        self.stops.append(stop_at)
        if index >= len(self.replies):
            raise AssertionError(f"the fake model has no reply for call {index + 1}")
        reply = self.replies[index]
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, Paused):
            return _paused(reply)
        return iter([reply] if isinstance(reply, str) else list(reply))


class KeepAliveTokens:
    """The model's tokens, with ``None`` in the gaps where nothing arrived.

    The model stream is read on its own thread, so the response can keep
    talking while that read blocks (issue #415). The reader asks the model for
    a token only when the consumer asks for one, so the consumer still stops
    pulling from the model the moment it abandons a turn, and nothing is read
    ahead (issue #361). Every token and every exception is handed across in
    order; nothing is dropped or swallowed.

    ``close()`` tells the reader to stop and waits briefly for it. A generator
    can only be closed by the thread running it, so the reader closes the
    model stream itself, which hands the HTTP connection back. A reader
    blocked in a read on a stream that stalled would not see the stop until
    that read returned, up to the read timeout later, holding the model the
    whole time, so ``close()`` first hangs up a stream that offers
    ``hang_up()``, which wakes the read (issue #904).
    """

    _DONE = object()
    _CLOSE_WAIT_SECONDS = 1.0

    def __init__(self, tokens: Iterable[str], interval: float):
        self._tokens = tokens
        self._interval = interval
        self._items: queue.Queue = queue.Queue()
        self._wanted = threading.Semaphore(0)
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        iterator = iter(self._tokens)
        try:
            while True:
                self._wanted.acquire()
                if self._stop.is_set():
                    break
                try:
                    token = next(iterator)
                except StopIteration:
                    break
                self._items.put(token)
        except BaseException as error:  # handed to the consumer, who raises it
            self._items.put(error)
        finally:
            try:
                close = getattr(self._tokens, "close", None)
                if close is not None:
                    close()
            except BaseException as error:
                # Logged here as well as handed on: after close() nobody is
                # left reading the queue, and the failure would vanish.
                logger.exception("A model stream failed to close")
                self._items.put(error)
            finally:
                # Always, or a consumer still reading would wait for good.
                self._items.put(self._DONE)

    def __iter__(self) -> Iterator[str | None]:
        while True:
            self._wanted.release()
            while True:
                try:
                    item = self._items.get(timeout=self._interval)
                    break
                except queue.Empty:
                    yield None
            if item is self._DONE:
                return
            if isinstance(item, BaseException):
                raise item
            yield item

    def close(self) -> None:
        self._stop.set()
        # A stream that already ended ignores this, so a turn that finished
        # cleanly hangs up on nothing.
        hang_up = getattr(self._tokens, "hang_up", None)
        try:
            if hang_up is not None:
                hang_up()
        finally:
            # The reader is released and waited for even if the hang-up failed.
            self._wanted.release()
            self._reader.join(timeout=self._CLOSE_WAIT_SECONDS)
