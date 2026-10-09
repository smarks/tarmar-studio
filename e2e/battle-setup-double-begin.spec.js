/**
 * E2E for "Begin the battle" pressed twice (issue #832).
 *
 * Two overlapping presses each loaded the draft before either deleted it, so
 * one setup built two battles. The page now sends one press and the server
 * refuses a second; what a reader sees is one battle under the name typed.
 */

const { test, expect, battleRowOnList } = require('./fixtures');

test('a double press of Begin the battle builds one battle', async ({ adminPage: page }) => {
    const battleName = `E2E Once ${Date.now()}`;
    await page.goto('/tarmar/battles/');
    await page.click('.battle-list-masthead button[type="submit"]');
    await page.waitForURL(/\/tarmar\/battles\/setup\/\d+\/$/);
    for (const archetype of ['archer', 'archer']) {
        await page.click(`.setup-fighter-grid form:has(input[value="${archetype}"]) button`);
        await page.waitForURL(/\/setup\/\d+\/$/);
    }
    await page.fill('input[name="name"]', battleName);

    await page.locator('.setup-begin').dblclick();
    await page.waitForURL(/\/tarmar\/battles\/\d+\/$/);
    await expect(page.locator('h1')).toHaveText(battleName);

    await expect(await battleRowOnList(page, battleName)).toHaveCount(1);
});

test('the Begin button is disabled once pressed', async ({ adminPage: page }) => {
    await page.goto('/tarmar/battles/');
    await page.click('.battle-list-masthead button[type="submit"]');
    await page.waitForURL(/\/tarmar\/battles\/setup\/\d+\/$/);
    for (const archetype of ['archer', 'archer']) {
        await page.click(`.setup-fighter-grid form:has(input[value="${archetype}"]) button`);
        await page.waitForURL(/\/setup\/\d+\/$/);
    }
    await page.fill('input[name="name"]', `E2E Held ${Date.now()}`);
    // Read in the same task as the press: the page is navigating away
    // straight after, and a locator would wait for that to finish.
    const disabledOnPress = await page.evaluate(() => {
        const button = document.querySelector('.setup-begin');
        button.click();
        return button.disabled;
    });
    expect(disabledOnPress).toBe(true);
    await page.waitForURL(/\/tarmar\/battles\/\d+\/$/);
});

test('a stopped press can be pressed again', async ({ adminPage: page }) => {
    const battleName = `E2E Stopped ${Date.now()}`;
    await page.goto('/tarmar/battles/');
    await page.click('.battle-list-masthead button[type="submit"]');
    await page.waitForURL(/\/tarmar\/battles\/setup\/\d+\/$/);
    for (const archetype of ['archer', 'archer']) {
        await page.click(`.setup-fighter-grid form:has(input[value="${archetype}"]) button`);
        await page.waitForURL(/\/setup\/\d+\/$/);
    }
    await page.fill('input[name="name"]', battleName);
    // Hold the start so it can be stopped the way Esc or the Stop button
    // stops it: the page stays, and the press must be possible again.
    let release;
    const held = new Promise((resolve) => { release = resolve; });
    await page.route('**/start/', async (route) => {
        await held;
        await route.abort();
    }, { times: 1 });
    await page.evaluate(() => {
        document.querySelector('.setup-begin').click();
        setTimeout(() => window.stop(), 200);
    });
    await page.waitForTimeout(500);
    release();
    await expect(page.locator('.setup-begin')).toBeEnabled();
    await page.locator('.setup-begin').click();
    await page.waitForURL(/\/tarmar\/battles\/\d+\/$/);
    await expect(page.locator('h1')).toHaveText(battleName);
});
