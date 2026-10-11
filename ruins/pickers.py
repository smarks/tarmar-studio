"""The hook generator's searchable party picker (issue #864).

Over the parties the generator accepts (``GenerateHookView``), newest first,
searched by name and owner. The party a generated hook was matched to stays
selectable on the page whatever page of results it is on.
"""

from django.db.models import QuerySet
from django.urls import reverse

from common.pickers import PickerPage, PickerSource, picker_page
from parties.models import Party, live_parties

#: A ``<select name="party">`` after its "No party" option.
HOOK_PARTY_SOURCE = PickerSource(
    rows_template="ruins/_hook_party_options.html",
    search_label="Search parties by name or owner",
    placeholder="Search parties by name or owner…",
    search_fields=("name", "owner__username"),
    order_by=("-created_at", "-pk"),
    noun="party",
    noun_plural="parties",
)


def hook_party_rows() -> QuerySet:
    """The parties the hook generator matches hooks to: none disbanded (#971)."""
    return live_parties()


def chosen_party(raw_party: str) -> Party | None:
    """The party the form names, to keep it selectable; None for none."""
    if not raw_party.isdigit():
        return None
    return hook_party_rows().filter(pk=int(raw_party)).first()


def hook_party_picker(
    raw_party: str = "", *, query: str = "", page_number: int | str = 1
) -> PickerPage:
    """One page of the hook generator's party picker.

    Args:
        raw_party: The party the form chose (``?party=``), pinned to the
            options so the form shows it chosen.
        query: What to match.
        page_number: The page asked for.
    """
    chosen = chosen_party(raw_party)
    url = reverse("hooks:party_picker")
    if chosen is not None:
        # A search keeps the chosen party chosen, as the session rail keeps
        # the open session with ?active=.
        url = f"{url}?party={chosen.pk}"
    return picker_page(
        HOOK_PARTY_SOURCE,
        hook_party_rows(),
        element_id="hook-party-picker",
        url=url,
        query=query,
        page_number=page_number,
        pinned=[chosen] if chosen is not None else [],
    )
