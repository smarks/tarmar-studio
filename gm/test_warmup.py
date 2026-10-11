"""Tests for the GM prompt-cache warm-up (issue #330).

Nothing here reaches Ollama: every request goes through an
``httpx.MockTransport`` that records what was sent and answers with canned
``/api/chat`` bodies.
"""

import json
import tempfile
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from gm import warmup
from gm.engine import SYSTEM_PROMPT, SYSTEM_PROMPTS_BY_AUTHORITY_AND_MODE
from gm.retrieval import estimate_message_tokens

FAKE_SYSTEM = {"role": "system", "content": "You are the GM."}
# The real prompt's estimated size, so the command tests below can script a
# cached count that means "today's prompt is warm" however the prompt grows.
SYSTEM_PROMPT_TOKENS = estimate_message_tokens(
    {"role": "system", "content": SYSTEM_PROMPT}
)
FAKE_OPTIONS: dict[str, int | float] = {"num_ctx": 4096, "temperature": 0.5}
FILLER = "canon " * 40


def chat_reply(
    *,
    prompt_tokens: int,
    cached_tokens: int | None,
    content: str = "Ready",
    prefill_seconds: float = 0.2,
) -> dict[str, Any]:
    """A non-streaming ``/api/chat`` response body as Ollama 0.33 sends it."""
    body: dict[str, Any] = {
        "model": "fake-model",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "prompt_eval_count": prompt_tokens,
        "prompt_eval_duration": int(prefill_seconds * 1_000_000_000),
        "eval_count": 1,
    }
    if cached_tokens is not None:
        body["prompt_eval_cached_count"] = cached_tokens
    return body


class RecordingOllama:
    """A fake Ollama behind ``httpx.MockTransport`` that logs every request."""

    def __init__(
        self,
        chat_replies: list[dict[str, Any]],
        *,
        version_failures: int = 0,
    ) -> None:
        self.chat_replies = list(chat_replies)
        self.version_failures = version_failures
        self.requests: list[httpx.Request] = []
        self.version_attempts = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/api/chat":
            if not self.chat_replies:
                return httpx.Response(500, text="no reply scripted")
            return httpx.Response(200, json=self.chat_replies.pop(0))
        # Anything else is the /api/version reachability poll.
        self.version_attempts += 1
        if self.version_attempts <= self.version_failures:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"version": "0.33.3"})

    def client(self) -> httpx.Client:
        return httpx.Client(
            base_url="http://ollama", transport=httpx.MockTransport(self.handler)
        )

    @property
    def chat_payloads(self) -> list[dict[str, Any]]:
        return [
            json.loads(request.content)
            for request in self.requests
            if request.url.path == "/api/chat"
        ]


def three_turns_then_probe(probe_cached: int | None) -> list[dict[str, Any]]:
    return [
        chat_reply(prompt_tokens=8947, cached_tokens=0, prefill_seconds=27.4),
        chat_reply(prompt_tokens=8966, cached_tokens=8942, content="Ready."),
        chat_reply(prompt_tokens=8988, cached_tokens=8961, content="Ready!"),
        chat_reply(prompt_tokens=7342, cached_tokens=probe_cached, content="GM"),
    ]


class BuildFillerTest(SimpleTestCase):
    def test_stops_at_first_chunk_that_reaches_the_target(self):
        chunks = ["a" * 100, "b" * 100, "c" * 100]
        filler = warmup.build_filler(chunks, target_tokens=50)
        self.assertEqual(filler, "a" * 100 + "\n\n" + "b" * 100)

    def test_is_deterministic_for_the_same_chunks(self):
        chunks = ["one " * 30, "two " * 30]
        first = warmup.build_filler(iter(chunks), target_tokens=20)
        second = warmup.build_filler(iter(chunks), target_tokens=20)
        self.assertEqual(first, second)

    def test_raises_when_corpus_runs_short(self):
        with self.assertRaisesRegex(ValueError, "supplied only 12 characters"):
            warmup.build_filler(["tiny", "text"], target_tokens=100)


class WaitForOllamaTest(SimpleTestCase):
    def _fake_clock(self, step: float = 1.0) -> tuple[Callable[[], float], list]:
        ticks: list[float] = [0.0]
        sleeps: list[float] = []

        def monotonic() -> float:
            return ticks[-1]

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            ticks.append(ticks[-1] + step)

        self._sleep = sleep
        return monotonic, sleeps

    def test_returns_version_on_first_attempt_without_sleeping(self):
        fake = RecordingOllama([])
        monotonic, sleeps = self._fake_clock()
        with fake.client() as client:
            version = warmup.wait_for_ollama(
                client, wait_seconds=0, sleep=self._sleep, monotonic=monotonic
            )
        self.assertEqual(version, "0.33.3")
        self.assertEqual(sleeps, [])

    def test_polls_until_ollama_answers(self):
        fake = RecordingOllama([], version_failures=2)
        monotonic, sleeps = self._fake_clock()
        with fake.client() as client:
            version = warmup.wait_for_ollama(
                client, wait_seconds=30, sleep=self._sleep, monotonic=monotonic
            )
        self.assertEqual(version, "0.33.3")
        self.assertEqual(fake.version_attempts, 3)
        self.assertEqual(sleeps, [warmup.POLL_INTERVAL_SECONDS] * 2)

    def test_gives_up_at_the_deadline(self):
        fake = RecordingOllama([], version_failures=100)
        monotonic, sleeps = self._fake_clock(step=1.0)
        with fake.client() as client:
            with self.assertRaisesRegex(
                warmup.OllamaUnavailableError, "within 2s: .*refused"
            ):
                warmup.wait_for_ollama(
                    client, wait_seconds=2, sleep=self._sleep, monotonic=monotonic
                )
        self.assertEqual(fake.version_attempts, 3)

    def test_zero_wait_makes_exactly_one_attempt(self):
        fake = RecordingOllama([], version_failures=1)
        monotonic, _sleeps = self._fake_clock()
        with fake.client() as client:
            with self.assertRaises(warmup.OllamaUnavailableError):
                warmup.wait_for_ollama(
                    client, wait_seconds=0, sleep=self._sleep, monotonic=monotonic
                )
        self.assertEqual(fake.version_attempts, 1)

    def test_defaults_to_the_time_module(self):
        fake = RecordingOllama([], version_failures=1)
        with (
            fake.client() as client,
            patch.object(warmup.time, "sleep") as sleep,
            patch.object(warmup.time, "monotonic", side_effect=[0.0, 0.0, 5.0]),
        ):
            version = warmup.wait_for_ollama(client, wait_seconds=10)
        self.assertEqual(version, "0.33.3")
        sleep.assert_called_once_with(warmup.POLL_INTERVAL_SECONDS)

    def test_http_status_errors_count_as_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="loading")

        client = httpx.Client(
            base_url="http://ollama", transport=httpx.MockTransport(handler)
        )
        with client:
            with self.assertRaisesRegex(warmup.OllamaUnavailableError, "503"):
                warmup.wait_for_ollama(client, wait_seconds=0)


class CreateClientTest(SimpleTestCase):
    def test_applies_read_timeout_and_strips_trailing_slash(self):
        with warmup.create_client("http://ollama:11434/", read_timeout=123.0) as client:
            self.assertEqual(str(client.base_url), "http://ollama:11434")
            self.assertEqual(client.timeout.read, 123.0)
            self.assertEqual(client.timeout.connect, 10.0)


class WarmPromptCacheTest(SimpleTestCase):
    def _run(self, fake: RecordingOllama, **kwargs: Any) -> warmup.WarmupResult:
        with fake.client() as client:
            return warmup.warm_prompt_cache(
                client,
                model="fake-model",
                system_message=FAKE_SYSTEM,
                options=FAKE_OPTIONS,
                filler=FILLER,
                **kwargs,
            )

    def test_every_request_has_system_first_thinking_off_and_num_predict(self):
        fake = RecordingOllama(three_turns_then_probe(7320))
        self._run(fake, num_predict=8)
        payloads = fake.chat_payloads
        self.assertEqual(len(payloads), 4)
        for payload in payloads:
            self.assertEqual(payload["model"], "fake-model")
            self.assertEqual(payload["messages"][0], FAKE_SYSTEM)
            self.assertIs(payload["think"], False)
            self.assertIs(payload["stream"], False)
            self.assertEqual(
                payload["options"],
                {"num_ctx": 4096, "temperature": 0.5, "num_predict": 8},
            )

    def test_does_not_mutate_the_caller_options(self):
        fake = RecordingOllama(three_turns_then_probe(7320))
        options = dict(FAKE_OPTIONS)
        with fake.client() as client:
            warmup.warm_prompt_cache(
                client,
                model="fake-model",
                system_message=FAKE_SYSTEM,
                options=options,
                filler=FILLER,
            )
        self.assertEqual(options, FAKE_OPTIONS)

    def test_conversation_grows_across_three_turns_then_probe_is_fresh(self):
        fake = RecordingOllama(three_turns_then_probe(7320))
        result = self._run(fake)
        first, second, third, probe = fake.chat_payloads

        opening = {"role": "user", "content": warmup.FILLER_PREAMBLE + FILLER}
        self.assertEqual(first["messages"], [FAKE_SYSTEM, opening])
        self.assertEqual(
            second["messages"],
            [
                FAKE_SYSTEM,
                opening,
                {"role": "assistant", "content": "Ready"},
                {"role": "user", "content": warmup.FOLLOW_UP_MESSAGES[0]},
            ],
        )
        self.assertEqual(len(third["messages"]), 6)
        self.assertEqual(third["messages"][4]["content"], "Ready.")
        self.assertEqual(third["messages"][5]["content"], warmup.FOLLOW_UP_MESSAGES[1])
        # The probe is what a brand-new GM session's first turn looks like.
        self.assertEqual(
            probe["messages"],
            [FAKE_SYSTEM, {"role": "user", "content": warmup.PROBE_MESSAGE}],
        )
        self.assertEqual(len(result.turns), 3)
        self.assertEqual(result.turns[0].prompt_tokens, 8947)
        self.assertEqual(result.turns[0].cached_tokens, 0)
        self.assertAlmostEqual(result.turns[0].prefill_seconds, 27.4)
        self.assertEqual(result.turns[2].content, "Ready!")
        self.assertEqual(result.probe.cached_tokens, 7320)
        self.assertIs(result.system_prompt_cached, True)

    def test_reports_progress_per_request(self):
        fake = RecordingOllama(three_turns_then_probe(7320))
        seen: list[tuple[str, int]] = []
        self._run(
            fake, on_turn=lambda label, turn: seen.append((label, turn.prompt_tokens))
        )
        self.assertEqual(
            seen,
            [
                ("turn 1", 8947),
                ("turn 2", 8966),
                ("turn 3", 8988),
                ("fresh probe", 7342),
            ],
        )

    def test_probe_with_zero_cached_is_not_cached(self):
        fake = RecordingOllama(three_turns_then_probe(0))
        self.assertIs(self._run(fake).system_prompt_cached, False)

    def test_missing_cached_count_is_unknown(self):
        fake = RecordingOllama(three_turns_then_probe(None))
        result = self._run(fake)
        self.assertIsNone(result.probe.cached_tokens)
        self.assertIsNone(result.system_prompt_cached)

    def test_reports_the_size_of_the_prompt_it_warmed(self):
        fake = RecordingOllama(three_turns_then_probe(7320))
        result = self._run(fake)
        self.assertEqual(
            result.system_prompt_tokens, estimate_message_tokens(FAKE_SYSTEM)
        )

    def _long_system_run(self, probe_cached: int) -> warmup.WarmupResult:
        """Warm a deliberately long system prompt, scripting the probe's count.

        The real prompt is thousands of tokens, so only a long fake one can
        tell a full cache apart from a stale prefix.
        """
        long_system = {"role": "system", "content": "You are the GM. " * 500}
        fake = RecordingOllama(three_turns_then_probe(probe_cached))
        with fake.client() as client:
            return warmup.warm_prompt_cache(
                client,
                model="fake-model",
                system_message=long_system,
                options=FAKE_OPTIONS,
                filler=FILLER,
            )

    def test_cache_covering_the_whole_prompt_counts_as_warm(self):
        expected = estimate_message_tokens(
            {"role": "system", "content": "You are the GM. " * 500}
        )
        result = self._long_system_run(expected)
        self.assertIs(result.system_prompt_cached, True)
        self.assertAlmostEqual(result.cache_coverage or 0.0, 1.0, places=2)

    def test_stale_prefix_short_of_the_current_prompt_is_not_warm(self):
        """A prompt change leaves the old prefix cached; that is not warm.

        Ollama still reports the shared head of the previous prompt as cached,
        so a non-zero count alone would call a failed warm-up a success.
        """
        expected = estimate_message_tokens(
            {"role": "system", "content": "You are the GM. " * 500}
        )
        result = self._long_system_run(expected // 2)
        self.assertIs(result.system_prompt_cached, False)
        self.assertAlmostEqual(result.cache_coverage or 0.0, 0.5, places=2)

    def test_http_error_propagates(self):
        fake = RecordingOllama(three_turns_then_probe(7320)[:2])
        with self.assertRaises(httpx.HTTPStatusError):
            self._run(fake)
        self.assertEqual(len(fake.chat_payloads), 3)


class PromptBudgetTest(SimpleTestCase):
    """The prompt's size against its share of GM_NUM_CTX (issue #907)."""

    def test_at_the_ceiling_is_within_it(self):
        budget = warmup.PromptBudget(prompt_tokens=16384, num_ctx=65536)
        self.assertEqual(budget.ceiling_tokens, 16384)
        self.assertIs(budget.within_ceiling, True)

    def test_one_token_over_is_not(self):
        budget = warmup.PromptBudget(prompt_tokens=16385, num_ctx=65536)
        self.assertIs(budget.within_ceiling, False)

    def test_ceiling_moves_with_the_window(self):
        self.assertEqual(
            warmup.PromptBudget(prompt_tokens=0, num_ctx=8192).ceiling_tokens, 2048
        )

    def test_longest_prompt_uses_the_shared_message_estimate(self):
        prompts = ("short", "x" * 400, "y" * 40)
        self.assertEqual(
            warmup.estimate_longest_prompt_tokens(prompts),
            estimate_message_tokens({"role": "system", "content": "x" * 400}),
        )

    def test_no_prompts_is_an_error(self):
        with self.assertRaises(ValueError):
            warmup.estimate_longest_prompt_tokens(())


class WarmGMCommandTest(SimpleTestCase):
    """manage.py warm_gm."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name) / "corpus"
        self.root.mkdir()
        (self.root / "canon.md").write_text(
            "# Canon\n\n" + "The dragon sleeps under the mountain. " * 40,
            encoding="utf-8",
        )

    def tearDown(self):
        self._temp.cleanup()

    def _settings(self, corpus_root: Path | None = None, num_ctx: int = 4096):
        return override_settings(
            GM_CORPUS_ROOT=self.root if corpus_root is None else corpus_root,
            GM_MODEL="fake-model",
            GM_NUM_CTX=num_ctx,
            GM_TEMPERATURE=0.5,
            OLLAMA_BASE_URL="http://ollama:11434",
        )

    def _run(self, fake: RecordingOllama, *arguments: str, num_ctx: int = 4096) -> str:
        out = StringIO()

        def fake_create_client(base_url: str, *, read_timeout: float) -> httpx.Client:
            self.assertEqual(base_url, "http://ollama:11434")
            self.seen_read_timeout = read_timeout
            return fake.client()

        with (
            self._settings(num_ctx=num_ctx),
            patch.object(warmup, "create_client", fake_create_client),
        ):
            call_command("warm_gm", *arguments, "--filler-tokens", "50", stdout=out)
        return out.getvalue()

    def test_states_both_prompt_budgets_within_the_ceiling(self):
        """Issue #907: the estimate before warming, Ollama's count after."""
        fake = RecordingOllama(three_turns_then_probe(SYSTEM_PROMPT_TOKENS))
        output = self._run(fake, num_ctx=65536)
        longest = warmup.estimate_longest_prompt_tokens(
            SYSTEM_PROMPTS_BY_AUTHORITY_AND_MODE.values()
        )
        self.assertIn(
            f"prompt budget: longest GM prompt, estimated {longest} tokens, "
            "ceiling 16384 (25% of GM_NUM_CTX 65536)",
            output,
        )
        self.assertIn(
            "prompt budget: shortest GM prompt with the probe, measured by "
            "Ollama 7342 tokens, ceiling 16384 (25% of GM_NUM_CTX 65536)",
            output,
        )
        self.assertNotIn("OVER the ceiling", output)
        self.assertLess(
            output.index("prompt budget: longest"), output.index("Ollama 0.33.3")
        )

    def test_prompt_over_the_ceiling_is_reported_loudly_and_exits_zero(self):
        fake = RecordingOllama(three_turns_then_probe(SYSTEM_PROMPT_TOKENS))
        output = self._run(fake)
        self.assertIn(
            "ceiling 1024 (25% of GM_NUM_CTX 4096) -- OVER the ceiling", output
        )
        self.assertEqual(output.count("OVER the ceiling"), 2)
        self.assertIn("warm: system prompt cached", output)

    def test_reports_cached_system_prompt(self):
        fake = RecordingOllama(three_turns_then_probe(SYSTEM_PROMPT_TOKENS))
        output = self._run(fake)
        self.assertIn(
            "Ollama 0.33.3 at http://ollama:11434; warming fake-model", output
        )
        self.assertIn("turn 1: prompt 8947 tokens, cached 0, prefill 27.4s", output)
        self.assertIn(
            "fresh probe: prompt 7342 tokens, "
            f"cached {SYSTEM_PROMPT_TOKENS}, prefill 0.2s",
            output,
        )
        self.assertIn(
            f"warm: system prompt cached ({SYSTEM_PROMPT_TOKENS} tokens, "
            f"~{SYSTEM_PROMPT_TOKENS} expected)",
            output,
        )
        self.assertEqual(self.seen_read_timeout, warmup.DEFAULT_READ_TIMEOUT)

    def test_sends_real_system_prompt_and_gm_options(self):
        fake = RecordingOllama(three_turns_then_probe(SYSTEM_PROMPT_TOKENS))
        self._run(fake, "--num-predict", "4")
        for payload in fake.chat_payloads:
            self.assertEqual(payload["model"], "fake-model")
            self.assertEqual(
                payload["messages"][0], {"role": "system", "content": SYSTEM_PROMPT}
            )
            self.assertIs(payload["think"], False)
            self.assertEqual(
                payload["options"],
                {"num_ctx": 4096, "temperature": 0.5, "num_predict": 4},
            )
        opening = fake.chat_payloads[0]["messages"][1]["content"]
        self.assertTrue(opening.startswith(warmup.FILLER_PREAMBLE))
        self.assertIn("The dragon sleeps under the mountain.", opening)
        self.assertGreaterEqual(len(opening), 50 * warmup.CHARS_PER_TOKEN)

    def test_not_cached_still_exits_zero(self):
        fake = RecordingOllama(three_turns_then_probe(0))
        output = self._run(fake)
        self.assertIn("warm: NOT cached", output)

    def test_cache_short_of_the_current_prompt_is_reported_loudly(self):
        """A cached prefix that does not cover today's prompt is a failure.

        The deploy log has to say so with both numbers, or the next prompt
        change looks warm while every fresh session pays a full prefill.
        """
        fake = RecordingOllama(three_turns_then_probe(SYSTEM_PROMPT_TOKENS // 2))
        output = self._run(fake)
        self.assertIn("warm: NOT cached", output)
        self.assertIn(str(SYSTEM_PROMPT_TOKENS // 2), output)
        self.assertIn(f"~{SYSTEM_PROMPT_TOKENS}", output)
        self.assertIn("pays a full prefill", output)

    def test_missing_cached_count_is_reported_not_guessed(self):
        fake = RecordingOllama(three_turns_then_probe(None))
        output = self._run(fake)
        self.assertIn("cached not reported", output)
        self.assertIn("prompt_eval_cached_count not reported", output)
        self.assertNotIn("NOT cached", output)

    def test_wait_polls_version_before_warming(self):
        fake = RecordingOllama(
            three_turns_then_probe(SYSTEM_PROMPT_TOKENS), version_failures=2
        )
        with (
            patch.object(warmup.time, "sleep") as sleep,
            patch.object(warmup.time, "monotonic", return_value=0.0),
        ):
            output = self._run(fake, "--wait", "120")
        self.assertEqual(fake.version_attempts, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertIn("warm: system prompt cached", output)

    def test_unreachable_ollama_is_a_command_error(self):
        fake = RecordingOllama([], version_failures=1)
        with self.assertRaisesRegex(CommandError, "did not answer /api/version"):
            self._run(fake)
        self.assertEqual(fake.chat_payloads, [])

    def test_failed_chat_request_is_a_command_error(self):
        fake = RecordingOllama(three_turns_then_probe(SYSTEM_PROMPT_TOKENS)[:1])
        with self.assertRaisesRegex(CommandError, "GM warm-up request failed.*500"):
            self._run(fake)

    def test_empty_corpus_is_a_command_error(self):
        fake = RecordingOllama(three_turns_then_probe(SYSTEM_PROMPT_TOKENS))
        with self._settings(corpus_root=self.root / "absent"):
            with self.assertRaisesRegex(CommandError, "GM warm-up filler failed"):
                call_command("warm_gm", stdout=StringIO())
        self.assertEqual(fake.requests, [])

    def test_timeout_flag_reaches_the_client(self):
        fake = RecordingOllama(three_turns_then_probe(SYSTEM_PROMPT_TOKENS))
        self._run(fake, "--timeout", "45")
        self.assertEqual(self.seen_read_timeout, 45.0)

    def test_it_warms_the_prefix_every_mode_shares(self):
        """Issue #372: four prompts, one warm-up, and it is the shortest.

        Whatever the person at the table has chosen, the request they send
        opens with ``SYSTEM_PROMPT`` — so warming that, and nothing past it,
        establishes a restore point every one of the four extends. Warming a
        longer variant would put the restore point past where three of the
        four diverge.
        """
        from gm.engine import (
            SYSTEM_PROMPT,
            SYSTEM_PROMPTS_BY_AUTHORITY_AND_MODE,
        )

        fake = RecordingOllama(three_turns_then_probe(SYSTEM_PROMPT_TOKENS))
        self._run(fake)
        warmed = fake.chat_payloads[0]["messages"][0]["content"]
        self.assertEqual(warmed, SYSTEM_PROMPT)
        for prompt in SYSTEM_PROMPTS_BY_AUTHORITY_AND_MODE.values():
            with self.subTest(prompt=prompt[:40]):
                self.assertTrue(prompt.startswith(warmed))
