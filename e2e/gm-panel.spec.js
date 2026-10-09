/**
 * E2E tests for the GM chat's side panel — the Characters tab (issue #341).
 *
 * Attaching a character needs no Ollama: the attach endpoint creates the
 * session and the page re-renders the character's sheet from the model. The
 * seeded 'Aldwin the Bold' has every attribute at 12 and a fatigue roll of
 * 7, so Fatigue = 12 + 12 + 12 + 12 + 7 = 55 and Body = ceil(55 * 2/3) = 37.
 */

const { test, expect, loginAs, TEST_USER } = require('./fixtures');

test.describe('GM panel — Characters tab', () => {
    test.beforeEach(async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/gm/');
    });

    test('attaching a character shows its sheet and detaching removes it', async ({ page }) => {
        const panel = page.locator('#gm-panel');
        await expect(panel).toBeVisible();
        await expect(page.locator('#gm-panel-tab-characters')).toHaveAttribute('aria-pressed', 'true');
        await expect(page.locator('#gm-panel-characters')).toBeVisible();
        await expect(page.locator('#gm-sheets-empty')).toBeVisible();

        await page.selectOption('#gm-character-select', { label: 'Aldwin the Bold' });
        await page.click('#gm-character-attach');

        // The attach created a session; the page reloads on it.
        await page.waitForURL(/\/tarmar\/gm\/\?session=\d+$/);
        const sheet = page.locator('.gm-sheet');
        await expect(sheet).toHaveCount(1);
        await expect(sheet.locator('.gm-sheet-name')).toHaveText('Aldwin the Bold');
        await expect(sheet.locator('.gm-sheet-name a')).toHaveAttribute('href', /\/tarmar\/characters\/\d+\/$/);
        await expect(sheet.locator('.gm-sheet-status')).toHaveText('Alive');
        await expect(sheet.locator('.gm-sheet-pools')).toContainText('Fatigue');
        await expect(sheet.locator('.gm-sheet-pools')).toContainText('55');
        await expect(sheet.locator('.gm-sheet-pools')).toContainText('37');
        await expect(sheet.locator('.gm-sheet-attributes')).toContainText('STR');

        // The attached character has left the picker.
        await expect(page.locator('#gm-character-select option', { hasText: 'Aldwin the Bold' })).toHaveCount(0);
        await expect(page.locator('#gm-character-select option', { hasText: 'Mira Quickhand' })).toHaveCount(1);

        await sheet.locator('.gm-sheet-detach').click();
        await expect(page.locator('.gm-sheet')).toHaveCount(0);
        await expect(page.locator('#gm-sheets-empty')).toBeVisible();
        await expect(page.locator('#gm-character-select option', { hasText: 'Aldwin the Bold' })).toHaveCount(1);
    });

    test('the panel collapses on a small screen and the Panel button opens it', async ({ page }) => {
        await page.setViewportSize({ width: 600, height: 900 });
        await expect(page.locator('#gm-panel')).toBeHidden();
        await page.click('#gm-panel-toggle');
        await expect(page.locator('#gm-panel')).toBeVisible();
        await expect(page.locator('#gm-panel-toggle')).toHaveAttribute('aria-expanded', 'true');
        await page.click('#gm-panel-toggle');
        await expect(page.locator('#gm-panel')).toBeHidden();
    });
});
