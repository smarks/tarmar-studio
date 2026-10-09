/**
 * E2E for clearing a character's name (issue #339, item 23).
 *
 * An empty name is refused before saving. The editor used to put the old name
 * back without a word; it now says why, beside the field.
 */

const { test, expect, createCharacter } = require('./fixtures');

test('an emptied name is restored and the page says a character needs a name', async ({ authenticatedPage: page }) => {
    await createCharacter(page);
    const nameInput = page.locator('#id_name');
    const original = await nameInput.inputValue();

    await nameInput.fill('');
    await nameInput.blur();

    await expect(nameInput).toHaveValue(original);
    const field = page.locator('.name-field');
    await expect(field).toHaveClass(/error/);
    await expect(field).toContainText('A character needs a name.');
});
