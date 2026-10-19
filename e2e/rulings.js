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

module.exports = {
    rulingWords, hthWholeBattleLogLine, magicBoundsLogLine, offeredBounds, pastByRuling,
};
