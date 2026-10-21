/**
 * E2E test for pushing mana into a cast (issue #960).
 *
 * The default canon's magic rules (the public configuration) offer Push for a
 * damage spell, rolled against INT with a cap of three pushed mana and no
 * Channel. What only a real browser proves: the seated wizard's spell stage
 * offers the push as a control beside the cast, listing exactly the amounts
 * the engine put on the menu with none picked; picking one and taking the
 * action sends it; and the turn's log carries the pushed cast, its Control
 * Roll and that roll's outcome.
 *
 * Under the Tarmar canon the same generated wizard is offered no push: the
 * spec drives the same claim and turn and pins that the stage lists Fire
 * Missile with no push control, then ends. The spec says only that no push
 * is offered there, not why; the reasons are the canon's.
 *
 * Borrowing (tarmar-engine #28), in the second test: the same wizard, its
 * pool emptied before the turn, is short of mana for every spell. Under the
 * default canon the stage still opens, Fire Missile offers a borrowing
 * control listing exactly the copies the engine put on the menu (no plain
 * cast), and borrowing the mana it lacks logs the borrowed cast and its
 * debt roll against WIS. Under the Tarmar canon the spec pins only that one
 * borrowing copy is offered and that the debt roll is logged; why, and the
 * numbers, are the canon's.
 *
 * Runaway (tarmar-engine #30), in the third test. Under the default canon
 * the turns' seed is pinned so the wizard's Fire Missile, pushed by two, is
 * a Runaway on its Control Roll's automatic total: the roster card shows it
 * running, the next turn's Renew Spells phase drains the wizard's mana, and
 * the wizard takes the anchor the menu offers, whose roll holds and ends it.
 * Under the Tarmar canon, where this wizard pushes nothing, the Runaway is
 * started on the board before the turn, and the spec pins only that the card
 * shows it, the wizard's own anchor is the AI's pick, and the drain and the
 * anchor roll are logged; the numbers are the canon's.
 *
 * Counterspell (tarmar-engine #31), in the fourth test: two seated wizards,
 * the test user's and the Game Master's, face each other. The test user's
 * counters the Game Master's with a commitment the counter control offers,
 * and the Game Master's casts Fire Missile at it. Under the default canon the
 * turns' seed is pinned so the defender wins the opposed roll: the log reads
 * the declaration, both totals and the result, and the caster's card shows
 * the spell standing countered. The next turn the caster presses it again,
 * the stage marking the cast as such, and the defender lets it through by
 * doing something else: the contest ends unopposed and the casting roll is
 * made. Under the Tarmar canon the spec pins only what its rulings give: the
 * test user's wizard may counter the enemy caster, the AI's pick is never a
 * counter, the commitments rise one mana at a time, and the opposed roll
 * and its result are logged; the numbers are the canon's.
 *
 * A casting roll's failing totals (tarmar-engine #46), in the fifth and
 * sixth tests. The spec reads the loaded canon's casting_failure block
 * through the Django shell and names no total of its own, so it asserts the
 * same under either canon: the wizard's plain Fire Missile fails when its
 * total is over the attribute or at or above the floor, and the wizard's
 * pool, as the roster card shows it, changes exactly when the cast succeeds
 * or the total is one the block says spends the mana. Under the default
 * canon the turns' seeds are pinned so one cast lands on the floor itself,
 * a failure that spends the mana, and the other on a total over the floor
 * that the block does not name, a failure that keeps it; the spec checks
 * each landed where it was meant to.
 *
 * Every die is pinned. The battle's seed fixes the turn's rolls and the
 * opponent; the wizard is generated on the claim, so that one request names
 * its dice (X-E2E-Dice-Seed, e2e/dice.js). The seeds were chosen, by
 * running the turn on the started battle for each seed, so the casting roll
 * succeeds under the default canon's fumble floor and the Control Roll (or
 * the debt roll) is made, and in the fifth and sixth tests so it fails; the
 * log assertions are what prove the pin took.
 */

const {
    test, expect, TEST_USER, loginAs, submitPassphrase, createBattleViaSetup, battleRowOnList,
    loadedCanon, DEFAULT_CANON, escaped,
} = require('./fixtures');
const { uniqueName } = require('./unique-name');
const { startCoverage, stopCoverage } = require('./coverage');
const {
    BATTLE_SEED, PUSH_TURNS_SEED, WIZARD_SEED, pinTheTurns, claimAWizard, openTheWizardsStage,
} = require('./wizard-stage');
const { readDjangoShell, runDjangoShell } = require('./django');
const { menuWords, menuLine, sentencePattern } = require('./turn-menu');

/**
 * The turns' seed for the Runaway branch (tarmar-engine #30), set after the
 * battle starts: turn 1's Control Roll is an 18 and turn 2's anchor roll
 * holds, under the default canon.
 */
const RUNAWAY_TURNS_SEED = 502;
/**
 * The casting failure tests (tarmar-engine #46): each pins the turns' seed,
 * set after the battle starts, and says which of the loaded block's cases its
 * turn 1 casting roll lands in under the default canon. The checks are the
 * block's own, so they hold under any canon; only `landsOn` is default-canon.
 */
const CASTING_FAILURE_CASES = [
    {
        title: 'a casting roll at the fumble floor fails and spends the mana',
        seed: 10,
        landsOn: (total, block) => total === block.fumble_floor,
    },
    {
        title: 'a casting roll past the floor that no total names fails and keeps the mana',
        seed: 5,
        landsOn: (total, block) => total > block.fumble_floor
            && !block.mana_lost_totals.includes(total),
    },
];
/** The Game Master's claim's seed: the caster's attributes. */
const CASTER_SEED = 23;
/**
 * The turns' seed for the Counterspell branch (tarmar-engine #31), set after
 * the battle starts: turn 1's opposed roll goes to the defender under the
 * default canon.
 */
const COUNTER_TURNS_SEED = 1;
/** The commitment the defender picks under the default canon. */
const COUNTER_MANA = 4;

/**
 * Empty the battle's wizard's mana pool, before the turn it decides in.
 *
 * @param {number} battleId
 */
function emptyTheWizardsPool(battleId) {
    runDjangoShell(`
from battle.models import Battle
from battle.persistence import dump_state, load_state
battle = Battle.objects.get(pk=${battleId})
state = load_state(battle.state_json, battle.rules_profile)
for figure in state.combatants:
    if figure.spells:
        figure.mana = 0
battle.state_json = dump_state(state)
battle.save(update_fields=["state_json"])
`);
}

/**
 * Seed the started battle's turns so turn 1's pushed Fire Missile reaches a
 * Control Roll of 18, the default canon's automatic Runaway total, and turn
 * 2's anchor roll holds. The opponent and the wizard are already made from
 * BATTLE_SEED and WIZARD_SEED; only the turns' dice follow this seed.
 *
 * @param {number} battleId
 */
function pinTheRunawayTurns(battleId) {
    pinTheTurns(battleId, RUNAWAY_TURNS_SEED);
}

/**
 * Start a Runaway of Fire Missile on the battle's wizard before the first
 * turn, under a canon whose Push this wizard is not offered.
 *
 * @param {number} battleId
 */
function startARunaway(battleId) {
    runDjangoShell(`
from battle.models import Battle
from battle.persistence import dump_state, load_state
from tarmar_engine.state import RunawayState
battle = Battle.objects.get(pk=${battleId})
state = load_state(battle.state_json, battle.rules_profile)
for figure in state.combatants:
    if figure.spells:
        figure.runaway = RunawayState("fire_missile", 1)
battle.state_json = dump_state(state)
battle.save(update_fields=["state_json"])
`);
}

/**
 * The loaded canon's casting_failure block, as the battle engine has it, or
 * null when the canon names none.
 *
 * @returns {{fumble_floor: number, mana_lost_totals: number[]} | null}
 */
function loadedCastingFailure() {
    const output = readDjangoShell(`
import json
from battle.magic_rules import magic_rules
block = magic_rules().casting_failure
print("CASTING_FAILURE " + json.dumps(None if block is None else {
    "fumble_floor": block.fumble_floor,
    "mana_lost_totals": sorted(block.mana_lost_totals),
}))
`);
    return JSON.parse(output.match(/^CASTING_FAILURE (.*)$/m)[1]);
}

/**
 * The seated wizard's pool as its roster card shows it ("10 / 10"): the one
 * card with a Mana row, since the opponent casts nothing.
 *
 * @param {import('@playwright/test').Page} page
 * @returns {import('@playwright/test').Locator}
 */
function wizardsPool(page) {
    return page.locator('.combatant-card dt:text-is("Mana") + dd .detail-value');
}

/**
 * Take one turn in which each of two seated players chooses, in whatever
 * order the turn asks them: each page's choice runs when its panel asks.
 *
 * @param {Array<{page: import('@playwright/test').Page,
 *     choose: () => Promise<void>}>} players
 */
async function bothChoose(players) {
    const waiting = [...players];
    while (waiting.length) {
        // The page the poll found asked, kept from that same read (#9): a
        // second read could miss a prompt that went in between, and
        // splice(-1, 1) would then take the wrong player.
        let askedIndex = -1;
        await expect.poll(async () => {
            const asked = await Promise.all(waiting.map(
                ({ page }) => page.locator('#turn-panel .turn-prompt').isVisible()
            ));
            askedIndex = asked.indexOf(true);
            return askedIndex;
        }, { timeout: 15000 }).toBeGreaterThan(-1);
        const [player] = waiting.splice(askedIndex, 1);
        await player.choose();
        await expect(player.page.locator('#turn-panel .turn-prompt')).toHaveCount(0);
    }
}

test.describe('Pushing mana into a cast', () => {
    /** The player's page, kept so a failure can show the player's panel. */
    let playerPage = null;

    test.afterEach(async ({}, testInfo) => {
        if (!playerPage) { return; }
        if (testInfo.status !== testInfo.expectedStatus && !playerPage.isClosed()) {
            await testInfo.attach('player page', {
                body: await playerPage.content(), contentType: 'text/html',
            });
            await testInfo.attach('player screenshot', {
                body: await playerPage.screenshot({ fullPage: true }),
                contentType: 'image/png',
            });
        }
        await playerPage.context().close();
        playerPage = null;
    });

    test('a seated wizard is offered the push its canon allows, and a pushed cast is logged', async ({ adminPage, browser }, testInfo) => {
        test.setTimeout(120000);
        // The push amounts below are the default canon's; under any other
        // canon the spec pins that this wizard is offered no push.
        await adminPage.goto('/tarmar/gm/');
        const pushOffered = (await loadedCanon(adminPage)) === DEFAULT_CANON;
        const battleName = uniqueName('E2E Push');

        playerPage = await openTheWizardsStage(adminPage, browser, battleName, {
            beforeTurn: pushOffered
                ? (battleId) => pinTheTurns(battleId, PUSH_TURNS_SEED)
                : undefined,
        });
        const stage = playerPage.locator('#turn-panel .turn-cast-stage');

        if (!pushOffered) {
            // Fire Missile is on the stage, castable, with no push control
            // and not a word about pushing.
            const missile = stage.locator('.turn-spell', {
                has: playerPage.locator('.turn-spell-name', { hasText: 'Fire Missile' }),
            });
            await expect(missile).toBeEnabled();
            await expect(stage.locator('.turn-push')).toHaveCount(0);
            await expect(stage.locator('.turn-push-amount')).toHaveCount(0);
            await expect(stage).not.toContainText(/push/i);
            await stopCoverage(playerPage, `${testInfo.title} (player context)`);
            return;
        }

        // The spell stage: Fire Missile carries the push control, with
        // nothing pushed until the player picks an amount.
        const missileGroup = stage.locator('.turn-spell-group', {
            has: playerPage.locator('.turn-spell-name', { hasText: 'Fire Missile' }),
        });
        // A cast the menu offers pushed copies of carries the push control;
        // asserted, so a menu without them fails here and says so.
        await expect(missileGroup.locator('.turn-push')).toBeVisible();
        await missileGroup.locator('.turn-spell').click();
        const amounts = missileGroup.locator('.turn-push-amount');
        // The default canon caps a push at three mana, and the wizard has
        // the mana for all three.
        const { panel, tarmar } = menuWords();
        await expect(amounts).toHaveText([
            menuLine(panel.push_none),
            ...[1, 2, 3].map((mana) => menuLine(panel.push_amount, { mana: String(mana) })),
        ]);
        await expect(amounts.first()).toHaveAttribute('aria-pressed', 'true');

        await amounts.nth(2).click();
        await expect(amounts.nth(2)).toHaveAttribute('aria-pressed', 'true');
        await expect(missileGroup.locator('.turn-push-cost')).toHaveText(
            menuLine(panel.costs, {
                cost: sentencePattern(tarmar.push_cost, { total: '3', level: '1', pushed: '2' }),
            })
        );
        await playerPage.click('#turn-panel .turn-confirm');

        // The turn ran: the player's pushed cast, its Control Roll against
        // INT, and the roll's outcome, all in the log.
        await expect(playerPage.locator('#battle-turn')).toHaveText('1');
        await expect(playerPage.locator('.event-decision', {
            hasText: `chosen by ${TEST_USER.username}`,
        })).toContainText('Fire Missile (PUSH 2 MANA)');
        const controlRoll = playerPage.locator('.event-roll', { hasText: 'control 3d6' });
        await expect(controlRoll).toHaveCount(1);
        await expect(controlRoll).toContainText('vs INT');
        await expect(playerPage.locator('.battle-event', {
            hasText: /pushed Fire Missile|Fire Missile runs away/,
        })).toHaveCount(1);

        await stopCoverage(playerPage, `${testInfo.title} (player context)`);
    });
    test('a seated wizard short of mana borrows what it lacks, and the debt roll is logged', async ({ adminPage, browser }, testInfo) => {
        test.setTimeout(120000);
        await adminPage.goto('/tarmar/gm/');
        const defaultCanon = (await loadedCanon(adminPage)) === DEFAULT_CANON;
        playerPage = await openTheWizardsStage(
            adminPage, browser, uniqueName('E2E Borrow'),
            { beforeTurn: emptyTheWizardsPool }
        );
        const stage = playerPage.locator('#turn-panel .turn-cast-stage');
        // The configured words, the pool named as the server names it.
        const pool = { pool_label: escaped(menuWords().stage.mana_pool), pool: '0' };
        await expect(stage.locator('.turn-cast-pool')).toHaveText(menuLine(
            menuWords().panel.stage_pool,
            {
                pool: `(?:${sentencePattern(menuWords().panel.pool, pool)}`
                    + `|${sentencePattern(menuWords().panel.pool_owing, pool)})`,
            }
        ));

        // Fire Missile has no plain cast with an empty pool: its row is
        // disabled with the server's reason, and its borrowing control lists
        // what the menu offers, nothing picked.
        const missileGroup = stage.locator('.turn-spell-group', {
            has: playerPage.locator('.turn-spell-name', { hasText: 'Fire Missile' }),
        });
        await expect(missileGroup.locator('.turn-spell')).toBeDisabled();
        // Fire Missile's level 1 against an empty pool: one short, borrowed.
        await expect(missileGroup.locator('.turn-spell-note'))
            .toHaveText(menuLine(menuWords().stage.mana_borrowable, {
                level: '1', mana: '0', shortfall: '1',
            }));
        const copies = missileGroup.locator('.turn-borrow-amount');
        const { panel } = menuWords();
        const borrowOne = menuLine(panel.borrow_amount, { borrowed: '1' });
        if (defaultCanon) {
            // The default canon: no Channel, a debt cap of four, a push cap
            // of three.
            await expect(copies).toHaveText([borrowOne, ...[1, 2, 3].map((pushed) => menuLine(
                panel.borrow_pushed, { pushed: String(pushed), borrowed: String(pushed + 1) }
            ))]);
        } else {
            await expect(copies).toHaveText([borrowOne]);
        }
        await expect(copies.first()).toHaveAttribute('aria-pressed', 'false');

        await copies.first().click();
        await expect(copies.first()).toHaveAttribute('aria-pressed', 'true');
        await expect(missileGroup.locator('.turn-borrow-cost'))
            .toContainText('borrows the 1 you lack as debt');
        await playerPage.click('#turn-panel .turn-confirm');

        // The turn ran: the borrowing cast, then its debt roll and how it
        // went, all in the log.
        await expect(playerPage.locator('#battle-turn')).toHaveText('1');
        await expect(playerPage.locator('.event-decision', {
            hasText: `chosen by ${TEST_USER.username}`,
        })).toContainText('Fire Missile (BORROW 1 MANA)');
        await expect(playerPage.locator('.event-action', { hasText: 'casts Fire Missile' }))
            .toContainText('borrows 1, debt 1');
        const debtRoll = playerPage.locator('.event-roll', { hasText: ' debt ' });
        await expect(debtRoll).toHaveCount(1);
        if (defaultCanon) {
            await expect(debtRoll).toContainText('vs WIS');
        }
        await expect(playerPage.locator('.battle-event', {
            hasText: /(holds|fails) the debt roll for Fire Missile \(1 owed\)/,
        })).toHaveCount(1);

        await stopCoverage(playerPage, `${testInfo.title} (player context)`);
    });

    test('a pushed cast that runs away feeds on its caster, and the caster anchors it', async ({ adminPage, browser }, testInfo) => {
        test.setTimeout(120000);
        await adminPage.goto('/tarmar/gm/');
        const defaultCanon = (await loadedCanon(adminPage)) === DEFAULT_CANON;
        playerPage = await openTheWizardsStage(
            adminPage, browser, uniqueName('E2E Runaway'),
            { beforeTurn: defaultCanon ? pinTheRunawayTurns : startARunaway }
        );
        const runawayLine = playerPage.locator('.combatant-runaway');

        if (defaultCanon) {
            // Turn 1: Fire Missile pushed by 2, a 3 mana casting, whose
            // Control Roll is the default canon's automatic Runaway total.
            const missileGroup = playerPage.locator('#turn-panel .turn-spell-group', {
                has: playerPage.locator('.turn-spell-name', { hasText: 'Fire Missile' }),
            });
            await missileGroup.locator('.turn-spell').click();
            await missileGroup.locator('.turn-push-amount').nth(2).click();
            await playerPage.click('#turn-panel .turn-confirm');
            await expect(playerPage.locator('#battle-turn')).toHaveText('1');
            await expect(playerPage.locator('.event-roll', { hasText: 'control 3d6' }))
                .toContainText('= 18 vs INT');
            await expect(playerPage.locator('.battle-event', {
                hasText: 'Fire Missile runs away and will feed on',
            })).toContainText('(3 mana cast)');
            // The card says so, from the state: the drain is the cost and 1.
            await expect(runawayLine).toHaveText(
                'Runaway Fire Missile: began this turn; it takes 4 a turn from mana'
            );

            await adminPage.click('#advance-turn');
            await expect(adminPage.locator('#battle-status')).toHaveText('Awaiting players');
        }

        // The anchor is the caster's own, and the AI's pick: an AI-run
        // caster always anchors its own Runaway.
        const anchor = playerPage.locator('#turn-panel .turn-option-anchor');
        await expect(anchor).toHaveCount(1, { timeout: 15000 });
        await expect(anchor.locator('.turn-option-ai-pick')).toBeVisible();
        // The configured words, with the default canon's roll: 3d6 against WIS.
        await expect(anchor.locator('.turn-option-plain')).toHaveText(
            menuLine(menuWords().tarmar.self_anchor, {
                spell: 'Fire Missile',
                ...(defaultCanon ? { dice: '3d6', attribute: 'WIS' } : {}),
            })
        );
        await expect(runawayLine).toHaveCount(1);
        await anchor.click();
        await playerPage.click('#turn-panel .turn-confirm');

        // The turn ran: the spell fed in the Renew Spells phase, then the
        // caster's anchor roll and how it went, all in the log.
        await expect(playerPage.locator('#battle-turn')).toHaveText(defaultCanon ? '2' : '1');
        const drain = playerPage.locator('.battle-event', { hasText: 'Fire Missile feeds on' });
        await expect(drain).toHaveCount(1);
        await expect(drain).toContainText('turn 1: it drains');
        const anchorRoll = playerPage.locator('.event-roll', { hasText: ' anchor ' });
        await expect(anchorRoll).toHaveCount(1);
        if (defaultCanon) {
            await expect(drain).toContainText('turn 1: it drains 4 mana (3 left)');
            await expect(anchorRoll).toContainText('vs WIS');
            await expect(anchorRoll).toContainText('— held');
            // Fire Missile is not a continuing spell: held, the Runaway ends,
            // and the card no longer shows one.
            await expect(playerPage.locator('.battle-event', {
                hasText: 'anchors Fire Missile after 1 turn(s)',
            })).toHaveCount(1);
            await expect(runawayLine).toHaveCount(0);
        } else {
            await expect(playerPage.locator('.battle-event', {
                hasText: /anchors Fire Missile after|fails to anchor Fire Missile/,
            })).toHaveCount(1);
        }

        await stopCoverage(playerPage, `${testInfo.title} (player context)`);
    });

    for (const { title, seed, landsOn } of CASTING_FAILURE_CASES) {
        test(title, async ({ adminPage, browser }, testInfo) => {
            test.setTimeout(120000);
            await adminPage.goto('/tarmar/gm/');
            const defaultCanon = (await loadedCanon(adminPage)) === DEFAULT_CANON;
            const block = loadedCastingFailure();
            playerPage = await openTheWizardsStage(
                adminPage, browser, uniqueName('E2E Casting failure'),
                { beforeTurn: (battleId) => pinTheTurns(battleId, seed) }
            );
            const poolBefore = (await wizardsPool(playerPage).textContent()).trim();
            // The plain cast, under either canon: no push picked.
            await playerPage.locator('#turn-panel .turn-cast-stage .turn-spell', {
                has: playerPage.locator('.turn-spell-name', { hasText: 'Fire Missile' }),
            }).click();
            await playerPage.click('#turn-panel .turn-confirm');

            await expect(playerPage.locator('#battle-turn')).toHaveText('1');
            const castingRoll = playerPage.locator('.event-roll', { hasText: ' casting 3d6' });
            await expect(castingRoll).toHaveCount(1);
            const [, total, attribute] = (await castingRoll.textContent())
                .match(/= (\d+) vs INT (\d+)/).map(Number);
            if (defaultCanon) {
                // The pin took, and the roll is the case this test is for.
                expect(block).not.toBeNull();
                expect(landsOn(total, block)).toBe(true);
            }
            const failed = total > attribute
                || (block !== null && total >= block.fumble_floor);
            const spends = !failed
                || (block !== null && block.mana_lost_totals.includes(total));
            const cast = playerPage.locator('.event-action', { hasText: 'casts Fire Missile' });
            await expect(cast).toContainText(failed ? ': failure (' : ': success (');

            // The pool after the turn, from the saved state: changed exactly
            // when the cast paid.
            await playerPage.reload();
            const poolAfter = (await wizardsPool(playerPage).textContent()).trim();
            if (spends) {
                expect(poolAfter).not.toBe(poolBefore);
            } else {
                expect(poolAfter).toBe(poolBefore);
            }

            await stopCoverage(playerPage, `${testInfo.title} (player context)`);
        });
    }

    test('a wizard counters another\'s spell, and the caster presses it again', async ({ adminPage, browser }, testInfo) => {
        test.setTimeout(150000);
        await adminPage.goto('/tarmar/gm/');
        const defaultCanon = (await loadedCanon(adminPage)) === DEFAULT_CANON;
        const battleName = uniqueName('E2E Counter');
        await createBattleViaSetup(adminPage, {
            name: battleName, seed: BATTLE_SEED, playerSeats: 2,
        });
        await expect(adminPage.locator('#battle-status')).toHaveText('Lobby');
        const battleId = Number(new URL(adminPage.url()).pathname.match(/battles\/(\d+)\//)[1]);

        // The defender: the test user's wizard, in a context of its own.
        const playerContext = await browser.newContext();
        playerPage = await playerContext.newPage();
        await startCoverage(playerPage);
        await submitPassphrase(playerPage);
        await loginAs(playerPage, TEST_USER);
        const listRow = await battleRowOnList(playerPage, battleName);
        await listRow.locator('a', { hasText: battleName }).first().click();
        const defender = await claimAWizard(playerPage, WIZARD_SEED);
        // The caster: the Game Master's own wizard.
        await adminPage.reload();
        await claimAWizard(adminPage, CASTER_SEED);
        await adminPage.click('.battle-start-form button[type="submit"]');
        await expect(playerPage.locator('#battle-arena')).toBeVisible({ timeout: 15000 });
        if (defaultCanon) {
            pinTheTurns(battleId, COUNTER_TURNS_SEED);
        }
        await adminPage.reload();
        await adminPage.click('#advance-turn');

        // Turn 1: the defender counters, the caster casts Fire Missile at it.
        let committed = '';
        await bothChoose([
            {
                page: playerPage,
                choose: async () => {
                    const control = playerPage.locator('#turn-panel .turn-counter');
                    await expect(control).toHaveCount(1);
                    // The AI never counters: no commitment is its pick.
                    await expect(control.locator('.turn-option-ai-pick')).toHaveCount(0);
                    const amounts = control.locator('.turn-counter-amount');
                    // Each button names its commitment in the configured
                    // words, and carries it as data-counter-mana.
                    const mana = (await amounts.evaluateAll((buttons) => buttons
                        .map((button) => button.dataset.counterMana))).map(Number);
                    await expect(amounts).toHaveText(mana.map((amount) => menuLine(
                        menuWords().panel.counter_amount, { mana: String(amount) }
                    )));
                    // One button per mana from the least the canon allows.
                    expect(mana).toEqual(mana.map((_amount, index) => mana[0] + index));
                    await expect(amounts.first()).toHaveAttribute('aria-pressed', 'false');
                    const picked = defaultCanon ? COUNTER_MANA - mana[0] : 0;
                    if (defaultCanon) {
                        // The default canon: 1 to 5 mana.
                        expect(mana).toEqual([1, 2, 3, 4, 5]);
                    }
                    committed = String(mana[picked]);
                    await amounts.nth(picked).click();
                    await expect(amounts.nth(picked)).toHaveAttribute('aria-pressed', 'true');
                    await expect(control.locator('.turn-counter-cost')).toContainText(
                        new RegExp(sentencePattern(menuWords().tarmar.counter_cost, {
                            mana: committed,
                        }))
                    );
                    await playerPage.click('#turn-panel .turn-confirm');
                },
            },
            {
                page: adminPage,
                choose: async () => {
                    await adminPage.locator('#turn-panel .turn-option-cast').click();
                    await adminPage.locator('#turn-panel .turn-spell', {
                        has: adminPage.locator('.turn-spell-name', { hasText: 'Fire Missile' }),
                    }).click();
                    await adminPage.click('#turn-panel .turn-confirm');
                },
            },
        ]);
        await expect(playerPage.locator('#battle-turn')).toHaveText('1');
        await expect(playerPage.locator('.battle-event', {
            hasText: `${defender} commits ${committed} mana to counter`,
        })).toHaveCount(1);
        const opposed = playerPage.locator('.event-roll', { hasText: / counter \d+d\d+/ });
        await expect(opposed).toHaveCount(2);
        const result = playerPage.locator('.battle-event', {
            hasText: /Fire Missile (is countered|gets through): \d+ against \d+/,
        });
        await expect(result).toHaveCount(1);
        if (!defaultCanon) {
            await stopCoverage(playerPage, `${testInfo.title} (player context)`);
            return;
        }
        await expect(opposed.first()).toContainText(' counter 2d10');
        await expect(result).toContainText('Fire Missile is countered');
        await expect(playerPage.locator('.battle-event', {
            hasText: `Fire Missile is countered by ${defender}: no casting roll`,
        })).toHaveCount(1);
        // The caster's card shows the spell standing countered.
        const contest = playerPage.locator('.combatant-contest');
        await expect(contest).toHaveText(
            `Fire Missile countered once by ${defender}; cast it again at ${defender} to press it`
        );

        // Turn 2: the caster presses it again; the defender does something
        // else, so no counter meets it.
        await adminPage.click('#advance-turn');
        await bothChoose([
            {
                page: playerPage,
                choose: async () => {
                    await playerPage.locator('#turn-panel .turn-option', {
                        has: playerPage.locator('.turn-option-name', { hasText: /^DODGE$/ }),
                    }).click();
                    await playerPage.click('#turn-panel .turn-confirm');
                },
            },
            {
                page: adminPage,
                choose: async () => {
                    await adminPage.locator('#turn-panel .turn-option-cast').click();
                    const pressed = adminPage.locator('#turn-panel .turn-spell.is-escalation');
                    await expect(pressed.locator('.turn-spell-escalate')).toHaveText(
                        menuLine(menuWords().tarmar.press_again_at, {
                            spell: 'Fire Missile', aimed: escaped(defender),
                        })
                    );
                    await pressed.click();
                    await adminPage.click('#turn-panel .turn-confirm');
                },
            },
        ]);
        await expect(playerPage.locator('#battle-turn')).toHaveText('2');
        await expect(playerPage.locator('.battle-event', {
            hasText: 'presses it again and no counter meets it: the contest over Fire '
                + 'Missile ends after 1 counter(s)',
        })).toHaveCount(1);
        await expect(playerPage.locator('.event-roll', { hasText: ' casting 3d6' }))
            .toHaveCount(1);
        await expect(contest).toHaveCount(0);

        await stopCoverage(playerPage, `${testInfo.title} (player context)`);
    });
});
