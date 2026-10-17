/**
 * The loaded canon's equipment catalog and equipment rules, and what the
 * server makes of them for a character, read where the server reads them
 * (issue #75).
 *
 * The specs that check the sheet's catalog picker, its load and movement, and
 * the attack panel choose their items and scenarios from the loaded files
 * here, and take every value the page should show from the server for the
 * character in question, so they type no catalog value or rule number and
 * restate none of the server's arithmetic: that the server's arithmetic
 * follows the rules is the Python drift and seated tests' business. The shell
 * inherits this run's environment, TARMAR_CANON_DIR included, so it loads the
 * canon the dev server loaded.
 */

const { readDjangoJson } = require('./django');

// The loaded files, read the first time a spec asks.
let loaded = null;

/**
 * The loaded catalog and rules, for choosing what a spec seeds and picks.
 *
 * ``groups`` are the picker's groups in order, each with its label and its
 * items as the file holds them (``id`` added); ``gait_order`` the gaits,
 * fastest first; ``armour_restriction_codes`` each code's forbidden gaits;
 * ``missile_classes`` the Missile classes; ``defender_dodge`` the dodge
 * bounds. Read once per worker: the canon does not change under a running
 * server.
 * @returns {object}
 */
function loadedEquipment() {
    if (loaded) return loaded;
    loaded = readDjangoJson(`
import json
from characters.equipment_rules import CATALOG_GROUPS, equipment_catalog, equipment_rules
catalog = equipment_catalog()
rules = equipment_rules()
labels = dict(CATALOG_GROUPS)
print("EQUIPMENT " + json.dumps({
    "groups": [
        {
            "key": group,
            "label": labels[group],
            "items": [dict(item.as_dict(), id=identifier) for identifier, item in items.items()],
        }
        for group, items in catalog.groups.items()
    ],
    "gait_order": list(rules.gaits.order),
    "armour_restriction_codes": {
        code: list(gaits) for code, gaits in rules.armour_restriction_codes.items()
    },
    "missile_classes": sorted(rules.missile_classes),
    "defender_dodge": {
        "minimum": rules.defender_dodge.minimum,
        "maximum": rules.defender_dodge.maximum,
    },
}))
`, 'EQUIPMENT');
    return loaded;
}

/**
 * The loaded group ``key``.
 * @param {string} key
 * @returns {object}
 */
function group(key) {
    return loadedEquipment().groups.find((candidate) => candidate.key === key);
}

/**
 * The loaded item ``id``, whatever its group.
 * @param {string} id
 * @returns {object}
 */
function item(id) {
    for (const candidate of loadedEquipment().groups) {
        const found = candidate.items.find((entry) => entry.id === id);
        if (found) return found;
    }
    throw new Error(`the loaded catalog has no item ${id}`);
}

/**
 * A load that costs movement: the heaviest item the canon sells outside the
 * armour (worn armour adds its own restriction), and the least STR, from the
 * attribute floor to the canon's ceiling, at which carrying it puts a
 * character at a level with a penalty and a forbidden gait that is not the
 * heaviest level. The server's own ``level_for`` chooses; nothing here
 * restates it.
 * @returns {{item: object, strength: number}}
 */
function movementCostingLoad() {
    return readDjangoJson(`
import json
from characters.equipment_rules import equipment_catalog, equipment_rules
from characters.models import MIN_ATTRIBUTE_VALUE
from characters.rules import character_rules
catalog = equipment_catalog()
encumbrance = equipment_rules().encumbrance
candidates = [
    (group, identifier, item)
    for group, items in catalog.groups.items()
    if group != "armor"
    for identifier, item in items.items()
]
group, identifier, heaviest = max(candidates, key=lambda candidate: candidate[2].weight)
def costs_movement(strength):
    level = next(
        level for level in encumbrance.levels
        if level.key == encumbrance.level_for(heaviest.weight, strength)
    )
    return level is not encumbrance.heaviest and level.penalty and level.forbidden_gaits
strength = next(
    (
        score
        for score in range(MIN_ATTRIBUTE_VALUE, character_rules().attributes.maximum + 1)
        if costs_movement(score)
    ),
    None,
)
print("LOAD " + json.dumps({
    "item": dict(heaviest.as_dict(), id=identifier, group=group),
    "strength": strength,
}))
`, 'LOAD');
}

/**
 * What the server says the sheet shows for character ``characterId`` as it
 * is stored now: the load, the level's wording, the penalty, the restriction
 * label, the movement modifier, each gait's readout, the carrying capacity and
 * the purse.
 * @param {string | number} characterId
 * @returns {object}
 */
function sheetValues(characterId) {
    return readDjangoJson(`
import json
from characters.models import Character
from characters.purse import format_purse
character = Character.objects.get(pk=${Number(characterId)})
print("SHEET " + json.dumps({
    "carried_weight": character.carried_weight,
    "encumbrance_label": character.encumbrance_label,
    "movement_penalty": character.movement_penalty,
    "movement_restriction_label": character.movement_restriction_label,
    "movement_modifier": character.movement_modifier,
    "gait_readout": character.gait_readout,
    "lightest_load_limit": character.lightest_load_limit,
    "purse": str(character.purse),
    "purse_text": format_purse(character),
}))
`, 'SHEET');
}

/**
 * The server's Target Number for ``weaponId`` against ``armourTier`` with the
 * shield ``shieldId`` and ``dodge``, and the matrix's base for it.
 * @param {string} weaponId
 * @param {string} armourTier
 * @param {string} shieldId
 * @param {number} dodge
 * @returns {{base: number, shield: number, total: number}}
 */
function targetNumber(weaponId, armourTier, shieldId, dodge) {
    return readDjangoJson(`
import json
from characters import combat
from characters.equipment import get_shield_bonus, get_weapon_class
weapon_class = get_weapon_class(${JSON.stringify(weaponId)})
shield = get_shield_bonus(${JSON.stringify(shieldId)})
tier = ${JSON.stringify(armourTier)}
print("TARGET " + json.dumps({
    "base": combat.MATRIX[weapon_class][tier],
    "shield": shield,
    "total": combat.target_number(weapon_class, tier, shield_bonus=shield, defender_dodge=${Number(dodge)}),
}))
`, 'TARGET');
}

module.exports = {
    loadedEquipment, group, item, movementCostingLoad, sheetValues, targetNumber,
};
