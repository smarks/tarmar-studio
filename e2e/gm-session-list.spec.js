/**
 * E2E for the GM session list (issue #339, item 12).
 *
 * The JSON session list at /tarmar/gm/sessions/ capped at 50 and nothing
 * called it; the chat page's own sidebar is the list players use. This proves
 * the sidebar carries a seeded session with its visible-message count, and
 * that the removed endpoint is gone rather than silently truncating.
 */

const { test, expect, loginAs, TEST_USER } = require('./fixtures');
const { readDjangoShell } = require('./django');

const SEED_SESSION = `
from django.contrib.auth import get_user_model
from gm.models import GMSession
user = get_user_model().objects.get(username='${TEST_USER.username}')
session = GMSession.objects.create(user=user, name='Session List E2E')
session.history = [{'role': 'user', 'content': 'Hello'}, {'role': 'assistant', 'content': 'Well met.'}]
session.save()
print('SESSION', session.pk)
`.trim().replace(/\n/g, '; ');

test.describe('GM chat — session list (#339 item 12)', () => {
    test('the sidebar lists the session; the unused JSON list is gone', async ({ page }) => {
        const match = readDjangoShell(SEED_SESSION).match(/SESSION (\d+)/);
        expect(match).not.toBeNull();
        const sessionId = match[1];
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/gm/');

        const item = page.locator(`#session-list .session-item[data-id="${sessionId}"]`);
        await expect(item.locator('.session-name')).toHaveText('Session List E2E');
        // The date as well as the count since #705/#712: two sessions named
        // after the same first message were otherwise one entry twice over.
        await expect(item.locator('.session-meta')).toContainText('2 msgs');
        await expect(item.locator('.session-meta time.local-time')).toHaveCount(1);

        const response = await page.request.get('/tarmar/gm/sessions/');
        expect(response.status()).toBe(404);
    });
});
