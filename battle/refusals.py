"""The battle app's refusals: errors whose words are meant for the player (#39).

A battle service says no for two kinds of reason. One is a rule or a guard
refusing what someone asked -- a seat already taken, a choice not on the
menu, a muster that is full -- and its sentence is written for the person
who asked. The other is an internal check failing -- a figure missing from
the stored state, a rules profile this app cannot load -- and its text names
ids, profiles and invariants that mean nothing to a player and are not
theirs to read.

Both used to be a bare ``ValueError``, so a view that caught one to show the
player the refusal showed the internal text word for word as well. A
refusal is now a :class:`BattleRefusal`; the views catch that type alone and
answer every other ``ValueError`` with a fixed, logged message (issue #13
set the pattern for the advance view).

This module holds only the type, so every battle module that refuses --
``services``, ``control``, ``views`` -- imports it without importing the
others.
"""

from __future__ import annotations


class BattleRefusal(ValueError):
    """A battle rule or guard refusing a request, in words for the player.

    Raise it where the message is written for the person who asked; keep a
    plain ``ValueError`` for an internal check. A ``ValueError`` still, so
    every caller that caught the refusal as one keeps catching it.

    Raising this changes only who may read the message, never who is
    refused: the condition in front of the ``raise`` decides that, and a
    Game Master's override sits in that condition, not in the type.
    """
