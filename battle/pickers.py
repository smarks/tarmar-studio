"""The battle pages' searchable pickers (issue #864).

Three lists: the setup page's saved-character shelf and its party
``<select>``, and the lobby's "Bring one of your characters" ``<select>``.
Each is a :class:`common.pickers.PickerSource` over the queryset the matching
endpoint already accepts from (``seatable_characters``, the parties, and
``claimable_characters``), so what a search offers is what a post takes.
"""

from django.db.models import QuerySet
from django.urls import reverse

from common.pickers import PickerPage, PickerSource, picker_page
from parties.models import Party, live_parties

from .access import Actor
from .models import Battle, BattleDraft
from .services import claimable_characters, seatable_characters

#: Characters, searched by name and owner, newest first.
CHARACTER_SEARCH_FIELDS = ("name", "user__username")
NEWEST_FIRST = ("-created_at", "-pk")

#: The setup page's shelf: a row per character with its Edit and Seat.
SHELF_SOURCE = PickerSource(
    rows_template="battle/_shelf_rows.html",
    search_label="Search characters by name or player",
    placeholder="Search by name or player…",
    search_fields=CHARACTER_SEARCH_FIELDS,
    order_by=NEWEST_FIRST,
    noun="character",
    noun_plural="characters",
)

#: The setup page's party import: a ``<select name="party_id">``.
PARTY_SOURCE = PickerSource(
    rows_template="battle/_party_options.html",
    search_label="Search parties by name or owner",
    placeholder="Search parties by name or owner…",
    search_fields=("name", "owner__username"),
    order_by=NEWEST_FIRST,
    noun="party",
    noun_plural="parties",
)

#: The lobby's claim: a ``<select name="character_id">`` of the viewer's own.
CLAIM_SOURCE = PickerSource(
    rows_template="battle/_claim_options.html",
    search_label="Search your characters by name",
    placeholder="Search your characters…",
    search_fields=CHARACTER_SEARCH_FIELDS,
    order_by=NEWEST_FIRST,
    noun="character",
    noun_plural="characters",
)

#: The setup pickers by the name their endpoint's URL carries.
SETUP_PICKER_KINDS = ("characters", "parties")


def shelf_rows(actor: Actor) -> QuerySet:
    """The characters the setup shelf offers: what ``battle_setup_add`` seats."""
    return seatable_characters(actor.user).select_related("user")


def party_rows(actor: Actor) -> QuerySet:
    """The parties the setup page offers: what ``import_party_into_draft``
    takes, so no disbanded one (#971); a guest is offered none (#305)."""
    if actor.is_guest:
        return Party.objects.none()
    return live_parties().prefetch_related("memberships")


def claim_rows(actor: Actor) -> QuerySet:
    """The characters a lobby seat may be claimed with: the viewer's own."""
    return claimable_characters(actor.user)


def setup_picker(
    draft: BattleDraft,
    actor: Actor,
    kind: str,
    *,
    query: str = "",
    page_number: int | str = 1,
) -> PickerPage:
    """One page of the setup page's shelf or party picker.

    Args:
        draft: The draft the page sets up.
        actor: Who is looking.
        kind: ``characters`` or ``parties`` (:data:`SETUP_PICKER_KINDS`).
        query: What to match.
        page_number: The page asked for.

    Returns:
        The page.
    """
    url = reverse("battle:setup-picker", args=[draft.pk, kind])
    if kind == "characters":
        return picker_page(
            SHELF_SOURCE,
            shelf_rows(actor),
            element_id="setup-character-picker",
            search_id="character-filter",
            url=url,
            query=query,
            page_number=page_number,
        )
    return picker_page(
        PARTY_SOURCE,
        party_rows(actor),
        element_id="setup-party-picker",
        url=url,
        query=query,
        page_number=page_number,
    )


def claim_picker(
    battle: Battle, actor: Actor, *, query: str = "", page_number: int | str = 1
) -> PickerPage:
    """One page of the lobby's claim picker (one per open seat on the page)."""
    return picker_page(
        CLAIM_SOURCE,
        claim_rows(actor),
        element_id="seat-claim-picker",
        url=reverse("battle:claim-picker", args=[battle.pk]),
        query=query,
        page_number=page_number,
    )
