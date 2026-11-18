"""A Game Master's ruling for single figures, as a battle records it.

Two rulings on a battle name one thing for one figure at a time: a magic bound
lifted (issue #37, ``battle.magic_bounds``) and a working granted
(tarmar-studio #208, ``battle.working_grants``). Each lives on the battle, the
one source of truth, as a list of entries in this shape::

    [{"combatant_id": int, <subject>: str, "by": str, "by_id": int, "at": str}]

``combatant_id`` is a ``BattleCombatant`` pk, which is the Tarmar engine's
combatant id; ``<subject>`` is what was ruled for the figure, under the name
its ruling stores it by (``"bound"``, ``"working"``); ``by`` and ``by_id`` are
the Game Master's username and user pk when it was ruled, and ``at`` is when,
as an ISO 8601 timestamp.

The record is written once, here. A kind of ruling subclasses
:class:`FigureRulings` and names the battle's field and the subject's stored
name; who may change a record, and what a change writes to the battle log, is
``battle.services``' (``set_magic_bounds_lifted``, ``set_working_granted``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Self

if TYPE_CHECKING:
    from .models import Battle

#: The stored record's field names every kind of ruling shares.
COMBATANT_ID = "combatant_id"
BY = "by"
BY_ID = "by_id"
AT = "at"


@dataclass(frozen=True)
class FigureRuling:
    """One thing ruled for one figure, with who ruled it and when.

    Attributes:
        combatant_id: The figure's ``BattleCombatant`` pk.
        subject: What was ruled for the figure: a bound's key, a working's.
        by: The Game Master's username when it was ruled.
        by_id: The Game Master's user pk.
        at: When, as an ISO 8601 timestamp.
    """

    combatant_id: int
    subject: str
    by: str
    by_id: int
    at: str


@dataclass(frozen=True)
class FigureRulings:
    """One kind of ruling as a battle stores it: every entry on the battle.

    A subclass names :attr:`battle_field` and :attr:`subject_field`, and may
    order a figure's entries (:meth:`subject_rank`).

    Attributes:
        entries: The entries, in the order they were ruled.
    """

    entries: tuple[FigureRuling, ...] = ()

    #: The ``Battle`` field the record is stored in.
    battle_field: ClassVar[str]
    #: The stored name of what an entry rules for its figure.
    subject_field: ClassVar[str]

    @classmethod
    def subject_rank(cls, subject: str) -> int:
        """Where a subject sorts among one figure's entries.

        The same for every subject here, so a figure's entries stay in the
        order they were ruled; a kind of ruling with an order of its own
        overrides it.

        Args:
            subject: What an entry rules.
        """
        return 0

    @classmethod
    def of(cls, battle: Battle) -> Self:
        """The battle's recorded ruling (empty for a battle with none).

        Args:
            battle: The ``Battle``.
        """
        return cls(
            tuple(
                FigureRuling(
                    combatant_id=int(entry[COMBATANT_ID]),
                    subject=str(entry[cls.subject_field]),
                    by=str(entry[BY]),
                    by_id=int(entry[BY_ID]),
                    at=str(entry[AT]),
                )
                for entry in getattr(battle, cls.battle_field) or []
            )
        )

    def in_order(self) -> list[FigureRuling]:
        """The entries figure by figure, each figure's by :meth:`subject_rank`
        and then in the order they were ruled."""
        return sorted(
            self.entries,
            key=lambda entry: (entry.combatant_id, self.subject_rank(entry.subject)),
        )

    def to_json(self) -> list[dict]:
        """The stored shape, in the order :meth:`in_order` gives."""
        return [
            {
                COMBATANT_ID: entry.combatant_id,
                self.subject_field: entry.subject,
                BY: entry.by,
                BY_ID: entry.by_id,
                AT: entry.at,
            }
            for entry in self.in_order()
        ]

    def for_figure(self, combatant_id: int) -> frozenset[str]:
        """What is ruled for one figure.

        Args:
            combatant_id: The figure's ``BattleCombatant`` pk.
        """
        return frozenset(
            entry.subject
            for entry in self.entries
            if entry.combatant_id == combatant_id
        )

    def with_ruled(
        self,
        combatant_id: int,
        subjects: Iterable[str],
        *,
        by: str,
        by_id: int,
        at: str,
    ) -> Self:
        """This record with ``subjects`` ruled for the figure.

        A subject already ruled for the figure keeps its first record of who
        and when.

        Args:
            combatant_id: The figure's ``BattleCombatant`` pk.
            subjects: What to rule for the figure.
            by: The Game Master's username.
            by_id: The Game Master's user pk.
            at: When, as an ISO 8601 timestamp.
        """
        already = self.for_figure(combatant_id)
        added = tuple(
            FigureRuling(combatant_id, subject, by, by_id, at)
            for subject in subjects
            if subject not in already
        )
        return type(self)(self.entries + added)

    def without(self, combatant_id: int, subjects: Iterable[str]) -> Self:
        """This record with ``subjects`` no longer ruled for the figure.

        Args:
            combatant_id: The figure's ``BattleCombatant`` pk.
            subjects: What to take off the figure's record.
        """
        taken_off = frozenset(subjects)
        return type(self)(
            tuple(
                entry
                for entry in self.entries
                if entry.combatant_id != combatant_id or entry.subject not in taken_off
            )
        )
