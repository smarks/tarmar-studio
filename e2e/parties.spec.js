/**
 * E2E test for parties (issue #274), the whole chain in one journey:
 * user A rolls a character and makes it public; user B — a role-less
 * registered user — forms a party, seats A's character through the
 * add-member shelf, and A then withdraws it (withdraw always wins: A
 * manages nothing about B's party except their own character's seat).
 */

const { test, expect, loginAs, logout, createCharacter, TEST_USER, TEST_WATCHER } = require('./fixtures');

test.describe('Parties', () => {
    test('A publishes a character, B parties it up, A withdraws it', async ({ page }) => {
        // A rolls a character, names it uniquely, and flips it public.
        await loginAs(page, TEST_USER);
        await createCharacter(page);
        const name = `Band Member ${Date.now()}`;
        const nameInput = page.locator('[data-field="name"] input');
        await nameInput.fill(name);
        await nameInput.blur();
        await page.locator('#visibility-toggle').check();
        await expect(page.locator('#visibility-badge')).toHaveText('Public');
        await logout(page);

        // B (no roles at all) reaches Parties from the nav and forms one.
        await loginAs(page, TEST_WATCHER);
        await page.goto('/tarmar/');
        await page.click('#menuToggle');
        await page.click('nav a:has-text("Parties")');
        await page.waitForURL('**/tarmar/parties/');
        await page.click('a:has-text("Form a New Party")');
        const partyName = `The Company ${Date.now()}`;
        await page.fill('#id_name', partyName);
        await page.fill('#id_description', 'Formed by a bystander, as any user may.');
        await page.click('button:has-text("Form Party")');
        await page.waitForURL(/\/tarmar\/parties\/\d+\//);
        const partyUrl = new URL(page.url()).pathname;
        await expect(page.locator('h1')).toHaveText(partyName);

        // B seats A's public character through the filtered shelf; the
        // character joins immediately — no acceptance step.
        await page.fill('#party-character-filter', name);
        const shelfRow = page.locator('.party-addable-row', { hasText: name });
        await expect(shelfRow).toBeVisible();
        await shelfRow.locator('button:has-text("Add")').click();
        const rosterRow = page.locator('.party-member', { hasText: name });
        await expect(rosterRow).toBeVisible();
        // B manages the party, so the seat carries a Remove control; the
        // roster row links the sheet (it's public) and sums experience.
        await expect(rosterRow.locator('button:has-text("Remove")')).toBeVisible();
        await expect(page.locator('.party-summary')).toContainText('1 member');
        await logout(page);

        // A owns the character but not the party: the row offers Withdraw
        // (never Remove), and withdrawing empties the roster.
        await loginAs(page, TEST_USER);
        await page.goto(partyUrl);
        const ownRow = page.locator('.party-member', { hasText: name });
        await expect(ownRow.locator('button:has-text("Withdraw")')).toBeVisible();
        await expect(page.locator('a:has-text("Edit name")')).toHaveCount(0);
        await ownRow.locator('button:has-text("Withdraw")').click();
        // Withdrawing asks first (issue #339, item 19).
        const question = page.locator('.notice-confirm');
        await expect(question).toContainText(`Withdraw ${name} from`);
        await question.locator('button:has-text("Withdraw")').click();
        await expect(page.locator('.party-member', { hasText: name })).toHaveCount(0);
        await expect(page.locator('.party-none').first()).toContainText('No members yet');
    });

    test('the owner renames by autosave, disbands, and restores a party (#339 items 17-18)', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/parties/create/');
        const partyName = `Autosave Band ${Date.now()}`;
        await page.fill('#id_name', partyName);
        await page.click('button:has-text("Form Party")');
        await page.waitForURL(/\/tarmar\/parties\/\d+\//);
        const partyUrl = new URL(page.url()).pathname;

        // Item 17: the edit page has no Save button; each field saves itself.
        await page.click('a:has-text("Edit name")');
        await expect(page.locator('.party-form button, .party-form form')).toHaveCount(0);
        const renamed = `${partyName} Renamed`;
        await page.fill('#party-name', renamed);
        await page.locator('#party-name').blur();
        await expect(page.locator('[data-field="name"]')).toHaveClass(/saved/);
        await page.fill('#party-description', 'Line one.\nLine two.');
        await page.locator('#party-description').blur();
        await expect(page.locator('[data-field="description"]')).toHaveClass(/saved/);

        // A blank name is refused beside the field and put back.
        await page.fill('#party-name', '');
        await page.locator('#party-name').blur();
        await expect(page.locator('[data-field="name"]')).toHaveClass(/error/);
        await expect(page.locator('#party-name')).toHaveValue(renamed);

        await page.goto(partyUrl);
        await expect(page.locator('h1')).toHaveText(renamed);
        await expect(page.locator('.party-description')).toContainText('Line two.');

        // Item 18: disband asks first, then the party leaves the list.
        await page.click(`button:has-text("Disband ${renamed}")`);
        const question = page.locator('.notice-confirm');
        await expect(question).toContainText(`Disband ${renamed}?`);
        await question.locator('button:has-text("Disband")').click();
        await page.waitForURL('**/tarmar/parties/');
        await expect(page.locator('.party-table a', { hasText: renamed })).toHaveCount(0);

        const disbanded = page.locator('#party-disbanded-list .party-member', { hasText: renamed });
        await expect(disbanded).toBeVisible();
        await disbanded.locator('button:has-text("Restore")').click();
        await page.locator('.notice-confirm button:has-text("Restore")').click();
        await page.waitForURL(`**${partyUrl}`);
        await expect(page.locator('h1')).toHaveText(renamed);
    });
});
