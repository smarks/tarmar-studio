/**
 * E2E test for the battle lobby and remote play (issue #250).
 *
 * What only two real browsers can prove: a DM opens a battle with a player
 * seat, a second browser context claims the seat and readies up, the DM's
 * polling lobby notices, the battle starts, and when the DM advances into
 * the player's turn the player's page offers the action menu, the choice
 * completes the turn on BOTH pages (the DM side purely by polling), and the
 * decision is logged crediting the player. Then a reload reproduces it all
 * from the database — no state lives only in the browser.
 */

const {
    test, expect, TEST_USER, TEST_WATCHER, loginAs, submitPassphrase, createBattleViaSetup, battleRowOnList,
} = require('./fixtures');
const { startCoverage, stopCoverage } = require('./coverage');
const { menuWords, sentencePattern } = require('./turn-menu');

test.describe('Battle lobby and remote play', () => {
    test('DM opens a lobby; a player claims, readies, and plays their turn', async ({ adminPage, browser }, testInfo) => {
        test.setTimeout(120000);
        const battleName = `E2E Lobby ${Date.now()}`;

        // The DM opens a battle: one generated AI fighter plus one player seat.
        await createBattleViaSetup(adminPage, {
            name: battleName, fighters: 1, seed: 42, playerSeats: 1,
        });

        // Landed in the lobby: one filled card, one open seat, Start visible.
        await expect(adminPage.locator('h1')).toHaveText(battleName);
        await expect(adminPage.locator('#battle-status')).toHaveText('Lobby');
        await expect(adminPage.locator('.lobby-seat.is-open')).toHaveCount(1);
        const battleUrl = adminPage.url();

        // A player arrives in a second, fully separate browser context.
        const playerContext = await browser.newContext();
        const playerPage = await playerContext.newPage();
        await startCoverage(playerPage);
        await submitPassphrase(playerPage);
        await loginAs(playerPage, TEST_USER);

        // The open lobby is on their battle list; they claim the seat with a
        // generated archer and mark themselves ready.
        const listRow = await battleRowOnList(playerPage, battleName);
        await listRow.locator('a', { hasText: battleName }).first().click();
        await expect(playerPage.locator('.lobby-lede')).toContainText('Claim an open seat');
        await playerPage.click('summary:has-text("Claim this seat")');
        await playerPage.selectOption('select[name="archetype"]', 'archer');
        await playerPage.click('button:has-text("Join with a generated fighter")');
        await expect(playerPage.locator('.lobby-seat.is-you')).toBeVisible();

        // With the only seat claimed, anyone else is told so instead of being
        // invited to claim a seat that is gone (issue #339, item 25).
        const watcherContext = await browser.newContext();
        const watcherPage = await watcherContext.newPage();
        await submitPassphrase(watcherPage);
        await loginAs(watcherPage, TEST_WATCHER);
        await watcherPage.goto(battleUrl);
        await expect(watcherPage.locator('.lobby-lede')).toContainText('Every seat is taken');
        await expect(watcherPage.locator('.lobby-lede')).not.toContainText('Claim an open seat');
        await watcherContext.close();

        await playerPage.click('button:has-text("I\'m ready")');
        await expect(
            playerPage.locator('.lobby-ready-chip.is-ready')
        ).toBeVisible();

        // The DM's lobby is polling: the claim and the ready chip arrive
        // without the DM touching anything. Then the DM starts the battle.
        await expect(adminPage.locator('.lobby-ready-chip.is-ready'))
            .toBeVisible({ timeout: 15000 });
        await adminPage.click('.battle-start-form button[type="submit"]');
        await expect(adminPage.locator('#battle-status')).toHaveText('Setup');
        await expect(adminPage.locator('#battle-arena .arena-combatant'))
            .toHaveCount(2);

        // The player's lobby page notices the start by polling and lands on
        // the battle view, their combatant tagged as theirs.
        await expect(playerPage.locator('#battle-arena'))
            .toBeVisible({ timeout: 15000 });
        await expect(playerPage.locator('.combatant-card', { hasText: 'Played by you' }))
            .toBeVisible();

        // The DM advances into the player's turn: the battle parks awaiting
        // the player, and the DM sees who it waits on.
        await adminPage.click('#advance-turn');
        await expect(adminPage.locator('#battle-status'))
            .toHaveText('Awaiting players');
        await expect(adminPage.locator('#turn-panel')).toContainText(
            `Waiting for`, { timeout: 15000 }
        );
        await expect(adminPage.locator('#advance-turn')).toBeDisabled();

        // The player's page polls its way into the action menu: the engine's
        // legal options, each read as a plain line with its cost, and the
        // policy's advice one switch away. This asserted "score" on the
        // option itself, which pinned #788: the AI's scoring sheet was the
        // first thing a player read. They pick the first option and confirm.
        const firstOption = playerPage.locator('#turn-panel .turn-option').first();
        await expect(firstOption).toBeVisible({ timeout: 15000 });
        await expect(firstOption.locator('.turn-option-plain')).not.toBeEmpty();
        // The configured words: what it costs, or that it costs nothing.
        const { panel } = menuWords();
        await expect(firstOption.locator('.turn-option-cost')).toHaveText(new RegExp(
            `^(?:${sentencePattern(panel.costs)}|${sentencePattern(panel.costs_nothing)})$`
        ));
        await playerPage.click('#turn-panel .turn-scoring-toggle');
        await expect(firstOption).toContainText('score');
        await firstOption.click();
        await expect(firstOption).toHaveAttribute('aria-pressed', 'true');
        await playerPage.click('#turn-panel .turn-confirm');

        // The turn completes on the player's page, the decision credited to
        // them with the alternatives preserved.
        await expect(playerPage.locator('#battle-turn')).toHaveText('1');
        const decision = playerPage.locator('.event-decision', {
            hasText: `chosen by ${TEST_USER.username}`,
        });
        await expect(decision).toBeVisible();
        await decision.locator('summary').click();
        await expect(decision.locator('ul li').first()).toContainText('score');

        // The DM's page catches the completed turn purely by polling, and
        // Advance is theirs again.
        await expect(adminPage.locator('#battle-turn'))
            .toHaveText('1', { timeout: 15000 });
        await expect(adminPage.locator('#advance-turn')).toBeEnabled();

        // A full reload reproduces the played turn from the database.
        await playerPage.reload();
        await expect(playerPage.locator('.turn-group[data-turn="1"]')).toBeVisible();
        await expect(
            playerPage.locator('.event-decision', {
                hasText: `chosen by ${TEST_USER.username}`,
            })
        ).toBeVisible();

        await stopCoverage(playerPage, `${testInfo.title} (player context)`);
        await playerContext.close();
    });
});
