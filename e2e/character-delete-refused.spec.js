/**
 * A refused delete says so (issue #640).
 *
 * deleteCharacter was `if (response.redirected || response.ok) { ... }` with
 * no else. A 403 from the permission check and a 429 from the rate limiter
 * are neither, so the branch was skipped: no notice, no reload, no row
 * removal. The user answered the app's own confirm panel and the row simply
 * stayed there, with nothing said.
 *
 * The refusal is served by route interception rather than by driving twenty
 * deletes into the rate limiter: what is under test is what the page does
 * with a refusal, and the notice must persist (the project forbids
 * auto-dismissing UI), which only a browser can show.
 */

const { test, expect, loginAs, createNamedCharacter, TEST_USER } = require('./fixtures');
const { uniqueName } = require('./unique-name');

test('a refused delete leaves the row and says why, and the notice stays', async ({ page }) => {
    const name = uniqueName('Undeletable');
    await loginAs(page, TEST_USER);
    await createNamedCharacter(page, name);

    await page.route('**/characters/*/delete/', (route) => route.fulfill({
        status: 403,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'Permission denied' }),
    }));

    await page.goto('/tarmar/characters/');
    const row = page.locator('tr', { hasText: name });
    await row.getByRole('button', { name: 'Delete' }).click();
    const question = page.locator('.notice-confirm');
    await expect(question).toContainText(`Delete character "${name}"?`);
    await question.locator('button:has-text("Delete")').click();

    await expect(page.locator('.notice')).toContainText('Permission denied');
    await expect(row).toBeVisible();

    // State-driven, not a toast: still there a good while later.
    await page.waitForTimeout(3000);
    await expect(page.locator('.notice')).toContainText('Permission denied');
});
