/**
 * A DODGE reads as the move the engine will make (issue #57), in a real
 * browser.
 *
 * Since tarmar-engine 0.9.16 a figure that no clear jog brings nearer to the
 * missile threat it dodges stands still, but the turn menu's DODGE line still
 * promised a jog toward it. Two archers face each other three hexes apart;
 * one is boxed in by six fallen figures, the other stands in the open. The
 * boxed archer's DODGE says it stands, the open one's that it jogs, and the
 * turn's log shows the engine doing exactly that.
 */

const { test, expect, createBattleViaSetup } = require('./fixtures');
const { readDjangoShell } = require('./django');

/**
 * Put the battle's first figure in the middle of the arena, ring it with six
 * of the others laid unconscious (they block hexes but neither fight nor
 * engage), and set the last figure three hexes off; arm both standing
 * figures with a longbow so each is the other's missile threat.
 *
 * @param {number} battleId
 * @returns {{boxed: string, open: string}} the two archers' names
 */
function boxInAnArcher(battleId) {
    const output = readDjangoShell(`
import json
from battle import profiles
from battle.engine import hexes
from battle.models import Battle
from battle.persistence import dump_state
from battle.services import _catalog_weapon
battle = Battle.objects.get(pk=${battleId})
state = profiles.for_battle(battle).load(battle.state_json)
boxed, *ring, open_archer = state.combatants
boxed.position, open_archer.position = (0, 0), (0, 3)
for fallen, cell in zip(ring, hexes.neighbors((0, 0)), strict=True):
    fallen.position = cell
    fallen.conscious = False
for archer, other in ((boxed, open_archer), (open_archer, boxed)):
    archer.weapon = _catalog_weapon("longbow")
    archer.facing = hexes.direction_towards(archer.position, other.position)
battle.state_json = dump_state(state)
battle.save(update_fields=["state_json"])
print("BOXED", json.dumps({"boxed": boxed.name, "open": open_archer.name}))
`);
    return JSON.parse(output.split('BOXED ')[1]);
}

/**
 * The awaited figure's name, once the turn waits on someone other than
 * ``previous``.
 *
 * @param {import('@playwright/test').Page} page
 * @param {string} previous - the figure that has just chosen, or ''
 * @returns {Promise<string>}
 */
async function awaited(page, previous) {
    const button = page.locator('#advance-turn');
    if (previous) {
        await expect(button).not.toHaveText(`Waiting on ${previous}'s move`);
    }
    await expect(button).toHaveText(/^Waiting on .+'s move$/);
    return (await button.textContent()).replace('Waiting on ', '').replace("'s move", '').trim();
}

test.describe('The DODGE line', () => {
    test('says standing for a boxed-in archer and jogging for an open one', async ({ adminPage: page }) => {
        await createBattleViaSetup(page, {
            name: `E2E Dodge standing ${Date.now()}`,
            fighters: Array(8).fill('unarmoured duelist'),
            seed: 42,
            controlMode: 'manual',
        });
        const battleId = Number(new URL(page.url()).pathname.match(/battles\/(\d+)\//)[1]);
        const { boxed, open } = boxInAnArcher(battleId);
        await page.reload();
        await page.click('#advance-turn');

        const panel = page.locator('#turn-panel');
        const dodge = panel.locator('.turn-option').filter({
            has: page.locator('.turn-option-name', { hasText: /^DODGE$/ }),
        });
        const expected = {
            [boxed]: `standing, since no clear jog gets nearer to ${open}`,
            [open]: `jogging toward ${boxed}`,
        };
        let previous = '';
        for (let turn = 0; turn < 2; turn += 1) {
            const archer = await awaited(page, previous);
            expect(Object.keys(expected)).toContain(archer);
            await expect(dodge.locator('.turn-option-plain')).toHaveText(
                'Dodge: +4 to your Target Number against missiles and thrown '
                + `weapons only, ${expected[archer]}`
            );
            await dodge.click();
            await panel.locator('.turn-confirm').click();
            previous = archer;
        }

        const turnOne = page.locator('.turn-group[data-turn="1"]');
        await expect(page.locator('#battle-turn')).toHaveText('1');
        const lines = (await turnOne.locator('.battle-event').allTextContents())
            .map((line) => line.replace(/\s+/g, ' ').trim());
        // The engine does what each line said: the open archer jogs toward
        // the boxed one, and the boxed archer does not move.
        expect(lines.some((line) => new RegExp(
            `${open} jogs \\d+ hex\\(es\\) toward ${boxed}`
        ).test(line))).toBe(true);
        expect(lines.some((line) => line.includes(`${boxed} jogs`))).toBe(false);
    });
});
