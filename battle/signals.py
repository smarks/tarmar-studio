"""What the battle app announces for other apps to act on.

The battle app knows nothing of the apps that seat their characters in it;
a signal lets one of them act on a battle's end without the battle app
importing it.
"""

from django.dispatch import Signal

#: Sent once, by :func:`battle.services.advance_turn`, after the turn that
#: ends a battle -- finished or stalemate -- has committed. Keyword argument
#: ``battle``: the battle as it stands. Receivers' exceptions propagate
#: (``send``, not ``send_robust``): a receiver that cannot do its part must
#: say so, not be skipped. The Games Master files its notice of how a session
#: fight ended on this (issue #785).
battle_ended = Signal()
