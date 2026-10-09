/**
 * E2E for a Games Master turn run as plan → run → narrate (issue #590).
 *
 * A real turn through the dev server against the fake Ollama
 * (e2e/fake-ollama.js): the planning call answers with a JSON plan that starts
 * two twins with all six attributes stated as 18, the server runs it through
 * the character service, and the narrate call answers in prose. The Game
 * Master sees the chips, the narration, and exactly two characters in the
 * live Characters panel, without a reload.
 */

const { test, expect } = require('./fixtures');

test.describe('GM chat — the plan turn engine', () => {
    test('twins with stated 18s are exactly two characters in the live panel', async ({ adminPage: page }, testInfo) => {
        const stamp = `${testInfo.workerIndex}${Date.now()}`;
        const brother = `Twain${stamp}`;
        const sister = `Thread${stamp}`;

        await page.goto('/tarmar/gm/');
        await page.click('#gm-panel-tab-characters');
        await expect(page.locator('#gm-panel-characters .gm-draft')).toHaveCount(0);
        let reloaded = false;
        page.on('load', () => { reloaded = true; });

        await page.fill('#user-input', `Build twins ${brother} and ${sister}, all 18s`);
        await page.click('#send-btn');

        await expect(page.locator('.message.assistant').last()).toContainText('nothing stirs', { timeout: 30000 });
        const chips = page.locator('.dice-roll');
        await expect(chips.filter({ hasText: `${brother}` })).toHaveCount(1);
        await expect(chips.filter({ hasText: 'all six attributes recorded as 18' })).toHaveCount(2);
        // No marker and no JSON ever reaches the page.
        await expect(page.locator('#messages')).not.toContainText('[[');
        await expect(page.locator('#messages')).not.toContainText('"actions"');

        const drafts = page.locator('#gm-panel-characters .gm-draft');
        await expect(drafts).toHaveCount(2);
        await expect(drafts.nth(0).locator('.gm-sheet-name')).toHaveText(brother);
        await expect(drafts.nth(1).locator('.gm-sheet-name')).toHaveText(sister);
        await expect(drafts.nth(0).locator('.gm-sheet-attributes')).toContainText('18');
        expect(reloaded).toBe(false);

        // A reload renders the same chips from the stored session.
        await page.reload();
        await expect(page.locator('.dice-roll').filter({ hasText: 'all six attributes recorded as 18' })).toHaveCount(2);
    });
});
