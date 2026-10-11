"""A player takes their character back out of a Game Master's session (#871).

A Game Master may attach any player's character to one of his sessions
(#759). While it stays attached, his session reads its sheet into every turn
and his turns may write to it; the owner could neither see that nor stop it,
because detaching (``gm_characters_detach``) is the session owner's alone.

So the character's sheet lists the Games Master sessions of other accounts
that hold it, each with a control that withdraws it. Withdrawing removes the
link and destroys nothing: the session's history, its turn records, the label
and grants on its ``SessionCharacter`` row and what a fight already wrote all
stay, as Spencer's ruling says ("a player's withdrawal leaves the session's
records intact"). The withdrawal itself is recorded, and a live fight lets the
character go (:func:`withdraw`). If the Game Master attaches it again, the
row's label and grants come back with it.
"""

from dataclasses import dataclass

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from battle.models import Battle
from battle.services import withdraw_from_fight
from characters.models import Character
from characters.services import DeadCharacterWrite, changing
from common.decorators import rate_limit
from common.helpers import database_integer

from .fight import fight_in_progress
from .models import GMSession
from .retrieval import SESSION_NOTICE_KIND


@dataclass(frozen=True)
class HoldingSession:
    """Another account's Games Master session that holds a character.

    Attributes:
        session: The session.
        battle: The session's fight while it is on the board with the
            character in it, so the sheet can send the owner to their seat
            (#853); ``None`` otherwise.
    """

    session: GMSession
    battle: Battle | None


def holding_sessions(character: Character, owner) -> list[HoldingSession]:
    """The other accounts' sessions ``character`` is attached to, for its owner.

    Args:
        character: The character.
        owner: The viewer; nothing is listed unless they own the character.

    Returns:
        The sessions, newest first; empty for anyone but the owner.
    """
    if not owner.is_authenticated or character.user_id != owner.pk:
        return []
    sessions = (
        character.gm_sessions.exclude(user=owner)
        .select_related("user", "battle")
        .order_by("-updated_at")
    )
    holding = []
    for session in sessions:
        battle = session.battle
        fighting = (
            battle is not None
            and fight_in_progress(session)
            and battle.combatants.filter(character=character).exists()
        )
        holding.append(HoldingSession(session, battle if fighting else None))
    return holding


def withdrawal_line(character: Character, owner) -> str:
    """What the Game Master's session is told when a character is withdrawn."""
    return (
        f"{owner.username} withdrew {character.name} from this session; "
        f"{character.name}'s sheet no longer comes to the table."
    )


def withdraw(character: Character, session_id: int, owner) -> bool:
    """Take ``character`` out of another account's session, for its owner.

    The link goes, and three records say so (review of #871): a line in the
    character's history, a line in the session's conversation that the Game
    Master sees, and -- when the session's fight is on the board with the
    character in it -- the figure leaving that fight
    (:func:`battle.services.withdraw_from_fight`; the withdrawal wins, a
    coordinator's ruling under the standing rule, Spencer may overrule).
    Everything the session already recorded stays; see the module docstring.

    Args:
        character: The owner's character.
        session_id: The session to withdraw it from.
        owner: Who asks; must own the character.

    Returns:
        True when a link was removed, False when there was none.

    Raises:
        PermissionError: The asker does not own the character.
    """
    if character.user_id != owner.pk:
        raise PermissionError("Only the character's owner withdraws it")
    with transaction.atomic():
        session = (
            GMSession.objects.select_for_update()
            .filter(pk=session_id, characters=character)
            .exclude(user=owner)
            .first()
        )
        if session is None:
            return False
        session.characters.remove(character)
        session.history = [
            *session.history,
            # A notice for the Game Master reading the session, never the
            # model's own words (second review of #871).
            {
                "role": "assistant",
                "kind": SESSION_NOTICE_KIND,
                "content": withdrawal_line(character, owner),
            },
        ]
        session.save(update_fields=["history_json", "updated_at"])
        # A dead character may be withdrawn as a living one may (#932).
        with changing(
            character.pk,
            actor=owner,
            mark_played=False,
            dead=DeadCharacterWrite.SESSION,
        ) as change:
            change.note("Games Master session", session.name, "withdrawn")
        if session.battle_id is not None:
            withdraw_from_fight(session.battle_id, character, by=owner)
    return True


@login_required
@require_POST
@rate_limit("gm_withdraw", max_requests=20, window_seconds=60)
def gm_character_withdraw(request: HttpRequest, character_id: int) -> HttpResponse:
    """The sheet's "Withdraw from this session" control (issue #871).

    A plain form post that goes back to the sheet, which renders the list
    from the links as they now stand. Someone else's character is not found.
    """
    character = get_object_or_404(
        Character, pk=character_id, user=request.user, is_deleted=False
    )
    try:
        session_id = database_integer(request.POST.get("session_id"))
    except ValueError:
        return HttpResponseBadRequest("session_id must be a session's id")
    withdraw(character, session_id, request.user)
    return redirect("characters:edit", pk=character.pk)
