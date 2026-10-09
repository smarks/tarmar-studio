/**
 * The classic arena's front door (issue #265, unification phase 5).
 *
 * What only a browser proves: the theme is chosen by the hostname the request
 * actually carries, the themed sheet is fetched and applied (not merely
 * linked), and the default host is left exactly as it was.
 *
 * The themed host here is `melee.localhost`, which Chromium resolves to
 * loopback with no DNS involved. That is deliberate: the datahorde cutover to
 * melee.origamisoftware.com is Spencer's and has not happened, and theming
 * that could not be driven until it did would ship untested.
 */

const {
    test, expect, ARENA_ORIGIN, passGateOnCurrentPage,
} = require('./fixtures');

test.describe('The classic arena front door', () => {
    test('the arena front door needs no passphrase and no account', async ({ page }) => {
        // Issue #305: the themed host's front door is the battle list, and
        // a visitor arrives at it rather than at a password prompt.
        const guest = await page.context().browser().newContext();
        const guestPage = await guest.newPage();
        await guestPage.goto(`${ARENA_ORIGIN}/`);
        await guestPage.waitForURL(`${ARENA_ORIGIN}/tarmar/battles/`);
        await expect(guestPage.locator('input[name="passphrase"]')).toHaveCount(0);
        await expect(guestPage.locator('html')).toHaveAttribute('data-theme', 'classic-arena');
        await expect(guestPage.locator('h1')).toHaveText('Battle Royale Simulator');
        await expect(guestPage.locator('.guest-standing-title')).toBeVisible();
        await guest.close();
    });

    test('the themed host wears the arena skin', async ({ page }) => {
        // Asked at a gated room rather than at the root, which is public
        // since #305 — the gate is what this test is about.
        await page.goto(`${ARENA_ORIGIN}/tarmar/`);

        await expect(page.locator('html')).toHaveAttribute('data-theme', 'classic-arena');
        await expect(page).toHaveTitle('Melee');

        // The arena now wears the same white ground as Tarmar (issue #306):
        // the dark skin repainted the ground but not the components standing
        // on it, so the battle log and every notice rendered near-white on
        // near-white. Matching grounds is the fix, which costs this test its
        // old proof -- a dark body no longer distinguishes an applied sheet
        // from an unfetched one.
        const background = await page.evaluate(
            () => getComputedStyle(document.body).backgroundColor
        );
        expect(background).toBe('rgb(255, 255, 255)');

        // So the proof moves to something only the arena's own rules produce.
        // The gate is self-contained and never links base.css; this rule
        // lives in its inline <style>, under the theme attribute.
        const attribution = page.getByText('Steve Jackson Games');
        await expect(attribution).toBeVisible();
        await expect(attribution).toHaveCSS('color', 'rgb(102, 102, 102)');
    });

    test('the default host is untouched', async ({ page }) => {
        await page.goto('/');

        await expect(page.locator('html')).toHaveAttribute('data-theme', 'tarmar');
        await expect(page).toHaveTitle('Tarmar Studio');

        const background = await page.evaluate(
            () => getComputedStyle(document.body).backgroundColor
        );
        expect(background).toBe('rgb(255, 255, 255)');
        await expect(page.getByText('Steve Jackson Games')).toHaveCount(0);
    });

    test('the skin survives entering, where a second template takes over', async ({ page }) => {
        // The gate and base.html are themed by separate paths: the gate is
        // self-contained and never links base.css, so a visitor could meet a
        // themed door and Tarmar's chrome one click later. This is the test
        // that would catch that.
        await page.goto(`${ARENA_ORIGIN}/tarmar/`);
        await passGateOnCurrentPage(page);
        await page.goto(`${ARENA_ORIGIN}/tarmar/`);

        await expect(page.locator('html')).toHaveAttribute('data-theme', 'classic-arena');
        await expect(page.locator('.logo-name')).toHaveText('Melee');
        await expect(page.locator('.logo-tagline')).toHaveText('The classic arena');

        // theme-classic-arena.css was fetched and applied, not merely linked.
        // The wordmark's treatment is the arena's identity now that its
        // palette matches Tarmar's, and base.css styles neither of these
        // classes -- so uppercase here can only have come from that sheet.
        await expect(page.locator('.logo-name')).toHaveCSS('text-transform', 'uppercase');
        // A text wordmark, by design: Tarmar's mark must not ride under
        // another game's name. See common/theme.py.
        await expect(page.locator('.logo img')).toHaveCount(0);
    });
});
