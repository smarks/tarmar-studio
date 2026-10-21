/**
 * E2E test for the combatant attribute panel (issue #302).
 *
 * What only a real browser can prove: the panel opens on a click and is
 * still open a turn later, when the roster has been repainted from the
 * server underneath it. That is the whole difference between this and the
 * transient popup the house rule forbids — a unit test can assert the open
 * set, but only a browser can play the turn that would have closed it.
 *
 * Also here: selecting a figure on the map opens the same panel rather than
 * floating a second one over the arena, closing it stays closed, and a
 * spectator's page carries no panel to open at all (#273's rule, at the one
 * new place a sheet could otherwise be read out).
 */

const {
    test, expect, TEST_WATCHER, loginAs, submitPassphrase,
    createBattleViaSetup,
} = require('./fixtures');
const { uniqueName } = require('./unique-name');
const { startCoverage, stopCoverage } = require('./coverage');

test.describe('Combatant attributes on the battle page', () => {
    test('a DM opens a panel, plays a turn, and it is still open', async ({ adminPage }) => {
        const battleName = uniqueName('E2E Attributes');
        await createBattleViaSetup(adminPage, {
            name: battleName, fighters: 2, seed: 42,
        });

        const card = adminPage.locator('.combatant-card').first();
        const panel = card.locator('.combatant-detail');

        // Closed on arrival: the card still reads as condition at a glance.
        await expect(panel.locator('.detail-group h4').first()).toBeHidden();

        await panel.locator('summary').click();

        // Grouped, not a wall of numbers.
        await expect(panel.locator('.detail-group h4')).toHaveText([
            'Attributes', 'Attack', 'Defence', 'Movement',
        ]);
        await expect(panel).toContainText('STR');
        await expect(panel).toContainText('Weapon');
        await expect(panel).toContainText('Stops');
        await expect(panel).toContainText('Walk');
        await expect(panel).toContainText('armour penalty included');

        // A turn repaints the roster from the server. The panel stays open —
        // it closes when the reader closes it, and at no other time.
        await adminPage.click('#advance-turn');
        await expect(adminPage.locator('.turn-group[data-turn="1"]')).toBeVisible();
        await expect(
            adminPage.locator('.combatant-card').first().locator('.detail-group h4').first()
        ).toBeVisible();

        // And it closes on the reader's say-so, staying closed.
        await adminPage.locator('.combatant-card').first()
            .locator('.combatant-detail summary').click();
        await expect(
            adminPage.locator('.combatant-card').first().locator('.detail-group h4').first()
        ).toBeHidden();
        await adminPage.reload();
        await expect(
            adminPage.locator('.combatant-card').first().locator('.detail-group h4').first()
        ).toBeHidden();
    });

    test('selecting a figure on the map opens that combatant panel', async ({ adminPage }) => {
        const battleName = uniqueName('E2E Attributes Map');
        await createBattleViaSetup(adminPage, {
            name: battleName, fighters: 2, seed: 7,
        });

        const marker = adminPage.locator('#battle-arena .arena-combatant').first();
        const combatantId = await marker.getAttribute('data-combatant');
        const card = adminPage.locator(
            `.combatant-card[data-combatant="${combatantId}"]`
        );
        await expect(card.locator('.detail-group h4').first()).toBeHidden();

        await marker.click();

        await expect(card.locator('.detail-group h4').first()).toBeVisible();
        // One panel, in the card — nothing floating over the arena.
        await expect(adminPage.locator('#battle-arena .combatant-detail'))
            .toHaveCount(0);
    });

    test('a spectator has no panel to open', async ({ adminPage, browser }, testInfo) => {
        const battleName = uniqueName('E2E Attributes Watched');
        await createBattleViaSetup(adminPage, {
            name: battleName, fighters: 2, seed: 11,
        });
        const battleUrl = adminPage.url();

        const watcherContext = await browser.newContext();
        const watcherPage = await watcherContext.newPage();
        await startCoverage(watcherPage);
        await submitPassphrase(watcherPage);
        await loginAs(watcherPage, TEST_WATCHER);
        await watcherPage.goto(battleUrl);

        await expect(watcherPage.locator('.combatant-card')).toHaveCount(2);
        await expect(watcherPage.locator('.combatant-detail')).toHaveCount(0);
        // Nor is any figure offered as something to click.
        await expect(
            watcherPage.locator('#battle-arena .arena-combatant.is-selectable')
        ).toHaveCount(0);

        await stopCoverage(watcherPage, `${testInfo.title} (watcher)`);
        await watcherContext.close();
    });
});
