"""Keep what the player typed beside each stored /roll prompt (issue #838).

Before #838 a ``/roll`` was stored as its adjudication prompt alone --
"[The player rolled 3d6 and got: 4, 5, 6 = 15. Please adjudicate this roll in
the current context.]" -- so a reload showed that as something the player
said, and a session opened by a roll was named after it. The prompt stays:
the model reads it on every later turn, and its faces and total are part of
the story. This adds the line typed (``typed``) and the dice
(``player_roll``) beside it, which the chat page and the session's name
read, and names such a session after its first real line again.

Rows touched: every session holding a stored roll prompt. On a copy of the
main checkout's local database, 2026-09-29: none of its 213 sessions, so it
was run both ways there with one seeded roll session added, and against the
cases in ``gm/test_command_history.py``. The reverse takes the two keys off
again and restores a name this migration changed.
"""

import json
import re

from django.db import migrations

#: ``gm.messages.build_roll_message`` as it stood when this was written.
ROLL_PROMPT = re.compile(
    r"^\[The player rolled (?P<expression>\S.*?) and got: "
    r"(?P<faces>-?\d+(?:, -?\d+)*) = (?P<total>-?\d+)\. "
    r"Please adjudicate this roll in the current context\.\]$"
)
TYPED_KEY = "typed"
PLAYER_ROLL_KEY = "player_roll"

# ``gm.models.name_from_history`` and what it reads, as they stood when this
# migration was written. Copied rather than imported, so a later change to
# how sessions are named cannot change what this migration did.
SESSION_NAME_MAX_LENGTH = 48
PLACEHOLDER_SESSION_NAME = "New Session"
HIDDEN_KINDS = frozenset(
    {
        "canon",
        "sheet",
        "battle",
        "ledger",
        "roll",
        "procedure",
        "mode",
        "interrupted",
        "recap",
    }
)


def with_typed(entry: dict) -> dict:
    """``entry`` with the line typed and the dice, when it is a stored /roll."""
    if entry.get("role") != "user" or entry.get("kind") or entry.get(TYPED_KEY):
        return entry
    matched = ROLL_PROMPT.match(str(entry.get("content", "")))
    if matched is None:
        return entry
    expression = matched["expression"]
    faces = [int(face) for face in matched["faces"].split(", ")]
    total = int(matched["total"])
    return {
        **entry,
        TYPED_KEY: f"/roll {expression}",
        PLAYER_ROLL_KEY: {
            "expression": expression,
            "rolls": faces,
            "total": total,
            "line": f"🎲 {expression}: [{matched['faces']}] = {total}",
        },
    }


def without_typed(entry: dict) -> dict:
    """``entry`` as it was before :func:`with_typed`."""
    if TYPED_KEY not in entry or not ROLL_PROMPT.match(str(entry.get("content", ""))):
        return entry
    return {
        key: value
        for key, value in entry.items()
        if key not in (TYPED_KEY, PLAYER_ROLL_KEY)
    }


def name_from_history(history: list[dict]) -> str:
    """A session's name from the first thing the player typed in it."""
    for message in history:
        if message.get("role") != "user" or message.get("kind") in HIDDEN_KINDS:
            continue
        typed = message.get(TYPED_KEY)
        text = " ".join(str(typed or message.get("content") or "").split())
        if not text:
            continue
        if len(text) <= SESSION_NAME_MAX_LENGTH:
            return text
        cut = text[:SESSION_NAME_MAX_LENGTH]
        head, _space, tail = cut.rpartition(" ")
        trimmed = head if head and len(tail) < SESSION_NAME_MAX_LENGTH // 2 else cut
        return f"{trimmed.rstrip()}…"
    return PLACEHOLDER_SESSION_NAME


def _rewrite(apps, change) -> None:
    GMSession = apps.get_model("gm", "GMSession")
    for session in GMSession.objects.only("pk", "name", "history_json").iterator():
        history = json.loads(session.history_json or "[]")
        rewritten = [change(entry) for entry in history]
        if rewritten == history:
            continue
        fields = ["history_json"]
        session.history_json = json.dumps(rewritten)
        if session.name == name_from_history(history):
            session.name = name_from_history(rewritten)
            fields.append("name")
        session.save(update_fields=fields)


def keep_rolls_as_typed(apps, schema_editor) -> None:
    """Add the typed line and the dice to each stored /roll prompt."""
    _rewrite(apps, with_typed)


def drop_typed_rolls(apps, schema_editor) -> None:
    """Take them off again, restoring any name the forward step changed."""
    _rewrite(apps, without_typed)


class Migration(migrations.Migration):
    dependencies = [
        ("gm", "0021_command_entries_stored_as_typed"),
    ]

    operations = [
        migrations.RunPython(keep_rolls_as_typed, drop_typed_rolls),
    ]
