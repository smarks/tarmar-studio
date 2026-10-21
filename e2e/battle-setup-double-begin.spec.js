/**
 * E2E for "Begin the battle" pressed twice (issue #832).
 *
 * Two overlapping presses each loaded the draft before either deleted it, so
 * one setup built two battles. The page now sends one press and the server
 * refuses a second; what a reader sees is one battle under the name typed.
 */

const { test, expect, battleRowOnList } = require('./fixtures');

test('a double press of Begin the battle builds one battle', async ({ adminPage: page }, testInfo) => {
    // The worker's index keeps two workers that start in the same
    // millisecond from naming two battles alike, which reads as one setup
    // building two (issue #88).
    const battleName = `E2E Once ${testInfo.workerIndex}${Date.now()}`;
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

test('the Begin button is disabled once pressed', async ({ adminPage: page }, testInfo) => {
    await page.goto('/tarmar/battles/');
    await page.click('.battle-list-masthead button[type="submit"]');
    await page.waitForURL(/\/tarmar\/battles\/setup\/\d+\/$/);
    for (const archetype of ['archer', 'archer']) {
        await page.click(`.setup-fighter-grid form:has(input[value="${archetype}"]) button`);
        await page.waitForURL(/\/setup\/\d+\/$/);
    }
    await page.fill('input[name="name"]', `E2E Held ${testInfo.workerIndex}${Date.now()}`);
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

test('a stopped press can be pressed again', async ({ adminPage: page }, testInfo) => {
    const battleName = `E2E Stopped ${testInfo.workerIndex}${Date.now()}`;
    await page.goto('/tarmar/battles/');
    await page.click('.battle-list-masthead button[type="submit"]');
    await page.waitForURL(/\/tarmar\/battles\/setup\/\d+\/$/);
    for (const archetype of ['archer', 'archer']) {
        await page.click(`.setup-fighter-grid form:has(input[value="${archetype}"]) button`);
        await page.waitForURL(/\/setup\/\d+\/$/);
    }
    await page.fill('input[name="name"]', battleName);
    // Hold the start so it can be stopped the way Esc or the Stop button
    // stops it: the page stays, and the press must be possible again. The
    // stop waits for the held request to reach the route, not for a guess
    // at how long that takes: stopped any earlier, the one-time route would
    // still be armed for the second press and abort that instead (issue #88).
    // The stop is the browser's own Stop, what the Stop button and Esc do,
    // sent over the DevTools protocol (Page.stopLoading); the page hears it
    // through navigateerror. A page.evaluate(() => window.stop()) made at
    // this point waited on the held navigation until the test timed out.
    const devtools = await page.context().newCDPSession(page);
    let arrived;
    const reachedRoute = new Promise((resolve) => { arrived = resolve; });
    let release;
    const held = new Promise((resolve) => { release = resolve; });
    await page.route('**/start/', async (route) => {
        arrived();
        await held;
        await route.abort();
    }, { times: 1 });
    await page.locator('.setup-begin').evaluate((button) => button.click());
    await reachedRoute;
    await devtools.send('Page.stopLoading');
    release();
    await expect(page.locator('.setup-begin')).toBeEnabled();
    await page.locator('.setup-begin').click();
    await page.waitForURL(/\/tarmar\/battles\/\d+\/$/);
    await expect(page.locator('h1')).toHaveText(battleName);
});
