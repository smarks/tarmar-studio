/**
 * E2E for the GM chat page's place in the site (issue #339, item 13).
 *
 * Every other page has named where it sits since issue #189; the GM page had
 * no trail and no you-are-here mark in the menu. The chat fills the window
 * below the header (issue #431), so the trail must not push the message box
 * out of it, and the box must sit inside the window at any size.
 */

const { test, expect } = require('./fixtures');

for (const viewport of [
    { name: 'desktop', width: 1440, height: 900 },
    { name: 'phone', width: 375, height: 812 },
]) {
    test(`the GM page names where it sits and keeps its input on screen (${viewport.name})`, async ({ authenticatedPage: page }) => {
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await page.goto('/tarmar/gm/');

        await expect(page.locator('.breadcrumb [aria-current="page"]')).toHaveText('Game Master');
        await expect(page.locator('.breadcrumb a', { hasText: 'Tarmar' })).toBeVisible();
        await expect(page.locator('a.submenu', { hasText: 'Game Master' }).first()).toHaveClass(/disabled/);

        const send = await page.locator('#send-btn').boundingBox();
        const main = await page.locator('main').boundingBox();
        expect(send).not.toBeNull();
        expect(send.y + send.height).toBeLessThanOrEqual(main.y + main.height);
        // Issue #431: the input used to sit 3px (desktop) to 17px (phone)
        // below the fold, because the layout allowed 60px for the header.
        expect(send.y + send.height).toBeLessThanOrEqual(viewport.height);
    });
}
