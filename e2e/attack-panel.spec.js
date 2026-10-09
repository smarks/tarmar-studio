/**
 * E2E test for the Attack Resolution panel (issue #101).
 *
 * The one thing neither the Jest suite nor the Django tests can prove on their
 * own: that a click in a real browser reaches the endpoint, that the numbers
 * painted into the page are the ones the server sent, and — the part the
 * project's no-transient-UI rule actually turns on — that the resolved attack
 * is still on the page after a reload, because it was persisted rather than
 * merely displayed.
 *
 * The character is written from an export file by the test, matching
 * skill-ladder.spec.js, so each run gets its own character. A player's import
 * no longer carries rolled attributes or equipment modifiers (#744), so the
 * file is seeded for the player rather than uploaded.
 */

const {
    test, expect, loginAs, seedCharacterFromExport, TEST_USER,
} = require('./fixtures');

// A saber (Striking, 2d6-2) behind a backpack, so the panel's weapon select
// has to filter the non-weapon out AND the surviving option's value has to be
// the saber's *equipment index* (1), not its position among the weapons (0).
const ATTACK_CHARACTER = {
    _format: 'tarmar-character',
    _version: 1,
    data: {
        name: 'Attack Testwright',
        strength: 14,
        dexterity: 16,
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
        // A Weapon skill tied to the saber, and Knowledge V beside it: only
        // the Weapon skill may add to the swing (issue #753).
        skills: { knowledge: 15, 'weapon (saber)': 10, 'maker skill': 1 },
        free_skill_points: 0,
        free_spell_points: 0,
        equipment: [
            { item_id: 'backpack', quantity: 1 },
            { item_id: 'saber', quantity: 1 },
        ],
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
 * Give the player ATTACK_CHARACTER and land on its edit page. Seeded rather
 * than imported: a player's import keeps no rolled or earned value (#744).
 * @param {import('@playwright/test').Page} page
 */
async function importAttackCharacter(page) {
    await seedCharacterFromExport(page, TEST_USER, ATTACK_CHARACTER);
}

/** The breakdown row whose label is `label`, as its value text. */
function breakdownValue(page, label) {
    return page
        .locator(`#attack-breakdown li:has(.attack-label:text-is("${label}")) .attack-value`)
        .first();
}

test.describe('Attack Resolution panel', () => {
    test('resolves an attack, shows the server breakdown, and keeps the log across a reload', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await importAttackCharacter(page);

        // The two numbers that were computed and exported but never displayed
        // before this feature.
        await expect(page.locator('#dex-combat-mod')).toBeVisible();
        await expect(page.locator('#dodge-modifier')).toBeVisible();

        // Only the catalog weapon is offered, and its value is its position in
        // the equipment list (1), not its position among the weapons (0).
        // toHaveValue reads a form control's value, so it goes on the <select>
        // (whose value is its selected option's) — an <option> is not an input
        // element and Playwright refuses it.
        const weaponSelect = page.locator('#attack-weapon');
        await expect(weaponSelect.locator('option')).toHaveCount(1);
        await expect(weaponSelect).toHaveValue('1');

        // Nothing has been rolled, so there is neither a result nor a log.
        await expect(page.locator('#attack-result')).toBeHidden();
        await expect(page.locator('.no-combat-log')).toBeVisible();

        // A defender the GM has described: medium armour, 3 stops, a large
        // shield, dodging 1, and the attacker is flanking for +2.
        // No skill select: the record decides which skill applies (#753).
        await expect(page.locator('#attack-skill')).toHaveCount(0);
        await page.locator('#attack-armour-tier').selectOption('Medium');
        await page.locator('#attack-stops').fill('3');
        await page.locator('#attack-shield').selectOption('large_shield');
        await page.locator('#attack-dodge').fill('1');
        await page.locator('#attack-situational').fill('2');
        await page.locator('#attack-resolve-btn').click();

        // The breakdown appears and carries the server's Target Number: the
        // §6 base for Striking vs Medium (16), the catalog's large-shield
        // bonus (2) and the dodge (1) — 19. The page never computed any of it.
        await expect(page.locator('#attack-result')).toBeVisible();
        await expect(breakdownValue(page, 'Target Number')).toHaveText('16 base + 2 shield + 1 dodge = 19');
        await expect(breakdownValue(page, 'To hit')).toContainText('+2 situational');
        await expect(breakdownValue(page, 'Skill')).toHaveText(
            'weapon (saber) level 4 (full: the weapon it was taken for)'
        );
        await expect(breakdownValue(page, 'To hit')).toContainText('+4 skill');
        await expect(breakdownValue(page, 'Attack roll')).toContainText('vs 19');
        await expect(page.locator('#attack-outcome')).not.toBeEmpty();
        await expect(page.locator('#attack-dice-log li').first()).toContainText('vs TN 19');

        // The roll's own outcome is whatever the die gave; what matters is
        // that the same outcome is what got written to the log.
        const outcome = (await page.locator('#attack-outcome').textContent()).trim();

        // The line joins the log on this page (#792), and survives a reload
        // because it was persisted. The empty line is hidden, not removed:
        // this asserted toHaveCount(0) after the reload, which held only
        // because the log was server-rendered once and never repainted.
        await expect(page.locator('.combat-log-entry').first()).toContainText('Saber');
        await expect(page.locator('.no-combat-log')).toBeHidden();
        await page.reload();
        await expect(page.locator('.no-combat-log')).toBeHidden();
        const entry = page.locator('.combat-log-entry').first();
        await expect(entry.locator('.combat-log-weapon')).toHaveText('Saber');
        await expect(entry.locator('.combat-log-outcome')).toHaveText(outcome);
        await expect(entry.locator('.combat-log-roll')).toContainText('vs TN 19');

        // The result panel itself is this page's copy of the last response, so
        // a fresh load starts empty again while the log stays.
        await expect(page.locator('#attack-result')).toBeHidden();

        // A second attack appends rather than replacing.
        await page.locator('#attack-resolve-btn').click();
        await expect(page.locator('#attack-result')).toBeVisible();
        await page.reload();
        await expect(page.locator('.combat-log-entry')).toHaveCount(2);
    });

    test('a bad defender number is refused by the server and nothing is rendered', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await importAttackCharacter(page);

        // The input's max attribute is advisory; fill() bypasses it, which is
        // exactly what a hand-rolled request would do. The server decides.
        await page.locator('#attack-dodge').fill('99');
        await page.locator('#attack-resolve-btn').click();

        await expect(page.locator('#attack-result')).toBeHidden();
        // The notice is state-driven and stays until dismissed (notify.js).
        await expect(page.locator('.notice .notice-text').first()).toContainText('dodge');
    });

    test('equipment edits refresh the weapon list and show saved or refused states (#339 items 22, 24)', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await importAttackCharacter(page);
        const weaponSelect = page.locator('#attack-weapon');
        await expect(weaponSelect.locator('option')).toHaveCount(1);

        // Item 24: adding a catalog weapon extends the select with no reload.
        await page.locator('#equipment-category').selectOption('weapons');
        await page.locator('#equipment-item').selectOption('dagger');
        await page.locator('#add-equipment-btn').click();
        await expect(weaponSelect.locator('option')).toHaveCount(2);
        await expect(weaponSelect.locator('option[data-item-id="dagger"]')).toHaveText('Dagger (Piercing, 1d6-1)');

        // Item 22: a row field that saves says so on the field itself.
        const backpackRow = page.locator('.equipment-row', { hasText: 'Backpack' });
        await backpackRow.locator('input[data-field="quantity"]').fill('2');
        await backpackRow.locator('input[data-field="quantity"]').press('Tab');
        await expect(page.locator('.equipment-row', { hasText: 'Backpack' }).locator('.item-quantity')).toHaveClass(/saved/);

        // A refused value is explained beside the field and put back.
        const refusedRow = page.locator('.equipment-row', { hasText: 'Backpack' });
        await refusedRow.locator('input[data-field="quantity"]').fill('0');
        await refusedRow.locator('input[data-field="quantity"]').press('Tab');
        const quantityField = refusedRow.locator('.item-quantity');
        await expect(quantityField).toHaveClass(/error/);
        await expect(quantityField.locator('.autosave-error')).toHaveText('Quantity must be at least 1');
        await expect(refusedRow.locator('input[data-field="quantity"]')).toHaveValue('2');

        // A skill rename that saves marks the name saved. A rename a player
        // may make (issue #768): naming the Maker Skill's craft. "Blade" moved
        // Knowledge's points into another skill, which only a Game Master may.
        const skillName = page.locator('.skill-row .skill-name', { hasText: 'Maker Skill' });
        await skillName.click();
        await skillName.fill('Maker Skill (Smith)');
        await skillName.press('Enter');
        await expect(page.locator('.skill-row .skill-name.saved')).toHaveText(/^Maker Skill \(smith\)$/i);

        // Removing the saber takes it out of the select, again without a reload.
        const saberRow = page.locator('.equipment-row', { hasText: 'Saber' });
        await saberRow.locator('.remove-btn').click();
        await page.locator('.notice-confirm button:has-text("Remove")').click();
        await expect(weaponSelect.locator('option')).toHaveCount(1);
        await expect(weaponSelect.locator('option')).toHaveAttribute('data-item-id', 'dagger');
    });
});
