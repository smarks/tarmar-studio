"""Character CRUD, creation, import, soft-delete, and single-field edits.

The "core" module of the ``characters.views`` package (issue #90): listing,
creating, importing, editing, deleting/restoring, and the mark-dead/revive and
single-field-update endpoints. Skills/experience live in ``.skills``,
equipment in ``.equipment``, and attack resolution in ``.combat`` — each of
those imports ``_log_character_event`` from here, since it's shared
audit-logging used across every mutating endpoint on the character sheet.
"""

import dataclasses
from collections.abc import Mapping
from urllib.parse import quote

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView

from accounts.models import User
from characters.constants import CHARACTER_ATTRIBUTE_FIELDS
from common.audit import audit_logger
from common.breadcrumbs import BreadcrumbMixin, step, trail
from common.decorators import rate_limit
from common.helpers import (
    _can_edit_object,
    _json_error,
    _parse_json_body,
    _permission_denied,
    database_integer,
    parse_json_text,
    safe_next_url,
)
from common.mixins import ActivePageMixin, AdminOrDMRequiredMixin
from common.pagination import PAGE_SIZE, query_without
from common.pickers import asked_for, picker_body_response
from gm.templatetags.gm_play import play_with_game_master

from .. import attack, services
from ..combat import ARMOUR_TIERS
from ..constants import (
    CHARACTER_TEXT_LIMITS,
    CORE_ATTRIBUTE_FIELDS,
    MAX_ARMOR_DEX_PENALTY_MAGNITUDE,
    MAX_ARMOR_STOPS,
    MAX_CHARACTER_NAME_LENGTH,
    MAX_DEATH_CAUSE_LENGTH,
    MAX_EQUIPMENT_MODIFIER,
    MAX_EQUIPMENT_QUANTITY,
    MAX_EQUIPMENT_WEIGHT_LBS,
    UNNAMED_CHARACTER,
    name_within_rule,
    too_long,
)
from ..dice import build_point_buy_character_stats, roll_all_character_stats
from ..equipment import CATALOG_GROUPS, ITEM_CATEGORIES, SHIELDS, get_item
from ..import_service import (
    IMPORT_NOTICE_KEY,
    CharacterImportError,
    held_to_player,
    save_imported_character,
    validate_import_payload,
)
from ..magic import MAX_MANA_GRANT, current_mana, known_spells, learnable_spells
from ..models import (
    CHARACTER_FIELDS,
    MAX_COMBAT_LOG_ENTRIES,
    MAX_EXPERIENCE_LOG_ENTRIES,
    SHEET_RULES_CELL_FIELDS,
    Character,
    HistoryEntry,
    character_has_field,
    characters_visible_to,
)
from ..pickers import owner_filter_picker
from ..purse import format_coin, format_purse
from ..rules import character_rules
from ..services import CharacterChangeError
from ..skill_checks import (
    GOVERNING_ATTRIBUTES,
    LITERACY_SKILL,
    MAX_DIFFICULTY,
    SKILL_CHECK_MAXIMUM,
    SKILL_CHECK_MINIMUM,
    governing_attribute,
    literacy_refusal,
)
from ..skill_rules import skill_rules
from ..skills import (
    DEFAULT_RUN_YEARS,
    MAX_EXPERIENCE_YEARS_PER_RUN,
    age_for_aging_stage,
    granted_skill_names,
    next_aging_change_after_age,
    skill_rows,
    survival_targets,
    track_labels,
)
from ..weapon_skill import UNRECORDED_WEAPON_SKILL, weapon_choices
from .access import answer_change, edit_denied, locked_character_or_404, may_edit

#: Each number input's bound on the sheet, by the name the template reads it
#: under (``number_limits.<field>``) and the script under the page config's
#: ``numberLimits``: the constant its endpoint refuses by, never a copy of it
#: (issue #938). A range symmetric about zero is given by its magnitude, and
#: a floor of 0 or 1 (no negative weight, no empty row) is not a limit here.
CHARACTER_NUMBER_LIMITS: dict[str, int] = {
    "equipment_quantity": MAX_EQUIPMENT_QUANTITY,
    "equipment_weight": MAX_EQUIPMENT_WEIGHT_LBS,
    "equipment_modifier": MAX_EQUIPMENT_MODIFIER,
    "armor_stops": MAX_ARMOR_STOPS,
    "armor_dex_penalty": MAX_ARMOR_DEX_PENALTY_MAGNITUDE,
    "check_difficulty": MAX_DIFFICULTY,
    "check_total_minimum": SKILL_CHECK_MINIMUM,
    "check_total_maximum": SKILL_CHECK_MAXIMUM,
    "mana_grant": MAX_MANA_GRANT,
    "defender_dodge": attack.MAX_DEFENDER_DODGE,
    "situational": attack.MAX_SITUATIONAL,
}

#: What the Location section's literacy row is painted from (issues #740,
#: #741, #743). The field-update response for ``location`` and
#: ``intelligence``, the roll-literacy and the rule-literacy responses carry
#: exactly these keys, and
#: static/js/character-edit.js repaints the row from the same list, declared
#: there as LITERACY_RESPONSE_KEYS. A test holds the two lists equal.
LITERACY_RESPONSE_KEYS = (
    "location_literacy_check",
    "location_literacy_modifier",
    "literacy_roll",
    "is_literate",
    "literacy_result",
    "can_roll_literacy",
    "can_change_origin",
    "literacy_ruling",
    "can_rule_literacy",
)


def literacy_response(character: Character, *, is_game_master: bool) -> dict:
    """The literacy row's values, keyed by :data:`LITERACY_RESPONSE_KEYS`.

    Args:
        character: The saved character.
        is_game_master: Whether the asker is a Game Master, who may roll the
            check again and rule on it at "Consult GM".

    Returns:
        One entry per key, read off the record.
    """
    values = {
        "location_literacy_check": character.location_literacy_check,
        "location_literacy_modifier": character.location_literacy_modifier,
        "literacy_roll": character.literacy_roll,
        "is_literate": character.is_literate,
        # The outcome as the sheet prints it, so the page sets this text
        # rather than rebuilding it (one rendering, Character.literacy_result).
        "literacy_result": character.literacy_result,
        "can_roll_literacy": services.can_roll_literacy(
            character, is_game_master=is_game_master
        ),
        # A player's Origin locks once literacy is rolled (#741): the roll's
        # answer turns the select read-only without a reload.
        "can_change_origin": services.can_change_origin(
            character, is_game_master=is_game_master
        ),
        # The Game Master's ruling at "Consult GM" (#743), and whether the
        # ruling control shows: an Origin change can bring it or take it away.
        "literacy_ruling": character.literacy_ruling,
        "can_rule_literacy": services.can_rule_literacy(
            character, is_game_master=is_game_master
        ),
    }
    return {key: values[key] for key in LITERACY_RESPONSE_KEYS}


def _emit_character_audit(
    event: str,
    *,
    actor: str,
    character_name: str,
    character_pk: int,
    owner: str,
    action: str = "on",
    detail: str = "",
) -> None:
    """Write one character audit line, in the app's single audit format.

    Every character-affecting event this app records reads the same way --
    ``EVENT: user=<actor> <action> character=<name> (pk=<n>) owned by
    user=<owner>`` -- and the endpoints used to assemble that sentence and
    its five arguments themselves, five times over. ``action`` is the only
    part that varies between them, so it is the only part they pass.

    Args:
        event: The event name, e.g. ``CHARACTER_DELETE``.
        actor: Username of whoever did it.
        character_name: The character's name at the time.
        character_pk: The character's primary key.
        owner: Username of the character's owner.
        action: The verb phrase between actor and character. Defaults to
            ``"on"``; endpoints that read better with their own verb pass it
            (``"soft-deleted"``, ``"restored"``, ``"changed field=age on"``).
        detail: Free text appended as ``detail=<repr>``. Omitted when empty.
    """
    audit_logger.info(
        "%s: user=%s %s character=%s (pk=%s) owned by user=%s%s",
        event,
        actor,
        action,
        character_name,
        character_pk,
        owner,
        f" detail={detail!r}" if detail else "",
    )


def _log_character_event(
    request,
    character,
    event: str,
    detail: str = "",
    *,
    action: str = "on",
    only_when_not_owner: bool = True,
) -> None:
    """Audit-log a character-affecting event, by default only for non-owners.

    Used by equipment mutations, the soft-delete and single-field writes, and
    the DM mark-dead / revive / restore endpoints. Self-edits don't log
    (matches the equipment-change convention) unless
    ``only_when_not_owner`` is False -- which restore passes, because a
    restore is a privileged act worth recording whoever owns the row.
    """
    if only_when_not_owner and character.user == request.user:
        return
    _emit_character_audit(
        event,
        actor=request.user.username,
        character_name=character.name,
        character_pk=character.pk,
        owner=character.user.username,
        action=action,
        detail=detail,
    )


class CharacterListView(BreadcrumbMixin, ActivePageMixin, LoginRequiredMixin, ListView):
    breadcrumb_label = "Characters"

    """List characters. Regular users see their own; DM/Admin see all with filtering."""

    model = Character
    template_name = "characters/character_list.html"
    context_object_name = "characters"
    login_url = "login"
    active_page = "characters"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        user = self.request.user
        show_deleted = self.request.GET.get("show_deleted") == "1"

        if user.can_manage_users:
            # DM/Admin: show all characters, optionally filtered by user
            qs = Character.objects.select_related("user").order_by(
                "is_deleted", "-created_at", "-pk"
            )
            # ?user= comes straight off the URL bar, so it need not be a
            # number: filtering on a non-numeric value raised ValueError out
            # of the view and answered 500 (issue #641). An owner filter that
            # names no user filters nothing, which is what the unfiltered
            # list already shows.
            filter_user_id = self.request.GET.get("user", "")
            if filter_user_id.isdigit():
                qs = qs.filter(user_id=filter_user_id)
            if not show_deleted:
                qs = qs.filter(is_deleted=False)
            return qs
        # Regular users: their own characters plus everyone's public ones
        # (issue #273) -- the same read scope characters_visible_to states.
        # "Show deleted" widens the OWN half only: another player's deleted
        # character stays out of sight, public flag or not.
        qs = characters_visible_to(user)
        if show_deleted:
            qs = qs | Character.objects.filter(user=user, is_deleted=True)
        # The pk breaks a tie in created_at, so a row stays on one page (#950).
        return qs.select_related("user").order_by("is_deleted", "-created_at", "-pk")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["show_deleted"] = self.request.GET.get("show_deleted") == "1"
        # Preserve every query param except `page` so pagination links keep filters.
        context["preserved_query"] = query_without(self.request, "page")
        if user.can_manage_users:
            context["is_manager"] = True
            filter_user_id = self.request.GET.get("user", "")
            # A page of accounts, newest first, with a search that reaches
            # every page; the one the list is filtered by stays chosen (#973).
            context["owner_picker"] = owner_filter_picker(filter_user_id)
            context["filter_user_id"] = filter_user_id
        return context


class OwnerFilterPickerView(LoginRequiredMixin, AdminOrDMRequiredMixin, View):
    """One page of the character list's owner filter (issue #973).

    What ``picker.js`` asks for as a Game Master types or turns a page; gated
    as the filter is, which ``CharacterListView`` applies for an account with
    ``can_manage_users`` only. ``?user=`` keeps the chosen owner chosen.
    """

    login_url = "login"

    def get(
        self, request: HttpRequest, *args: object, **kwargs: object
    ) -> HttpResponse:
        """The body ``picker.js`` puts in place for this query and page."""
        filter_user_id = request.GET.get("user", "").strip()
        query, page_number = asked_for(request)
        picker = owner_filter_picker(
            filter_user_id, query=query, page_number=page_number
        )
        return picker_body_response(request, picker, {"filter_user_id": filter_user_id})


CHARACTER_CREATE_TEMPLATE = "characters/character_create.html"

# Every stat a freshly generated character carries over from its stats dict.
# Both creation paths (rolled and point-buy) produce all of these.
_CREATION_STAT_FIELDS = (
    *CHARACTER_ATTRIBUTE_FIELDS,
    "appearance_roll",
    "height_roll",
    "weight_roll",
    "fatigue_roll",
    "provenance_class",
    "provenance_detail",
    "location",
    # The skill the rolled location grants. Seeded by
    # roll_secondary_character_stats, so both creation paths carry it.
    "skills",
    # Which of the skills holds that grant (issue #754).
    "origin_skill",
    "wealth_level",
    "starting_coin",
    "dice_log",
)


def _create_character_from_stats(
    user: User, stats: dict, *, name: str, is_public: bool = False
) -> Character:
    """Create a character from a generated stats dict.

    Args:
        user: The owner of the new character.
        stats: A stats dict from either creation path; must carry every key in
            ``_CREATION_STAT_FIELDS``.
        name: The checked name (``_creation_name``, issue #786).
        is_public: The visibility the creation form chose (issue #273).
            Defaults private, like every other way a character comes to exist
            (import included).

    Returns:
        The saved Character.
    """
    return services.create_character(
        user,
        name,
        {
            "is_public": is_public,
            **{field: stats[field] for field in _CREATION_STAT_FIELDS},
        },
    )


#: What the creation page says when a player leaves the name blank (#786).
NAME_REQUIRED_MESSAGE = "Give your character a name."


def _creation_name(
    submitted: Mapping[str, str], *, may_leave_blank: bool, account_name: str
) -> tuple[str | None, str | None]:
    """The name the creation form gave, checked, or why it was refused (#786).

    Creation never asked for a name, so every character took its account's.
    A player now names the character here. The name is held to the rule the
    sheet's rename and the import already apply
    (``Character.clean_field_value``), so the three cannot disagree about
    what a name may be.

    Args:
        submitted: The POST data; its ``name`` is the typed name.
        may_leave_blank: True for a Game Master (``can_manage_users``), whose
            creation path is unchanged: a blank name still takes the account's
            name, as every character's did before #786.
        account_name: The name a Game Master's blank name falls back to.

    Returns:
        ``(name, error_message)`` with exactly one of the two set.
    """
    typed = str(submitted.get("name", "")).strip()
    if not typed:
        if may_leave_blank:
            # An account name may hold "@" or "+", which the name rule
            # refuses; the fallback keeps what it accepts, so the character's
            # own export can be imported (issue #870).
            return name_within_rule(account_name, fallback=UNNAMED_CHARACTER), None
        return None, NAME_REQUIRED_MESSAGE
    try:
        return str(Character.clean_field_value("name", typed)), None
    except ValidationError as refusal:
        return None, f"{refusal.messages[0]}."


def _point_buy_defaults() -> list[int]:
    """Spread the canon's point-buy budget across the core six as evenly as it
    divides.

    Returns:
        One starting value per core attribute, in CORE_ATTRIBUTE_FIELDS order,
        summing to exactly the budget.
    """
    attribute_count = len(CORE_ATTRIBUTE_FIELDS)
    budget = character_rules().attributes.point_buy.total
    even_share, remainder = divmod(budget, attribute_count)
    return [
        even_share + 1 if index < remainder else even_share
        for index in range(attribute_count)
    ]


def _character_create_context(
    submitted: Mapping[str, str] | None = None,
    error: str | None = None,
    next_url: str = "",
    can_exceed_point_buy: bool = False,
    name_optional: bool = False,
) -> dict:
    """Context for the creation page, echoing back whatever was submitted.

    Args:
        submitted: The rejected POST data, so the player's own numbers come
            back in the fields. None on a first visit, which prefills defaults.
        error: The reason the submission was rejected, rendered on the page.
        next_url: A validated same-site continuation (the battle setup's
            round trip, #270) carried through both forms and honoured by the
            post-create redirect.
        can_exceed_point_buy: Whether this user may buy off budget (#299).
            Drives the tally script: for them an off-budget total warns and
            leaves the button live, rather than disabling it.
        name_optional: Whether this user may leave the name blank (#786): a
            Game Master may, and the character takes the account's name.

    Returns:
        The template context.
    """
    defaults = _point_buy_defaults()
    point_buy = character_rules().attributes.point_buy
    return {
        "next_url": next_url,
        # The name (issue #786), echoed back like the visibility below so a
        # rejected submission keeps what was typed.
        "name": submitted.get("name", "") if submitted is not None else "",
        "name_optional": name_optional,
        "name_max_length": MAX_CHARACTER_NAME_LENGTH,
        # The visibility choice (issue #273), echoed back like the point-buy
        # numbers so a rejected submission keeps it. First visits default
        # private -- public is always an explicit choice.
        "visibility": (
            submitted.get("visibility", "private")
            if submitted is not None
            else "private"
        ),
        "breadcrumb": trail(
            step("Characters", reverse("characters:list")),
            step("New character"),
        ),
        "active_page": "characters",
        "point_buy_total": point_buy.total,
        "point_buy_min": point_buy.minimum,
        "point_buy_max": point_buy.maximum,
        "point_buy_error": error,
        "can_exceed_point_buy": can_exceed_point_buy,
        "core_attributes": [
            {
                "field": field_name,
                "label": label,
                "value": (
                    submitted.get(field_name, default)
                    if submitted is not None
                    else default
                ),
            }
            for (field_name, label), default in zip(
                CORE_ATTRIBUTE_FIELDS, defaults, strict=True
            )
        ],
    }


def _parse_point_buy_attributes(
    submitted: Mapping[str, str],
    *,
    allow_off_budget: bool = False,
) -> tuple[dict[str, int] | None, str | None]:
    """Validate a submitted 65-point buy of the six core attributes.

    This is the authoritative check — the page's JavaScript only mirrors it.

    Args:
        submitted: The POST data carrying one value per core attribute.
        allow_off_budget: When true, a total other than the budget is
            accepted instead of refused (issue #299 — admins and DMs building
            NPCs). The per-attribute range still binds: it mirrors what 3d6 can
            produce, which is a different rule from the budget.

    Returns:
        ``(attributes, error_message)`` with exactly one of the two set.
    """
    point_buy = character_rules().attributes.point_buy
    attributes: dict[str, int] = {}
    for field_name, label in CORE_ATTRIBUTE_FIELDS:
        raw_value = str(submitted.get(field_name, "")).strip()
        try:
            value = int(raw_value)
        except ValueError:
            return None, f"{label} must be a whole number."
        if not point_buy.minimum <= value <= point_buy.maximum:
            return None, (
                f"{label} must be between {point_buy.minimum} and "
                f"{point_buy.maximum}; you entered {value}."
            )
        attributes[field_name] = value

    spent = sum(attributes.values())
    if spent != point_buy.total and not allow_off_budget:
        return None, (
            f"The six core attributes must total exactly {point_buy.total}; "
            f"yours total {spent}."
        )
    return attributes, None


@login_required
def character_create(request) -> HttpResponse:
    """Create a character with rolled attributes or a 65-point buy.

    GET shows the two options. POST carries `method=roll` or `method=point-buy`
    and the character's `name` (issue #786); the name and the point-buy values
    are validated here, server-side, and a bad one re-renders the form with the
    reason rather than creating anything.

    A validated ``next`` continuation (the battle setup's add-a-character
    round trip, #270) rides through the forms and onto the editor redirect,
    so the editor can offer the way back to the in-progress setup.
    """
    next_url = safe_next_url(request)
    # Admins and DMs build NPCs the 65-point budget does not describe (#299),
    # so for them it warns instead of refusing. `can_manage_users` is the
    # existing "admin or DM" property; this must not grow a second one.
    can_exceed_point_buy = request.user.can_manage_users

    def creation_page(
        submitted: Mapping[str, str] | None = None,
        error: str | None = None,
        *,
        status: int = 200,
    ) -> HttpResponse:
        """The creation page, echoing a refused submission and its reason."""
        return render(
            request,
            CHARACTER_CREATE_TEMPLATE,
            _character_create_context(
                submitted,
                error,
                next_url=next_url,
                can_exceed_point_buy=can_exceed_point_buy,
                # One role check for both allowances: a Game Master's path
                # through this page is otherwise what it was before #786.
                name_optional=can_exceed_point_buy,
            ),
            status=status,
        )

    if request.method != "POST":
        return creation_page()

    creation_method = request.POST.get("method", "")
    if creation_method not in ("roll", "point-buy"):
        return creation_page(
            request.POST,
            "The form did not say whether to roll the attributes or buy "
            f"them with {character_rules().attributes.point_buy.total} points. "
            "Use one of the two "
            "buttons below.",
            status=400,
        )
    attributes: dict[str, int] | None = None
    if creation_method == "point-buy":
        attributes, error = _parse_point_buy_attributes(
            request.POST, allow_off_budget=can_exceed_point_buy
        )
        if error is not None:
            return creation_page(request.POST, error, status=400)
    # After the buy's own check, so a refused buy still says what is wrong
    # with its numbers; before any dice, so a refused name rolls nothing.
    name, name_error = _creation_name(
        request.POST,
        may_leave_blank=can_exceed_point_buy,
        account_name=request.user.username,
    )
    if name is None:
        return creation_page(request.POST, name_error, status=400)
    stats = (
        build_point_buy_character_stats(attributes)
        if attributes is not None
        else roll_all_character_stats()
    )

    # The form's visibility choice (issue #273): anything but the explicit
    # "public" value -- including its absence -- creates a private character.
    is_public = request.POST.get("visibility") == "public"
    character = _create_character_from_stats(
        request.user, stats, name=name, is_public=is_public
    )
    edit_url = reverse("characters:edit", args=[character.pk])
    if next_url:
        return redirect(f"{edit_url}?next={quote(next_url)}")
    return redirect(edit_url)


# Maximum upload size for character imports — 256 KB is well above any real
# export (the largest historical export was ~12 KB) and is small enough to
# reject obviously bogus payloads without reading them.
CHARACTER_IMPORT_MAX_BYTES = 256 * 1024


@login_required
@require_POST
@rate_limit("character_import", max_requests=10, window_seconds=60)
def character_import(request):
    """Import a character from a JSON export.

    The importer becomes the owner of the resulting character (DM/Admin can
    import on their own behalf too — there's no separate "import as another
    user" affordance). The payload is checked and saved by
    ``characters.import_service`` -- the same code the GM's ``/character``
    command uses (issue #344) -- so this view only handles the upload. The
    payload's name is saved as it stands: names are not unique per user
    (issue #571), so an import never renames what it is given. A player's
    import arrives without its rolled, earned or ruled values, and with a
    notice saying so (issue #744). Audit-logs every import.
    """
    upload = request.FILES.get("file")
    if not upload:
        return _json_error("No file uploaded")
    if upload.size > CHARACTER_IMPORT_MAX_BYTES:
        return _json_error("File is too large to import")

    try:
        payload = parse_json_text(upload.read().decode("utf-8"))
    except ValueError:
        return _json_error("File is not valid JSON")

    try:
        imported = held_to_player(
            validate_import_payload(payload),
            is_game_master=request.user.can_manage_users,
            # A chain on the character this file exports is kept (#941).
            importer=request.user,
        )
        character = save_imported_character(request.user, imported)
    except CharacterImportError as error:
        return _json_error(str(error))
    if imported.left_out:
        # Kept in the history, so the sheet says what the file carried and
        # the import did not bring in (issue #757).
        character.record_field_change(
            "equipment",
            "",
            "import left out: " + "; ".join(imported.left_out),
            request.user.username,
        )
        character.save(update_fields=["history"])

    audit_logger.info(
        "CHARACTER_IMPORT: user=%s imported character=%s (pk=%s) original_name=%r",
        request.user.username,
        character.name,
        character.pk,
        imported.name,
    )
    return JsonResponse(
        {
            "success": True,
            "character_id": character.pk,
            "name": character.name,
            "left_out": imported.left_out,
            # What a player's import dropped (issue #744); the sheet the page
            # opens next shows the same text until it is dismissed.
            IMPORT_NOTICE_KEY: character.import_notice,
        }
    )


# How far past aging stage 13 (the public pages' last tabulated aging stage)
# the workbench preview precomputes concrete penalty numbers. Stage 20 is age 96 --
# comfortably past any character anyone actually plays to. Ages beyond this
# still take real, uncapped aging on the server; the preview notes the ongoing
# rate instead of listing every further stage (issue #163).
AGING_PREVIEW_HORIZON_STAGE = 20


def aging_preview_horizon_age() -> int:
    """The age :data:`AGING_PREVIEW_HORIZON_STAGE` begins at (96 on the public
    pages).

    A function, not a module constant: the age is the canon's
    (``characters.rules``), read when a page asks, so importing this module
    (and the URL configuration with it) never loads the canon and a bad file
    is reported by the ``characters.E001`` system check (issue #59).
    """
    return age_for_aging_stage(AGING_PREVIEW_HORIZON_STAGE)


def attack_weapon_choices(character) -> tuple[list[dict], list[str]]:
    """The Attack Resolution panel's weapons, from the character's saved sheet.

    Returns ``(attack_weapons, gm_adjudicated)``: the options the weapon
    select offers -- each carrying its *equipment index*, so an option can
    only mean what the server thinks it means -- and the names of weapons
    with a class but no rollable damage, which the panel's footnote lists.
    The edit page renders both, and the equipment endpoints return them so
    the page can refresh the select after an edit (issue #339, item 24).
    """
    attack_weapons = []
    gm_adjudicated = []
    in_hand = character.weapon_in_hand_index
    for index, item in enumerate(character.equipment or []):
        item_data = get_item(item.get("item_id", ""))
        if not item_data or item_data.get("category") != "weapons":
            continue
        if not item_data.get("weapon_class"):
            continue  # no class, no Target Number — the endpoint refuses it
        if not attack.damage_is_rollable(item_data):
            # Club, cestus, lasso, torch (weapon), bola: a real weapon class
            # but "varies" / "by STR" / "1d6 fire" / nothing for damage. No
            # expression to roll, so the select omits them and the footnote
            # below says why rather than leaving a player hunting for a
            # weapon they can see in their pack.
            gm_adjudicated.append(item_data.get("name", item.get("item_id", "")))
            continue
        attack_weapons.append(
            {
                "index": index,
                "item_id": item.get("item_id", ""),
                "name": item_data.get("name", item.get("item_id", "")),
                "weapon_class": item_data["weapon_class"],
                "damage": item_data.get("damage", ""),
                # The panel offers the weapon in hand first (issue #846).
                "in_hand": index == in_hand,
            }
        )
    return attack_weapons, gm_adjudicated


def skill_check_skills(character: Character) -> list[dict]:
    """The skill check's options: every skill held and every governed one.

    Issue #756. Split out so a response that moves the skills held (a
    rename, an allocation, a run of years) can send the options back
    re-rendered (issues #765, #792).
    """
    held = dict(character.skills or {})
    check_skills = []
    for skill_name in sorted(set(held) | set(GOVERNING_ATTRIBUTES)):
        attribute = governing_attribute(skill_name)
        if skill_name in GOVERNING_ATTRIBUTES and attribute is None:
            continue  # Luck is not rolled as a check
        check_skills.append(
            {
                "name": skill_name,
                "held": skill_name in held,
                "attribute": (attribute or "")[:3].upper(),
                # A character who failed the literacy check, or has not
                # settled it, is not known to read; only a Game Master calls
                # for the check (#863 and its review), so the option says so.
                "game_master_only": skill_name == LITERACY_SKILL
                and literacy_refusal(character) is not None,
            }
        )
    return check_skills


def mechanics_context(character: Character, is_game_master: bool) -> dict:
    """What the sheet's skill check, Weapon skill, purse and magic need.

    Issues #753, #756, #757, #774: every number here is the server's, read
    from the record through the modules that hold each rule.
    """
    return {
        "is_game_master": is_game_master,
        "unrecorded_weapon_skill": UNRECORDED_WEAPON_SKILL,
        "weapon_choices": weapon_choices(),
        "skill_check_skills": skill_check_skills(character),
        "skill_check_attributes": CHARACTER_ATTRIBUTE_FIELDS,
        "purse": format_purse(character),
        "coin_adjustment": format_coin(character.coin_adjustment),
        "known_spells": known_spells(character),
        "current_mana": current_mana(character),
        "learnable_spells": learnable_spells(),
        "mechanics_config": {
            "skillCheckUrl": reverse("characters:skill_check", args=[character.pk]),
            "weaponSkillUrl": reverse(
                "characters:record_weapon_skill", args=[character.pk]
            ),
            "coinUrl": reverse("characters:move_coin", args=[character.pk]),
            "learnSpellUrl": reverse("characters:learn_spell", args=[character.pk]),
            "grantManaUrl": reverse("characters:grant_mana", args=[character.pk]),
        },
    }


class CharacterEditView(
    BreadcrumbMixin, ActivePageMixin, LoginRequiredMixin, DetailView
):
    def get_breadcrumb(self, context):
        return trail(
            step("Characters", reverse("characters:list")),
            step(context["character"].name),
        )

    """Edit a character's attributes."""

    model = Character
    template_name = "characters/character_edit.html"
    context_object_name = "character"
    login_url = "login"
    active_page = "character_editor"

    def get_queryset(self):
        # Who may OPEN this sheet is the read rule, characters_visible_to:
        # your own, any public one (issue #273), all of them for a DM/admin
        # -- but never a soft-deleted one (404, matching the export views and
        # every mutation endpoint below; restore/the deleted-characters list
        # are the deliberate exceptions that still see deleted rows). A
        # reader who is not the owner or a DM/admin gets the read-only
        # rendering (`can_edit` below), and every mutation endpoint
        # independently re-checks _can_edit_object, so the widened GET grants
        # no write anywhere.
        return characters_visible_to(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Read vs edit (issue #273): anyone the queryset admits may read, but
        # the edit affordances -- autosave inputs, the workbench, equipment
        # and attack controls, delete -- render only for whoever
        # _can_edit_object admits (owner or DM/admin). The same check guards
        # every mutation endpoint, so hiding here is presentation, not the
        # security boundary.
        context["can_edit"] = _can_edit_object(self.request, self.object)
        # The Fatigue roll's control (issue #636). A player rolls once and a
        # Game Master may ask again (issue #590), so a player who has rolled
        # is offered nothing rather than a button the server would refuse.
        context["can_roll_fatigue"] = (
            self.object.fatigue_roll is None or self.request.user.can_manage_users
        )
        # The literacy roll's control (issue #741): the same one-roll rule, and
        # no roll at all for an origin that publishes no check.
        context["can_roll_literacy"] = services.can_roll_literacy(
            self.object, is_game_master=self.request.user.can_manage_users
        )
        # A player's Origin is fixed once literacy is rolled; a Game Master's
        # is not (Spencer's ruling on #741). The select renders disabled with
        # the reason beside it, the same state the roll's repaint leaves.
        context["origin_fixed"] = not services.can_change_origin(
            self.object, is_game_master=self.request.user.can_manage_users
        )
        context["origin_fixed_reason"] = services.ORIGIN_FIXED_REASON
        # A rolled Origin is fixed for a player outright (#970): the cell is
        # text with its own reason (services.can_set_field), so the literacy
        # note, which belongs to the select, is not rendered beside it.
        context["origin_settable"] = services.can_set_field(
            self.object, "location", is_game_master=self.request.user.can_manage_users
        )
        # Who may set what (issue #748): the cell tags read this and
        # services.update_fields_for, the same answer the field update gives,
        # so a player sees a rolled or awarded value read-only with its reason
        # rather than an input the server would refuse.
        is_game_master = self.request.user.can_manage_users
        context["is_game_master"] = is_game_master
        context["can_grant_skill_points"] = can_grant_skill_points(
            is_game_master=is_game_master
        )
        # The sheet's other rolls (issues #748, #749): attributes, appearance,
        # provenance, wealth and a Game Master's coin reroll. Each control
        # renders for an editor when the server would take the roll.
        context["sheet_rolls"] = services.sheet_rolls_for(
            self.object, is_game_master=is_game_master
        )
        # A player may buy the scores nobody has recorded (#571), held to the
        # 65-point buy; the sheet says so above those inputs.
        context["point_buy_open"] = (
            not is_game_master and not services.attributes_known(self.object)
        )
        context["can_grant_skill_points"] = can_grant_skill_points(
            is_game_master=is_game_master
        )
        # The cells for the sheetrules batch's fields (#801, #810), each
        # rendered only where the model carries it.
        context["sheet_fields"] = {
            name: character_has_field(name) for name in SHEET_RULES_CELL_FIELDS
        }
        context["can_set_item_modifiers"] = services.can_set_item_modifiers(
            is_game_master=is_game_master
        )
        context["item_modifier_reason"] = services.ITEM_MODIFIER_RULE.reason
        # The Game Master's literacy ruling (#743): the control renders for a
        # Game Master who may edit, shown at "Consult GM" and hidden elsewhere
        # so an Origin change can bring it back without a reload.
        context["may_rule_literacy"] = context["can_edit"] and is_game_master
        context["can_rule_literacy"] = services.can_rule_literacy(
            self.object, is_game_master=is_game_master
        )
        # Current Fatigue and Body (issue #751): a fight writes them and a
        # Game Master may set them; a player reads them.
        context["can_set_pools"] = is_game_master
        # The survival save off the board (#839): why it cannot be rolled
        # now, or None, from the same rule the endpoint enforces.
        context["survival_save_refusal"] = services.survival_save_refusal(
            self.object, is_game_master=is_game_master
        )
        # The battle setup's round trip (#270): arriving with a validated
        # ?next=<setup page> renders a persistent way back to the
        # in-progress setup, whose state lives server-side on its draft.
        context["next_url"] = safe_next_url(self.request)
        # Skill rows are rendered from server-computed levels: the level ladder
        # (and Arcane's doubled cost) lives in skills.py only, and the page
        # never re-derives it in JavaScript (issues #146, #154).
        context["skills_display"] = skill_rows(self.object.skills)
        # Suggestion corpus for the add-skill input's type-ahead: every real
        # skill any track's d20 table can grant, plus this character's own
        # skills (which can include a freely-typed name off no track at all).
        # A native <datalist> reads this list but never restricts the input
        # to it, so an unlisted skill stays typeable (issue #224).
        context["skill_name_suggestions"] = skill_name_suggestions(self.object)
        context.update(
            mechanics_context(self.object, self.request.user.can_manage_users)
        )
        # Recent edits, newest first.
        context["recent_history"] = recent_history_entries(self.object)
        # The workbench card's consequence preview for the years a living
        # character would roll next (issues #217, #221). Rendered server-side
        # so the preview is correct before any JavaScript runs; static/js/
        # character-edit.js recomputes the same numbers from
        # character_edit_config when the track or the year count changes.
        # A fresh page always offers the default run of one year.
        context["default_run_years"] = DEFAULT_RUN_YEARS
        context["max_run_years"] = MAX_EXPERIENCE_YEARS_PER_RUN
        survival_dice = skill_rules().survival_roll.dice
        labels = track_labels()
        context["survival_dice"] = survival_dice
        context["next_year_number"] = self.object.experience_years + 1
        context["next_run_age"] = self.object.age + DEFAULT_RUN_YEARS
        context["aging_preview_horizon_age"] = aging_preview_horizon_age()
        context["track_options"] = list(labels.items())
        # The equipment pickers' options, from characters/equipment.py rather
        # than typed into the template a second time (issue #338 item 21).
        context["equipment_catalog_groups"] = CATALOG_GROUPS
        # Each text input's maxlength, the server's own limit (issue #928).
        context["text_limits"] = CHARACTER_TEXT_LIMITS
        # Each number input's bound, the server's own (issue #938).
        context["number_limits"] = CHARACTER_NUMBER_LIMITS
        context["equipment_item_categories"] = ITEM_CATEGORIES
        # When the next aging hit lands, and what it costs -- the workbench
        # says so in as many words ("STR -1, DEX -1 at age 36") rather than
        # leaving the player to work it out from a table (issue #221).
        # The canon's aging never stops escalating (issue #163), so a
        # Python-only preview could show real numbers arbitrarily far out; cut
        # it off at the same horizon the JS uses, so the initial render and the
        # recomputed preview agree on when to switch to the general note.
        next_aging_change = next_aging_change_after_age(
            self.object.age, AGING_PREVIEW_HORIZON_STAGE
        )
        context["next_aging_change_age"] = (
            next_aging_change[0] if next_aging_change else None
        )
        context["next_aging_change_penalties"] = (
            next_aging_change[1] if next_aging_change else {}
        )
        context["past_aging_horizon"] = self.object.age >= aging_preview_horizon_age()
        # Aging change points, keyed by the age they land at, for the live
        # preview: every age at which the cumulative totals move, up to the
        # horizon. Short attribute names, because that is how the card prints
        # them.
        aging_by_age: dict[int, dict[str, int]] = {}
        scan_age = age_for_aging_stage(0)
        while True:
            change = next_aging_change_after_age(scan_age, AGING_PREVIEW_HORIZON_STAGE)
            if change is None:
                break
            change_age, penalties = change
            aging_by_age[change_age] = {
                attr[:3]: value for attr, value in penalties.items()
            }
            scan_age = change_age
        # Everything that crosses the template -> JavaScript boundary for this
        # page, in one dict the template emits with |json_script (issue #63).
        # static/js/character-edit.js reads it by id on load. The page carries
        # no inline JavaScript any more, so Jest can reach this logic, and
        # json_script's escaping -- not hand-quoting inside a <script> -- is
        # what keeps these values inert.
        context["character_edit_config"] = {
            "updateFieldUrl": reverse("characters:update_field", args=[self.object.pk]),
            # Skills / experience
            "addYearsUrl": reverse("characters:add_years", args=[self.object.pk]),
            "allocateUrl": reverse("characters:allocate_skill", args=[self.object.pk]),
            "renameSkillUrl": reverse("characters:rename_skill", args=[self.object.pk]),
            "currentAge": self.object.age,
            "currentYears": self.object.experience_years,
            "defaultRunYears": DEFAULT_RUN_YEARS,
            "maxRunYears": MAX_EXPERIENCE_YEARS_PER_RUN,
            "survivalDice": survival_dice,
            "survivalTargets": survival_targets(),
            "trackLabels": labels,
            "agingByAge": aging_by_age,
            "agingHorizonAge": aging_preview_horizon_age(),
            # The record keeps the latest runs only; so does the page's
            # Experience History when a run joins it (issue #903).
            "maxExperienceLogEntries": MAX_EXPERIENCE_LOG_ENTRIES,
            # Equipment
            "catalogUrl": reverse("characters:equipment_catalog"),
            "equipmentAddUrl": reverse(
                "characters:equipment_add", args=[self.object.pk]
            ),
            "equipmentUpdateUrl": reverse(
                "characters:equipment_update", args=[self.object.pk]
            ),
            "equipmentRemoveUrl": reverse(
                "characters:equipment_remove", args=[self.object.pk]
            ),
            "equipmentBulkRemoveUrl": reverse(
                "characters:equipment_bulk_remove", args=[self.object.pk]
            ),
            "equipmentTogglePackUrl": reverse(
                "characters:equipment_toggle_pack", args=[self.object.pk]
            ),
            "initialEquipment": self.object.equipment or [],
            "packDropped": bool(self.object.pack_dropped),
            # The rows holding the weapon in hand and the shield in use
            # (issues #804, #846); equipment responses carry the same two.
            "weaponInHandIndex": self.object.weapon_in_hand_index,
            "shieldInUseIndex": self.object.shield_in_use_index,
            # DM-only actions. The URLs are always emitted; the handlers bind
            # only when the buttons exist, and those render for DMs alone.
            "markDeadUrl": reverse("characters:mark_dead", args=[self.object.pk]),
            # Each text field's limit, for the inputs the script builds
            # (issue #928): the server's number, not a copy of it.
            "textLimits": CHARACTER_TEXT_LIMITS,
            # And each number input's bound (issue #938).
            "numberLimits": CHARACTER_NUMBER_LIMITS,
            "reviveUrl": reverse("characters:revive", args=[self.object.pk]),
            # The Fatigue roll's own control (issue #636); the handler binds
            # only when the button renders, which is for an editor alone.
            "rollFatigueUrl": reverse("characters:roll_fatigue", args=[self.object.pk]),
            # The literacy check's control (issue #741), bound the same way.
            "rollLiteracyUrl": reverse(
                "characters:roll_literacy", args=[self.object.pk]
            ),
            # The Game Master's ruling control (issue #743), bound the same way.
            "ruleLiteracyUrl": reverse(
                "characters:rule_literacy", args=[self.object.pk]
            ),
            # The import notice's dismiss control (issue #744), bound only when
            # the notice renders.
            "dismissImportNoticeUrl": reverse(
                "characters:dismiss_import_notice", args=[self.object.pk]
            ),
            # Current Fatigue and Body (issue #751): the inputs render for a
            # Game Master alone, and the handler binds only when they exist.
            "setPoolsUrl": reverse("characters:set_pools", args=[self.object.pk]),
            # Visibility (issue #273). canEdit mirrors the template's can_edit:
            # the equipment renderer draws static rows instead of inputs for a
            # read-only viewer. The URL is always emitted like the DM pair
            # above; the toggle handler binds only when the control exists.
            "canEdit": context["can_edit"],
            # Item modifiers are a Game Master's grant (issue #752): a player's
            # rows show them as text, with who set them.
            "canSetItemModifiers": context["can_set_item_modifiers"],
            # A Game Master's allocate controls stay at no free points once
            # the endpoint grants past them (#770, the sheetrules batch).
            "canGrantSkillPoints": context["can_grant_skill_points"],
            # Why a carried shield does not count beside a two-handed weapon
            # (review of #804); equipment responses restate it.
            "shieldIdleReason": self.object.shield_idle_reason,
            "setVisibilityUrl": reverse(
                "characters:set_visibility", args=[self.object.pk]
            ),
        }
        context.update(self._attack_panel_context())
        return context

    def _attack_panel_context(self) -> dict:
        """Everything the Attack Resolution panel renders (issue #101).

        The three selects are **server-rendered options**, not lists handed to
        JavaScript: the weapon option's value is the character's equipment
        index, so it can only ever mean what the server thinks it means, and
        the shield's bonus and the armour tiers come from the catalog and
        ``combat.ARMOUR_TIERS`` rather than from anything the page could
        retype. attack-panel.js reads only a URL and a token.

        The weapon list comes from :func:`attack_weapon_choices`, which the
        equipment endpoints also return, so adding or removing a weapon
        refreshes the select without a reload (issue #339, item 24).
        """
        attack_weapons, gm_adjudicated = attack_weapon_choices(self.object)
        return {
            "attack_panel_config": {
                "attackUrl": reverse("characters:attack", args=[self.object.pk]),
                # The live log keeps as many lines as the record (#792).
                "maxCombatLogEntries": MAX_COMBAT_LOG_ENTRIES,
            },
            "attack_weapons": attack_weapons,
            "attack_gm_adjudicated_weapons": gm_adjudicated,
            "attack_armour_tiers": ARMOUR_TIERS,
            "attack_shields": [
                {"item_id": item_id, "name": data.get("name", item_id)}
                for item_id, data in SHIELDS.items()
            ],
            # Newest first: stored oldest-first like history, reversed for display.
            "combat_log_entries": list(reversed(self.object.combat_log or [])),
        }


@login_required
@require_POST
@rate_limit("delete_character", max_requests=20, window_seconds=60)
def delete_character(request, pk):
    """Soft delete a character (can be restored by admin/DM)."""
    # Lock the row for the read-modify-write so a concurrent equipment/skill
    # write can't be clobbered by this save (mirrors mark-dead/revive).
    with transaction.atomic():
        character = locked_character_or_404(pk)
        denied = edit_denied(request, character)
        if denied is not None:
            return denied

        character.is_deleted = True
        character.deleted_at = timezone.now()
        character.save()

        # Audit log when admin/DM deletes another user's character
        _log_character_event(
            request, character, "CHARACTER_DELETE", action="soft-deleted"
        )

    return redirect("characters:list")


def _looks_like_pk(candidate: object) -> bool:
    """Whether ``candidate`` names a primary key this view can look up.

    An ``int`` that is not a ``bool`` (``True`` is an ``int`` in Python and
    would silently mean pk 1), or a string of ASCII digits, and either way
    one SQLite can hold: ``isdigit`` let "²" through to ``int``, and a longer
    number raised out of the ``pk__in`` query (issue #767). See
    ``common.helpers.database_integer``.

    Args:
        candidate: One member of a request's ``ids`` list.
    """
    try:
        database_integer(candidate)
    except ValueError:
        return False
    return True


@login_required
@require_POST
@rate_limit("bulk_delete", max_requests=5, window_seconds=60)
def bulk_delete_characters(request):
    """Soft delete multiple characters at once."""
    data, err = _parse_json_body(request)
    if err:
        return err
    ids = data.get("ids", [])

    if not ids or not isinstance(ids, list):
        return _json_error("No character IDs provided")

    # The members were never checked, only the list itself, so a hand-built
    # body of {"ids": ["not-a-number"]} reached the queryset and raised
    # ValueError out of the view -- a 500 any logged-in user could ask for
    # (issue #641). Anything that will not coerce to a primary key cannot
    # name a row, so it is dropped; a body that names no row at all is the
    # same refusal an empty list gets.
    ids = [int(candidate) for candidate in ids if _looks_like_pk(candidate)]
    if not ids:
        return _json_error("No character IDs provided")

    # DM/admin can bulk-delete any user's characters, matching the ownership
    # check single delete_character uses (_can_edit_object); regular users
    # are restricted to their own, same as before.
    characters = Character.objects.filter(pk__in=ids, is_deleted=False)
    if not request.user.can_manage_users:
        characters = characters.filter(user=request.user)

    # Capture the cross-user rows before update() flips is_deleted, so the
    # audit trail has the same per-character detail as single delete_character
    # (issue #162 -- single delete already logs DM deletes of other users'
    # characters; bulk delete didn't).
    cross_user_characters = list(
        characters.exclude(user=request.user).values("pk", "name", "user__username")
    )

    deleted_count = characters.count()
    characters.update(is_deleted=True, deleted_at=timezone.now())

    for row in cross_user_characters:
        # The rows were read before update() flipped is_deleted, so this logs
        # names and owners rather than Character objects.
        _emit_character_audit(
            "CHARACTER_DELETE",
            actor=request.user.username,
            character_name=row["name"],
            character_pk=row["pk"],
            owner=row["user__username"],
            action="soft-deleted",
        )

    return JsonResponse({"success": True, "deleted": deleted_count})


@login_required
@require_POST
@rate_limit("restore_character", max_requests=20, window_seconds=60)
def restore_character(request, pk):
    """Restore a soft-deleted character: its owner, or a DM/admin (any).

    A player sees their own deleted characters under "Show deleted", so they
    may bring them back too (issue #339, item 15); restore is the same set of
    people as delete.
    """
    # Lock the row for the read-modify-write (mirrors delete_character).
    with transaction.atomic():
        character = locked_character_or_404(pk, is_deleted=True)
        if not (request.user.can_manage_users or character.user_id == request.user.pk):
            return _permission_denied()
        character.is_deleted = False
        character.deleted_at = None
        character.save()

        _log_character_event(
            request,
            character,
            "CHARACTER_RESTORE",
            action="restored",
            only_when_not_owner=False,
        )

    return JsonResponse({"success": True, "character_id": character.pk})


@login_required
@require_POST
@rate_limit("set_visibility", max_requests=20, window_seconds=60)
def character_set_visibility(request, pk):
    """Owner/DM-only: flip a character between private and public (#273).

    The body carries ``{"is_public": true|false}``. Visibility is a write to
    the character, so it goes through the edit check (_can_edit_object) like
    every other mutation -- a public character does not let strangers make it
    private again, or vice versa.
    """
    data, err = _parse_json_body(request)
    if err:
        return err

    is_public = data.get("is_public")
    if not isinstance(is_public, bool):
        return _json_error("is_public must be true or false")

    # Lock the row for the read-modify-write, matching mark_dead/revive.
    with transaction.atomic():
        character = locked_character_or_404(pk)
        denied = edit_denied(request, character)
        if denied is not None:
            return denied

        character.is_public = is_public
        character.save()

    _log_character_event(
        request,
        character,
        "CHARACTER_SET_VISIBILITY",
        detail="public" if is_public else "private",
    )
    return JsonResponse({"success": True, "is_public": character.is_public})


@login_required
@require_POST
@rate_limit("roll_fatigue", max_requests=20, window_seconds=60)
def character_roll_fatigue(request, pk):
    """Roll the character's 2d6 Fatigue roll from the editor (issue #636).

    The Fatigue roll was the one field in ``CHARACTER_FIELDS`` with no widget
    anywhere on the sheet: a character created in a Games Master session is
    saved without it, the editor said so, and offered no way to record it. The
    six attributes each have an editable cell; this is the roll's.

    Thin over ``services.roll_fatigue`` — the same service and the same dice
    the Games Master path uses, so a roll made here and a roll made in a
    session are the same event in the character's history. A player gets one
    roll (issue #590); a Game Master may ask for another.
    """
    is_game_master = request.user.can_manage_users

    def run() -> HttpResponse:
        result = services.roll_fatigue(
            pk,
            actor=request.user,
            authorize=may_edit(request),
            once_for_players=True,
            is_game_master=is_game_master,
        )
        character = result.character
        _log_character_event(request, character, "CHARACTER_ROLL_FATIGUE", result.note)
        return JsonResponse(
            {
                "success": True,
                "note": result.note,
                "fatigue_roll": character.fatigue_roll,
                "fatigue": character.fatigue,
                "body": character.body,
                "injury_thresholds_html": render_injury_thresholds(
                    character, is_game_master=is_game_master
                ),
                # A player has had their one roll; the control says so rather
                # than offering a second the server would refuse.
                "can_roll_fatigue": is_game_master,
                # The roll can complete the character (issue #764).
                **sheet_repaint(character, request.user),
            }
        )

    return answer_change(run, json_not_found=False)


@login_required
@require_POST
@rate_limit("dismiss_import_notice", max_requests=20, window_seconds=60)
def character_dismiss_import_notice(request, pk):
    """Clear the notice a player's import left on the sheet (issue #744).

    The notice stays until somebody who may edit the character dismisses it:
    no information on this site goes away on a timer. Thin over
    ``services.dismiss_import_notice``.
    """

    def run() -> HttpResponse:
        result = services.dismiss_import_notice(
            pk, actor=request.user, authorize=may_edit(request)
        )
        # The dismissal is a history line; Recent Changes shows it without a
        # reload (#792 sweep).
        return JsonResponse(
            {"success": True, **sheet_repaint(result.character, request.user)}
        )

    return answer_change(run, json_not_found=False)


@login_required
@require_POST
@rate_limit("rule_literacy", max_requests=20, window_seconds=60)
def character_rule_literacy(request, pk):
    """Record a Game Master's literacy ruling from the editor (issue #743).

    The body carries ``{"literate": true|false|null}``; ``null`` withdraws a
    ruling. Thin over ``services.rule_literacy``, which refuses a player and
    an origin with a check to roll, so this is the same rule the Games
    Master's ``rule_literacy`` action is held to. The answer carries the
    literacy row's values (:data:`LITERACY_RESPONSE_KEYS`).
    """
    data, err = _parse_json_body(request)
    if err:
        return err
    literate = data.get("literate")
    if literate is not None and not isinstance(literate, bool):
        return _json_error("literate must be true, false or null")
    is_game_master = request.user.can_manage_users

    def run() -> HttpResponse:
        result = services.rule_literacy(
            pk,
            literate,
            actor=request.user,
            authorize=may_edit(request),
            is_game_master=is_game_master,
        )
        character = result.character
        _log_character_event(request, character, "CHARACTER_RULE_LITERACY", result.note)
        return JsonResponse(
            {
                "success": True,
                "note": result.note,
                **literacy_response(character, is_game_master=is_game_master),
                **sheet_repaint(character, request.user),
            }
        )

    return answer_change(run, json_not_found=False)


@login_required
@require_POST
@rate_limit("roll_literacy", max_requests=20, window_seconds=60)
def character_roll_literacy(request, pk):
    """Roll the character's 3d6 literacy check from the editor (issue #741).

    The Location section printed the check and offered no way to make it, and
    nothing recorded whether the character could read. Thin over
    ``services.roll_literacy``, as the Fatigue roll is over its service: a
    player rolls once, a Game Master may roll again, and the answer carries
    the literacy row's values (:data:`LITERACY_RESPONSE_KEYS`) so the page
    repaints the row the same way an Origin change does.
    """
    is_game_master = request.user.can_manage_users

    def run() -> HttpResponse:
        result = services.roll_literacy(
            pk,
            actor=request.user,
            authorize=may_edit(request),
            is_game_master=is_game_master,
        )
        character = result.character
        _log_character_event(request, character, "CHARACTER_ROLL_LITERACY", result.note)
        return JsonResponse(
            {
                "success": True,
                "note": result.note,
                **literacy_response(character, is_game_master=is_game_master),
                **sheet_repaint(character, request.user),
            }
        )

    return answer_change(run, json_not_found=False)


@login_required
@require_POST
@rate_limit("roll_sheet", max_requests=20, window_seconds=60)
def character_roll(request, pk, kind: str) -> HttpResponse:
    """Make one of the sheet's rolls: attributes, appearance, a blank height
    or weight (#941), provenance, wealth, or a Game Master's coin reroll
    (issues #748, #749).

    Thin over ``services.roll_on_sheet``, as the Fatigue and literacy rolls
    are over theirs: the server rolls, a player rolls each once, a Game
    Master may roll again, and the dice go into the dice log. The page
    reloads from the saved character afterwards, since a roll can move many
    cells at once (six attributes and everything derived from them).
    """
    is_game_master = request.user.can_manage_users

    def run() -> HttpResponse:
        result = services.roll_on_sheet(
            pk,
            kind,
            actor=request.user,
            authorize=may_edit(request),
            is_game_master=is_game_master,
        )
        _log_character_event(
            request, result.character, "CHARACTER_ROLL", detail=result.note
        )
        return JsonResponse(
            {
                "success": True,
                "note": result.note,
                "can_roll": services.sheet_rolls_for(
                    result.character, is_game_master=is_game_master
                )[kind].available,
            }
        )

    return answer_change(run, json_not_found=False)


@login_required
@require_POST
@rate_limit("mark_dead", max_requests=20, window_seconds=60)
def character_mark_dead(request, pk):
    """DM-only: mark a character dead outside the experience-term flow."""
    if not request.user.can_manage_users:
        return _permission_denied()

    data, err = _parse_json_body(request)
    if err:
        return err

    cause = data.get("cause") or ""
    # A list has no strip(); it raised AttributeError, a 500 (#767).
    if not isinstance(cause, str):
        return _json_error("Cause of death is required")
    cause = cause.strip()
    if not cause:
        return _json_error("Cause of death is required")
    refusal = too_long("Cause", cause, MAX_DEATH_CAUSE_LENGTH)
    if refusal:
        return _json_error(refusal)

    def mark_dead(character: Character) -> None:
        services.apply_death(character, cause)

    def run() -> HttpResponse:
        with services.changing(
            pk,
            actor=request.user,
            mark_played=False,
            dead=services.DeadCharacterWrite.DEATH_AND_REVIVAL,
        ) as change:
            mark_dead(change.character)
            change.note("is_alive", True, False)
        _log_character_event(
            request, change.character, "CHARACTER_MARK_DEAD", detail=cause
        )
        return JsonResponse(
            {
                "success": True,
                "is_alive": False,
                "death_cause": change.character.death_cause,
            }
        )

    return answer_change(run, json_not_found=False)


@login_required
@require_POST
@rate_limit("revive_character", max_requests=20, window_seconds=60)
def character_revive(request, pk):
    """DM-only: undo a mark-dead. Clears the recorded cause of death."""
    if not request.user.can_manage_users:
        return _permission_denied()

    def run() -> HttpResponse:
        with services.changing(
            pk,
            actor=request.user,
            mark_played=False,
            dead=services.DeadCharacterWrite.DEATH_AND_REVIVAL,
        ) as change:
            if change.character.is_alive:
                raise CharacterChangeError("Character is already alive")
            services.apply_revive(change.character)
            change.note("is_alive", False, True)
        _log_character_event(request, change.character, "CHARACTER_REVIVE")
        return JsonResponse({"success": True, "is_alive": True})

    return answer_change(run, json_not_found=False)


def _pool_value(data: Mapping, key: str) -> int | None:
    """One pool from a set-pools body: absent or blank is ``None``.

    Raises:
        CharacterChangeError: A value that is not a whole number.
    """
    raw = data.get(key)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        raise CharacterChangeError(
            f"{key.capitalize()} must be a whole number"
        ) from None


@login_required
@require_POST
@rate_limit("set_pools", max_requests=60, window_seconds=60)
def character_set_pools(request, pk):
    """Set current Fatigue, Body, Mana and/or the mana owed from the sheet.

    Issue #751; the mana owed since tarmar-engine #28.

    A Game Master only (``services.apply_current_pools`` refuses anyone
    else): a fight on the board writes a character's pools as it goes, and
    healing, rest and a blow struck off the board are the Game Master's to
    rule. Answers with the re-rendered injury readout, which carries the
    current pools, so the page repaints it the way an attribute change does.
    """
    data, err = _parse_json_body(request)
    if err:
        return err
    is_game_master = request.user.can_manage_users

    def run() -> HttpResponse:
        result = services.set_current_pools(
            pk,
            fatigue=_pool_value(data, "fatigue"),
            body=_pool_value(data, "body"),
            mana=_pool_value(data, "mana"),
            owed=_pool_value(data, "owed"),
            actor=request.user,
            authorize=may_edit(request),
            is_game_master=is_game_master,
        )
        character = result.character
        _log_character_event(request, character, "CHARACTER_SET_POOLS", result.note)
        return JsonResponse(
            {
                "success": True,
                "note": result.note,
                "injury_thresholds_html": render_injury_thresholds(
                    character, is_game_master=is_game_master
                ),
                # The Magic section prints the Mana in hand too; it repaints
                # from this rather than waiting for a reload.
                "current_mana": current_mana(character),
                **sheet_repaint(character, request.user),
            }
        )

    return answer_change(run, json_not_found=False)


class DeletedCharactersView(
    BreadcrumbMixin,
    ActivePageMixin,
    LoginRequiredMixin,
    AdminOrDMRequiredMixin,
    ListView,
):
    def get_breadcrumb(self, context):
        return trail(
            step("Characters", reverse("characters:list")),
            step("Deleted"),
        )

    """List all soft-deleted characters (DM/admin only)."""

    model = Character
    template_name = "characters/deleted_characters.html"
    context_object_name = "characters"
    login_url = "login"
    # A page about characters, reached from the character list (issue #339,
    # item 16); it used to claim the Users entry in the menu.
    active_page = "characters"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        # Most recently deleted first, the pk breaking ties, so a row stays on
        # one page from request to request (issue #950). A row deleted before
        # deleted_at was recorded has none and goes last.
        return (
            Character.objects.filter(is_deleted=True)
            .select_related("user")
            .order_by(F("deleted_at").desc(nulls_last=True), "-pk")
        )


#: Whether the allocate endpoint grants a Game Master a point past the free
#: ones (#770). The sheetrules batch makes the endpoint do so and sets this
#: True, so the sheet offers him the grant the server now takes.
GAME_MASTER_GRANTS_SKILL_POINTS = True


def can_grant_skill_points(*, is_game_master: bool) -> bool:
    """Whether this viewer's sheet offers the allocate controls at no points."""
    return is_game_master and GAME_MASTER_GRANTS_SKILL_POINTS


def render_skills_list(character: Character, *, is_game_master: bool = False) -> str:
    """The sheet's skills list, rendered by the partial the page uses.

    A response that moves skills sends this, so the browser inserts the rows
    a reload would show rather than rebuilding its own (issues #338, #754).
    A Game Master's rows keep the allocate control at no free points, since
    he may grant one past them (#770).

    Args:
        character: The character.
        is_game_master: Whether the viewer is a Game Master, who keeps the
            controls a player at 0 free points does not get (#770).
    """
    return render_to_string(
        "characters/_skills_list.html",
        {
            "skills_display": skill_rows(character.skills),
            "character": character,
            "can_edit": True,
            "can_grant_skill_points": can_grant_skill_points(
                is_game_master=is_game_master
            ),
            "weapon_choices": weapon_choices(),
        },
    )


def render_injury_thresholds(character, *, is_game_master: bool) -> str:
    """Render the Injury Thresholds readout as the page renders it.

    The same partial the sheet includes, so a live update and a reload say
    the same thing. The readout is a table of numbers for a complete
    character and a one-line "not known yet" for an incomplete one (issues
    #571, #639), and a character crosses that line without a reload --
    recording the last attribute, or rolling Fatigue -- so the page swaps the
    whole block rather than writing cells that may not exist.

    The readout opens with where the character's pools stand now (issue
    #751), which a Game Master may set, so it is rendered for its viewer.

    Args:
        character: The saved character.
        is_game_master: Whether the viewer may set the current pools.

    Returns:
        The rendered HTML fragment.
    """
    return render_to_string(
        "characters/_injury_thresholds.html",
        {
            "thresholds": character.injury_thresholds,
            "condition": character.condition,
            "character": character,
            "can_set_pools": is_game_master,
            # Every caller answers an edit this viewer was allowed to make;
            # the survival save's control (#839) renders for such a viewer.
            "can_edit": True,
            "survival_save_refusal": services.survival_save_refusal(
                character, is_game_master=is_game_master
            ),
        },
    )


#: How many of the latest changes the sheet's Recent Changes table shows.
RECENT_HISTORY_ROWS = 10


def recent_history_entries(character: Character) -> list[HistoryEntry]:
    """The latest changes, newest first. Stored chronologically; reversed here."""
    history = character.history or []
    return [HistoryEntry.from_stored(entry) for entry in reversed(history)][
        :RECENT_HISTORY_ROWS
    ]


def movement_response(character: Character) -> dict:
    """The encumbrance bar, the Movement row and the combat tiles, as keys.

    Every value here derives from the equipment and from the effective STR,
    DEX and CON, so an equipment change, an attribute edit and a run of years
    all send it, and the page paints it with one painter (issues #163, #634,
    #763). ``effective_strength`` is the carrying capacity the bar prints
    beside the load.
    """
    return {
        "carried_weight": character.carried_weight,
        "pack_weight": character.pack_weight,
        "effective_strength": character.effective_strength,
        "encumbrance_level": character.encumbrance_level,
        "movement_penalty": character.movement_penalty,
        "movement_restriction_label": character.movement_restriction_label,
        "movement_modifier": character.movement_modifier,
        "gait_readout": character.gait_readout,
        "armor_stops": character.armor_stops,
        "armor_dex_penalty": character.armor_dex_penalty,
        # Both ride on combat_dexterity, which armour's DEX penalty moves, so
        # equipping a suit or picking up a shield restates them. They used to
        # be rendered once by the template and never again, leaving the two
        # tiles contradicting the Armor DEX Penalty tile beside them and the
        # Attack Resolution panel below them (issue #634).
        "dex_modifier": character.dex_modifier,
        "dodge_modifier": character.dodge_modifier,
        "weapon_to_hit_mod": character.weapon_to_hit_mod,
        "weapon_damage_mod": character.weapon_damage_mod,
        "weapon_critical_mod": character.weapon_critical_mod,
    }


def skill_name_suggestions(character: Character) -> list[str]:
    """The add-skill input's type-ahead, for the page and for a repaint.

    Every real skill any track's d20 table can grant, plus this character's
    own skills (which can include a freely-typed name off no track at all).
    A native <datalist> reads this list but never restricts the input to it,
    so an unlisted skill stays typeable (issue #224). A skill allocated in
    place joins it without a reload (issue #903).
    """
    return sorted(set(granted_skill_names()) | set((character.skills or {}).keys()))


def sheet_repaint(character: Character, viewer: User | AnonymousUser) -> dict:
    """The parts of the sheet any change can move, for the page to repaint.

    Every response to a change made without a reload carries these, and
    ``paintSheet`` in character-edit.js paints them: what the character
    still needs and whether it can be exported (issue #764), the free points
    and the controls that spend them (#792), the skill check's options
    (#765), and the Recent Changes rows and the dice log (#792). They were
    rendered once, at load, and nothing wrote them again. Issue #903 swept
    the same class again: whether the character lives, which the alive-only
    and dead-only controls are hidden by; the saved name, which every place
    the sheet prints it is painted from; the "Play with the Game Master"
    slot, which completing, killing or renaming the character changes; and
    the add-skill type-ahead, which a skill allocated in place joins.

    Args:
        character: The saved character.
        viewer: Who made the change; the "Play with the Game Master" slot
            offers that viewer's own sessions.

    Returns:
        The keys ``paintSheet`` reads.
    """
    return {
        "name": character.name,
        "is_complete": character.is_complete,
        # Every change answered here was one its viewer could make, so the
        # partial renders for an editor (the gear step's link, #844 review).
        "completion_html": render_to_string(
            "characters/_completion.html", {"character": character, "can_edit": True}
        ),
        "incomplete_marks_html": render_to_string(
            "characters/_incomplete_marks.html", {"character": character}
        ),
        "is_alive": character.is_alive,
        # The slot renders empty where the attach would be refused (dead or
        # incomplete), and names the character in its label (#903).
        "play_with_gm_html": render_to_string(
            "gm/_play_with_gm.html", play_with_game_master(character, viewer)
        ),
        "skill_name_suggestions_html": render_to_string(
            "characters/_skill_name_suggestions.html",
            {"skill_name_suggestions": skill_name_suggestions(character)},
        ),
        "free_skill_points": character.free_skill_points,
        "free_spell_points": character.free_spell_points,
        # The Magic section's Mana in hand rides on the spell points, which a
        # run of years can grant (#792 sweep).
        "current_mana": current_mana(character),
        "skill_check_options_html": render_to_string(
            "characters/_skill_check_options.html",
            {"skill_check_skills": skill_check_skills(character)},
        ),
        "recent_changes_html": render_to_string(
            "characters/_recent_changes.html",
            {"recent_history": recent_history_entries(character)},
        ),
        "dice_log": character.dice_log or "",
    }


def _build_field_update_response(
    character: Character, field: str, *, viewer: User | AnonymousUser
) -> JsonResponse:
    """Return the derived values that change when `field` is edited.

    Args:
        character: The saved character.
        field: The field the edit saved.
        viewer: Who saved it; a Game Master's readouts offer his controls.
    """
    is_game_master = isinstance(viewer, User) and viewer.can_manage_users
    response_data: dict[str, bool | int | str | dict | None] = {"success": True}
    if field in {
        "constitution",
        "wisdom",
        "intelligence",
        "dexterity",
        "strength",
        "fatigue_roll",
    }:
        response_data["fatigue"] = character.fatigue
        response_data["body"] = character.body
        # Injury breakpoints derive from the pool maxima (and CON), so they move
        # whenever these fields do -- keep the sheet's readout in step.
        response_data["injury_thresholds_html"] = render_injury_thresholds(
            character, is_game_master=is_game_master
        )
    if field == "name":
        # The survival save's confirm names the character, in an attribute
        # no text painter reaches; the readout is re-rendered from the saved
        # name instead (issue #903).
        response_data["injury_thresholds_html"] = render_injury_thresholds(
            character, is_game_master=is_game_master
        )
    if field in {"strength", "dexterity", "constitution"}:
        response_data["physical"] = character.physical
        # Movement reads effective CON, STR and DEX; encumbrance and the
        # carrying capacity read effective STR. The row and the bar kept the
        # old numbers until a reload (issue #763).
        response_data.update(movement_response(character))
    if field == "dexterity":
        # The two combat tiles derive from DEX; typing a new one used to
        # leave them showing the old character's aim and dodge (issue #634).
        response_data["dex_modifier"] = character.dex_modifier
        response_data["dodge_modifier"] = character.dodge_modifier
    if field in {"wisdom", "intelligence", "charisma"}:
        response_data["mental"] = character.mental
    if field in {"location", "intelligence"}:
        # The literacy check is read from the origin, and the stored roll is
        # read against INT less the origin's penalty; the row was rendered
        # once and kept the old origin's check until a reload (issue #740).
        response_data.update(
            literacy_response(character, is_game_master=is_game_master)
        )
    if field in {"location", "total_xp"}:
        # The Origin's skill moves with it (#754), and an XP change can grant
        # points (#769, the sheetrules batch): the list and the allocated
        # total showed the old ones until a reload.
        response_data["skills_html"] = render_skills_list(
            character, is_game_master=is_game_master
        )
        response_data["total_allocated_points"] = character.total_allocated_skill_points
    if field == "location":
        response_data["location_recorded"] = character.location_recorded
        # A Game Master's ruling on "Consult GM" (#801) is withdrawn when the
        # Origin moves to a row of the table; the cell repaints from this.
        if character_has_field("origin_ruling"):
            response_data["origin_ruling"] = character.origin_ruling
    if field == "height_roll":
        response_data["height_display"] = character.height_display
    if field in {"weight_roll", "strength"}:
        response_data["weight_lbs"] = character.weight_lbs
    if field == "wealth_level":
        response_data["starting_coin"] = character.starting_coin
    if field in {"wealth_level", "starting_coin"}:
        # The purse and the Wealth table follow the coin (review of #757).
        response_data["purse"] = format_purse(character)
        response_data["coin_adjustment"] = format_coin(character.coin_adjustment)
    if field == "total_xp":
        response_data["level"] = character.level
        # The points the XP earned (#769), for the editor to repaint.
        response_data["free_skill_points"] = character.free_skill_points
    # The sheet's roll controls can change with any field (the last score
    # recorded turns "roll the missing ones" into a reroll to confirm, or
    # closes a buyer's roll): the page repaints them from this, not from what
    # it loaded with (#748 review).
    response_data["sheet_rolls"] = {
        kind: dataclasses.asdict(offer)
        for kind, offer in services.sheet_rolls_for(
            character, is_game_master=is_game_master
        ).items()
    }
    # A player's bought score locks once recorded (#748): the page turns the
    # cell to text with this reason, as a reload would render it.
    if not services.can_set_field(character, field, is_game_master=is_game_master):
        response_data["locked_reason"] = services.field_refusal(field)
    # The last score recorded completes the character and closes the point
    # buy; any edit adds a line to Recent Changes (issues #764, #792).
    response_data.update(sheet_repaint(character, viewer))
    response_data["point_buy_open"] = not is_game_master and not (
        services.attributes_known(character)
    )
    return JsonResponse(response_data)


@login_required
@require_POST
@rate_limit("character_update", max_requests=60, window_seconds=60)
def character_update_field(request, pk):
    """Update a single character field via AJAX."""
    data, err = _parse_json_body(request)
    if err:
        return err
    field = data.get("field")
    value = data.get("value")

    # A list is unhashable, so ``in`` a set raised TypeError (#767).
    if not isinstance(field, str) or field not in CHARACTER_FIELDS:
        return _json_error("Invalid field")

    try:
        # A player's height and weight are one face (#748): that rule is the
        # one the player is held to, so it speaks before the die grammar
        # every author shares (#805). The service checks it again.
        services.check_player_choice(
            field, value, is_game_master=request.user.can_manage_users
        )
    except CharacterChangeError as refusal:
        return _json_error(str(refusal))
    try:
        value = Character.clean_field_value(field, value)
    except ValidationError as validation_error:
        return _json_error(validation_error.messages[0])

    # Names need not be unique (issue #571): a rename is just a field change.
    def run() -> HttpResponse:
        result = services.update_field(
            pk,
            field,
            value,
            actor=request.user,
            authorize=may_edit(request),
            is_game_master=request.user.can_manage_users,
        )
        # Audit log when admin/DM edits another user's character
        _log_character_event(
            request,
            result.character,
            "CHARACTER_EDIT",
            action=f"changed field={field} on",
            # A Game Master's Origin change can reroll literacy (#741); the
            # audit line says so the way the roll endpoint's line does.
            detail=result.note if field == "location" else "",
        )
        return _build_field_update_response(
            result.character, field, viewer=request.user
        )

    return answer_change(run, json_not_found=False)
