/**
 * E2E: a slash command stays what the player typed after a reload (issue #758).
 *
 * `/rules` and `/state` hand the model a message the server writes. It used to
 * be stored as the player's own message, so after a reload the chat showed
 * "I need a precise rules lookup. Here are the full rules for reference: ..."
 * -- the whole rules tree, about 80,000 characters -- as something the player
 * had said. The turns are played through the dev server against the fake
 * Ollama (e2e/fake-ollama.js).
 *
 * The spec signs in as its own user so its sessions cannot collide with
 * another spec's, named for the worker and the repeat so copies of it under
 * --repeat-each cannot collide either (issue #889).
 */

const { test, expect, loginAs } = require('./fixtures');
const { runDjangoShell } = require('./django');
const { specAccount, deleteSpecAccountsScript } = require('./accounts');

/** This worker's and this repeat's own account (issue #889). */
const askerFor = (testInfo) => specAccount('rulesasker', 'rulesasker-pass-758', testInfo);
let ASKER;

test.describe.configure({ mode: 'serial' });

test.describe('GM chat — a command is stored as the player typed it', () => {
    test.beforeEach(({}, testInfo) => {
        ASKER = askerFor(testInfo);
        runDjangoShell(`
from django.contrib.auth import get_user_model
from gm.models import GMSession
user, _created = get_user_model().objects.get_or_create(username=${JSON.stringify(ASKER.username)})
user.set_password(${JSON.stringify(ASKER.password)})
user.save()
GMSession.objects.filter(user=user).delete()
`);
    });

    test.afterAll(({}, testInfo) => {
        runDjangoShell(deleteSpecAccountsScript([askerFor(testInfo)]));
    });

    async function send(page, line) {
        const replies = page.locator('.message.assistant');
        const before = await replies.count();
        await page.fill('#user-input', line);
        await page.click('#send-btn');
        await expect(replies).toHaveCount(before + 1);
        await expect(replies.last()).toContainText('The crypt is quiet', { timeout: 30000 });
        await expect(page).toHaveURL(/session=\d+/);
        // The words stream before the turn is saved; Send comes back only once
        // the stream has closed, after the done frame the server sends once the
        // turn is saved (issue #910).
        await expect(page.locator('#send-btn')).toBeEnabled({ timeout: 30000 });
    }

    test('/rules and /state read back as the commands after a reload', async ({ page }) => {
        await loginAs(page, ASKER);
        await page.goto('/tarmar/gm/');

        await send(page, '/rules how far can I move in a turn?');
        await send(page, '/state');
        await page.reload();

        const said = page.locator('.message.user .message-content');
        await expect(said).toHaveText(['/rules how far can I move in a turn?', '/state']);
        const messages = page.locator('.chat-messages');
        await expect(messages).not.toContainText('I need a precise rules lookup');
        await expect(messages).not.toContainText('Please summarize the current game state');
    });
});
