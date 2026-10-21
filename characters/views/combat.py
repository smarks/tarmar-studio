"""Attack resolution endpoint for the sheet's Attack Resolution panel.

Split out of the monolithic ``characters/views.py`` (issue #90). The actual
resolution math lives in ``characters/attack.py``; this view stays thin:
validate the weapon index (the same equipment-index rule every equipment
endpoint uses), resolve, and append to the combat log.
"""

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from common.decorators import rate_limit
from common.helpers import (
    _json_error,
    _parse_json_body,
    _require_field,
)

from .. import attack
from ..equipment_service import validate_equipment_index
from ..models import MAX_COMBAT_LOG_ENTRIES
from .access import edit_denied, locked_character_or_404
from .characters import _log_character_event, sheet_repaint


@login_required
@require_POST
@rate_limit("resolve_attack", max_requests=60, window_seconds=60)
def character_resolve_attack(request, pk):
    """Resolve one d20 attack from the sheet's Attack Resolution panel (issue #101).

    Every number in the response is decided here; the panel formats what it is
    given and derives nothing. The resolved attack is appended to
    ``character.combat_log`` so the result survives a reload — a panel that
    forgot its own last roll would be transient UI, which this project's rules
    forbid (``static/js/notify.js``). The dice go into ``character.dice_log``
    (issue #147), and the answer carries :func:`sheet_repaint`, so the
    sheet's Dice Log shows them without a reload.
    """
    data, err = _parse_json_body(request)
    if err:
        return err

    weapon_index_raw, err = _require_field(data, "weapon_index", "Weapon required")
    if err:
        return err

    # Lock the row for the read-modify-write on combat_log, exactly as the
    # equipment and experience-term writes do: two attacks resolved at once
    # must not lose an entry.
    with transaction.atomic():
        character = locked_character_or_404(pk)
        denied = edit_denied(request, character)
        if denied is not None:
            return denied

        # A character whose attributes nobody has recorded cannot swing at
        # anything: every number the resolution needs (DEX to hit, STR for
        # the weapon's fit) would be derived from an unrecorded attribute.
        # The exports answer 409 for the same reason, and battle, parties and
        # the Games Master all refuse such a row (issues #571, #630).
        if not character.is_complete:
            return _json_error(
                f"{character.name} is not complete: "
                f"{', '.join(character.missing_parts)}.",
                status=409,
            )

        # The house index validator runs first so a bad index gets the same
        # error shape here as everywhere else on this page; attack.resolve
        # re-checks the bound independently, so the service is safe standalone.
        equipment = character.equipment or []
        weapon_index, err = validate_equipment_index(weapon_index_raw, equipment)
        if err:
            return err

        params = dict(data, weapon_index=weapon_index)
        try:
            result = attack.resolve(character, params)
        except attack.AttackValidationError as error:
            return _json_error(str(error))

        entries = list(character.combat_log or [])
        entry = attack.log_entry(
            result, request.user.username, timezone.now().isoformat()
        )
        entries.append(entry)
        character.combat_log = entries[-MAX_COMBAT_LOG_ENTRIES:]
        character.save(update_fields=["combat_log", "dice_log"])

    _log_character_event(request, character, "ATTACK_RESOLVE", result["weapon"]["name"])

    return JsonResponse(
        {
            "success": True,
            "attack": result,
            # The log line a reload would show, for Recent attacks (issue #792).
            "combat_log_entry_html": render_to_string(
                "characters/_combat_log_entry.html", {"entry": entry}
            ),
            # The dice log the swing added to, and the rest of the sheet a
            # change repaints (issue #147).
            **sheet_repaint(character, request.user),
        }
    )
