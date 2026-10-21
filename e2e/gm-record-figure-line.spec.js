/**
 * E2E for the record-figure line a reply's own numbers must not silence
 * (issues #622, #727).
 *
 * A real turn through the server against the fake Ollama (e2e/fake-ollama.js).
 * A Game Master records two characters, then asks what level one of them is.
 * The fake model answers with prose that states no level but does carry a
 * "+1" weapon modifier. The server used to read that digit as the level
 * already stated and say nothing, which left a question about a figure
 * answered with no figure anywhere in the reply -- the failure #622 exists to
 * prevent. The player must read the record's own line under the narration.
 */

const { test, expect } = require('./fixtures');
const { uniqueStamp } = require('./unique-name');

test.describe('GM chat — the record line fires beside an unrelated number', () => {
    test('a weapon modifier in the reply does not pass for the level', async ({ adminPage: page }) => {
        const stamp = uniqueStamp();
        const brother = `Ledger${stamp}`;
        const sister = `Quire${stamp}`;

        await page.goto('/tarmar/gm/');
        await page.fill('#user-input', `Build twins ${brother} and ${sister}, all 18s`);
        await page.click('#send-btn');
        await expect(page.locator('.message.assistant').last()).toContainText('nothing stirs', { timeout: 30000 });
        await expect(page).toHaveURL(/session=\d+/);

        await page.fill('#user-input', `What level is ${brother} right now?`);
        await page.click('#send-btn');

        const answer = page.locator('.message.assistant').last();
        await expect(answer).toContainText('Her sword arm gains', { timeout: 30000 });
        await expect(answer).toContainText(`${brother}'s record: level 1.`);
    });
});
