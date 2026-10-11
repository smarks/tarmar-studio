/**
 * Changing the email address takes the password and a link (issue #931).
 *
 * Since password reset (#929) the address on an account is where a reset
 * link goes. This walks the change as a user would: the profile's "Change
 * email address", the password asked for, the profile showing the new
 * address as pending while the old one stays, the two messages the site
 * actually sent (read from the dev server's mail directory, see e2e/mail.js),
 * the link opened in a fresh browser that has never seen the passphrase
 * gate, the button that makes the change, and the profile showing the new
 * address confirmed. The link is spent once used.
 *
 * The second test is issue #954: an Admin sets another account's address
 * from its profile. It lands unconfirmed, both pages say so, the old address
 * is told, and the link sent to the new one confirms it.
 */

const { test, expect, loginAs, TEST_ADMIN } = require('./fixtures');
const { readDjangoShell, runDjangoShell } = require('./django');
const { missingMailMessage, takeMessageTo } = require('./mail');

const PASSWORD = 'HarbourLamp52-mail';
const CONFIRM_LINK = /https?:\/\/\S+\/tarmar\/accounts\/password_reset\/address\/[^/\s]+\/[^/\s]+\//;

/**
 * The message to `address`, once it has arrived.
 * @param {string} address
 * @returns {Promise<string>}
 */
async function awaitMessageTo(address) {
    let message = null;
    await expect.poll(() => {
        message = takeMessageTo(address);
        return message !== null;
    }, { message: missingMailMessage(address) }).toBe(true);
    return message;
}

test('a new email address replaces the old one only through its link', async ({ page, browser }, testInfo) => {
    const username = `e2e_mail_${testInfo.parallelIndex}_${Date.now()}`.slice(0, 25);
    const oldEmail = `${username}-old@example.test`;
    const newEmail = `${username}-new@example.test`;
    runDjangoShell(`
from django.contrib.auth import get_user_model
get_user_model().objects.create_user(
    username=${JSON.stringify(username)},
    email=${JSON.stringify(oldEmail)},
    email_verified=True,
    password=${JSON.stringify(PASSWORD)},
)
`);
    const mailClient = await browser.newContext();
    try {
        await loginAs(page, { username, password: PASSWORD });
        await page.goto('/tarmar/accounts/profile/');
        await expect(page.locator('#email-status')).toHaveText('Confirmed.');
        await page.click('#email-change-link');
        await expect(page.locator('h1')).toHaveText('Change email address');

        // A wrong password changes nothing.
        await page.fill('#id_new_email', newEmail);
        await page.fill('#id_current_password', 'not-the-password');
        await page.click('#email-change-form button[type="submit"]');
        await expect(page.locator('#email-change-form .error')).toHaveText(
            'That is not the password for this account.',
        );

        await page.fill('#id_new_email', newEmail);
        await page.fill('#id_current_password', PASSWORD);
        await page.click('#email-change-form button[type="submit"]');
        await expect(page).toHaveURL(/\/tarmar\/accounts\/profile\/$/);
        await expect(page.locator('#id_email')).toHaveValue(oldEmail);
        await expect(page.locator('#email-pending')).toContainText(newEmail);

        const notice = await awaitMessageTo(oldEmail);
        expect(notice).toContain('If this was not you');
        expect(notice).not.toMatch(CONFIRM_LINK);

        const confirmation = await awaitMessageTo(newEmail);
        const link = confirmation.match(CONFIRM_LINK)?.[0];
        expect(link, confirmation).toBeTruthy();
        expect(new URL(link).origin).toBe(new URL(page.url()).origin);

        const mailPage = await mailClient.newPage();
        await mailPage.goto(link);
        await expect(mailPage.locator('h1')).toHaveText('Confirm your email address');
        await expect(mailPage.locator('#email-confirm-pending')).toContainText(newEmail);
        await mailPage.click('#email-confirm-form button[type="submit"]');
        await expect(mailPage.locator('h1')).toHaveText('Email address confirmed');
        await expect(mailPage.locator('#email-confirmed')).toContainText(newEmail);

        await page.reload();
        await expect(page.locator('#id_email')).toHaveValue(newEmail);
        await expect(page.locator('#email-status')).toHaveText('Confirmed.');
        await expect(page.locator('#email-pending')).toHaveCount(0);

        // The link is spent once used.
        await mailPage.goto(link);
        await expect(mailPage.locator('h1')).toHaveText('Confirmation link not valid');
    } finally {
        await mailClient.close();
        runDjangoShell(`
from django.contrib.auth import get_user_model
get_user_model().objects.filter(username=${JSON.stringify(username)}).delete()
`);
    }
});

test('an Admin sets another account\'s address, which lands unconfirmed', async ({ page, browser }, testInfo) => {
    const username = `e2e_aset_${testInfo.parallelIndex}_${Date.now()}`.slice(0, 25);
    const oldEmail = `${username}-old@example.test`;
    const newEmail = `${username}-new@example.test`;
    const output = readDjangoShell(`
from django.contrib.auth import get_user_model
print('ACCOUNT', get_user_model().objects.create_user(
    username=${JSON.stringify(username)},
    email=${JSON.stringify(oldEmail)},
    email_verified=True,
    password=${JSON.stringify(PASSWORD)},
).pk)
`);
    // The shell prints its own preamble first; the pk is on the marked line.
    const userId = output.match(/^ACCOUNT (\d+)$/m)?.[1];
    expect(userId, output).toBeTruthy();
    const mailClient = await browser.newContext();
    try {
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/accounts/profile/${userId}/`);
        await expect(page.locator('#email-status')).toHaveText('Confirmed.');

        // A malformed address is refused inline and the field put back.
        await page.fill('#id_email', 'not-an-address');
        await page.click('#email-set-button');
        await expect(page.locator('#profile-email .autosave-error')).toHaveText(
            'Enter a valid email address.',
        );
        await expect(page.locator('#id_email')).toHaveValue(oldEmail);

        await page.fill('#id_email', newEmail);
        await page.click('#email-set-button');
        await expect(page.locator('#email-status')).toHaveText(
            'Not confirmed yet, so a password-reset link will not be sent to it.',
        );
        await expect(page.locator('#id_email')).toHaveValue(newEmail);
        await expect(page.locator('#email-pending')).toContainText(
            'It is confirmed when that link is opened.',
        );

        await page.goto('/tarmar/accounts/users/');
        const row = page.locator('.users-table tbody tr').filter({
            has: page.locator(`td.cell-name a:text-is("${username}")`),
        });
        await expect(row.locator('td[data-label="Email"]')).toHaveText(
            `${newEmail} (not confirmed)`,
        );

        const notice = await awaitMessageTo(oldEmail);
        expect(notice).toContain(`The Admin "${TEST_ADMIN.username}" at`);
        expect(notice).not.toMatch(CONFIRM_LINK);
        const confirmation = await awaitMessageTo(newEmail);
        const link = confirmation.match(CONFIRM_LINK)?.[0];
        expect(link, confirmation).toBeTruthy();

        const mailPage = await mailClient.newPage();
        await mailPage.goto(link);
        await mailPage.click('#email-confirm-form button[type="submit"]');
        await expect(mailPage.locator('#email-confirmed')).toContainText(newEmail);

        await page.reload();
        await expect(row.locator('td[data-label="Email"]')).toHaveText(newEmail);
    } finally {
        await mailClient.close();
        runDjangoShell(`
from django.contrib.auth import get_user_model
get_user_model().objects.filter(username=${JSON.stringify(username)}).delete()
`);
    }
});
