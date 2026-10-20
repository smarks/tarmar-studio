/**
 * The sheet repaints what a change moves, without a reload (issues #763,
 * #764, #765, #766, #792, #797, #798, #804, #805, #846, #851).
 *
 * #903 adds the states a fatal run, a completion and a rename move: the
 * living and dead controls, the "Play with the Game Master" slot, and the name
 * in every place the sheet prints it.
 *
 * Each case makes one change on the sheet and asserts on the page it was
 * made on: the server's response carries what changed and one painter in
 * character-edit.js writes it. A reload used to be the only way to see it.
 */

const { test, expect, loginAs, TEST_USER, TEST_ADMIN } = require('./fixtures');
const { readDjangoShell } = require('./django');
const { pinDice } = require('./dice');
const {
    loadedEquipment, group, item, movementCostingLoad, sheetValues,
} = require('./equipment-rules');

/**
 * Seed a character for the test user and return its id.
 * @param {string} name
 * @param {string} fields - Python keyword arguments, single-quoted strings
 *   only (the script goes through `manage.py shell -c "..."`).
 */
function seedCharacter(name, fields) {
    const output = readDjangoShell(`
from django.contrib.auth import get_user_model
from characters.models import Character
from common.test_helpers import CHARACTER_DEFAULTS
user = get_user_model().objects.get(username='${TEST_USER.username}')
values = dict(CHARACTER_DEFAULTS, name='${name}')
values.update(dict(${fields}))
print('CHARACTER', Character.objects.create(user=user, **values).pk)
`.trim().replace(/\n/g, '; '));
    const match = output.match(/CHARACTER (\d+)/);
    expect(match, `no character id in: ${output}`).not.toBeNull();
    return match[1];
}

/** A name unique to this worker and moment. */
function uniqueName(label, testInfo) {
    return `${label} ${testInfo.workerIndex}${Date.now()}`;
}

/**
 * The load bar and the gaits show what the server says for character
 * ``characterId`` as stored (issue #75): the load, the level's wording, its
 * penalty, the restriction label, each gait's readout and the carrying
 * capacity. The page is held to the server; that the server follows the
 * loaded rules is the Python tests' business.
 * @param {import('@playwright/test').Page} page
 * @param {string} characterId
 * @returns {Promise<object>} the server's values (``sheetValues``)
 */
async function expectTheServersLoadAndGaits(page, characterId) {
    const sheet = sheetValues(characterId);
    // The server prints 16.0 where the painter prints 16, so compare numbers.
    await expect.poll(async () => Number(await page.locator('#carried-weight').textContent()))
        .toBe(sheet.carried_weight);
    await expect(page.locator('#encumbrance-level')).toHaveText(sheet.encumbrance_label ?? '—');
    await expect(page.locator('#max-unencumbered')).toHaveText(sheet.lightest_load_limit ?? '—');
    if (sheet.movement_penalty === 0) {
        await expect(page.locator('#movement-penalty')).toBeHidden();
    } else {
        await expect(page.locator('#movement-penalty')).toHaveText(`${sheet.movement_penalty} movement`);
    }
    if (sheet.movement_restriction_label) {
        await expect(page.locator('#movement-restriction')).toHaveText(sheet.movement_restriction_label);
    } else {
        await expect(page.locator('#movement-restriction')).toBeHidden();
    }
    for (const gait of sheet.gait_readout) {
        let shown = String(gait.distance);
        if (sheet.movement_modifier === null) shown = '—';
        else if (gait.na) shown = 'n/a';
        await expect(page.locator(`#gait-${gait.key} .gait-value`)).toHaveText(shown);
    }
    return sheet;
}

/** Type a value into one autosaving cell and wait for the save. */
async function saveCell(page, field, value) {
    const input = page.locator(`[data-field="${field}"] input`);
    await input.fill(String(value));
    await input.blur();
    await expect(page.locator(`[data-field="${field}"]`)).toHaveClass(/saved/);
}

test.describe('The sheet repaints without a reload', () => {
    test('recording the last score completes the character: exports, attacks, movement (#763, #764)', async ({ page }, testInfo) => {
        const characterId = seedCharacter(uniqueName('Nearly', testInfo),
            "strength=None, equipment=[{'item_id': 'dagger', 'quantity': 1}], history=[]");
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/characters/${characterId}/`);
        await expect(page.locator('#incomplete-marks')).toContainText('needs attributes');
        await expect(page.locator('#attack-form')).toBeHidden();
        await expect(page.locator('#skill-check')).toBeHidden();
        await expect(page.locator('#max-unencumbered')).toHaveText('—');
        await expect(page.locator('#recent-changes')).toBeHidden();

        // Nowhere to take an incomplete character yet (#903).
        const playForm = page.locator(`#play-with-gm-${characterId} form.play-with-gm`);
        await expect(playForm).toHaveCount(0);

        await saveCell(page, 'strength', 14);

        await expect(playForm).toBeVisible();
        await expect(page.getByRole('link', { name: 'Export PDF' })).toBeVisible();
        await expect(page.locator('#incomplete-marks')).toHaveCount(0);
        await expect(page.locator('#attack-incomplete')).toBeHidden();
        await expect(page.locator('#attack-form')).toBeVisible();
        await expect(page.locator('#skill-check')).toBeVisible();
        await expect(page.locator('#encumbrance-level')).not.toHaveText('—');
        await expect(page.locator('#movement-modifier')).not.toHaveText('—');
        await expectTheServersLoadAndGaits(page, characterId);
        await expect(page.locator('#recent-changes')).toBeVisible();
        await expect(page.locator('#recent-changes-rows')).toContainText('strength');

        // The attack the completion unlocked resolves, and its line joins
        // Recent attacks on the same page (#792).
        await expect(page.locator('.no-combat-log')).toBeVisible();
        await page.locator('#attack-resolve-btn').click();
        await expect(page.locator('#attack-result')).toBeVisible();
        await expect(page.locator('.combat-log-entry').first()).toContainText('Dagger');
        await expect(page.locator('.no-combat-log')).toBeHidden();
    });

    test('a lower STR moves the encumbrance badge on the spot (#763)', async ({ page }, testInfo) => {
        // A tower shield against STR 14, then against STR 3: the server says
        // which level each is, and the two must differ for the test to mean
        // anything.
        const characterId = seedCharacter(uniqueName('Laden', testInfo),
            "equipment=[{'item_id': 'tower_shield', 'quantity': 1}]");
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/characters/${characterId}/`);
        const before = await expectTheServersLoadAndGaits(page, characterId);

        await saveCell(page, 'strength', 3);

        const after = await expectTheServersLoadAndGaits(page, characterId);
        expect(after.encumbrance_label, 'a lower STR must move the load to another level')
            .not.toBe(before.encumbrance_label);
    });

    test('the picker offers the loaded catalog, and an added item moves the load and the gaits by the loaded rules (#75)', async ({ page }, testInfo) => {
        // The heaviest item the canon sells outside the armour, and a STR at
        // which carrying it costs movement and forbids a gait without
        // overloading; the server's own levels choose both.
        const { item: heaviest, strength } = movementCostingLoad();
        expect(strength, `no STR puts ${heaviest.id} at a level that forbids a gait`).not.toBeNull();
        const equipment = loadedEquipment();

        const characterId = seedCharacter(uniqueName('Loader', testInfo),
            `strength=${strength}, equipment=[]`);
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/characters/${characterId}/`);
        await expectTheServersLoadAndGaits(page, characterId);

        // The groups, in order, with their labels; and each group's items by
        // name, with their loaded weights.
        await expect(page.locator('#equipment-category option:not([value=""])')).toHaveText(
            equipment.groups.map((candidate) => candidate.label)
        );
        await page.locator('#equipment-category').selectOption(heaviest.group);
        const offered = [...group(heaviest.group).items]
            .sort((first, second) => first.name.localeCompare(second.name))
            .map((entry) => `${entry.name} (${entry.weight} lbs)`);
        await expect(page.locator('#equipment-item option:not([value=""])')).toHaveText(offered);

        await page.locator('#equipment-item').selectOption(heaviest.id);
        await page.locator('#add-free').check();
        await page.locator('#add-equipment-btn').click();
        await expect(page.locator('.equipment-row .item-name')).toContainText(heaviest.name);
        const sheet = await expectTheServersLoadAndGaits(page, characterId);
        // The load is the item's loaded weight, and it costs movement.
        expect(sheet.carried_weight).toBe(heaviest.weight);
        expect(sheet.movement_penalty).not.toBe(0);
        expect(sheet.movement_restriction_label).not.toBe('');
    });

    test('worn armour forbids the gaits its loaded restriction code names (#75)', async ({ page }, testInfo) => {
        // The lightest loaded armour whose restriction code forbids a gait.
        const codes = loadedEquipment().armour_restriction_codes;
        const restricting = group('armor').items.filter(
            (entry) => entry.movement_restriction && (codes[entry.movement_restriction] || []).length
        );
        expect(restricting.length, 'the loaded catalog has no armour that restricts movement')
            .toBeGreaterThan(0);
        const armour = [...restricting].sort((first, second) => first.weight - second.weight)[0];
        const forbidden = codes[armour.movement_restriction];
        const characterId = seedCharacter(uniqueName('Armoured', testInfo),
            `equipment=[{'item_id': '${armour.id}', 'quantity': 1, 'equipped': True}]`);
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/characters/${characterId}/`);
        const sheet = await expectTheServersLoadAndGaits(page, characterId);
        // Every gait the code forbids is n/a, and the label names the armour.
        expect(sheet.movement_restriction_label).toContain(`(${armour.name})`);
        for (const gait of forbidden) {
            await expect(page.locator(`#gait-${gait} .gait-value`)).toHaveText('n/a');
        }
    });

    test('a renamed skill is offered by the skill check under its new name (#765)', async ({ page }, testInfo) => {
        const characterId = seedCharacter(uniqueName('Herbalist', testInfo), "skills={'herbalism': 3}");
        // A Game Master renames: the sheetrules batch holds a player's rename
        // to rules this repaint does not depend on (#768).
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/characters/${characterId}/`);
        await expect(page.locator('#skill-check-skill option[value="herbalism"]')).toHaveCount(1);

        const name = page.locator('.skill-row .skill-name').first();
        await name.fill('Herb Lore');
        await name.blur();
        await expect(name).toHaveClass(/saved/);

        await expect(page.locator('#skill-check-skill option[value="herb lore"]')).toHaveCount(1);
        await expect(page.locator('#skill-check-skill option[value="herbalism"]')).toHaveCount(0);
        await page.locator('#skill-check-skill').selectOption('herb lore');
        await page.locator('#skill-check-btn').click();
        await expect(page.locator('#skill-check-result')).toContainText('herb lore');
    });

    test('an item added to an incomplete character lists at once with no error (#766)', async ({ page }, testInfo) => {
        const characterId = seedCharacter(uniqueName('Unfinished', testInfo),
            'strength=None, dexterity=None, constitution=None, fatigue_roll=None');
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/characters/${characterId}/`);

        await page.locator('#equipment-category').selectOption('gear');
        await page.locator('#equipment-item').selectOption('backpack');
        await page.locator('#add-equipment-btn').click();

        await expect(page.locator('.equipment-row .item-name')).toContainText('Backpack');
        await expect(page.locator('.notice')).toHaveCount(0);
        await expect(page.locator('#encumbrance-level')).toHaveText('—');
        await expect(page.locator('#movement-modifier')).toHaveText('—');
    });

    test('a failed catalog load says so, keeps the items listed, and retries (#797)', async ({ page }, testInfo) => {
        const characterId = seedCharacter(uniqueName('Offline', testInfo),
            "equipment=[{'item_id': 'backpack', 'quantity': 1}]");
        await loginAs(page, TEST_USER);
        await page.route('**/characters/equipment/catalog/', (route) => route.abort());
        await page.goto(`/tarmar/characters/${characterId}/`);

        const notice = page.locator('#equipment-catalog-error');
        await expect(notice).toBeVisible();
        await expect(page.locator('.equipment-row .item-name')).toContainText('backpack');

        await page.unroute('**/characters/equipment/catalog/');
        await page.locator('#equipment-catalog-retry').click();
        await expect(notice).toBeHidden();
        await expect(page.locator('.equipment-row .item-name')).toContainText('Backpack');
    });

    test('a refused new skill keeps the name the player typed (#798)', async ({ page }, testInfo) => {
        const characterId = seedCharacter(uniqueName('Keeper', testInfo), 'free_skill_points=1');
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/characters/${characterId}/`);
        await page.route('**/allocate-skill/', (route) => route.fulfill({
            status: 400,
            contentType: 'application/json',
            body: JSON.stringify({ error: 'No free skill points available' }),
        }));

        await page.locator('#new-skill-name').fill('Herbalism');
        await page.locator('#add-skill-btn').click();
        await expect(page.locator('.notice .notice-text').first()).toContainText('No free skill points');
        await expect(page.locator('#new-skill-name')).toHaveValue('Herbalism');
    });

    test('the weapon in hand drives the tiles and the Attack panel, and one shield counts (#804, #846)', async ({ page }, testInfo) => {
        const characterId = seedCharacter(uniqueName('Armed', testInfo), `equipment=[
            {'item_id': 'broadsword', 'quantity': 1, 'to_hit_mod': 2},
            {'item_id': 'dagger', 'quantity': 1, 'to_hit_mod': 1},
            {'item_id': 'small_shield', 'quantity': 1},
            {'item_id': 'tower_shield', 'quantity': 1}]`.replace(/\n\s*/g, ' '));
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/characters/${characterId}/`);

        // One weapon's modifier, not the sum; one shield's stops, not both.
        await expect(page.locator('#weapon-to-hit')).toHaveText('+2');
        await expect(page.locator('#armor-stops')).toHaveText('1');
        await expect(page.locator('#attack-weapon option:checked')).toContainText('in hand');

        const daggerRow = page.locator('.equipment-row[data-index="1"]');
        await daggerRow.locator('input[name="held-weapons"]').check();
        await expect(page.locator('#weapon-to-hit')).toHaveText('+1');
        await expect(page.locator('#attack-weapon')).toHaveValue('1');

        const towerRow = page.locator('.equipment-row[data-index="3"]');
        await towerRow.locator('input[name="held-shield"]').check();
        await expect(page.locator('#armor-stops')).toHaveText('3');

        // Dropping the shield in use with the pack leaves no shield at all.
        await page.locator('.equipment-row[data-index="3"] [data-field="in_pack"]').check();
        await expect(page.locator('#armor-stops')).toHaveText('1');
    });

    test('a player buys nothing before the wealth roll (#851)', async ({ page }, testInfo) => {
        const characterId = seedCharacter(uniqueName('Unrolled Purse', testInfo), 'wealth_recorded=False');
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/characters/${characterId}/`);

        await page.locator('#equipment-category').selectOption('weapons');
        await page.locator('#equipment-item').selectOption('dagger');
        await page.locator('#add-equipment-btn').click();

        await expect(page.locator('.notice .notice-text').first()).toContainText('Roll wealth first');
        await expect(page.locator('.equipment-row')).toHaveCount(0);
    });

    test('nonsense in Height is refused and the height stays as it was (#805)', async ({ page }, testInfo) => {
        const characterId = seedCharacter(uniqueName('Measured', testInfo), '');
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/characters/${characterId}/`);
        const before = await page.locator('#height_display').textContent();

        const input = page.locator('[data-field="height_roll"] input');
        await input.fill('tall');
        await input.blur();
        await expect(page.locator('[data-field="height_roll"]')).toHaveClass(/error/);
        await expect(page.locator('[data-field="height_roll"] .autosave-error')).toContainText('die faces 1 to 6');
        await expect(page.locator('#height_display')).toHaveText(before);
    });

    test('an equipment edit keeps the weapon picked in the Attack panel (#846 review)', async ({ page }, testInfo) => {
        const characterId = seedCharacter(uniqueName('Picker', testInfo),
            "equipment=[{'item_id': 'saber', 'quantity': 1}, {'item_id': 'dagger', 'quantity': 1}, {'item_id': 'torch', 'quantity': 1}]");
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/characters/${characterId}/`);
        const select = page.locator('#attack-weapon');
        await expect(select).toHaveValue('0');
        await select.selectOption('1');

        const notes = page.locator('.equipment-row[data-index="2"] [data-field="notes"]');
        await notes.fill('lit');
        await notes.blur();
        await expect(page.locator('.equipment-row[data-index="2"] .item-notes')).toHaveClass(/saved/);
        await expect(select).toHaveValue('1');

        // Readying the dagger changes the hand, and the panel follows it.
        await select.selectOption('0');
        await page.locator('.equipment-row[data-index="1"] input[name="held-weapons"]').check();
        await expect(select).toHaveValue('1');
    });

    test.describe('in a zone away from UTC', () => {
        test.use({ timezoneId: 'America/New_York' });

        test('Recent Changes rows added live read in the viewer\'s zone (#700 kept)', async ({ page }, testInfo) => {
            const characterId = seedCharacter(uniqueName('Zoned', testInfo), 'history=[]');
            await loginAs(page, TEST_ADMIN);
            await page.goto(`/tarmar/characters/${characterId}/`);
            await saveCell(page, 'charisma', 11);
            const live = await page.locator('#recent-changes-rows time').first().textContent();
            await page.reload();
            await expect(page.locator('#recent-changes-rows time').first()).toHaveText(live);
        });
    });

    test('renaming the character renames the breadcrumb and the title (#792)', async ({ page }, testInfo) => {
        const characterId = seedCharacter(uniqueName('Firstname', testInfo), '');
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/characters/${characterId}/`);
        const renamed = uniqueName('Secondname', testInfo);

        await page.locator('#id_name').fill(renamed);
        await page.locator('#id_name').blur();
        await expect(page.locator('[data-field="name"]')).toHaveClass(/saved/);

        await expect(page.locator('.breadcrumb [aria-current="page"]')).toHaveText(renamed);
        await expect(page).toHaveTitle(renamed);
    });

    test('packing worn armour takes it off, and it stops counting (#869)', async ({ page }, testInfo) => {
        const characterId = seedCharacter(uniqueName('Packed Mail', testInfo),
            "equipment=[{'item_id': 'chainmail', 'quantity': 1, 'equipped': True}]");
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/characters/${characterId}/`);
        const stops = String(item('chainmail').stops);
        await expect(page.locator('#armor-stops')).toHaveText(stops);

        await page.locator('.equipment-row[data-index="0"] [data-field="in_pack"]').check();
        await expect(page.locator('#armor-stops')).toHaveText('0');
        await expect(page.locator('.equipment-row[data-index="0"] [data-field="equipped"]')).not.toBeChecked();

        // Putting it back on takes it out of the pack again.
        await page.locator('.equipment-row[data-index="0"] [data-field="equipped"]').check();
        await expect(page.locator('#armor-stops')).toHaveText(stops);
        await expect(page.locator('.equipment-row[data-index="0"] [data-field="in_pack"]')).not.toBeChecked();
    });

    test('a fatal run of years leaves the dead character\'s controls, then a rename names the death card (#903)', async ({ page }, testInfo) => {
        // Every score 3 is a best modifier of -5 against the Campaigner's 9.
        // The year's dice are pinned (issue #959): seed 3 rolls a d20 of 8,
        // then 3d6 of [5, 5, 2], and 12 - 5 = 7 misses the 9, so the first
        // year kills -- no longer a bet that twenty real years cannot all pass.
        const characterId = seedCharacter(uniqueName('Doomed', testInfo),
            'strength=3, dexterity=3, intelligence=3, wisdom=3, constitution=3, charisma=3');
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/characters/${characterId}/`);
        const playForm = page.locator(`#play-with-gm-${characterId} form.play-with-gm`);
        await expect(page.locator('#mark-dead-btn')).toBeVisible();
        await expect(page.locator('#revive-btn')).toBeHidden();
        await expect(page.locator('#magic-controls')).toBeVisible();
        await expect(playForm).toBeVisible();
        await page.evaluate(() => { window.notReloaded = true; });

        await pinDice(page, '**/add-years/', 3);
        await page.locator('#track-select').selectOption('campaigner');
        await page.locator('#roll-years-btn').click();

        const deathCard = page.locator('#workbench-card .death-card');
        await expect(deathCard).toBeVisible();
        const yearEntries = page.locator('#experience-log .term-entry');
        await expect(yearEntries).toHaveCount(1);
        await expect(yearEntries.first()).toContainText(
            '(rolled 8) — survival 3d6 [5, 5, 2] - 5 = 7 vs 9: died'
        );
        await expect(page.locator('#mark-dead-btn')).toBeHidden();
        await expect(page.locator('#revive-btn')).toBeVisible();
        await expect(page.locator('#magic-controls')).toBeHidden();
        await expect(playForm).toHaveCount(0);
        expect(await page.evaluate(() => window.notReloaded)).toBe(true);

        const newName = uniqueName('Late', testInfo);
        await saveCell(page, 'name', newName);
        await expect(deathCard.locator('h3')).toHaveText(`${newName} died`);
        await expect(page).toHaveTitle(newName);
        expect(await page.evaluate(() => window.notReloaded)).toBe(true);
    });

    test('a rename names the character in every place the sheet prints it (#903)', async ({ page }, testInfo) => {
        // The player's own character, dying (the survival save offered) and
        // held at a Game Master's table (the sessions holding it listed).
        const output = readDjangoShell(`
from django.contrib.auth import get_user_model
from characters.models import Character
from common.test_helpers import CHARACTER_DEFAULTS
from gm.models import GMSession
users = get_user_model().objects
values = dict(CHARACTER_DEFAULTS, name='${uniqueName('Wounded', testInfo)}')
character = Character.objects.create(user=users.get(username='${TEST_USER.username}'), **values)
character.body_damage = character.body + (character.body + 1) // 2
character.save()
session = GMSession.objects.create(user=users.get(username='${TEST_ADMIN.username}'), name='Kell table')
session.characters.add(character)
print('CHARACTER', character.pk)
`.trim().replace(/\n/g, '; '));
        const match = output.match(/CHARACTER (\d+)/);
        expect(match, `no character id in: ${output}`).not.toBeNull();
        const characterId = match[1];
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/characters/${characterId}/`);
        await expect(page.locator('#roll-survival-save-btn')).toBeVisible();
        await expect(page.locator('#sessions-holding')).toBeVisible();
        await page.evaluate(() => { window.notReloaded = true; });

        // A save that leaves the slot as it was leaves the element itself,
        // with whatever the viewer had chosen in it (#903 review).
        const slotSelector = `#play-with-gm-${characterId}`;
        await page.evaluate((selector) => { document.querySelector(selector).kept = true; }, slotSelector);
        await saveCell(page, 'height_roll', 5);
        expect(await page.evaluate((selector) => document.querySelector(selector).kept, slotSelector)).toBe(true);

        const newName = uniqueName('Renamed', testInfo);
        await saveCell(page, 'name', newName);

        await expect(page).toHaveTitle(newName);
        await expect(page.locator('.breadcrumb [aria-current="page"]')).toHaveText(newName);
        await expect(page.locator('#roll-survival-save-btn')).toHaveAttribute(
            'data-confirm', new RegExp(`^Roll ${newName}'s survival save\\?`));
        await expect(page.locator('#sessions-holding [data-name-chrome]').first()).toHaveText(newName);
        // The slot's own label; the session picker's search box carries a
        // second, screen-reader label beside it (#973).
        await expect(page.locator(`#play-with-gm-${characterId} label[for="play-with-gm-session-${characterId}"]`))
            .toHaveText(`Take ${newName} to`);
        await expect(page.locator(`#play-with-gm-${characterId} form.play-with-gm`)).toBeVisible();
        expect(await page.evaluate(() => window.notReloaded)).toBe(true);
    });

    test('a player cannot start a skill no rule names (#862)', async ({ page }, testInfo) => {
        const characterId = seedCharacter(uniqueName('Brawler', testInfo), 'free_skill_points=1');
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/characters/${characterId}/`);

        await page.locator('#new-skill-name').fill('Brawling');
        await page.locator('#add-skill-btn').click();
        await expect(page.locator('.notice .notice-text').first()).toContainText('skills glossary names');
        await expect(page.locator('#free-points')).toHaveText('1');
        await expect(page.locator('.skill-row[data-skill="brawling"]')).toHaveCount(0);

        // A skill the glossary names is taken.
        await page.locator('#new-skill-name').fill('Perception');
        await page.locator('#add-skill-btn').click();
        await expect(page.locator('.skill-row[data-skill="perception"]')).toHaveCount(1);
        await expect(page.locator('#free-points')).toHaveText('0');
    });

    test('a Village player\'s choice of Survival is recorded as theirs, not a Game Master\'s call (#67)', async ({ page }, testInfo) => {
        const characterId = seedCharacter(uniqueName('Villager', testInfo),
            "location='Village', origin_skill='street smarts', skills={'street smarts': 1}");
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/characters/${characterId}/`);

        const name = page.locator('.skill-row[data-skill="street smarts"] .skill-name');
        await name.fill('Survival');
        await name.blur();
        await expect(page.locator('.skill-row[data-skill="survival"]')).toHaveCount(1);

        const latest = page.locator('#recent-changes-rows tr').first();
        await expect(latest).toContainText('street smarts');
        await expect(latest).toContainText('survival');
        await expect(latest).not.toContainText("Game Master's call");
    });
});
