"""Archetype-based combatant generation for battles.

Builds real ``Character`` rows — owned by the battle's creator, named with the
battle's name as a prefix, ``is_played=False`` — via the same stats roller and
persistence fields the character-creation views use
(``characters.dice.roll_all_character_stats`` + ``_CREATION_STAT_FIELDS``),
then applies an archetype: equipment picked from the catalog respecting
``str_req``, skill points allocated to the weapon in hand, and — for casters —
a spell loadout plus Spell Points.

Mana: ``reference/content/public-rules/magic.md`` and
``dm-rules/magic-mechanics/mana-pool.md`` (#297) say the Mana Pool is the
accumulated Spell Point pool — visible, starting at 0, not rolled at creation
— and the skills glossary defines Spell Points as "magic fuel toward your
Mana Pool ... tracked separately from skill points" — the model's
``free_spell_points`` field. Generation therefore grants casters Spell Points
and the engine reads ``free_spell_points`` as the Mana Pool. (The design doc's
"mana pool = mental pool" assumption is overruled by the rules doc.)
"""

import random
from dataclasses import dataclass

from characters.constants import UNNAMED_CHARACTER, name_within_rule
from characters.dice import roll_all_character_stats
from characters.equipment import (
    ENCUMBRANCE_ORDER,
    calculate_encumbrance_level,
    get_item,
)
from characters.equipment_rules import equipment_catalog
from characters.models import Character, board_can_seat_weapon
from characters.skills import points_for_level
from characters.weapon_skill import weapon_skill_name
from common.rolling import Roller

from .models import SEED_RANGE
from .spells import get_spell

# The archetype cycle. A battle of N draws these round-robin, with per-index
# variation inside each archetype (weapon pick and skill budget rotate), so
# every mix appears and two same-archetype combatants still differ.
ARCHETYPES: tuple[str, ...] = (
    "armoured fighter",
    "unarmoured duelist",
    "brawler",
    "archer",
    "wizard",
    "fighter-mage",
)

# Weapon candidates per archetype, strongest first; the first one whose
# str_req the rolled character meets is taken (dagger has none, so the
# duelist's list always terminates).
#
# The brawler carries a dagger, the only weapon besides bare hands the
# hand-to-hand table allows ("Bare hands or dagger",
# hand-to-hand-and-grappling.md), so it can fight an armed enemy: bare hands
# alone have no plain attack and enter hand-to-hand only by an entry
# condition (tarmar-engine 0.9.5, #815/#823). Coordinator's ruling under the
# standing rule on #867; Spencer may overrule.
_WEAPON_CANDIDATES: dict[str, tuple[str, ...]] = {
    "armoured fighter": ("bastard_sword_1h", "broadsword", "war_ax", "mace"),
    "unarmoured duelist": ("saber", "rapier", "dagger"),
    "brawler": ("dagger",),
    "archer": ("longbow", "horse_bow", "small_bow"),
    "wizard": ("dagger",),
    "fighter-mage": ("shortsword", "saber", "dagger"),
}

_ARMOUR: dict[str, tuple[str, ...]] = {
    "armoured fighter": ("chainmail", "large_shield"),
    "unarmoured duelist": ("main_gauche",),
    "brawler": (),
    "archer": ("leather_armor",),
    "wizard": (),
    "fighter-mage": ("cloth_armor",),
}

# What an archetype's armour and shield step down to when its full kit is
# more than its STR can bear (issue #883), heaviest first, the last always the
# lightest; ``None`` is going without. A piece in neither table (the
# duelist's main-gauche) is kept. The order they shed in is ``_kit_steps``'.
_ARMOUR_STEPS: dict[str, tuple[str | None, ...]] = {
    "chainmail": ("chainmail", "leather_armor", "cloth_armor", None),
    "leather_armor": ("leather_armor", "cloth_armor", None),
    "cloth_armor": ("cloth_armor", None),
}
_SHIELD_STEPS: dict[str, tuple[str | None, ...]] = {
    "large_shield": ("large_shield", "small_shield", None),
}

#: The heaviest load a generated fighter carries (issue #883): Medium, the
#: last level at which a figure may still Jog (movement.md; equipment.py's
#: ``ENCUMBRANCE_LEVELS``). Heavy bars Jog and Overloaded caps Walk at 1 hex,
#: so a figure dealt past Medium can neither charge nor close with a foe that
#: steps away. Coordinator's ruling under the standing rule; Spencer may
#: overrule.
HEAVIEST_GENERATED_LOAD = "medium"

# Weapon-skill levels rotate through these per same-archetype index, so the
# third archer is not a clone of the first.
_SKILL_LEVEL_CYCLE: tuple[int, ...] = (4, 3, 5)

# Spell loadouts rotate per caster index. Keys are battle.spells catalog keys.
_WIZARD_LOADOUTS: tuple[tuple[str, ...], ...] = (
    ("fire_missile", "lightning_bolt", "shield"),
    ("fire_ball", "fatigue", "blur"),
    ("fire_missile", "wound", "heal"),
)
_FIGHTER_MAGE_LOADOUTS: tuple[tuple[str, ...], ...] = (
    ("fire_missile", "shield"),
    ("fatigue", "heal"),
)

# Spell Points (= Mana Pool, see module docstring) rotate too.
_MANA_CYCLE: tuple[int, ...] = (10, 12, 8)
_FIGHTER_MAGE_MANA_CYCLE: tuple[int, ...] = (6, 8)

# Name-part tables for generated combatants (issue #187). A given name is a
# stem plus an ending; the epithet doubles as a surname. All parts satisfy
# ``characters.constants.CHARACTER_NAME_PATTERN``.
_GIVEN_STEMS: tuple[str, ...] = (
    "Al", "Bran", "Cael", "Dor", "Eln", "Fen", "Gal", "Har", "Il",
    "Jor", "Kest", "Lir", "Mor", "Ned", "Osk", "Pell", "Quil", "Rav",
    "Sel", "Tam", "Ulf", "Vess", "Wyn", "Yar", "Zeph",
)  # fmt: skip
_GIVEN_ENDINGS: tuple[str, ...] = (
    "a", "an", "ar", "eth", "ia", "ic", "in", "is", "on", "or", "ra", "us",
)  # fmt: skip
_EPITHETS: tuple[str, ...] = (
    "Ashvale", "Blackbriar", "Coldwater", "Duskmere", "Emberfall",
    "Fernhollow", "Greymarsh", "Hawkridge", "Ironwood", "Kettleburn",
    "Longmarch", "Mossbank", "Nightfield", "Oakhart", "Palegrove",
    "Quickwater", "Ravenshaw", "Stonemead", "Thornbury", "Underhill",
    "Veilwood", "Wolfden", "Yewbarrow", "Redfen",
)  # fmt: skip

# Redraw budget before generate_name falls back to a numbered variant.
_NAME_ATTEMPTS = 50


def generate_name(rng: random.Random, used_names: set[str]) -> str:
    """One fantasy name ("Kestra Duskmere"), unique against ``used_names``.

    Draws come from ``rng`` only, so a seeded generator reproduces the same
    sequence of names. The drawn name is added to ``used_names``. When the
    tables cannot yield a fresh combination inside the attempt budget, the
    last draw gets a numbered suffix — uniqueness never fails.
    """
    candidate = ""
    for _attempt in range(_NAME_ATTEMPTS):
        given = rng.choice(_GIVEN_STEMS) + rng.choice(_GIVEN_ENDINGS)
        candidate = f"{given} {rng.choice(_EPITHETS)}"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
    suffix = 2
    while f"{candidate} ({suffix})" in used_names:
        suffix += 1
    candidate = f"{candidate} ({suffix})"
    used_names.add(candidate)
    return candidate


def stat_roller_for_slot(seed: int | None, index: int) -> Roller | None:
    """The stat roller for roster slot ``index``, derived from a battle seed.

    Seeding each generated character separately, rather than running the whole
    roster off one shared roller, is deliberate: slot ``k``'s rolls must not
    depend on how many slots come after it, so growing a roster leaves the
    fighters already in it exactly as they were.

    The per-slot seeds are drawn in slot order from one ``random.Random(seed)``
    rather than computed as ``seed + index`` — adding the index would hand
    battles with adjacent seeds the same characters in shifted slots.

    Args:
        seed: The battle's seed, or None for an unseeded caller (the lobby's
            ad-hoc generation path).
        index: The roster slot the character fills, counting from zero.

    Returns:
        A seeded :class:`~common.rolling.Roller`, or None when ``seed`` is
        None — which leaves the caller on the unseeded module default, i.e.
        today's behaviour.
    """
    if seed is None:
        return None
    slot_seeds = random.Random(seed)
    for _preceding_slot in range(index):
        slot_seeds.randrange(SEED_RANGE)
    return Roller(seed=slot_seeds.randrange(SEED_RANGE))


def pick_weapon(archetype: str, effective_strength: int) -> str | None:
    """First candidate weapon the character is strong enough for, or None.

    ``str_req`` is respected: a weapon needing more STR than the character
    has is skipped (the rules allow under-strength swings, but generation
    should not deal a character a weapon it fights badly with). The last
    candidate is taken even under-strength if nothing else fits.
    """
    candidates = _WEAPON_CANDIDATES[archetype]
    if not candidates:
        return None
    for weapon_id in candidates:
        weapon = get_item(weapon_id)
        str_req = (weapon or {}).get("str_req")
        if not str_req or effective_strength >= str_req:
            return weapon_id
    return candidates[-1]


def archetype_for_index(index: int, archetypes: tuple[str, ...] = ARCHETYPES) -> str:
    """Round-robin archetype for the Nth combatant."""
    return archetypes[index % len(archetypes)]


def spell_loadout(archetype: str, variant: int) -> tuple[tuple[str, ...], int]:
    """(spell keys, spell points) for a caster archetype, varied by index."""
    if archetype == "wizard":
        loadout = _WIZARD_LOADOUTS[variant % len(_WIZARD_LOADOUTS)]
        mana = _MANA_CYCLE[variant % len(_MANA_CYCLE)]
        return loadout, mana
    if archetype == "fighter-mage":
        loadout = _FIGHTER_MAGE_LOADOUTS[variant % len(_FIGHTER_MAGE_LOADOUTS)]
        mana = _FIGHTER_MAGE_MANA_CYCLE[variant % len(_FIGHTER_MAGE_MANA_CYCLE)]
        return loadout, mana
    return (), 0


def _equipment_entries(
    weapon_id: str | None, protection: tuple[str, ...]
) -> list[dict]:
    """The equipment list for a weapon and the armour and shield worn with it."""
    entries: list[dict] = []
    if weapon_id:
        entries.append({"item_id": weapon_id, "quantity": 1})
        weapon = get_item(weapon_id) or {}
        if weapon.get("weapon_class") == "Missile — Bows" and weapon.get("two_handed"):
            entries.append({"item_id": "arrows_20", "quantity": 1})
    for item_id in protection:
        item = get_item(item_id) or {}
        entry: dict = {"item_id": item_id, "quantity": 1}
        if item.get("category") == "armor":
            entry["equipped"] = True
        entries.append(entry)
    return entries


def _catalog_weight(entries: list[dict]) -> float:
    """What a generated equipment list weighs; every entry is a catalog item."""
    return sum(
        float((get_item(entry["item_id"]) or {}).get("weight") or 0)
        * int(entry.get("quantity", 1))
        for entry in entries
    )


def _lighter_weapons(weapon_id: str, effective_strength: int) -> list[str]:
    """Lighter catalog weapons that fight as ``weapon_id`` does, heaviest first.

    Same weapons.md class and the same hands (a one-handed weapon stays
    one-handed), strictly lighter, one the STR meets and the board can seat.
    A missile weapon is not stepped down: an archer's lighter "bows" in the
    catalog are a sling, a boomerang and a rock, and an archer without a bow
    is no longer an archer.
    """
    held = get_item(weapon_id) or {}
    weapon_class = held.get("weapon_class")
    if not weapon_class or str(weapon_class).startswith("Missile"):
        return []
    weight = float(held.get("weight") or 0)
    lighter = [
        (float(weapon.weight), item_id)
        for item_id, weapon in equipment_catalog().weapons.items()
        if weapon.weapon_class == weapon_class
        and weapon.two_handed == bool(held.get("two_handed"))
        and weapon.weight < weight
        and (weapon.str_req or 0) <= effective_strength
        and board_can_seat_weapon({"item_id": item_id})
    ]
    return [
        item_id
        for _weight, item_id in sorted(lighter, key=lambda row: (-row[0], row[1]))
    ]


def _kit_steps(
    archetype: str, weapon_id: str | None, effective_strength: int
) -> list[tuple[str | None, tuple[str, ...]]]:
    """The archetype's full kit, then each lighter one, in the order they shed.

    Coordinator's ruling under the standing rule on #883 (review), Spencer
    may overrule: the **shield** steps down first (large, small, none), then
    the **weapon** to a lighter one of the same class (:func:`_lighter_weapons`),
    and the **armour** last (chainmail, leather, cloth, none), so the
    archetype stays recognisable as long as its STR allows. Each armour is
    tried with the full shield and weapon again before it is given up.

    Returns:
        ``(weapon, protection)`` pairs, heaviest first.
    """
    pieces = _ARMOUR[archetype]
    armour = next((item for item in pieces if item in _ARMOUR_STEPS), None)
    shield = next((item for item in pieces if item in _SHIELD_STEPS), None)
    fixed = tuple(item for item in pieces if item not in (armour, shield))
    armours: tuple[str | None, ...] = _ARMOUR_STEPS[armour] if armour else (None,)
    shields: tuple[str | None, ...] = _SHIELD_STEPS[shield] if shield else (None,)
    weapons: list[str | None] = [weapon_id]
    if weapon_id:
        weapons += _lighter_weapons(weapon_id, effective_strength)
    steps: list[tuple[str | None, tuple[str, ...]]] = []
    for worn in armours:
        carried = [(weapons[0], carried_shield) for carried_shield in shields]
        carried += [(lighter, shields[-1]) for lighter in weapons[1:]]
        for weapon, carried_shield in carried:
            protection = tuple(
                item for item in (worn, carried_shield, *fixed) if item is not None
            )
            steps.append((weapon, protection))
    return steps


def deal_equipment(
    archetype: str,
    weapon_id: str | None,
    effective_strength: int,
    *,
    within_load: bool = True,
) -> list[dict]:
    """The archetype's equipment, lightened until its STR can bear it (#883).

    The first kit from :func:`_kit_steps` whose load is no heavier than
    :data:`HEAVIEST_GENERATED_LOAD`, read by the sheet's own
    :func:`~characters.equipment.calculate_encumbrance_level`; the lightest
    kit when none is. The weapon in the kit comes first in the list, as it
    always has, and may be lighter than the one :func:`pick_weapon` dealt.

    Args:
        archetype: One of :data:`ARCHETYPES`.
        weapon_id: The weapon :func:`pick_weapon` dealt, or None.
        effective_strength: The STR the load is priced against.
        within_load: False deals the full kit whatever it weighs: the classic
            profile has no encumbrance (a Melee figure's move comes from its
            armour alone), so its figures keep the kit the archetype names.
    """
    steps = _kit_steps(archetype, weapon_id, effective_strength)
    if not within_load:
        return _equipment_entries(*steps[0])
    for weapon, protection in steps:
        entries = _equipment_entries(weapon, protection)
        level = calculate_encumbrance_level(
            _catalog_weight(entries), effective_strength
        )
        if ENCUMBRANCE_ORDER.index(level) <= ENCUMBRANCE_ORDER.index(
            HEAVIEST_GENERATED_LOAD
        ):
            return entries
    return _equipment_entries(*steps[-1])


@dataclass(frozen=True)
class GeneratedCombatant:
    """One generated combatant: its Character plus its battle loadout.

    The ``Character`` is a persisted row for a signed-in creator and an
    unsaved instance (``pk`` is ``None``) for a guest's — see
    :func:`generate_character`.
    """

    character: Character
    archetype: str
    spells: tuple[str, ...]
    mana: int
    # The battle-facing name ("Kestra Duskmere") — what the roster, arena,
    # and event log show. The Character row's name keeps the battle-name
    # prefix for provenance.
    display_name: str


def generate_character(
    user,
    battle_name: str,
    index: int,
    archetypes: tuple[str, ...] = ARCHETYPES,
    *,
    rng: random.Random | None = None,
    used_names: set[str] | None = None,
    stats_roller: Roller | None = None,
    within_load: bool = True,
) -> GeneratedCombatant:
    """Roll and persist one archetype combatant.

    The character is a real rolled character: ``roll_all_character_stats``
    provides every creation field, persisted with the creation views'
    ``_CREATION_STAT_FIELDS`` so the two paths cannot drift. The display
    name is a generated fantasy name drawn from ``rng`` (the battle's seeded
    roster RNG, so identical seeds name identical rosters), unique against
    ``used_names``; the persisted row is prefixed with the battle name and
    ``is_played`` stays False so it reads as generated, not hand-played.
    ``archetypes`` narrows the round-robin cycle when the battle asked for a
    particular mix.

    ``stats_roller`` seeds the character's own dice (issue #261). Given one,
    every rolled attribute — and therefore the weapon ``pick_weapon`` deals
    from effective strength — is reproducible; omitted, the rolls come from
    the unseeded module default as they always have.

    ``user`` may be ``None`` (issue #305): a guest's fighter is rolled
    exactly the same way and then *not saved*. A guest cannot save
    characters, so there is no owner to give the row to — and persisting it
    anyway would make every anonymous battle mint up to a dozen ownerless
    ``Character`` rows, turning an unauthenticated write into a much
    larger one. The returned ``Character`` is a live, fully-populated
    unsaved instance, which is all the snapshot needs: ``services.
    snapshot_character`` reads attributes and computed properties, never a
    primary key.

    ``within_load`` lightens the archetype's armour and shield until the
    load is one its STR bears (:func:`deal_equipment`, issue #883); the
    classic profile, which has no encumbrance, passes False.
    """
    # The creation views' single source of truth for which stats persist.
    from characters.views import _CREATION_STAT_FIELDS

    archetype = archetype_for_index(index, archetypes)
    variant = index // len(archetypes)
    stats = roll_all_character_stats(stats_roller)

    display_name = generate_name(
        rng if rng is not None else random.Random(),
        used_names if used_names is not None else set(),
    )
    # Names need not be unique (issue #571). The battle's name is free text,
    # so the row's name keeps only what the name rule accepts, capped at 100,
    # or the character's export could not be imported (issue #870).
    name = name_within_rule(f"{battle_name} {display_name}", fallback=UNNAMED_CHARACTER)
    character = Character(
        user=user,
        name=name,
        is_played=False,
        **{field: stats[field] for field in _CREATION_STAT_FIELDS},
    )
    if user is not None:
        character.save()

    # Generation always writes all six attributes, so STR is recorded; an
    # unrecorded one would be None since issue #630, not a silent 0.
    effective_strength = character.effective_strength
    if effective_strength is None:  # pragma: no cover - generation sets STR
        raise ValueError(f"{name} was generated without a strength")
    weapon_id = pick_weapon(archetype, effective_strength)
    character.equipment = deal_equipment(
        archetype, weapon_id, effective_strength, within_load=within_load
    )
    if weapon_id:
        # The weapon the load allowed, which may be lighter than the one
        # dealt (#883): the skill below is trained with the one carried.
        weapon_id = character.equipment[0]["item_id"]

    skill_level = _SKILL_LEVEL_CYCLE[variant % len(_SKILL_LEVEL_CYCLE)]
    skills = dict(character.skills or {})
    if weapon_id:
        # Recorded the way a player's Weapon skill is, tied to the weapon in
        # the skill's name, so the resolver reads both alike (issue #753).
        # The rules have no bare-handed skill, so a figure with no weapon
        # trains none (#867).
        skills[weapon_skill_name(weapon_id)] = points_for_level(skill_level)
    character.skills = skills

    spells, mana = spell_loadout(archetype, variant)
    if mana:
        character.free_spell_points = mana
    if spells:
        # Written to the record too (issue #774), so a generated caster the
        # player keeps knows its spells on the sheet and the next time it is
        # seated. Each at a mastery equal to the spell's own level, so a
        # Channel that derives from the spell's level allows its cost
        # (issue #960). Coordinator's ruling, marked for Spencer: the
        # generator makes stronger figures, and a saved player character is
        # held to the canon as written.
        character.spells = {
            key: points_for_level(get_spell(key).level) for key in spells
        }
    if user is not None:
        character.save()
    return GeneratedCombatant(
        character=character,
        archetype=archetype,
        spells=spells,
        mana=mana,
        display_name=display_name,
    )
