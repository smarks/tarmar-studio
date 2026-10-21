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
