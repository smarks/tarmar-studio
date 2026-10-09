/**
 * The sheet's "DEX Combat Mod" and "Dodge" tiles stay in step (issue #634).
 *
 * Both derive from combat DEX. The template rendered them once and nothing on
 * the page ever wrote them again, so equipping armour left "Armor DEX Penalty
 * -4" beside a "DEX Combat Mod" computed without it — and the Attack
 * Resolution panel below, which asks the server, then disagreed with the tile
 * the player had just read. Only a real browser can show that the numbers move
 * without a reload.
 */

const { test, expect, loginAs, createCharacter, TEST_ADMIN } = require('./fixtures');

/**
 * Add a catalog item through the sheet's own picker and wait for the repaint.
 * @param {import('@playwright/test').Page} page
 * @param {string} category - The catalog group, e.g. "armor".
 * @param {string} itemId - The catalog item id, e.g. "chainmail".
 */
async function addEquipment(page, category, itemId) {
    await page.selectOption('#equipment-category', category);
    await page.selectOption('#equipment-item', itemId);
    // Catalog gear is bought (issue #757) and a rolled character's purse may
    // not cover chainmail; this spec is about the tiles, so a Game Master
    // gives the armour.
    await page.locator('#add-free').check();
    await page.locator('#add-equipment-btn').click();
    await expect(page.locator('.equipment-row')).toHaveCount(1);
}

test.describe('Combat DEX tiles (#634)', () => {
    test('equipping armour moves aim and dodge with the armour penalty', async ({ page }) => {
        // A Game Master types the DEX below; a player's is rolled (#748).
        await loginAs(page, TEST_ADMIN);
        await createCharacter(page);

        // A DEX the tiles can actually move from: 16 gives +3 aim and +3 dodge,
        // and chainmail's penalty has room to drag both down.
        await page.fill('[data-field="dexterity"] input', '16');
        await page.locator('[data-field="dexterity"] input').blur();
        await expect(page.locator('#dex-combat-mod')).toHaveText('+3');
        await expect(page.locator('#dodge-modifier')).toHaveText('+3');

        await addEquipment(page, 'armor', 'chainmail');
        // Only equipped armour drags DEX down, so wear it.
        await page.locator('.equipment-row [data-field="equipped"]').check();
        await expect(page.locator('#armor-dex-penalty')).not.toHaveText('0');

        const penalty = Number(await page.locator('#armor-dex-penalty').textContent());
        expect(penalty).toBeLessThan(0);
        // No reload: these are the live numbers, and they agree with the
        // penalty tile beside them.
        await expect(page.locator('#dex-combat-mod')).not.toHaveText('+3');
        await expect(page.locator('#dodge-modifier')).not.toHaveText('+3');

        const aimAfterArmour = await page.locator('#dex-combat-mod').textContent();
        const dodgeAfterArmour = await page.locator('#dodge-modifier').textContent();
        await page.reload();
        await expect(page.locator('#dex-combat-mod')).toHaveText(aimAfterArmour);
        await expect(page.locator('#dodge-modifier')).toHaveText(dodgeAfterArmour);
    });
});
