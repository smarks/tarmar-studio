"""What a seated player is offered on their turn, and how it reads.

Two things the engine's candidate menu does not do for a person choosing by
hand, both done here, at the pause, from the live state the menu was built
from:

* **Every legal target** (issue #775). The engine's policy aims each attack
  where the AI would: ATTACK at the adjacent enemy it scores best, CHARGE,
  MISSILE and ONE LAST SHOT at the nearest, a hand-to-hand strike or grapple
  at one of the enemies an entry condition admits. So a player offered one
  "Strike Gray Wolf" could not strike the other wolf beside them, or shoot
  the wizard at the back. A seated figure's menu is the engine's own
  every-target menu (``choose_option(..., every_target=True)``, built by
  :func:`battle.profiles.decider`; tarmar-engine #21): each aimed option,
  casts included, at every enemy it may be aimed at, scored and forecast by
  the engine for that target, with the AI's own choice unchanged among them.

* **A choice a player can weigh** (issue #788). The menu used to lead with
  the AI's scoring sheet: "score 0.178 — Strike Gray Wolf: bare hands at 1
  hex, d20 -3 vs TN 16 — P(hit) 0.100 ...". :func:`describe` gives each
  option a plain line built from the same figures, the engine's own
  ``Candidate.forecast`` (tarmar-engine #22: who it is aimed at, the chance
  to hit as "2 in 20", the damage dice and what a hit does on average), what
  it costs, and which option the AI would take. The score and its working
  stay on the payload for the page to show on request.

Only the Tarmar profile's menu is widened to every target. A classic
figure's menu comes from the classic policy, whose candidates and state are
shaped differently (a ``GameState`` of ``Figure`` s): it keeps the engine's
own options and targets, and each option is described the same way (issue
#890) from the classic figures' own numbers, read through the classic
engine at the pause -- its attack forecast (``GameState.attack_forecast``,
tarmar-engine #23: the adjDX to roll under on 3d6 or 4d6, the weapon's dice
less the target's armour, what a hit does on average), the ST a spell
invests -- because the classic policy's rationale is a tactic label ("already
in reach: stand and strike"), not a forecast. Either way the AI's own pick is
marked, which both profiles' pages read.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from hexarena.hex import Hex
from tarmar_engine import actions, combat_math, movement
from tarmar_engine.classic import ai as classic_ai
from tarmar_engine.classic import policy as classic_policy
from tarmar_engine.classic.ai import BlowKind
from tarmar_engine.classic.combat import AttackForecast
from tarmar_engine.classic.data import DIE_FACES as CLASSIC_DIE_FACES
from tarmar_engine.classic.data import THREE_DICE, DamageDice
from tarmar_engine.classic.figure import Figure
from tarmar_engine.classic.options import Option as ClassicOption
from tarmar_engine.classic.options import spec as classic_spec
from tarmar_engine.classic.state import GameState
from tarmar_engine.engine import RUN_FATIGUE_COST, SPRINT_FATIGUE_COST
from tarmar_engine.policy import (
    ANCHOR_OPTION,
    Candidate,
    Forecast,
    _hth_partners,
    best_weapon,
    weapons_in_reach,
)
from tarmar_engine.resolution import DIE_FACES
from tarmar_engine.spells import get_spell
from tarmar_engine.state import BattleState, CombatantState

from . import magic_bounds, profiles
from .magic_rules import magic_rules
from .persistence import TARMAR_PROFILE

#: What DODGE and DEFEND guard against, in the words of the "Defending and
#: Dodging" table (special-combat-situations.md): Dodge "Missiles and thrown
#: only", Defend "Melee and non-missile only". The engine agrees: it applies
#: Defend only to an attack that is not ranged, and a throw is ranged. A
#: drift test holds these to the page.
DODGE_DEFENDS_AGAINST = "missiles and thrown weapons only"
DEFEND_DEFENDS_AGAINST = "melee and non-missile attacks only"

#: The options that walk the engine's own route toward their target and say
#: where it takes them, so a route that goes nowhere is read as standing
#: (issue #57). A CHARGE says its reach instead.
STANDING_MOVERS: frozenset[str] = frozenset({"a", "sprint", "c"})


def _adjacent_enemies(
    state: BattleState, actor: CombatantState
) -> list[CombatantState]:
    """The enemies beside ``actor``, in the state's order: those a DISENGAGE
    steps away from, the first of them when no hex is picked
    (``TurnRunner.disengage_step``)."""
    return [
        enemy
        for enemy in state.enemies_of(actor)
        if combat_math.figures_adjacent(actor, enemy)
    ]


def _odds(forecast: Forecast | None) -> str:
    """ "2 in 20 to hit, 1d6-1 less 1 armour (about 2 a hit)", from a forecast.

    Read off the engine's own forecast (``Candidate.forecast``,
    tarmar-engine #22), the figures its rationale is written from, so the
    plain line and the score line a player can open never disagree. Empty
    for an option with no d20 forecast (DEFEND, DISENGAGE, a grapple, a
    charge that falls short).
    """
    if forecast is None or forecast.winning_faces is None:
        return ""
    return (
        f"{forecast.winning_faces} in {DIE_FACES} to hit, "
        f"{forecast.damage_expression}{_armour_less(forecast.armour_stops)} "
        f"({_average_hit(forecast.expected_damage)} a hit)"
    )


def _armour_less(stops: int) -> str:
    """ " less 2 armour", or "" when no armour stops anything, in either
    profile's odds."""
    return f" less {stops} armour" if stops else ""


def _average_hit(expected_damage: float) -> str:
    """ "about 4", or "under 1": what a hit does on average, in either
    profile's odds, from the engine's own expected damage."""
    return "under 1" if expected_damage < 0.5 else f"about {round(expected_damage)}"


def _weapon_phrase(actor: CombatantState) -> str:
    """ "the Saber", or "bare hands" for a figure with no weapon in hand."""
    return f"the {actor.weapon.name}" if actor.weapon.item_id else "bare hands"


def _close_weapon_phrase(actor: CombatantState) -> str:
    """What an HTH strike (t) is made with: "Bare hands or dagger", so a
    sword in hand strikes with bare hands (tarmar-engine #19)."""
    return f"the {actor.weapon.name}" if actor.weapon.hth_usable else "bare hands"


def _with_odds(sentence: str, forecast: Forecast | None) -> str:
    odds = _odds(forecast)
    return f"{sentence}: {odds}" if odds else sentence


def _ends_from(move: str, distance: int, aimed: str) -> str:
    """ "a clear jog of up to 7 hexes ends 2 hexes from Foe": where a move
    that falls short stops, in either profile."""
    return f"a {move} ends {combat_math.hexes_text(distance)} from {aimed}"


def _no_attack_charge_line(aimed: str, reason: str) -> str:
    """The sentence for a charge that strikes nobody, in either profile
    (issue #56)."""
    return f"Charge {aimed}: no attack, since {reason}"


def _clear_gait(option: str) -> str:
    """ "clear jog": the move a Tarmar option makes, as :func:`_no_nearer`
    names it, at the engine's gait for it (``movement.option_gait``)."""
    return f"clear {movement.option_gait(option)}"


def _no_nearer(move: str, aimed: str) -> str:
    """ "no clear jog gets nearer to Foe": why a figure's move stays put."""
    return f"no {move} gets nearer to {aimed}"


def _standing(reason: str) -> str:
    """ "standing, since ...": a DODGE that does not move, in either profile."""
    return f"standing, since {reason}"


def _stand_line(reason: str) -> str:
    """The sentence for a move the engine would not make, in either profile."""
    return f"Stand where you are, since {reason}"


def _no_step_away(
    state: BattleState, actor: CombatantState, threats: list[CombatantState]
) -> str:
    """Why a step away the engine would not take stays put, or "".

    With no hex picked, a DISENGAGE (n) or a break away from hand-to-hand
    (v) steps straight back from the first of ``threats``, else to either
    flank, the engine's own choice
    (:func:`tarmar_engine.movement.step_away_hex`), and the engine logs
    "nowhere to disengage to" (n) or "nowhere to step to" (v) when none of
    the three is clear. A hex the player picks is stepped to as picked.

    Args:
        state: The live state the menu was built from.
        actor: The figure choosing.
        threats: The figures it would step away from, the engine's first.

    Returns:
        The reason, e.g. "with no hex picked you stay where you are, since no
        hex straight back from Foe or to either flank is clear", or "" when
        the engine has a hex to step to (or nothing to step away from).
    """
    if not threats or movement.step_away_hex(state, actor, threats[0]) is not None:
        return ""
    return (
        "with no hex picked you stay where you are, since no hex straight "
        f"back from {threats[0].name} or to either flank is clear"
    )


def _mover_line(
    actor: CombatantState,
    letter: str,
    target: CombatantState | None,
    fatigue: int,
    *,
    stands: bool,
) -> tuple[str, str]:
    """A MOVE or SPRINT's sentence and cost (issue #57).

    The move the engine would make: toward the target along its own route
    (``movement.default_approach``), or no move at all when that route is
    empty, and then no Fatigue either, since the engine charges a gait's
    Fatigue only for hexes walked. A hex the player picks is walked, and
    charged, as picked. The gait and its allowance are the engine's for the
    option (``movement.option_gait``, ``movement.option_allowance``).

    Args:
        actor: The figure choosing.
        letter: The option's key ("a", "sprint", or a yielded one).
        target: The figure it closes on, or None when none remains.
        fatigue: The Fatigue the gait costs.
        stands: Whether the engine's route toward ``target`` is empty.

    Returns:
        ``(plain, cost)``, as :func:`_plain_line` gives them.
    """
    gait = movement.option_gait(letter)
    allowance = movement.option_allowance(actor, letter)
    fatigue_text = f"{fatigue} Fatigue"
    if target is not None and stands:
        return (
            _stand_line(_no_nearer(_clear_gait(letter), target.name)),
            f"{fatigue_text}, only if you move",
        )
    aimed = target.name if target is not None else "the nearest enemy"
    return (
        f"{gait.capitalize()} up to {allowance} hexes toward {aimed}",
        fatigue_text,
    )


def _anchor_line(
    actor: CombatantState, target: CombatantState | None
) -> tuple[str, str]:
    """An anchor option's sentence and cost (tarmar-engine #30).

    Read from the Runaway on the figure the option names and the canon's
    injected rules: the roll the engine will make (the caster's own, or a
    helper's), and for a helper the mana the engine takes whatever comes of
    it (:meth:`~tarmar_engine.magic.RunawayRules.helper_cost`) and whether a
    failure catches them. The roll's target, less the casting penalties, is
    the engine's, in the option's rationale.

    Args:
        actor: The figure choosing.
        target: The figure whose Runaway the option anchors.

    Returns:
        ``(plain, cost)``, as :func:`_plain_line` gives them.
    """
    rules = magic_rules().runaway
    if target is None or target.runaway is None or rules is None:
        return "Anchor a runaway spell", ""
    spell = get_spell(target.runaway.spell_key)
    if target.combatant_id == actor.combatant_id:
        roll = rules.self_anchor
        return (
            f"Take back control of your runaway {spell.name}: {roll.dice} at or "
            f"under your {roll.attribute}",
            "",
        )
    roll = rules.helper_anchor
    cost = (
        f"{rules.helper_cost(target.runaway.original_cost)} mana, paid whether "
        "it holds or not"
    )
    if rules.helper_failure_catches_helper:
        cost += f"; if it fails, {spell.name} runs away from you too"
    return (
        f"Help {target.name} take back their runaway {spell.name}: {roll.dice} "
        f"at or under your {roll.attribute}",
        cost,
    )


def _when_mana_is_paid() -> str:
    """When a casting pays its mana, in words, from the canon's casting failure
    rules (tarmar-engine #46).

    A success pays it, and so does a failing natural total the canon names in
    ``casting_failure.mana_lost_totals``; every other failure keeps it
    (tarmar-studio #816). With no such totals, only a success pays.

    Returns:
        The phrase, e.g. "paid only if it succeeds or the roll is 12 or 13".
    """
    casting_failure = magic_rules().casting_failure
    lost_totals = (
        [] if casting_failure is None else sorted(casting_failure.mana_lost_totals)
    )
    if not lost_totals:
        return "paid only if it succeeds"
    named = " or ".join(str(total) for total in lost_totals)
    return f"paid only if it succeeds or the roll is {named}"


def _counter_line(mana: int, caster: CombatantState | None) -> tuple[str, str]:
    """A counter option's sentence and cost (tarmar-engine #31).

    The counter names a caster, not a spell, and meets whatever that caster
    casts this turn; its mana is spent whether a cast comes or not (the
    canon's ``committed_mana_is_spent``). The opposed roll and the bonus the
    commitment buys are the engine's, in the option's rationale.

    Args:
        mana: The mana the counter commits.
        caster: The enemy caster it opposes.

    Returns:
        ``(plain, cost)``, as :func:`_plain_line` gives them.
    """
    name = caster.name if caster is not None else "an enemy caster"
    return (
        f"Counter {name}'s spell this turn, committing {mana} mana",
        f"{mana} mana, spent whether {name} casts or not",
    )


def _plain_line(
    state: BattleState,
    actor: CombatantState,
    candidate: Candidate,
    target: CombatantState | None,
) -> tuple[str, str]:
    """The sentence a player reads for one option, and what it costs.

    Returns:
        ``(plain, cost)``: the sentence, and the cost in words ("1 Fatigue",
        "3 mana, paid only if it succeeds", "the Dagger leaves your hand"), or
        "" when it costs nothing. A spell's mana is paid on a success or on a
        failing total the canon names, and kept on any other failure
        (tarmar-studio #816, :func:`_when_mana_is_paid`).
    """
    option = actions.base_option(candidate.letter)
    aimed = target.name if target is not None else ""
    weapon = _weapon_phrase(actor)
    forecast = candidate.forecast
    plain, cost = "", ""
    # A mover the engine would walk nowhere says so (issue #57): its default
    # route toward the target is empty (``movement.default_approach``).
    stands = (
        option in STANDING_MOVERS
        and target is not None
        and not movement.default_approach(state, actor, candidate.letter, target).route
    )
    # A charge the engine's jog cannot bring beside its target says so
    # (issue #56): the engine's own row when every enemy is out of reach.
    charge = (
        movement.default_approach(state, actor, candidate.letter, target)
        if option == "b" and target is not None
        else None
    )
    falls_short = charge is not None and not movement.charge_reaches(charge)
    if option == "a":
        plain, cost = _mover_line(
            actor, candidate.letter, target, RUN_FATIGUE_COST, stands=stands
        )
    elif option == "sprint":
        plain, cost = _mover_line(
            actor, candidate.letter, target, SPRINT_FATIGUE_COST, stands=stands
        )
    elif option == "b" and charge is not None and falls_short:
        jog = movement.option_allowance(actor, candidate.letter)
        gait = _clear_gait(candidate.letter)
        plain = _no_attack_charge_line(
            aimed,
            _ends_from(f"{gait} of up to {jog} hexes", charge.distance, aimed)
            if charge.route
            else _no_nearer(gait, aimed),
        )
    elif option == "b":
        plain = _with_odds(
            f"Charge {aimed}: {movement.option_gait(candidate.letter)} up to "
            f"{movement.option_allowance(actor, candidate.letter)} hexes and "
            f"attack with {weapon}",
            forecast,
        )
    elif option == "c":
        plain = f"Dodge: +4 to your Target Number against {DODGE_DEFENDS_AGAINST}"
        if stands:
            plain += f", {_standing(_no_nearer(_clear_gait(candidate.letter), aimed))}"
        elif target is not None:
            plain += f", jogging toward {aimed}"
    elif option == "d":
        # DROP makes no move toward a target, so ``option_allowance`` refuses
        # it; its placed move is bounded by the engine's own gait for a
        # placed DROP (``movement.PLACED_GAITS``, "Jog or less").
        drop_gait = movement.PLACED_GAITS[option]
        plain = (
            f"Drop prone where you stand, or first {drop_gait} up to "
            f"{movement.gait_allowance(actor, drop_gait)} hexes to a hex you pick"
        )
    elif option == "e":
        spares = [spare for spare in actor.spare_weapons if spare.item_id]
        choice = best_weapon(actor, spares)
        plain = f"Ready the {choice.name}" if choice else "Ready another weapon"
    elif option == "f":
        plain = _with_odds(f"Shoot {aimed} with {weapon}", forecast)
        if actor.weapon.is_thrown and not actor.weapon.is_missile:
            cost = f"the {actor.weapon.name} leaves your hand"
    elif option in ("g", "p"):
        plain = "Stand up; it takes the whole turn"
    elif option in ("h", "r") and candidate.spell_key:
        spell = get_spell(candidate.spell_key)
        verb = (
            f"Press your countered {spell.name} again"
            if candidate.escalate
            else f"Cast {spell.name}"
        )
        plain = verb + (f" at {aimed}" if aimed else "")
        paid_when = _when_mana_is_paid()
        if candidate.push_mana:
            # A pushed copy of the cast (issue #960): the engine's menu offers
            # it, with the Control Roll's working in its rationale.
            plain += f", pushing {candidate.push_mana} more mana into it"
            cost = (
                f"{spell.level + candidate.push_mana} mana ({spell.level} and "
                f"{candidate.push_mana} pushed), {paid_when}; a success makes a "
                "Control Roll"
            )
        else:
            cost = f"{spell.level} mana, {paid_when}"
        if candidate.borrow:
            # A borrowing copy (tarmar-engine #28): the engine offers it for a
            # cast the caster cannot afford, with the debt roll's working in
            # its rationale.
            owed = borrowed_mana(actor, candidate)
            plain += f", borrowing the {owed} mana you lack"
            cost += (
                f"; paying it empties your pool and borrows the {owed} you lack "
                "as debt, and a debt roll follows the spell"
            )
        if candidate.escalate:
            # A cast pressing a countered spell again (tarmar-engine #31):
            # the engine marks it, for a player; the opposed roll comes again
            # if a counter meets it.
            cost += (
                "; a counter against it means another opposed roll, and "
                "countered again its mana is spent with no casting roll"
            )
    elif option == ANCHOR_OPTION:
        plain, cost = _anchor_line(actor, target)
    elif option == actions.COUNTER_OPTION and candidate.counter_mana is not None:
        plain, cost = _counter_line(candidate.counter_mana, target)
    elif option == "j":
        plain = _with_odds(f"Attack {aimed} with {weapon}", forecast)
    elif option == "k":
        plain = f"Defend: +4 to your Target Number against {DEFEND_DEFENDS_AGAINST}"
    elif option == "l":
        plain = _with_odds(f"One last shot at {aimed} with {weapon}", forecast)
    elif option == "m":
        spares = [
            spare
            for spare in actor.spare_weapons
            if spare.item_id and not spare.is_missile
        ]
        choice = best_weapon(actor, spares)
        plain = f"Change to the {choice.name}" if choice else "Change weapons"
        if actor.weapon.item_id:
            cost = f"the {actor.weapon.name} is dropped"
    elif option == "n":
        plain = "Step one hex away instead of attacking"
        # The engine steps away from the first adjacent enemy
        # (``TurnRunner.disengage_step``).
        stuck = _no_step_away(state, actor, _adjacent_enemies(state, actor))
        if stuck:
            plain += f": {stuck}"
    elif option == "o":
        plain = f"Try to grapple {aimed}: a hold pins them rather than hurting them"
    elif option == "q":
        choice = best_weapon(actor, weapons_in_reach(state, actor))
        plain = f"Pick up the {choice.name}" if choice else "Pick up a weapon"
        if actor.weapon.item_id:
            cost = f"the {actor.weapon.name} is dropped"
    elif option == "t":
        plain = _with_odds(
            f"Strike {aimed} in close with {_close_weapon_phrase(actor)}", forecast
        )
    elif option == "u":
        plain = "Try to draw your dagger: 3d6 at or under your DEX"
    elif option == "v" and actor.grappled_by is None:
        plain = "Break away from hand-to-hand: 4d6 at or under your DEX"
        # The engine steps away from the first partner it is in
        # hand-to-hand with (``TurnRunner.hth_disengage``).
        stuck = _no_step_away(state, actor, _hth_partners(state, actor))
        plain += f"; {stuck}" if stuck else ", then stand in a hex next to you"
    elif option == "v":
        plain = f"Struggle free of {aimed}: 4d6 at or under your DEX"
    elif option == "hold_still":
        plain = f"Hold still in {aimed}'s grip"
    elif option == "squeeze":
        plain = f"Squeeze {aimed}"
    elif option == "maintain":
        plain = f"Keep your hold on {aimed}"
    elif option == "release":
        plain = f"Let {aimed} go"
    else:
        plain = candidate.name.capitalize()
    if actions.is_yielded(candidate.letter) and (stands or falls_short):
        # The engine walks a yielded mover's route from the board as it is
        # in Final Movement, so a way opened by then is taken.
        plain += ", unless a way opens by Final Movement"
    elif actions.is_yielded(candidate.letter):
        plain += ", moving in Final Movement instead"
    # A cast or counter past a bound a Game Master lifted for this figure
    # (#37) says so, as its log line will: the engine's own answer, carried
    # on the option (``Candidate.lifted_bounds``).
    past = magic_bounds.past_by_ruling(candidate.lifted_bounds)
    if past:
        cost = f"{cost}; {past}" if cost else past
    return plain, cost


@dataclass
class DescribedCandidate(Candidate):
    """An engine candidate as a player weighs it (issue #788).

    The engine's own fields, and the payload it writes, plus whether it is
    the option the AI would take, and for a Tarmar figure the plain line,
    the cost in words and the target's name. Being a ``Candidate``, it is
    what the decider returns and what the engine logs, so the decision the
    log records lists exactly the options the player was offered.

    Attributes:
        ai_choice: The option the AI chose from the engine's own menu.
        plain: The sentence a player reads ("" when not described).
        cost: What it costs in words, or "" when nothing.
        target_name: Who it is aimed at, or "".
        described: Whether ``plain``, ``cost`` and ``target_name`` were
            written (a Tarmar figure's option, or a classic one read from
            its ``GameState``, #890); otherwise it carries the AI's mark
            alone.
        destinations: The hexes this option may place the figure at, from
            ``tarmar_engine.movement.legal_destinations`` on the live state
            (#819, #866); empty for an option that takes none and for a
            classic figure's.
        borrowed: The mana a borrowing cast borrows (tarmar-engine #28,
            :func:`borrowed_mana`), for the battle screen's borrow control
            to name; 0 for every other option, and then not in the payload.
    """

    ai_choice: bool = False
    plain: str = ""
    cost: str = ""
    target_name: str = ""
    described: bool = False
    destinations: tuple[tuple[int, int], ...] = ()
    borrowed: int = 0

    def to_payload(self) -> dict:
        payload = super().to_payload()
        payload["ai_choice"] = self.ai_choice
        if self.described:
            payload["plain"] = self.plain
            payload["cost"] = self.cost
            payload["target_name"] = self.target_name
        if self.borrowed:
            payload["borrowed"] = self.borrowed
        return payload

    def menu_payload(self) -> dict:
        """The payload the pending menu stores: :meth:`to_payload` plus the
        hexes this option may place the figure at (#819, #866), for the
        arena's picker and for checking a submitted hex. Only an option that
        takes one carries the key, and the decision the log records does
        not: the hex chosen is on its ``chosen``, and the board's every legal
        hex would swell each manual decision by hundreds of entries."""
        payload = self.to_payload()
        if self.destinations:
            payload["destinations"] = [list(cell) for cell in self.destinations]
        return payload


def menu_payload(candidate: Candidate) -> dict:
    """A pending menu entry: a described option's :meth:`DescribedCandidate.
    menu_payload`, any other candidate's own payload."""
    if isinstance(candidate, DescribedCandidate):
        return candidate.menu_payload()
    return candidate.to_payload()


def mana_short(actor: CombatantState, cost: int) -> int:
    """The mana a casting of ``cost`` would borrow: the cost less the pool.

    The studio's one reading of the shortfall (tarmar-engine #28), for the
    menu (:func:`borrowed_mana`) and the cast stage
    (``battle.services.caster_magic``) alike. The engine's
    :class:`~tarmar_engine.policy.Candidate` does not carry it; the engine
    borrows exactly this when the cast pays (``TurnRunner.cast_spell``,
    whose action payload's ``borrowed`` ``battle.tests.test_magic_seating``
    compares it to). 0 when the pool covers the cost.
    """
    return max(0, cost - actor.mana)


def borrowed_mana(actor: CombatantState, candidate: Candidate) -> int:
    """The mana a borrowing cast borrows: its whole cost less the pool.

    0 for a cast that does not borrow.
    """
    if not candidate.borrow or not candidate.spell_key:
        return 0
    return mana_short(actor, get_spell(candidate.spell_key).level + candidate.push_mana)


def _base_fields(candidate: Candidate) -> dict:
    """A candidate's own engine fields, to rebuild it as a described one.

    Every field the engine's ``Candidate`` declares, read from the dataclass
    itself, so a field the engine adds (``forecast``, tarmar-engine #22) is
    carried rather than silently dropped from the menu.
    """
    return {field.name: getattr(candidate, field.name) for field in fields(Candidate)}


def describe(
    state: BattleState,
    actor: CombatantState,
    candidate: Candidate,
    *,
    ai_choice: bool,
) -> DescribedCandidate:
    """One Tarmar option as the turn panel draws it.

    A classic figure's option is :func:`describe_classic`'s (issue #890).
    """
    target = (
        state.by_id(int(candidate.target_id))
        if candidate.target_id is not None
        else None
    )
    plain, cost = _plain_line(state, actor, candidate, target)
    return DescribedCandidate(
        **_base_fields(candidate),
        ai_choice=ai_choice,
        plain=plain,
        cost=cost,
        target_name=target.name if target is not None else "",
        described=True,
        borrowed=borrowed_mana(actor, candidate),
        # Every hex the engine would take for this option, from the engine's
        # own rule (tarmar_engine.movement), so the picker offers nothing the
        # engine refuses on this board.
        destinations=tuple(
            sorted(movement.legal_destinations(state, actor, candidate.letter))
        ),
    )


#: The classic options that strike a blow.
_CLASSIC_STRIKES = frozenset(
    {
        ClassicOption.ATTACK,
        ClassicOption.SHIFT_ATTACK,
        ClassicOption.CHARGE_ATTACK,
        ClassicOption.MISSILE_ATTACK,
        ClassicOption.ONE_LAST_SHOT,
    }
)


#: The classic options that move toward their target and say so, read as
#: standing when the engine's plan for them walks nowhere (issue #57).
_CLASSIC_STANDING_MOVERS = frozenset(
    {ClassicOption.MOVE, ClassicOption.HALF_MOVE, ClassicOption.DODGE}
)


#: What a classic grapple does, in the HTH ATTACK row's words and a
#: bare-handed CHARGE's (Melee p.17).
_CLASSIC_GRAPPLE_WORDS = "move into their hex and fight hand to hand"


@dataclass(frozen=True)
class _ClassicChargeBlow:
    """A classic charge as the engine would make it (issue #56).

    Attributes:
        path: The hexes the charge walks.
        struck: The foe it attacks, from the live game, or None when no foe
            is in reach where it ends.
        grapples: Whether that attack is a grapple (bare hands,
            ``BlowKind.HAND_TO_HAND``, settled by the defender's roll) rather
            than a weapon's blow.
        forecast: The weapon blow's forecast from where the charge ends
            (``GameState.attack_forecast`` on the rehearsal), its facing bonus
            and a pole's charge die included; None for a grapple or no blow.
    """

    path: list[Hex]
    struck: Figure | None
    grapples: bool
    forecast: AttackForecast | None


def _classic_charge_blow(
    game: GameState, figure: Figure, candidate: Candidate
) -> _ClassicChargeBlow:
    """The classic engine's charge, rehearsed: where it moves, whom it strikes,
    and what the blow faces from there.

    The engine's own rules throughout, played on a copy of the game (its
    snapshot contract, ``to_dict``/``from_dict``) so the live one is
    untouched: the plan the engine enacts for this charge
    (``classic.policy.plan_for``), applied through its own verbs
    (``classic.ai.apply``); the attack the combat phase would declare from
    where it ends (``classic.ai.attack_blow``, tarmar-engine #54: a weapon's
    front hexes and a pole's jab, any hex of a multi-hex foe, a grapple with
    bare hands); and, for a weapon's blow, the forecast from there
    (``GameState.attack_forecast``, tarmar-engine #23). This is
    ``classic.ai.planned_blow``'s rehearsal, made here rather than through it
    because the forecast must be asked of the same rehearsed board, which
    ``planned_blow`` keeps to itself. A charge may strike a foe other than the
    one it closes on, or nobody (issue #56).

    Args:
        game: The live game the menu was built from.
        figure: The figure choosing.
        candidate: Its CHARGE ATTACK option.

    Returns:
        The charge's path, the foe it attacks, whether it grapples, and the
        blow's forecast.

    Raises:
        LookupError: if the copy of the game has lost the figure, or the live
            game the foe it strikes, which the snapshot contract rules out
            (as ``classic.ai.planned_blow`` raises).
    """
    plan = classic_policy.plan_for(game, figure, candidate)
    rehearsal = GameState.from_dict(game.to_dict())
    charger = _classic_figure(rehearsal, figure.uid)
    if charger is None:
        raise LookupError(f"{figure.name} is missing from a copy of the game")
    classic_ai.apply(rehearsal, charger, plan)
    blow = classic_ai.attack_blow(rehearsal, charger)
    if blow is None:
        return _ClassicChargeBlow(list(plan.path), None, False, None)
    grapples = blow.kind is BlowKind.HAND_TO_HAND
    forecast = (
        None
        if grapples
        else _classic_forecast(
            rehearsal, charger, ClassicOption.CHARGE_ATTACK, blow.target
        )
    )
    struck = _classic_figure(game, blow.target.uid)
    if struck is None:
        raise LookupError(f"{blow.target.name} is missing from the game")
    return _ClassicChargeBlow(list(plan.path), struck, grapples, forecast)


def _classic_dice(damage: DamageDice) -> str:
    """ "2d6", "1d6+2", "1d6-1": a classic damage expression in the page's words."""
    modifier = f"{damage.modifier:+d}" if damage.modifier else ""
    return f"{damage.count}d{CLASSIC_DIE_FACES}{modifier}"


def _classic_figure(game: GameState, uid: object) -> Figure | None:
    """The classic figure whose uid a candidate names, or None."""
    if not uid:
        return None
    return next((figure for figure in game.figures if figure.uid == uid), None)


def _classic_forecast(
    game: GameState, figure: Figure, option: ClassicOption, target: Figure
) -> AttackForecast | None:
    """The classic engine's forecast of ``figure``'s weapon blow at ``target``
    under ``option`` from where both stand (``GameState.attack_forecast``,
    tarmar-engine #23), or None where there is no to-hit roll to forecast:
    no ready weapon (bare hands grapple, settled by the defender's roll) or a
    figure off the field, the two cases the engine refuses.
    """
    if (
        figure.ready_weapon is None
        or figure.position is None
        or target.position is None
    ):
        return None
    return game.attack_forecast(figure, target, option)


def _classic_strike(forecast: AttackForecast | None) -> str:
    """ "10 or less on 3d6 to hit (about 50%), 2d6 less 2 armour (about 5 a
    hit)", or "" with no forecast.

    Every figure is the engine's (``AttackForecast``): the adjDX the blow
    would be declared at, with the situation, range and facing; the dice (4
    against a defender or a dodger); the chance through the engine's own
    table of special totals; the weapon's dice and a pole's extra charge
    dice; the stops of armour, shield, protection and a ready main-gauche's
    parry (#131); and what a hit takes off on average.
    """
    if forecast is None:
        return ""
    odds = (
        f"{forecast.needed} or less on {forecast.dice_count}d{CLASSIC_DIE_FACES} "
        f"to hit (about {round(100 * forecast.hit_chance)}%)"
    )
    armour = _armour_less(forecast.armour_stops)
    # A pole's charge die is its own clause, so the armour after it reads as
    # coming off the whole: "1d6+1, and 1d6 more for the charge, less 2
    # armour".
    extra = (
        f", and {forecast.extra_dice}d{CLASSIC_DIE_FACES} more for the charge"
        f"{',' if armour else ''}"
        if forecast.extra_dice
        else ""
    )
    return (
        f"{odds}, {_classic_dice(forecast.weapon.damage)}{extra}{armour} "
        f"({_average_hit(forecast.expected_damage)} a hit)"
    )


def _classic_plain_line(
    game: GameState, figure: Figure, candidate: Candidate, target: Figure | None
) -> tuple[str, str]:
    """The sentence a player reads for one classic option, and what it costs.

    The classic counterpart of :func:`_plain_line` (issue #890). Only a spell
    costs anything in a classic turn: the ST it invests, which is also the
    caster's hit points.
    """
    option = ClassicOption(candidate.letter)
    aimed = target.name if target is not None else ""
    toward = f" toward {aimed}" if aimed else ""
    weapon = figure.ready_weapon
    wielding = f"the {weapon.name}" if weapon is not None else "bare hands"
    budget = game.rules.movement_budget(
        figure.movement_allowance, classic_spec(option).movement_cap
    )
    cost = ""
    # A move the engine would not make is read as standing (issue #57): the
    # classic engine enacts exactly the plan ``plan_for`` derives, and an
    # empty path is no move ("boxed in", ``classic.ai._closing_move``).
    plan = (
        classic_policy.plan_for(game, figure, candidate)
        if option in _CLASSIC_STANDING_MOVERS and target is not None
        else None
    )
    stands = plan is not None and not plan.path
    move = f"move of up to {budget} hexes"
    no_nearer = _no_nearer(move, aimed)
    # A charge says whom the engine's own charge strikes, or that it strikes
    # nobody (issue #56).
    charge_path: list[Hex] = []
    struck: Figure | None = target
    grapples = False
    forecast: AttackForecast | None = None
    if option == ClassicOption.CHARGE_ATTACK and target is not None:
        # Forecast from where the charge ends, on its rehearsal.
        blow = _classic_charge_blow(game, figure, candidate)
        charge_path, struck, grapples = blow.path, blow.struck, blow.grapples
        forecast = blow.forecast
    elif option in _CLASSIC_STRIKES and target is not None:
        forecast = _classic_forecast(game, figure, option, target)
    if struck is None and target is not None and target.position is not None:
        reach = f"with no foe in reach of {wielding}"
        reason = (
            _ends_from(
                move,
                game.arena.layout.distance(charge_path[-1], target.position),
                aimed,
            )
            + f", {reach}"
            if charge_path
            else f"{no_nearer}, {reach}"
        )
        plain = _no_attack_charge_line(aimed, reason)
    elif struck is not None and grapples:
        # A grapple is settled by the defender's roll, not a to-hit roll, so
        # it carries no odds (Melee p.17).
        whom = "them" if struck is target else struck.name
        lead = f"Charge {aimed}" if struck is target else f"Charge toward {aimed}"
        plain = (
            f"{lead}: move up to {budget} hexes and grapple {whom}: "
            f"{_CLASSIC_GRAPPLE_WORDS}"
        )
    elif struck is not None and target is not None and struck is not target:
        odds = _classic_strike(forecast)
        sentence = (
            f"Charge toward {aimed}: move up to {budget} hexes and attack "
            f"{struck.name} with {wielding}"
        )
        plain = f"{sentence}: {odds}" if odds else sentence
    elif option in _CLASSIC_STRIKES and target is not None:
        verbs = {
            ClassicOption.ATTACK: f"Attack {aimed} with {wielding}",
            ClassicOption.SHIFT_ATTACK: (
                f"Shift a hex and attack {aimed} with {wielding}"
            ),
            ClassicOption.CHARGE_ATTACK: (
                f"Charge {aimed}: move up to {budget} hexes and attack with {wielding}"
            ),
            ClassicOption.MISSILE_ATTACK: f"Shoot {aimed} with {wielding}",
            ClassicOption.ONE_LAST_SHOT: f"One last shot at {aimed} with {wielding}",
        }
        odds = _classic_strike(forecast)
        plain = f"{verbs[option]}: {odds}" if odds else verbs[option]
    elif option in (ClassicOption.MOVE, ClassicOption.HALF_MOVE) and stands:
        plain = _stand_line(no_nearer)
    elif option == ClassicOption.MOVE:
        plain = f"Move up to {budget} hexes{toward}"
    elif option == ClassicOption.HALF_MOVE:
        plain = f"Move up to {budget} hexes{toward} without attacking"
    elif option == ClassicOption.DODGE:
        dice = game.rules.resolution.attack_dice_count(
            dodging=True, defending=False, ranged=True
        )
        how = (
            _standing(no_nearer)
            if stands
            else f"while moving up to {budget} hexes{toward}"
        )
        plain = (
            f"Dodge {how}: missiles and thrown weapons roll "
            f"{dice}d{CLASSIC_DIE_FACES} to hit you, not "
            f"{THREE_DICE}d{CLASSIC_DIE_FACES}"
        )
    elif option == ClassicOption.SHIFT_DEFEND:
        dice = game.rules.resolution.attack_dice_count(
            dodging=False, defending=True, ranged=False
        )
        plain = (
            f"Shift a hex and defend: blows in melee roll {dice}d"
            f"{CLASSIC_DIE_FACES} to hit you, not "
            f"{THREE_DICE}d{CLASSIC_DIE_FACES}"
        )
    elif option in (ClassicOption.READY_WEAPON, ClassicOption.CHANGE_WEAPONS):
        # The weapon the engine takes up for this option, as it will enact it.
        taken = classic_policy.plan_for(game, figure, candidate).ready
        if option == ClassicOption.READY_WEAPON:
            plain = f"Ready the {taken}" if taken else "Ready another weapon"
        else:
            plain = f"Change to the {taken}" if taken else "Change weapons"
            if weapon is not None:
                cost = f"the {weapon.name} is dropped"
    elif option == ClassicOption.PICK_UP:
        taken = classic_policy.plan_for(game, figure, candidate).ready
        plain = f"Pick up the {taken}" if taken else "Pick up a weapon in reach"
    elif option == ClassicOption.STAND_UP:
        plain = "Stand up; it takes the whole turn"
    elif option == ClassicOption.CRAWL:
        plain = f"Crawl up to {budget} hexes, staying down"
    elif option == ClassicOption.DISENGAGE:
        plain = "Step away from the enemies engaging you"
    elif option == ClassicOption.HTH_ATTACK:
        plain = f"Grapple {aimed}: {_CLASSIC_GRAPPLE_WORDS}"
    elif option == ClassicOption.CAST and candidate.spell_key:
        offers = {
            (spell.id, subject.uid): invested
            for spell, subject, invested in classic_ai.castable_spells(game, figure)
        }
        spell_name = candidate.name.removeprefix(f"{classic_spec(option).name} ")
        plain = f"Cast {spell_name}" + (f" at {aimed}" if aimed else "")
        invested = offers.get((candidate.spell_key, target.uid if target else ""))
        if invested is not None:
            cost = f"{invested} ST"
    elif option == ClassicOption.GO_PRONE:
        plain = "Drop prone where you stand"
    elif option == ClassicOption.KNEEL:
        plain = "Drop to one knee"
    elif option == ClassicOption.DO_NOTHING:
        plain = "Hold where you are and do nothing this turn"
    elif option == ClassicOption.PASS:
        plain = "Wait: choose after the others have moved"
    else:
        plain = candidate.name.capitalize()
    return plain, cost


def describe_classic(
    game: GameState, figure: Figure, candidate: Candidate, *, ai_choice: bool
) -> DescribedCandidate:
    """One classic option as the turn panel draws it (issue #890)."""
    target = _classic_figure(game, candidate.target_id)
    plain, cost = _classic_plain_line(game, figure, candidate, target)
    return DescribedCandidate(
        **_base_fields(candidate),
        ai_choice=ai_choice,
        plain=plain,
        cost=cost,
        target_name=target.name if target is not None else "",
        described=True,
    )


def player_menu(profile_name: str, state, actor) -> list[Candidate]:
    """The menu a seated player chooses from, every option described.

    Built here, from the live state, through the profile's own AI with every
    target (:func:`battle.profiles.decider_for` with ``every_target``), the
    one place a policy is paired with its magic rules: for a Tarmar figure
    the engine's every-target menu (tarmar-engine #21), each option given its
    plain line (:func:`describe`); for a classic figure, the engine's own
    menu with each option described from the classic state
    (:func:`describe_classic`, issue #890). Either way the AI's own pick, the
    decision's ``chosen``, is marked, since the menu may hold an option aimed
    elsewhere that scores above it. Every entry is a
    :class:`DescribedCandidate`; the list is typed as the engine's
    ``Candidate`` because it becomes a ``Decision``'s.

    Args:
        profile_name: The battle's rules profile.
        state: The live state the menu was built from.
        actor: The figure choosing.
    """
    advice = profiles.decider_for(profile_name, every_target=True)(state, actor)
    described = describe if profile_name == TARMAR_PROFILE else describe_classic
    return [
        described(state, actor, candidate, ai_choice=candidate is advice.chosen)
        for candidate in advice.candidates
    ]
