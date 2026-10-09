/**
 * E2E tests for the character creation page (issue #123).
 *
 * Covers the chooser, the roll path, and both point-buy outcomes in a real
 * browser: the client-side tally that gates the submit button, and the
 * server-side validation that is the authoritative check.
 */

const { test, expect, loginAs, TEST_USER, TEST_ADMIN } = require('./fixtures');

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
