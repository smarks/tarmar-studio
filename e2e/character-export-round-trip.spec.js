/**
 * A character the sheet can produce must survive its own export and import.
 *
 * Issue #637: the free-text roll boxes autosave an empty string happily, and
 * the importer's full_clean then refused it, so a character could be built in
 * the browser whose own export would not come back.
 *
 * Issue #633: the Markdown export read a catalog item's name, weight and
 * category off the stored entry, which carries none of them, so every catalog
 * item printed as "Unknown ... 0 lbs ... -".
 */

const {
    test, expect, loginAs, TEST_USER, TEST_ADMIN, createCharacter,
} = require('./fixtures');

/**
 * Export the character the page is on, then import the file through the list
 * page's form, and wait for the imported character's sheet.
 * @param {import('@playwright/test').Page} page
 * @param {(payload: object) => void} [edit] - Changes the file before import.
 * @returns {Promise<string>} The exported file's text.
 */
async function exportAndImport(page, edit) {
    const characterId = page.url().match(/\/characters\/(\d+)\//)[1];
    const exported = await page.evaluate(async (id) => {
        const response = await fetch(`/tarmar/export/${id}/json/`);
        return { status: response.status, body: await response.text() };
    }, characterId);
    expect(exported.status).toBe(200);
    const payload = JSON.parse(exported.body);
    if (edit) edit(payload);
    await page.goto('/tarmar/characters/');
    await page.click('#import-character-toggle');
    await page.setInputFiles('#import-file', {
        name: 'character.json',
        mimeType: 'application/json',
        buffer: Buffer.from(JSON.stringify(payload), 'utf-8')
    });
    await page.click('#import-character-form button[type="submit"]');
    await page.waitForURL((url) => /\/characters\/\d+\/$/.test(url.pathname)
        && !url.pathname.includes(`/characters/${characterId}/`));
    return exported.body;
}

/** Clear one autosaving free-text cell and wait for the save to land. */
async function clearField(page, field) {
    const cell = page.locator(`.editable-field[data-field="${field}"] input`);
    await cell.fill('');
    await cell.blur();
    await expect(
        page.locator(`.editable-field[data-field="${field}"]`)
    ).toHaveClass(/saved/);
}

test.describe('Character export round trip', () => {
    test('a character with its free-text rolls cleared imports again', async ({
        page
    }) => {
        // A Game Master's character: appearance is a roll only a Game Master
        // may clear or retype (issue #748).
        await loginAs(page, TEST_ADMIN);
        await createCharacter(page);
        const editUrl = page.url();
        const characterId = editUrl.match(/\/characters\/(\d+)\//)[1];

        await clearField(page, 'appearance_roll');
        await clearField(page, 'height_roll');
        await clearField(page, 'weight_roll');

        // The character's own JSON export, fetched as the signed-in user.
        const exported = await page.evaluate(async (id) => {
            const response = await fetch(`/tarmar/export/${id}/json/`);
            return { status: response.status, body: await response.text() };
        }, characterId);
        expect(exported.status).toBe(200);
        expect(JSON.parse(exported.body).data.appearance_roll).toBe('');

        // And back in through the list page's import form.
        await page.goto('/tarmar/characters/');
        await page.click('#import-character-toggle');
        await page.setInputFiles('#import-file', {
            name: 'character.json',
            mimeType: 'application/json',
            buffer: Buffer.from(exported.body, 'utf-8')
        });
        await page.click('#import-character-form button[type="submit"]');

        await page.waitForURL(/\/characters\/\d+\//);
        expect(page.url()).not.toContain(`/characters/${characterId}/`);
        await expect(page.locator('#import-error')).toHaveCount(0);
    });

    test('a player\'s import arrives unrolled, and says so until dismissed (#744)', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await createCharacter(page);

        // A file edited to the best roll, the case #744 is about; the
        // player's own unedited export arrives the same way.
        await exportAndImport(page, (payload) => { payload.data.strength = 18; });

        // The dice were not kept: the sheet marks what it needs and says why.
        await expect(page.locator('#incomplete-marks')).toContainText('needs attributes');
        await expect(page.locator('#incomplete-marks')).toContainText('needs Fatigue roll');
        const notice = page.locator('#import-notice');
        await expect(notice).toBeVisible();
        await expect(notice).toContainText('a new, living character');
        await expect(notice).toContainText('STR 18');

        // It stays across a reload, and goes only when dismissed.
        await page.reload();
        await expect(notice).toBeVisible();
        await page.locator('#dismiss-import-notice-btn').click();
        await expect(notice).toHaveCount(0);
        await page.reload();
        await expect(notice).toHaveCount(0);
    });

    test('a Game Master\'s import is kept as it was exported (#744)', async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        await createCharacter(page);
        const strength = await page.locator('.editable-field[data-field="strength"] input').inputValue();

        await exportAndImport(page);

        await expect(page.locator('.editable-field[data-field="strength"] input')).toHaveValue(strength);
        await expect(page.locator('#import-notice')).toHaveCount(0);
        await expect(page.locator('#incomplete-marks')).toHaveCount(0);
    });

    test('the Markdown export names catalog equipment', async ({ adminPage: page }) => {
        await createCharacter(page);
        const characterId = page.url().match(/\/characters\/(\d+)\//)[1];

        // Add a catalog item the way the sheet does: item_id only.
        const added = await page.evaluate(async (id) => {
            const token = document.querySelector('[name=csrfmiddlewaretoken]')?.value
                || document.cookie.match(/csrftoken=([^;]+)/)[1];
            const response = await fetch(`/tarmar/characters/${id}/equipment/add/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': token
                },
                // Given by a Game Master: catalog gear is bought (issue #757)
                // and two broadswords are past a rolled character's purse.
                body: JSON.stringify({ item_id: 'broadsword', quantity: 2, free: true })
            });
            return response.status;
        }, characterId);
        expect(added).toBe(200);

        const markdown = await page.evaluate(async (id) => {
            const response = await fetch(`/tarmar/export/${id}/markdown/`);
            return response.text();
        }, characterId);

        expect(markdown).toContain('| Broadsword | 2 | 10.0 lbs |');
        expect(markdown).not.toContain('| Unknown |');
    });
});
