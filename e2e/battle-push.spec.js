/**
 * E2E test for pushing mana into a cast (issue #960).
 *
 * The default canon's magic rules (the public configuration) offer Push for a
 * damage spell, rolled against INT with a cap of three pushed mana and no
 * Channel. What only a real browser proves: the seated wizard's spell stage
 * offers the push as a control beside the cast, listing exactly the amounts
 * the engine put on the menu with none picked; picking one and taking the
 * action sends it; and the turn's log carries the pushed cast, its Control
 * Roll and that roll's outcome.
 *
 * Under the Tarmar canon the same generated wizard is offered no push: the
 * spec drives the same claim and turn and pins that the stage lists Fire
 * Missile with no push control, then ends. The spec says only that no push
 * is offered there, not why; the reasons are the canon's.
 *
 * Every die is pinned. The battle's seed fixes the turn's rolls and the
 * opponent; the wizard is generated on the claim, so that one request names
 * its dice (X-E2E-Dice-Seed, e2e/dice.js). The seeds were chosen so the
 * casting roll succeeds and the Control Roll is made; the log assertions are
 * what prove the pin took.
 */

const {
    test, expect, TEST_USER, loginAs, submitPassphrase, createBattleViaSetup, battleRowOnList,
    loadedCanon, DEFAULT_CANON,
} = require('./fixtures');
const { startCoverage, stopCoverage } = require('./coverage');
const { pinDice } = require('./dice');

/** The battle's seed: the opponent and every roll of the turn. */
const BATTLE_SEED = 11;
/** The claim's seed: the generated wizard's attributes, INT 17 among them. */
const WIZARD_SEED = 17;

test.describe('Pushing mana into a cast', () => {
    /** The player's page, kept so a failure can show the player's panel. */
    let playerPage = null;

    test.afterEach(async ({}, testInfo) => {
        if (!playerPage) { return; }
        if (testInfo.status !== testInfo.expectedStatus && !playerPage.isClosed()) {
            await testInfo.attach('player page', {
                body: await playerPage.content(), contentType: 'text/html',
            });
            await testInfo.attach('player screenshot', {
                body: await playerPage.screenshot({ fullPage: true }),
                contentType: 'image/png',
            });
        }
        await playerPage.context().close();
        playerPage = null;
    });

    test('a seated wizard is offered the push its canon allows, and a pushed cast is logged', async ({ adminPage, browser }, testInfo) => {
        test.setTimeout(120000);
        // The push amounts below are the default canon's; under any other
        // canon the spec pins that this wizard is offered no push.
        await adminPage.goto('/tarmar/gm/');
        const pushOffered = (await loadedCanon(adminPage)) === DEFAULT_CANON;
        const battleName = `E2E Push ${Date.now()}`;

        await createBattleViaSetup(adminPage, {
            name: battleName, fighters: 1, seed: BATTLE_SEED, playerSeats: 1,
        });
        await expect(adminPage.locator('#battle-status')).toHaveText('Lobby');

        const playerContext = await browser.newContext();
        playerPage = await playerContext.newPage();
        await startCoverage(playerPage);
        await submitPassphrase(playerPage);
        await loginAs(playerPage, TEST_USER);
        await pinDice(playerPage, /\/seats\/\d+\/claim\/$/, WIZARD_SEED);
        const listRow = await battleRowOnList(playerPage, battleName);
        await listRow.locator('a', { hasText: battleName }).first().click();
        await playerPage.click('summary:has-text("Claim this seat")');
        await playerPage.selectOption('select[name="archetype"]', 'wizard');
        await playerPage.click('button:has-text("Join with a generated fighter")');
        await expect(playerPage.locator('.lobby-seat.is-you')).toBeVisible();
        await playerPage.click('button:has-text("I\'m ready")');
        await expect(playerPage.locator('.lobby-ready-chip.is-ready')).toBeVisible();

        await expect(adminPage.locator('.lobby-ready-chip.is-ready'))
            .toBeVisible({ timeout: 15000 });
        await adminPage.click('.battle-start-form button[type="submit"]');
        await expect(playerPage.locator('#battle-arena'))
            .toBeVisible({ timeout: 15000 });
        await adminPage.click('#advance-turn');
        await expect(adminPage.locator('#battle-status'))
            .toHaveText('Awaiting players');

        const castOption = playerPage.locator('#turn-panel .turn-option-cast');
        await expect(castOption).toBeVisible({ timeout: 15000 });
        await castOption.click();
        const stage = playerPage.locator('#turn-panel .turn-cast-stage');

        if (!pushOffered) {
            // Fire Missile is on the stage, castable, with no push control
            // and not a word about pushing.
            const missile = stage.locator('.turn-spell', {
                has: playerPage.locator('.turn-spell-name', { hasText: 'Fire Missile' }),
            });
            await expect(missile).toBeEnabled();
            await expect(stage.locator('.turn-push')).toHaveCount(0);
            await expect(stage.locator('.turn-push-amount')).toHaveCount(0);
            await expect(stage).not.toContainText(/push/i);
            await stopCoverage(playerPage, `${testInfo.title} (player context)`);
            return;
        }

        // The spell stage: Fire Missile carries the push control, with
        // nothing pushed until the player picks an amount.
        const missileGroup = stage.locator('.turn-spell-group', {
            has: playerPage.locator('.turn-spell-name', { hasText: 'Fire Missile' }),
        });
        // A cast the menu offers pushed copies of carries the push control;
        // asserted, so a menu without them fails here and says so.
        await expect(missileGroup.locator('.turn-push')).toBeVisible();
        await missileGroup.locator('.turn-spell').click();
        const amounts = missileGroup.locator('.turn-push-amount');
        // The default canon caps a push at three mana, and the wizard has
        // the mana for all three.
        await expect(amounts).toHaveText(['None', '+1 mana', '+2 mana', '+3 mana']);
        await expect(amounts.first()).toHaveAttribute('aria-pressed', 'true');

        await amounts.nth(2).click();
        await expect(amounts.nth(2)).toHaveAttribute('aria-pressed', 'true');
        await expect(missileGroup.locator('.turn-push-cost'))
            .toContainText('3 mana (1 and 2 pushed)');
        await playerPage.click('#turn-panel .turn-confirm');

        // The turn ran: the player's pushed cast, its Control Roll against
        // INT, and the roll's outcome, all in the log.
        await expect(playerPage.locator('#battle-turn')).toHaveText('1');
        await expect(playerPage.locator('.event-decision', {
            hasText: `chosen by ${TEST_USER.username}`,
        })).toContainText('Fire Missile (PUSH 2 MANA)');
        const controlRoll = playerPage.locator('.event-roll', { hasText: 'control 3d6' });
        await expect(controlRoll).toHaveCount(1);
        await expect(controlRoll).toContainText('vs INT');
        await expect(playerPage.locator('.battle-event', {
            hasText: /pushed Fire Missile|Fire Missile runs away/,
        })).toHaveCount(1);

        await stopCoverage(playerPage, `${testInfo.title} (player context)`);
    });
});
