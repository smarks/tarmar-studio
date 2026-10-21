/**
 * E2E for the rules the sheet recorded and never acted on (issues #753,
 * #756, #757, #755/#774), each driven in a real browser:
 *
 * - #753: the attack panel offers no skill choice; an untied Weapon skill
 *   adds nothing until the player ties it once, and then the panel and the
 *   battle board price the same level.
 * - #756: the skill-check control rolls on the server, the result stays on
 *   the page, and the roll is in the history after a reload.
 * - #757: buying catalog gear spends the purse, and a purchase the purse
 *   cannot cover is refused.
 * - #774/#755: a spell learned with a skill point is on the sheet, and the
 *   saved caster is seated in a battle with it and its Mana Pool.
 * - #18: the player rolls a school of magic once, chooses from the row the
 *   roll lands on, and learns only that school's spells.
 * - #18: a spell is cast in the story from the sheet, by the player (a
 *   spell they know, from their Pool) and by a Game Master (any spell, with
 *   a total rolled at his table); the page shows the server's line and Mana.
 *
 * Characters are written from an export file for the player, so a retry gets
 * its own: a player's import of a hand-made file keeps only what a player
 * sets on the sheet (#744), so it cannot bring these attributes and skills
 * in. What only a Game Master may give (coin, mana) is given by one, not
 * carried in the file (the #757 review).
 */

const {
    test, expect, loginAs, TEST_USER, TEST_ADMIN, createBattleViaSetup, postAsGameMaster,
    characterIdFromUrl, seedCharacterFromExport, submitPassphrase,
} = require('./fixtures');
const { uniqueName } = require('./unique-name');
const { startCoverage } = require('./coverage');
const { pinDice } = require('./dice');
const { diceLogLine } = require('./dice-log');
const { runDjangoShell } = require('./django');
const { refusal, refusalFixedParts, gameMasterCallNote } = require('./refusals');
const { loadedSkillRules } = require('./skill-rules');
const { item, sheetValues } = require('./equipment-rules');
const { catalogSpells, lastStoryCast, schoolRowForPercentile } = require('./magic-rules');

/** The school roll's dice pinned (issue #959) to a known face. */
const SCHOOL_ROLL_SEED = 1;
/**
 * The d100 face the school roll gives on that seed. Asserting it on the page
 * is what proves the pin took (see dice.js); the row it lands on is the
 * loaded canon's table's call, read from the server.
 */
const SEEDED_SCHOOL_FACE = 18;

/** The cast's dice pinned (issue #959) to known faces. */
const STORY_CAST_SEED = 4;
/**
 * The casting dice that seed gives, as the server's line writes them.
 * Asserting them in the line is what proves the pin took (see dice.js);
 * what the total does is the loaded canon's call, read from the record.
 */
const SEEDED_CAST_DICE = '[2, 3, 1] = 6';

/** Why the school steps are skipped under a canon with no school table. */
const NULL_SCHOOLS_REASON =
    "the loaded canon's schools block is null: it gates no spell by school, so there is no table to roll on";

/**
 * Roll the school on the pinned face, choose a school of the row it lands on
 * that the battle has spells for, and return the first of those spells
 * (issue #18). Before the roll the player has no spell to learn; afterwards
 * they roll no more, choose once, and are offered only the school's spells.
 * @param {import('@playwright/test').Page} page
 * @param {{label: string, schools: string[], spells: Object<string, string[]>}} row
 *     The row the server says the face lands on.
 * @returns {Promise<string>} The spell key to learn.
 */
async function rollAndChooseSchool(page, row) {
    await expect(page.locator('#learn-spell-select')).toHaveCount(0);
    await expect(page.locator('#learn-spell-note')).toBeVisible();
    await pinDice(page, '**/roll/magic_school/', SCHOOL_ROLL_SEED);
    await Promise.all([
        page.waitForEvent('load'),
        page.locator('#roll-magic-school-btn').click(),
    ]);
    await expect(page.locator('#magic-school-roll')).toHaveText(
        `(d100 ${SEEDED_SCHOOL_FACE}: ${row.label}, ${row.schools.join(', ')})`
    );
    await expect(page.locator('#roll-magic-school-btn')).toHaveCount(0);
    await expect(page.locator('#choose-school-select option')).toHaveText(row.schools);
    const school = row.schools.find((name) => row.spells[name].length > 0);
    await page.locator('#choose-school-select').selectOption(school);
    await Promise.all([
        page.waitForEvent('load'),
        page.locator('#choose-school-btn').click(),
    ]);
    await expect(page.locator('#magic-school')).toHaveText(school);
    await expect(page.locator('#choose-school-select')).toHaveCount(0);
    await expect(page.locator('#learn-spell-select option')).toHaveCount(
        row.spells[school].length
    );
    return row.spells[school][0];
}

function mechanicsCharacter(name) {
    return {
        _format: 'tarmar-character',
        _version: 1,
        data: {
            name,
            strength: 14,
            dexterity: 16,
            intelligence: 14,
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
            // An untied Weapon skill at level 3, Knowledge V, Stealth II.
            skills: { weapon: 6, knowledge: 15, stealth: 3 },
            free_skill_points: 2,
            equipment: [{ item_id: 'saber', quantity: 1 }],
            pack_dropped: false,
            total_xp: 1500,
            age: 20,
            experience_terms: 1,
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
}

async function importCharacter(page, name) {
    await seedCharacterFromExport(page, TEST_USER, mechanicsCharacter(name));
}

/** An amount of coin in whole cents, so sums compare exactly. */
function cents(amount) {
    return Math.round(Number(amount) * 100);
}

/**
 * The purse comes to ``target`` gp on the server, and the sheet's two purse
 * readouts print it as the server does (issue #75).
 */
async function expectPurse(page, target) {
    const characterId = characterIdFromUrl(page);
    await expect.poll(() => cents(sheetValues(characterId).purse)).toBe(cents(target));
    const printed = sheetValues(characterId).purse_text;
    await expect(page.locator('#purse')).toHaveText(printed);
    return printed;
}

/** Set the purse to exactly `target` gp, as a Game Master. */
async function setPurse(page, browser, target) {
    const held = Number((await page.locator('#purse').textContent()).replace(/[^0-9.]/g, ''));
    if (held !== target) {
        const moved = await postAsGameMaster(
            browser, `/tarmar/characters/${characterIdFromUrl(page)}/coin/`,
            { amount: target - held, reason: 'e2e seed' }
        );
        expect(moved.status).toBe(200);
    }
    await page.reload();
    await expectPurse(page, target);
}

function breakdownValue(page, label) {
    return page
        .locator(`#attack-breakdown li:has(.attack-label:text-is("${label}")) .attack-value`)
        .first();
}

test.describe('Sheet mechanics', () => {
    test('#753: the Weapon skill is tied once and priced by the record', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await importCharacter(page, uniqueName('Tier'));

        await expect(page.locator('#attack-skill')).toHaveCount(0);
        await page.locator('#attack-resolve-btn').click();
        // Knowledge V is on the sheet and adds nothing; the Weapon skill is
        // not tied yet, so the swing is untrained.
        await expect(breakdownValue(page, 'Skill')).toHaveText('untrained');

        const tie = page.locator('.skill-row[data-skill="weapon"] .weapon-skill-tie');
        await tie.locator('select[name="item_id"]').selectOption('saber');
        await Promise.all([
            page.waitForEvent('load'),
            tie.locator('.mechanics-submit').click(),
        ]);
        await expect(page.locator('.skill-row[data-skill="weapon (saber)"]')).toBeVisible();
        await expect(page.locator('.weapon-skill-tie')).toHaveCount(0);

        await page.locator('#attack-resolve-btn').click();
        await expect(breakdownValue(page, 'Skill')).toHaveText(
            'weapon (saber) level 3 (full: the weapon it was taken for)'
        );
    });

    test('#756: a skill check rolls on the server and is kept in the history', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await importCharacter(page, uniqueName('Sneak'));

        await page.locator('#skill-check-skill').selectOption('stealth');
        // The difficulty is the Game Master's to set; a player is not offered
        // it, nor the attribute override (the #756 review).
        await expect(page.locator('#skill-check-difficulty')).toHaveCount(0);
        await expect(page.locator('select[name="attribute"]')).toHaveCount(0);
        await page.locator('#skill-check-btn').click();
        const result = page.locator('#skill-check-result');
        await expect(result).toBeVisible();
        // DEX 16 is +3 and Stealth II is +2; no difficulty, so no outcome,
        // only a natural named if the dice made one.
        await expect(result).toContainText(/^stealth: 2d6 \[\d, \d\] = \d+ \+3 DEX \(16\) \+2 level = \d+( \(natural (12|2)\))?$/);
        // The dice join the character's dice log on this page (#147), the
        // same faces the line shows.
        const [, faces, diceTotal] = (await result.textContent()).match(/2d6 \[(\d, \d)\] = (\d+)/);
        const logged = diceLogLine(
            'SKILL_CHECK_LOG_LABEL', { skill: 'stealth' }, '2d6', faces.split(', ').map(Number), Number(diceTotal),
        );
        await expect(page.locator('#dice-log')).toBeVisible();
        await expect(page.locator('#dice-log-text')).toContainText(logged);

        await page.reload();
        await expect(page.locator('#recent-changes')).toContainText('stealth check: 2d6');
        await expect(page.locator('#dice-log-text')).toContainText(logged);
    });

    test('#70: the check rolls the canon\'s dice and its natural success succeeds', async ({ page }) => {
        // Read from the loaded canon, so this holds under either canon: the
        // dice, the natural success, a skill the canon governs, and a seed
        // whose pinned dice make the natural.
        const { skill_check: check } = loadedSkillRules();
        const [skill] = check.governed_skills;
        await loginAs(page, TEST_ADMIN);
        await seedCharacterFromExport(page, TEST_ADMIN, mechanicsCharacter(uniqueName('Natural')));

        await pinDice(page, '**/skill-check/', check.natural_success_seed);
        await page.locator('#skill-check-skill').selectOption(skill);
        // A difficulty no total reaches: only the natural makes it a success.
        await page.locator('#skill-check-difficulty').fill(String(check.max_difficulty));
        await page.locator('#skill-check-btn').click();
        const result = page.locator('#skill-check-result');
        await expect(result).toContainText(`${skill}: ${check.dice} [`);
        await expect(result).toContainText(`= ${check.natural_success} `);
        await expect(result).toContainText(
            `vs ${check.max_difficulty}: success (natural ${check.natural_success})`
        );
        const total = Number((await result.textContent()).match(/= (-?\d+) vs /)[1]);
        expect(total, 'the total must fall short of the difficulty').toBeLessThan(check.max_difficulty);
        await page.reload();
        await expect(page.locator('#recent-changes')).toContainText(`${skill} check: ${check.dice}`);
    });

    test("#85: a Game Master's Literacy check for one not known to read ends with the server's call note", async ({ page }) => {
        // The note is the server's configured words, sent with the check's
        // answer; the sheet's script holds no copy of them.
        await loginAs(page, TEST_ADMIN);
        const characterId = await seedCharacterFromExport(
            page, TEST_ADMIN, mechanicsCharacter(uniqueName('Unread'))
        );
        runDjangoShell(
            'from characters.models import Character; '
            + `Character.objects.filter(pk=${characterId}).update(intelligence=10, `
            + "skills={'literacy': 3}, literacy_roll=18, literacy_rolled=True)"
        );
        await page.reload();
        await page.locator('#skill-check-skill').selectOption('literacy');
        await page.locator('#skill-check-btn').click();
        const result = page.locator('#skill-check-result');
        await expect(result).toBeVisible();
        const note = gameMasterCallNote('not known to read');
        expect(note).toContain(refusal('characters', 'common', 'a_game_masters_call'));
        await expect(result).toContainText(note.trim());
        expect((await result.textContent()).endsWith(note), 'the line ends with the note').toBe(true);
    });

    test('#757: catalog gear is bought from the purse', async ({ page, browser }) => {
        // The prices are the loaded catalog's (#75): enough for a broadsword
        // and a chainmail with a tenth of the chainmail's price left over, so
        // a second chainmail is refused.
        const sword = item('broadsword').cost;
        const mail = item('chainmail').cost;
        const leftOver = Math.round(mail * 10) / 100;
        const purse = sword + mail + leftOver;
        await loginAs(page, TEST_USER);
        await importCharacter(page, uniqueName('Buyer'));
        await setPurse(page, browser, purse);

        await page.locator('#equipment-category').selectOption('weapons');
        await page.locator('#equipment-item').selectOption('broadsword');
        await page.locator('#add-equipment-btn').click();
        const afterSword = await expectPurse(page, purse - sword);
        await expect(page.locator('#equipment-purse')).toHaveText(afterSword);

        // With the chainmail's price and a tenth over it is bought, and then
        // nothing that costs more than that tenth is.
        await page.locator('#equipment-category').selectOption('armor');
        await page.locator('#equipment-item').selectOption('chainmail');
        await page.locator('#add-equipment-btn').click();
        await expect(page.locator('#equipment-purse')).toHaveText(await expectPurse(page, leftOver));
        await page.locator('#equipment-category').selectOption('armor');
        await page.locator('#equipment-item').selectOption('chainmail');
        await page.locator('#add-equipment-btn').click();
        await expect(page.locator('.notice .notice-text').first()).toContainText(
            refusalFixedParts('characters', 'purse', 'not_enough')[0]
        );

        // Removing an item returns nothing (the #757 review ruling).
        const swordRow = page.locator('.equipment-row', { hasText: 'Broadsword' });
        await swordRow.locator('.remove-btn').click();
        await page.locator('.notice-confirm button:has-text("Remove")').click();
        await expect(page.locator('.equipment-row', { hasText: 'Broadsword' })).toHaveCount(0);
        await page.reload();
        await expectPurse(page, leftOver);
    });

    test('#774/#755/#18: a spell of the rolled school is learned, on the sheet and seated in battle', async ({ page, browser }) => {
        const name = uniqueName('Own Caster');
        await loginAs(page, TEST_USER);
        await importCharacter(page, name);

        // The player cannot grant mana; a Game Master can.
        await expect(page.locator('#mana-pool')).toHaveText('0');
        await expect(page.locator('.grant-mana')).toHaveCount(0);
        const granted = await postAsGameMaster(
            browser, `/tarmar/characters/${characterIdFromUrl(page)}/grant-mana/`, { amount: 6 }
        );
        expect(granted.status).toBe(200);
        await page.reload();
        await expect(page.locator('#mana-pool')).toHaveText('6');
        await expect(page.locator('#no-spells')).toBeVisible();

        // #18: the school roll and choice. Under a canon whose schools block
        // is null nothing gates a spell by school, so those steps are skipped,
        // with the reason recorded, and the first spell on offer is learned.
        const row = schoolRowForPercentile(SEEDED_SCHOOL_FACE);
        let spell;
        if (row === null) {
            test.info().annotations.push({ type: 'skipped steps', description: NULL_SCHOOLS_REASON });
            await expect(page.locator('#roll-magic-school-btn')).toHaveCount(0);
            spell = await page.locator('#learn-spell-select option').first().getAttribute('value');
        } else {
            spell = await rollAndChooseSchool(page, row);
        }

        await page.locator('#learn-spell-select').selectOption(spell);
        await Promise.all([
            page.waitForEvent('load'),
            page.locator('#learn-spell-btn').click(),
        ]);
        await expect(page.locator(`.known-spell[data-spell="${spell}"]`)).toBeVisible();
        await expect(page.locator('#free-points')).toHaveText('1');

        await createBattleViaSetup(page, {
            name: uniqueName('E2E Own Caster'),
            fighters: 1,
            seed: 5,
            characterNames: [name],
        });
        const card = page.locator('.combatant-card', { hasText: name });
        await card.locator('.combatant-detail summary').click();
        const panel = card.locator('.combatant-detail');
        await expect(panel).toContainText(spell);
        await expect(panel).toContainText('6 / 6');
    });

    test('#18: a spell is cast in the story from the sheet, by a player and a Game Master', async ({ page, browser }) => {
        const name = uniqueName('Story Caster');
        await loginAs(page, TEST_USER);
        await importCharacter(page, name);
        const characterId = characterIdFromUrl(page);
        const spells = catalogSpells();
        // A spell that need not hit for the player, one off the record for the
        // Game Master; the server's catalog names both.
        const known = spells.find((spell) => !spell.targeted).key;
        const offRecord = spells.find((spell) => spell.key !== known).key;
        for (const [path, body] of [
            ['grant-mana', { amount: 6 }],
            ['learn-spell', { spell: known }],
        ]) {
            const granted = await postAsGameMaster(browser, `/tarmar/characters/${characterId}/${path}/`, body);
            expect(granted.status).toBe(200);
        }
        await page.reload();

        // The player: only the spell they know, and no total of their own.
        await expect(page.locator('#cast-spell-select option')).toHaveCount(1);
        await expect(page.locator('#cast-spell-select option')).toHaveAttribute('value', known);
        await expect(page.locator('#cast-spell input[name="total"]')).toHaveCount(0);
        await pinDice(page, '**/cast-spell/', STORY_CAST_SEED);
        await page.locator('#cast-spell-btn').click();
        const result = page.locator('#cast-spell-result');
        await expect(result).toBeVisible();
        const playerCast = lastStoryCast(characterId);
        expect(playerCast.line).toContain(SEEDED_CAST_DICE);
        await expect(result).toHaveText(playerCast.line);
        // The Mana it spent is on the page without a reload, in the Magic
        // section and the injury readout alike.
        await expect(page.locator('#mana-current')).toHaveText(String(playerCast.current_mana));
        await expect(page.locator('#current-mana')).toHaveText(String(playerCast.current_mana));
        await expect(page.locator('#recent-changes-rows')).toContainText(playerCast.line);

        // A Game Master: any spell, with a total rolled at his table.
        const context = await browser.newContext();
        try {
            const gameMaster = await context.newPage();
            await startCoverage(gameMaster);
            await submitPassphrase(gameMaster);
            await loginAs(gameMaster, TEST_ADMIN);
            await gameMaster.goto(`/tarmar/characters/${characterId}/`);
            await expect(gameMaster.locator('#cast-spell-select option')).toHaveCount(spells.length);
            await gameMaster.locator('#cast-spell-select').selectOption(offRecord);
            await gameMaster.locator('#cast-spell-total').fill('10');
            await gameMaster.locator('#cast-spell-btn').click();
            const gameMasterResult = gameMaster.locator('#cast-spell-result');
            await expect(gameMasterResult).toBeVisible();
            const gameMasterCast = lastStoryCast(characterId);
            expect(gameMasterCast.line).not.toBe(playerCast.line);
            await expect(gameMasterResult).toHaveText(gameMasterCast.line);
            await expect(gameMaster.locator('#mana-current')).toHaveText(String(gameMasterCast.current_mana));
            // The stated total was for that cast only.
            await expect(gameMaster.locator('#cast-spell-total')).toHaveValue('');
        } finally {
            await context.close();
        }
    });
});
