/**
 * E2E for retrying a GM turn whose connection broke (issue #410).
 *
 * Safari reports a dropped fetch as "Load failed", and the page used to leave
 * the player with a notice and nothing to press. The first send is aborted at
 * the network layer; the notice must offer Retry, and Retry must send the same
 * turn again, marked as a retry.
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

test.describe('GM chat — a broken connection', () => {
    test('the notice offers Retry, and Retry sends the turn again', async ({ authenticatedPage: page }) => {
        await page.goto('/tarmar/gm/');
        const bodies = [];
        await page.route('**/tarmar/gm/stream/', async (route) => {
            bodies.push(route.request().postDataJSON());
            if (bodies.length === 1) {
                await route.abort('failed');
                return;
            }
            await route.fulfill({ status: 200, contentType: 'text/event-stream', body: sseBody('The gate opens.') });
        });

        await page.fill('#user-input', 'I knock on the gate');
        await page.click('#send-btn');

        const notice = page.locator('.notice', { hasText: 'The connection to the Game Master broke' });
        await expect(notice).toBeVisible();
        await notice.getByRole('button', { name: 'Retry' }).click();

        await expect(page.locator('.message.assistant')).toContainText('The gate opens.');
        await expect(notice).toHaveCount(0);
        expect(bodies[1].message).toBe('I knock on the gate');
        expect(bodies[1].retry).toBe(true);
    });
});
