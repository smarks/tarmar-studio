/**
 * A network blip leaves the sheet's controls usable (issue #643).
 *
 * The visibility toggle, Mark Dead and Revive all awaited postJson with no
 * try/catch. postJson guards only the body parse — fetch itself rejects on a
 * network failure — so the handler died at that line and everything after it
 * was skipped, including the re-enable. Each had already disabled its control,
 * and the visibility toggle had already been flipped by the browser, so the
 * page was left showing a state the server had never agreed to, with the
 * control dead for the rest of the page's life and nothing said.
 *
 * The blip is served by aborting the route: what is under test is what the
 * page does when the request never lands.
 */

const {
    test, expect, loginAs, createNamedCharacter, TEST_USER, TEST_ADMIN,
} = require('./fixtures');
const { readDjangoShell } = require('./django');

/** The pk of a character of this worker's admin, by name. */
function characterIdByName(name) {
    const output = readDjangoShell(`
from characters.models import Character
print('CHARACTER', Character.objects.get(name='${name}').pk)
`.trim().replace(/\n/g, '; '));
    const match = output.match(/CHARACTER (\d+)/);
    expect(match, `no character id in: ${output}`).not.toBeNull();
    return match[1];
}

test('a blip on the visibility toggle puts the checkbox back and says so', async ({ page }, testInfo) => {
    const name = `Blip ${testInfo.workerIndex}${Date.now()}`;
    await loginAs(page, TEST_USER);
    await createNamedCharacter(page, name);

    await page.route('**/set-visibility/', (route) => route.abort('failed'));
    await page.locator('#visibility-toggle').check();

    await expect(page.locator('.notice')).toContainText('Network error');
    // The badge is what the server last confirmed; the box agrees with it again.
    await expect(page.locator('#visibility-badge')).toHaveText('Private');
    await expect(page.locator('#visibility-toggle')).not.toBeChecked();
    await expect(page.locator('#visibility-toggle')).toBeEnabled();

    // And the control still works once the network is back.
    await page.unroute('**/set-visibility/');
    await page.locator('#visibility-toggle').check();
    await expect(page.locator('#visibility-badge')).toHaveText('Public');
});

test('a blip on Mark Dead leaves the button usable', async ({ page }, testInfo) => {
    const name = `Blip Dead ${testInfo.workerIndex}${Date.now()}`;
    await loginAs(page, TEST_ADMIN);
    await createNamedCharacter(page, name);
    const characterId = characterIdByName(name);

    await page.route('**/mark-dead/', (route) => route.abort('failed'));
    await page.goto(`/tarmar/characters/${characterId}/`);
    await page.locator('#mark-dead-btn').click();
    const prompt = page.locator('.notice-prompt');
    await prompt.locator('input, textarea').first().fill('a falling rock');
    await prompt.locator('button:has-text("Mark dead")').click();

    await expect(page.locator('.notice').last()).toContainText('Network error');
    await expect(page.locator('#mark-dead-btn')).toBeEnabled();
});
