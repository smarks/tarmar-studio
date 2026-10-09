/**
 * The character list survives a hand-edited owner filter (issue #641).
 *
 * ?user= went straight into qs.filter(user_id=...), so a DM who edited the
 * URL or followed a stale link got a 500 and an error-log line. This is the
 * surface a person actually reaches it from, so it is checked in a browser.
 */

const { test, expect, loginAs, TEST_ADMIN } = require('./fixtures');

test('a non-numeric owner filter shows the list rather than an error page', async ({ page }) => {
    await loginAs(page, TEST_ADMIN);

    const response = await page.goto('/tarmar/characters/?user=abc');

    expect(response.status()).toBe(200);
    await expect(page.locator('h1')).toContainText('Characters');
    await expect(page.locator('#user-filter')).toBeVisible();
});
