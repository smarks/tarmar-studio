"""Who may see, and who may control, an arena battle (issue #305).

Before guests, "who is this?" was always a ``User`` and the answer to every
question was some comparison against it. Opening the arena to visitors
without accounts splits that in two, and both halves need saying once:

*Identity* is an :class:`Actor` — a signed-in ``User``, or a guest holding
a ``common.guest`` key. Every ownership comparison goes through it, because
the naive spellings are wrong in a way that fails open: ``seat.player_id ==
user.pk`` is ``None == None`` for an anonymous visitor looking at an
unclaimed seat, which reads as "that seat is yours".

*Visibility* is two querysets, deliberately different:

- :func:`readable_battles` — what this viewer may open. Signed-in users keep
  #250's universal read. An anonymous viewer may open a battle only if its
  roster contains no ``Character`` row that is not public — which is the
  #273 rule applied to the one new place a character's numbers become
  visible. A guest battle has no ``Character`` rows at all (a guest cannot
  save characters), so guest battles stay linkable and watchable; a battle
  holding somebody's private character does not become world-readable
  because it was fought.
- :func:`listable_battles` — what this viewer is *offered*. Narrower on
  purpose. Battle names are free text, and battle creation is now an
  unauthenticated write, so a list that showed every guest's battle to
  everybody would be a public wall anyone could write on. A guest-made
  battle is listed only to the guest who made it and to DMs; everyone
  still reaches any battle they hold a link to through
  :func:`readable_battles`.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q, QuerySet

from common.guest import ensure_guest_key, peek_guest_key

from .models import Battle, BattleCombatant, BattleDraft


def claimed_seat_q(prefix: str = "") -> Q:
    """A ``Q`` matching seats somebody is sitting in.

    "Somebody" is a user or a guest since issue #305, so the old spelling
    (``player__isnull=False``) now under-counts. One statement of it here,
    with ``prefix`` for the times it is asked across a relation
    (``"combatants__"`` when annotating ``Battle``).
    """
    return Q(**{f"{prefix}player__isnull": False}) | ~Q(**{f"{prefix}guest_key": ""})


def is_dm(user) -> bool:
    """True for a DM/admin. Safe on ``AnonymousUser``, which has no flag."""
    return bool(getattr(user, "can_manage_users", False))


@dataclass(frozen=True)
class Actor:
    """Who is acting: a signed-in user, or a guest holding a session key.

    Exactly one of the two is set. ``guest_key`` may be ``""`` for a
    visitor who has never taken ownership of anything — such an actor owns
    nothing, which is why every guest comparison below requires a non-empty
    key before it can match.
    """

    user: object | None
    guest_key: str

    @classmethod
    def for_user_or_guest(cls, user, guest_key: str) -> Actor:
        """The actor for a signed-in ``user``, else the guest holding ``guest_key``.

        A signed-in user's actor never carries a guest key, even when the
        caller has one to hand, so ownership is judged on the account alone.

        Args:
            user: The signed-in user, or ``None`` for a guest.
            guest_key: The guest's session key; ignored when ``user`` is set.

        Returns:
            An ``Actor`` with exactly one of ``user`` and ``guest_key`` in play.
        """
        if user is not None:
            return cls(user=user, guest_key="")
        return cls(user=None, guest_key=guest_key)

    @property
    def is_guest(self) -> bool:
        return self.user is None

    @property
    def is_dm(self) -> bool:
        return is_dm(self.user)

    def owns(self, row) -> bool:
        """True when ``row`` (a Battle or BattleDraft) belongs to this actor."""
        if self.user is not None:
            return row.created_by_id == self.user.pk
        return bool(self.guest_key) and row.guest_key == self.guest_key

    def holds_seat(self, seat: BattleCombatant | None) -> bool:
        """True when ``seat`` is this actor's own claimed seat."""
        if seat is None:
            return False
        if self.user is not None:
            return seat.player_id == self.user.pk
        return bool(self.guest_key) and seat.guest_key == self.guest_key

    def seat_filter(self) -> Q:
        """A ``Q`` matching this actor's own seats. Matches nothing when a
        guest has no key yet — never an ``is null`` that would match every
        unclaimed seat."""
        if self.user is not None:
            return Q(player=self.user)
        if not self.guest_key:
            return Q(pk__in=[])
        return Q(player__isnull=True, guest_key=self.guest_key)


def actor_of(request, *, claim: bool = False) -> Actor:
    """The :class:`Actor` behind ``request``.

    ``claim=True`` mints a guest key if the visitor has none — pass it only
    where the visitor is about to take ownership of something, so that
    merely browsing never issues a session cookie.
    """
    user = request.user if getattr(request.user, "is_authenticated", False) else None
    if user is not None:
        return Actor.for_user_or_guest(user, "")
    key = ensure_guest_key(request) if claim else peek_guest_key(request)
    return Actor.for_user_or_guest(None, key)


#: Battles holding a Character row that is not public. Anonymous viewers
#: are kept out of these: the arena shows a combatant's strength, dexterity,
#: weapon and wounds, so an open battle page would otherwise read out the
#: sheet of a character issue #273 made private.
_HOLDS_A_PRIVATE_CHARACTER = Q(
    combatants__character__isnull=False, combatants__character__is_public=False
)


def readable_battles(actor: Actor) -> QuerySet[Battle]:
    """The battles ``actor`` may open, watch and poll."""
    if actor.user is not None:
        # #250's universal read for every signed-in user, unchanged.
        return Battle.objects.all()
    return Battle.objects.exclude(_HOLDS_A_PRIVATE_CHARACTER)


def deletable_battles(actor: Actor) -> QuerySet[Battle]:
    """The battles ``actor`` may delete or restore (#339, item 5).

    The same answer :func:`can_control` gives, as a queryset: the DM may
    clear away any battle, and everyone else may clear away the ones they
    made. Restore is deliberately the same set as delete rather than
    DM-only — a player who can hide their own battle and then cannot get it
    back has been handed a one-way door dressed as a tidy-up.
    """
    if actor.is_dm:
        return Battle.objects.all()
    if actor.user is not None:
        return Battle.objects.filter(created_by=actor.user)
    if not actor.guest_key:
        return Battle.objects.none()
    return Battle.objects.filter(created_by__isnull=True, guest_key=actor.guest_key)


def listable_battles(actor: Actor) -> QuerySet[Battle]:
    """The battles ``actor`` is offered on the battle list.

    Deleted battles are not among them: a soft-deleted battle is off every
    list until somebody asks for the deleted ones by name (the list page's
    status filter, which only offers what :func:`deletable_battles` allows).
    """
    return _undeleted(_listable_including_deleted(actor))


def _undeleted(battles: QuerySet[Battle]) -> QuerySet[Battle]:
    """``battles`` minus the soft-deleted ones."""
    return battles.filter(is_deleted=False)


def _listable_including_deleted(actor: Actor) -> QuerySet[Battle]:
    """What ``actor`` is offered, before the soft-delete filter."""
    if actor.is_dm:
        return Battle.objects.all()
    guest_made = Q(created_by__isnull=True)
    if actor.user is not None:
        # Signed-in players see the site's own battles. Guest-made ones
        # stay off the list (free-text names, unauthenticated writes) but
        # remain reachable by link.
        return Battle.objects.exclude(guest_made)
    if not actor.guest_key:
        return Battle.objects.none()
    return Battle.objects.filter(guest_made, guest_key=actor.guest_key)


def deleted_battles(actor: Actor) -> QuerySet[Battle]:
    """The soft-deleted battles ``actor`` may see and put back."""
    return _listable_including_deleted(actor).filter(
        is_deleted=True, pk__in=deletable_battles(actor).values("pk")
    )


def own_drafts(actor: Actor) -> QuerySet[BattleDraft]:
    """The setup drafts on ``actor``'s desk."""
    if actor.user is not None:
        return BattleDraft.objects.filter(created_by=actor.user)
    if not actor.guest_key:
        return BattleDraft.objects.none()
    return BattleDraft.objects.filter(
        created_by__isnull=True, guest_key=actor.guest_key
    )


def detail_visible_ids(actor: Actor, battle: Battle) -> frozenset[int]:
    """Combatant ids whose full attribute panel ``actor`` may open (#302).

    The battle page shows condition only; the panel adds the sheet-level
    numbers — attributes, weapon and damage, armour and stops, move rates.
    Spencer's ruling on the issue's open question is the rule:

    - a **DM** sees every combatant on the field;
    - a **player** sees their own combatant always — the seat they hold,
      and their own ``Character`` wherever in the battle it is fighting,
      since a DM may have seated it rather than the owner claiming it;
    - **another player's figure** shows nothing extra.

    One case sits outside those three, deliberately. Whoever *runs* the
    battle — its DM, or the player or guest who made it — also sees the
    figures that belong to nobody: a generated fighter with no saved
    ``Character`` behind it, and a catalog beast. Without it the feature is
    dead in the guest arena (#305), where every combatant is ownerless by
    construction and there is no owner's privacy to protect. It never
    widens to a combatant backed by somebody else's ``Character`` row,
    which is what #273 exists to keep private.

    Returned as ids rather than as a predicate because the view model is
    built once per response: asking per combatant would re-derive the same
    answer for every figure on the field.
    """
    rows = battle.combatants.select_related("character").all()
    if actor.is_dm:
        return frozenset(row.pk for row in rows)
    runs_the_battle = can_control(actor, battle)
    visible = set()
    for row in rows:
        if actor.holds_seat(row):
            visible.add(row.pk)
        elif row.character_id is not None:
            if actor.user is not None and row.character.user_id == actor.user.pk:
                visible.add(row.pk)
        elif runs_the_battle:
            visible.add(row.pk)
    return frozenset(visible)


def can_control(actor: Actor, battle: Battle) -> bool:
    """True when ``actor`` may run this battle: advance, restart, rename, start.

    The DM keeps the run of every battle. Beyond that the rule is now
    ownership rather than role — whoever assembled a battle runs it, guest
    or player. That is admission, not privilege: it grants nothing over
    anybody else's battle.
    """
    return actor.is_dm or actor.owns(battle)


def commands_foes(actor: Actor, battle: Battle) -> bool:
    """True when ``actor`` commands the side nobody sits in (#762).

    Restarting the battle, changing who decides, and choosing a move for a
    figure no player is sitting in. Whoever runs the battle
    (:func:`can_control`) does all three -- except in a fight a player's
    Games Master session put on the board, whose foes belong to the Game
    Master (``Battle.foes_belong_to_game_master``). There the player who made
    it still advances it and plays their own seats, and only a Game Master
    account (the DM flag, as everywhere else) may do the rest. Coordinator's
    ruling under the standing rule that players are held to the rules and the
    Game Master decides; Spencer may overrule.
    """
    if actor.is_dm:
        return True
    return can_control(actor, battle) and not battle.foes_belong_to_game_master


def decides_for(actor: Actor, battle: Battle, combatant: BattleCombatant) -> bool:
    """True when ``actor`` supplies this combatant's manual decisions (#308).

    Two owners, in order. A combatant somebody is *sitting in* belongs to
    that person and to nobody else — not even a DM, who may only hand the
    choice to the AI (:func:`battle.services.record_player_action`'s
    ``as_ai``). Every other combatant — a generated fighter, a beast, a
    saved character on the muster — belongs to whoever runs the battle,
    which is what makes a fully manual battle playable: otherwise "manual"
    on an unseated figure would name an owner who does not exist. In a
    fight whose foes belong to the Game Master that is a Game Master
    account, not the player who made it (:func:`commands_foes`, #762).
    """
    if combatant.player_id is not None or combatant.guest_key:
        return actor.holds_seat(combatant)
    return commands_foes(actor, battle)
