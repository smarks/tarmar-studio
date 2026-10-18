/**
 * E2E: a turn waiting behind another table's turn says so (issue #64).
 *
 * Ollama on the production host answers one request at a time, so a turn
 * sent while another table's turn is running waits in the model's line,
 * silent. The page used to say only "The Game Master is working: planning".
 *
 * This plays a real turn through the dev server against the fake Ollama
 * (e2e/fake-ollama.js), which holds a planning call that says "behind
 * another table" before answering anything, as Ollama holds a queued
 * request. Another account's turn is recorded as running first, which is
 * the server's own view of the line. Past the server's keep-alive the
 * working line says the Game Master is busy with another table; the wording
 * is the server's setting, read through the Django shell, not typed here.
 *
 * Both accounts are this worker's and this repeat's own (issue #889), and
 * the other table's running turn is finished as soon as it has been seen, so
 * no other spec's turn waits behind it for longer than this one does.
 */

const { test, expect, loginAs } = require('./fixtures');
const { runDjangoShell, readDjangoJson } = require('./django');
const { specAccount, deleteSpecAccountsScript } = require('./accounts');

const waiterFor = (testInfo) => specAccount('queuewaiter', 'queuewaiter-pass-64', testInfo);
const otherTableFor = (testInfo) => specAccount('othertable', 'othertable-pass-64', testInfo);

/** The Django shell lines that finish every running turn of these accounts. */
function finishRunningTurnsScript(accounts) {
    const usernames = JSON.stringify(accounts.map((account) => account.username));
    return `
from gm.models import GMTurnRecord
GMTurnRecord.objects.filter(
    user__username__in=${usernames}, outcome=GMTurnRecord.OUTCOME_RUNNING
).update(outcome=GMTurnRecord.OUTCOME_ERROR, error="e2e: the other table's stand-in turn")
`;
}

test.describe.configure({ mode: 'serial' });

test.describe('GM chat — a turn waiting behind another table', () => {
    let waiter;
    let otherTable;

    test.beforeEach(({}, testInfo) => {
        waiter = waiterFor(testInfo);
        otherTable = otherTableFor(testInfo);
        runDjangoShell(`
from django.contrib.auth import get_user_model
from gm.models import GMSession
for username, password in [
    (${JSON.stringify(waiter.username)}, ${JSON.stringify(waiter.password)}),
    (${JSON.stringify(otherTable.username)}, ${JSON.stringify(otherTable.password)}),
]:
    user, _created = get_user_model().objects.get_or_create(username=username)
    user.set_password(password)
    user.save()
    GMSession.objects.filter(user=user).delete()
`);
    });

    test.afterAll(({}, testInfo) => {
        const accounts = [waiterFor(testInfo), otherTableFor(testInfo)];
        // A running record outlives its user (SET_NULL), so it is finished
        // first or it would go on counting as a turn ahead of everyone's.
        runDjangoShell(finishRunningTurnsScript(accounts));
        runDjangoShell(deleteSpecAccountsScript(accounts));
    });

    test('the line says the GM is busy with another table, then the reply lands', async ({ page }) => {
        // The fake line alone holds the turn 12 s, past the server's 10 s
        // keep-alive, and the Django shell runs around it take seconds each.
        test.setTimeout(90000);
        // One shell run records the other table's turn as running and reads
        // the server's wording for one turn ahead and for several. The count
        // is every player's running turns, so another worker's turn can make
        // it more than one; either line will do.
        const busyLines = readDjangoJson(`
import json
from django.conf import settings
from django.contrib.auth import get_user_model
from gm.models import GMTurnRecord
GMTurnRecord.objects.create(
    user=get_user_model().objects.get(username=${JSON.stringify(otherTable.username)}),
    outcome=GMTurnRecord.OUTCOME_RUNNING,
    mode="play",
)
print("busy " + json.dumps([
    settings.GM_QUEUE_STATUS_ONE_AHEAD,
    settings.GM_QUEUE_STATUS_MANY_AHEAD,
]))
`, 'busy');
        // Each template's words up to its first field, which vary.
        const busyWords = busyLines.map((line) => line.split('{')[0]);
        for (const words of busyWords) expect(words.length).toBeGreaterThan(0);
        const escaped = (words) => words.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const eitherBusyLine = new RegExp(busyWords.map(escaped).join('|'));

        try {
            await loginAs(page, waiter);
            await page.goto('/tarmar/gm/');
            await page.fill('#user-input', 'I wait behind another table');
            await page.click('#send-btn');

            const status = page.locator('#gm-status');
            await expect(status).toContainText('planning');
            await expect(status).toContainText(eitherBusyLine, { timeout: 20000 });
            await expect(status).not.toContainText('The Game Master is working');
        } finally {
            runDjangoShell(finishRunningTurnsScript([otherTable]));
        }

        await expect(page.locator('.chat-messages'))
            .toContainText('The crypt is quiet, and nothing stirs.', { timeout: 30000 });
        await expect(page.locator('#gm-status')).toBeHidden();
    });
});
