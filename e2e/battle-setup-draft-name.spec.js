/**
 * E2E for a resumed battle draft being called by its name (issue #339, item 27).
 *
 * The setup page's heading, title and trail said "New battle" even for a
 * draft that had been named and left, which made a resumed draft look like a
 * fresh one. Only an unnamed draft is a "New battle" now.
 */

const { test, expect } = require('./fixtures');

test('a named draft is called by its name when the setup page is reopened', async ({ adminPage: page }) => {
    const battleName = `E2E Siege ${Date.now()}`;
    await page.goto('/tarmar/battles/');
    await page.click('.battle-list-masthead button[type="submit"]');
    await page.waitForURL(/\/tarmar\/battles\/setup\/\d+\/$/);
    await expect(page.locator('h1')).toHaveText('New battle');

    const nameInput = page.locator('input[name="name"]');
    await nameInput.fill(battleName);
    await Promise.all([
        page.waitForResponse((response) => response.url().includes('/update/') && response.ok()),
        nameInput.blur(),
    ]);

    await page.reload();

    await expect(page.locator('h1')).toHaveText(battleName);
    await expect(page.locator('.breadcrumb [aria-current="page"]')).toHaveText(battleName);
    await expect(page).toHaveTitle(new RegExp(battleName));
});
