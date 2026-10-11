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
const { runDjangoShell } = require('./django');
const { pinDice } = require('./dice');

/** Creation's dice pinned (issue #959) so the Origin has a check to roll. */
const ROLLED_ORIGIN_SEED = 7;
/** What creation's location roll gives on that seed. */
const SEEDED_ORIGIN = 'Rural';

/** The check each origin prints, from characters/dice.py's modifiers. */
const CHECKS = {
    City: '3d6 ≤ INT',
    Village: '3d6 ≤ INT − 2',
    Rural: '3d6 ≤ INT − 4',
    'Consult GM': 'Consult GM',
};
const ORIGINS = Object.keys(CHECKS);
const PENALTIES = { City: 0, Village: 2, Rural: 4 };

/** The reason a player's sheet prints beside a rolled Origin (#970). */
const ROLLED_ORIGIN_FIXED = 'The Origin was rolled, and a roll is final; only a Game Master can change it.';

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
 * What the row should read for a roll against an origin at this INT, in the
 * words Character.literacy_result prints.
 * @param {number} roll
 * @param {number} intelligence
 * @param {string} origin
 */
function expectedResult(roll, intelligence, origin) {
    const penalty = PENALTIES[origin];
    const against = penalty ? `${intelligence} − ${penalty}` : `${intelligence}`;
    return roll <= intelligence - penalty
        ? `Literate (rolled ${roll} ≤ ${against})`
        : `Illiterate (rolled ${roll} > ${against})`;
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
            if (origin === 'Consult GM') {
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
        if (origin === 'Consult GM') {
            origin = 'Village';
            await chooseOrigin(page, origin);
        }
        const intelligence = Number(
            // A player's INT is read-only once rolled (issue #748).
            await page.locator('[data-readonly-field="intelligence"]').textContent()
        );
        const result = page.locator('#literacy-result');
        await expect(result).toHaveText('Not rolled');
        await expect(originSelect(page)).toBeEnabled();
        await expect(page.locator('#origin-fixed-note')).toBeHidden();

        await page.locator('#roll-literacy-btn').click();

        await expect(result).toHaveText(/^(Literate|Illiterate) \(rolled \d+ [≤>] \d+/);
        const roll = Number((await result.textContent()).match(/rolled (\d+)/)[1]);
        expect(roll).toBeGreaterThanOrEqual(3);
        expect(roll).toBeLessThanOrEqual(18);
        await expect(result).toHaveText(expectedResult(roll, intelligence, origin));
        // A player has had their one roll, so the control hides, and their
        // Origin is fixed, with the reason beside it -- no reload.
        await expect(page.locator('#roll-literacy-btn')).toBeHidden();
        await expect(originSelect(page)).toBeDisabled();
        await expect(page.locator('#origin-fixed-note')).toBeVisible();

        // The lock is the server's, not the page's: a hand-built POST is refused.
        const refused = await page.evaluate(async (id) => {
            const token = document.querySelector('meta[name="csrf-token"]').content;
            const response = await fetch(`/tarmar/characters/${id}/update-field/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
                body: JSON.stringify({ field: 'location', value: 'City' }),
            });
            return { status: response.status, body: await response.json() };
        }, characterId);
        expect(refused.status).toBe(400);
        expect(refused.body.error).toContain('only a Game Master can change it');

        await page.reload();
        await expect(result).toHaveText(expectedResult(roll, intelligence, origin));
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

            const next = origin === 'Rural' ? 'City' : 'Rural';
            await chooseOrigin(gameMaster, next);
            const gmResult = gameMaster.locator('#literacy-result');
            await expect(gmResult).toHaveText(/^(Literate|Illiterate) \(rolled \d+ [≤>] \d+/);
            const reroll = Number((await gmResult.textContent()).match(/rolled (\d+)/)[1]);
            await expect(gmResult).toHaveText(expectedResult(reroll, intelligence, next));

            // Consult GM publishes no check: the roll is cleared.
            await chooseOrigin(gameMaster, 'Consult GM');
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
        await expect(page.locator('#literacy-check')).toHaveText('Consult GM');
        await expect(originSelect(page)).toBeDisabled();
        await expect(page.locator('#origin-fixed-note')).toBeVisible();
        await expect(page.locator('#roll-literacy-btn')).toBeHidden();
    });

    test('a Game Master rules at Consult GM and the ruling fixes the player\'s Origin (#743)', async ({ page, browser }) => {
        await loginAs(page, TEST_USER);
        const characterId = await createWithChosenOrigin(page);
        if (await originSelect(page).inputValue() !== 'Consult GM') {
            await chooseOrigin(page, 'Consult GM');
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
            await chooseOrigin(gameMaster, 'City');
            await expect(ruling).toBeHidden();
            await expect(gmResult).toHaveText(/^(Literate|Illiterate) \(rolled \d+ [≤>] \d+\)$/);
            await chooseOrigin(gameMaster, 'Consult GM');
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
        await expect(page.locator('#dice-log')).toContainText('Location: d100=');
        await expect(page.locator('[data-field="location"]')).toHaveCount(0);
        await expect(page.locator('[data-locked-field="location"]')).toHaveText(ROLLED_ORIGIN_FIXED);
        await expect(page.locator('#origin-fixed-note')).toHaveCount(0);
        const rolled = (await page.locator('#location .physical-table td').first().innerText())
            .match(/^(City|Village|Rural|Consult GM)/)[1];
        // The seed's Origin, which proves the pin took and has a check.
        expect(rolled).toBe(SEEDED_ORIGIN);
        await page.locator('#roll-literacy-btn').click();
        await expect(page.locator('#literacy-result')).toHaveText(/^(Literate|Illiterate) \(rolled/);
        await expect(page.locator('#origin-fixed-note')).toHaveCount(0);
        await expect(page.locator('[data-locked-field="location"]')).toHaveText(ROLLED_ORIGIN_FIXED);

        // The lock is the server's: a hand-built POST is refused with it.
        const refused = await page.evaluate(async ({ id, value }) => {
            const token = document.querySelector('meta[name="csrf-token"]').content;
            const response = await fetch(`/tarmar/characters/${id}/update-field/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
                body: JSON.stringify({ field: 'location', value }),
            });
            return { status: response.status, body: await response.json() };
        }, { id: characterId, value: rolled === 'City' ? 'Rural' : 'City' });
        expect(refused.status).toBe(400);
        expect(refused.body.error).toBe(ROLLED_ORIGIN_FIXED);

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
            await chooseOrigin(gameMaster, rolled === 'Rural' ? 'City' : 'Rural');
        } finally {
            await context.close();
        }
    });
});
