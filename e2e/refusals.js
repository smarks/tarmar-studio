/**
 * The words the app refuses a request in, read from the running settings (#97).
 *
 * The refusals are configuration (config/battle-refusals.toml and
 * config/gm-refusals.toml, loaded into settings.BATTLE_REFUSAL_WORDS and
 * settings.GM_REFUSAL_WORDS at startup): Spencer edits them with no Python and
 * no spec, so the specs read them, and have the server fill a sentence's
 * {names}, rather than typing a sentence here. The pattern is e2e/rulings.js's.
 */

const { readDjangoJson } = require('./django');

/** The settings each app's refusal words are loaded into, by app. */
const REFUSAL_WORDS_MODULES = {
    battle: 'battle.refusal_words',
    gm: 'gm.refusal_words',
};

/**
 * One refusal, as the server would say it.
 * @param {'battle'|'gm'} app The app whose file holds it.
 * @param {string} table The file's table (one per module).
 * @param {string} key The sentence's key in that table.
 * @param {Object<string, string|number>} [fields] What fills its {names}.
 * @returns {string}
 */
function refusal(app, table, key, fields = {}) {
    return readDjangoJson(`
import json
from ${REFUSAL_WORDS_MODULES[app]} import refusal_words
sentence = getattr(getattr(refusal_words(), ${JSON.stringify(table)}), ${JSON.stringify(key)})
print('REFUSAL', json.dumps(sentence.format(**json.loads(${JSON.stringify(JSON.stringify(fields))}))))
`, 'REFUSAL');
}

/**
 * The words of a refusal that stay the same whatever fills its {names}, for a
 * page that shows the sentence filled with values the spec cannot know.
 * @param {'battle'|'gm'} app
 * @param {string} table
 * @param {string} key
 * @returns {string[]} The non-empty runs of text between its {names}.
 */
function refusalFixedParts(app, table, key) {
    return readDjangoJson(`
import json, string
from ${REFUSAL_WORDS_MODULES[app]} import refusal_words
sentence = getattr(getattr(refusal_words(), ${JSON.stringify(table)}), ${JSON.stringify(key)})
parts = [text.strip() for text, _name, _spec, _conversion in string.Formatter().parse(sentence)]
print('REFUSAL_PARTS', json.dumps([part for part in parts if part]))
`, 'REFUSAL_PARTS');
}

module.exports = { refusal, refusalFixedParts };
