/**
 * E2E for a signed-out visitor opening a battle that holds a private
 * character (issue #339, item 32).
 *
 * Every signed-in user may read every battle, but a battle holding a
 * private character is closed to anonymous viewers. The visitor used to get
 * a bare 404 from a link someone shared; now they are sent to sign in and,
 * once signed in, land on the battle itself.
 */

const {
    test, expect, TEST_USER, submitPassphrase, createBattleViaSetup,
} = require('./fixtures');
const { startCoverage } = require('./coverage');

test('a signed-out visitor is sent to sign in, then lands on the battle', async ({ adminPage, browser }) => {
    test.setTimeout(120000);
    const battleName = `E2E Sign In ${Date.now()}`;
    await createBattleViaSetup(adminPage, { name: battleName, fighters: 2, seed: 71 });
    const battlePath = new URL(adminPage.url()).pathname;
    expect(battlePath).toMatch(/\/tarmar\/battles\/\d+\/$/);

    const context = await browser.newContext();
    const page = await context.newPage();
    await startCoverage(page);
    await submitPassphrase(page);

    await page.goto(battlePath);
    await expect(page).toHaveURL(/\/tarmar\/accounts\/login\/\?next=/);
    await expect(page.getByText(battleName)).toHaveCount(0);

    await page.fill('input[name="username"]', TEST_USER.username);
    await page.fill('input[name="password"]', TEST_USER.password);
    await page.click('button[type="submit"]');
    await page.waitForURL(`**${battlePath}`);
    await expect(page.locator('h1')).toHaveText(battleName);
    await context.close();
});
