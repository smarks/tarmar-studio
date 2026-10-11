/**
 * Hook generator E2E (issue #276): the DM seats a character in a party,
 * rolls a hook matched to that party's experience, and sees the die next to
 * the result; the URL carries the seed, so a reload reproduces the hook.
 * The first-session on-ramp is the public side of the same chain: it renders
 * for a logged-out reader, no DM role needed.
 */

const { test, expect, createCharacter, searchPicker } = require('./fixtures');

test.describe('Hook generator', () => {
    test('DM rolls a party-matched hook and sees the roll', async ({ adminPage }) => {
        // A fresh character (level 1), renamed so the party rows are unambiguous.
        await createCharacter(adminPage);
        const name = `Hookbait ${Date.now()}`;
        const nameInput = adminPage.locator('[data-field="name"] input');
        await nameInput.fill(name);
        await nameInput.blur();

        // Form a party and seat the character.
        await adminPage.goto('/tarmar/parties/');
        await adminPage.click('a:has-text("Form a New Party")');
        const partyName = `Hook Party ${Date.now()}`;
        await adminPage.fill('#id_name', partyName);
        await adminPage.click('button:has-text("Form Party")');
        await adminPage.waitForURL(/\/tarmar\/parties\/\d+\//);
        await searchPicker(adminPage, '#party-character-filter', name);
        const shelfRow = adminPage.locator('.party-addable-row', { hasText: name });
        await shelfRow.locator('button:has-text("Add")').click();
        await expect(adminPage.locator('.party-member', { hasText: name })).toBeVisible();

        // The generator is in the admin/DM menu.
        await adminPage.click('#menuToggle');
        await adminPage.click('nav a:has-text("Hook Generator")');
        await adminPage.waitForURL('**/tarmar/gm/hooks/');
        await expect(adminPage.locator('h1')).toHaveText('Hook Generator');

        // Pick the party (its option text carries the roster summary too,
        // so select by the option that names it) and roll with a fixed seed.
        const optionValue = await adminPage
            .locator('#hook-form select[name="party"] option', { hasText: partyName })
            .getAttribute('value');
        await adminPage.selectOption('#hook-form select[name="party"]', optionValue);
        await adminPage.fill('#hook-form input[name="seed"]', '42');
        await adminPage.click('#hook-form button');

        // The form serializes its level select too; the party wins when both
        // are present (the view's documented precedence).
        await adminPage.waitForURL(/\/tarmar\/gm\/hooks\/\?party=\d+&level=\d+&seed=42$/);
        const result = adminPage.locator('#hook-result');
        await expect(result).toBeVisible();
        await expect(result).toContainText('seed 42');
        // The matching rule surfaces: a lone level-1 character rolls as level 1.
        await expect(result).toContainText(partyName);
        await expect(result).toContainText('rolls as level 1');
        // The roll is shown: die and face beside the eligible-hook count.
        await expect(result.locator('.roll-detail')).toContainText(/d\d+ → \d+/);
        // The hook card links back to the reference page's anchor.
        const hookLink = result.locator('.generator-card h3 a');
        await expect(hookLink).toHaveAttribute(
            'href',
            /\/tarmar\/reference\/dm-rules\/gm-tools\/low-experience-hooks\/#.+/
        );

        // State-driven: the seed lives in the URL, so a reload rerolls nothing.
        const hookTitle = await hookLink.textContent();
        await adminPage.reload();
        await expect(adminPage.locator('#hook-result .generator-card h3 a')).toHaveText(hookTitle);

        // Generate-another never dead-ends: the form is still there, party kept.
        await expect(adminPage.locator('#hook-form button')).toContainText('Generate another');
    });

    test('players cannot reach the hook generator', async ({ authenticatedPage }) => {
        const response = await authenticatedPage.goto('/tarmar/gm/hooks/');
        expect(response.status()).toBe(403);
    });
});

test.describe('First-session on-ramp', () => {
    test('renders for a logged-out reader with the starting threads', async ({ page }) => {
        await page.goto('/tarmar/reference/first-session/');
        await expect(page.locator('main')).toContainText('What a First Session Looks Like');
        await expect(page.locator('main')).toContainText('Where Journeys Begin');
        await expect(page.locator('main')).not.toContainText('Access Denied');
        // It links the character-create flow for new players.
        await expect(page.locator('main a[href="/tarmar/characters/create/"]')).toBeVisible();
        // And the sidebar publishes it to everyone.
        await expect(page.locator('nav.sidebar')).toContainText('Your First Session');
    });
});
