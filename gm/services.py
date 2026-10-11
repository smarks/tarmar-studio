"""Game Master service — system prompts, rules loading, dice rolling."""

import logging
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import httpx
from django.conf import settings
from django.db import transaction

from battle.access import is_dm
from common.services import ollama_client
from common.services.ollama_client import StreamDeadlinePassed

from . import summary
from .battle_report import pending_battle_entries
from .character_schema import EXTRACTION_TEMPERATURE

# Single source of truth for the prompt, dice roller, and rules loader
# (shared with the CLI).
from .engine import (
    DM_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    default_mode_for,
    load_rules_from_dir,
    normalise_mode,
    roll_dice,
    system_prompt_for,
)

# The slash-command message wording, shared with the offline CLI. Re-exported
# so gm/views.py's import path is unchanged.
from .messages import (
    build_roll_message,
    build_rules_lookup_message,
    build_state_request_message,
)
from .model_config import GMModelConfig
from .models import GMSession
from .retrieval import ChatMessage, canon_entry_for_turn, model_messages
from .turn_engine.state import state_block_tokens

__all__ = [
    "SYSTEM_PROMPT",
    "DM_SYSTEM_PROMPT",
    "roll_dice",
    "build_roll_message",
    "build_rules_lookup_message",
    "build_state_request_message",
    "get_system_message",
    "default_mode_for_user",
    "mode_for_user",
    "get_gm_model",
    "get_gm_options",
    "load_rules",
    "ground_turn_in_canon",
    "ground_turn_in_battle",
    "messages_for_model",
    "extract_structured_json",
]

logger = logging.getLogger(__name__)


def get_gm_model() -> str:
    """The Ollama model the web GM talks to (``settings.GM_MODEL``)."""
    return settings.GM_MODEL


def get_gm_options() -> dict[str, int | float]:
    """The ``options`` object sent with every GM ``/api/chat`` call.

    Read from settings on each call rather than at import so
    ``override_settings`` and per-deploy environment values both take
    effect. Built through :class:`GMModelConfig` so the web GM and the CLI
    send the identical shape (issue #324).
    """
    return GMModelConfig(
        model=settings.GM_MODEL,
        num_ctx=settings.GM_NUM_CTX,
        temperature=settings.GM_TEMPERATURE,
    ).options()


def extraction_options() -> dict[str, int | float]:
    """The GM's ``options`` with the temperature pinned for extraction.

    Extraction wants the one right answer, not a creative one, so every
    structured call this app makes -- saving a character (issue #344) and
    reading a turn's changes (issue #356) -- sends the same options.
    """
    return {**get_gm_options(), "temperature": EXTRACTION_TEMPERATURE}


def extract_structured_json(
    build_messages: Callable[..., list[dict[str, str]]],
    schema: dict[str, Any],
    *,
    stop_at: float | None = None,
) -> str:
    """The GM's schema-constrained call: this app's model and options.

    :func:`common.services.ollama_client.chat_structured` owns the call and
    the fallback for a runner that refuses ``format``; this only says which
    model and options the GM sends. Every schema-constrained ask the web GM
    makes comes through here -- the ledger's
    per-turn intent read (issue #356), and ``/fight``'s foe list (issue
    #368) -- so none of them can be left behind when the fallback changes.
    The reading half is :func:`gm.character_schema.parse_extraction_reply`.

    Args:
        build_messages: Builds the call's messages; called with
            ``schema_in_prompt=True`` on the fallback path.
        schema: The JSON schema to constrain the answer to.
        stop_at: A ``time.monotonic()`` past which the call, its fallback
            included, does not hold the model (issue #936).

    Returns:
        The model's reply, unparsed -- each caller owns how it reads and how
        it reports a malformed one.

    Raises:
        StreamDeadlinePassed: ``stop_at`` passed.
    """
    return ollama_client.chat_structured(
        get_gm_model(),
        build_messages,
        schema,
        options=extraction_options(),
        stop_at=stop_at,
    )


def default_mode_for_user(user: object | None = None) -> str:
    """The mode a session this user opens starts in (issue #372).

    The single place the role's default mode is decided, so both paths that
    create a ``GMSession`` -- a first message, and attaching a character
    before one -- open it the same way.

    Args:
        user: The requesting user. ``None`` or an anonymous user is a player.

    Returns:
        One of :data:`gm.engine.GM_MODES`.
    """
    return default_mode_for(is_dm(user))


def mode_for_user(mode: object, user: object | None = None) -> str:
    """Coerce a requested mode, falling back to this user's default.

    Args:
        mode: The requested mode, from a session row, a form post or a query
            string. Anything unrecognised takes the default.
        user: The requesting user, used only to pick the fallback.

    Returns:
        One of :data:`gm.engine.GM_MODES`.
    """
    return normalise_mode(mode, is_game_master=is_dm(user))


def get_system_message(
    user: object | None = None, mode: str | None = None
) -> dict[str, str]:
    """Return the GM system message for the person at this table.

    Two independent questions decide it (issue #372):

    * **What may this person do?** A DM or admin gets the Game Master's
      authority appended (issue #352). The role is read off the signed-in
      user through :func:`battle.access.is_dm` -- the project's one "is this
      an admin or DM?" test -- so nothing typed into the chat, and nothing
      chosen on the page, can reach it.
    * **How should the model behave?** The session's mode: run the game, or
      assist someone running one (issue #353). Anyone may choose it for their
      own session, and choosing it grants nothing.

    Args:
        user: The requesting user. ``None`` or an anonymous user is a player.
        mode: The session's mode. ``None`` takes this role's default, which
            is what the offline and role-only callers want.
    """
    return {
        "role": "system",
        "content": system_prompt_for(is_dm(user), mode),
    }


def load_rules() -> str | None:
    """Load all markdown files from the rules directory into a single string."""
    return load_rules_from_dir(Path(settings.RULES_DIR))


def _degraded_notice(what: str) -> str:
    """The line the player is shown when one grounding block could not be built.

    Args:
        what: What is missing, named the way a player would name it (e.g.
            ``"battle log"``).

    Returns:
        The notice's text.
    """
    return (
        f"The {what} could not be attached to this turn, so the Game Master "
        "answered without it. The error has been logged. Treat anything in the "
        "reply that should have come from it as unverified."
    )


def _ground_or_degrade(what: str, ground: Callable[[], None]) -> str | None:
    """Run one grounding step, degrading to a logged, player-visible notice.

    A hidden block that cannot be built is a bug, and it used to be a fatal
    one: every one of these runs on every turn, before the reply streams, so
    one unrenderable block 500s the current turn and every turn after it in
    that session -- the conversation cannot even be read back from the chat
    (issue #374). Retrieval already answers this way when its index is missing
    (:func:`gm.retrieval.retrieve_for_query`): log it, and carry on with less.

    What is *not* copied from retrieval is the silence. A dropped block is
    exactly the condition under which the model goes back to making numbers up
    (issue #356), so the failure is never swallowed: the traceback is logged
    and the player is told in the chat.

    Args:
        what: What is being attached, for the log line and the notice.
        ground: The grounding step to run.

    Returns:
        The notice for the player, or ``None`` when the step succeeded.
    """
    try:
        ground()
    except Exception:
        logger.exception(
            "Grounding this turn in the %s failed; answering without it", what
        )
        return _degraded_notice(what)
    return None


def ground_turn_in_canon(history: list[ChatMessage], query: str) -> str | None:
    """File this turn's canon excerpts into ``history`` ahead of the player's message.

    Wraps :func:`gm.retrieval.canon_entry_for_turn` with the ``GM_*``
    settings (issues #325, #331). ``history`` must end with the player's
    message; the entry is inserted just before it and persists with the
    session so the model-visible prefix stays cache-friendly. Nothing is
    inserted with ``GM_RETRIEVAL_ENABLED`` off, when retrieval degrades, or
    when every retrieved chunk is already in the session.

    Returns:
        A notice for the player when the excerpts could not be attached, or
        ``None`` (see :func:`_ground_or_degrade`).
    """
    if not settings.GM_RETRIEVAL_ENABLED:
        return None

    def attach() -> None:
        entry = canon_entry_for_turn(
            history,
            query,
            index_path=Path(settings.GM_INDEX_PATH),
            base_url=settings.OLLAMA_BASE_URL,
            top_k=settings.GM_RETRIEVAL_TOP_K,
            max_tokens=settings.GM_RETRIEVAL_MAX_TOKENS,
            num_ctx=settings.GM_NUM_CTX,
        )
        if entry is not None:
            history.insert(len(history) - 1, entry)

    return _ground_or_degrade("rules excerpts", attach)


def ground_turn_in_battle(
    history: list[ChatMessage], session: GMSession | None
) -> str | None:
    """File what the session's hex-board battle has done into ``history``.

    Wraps :func:`gm.battle_report.pending_battle_entries` (issue #345).
    ``history`` must end with the player's message; every entry -- one per
    completed turn, plus the closing summary once the battle is over -- is
    inserted just before it, in order. Nothing is inserted for a session
    that does not exist yet, has no battle, or whose battle has produced
    nothing new since the last turn.

    Returns:
        A notice for the player when the battle log could not be attached, or
        ``None`` (see :func:`_ground_or_degrade`).
    """
    if session is None:
        return None

    def attach() -> None:
        for entry in pending_battle_entries(history, session):
            history.insert(len(history) - 1, entry)

    return _ground_or_degrade("battle log", attach)


class RecapOutOfTime(TimeoutError):
    """A recap the turn stopped waiting for; its call was closed (review of #783)."""


def write_session_recap(
    prompt: str,
    *,
    deadline: float | None = None,
    hang_up: ollama_client.HangUp | None = None,
) -> str:
    """Ask the model for a session recap, in one plain call (issue #579).

    Its own call rather than part of a turn: it is not narration, nothing
    streams from it, and a turn must not wait on it twice.

    Args:
        prompt: What the recap is asked for.
        deadline: ``time.monotonic()`` past which the call is abandoned. The
            model server answers one request at a time, so a recap left
            running after its turn gave up would hold up the next turn
            behind it (review of #783). The read timeout is held to the
            deadline, so a prefill that never reaches its first token is cut
            off too; once the response has begun, its connection is hung up
            at the deadline even while a read waits on a stream that stalled
            after its first tokens (issue #887), and it is closed at the
            deadline between tokens.
        hang_up: Lets the thread waiting on the recap end its call when it
            gives up on it (issue #936). One hang-up serves one call.

    Raises:
        RecapOutOfTime: The deadline passed.
        StreamHungUp: ``hang_up`` was used while the recap was written.
    """
    if deadline is None:
        tokens = ollama_client.stream_chat(
            get_gm_model(),
            [{"role": "user", "content": prompt}],
            options=get_gm_options(),
            hang_up=hang_up,
        )
        return "".join(tokens)
    left = deadline - time.monotonic()
    if left <= 0:
        raise RecapOutOfTime("the turn's time ran out before the recap was asked for")
    tokens = ollama_client.stream_chat(
        get_gm_model(),
        [{"role": "user", "content": prompt}],
        options=get_gm_options(),
        timeout=httpx.Timeout(connect=10.0, read=left, write=10.0, pool=10.0),
        stop_at=deadline,
        hang_up=hang_up,
    )
    written: list[str] = []
    try:
        for token in tokens:
            if time.monotonic() > deadline:
                raise RecapOutOfTime(
                    "the turn's time ran out while the recap was written"
                )
            written.append(token)
    except httpx.ReadTimeout as timed_out:
        raise RecapOutOfTime(
            "the recap's first token did not come in time"
        ) from timed_out
    except StreamDeadlinePassed as stalled:
        raise RecapOutOfTime(
            "the recap's stream stalled and was cut off at the deadline"
        ) from stalled
    finally:
        tokens.close()
    return "".join(written)


def fold_needed(session, history: Sequence[ChatMessage]) -> bool:
    """Whether ``session`` has grown enough to fold its older turns (#579).

    The state block is counted with the history: it is rebuilt whole for
    both of a turn's model calls and rides in the call's last message, which
    the context guard never touches, so the fold is the only thing that can
    make room for it (issue #647).
    """
    if session is None:
        return False
    return summary.should_fold(
        history,
        num_ctx=settings.GM_NUM_CTX,
        state_tokens=state_block_tokens(session),
    )


def write_fold(
    session,
    history: Sequence[ChatMessage],
    *,
    deadline: float | None = None,
    hang_up: ollama_client.HangUp | None = None,
) -> tuple[str, int]:
    """Write the recap a fold needs: one model call, and no database access.

    Kept apart from the write so a turn can run it off its own thread and
    send keep-alives while it waits (issue #783), and bounded by the turn's
    ``deadline`` so the call ends when the turn stops waiting for it.

    Args:
        session: The session whose earlier turns are folded.
        history: The stored history to fold.
        deadline: ``time.monotonic()`` past which the recap is abandoned.
        hang_up: Lets the turn end the recap's call when it stops waiting
            on it before the deadline (issue #935). One hang-up serves the
            one call.

    Returns:
        ``(summary, folded_through)``, as :func:`gm.summary.fold` gives them;
        unchanged when the recap ran out of time.
    """

    def ask_model(prompt: str) -> str:
        if deadline is None:
            return write_session_recap(prompt, hang_up=hang_up)
        return write_session_recap(prompt, deadline=deadline, hang_up=hang_up)

    return summary.fold(
        history,
        previous_summary=session.summary,
        already_folded=session.summary_covers,
        ask_model=ask_model,
    )


def save_fold(session, written: str, covered: int) -> bool:
    """Keep a fold's recap on the session, under a lock on its row (#783).

    The recap is written from the row as it stands when locked, and only when
    it reaches further than what the row already holds: a fold written from
    an older copy of the session never replaces a newer one. ``session`` is
    updated to what the row holds afterwards.

    Returns:
        Whether the recap was kept.
    """
    with transaction.atomic():
        locked = GMSession.objects.select_for_update().get(pk=session.pk)
        kept = covered > locked.summary_covers
        if kept:
            locked.summary = written
            locked.summary_covers = covered
            locked.save(update_fields=["summary", "summary_covers"])
    session.summary = locked.summary
    session.summary_covers = locked.summary_covers
    return kept


def folded_for_model(session, history: list[ChatMessage]) -> list[ChatMessage]:
    """``history`` as the model should see it, with the session's recap in."""
    if session is None:
        return history
    return summary.folded_history(
        history, summary=session.summary, folded_through=session.summary_covers
    )


def messages_for_model(history: list[ChatMessage]) -> list[dict[str, str]]:
    """The plain message list Ollama receives for ``history``.

    :func:`gm.retrieval.model_messages` with ``settings.GM_NUM_CTX``: strips
    the bookkeeping keys and applies the context guard.
    """
    return model_messages(history, num_ctx=settings.GM_NUM_CTX)
