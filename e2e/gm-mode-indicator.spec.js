/**
 * E2E for the GM chat's mode indicator and mode control (issues #353, #372).
 *
 * A Game Master consulting the aide at a real table needs to see which GM
 * they are talking to — and, since #372, to change it, because a DM who
 * wanted to play a game could not reach one. The strip is part of the page,
 * above the transcript, with nothing to dismiss and nothing that hides it
 * after a moment; changing the mode paints the page from the stored value,
 * in place (issue #89), rather than flashing anything at the reader: the
 * reload it used to do threw away the earlier turns the reader had loaded.
 * The seeded 'admin' is an admin and a DM; 'testuser' has neither role.
 */

const { test, expect, loginAs, TEST_USER, TEST_ADMIN } = require('./fixtures');
const { readDjangoShell } = require('./django');

/** Leave a mark on the page that a reload would wipe. */
async function markThePage(page) {
    await page.evaluate(() => { window.issue89NotReloaded = true; });
}

/** Whether the mark left by markThePage is still there. */
async function stillTheSamePage(page) {
    return page.evaluate(() => window.issue89NotReloaded === true);
}

test.describe('GM chat — mode indicator', () => {
    test('a Game Master sees the aide badge, and it stays', async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        await page.goto('/tarmar/gm/');

        const indicator = page.locator('#gm-mode-indicator');
        await expect(indicator).toBeVisible();
        await expect(indicator.locator('.gm-mode-name')).toHaveText('GM aide');
        await expect(indicator).toContainText('You are running the game; it is not.');

        // Above the transcript, inside the chat column — not floating over it.
        const messages = page.locator('#messages');
        const indicatorBox = await indicator.boundingBox();
        const messagesBox = await messages.boundingBox();
        expect(indicatorBox.y + indicatorBox.height).toBeLessThanOrEqual(messagesBox.y + 1);

        // Nothing dismisses it: still there after the page has settled and
        // after a reload.
        await page.waitForTimeout(1500);
        await expect(indicator).toBeVisible();
        await page.reload();
        await expect(page.locator('#gm-mode-indicator')).toBeVisible();
    });

    test('a player sees the strip naming their own mode', async ({ page }) => {
        // Before #372 a player saw nothing here, because play was the only
        // mode they could have. They can switch now, so the strip is theirs
        // too — naming the Game Master who runs the game, not the aide.
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/gm/');

        const indicator = page.locator('#gm-mode-indicator');
        await expect(indicator).toBeVisible();
        await expect(indicator.locator('.gm-mode-name')).toHaveText('Game Master');
        await expect(page.locator('#gm-mode-select')).toHaveValue('play');
    });

    test('a Game Master switches to play, and the page says so without a reload',
         async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        await page.goto('/tarmar/gm/');

        const select = page.locator('#gm-mode-select');
        await expect(select).toHaveValue('aide');
        await expect(page.locator('#user-input')).toHaveAttribute('placeholder',
                                                                  'Ask the aide');
        await markThePage(page);

        // Choosing posts the form; the server stores the choice and the page
        // is painted from it. Nothing appears and disappears on its own. With
        // no session yet the choice rides in the address, as it did.
        await select.selectOption('play');
        await page.waitForURL(/mode=play/);

        await expect(page.locator('#gm-mode-indicator .gm-mode-name'))
            .toHaveText('Game Master');
        await expect(page.locator('#gm-mode-select')).toHaveValue('play');
        await expect(page.locator('#user-input')).toHaveAttribute('placeholder',
                                                                  'What do you do?');
        await expect(page.locator('.empty-state')).toContainText('what you\'d like to do');
        expect(await stillTheSamePage(page)).toBe(true);

        // And it survives a reload: it is stored state, not a page-local flag.
        await page.reload();
        await expect(page.locator('#gm-mode-select')).toHaveValue('play');
    });

    test('switching a session\'s mode keeps the turns on the page (#89)', async ({ page }, testInfo) => {
        const output = readDjangoShell(`
from django.contrib.auth import get_user_model
from gm.models import GMSession
owner = get_user_model().objects.get(username=${JSON.stringify(TEST_ADMIN.username)})
session = GMSession.objects.create(
    user=owner, name='Mode switch ${testInfo.workerIndex}', mode='aide',
    history=[{'role': 'user', 'content': 'Who rules Asterhold?'},
             {'role': 'assistant', 'content': 'The Margrave does.'}],
)
print('SESSION', session.pk)
`);
        const sessionId = output.match(/SESSION (\d+)/)[1];
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);
        await markThePage(page);

        await page.locator('#gm-mode-select').selectOption('play');

        await expect(page.locator('#gm-mode-indicator .gm-mode-name')).toHaveText('Game Master');
        await expect(page.locator('#user-input')).toHaveAttribute('placeholder', 'What do you do?');
        await expect(page.locator('#messages')).toContainText('The Margrave does.');
        expect(new URL(page.url()).searchParams.get('session')).toBe(sessionId);
        expect(await stillTheSamePage(page)).toBe(true);

        // Stored on the session: a reload opens it in the mode chosen.
        await page.reload();
        await expect(page.locator('#gm-mode-select')).toHaveValue('play');
    });

    test('the control is a form on the page, not an item in a menu',
         async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        await page.goto('/tarmar/gm/');

        const form = page.locator('#gm-mode-indicator form.gm-mode-switch');
        await expect(form).toBeVisible();
        await expect(form).toHaveAttribute('action', '/tarmar/gm/mode/');
        // Both modes are offered, in one place, without opening anything.
        await expect(page.locator('#gm-mode-select option')).toHaveCount(2);
    });
});
