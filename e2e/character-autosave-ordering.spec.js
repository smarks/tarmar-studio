/**
 * Two saves of one field settle in order (issue #660).
 *
 * saveField recorded originalValues only in the success callback and nothing
 * serialised or cancelled an in-flight save, so a player who blurred,
 * refocused and blurred again quickly had two POSTs outstanding. If the first
 * answered last, originalValues held the older value: the next blur re-posted
 * an unchanged value, and a refusal put the older value into the input rather
 * than what the server holds.
 *
 * The first save's response is held back by route interception, which is the
 * only way to make the race happen on demand — the issue was recorded as
 * plausible rather than reproduced for exactly that reason.
 */

const { test, expect, loginAs, createNamedCharacter, TEST_USER } = require('./fixtures');
const { uniqueName } = require('./unique-name');

test('a second save waits for the first, and the field ends on the later value', async ({ page }) => {
    const name = uniqueName('Twice Saved');
    await loginAs(page, TEST_USER);
    await createNamedCharacter(page, name);

    const bodies = [];
    let releaseFirst;
    const firstHeld = new Promise((resolve) => { releaseFirst = resolve; });
    await page.route('**/update-field/', async (route) => {
        const body = JSON.parse(route.request().postData() || '{}');
        bodies.push(body);
        if (bodies.length === 1) await firstHeld;
        await route.continue();
    });

    // A free-text field the owner may set: coin is a Game Master's, and a
    // player's height is one d6 face, since #748.
    const field = page.locator('[data-field="name"] input');
    await field.fill('Twice 111');
    await field.blur();
    await field.fill('Twice 222');
    await field.blur();

    // The second save is held behind the first rather than racing it.
    await expect.poll(() => bodies.length).toBe(1);
    expect(bodies[0].value).toBe('Twice 111');
    releaseFirst();
    await expect.poll(() => bodies.length).toBe(2);
    expect(bodies[1].value).toBe('Twice 222');

    await expect(page.locator('[data-field="name"]')).toHaveClass(/saved/);
    await page.reload();
    await expect(page.locator('[data-field="name"] input')).toHaveValue('Twice 222');
});
