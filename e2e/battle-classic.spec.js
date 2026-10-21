/**
 * E2E for creating, playing and WATCHING a classic-profile battle (#251).
 *
 * What only a real browser can prove: that the create form's rules-profile
 * choice reaches the server, that the battle it makes is playable, that a
 * turn advanced through the Advance button lands in the log and survives a
 * reload — and, since this slice, that the arena and roster actually paint.
 *
 * The renderer slice is exactly why the last part is here. Slice 6 shipped
 * with a passing Django view test (status 200) and a page that drew nothing:
 * both panels are painted in the browser, so a 200 is no evidence at all
 * that a classic battle is watchable. Only a browser can say that.
 */

const {
    test, expect, TEST_USER, loginAs, submitPassphrase, createBattleViaSetup,
} = require('./fixtures');
const { uniqueName } = require('./unique-name');
const { startCoverage, stopCoverage } = require('./coverage');

test.describe('Classic-profile battle', () => {
    test('create as classic, watch it, advance a turn, log survives reload', async ({ adminPage }) => {
        const battleName = uniqueName('E2E Classic');

        await createBattleViaSetup(adminPage, {
            name: battleName, fighters: 2, seed: 4242,
            rulesProfile: 'classic-melee',
        });

        await expect(adminPage.locator('h1')).toHaveText(battleName);
        // The renderer boundary is gone with the notice that described it.
        await expect(adminPage.locator('.battle-notice')).toHaveCount(0);

        // The field is painted: the classic arena is a bounded rectangle of
        // 9x15 hexes, so many more cells than the two figure markers.
        const arena = adminPage.locator('#battle-arena');
        await expect(arena.locator('polygon').first()).toBeVisible();
        expect(await arena.locator('polygon').count()).toBeGreaterThan(100);

        // A marker per figure, and a roster card per figure — the two panels
        // that rendered empty before this slice.
        await expect(arena.locator('.arena-combatant')).toHaveCount(2);
        await expect(adminPage.locator('.combatant-card')).toHaveCount(2);

        // A classic figure's damage lands on ST, so ST is the bar it shows —
        // not a fatigue/body pair borrowed from the other rules.
        await expect(adminPage.locator('.combatant-card .pool-bar.st').first())
            .toBeVisible();
        await expect(adminPage.locator('.combatant-card').first())
            .toContainText('ST');
        await expect(adminPage.locator('.combatant-card .pool-bar.fatigue'))
            .toHaveCount(0);

        // It plays: one turn through the same Advance button.
        await adminPage.click('#advance-turn');
        await expect(adminPage.locator('.turn-group[data-turn="1"]')).toBeVisible();
        // And the arena re-renders from the advance response, not a reload.
        await expect(arena.locator('.arena-combatant').first()).toBeVisible();

        // State-driven, not transient: a full reload rebuilds the same log
        // from the BattleEvent rows, and repaints the same field.
        //
        // Count only once the response is fully applied. The turn label is
        // written after the log is appended (applyBattleResponse), so waiting
        // on it avoids counting a log that is still being built — which is
        // how this assertion first failed here, at 1 line of an eventual 20.
        await expect(adminPage.locator('#battle-turn')).toHaveText('1');
        const loggedLines = await adminPage.locator('.turn-group[data-turn="1"] li').count();
        expect(loggedLines).toBeGreaterThan(0);
        await adminPage.reload();
        await expect(adminPage.locator('.turn-group[data-turn="1"] li')).toHaveCount(loggedLines);
        await expect(adminPage.locator('.combatant-card')).toHaveCount(2);
    });

    test('a classic wizard casts a classic spell', async ({ adminPage }) => {
        // Slice 8: a studio caster crosses as a classic WIZARD, so classic
        // magic is live in this app for the first time. Only a browser run
        // proves the whole chain — the crossing builds a wizard, the v0.8.0
        // policy offers it a spell, the turn loop carries the declaration
        // into the combat phase, and the cast reaches the log.
        test.setTimeout(120000);
        const battleName = uniqueName('E2E Classic Wizard');

        await createBattleViaSetup(adminPage, {
            name: battleName, fighters: ['wizard', 'wizard'], seed: 9119,
            rulesProfile: 'classic-melee',
        });

        await expect(adminPage.locator('h1')).toHaveText(battleName);
        // The opening note records the crossing itself: the studio's own
        // spells are still refused, and the caster is a wizard regardless.
        await expect(adminPage.locator('#battle-log')).toContainText('classic wizard');

        // A cast within a few turns. Which spell and which turn are the
        // dice's business, so the assertion is that magic happened at all —
        // every classic spell name comes from the one catalog.
        const spellNames = /Magic Fist|Blur|Drop Weapon|Slow Movement|Clumsiness/;
        for (let turn = 0; turn < 6; turn += 1) {
            if (await adminPage.locator('#battle-log').filter({ hasText: spellNames }).count()) {
                break;
            }
            if (await adminPage.locator('#advance-turn').isDisabled()) { break; }
            await adminPage.click('#advance-turn');
            await expect(adminPage.locator('#battle-turn')).toHaveText(String(turn + 1));
        }
        await expect(adminPage.locator('#battle-log')).toContainText(spellNames);
    });

    test('a player seats into a classic lobby and plays a classic turn', async ({ adminPage, browser }, testInfo) => {
        // Slice 9, and the gate on everything player-facing: until now
        // create_battle REFUSED player seats for a classic battle, because
        // the lobby assembled state_json from Tarmar per-row snapshots and
        // classic had no assembly of its own. So no human had ever been
        // handed a classic decision.
        //
        // Two browsers are the only thing that can prove the whole chain:
        // the DM opens a classic lobby, a second context claims a seat and
        // readies, the start crosses the SETTLED roster (the claimed
        // character included) into classic figures, and the paused turn
        // offers that player a classic menu whose choice is theirs.
        test.setTimeout(120000);
        const battleName = uniqueName('E2E Classic Lobby');

        // The DM opens a classic battle with one AI fighter and one seat.
        await createBattleViaSetup(adminPage, {
            name: battleName, fighters: 1, seed: 515, playerSeats: 1,
            rulesProfile: 'classic-melee',
        });

        // It is a lobby, and the lobby says which rules are being joined —
        // what a player brings is rebuilt at the crossing, so being told
        // after the fact would be too late to choose differently.
        await expect(adminPage.locator('h1')).toHaveText(battleName);
        await expect(adminPage.locator('#battle-status')).toHaveText('Lobby');
        await expect(adminPage.locator('.lobby-rules')).toContainText('Classic Melee');
        await expect(adminPage.locator('.lobby-seat.is-open')).toHaveCount(1);
        const battleUrl = adminPage.url();

        // A player arrives in a separate context and takes the seat.
        const playerContext = await browser.newContext();
        const playerPage = await playerContext.newPage();
        await startCoverage(playerPage);
        await submitPassphrase(playerPage);
        await loginAs(playerPage, TEST_USER);
        await playerPage.goto(battleUrl);
        await expect(playerPage.locator('.lobby-rules')).toContainText('Classic Melee');
        await playerPage.click('summary:has-text("Claim this seat")');
        await playerPage.selectOption('select[name="archetype"]', 'archer');
        await playerPage.click('button:has-text("Join with a generated fighter")');
        await expect(playerPage.locator('.lobby-seat.is-you')).toBeVisible();
        await playerPage.click('button:has-text("I\'m ready")');
        await expect(playerPage.locator('.lobby-ready-chip.is-ready')).toBeVisible();

        // The DM starts, and the classic assembly runs over the settled
        // roster: a bounded rectangular field, a marker and card per figure,
        // ST rather than the Tarmar pools. The seated player's character is
        // one of the figures — it crossed from the same per-row snapshot.
        await expect(adminPage.locator('.lobby-ready-chip.is-ready'))
            .toBeVisible({ timeout: 15000 });
        await adminPage.click('.battle-start-form button[type="submit"]');
        await expect(adminPage.locator('#battle-status')).toHaveText('Setup');
        const arena = adminPage.locator('#battle-arena');
        expect(await arena.locator('polygon').count()).toBeGreaterThan(100);
        await expect(arena.locator('.arena-combatant')).toHaveCount(2);
        await expect(adminPage.locator('.combatant-card .pool-bar.st').first())
            .toBeVisible();
        await expect(adminPage.locator('.combatant-card .pool-bar.fatigue'))
            .toHaveCount(0);

        // The player's page lands on the started battle, their figure theirs.
        await expect(playerPage.locator('#battle-arena'))
            .toBeVisible({ timeout: 15000 });
        await expect(playerPage.locator('.combatant-card', { hasText: 'Played by you' }))
            .toBeVisible();

        // The DM advances: the turn reaches the seated figure and parks.
        // This is the assertion the whole slice exists for — under classic
        // rules a human is asked, rather than quietly played by the AI.
        await adminPage.click('#advance-turn');
        await expect(adminPage.locator('#battle-status'))
            .toHaveText('Awaiting players');

        // The player is offered the classic menu and takes an option.
        const firstOption = playerPage.locator('#turn-panel .turn-option').first();
        await expect(firstOption).toBeVisible({ timeout: 15000 });
        await firstOption.click();
        await expect(firstOption).toHaveAttribute('aria-pressed', 'true');
        await playerPage.click('#turn-panel .turn-confirm');

        // The classic turn completes and the decision is credited to them.
        await expect(playerPage.locator('#battle-turn')).toHaveText('1');
        await expect(playerPage.locator('.event-decision', {
            hasText: `chosen by ${TEST_USER.username}`,
        })).toBeVisible();

        // And it is in the database, not the browser: a reload rebuilds it.
        await playerPage.reload();
        await expect(playerPage.locator('.turn-group[data-turn="1"]')).toBeVisible();
        await expect(playerPage.locator('.event-decision', {
            hasText: `chosen by ${TEST_USER.username}`,
        })).toBeVisible();

        await stopCoverage(playerPage, `${testInfo.title} (player context)`);
        await playerContext.close();
    });

    test('a Tarmar battle still paints its own arena and both pools', async ({ adminPage }) => {
        // The other half of one renderer serving two profiles: the canonical
        // surface is unchanged by the neutral view model.
        const battleName = uniqueName('E2E Tarmar');

        await createBattleViaSetup(adminPage, { name: battleName, fighters: 2, seed: 77 });

        await expect(adminPage.locator('h1')).toHaveText(battleName);
        await expect(adminPage.locator('#battle-arena .arena-combatant')).toHaveCount(2);
        await expect(adminPage.locator('.combatant-card')).toHaveCount(2);
        await expect(adminPage.locator('.combatant-card .pool-bar.fatigue').first())
            .toBeVisible();
        await expect(adminPage.locator('.combatant-card .pool-bar.body').first())
            .toBeVisible();
    });
});
