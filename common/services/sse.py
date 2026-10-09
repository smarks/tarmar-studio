"""SSE streaming helpers for Ollama responses."""

import re


def strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks from model output.

    Also drops a trailing unterminated block: an opening `<think>` with no
    matching `</think>` anywhere after it. That mirrors what ThinkingFilter
    already does for a stream that ends mid-thinking-block -- once inside an
    unclosed block, everything through the end is thinking content, not
    visible output, even if it contains further literal "<think>" text.
    Whichever `<think>` remains after the regex above has removed every
    complete pair is therefore always the start of a single dangling block
    that runs to the end of the string (if a `</think>` existed anywhere
    after it, the regex would have consumed it), so cutting at that point is
    the correct -- not just the simplest -- fix.
    """
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    idx = text.find(_OPEN_TAG)
    if idx != -1:
        text = text[:idx]
    return text.strip()


_OPEN_TAG = "<think>"
_CLOSE_TAG = "</think>"


def partial_suffix_len(text: str, tag: str) -> int:
    """Length of the longest suffix of `text` that is a proper prefix of `tag`.

    That suffix could still grow into a full `tag` once more tokens arrive, so
    the caller holds it back rather than emitting (or discarding) it early.
    Returns 0 if no such suffix exists. Bounded by len(tag) - 1, so it can
    never grow the pending buffer without limit.

    Public because it is the project's one "could this still become a tag?"
    rule: :class:`ThinkingFilter` holds back a partial ``<think>`` tag with it.

    Args:
        text: The text seen so far.
        tag: The marker being watched for.

    Returns:
        How many trailing characters of ``text`` the caller must hold back.
    """
    max_len = min(len(text), len(tag) - 1)
    for length in range(max_len, 0, -1):
        if text.endswith(tag[:length]):
            return length
    return 0


class ThinkingFilter:
    """Token-level <think>...</think> suppressor for streamed model output.

    Feed each streamed token; returns the text to show the user (possibly
    spanning multiple tokens' worth of tags), or None if there is nothing to
    show yet. Accumulate the raw tokens separately and pass the full text
    through strip_thinking() to persist.

    A tag can be split across tokens by the model/streaming layer (e.g. "<thi"
    then "nk>"), so tokens are not searched for tags in isolation: a minimal
    suffix that could still be the start of a tag is held back and prefixed
    onto the next token before searching again. That suffix is at most
    len("</think>") - 1 characters, so buffering stays bounded and the stream
    still flows token-by-token in the common case.

    Shared by the web GM (gm/views.py) and the offline CLI (cli/gm.py).
    """

    def __init__(self) -> None:
        self._in_thinking = False
        self._pending = ""

    def feed(self, token: str) -> str | None:
        text = self._pending + token
        self._pending = ""
        visible_parts: list[str] = []

        while True:
            if self._in_thinking:
                idx = text.find(_CLOSE_TAG)
                if idx == -1:
                    # No close tag yet. Thinking content is discarded; only a
                    # suffix that could still become "</think>" is held back.
                    hold = partial_suffix_len(text, _CLOSE_TAG)
                    if hold:
                        self._pending = text[-hold:]
                    break
                text = text[idx + len(_CLOSE_TAG) :]
                self._in_thinking = False
                continue

            idx = text.find(_OPEN_TAG)
            if idx == -1:
                hold = partial_suffix_len(text, _OPEN_TAG)
                if hold:
                    visible_parts.append(text[:-hold])
                    self._pending = text[-hold:]
                else:
                    visible_parts.append(text)
                break
            visible_parts.append(text[:idx])
            text = text[idx + len(_OPEN_TAG) :]
            self._in_thinking = True

        visible = "".join(visible_parts)
        return visible if visible else None
