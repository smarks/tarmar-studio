/**
 * A beast the catalog gives no rollable damage for says so (#645).
 *
 * 53 of the 239 shipped beasts quietly fell back to bare-handed damage and
 * the default weapon class. Nothing on screen said it: the muster row
 * printed "<category> — ST N", the roster card printed "beast — tooth and
 * claw", and a DM who mustered a Troll got a figure that did not fight like
 * the Troll on the catalog page. Three of those turned out to be parsing
 * faults; the rest are real gaps in the source, and for those the rule is
 * that the substitution is visible, not that it stops happening.
 *
 * This walks the two surfaces a DM actually reads — the muster they build
 * the fight on, and the roster card they watch it from — for one beast that
 * keeps its catalog attack and one that does not.
 */

const { test, expect } = require('./fixtures');
const { uniqueName } = require('./unique-name');

// Shipped catalog rows. The snapping turtle's source entry carries no
// attacks at all; the dire wolf's carries a bite of 1d6+1.
const SUBSTITUTED_BEAST = 'monstrous-snapping-turtle';
const MODELLED_BEAST = 'dire-wolf';
const NOTE = 'no modelled attack; fights bare-handed';

async function musterBeasts(page, slugs) {
    await page.goto('/tarmar/battles/');
    await page.click('.battle-list-masthead button[type="submit"]');
    await page.waitForURL(/\/tarmar\/battles\/setup\/\d+\/$/);
    const setupUrl = page.url();
    for (const slug of slugs) {
        const beastForm = page.locator('.setup-beast-form').first();
        await beastForm.locator('select[name="slug"]').selectOption(slug);
        await beastForm.locator('button').click();
        await page.waitForURL(setupUrl);
    }
    return setupUrl;
}

test.describe('A beast with no modelled attack', () => {
    test('says so on the muster and on its roster card', async ({ adminPage }) => {
        test.setTimeout(120000);
        await musterBeasts(adminPage, [SUBSTITUTED_BEAST, MODELLED_BEAST]);

        // The muster: the beast the catalog does not model is flagged, the
        // one it does is not.
        const substituted = adminPage.locator('.muster-entry', {
            hasText: 'Monstrous Snapping Turtle',
        });
        await expect(substituted.locator('.muster-note')).toContainText('ST 70');
        await expect(substituted.locator('.muster-note')).toContainText(NOTE);
        const modelled = adminPage.locator('.muster-entry', { hasText: 'Dire Wolf' });
        await expect(modelled.locator('.muster-note')).not.toContainText(NOTE);

        // March, and read the same two figures off the arena's roster.
        await adminPage.fill('input[name="name"]', uniqueName('E2E Beast attack'));
        await adminPage.locator('input[name="name"]').blur();
        await adminPage.click('.setup-begin');
        await adminPage.waitForURL(/\/tarmar\/battles\/\d+\/$/);

        const turtleCard = adminPage.locator('.combatant-card', {
            hasText: 'Monstrous Snapping Turtle',
        });
        await expect(turtleCard.locator('.combatant-status')).toContainText(NOTE);
        await expect(turtleCard.locator('.combatant-status')).toContainText('ST 70');
        const wolfCard = adminPage.locator('.combatant-card', { hasText: 'Dire Wolf' });
        await expect(wolfCard.locator('.combatant-status')).toContainText('Bite');
        await expect(wolfCard.locator('.combatant-status')).not.toContainText(NOTE);

        // And the attribute panel behind the card carries the class the
        // catalog named, which is the half of the bug the dice hide: a
        // Piercing bite turned Striking changes the matrix row and how
        // armour stops apply, not just the damage.
        await wolfCard.locator('.combatant-detail summary').click();
        await expect(wolfCard.locator('.combatant-detail')).toContainText('Piercing');
    });
});
