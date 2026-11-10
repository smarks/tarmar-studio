/**
 * Django's admin sign-in spends the site's login budget (issue #173).
 *
 * What only the running server proves: the admin's own form at /admin/login/,
 * submitted from a real browser, is counted by the same per-address limiter
 * as /tarmar/accounts/login/, and the right password still signs in after a
 * few wrong ones. The lockout itself -- the 429 after ten wrong guesses, the
 * right password refused once the budget is spent -- and the refund of a
 * genuine sign-in's charge are pinned by the unit tests
 * (accounts/tests/test_admin_login_rate_limit.py) and deliberately not driven
 * here: every worker reaches the dev server from 127.0.0.1 and the counter is
 * per address, so spending the budget in one worker would lock the other
 * three workers' sign-ins out of the site for five minutes (see
 * playwright.config.js on #432), and the refund is one charge among the
 * other workers' in-flight ones. This spec charges three guesses, reads the
 * count back as a lower bound, and releases its row in `finally`.
 *
 * The admin's paths and the counter key are the server's to name and are read
 * from it (django.urls.reverse, accounts/login_limit.py), not retyped.
 */

const { test, expect, passGateOnCurrentPage } = require('./fixtures');
const { readDjangoJson, runDjangoShell, TEST_CLIENT_ADDRESS } = require('./django');
const { specAccount } = require('./accounts');

const WRONG_PASSWORD = 'not-the-password';
const WRONG_GUESSES = 3;

/**
 * The admin's sign-in and index paths, and the login limiter's counter key for
 * this client's address, as the server in hand declares them.
 * @returns {{loginPath: string, indexPath: string, counterKey: string}}
 */
function adminLoginSettings() {
    return readDjangoJson(`
import json
from django.urls import reverse
from accounts.login_limit import login_failures_key
print('ADMIN_LOGIN_SETTINGS', json.dumps({
    'loginPath': reverse('admin:login'),
    'indexPath': reverse('admin:index'),
    'counterKey': login_failures_key(${JSON.stringify(TEST_CLIENT_ADDRESS)}),
}))
`, 'ADMIN_LOGIN_SETTINGS');
}

/** How many failed sign-ins the server holds against `counterKey` now. */
function loginFailuresCharged(counterKey) {
    return readDjangoJson(`
import json
from common.rate_limit import current_count
print('LOGIN_FAILURES', json.dumps(current_count(${JSON.stringify(counterKey)})))
`, 'LOGIN_FAILURES');
}

/** A staff account of this spec's own, so the admin's form can sign it in. */
function createStaffAccount(account) {
    runDjangoShell(`
from django.contrib.auth import get_user_model
User = get_user_model()
User.objects.filter(username=${JSON.stringify(account.username)}).delete()
User.objects.create_user(${JSON.stringify(account.username)}, password=${JSON.stringify(account.password)}, is_staff=True, is_superuser=True)
`);
}

/** Release this spec's charges and its account. */
function cleanUp(account, counterKey) {
    runDjangoShell(`
from django.contrib.auth import get_user_model
from common.models import RateLimitCounter
deleted, _ = RateLimitCounter.objects.filter(key=${JSON.stringify(counterKey)}).delete()
get_user_model().objects.filter(username=${JSON.stringify(account.username)}).delete()
print(f'Released {deleted} login counter row(s) for the test client')
`);
}

/** Fill the admin's own form and submit it, waiting for the server's answer. */
async function signInAtTheAdminDoor(page, loginPath, username, password) {
    await page.fill('#login-form input[name="username"]', username);
    await page.fill('#login-form input[name="password"]', password);
    await Promise.all([
        page.waitForResponse(response =>
            response.request().method() === 'POST'
            && new URL(response.url()).pathname === loginPath),
        page.click('#login-form input[type="submit"]'),
    ]);
}

test('wrong passwords at the admin door are counted; the right one still signs in', async ({ browser }, testInfo) => {
    const { loginPath, indexPath, counterKey } = adminLoginSettings();
    const keeper = specAccount('keeper', 'keeper-pass-12345', testInfo);
    createStaffAccount(keeper);
    // A context of this spec's own: the shared `page` fixture would sign
    // nobody in, but its cookies are this worker's and best left alone.
    const context = await browser.newContext();
    try {
        const page = await context.newPage();
        // The gate answers the right passphrase by redirecting to the front
        // door, so the admin's page is asked for again once it is open.
        await page.goto(loginPath);
        await passGateOnCurrentPage(page);
        await page.goto(loginPath);
        await expect(page.locator('#login-form input[name="username"]')).toBeVisible();

        const chargedBefore = loginFailuresCharged(counterKey);
        for (let guess = 0; guess < WRONG_GUESSES; guess += 1) {
            await signInAtTheAdminDoor(page, loginPath, keeper.username, WRONG_PASSWORD);
            await expect(page.locator('.errornote')).toBeVisible();
        }
        // At least this spec's guesses: another worker's sign-in may be
        // mid-flight, charged and not yet released, so the count can read
        // higher for a moment, never lower.
        expect(loginFailuresCharged(counterKey)).toBeGreaterThanOrEqual(chargedBefore + WRONG_GUESSES);

        // Three wrong guesses are within the budget: the right password
        // still signs in, and lands on the admin's index.
        await signInAtTheAdminDoor(page, loginPath, keeper.username, keeper.password);
        await page.waitForURL(url => url.pathname === indexPath);
        await expect(page.locator('#user-tools')).toContainText(keeper.username);
    } finally {
        cleanUp(keeper, counterKey);
        await context.close();
    }
});
