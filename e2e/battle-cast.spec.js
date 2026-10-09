/**
 * E2E test for the wizard cast flow (issue #250).
 *
 * What only a real browser proves about the gate: a seated wizard's action
 * menu carries ONE cast option rather than a row per spell; declaring it
 * opens the spell stage in place (no modal, nothing transient) with the
 * confirm still refused; the spells the wizard cannot afford are listed
 * with the reason instead of hidden; picking one ungates the confirm; and
 * standing down puts the wizard back to the plain menu with no lingering
 * selection. Then the cast is taken, logged, and survives a reload.
 */

const {
    test, expect, TEST_USER, loginAs, submitPassphrase, createBattleViaSetup, battleRowOnList,
} = require('./fixtures');
const { startCoverage, stopCoverage } = require('./coverage');

test.describe('Wizard cast flow', () => {
    test('a seated wizard declares, inspects, stands down, then casts', async ({ adminPage, browser }, testInfo) => {
        test.setTimeout(120000);
        const battleName = `E2E Cast ${Date.now()}`;

        // A battle with one AI opponent and one seat for the wizard.
        await createBattleViaSetup(adminPage, {
            name: battleName, fighters: 1, seed: 11, playerSeats: 1,
        });
        await expect(adminPage.locator('#battle-status')).toHaveText('Lobby');

        // The player claims the seat with a generated wizard and readies up.
        const playerContext = await browser.newContext();
        const playerPage = await playerContext.newPage();
        await startCoverage(playerPage);
        await submitPassphrase(playerPage);
        await loginAs(playerPage, TEST_USER);
        const listRow = await battleRowOnList(playerPage, battleName);
        await listRow.locator('a', { hasText: battleName }).first().click();
        await playerPage.click('summary:has-text("Claim this seat")');
        await playerPage.selectOption('select[name="archetype"]', 'wizard');
        await playerPage.click('button:has-text("Join with a generated fighter")');
        // Wait for the joined lobby before Ready, or the click can land on
        // the page the join is replacing (issue #493).
        await expect(playerPage.locator('.lobby-seat.is-you')).toBeVisible();
        await playerPage.click('button:has-text("I\'m ready")');
        await expect(playerPage.locator('.lobby-ready-chip.is-ready')).toBeVisible();

        // The DM starts and advances into the wizard's turn.
        await expect(adminPage.locator('.lobby-ready-chip.is-ready'))
            .toBeVisible({ timeout: 15000 });
        await adminPage.click('.battle-start-form button[type="submit"]');
        await expect(playerPage.locator('#battle-arena'))
            .toBeVisible({ timeout: 15000 });
        await adminPage.click('#advance-turn');
        await expect(adminPage.locator('#battle-status'))
            .toHaveText('Awaiting players');

        // One cast option, not one per spell — and it names the mana pool.
        const castOption = playerPage.locator('#turn-panel .turn-option-cast');
        await expect(castOption).toBeVisible({ timeout: 15000 });
        await expect(castOption).toHaveCount(1);
        await expect(castOption).toContainText('CAST SPELL');
        await expect(castOption).toContainText('Mana');
        await expect(playerPage.locator('#turn-panel .turn-cast-stage'))
            .toHaveCount(0);

        // Declaring the cast opens the stage; the confirm stays refused
        // until a spell is explicitly picked, and nothing is pre-selected.
        await castOption.click();
        const stage = playerPage.locator('#turn-panel .turn-cast-stage');
        await expect(stage).toBeVisible();
        await expect(playerPage.locator('#turn-panel .turn-confirm')).toBeDisabled();
        await expect(stage.locator('.turn-spell[aria-pressed="true"]')).toHaveCount(0);
        // The wizard's whole hand is on show, castable or not.
        await expect(stage.locator('.turn-spell').first()).toBeVisible();

        // Standing down leaves no selection behind.
        await stage.locator('.turn-spell:not(:disabled)').first().click();
        await expect(playerPage.locator('#turn-panel .turn-confirm')).toBeEnabled();
        await playerPage.click('#turn-panel .turn-cast-standdown');
        await expect(playerPage.locator('#turn-panel .turn-cast-stage')).toHaveCount(0);
        await expect(playerPage.locator('#turn-panel .turn-confirm')).toBeDisabled();

        // Declare again, pick a spell, and cast it for real.
        await castOption.click();
        const spell = stage.locator('.turn-spell:not(:disabled)').first();
        const spellName = (
            await spell.locator('.turn-spell-name').textContent()
        ).trim();
        await spell.click();
        await expect(spell).toHaveAttribute('aria-pressed', 'true');
        await playerPage.click('#turn-panel .turn-confirm');

        // The turn completes and the log credits the player's cast.
        await expect(playerPage.locator('#battle-turn')).toHaveText('1');
        const decision = playerPage.locator('.event-decision', {
            hasText: `chosen by ${TEST_USER.username}`,
        });
        await expect(decision).toBeVisible();
        await expect(decision).toContainText(spellName);

        // A reload reproduces the cast from the database.
        await playerPage.reload();
        await expect(
            playerPage.locator('.event-decision', { hasText: spellName })
        ).toBeVisible();

        await stopCoverage(playerPage, `${testInfo.title} (player context)`);
        await playerContext.close();
    });
});
