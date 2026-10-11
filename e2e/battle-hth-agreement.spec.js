/**
 * A Game Master's "HTH by agreement" ruling (#867), in a real browser.
 *
 * Spencer's ruling of 2026-10-09: "a Game Master marks a battle, or a pair,
 * as 'HTH by agreement'; players alone still need the engine's entry
 * condition." The fight's runner marks the whole battle or a pair; the page
 * renders the ruling from the battle's record and the log says who changed
 * it.
 */

const { test, expect, createBattleViaSetup } = require('./fixtures');

test.describe('HTH by agreement', () => {
    test('a Game Master marks the whole battle and the log says so', async ({ adminPage }) => {
        await createBattleViaSetup(adminPage, {
            name: `E2E Agreement ${Date.now()}`,
            fighters: ['unarmoured duelist', 'unarmoured duelist'],
            seed: 42,
        });
        const panel = adminPage.locator('#battle-hth-agreement');
        await expect(panel.locator('.agreement-line')).toContainText('No pair is marked');
        await panel.getByRole('button', { name: 'Mark the whole battle' }).click();
        await expect(panel.locator('.agreement-line'))
            .toContainText('every pair of enemies in this battle may enter hand-to-hand by agreement');
        await expect(adminPage.locator('.battle-event').filter({
            hasText: 'rules the whole battle hand-to-hand by agreement',
        })).toHaveCount(1);

        await panel.getByRole('button', { name: 'Lift the ruling for the whole battle' }).click();
        await expect(panel.locator('.agreement-line')).toContainText('No pair is marked');
        await expect(adminPage.locator('.battle-event').filter({
            hasText: 'lifts hand-to-hand by agreement for the whole battle',
        })).toHaveCount(1);
    });

    test('a Game Master marks a pair and lifts it', async ({ adminPage }) => {
        await createBattleViaSetup(adminPage, {
            name: `E2E AgreementPair ${Date.now()}`,
            fighters: ['unarmoured duelist', 'unarmoured duelist'],
            seed: 42,
        });
        const panel = adminPage.locator('#battle-hth-agreement');
        await panel.getByRole('button', { name: 'Mark this pair' }).click();
        await expect(panel.locator('.agreement-line')).toContainText('these pairs may enter');
        await expect(panel.locator('.agreement-pair')).toHaveCount(1);
        await panel.locator('.agreement-pair').getByRole('button', { name: 'Lift' }).click();
        await expect(panel.locator('.agreement-pair')).toHaveCount(0);
    });
});
