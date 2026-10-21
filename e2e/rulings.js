/**
 * A Game Master's rulings' words, read from the running settings (#867, #37).
 *
 * The words are configuration (config/battle-rulings.toml, loaded into
 * settings.BATTLE_RULINGS_WORDING at startup): Spencer edits them with no
 * Python and no spec, so the specs read them, and have the server build the
 * log lines it would write, rather than typing a sentence here. The pattern
 * is e2e/gate-hint.spec.js's.
 */

const { readDjangoJson } = require('./django');

/**
 * Every table of the rulings' words: ``{common, hth_agreement, magic_bounds}``.
 * @returns {Object<string, Object<string, string>>}
 */
function rulingWords() {
    return readDjangoJson(`
import dataclasses, json
from battle.wording import words
print('RULING_WORDS', json.dumps(dataclasses.asdict(words())))
`, 'RULING_WORDS');
}

/**
 * The log line the server writes when a Game Master marks or lifts the
 * whole battle hand-to-hand by agreement.
 * @param {{agreed: boolean, username: string}} change
 * @returns {string}
 */
function hthWholeBattleLogLine({ agreed, username }) {
    return readDjangoJson(`
import json
from battle.agreement import change_message
from battle.wording import game_master_name
print('HTH_LOG_LINE', json.dumps(change_message(whole_battle=${agreed ? 'True' : 'False'}, pair=None, agreed=${agreed ? 'True' : 'False'}, names={}, by=game_master_name(${JSON.stringify(username)}))))
`, 'HTH_LOG_LINE');
}

/**
 * The log line the server writes when a Game Master lifts, or holds again,
 * bounds for a figure.
 * @param {{bounds: string[], name: string, lifted: boolean, username: string}} change
 * @returns {string}
 */
function magicBoundsLogLine({ bounds, name, lifted, username }) {
    return readDjangoJson(`
import json
from battle.magic_bounds import change_message
from battle.wording import game_master_name
print('BOUNDS_LOG_LINE', json.dumps(change_message(bounds=${JSON.stringify(bounds)}, lifted=${lifted ? 'True' : 'False'}, name=${JSON.stringify(name)}, by=game_master_name(${JSON.stringify(username)}))))
`, 'BOUNDS_LOG_LINE');
}

/**
 * The bounds the loaded canon imposes, which a Game Master may lift, in the
 * order the page offers them (battle.magic_bounds.offered_bounds).
 * @returns {string[]}
 */
function offeredBounds() {
    return readDjangoJson(`
import json
from battle.magic_bounds import offered_bounds
print('OFFERED_BOUNDS', json.dumps(list(offered_bounds())))
`, 'OFFERED_BOUNDS');
}

/**
 * The words for going past these lifted bounds, as the menu's cost line
 * carries them and as the battle log prints them beneath the line.
 * @param {string[]} bounds
 * @returns {{menu: string, log: string}}
 */
function pastByRuling(bounds) {
    return readDjangoJson(`
import json
from django.utils.text import capfirst
from battle.magic_bounds import past_by_ruling
past = past_by_ruling(${JSON.stringify(bounds)})
print('PAST_BY_RULING', json.dumps({'menu': past, 'log': capfirst(past)}))
`, 'PAST_BY_RULING');
}

/**
 * One of the battle's own lines (config/battle-rulings.toml, issue #136), as
 * the server would write it.
 * @param {string} table The file's table ("services", "control").
 * @param {string} key The sentence's key in that table.
 * @param {Object<string, string|number>} [fields] What fills its {names}.
 * @returns {string}
 */
function battleLine(table, key, fields = {}) {
    return readDjangoJson(`
import json
from battle.wording import words
sentence = getattr(getattr(words(), ${JSON.stringify(table)}), ${JSON.stringify(key)})
print('BATTLE_LINE', json.dumps(sentence.format(**json.loads(${JSON.stringify(JSON.stringify(fields))}))))
`, 'BATTLE_LINE');
}

/**
 * The runs of one of the battle's lines that stay the same whatever fills its
 * {names}, for a line the spec cannot fill in full.
 * @param {string} table
 * @param {string} key
 * @returns {string[]}
 */
function battleLineParts(table, key) {
    return readDjangoJson(`
import json, string
from battle.wording import words
sentence = getattr(getattr(words(), ${JSON.stringify(table)}), ${JSON.stringify(key)})
parts = [text.strip() for text, _name, _spec, _conversion in string.Formatter().parse(sentence)]
print('BATTLE_LINE_PARTS', json.dumps([part for part in parts if part]))
`, 'BATTLE_LINE_PARTS');
}

/**
 * How the battle log credits a player's choice (``[services] chosen_by``), up to
 * the option's own line: "chosen by alice —" for an owner, or the words before
 * the owner's name without one (issue #136).
 * @param {string} [owner]
 * @returns {string}
 */
function chosenBy(owner) {
    const cut = '\u0000';
    const filled = battleLine('services', 'chosen_by', {
        owner: owner === undefined ? cut : owner,
        rationale: cut,
    });
    return filled.split(cut)[0].trim();
}

/**
 * The roster card's line for the viewer's own manual figure.
 * @returns {string}
 */
function ownManualLine() {
    return battleLine('control', 'manual_line', { owner: battleLine('control', 'you') });
}

module.exports = {
    battleLine,
    chosenBy,
    ownManualLine,
    battleLineParts,
    rulingWords, hthWholeBattleLogLine, magicBoundsLogLine, offeredBounds, pastByRuling,
};
