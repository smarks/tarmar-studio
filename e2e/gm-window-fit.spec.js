/**
 * E2E for the full-window GM pages fitting the window (issue #431).
 *
 * The chat and map pages subtracted a guessed 60px header from the window,
 * but the header renders taller (75px on desktop, two rows on a phone), so
 * the bottom of each page sat below the fold and the phone panel overlapped
 * the header. Sizes are measured, never assumed.
 */

const { test, expect } = require('./fixtures');

test('the phone panel opens below the header, not over it', async ({ authenticatedPage: page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto('/tarmar/gm/');

    await page.click('#gm-panel-toggle');
    const panel = page.locator('#gm-panel');
    await expect(panel).toBeVisible();

    const header = await page.locator('body > header').boundingBox();
    const bar = await page.locator('.mobile-session-bar').boundingBox();
    const box = await panel.boundingBox();
    expect(box.y).toBeGreaterThanOrEqual(header.y + header.height);
    // Under the bar, so the Panel button that closes it is not covered.
    expect(box.y).toBeGreaterThanOrEqual(bar.y + bar.height - 1);
    expect(box.y + box.height).toBeLessThanOrEqual(812);

    await page.click('#gm-panel-toggle');
    await expect(panel).toBeHidden();
});

for (const viewport of [
    { name: 'desktop', width: 1440, height: 900 },
    { name: 'phone', width: 375, height: 812 },
]) {
    test(`the GM chat page does not scroll the window (${viewport.name})`, async ({ authenticatedPage: page }) => {
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await page.goto('/tarmar/gm/');

        const overflow = await page.evaluate(
            () => document.scrollingElement.scrollHeight - window.innerHeight,
        );
        expect(overflow).toBeLessThanOrEqual(1);
        await expect(page.locator('body > footer')).toBeInViewport();
    });

    test(`the map page fits the window (${viewport.name})`, async ({ authenticatedPage: page }) => {
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        const response = await page.goto('/tarmar/gm/map/');
        expect(response.status()).toBe(200);

        const map = await page.locator('.gm-map-page').boundingBox();
        expect(map.y + map.height).toBeLessThanOrEqual(viewport.height);
    });
}
