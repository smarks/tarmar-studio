"""The party page's searchable Add Members picker (issue #864).

Over :func:`parties.models.addable_characters`, the rows ``party_add_member``
accepts, less the party's members, so what a search offers is what the add
takes.
"""

from django.db.models import QuerySet
from django.urls import reverse

from common.pickers import PickerPage, PickerSource, picker_page

from .models import Party, addable_characters

#: One row per character, with its Add form.
ADDABLE_SOURCE = PickerSource(
    rows_template="parties/_addable_rows.html",
    search_label="Search characters by name or player",
    placeholder="Search by name or player…",
    search_fields=("name", "user__username"),
    order_by=("-created_at", "-pk"),
    noun="character",
    noun_plural="characters",
)


def addable_rows(user, party: Party) -> QuerySet:
    """The characters ``user`` may add to ``party``: addable, not yet members."""
    return (
        addable_characters(user)
        .exclude(party_memberships__party=party)
        .select_related("user")
    )


def addable_picker(
    user, party: Party, *, query: str = "", page_number: int | str = 1
) -> PickerPage:
    """One page of the Add Members picker for ``party``, as ``user`` sees it."""
    return picker_page(
        ADDABLE_SOURCE,
        addable_rows(user, party),
        element_id="party-character-picker",
        search_id="party-character-filter",
        url=reverse("parties:addable_picker", args=[party.pk]),
        query=query,
        page_number=page_number,
    )
