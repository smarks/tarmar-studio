/**
 * E2E: an account with as many turns running as it may is refused (issue #174).
 *
 * A turn holds a request thread for as long as it runs and goes on after its
 * page leaves (#515), and a message sent with no session started a fresh turn
 * every time, so one account could hold every thread the site has. The server
 * now refuses the turn past the account's cap, and the page shows the refusal
 * as a notice that stays, with the message back in the box -- never a passing
 * banner (the no-transient-UI rule).
 *
 * What only the running server proves: the real page, sending through the
 * real endpoint, meets the refusal in the server's own words and keeps it on
 * screen; and once the account's turns have finished, the same message is
 * answered. The account's running turns are recorded through the Django
 * shell, as gm-queued-turn.spec.js records another table's: a turn held open
 * on the fake Ollama would hold a dev-server thread for every worker, and the
 * count is the server's view of running turns either way. The cap and the
 * wording are read from the server, not typed here. The account is this
 * worker's and this repeat's own (issue #889), and its stand-in turns are
 * finished in `finally`, so a failing run frees them for the others.
 *
 * The site-wide cap has no spec of its own: the count is every account's
 * running turns on the one dev server, so filling it would refuse every
 * other worker's GM turn while this one ran. gm/test_turn_cap.py pins it.
 */

const { test, expect, loginAs } = require('./fixtures');
const { runDjangoShell, readDjangoJson } = require('./django');
const { specAccount, deleteSpecAccountsScript } = require('./accounts');

const cappedPlayerFor = (testInfo) => specAccount('cappedplayer', 'cappedplayer-pass-174', testInfo);

/** The Django shell lines that finish every running turn of ``account``. */
function finishRunningTurnsScript(account) {
    return `
from gm.models import GMTurnRecord
GMTurnRecord.objects.filter(
    user__username=${JSON.stringify(account.username)}, outcome=GMTurnRecord.OUTCOME_RUNNING
).update(outcome=GMTurnRecord.OUTCOME_ERROR, error="e2e: the account's stand-in turn")
`;
}

test.describe.configure({ mode: 'serial' });

test.describe('GM chat — an account with its turns all running', () => {
    let player;

    test.beforeEach(({}, testInfo) => {
        player = cappedPlayerFor(testInfo);
        runDjangoShell(`
from django.contrib.auth import get_user_model
from gm.models import GMSession
user, _created = get_user_model().objects.get_or_create(username=${JSON.stringify(player.username)})
user.set_password(${JSON.stringify(player.password)})
user.save()
GMSession.objects.filter(user=user).delete()
`);
    });

    test.afterAll(({}, testInfo) => {
        const account = cappedPlayerFor(testInfo);
        // A running record outlives its user (SET_NULL), so it is finished
        // first or it would go on counting towards the site's cap.
        runDjangoShell(finishRunningTurnsScript(account));
        runDjangoShell(deleteSpecAccountsScript([account]));
    });

    test('the refusal stays on the page in the server\'s words, and the turn runs once a slot is free', async ({ page }) => {
        // Shell runs take seconds each, and the reply at the end plays a
        // whole turn through the fake Ollama.
        test.setTimeout(90000);
        // One shell run records the cap's worth of running turns for the
        // account and reads the server's wording for the refusal.
        const refusal = readDjangoJson(`
import json
from django.contrib.auth import get_user_model
from gm.models import GMTurnRecord
from gm.refusal_words import refusal_words
from gm.turn_record import running_turn_cap
user = get_user_model().objects.get(username=${JSON.stringify(player.username)})
cap = running_turn_cap(is_game_master=user.can_manage_users)
for _turn in range(cap):
    GMTurnRecord.objects.create(user=user, outcome=GMTurnRecord.OUTCOME_RUNNING, mode="play")
print("refusal " + json.dumps({
    "cap": cap,
    "words": refusal_words().views.too_many_turns_running,
}))
`, 'refusal');
        expect(refusal.cap).toBeGreaterThan(0);
        expect(refusal.words.length).toBeGreaterThan(0);

        const message = 'I send one message too many';
        try {
            await loginAs(page, player);
            await page.goto('/tarmar/gm/');
            await page.fill('#user-input', message);
            await page.click('#send-btn');

            const notice = page.locator('.notice', { hasText: refusal.words });
            await expect(notice).toBeVisible();
            // The message is the player's to send again: back in the box.
            await expect(page.locator('#user-input')).toHaveValue(message);
            // State, not a toast: still there after the time a banner would
            // have taken to dismiss itself.
            await page.waitForTimeout(3000);
            await expect(notice).toBeVisible();
            await expect(page.locator('#gm-status')).toBeHidden();
        } finally {
            runDjangoShell(finishRunningTurnsScript(player));
        }

        // The slots are free: the same message is now answered.
        await page.click('#send-btn');
        await expect(page.locator('.chat-messages'))
            .toContainText('The crypt is quiet, and nothing stirs.', { timeout: 30000 });
        await expect(page.locator('#gm-status')).toBeHidden();
    });
});
