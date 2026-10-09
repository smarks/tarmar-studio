"""The fixed user messages the GM's slash commands send.

``/state``, ``/rules`` and ``/roll`` do not pass the player's words straight
to the model: each wraps them in a sentence that tells the GM what kind of
turn this is. That wording was written out twice -- once in ``gm/views.py``
by way of ``gm/services.py``, once inline in ``cli/gm.py`` -- so the offline
session and the web session were asking the same model two subtly different
questions and nothing kept them in step (issue #337).

Django-free on purpose, like :mod:`gm.engine` and :mod:`gm.character_schema`:
the offline CLI imports it without a settings file. ``gm.services``
re-exports these three so the web side's import path is unchanged.

It also holds the one message nobody types: the hidden note filed when a
session changes mode (issue #372).
"""

from collections.abc import Mapping, Sequence
from typing import Any

from .engine import MODE_AIDE, MODE_PLAY
from .retrieval import INTERRUPTED_KIND, MODE_KIND

# What each mode is called where the model reads it. Fixed strings so the
# note is byte-identical every time the same switch is made.
_MODE_DESCRIPTIONS: dict[str, str] = {
    MODE_PLAY: (
        "you are now running the game yourself: set the scene, play the "
        "world and its people, ask what they do, and call for the rolls the "
        "situation needs"
    ),
    MODE_AIDE: (
        "you are now the aide to a Game Master running the game at a table "
        "you cannot see: answer what is asked, at the length the answer "
        "needs, and stop"
    ),
}


def build_mode_switch_message(mode: str) -> dict[str, str]:
    """Build the hidden note filed when a session changes mode (issue #372).

    A switch keeps the conversation, so the model is left holding a transcript
    of itself speaking in the manner it has just been told to drop. The system
    prompt says the new thing, but it is thousands of tokens back and the last
    twenty turns all demonstrate the old one. This note sits at the seam and
    names the change, so the shift reads as an instruction rather than as a
    contradiction the model has to resolve on its own.

    It uses the ``user`` role for the same reason the canon block does: every
    chat template renders consecutive user messages, whereas a
    mid-conversation ``system`` message depends on the model's template
    honouring it.

    Args:
        mode: The mode now in force, one of :data:`gm.engine.GM_MODES`.

    Returns:
        The history entry, marked hidden from readers.

    Raises:
        KeyError: When ``mode`` is not a known mode. Callers normalise with
            :func:`gm.engine.normalise_mode` first, so reaching this means a
            new mode was added without wording to describe it.
    """
    return {
        "role": "user",
        "content": (
            "The person at this session has changed how they want to use "
            f"you, and the change takes effect from here: {_MODE_DESCRIPTIONS[mode]}. "
            "Everything above stays true as a record of what has happened; "
            "how you answer from now on follows this note and the standing "
            "instructions at the top of the conversation. Do not remark on "
            "the change or apologise for how you were answering before."
        ),
        "kind": MODE_KIND,
    }


# How a turn broke off, as the note's opening sentence. The page's connection
# going is one way (issue #410); the turn outrunning
# ``settings.GM_STREAM_BUDGET_SECONDS`` is the other (issue #553), and a note
# saying the connection broke when in fact the clock ran out tells the model
# something untrue about its own session.
INTERRUPTED_BY_CONNECTION = (
    "The connection to the player's page broke while you were answering the "
    "player's message above"
)
INTERRUPTED_BY_BUDGET = (
    "The turn answering the player's message above ran out of time and was cut short"
)


def build_interrupted_turn_message(
    opening: str = INTERRUPTED_BY_CONNECTION,
) -> dict[str, str]:
    """Build the hidden note filed when a turn breaks off part-way (issue #410).

    Filed after the procedures and rolls the server had already run for the
    player's message, whether the page's connection went (issue #410) or the
    turn ran out of time (issue #553), before the reply was finished. Those
    results changed the draft and cannot be taken back, so the note says they
    stand. It stays in the session, so it is worded to be true on every later
    turn too.

    Args:
        opening: How the turn broke off -- :data:`INTERRUPTED_BY_CONNECTION`
            or :data:`INTERRUPTED_BY_BUDGET`.

    Returns:
        The history entry, marked hidden from readers.
    """
    return {
        "role": "user",
        "content": (
            f"{opening}, after the server had already run the procedures and "
            "rolls filed for it. Those results are recorded and stand. If the "
            "player sends that message again, answer it from those results: do "
            "not ask for the same procedures or rolls again."
        ),
        "kind": INTERRUPTED_KIND,
    }


def cut_short_notice(
    procedures: Sequence[Mapping[str, Any]], gm_rolls: Sequence[Mapping[str, Any]]
) -> str:
    """What the player is told a cut-short turn kept (issue #553).

    The budget ends the turn between the model's tokens, long after the dice
    fell and the ledger was computed. Those results stand, so the player is
    told them in the same server-built words the chips carried, rather than
    being left to read an empty reply and assume the whole turn was thrown
    away -- which is how a Game Master's stated scores got silently rolled
    again on the next message.

    Args:
        procedures: The procedure frame payloads the turn produced, run and
            refused alike.
        gm_rolls: The roll frame payloads the turn produced.

    Returns:
        The notice, or ``""`` when the turn ran nothing to keep.
    """
    kept = [str(roll.get("line") or "") for roll in gm_rolls]
    kept += [
        str(procedure.get("label") or "")
        for procedure in procedures
        if procedure.get("ran")
    ]
    kept = [line for line in kept if line]
    if not kept:
        return ""
    return (
        "That turn was cut short before the Game Master finished writing. What "
        "the server had already worked out for it is recorded and stands: "
        f"{'; '.join(kept)}."
    )


def resumes_interrupted_turn(history: list, user_message: str) -> bool:
    """Whether sending ``user_message`` again picks up a turn that broke off.

    True when the session's last entry is the note
    :func:`build_interrupted_turn_message` files and the player message it
    follows is ``user_message`` (issue #410).

    Args:
        history: The session's stored history.
        user_message: The message being sent as a retry.
    """
    if not history or history[-1].get("kind") != INTERRUPTED_KIND:
        return False
    for entry in reversed(history):
        if entry.get("role") == "user" and not entry.get("kind"):
            return entry.get("content") == user_message
    return False


def build_rules_lookup_message(query: str, rules_text: str) -> dict[str, str]:
    """Build a user message that injects full rules text for precise lookup.

    Args:
        query: What the player asked.
        rules_text: The whole rules tree, from ``gm.engine.load_rules_from_dir``.
    """
    return {
        "role": "user",
        "content": (
            f"I need a precise rules lookup. Here are the full "
            f"rules for reference:\n\n{rules_text}\n\n"
            f"Based on these rules: {query}"
        ),
    }


def build_state_request_message() -> dict[str, str]:
    """Build a user message asking the GM to summarize game state."""
    return {
        "role": "user",
        "content": (
            "Please summarize the current game state: "
            "character stats (attributes, Fatigue, Body, Mana if known), "
            "inventory, location, active effects, and any ongoing "
            "situations or quests."
        ),
    }


def build_roll_message(expression: str, rolls: list[int], total: int) -> dict[str, str]:
    """Build a user message feeding a dice roll to the GM for adjudication.

    Args:
        expression: The dice expression as the player typed it, e.g. ``3d6+1``.
        rolls: The individual die results.
        total: Their sum, plus any modifier in the expression.
    """
    roll_str = ", ".join(str(r) for r in rolls)
    return {
        "role": "user",
        "content": (
            f"[The player rolled {expression} and got: {roll_str} = {total}. "
            "Please adjudicate this roll in the current context.]"
        ),
    }


#: The key a stored message keeps the line the player typed under, when the
#: model reads a different text for it (issue #838). Readers -- the chat page,
#: the session's name -- show this; the model is sent ``content`` as ever.
TYPED_KEY = "typed"
#: The key a stored ``/roll`` keeps its dice under, so a reloaded page shows
#: the same chip the turn showed live, from the dice the server rolled.
PLAYER_ROLL_KEY = "player_roll"


def player_roll_line(expression: str, rolls: Sequence[int], total: int) -> str:
    """The chip a ``/roll`` shows: ``🎲 3d6: [4, 5, 6] = 15`` (issues #175, #838).

    Built once, server-side, so the live chip and the reloaded one are the
    same text and the page never formats a roll.
    """
    faces = ", ".join(str(face) for face in rolls)
    return f"🎲 {expression}: [{faces}] = {total}"


def stored_roll_message(
    expression: str, rolls: list[int], total: int
) -> dict[str, Any]:
    """A ``/roll`` as the session stores it (issue #838).

    ``content`` is :func:`build_roll_message`'s adjudication prompt, which the
    model reads on this turn and every later one: it carries the faces and
    the total the story goes on from, so it stays word for word what the
    model has always been sent. What the player typed rides beside it under
    :data:`TYPED_KEY`, and the dice under :data:`PLAYER_ROLL_KEY`, so a
    reload shows ``/roll 3d6`` and its chip rather than the prompt as if the
    player had said it.

    Args:
        expression: The dice expression as the player typed it.
        rolls: The individual die results.
        total: Their sum, plus any modifier in the expression.
    """
    return {
        **build_roll_message(expression, rolls, total),
        TYPED_KEY: f"/roll {expression}",
        PLAYER_ROLL_KEY: {
            "expression": expression,
            "rolls": list(rolls),
            "total": total,
            "line": player_roll_line(expression, rolls, total),
        },
    }


def as_typed(message: Mapping[str, Any]) -> str:
    """What a reader is shown for a stored message: the line typed, if kept."""
    typed = message.get(TYPED_KEY)
    return str(typed) if typed else str(message.get("content") or "")
