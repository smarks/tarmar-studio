/**
 * E2E for the notice a turn carries when it described years it never played
 * (issue #395).
 *
 * The server decides the notice; the stream is stubbed with the done frame it
 * sends, so what is checked here is that the player sees it, in the chat, and
 * that it stays there rather than going away on a timer.
 */

const { test, expect } = require('./fixtures');

const NOTICE = 'No years of experience were played this turn: nothing was run on the server, '
    + 'so the character has none of the years, XP, coin or skills a reply may describe. '
    + 'Ask again for the years to be played.';

function sseBody(fullResponse, notices) {
    return [
        `data: ${JSON.stringify({ token: fullResponse })}`,
        '',
        `data: ${JSON.stringify({ done: true, full_response: fullResponse, notices })}`,
        '',
        '',
    ].join('\n');
}

test.describe('GM chat — years described but not played', () => {
    test('the reply carries a notice that stays', async ({ authenticatedPage: page }) => {
        await page.goto('/tarmar/gm/');
        await page.route('**/tarmar/gm/stream/', (route) => route.fulfill({
            status: 200,
            contentType: 'text/event-stream',
            body: sseBody("I will now run Twain's 5 years as a campaigner.", [NOTICE]),
        }));

        await page.fill('#user-input', 'Give Twain 5 years as a campaigner');
        await page.click('#send-btn');

        const notice = page.locator('.message.assistant .grounding-notice');
        await expect(notice).toContainText('No years of experience were played this turn');
        await page.waitForTimeout(3000);
        await expect(notice).toBeVisible();
    });
});
