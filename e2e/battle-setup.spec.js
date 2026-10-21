/**
 * E2E for the staged battle setup and saved characters (#270/#271).
 *
 * What only a real browser can prove: the DM's whole muster flow — terms
 * autosaving to the draft, a mixed roster (generated fighters, a beast, a
 * saved character) accruing onto the ledger, the round trip through the
 * character editor coming back to an intact setup, and the march landing on
 * a battle whose roster carries the saved character; then a player claiming
 * a lobby seat with a character of their own.
 */

const {
    test, expect, TEST_USER, loginAs, submitPassphrase, createNamedCharacter,
    createBattleViaSetup, battleRowOnList,
} = require('./fixtures');
const { uniqueName, uniqueStamp } = require('./unique-name');
const { startCoverage, stopCoverage } = require('./coverage');

test.describe('Battle setup', () => {
    test('DM builds a mixed roster with a saved character, round-trips the editor, and starts', async ({ adminPage }) => {
        test.setTimeout(120000);
        const stamp = uniqueStamp();
        const battleName = `E2E Muster ${stamp}`;
        const characterName = `E2E Morwen ${stamp}`;
        await createNamedCharacter(adminPage, characterName);

        // Open a fresh setup page from the slimmed list.
        await adminPage.goto('/tarmar/battles/');
        await adminPage.click('.battle-list-masthead button[type="submit"]');
        await adminPage.waitForURL(/\/tarmar\/battles\/setup\/\d+\/$/);
        const setupUrl = adminPage.url();

        // The terms autosave onto the draft as fields blur.
        await adminPage.fill('input[name="name"]', battleName);
        await adminPage.locator('input[name="name"]').blur();
        await expect(adminPage.locator('[data-field="name"]')).toHaveClass(/saved/);
        await adminPage.fill('input[name="seed"]', '4207');
        await adminPage.locator('input[name="seed"]').blur();
        await expect(adminPage.locator('[data-field="seed"]')).toHaveClass(/saved/);

        // Muster a fighter, a beast, and the saved character; the ledger
        // counts each arrival.
        await adminPage.click('.setup-fighter-grid form:has(input[value="archer"]) button');
        await adminPage.waitForURL(setupUrl);
        await expect(adminPage.locator('.muster-entry')).toHaveCount(1);
        const beastForm = adminPage.locator('.setup-beast-form').first();
        await beastForm.locator('select[name="slug"]').selectOption('dire-wolf');
        await beastForm.locator('button').click();
        await adminPage.waitForURL(setupUrl);
        const shelfRow = adminPage.locator('.setup-character', { hasText: characterName });
        await shelfRow.locator('button:has-text("Seat")').click();
        await adminPage.waitForURL(setupUrl);
        await expect(adminPage.locator('.muster-entry')).toHaveCount(3);
        await expect(adminPage.locator('.setup-capacity-count')).toHaveText('3');
        await expect(
            adminPage.locator('.muster-entry', { hasText: characterName })
        ).toContainText('snapshotted when the battle starts');

        // The round trip: edit the mustered character, come back, and the
        // setup is exactly as it was left — server-side draft state, not
        // browser memory.
        await adminPage
            .locator('.muster-entry', { hasText: characterName })
            .locator('a:has-text("Edit")')
            .click();
        await adminPage.waitForURL(/\/tarmar\/characters\/\d+\/\?next=/);
        await expect(adminPage.locator('#setup-return-link')).toBeVisible();
        await adminPage.click('#setup-return-link');
        await adminPage.waitForURL(setupUrl);
        await expect(adminPage.locator('.muster-entry')).toHaveCount(3);
        await expect(adminPage.locator('input[name="name"]')).toHaveValue(battleName);
        await expect(adminPage.locator('input[name="seed"]')).toHaveValue('4207');

        // March. The battle opens with all three on the arena, the saved
        // character under its own name.
        await adminPage.click('.setup-begin');
        await adminPage.waitForURL(/\/tarmar\/battles\/\d+\/$/);
        await expect(adminPage.locator('h1')).toHaveText(battleName);
        await expect(adminPage.locator('.combatant-card')).toHaveCount(3);
        await expect(
            adminPage.locator('.combatant-card', { hasText: characterName })
        ).toBeVisible();
    });

    test('the New character round trip returns to an intact setup with the character on the shelf', async ({ adminPage }) => {
        await adminPage.goto('/tarmar/battles/');
        await adminPage.click('.battle-list-masthead button[type="submit"]');
        await adminPage.waitForURL(/\/tarmar\/battles\/setup\/\d+\/$/);
        const setupUrl = adminPage.url();
        await adminPage.click('.setup-fighter-grid form:has(input[value="wizard"]) button');
        await adminPage.waitForURL(setupUrl);

        // Leave through New character: create page, roll, land in the
        // editor still carrying the way back.
        await adminPage.click('.setup-new-character');
        await adminPage.waitForURL(/\/tarmar\/characters\/create\/\?next=/);
        await adminPage.click('button:has-text("Roll a New Character")');
        await adminPage.waitForURL(/\/tarmar\/characters\/\d+\/\?next=/);
        const characterName = uniqueName('E2E Fresh');
        await adminPage.fill('#id_name', characterName);
        await adminPage.locator('#id_name').blur();
        await expect(adminPage.locator('.name-field')).toHaveClass(/saved/);
        await adminPage.click('#setup-return-link');

        // Back at the setup: the wizard is still mustered, and the new
        // character is on the shelf ready to seat.
        await adminPage.waitForURL(setupUrl);
        await expect(adminPage.locator('.muster-entry')).toHaveCount(1);
        const shelfRow = adminPage.locator('.setup-character', { hasText: characterName });
        await shelfRow.locator('button:has-text("Seat")').click();
        await adminPage.waitForURL(setupUrl);
        await expect(adminPage.locator('.muster-entry')).toHaveCount(2);

        // Striking a figure asks first; backing out keeps it, confirming
        // strikes it (issue #339, item 19).
        const question = adminPage.locator('.notice-confirm');
        await adminPage.locator('.muster-entry').last().locator('button:has-text("Strike")').click();
        await expect(question).toContainText('from the muster?');
        await question.locator('button:has-text("Cancel")').click();
        await expect(question).toHaveCount(0);
        await expect(adminPage.locator('.muster-entry')).toHaveCount(2);
        await adminPage.locator('.muster-entry').last().locator('button:has-text("Strike")').click();
        await question.locator('button:has-text("Strike")').click();
        await expect(adminPage.locator('.muster-entry')).toHaveCount(1);

        // Tidy the draft away rather than leaving it on the desk. That asks too.
        await adminPage.click('button:has-text("Discard this draft")');
        await question.locator('button:has-text("Discard")').click();
        await adminPage.waitForURL(/\/tarmar\/battles\/$/);
    });

    test('a player claims a lobby seat with their own saved character', async ({ adminPage, browser }, testInfo) => {
        test.setTimeout(120000);
        const stamp = uniqueStamp();
        const battleName = `E2E Own Character ${stamp}`;
        const characterName = `E2E Aldwin ${stamp}`;

        await createBattleViaSetup(adminPage, {
            name: battleName, fighters: 1, seed: 42, playerSeats: 1,
        });
        await expect(adminPage.locator('#battle-status')).toHaveText('Lobby');

        // The player brings a character of their own making.
        const playerContext = await browser.newContext();
        const playerPage = await playerContext.newPage();
        await startCoverage(playerPage);
        await submitPassphrase(playerPage);
        await loginAs(playerPage, TEST_USER);
        await createNamedCharacter(playerPage, characterName);

        const listRow = await battleRowOnList(playerPage, battleName);
        await listRow.locator('a', { hasText: battleName }).first().click();
        await playerPage.click('summary:has-text("Claim this seat")');
        await playerPage.selectOption(
            'select[name="character_id"]',
            { label: characterName }
        );
        await playerPage.click('button:has-text("Join with this character")');

        // Their seat wears the character's name, and the claim hint told
        // them the snapshot semantics before they joined.
        const mySeat = playerPage.locator('.lobby-seat.is-you');
        await expect(mySeat).toBeVisible();
        await expect(mySeat).toContainText(characterName);
        await expect(mySeat).toContainText(`Played by ${TEST_USER.username}`);

        await stopCoverage(playerPage, `${testInfo.title} (player)`);
        await playerContext.close();
    });
});
