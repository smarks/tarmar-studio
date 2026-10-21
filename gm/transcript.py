"""A GM session as one document, for whoever has to debug it (issue #389).

Everything the server holds about a session goes in: every history entry in
order, the hidden ones marked as hidden; each turn's record beside the
messages it wrote, including turns that failed and wrote none; the character
draft as stored; and every distinct system prompt the turns ran with, once.

One builder, three readers: the download the chat page offers, its JSON twin
for tools, and ``manage.py gm_transcript`` for reading it over ssh.
"""

import json
from dataclasses import dataclass
from typing import Any

from django.utils import timezone
from django.utils.text import slugify

from common.context_processors import git_info
from common.instants import labelled_instant

from .messages import TYPED_KEY
from .models import GMEvent, GMSession, GMTurnRecord
from .retrieval import (
    ChatMessage,
    is_hidden_message,
    is_procedure_message,
    is_roll_message,
)

RULE = "=" * 78
TURN_RULE = "-" * 78

# How much of a prompt hash names it in the turn headers; the appendix keys
# the full text by the same prefix.
SHORT_HASH_LENGTH = 16

VISIBLE = "visible"
CHIP = "hidden message, shown to the player as a chip"
#: What a model call's event keeps in a transcript: enough to read the call,
#: not the whole history again (the database keeps that, for replay).
REQUEST_SUMMARY_KEYS = ("call", "model", "prefix", "prefix_messages")
HIDDEN = "HIDDEN, model only"
#: A /roll (issue #838): the player sees the line under ``typed`` and its
#: chip; the model reads the entry's text.
TYPED = "shown to the player as typed; the model reads the text below"

# Keys every history entry carries and the transcript prints on its own line;
# anything else on an entry is printed under it as data.
ENTRY_KEYS = ("role", "content", "kind")


def session_characters(session: GMSession) -> list[dict[str, Any]]:
    """The session's characters as stored now, with labels (issue #571).

    Read from the character records the session is attached to, so a
    transcript shows exactly what the ledger and the pane show.
    """
    from .roster import roster

    return [
        {
            "id": entry.character.pk,
            "label": entry.label,
            "complete": entry.character.is_complete,
            **entry.values,
        }
        for entry in roster(session)
    ]


def visibility(message: ChatMessage) -> str:
    """Who can see ``message``: the player, the player as a chip, or the model only."""
    if is_roll_message(message) or is_procedure_message(message):
        return CHIP
    if is_hidden_message(message):
        return HIDDEN
    if message.get(TYPED_KEY):
        return TYPED
    return VISIBLE


@dataclass(frozen=True)
class Block:
    """One piece of the transcript, in reading order.

    Attributes:
        kind: ``"entry"``, ``"turn_start"`` or ``"turn_end"``.
        index: The history index, for an entry.
        record: The turn record, for a turn boundary.
        number: The turn's position in the session, counting from 1.
    """

    kind: str
    index: int = -1
    record: GMTurnRecord | None = None
    number: int = 0


def transcript_blocks(
    history: list[ChatMessage], records: list[GMTurnRecord]
) -> list[Block]:
    """Interleave ``history`` with the turn ``records`` that wrote it.

    Each record owns the entries from its ``history_start`` to its
    ``history_end``. Entries no record owns -- a session older than turn
    records, or a note filed outside a turn -- stay in place on their own. A
    turn that saved nothing sits at the point it happened, with no entries.

    Args:
        history: The session's stored history.
        records: Its turn records, oldest first.

    Returns:
        The blocks in reading order.
    """
    blocks: list[Block] = []
    index = 0
    for number, record in enumerate(records, start=1):
        start = min(record.history_start, len(history))
        while index < start:
            blocks.append(Block("entry", index=index))
            index += 1
        blocks.append(Block("turn_start", record=record, number=number))
        end = record.history_end if record.history_end is not None else index
        end = min(max(end, index), len(history))
        while index < end:
            blocks.append(Block("entry", index=index))
            index += 1
        blocks.append(Block("turn_end", record=record, number=number))
    while index < len(history):
        blocks.append(Block("entry", index=index))
        index += 1
    return blocks


def _records(session: GMSession) -> list[GMTurnRecord]:
    return list(
        session.turn_records.select_related("prompt")
        .prefetch_related("events")
        .order_by("started_at", "pk")
    )


def event_summary(event: GMEvent) -> dict[str, Any]:
    """One turn event as a transcript carries it (issue #590).

    A model request keeps what the call was and its last message -- the part
    that differs between calls -- rather than the whole history each time.
    """
    payload: dict[str, Any] = dict(event.payload)
    if event.kind == "model_request":
        messages = payload.get("messages") or []
        payload = {key: payload[key] for key in REQUEST_SUMMARY_KEYS if key in payload}
        payload["last_message"] = messages[-1] if messages else None
    return {
        "seq": event.seq,
        "kind": event.kind,
        "visible": event.visible,
        "payload": payload,
    }


def _indented(text: str, prefix: str = "    | ") -> list[str]:
    return [f"{prefix}{line}" for line in str(text).splitlines()] or [prefix]


def _event_lines(event: GMEvent) -> list[str]:
    """One turn event, as the text transcript reads it."""
    payload = event.payload
    kind = event.kind
    if kind == "state":
        return [f"  [{event.seq}] state: {payload.get('state')}"]
    if kind == "model_request":
        summary = event_summary(event)["payload"]
        last = summary.get("last_message") or {}
        lines = [
            f"  [{event.seq}] model call {summary.get('call')}: "
            f"{summary.get('prefix_messages', 0)} messages before the last"
            + (" (unchanged)" if summary.get("prefix") == "unchanged" else "")
        ]
        return lines + _indented(last.get("content", ""))
    if kind == "model_response":
        return [f"  [{event.seq}] model reply to {payload.get('call')}:"] + _indented(
            payload.get("raw", "")
        )
    if kind == "narration":
        return [
            f"  [{event.seq}] narration saved ({len(payload.get('text', ''))} characters)"
        ]
    if kind == "action":
        return [f"  [{event.seq}] action: {payload.get('result', '')}"]
    if kind == "roll":
        return [f"  [{event.seq}] roll: {payload.get('line', '')}"]
    if kind == "ruling":
        return [
            f"  [{event.seq}] ruling {payload.get('change')}: "
            f"{payload.get('topic')} (ruling {payload.get('ruling_id')}): "
            f"{payload.get('rule')}"
        ]
    return [f"  [{event.seq}] {kind}: {_compact(payload)}"]


def _owner(session: GMSession) -> str:
    return session.user.get_username() if session.user else "(no owner)"


def transcript_filename(session: GMSession, extension: str) -> str:
    """A download name that says which session and when it was taken."""
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    slug = slugify(session.name)[:40] or "session"
    return f"gm-session-{session.pk}-{slug}-{stamp}.{extension}"


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _entry_lines(index: int, message: ChatMessage) -> list[str]:
    kind = message.get("kind") or "message"
    lines = [
        f"[#{index}] {str(message.get('role', '?')).upper()} · {kind} · {visibility(message)}"
    ]
    lines.append(str(message.get("content", "")))
    extra = {key: value for key, value in message.items() if key not in ENTRY_KEYS}
    if extra:
        lines.append(f"  data: {_compact(extra)}")
    lines.append("")
    return lines


def _fold_lines(session: GMSession) -> list[str]:
    """The fold's recap, printed where the entries it stands for end (#791)."""
    return [
        f"[fold] ENTRIES #0-#{session.summary_covers - 1} ARE FOLDED: THE MODEL "
        "READS THIS RECAP IN THEIR PLACE",
        session.summary,
        "",
    ]


def _turn_start_lines(record: GMTurnRecord, number: int) -> list[str]:
    started = labelled_instant(record.started_at)
    authority = "Game Master authority" if record.is_game_master else "player"
    prompt = (
        record.prompt.prompt_hash[:SHORT_HASH_LENGTH] if record.prompt else "unknown"
    )
    lines = [
        TURN_RULE,
        f"TURN {number} · {started} · {record.duration_ms / 1000:.1f}s · {record.outcome}",
        f"mode {record.mode} · {authority} · command {record.command or '(message)'}",
        f"model {record.model or 'unknown'} · options {_compact(record.options)}",
        f"prompt {prompt} · app {record.app_version or 'unknown'}",
    ]
    if record.history_end is None:
        lines.append(f"player sent: {record.user_message or '(nothing)'}")
        lines.append("nothing from this turn was saved to the session history")
    lines.append(TURN_RULE)
    lines.append("")
    return lines


def _turn_end_lines(record: GMTurnRecord, number: int) -> list[str]:
    lines = [f"-- end of turn {number} ({record.outcome})"]
    events = list(record.events.all())
    if events:
        lines.append("  events:")
        for event in events:
            lines.extend(_event_lines(event))
    for roll in record.gm_rolls:
        lines.append(f"  roll: {_compact(roll)}")
    for procedure in record.procedures:
        lines.append(f"  procedure: {_compact(procedure)}")
    for note in record.ledger_applied:
        lines.append(f"  ledger applied: {note}")
    for note in record.ledger_refused:
        lines.append(f"  ledger refused: {note}")
    for notice in record.notices:
        lines.append(f"  notice shown: {notice}")
    if record.removed_narration:
        # The aide's machinery narration the server removed (issue #392): the
        # model's own words stay readable here, never hidden from debugging.
        lines.append(
            f"  machinery narration removed: {len(record.removed_narration)} "
            "sentence(s)"
        )
        for sentence in record.removed_narration:
            lines.append(f"    removed: {sentence}")
        lines.append("  reply as the model wrote it:")
        lines.extend(f"    | {line}" for line in record.original_reply.splitlines())
        lines.append("  reply as saved and shown:")
        lines.extend(f"    | {line}" for line in record.reply.splitlines())
    if record.error:
        lines.append(f"  error: {record.error}")
    lines.append("")
    return lines


def render_transcript_text(session: GMSession) -> str:
    """The session as a plain-text document.

    Args:
        session: The session to export.

    Returns:
        The whole transcript, ending in a newline.
    """
    history = session.history
    records = _records(session)
    lines = [
        "TARMAR STUDIO · GM SESSION TRANSCRIPT",
        RULE,
        f"Session {session.pk}: {session.name}",
        f"Owner: {_owner(session)}",
        f"Mode now: {session.mode}",
        f"Created: {labelled_instant(session.created_at)}",
        f"Updated: {labelled_instant(session.updated_at)}",
        f"Exported: {labelled_instant()} "
        f"by app {git_info(None)['git_sha_short'] or 'unknown'}",
        f"History entries: {len(history)} · turn records: {len(records)}",
        # Where a long session was folded, and what the model reads for the
        # entries before it (issue #791).
        (
            f"Folded: entries #0-#{session.summary_covers - 1} are read by the "
            "model as the recap printed where they end"
            if session.summary_covers
            else "Folded: no (the model reads every entry)"
        ),
        "",
        f"Entries marked '{HIDDEN}' were sent to the model and never shown to",
        f"the player. Entries marked '{CHIP}' were shown to the player only as a",
        "dice or procedure chip; the marker loop also sent them to the model, the",
        "plan engine does not. A turn's events list what it did, in order.",
        RULE,
        "",
    ]
    fold_shown = not session.summary_covers
    for block in transcript_blocks(history, records):
        if block.kind == "entry":
            if not fold_shown and block.index >= session.summary_covers:
                lines.extend(_fold_lines(session))
                fold_shown = True
            lines.extend(_entry_lines(block.index, history[block.index]))
        elif block.kind == "turn_start" and block.record is not None:
            lines.extend(_turn_start_lines(block.record, block.number))
        elif block.record is not None:
            lines.extend(_turn_end_lines(block.record, block.number))
    if not fold_shown:
        lines.extend(_fold_lines(session))
    lines.extend([RULE, "RECORDS SET BY HAND", RULE])
    edits = list(session.hand_edits.select_related("user"))
    if not edits:
        lines.append("(none: every record here was written by the turns)")
    for edit in edits:
        stamp = labelled_instant(edit.created_at)
        authority = "Game Master authority" if edit.is_game_master else "player"
        lines.append(f"{stamp} · {authority} · {edit.line}")
        lines.append(f"  before: {_compact(edit.before)}")
        lines.append(f"  after: {_compact(edit.after)}")
    lines.append("")
    lines.extend([RULE, "SESSION CHARACTERS, AS STORED NOW", RULE])
    lines.append(
        json.dumps(
            session_characters(session), indent=2, ensure_ascii=False, default=str
        )
    )
    lines.extend(["", RULE, "SYSTEM PROMPTS THESE TURNS RAN WITH", RULE])
    prompts = _distinct_prompts(records)
    if not prompts:
        lines.append("(no turn records: this session predates detailed recording)")
    for short_hash, (turns, text) in prompts.items():
        lines.append(f"--- prompt {short_hash} · turns {', '.join(map(str, turns))}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _distinct_prompts(records: list[GMTurnRecord]) -> dict[str, tuple[list[int], str]]:
    prompts: dict[str, tuple[list[int], str]] = {}
    for number, record in enumerate(records, start=1):
        if record.prompt is None:
            continue
        short_hash = record.prompt.prompt_hash[:SHORT_HASH_LENGTH]
        turns, _text = prompts.setdefault(short_hash, ([], record.prompt.text))
        turns.append(number)
    return prompts


def _record_payload(record: GMTurnRecord, number: int) -> dict[str, Any]:
    return {
        "turn": number,
        "started_at": record.started_at.isoformat(),
        "duration_ms": record.duration_ms,
        "outcome": record.outcome,
        "command": record.command,
        "mode": record.mode,
        "is_game_master": record.is_game_master,
        "model": record.model,
        "options": record.options,
        "prompt_hash": record.prompt.prompt_hash if record.prompt else None,
        "app_version": record.app_version,
        "user_message": record.user_message,
        "reply": record.reply,
        "original_reply": record.original_reply,
        "removed_narration": record.removed_narration,
        "history_start": record.history_start,
        "history_end": record.history_end,
        "gm_rolls": record.gm_rolls,
        "procedures": record.procedures,
        "ledger_applied": record.ledger_applied,
        "ledger_refused": record.ledger_refused,
        "notices": record.notices,
        "error": record.error,
        "events": [event_summary(event) for event in record.events.all()],
    }


def transcript_payload(session: GMSession) -> dict[str, Any]:
    """The same transcript as data, for tools rather than people.

    Args:
        session: The session to export.

    Returns:
        A JSON-serialisable mapping.
    """
    records = _records(session)
    return {
        "session": {
            "id": session.pk,
            "name": session.name,
            "owner": _owner(session),
            "mode": session.mode,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            # The fold (issue #791): the recap the model reads for entries
            # 0 .. summary_covers - 1.
            "summary": session.summary,
            "summary_covers": session.summary_covers,
        },
        "hand_edits": [
            {
                "created_at": edit.created_at.isoformat(),
                "user": edit.who,
                "is_game_master": edit.is_game_master,
                "record_kind": edit.record_kind,
                "record_id": edit.record_id,
                "change": edit.change,
                "before": edit.before,
                "after": edit.after,
            }
            for edit in session.hand_edits.select_related("user")
        ],
        "exported_at": timezone.now().isoformat(),
        "app_version": git_info(None)["git_sha_short"],
        "history": [
            {"index": index, "visibility": visibility(message), "message": message}
            for index, message in enumerate(session.history)
        ],
        "turns": [
            _record_payload(record, number)
            for number, record in enumerate(records, start=1)
        ],
        "characters": session_characters(session),
        "prompts": {
            record.prompt.prompt_hash: record.prompt.text
            for record in records
            if record.prompt is not None
        },
    }
