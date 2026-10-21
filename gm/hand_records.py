"""Campaign records the Game Master sets by hand (issues #789, #790, #791).

A session's house rules, the people and things in play, its open threads and
the recap its older turns were folded into were each written only when the
model planned the action that writes them, and corrected only by asking the
model again. This module is the other way in: the Game Master adds, corrects,
retires, resolves or removes a record from the page.

Who may: a Game Master's account (``can_manage_users``, through
:func:`battle.access.is_dm`) in his own session. Ruled with the batch
(Spencer's standing rule that players are held to the rules and a Game
Master may override): a player sees these records and may not change them,
which is what the model's own actions already hold them to for house rules.
Every change is written to :class:`gm.models.GMRecordEdit` with who made it,
so a hand edit is recorded as set, not as rolled or narrated, and the
transcript shows it.

House rules go through the executor's own ``record_house_rule`` and
``retire_house_rule`` actions, read by the plan reader, so a ruling made by
hand obeys exactly the limits and the one-active-ruling-per-topic rule a
ruling the model records does. The model has no action that corrects or
removes a person, a thing or a thread, or reopens one, so those are written
here under the same field limits the models declare.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms.models import model_to_dict
from django.utils import timezone

from battle.access import is_dm
from characters.constants import too_long

from .models import GMRecordEdit, GMSession, SceneEntity, StoryThread, TableRuling
from .refusal_words import refusal_words
from .summary import MAX_SUMMARY_CHARS
from .turn_record import running_turn_for


class HandEditError(Exception):
    """A hand edit that was refused; the message says why, for the page."""


@dataclass(frozen=True)
class HandEdit:
    """What a hand edit did.

    Attributes:
        edit: The logged change.
        message: A sentence for the page, saying what changed.
    """

    edit: GMRecordEdit
    message: str


def may_edit_by_hand(user: Any) -> bool:
    """Whether ``user`` may set a session's records by hand: a Game Master."""
    return is_dm(user)


def _require_game_master(user: Any) -> None:
    if not may_edit_by_hand(user):
        raise HandEditError(refusal_words().hand_records.game_master_only)


def _log(
    session: GMSession,
    user: Any,
    *,
    kind: str,
    record_id: int | None,
    change: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> GMRecordEdit:
    # JSON round trip: the snapshots hold dates, which the column cannot.
    return GMRecordEdit.objects.create(
        session=session,
        user=user,
        is_game_master=is_dm(user),
        record_kind=kind,
        record_id=record_id,
        change=change,
        before=json.loads(json.dumps(before, default=str)),
        after=json.loads(json.dumps(after, default=str)),
    )


def _clean_text(
    value: Any, *, label: str, limit: int, required: bool, lines: bool = False
) -> str:
    """``value`` as a record keeps it, or a refusal saying why not.

    Args:
        value: What the request carried.
        label: How the refusal names the field.
        limit: The column's length.
        required: Whether an empty value is refused.
        lines: Keep the text's paragraphs and lines, as a recap has them
            (review of #791); only the ends of each line and of the whole are
            trimmed. Otherwise the text is squeezed onto one line, as the
            one-line fields show it.
    """
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise HandEditError(refusal_words().hand_records.not_text.format(label=label))
    if lines:
        kept = [line.rstrip() for line in value.replace("\r\n", "\n").split("\n")]
        cleaned = "\n".join(kept).strip()
    else:
        cleaned = " ".join(value.split())
    if required and not cleaned:
        raise HandEditError(refusal_words().hand_records.needed.format(label=label))
    # The one wording every path gives past a limit (#928, #938).
    refusal = too_long(label, cleaned, limit)
    if refusal:
        raise HandEditError(refusal)
    return cleaned


# -- house rules (issue #789) -------------------------------------------------


def _run_ruling_action(session: GMSession, user: Any, action: dict[str, Any]) -> str:
    """Run one house-rule action as the executor runs a plan's.

    Raises:
        HandEditError: The plan reader or the executor refused it.
    """
    from .engine import MODE_AIDE
    from .turn_engine.executor import RecordsExecutor
    from .turn_engine.plan import read_plan
    from .turn_engine.policy import TurnPolicy

    policy = TurnPolicy.for_turn(MODE_AIDE, is_game_master=is_dm(user))
    reading = read_plan(json.dumps({"actions": [action]}), roster_ids=(), policy=policy)
    if not reading.ok:
        raise HandEditError("; ".join(reading.errors))
    executor = RecordsExecutor(policy, session, lambda: session, user)
    result = executor.run(reading.actions[0], lambda event: None)
    if not result.ran:
        raise HandEditError(
            result.error or refusal_words().hand_records.ruling_not_recorded
        )
    return result.note


def _ruling(session: GMSession, ruling_id: Any) -> TableRuling:
    try:
        return TableRuling.objects.get(session=session, pk=int(ruling_id), active=True)
    except (TypeError, ValueError, OverflowError, TableRuling.DoesNotExist):
        raise HandEditError(refusal_words().hand_records.no_such_ruling) from None


def add_ruling(session: GMSession, user: Any, topic: Any, text: Any) -> HandEdit:
    """Record a house rule by hand; one on the same topic is retired by it."""
    _require_game_master(user)
    topic = _clean_text(
        topic,
        label=refusal_words().hand_records.label_topic,
        limit=TableRuling.MAX_TOPIC_CHARS,
        required=True,
    )
    text = _clean_text(
        text,
        label=refusal_words().hand_records.label_rule,
        limit=TableRuling.MAX_RULE_CHARS,
        required=True,
    )
    with transaction.atomic():
        note = _run_ruling_action(
            session,
            user,
            {"action": "record_house_rule", "topic": topic, "rule": text},
        )
        ruling = TableRuling.objects.filter(session=session, active=True).latest("pk")
        edit = _log(
            session,
            user,
            kind=GMRecordEdit.RULING,
            record_id=ruling.pk,
            change=GMRecordEdit.ADDED,
            before={},
            after={"topic": ruling.topic, "text": ruling.text},
        )
    return HandEdit(edit, note[0].upper() + note[1:] + ".")


def edit_ruling(
    session: GMSession, user: Any, ruling_id: Any, topic: Any, text: Any
) -> HandEdit:
    """Correct a house rule by hand.

    A ruling is never edited in place (issue #600): the corrected one is
    recorded as a new ruling and the old one retired, so what the table
    played by before stays readable.
    """
    _require_game_master(user)
    with transaction.atomic():
        old = _ruling(session, ruling_id)
        topic = _clean_text(
            topic,
            label=refusal_words().hand_records.label_topic,
            limit=TableRuling.MAX_TOPIC_CHARS,
            required=True,
        )
        text = _clean_text(
            text,
            label=refusal_words().hand_records.label_rule,
            limit=TableRuling.MAX_RULE_CHARS,
            required=True,
        )
        if not TableRuling.same_topic(old.topic, topic):
            _run_ruling_action(
                session, user, {"action": "retire_house_rule", "ruling_id": old.pk}
            )
        _run_ruling_action(
            session,
            user,
            {"action": "record_house_rule", "topic": topic, "rule": text},
        )
        new = TableRuling.objects.filter(session=session, active=True).latest("pk")
        edit = _log(
            session,
            user,
            kind=GMRecordEdit.RULING,
            record_id=new.pk,
            change=GMRecordEdit.EDITED,
            before={"ruling_id": old.pk, "topic": old.topic, "text": old.text},
            after={"topic": new.topic, "text": new.text},
        )
    return HandEdit(
        edit,
        refusal_words().hand_records.ruling_corrected.format(
            old=old.pk, new=new.pk, text=new.text
        ),
    )


def retire_ruling(session: GMSession, user: Any, ruling_id: Any) -> HandEdit:
    """Retire a house rule by hand; it stays listed as retired."""
    _require_game_master(user)
    with transaction.atomic():
        ruling = _ruling(session, ruling_id)
        note = _run_ruling_action(
            session, user, {"action": "retire_house_rule", "ruling_id": ruling.pk}
        )
        edit = _log(
            session,
            user,
            kind=GMRecordEdit.RULING,
            record_id=ruling.pk,
            change=GMRecordEdit.RETIRED,
            before={"topic": ruling.topic, "text": ruling.text},
            after={},
        )
    return HandEdit(edit, note[0].upper() + note[1:] + ".")


# -- people and things (issue #790) -------------------------------------------

ENTITY_FIELDS = ("name", "where", "wants", "condition", "notes")


def _entity_values(values: dict[str, Any]) -> dict[str, str]:
    # Each field is named by its ``label_<field>`` key (issue #136).
    words = refusal_words().hand_records
    cleaned = {
        "name": _clean_text(
            values.get("name"),
            label=words.label_name,
            limit=SceneEntity.MAX_NAME_CHARS,
            required=True,
        )
    }
    for field in ENTITY_FIELDS[1:]:
        cleaned[field] = _clean_text(
            values.get(field),
            label=getattr(words, f"label_{field}"),
            limit=SceneEntity.MAX_DETAIL_CHARS,
            required=False,
        )
    return cleaned


def _entity_snapshot(entity: SceneEntity) -> dict[str, Any]:
    return model_to_dict(entity, fields=["kind", *ENTITY_FIELDS])


def _same_name_taken(session: GMSession, name: str, *, besides: int | None) -> bool:
    return (
        SceneEntity.objects.filter(session=session, name__iexact=name)
        .exclude(pk=besides)
        .exists()
    )


def _entity(session: GMSession, entity_id: Any) -> SceneEntity:
    try:
        return SceneEntity.objects.select_for_update().get(
            session=session, pk=int(entity_id)
        )
    except (TypeError, ValueError, OverflowError, SceneEntity.DoesNotExist):
        raise HandEditError(refusal_words().hand_records.no_such_entity) from None


def save_entity(
    session: GMSession,
    user: Any,
    kind: Any,
    values: dict[str, Any],
    *,
    entity_id: Any = None,
) -> HandEdit:
    """Add a person or thing by hand, or correct one already recorded.

    Every field is set as given, blanks included, so a wrong "where" can be
    cleared, not only replaced. Names stay one row each, as the model's
    ``record_npc`` keeps them: a name the session already holds is refused
    rather than written twice.
    """
    _require_game_master(user)
    if kind not in (SceneEntity.PERSON, SceneEntity.THING):
        raise HandEditError(refusal_words().hand_records.entity_kind)
    cleaned = _entity_values(values)
    with transaction.atomic():
        if entity_id in (None, ""):
            if _same_name_taken(session, cleaned["name"], besides=None):
                raise HandEditError(
                    refusal_words().hand_records.entity_already_recorded.format(
                        name=cleaned["name"]
                    )
                )
            entity = SceneEntity(session=session, kind=kind, **cleaned)
            change, before = GMRecordEdit.ADDED, {}
        else:
            entity = _entity(session, entity_id)
            if _same_name_taken(session, cleaned["name"], besides=entity.pk):
                raise HandEditError(
                    refusal_words().hand_records.entity_recorded_as_another_row.format(
                        name=cleaned["name"]
                    )
                )
            before = _entity_snapshot(entity)
            for field, value in cleaned.items():
                setattr(entity, field, value)
            entity.updated_at = timezone.now()
            change = GMRecordEdit.EDITED
        try:
            entity.full_clean(exclude=["session"])
        except ValidationError as error:
            raise HandEditError("; ".join(error.messages)) from None
        entity.save()
        edit = _log(
            session,
            user,
            kind=entity.kind,
            record_id=entity.pk,
            change=change,
            before=before,
            after=_entity_snapshot(entity),
        )
    words = refusal_words().hand_records
    done = (
        words.entity_recorded
        if change == GMRecordEdit.ADDED
        else words.entity_corrected
    )
    return HandEdit(edit, done.format(line=entity.line.removeprefix("- ")))


def remove_entity(session: GMSession, user: Any, entity_id: Any) -> HandEdit:
    """Take a person or thing out of the session, a duplicate most often."""
    _require_game_master(user)
    with transaction.atomic():
        entity = _entity(session, entity_id)
        before = _entity_snapshot(entity)
        removed_id = entity.pk
        kind = entity.kind
        entity.delete()
        edit = _log(
            session,
            user,
            kind=kind,
            record_id=removed_id,
            change=GMRecordEdit.REMOVED,
            before=before,
            after={},
        )
    return HandEdit(
        edit, refusal_words().hand_records.entity_removed.format(name=before["name"])
    )


# -- threads (issue #790) -----------------------------------------------------


def _thread_snapshot(thread: StoryThread) -> dict[str, Any]:
    return model_to_dict(thread, fields=["text", "resolved", "resolution"])


def _thread(session: GMSession, thread_id: Any) -> StoryThread:
    try:
        return StoryThread.objects.select_for_update().get(
            session=session, pk=int(thread_id)
        )
    except (TypeError, ValueError, OverflowError, StoryThread.DoesNotExist):
        raise HandEditError(refusal_words().hand_records.no_such_thread) from None


def _thread_text(value: Any, label: str | None = None, required: bool = True) -> str:
    return _clean_text(
        value,
        label=label or refusal_words().hand_records.label_thread,
        limit=StoryThread.MAX_TEXT_CHARS,
        required=required,
    )


def _change_thread(
    session: GMSession,
    user: Any,
    thread_id: Any,
    change: str,
    apply: Callable[[StoryThread], None],
) -> tuple[StoryThread, GMRecordEdit]:
    with transaction.atomic():
        thread = _thread(session, thread_id)
        before = _thread_snapshot(thread)
        apply(thread)
        thread.save()
        edit = _log(
            session,
            user,
            kind=GMRecordEdit.THREAD,
            record_id=thread.pk,
            change=change,
            before=before,
            after=_thread_snapshot(thread),
        )
    return thread, edit


def open_thread(session: GMSession, user: Any, text: Any) -> HandEdit:
    """Open a thread by hand."""
    _require_game_master(user)
    cleaned = _thread_text(text)
    with transaction.atomic():
        thread = StoryThread.objects.create(session=session, text=cleaned)
        edit = _log(
            session,
            user,
            kind=GMRecordEdit.THREAD,
            record_id=thread.pk,
            change=GMRecordEdit.ADDED,
            before={},
            after=_thread_snapshot(thread),
        )
    return HandEdit(
        edit,
        refusal_words().hand_records.thread_opened.format(
            thread=thread.pk, text=thread.text
        ),
    )


def edit_thread(session: GMSession, user: Any, thread_id: Any, text: Any) -> HandEdit:
    """Correct a thread's words by hand."""
    _require_game_master(user)
    cleaned = _thread_text(text)

    def apply(thread: StoryThread) -> None:
        thread.text = cleaned

    thread, edit = _change_thread(session, user, thread_id, GMRecordEdit.EDITED, apply)
    return HandEdit(
        edit,
        refusal_words().hand_records.thread_corrected.format(
            thread=thread.pk, text=thread.text
        ),
    )


def resolve_thread(
    session: GMSession, user: Any, thread_id: Any, resolution: Any
) -> HandEdit:
    """Close an open thread by hand, with how it ended if given."""
    _require_game_master(user)
    cleaned = _thread_text(
        resolution,
        label=refusal_words().hand_records.label_resolution,
        required=False,
    )

    def apply(thread: StoryThread) -> None:
        if thread.resolved:
            raise HandEditError(
                refusal_words().hand_records.thread_already_resolved.format(
                    thread=thread.pk
                )
            )
        thread.resolved = True
        thread.resolution = cleaned
        thread.resolved_at = timezone.now()

    thread, edit = _change_thread(
        session, user, thread_id, GMRecordEdit.RESOLVED, apply
    )
    return HandEdit(
        edit,
        refusal_words().hand_records.thread_closed.format(
            thread=thread.pk, text=thread.text
        ),
    )


def reopen_thread(session: GMSession, user: Any, thread_id: Any) -> HandEdit:
    """Open a resolved thread again, when it was closed by mistake."""
    _require_game_master(user)

    def apply(thread: StoryThread) -> None:
        if not thread.resolved:
            raise HandEditError(
                refusal_words().hand_records.thread_still_open.format(thread=thread.pk)
            )
        thread.resolved = False
        thread.resolution = ""
        thread.resolved_at = None

    thread, edit = _change_thread(
        session, user, thread_id, GMRecordEdit.REOPENED, apply
    )
    return HandEdit(
        edit,
        refusal_words().hand_records.thread_reopened.format(
            thread=thread.pk, text=thread.text
        ),
    )


# -- the fold's recap (issue #791) --------------------------------------------


def correct_recap(session: GMSession, user: Any, text: Any) -> HandEdit:
    """Correct the recap the session's older turns were folded into.

    The recap is what the model reads in place of those turns, so a wrong
    one is narrated from as though it happened. Only a folded session has
    one to correct; the turns it covers are not changed.
    """
    _require_game_master(user)
    cleaned = _clean_text(
        text,
        label=refusal_words().hand_records.label_recap,
        limit=MAX_SUMMARY_CHARS,
        required=True,
        lines=True,
    )
    with transaction.atomic():
        # The lock a turn's claim takes (gm.turn_record.claim_turn), so no
        # turn starts between the check below and the save.
        list(
            get_user_model()
            .objects.select_for_update()
            .filter(pk=session.user_id)
            .values_list("pk", flat=True)
        )
        # A turn may fold the session and write its own recap over this one,
        # without trace (review of #791); so not while one runs.
        if running_turn_for(session.user, session.pk) is not None:
            raise HandEditError(refusal_words().hand_records.recap_while_turn_running)
        locked = GMSession.objects.select_for_update().get(pk=session.pk)
        if not locked.summary_covers:
            raise HandEditError(refusal_words().hand_records.not_folded_yet)
        before = {"summary": locked.summary, "summary_covers": locked.summary_covers}
        locked.summary = cleaned
        locked.save(update_fields=["summary"])
        edit = _log(
            session,
            user,
            kind=GMRecordEdit.RECAP,
            record_id=None,
            change=GMRecordEdit.EDITED,
            before=before,
            after={"summary": cleaned, "summary_covers": locked.summary_covers},
        )
    session.summary = cleaned
    return HandEdit(edit, refusal_words().hand_records.recap_corrected)


def latest_hand_edits(session: GMSession | None) -> dict[tuple[str, int | None], str]:
    """The last hand edit of each record, as the pane marks it.

    Returns:
        ``{(record_kind, record_id): "Set by hand by gm: ..."}``.
    """
    if session is None:
        return {}
    marks: dict[tuple[str, int | None], str] = {}
    for edit in GMRecordEdit.objects.filter(session=session).select_related("user"):
        marks[(edit.record_kind, edit.record_id)] = edit.line
    return marks
