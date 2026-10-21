/**
 * E2E for an internal failure under the character sheet (issue #139).
 *
 * The battle endpoints answered a server failure in the fixed sentence since
 * #113; the sheet's endpoints answered Django's HTML 500, so the sheet could
 * only say that the roll failed. They answer the same sentence now, keeping
 * the error's own text in the log.
 */

const { refusal } = require('./refusals');
const { test, expect, loginAs, TEST_USER } = require('./fixtures');
const { readDjangoShell } = require('./django');

/** A character of the test player's with everything but the Fatigue roll. */
function seedUnrolledCharacter(name) {
    const output = readDjangoShell(`
from django.contrib.auth import get_user_model
from characters.models import Character
user = get_user_model().objects.get(username=${JSON.stringify(TEST_USER.username)})
character = Character.objects.create(
    user=user, name=${JSON.stringify(name)}, equipment=[], fatigue_roll=None,
    strength=12, dexterity=12, intelligence=12, wisdom=12, constitution=12, charisma=12,
)
print('CHARACTER', character.pk)
`);
    const match = output.match(/CHARACTER (\d+)/);
    expect(match, `no character in: ${output}`).not.toBeNull();
    return match[1];
}

/** Store a history the server cannot append to: a number, not a list. */
function breakHistory(characterId) {
    readDjangoShell(`
from characters.models import Character
Character.objects.filter(pk=${characterId}).update(history=7)
`);
}

/** The Fatigue roll the record holds, as Python prints it. */
function storedFatigueRoll(characterId) {
    const output = readDjangoShell(`
from characters.models import Character
print('ROLL', Character.objects.get(pk=${characterId}).fatigue_roll)
`);
    const match = output.match(/ROLL (\S+)/);
    expect(match, `no roll in: ${output}`).not.toBeNull();
    return match[1];
}

test.describe('Character sheet — an internal failure under a roll', () => {
    test('is reported in the fixed sentence, not as a bare failure (#139)', async ({ page }, testInfo) => {
        const name = `E2E Failing ${testInfo.workerIndex}x${Date.now() % 100000}`;
        const characterId = seedUnrolledCharacter(name);
        const message = refusal('common', 'decorators', 'request_failed');
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/characters/${characterId}/`);
        await expect(page.locator('#roll-fatigue-btn')).toBeVisible();

        breakHistory(characterId);
        const answered = page.waitForResponse((response) => (
            response.url().endsWith(`/tarmar/characters/${characterId}/roll-fatigue/`)
            && response.request().method() === 'POST'
        ));
        await page.locator('#roll-fatigue-btn').click();
        const response = await answered;

        expect(response.status()).toBe(500);
        expect(response.headers()['content-type']).toContain('application/json');
        expect((await response.json()).error).toBe(message);
        await expect(page.locator('#notice-region .notice-text', { hasText: message })).toBeVisible();
        // Nothing was rolled: the failure rolled the change back, so the
        // record holds no roll.
        expect(storedFatigueRoll(characterId)).toBe('None');
    });
});
