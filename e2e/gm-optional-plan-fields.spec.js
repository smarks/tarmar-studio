/**
 * E2E for a plan whose optional fields the model had nothing to say for
 * (issue #648).
 *
 * A real turn through the server against the fake Ollama (e2e/fake-ollama.js),
 * whose "meet <name>" plan writes `"wants": null` and `"condition": ""` --
 * routine for a model, and what `record_object` is told to do, since a thing
 * does not want anything. Reading those as missing values made the reply no
 * plan at all, so every well-formed action beside them was discarded, the
 * turn recorded nothing, and the player was told to ask again in other words.
 * Both actions must run, and that notice must not appear.
 */

const { test, expect } = require('./fixtures');

const NOTHING_RECORDED = 'could not work out what to record';

test.describe('GM chat — a plan with nothing to say for an optional field', () => {
    test('both actions still run, and the turn records them', async ({ adminPage: page }, testInfo) => {
        const stamp = `${testInfo.workerIndex}${Date.now()}`;
        const name = `Vane${stamp}`;

        await page.goto('/tarmar/gm/');
        await page.fill('#user-input', `We meet ${name} at the shrine`);
        await page.click('#send-btn');
        await expect(page.locator('.message.assistant').last()).toContainText('nothing stirs', { timeout: 30000 });

        const chips = page.locator('.chat-messages .dice-roll');
        await expect(chips).toHaveCount(2);
        await expect(chips.nth(0)).toContainText(name);
        await expect(chips.nth(0)).toContainText('the shrine');
        await expect(chips.nth(1)).toContainText('iron box');
        await expect(page.locator('.chat-messages')).not.toContainText(NOTHING_RECORDED);

        // And they are records, not just chips: they survive the reload. The
        // chips and words stream before the turn is saved, so the reload may
        // land while it is still saving; the page then says the turn is
        // running, asks after it every two seconds, and opens the session
        // when it lands (issue #880). The wait covers that path.
        await page.reload();
        await expect(page.locator('.chat-messages .dice-roll')).toHaveCount(2, { timeout: 15000 });
    });
});
