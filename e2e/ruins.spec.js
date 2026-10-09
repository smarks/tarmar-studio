/**
 * Ruin generator E2E: the DM rolls the Dungeons & Ruins tables and sees the
 * populated site with every roll's detail; the URL carries the seed, so a
 * reload reproduces the same ruin.
 *
 * Uses the admin fixture user, who has the DM role in global-setup.
 */

const { test, expect } = require('./fixtures');

test.describe('Ruin generator', () => {
    test('DM generates a ruin and sees rooms and roll details', async ({ adminPage }) => {
        await adminPage.goto('/tarmar/gm/ruins/');
        await expect(adminPage.locator('h1')).toHaveText('Ruin Generator');

        await adminPage.fill('#ruin-form input[name="rooms"]', '3');
        await adminPage.fill('#ruin-form input[name="seed"]', '42');
        await adminPage.click('#ruin-form button');

        await adminPage.waitForURL(/\/tarmar\/gm\/ruins\/\?rooms=3&seed=42$/);
        const result = adminPage.locator('#ruin-result');
        await expect(result).toBeVisible();
        await expect(result).toContainText('seed 42');
        await expect(result).toContainText('Built by...');
        await expect(result.locator('.generator-card')).toHaveCount(3);
        await expect(result).toContainText('Environmental Hazard');
        await expect(result).toContainText('Treasure Type');
        // Roll details: die and face rendered alongside each result.
        await expect(result.locator('.roll-detail').first()).toContainText(/d\d+ → \d+/);

        // State-driven: the seed lives in the URL, so a reload rerolls nothing.
        const originText = await result.locator('.ruin-site li').first().textContent();
        await adminPage.reload();
        await expect(adminPage.locator('#ruin-result .ruin-site li').first()).toHaveText(originText);

        // Generate-another never dead-ends: the form is still there, prefilled.
        await expect(adminPage.locator('#ruin-form input[name="rooms"]')).toHaveValue('3');
        await expect(adminPage.locator('#ruin-form button')).toContainText('Generate another');
    });

    test('generating without a seed redirects to a seeded, shareable URL', async ({ adminPage }) => {
        await adminPage.goto('/tarmar/gm/ruins/');
        await adminPage.fill('#ruin-form input[name="rooms"]', '2');
        await adminPage.click('#ruin-form button');
        await adminPage.waitForURL(/\/tarmar\/gm\/ruins\/\?rooms=2&seed=\d+$/);
        await expect(adminPage.locator('#ruin-result')).toBeVisible();
    });

    test('players cannot reach the ruin generator', async ({ authenticatedPage }) => {
        const response = await authenticatedPage.goto('/tarmar/gm/ruins/');
        expect(response.status()).toBe(403);
    });
});
