/**
 * E2E test for the skill level ladder on the character edit page.
 *
 * Covers the two rulings the page had to be reworked for:
 *   - issue #146: skill levels and points are uncapped. 15 points (level V) is
 *     no longer a ceiling, no row shows a MAX badge, and a character carrying
 *     more than 15 points in a skill imports cleanly.
 *   - issue #154: the Arcane skill costs double, so its points buy half as
 *     much level as any other skill's.
 *
 * The page no longer computes levels in JavaScript — characters/skills.py owns
 * the ladder and every response carries the rendered level string — so this
 * test is what proves the server's string actually reaches the row after a
 * click.
 *
 * The character is written from an export file by the test, as the import
 * path writes one, so each run gets its own character. A player's import no
 * longer carries earned skill points (#744), so the file is seeded for the
 * player rather than uploaded; the import of a skill past 15 points (#146)
 * is covered for a Game Master's import in characters/tests/test_import.py.
 */

const {
    test, expect, loginAs, seedCharacterFromExport, TEST_USER,
} = require('./fixtures');
const { loadedSkillRules } = require('./skill-rules');

const ROMAN = [[10, 'X'], [9, 'IX'], [5, 'V'], [4, 'IV'], [1, 'I']];

/** A small level as the sheet writes it: 3 is "III". */
function roman(number) {
    let left = number;
    let written = '';
    for (const [value, numeral] of ROMAN) {
        while (left >= value) {
            written += numeral;
            left -= value;
        }
    }
    return written;
}

/**
 * What a skill's row says for ``points``, priced as characters.skills'
 * points_for_level prices it: level N costs ``multiplier`` times N(N+1)/2 in
 * total, and the points past the level are shown beside it.
 */
function levelText(points, multiplier) {
    let level = 0;
    while (multiplier * ((level + 1) * (level + 2)) / 2 <= points) level += 1;
    const spare = points - multiplier * (level * (level + 1)) / 2;
    return `Level ${level ? roman(level) : '0'}${spare ? ` (+${spare})` : ''}`;
}

// A 20-point sword is level V with 5 points to spare, so a single click
// reaches level VI — past the cap that used to exist. 5 Arcane points is only
// level I (level I costs 2 and level II costs 6, both doubled), so its next
// point buys level II.
const LADDER_CHARACTER = {
    _format: 'tarmar-character',
    _version: 1,
    data: {
        name: 'Ladder Testwright',
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
        location: 'Village',
        wealth_level: 'Moderate',
        starting_coin: 100,
        skills: { sword: 20, arcane: 5 },
        free_skill_points: 2,
        free_spell_points: 0,
        equipment: [],
        pack_dropped: false,
        total_xp: 1500,
        age: 20,
        experience_terms: 1,
        experience_log: [],
        money_earned: 200,
        aging_str_penalty: 0,
        aging_dex_penalty: 0,
        aging_int_penalty: 0,
        aging_wis_penalty: 0,
        aging_con_penalty: 0,
        is_alive: true,
        death_cause: '',
    },
};

/**
 * Give the player LADDER_CHARACTER and land on its edit page. Seeded rather
 * than imported: a player's import keeps no earned skill points (#744), and
 * the import of a skill past 15 points (#146) is covered for the Game
 * Master's verbatim import in characters/tests/test_import.py.
 * @param {import('@playwright/test').Page} page
 */
async function importLadderCharacter(page) {
    await seedCharacterFromExport(page, TEST_USER, LADDER_CHARACTER);
}

test.describe('Skill level ladder', () => {
    test('allocating a point past level V renders the server-computed level', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await importLadderCharacter(page);

        const swordRow = page.locator('.skill-row[data-skill="sword"]');
        const arcaneRow = page.locator('.skill-row[data-skill="arcane"]');

        // Server-rendered starting state. 20 points is level V plus 5; 5 Arcane
        // points is level I plus 3, where any other skill would be level II.
        await expect(swordRow.locator('.skill-level')).toHaveText('Level V (+5)');
        await expect(swordRow.locator('.skill-points')).toHaveText('(20 pts)');
        await expect(arcaneRow.locator('.skill-level')).toHaveText('Level I (+3)');
        await expect(arcaneRow.locator('.skill-points')).toHaveText('(5 pts)');

        // No level is terminal any more, so nothing is badged MAX and the
        // level-V skill still offers its + button.
        await expect(page.locator('.max-badge')).toHaveCount(0);
        await expect(swordRow.locator('.allocate-btn')).toBeVisible();

        // 21st point: level VI, which the old client-side ladder could not
        // render (it stopped at V and showed "V (+6)").
        await swordRow.locator('.allocate-btn').click();
        await expect(swordRow.locator('.skill-level')).toHaveText('Level VI');
        await expect(swordRow.locator('.skill-points')).toHaveText('(21 pts)');
        await expect(page.locator('#free-points')).toHaveText('1');
        await expect(page.locator('.max-badge')).toHaveCount(0);

        // 6th Arcane point: level II on the doubled schedule, where the same
        // 6 points would be level III in any other skill.
        await arcaneRow.locator('.allocate-btn').click();
        await expect(arcaneRow.locator('.skill-level')).toHaveText('Level II');
        await expect(arcaneRow.locator('.skill-points')).toHaveText('(6 pts)');
        await expect(page.locator('#free-points')).toHaveText('0');

        // Spent out: the + buttons go away because there is nothing left to
        // spend, not because a skill hit a ceiling.
        await expect(page.locator('.allocate-btn')).toHaveCount(0);

        // A reload proves the server renders the same levels it just sent.
        await page.reload();
        await expect(swordRow.locator('.skill-level')).toHaveText('Level VI');
        await expect(arcaneRow.locator('.skill-level')).toHaveText('Level II');
    });

    test('the Arcane ladder is priced by the loaded canon\'s multiplier (#70)', async ({ page }) => {
        // The test above types the pages' doubled cost; this one reads the
        // loaded canon's, so it holds under either canon.
        const { arcane } = loadedSkillRules();
        // One point short of level II: the next point buys it.
        const points = 3 * arcane.multiplier - 1;
        await loginAs(page, TEST_USER);
        await seedCharacterFromExport(page, TEST_USER, {
            ...LADDER_CHARACTER,
            data: { ...LADDER_CHARACTER.data, skills: { [arcane.name]: points }, free_skill_points: 1 },
        });

        const row = page.locator(`.skill-row[data-skill="${arcane.name}"]`);
        await expect(row.locator('.skill-level')).toHaveText(levelText(points, arcane.multiplier));
        await row.locator('.allocate-btn').click();
        await expect(row.locator('.skill-level')).toHaveText(levelText(points + 1, arcane.multiplier));
        await expect(row.locator('.skill-level')).toHaveText('Level II');
        await expect(row.locator('.skill-points')).toHaveText(`(${points + 1} pts)`);
    });
});
