/**
 * The sheet catches up with a run of experience years (issue #638).
 *
 * The add-years response has carried strength, dexterity and constitution
 * since #163 and updateSkillsUI read none of them, while physical and mental
 * were not in the response at all. So a run that crossed an aging stage left
 * the Core Attributes inputs and both derived readouts showing the character
 * from before the run, beside a Fatigue and Body that had already moved. Only
 * a real browser shows that; the page never reloads here.
 */

const { test, expect, loginAs, TEST_USER } = require('./fixtures');
const { readDjangoShell } = require('./django');

// The aging stage at 68 is the first to take a point off INT and WIS, and a
// run can only ever grant +1 to STR, DEX or CON (skills.ATTRIBUTE_BONUS_FIELDS),
// so nothing the dice do can offset it: Mental moves by exactly one. Crossing
// 36 instead would have been offset by a granted +1 STR or +1 DEX, which is how
// this test first flaked. Aging is a pure function of age, so even the year
// that kills a character still moves it.
const AGE_BEFORE_AGING = 67;

function seedCharacterOnTheEdgeOfAging(name) {
    const output = readDjangoShell(`
from django.contrib.auth import get_user_model
from characters.models import Character
user = get_user_model().objects.get(username='${TEST_USER.username}')
character = Character.objects.create(user=user, name='${name}', strength=14, dexterity=14, intelligence=12, wisdom=12, constitution=12, charisma=12, fatigue_roll=7, age=${AGE_BEFORE_AGING})
print('CHARACTER', character.pk)
`.trim().replace(/\n/g, '; '));
    const match = output.match(/CHARACTER (\d+)/);
    expect(match, `no character id in: ${output}`).not.toBeNull();
    return match[1];
}

test.describe('A run of experience years (#638)', () => {
    test('leaves no attribute or derived cell showing the pre-run character', async ({ page }, testInfo) => {
        const name = `Greying ${testInfo.workerIndex}${Date.now()}`;
        const characterId = seedCharacterOnTheEdgeOfAging(name);

        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/characters/${characterId}/`);
        const mentalBefore = await page.locator('#mental').textContent();

        await page.selectOption('#track-select', 'laborer');
        await page.fill('#years-input', '1');
        await page.locator('#roll-years-btn').click();
        await expect(page.locator('#character-age')).toHaveText(String(AGE_BEFORE_AGING + 1));

        // Aging took a point off INT and WIS, so Mental moved with them.
        await expect(page.locator('#mental')).not.toHaveText(mentalBefore);

        // Whatever the run did, the page now says what a reload says. A
        // player's attributes are text, repainted from the run (issue #748).
        const live = {
            strength: await page.locator('[data-readonly-field="strength"]').textContent(),
            dexterity: await page.locator('[data-readonly-field="dexterity"]').textContent(),
            constitution: await page.locator('[data-readonly-field="constitution"]').textContent(),
            physical: await page.locator('#physical').textContent(),
            mental: await page.locator('#mental').textContent(),
        };
        await page.reload();
        await expect(page.locator('[data-readonly-field="strength"]')).toHaveText(live.strength);
        await expect(page.locator('[data-readonly-field="dexterity"]')).toHaveText(live.dexterity);
        await expect(page.locator('[data-readonly-field="constitution"]')).toHaveText(live.constitution);
        await expect(page.locator('#physical')).toHaveText(live.physical);
        await expect(page.locator('#mental')).toHaveText(live.mental);
    });
});
