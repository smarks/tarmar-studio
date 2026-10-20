/**
 * The GM page renders the latest page of a long session (issue #972).
 *
 * Spencer's ruling, 2026-10-10: "the GM page renders the latest PAGE_SIZE
 * turns and resolved threads; a persistent 'Show earlier' control loads the
 * previous page above (no transient UI); the transcript export keeps
 * everything."
 *
 * The session's earlier turns are written to its history directly: the turn
 * endpoint takes 30 turns a minute (rate_limit on gm_stream), fewer than a
 * page needs. One turn then goes through the fake model, so the live path is
 * checked against a paged page: the reply lands at the bottom, under the
 * turns the control loaded.
 */

const { test, expect, loginAs, TEST_USER, TEST_ADMIN } = require('./fixtures');
const { readDjangoShell } = require('./django');

const PAGE_SIZE = 50;
const TURNS = PAGE_SIZE + 2;

/** Seed a session of TURNS turns and TURNS resolved threads for ``owner``; its id. */
function seedLongSession(tag, owner = TEST_USER) {
    const output = readDjangoShell(`
from django.contrib.auth import get_user_model
from gm.models import GMSession, StoryThread
user = get_user_model().objects.get(username='${owner.username}')
history = []
for number in range(1, ${TURNS} + 1):
    history.append({'role': 'user', 'content': f'Player move {number}.'})
    history.append({'role': 'assistant', 'content': f'GM reply **{number}**.'})
session = GMSession.objects.create(user=user, name='Long game ${tag}', history=history)
for number in range(1, ${TURNS} + 1):
    StoryThread.objects.create(session=session, text=f'Settled matter {number}', resolved=True)
print('SESSION', session.pk)
`);
    return output.match(/SESSION (\d+)/)[1];
}

test.describe('GM page — a long session, a page at a time (#972)', () => {
    test('the latest turns show; Show earlier loads the page above in place', async ({ page }, testInfo) => {
        const sessionId = seedLongSession(`${testInfo.workerIndex}-${Date.now()}`);
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);

        const moves = page.locator('#messages .message.user .message-content');
        await expect(moves).toHaveCount(PAGE_SIZE);
        await expect(moves.first()).toHaveText('Player move 3.');
        await expect(moves.last()).toHaveText(`Player move ${TURNS}.`);
        const control = page.locator('#messages [data-earlier]');
        await expect(control).toContainText('2 earlier turns not shown.');

        // Scrolled to the top, where the control is, from the keyboard.
        const firstShown = page.locator('#messages .message.user', { hasText: 'Player move 3.' });
        await page.locator('#messages').evaluate((element) => { element.scrollTop = 0; });
        const before = await firstShown.boundingBox();
        const button = control.getByRole('button', { name: 'Show earlier turns' });
        await button.focus();
        await page.keyboard.press('Enter');

        await expect(moves).toHaveCount(TURNS);
        await expect(moves.first()).toHaveText('Player move 1.');
        await expect(control).toHaveCount(0);
        // The loaded replies are rendered as the page renders its own.
        await expect(page.locator('#messages .message.assistant').first().locator('strong')).toHaveText('1');
        // What the reader was looking at stayed where it was on screen.
        const after = await firstShown.boundingBox();
        expect(Math.abs(after.y - before.y)).toBeLessThanOrEqual(2);
        // Focus is on the first turn loaded, not lost to the page.
        await expect(page.locator('#messages .message.user').first()).toBeFocused();

        // A live turn through the fake model lands at the bottom.
        await page.fill('#user-input', 'I look around the crypt.');
        await page.click('#send-btn');
        await expect(page.locator('#messages .message.assistant').last()).toContainText('nothing stirs', { timeout: 30000 });
        await expect(moves).toHaveCount(TURNS + 1);
        await expect(moves.first()).toHaveText('Player move 1.');

        // A reload opens on the latest page again, the new turn last.
        await page.reload();
        await expect(moves).toHaveCount(PAGE_SIZE);
        await expect(moves.first()).toHaveText('Player move 4.');
        await expect(moves.last()).toHaveText('I look around the crypt.');
        await expect(control).toContainText('3 earlier turns not shown.');
    });

    test('the Campaign tab lists the latest resolved threads and loads the rest', async ({ page }, testInfo) => {
        const sessionId = seedLongSession(`threads-${testInfo.workerIndex}-${Date.now()}`);
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);
        await page.click('#gm-panel-tab-campaign');

        const resolved = page.locator('#gm-panel-campaign .gm-campaign-resolved');
        await resolved.locator('summary').click();
        await expect(resolved.locator('summary')).toHaveText(`${TURNS} resolved`);
        const names = resolved.locator('li[data-thread-id] .gm-campaign-name');
        await expect(names).toHaveCount(PAGE_SIZE);
        await expect(names.first()).toHaveText('Settled matter 3');

        await resolved.getByRole('button', { name: 'Show earlier threads' }).click();

        await expect(names).toHaveCount(TURNS);
        await expect(names.first()).toHaveText('Settled matter 1');
        await expect(resolved.locator('[data-earlier]')).toHaveCount(0);
    });

    test('Reopen on a thread from an earlier page leaves the loaded pages in place (#982)', async ({ page }, testInfo) => {
        // Reopen is a Game Master's control.
        const sessionId = seedLongSession(`reopen-${testInfo.workerIndex}-${Date.now()}`, TEST_ADMIN);
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);
        await page.click('#gm-panel-tab-campaign');

        const resolved = page.locator('#gm-panel-campaign .gm-campaign-resolved');
        await resolved.locator('summary').click();
        const names = resolved.locator('li[data-thread-id] .gm-campaign-name');
        await expect(names).toHaveCount(PAGE_SIZE);
        await resolved.getByRole('button', { name: 'Show earlier threads' }).click();
        await expect(names).toHaveCount(TURNS);
        await expect(names.first()).toHaveText('Settled matter 1');

        // A thread on the page that was loaded, not on the one the tab opened with.
        await resolved.locator('li[data-thread-id]')
            .filter({ has: page.getByText('Settled matter 1', { exact: true }) })
            .getByRole('button', { name: 'Reopen' }).click();

        // The change is saved and shown: the thread is open again, so it has
        // left the resolved list. Every other thread loaded stays, the
        // earlier page among them, and the list is still open.
        await expect(page.locator('#gm-panel-campaign-status')).toContainText('Reopened thread');
        await expect(page.locator('#gm-campaign-threads-group')).toContainText('Settled matter 1');
        await expect(resolved.locator('summary')).toHaveText(`${TURNS - 1} resolved`);
        await expect(names).toHaveCount(TURNS - 1);
        await expect(names.first()).toHaveText('Settled matter 2');
        await expect(names.last()).toHaveText(`Settled matter ${TURNS}`);
        await expect(resolved).toHaveAttribute('open', '');
        await expect(resolved.locator('[data-earlier]')).toHaveCount(0);
    });

    test('an earlier page that lands after a turn refetched the tab is still shown (#14)', async ({ page }, testInfo) => {
        const sessionId = seedLongSession(`race-${testInfo.workerIndex}-${Date.now()}`);
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);
        await page.click('#gm-panel-tab-campaign');
        const resolved = page.locator('#gm-panel-campaign .gm-campaign-resolved');
        await resolved.locator('summary').click();
        const names = resolved.locator('li[data-thread-id] .gm-campaign-name');
        await expect(names).toHaveCount(PAGE_SIZE);

        // The earlier page is held on its way until the turn's refetch of
        // the tab has landed and replaced the control that asked for it.
        let releaseEarlier;
        const earlierHeld = new Promise((resolve) => { releaseEarlier = resolve; });
        await page.route((url) => url.pathname.endsWith(`/sessions/${sessionId}/threads/resolved/`), async (route) => {
            await earlierHeld;
            await route.continue();
        });
        const button = resolved.getByRole('button', { name: 'Show earlier threads' });
        const pressed = await button.elementHandle();
        await button.click();

        const refetched = page.waitForResponse((response) => response.url().includes(`/sessions/${sessionId}/campaign/`));
        await page.fill('#user-input', 'I look around the crypt.');
        await page.click('#send-btn');
        await expect(page.locator('#messages .message.assistant').last()).toContainText('nothing stirs', { timeout: 30000 });
        await refetched;
        await expect.poll(() => pressed.evaluate((element) => element.isConnected)).toBe(false);

        releaseEarlier();

        await expect(names).toHaveCount(TURNS);
        await expect(names.first()).toHaveText('Settled matter 1');
        await expect(resolved.locator('[data-earlier]')).toHaveCount(0);
        // The reader had moved on to the chat: a page landing a turn later
        // does not take the focus from it.
        await expect(resolved.locator(':focus')).toHaveCount(0);
    });
});
