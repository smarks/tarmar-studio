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
 * Borrowing (tarmar-engine #28), in the second test: the same wizard, its
 * pool emptied before the turn, is short of mana for every spell. Under the
 * default canon the stage still opens, Fire Missile offers a borrowing
 * control listing exactly the copies the engine put on the menu (no plain
 * cast), and borrowing the mana it lacks logs the borrowed cast and its
 * debt roll against WIS. Under the Tarmar canon the spec pins only that one
 * borrowing copy is offered and that the debt roll is logged; why, and the
 * numbers, are the canon's.
 *
 * Every die is pinned. The battle's seed fixes the turn's rolls and the
 * opponent; the wizard is generated on the claim, so that one request names
 * its dice (X-E2E-Dice-Seed, e2e/dice.js). The seeds were chosen so the
 * casting roll succeeds and the Control Roll (or the debt roll) is made; the
 * log assertions are what prove the pin took.
 */

const {
    test, expect, TEST_USER, loginAs, submitPassphrase, createBattleViaSetup, battleRowOnList,
    loadedCanon, DEFAULT_CANON,
} = require('./fixtures');
const { startCoverage, stopCoverage } = require('./coverage');
const { pinDice } = require('./dice');
const { runDjangoShell } = require('./django');

/** The battle's seed: the opponent and every roll of the turn. */
const BATTLE_SEED = 11;
/** The claim's seed: the generated wizard's attributes, INT 17 among them. */
const WIZARD_SEED = 17;

/**
 * Empty the battle's wizard's mana pool, before the turn it decides in.
 *
 * @param {number} battleId
 */
function emptyTheWizardsPool(battleId) {
    runDjangoShell(`
from battle.models import Battle
from battle.persistence import dump_state, load_state
battle = Battle.objects.get(pk=${battleId})
state = load_state(battle.state_json, battle.rules_profile)
for figure in state.combatants:
    if figure.spells:
        figure.mana = 0
battle.state_json = dump_state(state)
battle.save(update_fields=["state_json"])
`);
}

/**
 * A battle with one generated fighter and the test user's generated wizard,
 * started, with the wizard's spell stage open on the first turn.
 *
 * @param {import('@playwright/test').Page} adminPage
 * @param {import('@playwright/test').Browser} browser
 * @param {string} battleName
 * @param {{beforeTurn?: (battleId: number) => void}} options - what to do to
 *     the started battle before its first turn is advanced
 * @returns {Promise<import('@playwright/test').Page>} the player's page
 */
async function openTheWizardsStage(adminPage, browser, battleName, { beforeTurn } = {}) {
    await createBattleViaSetup(adminPage, {
        name: battleName, fighters: 1, seed: BATTLE_SEED, playerSeats: 1,
    });
    await expect(adminPage.locator('#battle-status')).toHaveText('Lobby');
    const battleId = Number(new URL(adminPage.url()).pathname.match(/battles\/(\d+)\//)[1]);

    const playerContext = await browser.newContext();
    const page = await playerContext.newPage();
    await startCoverage(page);
    await submitPassphrase(page);
    await loginAs(page, TEST_USER);
    await pinDice(page, /\/seats\/\d+\/claim\/$/, WIZARD_SEED);
    const listRow = await battleRowOnList(page, battleName);
    await listRow.locator('a', { hasText: battleName }).first().click();
    await page.click('summary:has-text("Claim this seat")');
    await page.selectOption('select[name="archetype"]', 'wizard');
    await page.click('button:has-text("Join with a generated fighter")');
    await expect(page.locator('.lobby-seat.is-you')).toBeVisible();
    await page.click('button:has-text("I\'m ready")');
    await expect(page.locator('.lobby-ready-chip.is-ready')).toBeVisible();

    await expect(adminPage.locator('.lobby-ready-chip.is-ready'))
        .toBeVisible({ timeout: 15000 });
    await adminPage.click('.battle-start-form button[type="submit"]');
    await expect(page.locator('#battle-arena')).toBeVisible({ timeout: 15000 });
    if (beforeTurn) {
        beforeTurn(battleId);
        await adminPage.reload();
    }
    await adminPage.click('#advance-turn');
    await expect(adminPage.locator('#battle-status')).toHaveText('Awaiting players');

    const castOption = page.locator('#turn-panel .turn-option-cast');
    await expect(castOption).toBeVisible({ timeout: 15000 });
    await castOption.click();
    return page;
}

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

        playerPage = await openTheWizardsStage(adminPage, browser, battleName);
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
    test('a seated wizard short of mana borrows what it lacks, and the debt roll is logged', async ({ adminPage, browser }, testInfo) => {
        test.setTimeout(120000);
        await adminPage.goto('/tarmar/gm/');
        const defaultCanon = (await loadedCanon(adminPage)) === DEFAULT_CANON;
        playerPage = await openTheWizardsStage(
            adminPage, browser, `E2E Borrow ${Date.now()}`,
            { beforeTurn: emptyTheWizardsPool }
        );
        const stage = playerPage.locator('#turn-panel .turn-cast-stage');
        await expect(stage.locator('.turn-cast-pool')).toContainText('Mana 0 of');

        // Fire Missile has no plain cast with an empty pool: its row is
        // disabled with the server's reason, and its borrowing control lists
        // what the menu offers, nothing picked.
        const missileGroup = stage.locator('.turn-spell-group', {
            has: playerPage.locator('.turn-spell-name', { hasText: 'Fire Missile' }),
        });
        await expect(missileGroup.locator('.turn-spell')).toBeDisabled();
        await expect(missileGroup.locator('.turn-spell-note'))
            .toContainText('borrow the 1 it lacks');
        const copies = missileGroup.locator('.turn-borrow-amount');
        if (defaultCanon) {
            // The default canon: no Channel, a debt cap of four, a push cap
            // of three.
            await expect(copies).toHaveText([
                'Borrow 1 mana', '+1 mana, borrow 2', '+2 mana, borrow 3',
                '+3 mana, borrow 4',
            ]);
        } else {
            await expect(copies).toHaveText(['Borrow 1 mana']);
        }
        await expect(copies.first()).toHaveAttribute('aria-pressed', 'false');

        await copies.first().click();
        await expect(copies.first()).toHaveAttribute('aria-pressed', 'true');
        await expect(missileGroup.locator('.turn-borrow-cost'))
            .toContainText('borrows the 1 you lack as debt');
        await playerPage.click('#turn-panel .turn-confirm');

        // The turn ran: the borrowing cast, then its debt roll and how it
        // went, all in the log.
        await expect(playerPage.locator('#battle-turn')).toHaveText('1');
        await expect(playerPage.locator('.event-decision', {
            hasText: `chosen by ${TEST_USER.username}`,
        })).toContainText('Fire Missile (BORROW 1 MANA)');
        await expect(playerPage.locator('.event-action', { hasText: 'casts Fire Missile' }))
            .toContainText('borrows 1, debt 1');
        const debtRoll = playerPage.locator('.event-roll', { hasText: ' debt ' });
        await expect(debtRoll).toHaveCount(1);
        if (defaultCanon) {
            await expect(debtRoll).toContainText('vs WIS');
        }
        await expect(playerPage.locator('.battle-event', {
            hasText: /(holds|fails) the debt roll for Fire Missile \(1 owed\)/,
        })).toHaveCount(1);

        await stopCoverage(playerPage, `${testInfo.title} (player context)`);
    });
});
