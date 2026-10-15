/**
 * E2E tests for the character creation page (issue #123).
 *
 * Covers the chooser, the roll path, and both point-buy outcomes in a real
 * browser: the client-side tally that gates the submit button, and the
 * server-side validation that is the authoritative check.
 *
 * The last block reads the loaded canon's character rules (issue #59) through
 * the Django shell and holds the page and a rolled character to them, so it
 * runs unchanged under either canon. The blocks above it type the default
 * canon's numbers (the 65-point buy, its 3 to 18, the 3d6 in the log), which
 * the Tarmar canon's file states too today.
 */

const { test, expect, loginAs, TEST_USER, TEST_ADMIN } = require('./fixtures');
const { readDjangoShell } = require('./django');

const CREATE_URL = '/tarmar/characters/create/';

// Distinctive legal buy: 18 + 3 + 12 + 12 + 10 + 10 === 65.
const VALID_BUY = {
    strength: '18',
    dexterity: '3',
    intelligence: '12',
    wisdom: '12',
    constitution: '10',
    charisma: '10'
};

/**
 * Fill every core attribute field on the point-buy form.
 * @param {import('@playwright/test').Page} page
 * @param {Record<string, string>} values
 */
async function fillBuy(page, values) {
    for (const [field, value] of Object.entries(values)) {
        await page.fill(`#point-buy-${field}`, value);
    }
}

test.describe('Character Creation Chooser', () => {
    test.beforeEach(async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto(CREATE_URL);
    });

    test('offers both ways to determine the core attributes', async ({ page }) => {
        await expect(page.locator('h1')).toContainText('Create a Character');

        // Option one: roll.
        await expect(page.locator('button:has-text("Roll a New Character")')).toBeVisible();

        // Option two: divide 65 points, one field per core attribute.
        await expect(page.locator('#point-buy-form')).toBeVisible();
        await expect(page.locator('.point-buy-input')).toHaveCount(6);
        for (const field of Object.keys(VALID_BUY)) {
            await expect(page.locator(`#point-buy-${field}`)).toBeVisible();
        }
    });

    test('prefills a legal buy, so the tally starts at zero remaining', async ({ page }) => {
        await expect(page.locator('#point-buy-spent')).toHaveText('65');
        await expect(page.locator('#point-buy-remaining')).toHaveText('0');
        await expect(page.locator('#point-buy-submit')).toBeEnabled();
    });

    test('disables the submit button while the buy is illegal', async ({ page }) => {
        // Leaves the six in range but 7 points short of the budget.
        await page.fill('#point-buy-charisma', '3');

        await expect(page.locator('#point-buy-remaining')).toHaveText('7');
        await expect(page.locator('#point-buy-tally')).toHaveClass(/under/);
        await expect(page.locator('#point-buy-submit')).toBeDisabled();

        // And it comes back once the buy balances again.
        await page.fill('#point-buy-charisma', '10');
        await expect(page.locator('#point-buy-remaining')).toHaveText('0');
        await expect(page.locator('#point-buy-submit')).toBeEnabled();
    });
});

test.describe('Rolling a Character', () => {
    test('creates a character and lands on its sheet', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto(CREATE_URL);

        await page.fill('#roll-name', 'Rolled Hero');
        await page.click('button:has-text("Roll a New Character")');

        await expect(page).toHaveURL(/\/characters\/\d+\//);
        await expect(page.locator('h1')).toBeVisible();
        // The rolled path still logs its 3d6 chains, not a point-buy.
        await expect(page.locator('.dice-log pre')).toContainText('3d6');
    });
});

test.describe('Point-Buy Character Creation', () => {
    test('a valid 65-point buy creates the character with those attributes', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto(CREATE_URL);

        await page.fill('#point-buy-name', 'Bought Hero');
        await fillBuy(page, VALID_BUY);
        await expect(page.locator('#point-buy-remaining')).toHaveText('0');
        await page.click('#point-buy-submit');

        await expect(page).toHaveURL(/\/characters\/\d+\//);
        for (const [field, value] of Object.entries(VALID_BUY)) {
            // A player's attributes are read-only once made (issue #748).
            await expect(page.locator(`[data-readonly-field="${field}"]`)).toHaveText(value);
        }
        await expect(page.locator('.dice-log pre')).toContainText('point-buy');
    });

    test('a bad sum is refused by the server and creates nothing', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto(CREATE_URL);

        // 11 + 11 + 11 + 11 + 11 + 3 === 58, every value in range.
        await page.fill('#point-buy-charisma', '3');
        await expect(page.locator('#point-buy-submit')).toBeDisabled();

        // The disabled button is the client's guard. Submit the form directly to
        // reach the server check, which is the authoritative one -- this is what
        // a client with scripting off or tampered with would send. The submit is
        // deferred so this evaluate call returns before navigation tears down its
        // execution context.
        await page.evaluate(() => {
            setTimeout(() => document.getElementById('point-buy-form').submit(), 0);
        });

        const serverError = page.locator('#point-buy-server-error');
        await expect(serverError).toBeVisible();
        await expect(serverError).toContainText('must total exactly 65');
        await expect(serverError).toContainText('58');

        // No character was created: we are still on the form, not on a new sheet.
        await expect(page).toHaveURL(new RegExp(`${CREATE_URL}$`));
        await expect(page).not.toHaveURL(/\/characters\/\d+\//);

        // The player's own numbers came back so they can fix them in place.
        await expect(page.locator('#point-buy-charisma')).toHaveValue('3');
    });
});


test.describe('Off-budget point-buy for admins and DMs (issue #299)', () => {
    // 18 + 18 + 17 + 12 + 10 + 10 === 85, twenty over the 65-point budget.
    const OVER_BUDGET = {
        strength: '18',
        dexterity: '18',
        intelligence: '17',
        wisdom: '12',
        constitution: '10',
        charisma: '10'
    };

    test('a DM sees a warning and can still create the character', async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        await page.goto(CREATE_URL);

        await fillBuy(page, OVER_BUDGET);

        // The warning names the overspend and the button stays live.
        const warning = page.locator('#point-buy-off-budget');
        await expect(warning).toBeVisible();
        await expect(warning).toContainText('20 points over');
        await expect(page.locator('#point-buy-remaining')).toHaveText('-20');
        await expect(page.locator('#point-buy-submit')).toBeEnabled();

        await page.click('#point-buy-submit');

        // Landing on the editor is the proof: the server took the 85-point buy
        // and saved it, where a player's identical submission is a 400.
        await expect(page).toHaveURL(/\/characters\/\d+\//);
        await expect(page).not.toHaveURL(new RegExp(`${CREATE_URL}$`));
    });

    test('the warning clears when the DM brings the buy back on budget', async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        await page.goto(CREATE_URL);

        await fillBuy(page, OVER_BUDGET);
        await expect(page.locator('#point-buy-off-budget')).toBeVisible();

        await fillBuy(page, VALID_BUY);

        await expect(page.locator('#point-buy-remaining')).toHaveText('0');
        await expect(page.locator('#point-buy-off-budget')).toBeHidden();
        await expect(page.locator('#point-buy-submit')).toBeEnabled();
    });

    test('a plain player gets no warning element and a dead button', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto(CREATE_URL);

        await fillBuy(page, OVER_BUDGET);

        await expect(page.locator('#point-buy-off-budget')).toHaveCount(0);
        await expect(page.locator('#point-buy-submit')).toBeDisabled();
    });
});

/**
 * The loaded canon's character rules, read where the server reads them.
 *
 * The shell inherits this run's environment, TARMAR_CANON_DIR included, so it
 * loads the canon the dev server loaded.
 * @returns {object}
 */
function loadedCharacterRules() {
    const output = readDjangoShell(`
import json
from characters.rules import CoinRoll, character_rules
from common.rolling import dice_total_range
rules = character_rules()
def roll(rules_roll):
    return {"dice": rules_roll.dice, "minimum": rules_roll.minimum, "maximum": rules_roll.maximum}
def coin(value):
    if isinstance(value, CoinRoll):
        lowest, highest = dice_total_range(value.dice)
        return {"lowest": value.base + lowest, "highest": value.base + highest}
    return value
point_buy = rules.attributes.point_buy
print("RULES " + json.dumps({
    "point_buy": {"total": point_buy.total, "minimum": point_buy.minimum, "maximum": point_buy.maximum},
    "attribute_roll": roll(rules.attribute_roll),
    "fatigue_roll": roll(rules.fatigue_roll),
    "height": {
        "base": list(rules.height.base_inches_by_face),
        "per_extra_one": rules.height.inches_per_extra_one,
        "per_extra_six": rules.height.inches_per_extra_six,
        "minimum": rules.height.minimum_inches,
    },
    "weight": {
        "stones": list(rules.weight.stones_by_face),
        "pounds_per_stone": rules.weight.pounds_per_stone,
        "strength_divisor": rules.weight.strength_divisor,
    },
    "tables": {
        name: [[row.up_to, row.label] for row in table.rows]
        for name, table in (
            ("social_class", rules.social_class),
            ("nobility", rules.nobility),
            ("merchant", rules.merchant),
            ("commoner", rules.commoner),
            ("location", rules.location),
            ("wealth", rules.wealth),
        )
    },
    "coin": {row.label: coin(row.coin) for row in rules.wealth.rows},
}))
`);
    const line = output.split('\n').find((text) => text.startsWith('RULES '));
    return JSON.parse(line.slice('RULES '.length));
}

/**
 * A rolled character's stored values, by its id.
 * @param {number} id
 * @returns {object}
 */
function storedCharacter(id) {
    const output = readDjangoShell(`
import json
from characters.models import Character
character = Character.objects.get(pk=${id})
fields = ("strength", "dexterity", "intelligence", "wisdom", "constitution", "charisma",
          "height_roll", "weight_roll", "fatigue_roll", "provenance_class",
          "provenance_detail", "location", "wealth_level", "starting_coin")
print("CHARACTER " + json.dumps({field: getattr(character, field) for field in fields}))
`);
    const line = output.split('\n').find((text) => text.startsWith('CHARACTER '));
    return JSON.parse(line.slice('CHARACTER '.length));
}

/**
 * The label a d100 roll lands on: the first row whose up_to it does not pass,
 * the last for a roll past every row (``PercentileTable.row_for``).
 * @param {Array<[number, string]>} rows
 * @param {number} roll
 */
function rowFor(rows, roll) {
    const row = rows.find(([upTo]) => roll <= upTo) || rows[rows.length - 1];
    return row[1];
}

/**
 * The one line of the dice log that matches ``pattern``, its groups.
 * @param {string} diceLog
 * @param {RegExp} pattern
 */
function logLine(diceLog, pattern) {
    const match = diceLog.match(pattern);
    expect(match, `the dice log has a line matching ${pattern}`).not.toBeNull();
    return match;
}

/**
 * A pattern for a regular-expression-safe piece of text.
 * @param {string} text
 */
function escaped(text) {
    return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * A height in inches as the sheet prints it, e.g. 5'8".
 * @param {number} inches
 */
function printedHeight(inches) {
    return `${Math.floor(inches / 12)}'${inches % 12}"`;
}

test.describe('Creation follows the loaded canon\'s rules (#59)', () => {
    test('the point-buy form states and enforces the canon\'s budget and bounds', async ({ page }) => {
        const pointBuy = loadedCharacterRules().point_buy;
        await loginAs(page, TEST_USER);
        await page.goto(CREATE_URL);

        // The card around the form: its heading and the sentence above it.
        const card = page.locator('section.creation-option', { has: page.locator('#point-buy-form') });
        await expect(card.locator('h2')).toHaveText(`Divide ${pointBuy.total} Points`);
        await expect(card).toContainText(`Split exactly ${pointBuy.total} points`);
        await expect(card).toContainText(
            `must be between ${pointBuy.minimum} and ${pointBuy.maximum}`
        );
        // The prefilled buy spends the whole budget.
        await expect(page.locator('#point-buy-spent')).toHaveText(String(pointBuy.total));
        const inputs = page.locator('.point-buy-input');
        await expect(inputs).toHaveCount(6);
        for (let index = 0; index < 6; index += 1) {
            await expect(inputs.nth(index)).toHaveAttribute('min', String(pointBuy.minimum));
            await expect(inputs.nth(index)).toHaveAttribute('max', String(pointBuy.maximum));
        }
    });

    test('a rolled character is rolled on the canon\'s dice and tables', async ({ page }) => {
        const rules = loadedCharacterRules();
        await loginAs(page, TEST_USER);
        await page.goto(CREATE_URL);
        await page.fill('#roll-name', 'Canon Rolled');
        await page.click('button:has-text("Roll a New Character")');
        await expect(page).toHaveURL(/\/characters\/\d+\//);
        const id = Number(page.url().match(/\/characters\/(\d+)\//)[1]);
        const character = storedCharacter(id);

        // Height: the first face's base, each further 1 or 6 in the chain,
        // floored at the canon's minimum.
        const faces = character.height_roll.split('.').map(Number);
        const adjustment = faces.slice(1).reduce((total, face) => total
            + (face === 1 ? rules.height.per_extra_one : 0)
            + (face === 6 ? rules.height.per_extra_six : 0), 0);
        const inches = Math.max(
            rules.height.minimum, rules.height.base[faces[0] - 1] + adjustment
        );
        await expect(page.locator('#height_display')).toHaveText(`(${printedHeight(inches)})`);

        // Weight: the face's stones plus STR over the divisor, in pounds.
        const stones = rules.weight.stones[Number(character.weight_roll) - 1]
            + Math.floor(character.strength / rules.weight.strength_divisor);
        await expect(page.locator('#weight_lbs')).toHaveText(
            String(stones * rules.weight.pounds_per_stone)
        );

        // The Fatigue roll is one the canon's dice can make.
        await expect(page.locator('#fatigue-roll')).toHaveText(String(character.fatigue_roll));
        expect(character.fatigue_roll).toBeGreaterThanOrEqual(rules.fatigue_roll.minimum);
        expect(character.fatigue_roll).toBeLessThanOrEqual(rules.fatigue_roll.maximum);

        // Each table result is the row its own logged d100 lands on, so a
        // stored model default (Commoner, Laborer, Village, Moderate) cannot
        // pass for a roll.
        const diceLog = (await page.locator('#dice-log-text').textContent()) || '';
        const [, classRoll, classShown, detailRoll, detail] = logLine(
            diceLog, /^Provenance: d100=(\d+) -> (\S+), d100=(\d+) -> (.+)$/m
        );
        expect(rowFor(rules.tables.social_class, Number(classRoll))).toBe(
            character.provenance_class
        );
        expect(classShown.toLowerCase()).toBe(character.provenance_class);
        expect(rowFor(rules.tables[character.provenance_class], Number(detailRoll))).toBe(
            character.provenance_detail
        );
        expect(detail).toBe(character.provenance_detail);
        const [, locationRoll, location] = logLine(diceLog, /^Location: d100=(\d+) -> (.+)$/m);
        expect(rowFor(rules.tables.location, Number(locationRoll))).toBe(character.location);
        expect(location).toBe(character.location);
        const [, wealthRoll, wealthLevel] = logLine(
            diceLog, /^Wealth: d100=(\d+) -> (.+), starting coin = /m
        );
        expect(rowFor(rules.tables.wealth, Number(wealthRoll))).toBe(character.wealth_level);
        expect(wealthLevel).toBe(character.wealth_level);
        await expect(page.locator('#provenance')).toContainText(character.provenance_detail);
        await expect(page.locator('#location')).toContainText(character.location);
        await expect(page.locator('#wealth')).toContainText(character.wealth_level);

        // The starting coin is the wealth row's: fixed, base plus dice, or
        // unset for the Game Master.
        const coin = rules.coin[character.wealth_level];
        const shownCoin = page.locator('[data-readonly-field="starting_coin"]');
        if (coin === null) {
            expect(character.starting_coin).toBeNull();
            await expect(shownCoin).toHaveText('Consult GM');
        } else if (typeof coin === 'number') {
            expect(character.starting_coin).toBe(coin);
            await expect(shownCoin).toHaveText(String(coin));
        } else {
            expect(character.starting_coin).toBeGreaterThanOrEqual(coin.lowest);
            expect(character.starting_coin).toBeLessThanOrEqual(coin.highest);
            await expect(shownCoin).toHaveText(String(character.starting_coin));
        }

        // Each attribute is its own logged total on the canon's attribute
        // dice, and lies in their range.
        const attributeDice = escaped(rules.attribute_roll.dice);
        const attributes = {
            STR: 'strength', DEX: 'dexterity', INT: 'intelligence',
            WIS: 'wisdom', CON: 'constitution', CHR: 'charisma',
        };
        for (const [label, field] of Object.entries(attributes)) {
            const [, total] = logLine(
                diceLog, new RegExp(`^${label}: ${attributeDice}: \\[[\\d, ]+\\] = (\\d+)$`, 'm')
            );
            expect(Number(total), label).toBe(character[field]);
            expect(character[field], label).toBeGreaterThanOrEqual(rules.attribute_roll.minimum);
            expect(character[field], label).toBeLessThanOrEqual(rules.attribute_roll.maximum);
        }
        // The Fatigue roll is its own logged total on the canon's dice.
        const [, fatigueTotal] = logLine(
            diceLog,
            new RegExp(`^Fatigue: ${escaped(rules.fatigue_roll.dice)}: \\[[\\d, ]+\\] = (\\d+)$`, 'm')
        );
        expect(Number(fatigueTotal)).toBe(character.fatigue_roll);
    });
});
