"""How a character write starts: lock the row, then check the edit right.

Thirteen endpoints across this package open the same way. Each takes a
``pk``, re-reads the character under ``SELECT ... FOR UPDATE`` so the
read-modify-write it is about to do cannot lose a concurrent edit, and then
asks whether the requester may edit it at all. Both halves were written out
at every one of them (issue #337): the queryset, the ``is_deleted`` filter
and the permission test, thirteen times over. They live here instead, so
"which rows can be written, and by whom" is decided in one place.

The two shapes are kept apart deliberately. A page endpoint answers a missing
character with Django's HTML 404; the two skill endpoints answer with a JSON
404 their caller can read, because they are only ever reached by fetch(). The
audit flagged that split, and it is real -- the front end depends on it -- so
this module offers both rather than quietly picking one.
"""

import functools
from collections.abc import Callable

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404

from common.helpers import _can_edit_object, _json_error, _permission_denied

from ..models import Character
from ..refusal_words import refusal_words
from ..services import CharacterChangeError


def locked_character_or_404(pk: int, *, is_deleted: bool = False) -> Character:
    """Row-lock one character for a read-modify-write, or raise ``Http404``.

    Call inside ``transaction.atomic()``; the lock is held to the end of that
    block. ``is_deleted`` picks which side of the soft-delete line the row
    has to be on -- ``False`` for every ordinary write, ``True`` for restore,
    which is the one endpoint that wants a deleted row and nothing else.

    Args:
        pk: The character's primary key, from the URL.
        is_deleted: The soft-delete state the row must be in to be found.

    Returns:
        The locked character.
    """
    return get_object_or_404(
        Character.objects.select_for_update(), pk=pk, is_deleted=is_deleted
    )


def locked_character(pk: int) -> Character:
    """Row-lock one live character, raising ``Character.DoesNotExist``.

    The JSON endpoints' half of :func:`locked_character_or_404`: they catch
    the miss themselves and answer :func:`character_not_found_json` rather
    than letting Django render its 404 page into a fetch() response.

    Args:
        pk: The character's primary key, from the URL.

    Returns:
        The locked character.

    Raises:
        Character.DoesNotExist: When no live character has that ``pk``.
    """
    return Character.objects.select_for_update().get(pk=pk, is_deleted=False)


def edit_denied(request: HttpRequest, character: Character) -> JsonResponse | None:
    """The 403 to return when ``request`` may not edit ``character``.

    ``None`` when the write may go ahead, so a caller reads as
    ``denied = edit_denied(...)`` / ``if denied is not None: return denied``.
    The rule itself is ``common.helpers._can_edit_object`` -- owner, or an
    admin/DM -- which this does not restate.

    Args:
        request: The request, for its user.
        character: The already-locked character being written to.
    """
    if _can_edit_object(request, character):
        return None
    return _permission_denied()


def character_not_found_json() -> JsonResponse:
    """The JSON 404 the fetch-only endpoints answer a missing character with."""
    return _json_error(refusal_words().views_access.not_found, status=404)


def edit_right_first(
    *,
    json_not_found: bool = True,
    is_deleted: bool = False,
    game_master_only: bool = False,
) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    """Answer a stranger before the view reads a word of the body (issue #182).

    Every ``<pk>`` endpoint that writes a character locks the row and checks
    the edit right inside its change, which is the authoritative check. But
    most parse and validate the request body first, so a caller with no right
    to the character was answered 400 for a bad body and learned what a good
    one looks like before the 403. This decorator puts the question "may this
    caller write this character at all?" in front of the view, from one cheap
    read of the row, so the order cannot drift view by view: a missing
    character is the view's own 404 shape, a stranger is the usual 403, and
    only then does the view run and check again on the locked row. Which side
    of the player/Game Master line a write sits on is unchanged: the rule is
    still ``common.helpers._can_edit_object`` -- the owner, or a DM/admin.

    Args:
        json_not_found: Whether a missing character is the JSON 404
            (``character_not_found_json``) or Django's HTML 404, matching
            what the view answers from ``answer_change`` or
            ``locked_character_or_404``.
        is_deleted: The soft-delete state the row must be in, as
            ``locked_character_or_404`` takes it: ``True`` for restore only.
        game_master_only: The endpoint is a Game Master's alone (a death, a
            revival, the pools, coin, mana). A caller who is not one is
            refused before the row is read, so the refusal says nothing
            about whether the character exists: a 403 for an existing id and
            a 404 for a missing one would have been an existence oracle.

    Returns:
        The decorator.
    """

    def decorate(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @functools.wraps(view)
        def guarded(request: HttpRequest, pk: int, *args, **kwargs) -> HttpResponse:
            if game_master_only and not request.user.can_manage_users:
                return _permission_denied()
            character = (
                Character.objects.filter(pk=pk, is_deleted=is_deleted)
                .select_related("user")
                .only("id", "user")
                .first()
            )
            if character is None:
                if json_not_found:
                    return character_not_found_json()
                raise Http404(refusal_words().views_access.not_found)
            if not _can_edit_object(request, character):
                return _permission_denied()
            return view(request, pk, *args, **kwargs)

        return guarded

    return decorate


class ChangeRefused(Exception):
    """Raised inside a change to roll it back and answer with ``response``.

    For the endpoints whose validators already build their own error
    response: raising it leaves the row exactly as it was.
    """

    def __init__(self, response: HttpResponse) -> None:
        super().__init__("change refused")
        self.response = response


def may_edit(request: HttpRequest) -> Callable[[Character], None]:
    """The ``authorize`` check a character service call runs for ``request``.

    Raises ``PermissionDenied`` on the locked row when the requester may not
    edit it, the same owner-or-admin/DM rule as :func:`edit_denied`.
    """

    def authorize(character: Character) -> None:
        if not _can_edit_object(request, character):
            raise PermissionDenied

    return authorize


def answer_change(
    run: Callable[[], HttpResponse], *, json_not_found: bool = True
) -> HttpResponse:
    """Run a view's character change and answer its refusals.

    A rules refusal is a 400 carrying its reason, a refused edit right the
    usual 403, and a missing character the JSON 404 (or Django's HTML 404 for
    the page endpoints that always answered that way).

    Args:
        run: Makes the change through ``characters.services`` and builds the
            success response.
        json_not_found: Whether a missing character is a JSON 404.
    """
    try:
        return run()
    except ChangeRefused as refused:
        return refused.response
    except CharacterChangeError as error:
        return _json_error(str(error))
    except PermissionDenied:
        return _permission_denied()
    except Character.DoesNotExist:
        if json_not_found:
            return character_not_found_json()
        raise Http404(refusal_words().views_access.not_found) from None
