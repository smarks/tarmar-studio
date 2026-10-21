"""The GM page's "Show earlier" pages: older turns and resolved threads (#972).

The chat and the Campaign tab render their latest page; each page's control
asks one of these for the page above it, rendered with the same partial the
page used, so a loaded page cannot drift from a reload. Owner only, as the
session's page is: another account's session is not found.
"""

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from battle.access import is_dm
from common.helpers import database_integer

from .hand_records import latest_hand_edits, may_edit_by_hand
from .history_pages import history_page, resolved_threads_page
from .models import GMRecordEdit, GMSession, StoryThread
from .refusal_words import refusal_words
from .text_limits import GM_TEXT_LIMITS
from .views import _hand_marked, _visible_history


def _before(request: HttpRequest) -> int:
    """The ``?before=`` cursor: a whole number, written in ASCII digits.

    Raises:
        ValueError: It is missing or not such a number.
    """
    return database_integer(request.GET.get("before", ""))


@login_required
@require_GET
def gm_history_page(request: HttpRequest, session_id: int) -> HttpResponse:
    """The turns above the ones on the page: ``?before=<first turn shown>``.

    Answers the entries and, while earlier turns remain, the next control,
    for ``gm-history.js`` to put in place of the control that asked.
    """
    session = get_object_or_404(GMSession, pk=session_id, user=request.user)
    try:
        before = _before(request)
    except ValueError:
        return HttpResponseBadRequest(refusal_words().views_history.before_not_turn)
    return render(
        request,
        "gm/_history_entries.html",
        {
            "history_page": history_page(_visible_history(session), before=before),
            "active_session": session,
            # The fold's recap renders its correction form for a Game Master,
            # as it does on the page (issue #791).
            "is_game_master": is_dm(request.user),
            "gm_text_limits": GM_TEXT_LIMITS,
        },
    )


@login_required
@require_GET
def gm_resolved_threads(request: HttpRequest, session_id: int) -> HttpResponse:
    """The resolved threads listed above the ones on the page.

    ``?before=<id of the first thread shown>``: a thread of this session,
    resolved or since reopened, whose place in the order the page holds.
    """
    session = get_object_or_404(GMSession, pk=session_id, user=request.user)
    try:
        before_id = _before(request)
    except ValueError:
        return HttpResponseBadRequest(refusal_words().views_history.before_not_thread)
    before = StoryThread.objects.filter(session=session, pk=before_id).first()
    if before is None:
        raise Http404("No thread of this session has that id.")
    page = resolved_threads_page(session, before=before)
    _hand_marked(page.threads, GMRecordEdit.THREAD, latest_hand_edits(session))
    return render(
        request,
        "gm/_resolved_threads.html",
        {
            "campaign_session": session,
            "campaign_resolved_page": page,
            "can_edit_records": may_edit_by_hand(request.user),
        },
    )
