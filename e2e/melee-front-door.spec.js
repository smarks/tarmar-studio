/**
 * The melee door lands on the arena (issue #269, unification phase 5).
 *
 * What only a browser proves: the whole chain — gate, root redirect, login
 * with no `next` of its own — actually deposits a visitor on the battle
 * list when the hostname is the arena's, and on the studio home when it is
 * not. Same `melee.localhost` trick as theme-classic-arena.spec.js: Chromium
 * resolves any *.localhost name to loopback, so the second front door can be
 * driven before DNS moves.
 */

const {
    test, expect, TEST_USER, createBattleViaSetup, loginAs, passGateOnCurrentPage,
    ARENA_ORIGIN,
} = require('./fixtures');

test.describe('The melee front door', () => {
    test('login on the arena host lands on the battle list', async ({ page }) => {
        await page.goto(`${ARENA_ORIGIN}/`);
        await passGateOnCurrentPage(page);
        await loginAs(page, TEST_USER, { origin: ARENA_ORIGIN });

        // No `next` was carried, so this is the door's own choice of room.
        await expect(page).toHaveURL(`${ARENA_ORIGIN}/tarmar/battles/`);
        await expect(page.locator('html')).toHaveAttribute('data-theme', 'classic-arena');
    });

    test('the arena menu is battles-first, with a quiet studio cross-link', async ({ page }) => {
        await page.goto(`${ARENA_ORIGIN}/`);
        await passGateOnCurrentPage(page);
        await loginAs(page, TEST_USER, { origin: ARENA_ORIGIN });

        await page.click('#menuToggle');
        const menu = page.locator('#menu');
        await expect(menu.locator('a[href="/tarmar/battles/"]')).toHaveText('Battles');
        await expect(menu.getByText('Tarmar Studio')).toBeVisible();
        await expect(menu.locator('button[form="logout-form"]')).toBeVisible();
        // The studio's rooms are the tarmar door's job.
        await expect(menu.locator('a[href="/tarmar/characters/"]')).toHaveCount(0);
        await expect(menu.locator('a[href="/tarmar/reference/"]')).toHaveCount(0);
        await expect(menu.locator('a[href="/tarmar/gm/"]')).toHaveCount(0);
    });

    test('a battle opened at the arena door defaults to classic rules', async ({ page }) => {
        // Issue #332. The door said Melee and the fight was six-phase Tarmar
        // unless the visitor noticed the dropdown. What only a browser can
        // prove is the whole chain in one go: this hostname, the create
        // button, the setup page's own initial selection, and the battle
        // that comes out the far end.
        test.setTimeout(120000);
        await page.goto(`${ARENA_ORIGIN}/`);
        await passGateOnCurrentPage(page);
        await loginAs(page, TEST_USER, { origin: ARENA_ORIGIN });

        // The dropdown opens on classic, having been told nothing.
        await page.click('.battle-list-masthead button[type="submit"]');
        await page.waitForURL(/\/tarmar\/battles\/setup\/\d+\/$/);
        await expect(page.locator('select[name="rules_profile"]'))
            .toHaveValue('classic-melee');

        // Straight through with no other input: muster, name, start. The
        // seat is what makes the battle pause in a lobby, which is where a
        // player is told what they are joining.
        const battleName = `E2E Arena Default ${Date.now()}`;
        await createBattleViaSetup(page, {
            origin: ARENA_ORIGIN, name: battleName, fighters: 2, seed: 332,
            playerSeats: 1,
        });

        await expect(page.locator('h1')).toHaveText(battleName);
        await expect(page.locator('#battle-status')).toHaveText('Lobby');
        await expect(page.locator('.lobby-rules')).toContainText('Classic Melee');
    });

    test('the studio door still opens on Tarmar rules', async ({ authenticatedPage }) => {
        // The other half: nothing about the default host moved.
        await authenticatedPage.goto('/tarmar/battles/');
        await authenticatedPage.click('.battle-list-masthead button[type="submit"]');
        await authenticatedPage.waitForURL(/\/tarmar\/battles\/setup\/\d+\/$/);
        await expect(authenticatedPage.locator('select[name="rules_profile"]'))
            .toHaveValue('tarmar');
    });

    test('the default host still lands on the studio home with the full nav', async ({ page }) => {
        // The fixture already passed the gate on the default origin.
        await page.goto('/');
        await expect(page).toHaveURL(/\/tarmar\/$/);

        await page.click('#menuToggle');
        const menu = page.locator('#menu');
        await expect(menu.locator('a[href="/tarmar/characters/"]')).toHaveText('Characters');
        await expect(menu.locator('a[href="/tarmar/reference/"]')).toHaveText('Reference');
    });
});
