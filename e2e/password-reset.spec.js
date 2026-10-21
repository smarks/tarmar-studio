/**
 * A forgotten password, recovered from the login page (issue #929).
 *
 * The login page offered no way back and the reset URL was a 404. This walks
 * the whole flow as a user would: "Forgot your password?", the address, the
 * message the site actually sent (read from the dev server's mail directory,
 * see e2e/mail.js), the link opened in a fresh browser that has never seen the
 * passphrase gate -- which is where a mail client opens it -- and a sign-in
 * with the new password.
 */

const { test, expect, loginAs } = require('./fixtures');
const { uniqueStamp } = require('./unique-name');
const { runDjangoShell } = require('./django');
const { missingMailMessage, takeMessageTo } = require('./mail');

const OLD_PASSWORD = 'HarbourLamp52-old';
const NEW_PASSWORD = 'LanternBridge42-new';
const CONFIRM_LINK = /https?:\/\/\S+\/tarmar\/accounts\/password_reset\/[^/\s]+\/[^/\s]+\//;

test('a forgotten password is reset through the emailed link', async ({ page, browser }) => {
    const username = `e2e_reset_${uniqueStamp()}`;
    const email = `${username}@example.test`;
    runDjangoShell(`
from django.contrib.auth import get_user_model
get_user_model().objects.create_user(
    username=${JSON.stringify(username)},
    email=${JSON.stringify(email)},
    # The reset mails only a confirmed address (issue #931).
    email_verified=True,
    password=${JSON.stringify(OLD_PASSWORD)},
)
`);
    const mailClient = await browser.newContext();
    try {
        await page.goto('/tarmar/accounts/login/');
        await page.click('#forgot-password-link');
        await expect(page.locator('h1')).toHaveText('Reset your password');
        await page.fill('#id_email', email);
        await page.click('#password-reset-form button[type="submit"]');
        await expect(page.locator('h1')).toHaveText('Check your email');
        await expect(page.locator('#password-reset-sent')).toContainText(
            'If an account here has that email address',
        );

        let message = null;
        await expect.poll(() => {
            message = takeMessageTo(email);
            return message !== null;
        }, { message: missingMailMessage(email) }).toBe(true);
        const link = message.match(CONFIRM_LINK)?.[0];
        expect(link, message).toBeTruthy();
        expect(new URL(link).origin).toBe(new URL(page.url()).origin);

        const mailPage = await mailClient.newPage();
        await mailPage.goto(link);
        await expect(mailPage.locator('h1')).toHaveText('Choose a new password');
        await mailPage.fill('#id_new_password1', NEW_PASSWORD);
        await mailPage.fill('#id_new_password2', NEW_PASSWORD);
        await mailPage.click('#password-reset-confirm-form button[type="submit"]');
        await expect(mailPage.locator('#password-reset-complete')).toBeVisible();

        await mailPage.click('main .links a:has-text("Login")');
        await expect(mailPage).toHaveURL(/\/tarmar\/accounts\/login\/$/);
        await loginAs(mailPage, { username, password: NEW_PASSWORD });
        await expect(mailPage.locator('.identity-username')).toHaveText(username);

        // The link is spent once used.
        await mailPage.goto(link);
        await expect(mailPage.locator('h1')).toHaveText('Reset link not valid');
    } finally {
        await mailClient.close();
        runDjangoShell(`
from django.contrib.auth import get_user_model
get_user_model().objects.filter(username=${JSON.stringify(username)}).delete()
`);
    }
});
