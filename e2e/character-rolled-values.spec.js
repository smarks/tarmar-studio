/**
 * Rolled and awarded values on the character sheet (issues #748, #749, #752,
 * #948).
 *
 * The sheet's auto-save let a player type over anything rolled or awarded:
 * the Fatigue roll after the roll button had refused a second one, any
 * attribute from 1 to 30, XP and level, coin, appearance (#748); choose
 * Nobility, Merchant or Rich, rerolling a Merchant's coin by re-selecting it
 * (#749); and give their own gear +10 to hit, damage and stops (#752).
 *
 * Spencer's standing rule (2026-09-28): players are held to the rules
 * exactly, a Game Master may override. On #749: players roll provenance and
 * wealth and cannot choose them; the coin is rolled once when wealth is first
 * set and never rerolled by re-selecting; a Game Master may set either and
 * reroll the coin explicitly.
 */

const {
    test, expect, loginAs, logout, createCharacter, TEST_USER, TEST_ADMIN,
} = require('./fixtures');
const { runDjangoShell, readDjangoShell } = require('./django');
const { loadedCharacterRules } = require('./character-rules');
const { refusal } = require('./refusals');

/** The note that says once why a recorded attribute is locked (#84). */
const ATTRIBUTE_LOCKED_NOTE_ID = 'attribute-locked-reason';
const ATTRIBUTE_LOCKED_NOTE = `#${ATTRIBUTE_LOCKED_NOTE_ID}`;

/** The character id from the editor's URL. */
function characterId(page) {
    return Number(page.url().match(/\/characters\/(\d+)\//)[1]);
}

/**
 * POST a field update the way a hand-built request would, from the page.
 * @param {import('@playwright/test').Page} page
 * @param {number} id
 * @param {string} field
 * @param {*} value
 */
async function postFieldUpdate(page, id, field, value) {
    return page.evaluate(async ({ id, field, value }) => {
        const token = document.querySelector('meta[name="csrf-token"]').content;
        const response = await fetch(`/tarmar/characters/${id}/update-field/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
            body: JSON.stringify({ field, value }),
        });
        return { status: response.status, body: await response.json() };
    }, { id, field, value });
}

test.describe('Rolled and awarded values (#748, #749, #752)', () => {
    test('a player sees rolled values read-only, with why, and the server agrees', async ({ page }) => {
        // The reasons state the loaded canon's dice and point buy (#68).
        const rules = loadedCharacterRules();
        await loginAs(page, TEST_USER);
        await createCharacter(page);
        const id = characterId(page);

        for (const field of ['strength', 'intelligence', 'appearance_roll', 'provenance_class',
            'provenance_detail', 'wealth_level', 'starting_coin', 'total_xp']) {
            await expect(page.locator(`[data-field="${field}"]`), field).toHaveCount(0);
            await expect(page.locator(`[data-locked-field="${field}"]`), field).toBeVisible();
        }
        // The six attributes share one reason, so their table says it once,
        // under it, and each locked cell points to it (#84): the sentence was
        // printed in all six cells, and the table did not fit a phone.
        const attributeReason = refusal('characters', 'services', 'field_attribute', {
            dice: rules.attribute_dice, points: rules.point_buy.total,
        });
        await expect(page.locator(ATTRIBUTE_LOCKED_NOTE)).toHaveText(attributeReason);
        await expect(page.locator(ATTRIBUTE_LOCKED_NOTE)).toBeVisible();
        await expect(page.getByText(attributeReason, { exact: true })).toHaveCount(1);
        await expect(page.locator(`[data-locked-field][aria-describedby="${ATTRIBUTE_LOCKED_NOTE_ID}"]`))
            .toHaveCount(6);
        // What the rules let a player choose stays an input.
        await expect(page.locator('[data-field="name"]')).toHaveCount(1);
        // The Origin is "roll or choose", and creation rolled it: a roll is
        // final for a player (Spencer's ruling on #970). This asserted an
        // input before, which was the bug.
        await expect(page.locator('[data-field="location"]')).toHaveCount(0);
        await expect(page.locator('[data-locked-field="location"]')).toHaveText(
            refusal('characters', 'services', 'roll_fixed_origin')
        );
        // Height and weight are "choose or roll", and creation rolled them:
        // a roll is final for a player (Spencer's ruling on #948). This
        // asserted an input before, which was the bug.
        await expect(page.locator('[data-locked-field="height_roll"]')).toHaveText(
            refusal('characters', 'services', 'roll_fixed_height')
        );
        await expect(page.locator('[data-field="weight_roll"]')).toHaveCount(0);
        await expect(page.locator('[data-locked-field="weight_roll"]')).toBeVisible();
        const height = await postFieldUpdate(page, id, 'height_roll', '6');
        expect(height).toEqual({
            status: 400,
            body: { error: refusal('characters', 'services', 'roll_fixed_height') },
        });
        // The Fatigue roll was made at creation: no second roll offered.
        await expect(page.locator('#roll-fatigue-btn')).toHaveCount(0);

        const fatigue = await postFieldUpdate(page, id, 'fatigue_roll', 12);
        const strength = await postFieldUpdate(page, id, 'strength', 30);
        const coin = await postFieldUpdate(page, id, 'starting_coin', 1000000);
        expect(fatigue).toEqual({
            status: 400,
            body: {
                error: refusal('characters', 'services', 'field_fatigue_roll', { dice: rules.fatigue.dice }),
            },
        });
        expect(strength.status).toBe(400);
        expect(coin.status).toBe(400);
        const stored = readDjangoShell(
            `from characters.models import Character; c = Character.objects.get(pk=${id}); `
            + 'print(c.strength <= 18, c.starting_coin != 1000000)'
        );
        expect(stored.trim().split('\n').pop()).toBe('True True');
    });

    test('a player rolls wealth once; a Game Master re-selects without rerolling the coin', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await createCharacter(page);
        const id = characterId(page);
        // A character nobody rolled a background for, as one started in a
        // Games Master session is.
        runDjangoShell(
            'from characters.models import Character; '
            + `Character.objects.filter(pk=${id}).update(provenance_recorded=False, `
            + 'wealth_recorded=False, wealth_level="Moderate", starting_coin=100)'
        );
        await page.reload();

        await page.locator('#roll-wealth-btn').click();
        await expect(page.locator('#roll-wealth-btn')).toHaveCount(0);
        const coin = await page.locator('[data-readonly-field="starting_coin"]').textContent();
        await expect(page.locator('#dice-log')).toContainText('Wealth: d100=');
        await page.locator('#roll-provenance-btn').click();
        await expect(page.locator('#roll-provenance-btn')).toHaveCount(0);

        await logout(page);
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/characters/${id}/`);
        const wealth = page.locator('[data-field="wealth_level"] select');
        for (const level of ['Subsistence', 'Merchant', 'Moderate', 'Merchant']) {
            if (await wealth.inputValue() === level) continue;
            await wealth.selectOption(level);
            await expect(page.locator('[data-field="wealth_level"]')).toHaveClass(/saved/);
        }
        await page.reload();
        const kept = await page.locator('[data-field="starting_coin"] input').inputValue();
        expect(kept === '' ? 'Consult GM' : kept).toBe(coin.trim());
    });

    test('a player rolls the missing scores and the appearance, once each', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await createCharacter(page);
        const id = characterId(page);
        // A character started in a Games Master session: scores and
        // appearance not recorded yet (#571).
        runDjangoShell(
            'from characters.models import Character; '
            + `Character.objects.filter(pk=${id}).update(strength=None, dexterity=None, `
            + 'intelligence=None, wisdom=None, constitution=None, charisma=None, appearance_roll="")'
        );
        await page.reload();
        await expect(page.locator('#point-buy-note')).toBeVisible();

        await page.locator('#roll-attributes-btn').click();
        await expect(page.locator('#roll-attributes-btn')).toHaveCount(0);
        for (const field of ['strength', 'dexterity', 'charisma']) {
            await expect(page.locator(`[data-readonly-field="${field}"]`), field).toHaveText(/^\d+$/);
        }
        await expect(page.locator('#point-buy-note')).toHaveCount(0);
        await expect(page.locator('#dice-log')).toContainText(
            `charisma: ${loadedCharacterRules().attribute_dice}`
        );

        await page.locator('#roll-appearance-btn').click();
        await expect(page.locator('#roll-appearance-btn')).toHaveCount(0);
        await expect(page.locator('#dice-log')).toContainText('Appearance: 1d6');
    });

    test('a bought score locks in place and closes the roll for the rest', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await createCharacter(page);
        const id = characterId(page);
        runDjangoShell(
            'from characters.models import Character; '
            + `Character.objects.filter(pk=${id}).update(strength=None, dexterity=None, `
            + 'intelligence=None, wisdom=None, constitution=None, charisma=None)'
        );
        await page.reload();
        const roll = page.locator('#roll-attributes-btn');
        await expect(roll).toBeVisible();
        // No score is locked, so nothing says why yet (#84).
        await expect(page.locator(ATTRIBUTE_LOCKED_NOTE)).toBeHidden();

        const strength = page.locator('[data-field="strength"] input');
        await expect(strength).toHaveAttribute('min', '3');
        await expect(strength).toHaveAttribute('max', '18');
        await strength.fill('15');
        await strength.blur();

        // Bought, the cell locks in place with the reason, and a buyer may
        // no longer roll the rest (#748 review): no reload needed.
        await expect(page.locator('[data-readonly-field="strength"]')).toHaveText('15');
        await expect(page.locator('[data-locked-field="strength"]')).toBeVisible();
        await expect(page.locator(ATTRIBUTE_LOCKED_NOTE)).toBeVisible();
        await expect(roll).toBeHidden();
        await page.reload();
        await expect(roll).toHaveCount(0);
    });

    test('a player who buys the sixth score loses the roll control without a reload', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await createCharacter(page);
        const id = characterId(page);
        // Five scores a Game Master recorded, one missing: the player may roll
        // it or buy it, and the five count toward the 65 (#748 review).
        runDjangoShell(
            'from characters.models import Character; '
            + `Character.objects.filter(pk=${id}).update(strength=12, dexterity=12, `
            + 'intelligence=11, wisdom=10, constitution=10, charisma=None)'
        );
        await page.reload();
        await page.evaluate(() => { window.__noReload = 1; });
        const roll = page.locator('#roll-attributes-btn');
        await expect(roll).toBeVisible();

        const charisma = page.locator('[data-field="charisma"] input');
        await charisma.fill('10');
        await charisma.blur();

        await expect(page.locator('[data-readonly-field="charisma"]')).toHaveText('10');
        await expect(roll).toBeHidden();
        expect(await page.evaluate(() => window.__noReload)).toBe(1);
    });

    test('a Game Master who types the last score is asked before Roll replaces all six', async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        await createCharacter(page);
        const id = characterId(page);
        runDjangoShell(
            'from characters.models import Character; '
            + `Character.objects.filter(pk=${id}).update(charisma=None)`
        );
        await page.reload();
        const roll = page.locator('#roll-attributes-btn');
        // Rolling a missing score replaces nothing, so nothing is asked yet.
        await expect(roll).not.toHaveAttribute('data-confirm', /.+/);

        const charisma = page.locator('[data-field="charisma"] input');
        await charisma.fill('12');
        await charisma.blur();
        await expect(page.locator('[data-field="charisma"]')).toHaveClass(/saved/);

        await roll.click();
        const dialog = page.locator('.notice-confirm');
        await expect(dialog).toContainText('Roll all six attributes again?');
        await dialog.getByRole('button', { name: 'Cancel' }).click();
        await expect(page.locator('[data-field="charisma"] input')).toHaveValue('12');
    });

    test("a Game Master's reroll asks first, and the history keeps the old value", async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        await createCharacter(page);
        // An appearance no d6 face can equal, so the reroll always changes it
        // and always leaves a history line (a same-face roll records none).
        const before = 'a scar across one eye';
        runDjangoShell(
            'from characters.models import Character; '
            + `Character.objects.filter(pk=${characterId(page)}).update(appearance_roll=${JSON.stringify(before)})`
        );
        await page.reload();
        const reroll = page.locator('#roll-appearance-btn');

        await reroll.click();
        // The page's own persistent confirm panel, not a browser dialog.
        const dialog = page.locator('.notice-confirm');
        await expect(dialog).toContainText('Roll appearance again?');
        await dialog.getByRole('button', { name: 'Cancel' }).click();
        await expect(dialog).toHaveCount(0);

        await reroll.click();
        await page.locator('.notice-confirm').getByRole('button', { name: 'Roll again' }).click();
        await page.waitForLoadState('load');
        await expect(page.locator('.history-table')).toContainText('appearance_roll');
        const row = page.locator('.history-table tr', { hasText: 'appearance_roll' }).first();
        await expect(row).toContainText(before);
    });

    test("a player's rolled height is fixed; a Game Master changes it on the sheet (#948)", async ({ page }) => {
        await loginAs(page, TEST_USER);
        await createCharacter(page);
        const id = characterId(page);
        await expect(page.locator('[data-locked-field="height_roll"]')).toBeVisible();

        await logout(page);
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/characters/${id}/`);
        await expect(page.locator('[data-locked-field="height_roll"]')).toHaveCount(0);
        const input = page.locator('[data-field="height_roll"] input');
        await input.fill('6.6.3');
        await input.blur();
        await expect(page.locator('[data-field="height_roll"]')).toHaveClass(/saved/);
        const stored = readDjangoShell(
            `from characters.models import Character; print(Character.objects.get(pk=${id}).height_roll)`
        );
        expect(stored.trim().split('\n').pop()).toBe('6.6.3');
    });

    test('only a Game Master grants an item modifier, and the grant names them', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await createCharacter(page);
        const id = characterId(page);
        runDjangoShell(
            'from characters.models import Character; '
            + `Character.objects.filter(pk=${id}).update(equipment=[{"item_id": "broadsword", "quantity": 1}])`
        );
        await page.reload();

        const row = page.locator('.equipment-row').first();
        await expect(row).toContainText('Broadsword');
        await expect(row.locator('[data-field="to_hit_mod"]')).toHaveCount(0);
        await expect(page.locator('#item-modifier-note')).toContainText(
            refusal('characters', 'services', 'field_item_modifiers')
        );

        await logout(page);
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/characters/${id}/`);
        const toHit = page.locator('.equipment-row').first().locator('[data-field="to_hit_mod"]');
        await toHit.fill('1');
        await toHit.blur();
        await expect(page.locator('#weapon-to-hit')).toHaveText('+1');

        await logout(page);
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/characters/${id}/`);
        await expect(page.locator('.item-mods-granted')).toHaveText(
            `Hit +1 (set by ${TEST_ADMIN.username}, a Game Master)`
        );
    });
});
