"""Adopting a guest's arena into the account they sign into (issue #305).

The friendlier of the two answers to "what happens at sign-in?". A visitor
who plays a battle, hits a limit, and takes the offer to register should
find that battle waiting for them under their new account — dropping it
would punish exactly the person who accepted the invitation, and it would
make the invitation dishonest ("sign in to save" that saves nothing).

Adoption is possession-based, and that is sound: the rows moved are the
ones stamped with the guest key *in this very session*, so the only person
who can trigger it is the person who created them. It runs on
``user_logged_in``, which covers both signing in and registering (the
register view logs you straight in), and it works because Django's
``login()`` cycles the session key but preserves session *data* — the guest
key is still there when the signal fires.

The one thing adoption does not do is retroactively save characters. A
guest's fighters were never ``Character`` rows (see
``battle.models.BattleCombatant``), and inventing them at sign-in would
quietly fill the new account's roster with figures the person never chose
to keep. The battle is adopted; its combatants stay the battle's.
"""

from __future__ import annotations

from django.contrib.auth.signals import user_logged_in
from django.db import transaction
from django.dispatch import receiver

from common.guest import clear_guest_key, peek_guest_key

from .models import Battle, BattleCombatant, BattleDraft


@transaction.atomic
def adopt_guest_rows(user, guest_key: str) -> dict[str, int]:
    """Move every row stamped ``guest_key`` onto ``user``.

    Returns a count per kind, for the log line and for tests.

    Seats are the one careful case: a battle allows a user at most one
    seat, so a seat is adopted only in battles where ``user`` does not
    already sit. An unadopted seat keeps its guest key and keeps working
    for whoever holds that session — it simply does not become this
    account's.
    """
    if not guest_key:
        return {"battles": 0, "drafts": 0, "seats": 0}
    already_seated = BattleCombatant.objects.filter(player=user).values("battle_id")
    seats = (
        BattleCombatant.objects.filter(guest_key=guest_key, player__isnull=True)
        .exclude(battle_id__in=already_seated)
        .update(player=user, guest_key="")
    )
    battles = Battle.objects.filter(
        guest_key=guest_key, created_by__isnull=True
    ).update(created_by=user, guest_key="")
    drafts = BattleDraft.objects.filter(
        guest_key=guest_key, created_by__isnull=True
    ).update(created_by=user, guest_key="")
    return {"battles": battles, "drafts": drafts, "seats": seats}


@receiver(user_logged_in)
def adopt_on_login(sender, request, user, **kwargs):
    """Hand this session's guest battles to the account that just signed in."""
    if request is None:
        return
    guest_key = peek_guest_key(request)
    if not guest_key:
        return
    adopt_guest_rows(user, guest_key)
    clear_guest_key(request)
