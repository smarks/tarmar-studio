/**
 * E2E: the working line names the step being composed (issue #569).
 *
 * Reported from play on production: "It says the game master is writing but
 * it doesn't say what it's writing." A turn plans before it writes (issue
 * #590), and planning a long turn can take a minute before any text arrives.
 *
 * This plays a real turn through the dev server against the fake Ollama
 * (e2e/fake-ollama.js), whose planning reply waits before answering with a
 * roll when the message says "dice step". While it waits the line says the GM
 * is planning, never that it is writing; no plan and no marker reaches the
 * page; and the roll's dice line is shown before the narration.
 *
 * The spec signs in as its own user, so a turn it starts never holds up
 * another spec's account; named for the worker and the repeat, so copies of
 * it under --repeat-each never share one (issue #889).
 */

const { test, expect, loginAs } = require('./fixtures');
const { runDjangoShell } = require('./django');
const { specAccount, deleteSpecAccountsScript } = require('./accounts');

/** This worker's and this repeat's own account (issue #889). */
const rollerFor = (testInfo) => specAccount('stepwatcher', 'stepwatcher-pass-569', testInfo);
let ROLLER;

test.describe.configure({ mode: 'serial' });

test.describe('GM chat — while a turn is being planned', () => {
    test.beforeEach(({}, testInfo) => {
        ROLLER = rollerFor(testInfo);
        runDjangoShell(`
from django.contrib.auth import get_user_model
from gm.models import GMSession
user, _created = get_user_model().objects.get_or_create(username=${JSON.stringify(ROLLER.username)})
user.set_password(${JSON.stringify(ROLLER.password)})
user.save()
GMSession.objects.filter(user=user).delete()
`);
    });

    test.afterAll(({}, testInfo) => {
        runDjangoShell(deleteSpecAccountsScript([rollerFor(testInfo)]));
    });

    test('the line says the GM is planning, not "writing"', async ({ page }) => {
        await loginAs(page, ROLLER);
        await page.goto('/tarmar/gm/');
        await page.fill('#user-input', 'Show me a dice step');
        await page.click('#send-btn');

        const status = page.locator('#gm-status');
        await expect(status).toBeVisible();
        await expect(status).toContainText('planning');
        await expect(status).not.toContainText('writing');
        // Whatever the line says, it is never the plan the model wrote.
        await expect(status).not.toContainText('[[');
        await expect(status).not.toContainText('{');

        // The step runs, its dice line is shown, and the turn ends with the
        // line cleared.
        await expect(page.locator('.dice-roll')).toContainText('a test of nerve', { timeout: 30000 });
        await expect(page.locator('.chat-messages')).toContainText('The crypt is quiet, and nothing stirs.', { timeout: 30000 });
        await expect(status).toBeHidden();
    });
});
