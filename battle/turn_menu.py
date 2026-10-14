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
from tarmar_engine.engine import (
    CASTING_MANA_LOST_ROLLS,
    RUN_FATIGUE_COST,
    SPRINT_FATIGUE_COST,
)
from tarmar_engine.policy import (
    ANCHOR_OPTION,
    GRAPPLE_ATTEMPT_SCORE,
    GRAPPLE_TACTICS_RATIONALE,
    HURT_THRESHOLD,
    PLAYER_ONLY_SCORE,
    Candidate,
    Decision,
    _beast_caution,
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

from .magic_rules import magic_rules
from .persistence import TARMAR_PROFILE

#: What DODGE and DEFEND guard against, in the words of the "Defending and
#: Dodging" table (special-combat-situations.md): Dodge "Missiles and thrown
#: only", Defend "Melee and non-missile only". The engine agrees: it applies
#: Defend only to an attack that is not ranged, and a throw is ranged. A
#: drift test holds these to the page.
DODGE_DEFENDS_AGAINST = "missiles and thrown weapons only"
DEFEND_DEFENDS_AGAINST = "melee and non-missile attacks only"

#: The attack options a player aims (#775). A yielded CHARGE (``b_yield``)
#: is aimed as its CHARGE is.
AIMED_OPTIONS: frozenset[str] = frozenset({"j", "b", "f", "l", "t", "o"})


def _legal_targets(
    state: BattleState, actor: CombatantState, option: str
) -> list[CombatantState]:
    """Every enemy ``option`` may be aimed at, by the rules the engine applies.

    ATTACK and ONE LAST SHOT reach an adjacent enemy; CHARGE an enemy within
    its reach (a Jog and the last hex, the reach the policy prices it by); a
    MISSILE any enemy on the field (the engine takes the range penalty); a
    hand-to-hand strike or grapple an enemy an entry condition admits.
    """
    enemies = state.enemies_of(actor)
    if option in ("j", "l"):
        return [
            enemy for enemy in enemies if combat_math.figures_adjacent(actor, enemy)
        ]
    if option == "b":
        # The reach the policy prices a charge by, through the engine's one
        # jog rule (a barred jog charges at the walk, tarmar-engine #17).
        reach = movement.gait_allowance(actor, "jog") + 1
        return [
            enemy
            for enemy in enemies
            if combat_math.figure_distance(actor, enemy) <= reach
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
    faces = round(float(chance.group("chance")) * DIE_FACES)
    expected = float(damage.group("damage"))
    average = "under 1" if expected < 0.5 else f"about {round(expected)}"
    stops = damage.group("stops")
    armour = f" less {stops} armour" if stops != "0" else ""
    return (
        f"{faces} in {DIE_FACES} to hit, {damage.group('dice')}{armour} "
        f"({average} a hit)"
    )


def _weapon_phrase(actor: CombatantState) -> str:
    """ "the Saber", or "bare hands" for a figure with no weapon in hand."""
    return f"the {actor.weapon.name}" if actor.weapon.item_id else "bare hands"


def _close_weapon_phrase(actor: CombatantState) -> str:
    """What an HTH strike (t) is made with: "Bare hands or dagger", so a
    sword in hand strikes with bare hands (tarmar-engine #19)."""
    return f"the {actor.weapon.name}" if actor.weapon.hth_usable else "bare hands"


def _with_odds(sentence: str, rationale: str) -> str:
    odds = _odds(rationale)
    return f"{sentence}: {odds}" if odds else sentence


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


def _plain_line(
    state: BattleState,
    actor: CombatantState,
    candidate: Candidate,
    target: CombatantState | None,
) -> tuple[str, str]:
    """The sentence a player reads for one option, and what it costs.

    Returns:
        ``(plain, cost)``: the sentence, and the cost in words ("1 Fatigue",
        "3 mana, paid only if it succeeds or the roll is 17 or 18", "the
        Dagger leaves your hand"), or "" when it costs nothing. A spell's mana
        is paid on a success or a 17 or 18 and kept on any other failure
        (tarmar-studio #816, the engine's ``CASTING_MANA_LOST_ROLLS``).
    """
    option = actions.base_option(candidate.letter)
    aimed = target.name if target is not None else ""
    weapon = _weapon_phrase(actor)
    rationale = candidate.rationale
    plain, cost = "", ""
    if option == "a":
        plain = (
            f"Run up to {actor.move_run} hexes toward {aimed or 'the nearest enemy'}"
        )
        cost = f"{RUN_FATIGUE_COST} Fatigue"
    elif option == "sprint":
        plain = f"Sprint up to {actor.move_sprint} hexes toward {aimed}"
        cost = f"{SPRINT_FATIGUE_COST} Fatigue"
    elif option == "b":
        plain = _with_odds(
            f"Charge {aimed}: jog up to {movement.gait_allowance(actor, 'jog')} "
            f"hexes and attack "
            f"with {weapon}",
            rationale,
        )
    elif option == "c":
        plain = f"Dodge: +4 to your Target Number against {DODGE_DEFENDS_AGAINST}"
        if aimed:
            plain += f", jogging toward {aimed}"
    elif option == "d":
        plain = (
            "Drop prone where you stand, or first jog up to "
            f"{movement.gait_allowance(actor, 'jog')} hexes to a hex you pick"
        )
    elif option == "e":
        spares = [spare for spare in actor.spare_weapons if spare.item_id]
        choice = best_weapon(actor, spares)
        plain = f"Ready the {choice.name}" if choice else "Ready another weapon"
    elif option == "f":
        plain = _with_odds(f"Shoot {aimed} with {weapon}", rationale)
        if actor.weapon.is_thrown and not actor.weapon.is_missile:
            cost = f"the {actor.weapon.name} leaves your hand"
    elif option in ("g", "p"):
        plain = "Stand up; it takes the whole turn"
    elif option in ("h", "r") and candidate.spell_key:
        spell = get_spell(candidate.spell_key)
        plain = f"Cast {spell.name}" + (f" at {aimed}" if aimed else "")
        lost_rolls = " or ".join(str(roll) for roll in sorted(CASTING_MANA_LOST_ROLLS))
        if candidate.push_mana:
            # A pushed copy of the cast (issue #960): the engine's menu offers
            # it, with the Control Roll's working in its rationale.
            plain += f", pushing {candidate.push_mana} more mana into it"
            cost = (
                f"{spell.level + candidate.push_mana} mana ({spell.level} and "
                f"{candidate.push_mana} pushed), paid only if it succeeds or the "
                f"roll is {lost_rolls}; a success makes a Control Roll"
            )
        else:
            cost = f"{spell.level} mana, paid only if it succeeds or the roll is {lost_rolls}"
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
    elif option == ANCHOR_OPTION:
        plain, cost = _anchor_line(actor, target)
    elif option == "j":
        plain = _with_odds(f"Attack {aimed} with {weapon}", rationale)
    elif option == "k":
        plain = f"Defend: +4 to your Target Number against {DEFEND_DEFENDS_AGAINST}"
    elif option == "l":
        plain = _with_odds(f"One last shot at {aimed} with {weapon}", rationale)
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
    elif option == "o":
        plain = f"Try to grapple {aimed}: a hold pins them rather than hurting them"
    elif option == "q":
        choice = best_weapon(actor, weapons_in_reach(state, actor))
        plain = f"Pick up the {choice.name}" if choice else "Pick up a weapon"
        if actor.weapon.item_id:
            cost = f"the {actor.weapon.name} is dropped"
    elif option == "t":
        plain = _with_odds(
            f"Strike {aimed} in close with {_close_weapon_phrase(actor)}", rationale
        )
    elif option == "u":
        plain = "Try to draw your dagger: 3d6 at or under your DEX"
    elif option == "v" and actor.grappled_by is None:
        plain = (
            "Break away from hand-to-hand: 4d6 at or under your DEX, then stand "
            "in a hex next to you"
        )
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
    if actions.is_yielded(candidate.letter):
        plain += ", moving in Final Movement instead"
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


def _classic_dice(damage: DamageDice) -> str:
    """ "2d6", "1d6+2", "1d6-1": a classic damage expression in the page's words."""
    modifier = f"{damage.modifier:+d}" if damage.modifier else ""
    return f"{damage.count}d{CLASSIC_DIE_FACES}{modifier}"


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
    odds = (
        f"{needed} or less on {dice_count}d{CLASSIC_DIE_FACES} to hit (about {chance}%)"
    )
    if charging:
        odds += " before any flank or rear bonus"
    if weapon is None:
        return odds
    stopped = game.rules.absorbed(target, zone=zone)
    armour = f" less {stopped} armour" if stopped else ""
    # No "about N a hit": what a hit does on average turns on the engine's
    # special totals (3 triple, 4 double) and a pole weapon's extra dice, and
    # the engine does not say it yet (smarks/tarmar-engine#23); the studio
    # does not work it out on its own.
    return f"{odds}, {_classic_dice(weapon.damage)}{armour}"


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
    if option in _CLASSIC_STRIKES and target is not None:
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
        odds = _classic_strike(game, figure, option, target)
        plain = f"{verbs[option]}: {odds}" if odds else verbs[option]
    elif option == ClassicOption.MOVE:
        plain = f"Move up to {budget} hexes{toward}"
    elif option == ClassicOption.HALF_MOVE:
        plain = f"Move up to {budget} hexes{toward} without attacking"
    elif option == ClassicOption.DODGE:
        dice = game.rules.resolution.attack_dice_count(
            dodging=True, defending=False, ranged=True
        )
        plain = (
            f"Dodge while moving up to {budget} hexes{toward}: missiles and "
            f"thrown weapons roll {dice}d{CLASSIC_DIE_FACES} to hit you, not "
            f"{CLASSIC_PLAIN_DICE}d{CLASSIC_DIE_FACES}"
        )
    elif option == ClassicOption.SHIFT_DEFEND:
        dice = game.rules.resolution.attack_dice_count(
            dodging=False, defending=True, ranged=False
        )
        plain = (
            f"Shift a hex and defend: blows in melee roll {dice}d"
            f"{CLASSIC_DIE_FACES} to hit you, not "
            f"{CLASSIC_PLAIN_DICE}d{CLASSIC_DIE_FACES}"
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
        plain = f"Grapple {aimed}: move into their hex and fight hand to hand"
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
