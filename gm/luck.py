"""Luck, spent once per session per level (issue #861).

The skills glossary: "Luck ... **Special:** once per session per level, after
seeing a roll's result you may add this skill's level to it." Nothing counted
the uses, and the character record has no session to count them in. A Games
Master session does: each character's ``SessionCharacter`` row carries
``luck_spent``, and the Characters tab offers the spend beside the count.

A spend is a record (Spencer's ruling): the count on the session, and a line
in the character's history naming the session, the roll it was added to, and
how many of the session's uses are gone. A player is held to their Luck
level; a Game Master may grant a use past it, and the line says so (the
standing rule: players held to the rules, a Game Master may override).
"""

from dataclasses import dataclass

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST

from characters import services as character_services
from characters.constants import too_long
from characters.models import Character
from characters.skills import get_skill_level
from common.decorators import rate_limit
from common.helpers import _json_error, _parse_json_body, database_integer

from .models import GMSession, SessionCharacter

#: The Luck skill's stored name.
LUCK_SKILL = "luck"

#: The most a spend's note about the roll may say.
MAX_ROLL_NOTE = 120


@dataclass(frozen=True)
class LuckAccount:
    """One character's Luck in one session.

    Attributes:
        level: The character's Luck level (what a spend adds).
        spent: Uses spent in this session.
    """

    level: int
    spent: int

    @property
    def remaining(self) -> int:
        """Uses left this session; 0 once the level is used up."""
        return max(0, self.level - self.spent)


@dataclass(frozen=True)
class LuckPanel:
    """What the Characters tab needs to offer Luck: where to post, and whose.

    Attributes:
        url: The session's spend endpoint.
        accounts: Each attached character's Luck, by character id; characters
            without Luck are absent.
    """

    url: str
    accounts: dict[int, LuckAccount]


def luck_panel(session: GMSession | None) -> LuckPanel | None:
    """The Characters tab's Luck offers for ``session``, or None without one."""
    if session is None or session.pk is None:
        return None
    return LuckPanel(
        url=reverse("gm:spend_luck", args=[session.pk]),
        accounts=luck_accounts(session),
    )


def luck_level(character: Character) -> int:
    """The character's Luck level, 0 without the skill."""
    points = int((character.skills or {}).get(LUCK_SKILL, 0) or 0)
    return get_skill_level(points, LUCK_SKILL)


def luck_accounts(session: GMSession | None) -> dict[int, LuckAccount]:
    """Every attached character's Luck in ``session``, by character id.

    Characters without Luck are left out, so the pane offers nothing for them.
    """
    if session is None or session.pk is None:
        return {}
    spent = dict(
        SessionCharacter.objects.filter(session=session).values_list(
            "character_id", "luck_spent"
        )
    )
    accounts = {}
    for character in session.attached_characters():
        level = luck_level(character)
        if level:
            accounts[character.pk] = LuckAccount(level, spent.get(character.pk, 0))
    return accounts


def spend_luck(
    session: GMSession, character_id: int, *, is_game_master: bool, roll: str = ""
) -> str:
    """Spend one of a character's Luck uses in this session.

    Args:
        session: The session; the character must be attached to it.
        character_id: The character.
        is_game_master: Whether the session's owner is a Game Master, who may
            grant a use past the level.
        roll: What the Luck is added to, in the asker's words.

    Returns:
        The history line.

    Raises:
        CharacterChangeError: Not attached, no Luck, a player past the
            level, or a ``roll`` longer than :data:`MAX_ROLL_NOTE`.
    """
    roll = " ".join(roll.split())
    # Refused past the limit, never cut (#938): the history line keeps what
    # was asked for whole, or nothing is spent.
    refusal = too_long("The roll", roll, MAX_ROLL_NOTE)
    if refusal:
        raise character_services.CharacterChangeError(refusal)
    with transaction.atomic():
        character = session.attached_characters().filter(pk=character_id).first()
        if character is None:
            raise character_services.CharacterChangeError(
                "That character is not in this session"
            )
        # Before a use is counted: the dead spend nothing (#932). changing()
        # below would refuse them too, after the count was saved.
        character_services.refuse_the_dead(character)
        level = luck_level(character)
        if not level:
            raise character_services.CharacterChangeError(
                f"{character.name} has no Luck to spend"
            )
        link, _created = SessionCharacter.objects.get_or_create(
            session=session, character=character
        )
        link = SessionCharacter.objects.select_for_update().get(pk=link.pk)
        if link.luck_spent >= level and not is_game_master:
            raise character_services.CharacterChangeError(
                f"{character.name} has spent all {level} of this session's Luck "
                "(once per session per level); only a Game Master grants more"
            )
        link.luck_spent += 1
        link.save(update_fields=["luck_spent"])
        past = (
            " (past the level: the Game Master's grant)"
            if link.luck_spent > level
            else ""
        )
        line = (
            f"spent Luck in {session.name}: +{level} to "
            f"{roll or 'the roll just made'}; {link.luck_spent} of {level} this "
            f"session{past}"
        )
        with character_services.changing(
            character.pk, by=session.user.username, mark_played=False
        ) as change:
            change.note("luck", "", line)
    return line


@login_required
@require_POST
@rate_limit("gm_luck", max_requests=30, window_seconds=60)
def gm_spend_luck(request: HttpRequest, session_id: int) -> JsonResponse:
    """The Characters tab's "Spend Luck" control (issue #861).

    Body: ``{"character_id": <id>, "roll": "<optional words>"}``. The
    session's owner spends for its characters; the tab refetches itself from
    the answer, so the count on the page is the stored one.
    """
    session = get_object_or_404(GMSession, pk=session_id, user=request.user)
    data, error = _parse_json_body(request)
    if error:
        return error
    try:
        character_id = database_integer(data.get("character_id"))
    except ValueError:
        return _json_error("character_id must be a character's id")
    try:
        line = spend_luck(
            session,
            character_id,
            is_game_master=request.user.can_manage_users,
            roll=str(data.get("roll") or ""),
        )
    except character_services.CharacterChangeError as refusal:
        return _json_error(str(refusal))
    return JsonResponse({"ok": True, "note": line})
