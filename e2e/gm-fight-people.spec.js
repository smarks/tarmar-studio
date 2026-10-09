/**
 * A person named as a foe comes onto the board as a person (issue #787).
 *
 * "/fight a bandit" used to build "bandit — beast — Kick" with 51 Fatigue: the
 * bestiary holds no people, so the bandit was a beast stand-in with a beast's
 * attack and no weapon. A Game Master's /fight names the foes outright, and
 * two bandits are now generated fighters with catalog weapons, called Bandit
 * and Bandit (2) on the board, while the gray wolf beside them is the
 * catalog's beast.
 */

const { test, expect, TEST_ADMIN, TEST_USER } = require('./fixtures');
const { runDjangoShell } = require('./django');

/**
 * A complete character for this worker's Game Master (or `owner`) to take
 * to the fight.
 * @param {string} name
 * @param {{username: string}} [owner]
 */
function seedCharacter(name, owner = TEST_ADMIN) {
    runDjangoShell(`
from django.contrib.auth import get_user_model
from characters.models import Character

owner = get_user_model().objects.get(username=${JSON.stringify(owner.username)})
Character.objects.create(
    user=owner, name=${JSON.stringify(name)},
    strength=12, dexterity=12, intelligence=12, wisdom=12, constitution=12,
    charisma=12, appearance_roll='3', height_roll='3', weight_roll='3',
    fatigue_roll=7,
)
`);
}

test.describe('GM fight — people among the foes', () => {
    test('bandits are armed people on the board, not beasts', async ({ adminPage: page }, testInfo) => {
        const name = `Brannoc ${testInfo.workerIndex}-${Date.now() % 100000}`;
        seedCharacter(name);
        await page.goto('/tarmar/gm/');
        await page.selectOption('#gm-character-select', { label: name });
        await page.click('#gm-character-attach');
        await page.waitForURL(/\/tarmar\/gm\/\?session=\d+$/);

        await page.fill('#user-input', '/fight two bandits, a gray wolf');
        await page.press('#user-input', 'Enter');
        await expect(page.locator('#gm-battle')).toBeVisible();

        await expect(page.locator('#battle-arena .arena-combatant')).toHaveCount(4);
        const roster = page.locator('#battle-roster');
        for (const bandit of ['Bandit', 'Bandit (2)']) {
            const card = roster.locator('.combatant-card', { hasText: bandit }).first();
            await expect(card).toContainText(/unarmoured duelist — (Saber|Rapier|Dagger)/);
            await expect(card).not.toContainText('beast');
        }
        await expect(roster.locator('.combatant-card', { hasText: 'Gray Wolf' }))
            .toContainText('beast');
        await expect(page.locator('.message.assistant').last())
            .toContainText('2 × Bandit (a person: unarmoured duelist)');
    });

    test("a player's fight can face people the Game Master sets", async ({ authenticatedPage: page }, testInfo) => {
        // Issue #894: a player's /fight is answered by the model's set_foes,
        // which could name only catalog beasts, so a player never faced a
        // person. The fake Game Master answers "two bandits" by calling.
        const name = `Wren ${testInfo.workerIndex}-${Date.now() % 100000}`;
        seedCharacter(name, TEST_USER);
        await page.goto('/tarmar/gm/');
        await page.selectOption('#gm-character-select', { label: name });
        await page.click('#gm-character-attach');
        await page.waitForURL(/\/tarmar\/gm\/\?session=\d+$/);

        await page.fill('#user-input', '/fight two bandits');
        await page.press('#user-input', 'Enter');
        await expect(page.locator('#gm-battle')).toBeVisible();

        await expect(page.locator('#battle-arena .arena-combatant')).toHaveCount(3);
        const roster = page.locator('#battle-roster');
        for (const bandit of ['Bandit', 'Bandit (2)']) {
            const card = roster.locator('.combatant-card', { hasText: bandit }).first();
            await expect(card).toContainText(/unarmoured duelist — (Saber|Rapier|Dagger)/);
            await expect(card).not.toContainText('beast');
        }
        const opening = page.locator('.message.assistant').last();
        await expect(opening).toContainText('2 × Bandit (a person: unarmoured duelist)');
        await expect(opening).toContainText('the Game Master set the foes');
    });
});
