"""The battle log's lines for True Magic, in the configured words (tarmar-studio #208).

The engine logs every event of a turn with a line in its own words. For the
events True Magic brings (tarmar-engine #77: a craft's turns, the spells it
ends, the roll to keep it through a hit, a working lost, a working going off,
its hand-back, an effect the Game Master describes, the drain and a collapse
from it, and a True cast) the line a reader sees is this app's instead,
worded in ``config/battle-rulings.toml``'s ``[true_magic_log]`` table, which
Spencer edits with no Python.

The Tarmar profile rewords a turn's events as the turn hands them back
(``battle.profiles.TarmarProfile.run_turn``, :func:`narrated`), before they
are stored. So the battle page (``battle/_event.html``) and the Games
Master's battle report (``gm.battle_report``) both read the configured line
as the event's own message, and neither needs to know which events are True
Magic's. A line is worded once, with the board and the canon the turn ran
on: a figure renamed later, or a working a later canon no longer builds,
does not change a line already in the log.

Each event is told apart by the keys the engine writes on it, named once
below from its README's "Events" table, and worded from its payload. Nothing
the engine decided is decided again here. Three things stay the engine's:

* every dice line (a ``roll`` event), which has one format for every roll in
  the log, True Magic's included: the event the roll decides is what is
  worded here, with the roll's total and its target;
* the events a working shares with any cast once it goes off (its Control
  Roll, its damage, its debt roll, a Runaway), which name the working as
  they name a spell;
* a True cast's own line, which is the cast's and says everything a cast's
  line says: it is marked as a True cast in the configured words around it.
"""

from __future__ import annotations

from tarmar_engine.engine import CRAFT_BEGUN, DRAIN_REASON
from tarmar_engine.magic import CastingEnding, casting_mana
from tarmar_engine.state import CASTER_POOLS, BattleState

from . import workings
from .magic_rules import loaded_spell, magic_rules
from .wording import TrueMagicLogWords, listed, name_of, words

#: The event types True Magic's events arrive as.
ACTION = "action"
STATUS = "status"

#: The payload keys the engine writes on True Magic's events (its README,
#: "Events"), each named here once.
CRAFT = "craft"
CRAFTING = "crafting"
ENDED = "ended"
CRAFT_INTERRUPTED = "craft_interrupted"
CRAFT_LOST = "craft_lost"
WORKING = "working"
WORKING_EFFECT = "working_effect"
TRUE_CAST = "true_cast"
REASON = "reason"
UNCONSCIOUS = "unconscious"
HEALED = "healed"
TARGET = "target"
MANA_RETURNED = "mana_returned"
DEBT_RETURNED = "debt_returned"
SPELL = "spell"
TIER = "tier"
SHARE = "share"
BORROWED = "borrowed"
DEBT = "debt"
MANA_LEFT = "mana_left"
CHANNEL = "channel"
MAGNITUDE = "magnitude"


def lost_sentences(said: TrueMagicLogWords) -> dict[CastingEnding, str]:
    """The sentence for a craft lost by each ending a craft is lost by.

    One for every key of the engine's ``CRAFT_LOSS_REASONS``, which
    ``battle.tests.test_true_magic_narration`` holds this to.

    Args:
        said: The log's configured words.
    """
    return {
        CastingEnding.ABANDONED: said.lost_abandoned,
        CastingEnding.BLOCKED: said.lost_blocked,
        CastingEnding.UNPAID: said.lost_unpaid,
        CastingEnding.INTERRUPTED: said.lost_interrupted,
        CastingEnding.UNCONSCIOUS: said.lost_unconscious,
        CastingEnding.GRAPPLED: said.lost_grappled,
        CastingEnding.COUNTERED: said.lost_countered,
    }


class _Teller:
    """One turn's True Magic events, each told in the configured words."""

    def __init__(self, state: BattleState, events: list[dict]) -> None:
        self.said = words().true_magic_log
        self.events = events
        self.names = {figure.combatant_id: figure.name for figure in state.combatants}
        self.by_sequence = {event["sequence"]: event for event in events}

    def told(self, index: int) -> dict:
        """The event at ``index``, its message reworded if it is True
        Magic's, and as the engine wrote it otherwise."""
        event = self.events[index]
        line = self._line(index)
        return event if line is None else {**event, "message": line}

    def _line(self, index: int) -> str | None:
        """The configured line for the event at ``index``, or ``None`` for
        an event that is not True Magic's."""
        event = self.events[index]
        kind, name, payload = event["event_type"], event["actor"], event["payload"]
        if kind == ACTION and CRAFT in payload:
            return self._craft_action(name, payload)
        if kind == ACTION and payload.get(WORKING):
            return self._completion(name, payload)
        if kind == STATUS and payload.get(CRAFTING) and payload.get(ENDED):
            return self.said.spell_let_go.format(
                name=name, spell=loaded_spell(payload[SPELL]).name
            )
        if kind == STATUS and payload.get(CRAFT_INTERRUPTED):
            return self._interruption(name, payload)
        if kind == STATUS and payload.get(CRAFT_LOST):
            return self._lost(name, payload)
        if kind == STATUS and WORKING_EFFECT in payload:
            return self._narrated_effect(name, payload)
        if kind == STATUS and self._is_drain(event):
            return self._drain(name, payload)
        if kind == STATUS and payload.get(UNCONSCIOUS) and self._follows_drain(index):
            return self.said.drain_collapse.format(name=name)
        if kind == STATUS and HEALED in payload and TARGET in payload:
            return self._healed_teammate(name, payload)
        if payload.get(TRUE_CAST):
            return self.said.true_cast.format(line=event["message"])
        return None

    def _craft_action(self, name: str, payload: dict) -> str:
        """A craft action: the working, which of its turns this was, the
        share of mana it gathered and how it was paid, and the mana gathered
        of the whole."""
        said = self.said
        if not payload[SHARE]:
            share = said.share_none.format(mana_left=payload[MANA_LEFT])
        elif payload.get(BORROWED):
            share = said.share_borrowed.format(
                share=payload[SHARE],
                borrowed=payload[BORROWED],
                mana_left=payload[MANA_LEFT],
                debt=payload[DEBT],
            )
        else:
            share = said.share_paid.format(
                share=payload[SHARE], mana_left=payload[MANA_LEFT]
            )
        sentence = (
            said.craft_begun if payload[CRAFT] == CRAFT_BEGUN else said.craft_continued
        )
        return sentence.format(
            name=name,
            working=loaded_spell(payload[SPELL]).name,
            turn=payload["turn"],
            turns=payload["turns"],
            share=share,
            gathered=payload["gathered"],
            mana_total=payload["mana_total"],
            channel=payload[CHANNEL],
        )

    def _interruption(self, name: str, payload: dict) -> str:
        """The roll to keep a craft through a hit: its natural total, the
        target it had to be at or under, and the band it failed by, named as
        the canon labels it."""
        said = self.said
        figures = {
            "name": name,
            "working": loaded_spell(payload[SPELL]).name,
            "natural": payload["natural"],
            "target": payload[TARGET],
        }
        if payload["held"]:
            return said.craft_kept.format(**figures)
        bands = workings.true_magic().interruption.failure_bands
        label = next(band.label for band in bands if band.key == payload["band"])
        sentence = (
            said.craft_broken_runaway if payload["runaway"] else said.craft_broken
        )
        return sentence.format(**figures, margin=payload["margin"], band=label)

    def _lost(self, name: str, payload: dict) -> str:
        """A craft lost before its working went off, by how it ended."""
        sentence = lost_sentences(self.said)[CastingEnding(payload["ending"])]
        return sentence.format(
            name=name,
            working=loaded_spell(payload[SPELL]).name,
            mana_spent=payload["mana_spent"],
            defenders=listed(
                [
                    name_of(self.names, defender)
                    for defender in payload.get("defenders", [])
                ]
            ),
        )

    def _completion(self, name: str, payload: dict) -> str:
        """A working's roll when its craft is whole, and what became of the
        mana it gathered: spent on a success and on a failing total that
        loses it, handed back, the pool's share and the debt's, on any other
        failure."""
        said = self.said
        working = loaded_spell(payload[SPELL])
        roll = self.by_sequence[payload["casting_roll"]]["payload"]
        figures = {
            "name": name,
            "working": working.name,
            "roll": said.casting_roll.format(
                total=roll["total"],
                dice=roll["specification"],
                versus=roll["versus"],
                target=roll["target_number"],
            ),
            "mana_left": payload[MANA_LEFT],
            "channel": payload[CHANNEL],
        }
        if MANA_RETURNED in payload:
            line = said.failed_handed_back.format(
                **figures, mana_returned=payload[MANA_RETURNED]
            )
            if payload.get(DEBT_RETURNED):
                line = said.debt_handed_back.format(
                    line=line,
                    debt_returned=payload[DEBT_RETURNED],
                    debt=payload[DEBT],
                )
            return line
        sentence = said.completed if payload["success"] else said.failed_spent
        return sentence.format(
            **figures, mana=casting_mana(working, payload.get("push_mana", 0))
        )

    def _narrated_effect(self, name: str, payload: dict) -> str:
        """A working whose effect the Game Master describes: its tier, the
        magnitude rolled where its effect has dice, and the figure its craft
        named."""
        said = self.said
        figures = {
            "name": name,
            "working": workings.true_magic()
            .working(payload[WORKING_EFFECT], payload[TIER])
            .name,
            "tier": payload[TIER],
            "magnitude": (
                ""
                if payload[MAGNITUDE] is None
                else said.magnitude_clause.format(magnitude=payload[MAGNITUDE])
            ),
        }
        if payload[TARGET] is None:
            return said.narrated.format(**figures)
        return said.narrated_naming.format(
            **figures, target=name_of(self.names, payload[TARGET])
        )

    @staticmethod
    def _is_drain(event: dict) -> bool:
        """Is this the event that takes a casting's drain from its caster?"""
        return (
            event["event_type"] == STATUS
            and event["payload"].get(REASON) == DRAIN_REASON
        )

    def _follows_drain(self, index: int) -> bool:
        """Does the event at ``index`` come straight after the drain of the
        same figure? The engine checks for a collapse as it takes a cost, so
        a fall the drain brings is the very next event.

        The figure is asked after because the engine marks other events
        unconscious too: in the Renew Spells phase a Runaway that fells a
        crafter loses the craft, whose drain (under a canon that drains for
        a working lost so) may be followed at once by another figure's
        Runaway feeding on them as they lie, or by a spell they cannot
        renew. Neither is a collapse, and neither is the drained figure's.
        """
        if index == 0:
            return False
        before = self.events[index - 1]
        return self._is_drain(before) and before["actor"] == self.events[index]["actor"]

    @staticmethod
    def _pool(payload: dict) -> str:
        """The pool an event's payload reports the level of, as the engine
        names it."""
        return next(pool for pool in CASTER_POOLS if pool in payload)

    def _drain(self, name: str, payload: dict) -> str:
        """What a casting drained from its caster, and what the pool has
        left."""
        pool = self._pool(payload)
        return self.said.drain.format(
            name=name, cost=payload["cost"], pool=pool, left=payload[pool]
        )

    def _healed_teammate(self, name: str, payload: dict) -> str:
        """A healing working that restored another figure. Only a working
        heals anyone but its caster, and only then does the engine's event
        name whom."""
        pool = self._pool(payload)
        return self.said.healed_teammate.format(
            name=name,
            target=name_of(self.names, payload[TARGET]),
            healed=payload[HEALED],
            pool=pool,
            left=payload[pool],
        )


def narrated(state: BattleState, events: list[dict]) -> list[dict]:
    """One turn's events, with True Magic's in the configured words.

    Every other event, and every event of a turn run under a canon with no
    True Magic, is handed back as the engine wrote it.

    Args:
        state: The board as the turn left it, for the figures' names.
        events: The turn's events, in order, as the engine emitted them.

    Returns:
        The events, each True Magic event a copy with its ``message``
        reworded.

    Raises:
        KeyError: A lost craft's ending the configured words have no sentence
            for (:func:`lost_sentences`), or a working's key the loaded canon
            does not build.
    """
    if magic_rules().true_magic is None:
        return events
    teller = _Teller(state, events)
    return [teller.told(index) for index in range(len(events))]
