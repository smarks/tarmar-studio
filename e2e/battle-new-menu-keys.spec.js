/**
 * The options tarmar-engine 0.9.5 added to the turn menu, in a real browser.
 *
 * The engine's legal menu gained keys a player picks by hand: `sprint`, the
 * yielded movers (`a_yield`, `sprint_yield`, `b_yield`, `c_yield`), `l` One
 * Last Shot, `f` for a thrown weapon, `u` DRAW DAGGER, and PICK UP / CHANGE /
 * READY WEAPON (`q`/`m`/`e`). The turn panel draws whatever the pending menu
 * holds, so each arrives as a row a player can pick, badged with the rules'
 * letter (`optionLetter` in battle.js: Sprint is MOVE, a) and named by the
 * engine. These cases drive two of them end to end: a Sprint, carried out in
 * Initial Movement for the Sprint's 6 Fatigue (movement.md), and a yielded
 * Sprint, carried out in Final Movement instead (turn-sequence.md).
 */

const { test, expect, createBattleViaSetup } = require('./fixtures');

/**
 * Start a two-duelist battle played by hand and stop at the first figure.
 *
 * @param {import('@playwright/test').Page} page - a Game Master's page
 * @param {string} label - names the battle
 * @returns {Promise<string>} the awaited figure's name
 */
async function firstManualTurn(page, label) {
    await createBattleViaSetup(page, {
        name: `E2E ${label} ${Date.now()}`,
        fighters: ['unarmoured duelist', 'unarmoured duelist'],
        seed: 42,
        controlMode: 'manual',
    });
    await page.click('#advance-turn');
    await expect(page.locator('#advance-turn')).toHaveText(/^Waiting on .+'s move$/);
    const waitingLabel = await page.locator('#advance-turn').textContent();
    return waitingLabel.replace('Waiting on ', '').replace("'s move", '').trim();
}

/**
 * Take the option with this exact name for the awaited figure.
 *
 * @param {import('@playwright/test').Page} page
 * @param {string} name - the engine's printed option name
 */
async function choose(page, name) {
    const panel = page.locator('#turn-panel');
    const option = panel.locator('.turn-option').filter({
        has: page.locator('.turn-option-name', { hasText: new RegExp(`^${name.replace(/[()]/g, '\\$&')}$`) }),
    });
    await expect(option).toHaveCount(1);
    await option.click();
    await expect(option).toHaveAttribute('aria-pressed', 'true');
    await panel.locator('.turn-confirm').click();
    return option;
}

/**
 * Let the second duelist hold its ground, so the turn commits: DODGE while
 * disengaged (it moves only toward a missile threat, and there is none),
 * DEFEND once the first figure has closed on it.
 *
 * @param {import('@playwright/test').Page} page
 * @param {string} mover - the first figure, no longer awaited
 */
async function secondFigureHolds(page, mover) {
    await expect(page.locator('#advance-turn')).not.toHaveText(`Waiting on ${mover}'s move`);
    const names = page.locator('#turn-panel .turn-option-name');
    await expect(names.first()).toBeVisible();
    const offered = (await names.allTextContents()).map((name) => name.trim());
    await choose(page, offered.includes('DODGE') ? 'DODGE' : 'DEFEND');
}

/**
 * Whether any log line holds ``text``.
 *
 * @param {string[]} lines
 * @param {string} text
 */
function logHas(lines, text) {
    return lines.some((line) => line.includes(text));
}

/**
 * The turn's log lines, in order, once the turn has committed.
 *
 * @param {import('@playwright/test').Page} page
 * @returns {Promise<string[]>}
 */
async function turnOneLines(page) {
    const turnOne = page.locator('.turn-group[data-turn="1"]');
    await expect(turnOne).toBeVisible();
    await expect(page.locator('#battle-turn')).toHaveText('1');
    return (await turnOne.locator('.battle-event').allTextContents())
        .map((line) => line.replace(/\s+/g, ' ').trim());
}

test.describe('The 0.9.5 menu keys', () => {
    test('a player sprints from the turn menu', async ({ adminPage }) => {
        const sprinter = await firstManualTurn(adminPage, 'Sprint');
        const panel = adminPage.locator('#turn-panel');
        // The yielded variant is offered beside it, and both carry the rules'
        // letter a in the badge rather than the engine's token.
        const yielded = panel.locator('.turn-option').filter({
            has: adminPage.locator('.turn-option-name', {
                hasText: 'MOVE (SPRINT) (YIELD: MOVE IN FINAL MOVEMENT)',
            }),
        });
        await expect(yielded.locator('.turn-option-letter')).toHaveText('a');
        const sprint = await choose(adminPage, 'MOVE (SPRINT)');
        await expect(sprint.locator('.turn-option-letter')).toHaveText('a');
        await secondFigureHolds(adminPage, sprinter);

        const lines = await turnOneLines(adminPage);
        const decision = lines.findIndex((line) => line.includes(
            `${sprinter} chooses MOVE (SPRINT) (sprint): chosen by`
        ));
        const moved = lines.findIndex((line) => new RegExp(
            `${sprinter} sprints \\d+ hex\\(es\\) toward`
        ).test(line));
        const finalMovement = lines.findIndex((line) => line.startsWith('Phase 4'));
        expect(decision).toBeGreaterThanOrEqual(0);
        // Initial Movement: before Phase 4 begins.
        expect(moved).toBeGreaterThan(decision);
        expect(moved).toBeLessThan(finalMovement);
        expect(logHas(lines, `${sprinter} spends 6 fatigue sprinting`)).toBe(true);
    });

    test('a player yields a sprint to Final Movement', async ({ adminPage }) => {
        const sprinter = await firstManualTurn(adminPage, 'YieldSprint');
        await choose(adminPage, 'MOVE (SPRINT) (YIELD: MOVE IN FINAL MOVEMENT)');
        await secondFigureHolds(adminPage, sprinter);

        const lines = await turnOneLines(adminPage);
        expect(logHas(
            lines,
            `${sprinter} chooses MOVE (SPRINT) (YIELD: MOVE IN FINAL MOVEMENT) (sprint_yield): chosen by`
        )).toBe(true);
        const moved = lines.findIndex((line) => new RegExp(
            `${sprinter} sprints \\d+ hex\\(es\\) toward`
        ).test(line));
        const finalMovement = lines.findIndex((line) => line.startsWith('Phase 4'));
        const actions = lines.findIndex((line) => line.startsWith('Phase 5'));
        // The move waited for Final Movement, and still cost the Sprint.
        expect(finalMovement).toBeGreaterThanOrEqual(0);
        expect(moved).toBeGreaterThan(finalMovement);
        expect(moved).toBeLessThan(actions);
        expect(logHas(lines, `${sprinter} spends 6 fatigue sprinting`)).toBe(true);
    });
});
