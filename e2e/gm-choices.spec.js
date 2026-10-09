/**
 * GM choice buttons (issue #342): a GM reply that ends in a numbered list is
 * offered back as buttons plus "Something else…".
 *
 * The GM stream endpoint talks to Ollama, which the e2e run never has, so
 * every test stubs `gm/stream/` with a canned SSE body through page.route —
 * the same frames gm_stream emits, minus the model.
 */

const { test, expect, TEST_USER } = require('./fixtures');
const { runDjangoShell } = require('./django');

const CHOICE_REPLY = 'The guard blocks the gate.\n\n1. **Bribe** the guard\n2. Sneak past\n3. Draw steel';
const PLAIN_REPLY = 'The gate is shut. What do you do?';

/** One SSE token frame and a done frame, as gm_stream would send them. */
function sseBody(fullResponse) {
    return [
        `data: ${JSON.stringify({ token: fullResponse })}`,
        '',
        `data: ${JSON.stringify({ done: true, full_response: fullResponse })}`,
        '',
        '',
    ].join('\n');
}

/**
 * Answer every gm/stream POST with `fullResponse`, recording the bodies the
 * page sent so a test can check what the player's click transmitted.
 */
async function stubStream(page, fullResponse) {
    const sentBodies = [];
    await page.route('**/tarmar/gm/stream/', async (route) => {
        sentBodies.push(route.request().postDataJSON());
        await route.fulfill({
            status: 200,
            contentType: 'text/event-stream',
            body: sseBody(fullResponse),
        });
    });
    return sentBodies;
}

/** Send a message and wait for the stubbed reply to finish streaming. */
async function askTheGm(page, message) {
    const repliesBefore = await page.locator('.message.assistant').count();
    await page.fill('#user-input', message);
    await page.click('#send-btn');
    await expect(page.locator('.message.assistant')).toHaveCount(repliesBefore + 1);
    // The send button re-enables once the done frame has been handled.
    await expect(page.locator('#send-btn')).toBeEnabled();
}

test.describe('GM choice bar', () => {
    test.beforeEach(async ({ authenticatedPage }) => {
        await authenticatedPage.goto('/tarmar/gm/');
    });

    test('a reply ending in a numbered list renders buttons plus "Something else…"', async ({ authenticatedPage: page }) => {
        await stubStream(page, CHOICE_REPLY);
        await askTheGm(page, 'I approach the gate');

        const bar = page.locator('.message.assistant .choice-bar');
        await expect(bar).toHaveCount(1);
        const buttons = bar.locator('button');
        await expect(buttons).toHaveCount(4);
        await expect(buttons.nth(0).locator('.choice-label')).toHaveText('Bribe the guard');
        await expect(buttons.nth(1).locator('.choice-label')).toHaveText('Sneak past');
        await expect(buttons.nth(2).locator('.choice-label')).toHaveText('Draw steel');
        await expect(buttons.nth(3)).toHaveText('Something else…');
        // The bar is not transient: it is still there after a pause.
        await page.waitForTimeout(500);
        await expect(bar).toHaveCount(1);
    });

    test('a final "Something else?" item is the escape button, not a fourth option (#384)', async ({ authenticatedPage: page }) => {
        await stubStream(page, `${CHOICE_REPLY}\n4. Something else?`);
        await askTheGm(page, 'I approach the gate');

        const buttons = page.locator('.message.assistant .choice-bar button');
        await expect(buttons).toHaveCount(4);
        await expect(page.locator('.choice-bar .choice-btn[data-choice]')).toHaveCount(3);
        await expect(buttons.nth(3)).toHaveText('Something else…');
    });

    test('a plain reply renders no bar', async ({ authenticatedPage: page }) => {
        await stubStream(page, PLAIN_REPLY);
        await askTheGm(page, 'I approach the gate');
        await expect(page.locator('.choice-bar')).toHaveCount(0);
    });

    test('clicking an option sends "N. text" as the player message and removes the bar', async ({ authenticatedPage: page }) => {
        const sentBodies = await stubStream(page, CHOICE_REPLY);
        await askTheGm(page, 'I approach the gate');

        await page.click('.choice-bar .choice-btn[data-choice="2"]');

        await expect(page.locator('.message.user .message-content').last()).toHaveText('2. Sneak past');
        await expect(page.locator('.choice-bar')).toHaveCount(1); // the new reply's bar
        await expect.poll(() => sentBodies.length).toBe(2);
        expect(sentBodies[1].message).toBe('2. Sneak past');
        // The old bar is gone; the surviving one belongs to the newest reply.
        const assistantMessages = page.locator('.message.assistant');
        await expect(assistantMessages).toHaveCount(2);
        await expect(assistantMessages.nth(0).locator('.choice-bar')).toHaveCount(0);
        await expect(assistantMessages.nth(1).locator('.choice-bar')).toHaveCount(1);
    });

    test('"Something else…" dismisses the bar and focuses the input', async ({ authenticatedPage: page }) => {
        const sentBodies = await stubStream(page, CHOICE_REPLY);
        await askTheGm(page, 'I approach the gate');

        await page.click('.choice-bar .choice-btn-other');

        await expect(page.locator('.choice-bar')).toHaveCount(0);
        await expect(page.locator('#user-input')).toBeFocused();
        expect(sentBodies).toHaveLength(1);
    });

    test('a number key picks that option while the input is empty', async ({ authenticatedPage: page }) => {
        const sentBodies = await stubStream(page, CHOICE_REPLY);
        await askTheGm(page, 'I approach the gate');

        await page.locator('#user-input').press('3');

        await expect(page.locator('.message.user .message-content').last()).toHaveText('3. Draw steel');
        await expect.poll(() => sentBodies.length).toBe(2);
        expect(sentBodies[1].message).toBe('3. Draw steel');
        await expect(page.locator('#user-input')).toHaveValue('');
    });

    test('a number key is ordinary typing once the input holds text', async ({ authenticatedPage: page }) => {
        const sentBodies = await stubStream(page, CHOICE_REPLY);
        await askTheGm(page, 'I approach the gate');

        const input = page.locator('#user-input');
        await input.type('I have 2');

        await expect(input).toHaveValue('I have 2');
        await expect(page.locator('.choice-bar')).toHaveCount(1);
        expect(sentBodies).toHaveLength(1);
    });

    test('a typed message removes the bar', async ({ authenticatedPage: page }) => {
        await stubStream(page, CHOICE_REPLY);
        await askTheGm(page, 'I approach the gate');
        await expect(page.locator('.choice-bar')).toHaveCount(1);

        await page.unroute('**/tarmar/gm/stream/');
        await stubStream(page, PLAIN_REPLY);
        await askTheGm(page, 'I shout at the gate');

        await expect(page.locator('.choice-bar')).toHaveCount(0);
    });
});

test.describe('GM choice bar on a reloaded session', () => {
    // Two saved sessions for testuser: one whose last message is a GM choice
    // prompt, one where the player answered. Seeded here rather than in
    // global-setup because no other spec needs GM history; teardown's
    // deletion of testuser cascades to both.
    //
    // Named per worker (issue #428). Each worker runs this beforeAll, and it
    // deletes what it is about to seed; with shared names, a second worker's
    // delete removed the session a first worker's test was opening.
    const SESSION_NAMES = {};

    // Playwright requires the first argument to be a destructuring pattern.
    test.beforeAll(({}, workerInfo) => {
        const prefix = `e2e choices w${workerInfo.workerIndex}:`;
        SESSION_NAMES.prefix = prefix;
        SESSION_NAMES.gmLast = `${prefix} GM spoke last`;
        SESSION_NAMES.playerLast = `${prefix} player spoke last`;
        // runDjangoShell wraps the script in double quotes for the shell, so
        // the reply goes in as a single-quoted Python literal with \n escapes.
        const choiceReply = `'${CHOICE_REPLY.replace(/\n/g, '\\n')}'`;
        runDjangoShell(`
from django.contrib.auth import get_user_model
from gm.models import GMSession
owner = get_user_model().objects.get(username='${TEST_USER.username}')
GMSession.objects.filter(user=owner, name__startswith='${SESSION_NAMES.prefix}').delete()
gm_turn = {'role': 'assistant', 'content': ${choiceReply}}
opening = {'role': 'user', 'content': 'I approach the gate'}
gm_last = GMSession(user=owner, name='${SESSION_NAMES.gmLast}')
gm_last.history = [opening, gm_turn]
gm_last.save()
player_last = GMSession(user=owner, name='${SESSION_NAMES.playerLast}')
player_last.history = [opening, gm_turn, {'role': 'user', 'content': '2. Sneak past'}]
player_last.save()
print('Seeded GM choice sessions')
`);
    });

    async function openSession(page, name) {
        await page.goto('/tarmar/gm/');
        await page.click(`.session-item a:has-text(${JSON.stringify(name)})`);
        await page.waitForURL(/\?session=\d+/);
    }

    test('re-derives the bar when the GM choice prompt is the last message', async ({ authenticatedPage: page }) => {
        await openSession(page, SESSION_NAMES.gmLast);

        const bar = page.locator('.message.assistant .choice-bar');
        await expect(bar).toHaveCount(1);
        await expect(bar.locator('.choice-btn')).toHaveCount(4);
        // The reply itself still renders as markdown.
        await expect(page.locator('.message.assistant .message-content ol li')).toHaveCount(3);
    });

    test('renders no bar when the player spoke last', async ({ authenticatedPage: page }) => {
        await openSession(page, SESSION_NAMES.playerLast);

        await expect(page.locator('.message')).toHaveCount(3);
        await expect(page.locator('.choice-bar')).toHaveCount(0);
    });
});
