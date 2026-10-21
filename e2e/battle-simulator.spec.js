/**
 * E2E test for the battle royale simulator (issue #176).
 *
 * What only a real browser can prove: creating a battle from the form,
 * advancing two turns through the Advance button, and seeing the dice faces
 * and an AI decision rationale actually painted into the page — then that a
 * full reload reproduces the same log from the database (state-driven, no
 * transient UI).
 */

const { test, expect, createBattleViaSetup } = require('./fixtures');
const { uniqueName } = require('./unique-name');
const { readDjangoShell } = require('./django');
const { menuWords, sentencePattern } = require('./turn-menu');

test.describe('Battle simulator', () => {
    test('create, advance two turns, see rolls and decisions, survive reload', async ({ adminPage }) => {
        // Unique name per run so CI retries never collide.
        const battleName = uniqueName('E2E Skirmish');

        await createBattleViaSetup(adminPage, { name: battleName, fighters: 2, seed: 42 });

        // Landed on the detail page with the arena rendered from state.
        await expect(adminPage.locator('h1')).toHaveText(battleName);
        await expect(adminPage.locator('#battle-arena .arena-combatant')).toHaveCount(2);
        await expect(adminPage.locator('.combatant-card')).toHaveCount(2);

        // Advance two turns.
        for (const turn of [1, 2]) {
            await adminPage.click('#advance-turn');
            await expect(adminPage.locator(`.turn-group[data-turn="${turn}"]`)).toBeVisible();
            await expect(adminPage.locator('#battle-turn')).toHaveText(String(turn));
        }

        // Every roll shows its faces: the initiative d6 line has the
        // "[face] = total" shape.
        const initiativeRoll = adminPage.locator('.event-roll', { hasText: 'initiative 1d6' }).first();
        await expect(initiativeRoll).toContainText(/1d6: \[\d\]( [+-]\d+)? = -?\d+/);

        // A decision is present and expands into scored candidates with a
        // one-line rationale each.
        const decision = adminPage.locator('.event-decision').first();
        await expect(decision).toContainText('chooses');
        await decision.locator('summary').click();
        await expect(decision.locator('ul li').first()).toContainText('score');

        // Full reload reproduces everything from the DB.
        await adminPage.reload();
        await expect(adminPage.locator('.turn-group[data-turn="1"]')).toBeVisible();
        await expect(adminPage.locator('.turn-group[data-turn="2"]')).toBeVisible();
        await expect(
            adminPage.locator('.event-roll', { hasText: 'initiative 1d6' }).first()
        ).toContainText(/1d6: \[\d\]( [+-]\d+)? = -?\d+/);
        await expect(adminPage.locator('#battle-turn')).toHaveText('2');
        await expect(adminPage.locator('#battle-arena .arena-combatant').first()).toBeVisible();
    });

    test('the log shows every die and its verdict, and the toggle only hides them', async ({ adminPage }) => {
        // #301: a reader must be able to check the numbers. Only a browser
        // proves the attack line reaches the page with its verdict, that the
        // forecast is labelled as one, and that the dice checkbox hides the
        // roll lines without dropping them from the page or the database.
        const battleName = uniqueName('E2E Dice');

        await createBattleViaSetup(adminPage, { name: battleName, fighters: 2, seed: 42 });

        // Advance until an attack has actually been resolved.
        for (let turn = 1; turn <= 6; turn += 1) {
            await adminPage.click('#advance-turn');
            await expect(adminPage.locator(`.turn-group[data-turn="${turn}"]`)).toBeVisible();
            if (await adminPage.locator('.event-roll', { hasText: 'attack 1d20' }).count()) {
                break;
            }
        }

        // The d20 line carries its modifier, its Target Number, which way the
        // comparison runs, and how it came out.
        const attack = adminPage.locator('.event-roll', { hasText: 'attack 1d20' }).first();
        await expect(attack).toContainText(/1d20: \[\d+\]( [+-]\d+)? = -?\d+ vs TN \d+ \(need \d+\+\) — [a-z]+/);

        // The deliberation says it is a forecast, and its factors multiply
        // out to the score printed beside the option.
        const decision = adminPage.locator('.event-decision').first();
        await expect(decision).toContainText('Forecast —');
        await decision.locator('summary').click();
        const candidate = await decision.locator('ul li').first().textContent();
        const printedScore = candidate.match(/score (-?\d+\.\d+):/);
        expect(printedScore).not.toBeNull();
        if (candidate.includes('=')) {
            expect(candidate.trim().endsWith(printedScore[1])).toBe(true);
        }

        // The toggle is a permanent control, checked by default.
        const toggle = adminPage.locator('#battle-log-dice');
        await expect(toggle).toBeVisible();
        await expect(toggle).toBeChecked();
        await expect(attack).toBeVisible();

        // Unchecking hides the roll lines; they stay in the DOM, and the
        // control stays on the page.
        await toggle.uncheck();
        await expect(attack).toBeHidden();
        await expect(adminPage.locator('#battle-log .event-roll')).not.toHaveCount(0);
        await expect(toggle).toBeVisible();

        // State-driven: the choice survives a reload, and re-checking brings
        // the dice back — nothing was lost at write time.
        await adminPage.reload();
        await expect(adminPage.locator('#battle-log-dice')).not.toBeChecked();
        await expect(
            adminPage.locator('.event-roll', { hasText: 'attack 1d20' }).first()
        ).toBeHidden();
        await adminPage.locator('#battle-log-dice').check();
        await expect(
            adminPage.locator('.event-roll', { hasText: 'attack 1d20' }).first()
        ).toBeVisible();
    });

    test('a beast joins the battle, appears on the arena, and rolls', async ({ adminPage }) => {
        const battleName = uniqueName('E2E Beast Pit');

        // The bestiary catalog (loaded by global-setup) feeds the picker.
        await createBattleViaSetup(adminPage, {
            name: battleName, fighters: 2, seed: 7, beasts: ['dire-wolf'],
        });

        // Two generated characters plus the wolf, on cards and the arena.
        await expect(adminPage.locator('h1')).toHaveText(battleName);
        await expect(adminPage.locator('.combatant-card')).toHaveCount(3);
        const wolfCard = adminPage.locator('.combatant-card', { hasText: 'Dire Wolf' });
        await expect(wolfCard).toContainText('beast — Bite');
        await expect(adminPage.locator('#battle-arena .arena-combatant')).toHaveCount(3);
        await expect(adminPage.locator('#battle-arena .arena-combatant.is-beast')).toHaveCount(1);

        // Advance a turn: the wolf's own initiative roll shows its faces.
        await adminPage.click('#advance-turn');
        await expect(adminPage.locator('.turn-group[data-turn="1"]')).toBeVisible();
        await expect(
            adminPage.locator('.event-roll', { hasText: 'Dire Wolf initiative 1d6' })
        ).toContainText(/1d6: \[\d\]( [+-]\d+)? = -?\d+/);
    });

    test('Restart rewinds to turn 0 and replays the same battle', async ({ adminPage }) => {
        const battleName = uniqueName('E2E Restart');

        await createBattleViaSetup(adminPage, { name: battleName, fighters: 2, seed: 99 });
        await expect(adminPage.locator('h1')).toHaveText(battleName);

        // Run a turn, then remember what its first log line said.
        await adminPage.click('#advance-turn');
        await expect(adminPage.locator('.turn-group[data-turn="1"]')).toBeVisible();
        const firstLine = await adminPage
            .locator('.turn-group[data-turn="1"] .battle-event').first().textContent();

        // Restart asks in place — no modal — and Keep the log backs out.
        await adminPage.click('#restart-battle');
        await expect(adminPage.locator('#restart-confirm')).toBeVisible();
        await expect(adminPage.locator('.confirm-overlay')).toHaveCount(0);
        await adminPage.click('#restart-cancel');
        await expect(adminPage.locator('.turn-group[data-turn="1"]')).toBeVisible();
        await expect(adminPage.locator('#battle-turn')).toHaveText('1');

        // Confirming clears the log and puts the battle back at turn 0.
        await adminPage.click('#restart-battle');
        await adminPage.click('#restart-confirm');
        await expect(adminPage.locator('#battle-turn')).toHaveText('0');
        await expect(adminPage.locator('#battle-status')).toHaveText('Setup');
        await expect(adminPage.locator('.turn-group')).toHaveCount(0);
        await expect(adminPage.locator('#battle-log-empty')).toBeVisible();

        // State-driven: the reload agrees, and the replayed turn 1 is the
        // same turn 1 (same roster, same seed).
        await adminPage.reload();
        await expect(adminPage.locator('#battle-turn')).toHaveText('0');
        await expect(adminPage.locator('.turn-group')).toHaveCount(0);
        await adminPage.click('#advance-turn');
        await expect(adminPage.locator('.turn-group[data-turn="1"]')).toBeVisible();
        await expect(
            adminPage.locator('.turn-group[data-turn="1"] .battle-event').first()
        ).toHaveText(firstLine);
    });

    test('the command bar separates Advance from Restart and All battles', async ({ adminPage }) => {
        const battleName = uniqueName('E2E Command Bar');

        await createBattleViaSetup(adminPage, { name: battleName, fighters: 2, seed: 21 });

        // Advance sits in the primary well; Restart and All battles sit in
        // the secondary cluster, which is what "separate the buttons" means.
        const bar = adminPage.locator('.battle-command-bar');
        await expect(bar.locator('#advance-turn')).toBeVisible();
        await expect(bar.locator('.battle-actions-secondary #restart-battle')).toBeVisible();
        const newBattle = bar.locator('.battle-actions-secondary a', { hasText: 'All battles' });
        await expect(newBattle).toBeVisible();

        // The page uses the wide column, not the 600px default.
        const mainWidth = await adminPage.locator('main.battle-main')
            .evaluate((node) => node.getBoundingClientRect().width);
        expect(mainWidth).toBeGreaterThan(600);

        // Narrow viewport: the actions stack instead of crowding one row.
        await adminPage.setViewportSize({ width: 390, height: 844 });
        const advanceBox = await adminPage.locator('#advance-turn').boundingBox();
        const restartBox = await adminPage.locator('#restart-battle').boundingBox();
        expect(restartBox.y).toBeGreaterThan(advanceBox.y + advanceBox.height - 1);

        // And All battles goes back to the list (issue #339, item 30).
        await newBattle.click();
        await expect(adminPage.locator('h1')).toHaveText('Battle Royale Simulator');
    });

    test('the DM renames a combatant inline and the new name sticks', async ({ adminPage }) => {
        const battleName = uniqueName('E2E Rename');

        await createBattleViaSetup(adminPage, { name: battleName, fighters: 2, seed: 11 });
        await expect(adminPage.locator('h1')).toHaveText(battleName);

        // Click the roster name, type a new one, Enter saves inline —
        // no modal anywhere (house rule).
        const firstName = adminPage.locator('.combatant-card .combatant-name').first();
        await firstName.click();
        const editor = adminPage.locator('.combatant-rename-input');
        await editor.fill('Torin Blackbriar');
        await editor.press('Enter');

        await expect(
            adminPage.locator('.combatant-card .combatant-name').first()
        ).toHaveText('Torin Blackbriar');
        await expect(
            adminPage.locator('#battle-arena text', { hasText: 'Torin Blackbriar' })
        ).toBeVisible();

        // State-driven: a full reload reproduces the rename from the DB.
        await adminPage.reload();
        await expect(
            adminPage.locator('.combatant-card .combatant-name').first()
        ).toHaveText('Torin Blackbriar');
        await expect(
            adminPage.locator('#battle-arena text', { hasText: 'Torin Blackbriar' })
        ).toBeVisible();
    });

    test('a rename being typed survives the roster poll, and is not saved half-typed (#16)', async ({ adminPage }) => {
        // A battle awaiting the DM's own move: his page polls the state while
        // it waits, and a poll that brings anything new redraws the roster.
        await createBattleViaSetup(adminPage, {
            name: uniqueName('E2E Rename poll'), fighters: 3, seed: 7, controlMode: 'manual',
        });
        const battleId = new URL(adminPage.url()).pathname.match(/battles\/(\d+)\//)[1];
        await adminPage.click('#advance-turn');
        await expect(adminPage.locator('#turn-panel')).toContainText(new RegExp(sentencePattern(menuWords().panel.your_move)));

        const card = adminPage.locator('#battle-roster [data-combatant]').first();
        const combatantId = await card.getAttribute('data-combatant');
        await card.locator('.combatant-name').click();
        const editor = adminPage.locator('.combatant-rename-input');
        await editor.fill('Torin Bl');
        await card.evaluate((node) => { node.dataset.beforePoll = 'yes'; });

        // Meanwhile another seat renames that figure, and the log moves on:
        // the next poll has something new and redraws the roster.
        readDjangoShell(`
from battle.services import rename_combatant, set_battle_control_mode
rename_combatant(${battleId}, ${combatantId}, 'Shell Renamed')
set_battle_control_mode(${battleId}, 'manual')
print('DONE')
`);
        await expect(adminPage.locator('#battle-roster [data-before-poll]')).toHaveCount(0);

        // The edit goes on in the redrawn card, focused, with what was typed,
        // and says what the figure was renamed to meanwhile.
        await expect(editor).toHaveValue('Torin Bl');
        await expect(editor).toBeFocused();
        await expect(adminPage.locator('.rename-changed-note')).toHaveText('Renamed meanwhile to "Shell Renamed".');
        // The redraw's removal blurred the old field; nothing was saved by it.
        const midway = readDjangoShell(`
from battle.models import BattleCombatant
print('NAME', BattleCombatant.objects.get(pk=${combatantId}).display_name)
`);
        expect(midway).toContain('NAME Shell Renamed');

        await adminPage.keyboard.type('ackbriar');
        await adminPage.keyboard.press('Enter');
        await expect(adminPage.locator(`#battle-roster [data-combatant="${combatantId}"] .combatant-name`))
            .toHaveText('Torin Blackbriar');
        await expect(adminPage.locator('.rename-changed-note')).toHaveCount(0);
    });
});
