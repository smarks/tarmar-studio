"""What a seated player is offered on their turn, and how it reads.

Two things the engine's candidate menu does not do for a person choosing by
hand, both done here, at the pause, from the live state the menu was built
from:

* **Every legal target** (issue #775). The engine's policy aims each attack
  where the AI would: ATTACK at the adjacent enemy it scores best, CHARGE,
  MISSILE and ONE LAST SHOT at the nearest, a hand-to-hand strike or grapple
  at one of the enemies an entry condition admits. So a player offered one
  "Strike Gray Wolf" could not strike the other wolf beside them, or shoot
  the wizard at the back. :func:`every_target` adds the same option aimed at
  each other legal target, scored and explained by the engine's own forecast
  for that target. The engine's candidate already carries a target, and the
  engine resolves whatever target the chosen candidate names (and refuses a
  swing that no longer reaches, in its own log line), so no engine change is
  needed for attacks. A cast is still aimed at the nearest enemy: the
  policy prices a spell against that one enemy inside a private helper, and
  pricing it at another is the engine's to offer (smarks/tarmar-engine#21).

* **A choice a player can weigh** (issue #788). The menu used to lead with
  the AI's scoring sheet: "score 0.178 — Strike Gray Wolf: bare hands at 1
  hex, d20 -3 vs TN 16 — P(hit) 0.100 ...". :func:`describe` gives each
  option a plain line built from the same figures (who it is aimed at, the
  chance to hit as "2 in 20", the damage dice and what a hit does on
  average), what it costs, and which option the AI would take. The score
  and its working stay on the payload for the page to show on request.

Only the Tarmar profile's menu is widened to every target. A classic
figure's menu comes from the classic policy, whose candidates and state are
shaped differently (a ``GameState`` of ``Figure`` s): it keeps the engine's
own options and targets, and each option is described the same way (issue
#890) from the classic figures' own numbers, read through the classic
engine at the pause -- the adjDX to roll under on 3d6 (or 4d6 against a
defender), the weapon's dice less the target's armour, the ST a spell
invests -- because the classic policy's rationale is a tactic label ("already
in reach: stand and strike"), not a forecast. Either way the AI's own pick is
marked, which both profiles' pages read.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, replace

from hexarena.hex import Hex
from tarmar_engine import actions, combat_math, movement
from tarmar_engine.classic import ai as classic_ai
from tarmar_engine.classic import policy as classic_policy
from tarmar_engine.classic.data import DamageDice, WeaponKind
from tarmar_engine.classic.facing import attack_zone
from tarmar_engine.classic.figure import Figure
from tarmar_engine.classic.megahex import megahex_distance
from tarmar_engine.classic.options import Option as ClassicOption
from tarmar_engine.classic.options import spec as classic_spec
from tarmar_engine.classic.state import GameState
from tarmar_engine.engine import RUN_FATIGUE_COST, SPRINT_FATIGUE_COST
from tarmar_engine.policy import (
    ANCHOR_OPTION,
    GRAPPLE_ATTEMPT_SCORE,
    GRAPPLE_TACTICS_RATIONALE,
    HURT_THRESHOLD,
    PLAYER_ONLY_SCORE,
    Candidate,
    Decision,
    _beast_caution,
    _hth_partners,
    _hth_strike_forecast,
    _hth_targets,
    _hurt_fraction,
    _melee_score,
    _missile_score,
    best_weapon,
    score_text,
    weapons_in_reach,
)
from tarmar_engine.spells import get_spell
from tarmar_engine.state import BattleState, CombatantState

from . import magic_bounds
from .magic_rules import magic_rules
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

#: The attack options a player aims (#775). A yielded CHARGE (``b_yield``)
#: is aimed as its CHARGE is.
AIMED_OPTIONS: frozenset[str] = frozenset({"j", "b", "f", "l", "t", "o"})


def _legal_targets(
    state: BattleState, actor: CombatantState, option: str
) -> list[CombatantState]:
    """Every enemy ``option`` may be aimed at, by the rules the engine applies.

    ATTACK and ONE LAST SHOT reach an adjacent enemy; CHARGE an enemy its
    jog ends beside (:func:`_charge_reaches`, the reach the policy prices it
    by); a MISSILE any enemy on the field (the engine takes the range
    penalty); a hand-to-hand strike or grapple an enemy an entry condition
    admits.
    """
    enemies = state.enemies_of(actor)
    if option in ("j", "l"):
        return [
            enemy for enemy in enemies if combat_math.figures_adjacent(actor, enemy)
        ]
    if option == "b":
        # A charge the engine's jog cannot bring beside its target falls
        # short and the AI scores it 0, so it is not aimed there; the
        # engine's own CHARGE row says so when every enemy is out of reach
        # (issue #56).
        return [
            enemy
            for enemy in enemies
            if _charge_reaches(_approach(state, actor, option, enemy))
        ]
    if option == "f":
        return list(enemies)
    # "t" and "o", the last of AIMED_OPTIONS.
    return _hth_targets(state, actor)


def _thrown_away_note(actor: CombatantState) -> bool:
    """Whether a MISSILE throw would leave the figure without a hand weapon.

    The engine's own condition (``tarmar_engine.policy.choose_option``, the
    ``f`` branch): the weapon in hand is thrown, not a missile weapon, and no
    spare hand weapon is carried, so the AI keeps it.
    """
    spares = [spare for spare in actor.spare_weapons if spare.item_id]
    return (
        actor.weapon.is_thrown
        and not actor.weapon.is_missile
        and not any(not spare.is_missile for spare in spares)
    )


def _aimed(
    state: BattleState, actor: CombatantState, option: str, defender: CombatantState
) -> tuple[float, str]:
    """The engine's score and working for ``option`` aimed at ``defender``.

    Each branch is the engine policy's own for that letter, so the line a
    player reads for the second wolf is the one the AI would have written.
    Its words are therefore the engine's, as on the engine's own row beside
    it, and not the menu's configuration (#90).
    """
    hurt = _hurt_fraction(actor) < HURT_THRESHOLD
    if option == "j":
        score, rationale = _beast_caution(actor, hurt, *_melee_score(actor, defender))
        return score, f"Strike {defender.name}: {rationale}"
    if option == "b":
        score, rationale = _beast_caution(actor, hurt, *_melee_score(actor, defender))
        return score, f"Charge {defender.name}: {rationale}"
    if option == "f":
        score, rationale = _missile_score(actor, defender)
        if _thrown_away_note(actor):
            score = PLAYER_ONLY_SCORE
            rationale = (
                f"{rationale}; but the {actor.weapon.name} is the only hand "
                f"weapon carried, so the AI keeps it = {score_text(score)}"
            )
        return score, f"Shoot {defender.name}: {rationale}"
    if option == "l":
        score, rationale = _missile_score(actor, defender)
        return score, (
            f"One last shot at {defender.name}, the bow ready before the "
            f"engagement: {rationale}"
        )
    if option == "t":
        score, rationale = _hth_strike_forecast(state, actor, defender)
        return score, f"Strike {defender.name} {rationale}"
    # "o": a grapple is valued at 0 whoever it is aimed at (the policy's
    # "grapple tactics out of scope").
    return GRAPPLE_ATTEMPT_SCORE, (f"Grapple valued at 0 ({GRAPPLE_TACTICS_RATIONALE})")


def every_target(
    state: BattleState, actor: CombatantState, candidates: list[Candidate]
) -> list[Candidate]:
    """The engine's menu with each attack also aimed at every other legal target.

    Each added option sits right after the engine's own, carries the same
    letter and rules name, and is scored by the engine's forecast for its
    target; the engine's own candidates are untouched and keep their order.
    A figure in a grapple keeps the grapple's fixed menu, whose options all
    name the one figure it is locked with.

    Args:
        state: The live state the menu was built from.
        actor: The figure choosing.
        candidates: The engine policy's menu for it.

    Returns:
        The widened menu.
    """
    if actor.grappled_by is not None or actor.grappling is not None:
        return list(candidates)
    widened: list[Candidate] = []
    for candidate in candidates:
        widened.append(candidate)
        option = actions.base_option(candidate.letter)
        if (
            option not in AIMED_OPTIONS
            or candidate.target_id is None
            or candidate.spell_key
        ):
            continue
        for defender in _legal_targets(state, actor, option):
            if defender.combatant_id == candidate.target_id:
                continue
            score, rationale = _aimed(state, actor, option, defender)
            if actions.is_yielded(candidate.letter):
                rationale = (
                    f"As {actions.option_name(option)}, moving in Final "
                    f"Movement instead: {rationale}"
                )
            widened.append(
                replace(
                    candidate,
                    score=score,
                    rationale=rationale,
                    target_id=defender.combatant_id,
                )
            )
    return widened


# Read from prose until the engine carries a forecast as fields
# (smarks/tarmar-engine#22); EveryPlainLineCarriesItsOddsTest fails if the
# wording moves. The figures an engine forecast is written from (tarmar_engine.policy's
# ``_attack_forecast`` and ``_hth_strike_forecast``): the chance to hit, the
# expected damage a hit does, and the dice less the armour stops.
_HIT_CHANCE = re.compile(r"P\(hit\) (?P<chance>[\d.]+)")
_DAMAGE = re.compile(
    r"x (?P<damage>[\d.]+) expected\s+damage "
    r"\((?P<dice>[^()]+?) less (?P<stops>\d+) armour stops\)"
)
#: The faces of the d20 every attack roll is made on (attack-rolls.md).
DIE_FACES = 20


def _odds(rationale: str) -> str:
    """ "2 in 20 to hit, 1d6-1 less 1 armour (about 2 a hit)", from a forecast.

    Read off the engine's own working, so the plain line and the score line
    a player can open never disagree; the winning faces are counted as the
    engine counts them, ``round(P(hit) x 20)``. Empty when the rationale
    carries no forecast (DEFEND, DISENGAGE, a grapple).
    """
    chance = _HIT_CHANCE.search(rationale)
    damage = _DAMAGE.search(rationale)
    if chance is None or damage is None:
        return ""
    tarmar = _tarmar()
    faces = round(float(chance.group("chance")) * DIE_FACES)
    expected = float(damage.group("damage"))
    average = (
        tarmar.under_one
        if expected < 0.5
        else tarmar.about.format(damage=round(expected))
    )
    stops = damage.group("stops")
    figures = {
        "faces": faces,
        "sides": DIE_FACES,
        "dice": damage.group("dice"),
        "average": average,
    }
    if stops == "0":
        return tarmar.odds.format(**figures)
    return tarmar.odds_armoured.format(stops=stops, **figures)


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


def _with_odds(sentence: str, rationale: str) -> str:
    return _joined_odds(sentence, _odds(rationale))


def _approach(
    state: BattleState,
    actor: CombatantState,
    option: str,
    target: CombatantState,
) -> movement.Approach:
    """Where the engine's own move for ``option`` toward ``target`` would end.

    The engine walks :func:`tarmar_engine.movement.approach_route` at the
    option's gait (``TurnRunner.move_towards_target``), and its policy prices
    a CHARGE's reach and words a DODGE's move by that same route
    (tarmar-engine #37), so the menu reads the route there too rather than
    judging the board on its own.

    Args:
        state: The live state the menu was built from.
        actor: The figure choosing.
        option: A moving option's key (MOVE, SPRINT, CHARGE, DODGE, or a
            yielded one).
        target: The figure it closes on.

    Returns:
        The engine's approach: the route it would walk and the distance it
        would end at.
    """
    gait = movement.MOVEMENT_GAITS[actions.base_option(option)]
    allowance = movement.gait_allowance(actor, gait)
    return movement.approach_route(state, actor, target, allowance)


def _stands(
    state: BattleState,
    actor: CombatantState,
    option: str,
    target: CombatantState,
) -> bool:
    """Whether the engine's move for ``option`` toward ``target`` stays put.

    The engine walks nothing when its route is empty: no clear path at the
    option's gait gets nearer (tarmar-engine #37), or the figure is already
    beside the target or held where it stands.
    """
    return not _approach(state, actor, option, target).route


def _charge_reaches(approach: movement.Approach) -> bool:
    """Whether a Tarmar CHARGE whose jog is ``approach`` reaches its target.

    The policy's own reach test (``tarmar_engine.policy.choose_option``, the
    ``b`` branch): already adjacent, or the engine's jog along a clear path
    (:func:`_approach`) ends adjacent. The engine strikes only an adjacent
    target after the jog and logs "charge fell short" otherwise (issue #56).
    """
    return approach.distance <= 1


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


def _gait(option: str) -> str:
    """The engine's gait for a Tarmar moving option: "jog", "run", "sprint"."""
    return movement.MOVEMENT_GAITS[actions.base_option(option)]


def _clear_gait(option: str) -> str:
    """ "clear jog": the move a Tarmar option makes, as :func:`_no_nearer`
    names it."""
    return _tarmar().clear_gait.format(gait=_gait(option))


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
    letter: str,
    target: CombatantState | None,
    allowance: int,
    fatigue: int,
    *,
    stands: bool,
) -> tuple[str, str]:
    """A MOVE or SPRINT's sentence and cost (issue #57).

    The move the engine would make: toward the target along its own route,
    or no move at all when that route is empty (:func:`_stands`), and then no
    Fatigue either, since the engine charges a gait's Fatigue only for hexes
    walked. A hex the player picks is walked, and charged, as picked.

    Args:
        letter: The option's key ("a", "sprint", or a yielded one).
        target: The figure it closes on, or None when none remains.
        allowance: The hexes the gait covers.
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
        sentence.format(hexes=allowance, aimed=aimed),
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
        [] if casting_failure is None else sorted(casting_failure.mana_lost_totals)
    )
    if not lost_totals:
        return tarmar.paid_on_success
    named = tarmar.or_separator.join(str(total) for total in lost_totals)
    return tarmar.paid_on_success_or_total.format(totals=named)


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
    rationale = candidate.rationale
    plain, cost = "", ""
    # A mover the engine would walk nowhere says so (issue #57).
    stands = (
        option in STANDING_MOVERS
        and target is not None
        and _stands(state, actor, candidate.letter, target)
    )
    # A charge the engine's jog cannot bring beside its target says so
    # (issue #56): the engine's own row when every enemy is out of reach.
    charge = (
        _approach(state, actor, candidate.letter, target)
        if option == "b" and target is not None
        else None
    )
    falls_short = charge is not None and not _charge_reaches(charge)
    shared, tarmar = _shared(), _tarmar()
    if option == "a":
        plain, cost = _mover_line(
            candidate.letter,
            target,
            actor.move_run,
            RUN_FATIGUE_COST,
            stands=stands,
        )
    elif option == "sprint":
        plain, cost = _mover_line(
            candidate.letter,
            target,
            actor.move_sprint,
            SPRINT_FATIGUE_COST,
            stands=stands,
        )
    elif option == "b" and charge is not None and falls_short:
        jog = movement.gait_allowance(actor, movement.MOVEMENT_GAITS[option])
        plain = _no_attack_charge_line(
            aimed,
            _ends_from(
                shared.reach.format(move=_clear_gait(candidate.letter), hexes=jog),
                charge.distance,
                aimed,
            )
            if charge.route
            else _no_nearer(_clear_gait(candidate.letter), aimed),
        )
    elif option == "b":
        plain = _with_odds(
            tarmar.charge.format(
                aimed=aimed,
                hexes=movement.gait_allowance(actor, "jog"),
                weapon=weapon,
            ),
            rationale,
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
        plain = tarmar.drop.format(hexes=movement.gait_allowance(actor, "jog"))
    elif option == "e":
        spares = [spare for spare in actor.spare_weapons if spare.item_id]
        choice = best_weapon(actor, spares)
        plain = _ready_line(choice.name if choice else None)
    elif option == "f":
        plain = _with_odds(shared.shoot.format(aimed=aimed, weapon=weapon), rationale)
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
        plain = _with_odds(shared.attack.format(aimed=aimed, weapon=weapon), rationale)
    elif option == "k":
        plain = tarmar.defend.format(guards=tarmar.defend_guards)
    elif option == "l":
        plain = _with_odds(
            shared.last_shot.format(aimed=aimed, weapon=weapon), rationale
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
        # The engine steps away from the first adjacent enemy, the first of
        # the enemies an ATTACK reaches (``TurnRunner.disengage_step``).
        stuck = _no_step_away(state, actor, _legal_targets(state, actor, "j"))
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
            rationale,
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
    """A candidate's own engine fields, to rebuild it as a described one."""
    return {
        "letter": candidate.letter,
        "name": candidate.name,
        "score": candidate.score,
        "rationale": candidate.rationale,
        "target_id": candidate.target_id,
        "spell_key": candidate.spell_key,
        "push_mana": candidate.push_mana,
        "borrow": candidate.borrow,
        "anchor_target": candidate.anchor_target,
        "counter_mana": candidate.counter_mana,
        "escalate": candidate.escalate,
        "lifted_bounds": candidate.lifted_bounds,
        "destination": candidate.destination,
    }


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


#: The plain die a classic to-hit roll is made with, and how many of them a
#: roll uses when nothing makes the target harder to hit (Melee p.20).
CLASSIC_DIE_FACES = 6
CLASSIC_PLAIN_DICE = 3

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
            ``GameState.hth_attack``, settled by the defender's roll) rather
            than a weapon's blow.
    """

    path: list[Hex]
    struck: Figure | None
    grapples: bool


def _classic_charge_blow(
    game: GameState, figure: Figure, candidate: Candidate
) -> _ClassicChargeBlow:
    """The classic engine's charge, rehearsed: where it moves, whom it strikes.

    The engine's own rule, played on a copy of the game (its snapshot
    contract, ``to_dict``/``from_dict``) so the live one is untouched: the
    plan the engine enacts for this charge (``classic.policy.plan_for``),
    applied through its own verbs (``classic.ai.apply``); then the foes the
    combat phase may strike from where it ends
    (``GameState.attack_candidates``: a weapon's front hexes and a pole's
    jab, any hex of a multi-hex foe), and the one the engine's attack
    declaration picks among them (``classic.ai.queue_attack_for``, which
    strikes a melee foe with a weapon and grapples with bare hands). A
    charge may strike a foe other than the one it closes on, or nobody
    (issue #56).

    Args:
        game: The live game the menu was built from.
        figure: The figure choosing.
        candidate: Its CHARGE ATTACK option.

    Returns:
        The charge's path, the foe it attacks, and whether it grapples.

    Raises:
        LookupError: if the copy of the game has lost the figure, which the
            snapshot contract rules out.
    """
    rehearsal = GameState.from_dict(game.to_dict())
    charger = _classic_figure(rehearsal, figure.uid)
    if charger is None:
        raise LookupError(f"{figure.name} is missing from a copy of the game")
    plan = classic_policy.plan_for(rehearsal, charger, candidate)
    classic_ai.apply(rehearsal, charger, plan)
    reach = rehearsal.attack_candidates(charger)
    grapples = charger.ready_weapon is None
    foes = reach.hth if grapples else reach.melee
    if not foes:
        return _ClassicChargeBlow(list(plan.path), None, grapples)
    # ``queue_attack_for`` takes a melee foe by ``_best_target`` (lowest ST,
    # then nearest) and a grapple's by lowest ST alone; the two agree here,
    # since a grapple is begun only from one hex off
    # (``GameState._can_initiate_hth``), and ``min`` keeps the first of a tie
    # in both (smarks/tarmar-engine#54 asks for the engine's own call).
    blow = classic_ai._best_target(rehearsal, charger, foes)
    return _ClassicChargeBlow(
        list(plan.path), _classic_figure(game, blow.uid), grapples
    )


def _classic_roll(dice_count: int) -> str:
    """ "3d6": a classic roll of ``dice_count`` plain dice."""
    return f"{dice_count}d{CLASSIC_DIE_FACES}"


def _classic_dice(damage: DamageDice) -> str:
    """ "2d6", "1d6+2", "1d6-1": a classic damage expression in the page's words."""
    modifier = f"{damage.modifier:+d}" if damage.modifier else ""
    return f"{_classic_roll(damage.count)}{modifier}"


def _classic_hit_chance(game: GameState, needed: int, dice_count: int) -> int:
    """The chance, in whole percent, that ``dice_count`` d6 hit on ``needed``.

    Every total is put through the engine's own table
    (``Ruleset.classify_roll``), so the automatic hits and misses count as the
    engine counts them.
    """
    faces = range(1, CLASSIC_DIE_FACES + 1)
    rolls = list(itertools.product(faces, repeat=dice_count))
    hits = sum(
        game.rules.classify_roll(sum(roll), dice_count, needed)[0] for roll in rolls
    )
    return round(100 * hits / len(rolls))


def _classic_figure(game: GameState, uid: object) -> Figure | None:
    """The classic figure whose uid a candidate names, or None."""
    if not uid:
        return None
    return next((figure for figure in game.figures if figure.uid == uid), None)


def _classic_strike(
    game: GameState, figure: Figure, option: ClassicOption, target: Figure
) -> str:
    """ "10 or less on 3d6 to hit (about 50%), 2d6 less 2 armour",
    or "" for a figure off the field.

    Read through the classic engine as it stands at the pause, the way
    ``GameState.queue_attack`` would declare the blow now: the attacker's
    adjDX with wounds, spells and the situation (``Ruleset.to_hit_number``);
    a missile's range penalty by megahex, a hurled weapon's by hex, and no
    facing bonus for a missile; the target's defending or dodging (the
    four-dice rule); and the armour the blow meets from where the attacker
    stands. A CHARGE moves before it strikes, so its line leaves out the
    facing bonus, which depends on where it ends, and counts as a blow in
    reach.
    """
    weapon = figure.ready_weapon
    layout = game.arena.layout
    if figure.position is None or target.position is None:
        # Off the field: no line of attack to read odds from.
        return ""
    charging = option == ClassicOption.CHARGE_ATTACK
    missile = weapon is not None and weapon.kind == WeaponKind.MISSILE
    distance = 1 if charging else game.arena.distance(figure.position, target.position)
    thrown = weapon is not None and not missile and weapon.throwable and distance > 1
    ranged = missile or thrown
    zone = None if charging else attack_zone(layout, figure, target)
    if missile:
        range_penalty = game.rules.missile_range_penalty(
            megahex_distance(layout, figure.position, target.position)
        )
    elif thrown:
        range_penalty = -distance
    else:
        range_penalty = 0
    # Private: the classic engine offers no public forecast yet
    # (smarks/tarmar-engine#23). These are the modifiers queue_attack applies.
    situational, _note = game._situational_mods(
        figure, target, weapon, ranged, is_throw=thrown
    )
    needed = game.rules.to_hit_number(
        figure,
        zone=zone,
        ignore_facing=charging or missile,
        range_penalty=range_penalty,
        situational=situational,
    )
    dice_count = game.rules.attack_dice_count(target, ranged=ranged)
    chance = _classic_hit_chance(game, needed, dice_count)
    classic = _classic()
    odds = classic.odds.format(
        needed=needed, dice=_classic_roll(dice_count), chance=chance
    )
    if charging:
        odds = classic.odds_before_facing.format(odds=odds)
    if weapon is None:
        return odds
    stopped = game.rules.absorbed(target, zone=zone)
    # No "about N a hit": what a hit does on average turns on the engine's
    # special totals (3 triple, 4 double) and a pole weapon's extra dice, and
    # the engine does not say it yet (smarks/tarmar-engine#23); the studio
    # does not work it out on its own.
    damage = _classic_dice(weapon.damage)
    if stopped:
        return classic.odds_damage_armoured.format(
            odds=odds, damage=damage, stops=stopped
        )
    return classic.odds_damage.format(odds=odds, damage=damage)


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
    if option == ClassicOption.CHARGE_ATTACK and target is not None:
        blow = _classic_charge_blow(game, figure, candidate)
        charge_path, struck, grapples = blow.path, blow.struck, blow.grapples
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
            _classic_strike(game, figure, option, struck),
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
            _classic_strike(game, figure, option, target),
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
            plain_dice=_classic_roll(CLASSIC_PLAIN_DICE),
        )
    elif option == ClassicOption.SHIFT_DEFEND:
        dice = game.rules.resolution.attack_dice_count(
            dodging=False, defending=True, ranged=False
        )
        plain = classic.shift_defend.format(
            dice=_classic_roll(dice), plain_dice=_classic_roll(CLASSIC_PLAIN_DICE)
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


def player_menu(profile_name: str, state, actor, advice: Decision) -> list[Candidate]:
    """The menu a seated player chooses from, every option described.

    For a Tarmar figure, the engine's menu with each attack aimed at every
    legal target (:func:`every_target`) and each option given its plain line;
    for a classic figure, the engine's menu with each option described from
    the classic state (:func:`describe_classic`, issue #890). Either way the
    AI's own pick is marked, since the widened menu may hold an option that scores
    above it. Every entry is a :class:`DescribedCandidate`; the list is typed
    as the engine's ``Candidate`` because it becomes a ``Decision``'s.

    Args:
        profile_name: The battle's rules profile.
        state: The live state the menu was built from.
        actor: The figure choosing.
        advice: The profile's AI decision for that figure.
    """
    tarmar = profile_name == TARMAR_PROFILE
    if tarmar:
        return [
            describe(state, actor, candidate, ai_choice=candidate is advice.chosen)
            for candidate in every_target(state, actor, advice.candidates)
        ]
    return [
        describe_classic(state, actor, candidate, ai_choice=candidate is advice.chosen)
        for candidate in advice.candidates
    ]
