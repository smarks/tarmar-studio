/**
 * Current Fatigue and Body on the character sheet (issue #751).
 *
 * Nothing recorded a character's pools between fights: after a lost fight the
 * sheet, the Games Master's character list and the Characters pane all said
 * the character was unhurt. The sheet now prints the pools the record holds
 * and the injury table's row for them. A fight on the board writes them; a
 * Game Master may set them (healing, rest, a blow off the board); a player
 * reads them and is offered no control.
 */

const {
    test, expect, loginAs, createCharacter, submitPassphrase, TEST_USER, TEST_ADMIN,
} = require('./fixtures');
const { startCoverage } = require('./coverage');

test.describe('Current Fatigue and Body', () => {
    test('a Game Master sets them, and the player reads what was set', async ({ page, browser }, testInfo) => {
        await loginAs(page, TEST_USER);
        await createCharacter(page);
        const characterId = page.url().match(/\/characters\/(\d+)\//)[1];
        const fatigue = (await page.locator('#fatigue').textContent()).trim();
        const body = (await page.locator('#body').textContent()).trim();

        // The player's sheet: the pools now, unhurt, and nothing to change.
        const pools = page.locator('#current-pools');
        await expect(pools.locator('#current-fatigue')).toHaveText(fatigue);
        await expect(pools.locator('#current-body')).toHaveText(body);
        await expect(page.locator('#current-pools-state')).toHaveText('Unhurt');
        await expect(page.locator('.current-pool-input')).toHaveCount(0);

        const context = await browser.newContext();
        try {
            const gameMaster = await context.newPage();
            await startCoverage(gameMaster);
            await submitPassphrase(gameMaster);
            await loginAs(gameMaster, TEST_ADMIN);
            await gameMaster.goto(`/tarmar/characters/${characterId}/`);

            // Saved when the input loses focus, like every field on the sheet;
            // the readout comes back from the server with the band for it.
            const input = gameMaster.locator('input#current-fatigue');
            await expect(input).toHaveValue(fatigue);
            await input.fill('0');
            await input.blur();
            await expect(gameMaster.locator('#current-pools-state')).toHaveText('Unconscious');
            await expect(gameMaster.locator('input#current-fatigue')).toHaveValue('0');
            await gameMaster.screenshot({ path: testInfo.outputPath('game-master-sets-pools.png'), fullPage: true });

            // Above the maximum is refused, and says so.
            const again = gameMaster.locator('input#current-fatigue');
            await again.fill(String(Number(fatigue) + 1));
            await again.blur();
            await expect(gameMaster.locator('#notice-region .notice-error').last())
                .toContainText(`above its maximum of ${fatigue}`);
            // The refused number does not stay on the sheet: the input goes
            // back to what the record holds.
            await expect(gameMaster.locator('input#current-fatigue')).toHaveValue('0');

            await gameMaster.reload();
            await expect(gameMaster.locator('input#current-fatigue')).toHaveValue('0');
        } finally {
            await context.close();
        }

        // The player, reloaded, reads the Game Master's figure.
        await page.reload();
        await expect(page.locator('#current-fatigue')).toHaveText('0');
        await expect(page.locator('#current-pools-state')).toHaveText('Unconscious');
        await expect(page.locator('.current-pool-input')).toHaveCount(0);
    });
});
