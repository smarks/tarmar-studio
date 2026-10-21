/**
 * E2E tests for the GM chat's Battle tab (issue #345).
 *
 * A player's "/fight two gray wolves" is a request the Game Master answers
 * (issue #841): the fake Ollama sets the foes with a set_foes answer held to
 * the bestiary catalog, the battle is built through the battle app's setup
 * service, and the page reloads with the board in the panel and the opening
 * line saying who set the foes. The seeded 'Aldwin the Bold' is the party; he is
 * seated to the player, so the first Advance stops at his move and the
 * turn panel offers his options.
 */

const { refusal } = require('./refusals');
const { test, expect, loginAs, TEST_USER, chooseToAttach } = require('./fixtures');
const { runDjangoShell } = require('./django');

/**
 * Seed a character for this worker's player who comes onto the board past
 * any survival save (issue #750): the fight starts from the record's pools
 * (#751), so the engine's own phase 6 kills them on the first turn.
 * @param {string} name
 */
function seedDyingCharacter(name) {
    runDjangoShell(`
from django.contrib.auth import get_user_model
from characters.models import Character

owner = get_user_model().objects.get(username=${JSON.stringify(TEST_USER.username)})
character = Character.objects.create(
    user=owner, name=${JSON.stringify(name)},
    strength=12, dexterity=12, intelligence=12, wisdom=12, constitution=12,
    charisma=12, appearance_roll='3', height_roll='3', weight_roll='3',
    fatigue_roll=7,
)
character.fatigue_damage = character.fatigue + 5
character.body_damage = character.body + 200
character.save()
`);
}

test.describe('GM panel — Battle tab', () => {
    test.beforeEach(async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/gm/');
    });

    test('/fight puts the board beside the chat and the player moves their character', async ({ page }, testInfo) => {
        // Attach Aldwin; the attach creates the session and reloads on it.
        await chooseToAttach(page, 'Aldwin the Bold');
        await page.click('#gm-character-attach');
        await page.waitForURL(/\/tarmar\/gm\/\?session=\d+$/);
        const sessionUrl = page.url();

        // Before the fight: the tab offers the form and is not lit.
        await page.click('#gm-panel-tab-battle');
        await expect(page.locator('#gm-panel-battle')).toBeVisible();
        await expect(page.locator('#gm-battle-empty')).toBeVisible();
        await expect(page.locator('#gm-panel-tab-battle')).not.toHaveClass(/is-live/);
        // The tab no longer widens the panel: since issue #351 the width is
        // the viewer's, dragged on the panel's own handle.
        await expect(page.locator('#gm-panel')).not.toHaveClass(/gm-panel-wide/);

        // The slash command from the chat input.
        await page.fill('#user-input', '/fight two gray wolves');
        await page.press('#user-input', 'Enter');

        // The page reloads on the same session with the Battle tab up.
        const board = page.locator('#gm-battle');
        await expect(board).toBeVisible();
        expect(page.url()).toBe(sessionUrl);
        await expect(page.locator('#gm-panel-tab-battle')).toHaveAttribute('aria-pressed', 'true');
        await expect(page.locator('#gm-panel-tab-battle')).toHaveClass(/is-live/);
        await expect(page.locator('#gm-fight-start')).toBeDisabled();
        await expect(page.locator('.gm-battle-name a')).toHaveAttribute('href', /\/tarmar\/battles\/\d+\/$/);
        await expect(page.locator('#battle-status')).toHaveText('Setup');
        await expect(page.locator('#battle-turn')).toHaveText('0');

        // The board: three figures on the map, the roster by team, the GM's line.
        await expect(page.locator('#battle-arena .arena-combatant')).toHaveCount(3);
        const roster = page.locator('#battle-roster');
        await expect(roster).toContainText('Aldwin the Bold');
        await expect(roster).toContainText('Gray Wolf (2)');
        await expect(roster).toContainText('Party');
        await expect(roster).toContainText('Foes');
        await expect(roster).toContainText('played by you');
        await expect(page.locator('.message.assistant').last()).toContainText('The fight moves to the board');
        // What the player typed was a request; the line says who set the foes (#841).
        await expect(page.locator('.message.assistant').last())
            .toContainText('You asked for "two gray wolves"; the Game Master set the foes.');
        await page.screenshot({ path: testInfo.outputPath('battle-tab.png'), fullPage: true });

        // Advance: the turn stops at Aldwin, whose move is the player's.
        await page.click('#advance-turn');
        const panel = page.locator('#turn-panel');
        await expect(panel).toBeVisible();
        await expect(panel).toContainText('Aldwin the Bold');
        await expect(page.locator('#advance-turn')).toBeDisabled();
        await panel.locator('.turn-option').first().click();
        await panel.locator('.turn-confirm').click();

        // The turn commits: the log shows turn 1 and the counter moves.
        await expect(page.locator('#battle-log .turn-group[data-turn="1"]')).toBeVisible();
        await expect(page.locator('#battle-turn')).toHaveText('1');
        await expect(page.locator('#advance-turn')).toHaveText('Advance turn');

        // The same battle is on its own page too. Its foes belong to the
        // Game Master (#762): the player advances and plays Aldwin, and has
        // no restart and no control desk. This used to assert the desk was
        // there, which is the bug.
        await expect(page.locator('#restart-battle')).toHaveCount(0);
        await page.locator('.gm-battle-name a').click();
        await page.waitForURL(/\/tarmar\/battles\/\d+\/$/);
        await expect(page.locator('#battle-turn')).toHaveText('1');
        await expect(page.locator('#advance-turn')).toBeVisible();
        await expect(page.locator('#battle-control')).toHaveCount(0);
        await expect(page.locator('#restart-battle')).toHaveCount(0);
    });

    test('a death on the board is on the sheet before anyone says a word (#750)', async ({ page }, testInfo) => {
        const name = `Dain ${testInfo.workerIndex}-${Date.now() % 100000}`;
        seedDyingCharacter(name);
        await page.goto('/tarmar/gm/');
        await chooseToAttach(page, name);
        await page.click('#gm-character-attach');
        await page.waitForURL(/\/tarmar\/gm\/\?session=\d+$/);

        // The Characters pane reads the record's pools, not the maxima (#751).
        await page.click('#gm-panel-tab-characters');
        const sheet = page.locator('.gm-sheet', { hasText: name });
        await expect(sheet.locator('.gm-sheet-body')).toHaveText('-200/37');
        await expect(sheet.locator('.gm-sheet-condition')).toContainText('survival save each turn');

        await page.fill('#user-input', '/fight a gray wolf');
        await page.press('#user-input', 'Enter');
        await expect(page.locator('#gm-battle')).toBeVisible();
        // The session's foes are the Game Master's, and so is the restart.
        await expect(page.locator('#restart-battle')).toHaveCount(0);
        // A session's fight counts, and the tab says so while it is on.
        await expect(page.locator('#battle-counts-note')).toContainText('This battle counts');

        // One turn: the survival save cannot be made, and the fight is over.
        await page.click('#advance-turn');
        await expect(page.locator('#battle-status')).toHaveText('Finished');
        await page.screenshot({ path: testInfo.outputPath('death-on-the-board.png'), fullPage: true });

        // No chat message has been sent and the page has not been reloaded:
        // the Characters tab refetched when the board applied the turn, and
        // it already says what the board did.
        await page.click('#gm-panel-tab-characters');
        await expect(page.locator('.gm-sheet', { hasText: name }).locator('.gm-sheet-status'))
            .toContainText('Dead — Killed in battle');
        await expect(page.locator('.gm-sheet', { hasText: name }).locator('.gm-sheet-condition'))
            .toHaveText('Dead');
        const href = await page.locator('.gm-sheet', { hasText: name })
            .locator(`a[href^="/tarmar/characters/"]`).first().getAttribute('href');
        await page.goto(href);
        await expect(page.locator('.death-card .death-cause')).toContainText('Killed in battle');
    });

    test('a fight is refused while the Game Master is still answering (#836)', async ({ page }) => {
        await chooseToAttach(page, 'Aldwin the Bold');
        await page.click('#gm-character-attach');
        await page.waitForURL(/\/tarmar\/gm\/\?session=\d+$/);

        // A turn the fake model answers slowly, and the Fight button meanwhile.
        await page.fill('#user-input', 'We look around slowly');
        await page.press('#user-input', 'Enter');
        await expect(page.locator('.message.assistant').last()).toContainText('considers it');
        await page.click('#gm-fight-button');

        const error = page.locator('#gm-panel-battle-error');
        await expect(error).toBeVisible();
        await expect(error).toContainText(refusal('gm', 'views', 'fight_while_turn_running'));
        await expect(page.locator('#gm-battle')).toHaveCount(0);
        // The turn ends as it would have, with no fight in its history.
        await expect(page.locator('.message.assistant').last()).toContainText('nothing stirs', { timeout: 30000 });
    });

    test('the Fight button refuses without a character and says so in the tab', async ({ page }) => {
        await page.click('#gm-fight-button');
        await expect(page.locator('#gm-panel-tab-battle')).toHaveAttribute('aria-pressed', 'true');
        const error = page.locator('#gm-panel-battle-error');
        await expect(error).toBeVisible();
        await expect(error).toHaveText('Attach a character or send a message first.');
    });
});
