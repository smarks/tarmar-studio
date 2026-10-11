/**
 * GM-tools table roller E2E (issue #809): from a GM-tools table page the
 * Game Master follows the Roll link, lands on a seeded URL, and sees every
 * table's result with its die and face. The seed link reproduces the same
 * rolls when opened again, and a player cannot reach the roller.
 *
 * Canon-agnostic: it reads what the page rendered rather than naming any
 * table, so it passes on the default canon and the Tarmar canon alike.
 * Uses the admin fixture user, who has the DM role in global-setup.
 */

const { test, expect } = require('./fixtures');

test.describe('GM-tools table roller', () => {
    test('the Roll link rolls the page and its seed link reproduces the result', async ({ adminPage }) => {
        await adminPage.goto('/tarmar/reference/dm-rules/gm-tools/job-generator/');
        await adminPage.click('#table-roller-link');

        await adminPage.waitForURL(/\/tarmar\/gm\/tools\/job-generator\/roll\/\?seed=\d+$/);
        const seededUrl = adminPage.url();
        const seed = new URL(seededUrl).searchParams.get('seed');

        const result = adminPage.locator('#table-roll-result');
        await expect(result).toBeVisible();
        await expect(result).toContainText(`seed ${seed}`);
        const rolls = result.locator('.table-roll');
        expect(await rolls.count()).toBeGreaterThan(0);
        await expect(rolls.first().locator('.roll-detail')).toHaveText(/^d\d+ → \d+$/);
        const firstRolls = await rolls.allTextContents();

        // Leave, then open the shared link fresh: the same seed, the same rolls.
        await adminPage.goto('/tarmar/reference/dm-rules/gm-tools/');
        await adminPage.goto(seededUrl);
        await expect(adminPage.locator('#table-roll-result .table-roll')).toHaveText(firstRolls);

        // Rolling again from the form lands on a new seeded URL, never a dead end.
        await adminPage.click('#table-roll-form button');
        await adminPage.waitForURL(/\/roll\/\?seed=\d+$/);
        await expect(adminPage.locator('#table-roll-result')).toBeVisible();
    });

    test('players cannot reach the table roller', async ({ authenticatedPage }) => {
        const response = await authenticatedPage.goto('/tarmar/gm/tools/job-generator/roll/?seed=1');
        expect(response.status()).toBe(403);
    });
});
