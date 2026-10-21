/**
 * The turn menu's words, read from the running settings (#90).
 *
 * The menu's sentences are configuration (config/battle-rulings.toml's
 * [turn_menu], [tarmar_menu] and [classic_menu] tables, and the cast stage's
 * rows in [cast_stage] (#145), loaded into
 * settings.BATTLE_RULINGS_WORDING at startup): Spencer edits them with no
 * Python and no spec, so the specs read them rather than typing a sentence
 * here. A spec matches a line as a pattern built from the sentences the menu
 * fills: each sentence's own words literally, and each {name} in it by the
 * pattern the spec gives for that name (an escaped figure's name, or another
 * sentence's pattern), or by anything when it gives none. The pattern is
 * e2e/rulings.js's.
 */

const { escaped } = require('./fixtures');
const { rulingWords } = require('./rulings');

/** The menu's tables once read, for every later call in this process. */
let readWords = null;

/**
 * The menu's tables: the words both profiles use, a Tarmar figure's own, a
 * classic figure's own, the turn panel's own around them (#94), and the cast
 * stage's rows (#145). Read through the Django shell on the first call
 * only: the settings load the words once at startup, so they cannot change
 * under a running suite.
 * @returns {{shared: Object<string, string>, tarmar: Object<string, string>, classic: Object<string, string>, panel: Object<string, string>, stage: Object<string, string>}}
 */
function menuWords() {
    if (readWords === null) {
        const {
            turn_menu: shared, tarmar_menu: tarmar, classic_menu: classic, turn_panel: panel,
            cast_stage: stage,
        } = rulingWords();
        readWords = { shared, tarmar, classic, panel, stage };
    }
    return readWords;
}

/**
 * A regular expression's source matching ``sentence`` filled. The loader
 * admits a sentence only bare {name} fields and the {{ and }} escapes
 * (battle/wording.py), so those are the only shapes read here.
 * @param {string} sentence - a configured sentence, e.g. "Charge {aimed}: ..."
 * @param {Object<string, string>} [fills] - a pattern's source for each name
 * @returns {string}
 */
function sentencePattern(sentence, fills = {}) {
    return sentence.split(/(\{\{|\}\}|\{\w+\})/).map((part) => {
        if (part === '{{') {
            return '\\{';
        }
        if (part === '}}') {
            return '\\}';
        }
        const field = part.match(/^\{(\w+)\}$/);
        if (field) {
            return fillFor(fills, field[1]);
        }
        return escaped(part);
    }).join('');
}

/**
 * The pattern given for ``name``, or one matching anything.
 * @param {Object<string, string>} fills
 * @param {string} name
 * @returns {string}
 */
function fillFor(fills, name) {
    return Object.hasOwn(fills, name) ? fills[name] : '.+';
}

/**
 * A whole line: ``sentence`` filled, and nothing else.
 * @param {string} sentence
 * @param {Object<string, string>} [fills]
 * @returns {RegExp}
 */
function menuLine(sentence, fills = {}) {
    return new RegExp(`^${sentencePattern(sentence, fills)}$`);
}

module.exports = { menuWords, sentencePattern, menuLine };
