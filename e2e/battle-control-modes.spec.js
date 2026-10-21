/**
 * Control as an explicit mode (#308), in a real browser.
 *
 * Three things only a browser proves, and each is one of the issue's
 * acceptance lines:
 *
 * 1. **A fully automatic battle plays with nothing but Advance clicks** —
 *    no prompt ever appears, the button never goes dark waiting on anyone.
 * 2. **A mixed battle stops, and says who it stopped for** — the button
 *    itself reads "Waiting on <name>'s move" and is disabled, the awaited
 *    figure's menu is offered, and choosing lets the turn commit with the
 *    AI combatants' decisions in the same log.
 * 3. **The readout is state, not a flash** — a full reload says exactly the
 *    same thing, because the label is rendered from the stored pending
 *    state rather than pushed at the moment the pause happens.
 *
 * The control-change lines in the log are checked here too: a figure that
 * starts behaving differently mid-fight has to be explained somewhere the
 * reader can see.
 */

const { test, expect, createBattleViaSetup } = require('./fixtures');
const { uniqueName } = require('./unique-name');
const { menuWords, sentencePattern } = require('./turn-menu');

/**
 * Open the per-combatant disclosure so its rows can be driven.
 *
 * The rows sit in a closed <details> below the arena since #695: eight
 * combatants meant eight copies of one control above the hex board, which put
 * the board below the fold on a 1440x900 screen and two and a half screens
 * down on a phone.
 *
 * @param {import('@playwright/test').Page} page
 */
async function openWhoDecides(page) {
    const disclosure = page.locator('#battle-control .control-combatants-disclosure');
    await expect(disclosure).toHaveCount(1);
    if (!await disclosure.evaluate((node) => node.open)) {
        await disclosure.locator('summary').click();
    }
    await expect(disclosure).toHaveJSProperty('open', true);
}

test.describe('Battle control modes', () => {
    test('an automatic battle plays with nothing but Advance clicks', async ({ adminPage }) => {
        const battleName = uniqueName('E2E Automatic');
        await createBattleViaSetup(adminPage, {
            name: battleName,
            fighters: 2,
            seed: 42,
            controlMode: 'automatic',
        });

        // The desk says so before anything is pressed, from stored state.
        await expect(adminPage.locator('#battle-control .control-default'))
            .toContainText('Automatic');
        await expect(adminPage.locator('#battle-control .control-count'))
            .toContainText('Nothing waits for a person');
        await expect(adminPage.locator('#advance-turn')).toHaveText('Advance turn');

        // Six presses, no prompt, no pause: the only control is the button.
        for (let turn = 1; turn <= 6; turn += 1) {
            if (await adminPage.locator('#advance-turn').isDisabled()) {
                break; // the fight ended, which is the other legal stop
            }
            await adminPage.click('#advance-turn');
            await expect(adminPage.locator(`.turn-group[data-turn="${turn}"]`)).toBeVisible();
            await expect(adminPage.locator('#turn-panel')).toBeHidden();
            await expect(adminPage.locator('#battle-status')).not.toHaveText('Awaiting players');
        }

        // Every roster card says the AI is playing it.
        const controllers = adminPage.locator('.combatant-card .combatant-controller');
        await expect(controllers.first()).toContainText('Automatic');
    });

    test('a mixed battle stops at the manual figure and names it', async ({ adminPage }) => {
        const battleName = uniqueName('E2E Mixed');
        await createBattleViaSetup(adminPage, {
            name: battleName,
            fighters: 3,
            seed: 42,
            controlMode: 'automatic',
        });

        // Take one figure by hand: the per-combatant control overrides the
        // battle default, which stays automatic for everybody else.
        await openWhoDecides(adminPage);
        const row = adminPage.locator('#battle-control .control-combatant').first();
        const mineName = (await row.locator('.control-combatant-name').textContent()).trim();
        await row.locator('select[name="control_mode"]').selectOption('manual');
        await row.locator('button:has-text("Set")').click();
        await adminPage.waitForURL(/\/tarmar\/battles\/\d+\/$/);

        await expect(adminPage.locator('#battle-control .control-default'))
            .toContainText('Automatic');
        await expect(adminPage.locator('#battle-control .control-overrides-note'))
            .toContainText('1 combatant');
        // The change is in the log, where a reader can find out why this
        // figure is about to behave differently.
        await expect(adminPage.locator('.event-status').last())
            .toContainText('played by hand');

        // One press runs the AI figures and stops at the manual one.
        await adminPage.click('#advance-turn');
        const advance = adminPage.locator('#advance-turn');
        await expect(advance).toHaveText(`Waiting on ${mineName}'s move`);
        await expect(advance).toBeDisabled();
        await expect(adminPage.locator('#battle-status')).toHaveText('Awaiting players');

        // The readout is stored state, not a flash: a reload repeats it.
        await adminPage.reload();
        await expect(adminPage.locator('#advance-turn'))
            .toHaveText(`Waiting on ${mineName}'s move`);
        await expect(adminPage.locator('#advance-turn')).toBeDisabled();

        // The owner is offered the menu and takes an action; the turn then
        // commits, with the AI figures' decisions in the same log.
        const panel = adminPage.locator('#turn-panel');
        await expect(panel).toBeVisible();
        await expect(panel).toContainText(new RegExp(sentencePattern(menuWords().panel.your_move)));
        await panel.locator('.turn-option').first().click();
        await panel.locator('.turn-confirm').click();

        await expect(adminPage.locator('.turn-group[data-turn="1"]')).toBeVisible();
        await expect(adminPage.locator('#battle-turn')).toHaveText('1');
        await expect(adminPage.locator('#advance-turn')).toHaveText('Advance turn');
        await expect(adminPage.locator('#advance-turn')).toBeEnabled();
        // The manual figure's decision is credited to the person, the
        // others to the AI's own reasoning.
        await expect(adminPage.locator('.event-decision').first()).toContainText('chooses');
    });

    test('handing the awaited figure to the AI unsticks the battle', async ({ adminPage }) => {
        const battleName = uniqueName('E2E Handback');
        await createBattleViaSetup(adminPage, {
            name: battleName,
            fighters: 2,
            seed: 42,
            controlMode: 'manual',
        });

        await expect(adminPage.locator('#battle-control .control-default'))
            .toContainText('Manual');
        await adminPage.click('#advance-turn');
        // Wait for the server's readout, not for the optimistic disable the
        // click itself applies — otherwise the label is still "Advance turn".
        await expect(adminPage.locator('#advance-turn'))
            .toHaveText(/^Waiting on .+'s move$/);
        await expect(adminPage.locator('#advance-turn')).toBeDisabled();
        const waitingLabel = await adminPage.locator('#advance-turn').textContent();
        const awaited = waitingLabel.replace('Waiting on ', '').replace("'s move", '').trim();

        // Hand that very figure to the AI while the turn is paused on it.
        await openWhoDecides(adminPage);
        const row = adminPage.locator('#battle-control .control-combatant')
            .filter({ hasText: awaited }).first();
        await row.locator('select[name="control_mode"]').selectOption('automatic');
        await row.locator('button:has-text("Set")').click();
        await adminPage.waitForURL(/\/tarmar\/battles\/\d+\/$/);

        // The pause is gone and the button works again — no reload trick,
        // no wedged battle.
        await expect(adminPage.locator('#advance-turn')).toBeEnabled();
        await expect(adminPage.locator('.event-status').last())
            .toContainText('hands its decisions to the AI');
    });

    test('set every combatant to follow the battle clears the overrides', async ({ adminPage }) => {
        const battleName = uniqueName('E2E SetAll');
        await createBattleViaSetup(adminPage, {
            name: battleName,
            fighters: 3,
            seed: 42,
            controlMode: 'automatic',
        });

        await openWhoDecides(adminPage);
        const row = adminPage.locator('#battle-control .control-combatant').first();
        await row.locator('select[name="control_mode"]').selectOption('manual');
        await row.locator('button:has-text("Set")').click();
        await adminPage.waitForURL(/\/tarmar\/battles\/\d+\/$/);
        await expect(adminPage.locator('#battle-control .control-overrides-note'))
            .toContainText('1 combatant');

        await adminPage.click('button:has-text("Set every combatant to follow the battle")');
        await adminPage.waitForURL(/\/tarmar\/battles\/\d+\/$/);
        await expect(adminPage.locator('#battle-control .control-overrides-note'))
            .toContainText('No combatant overrides');
        await expect(adminPage.locator('#advance-turn')).toBeEnabled();
    });
});
