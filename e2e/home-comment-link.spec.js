/**
 * "Leave a comment" on the home page's link row.
 *
 * What only a browser proves: the link sits on the same row as Play at the
 * desktop and laptop widths, the row wraps instead of pushing the page
 * sideways where it no longer fits, and clicking the link opens the public
 * repository's issue forms in a new tab.
 * GitHub itself is never contacted; the new tab's request is answered
 * locally.
 */

const { test, expect, submitPassphrase } = require('./fixtures');

const HOME_URL = '/tarmar/';
const ISSUES_URL = 'https://github.com/smarks/tarmar-studio/issues/new/choose';

/** Top edge of a locator's box, rounded to the pixel. */
async function topOf(locator) {
    const box = await locator.boundingBox();
    expect(box).not.toBeNull();
    return Math.round(box.y);
}

test.describe('Leave a comment', () => {
    for (const viewport of [
        { width: 1440, height: 900 },
        { width: 1024, height: 768 },
    ]) {
        test(`sits after Play on one row at ${viewport.width}px`, async ({ page }) => {
            await page.setViewportSize(viewport);
            await submitPassphrase(page);
            await page.goto(HOME_URL);

            const links = page.locator('.home-nav a');
            await expect(links.last()).toHaveText('Leave a comment');
            await expect(links.nth(-2)).toHaveText('Play');
            const firstTop = await topOf(links.first());
            expect(await topOf(links.last())).toBe(firstTop);
        });
    }

    test('wraps rather than scrolling sideways at 800px and 390px', async ({ page }) => {
        await submitPassphrase(page);
        for (const viewport of [{ width: 800, height: 900 }, { width: 390, height: 844 }]) {
            await page.setViewportSize(viewport);
            await page.goto(HOME_URL);
            await expect(page.locator('.home-nav a').last()).toBeVisible();
            const overflow = await page.evaluate(
                () => document.documentElement.scrollWidth - document.documentElement.clientWidth
            );
            expect(overflow, `home scrolls sideways at ${viewport.width}px`).toBeLessThanOrEqual(0);
        }
    });

    test('opens the GitHub issues page in a new tab', async ({ page, context }) => {
        await context.route('https://github.com/**', (route) => route.fulfill({
            status: 200, contentType: 'text/html', body: '<title>issues</title>',
        }));
        await submitPassphrase(page);
        await page.goto(HOME_URL);

        const [issuesTab] = await Promise.all([
            context.waitForEvent('page'),
            page.locator('.home-nav a', { hasText: 'Leave a comment' }).click(),
        ]);
        await issuesTab.waitForLoadState();
        expect(issuesTab.url()).toBe(ISSUES_URL);
        await expect(page).toHaveURL(/\/tarmar\/$/);
    });
});
