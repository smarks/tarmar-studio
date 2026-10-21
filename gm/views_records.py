"""Endpoints for what a session holds besides its conversation.

Kept apart from :mod:`gm.views`, whose stream and turn code other work is
changing, so these stay small and read on their own:

* the notice of how a session fight ended (issue #785);
* the Game Master's hand edits of house rules, people, things, threads and
  the fold's recap (issues #789, #790, #791), each a call into
  :mod:`gm.hand_records`, which holds who may and what is logged.

Every endpoint answers JSON and is scoped to the signed-in user's own
sessions; another user's session is a 404, as everywhere in this app.
"""

from collections.abc import Callable
from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from common.decorators import internal_errors_logged, rate_limit
from common.helpers import _json_error, _parse_json_body, database_integer

from . import hand_records
from .battle_report import filed_verdict, notice_key
from .models import GMSession
from .refusal_words import refusal_words

#: How many hand edits one account may send a minute: far above any hand.
HAND_EDITS_PER_MINUTE = 60


def _own_session(request: HttpRequest, session_id: Any) -> GMSession | None:
    try:
        key = database_integer(session_id)
    except ValueError:
        return None
    return GMSession.objects.filter(pk=key, user=request.user).first()


@login_required
@require_GET
@internal_errors_logged(json_only=True)
def gm_fight_verdict(request: HttpRequest) -> JsonResponse:
    """The notice of how the session's fight ended, as filed (issue #785).

    ``?session_id=<id>``. Read only: the notice is filed by the server when
    the turn that ends the fight commits (:data:`battle.signals.battle_ended`),
    and the Battle tab asks for it then, to show it without a reload.

    Returns:
        ``{"ok": true, "verdict", "battle_id", "notice"}``, each null
        while the fight goes on.
    """
    session = _own_session(request, request.GET.get("session_id"))
    if session is None:
        return _json_error(refusal_words().views.session_not_found, status=404)
    notice = filed_verdict(session)
    return JsonResponse(
        {
            "ok": True,
            "verdict": notice["content"] if notice else None,
            "battle_id": notice.get("battle_id") if notice else None,
            # Which of the battle's notices this is, as the page marks it.
            "notice": notice_key(notice) if notice else None,
        }
    )


def _hand_edit(
    request: HttpRequest,
    session_id: int,
    changes: dict[str, Callable[[GMSession, Any, dict], hand_records.HandEdit]],
) -> JsonResponse:
    """Run the hand edit a body names, and answer as every hand edit does."""
    session = _own_session(request, session_id)
    if session is None:
        return _json_error(refusal_words().views.session_not_found, status=404)
    if not hand_records.may_edit_by_hand(request.user):
        return _json_error(refusal_words().hand_records.game_master_only, status=403)
    data, error = _parse_json_body(request)
    if error:
        return error
    change = changes.get(str(data.get("change", "")))
    if change is None:
        return _json_error(
            refusal_words().views_records.change_one_of.format(
                changes=", ".join(changes)
            )
        )
    try:
        done = change(session, request.user, data)
    except hand_records.HandEditError as refusal:
        return _json_error(str(refusal))
    return JsonResponse(
        {
            "ok": True,
            "message": done.message,
            "set_by": done.edit.line,
            # The record as saved, so the page shows what the server holds.
            "after": done.edit.after,
        }
    )


@login_required
@require_POST
@rate_limit("gm_hand_edit", max_requests=HAND_EDITS_PER_MINUTE, window_seconds=60)
@internal_errors_logged(json_only=True)
def gm_rulings(request: HttpRequest, session_id: int) -> JsonResponse:
    """Add, correct or retire a house rule by hand (issue #789).

    Body: ``{"change": "add"|"edit"|"retire", "ruling_id", "topic", "text"}``.
    A Game Master only; a player is answered 403.
    """
    return _hand_edit(
        request,
        session_id,
        {
            "add": lambda session, user, data: hand_records.add_ruling(
                session, user, data.get("topic"), data.get("text")
            ),
            "edit": lambda session, user, data: hand_records.edit_ruling(
                session,
                user,
                data.get("ruling_id"),
                data.get("topic"),
                data.get("text"),
            ),
            "retire": lambda session, user, data: hand_records.retire_ruling(
                session, user, data.get("ruling_id")
            ),
        },
    )


@login_required
@require_POST
@rate_limit("gm_hand_edit", max_requests=HAND_EDITS_PER_MINUTE, window_seconds=60)
@internal_errors_logged(json_only=True)
def gm_entities(request: HttpRequest, session_id: int) -> JsonResponse:
    """Add, correct or remove a person or thing by hand (issue #790).

    Body: ``{"change": "save"|"remove", "entity_id", "kind", "name", "where",
    "wants", "condition", "notes"}``; ``save`` without an id adds one.
    """
    return _hand_edit(
        request,
        session_id,
        {
            "save": lambda session, user, data: hand_records.save_entity(
                session,
                user,
                data.get("kind"),
                data,
                entity_id=data.get("entity_id"),
            ),
            "remove": lambda session, user, data: hand_records.remove_entity(
                session, user, data.get("entity_id")
            ),
        },
    )


@login_required
@require_POST
@rate_limit("gm_hand_edit", max_requests=HAND_EDITS_PER_MINUTE, window_seconds=60)
@internal_errors_logged(json_only=True)
def gm_threads(request: HttpRequest, session_id: int) -> JsonResponse:
    """Open, correct, resolve or reopen a thread by hand (issue #790).

    Body: ``{"change": "open"|"edit"|"resolve"|"reopen", "thread_id", "text",
    "resolution"}``.
    """
    return _hand_edit(
        request,
        session_id,
        {
            "open": lambda session, user, data: hand_records.open_thread(
                session, user, data.get("text")
            ),
            "edit": lambda session, user, data: hand_records.edit_thread(
                session, user, data.get("thread_id"), data.get("text")
            ),
            "resolve": lambda session, user, data: hand_records.resolve_thread(
                session, user, data.get("thread_id"), data.get("resolution")
            ),
            "reopen": lambda session, user, data: hand_records.reopen_thread(
                session, user, data.get("thread_id")
            ),
        },
    )


@login_required
@require_POST
@rate_limit("gm_hand_edit", max_requests=HAND_EDITS_PER_MINUTE, window_seconds=60)
@internal_errors_logged(json_only=True)
def gm_recap(request: HttpRequest, session_id: int) -> JsonResponse:
    """Correct the recap a long session's older turns were folded into (#791).

    Body: ``{"change": "edit", "text": <the corrected recap>}``.
    """
    return _hand_edit(
        request,
        session_id,
        {
            "edit": lambda session, user, data: hand_records.correct_recap(
                session, user, data.get("text")
            ),
        },
    )
