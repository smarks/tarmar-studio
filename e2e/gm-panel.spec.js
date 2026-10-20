/**
 * E2E tests for the GM chat's side panel — the Characters tab (issue #341).
 *
 * Attaching a character needs no Ollama: the attach endpoint creates the
 * session and the page re-renders the character's sheet from the model. The
 * seeded 'Aldwin the Bold' has every attribute at 12 and a fatigue roll of
 * 7, so Fatigue = 12 + 12 + 12 + 12 + 7 = 55 and Body = ceil(55 * 2/3) = 37.
 *
 * Once a session exists, attaching, detaching and deleting another session
 * change the page in place (issue #15): a reload threw away every earlier
 * page of the chat the reader had loaded. A mark left on the window before
 * each change is still there after it only if the page was not reloaded.
 */

/** Leave a mark on the page that a reload would wipe. */
async function markThePage(page) {
    await page.evaluate(() => { window.issue15NotReloaded = true; });
}

/** Whether the mark left by markThePage is still there. */
async function stillTheSamePage(page) {
    return page.evaluate(() => window.issue15NotReloaded === true);
}

/** Attach ``name`` from a blank chat, which makes a session; its id. */
async function sessionWith(page, name) {
    await page.goto('/tarmar/gm/');
    await chooseToAttach(page, name);
    await page.click('#gm-character-attach');
    await page.waitForURL(/\/tarmar\/gm\/\?session=\d+$/);
    return new URL(page.url()).searchParams.get('session');
}

const {
    test, expect, loginAs, TEST_USER, chooseToAttach, searchPicker,
} = require('./fixtures');

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

        await chooseToAttach(page, 'Aldwin the Bold');
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

        // The attached character has left the picker; a search finds the
        // other, whatever page it is on (#864).
        await searchPicker(page, '#gm-attach-picker-search', 'Aldwin the Bold');
        await expect(page.locator('#gm-character-select option', { hasText: 'Aldwin the Bold' })).toHaveCount(0);
        await searchPicker(page, '#gm-attach-picker-search', 'Mira Quickhand');
        await expect(page.locator('#gm-character-select option', { hasText: 'Mira Quickhand' })).toHaveCount(1);

        // The seeded characters carry nothing, so the Battle tab warns (#844).
        const unarmed = page.locator('#gm-panel-unarmed');
        await expect(unarmed).toContainText('Aldwin the Bold carries no weapon');
        await expect(unarmed).toHaveJSProperty('hidden', false);

        const sessionUrl = page.url();
        await markThePage(page);
        await sheet.locator('.gm-sheet-detach').click();
        await expect(page.locator('.gm-sheet')).toHaveCount(0);
        await expect(page.locator('#gm-sheets-empty')).toBeVisible();
        // Nobody bare-handed is attached now: the warning is repainted away
        // from the detach's answer, with no reload to render it again.
        await expect(unarmed).toHaveText('');
        await expect(unarmed).toHaveJSProperty('hidden', true);
        await searchPicker(page, '#gm-attach-picker-search', 'Aldwin the Bold');
        await expect(page.locator('#gm-character-select option', { hasText: 'Aldwin the Bold' })).toHaveCount(1);
        expect(await stillTheSamePage(page)).toBe(true);

        // Attached again to the session the page is on: in place as well.
        await page.selectOption('#gm-character-select', { label: 'Aldwin the Bold' });
        await page.click('#gm-character-attach');
        await expect(page.locator('.gm-sheet .gm-sheet-name')).toHaveText('Aldwin the Bold');
        await expect(unarmed).toContainText('Aldwin the Bold carries no weapon');
        await expect(unarmed).toHaveJSProperty('hidden', false);
        expect(page.url()).toBe(sessionUrl);
        expect(await stillTheSamePage(page)).toBe(true);
    });

    test('deleting another session takes it off the rail without reloading the page', async ({ page }) => {
        const gone = await sessionWith(page, 'Aldwin the Bold');
        const kept = await sessionWith(page, 'Mira Quickhand');
        const rail = page.locator('#session-list');
        await expect(rail.locator(`.session-item[data-id="${gone}"]`)).toHaveCount(1);
        // The rail's count line, as the server rendered it with the page.
        const railCount = page.locator('#session-rail-picker [data-picker-status]');
        const countBefore = Number.parseInt(await railCount.textContent(), 10);
        expect(countBefore).toBeGreaterThanOrEqual(2);
        await markThePage(page);

        await rail.locator(`.session-item[data-id="${gone}"] .session-delete-btn`).click();
        const question = page.locator('.notice-confirm');
        await question.locator('button:has-text("Delete")').click();

        await expect(rail.locator(`.session-item[data-id="${gone}"]`)).toHaveCount(0);
        await expect(page.locator(`#mobile-session-select option[value="${gone}"]`)).toHaveCount(0);
        // The rail was asked for again: its count is the server's, one fewer,
        // and it still marks the open session.
        await expect(railCount).toHaveText(new RegExp(`^\\s*${countBefore - 1} `));
        await expect(rail.locator(`.session-item.active[data-id="${kept}"]`)).toHaveCount(1);
        await expect(page.locator('.gm-sheet .gm-sheet-name')).toHaveText('Mira Quickhand');
        expect(new URL(page.url()).searchParams.get('session')).toBe(kept);
        expect(await stillTheSamePage(page)).toBe(true);
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
