"""Whether a character may be taken to one of a user's Games Master sessions.

One statement of the rule ``gm_characters_attach`` enforces (issue #341), in
the two shapes its callers need: ``attach_refusal`` answers for one character
(the endpoint, the sheet's "Play with the Game Master" and the home page's
Play, issues #827 and #828), and ``attachable_characters`` lists every
character it would take (the Characters tab's picker). A test holds the two
to the same answer, so no control can offer a character the endpoint refuses.
"""

from dataclasses import dataclass

from django.db.models import QuerySet

from characters.models import Character
from common.helpers import is_owner_or_manager

from .refusal_words import refusal_words


@dataclass(frozen=True)
class AttachRefusal:
    """Why the attach endpoint will not take a character.

    Attributes:
        message: The error the endpoint answers with.
        status: The HTTP status it answers with: 404 where it must not confirm
            the character exists to this user, 400 otherwise.
    """

    message: str
    status: int


def attach_refusal(user, character: Character) -> AttachRefusal | None:
    """Why ``user`` may not attach ``character`` to a session, or None.

    Args:
        user: The signed-in user who would attach it (sessions are always
            this user's own).
        character: The character to attach.

    Returns:
        None when the attach would go through; otherwise the refusal. A
        deleted character and one the user cannot edit (owner, or a Game
        Master) are both "not found"; a dead or incomplete one says so.
    """
    if character.is_deleted or not is_owner_or_manager(user, character.user):
        return AttachRefusal(refusal_words().views.character_not_found, 404)
    if not character.is_alive:
        return AttachRefusal(refusal_words().attach.dead, 400)
    if not character.is_complete:
        return AttachRefusal(
            refusal_words().attach.incomplete.format(
                missing=", ".join(character.missing_parts)
            ),
            400,
        )
    return None


def attachable_characters(user) -> QuerySet[Character]:
    """Every character ``user`` may attach: what ``attach_refusal`` accepts.

    A player's own; a Game Master's own and every player's (issue #759).
    Living, complete and not deleted either way.

    Args:
        user: The signed-in user.

    Returns:
        The characters, unordered, with each owner loaded.
    """
    living = Character.objects.playable().filter(is_alive=True, is_deleted=False)
    if not user.can_manage_users:
        living = living.filter(user=user)
    return living.select_related("user")
