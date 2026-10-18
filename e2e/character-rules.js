/**
 * The loaded canon's character creation rules, read where the server reads
 * them (issue #68).
 *
 * The specs that check the sentences the sheet prints about creation (the
 * dice, the point buy, the Fatigue sum, the base weight, the aging rate)
 * read their numbers here instead of typing them, so they hold under the
 * default canon and the Tarmar canon alike. The shell inherits this run's
 * environment, TARMAR_CANON_DIR included, so it loads the canon the dev
 * server loaded.
 */

const { readDjangoJson } = require('./django');

// The loaded rules, read the first time a spec asks.
let loaded = null;

/**
 * Every creation number the sheet's sentences state, from
 * ``characters.rules.character_rules()`` and, for the aging rate,
 * ``characters.skill_rules.skill_rules()``.
 *
 * ``fatigue.formula`` and ``fatigue.help_formula`` are the sum as the sheet's
 * formula cell and its help write it
 * (``characters.rules.FatigueRollRules.formula``).
 * ``aging.stage_years_words`` and ``aging.mental_years_words`` are the rate's
 * years as prose spells them (``common.number_words.number_in_words``), and
 * ``aging.horizon_age`` the age the workbench preview stops at, and
 * ``aging.physical`` and ``aging.mental`` the short names of the attributes
 * aging lowers every stage and every few stages.
 * @returns {object}
 */
function loadedCharacterRules() {
    if (loaded) return loaded;
    loaded = readDjangoJson(`
import json
from characters.constants import CORE_ATTRIBUTE_FIELDS
from characters.rules import character_rules
from characters.skill_rules import MENTAL_AGING_ATTRIBUTES, PHYSICAL_AGING_ATTRIBUTES, skill_rules
from characters.views.characters import aging_preview_horizon_age
from common.number_words import number_in_words
rules = character_rules()
stage_years = rules.age.years_per_stage
mental_years = stage_years * skill_rules().aging.stages_per_mental_step
print("CHARACTER_RULES " + json.dumps({
    "attribute_dice": rules.attribute_roll.dice,
    "point_buy": {
        "total": rules.attributes.point_buy.total,
        "minimum": rules.attributes.point_buy.minimum,
        "maximum": rules.attributes.point_buy.maximum,
    },
    "fatigue": {
        "dice": rules.fatigue_roll.dice,
        "formula": rules.fatigue_roll.formula("roll"),
        "help_formula": rules.fatigue_roll.formula("fatigue roll"),
    },
    "literacy_dice": rules.literacy_roll.dice,
    "aging": {
        "horizon_age": aging_preview_horizon_age(),
        "stage_years_words": number_in_words(stage_years),
        "mental_years_words": number_in_words(mental_years),
        "physical": [dict(CORE_ATTRIBUTE_FIELDS)[name] for name in PHYSICAL_AGING_ATTRIBUTES],
        "mental": [dict(CORE_ATTRIBUTE_FIELDS)[name] for name in MENTAL_AGING_ATTRIBUTES],
    },
}))
`, 'CHARACTER_RULES');
    return loaded;
}

module.exports = { loadedCharacterRules };
