/**
 * The hex picker (#819, #866), in a real browser, on tarmar-engine 0.9.10.
 *
 * An option that places the figure (MOVE, DODGE, DROP, an engaged Shift, a
 * DISENGAGE) carries the hexes the engine would take for it. Selecting it
 * draws those hexes on the board as buttons; the hex picked is marked and
 * sent with the choice, and the engine moves the figure there.
 */

const { test, expect, createBattleViaSetup, escaped } = require('./fixtures');

/**
 * Start a two-duelist battle played by hand and stop at the first figure.
 *
 * @param {import('@playwright/test').Page} page - a Game Master's page
 * @param {string} label - names the battle
 * @returns {Promise<string>} the awaited figure's name
 */
async function firstManualTurn(page, label) {
    await createBattleViaSetup(page, {
        name: `E2E ${label} ${Date.now()}`,
        fighters: ['unarmoured duelist', 'unarmoured duelist'],
        seed: 42,
        controlMode: 'manual',
    });
    await page.click('#advance-turn');
    await expect(page.locator('#advance-turn')).toHaveText(/^Waiting on .+'s move$/);
    const waitingLabel = await page.locator('#advance-turn').textContent();
    return waitingLabel.replace('Waiting on ', '').replace("'s move", '').trim();
}

/**
 * The turn-panel option with this exact name.
 *
 * @param {import('@playwright/test').Page} page
 * @param {string} name
 */
function option(page, name) {
    return page.locator('#turn-panel .turn-option').filter({
        has: page.locator('.turn-option-name', { hasText: new RegExp(`^${escaped(name)}$`) }),
    });
}

test.describe('The hex picker', () => {
    test('a player picks the hex a MOVE runs to', async ({ adminPage }) => {
        const mover = await firstManualTurn(adminPage, 'HexPicker');
        const arena = adminPage.locator('#battle-arena');
        await expect(arena.locator('.arena-destination')).toHaveCount(0);

        await option(adminPage, 'MOVE').click();
        const hexes = arena.locator('.arena-destination');
        await expect(hexes.first()).toBeVisible();
        expect(await hexes.count()).toBeGreaterThan(6);
        const panel = adminPage.locator('#turn-panel');
        await expect(panel.locator('.turn-destination-line')).toContainText('marked hexes');

        // Two steps from the mover is a hex a run reaches without engaging.
        const picked = hexes.nth(Math.floor((await hexes.count()) / 2));
        const hex = await picked.getAttribute('data-hex');
        await picked.click();
        await expect(arena.locator('.arena-destination.is-chosen')).toHaveAttribute('data-hex', hex);
        const [q, r] = hex.split(',');
        await expect(panel.locator('.turn-destination-line'))
            .toHaveText(`Moving to hex (${q}, ${r}).`);

        await panel.locator('.turn-confirm').click();
        await expect(adminPage.locator('#advance-turn')).not.toHaveText(`Waiting on ${mover}'s move`);
        await expect(arena.locator('.arena-destination')).toHaveCount(0);
        // The second duelist holds its ground so the turn commits.
        const names = panel.locator('.turn-option-name');
        await expect(names.first()).toBeVisible();
        const offered = (await names.allTextContents()).map((name) => name.trim());
        await option(adminPage, offered.includes('DODGE') ? 'DODGE' : 'DEFEND').click();
        await panel.locator('.turn-confirm').click();

        const turnOne = adminPage.locator('.turn-group[data-turn="1"]');
        await expect(turnOne).toBeVisible();
        const lines = (await turnOne.locator('.battle-event').allTextContents())
            .map((line) => line.replace(/\s+/g, ' ').trim());
        // Since tarmar-engine 0.9.16 a counted move also says how far it
        // ended from where it began; a route round an obstacle can end
        // nearer than the hexes it ran, so that figure is any number.
        expect(lines.some((line) => new RegExp(
            `^${mover} runs \\d+ hex\\(es\\) to \\(${q}, ${r}\\), ending \\d+ hex\\(es\\) from its start$`
        ).test(line))).toBe(true);
    });

    test('leaving the hex unpicked sends none', async ({ adminPage }) => {
        await firstManualTurn(adminPage, 'HexUnpicked');
        const arena = adminPage.locator('#battle-arena');
        const panel = adminPage.locator('#turn-panel');
        await option(adminPage, 'MOVE').click();
        await arena.locator('.arena-destination').first().click();
        await expect(arena.locator('.is-chosen')).toHaveCount(1);
        await panel.locator('.turn-destination-clear').click();
        await expect(arena.locator('.is-chosen')).toHaveCount(0);
        await expect(panel.locator('.turn-destination-clear')).toHaveCount(0);
        // DODGE jogs to a hex too; a CHARGE goes at its target and takes none.
        await option(adminPage, 'DODGE').click();
        await expect(arena.locator('.arena-destination').first()).toBeVisible();
        await option(adminPage, 'CHARGE ATTACK').click();
        await expect(arena.locator('.arena-destination')).toHaveCount(0);
    });
});
