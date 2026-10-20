"""A Game Master's ruling lifting a figure's magic bounds on a battle (issue #37).

The canon's magic rules bound every cast: the whole mana of a casting lies
within the caster's Channel for the spell, a push takes no more than the push
cap, and a counter commits an amount within the counter's commitment bounds
(``battle.magic_rules``). Players are held to them. Spencer's ruling of
2026-10-14, under the standing rule that a Game Master may override what
players are held to: the Game Master records "Channel lifted for <figure>",
and likewise the push cap and the counter's commitment bounds, each
separately or together, as a ruling on the battle. Players' menus for other
figures are unchanged.

The ruling lives on the battle (``Battle.magic_bounds_lifted_json``), the one
source of truth, as a list of liftings in this shape::

    [{"combatant_id": int, "bound": str, "by": str, "by_id": int, "at": str}]

``combatant_id`` is a ``BattleCombatant`` pk, which is the Tarmar engine's
combatant id; ``bound`` is one of :data:`LIFTABLE_BOUNDS`; ``by`` and ``by_id`` are the
Game Master's username and user pk when the bound was lifted, and ``at`` is
when, as an ISO 8601 timestamp. Who may change it is the HTH-by-agreement
ruling's answer (``battle.agreement``, #867): the fight's runner, while that
account is a Game Master (``battle.services.runs_the_seats``), through the
battle's GM controls (``battle.services.set_magic_bounds_lifted``). Every
change writes one line to the battle log.

The engine does the rest (tarmar-engine v0.9.18): before every turn, and
before the cast stage is drawn, the Tarmar profile writes the record onto each
figure's ``CombatantState.lifted_magic_bounds`` (:func:`fill`); the engine's
menu then offers that figure the wider amounts, each option carrying the
lifted bounds it goes past (``Candidate.lifted_bounds``), the turn runs them,
and a cast or counter that went past a lifted bound lists the same bounds in
its payload (``LIFTED_BOUNDS_KEY``), which the battle log and the GM battle
report narrate (:func:`event_past_by_ruling`). The bound keys are the
engine's, imported here, never spelled in the studio.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from tarmar_engine.magic import (
    CHANNEL,
    COUNTER_BOUNDS,
    LIFTABLE_BOUNDS,
    LIFTED_BOUNDS_KEY,
    PUSH_CAP,
    MagicRules,
)
from tarmar_engine.state import BattleState

from .magic_rules import magic_rules
from .wording import listed, words

# The bound keys (CHANNEL, PUSH_CAP, COUNTER_BOUNDS) and LIFTABLE_BOUNDS, every
# one of them in the order the page and the log name them, are the engine's
# (tarmar-engine v0.9.17), as is LIFTED_BOUNDS_KEY, the payload key naming the
# lifted bounds a cast or counter went past. The loaded canon decides which of
# them it imposes (:func:`offered_bounds`).

#: The GM control's form fields, named once for the endpoint, the tests and
#: the template (which reads them from the panel).
COMBATANT_FIELD = "combatant"
BOUNDS_FIELD = "bounds"
LIFTED_FIELD = "lifted"

#: The stored record's field names.
COMBATANT_ID = "combatant_id"
BOUND = "bound"
BY = "by"
BY_ID = "by_id"
AT = "at"


@dataclass(frozen=True)
class Lifting:
    """One bound lifted for one figure, with who lifted it and when."""

    combatant_id: int
    bound: str
    by: str
    by_id: int
    at: str

    def to_json(self) -> dict:
        """The stored shape of this one lifting."""
        return {
            COMBATANT_ID: self.combatant_id,
            BOUND: self.bound,
            BY: self.by,
            BY_ID: self.by_id,
            AT: self.at,
        }


@dataclass(frozen=True)
class LiftedBounds:
    """The ruling as stored: every lifting on the battle."""

    liftings: tuple[Lifting, ...] = ()

    @classmethod
    def of(cls, battle) -> LiftedBounds:
        """The battle's recorded ruling (empty for a battle with none)."""
        return cls(
            tuple(
                Lifting(
                    combatant_id=int(entry[COMBATANT_ID]),
                    bound=str(entry[BOUND]),
                    by=str(entry[BY]),
                    by_id=int(entry[BY_ID]),
                    at=str(entry[AT]),
                )
                for entry in battle.magic_bounds_lifted_json or []
            )
        )

    def to_json(self) -> list[dict]:
        """The stored shape, figure by figure and in :data:`LIFTABLE_BOUNDS` order."""
        return [lifting.to_json() for lifting in sorted(self.liftings, key=order)]

    def for_figure(self, combatant_id: int) -> frozenset[str]:
        """The bounds lifted for one figure."""
        return frozenset(
            lifting.bound
            for lifting in self.liftings
            if lifting.combatant_id == combatant_id
        )

    def lifted(
        self, combatant_id: int, bounds: Iterable[str], *, by: str, by_id: int, at: str
    ) -> LiftedBounds:
        """This ruling with ``bounds`` lifted for the figure.

        A bound already lifted keeps its first record of who and when.
        """
        already = self.for_figure(combatant_id)
        added = tuple(
            Lifting(combatant_id, bound, by, by_id, at)
            for bound in bounds
            if bound not in already
        )
        return LiftedBounds(self.liftings + added)

    def restored(self, combatant_id: int, bounds: Iterable[str]) -> LiftedBounds:
        """This ruling with ``bounds`` held again for the figure."""
        held = frozenset(bounds)
        return LiftedBounds(
            tuple(
                lifting
                for lifting in self.liftings
                if lifting.combatant_id != combatant_id or lifting.bound not in held
            )
        )


def order(lifting: Lifting) -> tuple[int, int]:
    """Sort key: figure by figure, then in :data:`LIFTABLE_BOUNDS` order."""
    return (lifting.combatant_id, LIFTABLE_BOUNDS.index(lifting.bound))


def bounds_imposed_by(rules: MagicRules) -> tuple[str, ...]:
    """The bounds ``rules`` impose, which are the ones there is anything to lift.

    Channel when the rules carry Channel; the push cap when Push has a cap;
    the counter's commitment bounds when a spell may be countered and a
    defender must commit more than nothing or may commit no more than a cap
    (a least of 0 and no most bound nothing).
    """
    counter_least, counter_most = (
        (0, None) if rules.counterspell is None else rules.counterspell.bounds()
    )
    imposed = {
        CHANNEL: rules.channel is not None,
        PUSH_CAP: rules.push is not None and rules.push.cap() is not None,
        COUNTER_BOUNDS: rules.counterspell is not None
        and (counter_least > 0 or counter_most is not None),
    }
    return tuple(bound for bound in LIFTABLE_BOUNDS if imposed[bound])


def offered_bounds() -> tuple[str, ...]:
    """The bounds the loaded canon imposes, in :data:`LIFTABLE_BOUNDS` order."""
    return bounds_imposed_by(magic_rules())


def bound_label(bound: str) -> str:
    """The words a bound is named in."""
    bound_words_by_key = words().magic_bounds
    return {
        CHANNEL: bound_words_by_key.channel,
        PUSH_CAP: bound_words_by_key.push_cap,
        COUNTER_BOUNDS: bound_words_by_key.counter_bounds,
    }[bound]


def bound_words(bounds: Iterable[str]) -> str:
    """The bounds named in prose, in :data:`LIFTABLE_BOUNDS` order: "A, B and C"."""
    named = frozenset(bounds)
    return listed([bound_label(bound) for bound in LIFTABLE_BOUNDS if bound in named])


def none_lifted_line() -> str:
    """What the page says when no bound is lifted for anyone."""
    return words().magic_bounds.none_lifted.format(bounds=bound_words(offered_bounds()))


def change_message(*, bounds: Iterable[str], lifted: bool, name: str, by: str) -> str:
    """The battle-log line for one change to the ruling."""
    bounds_words = words().magic_bounds
    named = bound_words(bounds)
    if lifted:
        return bounds_words.log_lifted.format(by=by, bounds=named, name=name)
    return bounds_words.log_held.format(by=by, bounds=named, name=name)


def fill(state: BattleState, record: LiftedBounds) -> None:
    """Write the ruling onto every figure's ``lifted_magic_bounds``.

    The engine reads the field (tarmar-engine v0.9.17,
    :func:`tarmar_engine.magic.bound_lifted`) for the menu, the cast and the
    counter. Overwrites what the state held, so a bound held again stops
    being lifted on the very next turn, as :func:`battle.agreement.fill` does
    for its ruling.
    """
    for figure in state.combatants:
        lifted = record.for_figure(figure.combatant_id)
        figure.lifted_magic_bounds = [
            bound for bound in LIFTABLE_BOUNDS if bound in lifted
        ]


def past_by_ruling(bounds: Iterable[str]) -> str:
    """The words for going past ``bounds`` by the ruling; ``""`` for none."""
    named = bound_words(bounds)
    if not named:
        return ""
    return words().magic_bounds.past_by_ruling.format(bounds=named)


def event_past_by_ruling(payload: dict | None) -> str:
    """What the log says beneath a cast or counter that went past a lifted
    bound (the engine's :data:`LIFTED_BOUNDS_KEY`); ``""`` for any other."""
    return past_by_ruling((payload or {}).get(LIFTED_BOUNDS_KEY, []))
