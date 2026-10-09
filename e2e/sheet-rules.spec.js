// @ts-check
/**
 * The sheet held to the rules for a rename and an XP award (issues #768,
 * #769). Seeded characters, one per test, so parallel workers never share
 * one.
 */

const {
    test, expect, loginAs, seedCharacterFromExport, TEST_USER, TEST_ADMIN,
} = require('./fixtures');

/** A made character with the given skills, XP and free points. */
function character(name, { skills, totalXp = 0, freePoints = 0 }) {
    return {
        _format: 'tarmar-character',
        _version: 1,
        data: {
            name,
            strength: 12,
            dexterity: 12,
            intelligence: 12,
            wisdom: 12,
            constitution: 12,
            charisma: 12,
            appearance_roll: '3',
            height_roll: '3',
            weight_roll: '3',
            fatigue_roll: 7,
            provenance_class: 'commoner',
            provenance_detail: 'Laborer',
            location: 'City',
            wealth_level: 'Moderate',
            starting_coin: 100,
            skills,
            free_skill_points: freePoints,
            free_spell_points: 0,
            total_xp: totalXp,
            equipment: [],
        },
    };
}

test.describe('The sheet holds a rename and an XP award to the rules', () => {
    test('a player cannot rename a skill into another, and may name its chosen part (#768)', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await seedCharacterFromExport(
            page, TEST_USER, character('Rename Testwright', { skills: { 'maker skill': 15 } })
        );
        // The only skill, so the row is found whatever its name reads mid-edit.
        const skillRow = page.locator('.skill-row').first();
        const skillName = skillRow.locator('.skill-name');

        // Into Arcane: the points would buy another rule. Refused, and put back.
        await skillName.click();
        await skillName.fill('Arcane');
        await skillName.press('Enter');
        await expect(skillName).toHaveClass(/error/);
        await expect(skillRow.locator('.autosave-error')).toContainText('only a Game Master');
        await expect(skillName).toHaveText(/^Maker Skill$/i);

        // The Maker Skill's craft is the player's to name: saved.
        await skillName.click();
        await skillName.fill('Maker Skill (Smith)');
        await skillName.press('Enter');
        await expect(skillName).toHaveClass(/saved/);
        await expect(skillName).toHaveText(/^Maker Skill \(smith\)$/i);
    });

    test('a Game Master awarding XP on the sheet grants the points it earns (#769)', async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        const id = await seedCharacterFromExport(
            page, TEST_ADMIN, character('Award Testwright', { skills: {} })
        );
        const level = page.locator('#character-level');
        const levelBefore = await level.textContent();
        const xp = page.locator('.editable-field[data-field="total_xp"] input');
        await xp.fill('3000');
        await xp.blur();
        await expect(page.locator('.editable-field[data-field="total_xp"]')).toHaveClass(/saved/);

        // prior-experience.md: "at 3,000 XP you would have earned 2 skill
        // points". Painted in place from the save's answer (#874): the free
        // points, the level and the control that spends them, with no reload.
        await expect(page.locator('#free-points')).toHaveText('2');
        await expect(level).not.toHaveText(levelBefore || '');
        const levelAfter = await level.textContent();
        await expect(page.locator('.add-skill-control')).toBeVisible();

        // A reload renders the same figures the save painted.
        await page.goto(`/tarmar/characters/${id}/`);
        await expect(page.locator('#free-points')).toHaveText('2');
        await expect(level).toHaveText(levelAfter || '');
    });

    test('a Game Master grants a skill point on the sheet at no free points (#770)', async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        await seedCharacterFromExport(
            page, TEST_ADMIN, character('Grant Testwright', { skills: { survival: 1 } })
        );
        const row = page.locator('.skill-row').first();
        await expect(page.locator('#free-points')).toHaveText('0');
        await row.locator('.allocate-btn').click();
        await expect(page.locator('.skill-row').first().locator('.skill-points')).toHaveText('(2 pts)');
        // The control stays for a Game Master at no free points: a second grant.
        await page.locator('.skill-row').first().locator('.allocate-btn').click();
        await expect(page.locator('.skill-row').first().locator('.skill-points')).toHaveText('(3 pts)');
    });
});
