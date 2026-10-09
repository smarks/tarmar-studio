/**
 * E2E for reaching the deleted-characters page (issue #339, item 16).
 *
 * The page existed but nothing linked to it, and its only way out said "Back
 * to Users". A manager now finds it from the character list, and the trail
 * says where it sits.
 */

const { test, expect } = require('./fixtures');

test('a manager reaches deleted characters from the character list', async ({ adminPage: page }) => {
    await page.goto('/tarmar/characters/');

    await page.getByRole('link', { name: 'Deleted characters' }).click();

    await expect(page).toHaveURL(/\/tarmar\/characters\/deleted\/$/);
    await expect(page.locator('.breadcrumb [aria-current="page"]')).toHaveText('Deleted');
    await expect(page.locator('.breadcrumb a', { hasText: 'Characters' })).toBeVisible();
    await expect(page.getByRole('link', { name: /Back to Users/ })).toHaveCount(0);
});
