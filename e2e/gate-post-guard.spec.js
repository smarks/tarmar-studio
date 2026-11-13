/**
 * The passphrase gate's POST and its pages, as a browser meets them (#175, #176).
 *
 * What only the running server proves: the gate page the dev server serves
 * carries X-Frame-Options; a POST to the gate with no CSRF token is refused
 * and opens nothing; a passphrase posted to a public page is not counted as a
 * gate guess; and the real form, token and all, still opens the gate. The
 * unit tests (common/tests/test_gate_post_guard.py) pin the counting and the
 * middleware order and are not repeated here.
 *
 * Every worker reaches the dev server from 127.0.0.1, and the gate's counter
 * is per address, so the counter is never read here: gate-hint.spec.js spends
 * and releases real guesses on the same row while this runs, and a count
 * read twice would see its work. Instead each test sends more than a whole
 * guessing budget of the requests the fix stops counting, then opens the
 * gate with the right passphrase: counted, those requests would have locked
 * the address out and the gate would answer 429 (the #175 lockout) instead.
 * Not counted, they spend nothing, so this spec can never lock another
 * worker out. The row is still released in `finally`, so a failing run of
 * this spec frees the address for the others.
 *
 * The gate's path, its counter key and its budget are the server's to name
 * (common/gate.py, common/middleware.py) and are read from it, not retyped.
 */

const { test, expect, GATE_PASSPHRASE, passGateOnCurrentPage } = require('./fixtures');
const { readDjangoJson, runDjangoShell, TEST_CLIENT_ADDRESS } = require('./django');

const GATED_PAGE = '/tarmar/gm/';
/** A public page the limiter used to count a passphrase field on. */
const PUBLIC_PAGE = '/tarmar/battles/';
const WRONG_PASSPHRASE = 'gandalf';

/**
 * The gate as the server in hand declares it: the path its form posts to,
 * the counter key for this client's address, and the guessing budget.
 * @returns {{path: string, counterKey: string, budget: number}}
 */
function gateSettings() {
    return readDjangoJson(`
import json
from common.gate import GATE_PATH
from common.middleware import GateRateLimitMiddleware, gate_counter_key
print('GATE_SETTINGS', json.dumps({
    'path': GATE_PATH,
    'counterKey': gate_counter_key(${JSON.stringify(TEST_CLIENT_ADDRESS)}),
    'budget': GateRateLimitMiddleware.MAX_ATTEMPTS,
}))
`, 'GATE_SETTINGS');
}

/** Release whatever is held against the test client's address at the gate. */
function releaseGateGuesses(counterKey) {
    runDjangoShell(`
from common.models import RateLimitCounter
deleted, _ = RateLimitCounter.objects.filter(key=${JSON.stringify(counterKey)}).delete()
print(f'Released {deleted} gate counter row(s) for the test client')
`);
}

/** Open the gate on `page` with the right passphrase and prove it opened. */
async function openTheGate(page) {
    await page.goto(GATED_PAGE);
    await expect(page.locator('input[name="passphrase"]')).toBeVisible();
    await passGateOnCurrentPage(page);
    await expect(page.locator('input[name="passphrase"]')).toHaveCount(0);
}

test.describe('The gate as a stranger meets it', () => {
    test('the gate page and the refused POST carry X-Frame-Options', async ({ browser }) => {
        const { path } = gateSettings();
        // The shared `page` fixture answers the gate before every test, so a
        // visitor who has not is a context of this spec's own.
        const context = await browser.newContext();
        try {
            const gatePage = await context.request.get(GATED_PAGE);
            expect(gatePage.status()).toBe(200);
            expect(await gatePage.text()).toContain('name="passphrase"');
            expect(gatePage.headers()['x-frame-options']).toBe('DENY');

            // Refused for want of a token (#175); the refusal is the gate's
            // own answer and has to carry the header too (#176).
            const tokenless = await context.request.post(path, {
                form: { passphrase: GATE_PASSPHRASE },
            });
            expect(tokenless.status()).toBe(403);
            expect(tokenless.headers()['x-frame-options']).toBe('DENY');
        } finally {
            await context.close();
        }
    });

    test('a POST with no CSRF token opens nothing and counts for nothing', async ({ browser }) => {
        const { path, counterKey, budget } = gateSettings();
        const context = await browser.newContext();
        try {
            const page = await context.newPage();
            await page.goto(GATED_PAGE);
            await expect(page.locator('input[name="passphrase"]')).toBeVisible();

            // The right passphrase, posted from this same context (cookies
            // and all) but without the form's token: refused, and the next
            // page load is still the gate.
            const refused = await context.request.post(path, {
                form: { passphrase: GATE_PASSPHRASE },
            });
            expect(refused.status()).toBe(403);
            await page.goto(GATED_PAGE);
            await expect(page.locator('input[name="passphrase"]')).toBeVisible();

            // A whole budget and one more of wrong passphrases, all without a
            // token: refused, and none of them a guess.
            for (let attempt = 0; attempt <= budget; attempt += 1) {
                const response = await context.request.post(path, {
                    form: { passphrase: WRONG_PASSPHRASE },
                });
                expect(response.status()).toBe(403);
            }

            // Counted, the posts above would have spent the budget and the
            // form would meet a 429 here. The form itself, token included,
            // still opens the gate.
            await openTheGate(page);
        } finally {
            releaseGateGuesses(counterKey);
            await context.close();
        }
    });

    test('a passphrase posted to a public page is not a gate guess', async ({ browser }) => {
        const { counterKey, budget } = gateSettings();
        const context = await browser.newContext();
        try {
            for (let attempt = 0; attempt <= budget; attempt += 1) {
                // The battle list answers a POST with its own refusal; what
                // matters is that the gate's counter never hears of it.
                const response = await context.request.post(PUBLIC_PAGE, {
                    form: { passphrase: WRONG_PASSPHRASE },
                });
                expect(response.status()).not.toBe(429);
            }

            // The #175 lockout: counted, those posts would answer the right
            // passphrase at the gate with a 429.
            await openTheGate(await context.newPage());
        } finally {
            releaseGateGuesses(counterKey);
            await context.close();
        }
    });
});
