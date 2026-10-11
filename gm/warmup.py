"""Warm Ollama's prompt cache for the GM system prompt (issue #330).

On the Mac Studio the MLX runner only holds a restore point covering the GM
system prompt once a conversation has run past roughly 8k tokens; until then
every fresh session pays the full ~7k-token prefill (20-30 s to first token).
This module drives one deliberately long conversation to establish that
restore point and then sends a fresh single-turn probe whose
``prompt_eval_cached_count`` shows whether the cache took.

It also measures the system prompt against its share of ``GM_NUM_CTX``
(issue #907): once by estimate for the longest of the four prompts, before
Ollama is asked anything, and once by the probe's own prompt count.

Django-free on purpose (``manage.py warm_gm`` wires in the settings) so the
HTTP exchange can be tested end to end against an ``httpx.MockTransport``.
"""

import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from common.services.ollama_client import chat_payload
from gm.model_config import system_prompt_token_ceiling
from gm.retrieval import CHARS_PER_TOKEN, estimate_message_tokens

# Total tokens the warm-up conversation must exceed before the MLX runner
# keeps a restore point for the shared prefix (measured 2026-09-08, issue #330).
RESTORE_POINT_TOKENS = 8200
# Text the filler is padded to, on top of the system prompt, so the first
# request already lands past RESTORE_POINT_TOKENS with a ~7.3k-token prompt.
DEFAULT_FILLER_TOKENS = 1500
DEFAULT_NUM_PREDICT = 8
DEFAULT_READ_TIMEOUT = 300.0
POLL_INTERVAL_SECONDS = 2.0
# How much of the current system prompt the probe's cached count must cover
# before the warm-up counts as successful (issue #361). The prompt size is a
# chars/4 estimate and Ollama counts a few template tokens of its own, so the
# bar is deliberately loose: it separates "cached today's prompt" from "cached
# a stale prefix, or nothing", outcomes that differ by far more than 20%.
SYSTEM_PROMPT_CACHE_FRACTION = 0.8

# Fixed wording so the warm-up conversation is byte-identical across runs.
FILLER_PREAMBLE = (
    "Before the session starts, read this background from the canon. "
    "Reply with the single word Ready and nothing else.\n\n"
)
FOLLOW_UP_MESSAGES: tuple[str, ...] = (
    "Reply with the single word Ready again.",
    "One more time: reply with the single word Ready.",
)
PROBE_MESSAGE = "In one word, what is your role at this table?"


class OllamaUnavailableError(RuntimeError):
    """Ollama did not answer ``/api/version`` within the allowed wait."""


@dataclass(frozen=True)
class ChatTurnResult:
    """What one non-streaming ``/api/chat`` call reported back.

    Attributes:
        prompt_tokens: ``prompt_eval_count`` — tokens in the prompt.
        cached_tokens: ``prompt_eval_cached_count`` — prompt tokens served from
            the cache, or ``None`` when the server did not report the field
            (Ollama before 0.33).
        prefill_seconds: ``prompt_eval_duration`` converted from nanoseconds.
        content: The assistant text, appended to the conversation.
    """

    prompt_tokens: int
    cached_tokens: int | None
    prefill_seconds: float
    content: str


@dataclass(frozen=True)
class WarmupResult:
    """The warm-up conversation's turns plus the fresh probe that follows.

    Attributes:
        turns: One entry per request in the warm-up conversation.
        probe: The fresh single-turn request that follows it.
        system_prompt_tokens: Estimated size of the system prompt that was
            warmed, so the probe's cached count can be judged against the
            prompt the app is actually sending today.
    """

    turns: tuple[ChatTurnResult, ...]
    probe: ChatTurnResult
    system_prompt_tokens: int

    @property
    def cache_coverage(self) -> float | None:
        """Cached prompt tokens as a fraction of the system prompt's size.

        ``None`` when the server did not report a cached count at all. The
        divisor is never zero: :func:`estimate_message_tokens` adds the chat
        template's per-message framing to every message, empty ones included.
        """
        if self.probe.cached_tokens is None:
            return None
        return self.probe.cached_tokens / self.system_prompt_tokens

    @property
    def system_prompt_cached(self) -> bool | None:
        """Whether the probe found *the current* system prompt cached.

        A non-zero cached count is not enough (issue #361): after a prompt
        change Ollama still reports the stale shared prefix as cached, so a
        warm-up that quietly failed to establish a restore point for today's
        prompt would look successful. The count has to cover essentially the
        whole prompt — see :data:`SYSTEM_PROMPT_CACHE_FRACTION`.

        ``None`` when the server did not report a cached count at all.
        """
        coverage = self.cache_coverage
        if coverage is None:
            return None
        return coverage >= SYSTEM_PROMPT_CACHE_FRACTION


@dataclass(frozen=True)
class PromptBudget:
    """A system prompt's size against its ceiling in the window (issue #907).

    Attributes:
        prompt_tokens: The prompt's size in tokens, estimated or measured.
        num_ctx: The context window it is judged against (``GM_NUM_CTX``).
    """

    prompt_tokens: int
    num_ctx: int

    @property
    def ceiling_tokens(self) -> int:
        """The most the prompt may take; see ``SYSTEM_PROMPT_CONTEXT_SHARE``."""
        return system_prompt_token_ceiling(self.num_ctx)

    @property
    def within_ceiling(self) -> bool:
        """Whether the prompt leaves the rest of the window its share."""
        return self.prompt_tokens <= self.ceiling_tokens


def estimate_longest_prompt_tokens(prompts: Iterable[str]) -> int:
    """The estimated size of the longest of ``prompts`` as a system message.

    Estimated with :func:`gm.retrieval.estimate_message_tokens`, the same
    estimate the context guard and the excerpt budget use, so the ceiling and
    the budgets it is reasoned from count tokens the same way.

    Args:
        prompts: System prompt texts, such as the four GM prompts.

    Returns:
        The largest estimate.

    Raises:
        ValueError: When ``prompts`` is empty.
    """
    return max(
        estimate_message_tokens({"role": "system", "content": prompt})
        for prompt in prompts
    )


def create_client(base_url: str, *, read_timeout: float) -> httpx.Client:
    """An ``httpx.Client`` for ``base_url`` with a generous read timeout.

    A cold ~7k-token prefill takes about 30 s on the Mac Studio and the MLX
    model load can add 20 s more, so ``read_timeout`` is per request and the
    other phases stay short.
    """
    return httpx.Client(
        base_url=base_url.rstrip("/"),
        timeout=httpx.Timeout(connect=10.0, read=read_timeout, write=10.0, pool=10.0),
    )


def wait_for_ollama(
    client: httpx.Client,
    *,
    wait_seconds: float,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> str:
    """Poll ``/api/version`` until Ollama answers or ``wait_seconds`` elapse.

    Always makes at least one attempt, so ``wait_seconds=0`` is a plain
    reachability check. ``sleep`` and ``monotonic`` default to the ``time``
    module's and exist so tests can run the poll loop without waiting.

    Returns:
        The version string Ollama reported.

    Raises:
        OllamaUnavailableError: When no attempt succeeded before the deadline.
    """
    if sleep is None:
        sleep = time.sleep
    if monotonic is None:
        monotonic = time.monotonic
    deadline = monotonic() + wait_seconds
    last_error: Exception | None = None
    while True:
        try:
            response = client.get("/api/version")
            response.raise_for_status()
            return str(response.json().get("version", "unknown"))
        except httpx.HTTPError as error:
            last_error = error
        if monotonic() >= deadline:
            raise OllamaUnavailableError(
                f"Ollama at {client.base_url} did not answer /api/version "
                f"within {wait_seconds:g}s: {last_error}"
            ) from last_error
        sleep(POLL_INTERVAL_SECONDS)


def build_filler(chunk_texts: Iterable[str], *, target_tokens: int) -> str:
    """Concatenate corpus chunks, in order, until ``target_tokens`` is reached.

    Taking chunks from the canon corpus in a fixed order (rather than random
    text) keeps the warm-up prompt identical from run to run, so consecutive
    warm-ups share their whole prefix instead of only the system prompt.

    Raises:
        ValueError: When the chunks run out before the target is reached, which
            means the corpus root is wrong or empty.
    """
    target_chars = target_tokens * CHARS_PER_TOKEN
    collected: list[str] = []
    collected_chars = 0
    for text in chunk_texts:
        collected.append(text)
        collected_chars += len(text) + 2
        if collected_chars >= target_chars:
            return "\n\n".join(collected)
    raise ValueError(
        f"corpus supplied only {collected_chars} characters of filler; "
        f"{target_chars} needed"
    )


def _chat(
    client: httpx.Client,
    *,
    model: str,
    messages: Sequence[dict[str, str]],
    options: dict[str, Any],
) -> ChatTurnResult:
    """One non-streaming, thinking-off ``/api/chat`` call.

    The request body is built by
    :func:`common.services.ollama_client.chat_payload`, so the warm-up
    conversation is byte-identical in shape to a real GM turn -- which is the
    whole point of warming a prefix. Only the reply handling differs: the
    shared client returns the reply text, and this needs the prefill counts
    that come with it.
    """
    response = client.post(
        "/api/chat",
        json=chat_payload(
            model,
            list(messages),
            stream=False,
            options=options,
            think=False,
            response_format=None,
        ),
    )
    response.raise_for_status()
    data = response.json()
    cached = data.get("prompt_eval_cached_count")
    return ChatTurnResult(
        prompt_tokens=int(data.get("prompt_eval_count", 0)),
        cached_tokens=None if cached is None else int(cached),
        prefill_seconds=int(data.get("prompt_eval_duration", 0)) / 1_000_000_000,
        content=str(data.get("message", {}).get("content", "")),
    )


def warm_prompt_cache(
    client: httpx.Client,
    *,
    model: str,
    system_message: dict[str, str],
    options: dict[str, int | float],
    filler: str,
    num_predict: int = DEFAULT_NUM_PREDICT,
    on_turn: Callable[[str, ChatTurnResult], None] | None = None,
) -> WarmupResult:
    """Run the warm-up conversation, then a fresh probe of the cache.

    The conversation opens with ``filler`` (so the total crosses
    :data:`RESTORE_POINT_TOKENS` on the first request), continues for
    :data:`FOLLOW_UP_MESSAGES`, and is then abandoned for a single fresh
    request carrying only the system message and :data:`PROBE_MESSAGE` —
    exactly what a new GM session's first turn looks like.

    The result carries an estimate of ``system_message``'s token count so the
    caller can check the probe's cached count against the prompt the app sends
    today, rather than settling for any non-zero cached prefix (issue #361).

    Args:
        client: Client bound to the Ollama base URL.
        model: The GM model name.
        system_message: The real GM system message, sent first every time.
        options: The GM's ``options`` object; ``num_predict`` is added so each
            reply stays a handful of tokens.
        filler: Deterministic canon text for the opening user message.
        num_predict: Reply length cap for every request.
        on_turn: Called with a label and the result after each request, for
            progress output.

    Raises:
        httpx.HTTPError: When any request fails or returns a non-2xx status.
    """
    chat_options: dict[str, Any] = {**options, "num_predict": num_predict}
    conversation: list[dict[str, str]] = [
        system_message,
        {"role": "user", "content": FILLER_PREAMBLE + filler},
    ]
    turns: list[ChatTurnResult] = []
    for index, follow_up in enumerate((None, *FOLLOW_UP_MESSAGES), start=1):
        if follow_up is not None:
            conversation.append({"role": "user", "content": follow_up})
        turn = _chat(client, model=model, messages=conversation, options=chat_options)
        conversation.append({"role": "assistant", "content": turn.content})
        turns.append(turn)
        if on_turn is not None:
            on_turn(f"turn {index}", turn)
    probe = _chat(
        client,
        model=model,
        messages=[system_message, {"role": "user", "content": PROBE_MESSAGE}],
        options=chat_options,
    )
    if on_turn is not None:
        on_turn("fresh probe", probe)
    return WarmupResult(
        turns=tuple(turns),
        probe=probe,
        system_prompt_tokens=estimate_message_tokens(system_message),
    )
