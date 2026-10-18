"""Short help text for game-specific UI elements on the character sheet.

Rendered next to each labelled element by `characters/templates/characters/_help.html`
as a `<details>` disclosure so the panel is state-driven (persists until the
user closes it) and needs no JavaScript.

Keep entries short — one or two sentences. The wiki has the full rules.

Every sentence that states a number a canon rules file holds is built from the
loaded rules when the help is first asked for (issue #68), never at import.
Left as written, because no rules file holds them: the injury bands and the
survival save, the casting dice, Body's 2/3, Physical and Mental's halves, the
Appearance roll, the skill cost ladder (1, 3, 6, ...), and the aim and dodge
formulas, which are the engine's (stages 6 and 7).
"""

import functools
from collections.abc import Mapping
from types import MappingProxyType

from characters.equipment_rules import equipment_rules
from characters.rules import character_rules
from characters.skill_rules import skill_rules
from characters.skills import ARCANE_SKILL_NAME
from common.number_words import number_in_words
from reference.rules_file import derived_from_rules

#: What a skill cost multiple is called in prose; any other multiple is "N×".
_COST_MULTIPLE_WORDS = {2: "double", 3: "triple"}


#: The entries that state no number a rules file holds, as written.
_WRITTEN_HELP: Mapping[str, str] = MappingProxyType(
    {
        # Core attributes
        "dexterity": (
            "Reflexes and coordination: ranged to-hit, dodge, and dexterity checks. "
            "Armor reduces effective DEX (see Armor DEX Penalty)."
        ),
        "intelligence": (
            "Reasoning and recall: language learning, spell capacity, and INT checks. "
            "Many crafts require an INT threshold to attempt."
        ),
        "wisdom": (
            "Perception and judgment: notice rolls, willpower, and WIS checks. "
            "WIS contributes to your Fatigue pool."
        ),
        "constitution": (
            "Endurance and toughness: hit points (Body), poison resistance, "
            "long-march stamina. CON contributes to your Fatigue pool."
        ),
        "charisma": (
            "Social presence and persuasion: NPC reactions, leadership rolls, "
            "and reaction-table modifiers."
        ),
        # Derived
        "body": (
            "Hit-point pool that absorbs physical damage. 2/3 of Fatigue, rounded up. "
            "At zero Body you are unconscious; dying starts once you pass minus half "
            "your starting Body (see Injury Thresholds)."
        ),
        "injury_thresholds": (
            "Breakpoints for either pool as it drops: -1 to all rolls at or below "
            "half its starting value, -2 from 5 down to 1, unconscious at 0, a "
            "survival save (3d6 ≤ CON) every turn past minus half, and penalized "
            "saves past minus the full starting value. Whichever pool is worse "
            "governs. The line above the table shows where your pools stand now."
        ),
        "survival_save": (
            "Roll 3d6 each turn against CON. Past minus your full starting pool the "
            "roll is penalized by how far past that mark you are, so at -35 with a "
            "starting 30 you need 3d6 ≤ CON - 5. Saves continue every turn until "
            "you are healed above the threshold or you fail one and die."
        ),
        "physical": (
            "Composite of bodily prowess. Half the sum of effective STR, DEX, and "
            "CON, rounded up."
        ),
        "mental": (
            "Composite of mental acuity. Half the sum of effective WIS, INT, and "
            "CHA, rounded up."
        ),
        # Physical
        "appearance": (
            "Roll 1d6: 1 is pretty, 6 is ugly, 2-5 unremarkable. A 1 or 6 stands "
            "as rolled — ask the GM to further qualify what it means."
        ),
        # Provenance / wealth
        "provenance_class": (
            "Social class and title from the Provenance roll (Nobility, Merchant, "
            "or Commoner, plus a rolled detail). Descriptive only — the app wires "
            "no starting equipment, coin, or roll modifiers to it; see the "
            "Provenance rules page."
        ),
        # Skills / experience
        "free_spell_points": (
            "The Mana Pool: the Magic track's Spell Point results, plus any mana a "
            "Game Master grants. Skill points do not convert into it. Spells are "
            "learned by putting skill points into them (level I casts the spell); "
            "casting costs mana equal to the spell's level and rolls 3d6 ≤ INT or "
            "WIS."
        ),
        "experience_term": (
            "Years of past life lived before play. Each year picks a track (Merchant, "
            "Laborer, Magic, etc.), rolls a skill and a survival check, and grants XP."
        ),
        "total_xp": (
            "Lifetime experience. Drives your level. Earned in play and prior "
            "experience; only a Game Master can change it by hand."
        ),
        # Combat / equipment
        "armor_stops": (
            "Damage reduction subtracted from each hit before it touches your Body. "
            "Summed across your worn armor and the shield in use: one shield, "
            "carried outside the pack, and none beside a two-handed weapon."
        ),
        "armor_dex_penalty": (
            "Negative modifier applied to all DEX checks. Summed across your "
            "worn armor and the shield in use (one shield, carried outside the "
            "pack). Heavier gear = larger penalty."
        ),
        "weapon_to_hit": (
            "The to-hit modifier of the weapon in hand: the one you ready in the "
            "equipment list, else the first carried outside the pack. A swing "
            "gets its own weapon's modifier, never a sum of several."
        ),
        "dex_combat_mod": (
            "Your aim: floor((combat DEX - 10) / 2), added to every attack d20. "
            "Combat DEX is your DEX after aging penalties and after the Armor DEX "
            "Penalty of your worn armor and shield, so heavy gear drags your aim "
            "down as well as protecting you."
        ),
        "dodge_modifier": (
            "What you add to an attacker's Target Number when someone swings at "
            "you — the same combat DEX modifier as your DEX Combat Mod (aging and "
            "armor included), floored at zero, so a clumsy or heavily armored "
            "character never makes themselves easier to hit."
        ),
        "attack_resolution": (
            "Rolls one d20 attack on the server and shows the whole breakdown: "
            "Target Number (weapon class vs the defender's armor, plus shield and "
            "dodge), your to-hit total, the roll, and damage after stops. A natural "
            "20 rolls to confirm a severe critical; a natural 1 rolls the fumble "
            "subtable. The four 'GM supplies' fields describe your target — ask the "
            "GM for them. The skill added is decided from the weapon: a Weapon skill "
            "gives its full level with the weapon it was taken for and half with "
            "another of the same class; Archery covers bows and crossbows. The "
            "to-hit and damage "
            "modifiers used are the selected weapon's own, not the inventory-wide "
            "sums the tiles above show."
        ),
    }
)


def _strength_help() -> str:
    """STR's help: what it does, and how the six are made at creation."""
    rules = character_rules()
    return (
        "Physical power: carry capacity, melee damage, and strength checks. "
        "At creation the six core attributes are either rolled "
        f"{rules.attribute_roll.dice} in order or bought by dividing "
        f"{rules.attributes.point_buy.total} points among them."
    )


def _fatigue_help() -> str:
    """Fatigue's help, with the canon's sum."""
    return (
        "Total stamina pool. Spent for spells, special maneuvers, and exhaustion. "
        f"Formula: {character_rules().fatigue_roll.formula('fatigue roll')}."
    )


def _fatigue_roll_help() -> str:
    """The Fatigue roll's help, with the canon's dice."""
    return (
        f"The {character_rules().fatigue_roll.dice} roll folded into Fatigue. "
        "Rolled once, by the server: roll it here, or in a Games Master "
        "session. Only a Game Master can have it rolled again."
    )


def _wealth_level_help() -> str:
    """Wealth's help, with each level's starting coin from the canon."""
    coins = ", ".join(
        f"{row.label} {row.coin_text}" for row in character_rules().wealth.rows
    )
    return (
        "Lifestyle tier you can sustain between adventures — rolled separately "
        f"from Provenance. Sets starting coin: {coins}."
    )


def _origin_grant_moves() -> str:
    """What changing the origin does to its granted skill's points."""
    points = character_rules().location.skill_points
    if points == 1:
        return (
            "the one point the old origin gave is taken back, points you bought "
            "stay, and the new origin's point is added"
        )
    spelled = number_in_words(points)
    return (
        f"the {spelled} points the old origin gave are taken back, points you "
        f"bought stay, and the new origin's {spelled} are added"
    )


def _location_help() -> str:
    """The origin's help, with the grant's points and the literacy dice."""
    return (
        "Where your character grew up. City grants Street Smarts; Village grants "
        "Street Smarts or Survival — your choice, rename the granted skill to "
        "switch; Rural grants Survival. Changing the origin moves the grant: "
        f"{_origin_grant_moves()}. Roll or choose the origin "
        "before rolling literacy; a rolled origin is final, and only a Game "
        "Master can change it. The literacy check is "
        "the check to read at all, not the Literacy skill, and the origin's "
        "number is a penalty on it — added to the "
        f"{character_rules().literacy_roll.dice} roll, equivalently taken "
        "off INT, so letters are hardest to come by out in the country. Roll "
        "it once with Roll literacy; a Game Master may roll it again. Once "
        "it is rolled only a Game Master can change the origin, and doing so "
        "rolls literacy again against the new one. Consult GM has no check to "
        "roll: the Game Master rules whether the character can read, and that "
        "ruling fixes the origin the way a roll does."
    )


def _free_skill_points_help() -> str:
    """Free points' help, with Arcane's cost multiple from the canon."""
    multiple = skill_rules().skill_cost.multiplier_for(ARCANE_SKILL_NAME)
    arcane = (
        ""
        if multiple == 1
        else (
            ", and Arcane costs "
            f"{_COST_MULTIPLE_WORDS.get(multiple, f'{multiple}×')} all the way up"
        )
    )
    return (
        "Unspent points from earned experience. Allocate via the + button on "
        "any skill. There is no maximum level: each level costs one point more "
        f"than the last (1, 3, 6, 10, 15, 21, ... points in total){arcane}."
    )


def _skill_check_help() -> str:
    """The skill check's help, with the canon's dice and naturals."""
    rules = skill_rules()
    check = rules.skill_check
    return (
        f"Roll {check.dice} + the governing attribute's modifier + your skill "
        f"level against the difficulty the GM sets. A natural "
        f"{check.natural_success} always succeeds, a natural "
        f"{check.natural_failure} always fails; untrained, you add the attribute "
        "modifier alone. A check on an attribute with no skill is "
        f"{rules.attribute_check.dice} ≤ the attribute instead."
    )


def _load_bound(multiplier: float | None) -> str:
    """A load level's top: "≤STR", "≤1.5×STR", or "above" for the last."""
    if multiplier is None:
        return "above"
    if multiplier == 1:
        return "≤STR"
    return f"≤{multiplier:g}×STR"


def _encumbrance_help() -> str:
    """Encumbrance's help, with every level and its multiple of STR."""
    levels = ", ".join(
        f"{level.key} ({_load_bound(level.max_multiplier)})"
        for level in equipment_rules().encumbrance.levels
    )
    return (
        f"Carry-weight tier based on STR. {levels[:1].upper()}{levels[1:]}. Each "
        "tier applies movement penalties and restricts faster gaits."
    )


def _movement_help() -> str:
    """Movement's help, with each gait's base distance and any Walk cap."""
    rules = equipment_rules()
    gaits = rules.gaits
    distances = ", ".join(
        f"{gait.title()} {gaits.base_distances[gait]}" for gait in gaits.order
    )
    caps = "".join(
        f"; {level.key.title()} caps {gaits.slowest.title()} at "
        f"{level.walk_limit} hex regardless of the formula"
        for level in rules.encumbrance.levels
        if level.walk_limit is not None
    )
    return (
        f"Hexes moved per gait: base distance ({distances}) "
        "plus your movement modifier (CON + STR + DEX modifiers), minus any "
        "encumbrance movement penalty. A struck-through gait is forbidden by "
        f"encumbrance or worn armor{caps}."
    )


@derived_from_rules
@functools.cache
def help_text() -> Mapping[str, str]:
    """Every help entry by key, built from the loaded rules on first use.

    The written entries and the ones built from the rules together. Cached,
    so the sheet renders the same text for the life of the process, and
    cleared with the other values derived from the rules when a canon is
    seated (issue #68).
    """
    return MappingProxyType(
        {
            **_WRITTEN_HELP,
            "strength": _strength_help(),
            "fatigue": _fatigue_help(),
            "fatigue_roll": _fatigue_roll_help(),
            "wealth_level": _wealth_level_help(),
            "location": _location_help(),
            "free_skill_points": _free_skill_points_help(),
            "skill_check": _skill_check_help(),
            "encumbrance": _encumbrance_help(),
            "movement": _movement_help(),
        }
    )
