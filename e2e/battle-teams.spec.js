/**
 * E2E for parties marching into the battle simulator as teams (#275).
 *
 * What only a real browser can prove end to end: the DM forms a party from
 * two of their characters, imports it on the setup page as a team (the
 * notice stays, the muster groups under the team's header), moves a lone
 * fighter onto that team through the entry's own move control, and marches —
 * landing on a battle whose roster and arena are grouped and tinted by team,
 * and whose first advance ends it with a victory line naming the TEAM (one
 * team standing is a finished battle, however it came to stand alone).
 */

const {
    test, expect, loginAs, TEST_ADMIN, createNamedCharacter, searchPicker,
} = require('./fixtures');
const { uniqueStamp } = require('./unique-name');

test.describe('Battle teams', () => {
    test('DM imports a party as a team and fights to a team victory', async ({ adminPage }) => {
        test.setTimeout(180000);
        const stamp = uniqueStamp();
        const partyName = `E2E Wolves ${stamp}`;
        const firstName = `E2E Ashe ${stamp}`;
        const secondName = `E2E Bram ${stamp}`;
        const battleName = `E2E Team Battle ${stamp}`;

        // Two characters to band together.
        await createNamedCharacter(adminPage, firstName);
        await createNamedCharacter(adminPage, secondName);

        // Form the party and seat both members.
        await adminPage.goto('/tarmar/parties/');
        await adminPage.click('a:has-text("Form a New Party")');
        await adminPage.fill('#id_name', partyName);
        await adminPage.click('button:has-text("Form Party")');
        await adminPage.waitForURL(/\/tarmar\/parties\/\d+\//);
        for (const memberName of [firstName, secondName]) {
            await searchPicker(adminPage, '#party-character-filter', memberName);
            await adminPage
                .locator('.party-addable-row', { hasText: memberName })
                .locator('button:has-text("Add")')
                .click();
            await expect(
                adminPage.locator('.party-member', { hasText: memberName })
            ).toBeVisible();
        }

        // A fresh setup; the party imports as a team and the notice stays.
        await adminPage.goto('/tarmar/battles/');
        await adminPage.click('.battle-list-masthead button[type="submit"]');
        await adminPage.waitForURL(/\/tarmar\/battles\/setup\/\d+\/$/);
        const setupUrl = adminPage.url();
        const partyForm = adminPage.locator('.setup-party-form');
        const partyOption = partyForm.locator('option', { hasText: partyName });
        await expect(partyOption).toContainText('2 members');
        await partyForm
            .locator('select[name="party_id"]')
            .selectOption(await partyOption.getAttribute('value'));
        await partyForm.locator('button:has-text("Import as a team")').click();
        // Back on the setup page's own address, not the import's POST URL
        // (issue #339, item 20), with the notice carried across.
        await adminPage.waitForURL(setupUrl);
        await expect(adminPage.locator('.setup-notice')).toContainText(
            `${partyName} joins as a team — 2 members mustered`
        );
        const teamSection = adminPage.locator(
            `.muster-team[data-team="${partyName}"]`
        );
        await expect(teamSection.locator('.muster-entry')).toHaveCount(2);
        await expect(teamSection).toContainText(firstName);
        await expect(teamSection).toContainText(secondName);
        // A reload is a GET of the setup page: nothing is imported twice.
        await adminPage.reload();
        await expect(teamSection.locator('.muster-entry')).toHaveCount(2);

        // A lone archer joins the free pool, then moves onto the team
        // through the entry's own move control.
        await adminPage.click(
            '.setup-fighter-grid form:has(input[value="archer"]) button'
        );
        await adminPage.waitForURL(setupUrl);
        const freePool = adminPage.locator('.muster-team-free');
        await expect(freePool.locator('.muster-entry')).toHaveCount(1);
        const archerEntry = freePool.locator('.muster-entry');
        await archerEntry
            .locator('select[name="team"]')
            .selectOption(partyName);
        await archerEntry.locator('button:has-text("Move")').click();
        await adminPage.waitForURL(setupUrl);
        await expect(
            adminPage.locator(`.muster-team[data-team="${partyName}"] .muster-entry`)
        ).toHaveCount(3);
        await expect(adminPage.locator('.muster-team-free')).toContainText(
            'Nobody fights alone'
        );

        // March. The battle page groups the roster under the team's header
        // (swatch and all) and tints the arena markers by team.
        await adminPage.fill('input[name="name"]', battleName);
        await adminPage.fill('input[name="seed"]', '4275');
        await adminPage.click('.setup-begin');
        await adminPage.waitForURL(/\/tarmar\/battles\/\d+\/$/);
        const teamHead = adminPage.locator('.roster-team-head');
        await expect(teamHead).toHaveCount(1);
        await expect(teamHead).toContainText(partyName);
        await expect(teamHead.locator('.roster-team-swatch')).toBeVisible();
        await expect(
            adminPage.locator(`.arena-combatant[data-team="${partyName}"]`)
        ).toHaveCount(3);

        // One team standing is a finished battle: the first advance ends it
        // and the victory line names the team, not a lone fighter.
        await adminPage.click('#advance-turn');
        await expect(adminPage.locator('#battle-status')).toHaveText('Finished');
        await expect(adminPage.locator('#battle-log')).toContainText(
            `${partyName} stands victorious`
        );
        await expect(adminPage.locator('#advance-turn')).toBeDisabled();
    });
});
