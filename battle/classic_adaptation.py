"""A studio roster becomes a classic *Melee* fight (issue #251, slice 6).

Slice 5 taught this app's turn loop to advance either profile's state, but
its tests built classic snapshots by hand: nothing turned a studio
:class:`~characters.models.Character` into a classic
:class:`~tarmar_engine.classic.figure.Figure`, so no one could start a
classic battle from the UI. This module is that missing path.

**It translates the Tarmar snapshot, not the Character row.** A character's
readied weapon, governing armour tier, shield bonus and combat attributes are
already worked out once, by :func:`battle.services.snapshot_character`, into
a :class:`~battle.engine.state.CombatantState`. Reading the equipment list a
second time here would be a second opinion about what a character is holding
— the exact duplication this unification exists to kill. So the seam is
``CombatantState -> Figure``, which also means a *beast* (``snapshot_beast``
produces the same dataclass) crosses into a classic fight for free.

What the two systems agree on, and what they cannot:

* **Attributes.** ST, DX and IQ map straight across from the snapshot's
  strength, dexterity and intelligence. Both systems roll d6s under an
  attribute, and studio characters are rolled on the same 3-18 span classic
  figures live on, so rescaling would invent precision neither has. Two
  edges are real, and named rather than smoothed: a classic figure must have
  ST and DX of at least 1 (a studio combatant ground to 0 by encumbrance or
  age is floored, since classic has no "cannot act at all" attribute value),
  and the snapshot's DX is already the studio's *encumbered* dexterity while
  classic subtracts its own armour DX penalty when it computes adjDX — so an
  armoured figure pays under both systems' accounting. Carrying the
  unencumbered DX across would need the snapshot to record it, which is a
  change to the Tarmar side and so not this slice's to make.
* **Injury.** A classic figure has no second pool: damage lands on ST and
  the ST thresholds decide knockdown, unconsciousness and death. The
  snapshot's ``fatigue``/``body`` therefore do **not** carry — a classic
  battle is fought under classic injury or it is not classic. Named here
  because it is the mapping's biggest single loss.
* **Movement.** Classic MA is a property of armour (``Armor.movement_allowance``),
  not of the figure, so the snapshot's walk/jog/run gaits have nowhere to go.
* **Magic.** No spell crosses. The studio's spells are Tarmar's, and the
  spell-canon ruling makes melee's 14-spell suite classic-only; mapping one
  catalog onto the other would be inventing canon rather than adapting data.
  What crosses is the *fact of being a caster*: a studio caster is built as
  a classic **wizard** under classic chargen (:func:`wizard_spells`), the
  same move this module already makes for weapons and armour — translate by
  the destination's own rules rather than by name identity. It costs the
  wizard its shield and its readied weapon, which classic forbids a caster
  outright (Wizard p.23); :func:`classic_figures` names who crossed this way.

Per the unification plan's copyright note the SJG-derived subpackage stays
quarantined: every import of it is inside the function that needs it, so a
Tarmar-only battle never pulls it in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import policy
from .engine.state import CombatantState, WeaponState

if TYPE_CHECKING:
    # Types only: the classic subpackage stays out of every import chain
    # until a function that needs it runs.
    from tarmar_engine.classic.figure import Figure, SideLabel

#: The studio armour tier a classic armour stands in for. The studio grades
#: armour in four tiers; classic lists six pieces. Cloth and half-plate have
#: no tier of their own to be picked by, so they are reachable only by a
#: character whose armour is *named* for them (see :func:`classic_armor`).
ARMOR_BY_TIER: dict[str, str] = {
    "None": "None",
    "Light": "Leather",
    "Medium": "Chainmail",
    "Heavy": "Plate",
}

#: Shield bonus (studio TN points) to the classic shield that stops as much.
#: Classic offers exactly three, so anything above a large shield's bonus is
#: a large shield.
SHIELD_BY_BONUS: dict[int, str] = {0: "None", 1: "Small shield", 2: "Large shield"}

#: What a figure falls back to when nothing in the classic catalog fits: the
#: rulebook's own answer for an unarmed or under-equipped fighter.
FALLBACK_WEAPON = "Dagger"

#: The lowest ST or DX a classic figure may be built with: its own
#: constructor refuses 0, and the studio's attributes can reach it.
MIN_CLASSIC_ATTRIBUTE = 1

#: A teamless combatant is its own side, which makes a teamless classic
#: battle here the free-for-all a studio battle already is (classic
#: initiative is per-character adjDX order, not per-side, so sides govern
#: only who counts as an enemy). A free-for-all figure's side is its uid,
#: so ``enemies_of`` reads "everyone else"; a TEAMED combatant (#275)
#: crosses with its team as its side (spelled by :func:`classic_side`), so
#: classic's own side semantics carry the no-friendly-fire rule.
SIDE_IS_UID = True

#: What a teamed figure's classic side starts with (#930). A uid is a
#: ``BattleCombatant`` row pk (``CLASSIC_UID_IS_ROW_PK``), all digits, so a
#: side that starts with this can never equal one. Without it a team named
#: "7" crossed with side "7", the same side as the teamless figure in row 7:
#: the engine read the two as allies and :func:`classic_team` read a row-7
#: member of team "7" as teamless. The team's name is everything after the
#: prefix, so no team name, digits included, is refused or renamed.
TEAM_SIDE_PREFIX = "team:"

#: What a log line calls a nameless teamless figure's side, by its 1-based
#: place in the roster (#909). The roster's order is fixed for the battle,
#: so the label is too, where a row id would mean nothing to a reader.
NAMELESS_SIDE_LABEL = "Side {position}"


def classic_side(team: str, combatant_id: int) -> str:
    """The classic side a studio combatant crosses with (#930).

    The one place a side is spelled; :func:`classic_team` is the one place
    it is read back.

    Args:
        team: The combatant's team name, or ``""`` for none.
        combatant_id: The combatant's ``BattleCombatant`` row pk, which is
            also the figure's uid.

    Returns:
        :data:`TEAM_SIDE_PREFIX` and the team's name for a teamed combatant;
        else the combatant's own uid (:data:`SIDE_IS_UID`).
    """
    if team:
        return f"{TEAM_SIDE_PREFIX}{team}"
    return str(combatant_id) if SIDE_IS_UID else "arena"


def classic_team(figure) -> str:
    """The team a classic figure fights for, or ``""`` when it has none.

    The one decoding of a side spelled by :func:`classic_side`: the prefixed
    form names a team, and a side equal to the figure's own uid names none.
    Battles saved before #930 spelled a team's side as its bare name; the
    data migration ``battle.0017_classic_team_sides_prefixed`` (#944)
    rewrote every stored one, so no other spelling is read.

    Args:
        figure: A classic figure, with ``side`` and ``uid``.

    Returns:
        The team's own name, never the side key; ``""`` for a teamless figure.

    Raises:
        ValueError: For a side :func:`classic_side` cannot have spelled, which
            would otherwise be guessed at and could change who fights whom.
    """
    side = figure.side
    if side.startswith(TEAM_SIDE_PREFIX):
        return side[len(TEAM_SIDE_PREFIX) :]
    if side == figure.uid:
        return ""
    raise ValueError(
        f"classic figure {figure.uid!r} has side {side!r}, which is neither "
        f"{TEAM_SIDE_PREFIX!r} and a team's name nor its own uid"
    )


def side_label(figure: Figure, roster_position: int) -> SideLabel:
    """What a reader calls the side ``figure`` fights on (#909), as the
    engine's :class:`~tarmar_engine.classic.figure.SideLabel` (tarmar-engine
    #25).

    A teamed figure's side is its team, named in the parens: "Ada (Red)",
    "The Red hold the field". A teamless figure is a side of one, named by
    its own name, or :data:`NAMELESS_SIDE_LABEL` by its 1-based
    ``roster_position`` when it has none: "Yaria Hawkridge swings", "Yaria
    Hawkridge holds the field". Either way the side key, a row pk for a
    teamless figure (``CLASSIC_UID_IS_ROW_PK``) or :data:`TEAM_SIDE_PREFIX`
    and the team's name for a teamed one, never reaches a reader.

    Args:
        figure: The classic figure.
        roster_position: Its 1-based place in the battle's roster.

    Returns:
        The label to give the figure.
    """
    from tarmar_engine.classic.figure import SideLabel

    team = classic_team(figure)
    if team:
        return SideLabel(team, team=True)
    return SideLabel(
        figure.name.strip() or NAMELESS_SIDE_LABEL.format(position=roster_position),
        team=False,
    )


def label_sides(figures: list[Figure]) -> None:
    """Give each classic figure its :func:`side_label`, by its place in
    ``figures``, the battle's roster order.

    Labelled when the figures are built (:func:`classic_figures`) and again
    before every turn (``ClassicProfile.run_turn``), from the figures as they
    stand: a battle snapshotted before tarmar-engine 0.9.19 loads unlabelled,
    and a figure renamed mid-battle (``ClassicProfile.rename``) is a side of
    one whose label is its name, so a label kept from an earlier turn would
    print a name the figure no longer has. The label is derived, never
    edited on its own.

    Args:
        figures: The battle's classic figures, in roster order.
    """
    for roster_position, figure in enumerate(figures, start=1):
        figure.side_label = side_label(figure, roster_position)


@dataclass(frozen=True)
class ClassicRoster:
    """The figures for a classic battle, and what the crossing cost.

    Attributes:
        figures: The classic figures, in roster order.
        disarmed: Display names whose readied weapon had no classic
            counterpart and who now carry :data:`FALLBACK_WEAPON`.
        wizards: Display names that crossed as classic wizards — casters in
            the studio, rebuilt by classic chargen (the module docstring's
            magic note). Their studio spell lists did not come with them.
        unspelled: Display names of casters that reached no classic spell at
            all, their IQ sitting below the catalog's lowest tier. They fight
            as warriors, which is the one honest answer left.
    """

    figures: list
    disarmed: tuple[str, ...] = ()
    wizards: tuple[str, ...] = ()
    unspelled: tuple[str, ...] = ()


def _average_damage(dice_expression: str) -> float:
    """Mean roll of a studio damage string, or 0.0 when it will not parse."""
    from characters.dice import parse_dice_expression

    try:
        count, sides, modifier = parse_dice_expression(dice_expression)
    except (ValueError, TypeError):
        return 0.0
    return count * (sides + 1) / 2 + modifier


def _classic_average(weapon) -> float:
    """Mean roll of a classic weapon's d6 damage."""
    return weapon.damage.count * 3.5 + weapon.damage.modifier


def classic_armor(tier: str, stops: int):
    """The classic armour a studio combatant wears.

    The tier is the studio's own grading and governs; ``stops`` breaks the
    tie the tier cannot, since classic armour is *defined* by what it stops.
    Among the pieces mapped to no tier (cloth, half-plate) this never picks —
    a tier always resolves — which is the documented cost of grading armour
    in four bands and listing it in six.
    """
    from tarmar_engine.classic.data import ARMORS

    named = ARMORS.get(ARMOR_BY_TIER.get(tier, "None"), ARMORS["None"])
    if named.stops == stops:
        return named
    # Same tier, different stopping power: prefer the piece that actually
    # stops what this combatant's armour stops, so a studio "Heavy" that
    # stops 4 lands on half-plate rather than being inflated to plate.
    exact = [armor for armor in ARMORS.values() if armor.stops == stops]
    return exact[0] if exact else named


def classic_shield(shield_bonus: int):
    """The classic shield standing in for a studio shield bonus."""
    from tarmar_engine.classic.data import SHIELDS

    if shield_bonus <= 0:
        return SHIELDS["None"]
    return SHIELDS[SHIELD_BY_BONUS.get(shield_bonus, "Large shield")]


def wizard_spells(intelligence: int) -> list[str]:
    """The classic spells a crossing caster knows, by classic chargen.

    Classic's own two bounds on a wizard's spell list, as melee's chargen
    validator states them (Wizard p.23): every spell's IQ tier is within the
    wizard's IQ, and it knows at most IQ of them. Nothing here consults the
    studio's spell list — under the spell-canon ruling the two catalogs never
    map onto each other; this is a *build*, not a translation.

    Lowest tier first (ties by spell id, so the hand is a function of IQ
    alone and a battle replays identically). That order is also why a wizard
    of any castable IQ knows Staff, the IQ 8 spell that starts it staff in
    hand — the one weapon it may hold and still cast (p.19, p.23). Below the
    catalog's lowest tier the list is empty, and the figure is a fighter that
    happens to have been a caster elsewhere.
    """
    from tarmar_engine.classic.spells import SPELLS

    legal = [spell for spell in SPELLS.values() if spell.iq_tier <= intelligence]
    legal.sort(key=lambda spell: (spell.iq_tier, spell.id))
    return [spell.id for spell in legal[: max(intelligence, 0)]]


def classic_weapon(weapon: WeaponState, strength: int):
    """The classic weapon a studio combatant fights with, and whether it fit.

    Three steps, most faithful first:

    1. **By name, if this figure can lift it.** The two catalogs overlap
       heavily (broadsword, spear, dagger, longbow …); a name match is the
       same weapon, not a guess. Classic gates weapons on starting ST and a
       :class:`~tarmar_engine.classic.figure.Figure` refuses to be built
       holding one it cannot wield, so a studio combatant too weak for its
       own weapon's classic twin still has to fall through — the studio's
       strength requirements are its own and do not line up with these.
    2. **By damage, within the same kind.** A studio weapon with no usable
       classic twin becomes the wieldable classic weapon of its own kind
       (melee, thrown, missile) whose average damage is closest. Kind is kept
       because swapping a bow for a mace changes what the figure can *do*,
       not just how hard it hits.
    3. **A dagger**, when nothing of that kind is wieldable at this ST.

    Returns:
        ``(weapon, matched_by_name)`` — the flag is what
        :class:`ClassicRoster` reports as a disarming, so falling through for
        either reason (no twin, or too heavy) is reported the same way.
    """
    from tarmar_engine.classic.data import WEAPONS, WeaponKind

    by_name = {name.lower(): value for name, value in WEAPONS.items()}
    named = by_name.get((weapon.name or "").strip().lower())
    if named is not None and named.min_strength <= strength:
        return named, True

    if weapon.is_missile:
        wanted = {WeaponKind.MISSILE}
    elif weapon.is_thrown:
        wanted = {WeaponKind.THROWN, WeaponKind.MELEE}
    else:
        wanted = {WeaponKind.MELEE, WeaponKind.POLE}
    target = _average_damage(weapon.damage)
    usable = [
        candidate
        for candidate in WEAPONS.values()
        if candidate.kind in wanted and candidate.min_strength <= strength
    ]
    if not usable:
        return WEAPONS[FALLBACK_WEAPON], False
    closest = min(usable, key=lambda one: abs(_classic_average(one) - target))
    return closest, False


def classic_figure(snapshot: CombatantState, *, position, facing: int):
    """One studio combatant as a classic figure, placed and facing.

    A caster (``snapshot.spells``) is built as a classic **wizard**: the
    IQ-legal hand :func:`wizard_spells` gives it, no shield, and no readied
    weapon but the staff its Staff spell grants. Those last two are not
    tidying — classic refuses a cast from a figure holding a shield or any
    non-staff weapon (Wizard p.23), so a wizard that kept the studio's
    broadsword readied would know spells it could never cast. It still
    *carries* its weapon and can ready it, at the cost of its magic, exactly
    as a classic wizard may.

    The figure's ``uid`` is ``str(snapshot.combatant_id)`` — the contract
    :data:`battle.profiles.CLASSIC_UID_IS_ROW_PK` states, and the reason this
    function takes a snapshot already bound to a saved ``BattleCombatant``
    row rather than minting ids of its own.
    """
    from tarmar_engine.classic.data import DAGGER, SHIELDS
    from tarmar_engine.classic.figure import Figure, create_wizard

    strength = max(MIN_CLASSIC_ATTRIBUTE, snapshot.strength)
    weapon, _matched = classic_weapon(snapshot.weapon, strength)
    carried = [weapon] if weapon.name == DAGGER.name else [weapon, DAGGER]
    common = {
        "name": snapshot.name,
        "strength": strength,
        "dexterity": max(MIN_CLASSIC_ATTRIBUTE, snapshot.dexterity),
        "side": classic_side(snapshot.team, snapshot.combatant_id),
        "intelligence": max(MIN_CLASSIC_ATTRIBUTE, snapshot.intelligence),
        "armor": classic_armor(snapshot.armour_tier, snapshot.stops),
        "weapons": carried,
        "char_class": snapshot.archetype,
        "position": position,
        "facing": facing,
        "uid": str(snapshot.combatant_id),
    }
    hand = wizard_spells(common["intelligence"]) if snapshot.spells else []
    if hand:
        # ``create_wizard`` readies the staff itself when the Staff spell is
        # known (p.19), which is why no ready_weapon is named here.
        return create_wizard(spells_known=hand, shield=SHIELDS["None"], **common)
    # Including a caster whose IQ sits below the classic catalog's lowest
    # tier: it learns no classic spell, so it is a fighter here and keeps
    # the shield and readied weapon a wizard would have given up. Stripping
    # those for a figure that cannot cast would send it in unarmed.
    return Figure(
        shield=classic_shield(snapshot.shield_bonus),
        ready_weapon=weapon,
        **common,
    )


def classic_figures(snapshots: list[CombatantState], arena) -> ClassicRoster:
    """Every studio combatant as a placed classic figure.

    Figures are spread evenly around the arena's rim facing its centre, the
    same shape :func:`battle.services.starting_positions` gives a Tarmar
    battle — classic's two entrance hexes seat a duel, not the free-for-all
    roster this app builds.
    """
    disarmed: list[str] = []
    wizards: list[str] = []
    unspelled: list[str] = []
    figures = []
    placements = rim_placements(arena, len(snapshots))
    for snapshot, (position, facing) in zip(snapshots, placements, strict=True):
        figure = classic_figure(snapshot, position=position, facing=facing)
        figures.append(figure)
        # What the figure BECAME, not what it was asked to be: a caster whose
        # IQ reaches no classic spell crosses as a fighter, and saying it
        # entered as a wizard would be a nicer sentence than the truth.
        if figure.spells_known:
            wizards.append(snapshot.name)
            continue
        if snapshot.spells:
            unspelled.append(snapshot.name)
        # A wizard's readied weapon is its staff, so a re-arming is only
        # reported for the figures that carry the substitute into the fight.
        _weapon, matched = classic_weapon(
            snapshot.weapon, max(MIN_CLASSIC_ATTRIBUTE, snapshot.strength)
        )
        if not matched:
            disarmed.append(snapshot.name)
    # Each side named as a reader knows it, by the engine (tarmar-engine #25).
    label_sides(figures)
    return ClassicRoster(
        figures=figures,
        disarmed=tuple(disarmed),
        wizards=tuple(wizards),
        unspelled=tuple(unspelled),
    )


#: The rulebook arena (Section V, p.9) — the floor a classic fight is sized
#: against. A larger roster grows it rather than crowding onto the same field.
BASE_ARENA_COLS = 9
BASE_ARENA_ROWS = 15

#: Combatants the rulebook arena seats before it has to grow. Above this the
#: arena widens by a column per extra pair, keeping the rim long enough that
#: figures do not start already engaged.
ARENA_SEATS = 6


def classic_arena(count: int):
    """The arena a classic battle of ``count`` combatants is fought on."""
    from tarmar_engine.classic.arena import Arena

    extra = max(0, count - ARENA_SEATS)
    return Arena(cols=BASE_ARENA_COLS + extra, rows=BASE_ARENA_ROWS)


def arena_rim(arena) -> list:
    """The arena's perimeter hexes, walked as one cycle.

    Clockwise from the north-west corner: the top row, the right column, the
    bottom row reversed, the left column reversed. A cycle rather than a set
    because :func:`rim_placements` spaces figures *along* it.
    """
    from hexarena.hex import Hex

    top = [Hex(col, 1) for col in range(1, arena.cols + 1)]
    right = [Hex(arena.cols, row) for row in range(2, arena.rows + 1)]
    bottom = [Hex(col, arena.rows) for col in range(arena.cols - 1, 0, -1)]
    left = [Hex(1, row) for row in range(arena.rows - 1, 1, -1)]
    return [
        hex_position
        for hex_position in top + right + bottom + left
        if arena.contains(hex_position)
    ]


def rim_placements(arena, count: int) -> list[tuple]:
    """``count`` evenly spaced ``(hex, facing)`` pairs around the arena's rim.

    Facing is toward the arena's middle, so every figure starts looking at
    the fight — the same opening :func:`battle.services.starting_positions`
    gives a Tarmar battle.
    """
    from hexarena.hex import Hex
    from tarmar_engine.classic.facing import facing_toward

    rim = arena_rim(arena)
    if not rim:  # pragma: no cover — an arena with no rim has no fight
        raise ValueError("this arena has no perimeter to place figures on")
    middle = Hex((arena.cols + 1) // 2, (arena.rows + 1) // 2)
    step = len(rim) / count
    placements = []
    for index in range(count):
        position = rim[int(index * step) % len(rim)]
        placements.append((position, facing_toward(arena.layout, position, middle)))
    return placements


def classic_state(snapshots: list[CombatantState], *, seed: int):
    """A ready-to-play classic game state for this roster.

    Args:
        snapshots: The combatants, already bound to saved ``BattleCombatant``
            rows (see :func:`classic_figure` on the uid contract).
        seed: The battle's seed. The state's dice are seeded from it so a
            fresh battle's first turn is reproducible; every later turn
            re-seeds per turn through ``battle.profiles`` as usual.

    Returns:
        ``(state, roster)`` — the state to snapshot into ``Battle.state_json``
        and the :class:`ClassicRoster` describing what the crossing cost.
    """
    from hexarena.dice import Dice
    from tarmar_engine.classic.state import GameState

    arena = classic_arena(len(snapshots))
    roster = classic_figures(snapshots, arena)
    state = GameState(arena, roster.figures, dice=Dice(seed=seed))
    return state, roster


#: The printed name of each classic spell-type code (``Spell.type``, the
#: "(M)/(T)/(C)/(S)" tag after a spell's name in the Spell Table), for the
#: cost line the cast stage prints. The codes are
#: :data:`tarmar_engine.classic.spells.MISSILE` and its siblings.
CLASSIC_SPELL_TYPES = {
    "M": "Missile",
    "T": "Thrown",
    "C": "Creation",
    "S": "Special",
}

#: 3d6 totals above this are an automatic miss whatever the caster's adjDX
#: (``classic.combat.classify_spell_roll``: 16 misses, 17/18 fizzle), so the
#: chance a cast lands is capped there rather than run off the top of the die.
CLASSIC_CAST_CEILING = 15


def classic_caster_magic(state, combatant_id: int) -> dict | None:
    """One classic wizard's magical situation, in the cast stage's shape.

    The classic half of :func:`battle.services.caster_magic`, publishing the
    same profile-neutral payload (#263): a *named* pool — classic pays ST,
    the same pool that holds its hit points, so the stage says "ST 9 of 12"
    and never Tarmar's mana — and a row per offered cast carrying its cost
    and its chance already worded.

    **A row is one cast, not one spell.** ``classic.ai.castable_spells``
    yields one offer per *(spell, target)* pair where Tarmar yields one per
    spell, and a candidate names both; a row per spell would collapse a
    wizard's targets to whichever came last. A spell nothing can be cast at
    gets a single uncastable row saying why, so the wizard's whole hand is
    on the stage either way.

    Every castable row is read off the engine's own offer list rather than
    re-deriving legality, so what the stage shows and what
    :meth:`GameState.queue_spell` accepts cannot drift; only the *reasons*
    for the rows the engine dropped are worked out here, and
    ``ClassicCastableSetAgreesWithPolicyTest`` pins the two sets equal.

    ``None`` for a figure that is not in this battle or is no wizard — the
    same honest absence a fighter gets.
    """
    from tarmar_engine.classic.ai import castable_spells
    from tarmar_engine.classic.spells import SPELLS, spell_cost_for
    from tarmar_engine.classic.state import cast_block_reason

    figure = next((one for one in state.figures if one.uid == str(combatant_id)), None)
    if figure is None or not figure.spells_known:
        return None
    offers: dict[str, list] = {}
    for spell, target, spent in castable_spells(state, figure):
        offers.setdefault(spell.id, []).append((spell, target, spent))
    # One reason covers every spell when the wizard cannot cast at all (a
    # shield up, a weapon readied, or something it already did this turn).
    blocked = cast_block_reason(figure) or state.turn_cast_block_reason(figure)
    rows = []
    for spell_id in figure.spells_known:
        spell = SPELLS.get(spell_id)
        if spell is None:
            continue
        offered = offers.get(spell_id, [])
        if offered:
            rows.extend(
                _classic_cast_row(state, figure, spell, target, spent)
                for _spell, target, spent in offered
            )
            continue
        if blocked is not None:
            reason = blocked
        else:
            targets = state.spell_targets(figure, spell)
            if not targets:
                # An empty target list is the engine's whole answer, and it
                # has several causes — no enemy within a missile's reach, a
                # debuff with nothing to strip, a spell whose type the cast
                # layer aims at nobody. Re-deriving which would be a second
                # opinion about ``spell_targets``, so the row says the one
                # thing that is true in every case.
                reason = "nothing this spell can be cast at"
            else:
                needed = (
                    spell.st_cost
                    if spell.variable_st
                    else min(spell_cost_for(spell, one.strength) for one in targets)
                )
                reason = f"needs {needed} ST, {figure.current_st} left"
        rows.append(
            {
                "key": spell_id,
                "target_id": None,
                "name": spell.name,
                "cost": f"{CLASSIC_SPELL_TYPES.get(spell.type, spell.type)}"
                f" — {spell.st_cost} ST",
                "note": "",
                "castable": False,
                "reason": reason,
            }
        )
    return {
        "pool_label": "ST",
        "pool": figure.current_st,
        "max_pool": figure.strength,
        "spells": rows,
    }


def cast_row_name(spell_name: str, target_name: str) -> str:
    """ "Magic Fist → Bram": a cast stage row for a spell aimed at one of
    several figures, in either profile, so the rows for its targets are told
    apart."""
    return f"{spell_name} → {target_name}"


def _classic_cast_row(state, figure, spell, target, spent: int) -> dict:
    """One offered classic cast, worded for the stage.

    A cast is a 3d6 roll at or under the caster's adjDX
    (``Ruleset.spell_to_hit_number``), which the *aim* also drags: a missile
    spell takes the megahex range penalty and a thrown one -1 per hex, both
    computed by ``GameState.queue_spell`` from the target's position at the
    moment of the declaration. Recomputing that distance here would be a
    second opinion about the same number, so a cast at another figure prints
    its unranged chance and says the range is still to come; a self-cast
    takes no range penalty at all and its number is exact.
    """
    at_self = target.uid == figure.uid
    needed = state.rules.spell_to_hit_number(figure)
    chance = policy.three_d6_at_most(min(needed, CLASSIC_CAST_CEILING))
    range_note = "" if at_self else ", before the range to aim"
    return {
        "key": spell.id,
        "target_id": target.uid,
        "name": spell.name if at_self else cast_row_name(spell.name, target.name),
        "cost": f"{CLASSIC_SPELL_TYPES.get(spell.type, spell.type)} — {spent} ST",
        "note": f"{chance:.0%} on 3d6 ≤ adjDX {needed}{range_note}",
        "castable": True,
        "reason": "",
    }
