/**
 * E2E for the full-window GM pages fitting the window (issue #431).
 *
 * The chat and map pages subtracted a guessed 60px header from the window,
 * but the header renders taller (75px on desktop, two rows on a phone), so
 * the bottom of each page sat below the fold and the phone panel overlapped
 * the header. Sizes are measured, never assumed.
 */

const { test, expect, TEST_USER } = require('./fixtures');
const { runDjangoShell } = require('./django');

/** Enough sessions that the rail is taller than any window. */
const LONG_RAIL = 60;
/** The name the spec gives them, and removes them by. */
const RAIL_ROW_PREFIX = 'Rail row ';
/** Enough replies that the transcript is more than twice the window. */
const LONG_TRANSCRIPT = 80;

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

    test(`a long session rail scrolls inside the page, not the window (${viewport.name})`, async ({ authenticatedPage: page }) => {
        // The rail's rows carry visually hidden words (issue #62); positioned
        // against the page they hung below the window and made it scroll.
        // The rows are the spec's own, and go when it is done: later specs
        // for this account must not meet them.
        const username = JSON.stringify(TEST_USER.username);
        const prefix = JSON.stringify(RAIL_ROW_PREFIX);
        try {
            runDjangoShell(`
from django.contrib.auth import get_user_model
from gm.models import GMSession
user = get_user_model().objects.get(username=${username})
GMSession.objects.bulk_create(
    [GMSession(user=user, name=${prefix} + str(index)) for index in range(${LONG_RAIL})]
)
`.trim());
            await page.setViewportSize({ width: viewport.width, height: viewport.height });
            await page.goto('/tarmar/gm/');
            await expect(page.locator('#session-list .session-item').first()).toBeAttached();

            const overflow = await page.evaluate(
                () => document.scrollingElement.scrollHeight - window.innerHeight,
            );
            expect(overflow).toBeLessThanOrEqual(1);
        } finally {
            runDjangoShell(`
from django.contrib.auth import get_user_model
from gm.models import GMSession
user = get_user_model().objects.get(username=${username})
GMSession.objects.filter(user=user, name__startswith=${prefix}).delete()
`.trim());
        }
    });

    test(`a visually hidden word deep in a long transcript stays in the transcript (${viewport.name})`, async ({ authenticatedPage: page }) => {
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await page.goto('/tarmar/gm/');
        // The page's own renderer, enough replies to run far below the window,
        // and a .sr-only word at the very end, as a label would be.
        await page.evaluate((count) => {
            for (let index = 0; index < count; index += 1) {
                addMessage('assistant', `Reply number ${index}, a line of the story.`);
            }
            const last = document.querySelector('#messages .message:last-child .message-content');
            last.insertAdjacentHTML('beforeend', '<span class="sr-only">hidden word</span>');
        }, LONG_TRANSCRIPT);

        const heights = await page.evaluate(() => ({
            transcript: document.getElementById('messages').scrollHeight,
            window: window.innerHeight,
            overflow: document.scrollingElement.scrollHeight - window.innerHeight,
        }));
        // The transcript really did run past the window, so the check bites.
        expect(heights.transcript).toBeGreaterThan(2 * heights.window);
        expect(heights.overflow).toBeLessThanOrEqual(1);
    });

    test(`the map page fits the window (${viewport.name})`, async ({ authenticatedPage: page }) => {
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        const response = await page.goto('/tarmar/gm/map/');
        expect(response.status()).toBe(200);

        const map = await page.locator('.gm-map-page').boundingBox();
        expect(map.y + map.height).toBeLessThanOrEqual(viewport.height);
    });
}
