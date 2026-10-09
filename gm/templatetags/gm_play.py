"""The control that takes a character to the Games Master (issues #827, #828).

The sheet and the home page both offer it, from one template, so the two
cannot drift. It renders only where ``gm_characters_attach`` would take the
character (``gm.attach.attach_refusal``), and the sessions it offers are the
viewer's own, because the endpoint attaches to the viewer's sessions only.

The viewer is passed in rather than read from the template context, so a
fragment rendered without a request (a repaint of part of the sheet) can
render this too.
"""

from django import template

from characters.models import Character
from gm.attach import attach_refusal
from gm.luck import LuckPanel
from gm.models import GMSession
from gm.unarmed import unarmed_notice
from gm.withdraw import holding_sessions

register = template.Library()


@register.inclusion_tag("gm/_play_with_gm.html")
def play_with_game_master(
    character: Character, viewer, *, choose_session: bool = True
) -> dict:
    """Render the "Play with the Game Master" control for ``character``.

    Args:
        character: The character the control takes to the table.
        viewer: The user looking at the page; the sessions offered are theirs.
        choose_session: Offer the viewer's sessions beside "A new session"
            (the sheet). False renders a single button that starts a new
            session (the home page's roster, one per character).

    Returns:
        The partial's context. ``offered`` is False when the attach would be
        refused; the partial then renders its slot empty.
    """
    offered = viewer.is_authenticated and attach_refusal(viewer, character) is None
    sessions: list[dict] = []
    if offered and choose_session:
        holding = set(
            character.gm_sessions.filter(user=viewer).values_list("pk", flat=True)
        )
        sessions = [
            {"pk": session.pk, "name": session.name, "holds": session.pk in holding}
            for session in GMSession.objects.filter(user=viewer).only("pk", "name")
        ]
    return {
        "offered": offered,
        "character": character,
        "choose_session": choose_session,
        "sessions": sessions,
    }


@register.inclusion_tag("gm/_sessions_holding.html")
def sessions_holding(character: Character, viewer) -> dict:
    """The other accounts' Games Master sessions holding ``character`` (#871).

    Rendered for the character's owner only, each session with a control
    that withdraws the character from it, and, while the session's fight is
    on the board with the character in it, the way to their seat (#853).

    Args:
        character: The character on the sheet.
        viewer: The user looking at the sheet.

    Returns:
        The partial's context; ``holding`` is empty for anyone but the owner.
    """
    return {"character": character, "holding": holding_sessions(character, viewer)}


@register.inclusion_tag("gm/_luck_control.html")
def luck_control(panel: LuckPanel | None, character_id: int) -> dict:
    """The Characters tab's Luck line and "Spend Luck" control (issue #861).

    Args:
        panel: The tab's :class:`gm.luck.LuckPanel`, or None without a session.
        character_id: The character the line is for.

    Returns:
        The partial's context; ``account`` is None where there is no Luck.
    """
    account = panel.accounts.get(character_id) if panel is not None else None
    return {
        "account": account,
        "character_id": character_id,
        "url": panel.url if panel is not None else "",
    }


@register.simple_tag
def unarmed_notice_for(session: GMSession | None) -> str:
    """Who in ``session`` would fight bare-handed (issue #844), or ``""``."""
    return unarmed_notice(session)
