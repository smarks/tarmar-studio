/**
 * A Game Master lifts a figure's magic bounds (#37), in a real browser.
 *
 * Spencer's ruling of 2026-10-14: the Game Master records "Channel lifted for
 * <figure>", and likewise the push cap and the counter's commitment bounds,
 * as a ruling on the battle, with who and when. The page renders the ruling
 * from the battle's record and the log says who changed it. The bounds offered
 * are the loaded canon's, and the words are configuration, so both are read
 * from the running server (e2e/rulings.js), never typed here.
 */

const {
    test, expect, createBattleViaSetup, TEST_ADMIN, loadedCanon, DEFAULT_CANON,
} = require('./fixtures');
const { uniqueName } = require('./unique-name');
const { stopCoverage } = require('./coverage');
const { runDjangoShell } = require('./django');
const {
    rulingWords, magicBoundsLogLine, offeredBounds, pastByRuling,
} = require('./rulings');
const { PUSH_TURNS_SEED, pinTheTurns, openTheWizardsStage } = require('./wizard-stage');

/** Mana enough for the wizard to push past any cap the canon sets. */
const FULL_POOL = 12;

/**
 * Give the battle's wizard a full pool of FULL_POOL mana, before its turn.
 *
 * @param {number} battleId
 */
function fillTheWizardsPool(battleId) {
    runDjangoShell(`
from battle.models import Battle
from battle.persistence import dump_state, load_state
battle = Battle.objects.get(pk=${battleId})
state = load_state(battle.state_json, battle.rules_profile)
for figure in state.combatants:
    if figure.spells:
        figure.mana = figure.max_mana = ${FULL_POOL}
battle.state_json = dump_state(state)
battle.save(update_fields=["state_json"])
`);
}

test.describe('Magic bounds lifted', () => {
    test('a Game Master lifts two bounds for a figure, then holds one again', async ({ adminPage }) => {
        const words = rulingWords().magic_bounds;
        const username = TEST_ADMIN.username;
        await createBattleViaSetup(adminPage, {
            name: uniqueName('E2E MagicBounds'),
            fighters: ['unarmoured duelist', 'unarmoured duelist'],
            seed: 42,
        });
        const panel = adminPage.locator('#battle-magic-bounds');
        await expect(panel.locator('.magic-bounds-line')).toBeVisible();
        await expect(panel.locator('.magic-bounds-lifting')).toHaveCount(0);
        const figure = (await panel.locator('select option').first().textContent()).trim();
        // The page offers only the bounds the loaded canon imposes; lift the
        // first two of them, whichever they are.
        const choices = panel.locator('.magic-bounds-choice');
        const bounds = [
            await choices.nth(0).locator('input').getAttribute('value'),
            await choices.nth(1).locator('input').getAttribute('value'),
        ];
        const labels = [
            (await choices.nth(0).textContent()).trim(),
            (await choices.nth(1).textContent()).trim(),
        ];
        await choices.nth(0).locator('input').check();
        await choices.nth(1).locator('input').check();
        await panel.getByRole('button', { name: words.lift_button }).click();

        const liftings = panel.locator('.magic-bounds-lifting');
        await expect(liftings).toHaveCount(2);
        await expect(liftings.nth(0)).toContainText(labels[0]);
        await expect(liftings.nth(0)).toContainText(figure);
        await expect(liftings.nth(1)).toContainText(labels[1]);
        await expect(adminPage.locator('.battle-event').filter({
            hasText: magicBoundsLogLine({ bounds, name: figure, lifted: true, username }),
        })).toHaveCount(1);

        await liftings.nth(0).getByRole('button', { name: words.hold_again }).click();
        await expect(panel.locator('.magic-bounds-lifting')).toHaveCount(1);
        await expect(panel.locator('.magic-bounds-lifting')).toContainText(labels[1]);
        await expect(adminPage.locator('.battle-event').filter({
            hasText: magicBoundsLogLine({
                bounds: [bounds[0]], name: figure, lifted: false, username,
            }),
        })).toHaveCount(1);
    });

    test("a Game Master lifts the canon's first bound for a wizard, whose menu then offers the cast past it", async ({ adminPage, browser }, testInfo) => {
        // The bound is the loaded canon's to impose: under the Tarmar canon
        // Channel (the wizard's Fire Missile level, which leaves no room to
        // push), under the default canon Push's own cap of three. Either way
        // the wizard's Fire Missile is offered pushed copies past it once
        // the Game Master lifts it, and the log says the cast went past it by
        // the ruling.
        await adminPage.goto('/tarmar/gm/');
        const defaultCanon = (await loadedCanon(adminPage)) === DEFAULT_CANON;
        const [bound] = offeredBounds();
        const words = rulingWords().magic_bounds;
        const past = pastByRuling([bound]);

        const playerPage = await openTheWizardsStage(
            adminPage, browser, uniqueName('E2E LiftedBound'),
            {
                beforeTurn: async (battleId, wizardName) => {
                    fillTheWizardsPool(battleId);
                    if (defaultCanon) { pinTheTurns(battleId, PUSH_TURNS_SEED); }
                    await adminPage.reload();
                    const panel = adminPage.locator('#battle-magic-bounds');
                    await panel.locator('select').selectOption({ label: wizardName });
                    await panel.locator(`input[value="${bound}"]`).check();
                    await panel.getByRole('button', { name: words.lift_button }).click();
                    await expect(panel.locator('.magic-bounds-lifting')).toHaveCount(1);
                },
            }
        );
        try {
            const stage = playerPage.locator('#turn-panel .turn-cast-stage');
            const missileGroup = stage.locator('.turn-spell-group', {
                has: playerPage.locator('.turn-spell-name', { hasText: 'Fire Missile' }),
            });
            await expect(missileGroup.locator('.turn-push')).toBeVisible();
            await missileGroup.locator('.turn-spell').click();
            const amounts = missileGroup.locator('.turn-push-amount');
            // "None", then the amounts: under the default canon more than its
            // cap of three, under the Tarmar canon any at all.
            await expect.poll(() => amounts.count()).toBeGreaterThan(defaultCanon ? 4 : 1);
            await amounts.last().click();
            await expect(missileGroup.locator('.turn-push-cost')).toContainText(past.menu);
            await playerPage.click('#turn-panel .turn-confirm');

            await expect(playerPage.locator('#battle-turn')).toHaveText('1');
            await expect(playerPage.locator('.event-past-by-ruling')).toHaveText([past.log]);
            await stopCoverage(playerPage, `${testInfo.title} (player context)`);
        } finally {
            await playerPage.context().close();
        }
    });
});
