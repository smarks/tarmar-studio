/**
 * A Game Master's "HTH by agreement" ruling (#867), in a real browser.
 *
 * Spencer's ruling of 2026-10-09: "a Game Master marks a battle, or a pair,
 * as 'HTH by agreement'; players alone still need the engine's entry
 * condition." The fight's runner marks the whole battle or a pair; the page
 * renders the ruling from the battle's record and the log says who changed
 * it. Its words are read from the running settings (e2e/rulings.js), since
 * they are configuration Spencer edits.
 */

const { test, expect, createBattleViaSetup, TEST_ADMIN } = require('./fixtures');
const { rulingWords, hthWholeBattleLogLine } = require('./rulings');

test.describe('HTH by agreement', () => {
    test('a Game Master marks the whole battle and the log says so', async ({ adminPage }) => {
        const words = rulingWords().hth_agreement;
        await createBattleViaSetup(adminPage, {
            name: `E2E Agreement ${Date.now()}`,
            fighters: ['unarmoured duelist', 'unarmoured duelist'],
            seed: 42,
        });
        const panel = adminPage.locator('#battle-hth-agreement');
        await expect(panel.locator('.agreement-line')).toHaveText(words.none_marked);
        await panel.getByRole('button', { name: words.mark_whole_battle }).click();
        await expect(panel.locator('.agreement-line')).toHaveText(words.whole_battle_line);
        await expect(adminPage.locator('.battle-event').filter({
            hasText: hthWholeBattleLogLine({ agreed: true, username: TEST_ADMIN.username }),
        })).toHaveCount(1);

        await panel.getByRole('button', { name: words.lift_whole_battle }).click();
        await expect(panel.locator('.agreement-line')).toHaveText(words.none_marked);
        await expect(adminPage.locator('.battle-event').filter({
            hasText: hthWholeBattleLogLine({ agreed: false, username: TEST_ADMIN.username }),
        })).toHaveCount(1);
    });

    test('a Game Master marks a pair and lifts it', async ({ adminPage }) => {
        const words = rulingWords().hth_agreement;
        await createBattleViaSetup(adminPage, {
            name: `E2E AgreementPair ${Date.now()}`,
            fighters: ['unarmoured duelist', 'unarmoured duelist'],
            seed: 42,
        });
        const panel = adminPage.locator('#battle-hth-agreement');
        await panel.getByRole('button', { name: words.mark_pair }).click();
        await expect(panel.locator('.agreement-pair')).toHaveCount(1);
        await expect(panel.locator('.agreement-line')).not.toHaveText(words.none_marked);
        await panel.locator('.agreement-pair')
            .getByRole('button', { name: words.lift_pair, exact: true }).click();
        await expect(panel.locator('.agreement-pair')).toHaveCount(0);
        await expect(panel.locator('.agreement-line')).toHaveText(words.none_marked);
    });
});
