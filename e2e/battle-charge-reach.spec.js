/**
 * A CHARGE row is offered only at a target the engine's charge reaches
 * (issue #56), in a real browser.
 *
 * The turn menu aimed a charge at every enemy within a jog and a hex as the
 * crow flies, so an enemy walled off from the chooser drew a forecast the
 * engine would never act on: its jog along a clear path ends short, and the
 * charge falls short. Here the nearer enemy is ringed by fallen figures and
 * a farther one stands in the open: only the farther is offered a charge,
 * and the engine charges and strikes it. With the farther one gone, the one
 * CHARGE row says the charge strikes nobody and where the engine's jog ends,
 * and the engine's charge falls short.
 */

const { test, expect, createBattleViaSetup, escaped } = require('./fixtures');
const { readDjangoShell } = require('./django');
const { menuWords, menuLine, sentencePattern } = require('./turn-menu');

/**
 * Put the battle's first figure at the centre, the second three hexes off
 * ringed by six of the others laid unconscious (they block hexes but neither
 * fight nor engage), and the last four hexes the other way, or laid
 * unconscious out of the way when ``withFar`` is false. The two enemies are
 * handed to the AI and rooted where they stand, so the board the chooser's
 * menu reads is the board its charge moves on. Also reads, from the engine,
 * where the chooser's jog toward the nearer enemy would end, the jog's
 * reach and the engine's name for a CHARGE's gait.
 *
 * @param {number} battleId
 * @param {boolean} withFar
 * @returns {{chooser: string, near: string, far: string, arrival: string, jog: number, gait: string}}
 */
function wallOffTheNearerEnemy(battleId, withFar) {
    const output = readDjangoShell(`
import json
from battle import control
from battle.engine import hexes
from battle.models import Battle
from battle.persistence import dump_state
from battle.services import set_combatant_control_mode
from battle.engine.state import BattleState
from tarmar_engine import combat_math, movement
battle = Battle.objects.get(pk=${battleId})
state = BattleState.from_dict(battle.state_json)
chooser, near, *ring, far = state.combatants
chooser.position, near.position = (0, 0), (3, 0)
for fallen, cell in zip(ring, hexes.neighbors(near.position), strict=True):
    fallen.position = cell
    fallen.conscious = False
far.position = (-4, 0) if ${withFar ? 'True' : 'False'} else (0, -4)
far.conscious = ${withFar ? 'True' : 'False'}
for foe in (near, far):
    foe.facing = hexes.direction_towards(foe.position, chooser.position)
    foe.move_walk = foe.move_jog = foe.move_run = foe.move_sprint = 0
battle.state_json = dump_state(state)
battle.save(update_fields=["state_json"])
for foe in (near, far):
    set_combatant_control_mode(battle.pk, foe.combatant_id, control.AUTOMATIC)
jog = movement.gait_allowance(chooser, movement.MOVEMENT_GAITS["b"])
arrival = movement.approach_route(state, chooser, near, jog).distance
print("NAMES", json.dumps({
    "chooser": chooser.name,
    "near": near.name,
    "far": far.name,
    "arrival": combat_math.hexes_text(arrival),
    "jog": jog,
    "gait": movement.MOVEMENT_GAITS["b"],
}))
`);
    return JSON.parse(output.split('NAMES ')[1]);
}

/**
 * Seat nine duelists, wall off the nearer enemy, and open the chooser's menu.
 *
 * @param {import('@playwright/test').Page} page
 * @param {boolean} withFar
 * @returns {Promise<{chooser: string, near: string, far: string, arrival: string, jog: number, gait: string, charges: import('@playwright/test').Locator}>}
 */
async function openTheChoosersMenu(page, withFar) {
    await createBattleViaSetup(page, {
        name: `E2E Charge reach ${Date.now()}`,
        fighters: Array(9).fill('unarmoured duelist'),
        seed: 42,
        controlMode: 'manual',
    });
    const battleId = Number(new URL(page.url()).pathname.match(/battles\/(\d+)\//)[1]);
    const names = wallOffTheNearerEnemy(battleId, withFar);
    await page.reload();
    await page.click('#advance-turn');
    await expect(page.locator('#advance-turn')).toHaveText(`Waiting on ${names.chooser}'s move`);
    const charges = page.locator('#turn-panel .turn-option').filter({
        has: page.locator('.turn-option-name', { hasText: /^CHARGE ATTACK$/ }),
    });
    return { ...names, charges };
}

/**
 * Pick the one CHARGE row and confirm it.
 *
 * @param {import('@playwright/test').Page} page
 * @param {import('@playwright/test').Locator} charges
 */
async function chargeIt(page, charges) {
    await charges.click();
    await page.locator('#turn-panel .turn-confirm').click();
}

test.describe('The CHARGE rows', () => {
    test('aim only at the enemy the charge reaches, and the engine strikes it', async ({ adminPage: page }) => {
        const { chooser, near, far, charges } = await openTheChoosersMenu(page, true);
        await expect(charges).toHaveCount(1);
        // The configured words: a charge at Far, its jog, and its odds.
        const { shared, tarmar } = menuWords();
        await expect(charges.locator('.turn-option-plain')).toHaveText(
            menuLine(shared.with_odds, {
                line: sentencePattern(tarmar.charge, { aimed: escaped(far), hexes: '\\d+' }),
                odds: `(?:${sentencePattern(tarmar.odds)}|${sentencePattern(tarmar.odds_armoured)})`,
            })
        );
        await expect(charges.filter({ hasText: near })).toHaveCount(0);

        await chargeIt(page, charges);
        await expect(page.locator('.battle-event', {
            hasText: new RegExp(`${chooser} jogs \\d+ hex\\(es\\) toward ${far}`),
        })).toHaveCount(1);
        // The charge lands: the engine's attack line names Far, and no line
        // says the chooser's charge fell short (the rooted Near's own may).
        await expect(page.locator('.battle-event.event-action', {
            hasText: `${chooser} attacks ${far}`,
        })).toHaveCount(1);
        await expect(page.locator('.battle-event', {
            hasText: `${chooser}'s charge fell short`,
        })).toHaveCount(0);
    });

    test('say a charge no enemy is in reach of strikes nobody, and it falls short', async ({ adminPage: page }) => {
        const {
            chooser, near, arrival, jog, gait, charges,
        } = await openTheChoosersMenu(page, false);
        await expect(charges).toHaveCount(1);
        // The configured words: no attack, since the clear jog ends where
        // the engine's route does.
        const { shared, tarmar } = menuWords();
        await expect(charges.locator('.turn-option-plain')).toHaveText(
            menuLine(shared.no_attack_charge, {
                aimed: escaped(near),
                reason: sentencePattern(shared.ends_from, {
                    move: sentencePattern(shared.reach, {
                        move: sentencePattern(tarmar.clear_gait, { gait: escaped(gait) }),
                        hexes: String(jog),
                    }),
                    distance: escaped(arrival),
                    aimed: escaped(near),
                }),
            })
        );

        await chargeIt(page, charges);
        await expect(page.locator('.battle-event', {
            hasText: `${chooser}'s charge fell short of ${near}`,
        })).toHaveCount(1);
    });
});
