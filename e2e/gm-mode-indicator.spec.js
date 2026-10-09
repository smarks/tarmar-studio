/**
 * E2E for the GM chat's mode indicator and mode control (issues #353, #372).
 *
 * A Game Master consulting the aide at a real table needs to see which GM
 * they are talking to — and, since #372, to change it, because a DM who
 * wanted to play a game could not reach one. The strip is part of the page,
 * above the transcript, with nothing to dismiss and nothing that hides it
 * after a moment; changing the mode reloads the page from the stored value
 * rather than flashing anything at the reader. The seeded 'admin' is an admin
 * and a DM; 'testuser' has neither role.
 */

const { test, expect, loginAs, TEST_USER, TEST_ADMIN } = require('./fixtures');

test.describe('GM chat — mode indicator', () => {
    test('a Game Master sees the aide badge, and it stays', async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        await page.goto('/tarmar/gm/');

        const indicator = page.locator('#gm-mode-indicator');
        await expect(indicator).toBeVisible();
        await expect(indicator.locator('.gm-mode-name')).toHaveText('GM aide');
        await expect(indicator).toContainText('You are running the game; it is not.');

        // Above the transcript, inside the chat column — not floating over it.
        const messages = page.locator('#messages');
        const indicatorBox = await indicator.boundingBox();
        const messagesBox = await messages.boundingBox();
        expect(indicatorBox.y + indicatorBox.height).toBeLessThanOrEqual(messagesBox.y + 1);

        // Nothing dismisses it: still there after the page has settled and
        // after a reload.
        await page.waitForTimeout(1500);
        await expect(indicator).toBeVisible();
        await page.reload();
        await expect(page.locator('#gm-mode-indicator')).toBeVisible();
    });

    test('a player sees the strip naming their own mode', async ({ page }) => {
        // Before #372 a player saw nothing here, because play was the only
        // mode they could have. They can switch now, so the strip is theirs
        // too — naming the Game Master who runs the game, not the aide.
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/gm/');

        const indicator = page.locator('#gm-mode-indicator');
        await expect(indicator).toBeVisible();
        await expect(indicator.locator('.gm-mode-name')).toHaveText('Game Master');
        await expect(page.locator('#gm-mode-select')).toHaveValue('play');
    });

    test('a Game Master switches to play, and the page comes back saying so',
         async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        await page.goto('/tarmar/gm/');

        const select = page.locator('#gm-mode-select');
        await expect(select).toHaveValue('aide');
        await expect(page.locator('#user-input')).toHaveAttribute('placeholder',
                                                                  'Ask the aide');

        // Choosing submits the form; the server stores the choice and renders
        // the page from it. Nothing appears and disappears on its own.
        await select.selectOption('play');
        await page.waitForURL(/mode=play/);

        await expect(page.locator('#gm-mode-indicator .gm-mode-name'))
            .toHaveText('Game Master');
        await expect(page.locator('#gm-mode-select')).toHaveValue('play');
        await expect(page.locator('#user-input')).toHaveAttribute('placeholder',
                                                                  'What do you do?');
        await expect(page.locator('.empty-state')).toContainText('what you\'d like to do');

        // And it survives a reload: it is stored state, not a page-local flag.
        await page.reload();
        await expect(page.locator('#gm-mode-select')).toHaveValue('play');
    });

    test('the control is a form on the page, not an item in a menu',
         async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        await page.goto('/tarmar/gm/');

        const form = page.locator('#gm-mode-indicator form.gm-mode-switch');
        await expect(form).toBeVisible();
        await expect(form).toHaveAttribute('action', '/tarmar/gm/mode/');
        // Both modes are offered, in one place, without opening anything.
        await expect(page.locator('#gm-mode-select option')).toHaveCount(2);
    });
});
