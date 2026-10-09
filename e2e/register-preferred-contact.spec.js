/**
 * E2E for the preferred contact a new account is registered with (issue #339, item 31).
 *
 * With only one contact method filled in, the page picks it and disables the
 * dropdown. A disabled field is not submitted, so the account was created
 * with no preferred contact at all. The choice now reaches the server.
 */

const { test, expect } = require('./fixtures');
const { readDjangoShell } = require('./django');

test('registering with only an email saves email as the preferred contact', async ({ page }) => {
    const username = `e2e_contact_${Date.now()}`.slice(0, 25);

    await page.goto('/tarmar/accounts/register/');
    await page.fill('input[name="username"]', username);
    await page.fill('input[name="password1"]', 'QuillHarbor8k-long');
    await page.fill('input[name="password2"]', 'QuillHarbor8k-long');
    await page.fill('#id_email', `${username}@example.com`);
    await expect(page.locator('#id_preferred_contact')).toHaveValue('email');
    await page.click('button[type="submit"]');
    await page.waitForURL((url) => !url.pathname.includes('/register'));

    const output = readDjangoShell(`
from django.contrib.auth import get_user_model
user = get_user_model().objects.get(username='${username}')
print('PREFERRED', repr(user.preferred_contact))
user.delete()
`);
    expect(output).toContain("PREFERRED 'email'");
});
