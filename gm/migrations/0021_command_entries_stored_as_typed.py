"""Rewrite stored /rules and /state prompts as the commands typed (issue #758).

Before #758 the server stored the prompt it wrote for ``/rules`` -- "I need a
precise rules lookup. Here are the full rules for reference: ..." and the
whole rules tree after it -- and for ``/state`` as the player's own message.
A reload showed them as something the player said, every later turn sent the
rules tree to the model again, and a session opened by either was named
after the prompt. This rewrites each such entry to the line the player typed
and names such a session after its first real line again.

Forward only in effect: the reverse leaves the commands in place, since the
rules text a prompt carried is not worth restoring and could not be told
apart from the rules of the day it is run.
"""

import json

from django.db import migrations

RULES_PROMPT_PREFIX = (
    "I need a precise rules lookup. Here are the full rules for reference:"
)
RULES_QUERY_MARKER = "\n\nBased on these rules: "
#: The /state prompt exactly as ``build_state_request_message`` wrote it, so a
#: player's own message that happens to start the same way is left alone.
STATE_PROMPT = (
    "Please summarize the current game state: "
    "character stats (attributes, Fatigue, Body, Mana if known), "
    "inventory, location, active effects, and any ongoing "
    "situations or quests."
)


def as_typed(entry: dict) -> dict:
    """``entry`` as the player typed it, when it is a stored command prompt."""
    if entry.get("role") != "user" or entry.get("kind"):
        return entry
    content = str(entry.get("content", ""))
    if content.startswith(RULES_PROMPT_PREFIX) and RULES_QUERY_MARKER in content:
        query = content.rsplit(RULES_QUERY_MARKER, 1)[1].strip()
        return {**entry, "content": f"/rules {query}"}
    if content == STATE_PROMPT:
        return {**entry, "content": "/state"}
    return entry


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


def name_from_history(history: list[dict]) -> str:
    """A session's name from the first thing the player said in it."""
    for message in history:
        if message.get("role") != "user" or message.get("kind") in HIDDEN_KINDS:
            continue
        text = " ".join(str(message.get("content") or "").split())
        if not text:
            continue
        if len(text) <= SESSION_NAME_MAX_LENGTH:
            return text
        cut = text[:SESSION_NAME_MAX_LENGTH]
        head, _space, tail = cut.rpartition(" ")
        trimmed = head if head and len(tail) < SESSION_NAME_MAX_LENGTH // 2 else cut
        return f"{trimmed.rstrip()}…"
    return PLACEHOLDER_SESSION_NAME


def rewrite_command_prompts(apps, schema_editor) -> None:
    """Store each command prompt as its command, and rename what it named."""
    GMSession = apps.get_model("gm", "GMSession")
    for session in GMSession.objects.only("pk", "name", "history_json").iterator():
        history = json.loads(session.history_json or "[]")
        rewritten = [as_typed(entry) for entry in history]
        if rewritten == history:
            continue
        fields = ["history_json"]
        session.history_json = json.dumps(rewritten)
        if session.name == name_from_history(history):
            session.name = name_from_history(rewritten)
            fields.append("name")
        session.save(update_fields=fields)


class Migration(migrations.Migration):
    dependencies = [
        ("gm", "0020_gmsession_continued_from"),
    ]

    operations = [
        migrations.RunPython(rewrite_command_prompts, migrations.RunPython.noop),
    ]
