/**
 * A failed "Add Item" can be retried with one click (issue #659).
 *
 * The handler ended with `finally { this.disabled = true; }`. After a success
 * that is right — the selects have just been reset, so there is nothing to
 * add. After a failure it is not: the player's choice is still in the selects,
 * so the item select's change listener, the only thing that re-enables the
 * button, will not fire, and the retry needed the category and the item picked
 * again.
 */

const {
    test, expect, loginAs, createNamedCharacter, TEST_USER, postAsGameMaster, characterIdFromUrl,
} = require('./fixtures');
const { uniqueName } = require('./unique-name');

test('a refused add leaves the picker as it was, and the retry is one click', async ({ page, browser }) => {
    const name = uniqueName('Retrying');
    await loginAs(page, TEST_USER);
    await createNamedCharacter(page, name);
    // A dagger is bought (issue #757) and a rolled Rich character starts with
    // no coin, so a Game Master gives enough for it first.
    const given = await postAsGameMaster(
        browser, `/tarmar/characters/${characterIdFromUrl(page)}/coin/`, { amount: 10 }
    );
    expect(given.status).toBe(200);

    await page.route('**/equipment/add/', (route) => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: false, error: 'Try that again' }),
    }));

    await page.selectOption('#equipment-category', 'weapons');
    await page.selectOption('#equipment-item', 'dagger');
    await page.locator('#add-equipment-btn').click();
    await expect(page.locator('.notice')).toContainText('Try that again');

    // The choice is still there and the button is still offering it.
    await expect(page.locator('#equipment-item')).toHaveValue('dagger');
    await expect(page.locator('#add-equipment-btn')).toBeEnabled();

    await page.unroute('**/equipment/add/');
    await page.locator('#add-equipment-btn').click();
    await expect(page.locator('.equipment-row')).toHaveCount(1);
    // The selects are reset by a success, so there is nothing left to add.
    await expect(page.locator('#add-equipment-btn')).toBeDisabled();
});
