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
from .magic_rules import magic_rules, totals_in_words
from .persistence import TARMAR_PROFILE
from .wording import ClassicMenuWords, TarmarMenuWords, TurnMenuWords, words


def _shared() -> TurnMenuWords:
    """The menu's words both profiles use: every sentence a player reads on
    the menu is configuration (``config/battle-rulings.toml``, issue #90)."""
    return words().turn_menu


def _tarmar() -> TarmarMenuWords:
    """A Tarmar figure's own menu words (issue #90)."""
    return words().tarmar_menu


def _classic() -> ClassicMenuWords:
    """A classic figure's own menu words (issue #90)."""
    return words().classic_menu


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
    tarmar = _tarmar()
    figures = {
        "faces": forecast.winning_faces,
        "sides": DIE_FACES,
        "dice": forecast.damage_expression,
        "average": _average_hit(forecast.expected_damage),
    }
    if not forecast.armour_stops:
        return tarmar.odds.format(**figures)
    return tarmar.odds_armoured.format(stops=forecast.armour_stops, **figures)


def _average_hit(expected_damage: float) -> str:
    """ "about 4", or "under 1": what a hit does on average, in either
    profile's odds, from the engine's own expected damage."""
    shared = _shared()
    if expected_damage < 0.5:
        return shared.under_one
    return shared.about.format(damage=round(expected_damage))


def _weapon_words(weapon_name: str | None) -> str:
    """ "the Saber", or "bare hands" for ``None``, in either profile."""
    shared = _shared()
    if weapon_name is None:
        return shared.bare_hands
    return shared.the_weapon.format(weapon=weapon_name)


def _weapon_phrase(actor: CombatantState) -> str:
    """ "the Saber", or "bare hands" for a figure with no weapon in hand."""
    return _weapon_words(actor.weapon.name if actor.weapon.item_id else None)


def _close_weapon_phrase(actor: CombatantState) -> str:
    """What an HTH strike (t) is made with: "Bare hands or dagger", so a
    sword in hand strikes with bare hands (tarmar-engine #19)."""
    return _weapon_words(actor.weapon.name if actor.weapon.hth_usable else None)


def _joined_odds(sentence: str, odds: str) -> str:
    """``sentence`` and its odds, or the sentence alone when there are none,
    in either profile."""
    return _shared().with_odds.format(line=sentence, odds=odds) if odds else sentence


def _with_odds(sentence: str, forecast: Forecast | None) -> str:
    return _joined_odds(sentence, _odds(forecast))


def _ends_from(move: str, distance: int, aimed: str) -> str:
    """ "a clear jog of up to 7 hexes ends 2 hexes from Foe": where a move
    that falls short stops, in either profile."""
    return _shared().ends_from.format(
        move=move, distance=combat_math.hexes_text(distance), aimed=aimed
    )


def _no_attack_charge_line(aimed: str, reason: str) -> str:
    """The sentence for a charge that strikes nobody, in either profile
    (issue #56)."""
    return _shared().no_attack_charge.format(aimed=aimed, reason=reason)


def _clear_gait(option: str) -> str:
    """ "clear jog": the move a Tarmar option makes, as :func:`_no_nearer`
    names it, at the engine's gait for it (``movement.option_gait``)."""
    return _tarmar().clear_gait.format(gait=movement.option_gait(option))


def _no_nearer(move: str, aimed: str) -> str:
    """ "no clear jog gets nearer to Foe": why a figure's move stays put."""
    return _shared().no_nearer.format(move=move, aimed=aimed)


def _standing(reason: str) -> str:
    """ "standing, since ...": a DODGE that does not move, in either profile."""
    return _shared().standing.format(reason=reason)


def _stand_line(reason: str) -> str:
    """The sentence for a move the engine would not make, in either profile."""
    return _shared().stand.format(reason=reason)


def _ready_line(weapon_name: str | None) -> str:
    """ "Ready the Dagger", or "Ready another weapon", in either profile."""
    shared = _shared()
    if not weapon_name:
        return shared.ready_another
    return shared.ready.format(weapon=weapon_name)


def _change_line(weapon_name: str | None) -> str:
    """ "Change to the Dagger", or "Change weapons", in either profile."""
    shared = _shared()
    if not weapon_name:
        return shared.change_weapons
    return shared.change_to.format(weapon=weapon_name)


def _cast_line(spell_name: str, aimed: str) -> str:
    """ "Cast Fire Missile at Foe", or with no one aimed at, in either
    profile."""
    shared = _shared()
    if aimed:
        return shared.cast_at.format(spell=spell_name, aimed=aimed)
    return shared.cast.format(spell=spell_name)


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
    return _tarmar().no_step_away.format(threat=threats[0].name)


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
    tarmar = _tarmar()
    if target is not None and stands:
        return (
            _stand_line(_no_nearer(_clear_gait(letter), target.name)),
            tarmar.fatigue_if_moving.format(fatigue=fatigue),
        )
    aimed = target.name if target is not None else tarmar.nearest_enemy
    sentence = tarmar.sprint if actions.base_option(letter) == "sprint" else tarmar.run
    return (
        sentence.format(hexes=movement.option_allowance(actor, letter), aimed=aimed),
        tarmar.fatigue.format(fatigue=fatigue),
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
    tarmar = _tarmar()
    rules = magic_rules().runaway
    if target is None or target.runaway is None or rules is None:
        return tarmar.anchor, ""
    spell = get_spell(target.runaway.spell_key)
    if target.combatant_id == actor.combatant_id:
        roll = rules.self_anchor
        return (
            tarmar.self_anchor.format(
                spell=spell.name, dice=roll.dice, attribute=roll.attribute
            ),
            "",
        )
    roll = rules.helper_anchor
    cost = tarmar.helper_anchor_cost.format(
        mana=rules.helper_cost(target.runaway.original_cost)
    )
    if rules.helper_failure_catches_helper:
        cost = tarmar.helper_caught.format(cost=cost, spell=spell.name)
    return (
        tarmar.helper_anchor.format(
            name=target.name,
            spell=spell.name,
            dice=roll.dice,
            attribute=roll.attribute,
        ),
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
    tarmar = _tarmar()
    casting_failure = magic_rules().casting_failure
    lost_totals = (
        frozenset() if casting_failure is None else casting_failure.mana_lost_totals
    )
    if not lost_totals:
        return tarmar.paid_on_success
    totals = totals_in_words(lost_totals, or_word=words().common.or_word)
    return tarmar.paid_on_success_or_total.format(totals=totals)


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
    tarmar = _tarmar()
    name = caster.name if caster is not None else tarmar.an_enemy_caster
    return (
        tarmar.counter.format(name=name, mana=mana),
        tarmar.counter_cost.format(mana=mana, name=name),
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
    shared, tarmar = _shared(), _tarmar()
    if option == "a":
        plain, cost = _mover_line(
            actor, candidate.letter, target, RUN_FATIGUE_COST, stands=stands
        )
    elif option == "sprint":
        plain, cost = _mover_line(
            actor, candidate.letter, target, SPRINT_FATIGUE_COST, stands=stands
        )
    elif option == "b" and charge is not None and falls_short:
        gait = _clear_gait(candidate.letter)
        plain = _no_attack_charge_line(
            aimed,
            _ends_from(
                shared.reach.format(
                    move=gait,
                    hexes=movement.option_allowance(actor, candidate.letter),
                ),
                charge.distance,
                aimed,
            )
            if charge.route
            else _no_nearer(gait, aimed),
        )
    elif option == "b":
        plain = _with_odds(
            tarmar.charge.format(
                aimed=aimed,
                gait=movement.option_gait(candidate.letter),
                hexes=movement.option_allowance(actor, candidate.letter),
                weapon=weapon,
            ),
            forecast,
        )
    elif option == "c":
        plain = tarmar.dodge.format(guards=tarmar.dodge_guards)
        if stands:
            plain = tarmar.dodge_standing.format(
                line=plain,
                standing=_standing(_no_nearer(_clear_gait(candidate.letter), aimed)),
            )
        elif target is not None:
            plain = tarmar.dodge_toward.format(line=plain, aimed=aimed)
    elif option == "d":
        # DROP makes no move toward a target, so ``option_allowance`` refuses
        # it; its placed move is bounded by the engine's own gait for a
        # placed DROP (``movement.PLACED_GAITS``, "Jog or less").
        drop_gait = movement.PLACED_GAITS[option]
        plain = tarmar.drop.format(
            gait=drop_gait, hexes=movement.gait_allowance(actor, drop_gait)
        )
    elif option == "e":
        spares = [spare for spare in actor.spare_weapons if spare.item_id]
        choice = best_weapon(actor, spares)
        plain = _ready_line(choice.name if choice else None)
    elif option == "f":
        plain = _with_odds(shared.shoot.format(aimed=aimed, weapon=weapon), forecast)
        if actor.weapon.is_thrown and not actor.weapon.is_missile:
            cost = tarmar.leaves_hand.format(weapon=actor.weapon.name)
    elif option in ("g", "p"):
        plain = shared.stand_up
    elif option in ("h", "r") and candidate.spell_key:
        spell = get_spell(candidate.spell_key)
        if not candidate.escalate:
            plain = _cast_line(spell.name, aimed)
        elif aimed:
            plain = tarmar.press_again_at.format(spell=spell.name, aimed=aimed)
        else:
            plain = tarmar.press_again.format(spell=spell.name)
        paid_when = _when_mana_is_paid()
        if candidate.push_mana:
            # A pushed copy of the cast (issue #960): the engine's menu offers
            # it, with the Control Roll's working in its rationale.
            plain = tarmar.pushing.format(line=plain, pushed=candidate.push_mana)
            cost = tarmar.push_cost.format(
                total=spell.level + candidate.push_mana,
                level=spell.level,
                pushed=candidate.push_mana,
                paid=paid_when,
            )
        else:
            cost = tarmar.cast_cost.format(mana=spell.level, paid=paid_when)
        if candidate.borrow:
            # A borrowing copy (tarmar-engine #28): the engine offers it for a
            # cast the caster cannot afford, with the debt roll's working in
            # its rationale.
            owed = borrowed_mana(actor, candidate)
            plain = tarmar.borrowing.format(line=plain, owed=owed)
            cost = tarmar.borrowing_cost.format(cost=cost, owed=owed)
        if candidate.escalate:
            # A cast pressing a countered spell again (tarmar-engine #31):
            # the engine marks it, for a player; the opposed roll comes again
            # if a counter meets it.
            cost = tarmar.pressing_cost.format(cost=cost)
    elif option == ANCHOR_OPTION:
        plain, cost = _anchor_line(actor, target)
    elif option == actions.COUNTER_OPTION and candidate.counter_mana is not None:
        plain, cost = _counter_line(candidate.counter_mana, target)
    elif option == "j":
        plain = _with_odds(shared.attack.format(aimed=aimed, weapon=weapon), forecast)
    elif option == "k":
        plain = tarmar.defend.format(guards=tarmar.defend_guards)
    elif option == "l":
        plain = _with_odds(
            shared.last_shot.format(aimed=aimed, weapon=weapon), forecast
        )
    elif option == "m":
        spares = [
            spare
            for spare in actor.spare_weapons
            if spare.item_id and not spare.is_missile
        ]
        choice = best_weapon(actor, spares)
        plain = _change_line(choice.name if choice else None)
        if actor.weapon.item_id:
            cost = shared.dropped.format(weapon=actor.weapon.name)
    elif option == "n":
        plain = tarmar.step_away
        # The engine steps away from the first adjacent enemy
        # (``TurnRunner.disengage_step``).
        stuck = _no_step_away(state, actor, _adjacent_enemies(state, actor))
        if stuck:
            plain = tarmar.step_away_stuck.format(line=plain, stuck=stuck)
    elif option == "o":
        plain = tarmar.grapple.format(aimed=aimed)
    elif option == "q":
        choice = best_weapon(actor, weapons_in_reach(state, actor))
        plain = (
            shared.pick_up.format(weapon=choice.name) if choice else tarmar.pick_up_any
        )
        if actor.weapon.item_id:
            cost = shared.dropped.format(weapon=actor.weapon.name)
    elif option == "t":
        plain = _with_odds(
            tarmar.close_strike.format(aimed=aimed, weapon=_close_weapon_phrase(actor)),
            forecast,
        )
    elif option == "u":
        plain = tarmar.draw_dagger
    elif option == "v" and actor.grappled_by is None:
        # The engine steps away from the first partner it is in
        # hand-to-hand with (``TurnRunner.hth_disengage``).
        stuck = _no_step_away(state, actor, _hth_partners(state, actor))
        plain = (
            tarmar.break_away_stuck.format(line=tarmar.break_away, stuck=stuck)
            if stuck
            else tarmar.break_away_steps.format(line=tarmar.break_away)
        )
    elif option == "v":
        plain = tarmar.struggle_free.format(aimed=aimed)
    elif option == "hold_still":
        plain = tarmar.hold_still.format(aimed=aimed)
    elif option == "squeeze":
        plain = tarmar.squeeze.format(aimed=aimed)
    elif option == "maintain":
        plain = tarmar.maintain.format(aimed=aimed)
    elif option == "release":
        plain = tarmar.release.format(aimed=aimed)
    else:
        plain = candidate.name.capitalize()
    if actions.is_yielded(candidate.letter) and (stands or falls_short):
        # The engine walks a yielded mover's route from the board as it is
        # in Final Movement, so a way opened by then is taken.
        plain = tarmar.yielded_blocked.format(line=plain)
    elif actions.is_yielded(candidate.letter):
        plain = tarmar.yielded.format(line=plain)
    # A cast or counter past a bound a Game Master lifted for this figure
    # (#37) says so, as its log line will: the engine's own answer, carried
    # on the option (``Candidate.lifted_bounds``).
    past = magic_bounds.past_by_ruling(candidate.lifted_bounds)
    if past:
        cost = tarmar.cost_past_ruling.format(cost=cost, past=past) if cost else past
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


def _classic_roll(dice_count: int) -> str:
    """ "3d6": a classic roll of ``dice_count`` plain dice."""
    return f"{dice_count}d{CLASSIC_DIE_FACES}"


def _classic_dice(damage: DamageDice) -> str:
    """ "2d6", "1d6+2", "1d6-1": a classic damage expression in the page's words."""
    modifier = f"{damage.modifier:+d}" if damage.modifier else ""
    return f"{_classic_roll(damage.count)}{modifier}"


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
    classic = _classic()
    odds = classic.odds.format(
        needed=forecast.needed,
        dice=_classic_roll(forecast.dice_count),
        chance=round(100 * forecast.hit_chance),
    )
    damage = _classic_dice(forecast.weapon.damage)
    if forecast.extra_dice:
        # A pole's charge die is its own clause, so the armour after it reads
        # as coming off the whole: "1d6+1, and 1d6 more for the charge, less 2
        # armour".
        charge_dice = (
            classic.charge_dice_armoured
            if forecast.armour_stops
            else classic.charge_dice
        )
        damage = charge_dice.format(
            damage=damage, extra=_classic_roll(forecast.extra_dice)
        )
    average = _average_hit(forecast.expected_damage)
    if forecast.armour_stops:
        return classic.odds_damage_armoured.format(
            odds=odds, damage=damage, stops=forecast.armour_stops, average=average
        )
    return classic.odds_damage.format(odds=odds, damage=damage, average=average)


def _classic_plain_line(
    game: GameState, figure: Figure, candidate: Candidate, target: Figure | None
) -> tuple[str, str]:
    """The sentence a player reads for one classic option, and what it costs.

    The classic counterpart of :func:`_plain_line` (issue #890). Only a spell
    costs anything in a classic turn: the ST it invests, which is also the
    caster's hit points.
    """
    shared, classic = _shared(), _classic()
    option = ClassicOption(candidate.letter)
    aimed = target.name if target is not None else ""
    weapon = figure.ready_weapon
    wielding = _weapon_words(weapon.name if weapon is not None else None)
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
    move = shared.reach.format(move=classic.move_word, hexes=budget)
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
        reason = (
            _ends_from(
                move,
                game.arena.layout.distance(charge_path[-1], target.position),
                aimed,
            )
            if charge_path
            else no_nearer
        )
        plain = _no_attack_charge_line(
            aimed, classic.no_foe_in_reach.format(reason=reason, weapon=wielding)
        )
    elif struck is not None and grapples:
        # A grapple is settled by the defender's roll, not a to-hit roll, so
        # it carries no odds (Melee p.17).
        if struck is target:
            plain = classic.charge_grapple.format(
                aimed=aimed, hexes=budget, grapple=classic.grapple_words
            )
        else:
            plain = classic.charge_grapple_another.format(
                aimed=aimed,
                hexes=budget,
                struck=struck.name,
                grapple=classic.grapple_words,
            )
    elif struck is not None and target is not None and struck is not target:
        plain = _joined_odds(
            classic.charge_another.format(
                aimed=aimed, hexes=budget, struck=struck.name, weapon=wielding
            ),
            _classic_strike(forecast),
        )
    elif option in _CLASSIC_STRIKES and target is not None:
        verbs = {
            ClassicOption.ATTACK: shared.attack,
            ClassicOption.SHIFT_ATTACK: classic.shift_attack,
            ClassicOption.CHARGE_ATTACK: classic.charge,
            ClassicOption.MISSILE_ATTACK: shared.shoot,
            ClassicOption.ONE_LAST_SHOT: shared.last_shot,
        }
        plain = _joined_odds(
            verbs[option].format(aimed=aimed, hexes=budget, weapon=wielding),
            _classic_strike(forecast),
        )
    elif option in (ClassicOption.MOVE, ClassicOption.HALF_MOVE) and stands:
        plain = _stand_line(no_nearer)
    elif option == ClassicOption.MOVE:
        plain = (
            classic.move_toward.format(hexes=budget, aimed=aimed)
            if aimed
            else classic.move.format(hexes=budget)
        )
    elif option == ClassicOption.HALF_MOVE:
        plain = (
            classic.half_move_toward.format(hexes=budget, aimed=aimed)
            if aimed
            else classic.half_move.format(hexes=budget)
        )
    elif option == ClassicOption.DODGE:
        dice = game.rules.resolution.attack_dice_count(
            dodging=True, defending=False, ranged=True
        )
        if stands:
            how = _standing(no_nearer)
        elif aimed:
            how = classic.dodge_moving_toward.format(hexes=budget, aimed=aimed)
        else:
            how = classic.dodge_moving.format(hexes=budget)
        plain = classic.dodge.format(
            how=how,
            dice=_classic_roll(dice),
            plain_dice=_classic_roll(THREE_DICE),
        )
    elif option == ClassicOption.SHIFT_DEFEND:
        dice = game.rules.resolution.attack_dice_count(
            dodging=False, defending=True, ranged=False
        )
        plain = classic.shift_defend.format(
            dice=_classic_roll(dice), plain_dice=_classic_roll(THREE_DICE)
        )
    elif option in (ClassicOption.READY_WEAPON, ClassicOption.CHANGE_WEAPONS):
        # The weapon the engine takes up for this option, as it will enact it.
        taken = classic_policy.plan_for(game, figure, candidate).ready
        if option == ClassicOption.READY_WEAPON:
            plain = _ready_line(taken)
        else:
            plain = _change_line(taken)
            if weapon is not None:
                cost = shared.dropped.format(weapon=weapon.name)
    elif option == ClassicOption.PICK_UP:
        taken = classic_policy.plan_for(game, figure, candidate).ready
        plain = shared.pick_up.format(weapon=taken) if taken else classic.pick_up_any
    elif option == ClassicOption.STAND_UP:
        plain = shared.stand_up
    elif option == ClassicOption.CRAWL:
        plain = classic.crawl.format(hexes=budget)
    elif option == ClassicOption.DISENGAGE:
        plain = classic.disengage
    elif option == ClassicOption.HTH_ATTACK:
        plain = classic.hth_attack.format(aimed=aimed, grapple=classic.grapple_words)
    elif option == ClassicOption.CAST and candidate.spell_key:
        offers = {
            (spell.id, subject.uid): invested
            for spell, subject, invested in classic_ai.castable_spells(game, figure)
        }
        spell_name = candidate.name.removeprefix(f"{classic_spec(option).name} ")
        plain = _cast_line(spell_name, aimed)
        invested = offers.get((candidate.spell_key, target.uid if target else ""))
        if invested is not None:
            cost = classic.cast_cost.format(strength=invested)
    elif option == ClassicOption.GO_PRONE:
        plain = classic.go_prone
    elif option == ClassicOption.KNEEL:
        plain = classic.kneel
    elif option == ClassicOption.DO_NOTHING:
        plain = classic.do_nothing
    elif option == ClassicOption.PASS:
        plain = classic.pass_turn
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
