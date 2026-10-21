/**
 * E2E for a Game Master taking a seat in a battle lobby (issue #339, item 26).
 *
 * The lobby hid "Claim this seat" from a Game Master although the server has
 * always accepted a GM's claim. Spencer wants a GM able to play alongside
 * the table, so a GM now sees the claim form on an open seat like anyone else.
 */

const { test, expect, createBattleViaSetup } = require('./fixtures');
const { uniqueName } = require('./unique-name');

test('a Game Master sees and uses Claim this seat in a lobby', async ({ adminPage: page }) => {
    const battleName = uniqueName('E2E GM Seat');
    await createBattleViaSetup(page, { name: battleName, fighters: 1, seed: 7, playerSeats: 1 });
    await expect(page.locator('#battle-status')).toHaveText('Lobby');

    const openSeat = page.locator('.lobby-seat.is-open');
    await expect(openSeat).toHaveCount(1);
    await openSeat.locator('summary:has-text("Claim this seat")').click();
    await page.selectOption('select[name="archetype"]', 'archer');
    await page.click('button:has-text("Join with a generated fighter")');

    await expect(page.locator('.lobby-seat.is-you')).toBeVisible();
});
