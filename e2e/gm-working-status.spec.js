/**
 * E2E for what the chat shows while the GM works (issue #398).
 *
 * A turn that runs procedures can go a minute or more without new text, and
 * the prompt bar used to grey out with nothing on screen to say anything was
 * happening. The stream is stubbed and held open, so the in-between state can
 * be looked at: the status line is up, Send waits, and the box still takes
 * the player's next move without sending it.
 */

const { test, expect } = require('./fixtures');

function sseBody(fullResponse) {
    return [
        `data: ${JSON.stringify({ token: fullResponse })}`,
        '',
        `data: ${JSON.stringify({ done: true, full_response: fullResponse })}`,
        '',
        '',
    ].join('\n');
}

test.describe('GM chat — while the GM is working', () => {
    test('the page says so, Send waits, and the box stays open', async ({ authenticatedPage: page }) => {
        await page.goto('/tarmar/gm/');
        let release;
        const held = new Promise((resolve) => { release = resolve; });
        await page.route('**/tarmar/gm/stream/', async (route) => {
            await held;
            await route.fulfill({ status: 200, contentType: 'text/event-stream', body: sseBody('The gate is shut.') });
        });

        await page.fill('#user-input', 'I knock on the gate');
        await page.click('#send-btn');

        const status = page.locator('#gm-status');
        await expect(status).toBeVisible();
        await expect(status).toContainText('The Game Master is working');
        await expect(page.locator('#send-btn')).toBeDisabled();
        await expect(page.locator('#user-input')).toBeEnabled();

        // The next move can be written now; pressing Enter does not send it.
        await page.fill('#user-input', 'Then I wait by the wall');
        await page.press('#user-input', 'Enter');
        await expect(page.locator('.message.user')).toHaveCount(1);

        release();
        await expect(page.locator('.message.assistant')).toContainText('The gate is shut.');
        await expect(status).toBeHidden();
        await expect(page.locator('#send-btn')).toBeEnabled();
        await expect(page.locator('#user-input')).toHaveValue('Then I wait by the wall');
    });
});
