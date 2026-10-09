/**
 * E2E for Transcript and Send to Claude on a session this page started (issue #436).
 *
 * Both used to work only on a page opened with ?session=. A session started
 * from a fresh /tarmar/gm/ page left the transcript link empty until a reload,
 * and every result and question appeared at the top of the page, far from
 * the input the player was watching. The stream is stubbed (no model); the
 * session it names is real, so the transcript and review requests are real.
 */

const { execSync } = require('child_process');
const { test, expect, loginAs, TEST_ADMIN } = require('./fixtures');

function djangoShell(script) {
    return execSync(`uv run python manage.py shell -c "${script.replace(/"/g, '\\"')}"`, {
        cwd: process.cwd(),
        encoding: 'utf8',
        env: { ...process.env, DEBUG: 'true' },
    });
}

const SEED = `
from django.contrib.auth import get_user_model
from gm.models import GMSession
user = get_user_model().objects.get(username='${TEST_ADMIN.username}')
session = GMSession.objects.create(user=user, name='New session tools E2E')
session.history = [{'role': 'user', 'content': 'Build the twins.'}, {'role': 'assistant', 'content': 'Both twins are built.'}]
session.save()
print('SESSION', session.pk)
`.trim().replace(/\n/g, '; ');

test('Transcript and Send to Claude work on a session started from a fresh page', async ({ page }) => {
    const sessionId = Number(djangoShell(SEED).match(/SESSION (\d+)/)[1]);
    await loginAs(page, TEST_ADMIN);
    await page.goto('/tarmar/gm/');
    await page.route('**/tarmar/gm/stream/', (route) => route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: [
            `data: ${JSON.stringify({ token: 'Both twins are built.' })}`,
            '',
            `data: ${JSON.stringify({ done: true, full_response: 'Both twins are built.', session_id: sessionId })}`,
            '',
            '',
        ].join('\n'),
    }));

    await page.fill('#user-input', 'Build the twins.');
    await page.click('#send-btn');
    await expect(page).toHaveURL(new RegExp(`session=${sessionId}`));

    const notices = page.locator('.chat-container #notice-region');
    const [download] = await Promise.all([
        page.waitForEvent('download'),
        page.locator('#gm-transcript-button').click(),
    ]);
    expect(download.suggestedFilename()).not.toBe('');
    await expect(notices).toContainText('Transcript saved as');

    await page.locator('#gm-review-button').click();
    const ask = notices.locator('.notice-prompt');
    await expect(ask).toContainText('What should Claude look at?');
    await ask.locator('.notice-prompt-input').fill('the twins');
    await ask.locator('button:has-text("Send to Claude")').click();
    await expect(notices).toContainText('Sent to Claude as review #');
});
