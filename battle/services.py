"""Service layer: battle creation and the one-turn-per-request step.

``advance_turn`` is the deterministic-resume core: every turn's dice come
from ``Roller(seed=battle.seed + battle.turn_number)``, so re-running a
battle from any persisted state reproduces it exactly, and two battles with
the same seed and generation are identical event for event.
"""

from __future__ import annotations

import functools
import random
from collections.abc import Callable, Collection
from dataclasses import asdict, dataclass, replace
from typing import Concatenate

from django.db import transaction
from django.utils import timezone
from tarmar_engine.magic import ChannelRules, ChannelUnclassifiedSpell
from tarmar_engine.spells import Spell
from tarmar_engine.weapons import missile_rate_from_note

from accounts.models import User
from bestiary.generation import DIFFICULTY_BANDS, pick_base
from bestiary.models import CATEGORY_CHOICES, Beast
from characters import services as character_services
from characters.equipment import (
    get_armour_tier,
    get_item,
    get_shield_bonus,
    is_worn_armour,
)
from characters.equipment_rules import equipment_catalog, equipment_rules
from characters.magic import battle_loadout, current_mana, spell_mastery
from characters.models import (
    Character,
    board_can_seat_weapon,
    characters_visible_to,
)
from characters.services import FightReport, record_entry_into_play
from characters.skills import glossary_skill_level
from characters.weapon_skill import resolve_weapon_skill

from . import agreement, control, magic_bounds, policy, profiles, turn_menu
from .access import (
    Actor,
    claimed_seat_q,
    commands_foes,
    decides_for,
    is_dm,
    seat_is_held,
)
from .adaptation import engine_gait_names, snapshot_beast
from .classic_adaptation import classic_state
from .engine import hexes
from .engine.state import (
    BattleState,
    CombatantState,
    WeaponState,
    bare_handed_damage,
)
from .generation import (
    ARCHETYPES,
    GeneratedCombatant,
    generate_character,
    stat_roller_for_slot,
)
from .magic_rules import magic_rules
from .models import SEED_RANGE, Battle, BattleCombatant, BattleDraft, BattleEvent
from .persistence import (
    CLASSIC_PROFILE,
    PROFILE_CHOICES,
    TARMAR_PROFILE,
    dump_state,
    tarmar_state,
)
from .refusals import BattleRefusal
from .signals import battle_ended
from .spells import get_spell
from .wording import game_master_name, words


class DraftFieldError(BattleRefusal):
    """A refusal about one named field of the setup form (issue #711).

    "A battle needs a name" used to arrive as a bare ``ValueError`` and was
    drawn in a banner above the breadcrumb, while the empty field sat roughly
    1,200px below it, past the muster roll and a 239-option select. Carrying
    the field name lets the page put the sentence where the reader has to
    act. It stays a ``ValueError`` so every existing caller and test still
    catches what it always caught.

    Attributes:
        field: The form field the message belongs beside.
    """

    def __init__(self, message: str, *, field: str) -> None:
        super().__init__(message)
        self.field = field


# The §6 armour tiers from worst to best protection, for picking the
# governing tier when several pieces are worn.
_TIER_RANK = {"None": 0, "Light": 1, "Medium": 2, "Heavy": 3}

# The phase the victory line is filed under: the Tarmar turn's last phase,
# which is where a battle can actually end. A classic turn numbers its lines
# differently (battle.profiles.CLASSIC_LINE_PHASE) but the victory line is the
# app's own, not the engine's, so it keeps one home in both.
VICTORY_PHASE = 6

#: The turn the crossing notes are filed under. A classic ``GameState``
#: opens on turn 1, so the notes share it rather than inventing a turn 0 the
#: state never had.
OPENING_TURN = 1

#: How many consecutive full turns may pass hurting nobody — no combatant's
#: pools lower at the turn's end than at its start, and no death or collapse —
#: before the battle is declared a stalemate rather than advanced forever
#: (#289: some AI-vs-AI matchups are unwinnable by construction, e.g. armour
#: whose stops exceed the opponent's best possible blow, or combatants that
#: can never reach each other). The width separates *slow* from *stuck*:
#: measured over the QA seeds that do finish, the longest quiet stretch a
#: real battle survives is 33 turns (classic seed 26251; classic seed 7
#: shows 31, most battles single digits), so 50 clears every observed
#: false positive with room to spare while still ending a provably stuck
#: battle within a page or two of log.
STALEMATE_TURNS = 50

MIN_COMBATANTS = 2
MAX_COMBATANTS = 12
DEFAULT_COMBATANTS = 6

# Team names live in ``BattleCombatant.team`` (max_length=50); everything
# that mints one — party imports, the setup page's new-team form — trims to
# fit. The headroom keeps a numbered variant ("... (2)") inside the column.
MAX_TEAM_NAME = 45

# The arena grows with the field so a big battle is not born engaged.
MIN_ARENA_RADIUS = 5


#: The one catalog weapon the hand-to-hand table allows besides bare hands:
#: hand-to-hand-and-grappling.md, "Bare hands or dagger". It is what the
#: engine's HTH strike (t) and DRAW DAGGER (u) take (``WeaponState.hth_usable``).
HTH_WEAPON_IDS: frozenset[str] = frozenset({"dagger"})


@dataclass(frozen=True)
class SeatedArms:
    """Every weapon a character brings to the board, as the engine holds them.

    Attributes:
        weapon: The weapon in hand, or bare hands.
        skill_level: The effective Weapon-skill level with ``weapon``.
        spare_weapons: The other usable weapons carried outside the pack, in
            inventory order, one per piece: what READY WEAPON (e), CHANGE
            WEAPON (m) and DRAW DAGGER (u) take up. A packed weapon is slung
            away (``Character._held_index``) and is not among them.
        weapon_skills: The effective Weapon-skill level with every catalog
            weapon the board can seat that the character's skills reach, by
            catalog id: the ones carried, and any one picked up off the field
            mid-fight (PICK UP WEAPON, q), so each is used at the level the
            sheet's resolver gives. A weapon missing from the map is level 0
            to the engine, which is what the resolver gives it too.
    """

    weapon: WeaponState
    skill_level: int
    spare_weapons: tuple[WeaponState, ...]
    weapon_skills: dict[str, int]


def _catalog_weapon(item_id: str) -> WeaponState:
    """The engine's copy of one catalog weapon :func:`board_can_seat_weapon` passed.

    A missile weapon is one of the loaded canon's Missile classes (issue #75).
    Its rate of fire is read off its weapons.md Notes cell by the engine's own
    parser (``tarmar_engine.weapons.missile_rate_from_note``), so the numbers
    are never retyped here (#781).
    """
    item_data = get_item(item_id) or {}
    weapon_class = str(item_data.get("weapon_class"))
    is_missile = weapon_class in equipment_rules().missile_classes
    rate = missile_rate_from_note(item_data.get("notes") if is_missile else None)
    return WeaponState(
        item_id=item_id,
        name=item_data.get("name", item_id),
        weapon_class=weapon_class,
        damage=str(item_data.get("damage")),
        str_req=item_data.get("str_req") or 0,
        is_missile=is_missile,
        is_thrown=bool(item_data.get("throwable")),
        hth_usable=item_id in HTH_WEAPON_IDS,
        double_shot_dex=rate.double_shot_dex,
        reload_turns=rate.reload_turns,
        quick_reload_dex=rate.quick_reload_dex,
        quick_reload_turns=rate.quick_reload_turns,
    )


def _weapon_skill_levels(character: Character) -> dict[str, int]:
    """The board level with every seatable catalog weapon, where it is above 0.

    Read through :func:`resolve_weapon_skill`, the one answer to "which
    skill, at what bonus" for the board and the sheet's attack panel (issue
    #753); the engine adds a flat bonus per level, so a half transfer is
    seated as the halved level. Every catalog weapon is priced, not only the
    ones carried, because a figure may pick one up off the field (#780).
    Levels of 0 are left out: the engine reads a missing weapon as 0.
    """
    levels: dict[str, int] = {}
    for item_id in equipment_catalog().weapons:
        if not board_can_seat_weapon({"item_id": item_id}):
            continue
        level = resolve_weapon_skill(character.skills, item_id).effective_level
        if level:
            levels[item_id] = level
    return levels


def _seated_arms(character: Character) -> SeatedArms:
    """The weapon in hand, the weapons carried, and the skill with each.

    The weapon in hand is the sheet's (issue #846) when the board can seat
    it; otherwise the first usable catalog weapon in inventory, as before.
    Every other usable weapon carried outside the pack, and every further
    piece of a stack ("2 daggers"), is a spare. A character with no usable
    weapon fights bare-handed: the rules have no bare-handed skill (#815,
    #867), so bare hands are seated at level 0, and the engine's HTH strike
    ignores the striker's skill.

    Raises:
        ValueError: A character with no weapon and no recorded STR, whose
            bare-handed damage cannot be priced (issue #630).
    """
    equipment = list(character.equipment or [])
    in_hand = character.weapon_in_hand_index
    if in_hand is not None:
        equipment.insert(0, equipment.pop(in_hand))
    readied: WeaponState | None = None
    spares: list[WeaponState] = []
    weapon_skills = _weapon_skill_levels(character)
    for item in equipment:
        # The same test the sheet's default weapon in hand uses (#846).
        if not board_can_seat_weapon(item):
            continue
        item_id = item["item_id"]
        if readied is not None and item.get("in_pack"):
            continue
        pieces = max(1, int(item.get("quantity") or 1))
        if readied is None:
            readied = _catalog_weapon(item_id)
            pieces -= 1
        spares.extend(_catalog_weapon(item_id) for _piece in range(pieces))
    if readied is not None:
        return SeatedArms(
            weapon=readied,
            skill_level=weapon_skills.get(readied.item_id, 0),
            spare_weapons=tuple(spares),
            weapon_skills=weapon_skills,
        )
    # Bare-handed damage is priced from STR, which an incomplete character
    # does not have (issue #630). Only complete characters are ever seated,
    # so this states the contract rather than inventing a value.
    strength = character.effective_strength
    if strength is None:
        raise ValueError(
            f"{character.name} is not complete: {', '.join(character.missing_parts)}"
        )
    return SeatedArms(
        weapon=WeaponState(damage=bare_handed_damage(strength)),
        skill_level=0,
        spare_weapons=(),
        # A figure that starts bare-handed may still pick a weapon up, and
        # swings it at the sheet's level (review of the pin).
        weapon_skills=weapon_skills,
    )


@dataclass(frozen=True)
class SeatedGaits:
    """A character's gait distances as the engine reads them (0 = barred).

    Attributes:
        walk: Walk hexes.
        jog: Jog hexes; a figure barred from jogging charges and dodges at
            its walk (movement.md, "Jog or less").
        run: Run hexes, 0 when armour or load forbids running.
        sprint: Sprint hexes, 0 when armour or load forbids sprinting.
    """

    walk: int
    jog: int
    run: int
    sprint: int


def _seated_gaits(character: Character) -> SeatedGaits:
    """The sheet's gaits, with a barred gait seated as 0 (#778, #818).

    The sheet shows a gait armour or load forbids as n/a (armor-and-shields.md:
    chainmail and heavier "Cannot Run or Sprint", leather "Cannot Sprint";
    movement.md: a Medium load likewise), and the engine bars a gait whose
    distance is 0. The seat used to floor Run at the Jog or Walk distance, so
    a figure in chainmail ran anyway. Walk and Jog keep their floors: Walk at
    1 hex, since movement.md's fixed "Walk (slow)" still moves a figure whose
    formula reaches 0, and Jog at the Walk distance, since the engine's policy
    reads ``move_jog`` for a charge's reach and a figure that may not jog
    charges at its walk (tarmar-engine#17).

    The sheet's gaits are the loaded canon's, each seated as the engine gait
    in its place (:func:`battle.adaptation.engine_gait_names`, issue #75).
    """
    distances = character.gait_distances
    canon_gaits = engine_gait_names()
    walk = distances[canon_gaits["walk"]] or 1
    return SeatedGaits(
        walk=walk,
        jog=distances[canon_gaits["jog"]] or walk,
        run=distances[canon_gaits["run"]] or 0,
        sprint=distances[canon_gaits["sprint"]] or 0,
    )


def _armour_tier(character: Character) -> str:
    """The governing armour tier among the armour pieces worn.

    Worn is equipped and not in the pack, the sheet's reading
    (:func:`characters.equipment.is_worn_armour`, issue #869).
    """
    best = "None"
    for item in character.equipment or []:
        if not is_worn_armour(item):
            continue
        tier = get_armour_tier(item.get("item_id", ""))
        if tier and _TIER_RANK.get(tier, 0) > _TIER_RANK[best]:
            best = tier
    return best


def _shield_bonus(character: Character, weapon: WeaponState | None = None) -> int:
    """TN bonus from the shield in use, the one the sheet counts (issue #804).

    One shield, carried outside the pack, and none beside a two-handed
    weapon: every shield in inventory used to count, stacked, even packed.
    ``weapon`` is the one the board seats; when the weapon in hand is one
    the board cannot seat and it falls back to a two-handed one, that one
    takes the shield's hand (review of #846).
    """
    index = character.shield_in_use_index
    if index is None:
        return 0
    if weapon is not None and weapon.item_id:
        if (get_item(weapon.item_id) or {}).get("two_handed"):
            return 0
    return get_shield_bonus((character.equipment or [])[index].get("item_id", ""))


def _channel_skill_levels(character: Character) -> dict[str, int]:
    """The general skills the canon's Channel rules read, at the record's levels.

    A canon's ``channel.derivations`` may name, as a kind of magic's
    ``source_skill``, the general skill whose level that kind's Channel
    derives from (issue #960). Each one named is read off the record by its
    glossary name; the system check (``battle.checks``) holds a canon to
    names the glossary has. A skill held at no level carries no key, and the
    engine reads a missing skill at level 0. Only the record sets the level:
    the sheet's rules govern it, and nothing at the table writes it.

    Args:
        character: The character being seated.

    Returns:
        ``{skill key: level}`` for the Channel skills the character holds;
        empty when the canon bounds no casting by Channel.
    """
    channel = magic_rules().channel
    if channel is None:
        return {}
    levels: dict[str, int] = {}
    for derivation in channel.derivations.values():
        if derivation.source_skill is None:
            continue
        level = glossary_skill_level(character.skills, derivation.source_skill)
        if level:
            levels[derivation.source_skill] = level
    return levels


def snapshot_character(
    character: Character,
    combatant_id: int,
    *,
    display_name: str,
    archetype: str,
    spells: tuple[str, ...],
    mana: int,
    position: tuple[int, int] = (0, 0),
    facing: int = 0,
) -> CombatantState:
    """Freeze one Character row into the engine's combatant state.

    The shared core behind :func:`snapshot_combatant` (generated rosters)
    and lobby seat claims (#250), which snapshot a player's chosen or
    generated character position-free — the lobby's start deals positions.
    """
    arms = _seated_arms(character)
    gaits = _seated_gaits(character)
    fatigue, body = character.pools()
    # A character arrives as hurt as their record says (issue #751): the
    # rules keep damage until it is healed, so a fight begins from the pools
    # the last one left. pools() has refused an incomplete character, so
    # both are known here.
    current_fatigue = fatigue - character.fatigue_damage
    current_body = body - character.body_damage
    # pools() has already refused an incomplete character; this narrows the
    # five attributes it does not cover, which are None for the same row
    # (issue #630) rather than the zeroes they used to read.
    strength = character.effective_strength
    # The items' own numbers, no modifiers: the board reads all or none (#752).
    dexterity = character.combat_dexterity_without_modifiers
    intelligence = character.effective_intelligence
    wisdom = character.effective_wisdom
    constitution = character.effective_constitution
    # movement.md's modifier, read by the HTH entry condition "has a lower
    # movement modifier than you" (#823).
    movement_modifier = character.movement_modifier
    if (
        strength is None
        or dexterity is None
        or intelligence is None
        or wisdom is None
        or constitution is None
        or movement_modifier is None
    ):  # pragma: no cover - pools() raised
        raise ValueError(
            f"{character.name} is not complete: {', '.join(character.missing_parts)}"
        )
    # A spell's mastery is the character's skill level in it, on the ladder
    # every skill climbs (spell-mastery.md), so the grapple rules' mastery and
    # Channel's spell level are one reading of the record (issue #960). A
    # seated spell the record holds no points in is in neither map, and the
    # engine reads the two differently: mastery 1, Channel's level 0.
    seated_spell_levels = spell_mastery(character, spells)
    return CombatantState(
        combatant_id=combatant_id,
        name=display_name,
        archetype=archetype,
        strength=strength,
        dexterity=dexterity,
        intelligence=intelligence,
        wisdom=wisdom,
        constitution=constitution,
        max_fatigue=fatigue,
        max_body=body,
        fatigue=current_fatigue,
        body=current_body,
        # Either pool at or below 0 fells a combatant, as the engine's
        # reactions judge it once damage lands; a character already down
        # comes onto the board down.
        conscious=current_fatigue > 0 and current_body > 0,
        q=position[0],
        r=position[1],
        facing=facing,
        weapon=arms.weapon,
        weapon_skill_level=arms.skill_level,
        spare_weapons=list(arms.spare_weapons),
        weapon_skills=arms.weapon_skills,
        armour_tier=_armour_tier(character),
        stops=character.armor_stops_without_modifiers,
        shield_bonus=_shield_bonus(character, arms.weapon),
        move_walk=gaits.walk,
        move_jog=gaits.jog,
        move_run=gaits.run,
        move_sprint=gaits.sprint,
        movement_modifier=movement_modifier,
        max_mana=mana,
        # Spent mana stays spent between fights, as wounds do (coordinator's
        # ruling; Spencer may overrule): a caster arrives with what is left.
        mana=min(mana, current_mana(character)),
        # So does mana borrowed and not paid back (tarmar-engine #28): the
        # caster's next fight starts owing it.
        debt=character.mana_owed,
        spells=list(spells),
        spell_mastery=seated_spell_levels,
        spell_skill_levels=dict(seated_spell_levels),
        skill_levels=_channel_skill_levels(character),
    )


def snapshot_combatant(
    generated: GeneratedCombatant,
    combatant_id: int,
    position: tuple[int, int],
    facing: int,
) -> CombatantState:
    """Freeze one generated Character into the engine's combatant state."""
    return snapshot_character(
        generated.character,
        combatant_id,
        display_name=generated.display_name,
        archetype=generated.archetype,
        spells=generated.spells,
        mana=generated.mana,
        position=position,
        facing=facing,
    )


def starting_positions(count: int, arena_radius: int) -> list[tuple[int, int]]:
    """Evenly spaced hexes on the arena's outer ring."""
    rim = hexes.ring(arena_radius - 1)
    step = len(rim) / count
    return [rim[int(index * step) % len(rim)] for index in range(count)]


def seatable_characters(user):
    """The saved characters ``user`` may seat as combatants in a battle.

    The characters visibility rule (their own; all of them for a DM/admin)
    narrowed to the living — a dead or soft-deleted character does not
    fight. Both the setup page's picker and ``create_battle``'s validation
    ask here, so what is offered and what is accepted cannot drift apart.

    A guest has none: they cannot choose from saved characters at all
    (issue #305). ``user`` is ``None`` for a guest, and
    ``characters_visible_to`` independently answers "nothing" for anyone
    without an account, so neither side of the pair can drift into
    offering one.
    """
    if user is None:
        return Character.objects.none()
    # Only a complete character can fight (issue #571): the rules need all
    # six attributes and the Fatigue roll.
    return characters_visible_to(user).playable().filter(is_alive=True)


def claimable_characters(user):
    """The characters ``user`` may bring to a lobby seat: their OWN living ones.

    Stricter than :func:`seatable_characters` on purpose — a DM seats any
    character they can see when building a battle, but a seat in someone's
    lobby is claimed with your own character only. A guest has none — the
    same rule as above, reached from the other side.
    """
    if user is None:
        return Character.objects.none()
    return seatable_characters(user).filter(user=user)


def resolve_beasts(
    *,
    slugs: tuple[str, ...] = (),
    random_count: int = 0,
    category: str = "",
    difficulty: str = "",
    random_specs: tuple[tuple[str, str], ...] = (),
    rng: random.Random,
) -> list[Beast]:
    """The Beast rows joining a battle: chosen by slug, then random picks.

    Random picks reuse ``bestiary.generation.pick_base`` with the battle's
    seeded ``rng``, so the same seed drafts the same beasts. Repeats are
    allowed (two wolves are a pack). ``random_count`` draws share one
    ``category``/``difficulty`` filter (the classic form's shape);
    ``random_specs`` carries one ``(category, difficulty)`` pair per draw
    (the setup builder's shape, where each added random beast keeps its own
    filter). Both may be empty; both feed the same picker in order.

    Raises:
        Beast.DoesNotExist: for an unknown slug or an unmatchable filter.
        ValueError: for an unknown difficulty band (from ``pick_base``).
    """
    beasts = [Beast.objects.get(slug=slug) for slug in slugs]
    specs = tuple((category, difficulty) for _ in range(random_count)) + tuple(
        random_specs
    )
    for pick_category, pick_difficulty in specs:
        beasts.append(
            pick_base(
                category=pick_category or None,
                difficulty=pick_difficulty or None,
                rng=rng,
            )
        )
    return beasts


def _beast_display_names(beasts: list[Beast]) -> list[str]:
    """Unique display names: a repeated pick gets a parenthesized number.

    Beasts keep their species name — only duplicates are disambiguated, as
    "Cougar (2)" (issue #187).
    """
    counts: dict[str, int] = {}
    names = []
    for beast in beasts:
        counts[beast.name] = counts.get(beast.name, 0) + 1
        occurrence = counts[beast.name]
        names.append(beast.name if occurrence == 1 else f"{beast.name} ({occurrence})")
    return names


def _record_crossing(battle: Battle, roster) -> list[BattleEvent]:
    """File what crossing into classic rules cost this roster, as events.

    A studio character does not become a classic figure for free: a weapon
    with no counterpart in the classic table is swapped for the nearest one
    it can wield, and a caster arrives with a classic wizard's spells rather
    than its own (the spell-canon ruling makes melee's suite classic-only,
    so nothing of the studio's magic crosses — the caster is rebuilt, not
    translated). Both are worth knowing and neither should be a banner that
    vanishes — they go in the battle log, where they stay as long as the
    battle does: each note's payload carries ``{"crossing": true}``, and
    :func:`restart_battle` keeps the marked rows when it clears the rest of
    the log (#288). They cannot be *rewritten* at restart because the
    pre-crossing roster they describe is not persisted — a battle created
    without a lobby stores no per-row snapshots, only the post-crossing
    ``initial_state_json`` — so preservation is the whole contract.

    Returns:
        The events written, in order (empty when nothing was lost).
    """
    notes: list[str] = []
    if roster.disarmed:
        notes.append(
            f"Re-armed from the classic weapon table: {', '.join(roster.disarmed)}."
        )
    if roster.wizards:
        notes.append(
            f"No spell crosses into a classic battle: "
            f"{', '.join(roster.wizards)} enter as classic wizards, "
            f"staff in hand and knowing the classic spells their IQ allows."
        )
    if roster.unspelled:
        notes.append(
            f"{', '.join(roster.unspelled)} cast in Tarmar but reach no "
            f"classic spell at this IQ, and fight as warriors."
        )
    return BattleEvent.objects.bulk_create(
        BattleEvent(
            battle=battle,
            turn=OPENING_TURN,
            phase=profiles.CLASSIC_LINE_PHASE,
            sequence=index,
            event_type="info",
            actor="",
            # The marker restart_battle preserves these rows by (#288). A
            # classic turn's own log lines share every other field's shape,
            # so the payload is the one place the notes can say what they are.
            payload={"crossing": True},
            message=note,
        )
        for index, note in enumerate(notes)
    )


def _playable_state(battle: Battle, state: BattleState):
    """The state this battle actually plays under, and its crossing log.

    A Tarmar battle plays the state it was built with. A classic battle
    crosses that roster into classic figures — so this is where the two
    doors into a playable battle meet: creation with no lobby
    (:func:`create_battle`) and a lobby closing over its settled seats
    (:func:`_start_locked`). Both hand over a Tarmar ``BattleState`` and get
    back whatever the battle's own profile plays, with what the crossing
    cost filed as opening events by :func:`_record_crossing`.

    Written as one function on purpose: a second copy of the crossing in the
    lobby path is how the two would drift apart.
    """
    if battle.rules_profile != CLASSIC_PROFILE:
        return state
    # The roster crosses over as a whole: classic seats figures on its own
    # rectangular arena, so the Tarmar positions dealt above are replaced
    # rather than translated.
    playable, roster = classic_state(state.combatants, seed=battle.seed)
    _record_crossing(battle, roster)
    return playable


def standing_sides(
    profile: profiles.BattleProfile, survivors: list
) -> set[tuple[str, str]]:
    """The sides still standing, for the last-team-standing check (#275).

    A teamless combatant is its own team of one, so a free-for-all battle
    ends exactly where it always did — one survivor left. A team and a
    combatant of one are told apart by the key's kind, not by its text
    (#930): a team's name is free text, so a team named ``"solo:7"`` or
    ``"7"`` must never be the same side as the teamless combatant in row 7.

    Args:
        profile: The battle's rules profile, which reads each actor's team
            and row id.
        survivors: The actors still able to fight.

    Returns:
        One ``("team", name)`` or ``("solo", row id)`` key per side standing.
    """
    sides: set[tuple[str, str]] = set()
    for actor in survivors:
        team = profile.actor_team(actor)
        sides.add(("team", team) if team else ("solo", str(profile.actor_id(actor))))
    return sides


def _team_at(teams: tuple[str, ...], index: int) -> str:
    """The team tag for slot ``index`` — empty (free-for-all) past the end."""
    return teams[index] if index < len(teams) else ""


def create_battle(
    user,
    *,
    guest_key: str = "",
    name: str,
    count: int = DEFAULT_COMBATANTS,
    seed: int | None = None,
    archetypes: tuple[str, ...] = ARCHETYPES,
    beast_slugs: tuple[str, ...] = (),
    beast_count: int = 0,
    beast_category: str = "",
    beast_difficulty: str = "",
    random_beast_specs: tuple[tuple[str, str], ...] = (),
    character_ids: tuple[int, ...] = (),
    player_seats: int = 0,
    auto_start: bool = False,
    rules_profile: str = TARMAR_PROFILE,
    control_mode: str = control.AUTOMATIC,
    fighter_teams: tuple[str, ...] = (),
    character_teams: tuple[str, ...] = (),
    beast_teams: tuple[str, ...] = (),
    seat_teams: tuple[str, ...] = (),
    writes_to_records: bool | None = None,
    keep_generated: bool = True,
) -> Battle:
    """Create a battle: generate combatants and beasts, snapshot the state.

    ``count`` characters are generated; ``beast_slugs`` name specific catalog
    beasts and ``beast_count`` (optionally filtered by ``beast_category`` /
    ``beast_difficulty``) drafts random ones, drawn deterministically from
    the battle seed (``random_beast_specs`` is the per-draw-filter spelling
    of the same thing — see :func:`resolve_beasts`). Character display names
    draw from the same seeded roster RNG — after the beast picks, so those
    keep their historical stream — and each generated character rolls its
    attributes through its own seed, derived per roster slot by
    :func:`~battle.generation.stat_roller_for_slot`. The whole roster is
    therefore a function of the seed: the same names, the same beasts, and
    the same characters down to their rolled ST/DX/IQ and the weapons those
    decide (#261). Names are unique across the battle's characters and
    beasts.

    ``character_ids`` seat saved characters (#270): each must be one ``user``
    may seat (:func:`seatable_characters` — their own, or any living
    undeleted character for a DM), and each is snapshotted here, at seat
    time, exactly as a lobby claim snapshots (#252) — the battle plays the
    copy. Whether the fight's wounds and deaths are written back to those
    rows is ``writes_to_records`` (issues #750, #751): given explicitly by a
    Games Master session's fight, and otherwise true only for a battle a
    Game Master makes. A player's own battle writes nothing to any record;
    and another player's public character, seated by someone who is not a
    Game Master, fights only as a copy (``is_saved_character`` false) so no
    path can write to a record that is not theirs.

    ``keep_generated`` False rolls the generated fighters without saving a
    ``Character`` row for them, as a guest's are (#305): a Games Master
    session's fight builds its foes that way (#787), since they are the Game
    Master's figures, not characters on the player's list.

    ``player_seats`` > 0 creates an *open* battle (#250): that many
    claimable seats join the roster, the battle starts life as a lobby, and
    the engine state is built by :func:`start_battle` once the seats are
    settled — until then combatants carry position-free snapshots on their
    rows instead of a shared ``state_json``. A lobby works under either
    profile (#251): the per-row snapshot is the same dataclass whichever
    rules the battle will be fought under, and the crossing waits for the
    roster to settle rather than running over seats nobody has taken.

    The ``*_teams`` tuples (#275) tag combatants with team names, aligned
    slot by slot with their kind's own argument: ``fighter_teams`` with the
    generated slots, ``character_teams`` with ``character_ids``,
    ``beast_teams`` with the combined beast order (slugs first, then random
    specs — :func:`resolve_beasts`'s order), ``seat_teams`` with the open
    seats. A short (or absent) tuple leaves the remaining slots
    free-for-all, so every pre-team call site is unchanged. The tag lands
    on the ``BattleCombatant`` row and in the engine snapshot; teammates
    are never offered as targets and victory is last team standing (a
    teamless combatant is its own team of one).

    ``rules_profile`` picks which rules the battle is fought under (#251).
    The roster is generated identically either way — the same characters, the
    same beasts, from the same seed — and then either snapshotted as Tarmar
    state or crossed into classic figures by
    :func:`battle.classic_adaptation.classic_state`, whose losses (a weapon
    with no classic twin, a caster's spells) are filed as opening events
    rather than announced and forgotten.

    Raises:
        ValueError: when the combined field size is out of bounds, the
            difficulty band is unknown, a character is not seatable by this
            user (or named twice), or the profile is one this app cannot
            persist.
        Beast.DoesNotExist: for an unknown beast slug or unmatchable filter.

    ``user`` is ``None`` for a guest's battle (issue #305), and then
    ``guest_key`` stamps who made it. Two things follow, both of them the
    guest rules rather than incidental: ``character_ids`` must be empty
    (a guest cannot seat saved characters), and the generated fighters are
    rolled without being persisted as ``Character`` rows (a guest cannot
    save characters) — so their combatants carry an engine snapshot and no
    provenance FK. See ``battle.generation.generate_character``.
    """
    if user is None and character_ids:
        raise BattleRefusal(
            "Sign in to bring saved characters — a guest's battle is fought "
            "with generated fighters and catalog beasts"
        )
    if rules_profile not in dict(PROFILE_CHOICES):
        raise ValueError(
            f"unknown rules profile {rules_profile!r}: this app can run "
            f"{', '.join(sorted(dict(PROFILE_CHOICES)))}"
        )
    if len(set(character_ids)) != len(character_ids):
        raise BattleRefusal("A character cannot fight twice in the same battle")
    seatable = seatable_characters(user).in_bulk(character_ids)
    missing = [str(pk) for pk in character_ids if pk not in seatable]
    if missing:
        raise BattleRefusal(
            "Not characters you can seat (they may have died or been "
            f"deleted): {', '.join(missing)}"
        )
    picked_characters = [seatable[pk] for pk in character_ids]
    if seed is None:
        seed = random.randrange(SEED_RANGE)
    roster_rng = random.Random(seed)
    beasts = resolve_beasts(
        slugs=beast_slugs,
        random_count=beast_count,
        category=beast_category,
        difficulty=beast_difficulty,
        random_specs=random_beast_specs,
        rng=roster_rng,
    )
    total = count + len(picked_characters) + len(beasts) + player_seats
    if not MIN_COMBATANTS <= total <= MAX_COMBATANTS:
        raise BattleRefusal(
            f"A battle needs between {MIN_COMBATANTS} and {MAX_COMBATANTS} "
            f"combatants (characters, beasts, and player seats together); "
            f"got {total}"
        )
    is_lobby = player_seats > 0
    arena_radius = max(MIN_ARENA_RADIUS, total)
    game_master = is_dm(user)
    battle = Battle.objects.create(
        name=name,
        seed=seed,
        created_by=user,
        guest_key=guest_key if user is None else "",
        status=Battle.STATUS_LOBBY if is_lobby else Battle.STATUS_SETUP,
        auto_start=auto_start and is_lobby,
        rules_profile=rules_profile,
        # The battle's default answer to "who decides?" (#308); every
        # combatant inherits it until one is given a mode of its own.
        control_mode=control.clean_mode(control_mode),
        # Coordinator's ruling under the standing rule (#750/#751); Spencer
        # may overrule. See the docstring.
        writes_to_records=(
            game_master if writes_to_records is None else writes_to_records
        ),
    )
    positions = starting_positions(total, arena_radius)
    state = BattleState(arena_radius=arena_radius)
    display_names = _beast_display_names(beasts)
    used_names = set(display_names)
    # Reserve the picked characters' names before any generation, so a
    # generated fighter redraws around "Aldwin" rather than the player's
    # actual character arriving as "Aldwin (2)".
    picked_names = []
    for character in picked_characters:
        picked_name = _numbered_variant(character.name, used_names)
        used_names.add(picked_name)
        picked_names.append(picked_name)
    for index in range(count):
        generated = generate_character(
            user if keep_generated else None,
            name,
            index,
            archetypes,
            rng=roster_rng,
            used_names=used_names,
            stats_roller=stat_roller_for_slot(seed, index),
            # Gear a figure's STR can bear is a Tarmar rule (#883); a classic
            # figure's move comes from its armour alone.
            within_load=rules_profile != CLASSIC_PROFILE,
        )
        combatant = BattleCombatant.objects.create(
            battle=battle,
            # A guest's fighter was never saved, so there is no row to
            # point at — the snapshot below is the whole figure (#305).
            character=generated.character if generated.character.pk else None,
            display_name=generated.display_name,
            team=_team_at(fighter_teams, index),
        )
        position = positions[index]
        facing = hexes.direction_towards(position, (0, 0))
        snapshot = snapshot_combatant(generated, combatant.id, position, facing)
        snapshot.team = combatant.team
        if is_lobby:
            combatant.snapshot_json = asdict(snapshot)
            combatant.save(update_fields=["snapshot_json"])
        state.combatants.append(snapshot)
    for picked_index, character in enumerate(picked_characters):
        display_name = picked_names[picked_index]
        combatant = BattleCombatant.objects.create(
            battle=battle,
            character=character,
            display_name=display_name,
            # Someone else's public character is fought as a copy unless a
            # Game Master seats it: the record is its owner's.
            is_saved_character=game_master
            or (user is not None and character.user_id == user.pk),
            team=_team_at(character_teams, picked_index),
        )
        position = positions[count + picked_index]
        facing = hexes.direction_towards(position, (0, 0))
        # The character's own spells and Mana Pool (issue #755), so a
        # player's caster casts on the board and in /fight.
        loadout = battle_loadout(character)
        snapshot = snapshot_character(
            character,
            combatant.id,
            display_name=display_name,
            archetype="adventurer",
            spells=loadout.spells,
            mana=loadout.mana,
            position=position,
            facing=facing,
        )
        snapshot.team = combatant.team
        if is_lobby:
            combatant.snapshot_json = asdict(snapshot)
            combatant.save(update_fields=["snapshot_json"])
        state.combatants.append(snapshot)
    for beast_index, (beast, display_name) in enumerate(
        zip(beasts, display_names, strict=True)
    ):
        combatant = BattleCombatant.objects.create(
            battle=battle,
            beast=beast,
            display_name=display_name,
            team=_team_at(beast_teams, beast_index),
        )
        position = positions[count + len(picked_characters) + beast_index]
        facing = hexes.direction_towards(position, (0, 0))
        snapshot = snapshot_beast(beast, combatant.id, display_name, position, facing)
        snapshot.team = combatant.team
        if is_lobby:
            combatant.snapshot_json = asdict(snapshot)
            combatant.save(update_fields=["snapshot_json"])
        state.combatants.append(snapshot)
    for seat_index in range(player_seats):
        BattleCombatant.objects.create(
            battle=battle,
            is_open_seat=True,
            display_name=f"Open seat {seat_index + 1}",
            team=_team_at(seat_teams, seat_index),
        )
    if is_lobby:
        # No engine state yet: seats are unsettled. start_battle assembles
        # state_json (and the frozen initial_state_json) from the per-row
        # snapshots once the lobby closes.
        return battle
    playable = _playable_state(battle, state)
    # The same dict for both: state_json advances, initial_state_json is the
    # frozen turn-0 copy restart_battle rewinds to.
    battle.state_json = dump_state(playable)
    battle.initial_state_json = dump_state(playable)
    battle.save()
    # A fight puts its seated characters in play (issue #803).
    record_entry_into_play(battle_id=battle.pk)
    return battle


# -------------------------------------------------------------------- drafts


def holds_draft[**Params, Result](
    change: Callable[Concatenate[BattleDraft, Params], Result],
) -> Callable[Concatenate[BattleDraft, Params], Result]:
    """Run a draft change on the row as it stands, holding it (issue #832).

    Every change to a draft reads its JSON, alters it and writes it back.
    Two overlapping requests used to each load the draft, so the second
    write dropped the first one's entry, and two presses of "Begin the
    battle" each built a battle before either deleted the draft. The change
    now runs in one transaction that first reloads the draft from its locked
    row, so it starts from whatever the last change committed. On this
    project's SQLite every transaction begins IMMEDIATE (``settings``), which
    is what serialises the writers; ``select_for_update`` says the same to a
    database that locks rows.

    Raises:
        BattleDraft.DoesNotExist: The draft was started or discarded since
            the caller loaded it.
    """

    @functools.wraps(change)
    def held(draft: BattleDraft, *args: Params.args, **kwargs: Params.kwargs) -> Result:
        with transaction.atomic():
            draft.refresh_from_db(from_queryset=BattleDraft.objects.select_for_update())
            return change(draft, *args, **kwargs)

    return held


@holds_draft
def add_draft_team(draft: BattleDraft, name) -> str:
    """Name a new team on the draft; returns the name actually recorded.

    Whitespace collapses, over-long names trim to :data:`MAX_TEAM_NAME`,
    and a name the draft already has comes back numbered ("Red (2)") —
    the same disambiguation the roster's display names use.

    Raises:
        ValueError: for a blank name.
    """
    cleaned = " ".join(str(name or "").split())[:MAX_TEAM_NAME].strip()
    if not cleaned:
        raise BattleRefusal("A team needs a name")
    teams = list(draft.teams_json or [])
    unique = _numbered_variant(cleaned, set(teams))
    teams.append(unique)
    draft.teams_json = teams
    draft.save(update_fields=["teams_json", "updated_at"])
    return unique


@holds_draft
def remove_draft_team(draft: BattleDraft, name: str) -> None:
    """Disband one team: its entries stay mustered, back in the free pool.

    Raises:
        ValueError: for a team the draft does not have.
    """
    teams = list(draft.teams_json or [])
    if name not in teams:
        raise BattleRefusal("No such team on this draft")
    teams.remove(name)
    roster = []
    for entry in draft.roster_json or []:
        entry = dict(entry)
        if entry.get("team") == name:
            entry.pop("team")
        roster.append(entry)
    draft.teams_json = teams
    draft.roster_json = roster
    draft.save(update_fields=["teams_json", "roster_json", "updated_at"])


@holds_draft
def set_entry_team(draft: BattleDraft, index: int, team: str) -> None:
    """Move one muster entry (by roster position) onto a team, or off all
    of them (``team=""`` returns it to the free-for-all pool).

    Raises:
        ValueError: for a position the roster does not have, or a team the
            draft does not have.
    """
    roster = list(draft.roster_json or [])
    if not 0 <= index < len(roster):
        raise BattleRefusal("No such muster entry")
    if team and team not in (draft.teams_json or []):
        raise BattleRefusal("No such team on this draft")
    entry = dict(roster[index])
    if team:
        entry["team"] = team
    else:
        entry.pop("team", None)
    roster[index] = entry
    draft.roster_json = roster
    draft.save(update_fields=["roster_json", "updated_at"])


@holds_draft
def import_party_into_draft(draft: BattleDraft, user, party_id: int) -> dict:
    """Import a party as a NEW team seeded with its members (#275).

    The team is named for the party (numbered if the draft already has that
    name) and one character entry joins it per member ``user`` may seat.
    The rest are counted, not smuggled in: members hidden from this user
    (``characters_visible_to`` — a non-DM never imports another player's
    private character), members visible but dead, and members already on
    the muster are all skipped, and the returned counts let the page say so
    in a notice that stays. Snapshot semantics are unchanged — entries hold
    ids, and the copy is taken when the battle starts.

    Returns:
        ``{"team", "imported", "hidden", "fallen", "already_mustered"}``.

    Raises:
        ValueError: for an unknown party, a disbanded one (#971), a party
            with no members, a party
            with no importable members (nothing would join, so no team is
            created), or more importable members than the muster has room
            for.
    """
    from parties.models import Party, disbanded_refusal

    party = Party.objects.filter(pk=party_id).first()
    if party is None:
        raise BattleRefusal("No such party")
    if party.is_deleted:
        # The picker offers no disbanded party; one named by hand is refused
        # and says why (#971).
        raise BattleRefusal(disbanded_refusal(party))
    member_ids = list(
        party.memberships.order_by("added_at").values_list("character_id", flat=True)
    )
    if not member_ids:
        raise BattleRefusal(f"{party.name} has no members to import")
    visible = set(
        characters_visible_to(user)
        .filter(pk__in=member_ids)
        .values_list("pk", flat=True)
    )
    seatable = set(
        seatable_characters(user).filter(pk__in=member_ids).values_list("pk", flat=True)
    )
    mustered = {
        entry.get("character_id")
        for entry in draft.roster_json or []
        if entry.get("kind") == "character"
    }
    importable = [pk for pk in member_ids if pk in seatable and pk not in mustered]
    hidden = sum(1 for pk in member_ids if pk not in visible)
    fallen = sum(1 for pk in member_ids if pk in visible and pk not in seatable)
    already = sum(1 for pk in member_ids if pk in seatable and pk in mustered)
    if not importable:
        raise BattleRefusal(
            f"Nobody in {party.name} can join: {hidden} hidden from you, "
            f"{fallen} dead or gone, {already} already on the muster"
        )
    roster = list(draft.roster_json or [])
    if len(roster) + len(importable) > MAX_COMBATANTS:
        raise BattleRefusal(
            f"No room for {party.name}: {len(importable)} would join a "
            f"muster of {len(roster)}, and a battle holds at most "
            f"{MAX_COMBATANTS}"
        )
    team = add_draft_team(draft, party.name)
    roster.extend(
        {"kind": "character", "character_id": pk, "team": team} for pk in importable
    )
    draft.roster_json = roster
    draft.save(update_fields=["roster_json", "updated_at"])
    return {
        "team": team,
        "imported": len(importable),
        "hidden": hidden,
        "fallen": fallen,
        "already_mustered": already,
    }


@holds_draft
def add_draft_entry(
    draft: BattleDraft,
    user,
    *,
    kind: str,
    archetype: str = "",
    slug: str = "",
    category: str = "",
    difficulty: str = "",
    character_id: int | None = None,
    team: str = "",
) -> None:
    """Validate one muster entry and append it to the draft's roster.

    The single gate onto ``roster_json`` — everything the setup page can
    add comes through here, so a draft only ever holds entries
    :func:`draft_create_kwargs` knows how to compile. ``user`` is the DM
    doing the adding; character entries are checked against what *they* may
    seat (:func:`seatable_characters`), the same check ``create_battle``
    repeats at start.

    Raises:
        ValueError: for a full muster, an unknown kind, or invalid entry
            parameters (bad archetype, beast, filter, or character).
    """
    roster = list(draft.roster_json or [])
    if len(roster) >= MAX_COMBATANTS:
        raise BattleRefusal(
            f"The muster is full — a battle holds at most {MAX_COMBATANTS}"
        )
    if team and team not in (draft.teams_json or []):
        raise BattleRefusal("No such team on this draft")
    if kind == "fighter":
        if archetype not in ARCHETYPES:
            raise BattleRefusal("Unknown archetype")
        entry = {"kind": "fighter", "archetype": archetype}
    elif kind == "beast":
        if not Beast.objects.filter(slug=slug).exists():
            raise BattleRefusal("No catalog beast matches that request")
        entry = {"kind": "beast", "slug": slug}
    elif kind == "random_beast":
        if category and category not in dict(CATEGORY_CHOICES):
            raise BattleRefusal("Unknown beast category")
        if difficulty and difficulty not in DIFFICULTY_BANDS:
            raise BattleRefusal("Unknown difficulty band")
        entry = {"kind": "random_beast", "category": category, "difficulty": difficulty}
    elif kind == "character":
        character = seatable_characters(user).filter(pk=character_id).first()
        if character is None:
            raise BattleRefusal("Not a character you can seat")
        already = any(
            existing.get("kind") == "character"
            and existing.get("character_id") == character.pk
            for existing in roster
        )
        if already:
            raise BattleRefusal(f"{character.name} is already on the muster")
        entry = {"kind": "character", "character_id": character.pk}
    elif kind == "open_seat":
        entry = {"kind": "open_seat"}
    else:
        raise BattleRefusal("Unknown roster entry kind")
    if team:
        entry["team"] = team
    roster.append(entry)
    draft.roster_json = roster
    draft.save(update_fields=["roster_json", "updated_at"])


@holds_draft
def remove_draft_entry(draft: BattleDraft, index: int) -> None:
    """Strike one entry (by roster position) off the draft's muster.

    Raises:
        ValueError: for a position the roster does not have.
    """
    roster = list(draft.roster_json or [])
    if not 0 <= index < len(roster):
        raise BattleRefusal("No such muster entry")
    roster.pop(index)
    draft.roster_json = roster
    draft.save(update_fields=["roster_json", "updated_at"])


def draft_create_kwargs(draft: BattleDraft) -> dict:
    """Compile a draft into :func:`create_battle` keyword arguments.

    The muster's entries fold into the creation shapes: fighter entries
    become ``count`` + a per-slot ``archetypes`` tuple (generation draws
    archetypes round-robin, so a tuple exactly as long as ``count`` assigns
    them slot by slot), beast entries keep their order within
    ``resolve_beasts``'s slug/spec split, and character entries pass as
    ``character_ids`` for seat-time snapshotting.
    """
    fighters: list[str] = []
    fighter_teams: list[str] = []
    slugs: list[str] = []
    slug_teams: list[str] = []
    specs: list[tuple[str, str]] = []
    spec_teams: list[str] = []
    character_ids: list[int] = []
    character_teams: list[str] = []
    seat_teams: list[str] = []
    for entry in draft.roster_json or []:
        kind = entry.get("kind")
        team = entry.get("team", "")
        if kind == "fighter":
            fighters.append(entry["archetype"])
            fighter_teams.append(team)
        elif kind == "beast":
            slugs.append(entry["slug"])
            slug_teams.append(team)
        elif kind == "random_beast":
            specs.append((entry.get("category", ""), entry.get("difficulty", "")))
            spec_teams.append(team)
        elif kind == "character":
            character_ids.append(entry["character_id"])
            character_teams.append(team)
        elif kind == "open_seat":
            seat_teams.append(team)
    return {
        "name": draft.name.strip(),
        "seed": draft.seed,
        "rules_profile": draft.rules_profile,
        "auto_start": draft.auto_start,
        "control_mode": draft.control_mode,
        "count": len(fighters),
        "archetypes": tuple(fighters) or ARCHETYPES,
        "beast_slugs": tuple(slugs),
        "random_beast_specs": tuple(specs),
        "character_ids": tuple(character_ids),
        "player_seats": len(seat_teams),
        "fighter_teams": tuple(fighter_teams),
        "character_teams": tuple(character_teams),
        # resolve_beasts drafts slugs first, then the random specs — the
        # combined tuple keeps that order so each beast lands on its team.
        "beast_teams": tuple(slug_teams + spec_teams),
        "seat_teams": tuple(seat_teams),
        # Whether the battle counts (#750, #751): a Game Master's choice on
        # the setup page, never a player's -- a player's battle writes
        # nothing to any record whatever the draft row holds.
        "writes_to_records": draft.writes_to_records and is_dm(draft.created_by),
    }


@holds_draft
def start_draft(draft: BattleDraft) -> Battle:
    """Create the battle a draft describes, then retire the draft.

    Held (:func:`holds_draft`): a second press of "Begin the battle" waits
    for the first, then finds the draft gone and builds nothing (#832).

    Raises:
        BattleDraft.DoesNotExist: The draft was started or discarded since
            the caller loaded it.
        ValueError: for a nameless draft, or anything
            :func:`create_battle` refuses (size bounds, unseatable
            characters, unknown beasts).
        Beast.DoesNotExist: for a beast filter nothing in the catalog
            matches.
    """
    if not draft.name.strip():
        raise DraftFieldError("A battle needs a name", field="name")
    battle = create_battle(
        draft.created_by, guest_key=draft.guest_key, **draft_create_kwargs(draft)
    )
    draft.delete()
    return battle


# --------------------------------------------------------------------- lobby


def _numbered_variant(wanted: str, taken: set[str]) -> str:
    """``wanted`` if not taken, else the first free "wanted (N)" variant."""
    if wanted not in taken:
        return wanted
    suffix = 2
    while f"{wanted} ({suffix})" in taken:
        suffix += 1
    return f"{wanted} ({suffix})"


def _display_name_for(battle: Battle, wanted: str) -> str:
    """``wanted`` if free in this battle, else the first numbered variant."""
    taken = set(battle.combatants.values_list("display_name", flat=True))
    return _numbered_variant(wanted, taken)


def claim_seat(
    battle_id: int,
    seat_id: int,
    user,
    *,
    guest_key: str = "",
    character_id: int | None = None,
    archetype: str = "",
) -> BattleCombatant:
    """Claim one open seat, with a chosen or generated character.

    Exactly one of ``character_id`` (one of the player's own characters) or
    ``archetype`` (generate a fresh one, the battle-generation flow) picks
    the combatant. Concurrent claims are safe: the seat is taken with a
    single conditional UPDATE against an unclaimed seat, so on every
    backend exactly one of two racing claims wins and the loser sees
    "already taken".

    ``user`` is ``None`` for a guest (issue #305), whose claim is stamped
    with ``guest_key`` instead. A guest may only generate: ``character_id``
    is refused outright rather than silently ignored, and the fighter the
    archetype rolls is not saved as a ``Character`` row.

    Raises:
        ValueError: battle not in its lobby, seat unknown or taken, actor
            already seated, bad character or archetype choice.
    """
    if bool(character_id) == bool(archetype):
        raise BattleRefusal("Choose a character or an archetype to generate")
    if user is None and not guest_key:
        raise BattleRefusal("This browser has no guest session — reload and try again")
    if user is None and character_id:
        raise BattleRefusal("Sign in to bring one of your own characters to a seat")
    actor = Actor.for_user_or_guest(user, guest_key)
    with transaction.atomic():
        battle = Battle.objects.select_for_update().get(pk=battle_id)
        if battle.status != Battle.STATUS_LOBBY:
            raise BattleRefusal("This battle is no longer open for joining")
        if battle.combatants.filter(actor.seat_filter()).exists():
            raise BattleRefusal("You already hold a seat in this battle")
        won = (
            BattleCombatant.objects.filter(pk=seat_id, battle=battle, is_open_seat=True)
            .exclude(claimed_seat_q())
            .update(player=user, guest_key="" if user is not None else guest_key)
        )
        if not won:
            raise BattleRefusal("That seat is already taken")
        seat = battle.combatants.get(pk=seat_id)
        if character_id:
            # Own, living, undeleted (#270) — the same chokepoint the lobby's
            # character menu renders from.
            character = claimable_characters(user).filter(pk=character_id).first()
            if character is None:
                raise BattleRefusal("That is not one of your living characters")
            display_name = _display_name_for(battle, character.name)
            loadout = battle_loadout(character)
            snapshot = snapshot_character(
                character,
                seat.pk,
                display_name=display_name,
                archetype="adventurer",
                spells=loadout.spells,
                mana=loadout.mana,
            )
        else:
            if archetype not in ARCHETYPES:
                raise BattleRefusal("Unknown archetype")
            used_names = set(battle.combatants.values_list("display_name", flat=True))
            generated = generate_character(
                user,
                battle.name,
                0,
                (archetype,),
                rng=random.Random(),
                used_names=used_names,
                within_load=battle.rules_profile != CLASSIC_PROFILE,
            )
            character = generated.character
            display_name = generated.display_name
            snapshot = snapshot_character(
                character,
                seat.pk,
                display_name=display_name,
                archetype=generated.archetype,
                spells=generated.spells,
                mana=generated.mana,
            )
        # The seat's team survives the claim (#275): the combatant a player
        # brings fights on whatever side the DM put the seat on.
        snapshot.team = seat.team
        # A guest's generated fighter was never saved (#305): no row to
        # point at, and the snapshot below is the whole figure.
        seat.character = character if character.pk else None
        seat.display_name = display_name
        seat.snapshot_json = asdict(snapshot)
        seat.is_saved_character = bool(character_id)
        seat.is_ready = False
        # Claiming a seat *is* the statement "I will play this one" (#308),
        # so it writes the mode down instead of leaving it to be inferred
        # from ``player`` later. That keeps #250/#305 seated play working
        # in an otherwise automatic battle, and — because it is an override
        # rather than a shape — lets the player hand the figure to the AI
        # afterwards without giving up the seat.
        seat.control_mode = control.MANUAL
        seat.save()
    return seat


def release_seat(
    battle_id: int, seat_id: int, user, *, guest_key: str = ""
) -> BattleCombatant:
    """Give an occupied seat back to the lobby (its holder, or any DM).

    The character row the player brought or generated survives — only the
    seat's claim, snapshot, and ready flag clear.

    Raises:
        ValueError: battle not in its lobby, seat unknown, or not yours.
    """
    with transaction.atomic():
        battle = Battle.objects.select_for_update().get(pk=battle_id)
        if battle.status != Battle.STATUS_LOBBY:
            raise BattleRefusal("This battle is no longer open for joining")
        seat = battle.combatants.filter(pk=seat_id, is_open_seat=True).first()
        if seat is None or not seat_is_held(seat):
            raise BattleRefusal("That is not an occupied seat")
        actor = Actor.for_user_or_guest(user, guest_key)
        if not actor.holds_seat(seat) and not actor.is_dm:
            raise BattleRefusal("Only the seat's player or a DM can release it")
        rank = list(
            battle.combatants.filter(is_open_seat=True).values_list("pk", flat=True)
        ).index(seat.pk)
        seat.player = None
        seat.guest_key = ""
        seat.character = None
        seat.is_ready = False
        seat.is_saved_character = False
        # The claim's override goes with the claim: an empty seat has no
        # owner of its own and follows the battle again (#308).
        seat.control_mode = control.INHERIT
        seat.snapshot_json = {}
        seat.display_name = _display_name_for(battle, f"Open seat {rank + 1}")
        seat.save()
    return seat


def set_seat_ready(
    battle_id: int, seat_id: int, user, *, guest_key: str = "", ready: bool
) -> tuple[Battle, BattleCombatant]:
    """Set a claimed seat's ready flag (only its own player may).

    With the battle's ``auto_start`` rule on, the last seat turning ready —
    every seat claimed, every player ready — starts the battle in the same
    transaction; callers see the returned battle already in ``setup``.

    Raises:
        ValueError: battle not in its lobby, seat unknown or not yours.
    """
    with transaction.atomic():
        battle = Battle.objects.select_for_update().get(pk=battle_id)
        if battle.status != Battle.STATUS_LOBBY:
            raise BattleRefusal("This battle is no longer open for joining")
        actor = Actor.for_user_or_guest(user, guest_key)
        seat = battle.combatants.filter(actor.seat_filter(), pk=seat_id).first()
        if seat is None:
            raise BattleRefusal("That is not your seat")
        seat.is_ready = ready
        seat.save(update_fields=["is_ready"])
        if battle.auto_start and ready:
            seats = battle.combatants.filter(is_open_seat=True)
            all_settled = not seats.exclude(claimed_seat_q()).exists() and not (
                seats.filter(claimed_seat_q(), is_ready=False).exists()
            )
            # A seat whose character has died holds the start: the ready
            # still saves, and the lobby says which seat is in the way.
            if all_settled and not dead_seats(battle):
                _start_locked(battle)
    return battle, seat


def _with_current_pools(combatant: BattleCombatant, entry: dict) -> dict:
    """A claimed seat's snapshot, its pools re-read from the record at the start.

    A lobby seat is snapshotted when it is claimed, and the battle may start
    long after: a character hurt, healed or killed in between would otherwise
    come onto the board as the claim found them (issue #751). A saved
    character who has died since claiming the seat cannot fight at all.

    Raises:
        ValueError: The seat's character has died.
    """
    character = combatant.character if combatant.is_saved_character else None
    if character is None:
        return entry
    if not character.is_alive:
        raise BattleRefusal(
            f"{combatant.display_name} has died since claiming a seat; the seat "
            "must be released before the battle can start"
        )
    fatigue, body = character.pools()
    entry["max_fatigue"], entry["max_body"] = fatigue, body
    entry["fatigue"] = fatigue - character.fatigue_damage
    entry["body"] = body - character.body_damage
    entry["conscious"] = entry["fatigue"] > 0 and entry["body"] > 0
    entry["mana"] = min(int(entry.get("max_mana", 0)), current_mana(character))
    # The mana owed (tarmar-engine #28), written only when owed, as the
    # engine's own snapshot writes it.
    entry.pop("debt", None)
    if character.mana_owed:
        entry["debt"] = character.mana_owed
    return entry


def dead_seats(battle: Battle) -> list[BattleCombatant]:
    """Claimed seats whose saved character has died since the claim.

    Such a seat holds the start (:func:`_with_current_pools` refuses it), so
    the lobby names it rather than letting the last player's ready fail on
    someone else's seat.
    """
    return [
        seat
        for seat in battle.combatants.filter(
            claimed_seat_q(), is_open_seat=True, is_saved_character=True
        ).select_related("character")
        if seat.character is not None and not seat.character.is_alive
    ]


def _start_locked(battle: Battle) -> Battle:
    """Close the lobby and build the engine state. Caller holds the row lock.

    Unclaimed seats leave the roster (their figures never existed); claimed
    seats must all be ready. Positions are dealt fresh over the settled
    roster, then every per-row snapshot assembles into ``state_json`` and
    the frozen ``initial_state_json`` — from here the battle behaves exactly
    like one that never had a lobby. ("Classic" now names a rules profile,
    so the old wording for "ordinary" would read as the wrong thing.)

    A classic battle crosses here rather than at creation (#251), and this
    is the only place it could: the crossing rebuilds every figure by the
    destination's rules, so running it before the seats settled would cross
    a roster that still held open seats and characters nobody had brought
    yet. What the crossing costs a *player's* own character therefore lands
    in the log at the start, which is why the muster names the rules being
    joined before anyone claims a seat.
    """
    if battle.status != Battle.STATUS_LOBBY:
        raise BattleRefusal("This battle has already started")
    if battle.combatants.filter(
        claimed_seat_q(), is_open_seat=True, is_ready=False
    ).exists():
        raise BattleRefusal("Every seated player must be ready first")
    battle.combatants.filter(is_open_seat=True).exclude(claimed_seat_q()).delete()
    combatants = list(battle.combatants.select_related("character"))
    if len(combatants) < MIN_COMBATANTS:
        raise BattleRefusal(
            f"A battle needs at least {MIN_COMBATANTS} combatants; "
            "wait for more seats to fill"
        )
    arena_radius = max(MIN_ARENA_RADIUS, len(combatants))
    positions = starting_positions(len(combatants), arena_radius)
    entries = []
    for combatant, position in zip(combatants, positions, strict=True):
        entry = _with_current_pools(combatant, dict(combatant.snapshot_json))
        entry["q"], entry["r"] = position
        entry["facing"] = hexes.direction_towards(position, (0, 0))
        entries.append(entry)
    state = tarmar_state({"arena_radius": arena_radius, "combatants": entries})
    playable = _playable_state(battle, state)
    battle.status = Battle.STATUS_SETUP
    battle.state_json = dump_state(playable)
    battle.initial_state_json = dump_state(playable)
    battle.save()
    # A fight puts its seated characters in play (issue #803).
    record_entry_into_play(battle_id=battle.pk)
    return battle


def start_battle(battle_id: int) -> Battle:
    """The DM's start: close the lobby and deal the arena (see _start_locked).

    Raises:
        ValueError: not a lobby, unready players, or too few combatants.
    """
    with transaction.atomic():
        battle = Battle.objects.select_for_update().get(pk=battle_id)
        return _start_locked(battle)


#: Why a player may not restart a fight that has already marked a record.
RESULTS_RECORDED_REFUSAL = (
    "This fight has already hurt or killed a character on their record; only a "
    "Game Master can restart it"
)


#: Why a fight that writes to records will not go on (issue #842).
STALE_POOLS_REFUSAL = (
    "{names}: the record's pools have changed since this fight last wrote "
    "them -- healed, hurt in another fight, or handed back by a restart -- "
    "so the board no longer holds what the character has. A Game Master "
    "takes the pools from the records before the fight goes on"
)

#: Why only a Game Master takes the pools from the records.
RESNAPSHOT_GAME_MASTER_ONLY = (
    "Only a Game Master can take a fight's pools from the character records"
)


class StalePools(BattleRefusal):
    """A record-writing fight whose figures no longer match their records.

    A refusal in the player's words (#842). The advance and a player's
    choice (which resumes the turn) both reach it, and both views answer it
    with the readout beside it (#112).
    """


class BattleInLobby(BattleRefusal):
    """A battle asked to advance before it has started.

    Its own type since issue #13, so the advance view could answer this
    refusal in its words while every other exception the engine raises stays
    behind a fixed message; a :class:`BattleRefusal` since #39, as every
    refusal the battle views show is.
    """


def _figure_pools(combatant_state) -> tuple[int, int, int, int]:
    """A figure's Fatigue, Body, Mana and debt, as :func:`_record_pools`
    gives the record's."""
    return (
        combatant_state.fatigue,
        combatant_state.body,
        combatant_state.mana,
        combatant_state.debt,
    )


def _record_pools(combatant_state, character: Character) -> tuple[int, int, int, int]:
    """What the record holds now, as the board would have taken it.

    Fatigue and Body as the record's current pools; Mana capped at the
    figure's own pool, as :func:`snapshot_character` caps it. A character
    with no Mana Pool on the record has no mana to compare, so the figure's
    own stands. The mana owed (tarmar-engine #28) as the record owes it,
    as :func:`snapshot_character` seats it: a debt a Game Master settled
    mid-fight must not go on being rolled against or paid back on the board.
    """
    fatigue, body = character.pools()
    mana = (
        min(int(combatant_state.max_mana), current_mana(character))
        if character.free_spell_points
        else combatant_state.mana
    )
    return (
        fatigue - character.fatigue_damage,
        body - character.body_damage,
        mana,
        character.mana_owed,
    )


#: Why a fight no longer writes one character's record (review of #837), as
#: the battle log says it, once per character.
RECORDS_WITHHELD_NOTE = (
    "{name}'s wounds and death are no longer written to their record: {reason}"
)

#: The two reasons, by the kind of fight.
WITHHELD_OTHER_PLAYERS = (
    "the character belongs to another player, and the session's owner is no "
    "longer a Game Master"
)
WITHHELD_NOT_GAME_MASTER = (
    "the battle counted because a Game Master ran it, and whoever made it is no "
    "longer a Game Master"
)


def withheld_characters(battle: Battle) -> dict[int, str]:
    """Characters this record-writing fight may no longer write, and why.

    A fight writes to records because of who made it: a Games Master
    session's fight for its owner's characters, and any character a Game
    Master may change; a battle a Game Master runs because he runs it
    (``Battle.writes_to_records``, fixed at creation). The maker's role is
    read again here, on every write-back, so a demoted account stops writing
    to what the role let it write (review of #837, the executor's rule on the
    board). Coordinator's finding; the kinds are split as the standing rule
    splits them, and Spencer may overrule.

    Returns:
        ``{character_id: reason}``; empty when the fight writes to no record
        or its maker still holds whatever let it write.
    """
    if not battle.writes_to_records or battle.created_by_id is None:
        return {}
    maker = type(battle.created_by).objects.get(pk=battle.created_by_id)
    session_fight = battle.gm_sessions.exists()
    if not session_fight and is_dm(maker):
        return {}
    seated = battle.combatants.filter(
        is_saved_character=True, character__isnull=False, character__is_deleted=False
    ).values_list("character_id", "character__user_id")
    if session_fight:
        if is_dm(maker):
            return {}
        return {
            character_id: WITHHELD_OTHER_PLAYERS
            for character_id, owner_id in seated
            if owner_id != maker.pk
        }
    return {character_id: WITHHELD_NOT_GAME_MASTER for character_id, _owner in seated}


def _note_withheld(battle: Battle) -> None:
    """Say in the battle log, once each, which records the fight stopped writing.

    The log is kept with the battle and read on every load, so this is the
    persistent notice the change owes the table; nothing transient.
    """
    withheld = withheld_characters(battle)
    if not withheld:
        return
    names = dict(
        battle.combatants.filter(character_id__in=withheld).values_list(
            "character_id", "display_name"
        )
    )
    for character_id, reason in withheld.items():
        if battle.events.filter(payload__records_withheld=character_id).exists():
            continue
        _control_event(
            battle,
            RECORDS_WITHHELD_NOTE.format(
                name=names.get(character_id, "A character"), reason=reason
            ),
            {"records_withheld": character_id},
        )
    battle.save(update_fields=["state_json", "updated_at"])


def _moved_figures(battle: Battle, state) -> list[tuple[BattleCombatant, object]]:
    """Seated characters whose record moved since the board last wrote it (#842).

    A fight that writes to records writes each turn's losses as it commits
    them (#750, #751), so between turns every living figure's pools equal
    its character's record. When they do not, something outside this fight
    moved the record: a Game Master's healing while the fight sat unfinished,
    a turn of another unfinished fight the character also sits in, or a
    restart whose hand-back was floored at unhurt. Advancing then would
    fight on with pools the character no longer has. A dead figure, or a
    character dead on the record, takes nothing more, so is not compared.

    Returns:
        ``(combatant, figure state)`` for each that moved, in roster order.
    """
    # Classic rules carry no Fatigue or Body the record holds (their ST track
    # is the figure's own, FigureVitals says so), so a classic fight writes
    # only a death, and has no pools that could go stale. Intended; noted on
    # #842.
    if not battle.writes_to_records or battle.rules_profile == CLASSIC_PROFILE:
        return []
    figures = {figure.combatant_id: figure for figure in state.combatants}
    moved = []
    # A record the fight no longer writes is not one its pools answer to.
    withheld = withheld_characters(battle)
    rows = battle.combatants.filter(
        is_saved_character=True, character__isnull=False, character__is_deleted=False
    ).select_related("character")
    for combatant in rows.order_by("pk"):
        figure = figures.get(combatant.pk)
        character = combatant.character
        if figure is None or character is None or character.pk in withheld:
            continue
        if not figure.alive or not character.is_alive or not character.is_complete:
            continue
        if _figure_pools(figure) != _record_pools(figure, character):
            moved.append((combatant, figure))
    return moved


def stale_pools(battle: Battle) -> list[str]:
    """The names of the figures :func:`_moved_figures` finds, for the page."""
    if not battle.writes_to_records or not battle.state_json:
        return []
    if battle.status in (Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE):
        return []
    state = profiles.for_battle(battle).load(battle.state_json)
    return [
        combatant.display_name for combatant, _figure in _moved_figures(battle, state)
    ]


def resnapshot_pools(battle_id: int, *, is_game_master: bool) -> Battle:
    """Take each moved figure's pools from its character record (issue #842).

    The Game Master's answer to :data:`STALE_POOLS_REFUSAL`: the board takes
    the record's current Fatigue, Body and Mana and the mana owed
    (tarmar-engine #28), a figure at 0 or below
    comes down, and the log says so. Nothing is written to the record, which
    is what the board now agrees with. Coordinator's ruling under the
    standing rule; Spencer may overrule.

    Raises:
        ValueError: A player asking, or nothing to take.
    """
    if not is_game_master:
        raise BattleRefusal(RESNAPSHOT_GAME_MASTER_ONLY)
    with transaction.atomic():
        battle = Battle.objects.select_for_update().get(pk=battle_id)
        if not battle.state_json:
            raise BattleRefusal("This battle has not started")
        profile = profiles.for_battle(battle)
        state = profile.load(battle.state_json)
        moved = _moved_figures(battle, state)
        if not moved:
            raise BattleRefusal("Every figure already holds its record's pools")
        lines = []
        for combatant, figure in moved:
            fatigue, body, mana, debt = _record_pools(figure, combatant.character)
            lines.append(
                f"{combatant.display_name} Fatigue {figure.fatigue}→{fatigue}, "
                f"Body {figure.body}→{body}, Mana {figure.mana}→{mana}"
                # The debt (tarmar-engine #28) only where either side owes.
                + (f", owed {figure.debt}→{debt}" if figure.debt or debt else "")
            )
            figure.fatigue, figure.body, figure.mana = fatigue, body, mana
            figure.debt = debt
            figure.conscious = fatigue > 0 and body > 0
        battle.state_json = dump_state(state)
        battle.save(update_fields=["state_json", "updated_at"])
        _control_event(
            battle,
            "The Game Master took the pools from the character records: "
            + "; ".join(lines),
            {"resnapshot": [combatant.pk for combatant, _figure in moved]},
        )
        battle.save(update_fields=["state_json", "updated_at"])
    return battle


def _lost(start: int | None, end: int | None) -> int:
    """How much a pool fell from ``start`` to ``end``; 0 when it has no pool."""
    if start is None or end is None:
        return 0
    return start - end


def fight_reports(
    battle: Battle,
    before: dict[int, profiles.FigureVitals],
    after: dict[int, profiles.FigureVitals],
    *,
    turn: int,
    closes: str = "",
    started: dict[int, profiles.FigureVitals] | None = None,
) -> list[FightReport]:
    """What the board did to each saved character between two snapshots.

    The battle side of :class:`characters.services.FightReport`, the write-back
    contract (issues #750, #751): one report per saved character whose figure
    lost or regained pools, or died, between ``before`` and ``after`` -- and,
    when ``closes`` says the fight's history line is due, one for every saved
    character, so the line is written even for a turn that did nothing to them.

    Nothing at all for a battle that does not write to records
    (``Battle.writes_to_records``): a player's own battle, lobby or classic
    crossing leaves every record as it was.

    Only a *saved* character (``is_saved_character``, one brought from the
    character list or a Games Master session) has a record beyond this
    fight. A fighter generated for a battle is saved as a ``Character`` row
    too, but the battle is its whole life; and another player's public
    character seated by someone who is not a Game Master fights as a copy
    (:func:`create_battle`).

    Args:
        battle: The battle.
        before: The figures' vitals at the start of what is being reported.
        after: Their vitals at its end.
        turn: The turn the reports name.
        closes: ``characters.services.FIGHT_ENDED`` or ``FIGHT_RESTARTED``
            when the fight's history line is due, else ``""``.
        started: The figures' vitals when the fight began, the history
            line's old value when the fight ends.

    Returns:
        The reports that change anything, in roster order.
    """
    if not battle.writes_to_records:
        return []
    withheld = withheld_characters(battle)
    rows = [
        (combatant_id, character_id)
        for combatant_id, character_id in battle.combatants.filter(
            is_saved_character=True,
            character__isnull=False,
            character__is_deleted=False,
        )
        .order_by("pk")
        .values_list("pk", "character_id")
        # Not a record this fight may still write (review of #837).
        if character_id not in withheld
    ]
    # Who the history names (second review): the Game Master for a fight
    # whose foes are theirs -- the player who started it did not mark their
    # own wounds -- and otherwise the Game Master who made the battle count.
    if battle.foes_belong_to_game_master or battle.created_by is None:
        author = character_services.GAMES_MASTER_AUTHOR
    else:
        author = battle.created_by.username
    reports = []
    for combatant_id, character_id in rows:
        start, end = before.get(combatant_id), after.get(combatant_id)
        # A figure missing from one side (a lobby seat that never reached the
        # board) has nothing to compare.
        if start is None or end is None:
            continue
        began = (started or {}).get(combatant_id)
        report = FightReport(
            character_id=character_id,
            fight=battle.name,
            turn=turn,
            fatigue_lost=_lost(start.fatigue, end.fatigue),
            body_lost=_lost(start.body, end.body),
            mana_spent=_lost(start.mana, end.mana),
            # What the debt rose by: the fall from the end back to the start.
            debt_incurred=_lost(end.debt, start.debt),
            died=start.alive and not end.alive,
            closes=closes,
            started_fatigue=began.fatigue if began is not None else None,
            started_body=began.body if began is not None else None,
            started_mana=began.mana if began is not None else None,
            started_debt=began.debt if began is not None else None,
            author=author,
        )
        if report.changes_anything:
            reports.append(report)
    return reports


def results_recorded(battle: Battle) -> bool:
    """Whether this fight has already hurt or killed a character on the record.

    Asked before a restart: once it has, only a Game Master may restart it
    (:func:`restart_battle`), and the page offers the control accordingly.
    Always false for a battle that writes to no record.
    """
    if not (
        battle.writes_to_records and battle.initial_state_json and battle.state_json
    ):
        return False
    profile = profiles.for_battle(battle)
    starting = profile.figure_vitals(profile.load(battle.initial_state_json))
    reached = profile.figure_vitals(profile.load(battle.state_json))
    return bool(fight_reports(battle, starting, reached, turn=battle.turn_number))


def _write_back(reports: list[FightReport]) -> None:
    """Hand each report to the character record, in the caller's transaction."""
    for report in reports:
        character_services.record_fight(report)


def restart_battle(battle_id: int, *, is_game_master: bool = False) -> Battle:
    """Replay this battle from turn 0 with the same roster and seed.

    The turn-0 snapshot taken at creation goes back into ``state_json``, the
    event log is cleared, and the turn counter and status reset — so the next
    advance is turn 1 again, event for event, because the Roller is seeded
    ``battle.seed + battle.turn_number``. Combatants keep the names they
    carry now: a rename made mid-battle survives the restart.

    A classic battle's crossing notes survive too (#288): they describe the
    roster the restart puts back — deterministic from it, and irrecoverable
    once deleted, since the pre-crossing roster is not persisted — so
    clearing the log keeps the rows :func:`_record_crossing` marked with
    ``payload["crossing"]``.

    In a fight that writes to records (``Battle.writes_to_records``), what
    the abandoned run did to saved characters' pools is handed back to their
    records, because the replay takes it again; a death stays recorded
    (issue #750), and the dead go back on the board dead, which the log
    says (issue #854). Once such a fight has marked a record at all, only a Game
    Master may restart it (:func:`results_recorded`). A player's own battle
    writes nothing and restarts as it always has (#188).

    Args:
        battle_id: The battle.
        is_game_master: Whether a Game Master asked.

    Raises:
        ValueError: for a battle created before the starting snapshot
            existed, or a player restarting a fight that has marked a record.
    """
    with transaction.atomic():
        battle = Battle.objects.select_for_update().get(pk=battle_id)
        if not battle.initial_state_json:
            raise BattleRefusal("This battle has no starting snapshot to restart from")
        if not is_game_master and results_recorded(battle):
            raise BattleRefusal(RESULTS_RECORDED_REFUSAL)
        profile = profiles.for_battle(battle)
        state = profile.load(battle.initial_state_json)
        starting = profile.figure_vitals(state)
        reached = (
            profile.figure_vitals(profile.load(battle.state_json))
            if battle.state_json
            else starting
        )
        abandoned_turn = battle.turn_number
        for combatant in battle.combatants.all():
            profile.rename(state, combatant.pk, combatant.display_name)
        laid_dead = _lay_recorded_dead(battle, profile, state)
        kept_off = _leave_withdrawn_off(battle, state)
        # Two steps because a JSON key lookup is NULL (not false) for rows
        # whose payload lacks the key, and NOT NULL matches nothing — a
        # one-shot ``exclude(payload__crossing=True)`` would delete no rows.
        crossing_notes = list(
            battle.events.filter(payload__crossing=True).values_list("pk", flat=True)
        )
        battle.events.exclude(pk__in=crossing_notes).delete()
        battle.state_json = dump_state(state)
        battle.turn_number = 0
        battle.status = Battle.STATUS_SETUP
        battle.turns_without_progress = 0
        # A restart abandons any mid-turn player input along with the turn.
        battle.turn_inputs_json = {}
        battle.pending_input_json = {}
        if laid_dead:
            _control_event(
                battle,
                f"{', '.join(laid_dead)} stay"
                f"{'s' if len(laid_dead) == 1 else ''} dead: the record says "
                "so, and a restart does not bring anyone back. A Game Master "
                "revives a character on its sheet",
                {"laid_dead": laid_dead},
            )
        if kept_off:
            _control_event(
                battle,
                f"{', '.join(kept_off)} stay"
                f"{'s' if len(kept_off) == 1 else ''} off the board: withdrawn "
                "from the session by the player who owns the character",
                {"withdrawn_kept_off": kept_off},
            )
        battle.save()
        _write_back(
            fight_reports(
                battle,
                reached,
                starting,
                turn=abandoned_turn,
                closes=character_services.FIGHT_RESTARTED,
            )
        )
        _note_withheld(battle)
    return battle


def _lay_recorded_dead(battle: Battle, profile, state) -> list[str]:
    """Put back dead every figure whose character is dead on the record (#854).

    Spencer's ruling: death on the board is a record, and only a Game Master
    reverses it, by an explicit action the ledger names (``revive``), never
    by a restart. A restart puts the turn-0 snapshot back, where every
    figure stood; without this the dead stood up and fought on in the replay
    while the record, which takes nothing more from the dead
    (``apply_fight_report``), said they were dead. Only a fight that writes
    to records answers to them.

    Returns:
        The names laid dead, in roster order.
    """
    if not battle.writes_to_records:
        return []
    rows = battle.combatants.filter(
        is_saved_character=True,
        character__isnull=False,
        character__is_deleted=False,
        character__is_alive=False,
    ).order_by("pk")
    names = []
    for combatant in rows:
        profile.lay_dead(state, combatant.pk)
        names.append(combatant.display_name)
    return names


class PlayerInputNeeded(Exception):
    """The turn reached a player-controlled combatant with no recorded choice.

    Raised inside :func:`advance_turn`'s transaction (aborting it — nothing
    of the partial turn persists); the caller records which combatant the
    battle now waits on, and the candidate menu computed at the pause.
    """

    def __init__(self, combatant_id: int, candidates: list, *, stale: bool = False):
        super().__init__(f"Combatant {combatant_id} awaits a player choice")
        self.combatant_id = combatant_id
        self.candidates = candidates
        # A recorded choice no longer matching the replayed menu (only
        # possible via tampering or a bug) is dropped and re-asked.
        self.stale = stale


def same_target(one, other) -> bool:
    """Do these two target ids name the same combatant?

    Compared as text, because the two sides rarely arrive as the same type:
    a Tarmar combatant is an ``int`` that JSON may deliver as a string, and a
    classic figure is a string ``uid`` that must not be coerced to a number.
    ``None`` (no target) matches only ``None``.
    """
    if one is None or other is None:
        return one is None and other is None
    return str(one) == str(other)


def option_variant(fields: dict) -> dict:
    """What sets one menu option apart from another of the same letter,
    target and spell, from a menu option or a recorded choice.

    How a cast is paid: the mana it pushes into its spell (issue #960) and
    whether it borrows the mana its caster lacks (tarmar-engine #28). And
    whose Runaway an anchor takes back (tarmar-engine #30): the combatant id
    the engine's ``anchor_target`` names, the actor's own for its own. And
    the mana a counter commits against the caster its target names
    (tarmar-engine #31, the engine's ``counter_mana``). A missing
    ``push_mana`` is 0, a missing ``borrow`` false and a missing
    ``anchor_target`` or ``counter_mana`` ``None``, the plain option, as a
    menu or a choice stored before Push, Borrowing, Runaway or Counterspell
    reads; the engine's payload carries each key only when set. An
    escalation (the engine's ``escalate``) is not a variant: every cast of a
    countered spell at its target escalates, so the letter, target and
    spell already name it.

    Args:
        fields: A menu option's payload, a recorded choice, or the act
            endpoint's parsed body.

    Returns:
        ``{"push_mana": int, "borrow": bool, "anchor_target": int | None,
        "counter_mana": int | None}``.
    """
    anchor_target = fields.get("anchor_target")
    counter_mana = fields.get("counter_mana")
    return {
        "push_mana": fields.get("push_mana") or 0,
        "borrow": bool(fields.get("borrow")),
        "anchor_target": int(anchor_target) if anchor_target is not None else None,
        "counter_mana": int(counter_mana) if counter_mana is not None else None,
    }


def names_option(option: dict, recorded: dict) -> bool:
    """Does a recorded choice name this menu option?

    The one comparison both sides of a player's choice make: the submission
    against the stored pending menu, and the recorded choice against the
    replayed one. An option is its letter, its target, its spell and its
    variant (:func:`option_variant`): the mana it pushes into that spell
    (issue #960), whether it borrows (tarmar-engine #28), whose Runaway it
    anchors (tarmar-engine #30), and the mana a counter commits
    (tarmar-engine #31). So a pushed or borrowing cast is a different option
    from the plain cast of the same spell, an anchor names the one Runaway
    it takes back, a counter names its caster and its commitment, and a
    player may record only a push, a borrowing, an anchor or a counter the
    engine offered.

    Args:
        option: A menu option's fields, as :meth:`~tarmar_engine.policy.
            Candidate.to_payload` writes them.
        recorded: The choice: ``letter``, ``target_id``, ``spell_key``,
            ``push_mana``, ``borrow``, ``anchor_target`` and
            ``counter_mana``.
    """
    return (
        option.get("letter") == recorded.get("letter")
        and same_target(option.get("target_id"), recorded.get("target_id"))
        and (option.get("spell_key") or "") == (recorded.get("spell_key") or "")
        and option_variant(option) == option_variant(recorded)
    )


def _match_candidate(candidates: list, recorded: dict):
    """The replayed candidate a recorded player choice names, or None."""
    for candidate in candidates:
        if names_option(candidate.to_payload(), recorded):
            return candidate
    return None


def manual_controllers(battle: Battle) -> dict:
    """Every combatant the turn must stop for, mapped to who decides it.

    The one place "is this combatant played by a person?" is answered
    (#308). It is a *mode* now, not a shape: a row is manual because
    somebody set it manual, or because the battle's default is manual and
    the row has no mode of its own. Before this, the answer was
    ``player_id is not None``, which had two faults — it could not express
    "the AI runs my seat for a while", and it silently missed a guest's
    seat (#305 put the guest's key on ``guest_key``, not on ``player``), so
    a guest who claimed a seat was played by the AI.

    Values are the *label* the decision event credits, not an identity;
    who may actually submit is :func:`battle.access.decides_for`.
    """
    return {
        combatant.pk: control.owner_label(battle, combatant)
        for combatant in battle.combatants.select_related("player")
        if control.is_manual(battle, combatant)
    }


def _control_event(battle: Battle, message: str, payload: dict) -> BattleEvent | None:
    """File a control change in the battle log, or nothing if there is no log.

    Numbering runs through the profile so the line takes the next sequence
    the engine itself would have taken — and, for Tarmar, the bumped
    counter is written back into ``state_json``, because that is where a
    Tarmar battle keeps it. A battle still in its lobby has no state and no
    log yet: nothing has been decided, so there is nothing to explain, and
    inventing a sequence there would collide with the engine's own
    numbering once the lobby closes.
    """
    if not battle.state_json:
        return None
    profile = profiles.for_battle(battle)
    state = profile.load(battle.state_json)
    minted = profile.mint_event(
        state,
        phase=0,
        event_type="status",
        actor="",
        payload=payload,
        message=message,
    )
    event = BattleEvent.objects.create(battle=battle, **minted)
    battle.state_json = dump_state(state)
    return event


def _resettle_pending(battle: Battle) -> None:
    """After a control change, stop awaiting anyone the AI now speaks for.

    The battle parks in ``awaiting`` on one named combatant. Hand that
    combatant to the AI mid-pause and nobody is coming — the turn would sit
    there with its Advance button disabled forever. Clearing the pause puts
    the battle back where it was before the turn was attempted; the next
    advance replays the same turn from the same state and seed, and the AI
    now answers where the person used to.

    A recorded choice belonging to a combatant that is no longer manual is
    dropped in the same breath: it is the AI's decision now, and leaving the
    input would quietly resurrect the old one if the mode flipped back.
    """
    inputs = dict(battle.turn_inputs_json or {})
    manual = manual_controllers(battle)
    stale = [key for key in inputs if int(key) not in manual]
    for key in stale:
        inputs.pop(key)
    battle.turn_inputs_json = inputs
    if battle.status != Battle.STATUS_AWAITING:
        return
    awaited = (battle.pending_input_json or {}).get("combatant_id")
    if awaited not in manual:
        battle.pending_input_json = {}
        battle.status = Battle.STATUS_PAUSED


def set_battle_control_mode(
    battle_id: int, mode: str, *, apply_to_all: bool = False
) -> tuple[Battle, BattleEvent | None]:
    """Set the battle's default control mode (issue #308).

    The default is inherited by every combatant that has no mode of its
    own. ``apply_to_all`` is the "set them all to X" action Spencer asked
    for, expressed the honest way: it *clears* the per-combatant overrides
    rather than stamping the mode on every row. Stamping would leave a
    battle that looks uniform but ignores its own default forever; clearing
    leaves one that says what it is — everybody follows the battle.

    Raises:
        ValueError: for a mode that is not automatic or manual.
    """
    mode = control.clean_mode(mode)
    with transaction.atomic():
        battle = Battle.objects.select_for_update().get(pk=battle_id)
        battle.control_mode = mode
        if apply_to_all:
            battle.combatants.exclude(control_mode=control.INHERIT).update(
                control_mode=control.INHERIT
            )
        _resettle_pending(battle)
        event = _control_event(
            battle,
            control.battle_change_message(mode, applied_to_all=apply_to_all),
            {"control_mode": mode, "scope": "battle", "applied_to_all": apply_to_all},
        )
        battle.save()
    return battle, event


def set_hth_agreement(
    battle_id: int,
    actor: Actor,
    *,
    agreed: bool,
    whole_battle: bool = False,
    pair: tuple[int, int] | None = None,
) -> tuple[Battle, BattleEvent | None]:
    """Mark, or unmark, the whole battle or one pair "HTH by agreement" (#867).

    Spencer's ruling of 2026-10-09: "a Game Master marks a battle, or a
    pair, as 'HTH by agreement'; players alone still need the engine's
    entry condition." The Game Master is the fight's runner
    (:func:`runs_the_seats`: a session fight's owner, any other battle's
    maker, while a Game Master account), as for a seat hand-off; another Game
    Master is a spectator here, and a player's own practice battle has no
    Game Master to mark it (coordinator's ruling under the standing rule;
    Spencer may overrule). This path is the only one that changes the record
    (the admin shows the field read-only). The
    ruling is the battle's (``Battle.hth_agreement_json``); the Tarmar
    profile writes it onto the engine state every turn
    (:func:`battle.agreement.fill`). Each change files one line in the log.

    A change made while a person's move is awaited changes that person's
    menu, so the pause is cleared and the next advance replays the turn
    under the new ruling; a recorded choice no longer on the menu is asked
    again (the replay's stale check).

    Args:
        battle_id: The battle.
        actor: Who is asking; must be the fight's runner, a Game Master.
        agreed: Mark (True) or unmark (False).
        whole_battle: The change is to the whole battle.
        pair: Otherwise, the two ``BattleCombatant`` pks the change is to.

    Returns:
        The battle, and the log line (None when nothing changed or the
        battle has no log yet).

    Raises:
        ValueError: not the fight's Game Master, a classic battle, neither or both of
            ``whole_battle`` and ``pair``, or a pair that is not two enemies
            in this battle.
    """
    hth_words = words().hth_agreement
    if whole_battle == (pair is not None):
        raise BattleRefusal(hth_words.refuse_scope)
    with transaction.atomic():
        battle = Battle.objects.select_for_update().get(pk=battle_id)
        game_master = _ruling_game_master(battle, actor, hth_words.game_master_only)
        if battle.rules_profile != TARMAR_PROFILE:
            raise BattleRefusal(hth_words.refuse_classic)
        rows = {row.pk: row for row in battle.combatants.all()}
        names = {pk: row.display_name for pk, row in rows.items()}
        current = agreement.Agreement.of(battle)
        key = None
        if pair is not None:
            first, second = pair
            if first == second or first not in rows or second not in rows:
                raise BattleRefusal(hth_words.refuse_pair)
            if rows[first].team and rows[first].team == rows[second].team:
                raise BattleRefusal(
                    hth_words.refuse_same_side.format(
                        first=names[first], second=names[second]
                    )
                )
            key = agreement.pair_key(first, second)
            pairs = set(current.pairs)
            if agreed:
                pairs.add(key)
            else:
                pairs.discard(key)
            updated = agreement.Agreement(current.whole_battle, frozenset(pairs))
        else:
            updated = agreement.Agreement(agreed, current.pairs)
        if updated == current:
            return battle, None
        battle.hth_agreement_json = updated.to_json()
        _reopen_awaited_menu(battle)
        event = _control_event(
            battle,
            agreement.change_message(
                whole_battle=agreed if whole_battle else None,
                pair=key,
                agreed=agreed,
                names=names,
                by=game_master_name(game_master.username),
            ),
            {
                "hth_agreement": updated.to_json(),
                "scope": "battle" if whole_battle else "pair",
                "pair": list(key) if key is not None else None,
                "agreed": agreed,
            },
        )
        battle.save()
    return battle, event


def _reopen_awaited_menu(battle: Battle) -> None:
    """Clear a pause on a person's move after a ruling that changes menus.

    A Game Master's ruling made while a move is awaited changes that
    person's menu, so the pause is cleared and the next advance replays the
    turn under the new ruling; a recorded choice no longer on the menu is
    asked again (the replay's stale check).
    """
    if battle.status == Battle.STATUS_AWAITING:
        battle.pending_input_json = {}
        battle.status = Battle.STATUS_PAUSED


def _ruling_game_master(battle: Battle, actor: Actor, refusal: str) -> User:
    """The fight's Game Master making a ruling on it, or refuse.

    The runner of the fight, while a Game Master (:func:`runs_the_seats`),
    is the one account that records a ruling on it.

    Raises:
        BattleRefusal: ``refusal``, for anyone else.
    """
    game_master = actor.user
    if not isinstance(game_master, User) or not runs_the_seats(battle, game_master):
        raise BattleRefusal(refusal)
    return game_master


def set_magic_bounds_lifted(
    battle_id: int,
    actor: Actor,
    *,
    combatant_id: int,
    bounds: Collection[str],
    lifted: bool,
) -> tuple[Battle, BattleEvent | None]:
    """Lift, or hold again, magic bounds for one figure (issue #37).

    Spencer's ruling of 2026-10-14: a Game Master records "Channel lifted
    for <figure>", and likewise the push cap and the counter's commitment
    bounds, each separately or together, as a ruling on the battle; players'
    menus for other figures are unchanged. Who may record it is the
    HTH-by-agreement ruling's answer (:func:`set_hth_agreement`): the fight's
    runner, while a Game Master (:func:`runs_the_seats`). This path is the
    only one that changes the record (the admin shows the field read-only);
    the record keeps who lifted each bound and when, and each change files
    one line in the log. Lifting a bound already lifted keeps its first
    record. A change made while a move is awaited clears the pause
    (:func:`_reopen_awaited_menu`).

    Args:
        battle_id: The battle.
        actor: Who is asking; must be the fight's runner, a Game Master.
        combatant_id: The ``BattleCombatant`` pk of the figure.
        bounds: Which of :data:`battle.magic_bounds.LIFTABLE_BOUNDS` to change.
        lifted: Lift (True) or hold the figure to them again (False).

    Returns:
        The battle, and the log line (None when nothing changed or the
        battle has no log yet).

    Raises:
        BattleRefusal: not the fight's Game Master, a classic battle, no bound
            named, a bound to lift that the loaded canon does not impose
            (:func:`battle.magic_bounds.offered_bounds`) or an unknown one to
            hold again, or a figure not in this battle.
    """
    bounds_words = words().magic_bounds
    offered = magic_bounds.offered_bounds()
    # Any known bound may be held again, even one a changed canon no longer
    # imposes; only a bound the canon imposes may be lifted.
    allowed = offered if lifted else magic_bounds.LIFTABLE_BOUNDS
    if not bounds or any(bound not in allowed for bound in bounds):
        raise BattleRefusal(
            bounds_words.refuse_bounds.format(bounds=magic_bounds.bound_words(offered))
        )
    with transaction.atomic():
        battle = Battle.objects.select_for_update().get(pk=battle_id)
        game_master = _ruling_game_master(battle, actor, bounds_words.game_master_only)
        if battle.rules_profile != TARMAR_PROFILE:
            raise BattleRefusal(bounds_words.refuse_classic)
        figure = battle.combatants.filter(pk=combatant_id).first()
        if figure is None:
            raise BattleRefusal(bounds_words.refuse_combatant)
        current = magic_bounds.LiftedBounds.of(battle)
        already = current.for_figure(figure.pk)
        requested = frozenset(bounds)
        changing = [
            bound
            for bound in magic_bounds.LIFTABLE_BOUNDS
            if bound in requested and (bound in already) != lifted
        ]
        if lifted:
            updated = current.lifted(
                figure.pk,
                changing,
                by=game_master.username,
                by_id=game_master.pk,
                at=timezone.now().isoformat(),
            )
        else:
            updated = current.restored(figure.pk, changing)
        if not changing:
            return battle, None
        battle.magic_bounds_lifted_json = updated.to_json()
        _reopen_awaited_menu(battle)
        event = _control_event(
            battle,
            magic_bounds.change_message(
                bounds=changing,
                lifted=lifted,
                name=figure.display_name,
                by=game_master_name(game_master.username),
            ),
            {
                "magic_bounds_lifted": updated.to_json(),
                "combatant_id": figure.pk,
                "bounds": changing,
                "lifted": lifted,
            },
        )
        battle.save()
    return battle, event


def set_combatant_control_mode(
    battle_id: int, combatant_id: int, mode: str
) -> tuple[Battle, BattleCombatant, BattleEvent | None]:
    """Set one combatant's control mode, or hand it back to the battle (#308).

    ``mode`` may be :data:`battle.control.INHERIT` (the empty string), which
    removes the override rather than choosing a mode — the difference
    matters, because a row that merely *agrees* with the battle today should
    still follow it when the battle changes tomorrow.

    Raises:
        ValueError: for an unknown mode, or a combatant not in this battle.
    """
    mode = control.clean_combatant_mode(mode)
    with transaction.atomic():
        battle = Battle.objects.select_for_update().get(pk=battle_id)
        combatant = (
            battle.combatants.select_related("player").filter(pk=combatant_id).first()
        )
        if combatant is None:
            raise BattleRefusal("No such combatant in this battle")
        combatant.control_mode = mode
        combatant.save(update_fields=["control_mode"])
        _resettle_pending(battle)
        event = _control_event(
            battle,
            control.combatant_change_message(battle, combatant, mode),
            {
                "control_mode": mode,
                "scope": "combatant",
                "combatant_id": combatant.pk,
                "effective": control.effective_mode(battle, combatant),
            },
        )
        battle.save()
    return battle, combatant, event


#: Why only the battle's own Game Master takes a player's seat (issue #853).
TAKE_SEAT_GAME_MASTER_ONLY = (
    "Only the Game Master running this fight takes a seat, and only for a "
    "player who is absent"
)

#: Why a character withdrawn from the fight's session has no seat to take.
WITHDRAWN_SEAT_REFUSAL = (
    "{name} was withdrawn from this session by the player who owns the "
    "character; the seat is theirs alone"
)

#: Who may hand a taken seat back (issue #853).
RETURN_SEAT_OWNER_OR_GAME_MASTER = (
    "Only the character's owner or the Game Master running this fight hands a seat back"
)

#: Why a seat nobody else holds has nothing to give back (issue #60).
NO_SEAT_TO_RETURN = "Nobody has taken {name}'s seat, so there is none to give back"

#: Why a seat nobody holds has no absent player to stand in for (issue #60).
NO_SEAT_TO_TAKE = (
    "Nobody sits in {name}'s seat, so there is no absent player to stand in for"
)


def battle_runner_id(battle: Battle) -> int | None:
    """Whose fight this is, for standing in at a seat (issue #853 review).

    A Games Master session's fight is its session owner's; any other battle
    is its maker's (``created_by``). Read through the reverse relation, so
    the battle app needs no import of the Games Master app.
    """
    session = battle.gm_sessions.order_by("pk").first()
    if session is not None:
        return session.user_id
    return battle.created_by_id


def runs_the_seats(battle: Battle, user) -> bool:
    """Whether ``user`` is the Game Master who may stand in at a seat.

    The review's ruling: take and return are the battle's runner's -- the
    session owner for a session fight, the maker otherwise -- and only while
    that account is a Game Master. Any other Game Master is a spectator here.
    """
    return (
        user is not None
        and is_dm(user)
        and user.pk is not None
        and user.pk == battle_runner_id(battle)
    )


def withdrawn_from_session(battle: Battle, character: Character) -> bool:
    """Whether ``character`` sits in a session fight whose session no longer
    holds it: its owner withdrew it (#871 review)."""
    session = battle.gm_sessions.order_by("pk").first()
    return (
        session is not None and not session.characters.filter(pk=character.pk).exists()
    )


def held_by_another(seat: BattleCombatant) -> bool:
    """Whether someone other than the character's owner sits in ``seat`` (#60).

    The one test of whether there is a seat to give back. ``player_id !=
    owner`` alone is wrong in a way that misleads: an unseated character has
    ``player_id`` ``None``, which differs from every owner, so the owner of a
    character nobody holds was offered their own seat back.

    Args:
        seat: A saved character's seat (``seat.character`` is set).

    Returns:
        True when an account other than the owner, or a guest, holds it.
    """
    return seat_is_held(seat) and seat.player_id != seat.character.user_id


def _owned_seat(battle: Battle, combatant_id: int) -> BattleCombatant:
    """The saved character's seat ``combatant_id`` names, for a live battle.

    Raises:
        ValueError: The battle is over, or the combatant is not a saved
            character with an owner.
    """
    if battle.is_over:
        raise BattleRefusal("This battle is over")
    seat = (
        battle.combatants.select_related("character", "character__user", "player")
        .filter(pk=combatant_id)
        .first()
    )
    if seat is None or seat.character is None or seat.character.user_id is None:
        raise BattleRefusal(
            "Only a saved character's seat has an owner to stand in for"
        )
    return seat


def take_seat(
    battle_id: int, combatant_id: int, *, game_master
) -> tuple[BattleCombatant, BattleEvent | None]:
    """The fight's Game Master plays a character whose owner is absent (#853).

    Spencer's ruling: a seated character is played by its owner, unless the
    owner is absent and the Game Master takes it, and the ledger says so. So
    the seat moves to the Game Master's account and the battle log -- the
    fight's ledger -- names who took it from whom. The owner, or the same
    Game Master, hands it back with :func:`return_seat`. Nothing else about
    the figure changes: its control mode, its team, a choice already
    recorded for this turn.

    Only the battle's runner (:func:`runs_the_seats`) may, and never for a
    character its owner withdrew from the fight's session (#871 review).

    Args:
        battle_id: The battle.
        combatant_id: The character's combatant.
        game_master: The account taking the seat.

    Returns:
        The seat, and the battle log's line (``None`` before the board has a
        log, which a session fight always has).

    Raises:
        ValueError: Not this fight's Game Master, the battle is over, not a
            saved character, withdrawn, nobody holds the seat (#60), or the
            seat is already his.
    """
    with transaction.atomic():
        battle = Battle.objects.select_for_update().get(pk=battle_id)
        if not runs_the_seats(battle, game_master):
            raise BattleRefusal(TAKE_SEAT_GAME_MASTER_ONLY)
        seat = _owned_seat(battle, combatant_id)
        if withdrawn_from_session(battle, seat.character):
            raise BattleRefusal(WITHDRAWN_SEAT_REFUSAL.format(name=seat.display_name))
        if not seat_is_held(seat):
            raise BattleRefusal(NO_SEAT_TO_TAKE.format(name=seat.display_name))
        if seat.player_id == game_master.pk:
            raise BattleRefusal(f"You already play {seat.display_name}")
        owner = seat.character.user
        seat.player = game_master
        seat.guest_key = ""
        seat.save(update_fields=["player", "guest_key"])
        event = _control_event(
            battle,
            f"{game_master.username}, the Game Master, takes "
            f"{seat.display_name}'s seat: {owner.username}, who owns the "
            "character, is absent",
            {
                "seat": "taken",
                "combatant_id": seat.pk,
                "owner": owner.username,
                "by": game_master.username,
            },
        )
        battle.save()
    return seat, event


def return_seat(
    battle_id: int, combatant_id: int, *, user
) -> tuple[BattleCombatant, BattleEvent | None]:
    """Give a taken seat back to the character's owner (issue #853).

    The owner may take their character back whenever they return, and the
    fight's Game Master (:func:`runs_the_seats`) may hand it back; the battle
    log says so, as it said the seat was taken.

    Raises:
        ValueError: Neither the owner nor this fight's Game Master, the
            battle is over, not a saved character, the owner already holds
            the seat, or nobody holds it (:func:`held_by_another`, #60).
    """
    with transaction.atomic():
        battle = Battle.objects.select_for_update().get(pk=battle_id)
        seat = _owned_seat(battle, combatant_id)
        owner = seat.character.user
        if user is None or not (user.pk == owner.pk or runs_the_seats(battle, user)):
            raise BattleRefusal(RETURN_SEAT_OWNER_OR_GAME_MASTER)
        if seat.player_id == owner.pk:
            raise BattleRefusal(f"{owner.username} already plays {seat.display_name}")
        if not held_by_another(seat):
            raise BattleRefusal(NO_SEAT_TO_RETURN.format(name=seat.display_name))
        seat.player = owner
        seat.guest_key = ""
        seat.save(update_fields=["player", "guest_key"])
        event = _control_event(
            battle,
            f"{seat.display_name}'s seat goes back to {owner.username}, who "
            "owns the character",
            {
                "seat": "returned",
                "combatant_id": seat.pk,
                "owner": owner.username,
                "by": user.username,
            },
        )
        battle.save()
    return seat, event


def _take_off_board(state, combatant_id: int) -> None:
    """Remove one figure from a Tarmar board, and every reference to it.

    What a figure that leaves the field leaves behind: its hex, its holds,
    the per-turn lists that name it, an anchor aimed at its Runaway, the
    record that another figure's Runaway was caught from it (tarmar-engine
    #30), and a counter chosen or held against it and its place among the
    defenders of another's countered spell (tarmar-engine #31). A contest
    pressing a spell at it stays, and the engine ends it when its caster
    next chooses, since no cast can be aimed at a figure gone. The
    ``BattleCombatant`` row stays, so the roster and the log still know who
    it was.
    """
    state.combatants = [
        figure for figure in state.combatants if figure.combatant_id != combatant_id
    ]
    for figure in state.combatants:
        for listed in ("hth_with", "disengaged_from", "retreat_push_targets_this_turn"):
            setattr(
                figure,
                listed,
                [other for other in getattr(figure, listed) if other != combatant_id],
            )
        if figure.grappling == combatant_id:
            figure.grappling = None
        if figure.grappled_by == combatant_id:
            figure.grappled_by = None
        if figure.chosen_target == combatant_id:
            figure.chosen_target = None
        if figure.chosen_anchor == combatant_id:
            figure.chosen_anchor = None
        if figure.runaway is not None and figure.runaway.caught_from == combatant_id:
            figure.runaway.caught_from = None
        if (
            figure.chosen_counter is not None
            and figure.chosen_counter[0] == combatant_id
        ):
            figure.chosen_counter = None
        if figure.counter is not None and figure.counter.caster_id == combatant_id:
            figure.counter = None
        if figure.contest is not None:
            figure.contest.defender_ids = [
                defender
                for defender in figure.contest.defender_ids
                if defender != combatant_id
            ]


def withdraw_from_fight(battle_id: int, character: Character, *, by) -> bool:
    """Take a withdrawn character's figure off a live fight (#871 review).

    The coordinator's ruling under the standing rule (Spencer may overrule):
    the owner's withdrawal wins. The figure leaves the board as a figure that
    left the field does, the log says so, any Game Master's hold on its seat
    goes back to the owner, and the fight writes nothing more to the record
    (:func:`fight_reports` passes over a figure missing from the board). What
    the fight had already written stays, as the ruling keeps records intact.

    Args:
        battle_id: The session's battle.
        character: The withdrawn character.
        by: The owner who withdrew it.

    Returns:
        Whether a figure left the board; False when the battle is over, not
        Tarmar, or the character is not on it.
    """
    with transaction.atomic():
        battle = Battle.objects.select_for_update().get(pk=battle_id)
        if (
            battle.is_over
            or battle.rules_profile != TARMAR_PROFILE
            or not battle.state_json
        ):
            return False
        seat = battle.combatants.filter(character=character).first()
        if seat is None:
            return False
        profile = profiles.for_battle(battle)
        state = profile.load(battle.state_json)
        if not any(figure.combatant_id == seat.pk for figure in state.combatants):
            return False
        _take_off_board(state, seat.pk)
        battle.state_json = dump_state(state)
        seat.player = character.user
        seat.guest_key = ""
        seat.save(update_fields=["player", "guest_key"])
        inputs = dict(battle.turn_inputs_json or {})
        inputs.pop(str(seat.pk), None)
        battle.turn_inputs_json = inputs
        if (battle.pending_input_json or {}).get("combatant_id") == seat.pk:
            battle.pending_input_json = {}
            battle.status = Battle.STATUS_PAUSED
        _control_event(
            battle,
            f"{seat.display_name} leaves the fight: {by.username}, who owns the "
            "character, withdrew them from the session",
            {"withdrawn": seat.pk, "by": by.username},
        )
        battle.save()
    return True


def _leave_withdrawn_off(battle: Battle, state) -> list[str]:
    """On a restart, keep withdrawn characters off the board (#871 review).

    The turn-0 snapshot has every figure the fight began with; one whose owner
    has since withdrawn it from the session stays off, as it left.

    Returns:
        The names kept off, in roster order.
    """
    if battle.rules_profile != TARMAR_PROFILE:
        return []
    names = []
    rows = battle.combatants.filter(
        is_saved_character=True, character__isnull=False
    ).select_related("character")
    for combatant in rows.order_by("pk"):
        if withdrawn_from_session(battle, combatant.character) and any(
            figure.combatant_id == combatant.pk for figure in state.combatants
        ):
            _take_off_board(state, combatant.pk)
            names.append(combatant.display_name)
    return names


def bare_handed_figures(battle: Battle) -> list[str]:
    """Living saved characters on the board with no weapon in hand or carried.

    Issue #844: the board seats a character with no usable weapon
    bare-handed. A figure that picks one up since the fight began drops off
    the list, which is why this is read from the board and not the sheet.
    """
    if (
        battle.rules_profile != TARMAR_PROFILE
        or not battle.state_json
        or battle.is_over
    ):
        return []
    state = profiles.for_battle(battle).load(battle.state_json)
    seated = set(
        battle.combatants.filter(character__isnull=False).values_list("pk", flat=True)
    )
    return [
        figure.name
        for figure in state.combatants
        if figure.combatant_id in seated
        and figure.alive
        and not figure.weapon.item_id
        and not figure.spare_weapons
    ]


def unarmed_attached_line(session) -> str:
    """Who in a Games Master session would fight bare-handed, or ``""``.

    Before a fight (issue #844): the attached characters who could fight and
    carry no weapon the board can seat. ``session`` is a ``gm.GMSession``,
    read through its own methods so this app needs no import of that one.
    """
    if session is None or session.pk is None:
        return ""
    names = [
        character.name
        for character in session.attached_characters().playable()
        if character.is_alive and not character.carries_weapon
    ]
    if not names:
        return ""
    verb = "carry" if len(names) > 1 else "carries"
    return (
        f"{', '.join(names)} {verb} no weapon and would fight bare-handed. Buy "
        "gear on the character's sheet before the fight."
    )


#: The key the bare-handed line travels under: in the board's payload, and in
#: the Games Master's attach and detach answers (issues #844, #15). The pages
#: reading either repaint the Battle tab's line from it.
BARE_HANDED_KEY = "bare_handed"


def bare_handed_line(battle: Battle) -> str:
    """The Battle tab's line on who fights, or would fight, bare-handed.

    While the fight is on, the figures :func:`bare_handed_figures` names;
    once it is over, what the next fight would face -- the attached
    characters of the session it belongs to (:func:`unarmed_attached_line`,
    the second review of #844), so the warning is back before the next Fight
    without a reload. Worked out here, so the page that renders it at load
    and the payload that repaints it say the same thing.
    """
    if battle.is_over:
        return unarmed_attached_line(battle.gm_sessions.order_by("pk").first())
    names = bare_handed_figures(battle)
    if not names:
        return ""
    verb = "are" if len(names) > 1 else "is"
    return (
        f"{', '.join(names)} {verb} on the board with no weapon, fighting bare-handed."
    )


def _human_decider(profile, controllers: dict, inputs: dict):
    """Wrap the AI policy: manual combatants take their recorded choices.

    ``controllers`` maps combatant id -> the owner's label (see
    :func:`manual_controllers`); ``inputs`` holds the recorded choices for
    the in-progress turn. The AI policy still runs first for a manual
    combatant — its candidates ARE the legal menu (and the scores shown as
    advice) — but the *chosen* one is the owner's, its rationale rewritten
    to credit them. A manual combatant with no recorded choice pauses the
    turn via :class:`PlayerInputNeeded`, which is what makes a mixed battle
    work: the automatic combatants run, and the turn stops at the first
    manual one that has not spoken.

    ``profile`` supplies both the AI to consult and the identifier a
    combatant is known by, so a manual combatant works the same whichever
    rules the battle runs under.
    """
    choose = profiles.decider(profile.battle)

    def decide(state, actor):
        advice = choose(state, actor)
        combatant_id = profile.actor_id(actor)
        owner = controllers.get(combatant_id)
        if owner is None:
            return advice
        # A person aims their own attacks (#775), and reads each option as a
        # choice rather than as the AI's scoring (#788): the engine's menu,
        # widened and described, built here from the live state because the
        # rolled-back turn does not keep it.
        menu = turn_menu.player_menu(profile.name, state, actor, advice)
        recorded = inputs.get(str(combatant_id))
        if recorded is None:
            raise PlayerInputNeeded(combatant_id, menu)
        chosen = _match_candidate(menu, recorded)
        if chosen is None:
            raise PlayerInputNeeded(combatant_id, menu, stale=True)
        # The hex the person picked (#819, #866), checked against the menu
        # when it was recorded; the engine checks it again on the board as
        # it stands when the move is made, and logs a refusal. None when no
        # hex was picked: the option makes its own move, whatever hex the
        # AI's forecast had in mind.
        destination = recorded.get("destination")
        picked = replace(
            chosen,
            rationale=f"chosen by {owner} — {chosen.rationale}",
            destination=tuple(destination) if destination is not None else None,
        )
        return policy.Decision(chosen=picked, candidates=menu)

    return decide


def advance_turn(battle_id: int) -> tuple[Battle, list[BattleEvent]]:
    """Run one full turn in a transaction; returns (battle, new events).

    The Roller is seeded ``battle.seed + battle.turn_number`` so a battle can
    be resumed — or replayed from scratch — deterministically. A finished
    battle advances no further and returns no events; a lobby refuses.

    Combatants seated by players (#250) take their decisions from the
    battle's recorded ``turn_inputs_json`` instead of the AI. When the turn
    reaches a seated combatant with no recorded choice, the whole partial
    turn rolls back and the battle parks in ``awaiting`` with the pending
    menu stored; once the player's choice is recorded the turn re-runs from
    the same persisted state and seed, reproducing every roll and every
    earlier decision identically until it either completes or pauses on the
    next seated combatant.
    """
    try:
        battle, events = _advance_turn_once(battle_id)
    except PlayerInputNeeded as needed:
        with transaction.atomic():
            battle = Battle.objects.select_for_update().get(pk=battle_id)
            battle.status = Battle.STATUS_AWAITING
            battle.pending_input_json = {
                "combatant_id": needed.combatant_id,
                "turn": battle.turn_number + 1,
                # The menu keeps the hexes each option may take (#819, #866).
                "candidates": [
                    turn_menu.menu_payload(candidate) for candidate in needed.candidates
                ],
            }
            if needed.stale:
                inputs = dict(battle.turn_inputs_json or {})
                inputs.pop(str(needed.combatant_id), None)
                battle.turn_inputs_json = inputs
            battle.save()
        return battle, []
    # A battle that has already ended advances no further and returns no
    # events, so this is the turn that ended it, committed (issue #785).
    if events and battle.status in (Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE):
        battle_ended.send(sender=Battle, battle=battle)
    return battle, events


def _turn_hurt_somebody(before: dict, after: dict) -> bool:
    """Did the turn leave anyone worse off than it found them?

    ``before`` and ``after`` are one profile's :meth:`~battle.profiles
    .BattleProfile.vitals` snapshots bracketing the turn: per combatant, a
    tuple whose elements only fall when that combatant is worse off. Any
    element lower at the turn's end — pools drained, consciousness or life
    lost — is hurt; so is a combatant gone from the state entirely.
    Recovery is deliberately not progress: a hit that heals back within the
    turn leaves the fight exactly where it stood, which is the shape of the
    unwinnable matchups #289 is about.
    """
    for key, opening in before.items():
        closing = after.get(key)
        if closing is None:
            return True
        if any(end < start for end, start in zip(closing, opening, strict=True)):
            return True
    return False


def _advance_turn_once(battle_id: int) -> tuple[Battle, list[BattleEvent]]:
    """One attempt at running the turn; raises PlayerInputNeeded to pause."""
    with transaction.atomic():
        battle = Battle.objects.select_for_update().get(pk=battle_id)
        if battle.status in (Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE):
            return battle, []
        if battle.status == Battle.STATUS_LOBBY:
            raise BattleInLobby("This battle is still in its lobby")
        profile = profiles.for_battle(battle)
        controllers = manual_controllers(battle)
        decide = (
            _human_decider(profile, controllers, battle.turn_inputs_json or {})
            if controllers
            else profiles.decider(battle)
        )
        state = profile.load(battle.state_json)
        # A fight that writes to records goes on only from what the records
        # hold (issue #842, coordinator's ruling under the standing rule).
        moved = _moved_figures(battle, state)
        if moved:
            raise StalePools(
                STALE_POOLS_REFUSAL.format(
                    names=", ".join(combatant.display_name for combatant, _ in moved)
                )
            )
        vitals_before = profile.vitals(state)
        figures_before = profile.figure_vitals(state)
        raw_events = profile.run_turn(state, profile.roller(), decide)

        survivors = profile.survivors(state)
        if len(standing_sides(profile, survivors)) <= 1:
            battle.status = Battle.STATUS_FINISHED
            if survivors:
                winning_team = profile.actor_team(survivors[0])
                if winning_team:
                    names = ", ".join(profile.actor_name(actor) for actor in survivors)
                    raw_events.append(
                        profile.mint_event(
                            state,
                            phase=VICTORY_PHASE,
                            event_type="info",
                            actor=winning_team,
                            payload={
                                "winner_team": winning_team,
                                "winners": [
                                    profile.actor_id(actor) for actor in survivors
                                ],
                            },
                            message=(
                                f"{winning_team} stands victorious — "
                                f"{names} left on the field"
                            ),
                        )
                    )
                else:
                    winner = survivors[0]
                    winner_name = profile.actor_name(winner)
                    raw_events.append(
                        profile.mint_event(
                            state,
                            phase=VICTORY_PHASE,
                            event_type="info",
                            actor=winner_name,
                            payload={"winner": profile.actor_id(winner)},
                            message=f"{winner_name} stands victorious",
                        )
                    )
        else:
            # No winner yet: count how long the fight has been going nowhere.
            # A turn "hurt somebody" when anyone's vitals ended it lower than
            # they began it, or somebody died or dropped — judged from the
            # state, deterministically, so a replay stalls at the same turn.
            if _turn_hurt_somebody(vitals_before, profile.vitals(state)):
                battle.turns_without_progress = 0
            else:
                battle.turns_without_progress += 1
            if battle.turns_without_progress >= STALEMATE_TURNS:
                battle.status = Battle.STATUS_STALEMATE
                names = ", ".join(profile.actor_name(actor) for actor in survivors)
                raw_events.append(
                    profile.mint_event(
                        state,
                        phase=VICTORY_PHASE,
                        event_type="info",
                        actor="",
                        payload={
                            "stalemate": True,
                            "standing": [
                                profile.actor_id(actor) for actor in survivors
                            ],
                        },
                        message=(
                            f"Stalemate — {STALEMATE_TURNS} turns without "
                            f"anyone hurt; {names} still standing"
                        ),
                    )
                )
            else:
                battle.status = Battle.STATUS_PAUSED

        events = BattleEvent.objects.bulk_create(
            BattleEvent(battle=battle, **event) for event in raw_events
        )
        battle.state_json = dump_state(state)
        battle.turn_number = profile.turn_number(state)
        # The turn committed: its recorded player choices are consumed and
        # nobody is awaited any more.
        battle.turn_inputs_json = {}
        battle.pending_input_json = {}
        battle.save()
        # The moment the engine records a wound or a death is the moment the
        # character's record takes it, in this same transaction (issues #750,
        # #751). It used to wait for the player's next chat message, and a
        # restart or a detach in between left a character dead on the board
        # alive on their sheet.
        # The pools go on the record every turn; the turn that ends the fight
        # also writes each character's one history line for it.
        ended = battle.status in (Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE)
        _write_back(
            fight_reports(
                battle,
                figures_before,
                profile.figure_vitals(state),
                turn=battle.turn_number,
                closes=character_services.FIGHT_ENDED if ended else "",
                # The history line (at the end, or at a death) carries the
                # pools the fight found each character at.
                started=(
                    profile.figure_vitals(profile.load(battle.initial_state_json))
                    if battle.writes_to_records and battle.initial_state_json
                    else None
                ),
            )
        )
        _note_withheld(battle)
    return battle, events


def _channel_reading(
    channel: ChannelRules | None, actor: CombatantState, spell: Spell
) -> tuple[int | None, str]:
    """The caster's Channel for ``spell``, and why the menu withholds it, if it does.

    The engine's own test (``tarmar_engine.policy._within_channel``): the
    spell's cost past the caster's Channel for it, or a spell the Channel
    rules give no kind of magic. Both are read from the engine's
    :meth:`~tarmar_engine.magic.ChannelRules.bound_for`, which is ``None``
    for a caster a Game Master has lifted Channel for (#37): such a cast is
    not withheld, and no Channel bounds it. Only the words are the cast
    stage's.

    Args:
        channel: The canon's Channel rules, or ``None`` when it has none.
        actor: The caster.
        spell: The spell offered.

    Returns:
        ``(channel, reason)``: the Channel bounding the cast, or ``None`` when
        no rules give the spell one or it is lifted for the caster; and the
        reason in the player's words, or ``""`` when the Channel allows the
        cast.
    """
    if channel is None:
        return None, ""
    try:
        limit = channel.bound_for(actor, spell)
    except ChannelUnclassifiedSpell:
        return None, "the rules give it no kind of magic, so no Channel for it"
    if limit is None:
        return None, ""
    if spell.level > limit:
        return limit, f"costs {spell.level} mana, past a Channel of {limit} for it"
    return limit, ""


def caster_magic(state: BattleState, combatant_id: int) -> dict | None:
    """One Tarmar caster's magical situation, in the cast stage's own shape.

    ``None`` for a combatant who knows no spells (or is not in the state at
    all) — there is no cast stage to draw. Otherwise the named pool plus a
    row per offered cast: what it costs, what it needs to land, whether it
    can be cast right now, and, when it cannot, why not in the player's
    words.

    The shape is profile-neutral (#263): the pool carries its own label and
    every row carries its cost and note already worded, because a classic
    wizard pays ST out of the pool that is also its hit points and rolls
    against a different attribute — numbers the page cannot dress in
    Tarmar's vocabulary without printing something false. The page renders
    rows; each profile decides what a row says. ``ClassicProfile`` builds
    the same shape in :func:`battle.classic_adaptation.classic_caster_magic`.

    A row is keyed by *(spell, target)* rather than by spell alone, because
    that pair is what a candidate names — classic offers the same spell at
    several targets, and keying by spell would collapse them to one.

    The castability rules restate ``policy._cast_candidates``' filters —
    the engine drops the casts it will not offer, this says out loud why
    each was dropped — and then the menu's Channel filter (issue #960),
    through :func:`_channel_reading`, on the same magic rules the menu is
    built with. ``battle.tests.test_cast_flow`` pins the two sets equal,
    against a menu built with those rules, so the explanation can never
    contradict the menu.

    A spell the caster cannot afford, and could otherwise cast, is
    ``borrowable`` when the canon's Borrowing rules allow it (tarmar-engine
    #28): within the engine's own
    :meth:`~tarmar_engine.magic.BorrowingRules.borrowing_ceiling`, the debt
    cap and the Channel. Such a row is not castable (the plain cast is not
    on the menu) and not withheld: the menu offers it as a borrowing copy,
    and the row carries the ``shortfall`` that copy borrows.
    """
    try:
        actor = state.by_id(combatant_id)
    except KeyError:
        return None
    if not actor.spells:
        return None
    # The one enemy a damage spell would be aimed at, chosen exactly as
    # ``policy._cast_candidates`` chooses it — a spell with nobody to aim at
    # is not offered, so the row has to say so rather than claim castable.
    enemy = policy.nearest_enemy(state, actor)
    rules = magic_rules()
    rows = []
    for key in actor.spells:
        spell = get_spell(key)
        aimed = spell.damage is not None and not spell.heals
        spell_channel, channel_refusal = _channel_reading(rules.channel, actor, spell)
        other_reason = ""
        if spell.continuing and key in actor.active_spells:
            other_reason = "already up — renews itself"
        elif aimed and enemy is None:
            other_reason = "nothing in reach to aim at"
        elif channel_refusal:
            other_reason = channel_refusal
        shortfall = turn_menu.mana_short(actor, spell.level)
        # A borrowing casting's bound is the engine's own
        # (:meth:`~tarmar_engine.magic.MagicRules.channel_bound`): with Channel
        # lifted for the caster, the debt cap, or with none the purse plus
        # the Channel (#37).
        borrowable = (
            bool(shortfall)
            and not other_reason
            and rules.borrowing is not None
            and spell.level
            <= rules.borrowing.borrowing_ceiling(
                actor.mana,
                actor.debt,
                rules.channel_bound(actor, spell, borrowing=True),
            )
        )
        if borrowable:
            reason = (
                f"needs {spell.level} mana, {actor.mana} left: "
                f"borrow the {shortfall} it lacks"
            )
        elif shortfall:
            reason = f"needs {spell.level} mana, {actor.mana} left"
        else:
            reason = other_reason
        castable = not reason
        attribute = actor.intelligence if spell.attribute == "INT" else actor.wisdom
        chance = policy.three_d6_at_most(attribute)
        aim = ", then a DEX roll to land it" if spell.targeted else ""
        rows.append(
            {
                "key": key,
                "target_id": enemy.combatant_id if aimed and enemy else None,
                "name": spell.name,
                "cost": f"{spell.school} — {spell.level} mana",
                "note": f"{chance:.0%} on 3d6 \u2264 {spell.attribute}{aim}",
                "castable": castable,
                "reason": reason,
                # Cast only by borrowing the shortfall (tarmar-engine #28).
                "borrowable": borrowable,
                "shortfall": shortfall if borrowable else 0,
                # The most mana one casting may take, cost and push together
                # (issue #960); None when the canon bounds no casting by it, or
                # a Game Master has lifted Channel for the caster (#37).
                "channel": spell_channel,
            }
        )
    return {
        "pool_label": "Mana",
        "pool": actor.mana,
        "max_pool": actor.max_mana,
        # Mana borrowed and not yet paid back (tarmar-engine #28).
        "owed": actor.debt,
        "spells": rows,
    }


def _stale_choice_message(
    battle: Battle,
    awaited_seat: BattleCombatant,
    awaited_turn,
    chosen_combatant_id: int,
    chosen_turn: int,
) -> str:
    """Why a submitted choice was refused for naming the wrong pause (#644).

    Written for the arena's notice region, so it names both ends of the
    disagreement: the figure and turn the menu was composed for, and the
    figure and turn the battle is actually sitting on.

    Args:
        battle: The battle the choice was submitted to.
        awaited_seat: The combatant the pause is actually held for.
        awaited_turn: The turn number recorded in ``pending_input_json``.
        chosen_combatant_id: The combatant the client says it answered for.
        chosen_turn: The turn the client says its menu was composed on.

    Returns:
        A one-sentence refusal naming both the stale and the current pause.
    """
    chosen_seat = battle.combatants.filter(pk=chosen_combatant_id).first()
    chosen_name = chosen_seat.display_name if chosen_seat else "another combatant"
    return (
        f"That choice was made for {chosen_name} on turn {chosen_turn}, but "
        f"the battle is waiting on {awaited_seat.display_name}'s turn "
        f"{awaited_turn}. The menu has moved on — choose again."
    )


def record_player_action(
    battle_id: int,
    user,
    *,
    guest_key: str = "",
    combatant_id: int,
    turn: int,
    letter: str,
    target_id: int | None = None,
    spell_key: str = "",
    push_mana: int = 0,
    borrow: bool = False,
    anchor_target: int | None = None,
    counter_mana: int | None = None,
    destination: tuple[int, int] | None = None,
    as_ai: bool = False,
) -> tuple[Battle, list[BattleEvent]]:
    """Record the awaited player's choice, then resume the paused turn.

    ``combatant_id`` and ``turn`` name the pause the choice was *composed
    for*, and both must match ``pending_input_json`` (#644). Without them a
    submission is applied to whoever happens to be awaited when it lands, so
    a second tab, a retried request or a poll that has not caught up records
    one figure's decision against another whenever the option is legal for
    both — which the generic options (DODGE, DEFEND, DISENGAGE) nearly
    always are. Binding the submission is what makes that a refusal instead
    of a silent misattribution.

    The submitted option must also be one of the pending menu's candidates
    (the deterministic replay re-derives that menu and re-checks — the
    stored copy is display-only). Only the seated player may choose for
    their combatant; a DM may instead pass ``as_ai=True`` to let the AI's
    top-scored candidate stand for an absent player (the ``letter``/
    ``target_id``/``spell_key``/``push_mana``/``borrow``/``anchor_target``/
    ``counter_mana`` arguments are ignored then, but the binding is not: an AI hand-off is a
    choice for one named figure too).

    ``push_mana`` is the extra mana a cast pushes into its spell (issue
    #960), ``borrow`` whether the cast borrows the mana its caster lacks
    (tarmar-engine #28), ``anchor_target`` the combatant whose Runaway an
    anchor takes back (tarmar-engine #30), and ``counter_mana`` the mana a
    counter commits against the caster ``target_id`` names (tarmar-engine
    #31). A player, or a Game Master choosing for a foe, may record only a
    push, a borrowing, an anchor or a counter the engine offered on the
    menu; any other is not on the menu, and refused as an option that is
    not. An escalation is the same cast of the countered spell at its
    target, which the menu marks; it needs no argument of its own.

    ``destination`` is the hex the player picked for an option that places
    the figure (#819, #866). It must be one of that option's
    ``destinations`` on the pending menu, which the engine's own rule
    produced on the same board; the engine checks it again when the move is
    made.

    Returns ``advance_turn``'s shape: the battle plus any events the resumed
    turn committed (empty when it paused again on another player).

    Raises:
        ValueError: nothing pending, a choice composed for a different
            combatant or turn, not your combatant, an option that is not
            on the pending menu, or a hex that option cannot take.
    """
    with transaction.atomic():
        battle = Battle.objects.select_for_update().get(pk=battle_id)
        if battle.status != Battle.STATUS_AWAITING:
            raise BattleRefusal("No player input is pending")
        pending = battle.pending_input_json or {}
        awaited_combatant_id = pending.get("combatant_id")
        seat = battle.combatants.filter(pk=awaited_combatant_id).first()
        if seat is None:
            raise BattleRefusal("No player input is pending")
        awaited_turn = pending.get("turn")
        if combatant_id != awaited_combatant_id or turn != awaited_turn:
            raise BattleRefusal(
                _stale_choice_message(battle, seat, awaited_turn, combatant_id, turn)
            )
        candidates = pending.get("candidates", [])
        actor = Actor.for_user_or_guest(user, guest_key)
        if as_ai:
            # Widened from DM-only (#308): whoever runs the battle may hand
            # an awaited choice to the AI, and so may the combatant's own
            # owner. A guest's battle has no DM at all, so the old rule left
            # a guest-run battle with no way past an absent player.
            if not (commands_foes(actor, battle) or decides_for(actor, battle, seat)):
                raise BattleRefusal(
                    "Only this battle's DM, its owner, or the combatant's "
                    "own player can hand the choice to the AI"
                )
            if not candidates:
                raise BattleRefusal("No pending menu to choose from")
            # The option the AI chose, marked at the pause (#775): a player's
            # menu also holds each attack aimed elsewhere, and one of those
            # may score higher than the AI's own target. A menu stored
            # before the mark existed has no such options, so its best
            # score is the AI's pick.
            marked = [
                candidate for candidate in candidates if candidate.get("ai_choice")
            ]
            top = (
                marked[0]
                if marked
                else max(candidates, key=lambda candidate: candidate.get("score", 0))
            )
            letter = top["letter"]
            target_id = top.get("target_id")
            spell_key = top.get("spell_key") or ""
            # The AI never pushes, borrows, counters or escalates (house
            # readings), but it anchors its own Runaway: read from the option.
            variant = option_variant(top)
            # The AI's own hex, if it named one. On tarmar-engine 0.9.10 the AI
            # names none (house ruling ai_destinations), so this is None and
            # the option makes its own move; kept so a hand-off carries the
            # AI's hex if a later engine names one.
            destination = top.get("destination")
        else:
            if not decides_for(actor, battle, seat):
                raise BattleRefusal("That is not your combatant")
            variant = option_variant(
                {
                    "push_mana": push_mana,
                    "borrow": borrow,
                    "anchor_target": anchor_target,
                    "counter_mana": counter_mana,
                }
            )
            recorded = {
                "letter": letter,
                "target_id": target_id,
                "spell_key": spell_key or "",
                **variant,
            }
            matched = next(
                (
                    candidate
                    for candidate in candidates
                    if names_option(candidate, recorded)
                ),
                None,
            )
            if matched is None:
                raise BattleRefusal("That option is not on the pending menu")
            if destination is not None and list(destination) not in matched.get(
                "destinations", []
            ):
                raise BattleRefusal(
                    f"{matched.get('name', 'That option')} cannot move "
                    f"{seat.display_name} to hex {tuple(destination)}: pick one "
                    "of the marked hexes, or none"
                )
        inputs = dict(battle.turn_inputs_json or {})
        inputs[str(combatant_id)] = {
            "letter": letter,
            "target_id": target_id,
            "spell_key": spell_key or "",
            **variant,
            "destination": list(destination) if destination is not None else None,
        }
        battle.turn_inputs_json = inputs
        battle.save(update_fields=["turn_inputs_json", "updated_at"])
    return advance_turn(battle_id)


def rename_combatant(
    battle_id: int, combatant_id: int, new_name: str
) -> tuple[Battle, BattleCombatant]:
    """Rename one combatant: its row and the state_json display name.

    Future engine events (the state is the engine's only name source) use
    the new name; already-written ``BattleEvent`` text stays historical.

    Raises:
        ValueError: for a combatant not in this battle, or a name another
            combatant in the battle already carries.
    """
    with transaction.atomic():
        battle = Battle.objects.select_for_update().get(pk=battle_id)
        combatant = battle.combatants.filter(pk=combatant_id).first()
        if combatant is None:
            raise BattleRefusal("No such combatant in this battle")
        name_taken = (
            battle.combatants.exclude(pk=combatant_id)
            .filter(display_name=new_name)
            .exists()
        )
        if name_taken:
            raise BattleRefusal("Another combatant already has that name")
        combatant.display_name = new_name
        combatant.save(update_fields=["display_name"])
        profile = profiles.for_battle(battle)
        state = profile.load(battle.state_json)
        profile.rename(state, combatant_id, new_name)
        battle.state_json = dump_state(state)
        battle.save()
    return battle, combatant
