"""The Games Master's searchable pickers (issues #864, #973).

The session rail and its mobile ``<select>``, the Characters tab's attach
``<select>``, and the character sheet's "Play with the Game Master" session
``<select>``. Each is a :class:`common.pickers.PickerSource` over the rows the
matching endpoint accepts: the viewer's own sessions (``gm_characters_attach``
attaches to no one else's), and ``gm.attach.attachable_characters`` less the
ones already attached.
"""

from collections.abc import Iterable

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.db.models import QuerySet
from django.urls import reverse

from characters.models import Character
from common.pickers import PickerPage, PickerSource, picker_page

from .attach import attachable_characters
from .models import GMSession

#: Whoever a request carries; the endpoints behind these pickers require a
#: login, so in practice a signed-in account.
AnyUser = AbstractBaseUser | AnonymousUser

#: The rail and the menu list sessions most recently played first, the order
#: the rail always had: a session just made, or just played, is on page one.
SESSION_ORDER = ("-updated_at", "-pk")

#: The sidebar's rail: a ``<ul id="session-list">`` of links.
SESSION_RAIL_SOURCE = PickerSource(
    rows_template="gm/_session_rail_rows.html",
    search_label="Search your sessions by name",
    placeholder="Search sessions…",
    search_fields=("name",),
    order_by=SESSION_ORDER,
    noun="session",
    noun_plural="sessions",
    order_note="most recently played first",
)

#: The small screen's ``<select id="mobile-session-select">``.
SESSION_MENU_SOURCE = PickerSource(
    rows_template="gm/_session_menu_options.html",
    search_label="Search your sessions by name",
    placeholder="Search sessions…",
    search_fields=("name",),
    order_by=SESSION_ORDER,
    noun="session",
    noun_plural="sessions",
    order_note="most recently played first",
)

#: The session pickers by the name their endpoint's URL carries.
SESSION_PICKER_SOURCES = {"rail": SESSION_RAIL_SOURCE, "menu": SESSION_MENU_SOURCE}

#: The sheet's ``<select name="session_id">`` (issue #973): "A new session",
#: then a page of the viewer's sessions in the rail's order, so the session
#: just played is on page one.
PLAY_SESSION_SOURCE = PickerSource(
    rows_template="gm/_play_session_options.html",
    search_label="Search your sessions by name",
    placeholder="Search sessions…",
    search_fields=("name",),
    order_by=SESSION_ORDER,
    noun="session",
    noun_plural="sessions",
    order_note="most recently played first",
)

#: The Characters tab's ``<select id="gm-character-select">``.
ATTACHABLE_SOURCE = PickerSource(
    rows_template="gm/_attachable_options.html",
    search_label="Search characters to attach by name or player",
    placeholder="Search by name or player…",
    search_fields=("name", "user__username"),
    order_by=("-created_at", "-pk"),
    noun="character",
    noun_plural="characters",
)


def session_picker(
    user,
    view: str,
    *,
    active_session: GMSession | None = None,
    query: str = "",
    page_number: int | str = 1,
) -> PickerPage:
    """One page of the rail (``view="rail"``) or the menu (``"menu"``).

    Args:
        user: Whose sessions.
        view: ``rail`` or ``menu`` (:data:`SESSION_PICKER_SOURCES`).
        active_session: The session the page shows, pinned so it is listed
            (and marked) whatever page it is on.
        query: What to match.
        page_number: The page asked for.
    """
    url = reverse("gm:session_picker", args=[view])
    if active_session is not None:
        url = f"{url}?active={active_session.pk}"
    return picker_page(
        SESSION_PICKER_SOURCES[view],
        GMSession.objects.filter(user=user),
        element_id=f"session-{view}-picker",
        url=url,
        query=query,
        page_number=page_number,
        pinned=[active_session] if active_session is not None else [],
    )


def attachable_rows(user, attached: Iterable[Character]) -> QuerySet:
    """What the attach picker offers: attachable, and not attached already."""
    return (
        attachable_characters(user)
        .exclude(pk__in=[character.pk for character in attached])
        .select_related("user")
    )


def attachable_picker(
    user,
    session: GMSession | None,
    attached: Iterable[Character],
    *,
    query: str = "",
    page_number: int | str = 1,
) -> PickerPage:
    """One page of the Characters tab's attach picker.

    Args:
        user: Who attaches.
        session: The session the tab belongs to, or None before there is one.
        attached: The characters the session already holds.
        query: What to match.
        page_number: The page asked for.
    """
    url = reverse("gm:attach_picker")
    if session is not None:
        url = f"{url}?session={session.pk}"
    return picker_page(
        ATTACHABLE_SOURCE,
        attachable_rows(user, attached),
        element_id="gm-attach-picker",
        url=url,
        query=query,
        page_number=page_number,
    )


def play_session_picker(
    viewer: AnyUser,
    character: Character,
    *,
    query: str = "",
    page_number: int | str = 1,
) -> PickerPage:
    """One page of the sheet's "Take <name> to" session picker (issue #973).

    The rows are ``viewer``'s own sessions, the only ones
    ``gm_characters_attach`` attaches to; the caller decides whether the
    control is offered at all (``gm.attach.attach_refusal``).

    Args:
        viewer: Whose sessions.
        character: The character the control takes to the table; its id
            keeps one sheet's picker apart from another's and tells the
            endpoint which sessions already hold it.
        query: What to match.
        page_number: The page asked for.
    """
    return picker_page(
        PLAY_SESSION_SOURCE,
        GMSession.objects.filter(user=viewer).only("pk", "name", "updated_at"),
        element_id=f"play-with-gm-picker-{character.pk}",
        search_id=f"play-with-gm-search-{character.pk}",
        url=f"{reverse('gm:play_session_picker')}?character={character.pk}",
        query=query,
        page_number=page_number,
    )


def sessions_holding_character(viewer: AnyUser, character: Character) -> set[int]:
    """The ids of ``viewer``'s sessions that already hold ``character``."""
    return set(character.gm_sessions.filter(user=viewer).values_list("pk", flat=True))
