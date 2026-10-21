/**
 * The seated wizard's spell stage, set up for a spec (issues #960, #37).
 *
 * A battle with one generated fighter and the test user's generated wizard,
 * every die pinned, started and advanced to the wizard's first choice: what
 * e2e/battle-push.spec.js and e2e/battle-magic-bounds.spec.js both stand on.
 * The seeds were chosen by running the turn on the started battle for each
 * (see e2e/battle-push.spec.js).
 */

const {
    expect, TEST_USER, loginAs, submitPassphrase, createBattleViaSetup, battleRowOnList,
} = require('./fixtures');
const { startCoverage } = require('./coverage');
const { pinDice } = require('./dice');
const { runDjangoShell } = require('./django');

/** The battle's seed: the opponent and every roll of the turn. */
const BATTLE_SEED = 11;
/**
 * The turns' seed for the push branch, set after the battle starts: turn 1's
 * pushed casting roll succeeds under the default canon, so its Control Roll
 * is made.
 */
const PUSH_TURNS_SEED = 1;
/** The claim's seed: the generated wizard's attributes, INT 17 among them. */
const WIZARD_SEED = 17;
/**
 * Set the started battle's seed, which every later turn's dice follow; the
 * figures are already made.
 *
 * @param {number} battleId
 * @param {number} seed
 */
function pinTheTurns(battleId, seed) {
    runDjangoShell(`
from battle.models import Battle
Battle.objects.filter(pk=${battleId}).update(seed=${seed})
`);
}

/**
 * Claim the lobby's first open seat with a generated wizard, its attributes
 * from ``seed``, and ready it.
 *
 * @param {import('@playwright/test').Page} page - the claimant's page, on the
 *     battle
 * @param {number} seed - the claim's dice
 * @returns {Promise<string>} the seated wizard's name
 */
async function claimAWizard(page, seed) {
    await pinDice(page, /\/seats\/\d+\/claim\/$/, seed);
    const open = page.locator('.lobby-seat.is-open').first();
    await open.locator('summary:has-text("Claim this seat")').click();
    await open.locator('select[name="archetype"]').selectOption('wizard');
    await open.locator('button:has-text("Join with a generated fighter")').click();
    const mine = page.locator('.lobby-seat.is-you');
    await expect(mine).toBeVisible();
    await mine.locator('button:has-text("I\'m ready")').click();
    await expect(mine.locator('.lobby-ready-chip.is-ready')).toBeVisible();
    return (await mine.locator('.lobby-seat-name').textContent()).trim();
}

/**
 * A battle with one generated fighter and the test user's generated wizard,
 * started, with the wizard's spell stage open on the first turn.
 *
 * @param {import('@playwright/test').Page} adminPage
 * @param {import('@playwright/test').Browser} browser
 * @param {string} battleName
 * @param {{
 *     beforeTurn?: (battleId: number, wizardName: string) => (void|Promise<void>),
 *     fighters?: number,
 * }} options - what to do to the started battle before its first turn is
 *     advanced, given the battle and the seated wizard's name; and how many
 *     generated fighters oppose the wizard (one unless said)
 * @returns {Promise<import('@playwright/test').Page>} the player's page
 */
async function openTheWizardsStage(
    adminPage, browser, battleName, { beforeTurn, fighters = 1 } = {}
) {
    await createBattleViaSetup(adminPage, {
        name: battleName, fighters, seed: BATTLE_SEED, playerSeats: 1,
    });
    await expect(adminPage.locator('#battle-status')).toHaveText('Lobby');
    const battleId = Number(new URL(adminPage.url()).pathname.match(/battles\/(\d+)\//)[1]);

    const playerContext = await browser.newContext();
    const page = await playerContext.newPage();
    await startCoverage(page);
    await submitPassphrase(page);
    await loginAs(page, TEST_USER);
    const listRow = await battleRowOnList(page, battleName);
    await listRow.locator('a', { hasText: battleName }).first().click();
    const wizardName = await claimAWizard(page, WIZARD_SEED);

    await expect(adminPage.locator('.lobby-ready-chip.is-ready'))
        .toBeVisible({ timeout: 15000 });
    await adminPage.click('.battle-start-form button[type="submit"]');
    await expect(page.locator('#battle-arena')).toBeVisible({ timeout: 15000 });
    if (beforeTurn) {
        await beforeTurn(battleId, wizardName);
        await adminPage.reload();
    }
    await adminPage.click('#advance-turn');
    await expect(adminPage.locator('#battle-status')).toHaveText('Awaiting players');

    const castOption = page.locator('#turn-panel .turn-option-cast');
    await expect(castOption).toBeVisible({ timeout: 15000 });
    await castOption.click();
    return page;
}


module.exports = {
    BATTLE_SEED, PUSH_TURNS_SEED, WIZARD_SEED, pinTheTurns, claimAWizard, openTheWizardsStage,
};
