/**
 * E2E test for the spectator flow (issue #250).
 *
 * What only three real browsers can prove: while a DM and a seated player
 * play a turn between them, a third signed-in user finds the battle on
 * their list under a Watch action, opens it read-only — no advance, no
 * rename, no start, a "Watching" chip naming their role — and their page
 * follows the whole turn purely by polling: the awaiting notice (with no
 * candidate menu and no advice), then the completed turn's events and the
 * turn counter, with the controls still absent at the end.
 */

const {
    test, expect, TEST_USER, TEST_WATCHER, loginAs, submitPassphrase,
    createBattleViaSetup, battleRowOnList,
} = require('./fixtures');
const { uniqueName } = require('./unique-name');
const { chosenBy } = require('./rulings');
const { startCoverage, stopCoverage } = require('./coverage');

test.describe('Battle spectators', () => {
    test('a third user watches a turn play out without controls', async ({ adminPage, browser }, testInfo) => {
        test.setTimeout(120000);
        const battleName = uniqueName('E2E Spectate');

        // The DM opens a battle: one AI fighter plus one player seat.
        await createBattleViaSetup(adminPage, {
            name: battleName, fighters: 1, seed: 42, playerSeats: 1,
        });
        await expect(adminPage.locator('#battle-status')).toHaveText('Lobby');

        // The player claims the seat and readies up in a second context.
        const playerContext = await browser.newContext();
        const playerPage = await playerContext.newPage();
        await startCoverage(playerPage);
        await submitPassphrase(playerPage);
        await loginAs(playerPage, TEST_USER);
        await playerPage.goto(adminPage.url());
        await playerPage.click('summary:has-text("Claim this seat")');
        await playerPage.selectOption('select[name="archetype"]', 'archer');
        await playerPage.click('button:has-text("Join with a generated fighter")');
        // Wait for the joined lobby before Ready, or the click can land on
        // the page the join is replacing (issue #493).
        await expect(playerPage.locator('.lobby-seat.is-you')).toBeVisible();
        await playerPage.click('button:has-text("I\'m ready")');
        await expect(
            playerPage.locator('.lobby-ready-chip.is-ready')
        ).toBeVisible();

        // The DM starts the battle.
        await expect(adminPage.locator('.lobby-ready-chip.is-ready'))
            .toBeVisible({ timeout: 15000 });
        await adminPage.click('.battle-start-form button[type="submit"]');
        await expect(adminPage.locator('#battle-status')).toHaveText('Setup');

        // A spectator arrives in a third context: the battle is on their
        // list under a Watch action, and the page opens with no controls.
        const watcherContext = await browser.newContext();
        const watcherPage = await watcherContext.newPage();
        await startCoverage(watcherPage);
        await submitPassphrase(watcherPage);
        await loginAs(watcherPage, TEST_WATCHER);
        const battleRow = await battleRowOnList(watcherPage, battleName);
        await expect(battleRow.locator('a:has-text("Watch")')).toBeVisible();
        await battleRow.locator('a:has-text("Watch")').click();

        await expect(watcherPage.locator('h1')).toHaveText(battleName);
        await expect(watcherPage.locator('.battle-chip-watching'))
            .toContainText('Watching');
        await expect(watcherPage.locator('#battle-arena .arena-combatant'))
            .toHaveCount(2);
        await expect(watcherPage.locator('#advance-turn')).toHaveCount(0);
        await expect(watcherPage.locator('#restart-battle')).toHaveCount(0);
        await expect(watcherPage.locator('.battle-start-form')).toHaveCount(0);
        await expect(watcherPage.locator('.combatant-name')).toHaveCount(0);

        // The DM advances into the player's turn. The spectator's page polls
        // its way to the awaiting notice — naming who the battle waits on,
        // offering neither the candidate menu nor the AI hand-off.
        await adminPage.click('#advance-turn');
        await expect(adminPage.locator('#battle-status'))
            .toHaveText('Awaiting players');
        await expect(watcherPage.locator('#turn-panel'))
            .toContainText('Waiting for', { timeout: 15000 });
        await expect(watcherPage.locator('#turn-panel .turn-option'))
            .toHaveCount(0);
        await expect(watcherPage.locator('#turn-panel .turn-ai-choose'))
            .toHaveCount(0);

        // The player takes their turn.
        const firstOption = playerPage.locator('#turn-panel .turn-option').first();
        await expect(firstOption).toBeVisible({ timeout: 15000 });
        await firstOption.click();
        await playerPage.click('#turn-panel .turn-confirm');
        await expect(playerPage.locator('#battle-turn')).toHaveText('1');

        // The spectator's page catches the completed turn purely by polling:
        // the turn counter ticks and the event log grows a turn group — and
        // the page still offers no controls.
        await expect(watcherPage.locator('#battle-turn'))
            .toHaveText('1', { timeout: 15000 });
        await expect(watcherPage.locator('.turn-group[data-turn="1"]'))
            .toBeVisible();
        await expect(watcherPage.locator('.event-decision', {
            hasText: chosenBy(TEST_USER.username),
        })).toBeVisible();
        await expect(watcherPage.locator('#advance-turn')).toHaveCount(0);
        await expect(watcherPage.locator('#turn-panel .turn-option'))
            .toHaveCount(0);

        await stopCoverage(playerPage, `${testInfo.title} (player context)`);
        await stopCoverage(watcherPage, `${testInfo.title} (watcher context)`);
        await playerContext.close();
        await watcherContext.close();
    });
});
