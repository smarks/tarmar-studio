"""Folding the older turns of a long session into a recap (issue #579).

A session's history only ever grows. The context guard in
:mod:`gm.retrieval` drops canon excerpts to make room and never drops a
player or GM turn, so once the excerpts are gone it hands the model server a
conversation larger than the window and lets it truncate. The Game Master's
own session 36 reached 49,300 tokens of 65,536 with all 260 messages still
being sent, which is perhaps thirty turns from finding out what that
truncation does to the system prompt.

So the oldest turns are folded into a recap, written once and kept on the
session, and the recent ones are sent as they are.

What the recap is *for* is narrower than it looks. Scene, place, the clock,
who is in play, what they are carrying and what is still open are records
since #579, and the state block re-asserts every one of them on every call.
The recap carries the story -- what the party did and why it mattered -- and
none of the facts the records already own. That is deliberate: a recap is
the one thing here the model writes rather than the code, so it is kept away
from anything a wrong number could damage.
"""

import logging
from collections.abc import Callable, Sequence

from .retrieval import (
    RECAP_KIND,
    REPLY_RESERVE_TOKENS,
    ChatMessage,
    estimate_message_tokens,
    is_played_entry,
)

logger = logging.getLogger(__name__)

#: Fold when the history alone passes this share of the usable window. Well
#: before the guard runs out of excerpts, so folding is never the thing that
#: rescues a turn already at the edge.
FOLD_AT_FRACTION = 0.6

#: Turns kept verbatim after a fold. The model narrates from the recent past
#: in detail; this is the part it would be wrong to paraphrase.
KEEP_RECENT_ENTRIES = 24

#: The longest a recap may be. Long enough for a session's shape, short
#: enough that folding is worth doing.
MAX_SUMMARY_CHARS = 2000

#: How the recap is introduced in the conversation.
SUMMARY_PREFIX = "Earlier in this session:"

#: What the model is asked for.
SUMMARY_REQUEST = (
    "Write a recap of the part of this session below, for your own use as "
    "its Game Master. Say what the party did, what happened to them, and "
    "what it led to, in at most 200 words of plain prose.\n"
    "- Story only. Leave out attributes, skill points, dice, hit points, "
    "dates and any other figure: those are held as records and given to you "
    "separately, and a number here could only contradict them.\n"
    "- Leave out anything you are unsure of. A short recap is better than a "
    "confident one that is wrong.\n"
    "- No headings, no lists, no preamble -- the recap itself and nothing "
    "else."
)


def usable_budget(num_ctx: int) -> int:
    """The tokens a conversation may take, once the reply's room is kept."""
    return max(num_ctx - REPLY_RESERVE_TOKENS, 1)


def history_tokens(history: Sequence[ChatMessage]) -> int:
    """What ``history`` is estimated to cost."""
    return sum(estimate_message_tokens(message) for message in history)


def should_fold(
    history: Sequence[ChatMessage], *, num_ctx: int, state_tokens: int = 0
) -> bool:
    """Whether this session has grown enough to fold its older turns.

    Args:
        history: The session's stored conversation.
        num_ctx: The model's context window in tokens.
        state_tokens: What the state block will cost on top of the history
            (issue #647). It rides in the last message of each of a turn's
            two model calls, appended after the context guard has run, so
            nothing else in the budget accounts for it; counting it here is
            what makes the fold happen before the block is what overflows
            the window.
    """
    if fold_point(history) is None:
        return False
    return (
        history_tokens(history) + state_tokens
        > usable_budget(num_ctx) * FOLD_AT_FRACTION
    )


def fold_point(history: Sequence[ChatMessage]) -> int | None:
    """The index up to which entries may be folded, or None when too short.

    The system message stays, the most recent :data:`KEEP_RECENT_ENTRIES`
    stay, and what lies between is what a recap replaces. Hidden bookkeeping
    entries are counted like any other: they are what the guard was dropping
    already.
    """
    first = 1 if history and history[0].get("role") == "system" else 0
    last = len(history) - KEEP_RECENT_ENTRIES
    if last - first < KEEP_RECENT_ENTRIES // 2:
        return None
    return last


def for_recap(message: ChatMessage) -> bool:
    """Whether a recap reads ``message``: what was said, not bookkeeping.

    Neither what the model alone reads nor what the reader alone sees -- the
    notice of how a fight ended would come back as the Game Master's own
    words (issue #785).
    """
    return is_played_entry(message)


def entries_to_fold(
    history: Sequence[ChatMessage], *, already_folded: int
) -> list[ChatMessage]:
    """The entries a new recap must cover, beyond the ones already covered."""
    cut = fold_point(history)
    if cut is None or cut <= already_folded:
        return []
    return [message for message in history[already_folded:cut] if for_recap(message)]


def as_prose(entries: Sequence[ChatMessage]) -> str:
    """The entries as the summarising call reads them."""
    return "\n\n".join(
        f"{'The player' if message.get('role') == 'user' else 'You'}: "
        f"{str(message.get('content', '')).strip()}"
        for message in entries
        if str(message.get("content", "")).strip()
    )


def summary_message(summary: str) -> ChatMessage:
    """The recap, as the one history entry that carries it.

    It takes the ``user`` role and a :data:`gm.retrieval.RECAP_KIND` marker,
    for the two reasons :func:`gm.messages.build_mode_switch_message` gives
    for the same choice: every chat template renders a user message, whereas
    a mid-conversation ``system`` message is honoured only if the template
    happens to, and the marker says plainly that this is bookkeeping rather
    than something a person said.

    It was a ``system`` message until issue #631, where the cost of that
    showed: a stored history never holds a system entry, so the recap landed
    at index 0 and ``gm_stream``'s "is the first entry a system message?"
    guard read it as the GM's own prompt and stopped sending the real one --
    on every turn of every folded session, the plan schema included.
    """
    return {
        "role": "user",
        "kind": RECAP_KIND,
        "content": f"{SUMMARY_PREFIX}\n{summary}",
    }


def fold(
    history: Sequence[ChatMessage],
    *,
    previous_summary: str,
    already_folded: int,
    ask_model: Callable[[str], str],
) -> tuple[str, int]:
    """Write the recap covering the older turns, and say how far it reaches.

    Args:
        history: The session's stored conversation.
        previous_summary: The recap so far, folded into the new one.
        already_folded: How many entries the previous recap covered.
        ask_model: Sends a prompt and returns the reply.

    Returns:
        ``(summary, folded_through)``. Unchanged when there is nothing to
        fold, or when the model gives nothing usable -- a failed recap leaves
        the session exactly as it was rather than losing its past.
    """
    entries = entries_to_fold(history, already_folded=already_folded)
    if not entries:
        return previous_summary, already_folded
    written = _write_recap(previous_summary, entries, ask_model)
    if not written:
        return previous_summary, already_folded
    cut = fold_point(history)
    assert cut is not None  # entries_to_fold returned some, so there is a point
    logger.info(
        "GM session recap written: %d entries folded into %d characters",
        cut - already_folded,
        len(written),
    )
    return written[:MAX_SUMMARY_CHARS], cut


def _write_recap(
    previous_summary: str,
    entries: Sequence[ChatMessage],
    ask_model: Callable[[str], str],
) -> str:
    """Ask for a recap of ``entries`` that takes ``previous_summary`` in.

    Returns:
        The recap, or ``""`` when the model failed or gave nothing, which is
        logged: a recap that could not be written never costs anything else.
    """
    asked = SUMMARY_REQUEST
    if previous_summary:
        asked += f"\n\nThe recap so far, which yours replaces:\n{previous_summary}"
    asked += f"\n\nThe session:\n{as_prose(entries)}"
    try:
        written = ask_model(asked).strip()
    except Exception:
        logger.exception("GM session recap could not be written; history kept whole")
        return ""
    if not written:
        logger.warning("GM session recap came back empty; history kept whole")
    return written


def recap_of_whole_session(
    history: Sequence[ChatMessage],
    *,
    previous_summary: str,
    already_folded: int,
    ask_model: Callable[[str], str],
    num_ctx: int,
) -> str:
    """A recap of a whole session, written the way the fold writes one (#761).

    A session's own recap covers only the turns its last fold reached; the
    two dozen after it are sent as they are. A new session continuing this
    one has none of them, so its opening recap is written over everything:
    the recap so far taken in, and every visible entry after it.

    Args:
        history: The session's stored conversation.
        previous_summary: Its recap so far, possibly empty.
        already_folded: How many entries that recap covers.
        ask_model: Sends a prompt and returns the reply.
        num_ctx: The model's context window. A request that would not fit
            the window the fold itself works within is not sent, so a long
            unfolded tail cannot be truncated silently by the model server.

    Returns:
        The recap, or ``""`` when it could not be written or would not fit.
        A session with nothing after its last fold returns that recap
        unchanged.
    """
    entries = [message for message in history[already_folded:] if for_recap(message)]
    if not entries:
        return previous_summary
    asked = history_tokens(
        [*entries, {"content": f"{SUMMARY_REQUEST}\n{previous_summary}"}]
    )
    if asked > usable_budget(num_ctx):
        logger.warning(
            "GM continuation recap not asked for: %d tokens over a %d budget",
            asked,
            usable_budget(num_ctx),
        )
        return ""
    return _write_recap(previous_summary, entries, ask_model)[:MAX_SUMMARY_CHARS]


def folded_history(
    history: Sequence[ChatMessage], *, summary: str, folded_through: int
) -> list[ChatMessage]:
    """``history`` with its folded entries replaced by the recap.

    A stored history holds no system message -- ``_persist_turns`` strips
    them -- so ordinarily the recap comes first and the unfolded turns
    follow. A caller that does lead with a system message (the offline
    callers) keeps it at the front, with the recap after it. Either way the
    recap is marked, so ``gm_stream`` still knows it has to put the GM's
    system prompt in front of the lot (issue #631). A session with no recap
    is returned unchanged, so nothing about a short session moves.
    """
    if not summary or folded_through <= 0:
        return list(history)
    head = list(history[:1]) if history and history[0].get("role") == "system" else []
    kept = list(history[max(folded_through, len(head)) :])
    return [*head, summary_message(summary), *kept]
