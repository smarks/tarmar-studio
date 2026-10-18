"""Framework-free GM engine pieces shared by the web app and the offline CLI.

This module must not import Django (the CLI imports it standalone), so it holds
only the pure, single-source-of-truth bits: the system prompt, the dice roller,
and the rules-file loader.

Every prompt sentence that states a number a canon rules file holds is built
from the loaded rules (issue #68). Left as code, for stages 6 and 7 or because
no rules file holds them yet:

- the engine's own numbers: initiative 1d6, crits and fumbles, the matrix, the
  survival save 3d6 ≤ CON, the injury bands, the casting dice 3d6 ≤ INT or
  WIS, the megahex of 7, hand-to-hand's 4d6 ≤ DEX, the defender dodge's
  ``max(0, floor((DEX−10)/2))`` in the Target Number formula (the engine's
  ``dodge_modifier``, which ``characters.combat`` re-exports), and the saves'
  and spells' 3d6 beside the attribute check in the combat section;
- Movement Speeds' base distances (18, 12, 7, 4), which the canon's
  ``gaits.base_distances`` holds but whose per-gait prose it does not;
- what the tracks yield (:data:`TRACK_YIELDS_SENTENCE`), prose about the
  default and Tarmar canons' tracks that no rules file carries;
- the currency line, the Appearance roll, and the d20 track table's 20
  entries (``characters.skill_rules.TRACK_TABLE_DIE_FACES``);
- the plan format's illustrative values (a provenance, Location and Wealth, a
  difficulty, a skill, an item), and the quoted examples in the outcomes and
  authority sections.
"""

import functools
import logging
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from battle.engine.state import BARE_HANDED_DAMAGE_TABLE
from characters.combat import (
    ARMOUR_TIERS,
    CRIT_DAMAGE_ROLLS,
    FUMBLE_TABLE,
    MATRIX,
    SEVERE_CRIT_DAMAGE_ROLLS,
)
from characters.constants import CHARACTER_ATTRIBUTE_FIELDS
from characters.dice import format_height, roll_expression, roll_expression_record
from characters.equipment import combine_movement_restrictions, list_gaits
from characters.equipment_rules import (
    EncumbranceLevel,
    GaitRules,
    Weapon,
    equipment_catalog,
    equipment_rules,
)
from characters.rules import character_rules
from characters.skill_rules import skill_rules
from characters.skills import (
    calculate_attribute_modifier,
    normalize_skill_name,
    selectable_tracks,
    track_keys,
)
from characters.weapon_skill import WEAPON_PARRY_SKILL, is_weapon_skill
from common.rolling import RollRecord
from reference.paths import (
    CANON_NAME,
    DEFAULT_MAGIC_PROMPT_FILE,
    MAGIC_PROMPT_FILE,
    PUBLIC_RULES_DIR,
    TARMAR_CANON_NAME,
    WORLD_PROMPT_FILE,
)
from reference.rules_file import derived_from_rules

from . import rules_lookup

logger = logging.getLogger(__name__)

# Canonical rules tree — the single source of truth for both the web GM
# (default of settings.RULES_DIR) and the offline CLI (cli/gm.py). The path
# itself is derived once, in reference.paths.
DEFAULT_RULES_DIR = PUBLIC_RULES_DIR


def _render_matrix_fragment(
    matrix: dict[str, dict[str, int]], tiers: tuple[str, ...]
) -> str:
    """Render the §6 base-TN matrix as the one-line summary in the GM prompt.

    Built directly from :data:`characters.combat.MATRIX` (issue #104) so the
    prompt cannot silently drift from the code — see gm/tests.py's
    ``test_system_prompt_combat_matrix_matches_code`` for the drift guard.
    """
    tier_label = "/".join(tiers)
    rows = "; ".join(
        f"{weapon_class} " + "/".join(str(row[tier]) for tier in tiers)
        for weapon_class, row in matrix.items()
    )
    return f"Matrix — base TN by weapon class vs armour tier [{tier_label}]: {rows}.\n"


def _render_crit_fragment(crit_rolls: int, severe_rolls: int) -> str:
    """Render the §7 natural-20 rule as the prompt's crit sentence (issue #105).

    Built from :data:`characters.combat.CRIT_DAMAGE_ROLLS` and
    :data:`characters.combat.SEVERE_CRIT_DAMAGE_ROLLS` so the prompt cannot
    promise a multiplier the code does not apply. It used to say only "roll
    again to confirm a severe crit: triple damage", never naming the doubling
    on the unconfirmed crit and never tying either number to the code.
    Drift-guarded by gm/tests.py's
    ``test_system_prompt_crit_multipliers_match_code``.
    """
    return (
        f"Natural 20 = auto-hit + critical: roll the weapon's damage dice "
        f"{crit_rolls}× and sum. Then confirm — a second d20 at the same TN "
        f"with the same bonus (natural 1 just fails to confirm, natural 20 "
        f"auto-confirms); if it also hits, the blow is severe: damage dice "
        f"{severe_rolls}× instead, plus bleeding, reaching Body as well as "
        f"Fatigue. "
    )


def _render_fumble_fragment(table: tuple[tuple[int, str, str, str], ...]) -> str:
    """Render the §7 1d6 fumble bands as the prompt's parenthetical (issue #105).

    Built from :data:`characters.combat.FUMBLE_TABLE` for the same reason the
    matrix is built from ``MATRIX``: the prompt used to abbreviate the subtable
    to "off-balance, or drop/break weapon", which is not a table the model can
    roll on. Each band carries its effect too, so the GM has something to
    narrate. Drift-guarded by gm/tests.py's ``test_system_prompt_fumble_ranges_match_code``.
    """
    bands = []
    low = 1
    for threshold, _key, label, effect in table:
        span = str(low) if low == threshold else f"{low}–{threshold}"
        detail = f" ({effect.lower()})" if effect else ""
        bands.append(f"{span} {label.lower()}{detail}")
        low = threshold + 1
    return "; ".join(bands)


def _render_modifier_fragment() -> str:
    """The attribute modifier table, from the function the code uses (#807).

    Built from :func:`characters.skills.calculate_attribute_modifier` over the
    rows the canon's table prints (3-18 on attributes.md; issue #70),
    labelled as the page labels them, and what it does past either end said
    from the same function: flat at the end rows, or the slope continuing.
    So the prompt says what the sheet computes, whichever way that is ruled.
    """
    table = skill_rules().attribute_modifier
    lowest, highest = table.lowest_score, table.highest_score
    bands: list[str] = []
    low = lowest
    while low <= highest:
        modifier = calculate_attribute_modifier(low)
        high = low
        while high < highest and calculate_attribute_modifier(high + 1) == modifier:
            high += 1
        label = str(low) if low == high else f"{low}-{high}"
        bands.append(f"{label}={rules_lookup.signed(modifier)}")
        low = high + 1
    fragment = "Modifiers: " + ", ".join(bands) + "."
    below = calculate_attribute_modifier(lowest - 1)
    above = calculate_attribute_modifier(highest + 1)
    if (below, above) == (
        calculate_attribute_modifier(lowest),
        calculate_attribute_modifier(highest),
    ):
        fragment += (
            f" Below {lowest} the modifier stays {rules_lookup.signed(below)} "
            f"and above {highest} it stays {rules_lookup.signed(above)}."
        )
    else:
        fragment += (
            f" Past {highest} and below {lowest} the modifier keeps changing by "
            "1 a point."
        )
    return fragment


def _render_bare_handed_fragment() -> str:
    """Bare-handed damage by STR, from the table the battle uses (#807).

    :data:`battle.engine.state.BARE_HANDED_DAMAGE_TABLE` is held to
    special-combat-situations.md by the battle's drift test; the prompt used
    to stop at 21-24 and drop the three rows above it.
    """
    bands: list[str] = []
    low = None
    for top, damage in BARE_HANDED_DAMAGE_TABLE:
        label = f"{top} or less" if low is None else f"{low}-{top}"
        bands.append(f"{label} {damage.replace('-', '−')}")
        low = top + 1
    return "Bare-handed damage by STR: " + "; ".join(bands) + "."


#: The dice the survival save and the casting roll are made on: the engine's
#: own numbers, not yet a canon's (stages 6 and 7 of rules-as-data).
_SAVES_AND_SPELLS_DICE = "3d6"


def _render_roll_under_fragment() -> str:
    """The roll-under checks beside the attack roll, in the combat section (#68).

    The attribute check's dice are the canon's ``attribute_check``
    (``skill-rules.json``); the saves' and spells' are the engine's. When the
    two agree they are said once: "attribute checks, saves, and spells still
    roll 3d6 ≤ attribute."
    """
    dice = skill_rules().attribute_check.dice
    if dice == _SAVES_AND_SPELLS_DICE:
        return f"attribute checks, saves, and spells still roll {dice} ≤ attribute."
    return (
        f"attribute checks roll {dice} ≤ attribute; saves and spells "
        f"{_SAVES_AND_SPELLS_DICE} ≤ attribute."
    )


def _render_checks_fragment() -> str:
    """The attribute and skill checks' dice and naturals, from the canon (#68).

    The canon's ``attribute_check`` and ``skill_check`` (``skill-rules.json``),
    which :mod:`characters.skill_checks` rolls.
    """
    attribute_check = skill_rules().attribute_check
    skill_check = skill_rules().skill_check
    return (
        f"An attribute check with no skill rolls {attribute_check.dice} ≤ the "
        "attribute (or adjusted attribute). A skill check rolls "
        f"{skill_check.dice} + the governing attribute's modifier + the skill "
        f"level against a difficulty the GM sets; a natural "
        f"{skill_check.natural_success} always succeeds and a natural "
        f"{skill_check.natural_failure} always fails (the skills glossary is "
        "the source)."
    )


def _render_attribute_dice_fragment() -> str:
    """How the core attributes are made: the dice, or the point buy (#68).

    The canon's ``attribute_roll`` and ``attributes.point_buy``
    (``character-rules.json``).
    """
    rules = character_rules()
    point_buy = rules.attributes.point_buy
    return (
        f"each {rules.attribute_roll.dice}, or divide {point_buy.total} points "
        "between them with every attribute ending between "
        f"{point_buy.minimum} and {point_buy.maximum}."
    )


def _render_height_and_weight_fragment() -> str:
    """The height and weight bands, from the canon's tables (#68).

    The lowest and highest face of ``height.base_inches_by_face`` and of
    ``weight.stones_by_face``, the STR divisor and the pounds in a stone
    (``character-rules.json``).
    """
    rules = character_rules()
    heights = rules.height.base_inches_by_face
    weight = rules.weight
    return (
        f"Height (1d6: {format_height(min(heights))} to "
        f"{format_height(max(heights))}), Weight "
        f"({min(weight.stones_by_face)}-{max(weight.stones_by_face)} stones base "
        f"+ STR/{weight.strength_divisor} stones; a stone is "
        f"{weight.pounds_per_stone} lbs)."
    )


# Canonical Tarmar GM system prompt — the single source of truth for both the
# web GM (gm/services.py) and the offline CLI (cli/gm.py).
#
# Until issue #324 the full prompt lived in cli/Modelfile, baked into a custom
# ``tarmar-gm`` Ollama model, while this module carried only a quick
# reference. Ollama's ``/api/chat`` prepends a Modelfile SYSTEM only when the
# request's first message is not itself a ``system`` message (server/routes.go,
# ``ChatHandler``), and both clients always send one — so neither ever saw
# the Modelfile prompt. The sections below carry its substance instead. The
# world section is a digest of ``reference/content/`` (the canon); where the
# old Modelfile and the reference pages disagreed, the reference pages won.

SECTION_ROLE = (
    "You are an experienced Game Master for Tarmar, a sword-and-sorcery "
    "tabletop RPG. Tarmar evolved from Steve Jackson's The Fantasy Trip "
    "(TFT), designed by Spencer Marks with Stan Hickman.\n\n"
    "## Your Role\n"
    "You are a fair, creative, and experienced GM. You:\n"
    "- Never railroad players — present situations, not solutions\n"
    "- Give vivid but concise descriptions (2-3 paragraphs max for scenes)\n"
    "- Offer meaningful choices with real consequences\n"
    "- Track game state meticulously (Fatigue, Body, Mana, inventory, "
    "position, location, active effects)\n"
    "- Flag any rules inconsistencies you notice (this is also a playtesting "
    "tool)\n"
    "- Keep hidden mechanics hidden — don't reveal damage formulas, GM-only "
    "tables, or the GM-only world background marked below\n"
    "- Answer out-of-character rules questions plainly and briefly, then "
    "return to the scene"
)

SECTION_PHILOSOPHY = (
    "## Design Philosophy\n"
    "- Storytelling-first: narrative, puzzle-solving, and player agency "
    "matter most\n"
    "- Freeform character development: no classes, no required skill trees. "
    "Anyone can attempt anything; skill helps but doesn't guarantee success\n"
    "- No endgame: a beggar surviving on the streets is as valid as a "
    "dragon-slaying hero. Success is defined by the players\n"
    "- Lightweight mechanics: favor generalizations over special cases\n"
    "- Hidden mechanics: not all rules are transparent to players; some can "
    "be discovered through experimentation"
)

SECTION_NOT_DND = (
    "## CRITICAL: Tarmar Is NOT D&D\n"
    "Tarmar has its own unique mechanics. NEVER use D&D concepts:\n"
    "- NO races with mechanical traits (no Tiefling, Elf, Dwarf racial "
    "abilities). Peoples exist in the world; they carry no stat blocks\n"
    "- NO classes (no Rogue, Fighter, Wizard). Characters are defined by "
    "attributes and learned skills\n"
    "- NO proficiency bonus, advantage/disadvantage, or D&D saving throws "
    "and ability checks: Tarmar's checks are its own, below\n"
    "- NO hit points — Tarmar uses Fatigue and Body pools\n"
    "- NO spell slots — Tarmar uses a Mana Pool\n"
    "- NO AC (armor class) — armor sets the attacker's Target Number and "
    "stops fixed damage amounts\n"
    "- NO skill proficiencies — anyone can attempt anything, skill level "
    "adjusts difficulty\n"
    "- NO cantrips, no D&D spell levels — Tarmar spells cost mana equal to "
    "spell level and are cast with 3d6 ≤ INT or WIS\n"
    "Use ONLY the Tarmar mechanics described below."
)


@derived_from_rules
@functools.cache
def section_core() -> str:
    """The core resolution section, built the first time a prompt needs it.

    Built on first use rather than at import because its checks, modifier
    table, attribute dice, height and weight bands and Fatigue sum are the
    loaded canon's ``skill-rules.json`` (issue #70) and
    ``character-rules.json`` (issue #68), which no module may read while it
    imports. Cached, so every prompt holds the same string for the life of
    the process.
    """
    fatigue_roll = character_rules().fatigue_roll
    return (
        "## Core Resolution Mechanic\n"
        f"{_render_checks_fragment()} Only combat attacks use a d20 (see "
        "Combat).\n\n"
        "### Attributes\n"
        "Six core attributes: STR, DEX, INT, WIS, CON, CHR — "
        f"{_render_attribute_dice_fragment()}\n"
        f"{_render_modifier_fragment()}\n"
        "Play uses aged scores everywhere; only the literacy check and the "
        "weight from STR use the base score.\n"
        "STR governs weapon requirements and physical damage; DEX governs "
        "to-hit, action order, and avoiding falls; INT governs perception, "
        "learning, and most casting rolls; WIS governs insight, judgment, "
        "persuasion, some casting rolls, and Anchor rolls during Runaway; CHR "
        "governs influence, morale, and leadership; CON governs endurance, the "
        "pools, and survival saves.\n"
        "Secondary: Appearance (1d6: 1=pretty, 6=ugly), "
        f"{_render_height_and_weight_fragment()}\n\n"
        "### Derived Pools\n"
        f"- Fatigue = {fatigue_roll.formula(fatigue_roll.dice)}. Normal hits "
        "reduce Fatigue only. At 0 = unconscious.\n"
        "- Body = 2/3 × Fatigue (round up). Severe critical hits (a confirmed "
        "natural 20) reduce Fatigue AND Body. At 0 = unconscious.\n"
        "- Mana Pool = the Spell Points earned through the Magic skill track. It "
        "starts at 0, grows only as the character trains, and is NOT rolled at "
        "creation and NOT secret: the player always knows their exact Pool. Mana "
        "is separate from every attribute. Skill points never convert into mana; "
        "a Game Master may grant mana. Mana a fight spends stays spent until a "
        "Game Master restores it.\n"
        "- Physical = ceil((STR + DEX + CON) / 2) and Mental = ceil((WIS + INT + "
        "CHR) / 2) are sheet read-outs, not pools.\n\n"
        "### Injury Thresholds (apply to Fatigue and Body, whichever is worse)\n"
        "- Down to 1/2 (but above 5): −1 to all rolls\n"
        "- Between 1 and 5: −2 to all rolls\n"
        "- 0: unconscious\n"
        "- Negative 1/2 of starting value: survival save each turn (3d6 ≤ CON)\n"
        "- Negative full starting value: saves continue, penalised by how far "
        "past that threshold the pool has gone (at −35 on a starting 30, roll "
        "3d6 ≤ CON − 5)\n"
        "Saves continue each turn until the character is healed above the "
        "threshold or fails one and dies."
    )


def _render_provenance_fragment() -> str:
    """The provenance bands: "Nobility 1-10%, Merchant 11-30%, ..." (#68).

    The canon's ``social_class`` table (``character-rules.json``), each row's
    label and the d100 rolls that land on it.
    """
    bands: list[str] = []
    low = 1
    for row in character_rules().social_class.rows:
        span = str(low) if low == row.up_to else f"{low}-{row.up_to}"
        bands.append(f"{row.label.title()} {span}%")
        low = row.up_to + 1
    return ", ".join(bands)


def _render_weapon_lead_fragment() -> str:
    """ "Weapon skills and Weapon Parry lead most tracks; ", when they do (#68).

    True when more than half the canon's tracks open their d20 table with a
    Weapon skill or Weapon Parry; otherwise the clause is left out and the
    yields sentence after it stands alone.
    """
    tracks = skill_rules().tracks.tracks.values()
    led = sum(
        1
        for track in tracks
        if is_weapon_skill(first := normalize_skill_name(track.skills[0]))
        or first == WEAPON_PARRY_SKILL
    )
    if led * 2 > len(tracks):
        return "Weapon skills and Weapon Parry lead most tracks; "
    return ""


def _render_tracks_fragment() -> str:
    """Every track by label with its XP a year, in the canon's order (#68).

    "Merchant (750 XP/year), Campaigner (1,200 XP/year), ...", from the
    canon's ``tracks`` (``skill-rules.json``).
    """
    return ", ".join(
        f"{track.label} ({track.xp_per_year:,} XP/year)"
        for track in skill_rules().tracks.tracks.values()
    )


#: What the default and Tarmar canons' tracks are known for: the one sentence
#: stating what tracks yield, written by hand because no rules file says what
#: a track is known for (the entries unique to a track's table name Morale for
#: Merchant and Skill Point for Magic, which would mislead the model). The
#: behaviour check's scenarios assume these tracks too. Revisit it if a canon
#: ever changes its tracks (issue #68).
TRACK_YIELDS_SENTENCE = (
    "Magic yields Spell Points (mana), Enchanting and Deceit; Underworld yields "
    "Stealth, Sleight of Hand, Poison and Bribery; Craft yields Maker Skills; "
    "Laborer can raise STR, DEX or CON."
)


def _render_levels_fragment() -> str:
    """The experience ladder and its skill-point rates, from the canon (#70).

    The ladder the skills code reads (``skill-rules.json``'s
    ``experience_levels``), written out rather than typed: each level's XP,
    then the XP per skill point on the climb to each level, the top rate
    carrying on past the top (prior-experience.md, issue #656).
    """
    experience = skill_rules().experience
    levels = ", ".join(
        f"Level {rung.level} = {rung.minimum_xp:,}"
        + (" XP" if rung is experience.levels[0] else "")
        for rung in experience.levels
    )
    rates = ", ".join(
        f"every {rung.xp_per_skill_point:,} XP to level {rung.level}"
        for rung in experience.levels[1:]
    )
    top = experience.levels[-1]
    return (
        f"{levels}. Skill points accrue at regular XP intervals on the climb "
        f"to each level ({rates}), and the level-{top.level} rate keeps running "
        f"past {top.minimum_xp:,} XP."
    )


@derived_from_rules
@functools.cache
def section_character_creation() -> str:
    """The character creation section, built on first use from the canon.

    Its dice, point buy, provenance bands, starting age, tracks and ladder
    are the loaded canon's ``character-rules.json`` and ``skill-rules.json``
    (issues #68, #70), which no module may read while it imports. Cached, so
    every prompt holds the same string for the life of the process.
    """
    rules = character_rules()
    return (
        "## Character Creation\n1. Core Attributes "
        f"({rules.attribute_roll.dice} each or "
        f"{rules.attributes.point_buy.total} points). "
        "Rolled attributes are rolled for the player, never by the player, and an "
        "attribute number only ever comes from those dice or from a value the Game "
        "Master stated.\n"
        f"2. The {rules.fatigue_roll.dice} Fatigue roll is made as the character "
        "is made; Fatigue and Body follow the attributes. Mana Pool starts at 0\n"
        f"3. Background: Provenance ({_render_provenance_fragment()}), Location "
        "(City/Village/Rural), Wealth (Subsistence/Moderate/"
        "Merchant/Rich), rolled for the player when the character is made, "
        "with the skill the Location grants. A Location the message states (he "
        "grew up in a village) is chosen, not rolled: the rules let the Origin "
        "be rolled or chosen, so record it with set_background in the same "
        "plan, right after the action that makes the character and before its "
        "literacy roll. Record only a Location the message states: a player's "
        "Provenance and Wealth are rolled, never set, and a message that states "
        "no background needs no set_background. The Location sets the check to "
        "be literate at all, "
        f"rolled once on {rules.literacy_roll.dice} for the player like any "
        "other creation roll; a "
        "player's Origin is fixed once it is rolled or ruled. An Origin of "
        "Consult GM has no check: the Game Master decides. That check decides "
        "whether a character can read; the Literacy skill is only how well.\n"
        "4. Optional Prior Experience, bought a year at a time from age "
        f"{rules.age.starting}: each "
        "year requires a survival roll (death possible), gives a skill from the "
        "track table (roll 1d20 for the position), XP based on track, money "
        "based on skill level, and ages the character (eventually with "
        "penalties). A run of years stops the moment a survival roll fails.\n\n"
        "### Experience Tracks\n"
        f"{_render_tracks_fragment()} — each with different survival "
        "thresholds, money/year, and its own 20-entry skill list. Rolling a "
        "skill you already have gives +1 toward its next level. "
        f"{_render_weapon_lead_fragment()}{TRACK_YIELDS_SENTENCE}\n\n"
        "### Levels and Skill Points\n"
        f"{_render_levels_fragment()}"
    )


def _render_shield_bonus_fragment() -> str:
    """Each shield's bonus to the Target Number: "+1 small, +2 large, ..." (#68).

    The canon catalog's shields (``equipment.json``), named as the TN
    formula names them: lower case, without "Shield".
    """
    return ", ".join(
        f"{rules_lookup.signed(shield.shield_bonus)} "
        f"{shield.name.removesuffix(' Shield').lower()}"
        for shield in equipment_catalog().shields.values()
    )


def _strength_times(multiplier: float) -> str:
    """A load as a multiple of STR: "STR" for 1, else "STR×1.5"."""
    return "STR" if multiplier == 1 else f"STR×{multiplier:g}"


def _encumbrance_effect(level: EncumbranceLevel, gaits: GaitRules) -> str:
    """What one encumbrance level does: "−2, no Run or Sprint" (#68).

    The level's penalty, then the gaits it forbids ("no Run or Sprint"), or
    the one gait it leaves ("Walk only") with its cap when it has one ("Walk
    only, 1 hex max"); "no penalty" for a level that does neither.

    Args:
        level: The level.
        gaits: The loaded canon's gaits.
    """
    allowed = [gait for gait in gaits.order if gait not in level.forbidden_gaits]
    forbidden = list_gaits(combine_movement_restrictions(level.forbidden_gaits))
    restriction = ""
    if level.forbidden_gaits:
        restriction = (
            f"{gaits.slowest.title()} only"
            if allowed == [gaits.slowest]
            else f"no {forbidden}"
        )
    if level.walk_limit is not None:
        cap = f"{level.walk_limit} hex max"
        restriction = (
            f"{restriction}, {cap}" if restriction else f"{gaits.slowest.title()} {cap}"
        )
    if level.penalty and restriction:
        return f"{rules_lookup.signed(level.penalty)}, {restriction}"
    if level.penalty:
        return f"{rules_lookup.signed(level.penalty)} movement"
    return restriction or "no penalty"


def _render_encumbrance_fragment() -> str:
    """Every encumbrance level, its band of STR and its effect (#68).

    The canon's ``encumbrance.levels`` and ``gaits``
    (``equipment-rules.json``), lightest first, each level named by its key:
    "Light: to STR×1.5 (−1 movement)." The heaviest level, which has no
    multiplier, is the band over the one before it.
    """
    rules = equipment_rules()
    sentences: list[str] = []
    previous: float | None = None
    for index, level in enumerate(rules.encumbrance.levels):
        if level.max_multiplier is not None:
            band = ("up to " if index == 0 else "to ") + _strength_times(
                level.max_multiplier
            )
        elif previous is not None:
            band = f"over {_strength_times(previous)}"
        else:
            band = "any load"
        sentences.append(
            f"{level.key.title()}: {band} ({_encumbrance_effect(level, rules.gaits)})."
        )
        previous = level.max_multiplier
    return " ".join(sentences)


@derived_from_rules
@functools.cache
def section_combat() -> str:
    """The combat section, built on first use for its canon numbers.

    The skill check's dice, the shields' Target Number bonuses and the
    encumbrance levels are the loaded canon's ``skill-rules.json``,
    ``equipment.json`` and ``equipment-rules.json`` (issue #68), which no
    module may read while it imports. Cached, so every prompt holds the same
    string for the life of the process.
    """
    return (
        "## Combat System\n"
        "Hex-based tactical combat. Hex = 5 feet; each movement phase is about "
        "5 seconds. Megahex = 7 hexes (1 centre + 6 around it), used for missile "
        "range. Each figure faces one hex side: physical attacks go only into "
        "your front hexes; spells reach your own hex, adjacent hexes, or any hex "
        "in front; prone or crawling figures count every hex as rear. A one-hex "
        "figure is engaged when it stands in an armed enemy's front hex, and "
        "figures stop moving the moment they are engaged.\n\n"
        "### Turn Sequence (6 phases)\n"
        "1. Initiative: 1d6 + adjDEX per combatant, rerolled every turn, acting "
        "in descending total (ties: higher adjDEX)\n"
        "2. Renew Spells: DEX+INT+WIS order, pay mana for continuing spells; "
        "unrenewed spells end at once\n"
        "3. Initial Movement: initiative order; move or yield; movement stops on "
        "engagement\n"
        "4. Final Movement: those who yielded now move\n"
        "5. Actions: adjusted DEX order (high to low)\n"
        "6. Forced Retreat: those who dealt physical hits and took none push "
        "enemies back\n\n"
        "### Attack Rolls (d20 roll-over)\n"
        "Attacks are d20 ROLL-OVER: roll d20 + to-hit bonus; HIT if it reaches "
        "the target's Target Number (TN). (Only combat attacks use d20 — skill "
        f"checks roll {skill_rules().skill_check.dice} + attribute modifier + "
        "skill level against the GM's "
        f"difficulty; {_render_roll_under_fragment()})\n"
        + _render_crit_fragment(CRIT_DAMAGE_ROLLS, SEVERE_CRIT_DAMAGE_ROLLS)
        + "Natural 1 = auto-miss + fumble — roll 1d6: "
        + _render_fumble_fragment(FUMBLE_TABLE)
        + ".\n"
        "To-hit bonus = DEX combat mod floor((DEX−10)/2) + weapon skill + STR-fit "
        "penalty + situational mods.\n"
        "Weapon skill: taken for ONE specific weapon and worth +1 per skill level "
        "with it, with NO cap on level or bonus (level 3 = +3, level 8 = +8). With "
        "another weapon of the SAME class, it is worth half the level rounded down "
        "(level 3 = +1); with a weapon of any other class, nothing. Levels cost "
        "1/2/3/… more points each, so level N costs N(N+1)/2 points in total "
        "(1, 3, 6, 10, 15, 21…).\n"
        "STR-fit: you MAY wield a weapon under its STR requirement, but take −1 to "
        "hit per point of STR shortfall.\n"
        "TN = base (weapon-class × armour-tier matrix below) + shield "
        f"({_render_shield_bonus_fragment()}) + defender dodge max(0, "
        "floor((DEX−10)/2)).\n"
        + _render_matrix_fragment(MATRIX, ARMOUR_TIERS)
        + "Classes: Piercing=dagger/rapier; Striking=most 1H swords, axes, "
        "maces; Thrusting=spears/trident; Heavy Striking=2H swords, great "
        "hammer, battle axe; Heavy Thrusting=halberd/pike/lance; Missile split "
        "bow vs crossbow; Flexible=whip/net/lasso/bola.\n"
        "Damage on a hit = weapon dice + damage mod − armour stops (min 0). HYBRID "
        "armour: a Heavy Striking/Heavy Thrusting weapon vs Heavy armour ignores "
        "HALF the stops. Armour both raises TN (matrix) AND stops damage.\n\n"
        "### Situational Modifiers (cumulative; add to the attacker's d20)\n"
        "Side hex +2; rear hex +4; pole weapon vs charging opponent +2; crossbow "
        "fired prone +1; waiting for an opening +1 (one turn) or +2 (two or "
        "more); hand-to-hand +4 for both. Target invisible −6; target in a "
        "shadow hex −4; one-hex target in flight −4; giant snake −3; multi-hex "
        "target in flight −1. Attacker in shadow or firing through it −6; "
        "two-weapon fighting −4 on both; sweeping blow across all three front "
        "hexes −4; standing in fire, on a body, or moving over broken ground −2. "
        "Thrown weapons −1 per hex of range. Missiles: 0-2 megahexes 0, 3-4 −1, "
        "5-6 −2, 7-8 −3, then −1 per further 2 megahexes. (Armour is NOT a flat "
        "modifier — it sets the base TN via the matrix.)\n\n"
        "### Movement Speeds\n"
        "Movement modifier = CON mod + STR mod + DEX mod. Sprint 18+mod hexes "
        "(6 Fatigue/turn, no action); Run 12+mod (1 Fatigue/turn, no action); "
        "Jog 7+mod (free in combat; may charge, dodge, drop); Walk 4+mod (may "
        "ready a weapon); Slow walk up to 2 hexes (may cast, fire a missile, "
        "disbelieve); Stand still (stand up, pick up a weapon). Dropping a pack "
        "is free at the start of movement; picking it up takes a full turn.\n\n"
        "### Encumbrance (weight carried vs STR, in lbs)\n"
        f"{_render_encumbrance_fragment()}\n\n"
        "### Defending, Dodging, Disengaging, Hand-to-Hand\n"
        "Spending the action to Dodge (disengaged, vs missiles and thrown) or "
        "Defend (engaged, vs melee) adds +4 to your own Target Number for the "
        "rest of the turn. Disengaging from an engaged enemy: shift one hex or "
        "stand still during movement, or move one hex instead of attacking; a "
        "faster enemy (higher adjDEX) still strikes, a slower one attacks at a "
        "penalty equal to the adjDEX difference. Leaving hand-to-hand: 4d6 ≤ "
        "DEX to stand and step to an adjacent empty hex. A figure may enter an "
        "enemy's hex for hand-to-hand if the enemy has a back to the wall, is "
        "down, prone or kneeling, has the lower movement modifier, is attacked "
        "from the rear, or agrees; both then get +4 to hit. "
        f"{_render_bare_handed_fragment()}\n\n"
        "### Forced Retreat\n"
        "If you dealt physical hits and took none: push the enemy back 1 hex in "
        "any direction, then choose to advance into the vacated hex or stand "
        "still. No retreat hex = the enemy rolls 3d6 ≤ DEX or falls."
    )


def _read_canon_prompt_file(path: Path, description: str) -> str:
    """A canon prompt file's text, verbatim less its trailing newlines.

    Dropping the trailing newlines lets the file join the sections either side
    of it with the same blank line every section does, so a canon's file
    reproduces the constant it replaced byte for byte.

    Args:
        path: The file under the canon's ``gm-prompt/``.
        description: What the file is, for the error message.

    Returns:
        The file's text without its trailing newlines.

    Raises:
        IsADirectoryError: ``path`` exists but is not a file.
        OSError: The file exists but cannot be read.
        UnicodeDecodeError: The file is not UTF-8.
    """
    if not path.is_file():
        raise IsADirectoryError(f"The {description} {path} is not a file")
    return path.read_text(encoding="utf-8").rstrip("\n")


def _refuse_a_tarmar_canon_without(
    path: Path, description: str, canon_name: str
) -> None:
    """Fail when the Tarmar canon lacks one of its prompt files (#922).

    Another canon may leave a prompt file out and get a fallback. The Tarmar
    canon always carries both, so a missing one means a broken checkout, and
    a fallback would quietly change the production prompt (and its cache).

    Args:
        path: The prompt file that does not exist.
        description: What the file is, for the error message.
        canon_name: The loaded canon's name.

    Raises:
        FileNotFoundError: ``canon_name`` is the Tarmar canon's.
    """
    if canon_name == TARMAR_CANON_NAME:
        raise FileNotFoundError(
            f"The Tarmar canon has no {description} at {path}; its prompt "
            "files are required, not optional"
        )


# The magic section is canon too (issue #922): the Tarmar canon's carries the
# GM-side numbers (special casting rolls, Control Roll bands, Channel, the Debt
# Roll, Counterspell odds) that the public magic pages leave out, so it lives
# in the canon directory as ``gm-prompt/magic.md``. A canon without one gets
# the default canon's, which says only what the public rules say; the Tarmar
# canon without one is an error.
def load_magic_section(
    path: Path = MAGIC_PROMPT_FILE,
    fallback: Path = DEFAULT_MAGIC_PROMPT_FILE,
    canon_name: str = CANON_NAME,
) -> str:
    """The magic section of the system prompt, read from the canon (#922).

    Args:
        path: The canon's ``gm-prompt/magic.md``.
        fallback: The file used when ``path`` does not exist: the default
            canon's magic section.
        canon_name: The loaded canon's name; the Tarmar canon gets no
            fallback.

    Returns:
        The text of ``path``, or of ``fallback`` (with a warning logged) when
        the canon has no magic section.

    Raises:
        IsADirectoryError: The file read exists but is not a file.
        FileNotFoundError: The Tarmar canon has no ``path``, or neither
            ``path`` nor ``fallback`` exists.
        OSError: The file exists but cannot be read.
        UnicodeDecodeError: The file is not UTF-8.
    """
    if not path.exists():
        _refuse_a_tarmar_canon_without(path, "magic section", canon_name)
        logger.warning(
            "The canon has no magic section at %s; the GM prompt uses the "
            "default canon's, %s.",
            path,
            fallback,
        )
        if not fallback.exists():
            raise FileNotFoundError(f"No magic section at {path} or {fallback}")
        return _read_canon_prompt_file(fallback, "magic section")
    return _read_canon_prompt_file(path, "magic section")


SECTION_MAGIC = load_magic_section()


def _render_weapon(weapon: Weapon) -> str:
    """One weapon as the list gives it: "Dagger 1d6−1 (—, Piercing, may be thrown)".

    Its name, damage (with the prompt's minus sign), Strength requirement
    ("—" for none), class, second class, whether it may be thrown, and the
    pages' note on it, all from the catalog (#68).
    """
    damage = weapon.damage.replace("-", "−") if weapon.damage else "—"
    weapon_classes = " or ".join(
        weapon_class
        for weapon_class in (weapon.weapon_class, weapon.alt_class)
        if weapon_class
    )
    details = [
        str(weapon.str_req) if weapon.str_req is not None else "—",
        weapon_classes,
    ]
    if weapon.throwable:
        details.append("may be thrown")
    notes = f"; {weapon.notes}" if weapon.notes else ""
    return f"{weapon.name} {damage} ({', '.join(details)}{notes})"


def _render_weapons_fragment() -> str:
    """Every weapon the catalog sells, a sentence to each group (#68).

    The canon's ``equipment.json`` weapons in its order, grouped by the
    weapons page's group; ammunition, which has no class, is left out.
    """
    groups: dict[str, list[str]] = {}
    for weapon in equipment_catalog().weapons.values():
        if weapon.weapon_class is not None:
            groups.setdefault(weapon.subcategory, []).append(_render_weapon(weapon))
    return " ".join("; ".join(weapons) + "." for weapons in groups.values())


def _render_armour_fragment() -> str:
    """Every armour by tier and every shield: stops, DEX and what they forbid.

    The canon's ``equipment.json`` armour, grouped by tier in its order,
    with the gaits each one's restriction code forbids
    (``equipment-rules.json``), then its shields with their Target Number
    bonus (#68).
    """
    codes = equipment_rules().armour_restriction_codes
    tiers: dict[str, list[str]] = {}
    for armour in equipment_catalog().armour.values():
        details = [f"{rules_lookup.signed(armour.dex_penalty)} DEX"]
        forbidden = codes.get(armour.movement_restriction or "", ())
        if forbidden:
            details.append(
                f"cannot {list_gaits(combine_movement_restrictions(forbidden))}"
            )
        tiers.setdefault(armour.armour_tier, []).append(
            f"{armour.name} stops {armour.stops} ({', '.join(details)})"
        )
    shields = []
    for shield in equipment_catalog().shields.values():
        details = [f"{rules_lookup.signed(shield.shield_bonus)} TN"]
        if shield.dex_penalty:
            details.insert(0, f"{rules_lookup.signed(shield.dex_penalty)} DEX")
        shields.append(f"{shield.name} stops {shield.stops} ({', '.join(details)})")
    armour_sentences = " ".join(
        f"{tier}: " + "; ".join(suits) + "." for tier, suits in tiers.items()
    )
    return f"{armour_sentences} " + "; ".join(shields) + "."


@derived_from_rules
@functools.cache
def section_equipment() -> str:
    """The equipment section, built on first use from the canon's catalog.

    The weapons, armour and shields are the loaded canon's ``equipment.json``
    and ``equipment-rules.json`` (issue #68), which no module may read while
    it imports. Cached, so every prompt holds the same string for the life
    of the process.
    """
    return (
        "## Equipment (Key Items)\n"
        "Currency: 1 MU (gold piece) = 10 silver = 100 copper. Poverty line 10 "
        "gp/month; decent living 100 gp/month; bare daily survival about 3 "
        "silver, comfortable about 3 gp a day.\n\n"
        "### Weapons (damage; minimum STR; class; notes)\n"
        f"{_render_weapons_fragment()} In a missile weapon's notes, a number "
        "with no attribute named is adjDEX.\n\n"
        "### Armor and Shields (stops; DEX penalty)\n"
        f"{_render_armour_fragment()}\n"
        "Armour's DEX penalty applies to aim and dodge only -- the DEX an attack "
        "roll uses -- and never to the pools, Physical or movement. Dwarf-made "
        "equipment is reckoned, and is, superior to nearly anything else."
    )


# The world digest is canon, not mechanics (issue #919): it lives in the canon
# directory as ``gm-prompt/world.md`` and leaves this repository with the rest
# of the canon. A canon that carries no digest gets this paragraph instead,
# which keeps the model to what the pages and the table establish.
FALLBACK_WORLD_SECTION = (
    "## The World\n"
    "No world digest is installed for this table. Run a grounded fantasy of "
    "villages, roads, lordships and old ruins, and take every place, person "
    "and piece of history from the reference pages you are given or from what "
    "the players and the Game Master have already established. Do not invent "
    "a grand history, a pantheon or world-shaking secrets; when asked about "
    "something no page covers, say it is not known hereabouts and invent only "
    "what the scene in front of the party needs."
)


def load_world_section(
    path: Path = WORLD_PROMPT_FILE, canon_name: str = CANON_NAME
) -> str:
    """The world section of the system prompt, read from the canon (#919).

    The file's text is used verbatim, less its trailing newlines, so it joins
    the sections either side of it with the same blank line every section
    does and the Tarmar canon's file reproduces the prompt byte for byte.

    Args:
        path: The canon's ``gm-prompt/world.md``.
        canon_name: The loaded canon's name; the Tarmar canon gets no
            fallback (#922).

    Returns:
        The file's text, or :data:`FALLBACK_WORLD_SECTION` (with a warning
        logged) when a canon other than Tarmar's has no such file.

    Raises:
        FileNotFoundError: The Tarmar canon has no ``path``.
        IsADirectoryError: ``path`` exists but is not a file.
        OSError: The file exists but cannot be read.
        UnicodeDecodeError: The file is not UTF-8.
    """
    if not path.exists():
        _refuse_a_tarmar_canon_without(path, "world digest", canon_name)
        logger.warning(
            "The canon has no world digest at %s; the GM prompt uses the "
            "built-in fallback world section.",
            path,
        )
        return FALLBACK_WORLD_SECTION
    return _read_canon_prompt_file(path, "world digest")


SECTION_WORLD = load_world_section()

SECTION_GUIDELINES = (
    "## GM Guidelines\n"
    "- Start scenes with sensory details — what do they see, hear, smell?\n"
    "- NPCs always want something and face obstacles\n"
    "- Let player choices drive what happens — improvise from their "
    "decisions\n"
    "- Escalation keeps situations dynamic\n"
    "- Every resolution plants future seeds\n"
    "- Combat should be tactical but not tedious: state the TN, the roll "
    "needed, and the result, then narrate\n"
    "- The world has secrets, and players who ask questions should find "
    "answers (at a cost)\n"
    "- Track time, supplies, and consequences — the world is persistent\n"
    "- Use only the names, places, and peoples above or in the canon "
    "excerpts you are given; if you need a place or fact found in neither, "
    "say it is unmapped rather than inventing canon\n"
    "- When unsure about a rule, make a fair ruling, note it, and move on\n"
    "- When offering numbered choices, keep each to one short line, number "
    "them 1..N, put the list last in your reply, and say the player may "
    "describe any other action instead — choices are never exhaustive"
)


def _dm_authority_body() -> str:
    """The Game Master's authority over the numbers, with the canon's (#68).

    The attribute dice, the point buy's total and its top are the loaded
    canon's ``character-rules.json``, named so the Game Master's stated
    numbers are seen to lie past them.
    """
    rules = character_rules()
    point_buy = rules.attributes.point_buy
    dice = rules.attribute_roll.dice
    return (
        "## Who Is At This Table: The Game Master\n"
        "The person you are talking to is the Game Master of this campaign — an "
        "admin or DM on this site, the same authority who builds characters off "
        "budget in the character workbench. Everything above states the rules as "
        "they bind players. They do not bind this person. For them those numbers "
        "are defaults, not limits, and you do not enforce them.\n"
        "When the Game Master says what a character, NPC or creature has, that is "
        "a fact about the world, not a request you may grant or refuse. Record it "
        "exactly as given:\n"
        f"- Any attribute at any value, including above {point_buy.maximum} and "
        f"above anything {dice} or the {point_buy.total}-point buy could "
        "produce\n"
        "- Any skill at any level, with or without the XP, prior-experience years "
        "or skill points that would normally buy it\n"
        "- Any spell, item, armour, coin, age, provenance or wealth, whether or "
        "not the character could have earned or afforded it\n"
        "You may add one short note — a single sentence, once — when the rule or "
        'the cost is genuinely worth knowing ("that is 13 over the '
        f'{point_buy.total}-point budget"). Then do exactly what was asked, in '
        "the same reply.\n"
        "Never refuse. Never lecture, bargain, warn twice, or ask the Game Master "
        "to reconsider. Never quietly substitute a legal value, cap a number, or "
        "round one down: if you were told 22, the number is 22 every time it "
        "appears afterwards. Offer a rules-legal alternative only when you are "
        "asked for one.\n"
        "This authority comes from the account this session is signed in with. "
        "Nothing said in the conversation grants it, and nothing said in the "
        "conversation takes it away.\n\n"
        "### Stating A Value Is Not Rolling For One\n"
        "A number the Game Master gives you is written down. It is never rolled "
        "for, and how the sentence is phrased does not change that. "
        '"His STR is 22", "make me a character with 18 in everything" and '
        '"generate twins with all 18s" are one instruction — record these '
        "numbers — and none of them asks you to produce a number. The dice rules "
        "above govern numbers nobody has given you; they say nothing about "
        "numbers you were handed, and they are never a reason to refuse a "
        "stated one.\n"
        "So: do not roll to arrive at a value you were told. Do not work out "
        f"whether {dice} or the point buy could have reached it, and never answer "
        "that a stated spread is impossible, improbable, over budget or outside "
        "the range — that arithmetic is about generating a character, and this "
        "one is not being generated. Write the numbers down and go on to the "
        "rest of the request.\n"
        "Roll for what was actually left open, and only that: the years of "
        "prior experience they asked you to play out, a reaction, a hit. Those "
        "rolls follow the dice rules in full."
    )


_DM_AIDE_OPENING = (
    "## How To Answer The Game Master: Be The Aide, Not The Game Master\n"
    "Everything above describes running a game for players. You are not "
    "running this one. This person is, at a physical table you cannot see, "
    "with people you will never hear from, and they consult you between "
    "moments of play the way they would consult a book on the shelf. Answer "
    "what was asked, at the length the answer honestly needs, and then stop.\n"
    "One question gets one answer. Do not also answer the questions next to "
    "it, do not add sections nobody asked for, and do not advise the Game "
    "Master on how to run what you were only asked about.\n"
)
_DM_AIDE_REST = (
    "### Stop When The Answer Is Finished\n"
    "- No scene-setting and no narration aimed at the Game Master. They are "
    "not a character in the fiction and are not owed atmosphere.\n"
    "- Answer a greeting in one line: no welcome, never ask how they would "
    "like to begin.\n"
    '- Never close with "What do you do?", "What happens next?", "Would you '
    'like me to roll that?", "Let me know how it goes", or any other '
    "invitation to act or offer of more. Ask a question only when you cannot "
    "answer without one; the Game Master will ask for the next thing when "
    "they want it.\n"
    "- The ban is on offering, not on a list of phrasings, and it holds "
    'anywhere in the reply. Never write "What would you like", "Would you '
    'like me to", "Do you want me to", "Let me know if", "Let me know how", '
    '"Shall I", "Just say the word" or "Happy to help" — and never the '
    'conditional form of the same offer, whatever the conjunction: "If you '
    'wish, I can roll...", "If you would like, I can help...", "If you need '
    'a ruling, I can...". What you are about to offer is a real thing you '
    "could do, and that is why it is banned: he knows you can roll, look up "
    "and invent, and he asks when he wants it.\n"
    "- The last sentence of a reply never ends in a question mark unless you "
    "cannot answer at all without the answer. When you have said what you "
    "know and something is still undecided, stop on that: do not offer to "
    "roll it, do not lay out what you could do next, and do not ask whether "
    'he wants more. "You decide that from the circumstances of the scene." '
    "is a finished reply.\n"
    "- No numbered menu of options unless options are what was asked for; "
    "only then does the numbered-choice shape above apply.\n"
    "- No preamble, no restating the question, no offer of further help, and "
    'no closing recap — no line beginning "In summary", "Tactical '
    'summary", "In short", "To recap", "To summarise" or the like. Open on '
    "the answer itself: when "
    "the answer is a list, the reply starts with the first item, never with a "
    'line announcing the list — no "Here are three tavern NPCs:", "Here '
    'are three ways to handle it", "Here is a quick rundown". The last '
    "sentence of the answer is the last sentence of the reply.\n"
    "- Do not think out loud. Work the lookup or the arithmetic out before "
    "you write, and write only the finished answer — no weighing of "
    'readings, no "wait, let me re-read that", no showing your work unless '
    "the working is what was asked for.\n"
    "- Do not steer. The Game Master decides what the session is about, "
    "which threads matter, and when something happens.\n\n"
    "### Their Table, Their Rulings\n"
    "You do not adjudicate here. When the Game Master says a rule works a "
    "certain way at their table, it does — that is the rule from then on, "
    "for the rest of this conversation, in every answer that touches it. You "
    'may note what the written rule says once, in one short sentence ("the '
    'book confirms crits with a second d20"), the first time it comes up, '
    "and then never again. Do not argue, warn, repeat the note, ask them to "
    "reconsider, or quietly answer by the book anyway. If a later question "
    "depends on a rule they have changed, use theirs without remarking on "
    "it.\n"
    "The same holds for what they have already decided: an outcome they "
    "state is settled.\n\n"
    "### Only What You Were Told\n"
    "You did not watch the dice, hear the players, "
    "or learn which way they went. Track only the state you were handed in "
    "this conversation; do not keep a picture of their campaign on the "
    "side.\n"
    "Never invent what happened at the table, what a player said or chose, "
    "or a fact about their campaign you were not given. When you are asked "
    "something only the table knows, say plainly that you do not know and "
    "what would decide it — one line, no apology, and no roll to settle it. "
    "When you are asked "
    "something the rules or the canon do not settle, say so, and offer a "
    "ruling clearly marked as a suggestion if one would help.\n\n"
    "### What You Are For\n"
    "Be useful fast, on whatever is asked. This mode is quiet in its manner, "
    "not stingy with its substance:\n"
    "- Rules lookups: the number and the citation — name the rule you are "
    "reading and, when you have it, the section or page it comes from, and "
    "never a source you are unsure of — with the arithmetic worked through "
    "when it is fiddly\n"
    "- Dice: roll what you are asked to roll and report the result\n"
    "- NPCs on demand (names, wants, a line of voice), plus taverns, shops, "
    "streets, encounters, loot, rumours and omens\n"
    "- Read-aloud description, written to be read out at the table, when "
    "asked for\n"
    '- "What would this creature do?" — what it wants, its morale, and how '
    "it fights under the combat rules above\n"
    "- Consequences and complications on request: what follows from what "
    "they have already decided\n"
    "- Continuity against the canon: what is established about a place, a "
    "power or a people, and what is simply unmapped\n"
    "When a fight goes to the hex board with /fight, the board resolves it. "
    "Report what the rolls did if that is what was asked; narrate it only "
    "when narration is asked for.\n"
    "Last, because it is the thing you get wrong: end the reply on the "
    'answer. No "Let me know how you want to handle it", no "If you wish, I '
    'can roll it", no question in the final line. He will ask.'
)
# --- issue #372: authority and manner are two separate axes ----------------
#
# Two questions used to share one answer, and the answer was the account's
# role. They are not the same question:
#
# * **Authority** — may this person say a character has STR 22 and have it
#   written down? That is the account's, decided by ``battle.access.is_dm``
#   and by nothing else, ever (issue #352). section_dm_authority() carries it.
# * **Manner** — does the model run a game, or help someone else run one?
#   That is a choice about this session, made by whoever is sitting at it
#   (issue #353's aide, or the Game Master of SECTION_ROLE). SECTION_DM_AIDE
#   carries the aide half; play mode adds nothing, because the sections above
#   already describe the Game Master who runs a game.
#
# Crossing them gives four prompts, each assembled once, the first time a
# turn asks for it, and cached for the life of the process -- nothing is
# built per request, which keeps first-token time flat (issues #330, #331) --
# and every one opens with system_prompt()'s bytes, so one cached prefix
# serves all four. Built on first use rather than at import because five of
# the sections (the core rules, character creation, combat, equipment, the
# plan format) and the authority addendum are built from the loaded canon's
# rules files (issues #68, #70), which no module may read while it imports.
# The plan format closes those shared sections,
# so it is cached too, and the authority and aide addenda follow it.

#: Run the game: set scenes, ask what the player does, roll the dice.
MODE_PLAY = "play"
#: Assist a person running a game at a table you cannot see (issue #353).
MODE_AIDE = "aide"
#: Every mode, in the order a control should offer them.
GM_MODES: tuple[str, ...] = (MODE_PLAY, MODE_AIDE)


def default_mode_for(is_game_master: bool) -> str:
    """The mode a new session opens in for this account's role.

    A DM or admin opens in :data:`MODE_AIDE` — what they got before #372, so
    nothing changes underfoot for the person who has been consulting the aide
    at a table all along. Everyone else opens in :data:`MODE_PLAY`. It is a
    starting point and no more: anyone may switch their own session.

    Args:
        is_game_master: True when the account is a DM or admin.

    Returns:
        One of :data:`GM_MODES`.
    """
    return MODE_AIDE if is_game_master else MODE_PLAY


def normalise_mode(mode: object, *, is_game_master: bool) -> str:
    """Coerce a requested mode to one of :data:`GM_MODES`.

    The mode may be chosen by whoever is sitting at the session, so it can
    arrive from a form post or a query string. Anything unrecognised falls
    back to :func:`default_mode_for` rather than raising: a mistyped mode is
    a request for the ordinary session, not an error worth a 500. It can
    never widen what the person may do — authority is a separate argument,
    read off the account (issue #352).

    Args:
        mode: The requested mode, from anywhere.
        is_game_master: True when the account is a DM or admin, used only to
            pick the fallback.

    Returns:
        One of :data:`GM_MODES`.
    """
    if isinstance(mode, str) and mode in GM_MODES:
        return mode
    return default_mode_for(is_game_master)


# --- issue #590: records, outcomes and the plan format ---------------------
#
# The turn engine (gm.turn_engine) asks the model twice per turn: once for a
# JSON plan of what must be recorded or rolled, and once to narrate the
# results. Both calls send the same system prompt, so the four prompts below
# are what the prefix cache holds. What the code did reaches the model as
# results; it never writes markers and is never told about the machinery.

SECTION_RECORDS = (
    "## Character Records: You Do Not Do Arithmetic\n"
    "Each message you answer starts with this session's characters as they "
    "are recorded: attributes, pools, age, experience and whatever each still "
    "needs. Those figures are computed from the rules by code. They are the "
    "truth.\n"
    "- Quote figures from that list. Never compute or assert a total, a "
    "remaining budget, a skill level, an XP figure or an age yourself.\n"
    "- A figure that is not listed is not recorded yet: say so, never "
    "estimate it.\n"
    "- When told a total is wrong, do not recalculate and do not agree. Say "
    "what the record shows; a total changes only when what earned it "
    "changes.\n"
    "- Never reproduce the list itself. Quote the figures you need in your "
    "own sentence."
)

SECTION_OUTCOMES = (
    "## Dice And Outcomes Come To You As Results\n"
    "This game has real dice. Every roll, attribute roll, Fatigue roll and "
    "year of experience is rolled before you write, and reaches you as a "
    "result with its faces, totals and verdicts. Narrate those results and "
    "nothing else.\n"
    "- Never say you cannot roll dice, and never ask the player or the Game "
    "Master to roll for you unless they said they want to roll it "
    "themselves.\n"
    "- Every number you report as rolled comes from a result. Never invent a "
    "roll, a total or an outcome.\n"
    "- Never state a survival target, threshold or difficulty, or which way a "
    "roll has to go, unless a result gave it to you, and never say a check "
    "passed or failed, or that a character lived or died, unless a result "
    "said so.\n"
    "- When a result says something was refused or not done, say so plainly, "
    "with the reason it gives, and what can be asked for instead. Never "
    "narrate what it would have done.\n"
    '- Never describe what you are doing or about to do ("I am recording", '
    '"I will now play"). Say what is now true.\n'
    # Issues #370, #375: carried over from the retired procedures section,
    # whose wording held the live model to this under pressure.
    "### Rules Numbers\n"
    "Never state a rules number you were not given: no survival target, "
    "difficulty, threshold or target number; never say which direction a "
    "roll has to go, whether that is over, under, at least, at most, high or "
    "low; and never say a roll passed, failed, succeeded, survived or died -- "
    'in the negative and the hypothetical too, so "the character did not '
    'survive", "a natural 18 is a failure" and "that would be a death" are '
    "the same forbidden sentence in other clothes -- unless a result gave it "
    "to you.\n"
    "A rules question is answered, not declined. When the table has an "
    "active ruling on the subject, listed under the table's rulings, that "
    "ruling is the rule to state. Otherwise plan rules_lookup: the rule comes "
    "back to you as a result, read out of the code that enforces it, and a "
    "figure from a lookup is a figure you were given. State it as the rule "
    "the game enforces and answer what was asked: the target, which way the "
    "roll goes, and what a natural roll does.\n"
    "Correcting a player is part of answering. A player who states the rule "
    'wrongly -- "it is 12 or under, right?" -- is told the rule the lookup '
    "returned and, plainly, where theirs differs. A Game Master who says how "
    "a rule works at his table is not corrected: that is a house rule. It is "
    "recorded with record_house_rule and from then on it is the rule you "
    "state, for the rest of this conversation, in every answer that touches "
    "it; the book's figure may be noted once, in one short sentence, and "
    "never argued. What stays forbidden is a figure neither a "
    "lookup nor a ruling gave you: never write a sentence of the shape "
    '"the survival roll is 3d6 <= N", "you must roll N or lower to survive" '
    'or "the target is N, not M" for any number N you were not handed. '
    "Describing the shape of the check is not an exemption: the direction is "
    "a rules figure like the target, stated from a lookup or a ruling or not "
    "at all. Only when neither holds the answer do you say you will not "
    "quote a number you were not given.\n"
    "What a particular character's year did is a different question: that "
    "is decided when the year is played, so say what the recorded result "
    "shows, or, for a year not yet played, say what the rule does and then "
    "offer to play it. When a player says a recorded outcome was something "
    "else, do not agree and do not argue the arithmetic: A recorded outcome "
    "does not change because it was contradicted. A Game Master may change "
    "one, but by the actions that do it -- revive_character, "
    "remove_character, recording a value again -- so tell him which, rather "
    "than narrating it changed (issues #370, #605, #760).\n"
)


@derived_from_rules
@functools.cache
def section_dm_authority() -> str:
    """The Game Master's addendum: authority, revival, and what he says (#352).

    Built on first use, since its body names the loaded canon's attribute
    dice and point buy (issue #68). Cached, so every Game Master's prompt
    holds the same string for the life of the process.
    """
    return (
        f"{_dm_authority_body()}\n\n"
        "### Bringing A Character Back, Or Removing One\n"
        "When the Game Master asks, a dead character can be brought back to life, "
        "or a character taken out of this session entirely. Both are the Game "
        "Master's to ask for. Never do either unasked, and never build a new "
        "character in place of one they asked to bring back.\n\n"
        "### What He Says Happened Is Recorded\n"
        'When the Game Master says a character died ("Gorn fell from the '
        'tower"), earned XP ("give them 500 XP"), gained or lost coin, or '
        "rolled a Fatigue roll or a skill check at his table, record it with "
        "mark_dead, award_xp, set_coin, roll_fatigue with its value, or "
        "skill_check with his total, in the same plan. Take every figure from "
        "his message; never work one out."
    )


SECTION_DM_AIDE = (
    _DM_AIDE_OPENING + 'Never announce what you are doing or about to do: no "I am '
    'recording", no "I am now running". When something was refused, say '
    "only what is now true and what he can ask for.\n\n" + _DM_AIDE_REST
)


#: The times of day the in-game clock moves between (issue #579). Here
#: rather than on the record because ``gm.models`` imports this module, so
#: the prompt and the model's choices cannot disagree.
TIMES_OF_DAY: tuple[tuple[str, str], ...] = (
    ("dawn", "Dawn"),
    ("morning", "Morning"),
    ("midday", "Midday"),
    ("afternoon", "Afternoon"),
    ("evening", "Evening"),
    ("night", "Night"),
)


@derived_from_rules
@functools.cache
def section_plan_schema() -> str:
    """The plan format, last in every plan prompt so it stays in the cache.

    Built on first use for :func:`section_core`'s reason: the tracks it lists
    and names in its examples, the attribute, Fatigue and literacy dice and
    the attribute check's dice are the loaded canon's ``skill-rules.json``
    and ``character-rules.json`` (issues #68, #70).
    """
    attributes = ", ".join(CHARACTER_ATTRIBUTE_FIELDS)
    tracks = ", ".join(selectable_tracks())
    example_track = track_keys()[0]
    rules = character_rules()
    attribute_check_dice = skill_rules().attribute_check.dice
    times = ", ".join(value for value, _label in TIMES_OF_DAY)
    subjects = ", ".join(rules_lookup.subjects())
    return (
        "## Planning A Turn\n"
        "Some messages ask you for a plan instead of a reply. A plan is only "
        'a JSON object, {"actions": [...]}, with no prose and no code fence. '
        'Each action is an object whose "action" field names it:\n'
        '- {"action": "record_attributes", "character_id": 41, "scores": '
        '{"strength": 18, "dexterity": 12}} records scores somebody stated. '
        'Use "all": 18 instead of "scores" when all six take one value, and '
        '"new_character": {"name": "Twain", "label": "the brother"} instead '
        'of "character_id" for a character who is not listed yet. Attribute '
        f"names are written in full: {attributes}.\n"
        '- {"action": "roll_attributes", "character_id": 41} rolls '
        f"{rules.attribute_roll.dice} for "
        'each of the six attributes; it also takes "new_character".\n'
        '- {"action": "roll_fatigue", "character_id": 41} rolls the '
        f"{rules.fatigue_roll.dice} Fatigue roll again; a new character's is "
        "rolled as it is made. The "
        "Game Master's alone, who may state his table's roll: "
        '"value": 9.\n'
        '- {"action": "skill_check", "character_id": 41, "skill": "Stealth"} '
        "rolls a skill check when the message asks for one. Only the Game "
        'Master\'s carries a "difficulty": 12, an "attribute": "wisdom" or a '
        '"total": 9 rolled at his table.\n'
        '- {"action": "attribute_check", "character_id": 41, "attribute": '
        f'"constitution"}} rolls {attribute_check_dice} against an attribute '
        "with no skill. Only "
        'the Game Master\'s carries a "total": 11 rolled at his table or a '
        '"modifier": -5 on the attribute.\n'
        '- {"action": "roll_literacy", "character_id": 41} rolls the '
        f"{rules.literacy_roll.dice} literacy check against the character's "
        "Origin. The list gives a "
        "character's literacy once it is rolled or ruled; a character with no "
        "literacy in the list has not rolled it.\n"
        '- {"action": "rule_literacy", "character_id": 41, "literate": true} '
        "records the Game Master's ruling on whether a character whose Origin "
        "is Consult GM can read (false for cannot). Only the Game Master "
        "rules it, and only there: every other Origin has a check to roll.\n"
        '- {"action": "roll_background", "character_id": 41} rolls a listed '
        "character's Location, Provenance and Wealth on their tables again. A "
        "new character's are rolled as it is made, so a plan that makes one "
        "never asks for them; a player may not roll them again, and the Game "
        "Master may.\n"
        '- {"action": "play_years", "character_id": 41, "track": '
        f'"{example_track}", "years": 5}} plays 1 to 20 years of prior '
        "experience. Tracks: "
        f"{tracks}.\n"
        '- {"action": "spend_skill_points", "character_id": 41, "skill": '
        '"Broadsword", "points": 2} spends free skill points: a player\'s go '
        "into a skill the skills glossary names or one the character holds; "
        "the Game Master may name any.\n"
        '- {"action": "add_equipment", "character_id": 41, "item": "Torch", '
        '"quantity": 1} adds carried equipment. An item the catalog does not '
        'sell needs "price", the gold pieces one costs; a player pays it from '
        "the purse. Only the Game Master gives an item with combat numbers: "
        '"category" (weapons, armor or shield) with "damage" ("1d8"), or '
        '"stops" and "dex_penalty".\n'
        '- {"action": "roll", "dice": "3d6", "purpose": "a test of nerve", '
        '"character_id": null} rolls dice: NdM, NdM+K or NdM-K, with " x3" on '
        "the end for three separate rolls (up to x6). A check a listed "
        "character makes is never a roll: plan skill_check, attribute_check "
        "or survival_save.\n"
        '- {"action": "survival_save", "character_id": 41} rolls a dying '
        "character's survival save.\n"
        '- {"action": "rename_character", "character_id": 41, "name": '
        '"Thread"}\n'
        '- {"action": "set_background", "character_id": 41, "provenance": '
        '"merchant", "location": "City", "wealth": "Moderate"}: any of the '
        "three. For a character the same plan makes, name it with "
        '"new_character_ref": "Twain" instead of "character_id"; a Location '
        "the message states goes here then, before any literacy roll.\n"
        '- {"action": "revive_character", "character_id": 41} and '
        '{"action": "remove_character", "character_id": 41}.\n'
        '- {"action": "mark_dead", "character_id": 41, "cause": "a fall"}, '
        '{"action": "award_xp", "character_id": 41, "xp": 500} and '
        '{"action": "set_coin", "character_id": 41, "change": -20, "reason": '
        '"the ferry"} (or "purse": 150 for the total) are the Game Master\'s '
        "alone.\n"
        '- {"action": "record_house_rule", "topic": "fumbles", "rule": '
        '"a roll of 3 drops the weapon"} records how the '
        "Game Master says something works at his table, in his words. Ruling "
        "the same topic again replaces the earlier ruling; "
        '{"action": "retire_house_rule", "ruling_id": 3} drops one.\n'
        '- {"action": "move_party", "place": "the well beneath Farport"} '
        "records where the party is now.\n"
        '- {"action": "advance_time", "days": 1, "time_of_day": "evening"} '
        "moves the in-game clock; either field alone is fine. Times of day: "
        f"{times}.\n"
        '- {"action": "adjust_supplies", "item": "torches", "amount": "3"} '
        "records one thing the party carries between them, one action per "
        'thing; naming it again changes that one, and {"spent": true} drops '
        "it when the last is gone.\n"
        '- {"action": "record_npc", "name": "Mother Vane", "where": "the '
        'Farport shrine", "wants": "the box sealed", "condition": "wary of '
        'the party"} records somebody they met. Recording the same name '
        "again updates that record, so use it when an NPC changes.\n"
        '- {"action": "record_object", "name": "iron box", "where": "a ledge '
        'in the well", "condition": "chained to the rock"} does the same for '
        "a thing in play. Record it the moment its state changes, and take "
        "its state from the list rather than from memory: a thing is where "
        "the record says it is.\n"
        '- {"action": "open_thread", "text": "the Sleeper must not wake"} '
        "notes something left hanging -- a promise the party made, a debt "
        "they owe, a hook or a mystery. An obligation goes here and never "
        "inside somebody's record, where nothing can close it. "
        '{"action": "resolve_thread", "thread_id": 2, "resolution": "they '
        'sealed it"} closes one, by an id from the open threads above and '
        "never an invented one (issue #627).\n"
        '- {"action": "rules_lookup", "subject": "survival", "track": '
        f'"{example_track}"}} reads a rule out of the code and hands it back as a '
        "result, so you may then state the number. Subjects: "
        f"{subjects}. Use it whenever somebody asks what a rule is, "
        "including when they state one wrongly -- look it up and answer "
        "from the result rather than declining.\n"
        '- {"action": "ask_gm", "question": "Which track should Twain play?"} '
        "asks for a value the message does not give.\n"
        "A character_id is an id from the character list, or the name of a "
        "new_character created earlier in the same plan. The actions run in "
        "the order written, so record or roll attributes before the years "
        'that need them. When nothing needs doing, the plan is {"actions": []}.'
    )


@derived_from_rules
@functools.cache
def system_prompt_sections() -> tuple[str, ...]:
    """Every section the four prompts share, in the order they are sent."""
    return (
        SECTION_ROLE,
        SECTION_PHILOSOPHY,
        SECTION_NOT_DND,
        section_core(),
        section_character_creation(),
        SECTION_RECORDS,
        section_combat(),
        SECTION_MAGIC,
        section_equipment(),
        SECTION_WORLD,
        SECTION_GUIDELINES,
        SECTION_OUTCOMES,
        section_plan_schema(),
    )


def _prompt(*addenda: str) -> str:
    return "\n\n".join((*system_prompt_sections(), *addenda))


@derived_from_rules
@functools.cache
def system_prompt() -> str:
    """A player running a game."""
    return _prompt()


@derived_from_rules
@functools.cache
def aide_system_prompt() -> str:
    """A player running a game for friends, answered as an aide.

    The manner changes; the limits on what may be written down do not (issue
    #352).
    """
    return _prompt(SECTION_DM_AIDE)


@derived_from_rules
@functools.cache
def dm_play_system_prompt() -> str:
    """A DM or admin playing a game."""
    return _prompt(section_dm_authority())


@derived_from_rules
@functools.cache
def dm_system_prompt() -> str:
    """A DM or admin consulting the aide -- their default mode."""
    return _prompt(section_dm_authority(), SECTION_DM_AIDE)


@derived_from_rules
@functools.cache
def system_prompts_by_authority_and_mode() -> Mapping[tuple[bool, str], str]:
    """The 2x2 itself, by ``(is_game_master, mode)``.

    A mapping rather than a chain of conditionals so the four cases are
    visible at once and each names the builder it returns. Read-only, since
    every caller shares the one cached mapping.
    """
    return MappingProxyType(
        {
            (False, MODE_PLAY): system_prompt(),
            (False, MODE_AIDE): aide_system_prompt(),
            (True, MODE_PLAY): dm_play_system_prompt(),
            (True, MODE_AIDE): dm_system_prompt(),
        }
    )


def system_prompt_for(is_game_master: bool, mode: str | None = None) -> str:
    """Return the fixed prompt string for this table, on both axes.

    The same on a turn's plan and narrate calls, and from turn to turn, so the
    model's prefix cache holds (issue #590).

    Args:
        is_game_master: True when the signed-in user is a DM or admin. It is
            read from the session's user, never from anything typed into the
            chat, and it alone decides whether the Game Master's authority is
            granted (issue #352).
        mode: The session's mode, one of :data:`GM_MODES`. ``None`` -- and
            anything unrecognised -- takes this role's default.

    Returns:
        One of the four cached prompts in
        :func:`system_prompts_by_authority_and_mode`: the same string object
        for the life of the process, never a freshly built one.
    """
    chosen = normalise_mode(mode, is_game_master=is_game_master)
    return system_prompts_by_authority_and_mode()[(bool(is_game_master), chosen)]


def roll_dice(expression: str) -> tuple[list[int], int]:
    """Parse and roll a dice expression like '3d6' or '2d6+3'.

    Delegates to :func:`characters.dice.roll_expression`, which draws from the
    module-default :class:`common.rolling.Roller` — the project's one roll
    service and dice-notation parser (issues #105 and #175), shared with
    weapon damage rolls, with the same bounds and the same error messages.
    Neither ``characters.dice`` nor ``common.rolling`` imports Django, so this
    module's standalone-CLI contract survives.
    """
    return roll_expression(expression)


def roll_dice_record(expression: str, *, purpose: str) -> RollRecord:
    """Roll a dice expression and return the whole record (issue #363).

    The same one roll service :func:`roll_dice` uses, asked for the full
    :class:`common.rolling.RollRecord` instead of just the faces and the
    total, because a roll the GM asked for has to remember what it was for:
    the hidden entry it is filed as, and the dice line the player sees, both
    name the purpose. No second randomness source — this is
    ``characters.dice``'s module-default :class:`common.rolling.Roller`, so a
    seeded session replays these rolls exactly like every other.

    Args:
        expression: ``NdS`` or ``NdS±M``, e.g. ``"2d6"`` or ``"1d20+3"``.
        purpose: What the roll is for, recorded verbatim on the record.

    Returns:
        The completed :class:`common.rolling.RollRecord`.

    Raises:
        ValueError: If the expression does not parse or is out of bounds. The
            caller reports that to the player rather than inventing a number.
    """
    return roll_expression_record(expression, purpose=purpose)


def load_rules_from_dir(rules_dir: Path) -> str | None:
    """Load all markdown files under `rules_dir` into a single string.

    Shared by the web GM (gm/services.py, via settings.RULES_DIR) and the
    offline CLI (cli/gm.py, via DEFAULT_RULES_DIR) so there is one
    implementation of "how rules get loaded from disk". Returns None if the
    directory is missing or contains no markdown files.
    """
    if not rules_dir.exists():
        return None

    rules_files = sorted(rules_dir.rglob("*.md"))
    if not rules_files:
        return None

    parts: list[str] = []
    for path in rules_files:
        relative = path.relative_to(rules_dir)
        content = path.read_text(encoding="utf-8")
        parts.append(f"--- {relative} ---\n{content}")

    return "\n\n".join(parts)
