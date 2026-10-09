"""How a session's stored history meets the turn engine (issue #590).

The model reads only the real conversation: the players' and Game Master's
messages, the narrations, and the world it was grounded in (canon excerpts,
the hex-board battle's log, a mode change). Character state reaches it in the
state block of each call's last message instead, so the old hidden sheet and
ledger blocks, and the chips a turn files for the page, are left out.
"""

from collections.abc import Sequence
from typing import Any

from ..retrieval import (
    INTERRUPTED_KIND,
    LEDGER_KIND,
    PROCEDURE_KIND,
    READER_ONLY_KINDS,
    ROLL_KIND,
    SHEET_KIND,
    ChatMessage,
)

#: Stored entries the plan engine never sends to the model.
NOT_FOR_THE_MODEL = frozenset(
    {
        SHEET_KIND,
        LEDGER_KIND,
        ROLL_KIND,
        PROCEDURE_KIND,
        INTERRUPTED_KIND,
        # Notices for the reader alone: a fight's end (issue #785), whose
        # hidden closing summary the model reads instead, and a player's
        # withdrawal (issue #871).
        *READER_ONLY_KINDS,
    }
)

#: What the results say before the results of a turn resumed after a break.
ALREADY_DONE = "Already done when this message was first sent"


def model_history(history: Sequence[ChatMessage]) -> list[ChatMessage]:
    """The stored entries the model reads, in order."""
    return [entry for entry in history if entry.get("kind") not in NOT_FOR_THE_MODEL]


def latest_message_index(history: Sequence[ChatMessage]) -> int:
    """Where the message this turn answers sits: the last plain user entry.

    Raises:
        ValueError: The history holds no user message.
    """
    for index in range(len(history) - 1, -1, -1):
        entry = history[index]
        if entry.get("role") == "user" and not entry.get("kind"):
            return index
    raise ValueError("the session has no message to answer")


def results_filed_after(history: Sequence[ChatMessage], index: int) -> tuple[str, ...]:
    """The results already filed for the message at ``index``, as sentences.

    A turn that broke off after its actions ran keeps their chips (issue
    #410); answering that message again narrates them rather than running
    them twice.
    """
    return tuple(
        f"{ALREADY_DONE}: {entry.get('content', '')}"
        for entry in history[index + 1 :]
        if entry.get("kind") in (PROCEDURE_KIND, ROLL_KIND)
    )


def procedure_entry(result: str, frame: dict[str, Any]) -> ChatMessage:
    """A character change's chips, as the session keeps them for the page."""
    return {
        "role": "user",
        "content": result,
        "kind": PROCEDURE_KIND,
        "procedure": frame,
    }


def roll_entry(frame: dict[str, Any]) -> ChatMessage:
    """A die the plan rolled, as the session keeps it for the page."""
    return {
        "role": "user",
        "content": str(frame.get("line", "")),
        "kind": ROLL_KIND,
        "roll": frame,
    }
