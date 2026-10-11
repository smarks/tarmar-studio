/**
 * E2E for Games Master characters being records from their first change
 * (issue #571).
 *
 * A real turn through the server against the fake Ollama (e2e/fake-ollama.js):
 * the plan records all six attributes for two twins, stated by a Game Master
 * (a player's stated scores are refused, issue #590).
 * Each twin is a Character row at once, named as stated, shown in the
 * session's Characters pane and in the character list, and never suffixed.
 * Its Fatigue roll is made as it is made (issue #859), so twins with all six
 * attributes are complete; these tests asserted "needs Fatigue roll" before,
 * which pinned the older design.
 */

const { test, expect } = require('./fixtures');

test.describe('GM chat — characters are records from the first change', () => {
    test('twins are two characters, in the pane and the list', async ({ adminPage: page }, testInfo) => {
        const stamp = `${testInfo.workerIndex}${Date.now()}`;
        const brother = `Twain${stamp}`;
        const sister = `Sister${stamp}`;

        await page.goto('/tarmar/gm/');
        await page.fill('#user-input', `Build twins ${brother} and ${sister}, all 18s`);
        await page.click('#send-btn');
        await expect(page.locator('.message.assistant').last()).toContainText('nothing stirs', { timeout: 30000 });
        await expect(page).toHaveURL(/session=\d+/);
        // The words stream before the turn is saved; Send comes back only once
        // the stream has closed, after the done frame the server sends once the
        // turn is saved (issue #910).
        await expect(page.locator('#send-btn')).toBeEnabled({ timeout: 30000 });

        await page.reload();
        await page.click('#gm-panel-tab-characters');
        const drafts = page.locator('.gm-draft');
        await expect(drafts).toHaveCount(2);
        await expect(drafts.nth(0).locator('.gm-sheet-name')).toHaveText(brother);
        await expect(drafts.nth(1).locator('.gm-sheet-name')).toHaveText(sister);
        await expect(drafts.nth(0)).toContainText('Complete');
        await expect(drafts.nth(0)).not.toContainText('needs Fatigue roll');
        await expect(drafts.nth(0).locator('.gm-sheet-attributes')).toContainText('18');

        await page.goto('/tarmar/characters/');
        for (const name of [brother, sister]) {
            const row = page.locator('tr', { has: page.getByRole('link', { name, exact: true }) });
            await expect(row).toHaveCount(1);
            await expect(row).not.toContainText('needs Fatigue roll');
        }
        await expect(page.getByText(`${brother}.1`)).toHaveCount(0);
    });

    test('the Characters tab shows them during the turn, without a reload', async ({ adminPage: page }, testInfo) => {
        const stamp = `${testInfo.workerIndex}${Date.now()}`;
        const brother = `Live${stamp}`;
        const sister = `Wren${stamp}`;

        await page.goto('/tarmar/gm/');
        await page.click('#gm-panel-tab-characters');
        await expect(page.locator('.gm-draft')).toHaveCount(0);
        let reloaded = false;
        page.on('load', () => { reloaded = true; });

        await page.fill('#user-input', `Build twins ${brother} and ${sister}, all 18s`);
        await page.click('#send-btn');

        const drafts = page.locator('#gm-panel-characters .gm-draft');
        await expect(drafts).toHaveCount(2, { timeout: 30000 });
        await expect(drafts.nth(0).locator('.gm-sheet-name')).toHaveText(brother);
        await expect(drafts.nth(1)).toContainText('Complete');
        await expect(page.locator('.message.assistant').last()).toContainText('nothing stirs');
        // The picker in the swapped-in contents is still the page's control.
        await expect(page.locator('#gm-character-picker')).toHaveCount(1);
        expect(reloaded).toBe(false);
    });
});
