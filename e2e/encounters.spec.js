/**
 * Encounter generator E2E: roll a wilderness encounter on the DM-gated page
 * and confirm the seeded URL makes the result reload-stable (the page's
 * state-driven contract — the query string is the state).
 */

const { test, expect } = require('./fixtures');

test.describe('Encounter generator', () => {
    test('DM rolls an encounter and the seeded URL reproduces it', async ({ adminPage }) => {
        await adminPage.goto('/tarmar/gm/encounters/');
        await expect(adminPage.locator('h1')).toHaveText('Encounter generator');

        await adminPage.selectOption('select[name="terrain"]', 'forest');
        await adminPage.click('button:has-text("Roll encounter")');

        // The POST redirects to a seeded GET carrying the whole result.
        await adminPage.waitForURL(/terrain=forest&seed=\d+/);
        const heading = adminPage.locator('.encounter-result h2');
        await expect(heading).toBeVisible();
        await expect(adminPage.locator('.encounter-rolls li').first()).toContainText('encounter table');

        // State-driven: a reload re-rolls nothing — same seed, same result.
        const rolled = await heading.textContent();
        await adminPage.reload();
        await expect(adminPage.locator('.encounter-result h2')).toHaveText(rolled);
    });

    test('a shared link with the seed trimmed off still rolls one', async ({ adminPage }) => {
        // The ordinary way to arrive here: a seeded URL pasted into chat and
        // mangled. The page used to answer a 404 whose message named an
        // empty string; it now rolls a seed and lands on the fully
        // specified URL, the way both ruin generators do (#687).
        await adminPage.goto('/tarmar/gm/encounters/?terrain=mountains');
        await adminPage.waitForURL(/terrain=mountains&seed=\d+/);
        await expect(adminPage.locator('.encounter-result h2')).toBeVisible();

        // And what it landed on is reload-stable, like any other seeded URL.
        const rolled = await adminPage.locator('.encounter-result h2').textContent();
        await adminPage.reload();
        await expect(adminPage.locator('.encounter-result h2')).toHaveText(rolled);
    });

    test('the catalog links to the generator', async ({ adminPage }) => {
        await adminPage.goto('/tarmar/gm/bestiary/');
        // Scoped to the toolbar: the site nav also carries an "Encounter
        // Generator" link now (issue #218), and a bare text selector matches
        // both.
        await adminPage.click('.bestiary-toolbar a:has-text("Encounter generator")');
        await adminPage.waitForURL(/gm\/encounters\/$/);
        await expect(adminPage.locator('h1')).toHaveText('Encounter generator');
    });

    test('players cannot reach the encounter generator', async ({ authenticatedPage }) => {
        const response = await authenticatedPage.goto('/tarmar/gm/encounters/');
        expect(response.status()).toBe(403);
    });
});
