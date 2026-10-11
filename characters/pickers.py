"""The character list's searchable owner filter (issue #973).

A Game Master's character list filtered by owner from a ``<select>`` of every
active account, a list that grows with every player. It is now a
:class:`common.pickers.PickerSource`: a page of active accounts, newest first,
searched by username, with the account the list is filtered by kept chosen
whatever page it is on.

Who may use it: ``CharacterListView`` applies ``?user=`` for an account with
``can_manage_users`` only, and the picker's endpoint
(``OwnerFilterPickerView``) is gated the same way.
"""

from django.db.models import QuerySet
from django.urls import reverse

from accounts.models import User
from common.pickers import PickerPage, PickerSource, picker_page

#: A ``<select id="user-filter">`` after its "All Users" option.
OWNER_FILTER_SOURCE = PickerSource(
    rows_template="characters/_owner_filter_options.html",
    search_label="Search owners by username",
    placeholder="Search owners…",
    search_fields=("username",),
    order_by=("-date_joined", "-pk"),
    noun="account",
    noun_plural="accounts",
)


def owner_filter_rows() -> QuerySet:
    """The accounts the owner filter offers: every active one."""
    return User.objects.filter(is_active=True)


def chosen_owner(raw_user: str) -> User | None:
    """The account ``?user=`` filters the list by, to keep it chosen.

    Any account the list filters by, active or not: ``CharacterListView``
    filters by whatever id the address names, and the select should say so
    rather than claim "All Users" over a filtered list.
    """
    if not raw_user.isdigit():
        return None
    return User.objects.filter(pk=int(raw_user)).first()


def owner_filter_picker(
    raw_user: str = "", *, query: str = "", page_number: int | str = 1
) -> PickerPage:
    """One page of the character list's owner filter.

    Args:
        raw_user: The account the list is filtered by (``?user=``), pinned
            so the select shows it chosen.
        query: What to match.
        page_number: The page asked for.
    """
    chosen = chosen_owner(raw_user)
    url = reverse("characters:owner_picker")
    if chosen is not None:
        # A search keeps the chosen owner chosen, as the hook generator's
        # party picker keeps its party with ?party=.
        url = f"{url}?user={chosen.pk}"
    return picker_page(
        OWNER_FILTER_SOURCE,
        owner_filter_rows(),
        element_id="owner-filter-picker",
        search_id="owner-filter-search",
        url=url,
        query=query,
        page_number=page_number,
        pinned=[chosen] if chosen is not None else [],
    )
