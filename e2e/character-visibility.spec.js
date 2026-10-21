/**
 * E2E test for the public/private character visibility flag (issue #273).
 *
 * The whole round trip in one journey: the owner creates a character (private
 * by default), flips it public with the sheet's toggle, another registered
 * user then reads the sheet with no edit affordances anywhere, and the owner
 * flips it back — at which point the other user is locked out again.
 */

const { test, expect, loginAs, logout, createCharacter, TEST_USER, TEST_WATCHER } = require('./fixtures');
const { uniqueName } = require('./unique-name');

test.describe('Character visibility', () => {
    test('owner flips public, another user reads it read-only, owner flips back', async ({ page }) => {
        // The owner rolls a fresh character: private by default.
        await loginAs(page, TEST_USER);
        await createCharacter(page);
        const sheetUrl = new URL(page.url()).pathname;

        const badge = page.locator('#visibility-badge');
        await expect(badge).toHaveText('Private');

        // Rename it so the read-only assertions look for something unique.
        const name = uniqueName('Public Figure');
        const nameInput = page.locator('[data-field="name"] input');
        await nameInput.fill(name);
        await nameInput.blur();
        await expect(badge).toHaveText('Private'); // still private after edits

        // Flip it public with the sheet's toggle; the badge follows in place.
        await page.locator('#visibility-toggle').check();
        await expect(badge).toHaveText('Public');
        await logout(page);

        // Another registered user (no roles at all) can now open the sheet...
        await loginAs(page, TEST_WATCHER);
        await page.goto(sheetUrl);
        await expect(page.locator('h1')).toHaveText(name);
        await expect(badge).toHaveText('Public');

        // ...but reads it with no edit affordances: no autosave inputs, no
        // visibility toggle, no delete, no workbench roll, no attack form,
        // no equipment controls beyond filter/sort.
        await expect(page.locator('.autosave')).toHaveCount(0);
        await expect(page.locator('#visibility-toggle')).toHaveCount(0);
        await expect(page.locator('#delete-character-form')).toHaveCount(0);
        await expect(page.locator('#roll-years-btn')).toHaveCount(0);
        await expect(page.locator('#attack-form')).toHaveCount(0);
        await expect(page.locator('#add-equipment-btn')).toHaveCount(0);
        await expect(page.locator('#equipment-bulk-delete')).toHaveCount(0);
        await expect(page.locator('.allocate-btn')).toHaveCount(0);

        // The list shows the public row with its badge and owner, but no
        // delete button for someone else's character.
        await page.goto('/tarmar/characters/');
        const row = page.locator('tr', { hasText: name });
        await expect(row).toHaveCount(1);
        await expect(row.locator('.visibility-public')).toHaveText('Public');
        await expect(row).toContainText(TEST_USER.username);
        await expect(row.locator('button:has-text("Delete")')).toHaveCount(0);
        await logout(page);

        // The owner flips it back to private...
        await loginAs(page, TEST_USER);
        await page.goto(sheetUrl);
        await page.locator('#visibility-toggle').uncheck();
        await expect(badge).toHaveText('Private');
        await logout(page);

        // ...and the other user is locked out again.
        await loginAs(page, TEST_WATCHER);
        const response = await page.goto(sheetUrl);
        expect(response.status()).toBe(404);
    });
});
