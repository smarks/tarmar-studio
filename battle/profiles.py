"""What this app needs of a battle's state, whichever profile wrote it.

``battle.persistence`` (#251 slice 3) made *storage* profile-aware: a battle
row records which state type its snapshot holds, and the loader dispatches on
it. Everything above storage stayed Tarmar-only — the turn loop, the survivor
check, rename-by-combatant-id, the cast stage — reading through
``load_tarmar_state`` so a classic battle failed by name at the boundary
rather than by ``AttributeError`` three frames down.

This module is that boundary turned into a seam. It names the handful of
things the service layer actually asks of a battle's state, and answers them
per profile:

* **the dice** — Tarmar's rolls are recorded (``common.rolling.Roller``);
  the classic profile takes a ``hexarena.dice.Dice``. Both are seeded
  ``battle.seed + battle.turn_number``, which is what makes a turn replayable.
* **the turn** — run it, and hand back this app's event dicts.
* **the roster** — who is still standing, what a combatant is called, and
  what identifier the app knows it by.
* **the cast stage** — a caster's magical situation, or nothing.

Two asymmetries are real, not smoothed over:

**Identity.** A Tarmar combatant is an ``int`` (its ``BattleCombatant`` row's
pk); a classic figure is a string ``uid``. The app's own identifier is always
the row pk, so a classic battle's figures carry ``str(pk)`` as their uid —
:data:`CLASSIC_UID_IS_ROW_PK`, this app's contract with any code that builds a
classic battle here.

**Events.** The Tarmar engine emits structured events and numbers them itself
(``state.next_sequence``, snapshotted with the state). The classic runner
emits log *lines* and counts nothing, so the app supplies the numbering,
continuing from the rows it has already written. That is why an adapter is
built per battle rather than being a module-level singleton.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Max
from hexarena.dice import Dice

from common.rolling import Roller

from . import agreement, policy, viewmodel
from .engine.engine import run_turn as run_tarmar_turn
from .engine.state import BattleState
from .persistence import CLASSIC_PROFILE, TARMAR_PROFILE, load_state

#: A classic figure's ``uid`` is its ``BattleCombatant`` row's pk as a string.
#: The engine identifies figures by uid and this app by row pk; rather than
#: keep a mapping that can rot, the two are the same value.
CLASSIC_UID_IS_ROW_PK = True

#: The phase number recorded for a classic log line. The classic turn's own
#: phase table (movement/attacks/retreat/end) is not reported per line by the
#: runner, so pinning a line to its phase would mean guessing; 0 says "the
#: turn as a whole" instead of inventing a phase the line may not belong to.
CLASSIC_LINE_PHASE = 0


@dataclass(frozen=True)
class FigureVitals:
    """What a figure's record cares about: alive, Tarmar's two pools and Mana.

    The battle writes a saved character's record from the difference between
    two of these (:func:`battle.services.fight_reports`, issues #750/#751).

    Attributes:
        alive: The figure is not dead.
        fatigue: Current Fatigue, or ``None`` under rules with no Fatigue
            pool (classic's single ST track is not the character's Fatigue
            or Body, so it is not carried back to the record).
        body: Current Body, or ``None`` likewise.
        mana: Current Mana, or ``None`` under rules whose casters spend
            something else (classic wizards spend ST).
    """

    alive: bool
    fatigue: int | None = None
    body: int | None = None
    mana: int | None = None


class BattleProfile:
    """One profile's answers, bound to the battle asking the questions."""

    name: str = ""

    def __init__(self, battle) -> None:
        self.battle = battle

    # -- state ---------------------------------------------------------
    def load(self, snapshot: dict):
        """Rebuild ``snapshot`` as this profile's state type."""
        return load_state(snapshot, self.name)

    def roller(self):
        """This profile's dice source, seeded so the turn replays exactly."""
        raise NotImplementedError

    # -- the turn ------------------------------------------------------
    def run_turn(self, state, roller, decide) -> list[dict]:
        """Run one turn; return this app's event dicts, in order.

        ``decide(state, actor)`` returns a
        :class:`~tarmar_engine.policy.Decision` — the same contract for both
        profiles, which is what lets ``services`` keep one decider (and one
        player-input pause) rather than a branch per profile.
        """
        raise NotImplementedError

    def mint_event(self, state, **fields) -> dict:
        """One more event dict, numbered after everything the turn produced.

        The app writes exactly one event of its own — the victory line — and
        it has to carry a sequence unique within the battle like any other.
        """
        raise NotImplementedError

    # -- the roster ----------------------------------------------------
    def survivors(self, state) -> list:
        """The combatants still able to fight; one left ends the battle."""
        raise NotImplementedError

    def vitals(self, state) -> dict:
        """Every combatant's hurt-state, keyed by the profile's own id.

        Each value is a tuple whose elements only *fall* when the combatant
        is worse off — pools drained, consciousness or life lost. The
        service layer compares a turn's start against its end, element by
        element, to judge whether the turn hurt anybody; a battle that goes
        :data:`~battle.services.STALEMATE_TURNS` turns hurting nobody is a
        stalemate (#289). Read from the state rather than the event stream
        because the classic runner logs prose lines, not typed events.
        """
        raise NotImplementedError

    def figure_vitals(self, state) -> dict[int, FigureVitals]:
        """Every figure's :class:`FigureVitals`, keyed by ``BattleCombatant`` pk."""
        raise NotImplementedError

    def actor_id(self, actor):
        """The app's identifier for this combatant (a ``BattleCombatant`` pk)."""
        raise NotImplementedError

    def actor_name(self, actor) -> str:
        raise NotImplementedError

    def actor_team(self, actor) -> str:
        """This combatant's team tag — empty for a free-for-all combatant.

        Victory is last team standing (#275), judged here so the service
        layer keeps one victory check for both profiles.
        """
        raise NotImplementedError

    def turn_number(self, state) -> int:
        raise NotImplementedError

    def rename(self, state, combatant_id: int, new_name: str) -> None:
        """Rename one combatant in the state — the engine's only name source."""
        raise NotImplementedError

    def lay_dead(self, state, combatant_id: int) -> None:
        """Put one combatant on the board dead (issue #854).

        For a restart of a fight whose character is dead on the record: the
        death is a record, and only a Game Master's revive undoes it.
        """
        raise NotImplementedError

    def next_sequence(self, state) -> int:
        """The number the next event written for this battle will carry."""
        raise NotImplementedError

    # -- the page ------------------------------------------------------
    def view(self, state, *, detail_ids: frozenset[int] = frozenset()) -> dict:
        """What the arena and roster panels draw (see :mod:`.viewmodel`).

        ``detail_ids`` names the combatants whose attribute panel this
        viewer may open (#302). It defaults to none of them: the profiles
        answer the same way about who may read a sheet, and the default is
        the safe half of that answer.
        """
        raise NotImplementedError

    # -- the cast stage ------------------------------------------------
    def caster_magic(self, state, combatant_id: int) -> dict | None:
        """The cast stage's payload for this combatant, or ``None``."""
        raise NotImplementedError


class TarmarProfile(BattleProfile):
    """The six-phase Tarmar profile — every battle this app has ever run."""

    name = TARMAR_PROFILE

    def roller(self) -> Roller:
        return Roller(seed=self.battle.seed + self.battle.turn_number)

    def run_turn(self, state: BattleState, roller, decide) -> list[dict]:
        # A Game Master's "HTH by agreement" ruling, read from the battle
        # every turn so a change takes effect on the next one (#867).
        agreement.fill(state, agreement.Agreement.of(self.battle))
        events: list[dict] = []
        run_tarmar_turn(state, roller, events.append, decide)
        return events

    def mint_event(self, state: BattleState, **fields) -> dict:
        sequence = state.next_sequence
        state.next_sequence += 1
        return {"turn": state.turn, "sequence": sequence, **fields}

    def next_sequence(self, state: BattleState) -> int:
        return state.next_sequence

    def view(
        self, state: BattleState, *, detail_ids: frozenset[int] = frozenset()
    ) -> dict:
        return viewmodel.tarmar_view(
            state, next_sequence=state.next_sequence, detail_ids=detail_ids
        )

    def survivors(self, state: BattleState) -> list:
        return state.active_combatants()

    def vitals(self, state: BattleState) -> dict:
        return {
            combatant.combatant_id: (
                combatant.fatigue,
                combatant.body,
                combatant.alive,
                combatant.conscious,
            )
            for combatant in state.combatants
        }

    def figure_vitals(self, state: BattleState) -> dict[int, FigureVitals]:
        return {
            combatant.combatant_id: FigureVitals(
                alive=combatant.alive,
                fatigue=combatant.fatigue,
                body=combatant.body,
                mana=combatant.mana,
            )
            for combatant in state.combatants
        }

    def actor_id(self, actor):
        return actor.combatant_id

    def actor_name(self, actor) -> str:
        return actor.name

    def actor_team(self, actor) -> str:
        return actor.team

    def turn_number(self, state: BattleState) -> int:
        return state.turn

    def rename(self, state: BattleState, combatant_id: int, new_name: str) -> None:
        state.by_id(combatant_id).name = new_name

    def lay_dead(self, state: BattleState, combatant_id: int) -> None:
        state.by_id(combatant_id).alive = False
        state.by_id(combatant_id).conscious = False

    def caster_magic(self, state: BattleState, combatant_id: int) -> dict | None:
        # Deferred: ``services`` imports this module, so the cast-stage
        # builder can only be reached from inside the call.
        from .services import caster_magic

        return caster_magic(state, combatant_id)


class ClassicProfile(BattleProfile):
    """Classic *The Fantasy Trip: Melee*, over the engine's classic state.

    The SJG-derived classic subpackage stays quarantined per the unification
    plan's copyright note: every import of it here is inside the method that
    needs it, so a Tarmar-only battle never pulls it in.
    """

    name = CLASSIC_PROFILE

    def __init__(self, battle) -> None:
        super().__init__(battle)
        #: Where the next event's number comes from once a turn has run.
        #: ``None`` until then, so a mint before any turn still reads the rows.
        self._sequence_cursor: int | None = None

    def roller(self) -> Dice:
        return Dice(seed=self.battle.seed + self.battle.turn_number)

    def _next_sequence(self) -> int:
        """One past the highest event this battle has written.

        The classic state carries no counter of its own (the Tarmar state
        snapshots ``next_sequence``), so the app derives it from the rows —
        which is also the only place it could be authoritative, since a
        restart deletes events and the numbering has to start over with them.
        """
        highest = self.battle.events.aggregate(highest=Max("sequence"))["highest"]
        return 0 if highest is None else highest + 1

    def run_turn(self, state, roller, decide) -> list[dict]:
        """Drive a classic turn, logging each decision the way Tarmar does.

        The classic runner takes a callable returning the chosen
        :class:`~tarmar_engine.policy.Candidate` and enacts it; unlike the
        Tarmar engine it writes no decision event of its own, so this records
        one per figure — the same "what was considered" trail the command bar
        and the event log are built on. The runner's own output is log lines,
        which land as ``info`` events after them.
        """
        from tarmar_engine.profile import get_profile

        sequence = self._next_sequence()
        turn = state.turn_number
        decisions: list[dict] = []

        def choose(game, figure):
            nonlocal sequence
            decision = decide(game, figure)
            decisions.append(
                {
                    "turn": turn,
                    "phase": CLASSIC_LINE_PHASE,
                    "sequence": sequence,
                    "event_type": "decision",
                    "actor": figure.name,
                    "payload": {
                        "chosen": decision.chosen.to_payload(),
                        "candidates": [
                            candidate.to_payload() for candidate in decision.candidates
                        ],
                    },
                    "message": (
                        f"{figure.name} chooses {decision.chosen.name} "
                        f"— {decision.chosen.rationale}"
                    ),
                }
            )
            sequence += 1
            return decision.chosen

        lines: list[str] = []
        get_profile(self.name).run_turn(state, roller, lines.append, choose)

        from .classic_adaptation import name_sides_in_lines

        events = list(decisions)
        for line in name_sides_in_lines(lines, state.figures):
            events.append(
                {
                    "turn": turn,
                    "phase": CLASSIC_LINE_PHASE,
                    "sequence": sequence,
                    "event_type": "info",
                    "actor": "",
                    "payload": {},
                    "message": line,
                }
            )
            sequence += 1
        self._sequence_cursor = sequence
        return events

    def mint_event(self, state, **fields) -> dict:
        sequence = self.next_sequence(state)
        self._sequence_cursor = sequence + 1
        return {"turn": self.turn_number(state), "sequence": sequence, **fields}

    def next_sequence(self, state) -> int:
        if self._sequence_cursor is None:
            return self._next_sequence()
        return self._sequence_cursor

    def view(self, state, *, detail_ids: frozenset[int] = frozenset()) -> dict:
        return viewmodel.classic_view(
            state,
            turn=self.turn_number(state),
            next_sequence=self.next_sequence(state),
            detail_ids=detail_ids,
        )

    def survivors(self, state) -> list:
        """Figures that can still fight — deliberately not ``state.living()``.

        ``living()`` only excludes the *dead*, so a figure knocked unconscious
        still counts; a battle judged by it would never end while a collapsed
        loser lay on the field. ``can_act()`` is the classic analogue of the
        Tarmar state's ``active``: conscious and alive.
        """
        return [figure for figure in state.figures if figure.can_act()]

    def vitals(self, state) -> dict:
        """(remaining ST, still able to fight) per figure.

        ``current_st`` is ST less accumulated hits, so it falls exactly when
        damage lands; ``can_act()`` falls on collapse or death — the same
        judgement :meth:`survivors` reads.
        """
        return {
            figure.uid: (figure.current_st, figure.can_act())
            for figure in state.figures
        }

    def figure_vitals(self, state) -> dict[int, FigureVitals]:
        """Alive or dead only: classic ST is not the record's Fatigue or Body."""
        return {
            self.actor_id(figure): FigureVitals(alive=not figure.is_dead)
            for figure in state.figures
        }

    def actor_id(self, actor):
        try:
            return int(actor.uid)
        except (TypeError, ValueError) as bad_uid:
            raise ValueError(
                f"classic figure {actor.name!r} has uid {actor.uid!r}, which is "
                f"not a BattleCombatant row pk (see CLASSIC_UID_IS_ROW_PK)"
            ) from bad_uid

    def actor_name(self, actor) -> str:
        return actor.name

    def actor_team(self, actor) -> str:
        """A classic figure's team, decoded from its side by ``classic_team``.

        A team's side is spelled with ``TEAM_SIDE_PREFIX`` and a teamless
        figure's is its own uid (``SIDE_IS_UID``), both in
        :mod:`battle.classic_adaptation`; the name returned is the team's
        own, never the side key (#930).
        """
        from .classic_adaptation import classic_team

        return classic_team(actor)

    def turn_number(self, state) -> int:
        """The last turn played — one behind the classic state's own counter.

        ``GameState.turn_number`` names the turn *about to be* played: it
        starts at 1 on an unplayed battle and ``end_turn`` increments it. This
        app's ``Battle.turn_number`` names the last turn *completed* (Tarmar's
        ``state.turn`` is 0 before its first turn), and everything built on it
        reads that way — the page's turn label beside a log whose top group is
        that turn, the seat prompt's ``turn + 1``, and the per-turn dice seed.
        Reporting the engine's own counter put a classic battle's label one
        ahead of its own event log.
        """
        return state.turn_number - 1

    def lay_dead(self, state, combatant_id: int) -> None:
        """ST -1 is dead in classic (p.3): the hits that put it there."""
        figure = next(
            (one for one in state.figures if one.uid == str(combatant_id)), None
        )
        if figure is None:
            raise ValueError(f"no combatant {combatant_id} in this battle's state")
        figure.damage_taken = max(figure.damage_taken, figure.strength + 1)

    def rename(self, state, combatant_id: int, new_name: str) -> None:
        figure = next(
            (one for one in state.figures if one.uid == str(combatant_id)), None
        )
        if figure is None:
            raise ValueError(f"no combatant {combatant_id} in this battle's state")
        figure.name = new_name

    def caster_magic(self, state, combatant_id: int) -> dict | None:
        """The classic wizard's own hand, in the profile-neutral shape (#263).

        Slice 8 gave a classic battle real wizards and slice 9 seated a
        player beside them, which made this method the last thing between a
        seated classic wizard and its spells: with no payload the CAST
        option still rendered (the menu collapses every candidate carrying a
        ``spell_key`` into it) and opened a stage with nothing on it,
        advertising a mana pool classic does not have.

        What it publishes now is the same payload
        :func:`battle.services.caster_magic` publishes for Tarmar, in the
        vocabulary classic actually uses: a pool named ST — the very pool
        that holds the wizard's hit points — and a row per *(spell, target)*
        offer, since classic aims the same spell at several figures where
        Tarmar aims once. The building is in
        :func:`battle.classic_adaptation.classic_caster_magic`, imported
        inside this call so a Tarmar-only battle never pulls the SJG-derived
        subpackage in.
        """
        from .classic_adaptation import classic_caster_magic

        return classic_caster_magic(state, combatant_id)


#: Every profile this app can run, by the name a battle row records.
PROFILES: dict[str, type[BattleProfile]] = {
    TarmarProfile.name: TarmarProfile,
    ClassicProfile.name: ClassicProfile,
}


def for_battle(battle) -> BattleProfile:
    """The adapter for this battle's rules profile.

    Raises:
        ValueError: for a profile this app cannot run — by name, because
            running a battle under the wrong rules is worse than stopping.
    """
    adapter = PROFILES.get(battle.rules_profile)
    if adapter is None:
        raise ValueError(
            f"unknown rules profile {battle.rules_profile!r}: this app can run "
            f"{', '.join(sorted(PROFILES))}"
        )
    return adapter(battle)


def view_of(battle, *, detail_ids: frozenset[int] = frozenset()) -> dict:
    """What this battle's page draws, in the one profile-neutral shape.

    A battle still in its lobby has no state to draw — the roster rows exist
    but the snapshot is assembled at start — so the answer is an empty arena
    rather than a loader forced to invent one.

    ``detail_ids`` (#302) names the combatants this viewer may see the full
    attributes of — ``battle.access.detail_visible_ids`` is what works that
    out. Omitted, nobody's: this function has no viewer to ask, so the
    default is the answer that cannot leak.
    """
    profile = for_battle(battle)
    if not battle.state_json:
        return viewmodel.empty_view(profile.name, turn=battle.turn_number)
    return profile.view(profile.load(battle.state_json), detail_ids=detail_ids)


def decider(battle):
    """This battle's AI: the profile's own policy, in one Decision shape."""
    if battle.rules_profile == CLASSIC_PROFILE:
        from tarmar_engine.classic import policy as classic_policy

        return classic_policy.choose_option
    return policy.choose_option
