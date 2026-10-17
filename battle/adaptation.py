"""Beast → engine adaptation (issue #181, part two of the bestiary design).

A :class:`bestiary.models.Beast` becomes the same
:class:`~battle.engine.state.CombatantState` dataclass a Character does, so
the engine, the AI policy and the renderer never special-case beasts beyond
the ``is_beast`` flag:

* **Natural attacks** — the beast's ``attacks`` JSON entries are weapon
  entries already (``{name, damage, weapon_class}`` with ``weapon_class``
  keys of ``characters.combat.MATRIX``); the adaptation readies the first
  melee attack with a rollable damage expression. Breath weapons and other
  ``Missile``-class attacks are skipped — v1 beasts fight tooth and claw
  only, keeping the AI to the melee action subset. A beast whose catalog
  entry names no rollable damage strikes bare-handed at its STR
  (special-combat-situations.md's Bare-Handed Damage table, via
  :func:`battle.engine.state.bare_handed_damage`) — but it keeps the
  ``weapon_class`` its catalog attack named, and it says so: the
  substitution is carried in the weapon's own name, so every surface that
  prints a weapon prints the fact that the catalog's damage was not
  modelled. See :class:`BeastAttack` (#645).
* **Armour** — the beast's hide: its ``armour_tier`` and ``stops`` feed
  ``characters.combat`` unchanged. No shield.
* **Movement** — ``Beast.movement`` is the creature's full move, so it maps
  onto the run gait; walk and jog scale by the loaded canon's own gait
  proportions (movement.md's 4:7:12), each gait found by its place in the
  canon's order (:func:`engine_gait_names`, issue #75). A rooted beast
  (movement 0) cannot move at any gait.
  Two coordinator's rulings under the standing rule, Spencer may overrule
  (#867): a beast has no Sprint (the bestiary gives one full move, which is
  the run; no page gives a beast a faster gait), and its movement modifier,
  which the HTH entry condition "has a lower movement modifier than you"
  reads (#823), is the one movement.md's Run row implies, ``Run = 12 +
  modifier``, so a beast faster than a person reads as faster.
* **Magic** — none in v1: no mana, no spells, whatever ``special_abilities``
  hints (a loud simplification, noted here on purpose — beast casting would
  need per-ability rules the bestiary does not encode).
* **Survival** — DEX/CON drive dodge modifiers and survival saves through
  the same fields characters use; ``fatigue_pool``/``body_pool`` are the
  engine pools directly.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass

from bestiary.models import Beast
from characters.dice import parse_dice_expression
from characters.equipment_rules import equipment_rules
from reference.rules_file import derived_from_rules

from .engine.state import CombatantState, WeaponState, bare_handed_damage

# The rollable head of a catalog damage string. Many bestiary entries carry
# qualifiers after the dice ("2d6, 2d6+1 in HTH", "1d6 + poison"); the engine
# rolls the leading expression and leaves the qualifier to the GM's table.
_DICE_PREFIX = re.compile(r"^\s*(\d+d\d+(?:\s*[+-]\s*\d+)?)")

# The same expression, found anywhere in the prose rather than at its head.
# Only the *primary* attack's damage is read this way (see ``beast_attack``):
# a rider entry's conditional dice — a spider's "save at 3/ST or take an
# additional 2d6" — belongs to the venom, not to the bite being readied.
_DICE_ANYWHERE = re.compile(r"\d+d\d+(?:\s*[+-]\s*\d+)?")

# What a beast wears instead of an attack the catalog does not model, and
# how the arena and the muster say so. One sentence, two renderings: the
# muster prints it beside the ST it already shows, and the weapon carries
# the longer form as its own name so the roster card, the attribute panel
# and every log line that names a weapon repeat it. Silence here was the
# bug (#645): a DM who mustered a Troll got a figure that did not fight
# like the Troll on the catalog page, and nothing on screen said so.
NO_MODELLED_ATTACK_NOTE = "no modelled attack; fights bare-handed"


def no_modelled_attack_name(strength: int) -> str:
    """The weapon name a beast with no modelled attack wears.

    Args:
        strength: The beast's STR, which is what its damage falls back to.

    Returns:
        :data:`NO_MODELLED_ATTACK_NOTE` with the STR being substituted in.
    """
    return f"{NO_MODELLED_ATTACK_NOTE} at ST {strength}"


@dataclass(frozen=True)
class BeastAttack:
    """What a beast fights with, and whether the catalog supplied it.

    Attributes:
        weapon: The readied weapon as the engine sees it.
        substituted: ``True`` when the catalog named no rollable damage and
            the published Bare-Handed Damage table stood in for it. The
            beast still keeps whatever ``weapon_class`` its catalog attack
            named, because the class decides the §6 matrix row and the §8
            hybrid-armour bypass and is a separate fact from the dice.
    """

    weapon: WeaponState
    substituted: bool = False


# The roster/archetype label beasts carry in state and the UI.
BEAST_ARCHETYPE = "beast"

#: The engine's gaits, fastest first: the names its combatant fields carry
#: (``move_sprint`` to ``move_walk``) and its turn menu reads. They are the
#: engine's, fixed until stage 6 of rules-as-data seats the engine's own
#: rules, so a canon must name exactly as many gaits (the ``battle.E003``
#: system check, :func:`battle.checks.check_engine_gaits`).
ENGINE_GAITS: tuple[str, ...] = ("sprint", "run", "jog", "walk")


def engine_gait_names() -> dict[str, str]:
    """The loaded canon's gait each engine gait seats, by place (issue #75).

    The canon names its gaits; the engine's four are matched to them by their
    place in the canon's order, fastest first: the fastest is the engine's
    sprint, the second its run, the second-slowest its jog and the slowest
    its walk.

    Returns:
        Each of :data:`ENGINE_GAITS` to the canon's gait.

    Raises:
        ValueError: The canon names other than four gaits, which
            ``battle.E003`` refuses before any battle is seated.
    """
    return dict(zip(ENGINE_GAITS, equipment_rules().gaits.order, strict=True))


@derived_from_rules
@functools.cache
def gait_proportions() -> dict[str, float]:
    """The canon's walk and jog as fractions of its run, by engine gait.

    A beast's gaits keep the proportions of a character's walk and jog to its
    run (movement.md's 4:7:12 in the default canon), applied to its full
    move. Built from the loaded rules the first time a beast is seated, never
    at import.
    """
    canon_gaits = engine_gait_names()
    base_distances = equipment_rules().gaits.base_distances
    run = base_distances[canon_gaits["run"]]
    return {gait: base_distances[canon_gaits[gait]] / run for gait in ("walk", "jog")}


def beast_gaits(movement: int) -> tuple[int, int, int]:
    """(walk, jog, run) for a beast whose full move is ``movement`` hexes."""
    if movement <= 0:
        return 0, 0, 0
    proportions = gait_proportions()
    walk = max(1, round(movement * proportions["walk"]))
    jog = max(1, round(movement * proportions["jog"]))
    return walk, jog, movement


def _melee_attacks(beast: Beast) -> list[dict]:
    """The beast's catalog attacks that a v1 figure can ready.

    Breath weapons and other ``Missile``-class entries are skipped — v1
    beasts fight tooth and claw only, which keeps the AI to the melee
    action subset.
    """
    return [
        attack
        for attack in beast.attacks or []
        if (attack.get("weapon_class") or "")
        # The bestiary's own class vocabulary, not the canon's Missile classes.
        and not (attack.get("weapon_class") or "").startswith("Missile")
    ]


def _rollable(damage_text: str) -> str:
    """The dice expression at the head of a damage string, or ``""``."""
    dice_match = _DICE_PREFIX.match(damage_text or "")
    if not dice_match:
        return ""
    damage = dice_match.group(1).replace(" ", "")
    try:
        parse_dice_expression(damage)
    except ValueError:  # pragma: no cover — the regex guarantees a parse
        return ""
    return damage


def beast_attack(beast: Beast) -> BeastAttack:
    """The natural attack the beast fights with (module docstring rules).

    Three passes, narrowest first:

    1. The first melee entry whose damage *begins* with a rollable
       expression. This is the ordinary case, and it is first so that a
       beast whose opening entry is a grapple ("Tentacle") still readies
       the bite that follows it.
    2. Failing that, the *primary* (first melee) entry, when its prose
       holds exactly one rollable expression — the hydret's "three heads,
       1d6-1 per head". Only the primary entry is read this way: a rider's
       conditional dice describe a venom save, not the bite.
    3. Failing that, the published Bare-Handed Damage table at the beast's
       STR, keeping the primary entry's ``weapon_class`` and saying in the
       weapon's own name that the catalog's damage was not modelled.

    Args:
        beast: The catalog (or generated) creature being seated.

    Returns:
        The weapon, and whether pass 3 had to stand in for the catalog.
    """
    melee = _melee_attacks(beast)
    for attack in melee:
        damage = _rollable(attack.get("damage") or "")
        if damage:
            return BeastAttack(
                WeaponState(
                    item_id="",
                    name=attack.get("name") or "natural attack",
                    weapon_class=attack["weapon_class"],
                    damage=damage,
                )
            )
    if melee:
        found = _DICE_ANYWHERE.findall(melee[0].get("damage") or "")
        if len(found) == 1 and _rollable(found[0]):
            return BeastAttack(
                WeaponState(
                    item_id="",
                    name=melee[0].get("name") or "natural attack",
                    weapon_class=melee[0]["weapon_class"],
                    damage=_rollable(found[0]),
                )
            )
    substitute = WeaponState(
        name=no_modelled_attack_name(beast.strength),
        damage=bare_handed_damage(beast.strength),
    )
    if melee:
        substitute = WeaponState(
            name=substitute.name,
            weapon_class=melee[0]["weapon_class"],
            damage=substitute.damage,
        )
    return BeastAttack(substitute, substituted=True)


def beast_movement_modifier(movement: int) -> int:
    """The movement modifier movement.md's Run row implies for a full move.

    Run is ``12 + modifier`` for a character; a beast's catalog movement is
    its run (module docstring), so its modifier is the difference. A rooted
    beast (0) reads as the slowest thing on the field, which it is.
    """
    run = engine_gait_names()["run"]
    return movement - equipment_rules().gaits.base_distances[run]


def snapshot_beast(
    beast: Beast,
    combatant_id: int,
    name: str,
    position: tuple[int, int],
    facing: int,
) -> CombatantState:
    """Freeze one Beast into the engine's combatant state."""
    walk, jog, run = beast_gaits(beast.movement)
    return CombatantState(
        combatant_id=combatant_id,
        name=name,
        archetype=BEAST_ARCHETYPE,
        strength=beast.strength,
        dexterity=beast.dexterity,
        intelligence=beast.intelligence,
        wisdom=beast.wisdom,
        constitution=beast.constitution,
        max_fatigue=beast.fatigue_pool,
        max_body=beast.body_pool,
        fatigue=beast.fatigue_pool,
        body=beast.body_pool,
        q=position[0],
        r=position[1],
        facing=facing,
        weapon=beast_attack(beast).weapon,
        weapon_skill_level=0,
        armour_tier=beast.armour_tier,
        stops=beast.stops,
        shield_bonus=0,
        move_walk=walk,
        move_jog=jog,
        move_run=run,
        # No Sprint, and the modifier the Run row implies (module docstring).
        move_sprint=0,
        movement_modifier=beast_movement_modifier(beast.movement),
        size_hexes=beast.size_hexes,
        is_beast=True,
    )
