"""Sheet endpoints for the rules the sheet used to record and never act on.

Each is thin: parse the request, make the change through
``characters.services.changing`` (one locked row, one history line), and
answer. The rules live in the modules they call:

* the Weapon skill's weapon — :func:`characters.weapon_skill.record_weapon_skill`
  (issue #753);
* the skill check — :func:`characters.skill_checks.skill_check` (issue #756);
* coin — :func:`characters.purse.move_coin_for_reason` (issues #757, #928);
* spells and mana — :func:`characters.magic.learn_spell` and
  :func:`characters.magic.grant_mana` (issues #774, #755);
* the school of magic chosen after its roll —
  :func:`characters.magic.choose_school` (issue #18);
* a spell cast outside a battle — :func:`characters.magic.cast_in_story`
  (issue #18).

Every one is a Game Master override where the rule allows one, decided
server-side from ``can_manage_users``; nothing the browser sends says who
asked.
"""

from collections.abc import Callable
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from typing import cast

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_POST

from accounts.models import User
from common.decorators import internal_errors_logged, rate_limit
from common.helpers import _parse_json_body, whole_number

from .. import services
from ..magic import cast_in_story, choose_school, grant_mana, learn_spell
from ..models import Character
from ..purse import (
    MAX_COIN_MOVEMENT,
    PurseError,
    format_purse,
    move_coin_for_reason,
)
from ..refusal_words import game_master_call_note
from ..skill_checks import skill_check
from ..skills import normalize_skill_name, skill_name_refusal
from ..weapon_skill import record_weapon_skill
from .access import answer_change, may_edit
from .characters import render_injury_thresholds, sheet_repaint


def _optional_int(data: dict, key: str) -> int | None:
    """An optional integer field; absent or blank is None, junk is refused."""
    raw = data.get(key)
    if raw is None or raw == "":
        return None
    try:
        return whole_number(raw)
    except ValueError:
        raise services.CharacterChangeError(f"Invalid {key}") from None


def _required_text(data: dict, key: str) -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise services.CharacterChangeError(f"{key} is required")
    return value


def _change(
    request: HttpRequest,
    pk: int,
    field_name: str,
    apply: Callable[[Character, dict], str],
    answer: Callable[[Character, str], dict] | None = None,
) -> HttpResponse:
    """Make one change on the locked character and answer it as JSON."""
    data, error = _parse_json_body(request)
    if error:
        return error

    def run() -> HttpResponse:
        # login_required has turned away anyone who is not a User.
        actor = cast(User, request.user)
        with services.changing(pk, actor=actor, authorize=may_edit(request)) as change:
            try:
                note = apply(change.character, data)
            except PurseError as refusal:
                raise services.CharacterChangeError(str(refusal)) from refusal
            change.note(field_name, "", note)
        # A check leaves the page where it is; its history line joins the
        # Recent Changes table from this (issue #792).
        body = {
            "success": True,
            "note": note,
            **sheet_repaint(change.character, actor),
        }
        if answer is not None:
            body.update(answer(change.character, note))
        return JsonResponse(body)

    return answer_change(run)


@login_required
@require_POST
@rate_limit("weapon_skill", max_requests=30, window_seconds=60)
@internal_errors_logged(json_only=True)
def character_record_weapon_skill(request: HttpRequest, pk: int) -> HttpResponse:
    """Tie a Weapon skill to the weapon it was taken for (issue #753)."""
    is_game_master = request.user.can_manage_users

    def apply(character: Character, data: dict) -> str:
        return record_weapon_skill(
            character,
            _required_text(data, "skill"),
            _required_text(data, "item_id"),
            is_game_master=is_game_master,
        )

    return _change(request, pk, "skills", apply)


#: The skill check answer's key for what ends its line when a Game Master
#: called it past the rules (``static/js/mechanics.js`` reads it).
GAME_MASTER_CALL_NOTE = "game_master_call_note"


@login_required
@require_POST
@rate_limit("skill_check", max_requests=60, window_seconds=60)
@internal_errors_logged(json_only=True)
def character_skill_check(request: HttpRequest, pk: int) -> HttpResponse:
    """Roll one skill check and keep it in the history (issue #756)."""
    is_game_master = request.user.can_manage_users
    checks = []

    def apply(character: Character, data: dict) -> str:
        received_name = _required_text(data, "skill")
        refusal = skill_name_refusal(received_name)
        if refusal:
            raise services.CharacterChangeError(refusal)
        check = skill_check(
            character,
            normalize_skill_name(received_name),
            difficulty=_optional_int(data, "difficulty"),
            attribute=str(data.get("attribute") or "") or None,
            stated_total=_optional_int(data, "total"),
            is_game_master=is_game_master,
        )
        checks.append(check)
        return check.line

    return _change(
        request,
        pk,
        "skill_check",
        apply,
        # The page writes the check's line itself, from the server's numbers;
        # what a Game Master called it past comes in the configured words, so
        # the script holds no copy of them (issue #85).
        answer=lambda character, note: {
            "check": {
                **asdict(checks[0]),
                GAME_MASTER_CALL_NOTE: game_master_call_note(
                    checks[0].game_master_call
                ),
            }
        },
    )


@login_required
@require_POST
@rate_limit("move_coin", max_requests=30, window_seconds=60)
@internal_errors_logged(json_only=True)
def character_move_coin(request: HttpRequest, pk: int) -> HttpResponse:
    """A Game Master gives or takes coin (issue #757)."""
    is_game_master = request.user.can_manage_users

    def apply(character: Character, data: dict) -> str:
        if not is_game_master:
            raise services.CharacterChangeError(
                "Only a Game Master can give or take coin; buy gear from the catalog"
            )
        try:
            amount = Decimal(str(data.get("amount", "")))
        except InvalidOperation:
            raise services.CharacterChangeError("Invalid amount") from None
        if not amount.is_finite() or amount == 0 or abs(amount) > MAX_COIN_MOVEMENT:
            raise services.CharacterChangeError(
                f"Move between -{MAX_COIN_MOVEMENT} and {MAX_COIN_MOVEMENT} gp, not 0"
            )
        reason = str(data.get("reason") or "").strip() or "Game Master"
        return move_coin_for_reason(character, amount, reason=reason)

    return _change(
        request,
        pk,
        "purse",
        apply,
        answer=lambda character, note: {"purse": format_purse(character)},
    )


@login_required
@require_POST
@rate_limit("learn_spell", max_requests=30, window_seconds=60)
@internal_errors_logged(json_only=True)
def character_learn_spell(request: HttpRequest, pk: int) -> HttpResponse:
    """Put skill points into a spell (issue #774)."""
    is_game_master = request.user.can_manage_users

    def apply(character: Character, data: dict) -> str:
        points = _optional_int(data, "points")
        return learn_spell(
            character,
            _required_text(data, "spell"),
            1 if points is None else points,
            is_game_master=is_game_master,
        )

    return _change(request, pk, "spells", apply)


@login_required
@require_POST
@rate_limit("choose_school", max_requests=30, window_seconds=60)
@internal_errors_logged(json_only=True)
def character_choose_school(request: HttpRequest, pk: int) -> HttpResponse:
    """Choose the school of magic the school roll opened (issue #18)."""
    is_game_master = request.user.can_manage_users

    def apply(character: Character, data: dict) -> str:
        return choose_school(
            character, _required_text(data, "school"), is_game_master=is_game_master
        )

    return _change(request, pk, "magic_school", apply)


#: The history field a cast off the board is kept under (issue #18).
CAST_HISTORY_FIELD = "spell_cast"


@login_required
@require_POST
@rate_limit("cast_spell", max_requests=30, window_seconds=60)
@internal_errors_logged(json_only=True)
def character_cast_spell(request: HttpRequest, pk: int) -> HttpResponse:
    """Cast a spell outside a battle and keep it in the history (issue #18).

    Through :func:`_change`, so the cast runs on the row
    ``services.changing`` has locked (``select_for_update``), and the mana
    spent, the dice log and the history line are saved in its one
    transaction or not at all. The page stays where it is: the answer
    carries the cast, which the sheet shows until the next one, and the
    injury readout re-rendered, since its Mana moved.
    """
    is_game_master = request.user.can_manage_users
    casts = []

    def apply(character: Character, data: dict) -> str:
        cast = cast_in_story(
            character,
            _required_text(data, "spell"),
            is_game_master=is_game_master,
            stated_total=_optional_int(data, "total"),
        )
        casts.append(cast)
        return cast.line

    return _change(
        request,
        pk,
        CAST_HISTORY_FIELD,
        apply,
        answer=lambda character, note: {
            "cast": {**asdict(casts[0]), "line": note},
            "injury_thresholds_html": render_injury_thresholds(
                character, is_game_master=is_game_master
            ),
        },
    )


@login_required
@require_POST
@rate_limit("grant_mana", max_requests=30, window_seconds=60)
@internal_errors_logged(json_only=True)
def character_grant_mana(request: HttpRequest, pk: int) -> HttpResponse:
    """A Game Master grants mana (issue #774)."""
    is_game_master = request.user.can_manage_users

    def apply(character: Character, data: dict) -> str:
        amount = _optional_int(data, "amount")
        if amount is None:
            raise services.CharacterChangeError("amount is required")
        return grant_mana(character, amount, is_game_master=is_game_master)

    return _change(
        request,
        pk,
        "free_spell_points",
        apply,
        answer=lambda character, note: {"mana": character.free_spell_points},
    )


@login_required
@require_POST
@rate_limit("survival_save", max_requests=30, window_seconds=60)
@internal_errors_logged(json_only=True)
def character_roll_survival_save(request: HttpRequest, pk: int) -> HttpResponse:
    """Roll a dying character's survival save off the board (issue #839).

    The sheet printed "survival save each turn (3d6 ≤ CON N)" for a character
    the record says is dying, and nothing off the hex board could roll it.
    Thin over ``services.roll_survival_save``: the server rolls, the dice go
    into the dice log, and a failure marks the character dead. The owner
    rolls their own character's save and a Game Master rolls any, through the
    edit right every sheet write checks; a player once for each state of the
    pools, and nobody while the board is rolling it (the #839 review).
    """

    def run() -> HttpResponse:
        result = services.roll_survival_save(
            pk,
            actor=cast(User, request.user),
            authorize=may_edit(request),
            is_game_master=request.user.can_manage_users,
        )
        return JsonResponse(
            {
                "success": True,
                "note": result.note,
                **sheet_repaint(result.character, cast(User, request.user)),
            }
        )

    return answer_change(run)
