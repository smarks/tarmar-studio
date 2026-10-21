/**
 * The loaded canon's school of magic table, read where the server reads it
 * (issue #18).
 *
 * A spec that pins the school roll's face asks the server which row the face
 * lands on and which of the row's schools the battle has spells for, so it
 * types none of the canon's numbers or names and holds under any canon.
 */

const { readDjangoJson } = require('./django');

/**
 * The row of the loaded canon's school table a percentile roll lands on.
 *
 * Read through the server's own lookup (``SchoolTable.row_for``), with each
 * of the row's schools mapped to the keys of the battle's spells filed under
 * it (``characters.magic.learnable_spells``), in catalog order.
 * @param {number} face - A d100 result, 1 to 100.
 * @returns {{label: string, schools: string[], spells: Object<string, string[]>} | null}
 *     The row, or null under a canon whose ``schools`` block is null, which
 *     gates no spell by school and has no table to roll on.
 */
function schoolRowForPercentile(face) {
    return readDjangoJson(`
import json
from battle.magic_rules import magic_schools
from characters.magic import learnable_spells
table = magic_schools()
row = None if table is None else table.row_for(${Number(face)})
print("SCHOOL_ROW " + json.dumps(None if row is None else {
    "label": row.label,
    "schools": list(row.schools),
    "spells": {
        school: [spell.key for spell in learnable_spells() if spell.school == school]
        for school in row.schools
    },
}))
`, 'SCHOOL_ROW');
}

module.exports = { schoolRowForPercentile };

/**
 * The battle's spells as the server's catalog holds them, in catalog order
 * (``characters.magic.learnable_spells``): each one's key and whether it must
 * hit, so a spec picks a spell without typing the catalog (issue #18).
 * @returns {{key: string, targeted: boolean}[]}
 */
function catalogSpells() {
    return readDjangoJson(`
import json
from characters.magic import learnable_spells
print("SPELLS " + json.dumps([
    {"key": spell.key, "targeted": spell.targeted} for spell in learnable_spells()
]))
`, 'SPELLS');
}

/**
 * What the record holds after a cast in the story (issue #18): the newest
 * history line under the cast's field and the Mana in hand.
 * @param {string | number} characterId
 * @returns {{line: string, current_mana: number}}
 */
function lastStoryCast(characterId) {
    return readDjangoJson(`
import json
from characters.models import Character
from characters.views.mechanics import CAST_HISTORY_FIELD
character = Character.objects.get(pk=${Number(characterId)})
lines = [entry["new"] for entry in character.history if entry["field"] == CAST_HISTORY_FIELD]
print("CAST " + json.dumps({"line": lines[-1], "current_mana": character.current_mana}))
`, 'CAST');
}

module.exports.catalogSpells = catalogSpells;
module.exports.lastStoryCast = lastStoryCast;
