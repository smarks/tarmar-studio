/**
 * E2E: a GM turn is not lost because the page went away (issue #515).
 *
 * Production lost two turns from an iPhone whose page dropped the stream
 * mid-turn. The turn now runs to the end on the server either way. These play
 * a real turn through the dev server against the fake Ollama
 * (e2e/fake-ollama.js), whose reply pauses when the message says "slowly",
 * and leave the page while it is paused.
 *
 * The specs sign in as their own user: a running turn shows on that user's
 * chat page, and would hold up any other spec sharing the account. The user
 * is named for the worker and the repeat, so copies of this spec running at
 * once under --repeat-each never share one (issue #889).
 */

const { test, expect, loginAs } = require('./fixtures');
const { runDjangoShell } = require('./django');
const { specAccount, deleteSpecAccountsScript } = require('./accounts');

/** This worker's and this repeat's own account (issue #889). */
let KEEPER;

/**
 * Create the account if it is new and clear its sessions.
 * @param {{parallelIndex: number, repeatEachIndex: number}} testInfo
 */
function seedKeeper(testInfo) {
    KEEPER = specAccount('turnkeeper', 'turnkeeper-pass-515', testInfo);
    runDjangoShell(`
from django.contrib.auth import get_user_model
from gm.models import GMSession
user, _created = get_user_model().objects.get_or_create(username=${JSON.stringify(KEEPER.username)})
user.set_password(${JSON.stringify(KEEPER.password)})
user.save()
GMSession.objects.filter(user=user).delete()
`);
}

test.describe.configure({ mode: 'serial' });

test.afterAll(({}, testInfo) => {
    runDjangoShell(deleteSpecAccountsScript([
        specAccount('turnkeeper', 'turnkeeper-pass-515', testInfo),
    ]));
});

test.describe('GM chat — a turn outlives its page', () => {
    test.beforeEach(({}, testInfo) => {
        seedKeeper(testInfo);
    });

    async function startSlowTurn(page) {
        await loginAs(page, KEEPER);
        await page.goto('/tarmar/gm/');
        await page.fill('#user-input', 'I search the crypt slowly');
        await page.click('#send-btn');
        // The first words are on screen: the server is inside the turn, and
        // the fake model is holding the rest back.
        await expect(page.locator('.message.assistant')).toContainText('The fake Game Master considers it.');
    }

    test('reloading mid-turn says it is still running, then shows the reply', async ({ page }) => {
        await startSlowTurn(page);

        await page.reload();

        const running = page.locator('#gm-turn-running');
        await expect(running).toBeVisible();
        await expect(running).toContainText('still working on your last message');
        await expect(running).toContainText('I search the crypt slowly');

        const messages = page.locator('.chat-messages');
        await expect(messages).toContainText('The crypt is quiet, and nothing stirs.', { timeout: 30000 });
        await expect(messages).toContainText('I search the crypt slowly');
        await expect(page).toHaveURL(/session=\d+/);
        await expect(running).toBeHidden();
    });

    test('closing the page mid-turn and coming back finds the reply in the history', async ({ page, context }) => {
        // The fixture's own page stays open (it collects coverage); the turn
        // is played in a second tab of the same browser, which is closed.
        await loginAs(page, KEEPER);
        const player = await context.newPage();
        await player.goto('/tarmar/gm/');
        await player.fill('#user-input', 'I search the crypt slowly');
        await player.click('#send-btn');
        await expect(player.locator('.message.assistant')).toContainText('The fake Game Master considers it.');

        await player.close();
        // Long enough for the fake model's pause to end and the turn to save.
        await new Promise((resolve) => setTimeout(resolve, 8000));

        const again = page;
        await again.goto('/tarmar/gm/');
        // The turn saved its session under a name taken from the message.
        await again.locator('.session-item a').filter({ hasText: 'I search the crypt' }).first().click();
        await expect(again).toHaveURL(/session=\d+/);

        const messages = again.locator('.chat-messages');
        await expect(messages).toContainText('I search the crypt slowly');
        await expect(messages).toContainText('The crypt is quiet, and nothing stirs.');
        await expect(again.locator('#gm-turn-running')).toBeHidden();
    });
});

test.describe('GM chat — two blank tabs are two games', () => {
    test.beforeEach(({}, testInfo) => {
        seedKeeper(testInfo);
    });

    test('a second blank tab is usable while the first tab\'s turn runs', async ({ page, context }) => {
        await loginAs(page, KEEPER);
        const first = await context.newPage();
        await first.goto('/tarmar/gm/');
        await first.fill('#user-input', 'I search the crypt slowly');
        await first.click('#send-btn');
        await expect(first.locator('.message.assistant')).toContainText('The fake Game Master considers it.');

        // A turn with no session yet names no session, so nothing ties it to
        // the page that sent it. This tab used to be handed it: read-only
        // until it landed, then navigated into the other tab's session
        // (issue #650).
        const second = await context.newPage();
        await second.goto('/tarmar/gm/');

        await expect(second.locator('#gm-turn-running')).toBeHidden();
        await expect(second.locator('#send-btn')).toBeEnabled();
        await expect(second).toHaveURL(/\/tarmar\/gm\/$/);

        // And it can start a game of its own, which is what the server was
        // already willing to allow.
        await second.fill('#user-input', 'I walk to Farport');
        await second.click('#send-btn');
        await expect(second.locator('.message.assistant').last()).toContainText('nothing stirs', { timeout: 30000 });
        await expect(second).toHaveURL(/session=\d+/);

        // Two games, not one: the first tab lands in its own session.
        await expect(first.locator('.message.assistant').last()).toContainText('nothing stirs', { timeout: 30000 });
        await expect(first).toHaveURL(/session=\d+/);
        expect(new URL(first.url()).searchParams.get('session'))
            .not.toBe(new URL(second.url()).searchParams.get('session'));
        await first.close();
        await second.close();
    });
});
