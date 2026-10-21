"""Party pages and roster endpoints (issue #274).

Parties are public objects: any registered user sees the list and any
detail page. What varies by reader is the *rendering* of each roster row —
full card when the reader can see the character (``characters_visible_to``),
name-only otherwise — and which controls appear: manage controls for the
party owner and DM/admin, a withdraw button on rows whose character the
reader owns (withdraw always wins, see ``parties.models``).

Roster changes are plain form posts the server re-renders, following the
battle setup's shape (#270): no client-side state, errors re-render the
page with an inline notice and status 400.

The edit page autosaves each field through :func:`party_update_field`, like
every other editable page (issue #339, item 17), and a manager may disband a
party -- a soft delete -- and restore it from the party list (item 18).
"""

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, DetailView, ListView

from characters.models import characters_visible_to
from common.breadcrumbs import BreadcrumbMixin, step, trail
from common.decorators import internal_errors_logged
from common.helpers import parse_json_text
from common.mixins import ActivePageMixin
from common.pagination import PAGE_SIZE
from common.pickers import asked_for, picker_body_response
from common.refusal_words import refusal_words as common_refusal_words

from .forms import PartyForm
from .models import (
    Party,
    PartyMembership,
    addable_characters,
    can_manage_party,
    can_remove_membership,
    manageable_parties,
)
from .pickers import addable_picker
from .refusal_words import refusal_words

DETAIL_TEMPLATE = "parties/party_detail.html"


class PartyAccessMixin(ActivePageMixin, LoginRequiredMixin):
    """Shared gate: any signed-in user. Parties are public objects."""

    login_url = "login"
    active_page = "parties"


class PartyListView(BreadcrumbMixin, PartyAccessMixin, ListView):
    """Every party, newest first, for every registered user."""

    breadcrumb_label = "Parties"

    model = Party
    template_name = "parties/party_list.html"
    context_object_name = "parties"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        return (
            Party.objects.filter(is_deleted=False)
            .select_related("owner")
            .annotate(member_count=Count("memberships"))
            # The pk breaks a tie in created_at, so a row stays on one page
            # (issue #950).
            .order_by("-created_at", "-pk")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Only the parties this reader could restore: a disbanded party is
        # off everyone else's list (issue #339, item 18).
        context["disbanded_parties"] = (
            manageable_parties(self.request.user)
            .filter(is_deleted=True)
            .select_related("owner")
            .order_by("-deleted_at")
        )
        return context


def _detail_context(request, party, error=""):
    """Everything the detail page renders, derived fresh from the database.

    Each roster row gets two request-scoped verdicts stapled on:

    - ``character_visible`` — may this reader see the character? Decides
      full card versus name-only. One rule, one statement: anything beyond
      the name goes through ``characters_visible_to``, so a member that
      went private (or was soft-deleted) collapses to name-only for every
      reader it isn't visible to — the party owner included, if it isn't
      theirs. There is no snapshot of what the owner could see at add
      time; visibility is always current.
    - ``can_withdraw`` — does this reader own the character? Ownership,
      not visibility: your own soft-deleted character still shows its
      withdraw button.
    """
    memberships = list(party.memberships.select_related("character", "character__user"))
    visible_ids = set(
        characters_visible_to(request.user)
        .filter(party_memberships__party=party)
        .values_list("pk", flat=True)
    )
    for membership in memberships:
        membership.character_visible = membership.character_id in visible_ids
        membership.can_withdraw = membership.character.user_id == request.user.pk
    manager = can_manage_party(request.user, party)
    context = {
        "party": party,
        "memberships": memberships,
        "can_manage": manager,
        "error": error,
        "breadcrumb": trail(step("Parties", reverse("parties:list")), step(party.name)),
        "active_page": "parties",
    }
    if manager:
        # A page at a time, newest first, with a search that reaches every
        # page (#864).
        picker = addable_picker(request.user, party)
        context["addable_picker"] = picker
        # The rows the shelf shows, for what reads them rather than the picker.
        context["addable_characters"] = picker.rows
    return context


@login_required
@require_GET
def party_addable_picker(request, pk):
    """One page of the Add Members picker (issue #864), for ``picker.js``.

    A manager only, as the shelf and :func:`party_add_member` are: anyone
    else is refused rather than shown whom they could not add.
    """
    party = get_object_or_404(Party, pk=pk, is_deleted=False)
    if not can_manage_party(request.user, party):
        raise PermissionDenied(refusal_words().views.owner_only_add)
    query, page_number = asked_for(request)
    picker = addable_picker(request.user, party, query=query, page_number=page_number)
    return picker_body_response(request, picker, {"party": party, "user": request.user})


class PartyDetailView(BreadcrumbMixin, PartyAccessMixin, DetailView):
    """The roster, visibility-aware, with the manager's add-member shelf."""

    queryset = Party.objects.filter(is_deleted=False)
    template_name = DETAIL_TEMPLATE
    context_object_name = "party"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_detail_context(self.request, self.object))
        return context


class PartyCreateView(BreadcrumbMixin, PartyAccessMixin, CreateView):
    """Any registered user forms a party; they become its owner."""

    model = Party
    form_class = PartyForm
    template_name = "parties/party_form.html"

    def get_breadcrumb(self, context):
        return trail(step("Parties", reverse("parties:list")), step("New party"))

    def form_valid(self, form):
        form.instance.owner = self.request.user
        return super().form_valid(form)


class PartyEditView(BreadcrumbMixin, PartyAccessMixin, DetailView):
    """Name/description edits, for the party owner or a DM/admin.

    Each field autosaves through :func:`party_update_field` (issue #339,
    item 17); there is no Save button. The queryset carries the permission:
    a party the reader may not manage is a 404 here, matching how the
    character editor hides what you may not touch rather than advertising
    it with a 403.
    """

    template_name = "parties/party_edit.html"
    context_object_name = "party"

    def get_queryset(self):
        return manageable_parties(self.request.user).filter(is_deleted=False)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # The inputs' limits come from the form, the one statement of them.
        form = PartyForm(instance=self.object)
        context["name_max_length"] = form.fields["name"].max_length
        context["description_max_length"] = form.fields["description"].widget.attrs[
            "maxlength"
        ]
        return context

    def get_breadcrumb(self, context):
        party = context["party"]
        return trail(
            step("Parties", reverse("parties:list")),
            step(party.name, reverse("parties:detail", kwargs={"pk": party.pk})),
            step("Edit"),
        )


@login_required
@require_POST
def party_add_member(request, pk):
    """Seat one character in the party — manager only, added immediately.

    The candidate is fetched through :func:`addable_characters`, so a
    character the manager may not seat (someone else's private one, a
    soft-deleted one, a made-up id) is a 404, never a hint. Adding a
    character already seated re-renders with an inline notice rather than
    silently succeeding: the manager's shelf never offers seated
    characters, so a duplicate post means the page is stale.
    """
    party = get_object_or_404(Party, pk=pk, is_deleted=False)
    if not can_manage_party(request.user, party):
        return render(
            request,
            DETAIL_TEMPLATE,
            _detail_context(request, party, refusal_words().views.owner_only_add),
            status=403,
        )
    try:
        character_id = int(request.POST.get("character_id") or "")
    except ValueError:
        return render(
            request,
            DETAIL_TEMPLATE,
            _detail_context(
                request, party, common_refusal_words().requests.numeric_character_id
            ),
            status=400,
        )
    character = get_object_or_404(addable_characters(request.user), pk=character_id)
    # get_or_create rather than create: it absorbs the duplicate-post race
    # atomically (its internal savepoint keeps an IntegrityError from
    # poisoning the surrounding transaction).
    _, created = PartyMembership.objects.get_or_create(
        party=party, character=character, defaults={"added_by": request.user}
    )
    if not created:
        return render(
            request,
            DETAIL_TEMPLATE,
            _detail_context(
                request,
                party,
                refusal_words().views.already_in_party.format(name=character.name),
            ),
            status=400,
        )
    return redirect("parties:detail", pk=party.pk)


@login_required
@require_POST
def party_remove_member(request, pk):
    """Strike one roster row: the party's manager removing a member, or a
    character's owner withdrawing their character (which always wins —
    :func:`can_remove_membership`)."""
    party = get_object_or_404(Party, pk=pk, is_deleted=False)
    try:
        membership_id = int(request.POST.get("membership_id") or "")
    except ValueError:
        return render(
            request,
            DETAIL_TEMPLATE,
            _detail_context(
                request, party, refusal_words().views.numeric_membership_id
            ),
            status=400,
        )
    try:
        membership = party.memberships.select_related("character", "party").get(
            pk=membership_id
        )
    except PartyMembership.DoesNotExist:
        raise Http404("No such member in this party") from None
    if not can_remove_membership(request.user, membership):
        return render(
            request,
            DETAIL_TEMPLATE,
            _detail_context(request, party, refusal_words().views.remove_denied),
            status=403,
        )
    membership.delete()
    return redirect("parties:detail", pk=party.pk)


PARTY_EDITABLE_FIELDS = ("name", "description")


def _field_refusal(message):
    return JsonResponse({"success": False, "error": message}, status=400)


@login_required
@require_POST
@internal_errors_logged(json_only=True)
def party_update_field(request, pk):
    """Autosave one of a party's fields (issue #339, item 17).

    Takes ``{"field": "name"|"description", "value": "..."}`` and answers in
    the shape ``autosave.js`` reads: ``success`` and the stored ``value``, or
    ``success: false`` with the ``error`` shown beside the field. The value
    is validated by :class:`PartyForm` together with the party's other
    current field, so the create form and autosave refuse the same things.
    """
    party = get_object_or_404(manageable_parties(request.user), pk=pk, is_deleted=False)
    words = refusal_words().views
    try:
        payload = parse_json_text(request.body or b"{}")
    except ValueError:
        # Answered as a body that is not an object is, just below.
        payload = None
    if not isinstance(payload, dict):
        return _field_refusal(words.request_not_json)
    field = payload.get("field")
    if field not in PARTY_EDITABLE_FIELDS:
        return _field_refusal(words.field_not_editable)
    value = payload.get("value")
    if not isinstance(value, str):
        return _field_refusal(words.text_value_required)
    data = {name: getattr(party, name) for name in PARTY_EDITABLE_FIELDS}
    data[field] = value
    form = PartyForm(data, instance=party)
    if not form.is_valid():
        messages = form.errors.get(field) or [
            message for errors in form.errors.values() for message in errors
        ]
        return _field_refusal(messages[0])
    form.save()
    return JsonResponse({"success": True, "value": getattr(party, field)})


@login_required
@require_POST
def party_disband(request, pk):
    """Disband a party: its owner or a DM/admin (issue #339, item 18).

    A soft delete, as for characters and battles: the party leaves the list
    and its page, the roster rows stay, and :func:`party_restore` puts it
    back. The members' characters are untouched. A party the reader may
    not manage, or one already disbanded, is a 404.
    """
    party = get_object_or_404(manageable_parties(request.user), pk=pk, is_deleted=False)
    party.is_deleted = True
    party.deleted_at = timezone.now()
    party.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
    return redirect("parties:list")


@login_required
@require_POST
def party_restore(request, pk):
    """Bring a disbanded party back: the same people who may disband it."""
    party = get_object_or_404(manageable_parties(request.user), pk=pk, is_deleted=True)
    party.is_deleted = False
    party.deleted_at = None
    party.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
    return redirect("parties:detail", pk=party.pk)
