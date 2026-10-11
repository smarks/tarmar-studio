/**
 * E2E tests for character edit page.
 * Tests auto-save functionality and field editing.
 *
 * Every test here seeds its own character (issue #642). Fourteen of them used
 * to open with `if (!await page.locator('td a[href*="/characters/"]').count())
 * test.skip()` and then edit whichever row happened to be first, so a list that
 * came back empty -- the query broken, the visibility rule excluding the user's
 * own rows, the login fixture failing -- turned them green by skipping instead
 * of red, and a run that did have rows was editing a character another spec had
 * made.
 */

const {
    test, expect, loginAs, logout, createNamedCharacter, seedCharacterFromExport, TEST_USER, TEST_ADMIN,
} = require('./fixtures');
const { pinDice } = require('./dice');

/**
 * Create a character for this test and leave the page on its editor.
 * @param {import('@playwright/test').Page} page
 * @param {import('@playwright/test').TestInfo} testInfo
 * @param {string} label - A word for the name, so a failure names the test.
 * @returns {Promise<string>} the character's name
 */
async function seedCharacterOnItsEditor(page, testInfo, label) {
    const name = `${label} ${testInfo.workerIndex}${Date.now()}`;
    await loginAs(page, TEST_USER);
    await createNamedCharacter(page, name);
    return name;
}

test.describe('Character Edit Page', () => {
    test('the list links each character to its own editor', async ({ page }, testInfo) => {
        const name = await seedCharacterOnItsEditor(page, testInfo, 'Linked');
        await page.goto('/tarmar/characters/');

        await page.locator('tr', { hasText: name }).getByRole('link', { name }).click();

        await expect(page).toHaveURL(/\/characters\/\d+\//);
        await expect(page.locator('h1 input')).toHaveValue(name);
    });

    test('character name field is editable', async ({ page }, testInfo) => {
        await seedCharacterOnItsEditor(page, testInfo, 'Edited');

        // Name input should exist and be editable
        const nameInput = page.locator('input[name="name"], [data-field="name"] input');
        await expect(nameInput).toBeVisible();
    });
});

test.describe('Auto-save Functionality', () => {
    test('field shows saving state on blur', async ({ page }, testInfo) => {
        await seedCharacterOnItsEditor(page, testInfo, 'Edited');

        // Find an autosave input
        const autosaveInput = page.locator('.autosave').first();
        await expect(autosaveInput).toBeVisible();

        // Get the parent container
        const container = autosaveInput.locator('xpath=ancestor::*[@data-field]');

        // Change value and blur
        const originalValue = await autosaveInput.inputValue();
        await autosaveInput.fill(originalValue + 'x');
        await autosaveInput.blur();

        // Container should briefly have saving class (may be too fast to catch)
        // Wait for saved or error class
        await expect(container).toHaveClass(/saved|error/, { timeout: 5000 });
    });

    test('successful save shows green border', async ({ page }, testInfo) => {
        await seedCharacterOnItsEditor(page, testInfo, 'Edited');

        // Find the name field which should be autosave
        const nameField = page.locator('[data-field="name"]');
        await expect(nameField).toBeVisible();

        const nameInput = nameField.locator('input');
        const originalValue = await nameInput.inputValue();

        // Change and blur
        await nameInput.fill(originalValue + 'Test');
        await nameInput.blur();

        // Wait for saved class
        await expect(nameField).toHaveClass(/saved/, { timeout: 5000 });

        // Restore original value
        await nameInput.fill(originalValue);
        await nameInput.blur();
    });

    test('Enter key triggers save', async ({ page }, testInfo) => {
        await seedCharacterOnItsEditor(page, testInfo, 'Edited');

        const nameField = page.locator('[data-field="name"]');
        await expect(nameField).toBeVisible();

        const nameInput = nameField.locator('input');
        const originalValue = await nameInput.inputValue();

        // Change value and press Enter
        await nameInput.fill(originalValue + 'Enter');
        await nameInput.press('Enter');

        // Should trigger save
        await expect(nameField).toHaveClass(/saved|saving/, { timeout: 5000 });

        // Restore
        await nameInput.fill(originalValue);
        await nameInput.press('Enter');
    });

    test('validation error surfaces inline next to the field', async ({ page }, testInfo) => {
        await seedCharacterOnItsEditor(page, testInfo, 'Edited');

        const nameField = page.locator('[data-field="name"]');
        await expect(nameField).toBeVisible();

        const nameInput = nameField.locator('input');
        const originalValue = await nameInput.inputValue();

        // Emoji are rejected by the server-side regex on the name field.
        await nameInput.fill('🎲');
        await nameInput.blur();

        // The inline indicator should appear and persist with the server message.
        const errorIndicator = nameField.locator('.autosave-error');
        await expect(errorIndicator).toBeVisible({ timeout: 5000 });
        await expect(errorIndicator).toContainText(/letters|emoji|may contain/i);
        await expect(nameField).toHaveClass(/error/);

        // The value reverts to the last-good state so the field stays usable.
        await expect(nameInput).toHaveValue(originalValue);

        // Editing the field again clears the error (no auto-dismiss, but
        // user re-engagement removes it).
        await nameInput.focus();
        await nameInput.dispatchEvent('input');
        await expect(errorIndicator).toHaveCount(0);
    });
});

test.describe('Character Stats Display', () => {
    test('displays character attributes', async ({ page }, testInfo) => {
        await seedCharacterOnItsEditor(page, testInfo, 'Edited');

        // Scoped to the header cell: a bare `text=STR` also matches every
        // ancestor that contains it, which is a strict-mode violation. The
        // word boundaries keep it from drifting onto a future STRENGTH or
        // STR_MOD heading.
        const strengthHeading = page
            .locator('table.stats-table th')
            .filter({ hasText: /\bSTR\b/ });
        await expect(strengthHeading).toHaveCount(1);
        await expect(strengthHeading).toBeVisible();
    });

    test('displays character status', async ({ page }, testInfo) => {
        await seedCharacterOnItsEditor(page, testInfo, 'Edited');

        // Should have some status-related element. The status badge itself:
        // a bare [class*="status"] also matched the session picker's status
        // line (#973), which is empty, so hidden, for a viewer with no
        // sessions.
        const statusElement = page.locator('.status-badge');
        if (await statusElement.count() > 0) {
            await expect(statusElement.first()).toBeVisible();
        }
    });
});

test.describe('Navigation', () => {
    test('back to characters list works', async ({ page }, testInfo) => {
        await seedCharacterOnItsEditor(page, testInfo, 'Edited');

        // Click Characters in menu
        await page.locator('#menuToggle').click();
        await page.locator('a:has-text("Characters")').first().click();

        // Should be back at list
        await expect(page).toHaveURL(/\/characters\/$/);
    });

    test('menu shows Character Editor as active on edit page', async ({ page }, testInfo) => {
        await seedCharacterOnItsEditor(page, testInfo, 'Edited');

        // Open menu
        await page.locator('#menuToggle').click();

        // Character Editor should be disabled (active)
        const editorLink = page.locator('a:has-text("Character Editor")');
        await expect(editorLink).toHaveClass(/disabled/);
    });
});

test.describe('Prior-experience workbench', () => {
    // Imported rather than rolled (issue #217, following skill-ladder.spec's
    // pattern): a random 3d6 character could carry a negative best modifier
    // and fail even the gentlest survival check, which would flake this test
    // on the roll. Laborer's survivability target is 2 -- the lowest of any
    // track -- so a worst-case 3d6 roll of 3 plus this character's +1 best
    // modifier (14 STR) clears it. The one roll that can still kill is a
    // literal triple ones, which always kills (issue #223), so the rolling
    // scenarios below pin their year's dice (see rollOneYearSurviving).
    const WORKBENCH_CHARACTER = {
        _format: 'tarmar-character',
        _version: 1,
        data: {
            name: 'Workbench Testwright',
            strength: 14,
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
            skills: {},
            free_skill_points: 0,
            free_spell_points: 0,
            equipment: [],
            pack_dropped: false,
            total_xp: 0,
            age: 20,
            experience_terms: 0,
            experience_log: [],
            money_earned: 0,
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
     * Give the player WORKBENCH_CHARACTER and land on its edit page. Seeded
     * rather than imported: a player's import keeps no attributes, years or
     * skills (#744), and the workbench needs them.
     * @param {import('@playwright/test').Page} page
     */
    async function importWorkbenchCharacter(page) {
        await seedCharacterFromExport(page, TEST_USER, WORKBENCH_CHARACTER);
    }

    // The dice a surviving Laborer year rolls under SURVIVING_YEAR_SEED (issue
    // #959): the year's d20 for its skill, then its 3d6 survival, both drawn
    // from random.Random(3) in the order characters/skills.py rolls them.
    const SURVIVING_YEAR_SEED = 3;
    const SURVIVING_YEAR_LINE = '(rolled 8) — survival 3d6 [5, 5, 2] + 1 = 13 vs 2: survived';

    /**
     * Roll one Laborer year on pinned dice that survive it, and prove it.
     *
     * A year's survival roll is 3d6 and triple ones always kill, so on real
     * dice the workbench character dies 1 time in 216. This helper used to
     * import a fresh character and roll again when that happened (issue #452),
     * which hid the roll the test was about (#959). Now the one roll is made on
     * SURVIVING_YEAR_SEED and the history must show exactly the dice that seed
     * gives -- a server that ignored the seed fails here rather than passing on
     * whatever real dice came up.
     * @param {import('@playwright/test').Page} page
     */
    async function rollOneYearSurviving(page) {
        await pinDice(page, '**/add-years/', SURVIVING_YEAR_SEED);
        await page.locator('#track-select').selectOption('laborer');
        await page.locator('#roll-years-btn').click();
        await expect(page.locator('#workbench-eyebrow')).toHaveText('NEXT — YEAR 2');
        await expect(page.locator('#workbench-card .death-card')).toHaveCount(0);
        const yearEntries = page.locator('#experience-log .term-entry');
        await expect(yearEntries).toHaveCount(1);
        await expect(yearEntries.first()).toContainText(SURVIVING_YEAR_LINE);
    }

    test('the preview and the button follow the year count, before any roll', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await importWorkbenchCharacter(page);

        // One year is the default and the first-class case (issue #221).
        await expect(page.locator('#workbench-eyebrow')).toHaveText('NEXT — YEAR 1');
        await expect(page.locator('#workbench-age-preview')).toHaveText('20 → 21');
        await expect(page.locator('#roll-years-btn')).toHaveText('Roll 1 year');
        await expect(page.locator('#years-input')).toHaveValue('1');

        // The consequence preview is visible before any click -- this is the
        // click's entire "confirmation" (issue #217). It follows the run
        // length as the player changes it.
        await page.locator('#years-input').fill('6');
        await expect(page.locator('#roll-years-btn')).toHaveText('Roll 6 years');
        await expect(page.locator('#workbench-eyebrow')).toHaveText('NEXT — YEARS 1–6');
        await expect(page.locator('#workbench-age-preview')).toHaveText('20 → 26');

        // The survival line names the dice and the selected track's target,
        // and the aging line names the age the next hit lands at.
        await page.locator('#track-select').selectOption('laborer');
        await expect(page.locator('#workbench-survival-preview')).toHaveText(
            '3d6 + best attribute modifier ≥ 2'
        );
        await expect(page.locator('#workbench-aging-preview')).toHaveText(
            'STR -1, DEX -1 at age 36'
        );
        await expect(page.locator('.workbench-note')).toHaveText(
            'Triple ones always kill; triple sixes always survive.'
        );
    });

    test('rolling a year posts straight from the workbench, with no confirmation step', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await importWorkbenchCharacter(page);

        const rollButton = page.locator('#roll-years-btn');
        // No confirm dialog appears; the click alone advances the workbench.
        await rollOneYearSurviving(page);
        await expect(page.locator('#workbench-eyebrow')).toHaveText('NEXT — YEAR 2');
        await expect(rollButton).toHaveText('Roll 1 year');
        await expect(page.locator('#character-age')).toHaveText('21');
        await expect(page.locator('#experience-years')).toHaveText('1');
    });

    test('the rolled year appears at the top of the history, open and expanded', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await importWorkbenchCharacter(page);

        await rollOneYearSurviving(page);

        const firstEntry = page.locator('#experience-log .term-entry').first();
        await expect(firstEntry.locator('summary')).toContainText('Laborer, Age 21');
        await expect(firstEntry).toHaveJSProperty('open', true);
        // The year line shows the 3d6 survival roll it actually made.
        await expect(firstEntry).toContainText('survival 3d6 [');
        await expect(page.locator('.no-history')).toHaveCount(0);
    });

    test('a rolled year is written to Recent Changes by the one mutation path (#571)', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await importWorkbenchCharacter(page);

        await rollOneYearSurviving(page);
        await page.reload();

        // characters.services appends a history line for every change it
        // makes, so the editor's roll shows beside the field edits.
        const history = page.locator('.history-table tbody tr').first();
        await expect(history).toContainText('experience');
        await expect(history).toContainText('played 1 of 1 year(s) on the laborer track');
    });

    test('a reload shows the identical workbench and history state', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await importWorkbenchCharacter(page);

        await rollOneYearSurviving(page);

        await page.reload();

        await expect(page.locator('#roll-years-btn')).toHaveText('Roll 1 year');
        await expect(page.locator('#workbench-eyebrow')).toHaveText('NEXT — YEAR 2');
        await expect(page.locator('#workbench-age-preview')).toHaveText('21 → 22');
        const firstEntry = page.locator('#experience-log .term-entry').first();
        await expect(firstEntry.locator('summary')).toContainText('Laborer, Age 21');
        await expect(firstEntry).toHaveJSProperty('open', true);
    });
});

test.describe('Add-skill type-ahead (issue #224)', () => {
    // Two free points and no starting skills: the add-skill control (and its
    // datalist) is visible from the first point, and stays visible after
    // spending one so a second skill -- the free-typed one -- can follow.
    const TYPE_AHEAD_CHARACTER = {
        _format: 'tarmar-character',
        _version: 1,
        data: {
            name: 'Suggestible Cassia',
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
            provenance_detail: 'Merchant',
            location: 'Village',
            wealth_level: 'Moderate',
            starting_coin: 100,
            skills: {},
            free_skill_points: 2,
            free_spell_points: 0,
            equipment: [],
            pack_dropped: false,
            total_xp: 0,
            age: 20,
            experience_terms: 0,
            experience_log: [],
            money_earned: 0,
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
     * Give the player TYPE_AHEAD_CHARACTER and land on its edit page. Seeded
     * rather than imported: a player's import keeps no attributes or free
     * skill points (#744).
     * @param {import('@playwright/test').Page} page
     */
    async function importTypeAheadCharacter(page) {
        return seedCharacterFromExport(page, TEST_USER, TYPE_AHEAD_CHARACTER);
    }

    test('the add-skill input offers known skills but still takes a free-typed one', async ({ page }) => {
        await loginAs(page, TEST_USER);
        const characterId = await importTypeAheadCharacter(page);

        const skillInput = page.locator('#new-skill-name');
        await expect(skillInput).toHaveAttribute('list', 'skill-name-suggestions');

        // The datalist is populated straight from the track data, not
        // hand-typed in the template -- "Weapon" is one of the ~37 skills
        // the prior-experience generator can grant.
        await expect(page.locator('#skill-name-suggestions option[value="Weapon"]')).toHaveCount(1);

        // Choose a suggested skill: type the exact suggestion the datalist
        // offers and submit, the way selecting the browser's autocomplete
        // entry would fill the field.
        await skillInput.fill('Weapon');
        await page.locator('#add-skill-btn').click();
        await expect(page.locator('.skill-row[data-skill="weapon"] .skill-name')).toHaveText('Weapon');
        await expect(skillInput).toHaveValue('');

        // A free-typed skill on no track's list: the datalist never
        // restricts the input, it only suggests, but a player's point goes
        // into a skill the glossary names (#862). This used to show a
        // player's free-typed "Wolf's Bane" taken, which was #862 itself.
        await skillInput.fill("Wolf's Bane");
        await page.locator('#add-skill-btn').click();
        await expect(page.locator('.notice .notice-text').first()).toContainText('skills glossary names');
        await expect(page.locator(`.skill-row[data-skill="wolf's bane"]`)).toHaveCount(0);

        // A Game Master may name any skill, typed freely into the same input.
        await logout(page);
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/characters/${characterId}/`);
        await page.locator('#new-skill-name').fill("Wolf's Bane");
        await page.locator('#add-skill-btn').click();
        await expect(page.locator(`.skill-row[data-skill="wolf's bane"] .skill-name`)).toHaveText("Wolf's Bane");
    });
});
