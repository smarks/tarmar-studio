"""Who would fight bare-handed in a session's fight (issue #844).

A new character with coin and no weapon walked into ``/fight`` with nothing
telling the player: the board seated them bare-handed and they were knocked
out without landing a blow. The Battle tab now names them, from state, both
before the board is built (the attached characters who carry no weapon) and
while it is on (the party's figures with nothing in hand and nothing to draw).
The line is rendered with the tab, and during a fight the board's responses
repaint it (``bare_handed`` in the battle payload), so it stays exactly as
long as it is true; it is never a toast.
"""

from battle.services import bare_handed_line, unarmed_attached_line

from .fight import fight_in_progress
from .models import GMSession


def unarmed_notice(session: GMSession | None) -> str:
    """The Battle tab's line naming who would fight bare-handed, or ``""``.

    The battle app's own sentences, which the battle payload repaints
    (``bare_handed``): the figures on the board while a fight is on, and the
    attached characters otherwise.
    """
    if session is None or session.pk is None:
        return ""
    if fight_in_progress(session) and session.battle is not None:
        return bare_handed_line(session.battle)
    return unarmed_attached_line(session)
