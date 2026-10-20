"""Experience-term and skill-allocation endpoints.

Split out of the monolithic ``characters/views.py`` (issue #90). The core
game rules a run applies (aging, survival checks, XP thresholds) live in
``characters/skills.py``; what's left here is thin: parse the request,
simulate the run, hand the result to ``Character.apply_experience_run``
(issue #89), and shape the response.
"""

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from accounts.models import User
from common.decorators import rate_limit
from common.helpers import (
    _json_error,
    _parse_json_body,
    _require_field,
)

from .. import services
from ..models import Character
from ..skills import (
    DEFAULT_RUN_YEARS,
    MAX_EXPERIENCE_YEARS_PER_RUN,
    RANDOM_TRACK_KEY,
    format_skill_level,
    get_skill_level,
    normalize_skill_name,
    selectable_tracks,
    skill_name_refusal,
)
from ..templatetags.dice_display import survival_roll
from ..weapon_skill import needs_weapon_tie, weapon_choices
from .access import answer_change, may_edit
from .characters import (
    can_grant_skill_points,
    render_injury_thresholds,
    render_skills_list,
    sheet_repaint,
)
from .equipment import _get_equipment_response


def _parse_requested_years(data):
    """Read the "years" field of an add-years request body.

    Returns ``(years, None)`` for a valid count, or ``(None, error_response)``.
    An absent field means the default run of one year, so the endpoint keeps
    working for a caller that only names a track.
    """
    raw_years = data.get("years", DEFAULT_RUN_YEARS)
    if isinstance(raw_years, bool) or not isinstance(raw_years, int | str):
        return None, _json_error("Years must be a whole number")
    try:
        years = int(raw_years)
    except (TypeError, ValueError):
        return None, _json_error("Years must be a whole number")
    if years < 1 or years > MAX_EXPERIENCE_YEARS_PER_RUN:
        return None, _json_error(
            f"Years must be between 1 and {MAX_EXPERIENCE_YEARS_PER_RUN}"
        )
    return years, None


def _with_survival_text(run_result):
    """Copy ``run_result`` with each survival check's sentence written out.

    The survival roll reads as ``3d6 [2, 3, 1] + 1 = 7 vs 4``, with a tail
    when a natural roll overrode the target. ``dice_display.survival_roll``
    has always built that string for the page, the PDF and the Markdown
    export -- and ``static/js/character-edit.js`` used to build it a second
    time, in JavaScript, so the line a player saw the moment a term was
    rolled came from a different implementation than the line the same page
    showed after a reload (issue #338 item 4). The sentence ships with the
    answer now, and the browser prints what it is given.

    The copy matters: these check dicts are the ones stored on the character,
    and a display string has no business being saved with them.
    """
    years = []
    for year in run_result.get("years", []):
        check = year.get("survival_check")
        if not check:
            years.append(year)
            continue
        years.append(
            {**year, "survival_check": {**check, "text": survival_roll(check)}}
        )
    return {**run_result, "years": years}


def _build_add_years_response(
    character: Character, run_result: dict, *, viewer: User
) -> JsonResponse:
    """Build the JSON response describing the character after an added run.

    Args:
        character: The saved character.
        run_result: The simulator's result for the run.
        viewer: Who rolled it. A Game Master may set current pools, which the
            re-rendered injury readout offers (issue #751), and the "Play with
            the Game Master" slot offers the viewer's own sessions (#903).
    """
    is_game_master = viewer.can_manage_users
    skills_display = {
        skill_name: {
            "points": points,
            "level": get_skill_level(points, skill_name),
            "display": format_skill_level(points, skill_name),
        }
        for skill_name, points in (character.skills or {}).items()
    }
    # The skills list and the new history entry, rendered by the same partials
    # the page uses, so the browser inserts markup instead of rebuilding its
    # own copy of it (issue #338 items 3 and 4).
    skills_html = render_skills_list(character, is_game_master=is_game_master)
    run_html = render_to_string(
        "characters/_term_entry.html",
        {"run": character.experience_log[-1], "open": True},
    )
    return JsonResponse(
        {
            "success": True,
            "run_result": _with_survival_text(run_result),
            "run_html": run_html,
            "character": {
                "skills_html": skills_html,
                "age": character.age,
                "total_xp": character.total_xp,
                "level": character.level,
                "money_earned": character.money_earned,
                "free_skill_points": character.free_skill_points,
                "free_spell_points": character.free_spell_points,
                "skills": skills_display,
                "experience_years": character.experience_years,
                "is_alive": character.is_alive,
                "death_cause": character.death_cause,
                "total_allocated_points": character.total_allocated_skill_points,
                # A run can grant +1 to STR, DEX or CON (skills.
                # ATTRIBUTE_BONUS_FIELDS), so the Core Attributes inputs move;
                # aging moves the effective scores under them, so Physical and
                # Mental move too. All five were stale on the page the player
                # was looking at while the modal was open (issues #163, #638).
                # The five aging_*_penalty values this used to send as well
                # have no cell anywhere on the sheet, so they went nowhere.
                "strength": character.strength,
                "dexterity": character.dexterity,
                "constitution": character.constitution,
                "physical": character.physical,
                "mental": character.mental,
                # Weight is plain STR (#691), which a "+1 STR" year raises
                # (issue #763).
                "weight_lbs": character.weight_lbs,
                # Aging moves STR/DEX/CON, which moves fatigue/body and the
                # injury-thresholds table (issue #130) the same way any other
                # attribute edit does -- same field names and shapes as
                # _build_field_update_response, so the JS success handler can
                # refresh them with the pattern already wired up there
                # (issue #163: these used to go stale until modal-Close's
                # full-page reload caught them up).
                "fatigue": character.fatigue,
                "body": character.body,
                "injury_thresholds_html": render_injury_thresholds(
                    character, is_game_master=is_game_master
                ),
            },
            # STR also sets carrying capacity, so aging can shift encumbrance,
            # movement, and the armor/weapon combat mods that ride on it.
            # The movement and equipment figures _get_equipment_response
            # builds; the equipment endpoints send the same payload plus the
            # sheet repaint, which this response carries once at the top
            # level (issues #163, #933). updateEquipmentDisplay() renders it.
            "movement": _get_equipment_response(character),
            # Points earned, skills held, the history line (issue #792). The
            # only copy in the response: the movement payload carries none,
            # so the slot and its session queries render once (issue #933).
            **sheet_repaint(character, viewer),
        }
    )


@login_required
@require_POST
@rate_limit("add_years", max_requests=30, window_seconds=60)
def character_add_experience_years(request, pk):
    """Play a run of prior-experience years for a character (issue #221)."""
    data, err = _parse_json_body(request)
    if err:
        return err
    track = data.get("track", RANDOM_TRACK_KEY)

    if track not in selectable_tracks():
        return _json_error(f"Unknown track: {track}")

    years_requested, err = _parse_requested_years(data)
    if err is not None:
        return err
    assert years_requested is not None

    def run() -> JsonResponse:
        with services.changing(
            pk, actor=request.user, authorize=may_edit(request)
        ) as change:
            # A dead character was refused by services.changing before this
            # runs, as on every sheet write (issue #932): the record takes no
            # change the rules govern until a Game Master brings the
            # character back. Only the name stays editable.
            # The simulator, the XP thresholds and apply_experience_run, the
            # same rules the Games Master plays years by (issues #221, #571).
            # A player adds years before play only; a Game Master may after
            # (issue #803).
            note, run_result = services.apply_experience_years(
                change.character,
                track,
                years_requested,
                is_game_master=request.user.can_manage_users,
            )
            change.note("experience", "", note)
        return _build_add_years_response(
            change.character, run_result, viewer=request.user
        )

    return answer_change(run)


@login_required
@require_POST
@rate_limit("allocate_skill", max_requests=25, window_seconds=60)
def character_allocate_skill_point(request, pk):
    """Allocate a free skill point to a skill."""
    data, err = _parse_json_body(request)
    if err:
        return err

    skill_name, err = _require_field(data, "skill", "Skill name required")
    if err:
        return err

    # Refuse an overlong name as received, then normalize it before the
    # transaction.
    refusal = skill_name_refusal(str(skill_name))
    if refusal:
        return _json_error(refusal)
    skill = normalize_skill_name(str(skill_name))

    def run() -> JsonResponse:
        with services.changing(
            pk, actor=request.user, authorize=may_edit(request)
        ) as change:
            character = change.character
            # A player spends the points earned; a Game Master may grant one
            # past them, as the chat's spend_skill_points lets him (#770). A
            # player starts only a skill the glossary names (#862, #902); a
            # Game Master may name any. apply_skill_points decides both from
            # the authority, as it does for the chat.
            target = services.allocation_target(character.skills, skill)
            note, _granted = services.apply_skill_points(
                character,
                skill,
                1,
                is_game_master=request.user.can_manage_users,
            )
            change.note("skills", "", note)
        skill_written = target

        new_points = character.skills[skill_written]
        row = {
            "name": skill_written,
            "points": new_points,
            "display": format_skill_level(new_points, skill_written),
            "needs_weapon_tie": needs_weapon_tie(skill_written),
        }
        return JsonResponse(
            {
                "success": True,
                # The row as the page renders it (issue #338 item 3).
                "row_html": render_to_string(
                    "characters/_skill_row.html",
                    {
                        "skill": row,
                        "character": character,
                        "can_edit": True,
                        "can_grant_skill_points": can_grant_skill_points(
                            is_game_master=request.user.can_manage_users
                        ),
                        "weapon_choices": weapon_choices(),
                    },
                ),
                "skill": skill_written,
                "points": new_points,
                "level": get_skill_level(new_points, skill_written),
                "display": format_skill_level(new_points, skill_written),
                "free_skill_points": character.free_skill_points,
                "total_allocated_points": character.total_allocated_skill_points,
                # A new skill joins the skill check's options (issue #765).
                **sheet_repaint(character, request.user),
            }
        )

    return answer_change(run)


@login_required
@require_POST
@rate_limit("rename_skill", max_requests=30, window_seconds=60)
def character_rename_skill(request, pk):
    """Rename a skill."""
    data, err = _parse_json_body(request)
    if err:
        return err

    old_name, err = _require_field(data, "old_name", "Original skill name required")
    if err:
        return err

    new_name, err = _require_field(data, "new_name", "New skill name required")
    if err:
        return err

    # Refuse an overlong new name as received. The old name is only matched
    # against the skills held, so a record holding one from before #928 can
    # still be renamed out of it.
    refusal = skill_name_refusal(str(new_name))
    if refusal:
        return _json_error(refusal)
    # Normalize names before the transaction (mirrors the skill-allocation write)
    old_skill = normalize_skill_name(str(old_name))
    new_skill = normalize_skill_name(str(new_name))

    def run() -> JsonResponse:
        with services.changing(
            pk, actor=request.user, authorize=may_edit(request)
        ) as change:
            if old_skill == new_skill:
                change.discard()
                return JsonResponse(
                    {"success": True, "skill": new_skill, "unchanged": True}
                )
            # The rule (issue #768) is the service's: a rename never moves
            # points to another skill for a player.
            recorded, points = services.apply_skill_rename(
                change.character,
                old_skill,
                new_skill,
                is_game_master=request.user.can_manage_users,
            )
            change.note("skills", old_skill, recorded)
        return JsonResponse(
            {
                "success": True,
                "skill": new_skill,
                "points": points,
                # The points don't move, but the level can: renaming a skill
                # to or from Arcane re-prices it (issue #154), so the row's
                # level has to be re-rendered from the server's ladder.
                "level": get_skill_level(points, new_skill),
                "display": format_skill_level(points, new_skill),
                # The skill check offered the old name until a reload (#765).
                **sheet_repaint(change.character, request.user),
                # The whole list: a Village choice may join a skill already
                # held, which leaves one row where there were two (#768).
                "skills_html": render_skills_list(
                    change.character, is_game_master=request.user.can_manage_users
                ),
            }
        )

    return answer_change(run, json_not_found=False)
