/**
 * The battle page opens on the log's last few turns (#662).
 *
 * `arena_context` fetched every BattleEvent row for the battle and the
 * template rendered all of them, uncapped, for every viewer on every load —
 * spectators included, for whom the docstring calls the replay free. A plain
 * six-fighter battle run to its end wrote 1,834 events over 83 turns, and a
 * battle may hold twelve combatants.
 *
 * What only a browser proves: that the reader is told how much is being
 * held back, that following the offer actually shows it, and that the
 * choice is state they carry — a reload lands on the same log, and the way
 * back is there too. Nothing here is a toggle that forgets itself.
 */

const { test, expect, createBattleViaSetup } = require('./fixtures');

// One more than the server's window, so a battle that reaches it has
// something to hold back.
const TURNS_TO_PLAY = 14;

test.describe('The battle event log', () => {
    test('opens on its last turns and offers the earlier ones by URL',
        async ({ adminPage }) => {
            test.setTimeout(180000);
            await createBattleViaSetup(adminPage, {
                name: `E2E Log window ${Date.now()}`,
                fighters: 2,
                seed: 4242,
                controlMode: 'automatic',
            });
            const battleUrl = adminPage.url();

            const advance = adminPage.locator('#advance-turn');
            for (let turn = 1; turn <= TURNS_TO_PLAY; turn += 1) {
                if (await advance.isDisabled()) { break; } // the fight ended
                await advance.click();
                await expect(adminPage.locator(`.turn-group[data-turn="${turn}"]`))
                    .toBeVisible();
            }
            const playedTurns = Number(
                await adminPage.locator('#battle-turn').textContent()
            );
            test.skip(
                playedTurns <= 10,
                'this seed ended the fight inside the window'
            );

            // Everything played so far is on the page — it was appended as
            // it happened. A reload is where the window applies.
            await adminPage.goto(battleUrl);
            const window = adminPage.locator('.battle-log-window');
            await expect(window).toContainText('Showing the last 10 turns');
            await expect(window).toContainText('earlier turn');
            await expect(adminPage.locator('.turn-group')).toHaveCount(10);
            await expect(adminPage.locator('.turn-group[data-turn="1"]'))
                .toHaveCount(0);

            // Following the offer shows the whole log.
            await window.locator('a').click();
            await adminPage.waitForURL(/\?log=all$/);
            await expect(adminPage.locator('.turn-group[data-turn="1"]'))
                .toBeVisible();
            await expect(adminPage.locator('.turn-group'))
                .toHaveCount(playedTurns);
            await expect(adminPage.locator('.battle-log-window'))
                .toContainText('Showing every turn');

            // And it is the reader's state, not a flash: reloading the URL
            // shows the same log, and the way back is offered.
            await adminPage.reload();
            await expect(adminPage.locator('.turn-group[data-turn="1"]'))
                .toBeVisible();
            await adminPage.locator('.battle-log-window a').click();
            await expect(adminPage.locator('.turn-group')).toHaveCount(10);
            await expect(adminPage.locator('.turn-group[data-turn="1"]'))
                .toHaveCount(0);
        });
});
