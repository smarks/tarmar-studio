"""Parties: characters band together (issue #274).

A ``Party`` is a public object — any registered user can see any party —
whose roster is a set of :class:`PartyMembership` rows pointing at Character
rows. The membership is deliberately looser than character visibility:

- The party OWNER adds members, drawn from the characters they can read
  (:func:`characters.models.characters_visible_to` — their own plus any
  public one; everything for a DM/admin). Added characters join
  immediately; there is no acceptance step (Spencer's ruling on #274).
- The party owner (or a DM/admin) removes any member.
- A character's OWNER can always withdraw their character from any party,
  whether or not they can otherwise touch the party (ruling: withdraw
  always wins).

Membership survives the member character going private or being
soft-deleted: the roster keeps the row, but every reader the character is
no longer visible to — the party owner included — sees it name-only (see
``PartyDetailView``). The rule has one statement: anything on a roster row
beyond the character's name goes through ``characters_visible_to``.
"""

from django.conf import settings
from django.db import models
from django.urls import reverse

from common.helpers import is_owner_or_manager


class Party(models.Model):
    """A named band of characters, owned by the user who formed it."""

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="parties_owned",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Disbanding is a soft delete (issue #339, item 18), as it is for
    # characters and battles: the party and its roster rows stay, it drops
    # off the list and its page, and restore puts it back. The members'
    # characters are never touched either way.
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "parties"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("parties:detail", kwargs={"pk": self.pk})

    @property
    def experience_summary(self):
        """Aggregate of the roster's experience levels, for display and for
        #276's hook generator.

        Levels are ``Character.level`` (1–10, derived from ``total_xp``).
        Every non-soft-deleted member counts, private ones included — the
        aggregate names no character, so it leaks nothing a name-only
        roster row doesn't already show. Soft-deleted members are out of
        play and are skipped.

        Returns ``None`` for an empty (or all-deleted) roster, else a dict
        with ``count``, ``average_level`` (a float, one decimal's worth of
        precision left to the display layer), ``min_level``, and
        ``max_level``.
        """
        levels = [
            membership.character.level
            for membership in self.memberships.select_related("character")
            if not membership.character.is_deleted
        ]
        if not levels:
            return None
        return {
            "count": len(levels),
            "average_level": sum(levels) / len(levels),
            "min_level": min(levels),
            "max_level": max(levels),
        }


class PartyMembership(models.Model):
    """One character's seat in one party.

    A character may sit in any number of parties, but only once in each
    (the unique constraint). ``added_by`` is provenance — who put the
    character here — and survives that user's deletion as NULL rather than
    taking the membership with it.
    """

    party = models.ForeignKey(
        Party, on_delete=models.CASCADE, related_name="memberships"
    )
    character = models.ForeignKey(
        "characters.Character",
        on_delete=models.CASCADE,
        related_name="party_memberships",
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="party_members_added",
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["added_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["party", "character"], name="unique_party_character"
            )
        ]

    def __str__(self):
        return f"{self.character.name} in {self.party.name}"


def can_manage_party(user, party) -> bool:
    """The one statement of party-management power: the party's owner, or a
    DM/admin (``common.helpers.is_owner_or_manager``, the same rule
    ``_can_edit_object`` applies to objects owned through ``user``).

    Managing covers editing the party's name/description, adding members,
    and removing any member. It does NOT gate withdrawal — see
    :func:`can_remove_membership`.
    """
    return is_owner_or_manager(user, party.owner)


def manageable_parties(user):
    """:func:`can_manage_party` as a queryset: every party for a DM/admin,
    otherwise the ones ``user`` owns. Edit, autosave, disband and restore
    fetch through it, so a party the reader may not manage is a 404."""
    if user.can_manage_users:
        return Party.objects.all()
    return Party.objects.filter(owner=user)


def can_remove_membership(user, membership) -> bool:
    """May ``user`` strike this roster row?

    Two doors: party management (:func:`can_manage_party`), and character
    ownership — a character's owner can always pull their character out of
    any party, even a private or soft-deleted character (the withdraw-
    always-wins ruling; ownership needs no visibility check).
    """
    return can_manage_party(user, membership.party) or (
        membership.character.user == user
    )


def addable_characters(user):
    """The Character rows ``user`` may seat in a party they manage.

    Exactly the characters they can read — their own (public or private)
    plus any public one; everything for a DM/admin — so the add rule is
    the read rule, stated once in ``characters_visible_to``. Soft-deleted
    characters are out with the rest of the invisible.
    """
    from characters.models import characters_visible_to

    # A party musters into battle, so an incomplete character -- one still
    # missing attributes or its Fatigue roll -- cannot join (issue #571).
    return characters_visible_to(user).playable()
