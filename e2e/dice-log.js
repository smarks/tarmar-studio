/**
 * The character's dice log as the server writes it, read from its own
 * constants and helpers (characters.dice, issue #147).
 *
 * The labels the sheet's rolls are kept under are declared once, in
 * characters/dice.py; the specs ask the server for them, and have it build
 * a line's shape, rather than typing either here. The pattern is
 * e2e/refusals.js's.
 */

const { readDjangoJson } = require('./django');

/**
 * One labelled roll, as the dice log keeps it.
 * @param {string} label The name of a label in characters.dice, e.g. 'SKILL_CHECK_LOG_LABEL'.
 * @param {Object<string, string>} fields What fills the label's {names}.
 * @param {string} dice The dice thrown, e.g. '2d6'.
 * @param {number[]} faces Every face thrown.
 * @param {number} total What they came to.
 * @returns {string}
 */
function diceLogLine(label, fields, dice, faces, total) {
    const call = JSON.stringify(JSON.stringify({ label, fields, dice, faces, total }));
    return readDjangoJson(`
import json
from characters import dice as dice_module
asked = json.loads(${call})
label = getattr(dice_module, asked['label']).format(**asked['fields'])
text = dice_module.dice_log_text(asked['dice'], asked['faces'], asked['total'])
print('DICE_LOG_LINE', json.dumps(dice_module.dice_log_line(label, text)))
`, 'DICE_LOG_LINE');
}

/**
 * One die of a sheet attack, as the dice log keeps it.
 * @param {string} weapon The weapon's name.
 * @param {string} part The name of the part in characters.dice, e.g. 'ATTACK_TO_HIT_PART'.
 * @param {string} dice The name of the dice in characters.dice, e.g. 'D20_DICE'.
 * @param {number} face The face thrown.
 * @returns {string}
 */
function attackLogLine(weapon, part, dice, face) {
    const call = JSON.stringify(JSON.stringify({ weapon, part, dice, face }));
    return readDjangoJson(`
import json
from characters import dice as dice_module
asked = json.loads(${call})
label = dice_module.ATTACK_LOG_LABEL.format(
    weapon=asked['weapon'], part=getattr(dice_module, asked['part'])
)
thrown = getattr(dice_module, asked['dice'])
text = dice_module.dice_log_text(thrown, [asked['face']], asked['face'])
print('ATTACK_LOG_LINE', json.dumps(dice_module.dice_log_line(label, text)))
`, 'ATTACK_LOG_LINE');
}

module.exports = { diceLogLine, attackLogLine };
