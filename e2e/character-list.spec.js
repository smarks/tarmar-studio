/**
 * E2E tests for character list page.
 * These tests verify that JavaScript functionality works correctly,
 * including the csrfToken being accessible to onclick handlers.
 *
 * Every test that needs a row seeds one (issue #642). Eleven of them used to
 * open with `if (!await page.locator('.character-table').count()) test.skip()`.
 * That table renders only inside {% if characters %}, so the exact regressions
 * these tests exist to catch -- the list query returning nothing, the
 * visibility rule excluding the user's own rows, the login fixture failing --
 * turned every one of them green by skipping instead of red.
 */

const {
    test, expect, loginAs, createNamedCharacter, TEST_USER, TEST_ADMIN,
} = require('./fixtures');

/**
 * Create a character owned by whoever this page is signed in as, and land
 * back on the character list with its row present.
 * @param {import('@playwright/test').Page} page
 * @param {import('@playwright/test').TestInfo} testInfo
 * @param {string} label - A word for the name, so a failure names the test.
 * @returns {Promise<string>} the character's name
 */
async function seedListedCharacter(page, testInfo, label) {
    const name = `${label} ${testInfo.workerIndex}${Date.now()}`;
    await createNamedCharacter(page, name);
    await page.goto('/tarmar/characters/');
    await expect(page.locator('.character-table')).toBeVisible();
    await expect(page.locator('tr', { hasText: name })).toBeVisible();
    return name;
}

test.describe('Character List Page', () => {
    test.beforeEach(async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/characters/');
    });

    test('displays characters page', async ({ page }) => {
        await expect(page.locator('h1')).toContainText('Characters');
        await expect(page.locator('a:has-text("Create New Character")')).toBeVisible();
    });

    test('bulk actions bar is visible when characters exist', async ({ page }, testInfo) => {
        await seedListedCharacter(page, testInfo, 'Bulk Bar');

        await expect(page.locator('#bulk-actions')).toBeVisible();
    });

    test('delete button is disabled when no characters selected', async ({ page }, testInfo) => {
        await seedListedCharacter(page, testInfo, 'Nothing Picked');

        await expect(page.locator('#delete-selected-btn')).toBeDisabled();
    });
});

test.describe('Character Selection', () => {
    test.beforeEach(async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/characters/');
    });

    test('selecting checkbox enables delete button', async ({ page }, testInfo) => {
        await seedListedCharacter(page, testInfo, 'Picked');

        const deleteBtn = page.locator('#delete-selected-btn');
        await expect(deleteBtn).toBeDisabled();

        // Select first character
        await page.locator('.character-checkbox').first().check();

        // Delete button should now be enabled
        await expect(deleteBtn).toBeEnabled();
    });

    test('select-all checkbox selects all characters', async ({ page }, testInfo) => {
        await seedListedCharacter(page, testInfo, 'All Picked');
        const checkboxCount = await page.locator('.character-checkbox').count();
        expect(checkboxCount).toBeGreaterThan(0);

        // Click select-all
        await page.locator('#select-all').check();

        // All checkboxes should be checked
        const allChecked = await page.locator('.character-checkbox').evaluateAll(
            checkboxes => checkboxes.every(cb => cb.checked)
        );
        expect(allChecked).toBe(true);

        // Count should match total
        const countText = await page.locator('#selected-count').textContent();
        expect(parseInt(countText)).toBe(checkboxCount);
    });

    test('deselecting all disables delete button', async ({ page }, testInfo) => {
        await seedListedCharacter(page, testInfo, 'Unpicked');

        // Select all
        await page.locator('#select-all').check();

        // Deselect all
        await page.locator('#select-all').uncheck();

        // Delete button should be disabled
        await expect(page.locator('#delete-selected-btn')).toBeDisabled();
    });
});

test.describe('Character Delete - csrfToken Accessibility', () => {
    /**
     * These tests verify that the CSRF token is reachable from the onclick
     * handlers. They would catch the bug where the token was defined inside
     * DOMContentLoaded and not accessible globally.
     *
     * Since issue #338 the page no longer declares a csrfToken constant of
     * its own: base.html carries the token in a meta tag and api.js's
     * csrfToken() reads it, so what the handlers reach for is that function.
     */
    test('delete button onclick can access csrfToken', async ({ page }, testInfo) => {
        await loginAs(page, TEST_USER);
        await seedListedCharacter(page, testInfo, 'Token Reader');

        // Verify csrfToken() is published in global scope
        const csrfTokenDefined = await page.evaluate(() => typeof csrfToken === 'function');
        expect(csrfTokenDefined).toBe(true);
    });

    test('per-row delete button can access csrfToken', async ({ page }, testInfo) => {
        await loginAs(page, TEST_USER);
        await seedListedCharacter(page, testInfo, 'Row Token');

        // Since #338 item 19 the button carries its {% url %}-rendered
        // endpoint in data-delete-url, and character-management.js reads
        // the token through csrfToken() when it is clicked.
        const deleteUrl = await page.locator('button.delete-btn.action-btn').first().getAttribute('data-delete-url');
        expect(deleteUrl).toMatch(/\/tarmar\/characters\/\d+\/delete\/$/);

        // Verify the handler would work by doing what the click handler does.
        const canAccessToken = await page.evaluate(() => {
            try {
                const token = csrfToken();
                return typeof token === 'string' && token.length > 0;
            } catch {
                return false;
            }
        });
        expect(canAccessToken).toBe(true);
    });

    test('clicking delete asks in the page, naming the character', async ({ page }, testInfo) => {
        // This used to register page.on('dialog') and put both of its
        // expect() calls inside that handler. Since #190 the app confirms
        // through an in-page .notice-confirm panel, so no native dialog is
        // ever raised, the handler never fired, and the body held no
        // assertion outside it: a test that could only pass (issue #642).
        await loginAs(page, TEST_USER);
        const name = await seedListedCharacter(page, testInfo, 'Asked About');

        await page.locator('tr', { hasText: name })
            .locator('button.delete-btn.action-btn').click();

        const panel = page.locator('.notice-confirm');
        await expect(panel).toBeVisible();
        await expect(panel).toContainText(`Delete character "${name}"?`);
        await expect(page.locator('[aria-modal]')).toHaveCount(0);

        // Backing out leaves the row where it was.
        await panel.locator('button', { hasText: 'Cancel' }).click();
        await expect(panel).toHaveCount(0);
        await expect(page.locator('tr', { hasText: name })).toBeVisible();
    });
});

test.describe('Show Deleted Checkbox', () => {
    test('checkbox exists and is unchecked by default', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/characters/');

        const checkbox = page.locator('#show-deleted');
        await expect(checkbox).toBeVisible();
        await expect(checkbox).not.toBeChecked();
    });

    test('checking checkbox adds show_deleted parameter to URL', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/characters/');

        // Click the checkbox to trigger onchange and navigation
        await page.click('#show-deleted');

        // Wait for page to reload with new URL
        await page.waitForURL('**/characters/?show_deleted=1');
        expect(page.url()).toContain('show_deleted=1');
    });

    test('checkbox is checked when show_deleted=1 in URL', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/characters/?show_deleted=1');

        const checkbox = page.locator('#show-deleted');
        await expect(checkbox).toBeChecked();
    });

    test('unchecking checkbox removes show_deleted parameter', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/characters/?show_deleted=1');

        const checkbox = page.locator('#show-deleted');
        await expect(checkbox).toBeChecked();

        // Click to uncheck - triggers navigation
        await page.click('#show-deleted');

        // Wait for page to reload without show_deleted param
        await page.waitForURL('**/characters/');
        expect(page.url()).not.toContain('show_deleted');
    });

    test('show_deleted preserves other URL parameters', async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        await page.goto('/tarmar/characters/?user=1');

        // Click checkbox
        await page.click('#show-deleted');

        // Wait for navigation - should have both parameters
        await page.waitForURL(/show_deleted=1/);
        expect(page.url()).toContain('show_deleted=1');
        expect(page.url()).toContain('user=1');
    });
});

test.describe('Manager Character List', () => {
    test('manager can see user filter dropdown', async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        await page.goto('/tarmar/characters/');

        // Admin/DM should see user filter
        const filterDropdown = page.locator('#user-filter');
        await expect(filterDropdown).toBeVisible();
    });

    test('manager can see owner column', async ({ page }, testInfo) => {
        await loginAs(page, TEST_ADMIN);
        await seedListedCharacter(page, testInfo, 'Owned');

        // Should have Owner column header
        const ownerHeader = page.locator('th:has-text("Owner")');
        await expect(ownerHeader).toBeVisible();
    });

    test('manager can see restore button for deleted characters', async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        // global-setup seeds a soft-deleted character ("Old Ghost") owned by
        // TEST_USER. The manager's list is paginated at 50 and sorts deleted
        // rows last, so an unfiltered page 1 stops holding it as soon as the
        // rest of the suite has rolled 50 characters between them -- which
        // every battle spec does, a few fighters at a time. Filtering to its
        // owner makes this test about the restore button rather than about
        // how many characters the run happened to create before it.
        await page.goto('/tarmar/characters/?show_deleted=1');
        const ownerId = await page.locator('#user-filter option')
            .filter({ hasText: new RegExp(`^${TEST_USER.username}$`) })
            .first()
            .getAttribute('value');
        await page.goto(
            `/tarmar/characters/?show_deleted=1&user=${ownerId}`
        );

        const deletedRow = page.locator('.deleted-row', { hasText: 'Old Ghost' });
        await expect(deletedRow.locator('button.restore-btn')).toBeVisible();
    });

    test('filtering by user updates character list', async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        await page.goto('/tarmar/characters/');

        // The suite's own accounts are seeded before it runs, so the filter
        // always offers more than "All Users"; it used to skip itself when it
        // did not, which would have hidden the filter losing its options.
        const filterDropdown = page.locator('#user-filter');
        await expect(filterDropdown.locator('option')).not.toHaveCount(1);

        // Select a specific user (second option, after "All Users")
        await filterDropdown.selectOption({ index: 1 });

        // URL should include user parameter
        await expect(page).toHaveURL(/user=/);
    });
});

test.describe('Inline confirmation (issue #190)', () => {
    test.beforeEach(async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/characters/');
    });

    test('asking to delete opens a panel in the page, not a modal', async ({ page }, testInfo) => {
        const name = await seedListedCharacter(page, testInfo, 'In Page');

        await page.locator('tr', { hasText: name })
            .locator('button.delete-btn.action-btn').click();

        const panel = page.locator('.notice-confirm');
        await expect(panel).toBeVisible();
        // Nothing modal about it: no overlay, no aria-modal, and the page
        // behind stays reachable.
        await expect(page.locator('.confirm-overlay')).toHaveCount(0);
        await expect(page.locator('[aria-modal]')).toHaveCount(0);
        await expect(page.locator('h1')).toBeVisible();

        // The question sits beside the button that raised it (#711). It used
        // to be drawn in the notice region at the top of <main>, so on a list
        // of twenty characters it appeared off-screen.
        await expect(page.locator('.cell-actions .notice-confirm')).toHaveCount(1);
        await expect(page.locator('#notice-region .notice-confirm')).toHaveCount(0);

        // Backing out leaves the character alone and clears the question.
        const rowsBefore = await page.locator('.character-table tbody tr').count();
        await panel.locator('button', { hasText: 'Cancel' }).click();
        await expect(panel).toHaveCount(0);
        await expect(page.locator('.character-table tbody tr')).toHaveCount(rowsBefore);
    });
});
