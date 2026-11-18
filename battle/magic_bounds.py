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
when, as an ISO 8601 timestamp. The record itself is the one every ruling for
single figures keeps (:class:`battle.figure_rulings.FigureRulings`), which a
granted working shares (``battle.working_grants``, tarmar-studio #208). Who
may change it is the HTH-by-agreement
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

from tarmar_engine.magic import (
    CHANNEL,
    COUNTER_BOUNDS,
    LIFTABLE_BOUNDS,
    LIFTED_BOUNDS_KEY,
    PUSH_CAP,
    MagicRules,
)
from tarmar_engine.state import BattleState

from .figure_rulings import FigureRulings
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

#: The stored name of what a lifting lifts: one of :data:`LIFTABLE_BOUNDS`.
BOUND = "bound"


class LiftedBounds(FigureRulings):
    """The ruling as stored: every lifting on the battle, each one bound
    lifted for one figure with who lifted it and when."""

    battle_field = "magic_bounds_lifted_json"
    subject_field = BOUND

    @classmethod
    def subject_rank(cls, subject: str) -> int:
        """A bound's place in :data:`LIFTABLE_BOUNDS`, the order the page and
        the log name a figure's bounds in."""
        return LIFTABLE_BOUNDS.index(subject)

    def lifted(
        self, combatant_id: int, bounds: Iterable[str], *, by: str, by_id: int, at: str
    ) -> LiftedBounds:
        """This ruling with ``bounds`` lifted for the figure.

        A bound already lifted keeps its first record of who and when.
        """
        return self.with_ruled(combatant_id, bounds, by=by, by_id=by_id, at=at)

    def restored(self, combatant_id: int, bounds: Iterable[str]) -> LiftedBounds:
        """This ruling with ``bounds`` held again for the figure."""
        return self.without(combatant_id, bounds)


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
