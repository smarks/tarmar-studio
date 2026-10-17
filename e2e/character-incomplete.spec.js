/**
 * E2E tests for characters that are not complete yet (issue #571).
 *
 * A character built in a Games Master session is a record from its first
 * change, before its attributes and Fatigue roll are known. It shows in the
 * character list and on its sheet with a mark saying what it needs, and it
 * cannot be exported until it is complete.
 */

const { test, expect, loginAs, TEST_USER, TEST_ADMIN } = require('./fixtures');
const { readDjangoShell } = require('./django');
const { loadedEquipment } = require('./equipment-rules');

/**
 * Seed an incomplete character.
 * @param {string} name
 * @param {{equipment?: string, fields?: string}} [options] - Both are Python
 *   source, written with single quotes: the script reaches Django through
 *   `manage.py shell -c "..."`, where a double quote would close the shell's
 *   own string. `fields` replaces the default "nothing recorded" attributes.
 */
function seedIncompleteCharacter(name, {
    equipment = '[]',
    fields = 'strength=None, dexterity=None, intelligence=None, wisdom=None, '
        + 'constitution=None, charisma=None, fatigue_roll=None',
} = {}) {
    const output = readDjangoShell(`
from django.contrib.auth import get_user_model
from characters.models import Character
user = get_user_model().objects.get(username='${TEST_USER.username}')
fields = dict(${fields}, equipment=${equipment})
print('CHARACTER', Character.objects.create(user=user, name='${name}', **fields).pk)
`.trim().replace(/\n/g, '; '));
    const match = output.match(/CHARACTER (\d+)/);
    expect(match, `no character id in: ${output}`).not.toBeNull();
    return match[1];
}

test.describe('Incomplete characters (#571)', () => {
    test('the list and the sheet mark what the character still needs', async ({ page }, testInfo) => {
        const name = `Half Built ${testInfo.workerIndex}${Date.now()}`;
        const characterId = seedIncompleteCharacter(name);

        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/characters/');
        const row = page.locator(`tr[data-id="${characterId}"]`);
        await expect(row).toContainText('needs attributes');
        await expect(row).toContainText('needs Fatigue roll');
        await expect(row.locator('td[data-label="STR"]')).toHaveText('—');
        await expect(row.getByRole('link', { name: 'PDF' })).toHaveCount(0);

        await row.getByRole('link', { name }).click();
        await expect(page.locator('#incomplete-marks')).toContainText('needs attributes');
        await expect(page.locator('#fatigue')).toHaveText('—');
        await expect(page.getByRole('link', { name: 'Export PDF' })).toHaveCount(0);

        const refused = await page.request.get(`/tarmar/export/${characterId}/json/`);
        expect(refused.status()).toBe(409);
    });

    test('the sheet states no combat or movement number, and offers no attack form (#630)', async ({ page }, testInfo) => {
        // A dagger in hand: the attack panel's form used to render, and the
        // endpoint behind it used to resolve the swing, off a DEX of 0.
        const name = `Bare Handed ${testInfo.workerIndex}${Date.now()}`;
        const characterId = seedIncompleteCharacter(name, {
            equipment: "[{'item_id': 'dagger', 'quantity': 1, 'in_pack': False}]",
        });

        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/characters/${characterId}/`);

        await expect(page.locator('#dex-combat-mod')).toHaveText('—');
        await expect(page.locator('#dodge-modifier')).toHaveText('—');
        await expect(page.locator('#movement-modifier')).toHaveText('—');
        await expect(page.locator('#max-unencumbered')).toHaveText('—');
        await expect(page.locator('#encumbrance-level')).toHaveText('—');
        for (const gait of loadedEquipment().gait_order) {
            await expect(page.locator(`#gait-${gait} .gait-value`)).toHaveText('—');
        }

        // Hidden, not left out: this asserted toHaveCount(0), which was issue
        // #764 -- completing the character could not show the form without a
        // reload.
        await expect(page.locator('#attack-form')).toBeHidden();
        await expect(page.locator('#attack-incomplete')).toContainText('needs attributes');
    });

    test('the Fatigue roll can be made from the editor (#636)', async ({ page }, testInfo) => {
        // The attributes are in and the roll is not: the sheet named that
        // blocking state and offered no control for it.
        const name = `Unrolled ${testInfo.workerIndex}${Date.now()}`;
        const characterId = seedIncompleteCharacter(name, {
            fields: 'strength=12, dexterity=12, intelligence=12, wisdom=12, '
                + 'constitution=12, charisma=12, fatigue_roll=None',
        });

        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/characters/${characterId}/`);
        await expect(page.locator('#fatigue-roll')).toHaveText('—');
        await expect(page.locator('#fatigue')).toHaveText('—');

        await page.locator('#roll-fatigue-btn').click();

        // The server rolled the dice; the page only wrote them out.
        await expect(page.locator('#fatigue-roll')).not.toHaveText('—');
        await expect(page.locator('#fatigue')).not.toHaveText('—');
        await expect(page.locator('#body')).not.toHaveText('—');
        // One roll per player (#590): the control is gone, not merely refused.
        await expect(page.locator('#roll-fatigue-btn')).toHaveCount(0);

        const rolled = await page.locator('#fatigue-roll').textContent();
        await page.reload();
        await expect(page.locator('#fatigue-roll')).toHaveText(rolled);
    });

    test('the injury readout says it is not known yet, and fills in when the last part is recorded (#639)', async ({ page }, testInfo) => {
        // One attribute short of whole, and it is CON, which the pools and so
        // the breakpoints are built from: a single edit crosses the line.
        const name = `Unhurt ${testInfo.workerIndex}${Date.now()}`;
        const characterId = seedIncompleteCharacter(name, {
            fields: 'strength=12, dexterity=12, intelligence=12, wisdom=12, '
                + 'constitution=None, charisma=12, fatigue_roll=7',
        });

        // A Game Master records the missing score; a player's is rolled (#748).
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/characters/${characterId}/`);

        // It used to be a grid of blank cells and "Survival save is 3d6 ≤"
        // with nothing after the sign.
        await expect(page.locator('.injury-table')).toHaveCount(0);
        await expect(page.locator('#injury-unknown')).toContainText('Recorded once');

        await page.fill('[data-field="constitution"] input', '12');
        await page.locator('[data-field="constitution"] input').blur();
        await expect(page.locator('[data-field="constitution"]')).toHaveClass(/saved/);

        // No reload: the table replaces the sentence the moment the record is whole.
        await expect(page.locator('#injury-unknown')).toHaveCount(0);
        await expect(page.locator('#injury-fatigue-minus_two_at')).toHaveText('5');
    });
});
