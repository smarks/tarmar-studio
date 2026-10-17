/**
 * E2E for the gate's hint after repeated wrong passphrases (issue #55).
 *
 * What only a browser proves: that after GATE_HINT_AFTER_WRONG_GUESSES wrong
 * submits on the real gate page the hint is drawn, visible, under "The doors
 * remain shut." The unit tests (common/tests/test_gate.py GateHintTest) pin
 * the counting -- no hint below the threshold, the hint from it on, the hint
 * on the 429 -- and are not repeated here. Every worker reaches the dev server
 * from 127.0.0.1, so a CI retry of this spec meets a counter its earlier
 * attempt already charged; this spec therefore asserts only that the hint is
 * present after its own guesses, never that it was absent before, and it
 * cleans up after itself: the counter row for the test client's address is
 * deleted when the test ends, so retries and back-to-back runs inside the
 * limiter's window start from zero rather than stacking towards the lockout.
 *
 * The hints are configuration, not code: config/gate-hints.txt, loaded into
 * settings.GATE_HINTS at startup, and one is drawn at random for every
 * response (issue #77), so the spec checks membership, not one sentence. The
 * hints and the threshold are read from the running settings rather than
 * mirrored here: the file is Spencer's to edit, with no Python and no spec.
 */

const { test, expect } = require('./fixtures');
const { readDjangoShell, runDjangoShell } = require('./django');

const WRONG_PASSPHRASE = 'gandalf';

/** The gate limiter's counter key for the address Playwright arrives from. */
const TEST_CLIENT_GATE_COUNTER_KEY = 'rate_limit:gate:127.0.0.1';

/**
 * settings.GATE_HINTS and settings.GATE_HINT_AFTER_WRONG_GUESSES, as the server
 * in hand has them. The shell prints its own "N objects imported" line first,
 * so the values follow a marker, the way seedCharacterFromExport reads an id.
 * @returns {{hints: string[], wrongGuessesBeforeHint: number}}
 */
function gateHintSettings() {
    const output = readDjangoShell(`
import json
from django.conf import settings
print('GATE_HINT_SETTINGS', json.dumps({'hints': list(settings.GATE_HINTS), 'wrongGuessesBeforeHint': settings.GATE_HINT_AFTER_WRONG_GUESSES}))
`);
    const match = output.match(/GATE_HINT_SETTINGS (.*)/);
    expect(match, `no gate hint settings in: ${output}`).not.toBeNull();
    return JSON.parse(match[1]);
}

/** Release the wrong guesses this spec charged against the test client's address. */
function releaseGateGuesses() {
    runDjangoShell(`
from common.models import RateLimitCounter
deleted, _ = RateLimitCounter.objects.filter(key=${JSON.stringify(TEST_CLIENT_GATE_COUNTER_KEY)}).delete()
print(f'Released {deleted} gate counter row(s) for the test client')
`);
}

test('the gate offers its hint under the refusal after enough wrong guesses', async ({ browser }) => {
    // The shared `page` fixture answers the gate before every test, so a
    // visitor who has not is a context of this spec's own.
    const context = await browser.newContext();
    try {
        const page = await context.newPage();
        await page.goto('/tarmar/');
        const passphraseField = page.locator('input[name="passphrase"]');
        await expect(passphraseField).toBeVisible();

        // The click waits for the navigation it starts, so the refusal check
        // runs against the new document; the response wait is there so a
        // POST the click did not start is never mistaken for one it did.
        const { hints, wrongGuessesBeforeHint } = gateHintSettings();
        expect(wrongGuessesBeforeHint).toBeGreaterThan(0);
        expect(hints.length).toBeGreaterThan(0);
        for (let guess = 0; guess < wrongGuessesBeforeHint; guess += 1) {
            await passphraseField.fill(WRONG_PASSPHRASE);
            await Promise.all([
                page.waitForResponse(response =>
                    response.request().method() === 'POST' && new URL(response.url()).pathname === '/gate/'),
                page.click('button[type="submit"]'),
            ]);
            await expect(page.locator('.error')).toBeVisible();
        }

        const hint = page.locator('.hint');
        await expect(hint).toBeVisible();
        expect(hints).toContain((await hint.textContent()).trim());
    } finally {
        // The release first: it is what the header promises, and it must not
        // depend on the browser context closing cleanly.
        releaseGateGuesses();
        await context.close();
    }
});
