/**
 * E2E for the UI-expectations fixes (issue #339, items 1-12).
 *
 * What only a real browser proves here: the nav link a player can actually
 * click; a refused lobby post landing back on the muster instead of on a
 * page of raw JSON; a seated player handing their own figure to the AI; the
 * DM freeing somebody else's seat through the app's own confirmation; a
 * battle deleted, listed under Deleted, and restored; and — on the GM chat —
 * a session renamed in place and a failed turn that hands the player their
 * message back instead of swallowing it.
 */

const { refusal } = require('./refusals');
const {
    test, expect, TEST_USER, TEST_ADMIN, loginAs, submitPassphrase,
    createBattleViaSetup, battleRowOnList, chooseToAttach,
} = require('./fixtures');
const { startCoverage } = require('./coverage');

/** A second, fully separate browser context signed in as the plain player. */
async function playerContextOf(browser) {
    const context = await browser.newContext();
    const page = await context.newPage();
    await startCoverage(page);
    await submitPassphrase(page);
    await loginAs(page, TEST_USER);
    return { context, page };
}

test.describe('Battles are reachable and manageable (#339)', () => {
    test('a player finds Battles in the menu and on the home page', async ({ page }) => {
        await submitPassphrase(page);
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/');

        // Item 1: the link used to render only inside the DM-only Admin
        // section, so a player's only way in was a pasted URL.
        await expect(page.locator('.home-nav a[href="/tarmar/battles/"]')).toBeVisible();
        await page.click('#menuToggle');
        await expect(page.locator('#menu a[href="/tarmar/battles/"]')).toBeVisible();

        await page.click('.home-nav a[href="/tarmar/battles/"]');
        await expect(page.locator('h1')).toHaveText('Battle Royale Simulator');
    });

    test('a refused claim lands back on the muster with the reason', async ({ adminPage, browser }) => {
        test.setTimeout(120000);
        const battleName = `E2E Refusal ${Date.now()}`;
        await createBattleViaSetup(adminPage, {
            name: battleName, fighters: 1, seed: 51, playerSeats: 1,
        });
        const battleUrl = adminPage.url();

        const { context, page } = await playerContextOf(browser);
        await page.goto(battleUrl);
        await page.click('summary:has-text("Claim this seat")');
        await page.selectOption('select[name="archetype"]', 'archer');
        await page.click('button:has-text("Join with a generated fighter")');
        await expect(page.locator('.lobby-seat.is-you')).toBeVisible();

        // A second browser takes the same seat: refused, and item 2 says so
        // on the page rather than replacing it with {"error": ...}.
        const rival = await browser.newContext();
        const rivalPage = await rival.newPage();
        await submitPassphrase(rivalPage);
        await loginAs(rivalPage, TEST_ADMIN);
        await rivalPage.goto(battleUrl);
        const seatId = await page.locator('.lobby-seat.is-you').getAttribute('data-combatant');
        await rivalPage.evaluate(([url, seat]) => {
            const token = document.querySelector('[name=csrfmiddlewaretoken]').value;
            const form = document.createElement('form');
            form.method = 'post';
            form.action = `${new URL(url).pathname}seats/${seat}/claim/`;
            form.innerHTML = `<input name=csrfmiddlewaretoken value="${token}">`
                + '<input name=archetype value="wizard">';
            document.body.appendChild(form);
            form.submit();
        }, [battleUrl, seatId]);

        await expect(rivalPage.locator('.setup-error')).toContainText(
            refusal('battle', 'services', 'seat_already_taken')
        );
        await expect(rivalPage.locator('.lobby-seats')).toBeVisible();
        await expect(rivalPage.locator('body')).not.toContainText('{"error"');

        await rival.close();
        await context.close();
    });

    test('a seated player hands their figure to the AI mid-battle', async ({ adminPage, browser }) => {
        test.setTimeout(120000);
        const battleName = `E2E Handover ${Date.now()}`;
        await createBattleViaSetup(adminPage, {
            name: battleName, fighters: 1, seed: 52, playerSeats: 1,
        });
        const battleUrl = adminPage.url();

        const { context, page } = await playerContextOf(browser);
        await page.goto(battleUrl);
        await page.click('summary:has-text("Claim this seat")');
        await page.selectOption('select[name="archetype"]', 'archer');
        await page.click('button:has-text("Join with a generated fighter")');
        // Joining posts and reloads the lobby; clicking Ready before that
        // page has landed can hit the old one and be lost (issue #493).
        await expect(page.locator('.lobby-seat.is-you')).toBeVisible();
        await page.click('button:has-text("I\'m ready")');
        await expect(page.locator('.lobby-ready-chip.is-ready')).toBeVisible();

        await expect(adminPage.locator('.lobby-ready-chip.is-ready'))
            .toBeVisible({ timeout: 15000 });
        await adminPage.click('.battle-start-form button[type="submit"]');
        await expect(adminPage.locator('#battle-status')).toHaveText('Setup');

        // Item 3: the panel the seated player never used to be offered, even
        // though the endpoint has always accepted them.
        // The player's lobby polls and reloads itself once the battle starts
        // (battle.js initLobbyPage). A goto here raced that reload and was
        // aborted with net::ERR_ABORTED (issue #517), so wait for the page's
        // own reload to land on the started battle instead.
        const panel = page.locator('#battle-my-control');
        await expect(panel).toBeVisible({ timeout: 15000 });
        await expect(panel.locator('.control-combatant-line'))
            .toContainText('Manual');
        await panel.locator('select[name="control_mode"]').selectOption('automatic');
        await panel.locator('button:has-text("Set")').click();

        await expect(page.locator('#battle-my-control .control-combatant-line'))
            .toContainText('Automatic');
        await expect(page.locator('#battle-log')).toContainText('hands its decisions');

        await context.close();
    });

    test('the DM frees a seat its holder walked away from', async ({ adminPage, browser }) => {
        test.setTimeout(120000);
        const battleName = `E2E Release ${Date.now()}`;
        await createBattleViaSetup(adminPage, {
            name: battleName, fighters: 1, seed: 53, playerSeats: 1,
        });
        const battleUrl = adminPage.url();

        const { context, page } = await playerContextOf(browser);
        await page.goto(battleUrl);
        await page.click('summary:has-text("Claim this seat")');
        await page.selectOption('select[name="archetype"]', 'archer');
        await page.click('button:has-text("Join with a generated fighter")');
        await expect(page.locator('.lobby-seat.is-you')).toBeVisible();
        await context.close();

        // Item 4: the DM is offered the release the services layer has
        // always allowed, and it asks first — the player loses their figure.
        await adminPage.goto(battleUrl);
        await adminPage.click('button:has-text("Release this seat")');
        const question = adminPage.locator('.notice-confirm');
        await expect(question).toContainText('loses the figure they brought');
        await question.locator('button:has-text("Release the seat")').click();

        await expect(adminPage.locator('.lobby-seat.is-open')).toHaveCount(1);
        await expect(adminPage.locator('.lobby-seats')).toContainText('Waiting for a player');
    });

    test('a battle is deleted, listed under Deleted, and restored', async ({ adminPage }) => {
        test.setTimeout(120000);
        const battleName = `E2E Delete ${Date.now()}`;
        await createBattleViaSetup(adminPage, { name: battleName, fighters: 2, seed: 54 });
        const battleUrl = adminPage.url();

        // Item 7: the rules profile is on the started page, not only the lobby.
        await expect(adminPage.locator('#battle-rules')).toHaveText('Tarmar');

        // Item 5: the delete asks in the app's own panel, never the browser's.
        const row = await battleRowOnList(adminPage, battleName);
        await expect(row).toContainText('Tarmar');
        await row.getByRole('button', { name: 'Delete' }).click();
        const question = adminPage.locator('.notice-confirm');
        await expect(question).toContainText('its log is kept');
        await question.locator('button:has-text("Delete")').click();

        await expect(adminPage.locator('tr', { hasText: battleName })).toHaveCount(0);

        // #461: the deleted battle's page says so on the warm panel ground its
        // rule always meant (it named a token nobody defined, so it had none).
        await adminPage.goto(battleUrl);
        const deletedNote = adminPage.locator('.battle-deleted-note');
        await expect(deletedNote).toContainText('This battle is deleted');
        await expect(deletedNote).toHaveCSS('background-color', 'rgb(250, 249, 246)');

        await adminPage.goto('/tarmar/battles/');
        await adminPage.click('a:has-text("Deleted")');
        const deletedRow = await battleRowOnList(adminPage, battleName, adminPage.url());
        await expect(deletedRow).toBeVisible();
        await deletedRow.getByRole('button', { name: 'Restore' }).click();

        // Restore lands on the battle, which no longer calls itself deleted.
        await expect(adminPage.locator('h1')).toHaveText(battleName);
        await expect(adminPage.locator('.battle-deleted-note')).toHaveCount(0);
        await expect(await battleRowOnList(adminPage, battleName)).toBeVisible();
    });
});

test.describe('The GM chat keeps what the player typed (#339)', () => {
    test('a session is renamed in place, and a failed turn is recoverable', async ({ page }) => {
        test.setTimeout(120000);
        await submitPassphrase(page);
        await loginAs(page, TEST_USER);

        // A real session, made by attaching a seeded character — the same way
        // gm-character.spec.js gets one without Ollama in the room.
        await page.goto('/tarmar/gm/');
        await chooseToAttach(page, 'Aldwin the Bold');
        await page.click('#gm-character-attach');
        await page.waitForURL(/\/tarmar\/gm\/\?session=\d+$/);

        // Item 10: the name is a field on the page, it saves on blur like
        // every other editable field in the studio, and the sidebar entry
        // follows it without the page being thrown away and reloaded.
        const field = page.locator('#session-name');
        await expect(field).toBeVisible();
        await expect(page.locator('.cmd-btn', { hasText: 'Rename' })).toBeVisible();
        await field.fill('The One-Handed Thief');
        await page.locator('#user-input').click();
        await expect(page.locator('[data-field="name"]')).toHaveClass(/saved/);
        await expect(page.locator('.session-item.active .session-name'))
            .toHaveText('The One-Handed Thief');
        await expect(page.locator('.gm-sheet')).toHaveCount(1);

        // An empty name is refused, and the field says so where it stands.
        await field.fill('   ');
        await page.locator('#user-input').click();
        await expect(page.locator('.autosave-error')).toContainText(
            refusal('gm', 'views', 'session_needs_name')
        );
        await expect(page.locator('.session-item.active .session-name'))
            .toHaveText('The One-Handed Thief');

        // Item 11: a turn the server cannot answer. The reason is a notice,
        // not a GM bubble; the message goes back in the box; and a non-JSON
        // body reads as a status rather than "Unexpected token <".
        await page.route('**/tarmar/gm/stream/', (route) => route.fulfill({
            status: 502,
            contentType: 'text/html',
            body: '<html><body>Bad Gateway</body></html>',
        }));
        await page.fill('#user-input', 'I ask the barkeep about the cellar');
        await page.click('#send-btn');

        const notice = page.locator('.notice-error');
        await expect(notice).toContainText('502');
        await expect(notice).not.toContainText('Unexpected token');
        await expect(page.locator('#user-input'))
            .toHaveValue('I ask the barkeep about the cellar');
        await expect(page.locator('.message.assistant')).toHaveCount(0);
        await expect(page.locator('.message.user')).toHaveCount(0);
        await expect(page.locator('#send-btn')).toBeEnabled();
        await page.unroute('**/tarmar/gm/stream/');

        // Item 9: the dice expression is asked for in the page, not through
        // the browser's prompt(). A native prompt would hang this click.
        await page.click('.cmd-btn:has-text("Roll 3d6")');
        const ask = page.locator('.notice-prompt');
        await expect(ask).toContainText('Dice expression');
        await expect(ask.locator('.notice-prompt-input')).toHaveValue('3d6');
        // The error notice is still standing beside it: nothing here is
        // modal and nothing expires on a timer.
        await expect(notice).toBeVisible();
        await ask.locator('button:has-text("Cancel")').click();
        await expect(ask).toHaveCount(0);
    });
});
