"""Orchestration for one resolved attack — the caller of ``characters.combat``.

Issue #101: the d20 engine in :mod:`characters.combat` was fully tested and
entirely unused. This module is the production caller. It takes a character, a
dict of already-parsed request parameters, and a ``roller`` (any object with
``characters.dice``'s three logged-roll functions), and returns one itemized
payload: every number the sheet's Attack Resolution panel prints, computed
here so the browser only formats what the server decided.

Two deliberate boundaries:

* **No Django imports beyond the model's own duck type.** The service reads
  attributes off the character and raises :class:`AttackValidationError`; the
  view turns that into the house ``_json_error`` shape. That keeps the whole
  breakdown unit-testable with a scripted roller and no HTTP.
* **Dice are injected.** ``roller`` defaults to :mod:`characters.dice`; tests
  pass a stub that returns a scripted sequence, so every branch (crit, confirm
  hit/miss, fumble, hybrid bypass) is exercised deterministically.

Modifier sourcing, deliberately narrow: the to-hit and damage modifiers used
here are **the selected item's own** ``to_hit_mod`` / ``damage_mod``, never
``Character.weapon_to_hit_mod`` / ``weapon_damage_mod``. Those properties read
the weapon in hand (issue #846; they used to sum every weapon carried), and the
panel may resolve a swing with another weapon the player picks, so the swing
reads the weapon swung.
"""

from types import ModuleType
from typing import Protocol

from common.helpers import whole_number

from . import combat, dice
from .constants import MAX_ARMOR_STOPS
from .equipment import get_item, get_shield_bonus
from .equipment_rules import equipment_rules
from .skill_checks import injury_penalty, unconscious_refusal
from .weapon_skill import resolve_weapon_skill

# The bound on the situational modifier the GM supplies: the band issue #101
# flagged as never exercised by any caller. The other number the GM supplies,
# the defender's dodge, is bounded by the loaded canon's
# ``equipment-rules.json`` ``defender_dodge`` (issue #75).
MAX_SITUATIONAL = 20


def damage_is_rollable(item_data: dict) -> bool:
    """Is this catalog weapon's ``damage`` an expression ``dice`` can roll?

    Five catalog weapons carry a ``weapon_class`` but no rollable damage: club
    and lasso say "varies", cestus "by STR", torch (weapon) "1d6 fire", and
    bola has no ``damage`` at all. Those entries are correct — the rules leave
    those weapons' damage to the GM — but there is nothing for
    :func:`characters.dice.parse_dice_expression` to parse, and calling it
    anyway would raise a bare ``ValueError`` that no caller here is catching.

    Both layers consult this: ``CharacterEditView`` omits these weapons from
    the panel's select (and names them in a footnote), and
    :func:`_selected_weapon` refuses one that arrives anyway.
    """
    damage = item_data.get("damage")
    if not isinstance(damage, str):
        return False
    try:
        dice.parse_dice_expression(damage)
    except ValueError:
        return False
    return True


class AttackValidationError(ValueError):
    """A caller-supplied attack parameter is missing, mistyped or out of range.

    Carries the message the endpoint shows the player verbatim, so the
    validation wording lives with the rule it enforces rather than in the view.
    """


def _bounded_int(params, key, minimum, maximum, label, default=0):
    """Read ``key`` off ``params`` as an int within ``[minimum, maximum]``.

    An absent or empty key means ``default``: the optional modifiers' neutral
    value, 0, or for the defender's dodge the canon's least (issue #75).
    Every value, the default included, is held to the band, so a canon whose
    band leaves out the default is refused rather than read. Anything present
    but unparseable or out of band raises rather than being coerced, so a typo
    can never quietly change a roll.
    """
    raw = params.get(key)
    if raw is None or raw == "":
        value = default
    else:
        try:
            value = whole_number(raw)
        except ValueError:
            raise AttackValidationError(f"Invalid {label}") from None
    if not minimum <= value <= maximum:
        raise AttackValidationError(f"{label} must be between {minimum} and {maximum}")
    return value


def _selected_weapon(character, weapon_index):
    """The equipment entry at ``weapon_index``, if it is a catalog weapon.

    Returns ``(index, equipment_item, catalog_data, weapon_class)``.

    Raises:
        AttackValidationError: if the index misses, or the item is not a
            catalog weapon with a ``weapon_class``. Custom weapons have no
            catalog entry and therefore no class, so they are refused here with
            a message rather than crashing on a ``MATRIX`` KeyError later.
    """
    equipment = character.equipment or []
    try:
        index = whole_number(weapon_index)
    except ValueError:
        raise AttackValidationError("Invalid weapon index") from None
    if index < 0 or index >= len(equipment):
        raise AttackValidationError("Invalid weapon index") from None

    item = equipment[index]
    item_data = get_item(item.get("item_id", ""))
    if item_data is None:
        # A custom item: no catalog entry, therefore no weapon class, therefore
        # no row in the §6 matrix. Refused here with a message rather than
        # allowed through to a KeyError deeper in.
        raise AttackValidationError(
            "A custom item has no combat class, so it has no Target Number"
        )
    if item_data.get("category") != "weapons":
        raise AttackValidationError("That item is not a weapon")
    weapon_class = item_data.get("weapon_class")
    if not weapon_class or weapon_class not in combat.MATRIX:
        raise AttackValidationError(
            "That weapon has no combat class, so it has no Target Number"
        )
    # Defence in depth: the panel does not offer these, but a hand-rolled
    # request could still name one, and letting it through would raise a plain
    # ValueError out of the dice parser — a 500, not a 400.
    if not damage_is_rollable(item_data):
        raise AttackValidationError(
            "This weapon's damage is GM-adjudicated, so it cannot be resolved here"
        )
    return index, item, item_data, weapon_class


def _validated_armour_tier(params):
    tier = params.get("armour_tier") or "None"
    if tier not in combat.ARMOUR_TIERS:
        raise AttackValidationError(f"Unknown armour tier: {tier}")
    return tier


def _validated_shield(params):
    """``(shield_item_id, shield_bonus)`` for the defender's shield, or none.

    The bonus is looked up from the catalog rather than accepted as a number,
    so a client cannot type its own Target Number adjustment.
    """
    shield_item_id = params.get("shield_item_id") or ""
    if not shield_item_id:
        return "", 0
    # A catalog id is text; a list here was an unhashable dict key (#767).
    if not isinstance(shield_item_id, str):
        raise AttackValidationError("Unknown shield")
    # The catalog's singular "shield" — the JSON's top-level *group* is
    # "shields" but each item's own category is not.
    shield_data = get_item(shield_item_id)
    if shield_data is None or shield_data.get("category") != "shield":
        raise AttackValidationError(f"Unknown shield: {shield_item_id}")
    return shield_item_id, get_shield_bonus(shield_item_id)


def _roll_damage(roller, expression, multiplier, damage_mod):
    """Roll ``expression``'s dice ``multiplier`` times, adding its modifiers once.

    attack-rolls.md: a critical rolls "the weapon's damage **dice** twice" (a
    confirmed severe one, three times). The first roll is the whole catalog
    expression, modifier included; every later roll is the dice alone, so a
    mace's ``2d6-1`` counts its -1 once on a critical, not twice (issue #875,
    #865). This is how tarmar-engine 0.9.5's ``resolve_attack`` rolls the same
    swing on the battle board (#824, the severe case read the same way:
    coordinator's ruling under the standing rule, Spencer may overrule), so the
    sheet and the board agree. The item's own ``damage_mod`` is likewise added
    once.

    Returns ``(rolls, raw_total, log_lines)``. ``rolls`` is the flat list of
    individual dice across every repetition, so the panel can show what was
    actually thrown on a crit. A multiplier of 0 (a miss) rolls nothing.
    """
    rolls: list[int] = []
    raw_total = 0
    log_lines: list[str] = []
    dice_count, dice_sides, expression_modifier = dice.parse_dice_expression(expression)
    dice_only = expression if expression_modifier == 0 else f"{dice_count}d{dice_sides}"
    for repetition in range(multiplier):
        repetition_expression = expression if repetition == 0 else dice_only
        repetition_rolls, repetition_total, log = roller.roll_expression_logged(
            repetition_expression
        )
        rolls.extend(repetition_rolls)
        raw_total += repetition_total
        label = f"damage {repetition + 1}/{multiplier}" if multiplier > 1 else "damage"
        note = (
            " (dice only: the modifier counts once)"
            if repetition and expression_modifier
            else ""
        )
        log_lines.append(f"{label} {log}{note}")
    if multiplier:
        raw_total += damage_mod
        if damage_mod:
            log_lines.append(f"weapon damage modifier: {damage_mod:+d}")
    return rolls, max(0, raw_total), log_lines


class AttackRoller(Protocol):
    """The three logged rolls an attack makes; :mod:`characters.dice` is one."""

    def roll_d20_logged(self) -> tuple[int, str]: ...

    def roll_d6_logged(self) -> tuple[int, str]: ...

    def roll_expression_logged(self, expression: str) -> tuple[list[int], int, str]: ...


def resolve(character, params, roller: AttackRoller | ModuleType = dice) -> dict:
    """Resolve one attack and return the full itemized breakdown.

    Args:
        character: The attacking :class:`~characters.models.Character`.
        params: Already-parsed request parameters — ``weapon_index``,
            ``armour_tier``, ``shield_item_id``, ``defender_stops``,
            ``defender_dodge``, ``situational``. Every one is validated here.
            The skill is not a parameter: which skill applies to the weapon
            in hand is the character record's to say, through
            :func:`characters.weapon_skill.resolve_weapon_skill` — the same
            function the battle board seats a fighter with (issue #753).
        roller: Anything exposing ``roll_d20_logged``, ``roll_d6_logged`` and
            ``roll_expression_logged``. Defaults to :mod:`characters.dice`.

    Returns:
        A dict with ``weapon``, ``defender``, ``target_number``, ``to_hit``,
        ``attack_roll``, ``confirm``, ``damage``, ``fumble``, ``severe``,
        ``bleeding``, ``reaches_body``, ``dice_log`` and ``log_entry``.

    Raises:
        AttackValidationError: on any bad parameter. The message is safe to
            show the player.
    """
    # An unconscious or dead character swings at nothing (the #840 review):
    # the one roll left to the unconscious is the survival save.
    unconscious = unconscious_refusal(character)
    if unconscious is not None:
        raise AttackValidationError(unconscious)
    weapon_index, item, item_data, weapon_class = _selected_weapon(
        character, params.get("weapon_index")
    )
    attack_weapon_id = item.get("item_id", "")
    weapon_skill = resolve_weapon_skill(character.skills, attack_weapon_id)
    armour_tier = _validated_armour_tier(params)
    shield_item_id, shield_bonus = _validated_shield(params)
    defender_stops = _bounded_int(
        params, "defender_stops", 0, MAX_ARMOR_STOPS, "Defender stops"
    )
    dodge_bounds = equipment_rules().defender_dodge
    defender_dodge = _bounded_int(
        params,
        "defender_dodge",
        dodge_bounds.minimum,
        dodge_bounds.maximum,
        "Defender dodge",
        default=dodge_bounds.minimum,
    )
    situational = _bounded_int(
        params, "situational", -MAX_SITUATIONAL, MAX_SITUATIONAL, "Situational modifier"
    )

    # --- The two numbers the d20 is measured against ---------------------
    base_target_number = combat.MATRIX[weapon_class][armour_tier]
    total_target_number = combat.target_number(
        weapon_class,
        armour_tier,
        shield_bonus=shield_bonus,
        defender_dodge=defender_dodge,
    )

    # This item's own modifier, not the inventory-wide sum (see module docstring).
    weapon_to_hit_mod = int(item.get("to_hit_mod", 0) or 0)
    weapon_damage_mod = int(item.get("damage_mod", 0) or 0)

    dex_modifier = combat.dex_modifier(character.combat_dexterity)
    strength_fit_penalty = combat.strength_fit_penalty(
        character.effective_strength, item_data.get("str_req")
    )
    # The injury table's -1 or -2 to all rolls (issue #840), as the board's
    # attack roll takes it (tarmar_engine's _attack_roll_penalty).
    injured = injury_penalty(character)
    core_bonus = (
        dex_modifier + weapon_skill.bonus + strength_fit_penalty + situational + injured
    )
    total_bonus = core_bonus + weapon_to_hit_mod

    # --- Dice -------------------------------------------------------------
    attack_die, attack_log = roller.roll_d20_logged()
    confirm_die = confirm_log = None
    fumble_die = fumble_log = None
    if attack_die == combat.DIE_FACES:
        confirm_die, confirm_log = roller.roll_d20_logged()
    elif attack_die == 1:
        fumble_die, fumble_log = roller.roll_d6_logged()

    outcome = combat.resolve_attack(
        attack_die,
        total_target_number,
        total_bonus,
        confirm_roll=confirm_die,
        fumble_roll=fumble_die,
    )

    injured_note = f" (includes {injured:+d} injured)" if injured else ""
    dice_log = [
        f"attack {attack_log} {total_bonus:+d}{injured_note} = {outcome['total']} "
        f"vs TN {total_target_number} -> {outcome['outcome']}"
    ]
    confirm = None
    if outcome["confirm"] is not None:
        confirm = dict(outcome["confirm"], log=confirm_log)
        dice_log.append(
            f"confirm {confirm_log} {total_bonus:+d} = {confirm['total']} "
            f"vs TN {total_target_number} -> "
            f"{'confirmed' if confirm['hit'] else 'not confirmed'}"
        )
    fumble = None
    if outcome["fumble_detail"] is not None:
        fumble = dict(outcome["fumble_detail"], log=fumble_log)
        dice_log.append(f"fumble {fumble_log} -> {fumble['label']}")

    # --- Damage -----------------------------------------------------------
    multiplier = outcome["damage_multiplier"]
    # Guaranteed parseable: _selected_weapon refused anything else.
    damage_expression = item_data["damage"]
    damage_rolls, raw_total, damage_log = _roll_damage(
        roller, damage_expression, multiplier, weapon_damage_mod
    )
    dice_log.extend(damage_log)
    applied_stops = combat.applied_armour_stops(
        defender_stops, weapon_class, armour_tier
    )
    final_damage = combat.damage_after_armour(
        raw_total, defender_stops, weapon_class, armour_tier
    )
    if multiplier:
        dice_log.append(f"armour: {raw_total} - {applied_stops} stops = {final_damage}")

    result = {
        "weapon": {
            "index": weapon_index,
            "item_id": attack_weapon_id,
            "name": item_data.get("name", attack_weapon_id),
            "weapon_class": weapon_class,
            "damage_expression": damage_expression,
            "str_req": item_data.get("str_req"),
            "to_hit_mod": weapon_to_hit_mod,
            "damage_mod": weapon_damage_mod,
        },
        "defender": {
            "armour_tier": armour_tier,
            "shield_item_id": shield_item_id,
            "shield_bonus": shield_bonus,
            "stops": defender_stops,
            "dodge": defender_dodge,
        },
        "skill": {
            "name": weapon_skill.skill_name,
            "level": weapon_skill.level,
            "weapon_id": weapon_skill.trained_weapon_id,
            "rule": weapon_skill.rule,
        },
        "target_number": {
            "base": base_target_number,
            "shield_bonus": shield_bonus,
            "defender_dodge": defender_dodge,
            "total": total_target_number,
        },
        "to_hit": {
            "dex_modifier": dex_modifier,
            "skill_bonus": weapon_skill.bonus,
            "strength_fit_penalty": strength_fit_penalty,
            "situational": situational,
            "injury_penalty": injured,
            "weapon_mod": weapon_to_hit_mod,
            "total": total_bonus,
        },
        "attack_roll": {
            "die": attack_die,
            "log": attack_log,
            "total": outcome["total"],
            "outcome": outcome["outcome"],
            "hit": outcome["hit"],
            "critical": outcome["critical"],
            "fumble": outcome["fumble"],
        },
        "confirm": confirm,
        "damage": {
            "rolls": damage_rolls,
            "multiplier": multiplier,
            "raw_total": raw_total,
            "weapon_mod": weapon_damage_mod,
            "stops": defender_stops,
            "applied_stops": applied_stops,
            "hybrid_bypass": combat.hybrid_bypass_applies(weapon_class, armour_tier),
            "final": final_damage,
        },
        "fumble": fumble,
        "severe": outcome["severe"],
        "bleeding": outcome["bleeding"],
        # Report-only, exactly as the spec's §7 note says: nothing here
        # persists damage or ticks bleeding. The character's current Fatigue
        # and Body are on the record (#751), written by a fight on the board
        # and set by a Game Master; one roll resolved on the sheet is not a
        # ruling on either.
        #
        # Damage applies to Fatigue first (attack-rolls.md, Damage and
        # Armour); only a severe critical also reaches Body. Matches
        # tarmar_engine.engine.apply_damage's reaches_body=result["severe"].
        "reaches_body": outcome["severe"] and final_damage > 0,
        "dice_log": dice_log,
    }
    return result


def log_entry(result: dict, username: str, timestamp: str) -> dict:
    """The compact record of ``result`` that ``Character.combat_log`` stores.

    Only server-decided values, and only the ones the sheet's log section
    renders — the full breakdown is far too big to keep twenty of.
    """
    damage = result["damage"]
    return {
        "ts": timestamp,
        "by": username,
        "weapon": result["weapon"]["name"],
        "armour_tier": result["defender"]["armour_tier"],
        "outcome": result["attack_roll"]["outcome"],
        "die": result["attack_roll"]["die"],
        "total": result["attack_roll"]["total"],
        "target_number": result["target_number"]["total"],
        "damage": damage["final"],
        "fumble": result["fumble"]["label"] if result["fumble"] else "",
    }
