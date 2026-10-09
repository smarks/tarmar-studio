/**
 * Site-wide navigation trail (issue #189).
 *
 * What only a browser proves: the trail renders on real pages, its steps are
 * real links, and following one lands where it says — including after a
 * redirect, which is where browser-history "back" fails.
 */

const { test, expect, createBattleViaSetup } = require('./fixtures');

test.describe('Navigation trail', () => {
    test('a character sheet trails back to the character list', async ({ adminPage }) => {
        await adminPage.goto('/tarmar/characters/');
        await expect(adminPage.locator('.breadcrumb [aria-current="page"]')).toHaveText('Characters');

        // The list's own trail is Tarmar › Characters; Tarmar goes home.
        await adminPage.locator('.breadcrumb a', { hasText: 'Tarmar' }).click();
        await expect(adminPage).toHaveURL(/\/tarmar\/$/);
        // The home page is the root, so it carries no trail of its own.
        await expect(adminPage.locator('.breadcrumb')).toHaveCount(0);
    });

    test('a battle trails back to the battle list after the create redirect', async ({ adminPage }) => {
        const battleName = `E2E Trail ${Date.now()}`;
        await adminPage.goto('/tarmar/battles/');
        await expect(adminPage.locator('.breadcrumb [aria-current="page"]')).toHaveText('Battles');

        await createBattleViaSetup(adminPage, { name: battleName, fighters: 2, seed: 3 });

        // Landed here by redirect; the trail still knows where it sits.
        await expect(adminPage.locator('.breadcrumb [aria-current="page"]')).toHaveText(battleName);
        await adminPage.locator('.breadcrumb a', { hasText: 'Battles' }).click();
        await expect(adminPage.locator('h1')).toHaveText('Battle Royale Simulator');
    });

    test('a beast trails back to its filtered category', async ({ adminPage }) => {
        await adminPage.goto('/tarmar/gm/bestiary/dire-wolf/');
        const trail = adminPage.locator('.breadcrumb');
        await expect(trail.locator('[aria-current="page"]')).toHaveText('Dire Wolf');

        // Bestiary › <Category> › Dire Wolf, with the category carrying its filter.
        await trail.locator('a', { hasText: 'Bestiary' }).click();
        await expect(adminPage.locator('h1')).toHaveText('Bestiary');
    });

    test('every DM page below home names where it sits', async ({ adminPage }) => {
        for (const [path, label] of [
            ['/tarmar/accounts/users/', 'Users'],
            ['/tarmar/reference/', 'Reference'],
            ['/tarmar/reference/public-rules/characters/prior-experience/skill-tracks/', 'Skill Tracks'],
            ['/tarmar/gm/ruins/', 'Ruin generator'],
            ['/tarmar/accounts/profile/', 'Profile'],
            ['/tarmar/characters/create/', 'New character'],
            ['/tarmar/gm/', 'Game Master'],
        ]) {
            await adminPage.goto(path);
            await expect(adminPage.locator('.breadcrumb [aria-current="page"]')).toHaveText(label);
            await expect(adminPage.locator('.breadcrumb a', { hasText: 'Tarmar' })).toBeVisible();
        }
    });

    test('the trail takes its own row on the two-column reference pages', async ({ adminPage }) => {
        // main is a flex row there (sidebar + content). As a plain flex child
        // the trail became a third column and squeezed the wide rules tables
        // into a sideways scroll — it must span the row instead.
        for (const path of [
            '/tarmar/reference/public-rules/characters/prior-experience/skill-tracks/',
            '/tarmar/reference/search/?q=skill',
        ]) {
            await adminPage.setViewportSize({ width: 1440, height: 900 });
            await adminPage.goto(path);
            await expect(adminPage.locator('.breadcrumb')).toBeVisible();

            const spansTheRow = await adminPage.evaluate(() => {
                const main = document.querySelector('main');
                const bar = main.querySelector(':scope > .breadcrumb');
                if (!bar) { return null; }
                const gap = parseFloat(getComputedStyle(main).columnGap) || 0;
                const room = main.clientWidth
                    - parseFloat(getComputedStyle(main).paddingLeft)
                    - parseFloat(getComputedStyle(main).paddingRight);
                return bar.getBoundingClientRect().width >= room - gap;
            });
            expect(spansTheRow).toBe(true);

            const overflow = await adminPage.evaluate(() => {
                const html = document.documentElement;
                return Math.max(
                    html.scrollWidth - html.clientWidth,
                    ...[...document.querySelectorAll('.content, .search-results')]
                        .map((el) => el.scrollWidth - el.clientWidth)
                );
            });
            expect(overflow).toBeLessThanOrEqual(1);
        }
    });
});
