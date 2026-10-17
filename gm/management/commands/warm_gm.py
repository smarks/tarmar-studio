"""``manage.py warm_gm`` — warm Ollama's prompt cache for the GM system prompt.

Drives one three-turn conversation against ``settings.GM_MODEL`` with the real
system message, thinking off and short replies, opened by enough deterministic
canon text that the total passes the point where the MLX runner keeps a
restore point for the shared prefix (issue #330). A fresh single-turn probe
then reports ``prompt_eval_cached_count`` so the log shows whether the next
real session's first turn will be fast. ``deploy.sh`` runs this after the
blue-green swap; run it by hand after an Ollama restart on the Mac Studio.

The cached count is checked against the size of the system prompt the app
sends today, not merely against zero (issue #361): after a prompt change
Ollama still reports the stale shared prefix as cached, which made a warm-up
that had not actually warmed the new prompt read as a success.

Before warming, it states the longest of the four GM prompts against its
ceiling, ``SYSTEM_PROMPT_CONTEXT_SHARE`` of ``GM_NUM_CTX`` (issue #907); after
the probe it states Ollama's own prompt count against the same ceiling, the
estimate's reference figure. Both lines stay in the deploy log.

A cold cache is a warm-up outcome, not a failure, so the command exits 0
whether or not the probe found the prompt cached — it says so loudly instead,
and ``deploy.sh`` turns that into a deploy warning. Ollama being unreachable
(after ``--wait``) is an error.
"""

from typing import Any

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from gm import warmup
from gm.engine import system_prompts_by_authority_and_mode
from gm.model_config import SYSTEM_PROMPT_CONTEXT_SHARE
from gm.retrieval import iter_corpus_chunks
from gm.services import get_gm_model, get_gm_options, get_system_message


class Command(BaseCommand):
    help = "Warm Ollama's prompt cache for the GM system prompt"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--wait",
            type=float,
            default=0.0,
            metavar="SECONDS",
            help="Poll /api/version this long for Ollama to come up before "
            "starting (default: one attempt only)",
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=warmup.DEFAULT_READ_TIMEOUT,
            metavar="SECONDS",
            help="Read timeout per request; a cold prefill plus model load "
            f"can take a minute (default: {warmup.DEFAULT_READ_TIMEOUT:g})",
        )
        parser.add_argument(
            "--filler-tokens",
            type=int,
            default=warmup.DEFAULT_FILLER_TOKENS,
            metavar="TOKENS",
            help="Estimated size of the canon filler in the opening message "
            f"(default: {warmup.DEFAULT_FILLER_TOKENS})",
        )
        parser.add_argument(
            "--num-predict",
            type=int,
            default=warmup.DEFAULT_NUM_PREDICT,
            metavar="TOKENS",
            help=f"Reply length cap per request (default: {warmup.DEFAULT_NUM_PREDICT})",
        )

    def handle(self, *args: object, **options: Any) -> None:
        wait_seconds = float(options["wait"])
        read_timeout = float(options["timeout"])
        filler_tokens = int(options["filler_tokens"])
        num_predict = int(options["num_predict"])
        model = get_gm_model()
        base_url = settings.OLLAMA_BASE_URL
        num_ctx = int(settings.GM_NUM_CTX)
        self._report_prompt_budget(
            "longest GM prompt, estimated",
            warmup.PromptBudget(
                prompt_tokens=warmup.estimate_longest_prompt_tokens(
                    system_prompts_by_authority_and_mode().values()
                ),
                num_ctx=num_ctx,
            ),
        )

        try:
            filler = warmup.build_filler(
                (chunk.text for chunk in iter_corpus_chunks(settings.GM_CORPUS_ROOT)),
                target_tokens=filler_tokens,
            )
        except (OSError, ValueError) as error:
            raise CommandError(f"GM warm-up filler failed: {error}") from error

        with warmup.create_client(base_url, read_timeout=read_timeout) as client:
            try:
                version = warmup.wait_for_ollama(client, wait_seconds=wait_seconds)
            except warmup.OllamaUnavailableError as error:
                raise CommandError(str(error)) from error
            self.stdout.write(f"Ollama {version} at {base_url}; warming {model}")
            try:
                result = warmup.warm_prompt_cache(
                    client,
                    model=model,
                    # There are four GM prompts now (issue #372), and this
                    # deliberately warms the shortest: system_prompt() is the
                    # exact prefix all four share, and every real request is
                    # an extension of it. Warming an extension instead would
                    # cover a few hundred more tokens for one of the four and
                    # would rely on the runner reusing a restore point longer
                    # than the request that follows -- the direction prefix
                    # caching is not guaranteed in. Do not "improve" this to
                    # the DM prompt; the ~7k tokens that cost 20-30 s are all
                    # in the shared part, and each addendum is a few hundred.
                    system_message=get_system_message(),
                    options=get_gm_options(),
                    filler=filler,
                    num_predict=num_predict,
                    on_turn=self._report_turn,
                )
            except httpx.HTTPError as error:
                raise CommandError(f"GM warm-up request failed: {error}") from error

        self._report_outcome(result)
        self._report_prompt_budget(
            "shortest GM prompt with the probe, measured by Ollama",
            warmup.PromptBudget(
                prompt_tokens=result.probe.prompt_tokens, num_ctx=num_ctx
            ),
        )

    def _report_prompt_budget(self, label: str, budget: warmup.PromptBudget) -> None:
        """State a prompt's size against its ceiling, loudly when it is over.

        Over the ceiling is not a warm-up failure -- the GM still answers --
        so it does not change the exit status; it is printed as an error for
        the deploy log to carry.
        """
        line = (
            f"prompt budget: {label} {budget.prompt_tokens} tokens, ceiling "
            f"{budget.ceiling_tokens} ({SYSTEM_PROMPT_CONTEXT_SHARE:.0%} of "
            f"GM_NUM_CTX {budget.num_ctx})"
        )
        if budget.within_ceiling:
            self.stdout.write(self.style.SUCCESS(line))
        else:
            self.stdout.write(
                self.style.ERROR(
                    f"{line} -- OVER the ceiling: the prompt is taking room the "
                    "conversation needs (issue #907)"
                )
            )

    def _report_turn(self, label: str, turn: warmup.ChatTurnResult) -> None:
        cached = (
            "not reported" if turn.cached_tokens is None else str(turn.cached_tokens)
        )
        self.stdout.write(
            f"{label}: prompt {turn.prompt_tokens} tokens, cached {cached}, "
            f"prefill {turn.prefill_seconds:.1f}s"
        )

    def _report_outcome(self, result: warmup.WarmupResult) -> None:
        """Say whether the probe found *today's* system prompt cached.

        Both numbers are printed either way, so a deploy log shows how far
        short a failed warm-up fell rather than only that it failed.
        """
        cached = result.system_prompt_cached
        expected = result.system_prompt_tokens
        if cached is None:
            self.stdout.write(
                self.style.WARNING(
                    "warm: prompt_eval_cached_count not reported "
                    "(Ollama older than 0.33?); cannot confirm the cache"
                )
            )
        elif cached:
            self.stdout.write(
                self.style.SUCCESS(
                    f"warm: system prompt cached ({result.probe.cached_tokens} "
                    f"tokens, ~{expected} expected)"
                )
            )
        else:
            self.stdout.write(
                self.style.ERROR(
                    f"warm: NOT cached — the probe found only "
                    f"{result.probe.cached_tokens} cached tokens against a "
                    f"system prompt of ~{expected}. The prompt the app sends "
                    f"is NOT warm; the next GM session pays a full prefill."
                )
            )
