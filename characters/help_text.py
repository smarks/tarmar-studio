"""Short help text for game-specific UI elements on the character sheet.

Rendered next to each labelled element by `characters/templates/characters/_help.html`
as a `<details>` disclosure so the panel is state-driven (persists until the
user closes it) and needs no JavaScript.

Keep entries short — one or two sentences. The wiki has the full rules.
"""

HELP_TEXT: dict[str, str] = {
    # Core attributes
    "strength": (
        "Physical power: carry capacity, melee damage, and strength checks. "
        "At creation the six core attributes are either rolled 3d6 in order or "
        "bought by dividing 65 points among them."
    ),
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
    "fatigue": (
        "Total stamina pool. Spent for spells, special maneuvers, and exhaustion. "
        "Formula: CON + WIS + INT + max(DEX,STR) + fatigue roll."
    ),
    "fatigue_roll": (
        "The 2d6 roll folded into Fatigue. Rolled once, by the server: roll it "
        "here, or in a Games Master session. Only a Game Master can have it "
        "rolled again."
    ),
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
    "wealth_level": (
        "Lifestyle tier you can sustain between adventures — rolled separately "
        "from Provenance. Sets starting coin: Subsistence 10, Moderate 100, "
        "Merchant 100 + d100, Rich by GM."
    ),
    "location": (
        "Where your character grew up. City grants Street Smarts; Village grants "
        "Street Smarts or Survival — your choice, rename the granted skill to "
        "switch; Rural grants Survival. Changing the origin moves the grant: "
        "the one point the old origin gave is taken back, points you bought "
        "stay, and the new origin's point is added. Roll or choose the origin "
        "before rolling literacy. The literacy check is "
        "the check to read at all, not the Literacy skill, and the origin's "
        "number is a penalty on it — added to the 3d6 roll, equivalently taken "
        "off INT, so letters are hardest to come by out in the country. Roll "
        "it once with Roll literacy; a Game Master may roll it again. Once "
        "it is rolled only a Game Master can change the origin, and doing so "
        "rolls literacy again against the new one. Consult GM has no check to "
        "roll: the Game Master rules whether the character can read, and that "
        "ruling fixes the origin the way a roll does."
    ),
    # Skills / experience
    "free_skill_points": (
        "Unspent points from earned experience. Allocate via the + button on "
        "any skill. There is no maximum level: each level costs one point more "
        "than the last (1, 3, 6, 10, 15, 21, ... points in total), and Arcane "
        "costs double all the way up."
    ),
    "free_spell_points": (
        "The Mana Pool: the Magic track's Spell Point results, plus any mana a "
        "Game Master grants. Skill points do not convert into it. Spells are "
        "learned by putting skill points into them (level I casts the spell); "
        "casting costs mana equal to the spell's level and rolls 3d6 ≤ INT or WIS."
    ),
    "skill_check": (
        "Roll 2d6 + the governing attribute's modifier + your skill level against "
        "the difficulty the GM sets. A natural 12 always succeeds, a natural 2 "
        "always fails; untrained, you add the attribute modifier alone. A check "
        "on an attribute with no skill is 3d6 ≤ the attribute instead."
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
    "encumbrance": (
        "Carry-weight tier based on STR. Unencumbered (≤STR), light (≤1.5×STR), "
        "medium (≤2×STR), heavy (≤2.5×STR), overloaded (above). Each tier "
        "applies movement penalties and restricts faster gaits."
    ),
    "movement": (
        "Hexes moved per gait: base distance (Sprint 18, Run 12, Jog 7, Walk 4) "
        "plus your movement modifier (CON + STR + DEX modifiers), minus any "
        "encumbrance movement penalty. A struck-through gait is forbidden by "
        "encumbrance or worn armor; Overloaded caps Walk at 1 hex regardless "
        "of the formula."
    ),
}
