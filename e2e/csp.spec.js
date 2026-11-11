/**
 * E2E for the Content-Security-Policy (issue #178).
 *
 * What only the running server and a real browser prove: the header the dev
 * server sends, on a view's page and on the gate's own page; that the inline
 * scripts the templates keep run under it by nonce (the 404 page draws its
 * stars from one); and that the GM chat's controls, which carried inline
 * handlers until #178, still work wired from data attributes. The shared
 * `page` fixture fails any test whose page logs a policy violation, so every
 * other spec in the suite is this policy's test too; the pages this spec
 * opens in contexts of its own watch for violations themselves.
 *
 * The GM chat test signs in as an account of this spec's own (issue #889),
 * made in the Django shell for this worker and this repeat and deleted
 * afterwards with the session its message starts, so no session is left on
 * a shared account for other specs to find.
 */

const { test, expect, loginAs } = require('./fixtures');
const { runDjangoShell } = require('./django');
const { specAccount, deleteSpecAccountsScript } = require('./accounts');

const PUBLIC_PAGE = '/tarmar/accounts/login/';
const GATED_PAGE = '/tarmar/gm/';
const HEADER = 'content-security-policy';
const REPORT_ONLY_HEADER = 'content-security-policy-report-only';

const gameMasterFor = (testInfo) => specAccount('cspmaster', 'cspmaster-pass-178', testInfo);

/** The Game Master's account, made afresh with the role the chat's controls are drawn for. */
function createGameMasterScript(gameMaster) {
    return `
from django.contrib.auth import get_user_model
user, _created = get_user_model().objects.get_or_create(username=${JSON.stringify(gameMaster.username)})
user.is_dm = True
user.set_password(${JSON.stringify(gameMaster.password)})
user.save()
`;
}

/** The header's directives, each to its sources. */
function directives(header) {
    const parsed = {};
    for (const clause of header.split(';')) {
        const [name, ...sources] = clause.trim().split(/\s+/);
        if (name) parsed[name] = sources;
    }
    return parsed;
}

test.describe('Content-Security-Policy', () => {
    test.afterAll(({}, testInfo) => {
        runDjangoShell(deleteSpecAccountsScript([gameMasterFor(testInfo)]));
    });

    test('a page carries the enforced policy the review asked for', async ({ page }) => {
        const response = await page.request.get(PUBLIC_PAGE);
        expect(response.status()).toBe(200);
        expect(response.headers()[REPORT_ONLY_HEADER]).toBeUndefined();
        const policy = directives(response.headers()[HEADER]);
        expect(policy['frame-ancestors']).toEqual(["'none'"]);
        expect(policy['object-src']).toEqual(["'none'"]);
        expect(policy['base-uri']).toEqual(["'self'"]);
        expect(policy['form-action']).toEqual(["'self'"]);
        expect(policy['img-src']).toEqual(["'self'"]);
        expect(policy['script-src']).not.toContain("'unsafe-inline'");
        expect(policy['script-src'][0]).toBe("'self'");
        expect(policy['script-src'][1]).toMatch(/^'nonce-/);
    });

    test('the gate\'s own page carries it too', async ({ browser }) => {
        // A context of this test's own; the fixtures watch its pages for
        // violations as they watch the shared page's.
        const context = await browser.newContext();
        try {
            const page = await context.newPage();
            const response = await page.goto(GATED_PAGE);
            expect(response.headers()[HEADER]).toContain("frame-ancestors 'none'");
            await expect(page.locator('input[name="passphrase"]')).toBeVisible();
        } finally {
            await context.close();
        }
    });

    test('base.html\'s inline menu script runs by nonce', async ({ page }) => {
        // The menu opens from the page's own inline <script>, admitted by its
        // nonce; the policy would refuse it otherwise (and the fixture would
        // report the violation). The 404 page's stars are the unit tests'
        // to check: the dev server runs with DEBUG on and Django's own 404.
        await page.goto('/tarmar/');
        await page.click('#menuToggle');
        await expect(page.locator('#menu')).toHaveClass(/open/);
        await expect(page.locator('#menuToggle')).toHaveAttribute('aria-expanded', 'true');
    });

    test('the GM chat\'s controls work without inline handlers', async ({ page }, testInfo) => {
        const gameMaster = gameMasterFor(testInfo);
        runDjangoShell(createGameMasterScript(gameMaster));
        await loginAs(page, gameMaster);
        await page.goto(GATED_PAGE);
        // Roll 3d6 asks for the dice in the page (data-command="roll").
        await page.click('.cmd-btn:has-text("Roll 3d6")');
        const ask = page.locator('.notice-prompt');
        await expect(ask).toContainText('Dice expression');
        await expect(ask.locator('.notice-prompt-input')).toHaveValue('3d6');
        await ask.locator('button:has-text("Cancel")').click();
        await expect(ask).toHaveCount(0);
        // The form submits from its listener: Enter sends the typed message,
        // which the page shows as the player's and the fake model answers.
        await page.fill('#user-input', 'I look around the crypt');
        await page.press('#user-input', 'Enter');
        await expect(page.locator('.message.user').last()).toContainText('I look around the crypt');
        await expect(page.locator('.message.assistant').last()).toContainText('nothing stirs', { timeout: 30000 });
        await expect(page.locator('#send-btn')).toBeEnabled({ timeout: 30000 });
        // Now there is a session: Rename asks for its name in the page
        // (data-action="rename-session").
        await page.click('.cmd-btn:has-text("Rename")');
        const rename = page.locator('.notice-prompt');
        await expect(rename).toBeVisible();
        await rename.locator('button:has-text("Cancel")').click();
        await expect(rename).toHaveCount(0);
    });
});
