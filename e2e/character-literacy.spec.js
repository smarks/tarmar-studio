/**
 * The Location section's literacy row (issues #740, #741).
 *
 * The row was rendered once from the origin the page loaded with, so picking
 * a new Origin saved it and left the old origin's check on show until a
 * reload (#740). And there was no way to roll the check or keep the result
 * (#741). Only a real browser shows that the row moves with no reload, and
 * that a roll made on the page is the one the page shows after a reload.
 *
 * Spencer's rulings on #741: once literacy is rolled a player may no longer
 * change the Origin, and a Game Master who does has literacy rolled again
 * against the new origin (cleared, for Consult GM, which has no check; the
 * player stays held to the roll they had).
 *
 * Spencer's ruling on #970: a rolled Origin is fixed for a player outright, so
 * the literacy lock is exercised on an Origin the player chose (no roll in the
 * dice log), and a rolled one is its own test.
 */

const {
    test, expect, loginAs, createCharacter, submitPassphrase, TEST_USER, TEST_ADMIN,
} = require('./fixtures');
const { startCoverage } = require('./coverage');
const { refusal } = require('./refusals');
const { runDjangoShell, readDjangoJson } = require('./django');
const { pinDice } = require('./dice');
const { loadedCharacterRules, originForPercentile } = require('./character-rules');

/** Creation's dice pinned (issue #959) so the Origin has a check to roll. */
const ROLLED_ORIGIN_SEED = 7;
/**
 * The d100 face creation's location roll gives on that seed. Asserting the
 * face is what proves the pin took (see dice.js); the Origin it lands on is
 * the loaded canon's table's call, read from the server.
 */
const SEEDED_LOCATION_FACE = 81;

/**
 * The loaded canon's origins, read through the Django shell: each one's label,
 * literacy modifier (null where the Game Master decides) and the check the
 * sheet prints for it. Nothing about them is typed here, so the spec holds
 * under any canon.
 */
const ORIGIN_RULES = loadedCharacterRules().origins;
const ORIGINS = ORIGIN_RULES.map((origin) => origin.label);
/** The check each origin prints. */
const CHECKS = Object.fromEntries(ORIGIN_RULES.map((origin) => [origin.label, origin.check]));
/** The origins with a check to roll. */
const CHECKED_ORIGINS = ORIGIN_RULES.filter((origin) => origin.modifier !== null).map((origin) => origin.label);
/** An origin the Game Master decides: it publishes no check, so nothing rolls. */
const NO_CHECK_ORIGIN = ORIGIN_RULES.find((origin) => origin.modifier === null)?.label;
if (CHECKED_ORIGINS.length < 2 || NO_CHECK_ORIGIN === undefined) {
    throw new Error(
        'These scenarios need two origins with a literacy check and one without; '
        + `the loaded canon has ${JSON.stringify(ORIGIN_RULES)}`
    );
}

/**
 * Any origin other than `origin`, among those with a check unless asked
 * otherwise.
 * @param {string} origin
 * @param {string[]} [among]
 */
function anotherOrigin(origin, among = CHECKED_ORIGINS) {
    return among.find((candidate) => candidate !== origin);
}

/** The reason a player's sheet prints beside a rolled Origin (#970). */
/** The configured reason a rolled Origin is final (#970, #85). */
const rolledOriginFixed = () => refusal('characters', 'services', 'roll_fixed_origin');

/**
 * Create a character whose player chose the Origin rather than rolled it.
 *
 * Creation rolls the Origin, which fixes it for the player (#970). Taking the
 * location table's line out of the dice log leaves the Origin creation
 * landed on as a choice (location.md: "Roll percentile dice or choose"),
 * which stays the player's until literacy is rolled or ruled (#741).
 *
 * @param {import('@playwright/test').Page} page
 * @returns {Promise<string>} The character's id.
 */
async function createWithChosenOrigin(page) {
    await createCharacter(page);
    const characterId = page.url().match(/\/characters\/(\d+)\//)[1];
    runDjangoShell(
        'from characters.models import Character; '
        + `character = Character.objects.get(pk=${characterId}); `
        + 'character.dice_log = chr(10).join(line for line in character.dice_log.splitlines() '
        + 'if not line.startswith("Location: ")); '
        + 'character.save(update_fields=["dice_log"])'
    );
    await page.reload();
    return characterId;
}

/** The sheet's Origin select. */
function originSelect(page) {
    return page.locator('[data-field="location"]').getByRole('combobox', { name: 'Origin' });
}

/**
 * Pick an Origin through the sheet's own select and wait for the repaint.
 *
 * Autosave skips a select whose value did not change, so picking the origin
 * creation already rolled saves nothing and repaints nothing. Callers pick a
 * different one; this refuses to wait on a change that cannot come.
 *
 * @param {import('@playwright/test').Page} page
 * @param {string} origin
 */
async function chooseOrigin(page, origin) {
    const select = originSelect(page);
    expect(await select.inputValue(), `the Origin is already ${origin}`).not.toBe(origin);
    await select.selectOption(origin);
    await expect(page.locator('[data-field="location"]')).toHaveClass(/saved/);
    await expect(page.locator('#literacy-check')).toHaveText(CHECKS[origin]);
}

/**
 * The literacy roll the server saved for a character, and the words it reads
 * the row in (``Character.literacy_result``). The spec holds the page to the
 * server's own answer and works out neither the pass mark nor the wording.
 * @param {string} characterId
 * @returns {{roll: number | null, result: string}}
 */
function savedLiteracy(characterId) {
    return readDjangoJson(`
import json
from characters.models import Character
character = Character.objects.get(pk=${Number(characterId)})
print("LITERACY " + json.dumps({"roll": character.literacy_roll, "result": character.literacy_result}))
`, 'LITERACY');
}

/**
 * Hold the row to the server's saved answer for a roll the page showed.
 * @param {import('@playwright/test').Locator} row - The sheet's literacy result
 * @param {string} characterId
 * @param {number} shownRoll - The roll the row printed
 */
async function expectRowToMatchServer(row, characterId, shownRoll) {
    const saved = savedLiteracy(characterId);
    expect(saved.roll, 'the roll the server saved is the one the page showed').toBe(shownRoll);
    await expect(row).toHaveText(saved.result);
}

test.describe('Literacy row (#740, #741)', () => {
    test('changing the Origin repaints the check with no reload', async ({ page }) => {
        await loginAs(page, TEST_USER);
        // A chosen Origin: this chose over creation's roll, which a player
        // may no longer do (#970).
        await createWithChosenOrigin(page);
        const rolledOrigin = await originSelect(page).inputValue();
        // A page-lifetime marker: any reload, however late, wipes it.
        await page.evaluate(() => { window.__noReload = 1; });

        // Every other origin, then back to the one creation rolled, so each
        // step is a real change and Consult GM is always among them.
        const sequence = [...ORIGINS.filter((origin) => origin !== rolledOrigin), rolledOrigin];
        for (const origin of sequence) {
            await chooseOrigin(page, origin);
            // Consult GM has no check to roll, so the control hides; any
            // other origin has one, so it is back.
            if (origin === NO_CHECK_ORIGIN) {
                await expect(page.locator('#roll-literacy-btn')).toBeHidden();
            } else {
                await expect(page.locator('#roll-literacy-btn')).toBeVisible();
            }
        }

        expect(await page.evaluate(() => window.__noReload)).toBe(1);
    });

    test('rolling records the outcome and fixes a player\'s Origin; a Game Master\'s change rolls again', async ({ page, browser }) => {
        await loginAs(page, TEST_USER);
        const characterId = await createWithChosenOrigin(page);
        let origin = await originSelect(page).inputValue();
        if (origin === NO_CHECK_ORIGIN) {
            origin = CHECKED_ORIGINS[0];
            await chooseOrigin(page, origin);
        }
        const result = page.locator('#literacy-result');
        await expect(result).toHaveText('Not rolled');
        await expect(originSelect(page)).toBeEnabled();
        await expect(page.locator('#origin-fixed-note')).toBeHidden();

        await page.locator('#roll-literacy-btn').click();

        await expect(result).toHaveText(/^(Literate|Illiterate) \(rolled \d+ [≤>] \d+/);
        const roll = Number((await result.textContent()).match(/rolled (\d+)/)[1]);
        // The loaded canon's literacy dice bound the roll, not a typed 3 to 18.
        const [lowest, highest] = loadedCharacterRules().literacy_range;
        expect(roll).toBeGreaterThanOrEqual(lowest);
        expect(roll).toBeLessThanOrEqual(highest);
        await expectRowToMatchServer(result, characterId, roll);
        // A player has had their one roll, so the control hides, and their
        // Origin is fixed, with the reason beside it -- no reload.
        await expect(page.locator('#roll-literacy-btn')).toBeHidden();
        await expect(originSelect(page)).toBeDisabled();
        await expect(page.locator('#origin-fixed-note')).toBeVisible();

        // The lock is the server's, not the page's: a hand-built POST is refused.
        const refused = await page.evaluate(async ({ id, value }) => {
            const token = document.querySelector('meta[name="csrf-token"]').content;
            const response = await fetch(`/tarmar/characters/${id}/update-field/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
                body: JSON.stringify({ field: 'location', value }),
            });
            return { status: response.status, body: await response.json() };
        }, { id: characterId, value: anotherOrigin(origin, ORIGINS) });
        expect(refused.status).toBe(400);
        expect(refused.body.error).toContain('only a Game Master can change it');

        await page.reload();
        await expectRowToMatchServer(result, characterId, roll);
        await expect(page.locator('#roll-literacy-btn')).toBeHidden();
        await expect(originSelect(page)).toBeDisabled();
        await expect(originSelect(page)).toHaveValue(origin);
        await expect(page.locator('#origin-fixed-note')).toBeVisible();

        // A Game Master, in a separate browser, may change it; that rolls
        // literacy again against the new origin.
        const context = await browser.newContext();
        try {
            const gameMaster = await context.newPage();
            await startCoverage(gameMaster);
            await submitPassphrase(gameMaster);
            await loginAs(gameMaster, TEST_ADMIN);
            await gameMaster.goto(`/tarmar/characters/${characterId}/`);
            await expect(originSelect(gameMaster)).toBeEnabled();
            await expect(gameMaster.locator('#origin-fixed-note')).toHaveCount(1);
            await expect(gameMaster.locator('#origin-fixed-note')).toBeHidden();

            const next = anotherOrigin(origin);
            await chooseOrigin(gameMaster, next);
            const gmResult = gameMaster.locator('#literacy-result');
            await expect(gmResult).toHaveText(/^(Literate|Illiterate) \(rolled \d+ [≤>] \d+/);
            const reroll = Number((await gmResult.textContent()).match(/rolled (\d+)/)[1]);
            await expectRowToMatchServer(gmResult, characterId, reroll);

            // An origin the Game Master decides publishes no check: the roll is cleared.
            await chooseOrigin(gameMaster, NO_CHECK_ORIGIN);
            await expect(gmResult).toHaveText('Roll cleared; the Game Master decides');
            await expect(gameMaster.locator('#roll-literacy-btn')).toBeHidden();
        } finally {
            await context.close();
        }

        // The player's sheet, reloaded, reads what the Game Master left. The
        // roll is cleared, but literacy was rolled once, so the player is
        // still held to it: no fresh Origin, and no second roll.
        await page.reload();
        await expect(result).toHaveText('Roll cleared; the Game Master decides');
        await expect(page.locator('#literacy-check')).toHaveText(CHECKS[NO_CHECK_ORIGIN]);
        await expect(originSelect(page)).toBeDisabled();
        await expect(page.locator('#origin-fixed-note')).toBeVisible();
        await expect(page.locator('#roll-literacy-btn')).toBeHidden();
    });

    test('a Game Master rules at Consult GM and the ruling fixes the player\'s Origin (#743)', async ({ page, browser }) => {
        await loginAs(page, TEST_USER);
        const characterId = await createWithChosenOrigin(page);
        if (await originSelect(page).inputValue() !== NO_CHECK_ORIGIN) {
            await chooseOrigin(page, NO_CHECK_ORIGIN);
        }
        // A player is offered no ruling: literacy is the Game Master's call here.
        await expect(page.locator('#literacy-ruling-select')).toHaveCount(0);
        await expect(page.locator('#roll-literacy-btn')).toBeHidden();

        const context = await browser.newContext();
        try {
            const gameMaster = await context.newPage();
            await startCoverage(gameMaster);
            await submitPassphrase(gameMaster);
            await loginAs(gameMaster, TEST_ADMIN);
            await gameMaster.goto(`/tarmar/characters/${characterId}/`);
            const ruling = gameMaster.getByRole('combobox', { name: "Game Master's ruling" });
            await expect(ruling).toBeVisible();
            await expect(ruling).toHaveValue('');

            await ruling.selectOption('illiterate');

            const gmResult = gameMaster.locator('#literacy-result');
            await expect(gmResult).toHaveText("Illiterate (Game Master's ruling)");
            await gameMaster.reload();
            await expect(gmResult).toHaveText("Illiterate (Game Master's ruling)");
            await expect(ruling).toHaveValue('illiterate');

            // Away from Consult GM the control hides and the check is rolled
            // where the ruling was, as any Game Master's Origin change rolls it.
            await chooseOrigin(gameMaster, CHECKED_ORIGINS[0]);
            await expect(ruling).toBeHidden();
            await expect(gmResult).toHaveText(/^(Literate|Illiterate) \(rolled \d+ [≤>] \d+\)$/);
            await chooseOrigin(gameMaster, NO_CHECK_ORIGIN);
            await expect(gmResult).toHaveText('Roll cleared; the Game Master decides');
            await expect(ruling).toBeVisible();
            await ruling.selectOption('literate');
            await expect(gmResult).toHaveText("Literate (Game Master's ruling)");
        } finally {
            await context.close();
        }

        // The player reads the ruling, and may not move the Origin out from
        // under it.
        await page.reload();
        await expect(page.locator('#literacy-result')).toHaveText("Literate (Game Master's ruling)");
        await expect(originSelect(page)).toBeDisabled();
        await expect(page.locator('#origin-fixed-note')).toBeVisible();
    });

    test('a rolled Origin is fixed for a player and open to a Game Master (#970)', async ({ page, browser }) => {
        await loginAs(page, TEST_USER);
        await pinDice(page, '**/characters/create/', ROLLED_ORIGIN_SEED);
        await createCharacter(page);
        const characterId = page.url().match(/\/characters\/(\d+)\//)[1];
        // Creation rolled it: text with the reason, no select, and no
        // literacy note beside it, before literacy and after.
        // The seed's face proves the pin took; the loaded canon's table says
        // which Origin that face is, and the scenario needs one with a check.
        await expect(page.locator('#dice-log')).toContainText(`Location: d100=${SEEDED_LOCATION_FACE} `);
        await expect(page.locator('[data-field="location"]')).toHaveCount(0);
        await expect(page.locator('[data-locked-field="location"]')).toHaveText(rolledOriginFixed());
        await expect(page.locator('#origin-fixed-note')).toHaveCount(0);
        const rolled = originForPercentile(SEEDED_LOCATION_FACE);
        expect(CHECKED_ORIGINS, `face ${SEEDED_LOCATION_FACE} is ${rolled}, which has no check to roll`).toContain(rolled);
        expect((await page.locator('#location .physical-table td').first().innerText()).startsWith(rolled)).toBe(true);
        await page.locator('#roll-literacy-btn').click();
        await expect(page.locator('#literacy-result')).toHaveText(/^(Literate|Illiterate) \(rolled/);
        await expect(page.locator('#origin-fixed-note')).toHaveCount(0);
        await expect(page.locator('[data-locked-field="location"]')).toHaveText(rolledOriginFixed());

        // The lock is the server's: a hand-built POST is refused with it.
        const refused = await page.evaluate(async ({ id, value }) => {
            const token = document.querySelector('meta[name="csrf-token"]').content;
            const response = await fetch(`/tarmar/characters/${id}/update-field/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
                body: JSON.stringify({ field: 'location', value }),
            });
            return { status: response.status, body: await response.json() };
        }, { id: characterId, value: anotherOrigin(rolled, ORIGINS) });
        expect(refused.status).toBe(400);
        expect(refused.body.error).toBe(rolledOriginFixed());

        // A Game Master may change it.
        const context = await browser.newContext();
        try {
            const gameMaster = await context.newPage();
            await startCoverage(gameMaster);
            await submitPassphrase(gameMaster);
            await loginAs(gameMaster, TEST_ADMIN);
            await gameMaster.goto(`/tarmar/characters/${characterId}/`);
            await expect(originSelect(gameMaster)).toBeEnabled();
            await expect(gameMaster.locator('[data-locked-field="location"]')).toHaveCount(0);
            await chooseOrigin(gameMaster, anotherOrigin(rolled));
        } finally {
            await context.close();
        }
    });
});
