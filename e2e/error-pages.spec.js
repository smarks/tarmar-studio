/**
 * E2E for the page a refused account sees (issue #339, item 14).
 *
 * A player opening a Game Master page used to get Django's bare "403
 * Forbidden". The watcher account has no roles, so the deleted-characters
 * page refuses it and the site's own 403 page must be what renders.
 */

const { test, expect, loginAs, TEST_WATCHER } = require('./fixtures');

test.describe('Error pages', () => {
    test('an account refused a Game Master page gets the 403 page', async ({ page }) => {
        await loginAs(page, TEST_WATCHER);

        const response = await page.goto('/tarmar/characters/deleted/');

        expect(response.status()).toBe(403);
        await expect(page).toHaveTitle('403 — Tarmar Studio');
        await expect(page.getByRole('heading', { level: 1 })).toHaveText('You shall not pass.');
        await expect(page.getByRole('link', { name: 'Return to the Shire' })).toHaveAttribute('href', '/');
    });
});
