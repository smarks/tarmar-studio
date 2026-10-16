/**
 * The loaded canon's skills and experience rules, read where the server reads
 * them (issue #70).
 *
 * The specs that check the workbench, the skill ladder and the checks against
 * the canon read their numbers here instead of typing them, so they hold
 * under the default canon and the Tarmar canon alike. The shell inherits this
 * run's environment, TARMAR_CANON_DIR included, so it loads the canon the dev
 * server loaded.
 */

const { readDjangoShell } = require('./django');

/**
 * Every number the specs check, from ``characters.skill_rules.skill_rules()``.
 *
 * ``governed_skills`` are the skills a check may be rolled on, sorted.
 * ``natural_success_seed`` is the first seed whose pinned dice (e2e/dice.js)
 * make the skill check's natural success, found by rolling the check's dice
 * the way the server does inside ``common.rolling.pinned_dice``.
 * @returns {object}
 */
function loadedSkillRules() {
    const output = readDjangoShell(`
import json
from characters.dice import roll_dice_faces
from characters.rules import character_rules
from characters.skill_checks import MAX_DIFFICULTY
from characters.skill_rules import AGING_ATTRIBUTES, PHYSICAL_AGING_ATTRIBUTES, skill_rules
from characters.skills import ARCANE_SKILL_NAME, RANDOM_TRACK_KEY, RANDOM_TRACK_LABEL
from characters.views.characters import AGING_PREVIEW_HORIZON_STAGE
from common.rolling import pinned_dice
rules = skill_rules()
age = character_rules().age
checks = rules.skill_check
def natural_success_seed():
    for seed in range(10000):
        with pinned_dice(seed):
            _faces, total = roll_dice_faces(checks.dice)
        if total == checks.natural_success:
            return seed
    raise SystemExit("no seed makes the natural success")
print("SKILL_RULES " + json.dumps({
    "random": {"key": RANDOM_TRACK_KEY, "label": RANDOM_TRACK_LABEL},
    "tracks": [
        {
            "key": key,
            "label": track.label,
            "xp_per_year": track.xp_per_year,
            "survival_target": track.survival_target,
            "skills": list(track.skills),
        }
        for key, track in rules.tracks.tracks.items()
    ],
    "survival_roll": {"dice": rules.survival_roll.dice},
    "arcane": {
        "name": ARCANE_SKILL_NAME,
        "multiplier": rules.skill_cost.multiplier_for(ARCANE_SKILL_NAME),
    },
    "aging": {
        "starting_age": age.starting,
        "years_per_stage": age.years_per_stage,
        "first_stage_with_penalties": rules.aging.first_stage_with_penalties,
        "stages_per_mental_step": rules.aging.stages_per_mental_step,
        "preview_horizon_stage": AGING_PREVIEW_HORIZON_STAGE,
        "order": list(AGING_ATTRIBUTES),
        "physical": list(PHYSICAL_AGING_ATTRIBUTES),
        "stages": [
            {"stage": row.stage, "penalties": row.penalties} for row in rules.aging.stages
        ],
    },
    "skill_check": {
        "dice": checks.dice,
        "natural_success": checks.natural_success,
        "governed_skills": sorted(
            name for name, attribute in checks.governing_attributes.items() if attribute
        ),
        "natural_success_seed": natural_success_seed(),
        "max_difficulty": MAX_DIFFICULTY,
    },
    "attribute_check": {
        "dice": rules.attribute_check.dice,
        "minimum": rules.attribute_check.minimum,
        "maximum": rules.attribute_check.maximum,
        "modifier_limit": rules.attribute_check.game_master_modifier_limit,
    },
}))
`);
    const line = output.split('\n').find((text) => text.startsWith('SKILL_RULES '));
    return JSON.parse(line.slice('SKILL_RULES '.length));
}

/**
 * The cumulative aging penalties at ``stage``, as
 * ``characters.skill_rules.AgingRules.penalties_for`` gives them: none before
 * the first penalised stage, the table's row within it, and past its last row
 * each attribute falling from its own last value, physical ones a point a
 * stage and mental ones a point every ``stages_per_mental_step`` stages.
 * @param {object} aging - ``loadedSkillRules().aging``.
 * @param {number} stage
 * @returns {object | null} penalties by attribute field name
 */
function agingPenaltiesAt(aging, stage) {
    if (stage < aging.first_stage_with_penalties) return null;
    const last = aging.stages[aging.stages.length - 1];
    const row = aging.stages.find((tabulated) => tabulated.stage === stage);
    if (row) return row.penalties;
    const past = stage - last.stage;
    const mentalSteps = Math.floor(past / aging.stages_per_mental_step);
    return Object.fromEntries(aging.order.map((attribute) => [
        attribute,
        last.penalties[attribute] - (aging.physical.includes(attribute) ? past : mentalSteps),
    ]));
}

/**
 * The next aging change for a character of ``age``, as the workbench says it:
 * the first later stage whose totals differ, its non-zero penalties in the
 * table's order, e.g. "STR -1, DEX -1 at age 36".
 * @param {object} aging - ``loadedSkillRules().aging``.
 * @param {number} age
 * @returns {{stage: number, text: string} | null} the stage it lands at, and
 *     the preview's words
 */
function nextAgingChange(aging, age) {
    const stageAt = (years) => Math.floor((years - aging.starting_age) / aging.years_per_stage);
    const current = JSON.stringify(agingPenaltiesAt(aging, stageAt(age)) || {});
    for (let stage = stageAt(age) + 1; stage <= aging.preview_horizon_stage; stage += 1) {
        const totals = agingPenaltiesAt(aging, stage);
        if (!totals || JSON.stringify(totals) === current) continue;
        const said = aging.order
            .filter((attribute) => totals[attribute] !== 0)
            .map((attribute) => `${attribute.slice(0, 3).toUpperCase()} ${totals[attribute]}`);
        if (said.length) {
            const landsAt = aging.starting_age + aging.years_per_stage * stage;
            return { stage, text: `${said.join(', ')} at age ${landsAt}` };
        }
    }
    return null;
}

module.exports = { loadedSkillRules, agingPenaltiesAt, nextAgingChange };
