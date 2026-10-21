/**
 * Weapons lying on the field are drawn on the arena (issue #878).
 *
 * tarmar-engine 0.9.5 leaves a dropped, fumbled or thrown weapon in its hex
 * and offers PICK UP WEAPON (q) to a figure beside one, but the arena drew
 * none, so a player offered q could not see where the weapon lay or which it
 * was. A broadsword is laid in each duelist's hex before the first turn; the
 * arena shows both, named in the hex's tooltip, the menu offers PICK UP, and
 * after the pick-up the arena shows the saber the figure dropped instead.
 */

const { test, expect, createBattleViaSetup } = require('./fixtures');
const { uniqueName } = require('./unique-name');
const { readDjangoShell } = require('./django');

/**
 * Lay a copy of a Broadsword in every figure's own hex, through the
 * battle's own state, as a fumble or a throw would leave one.
 *
 * @param {number} battleId
 */
function layBroadswords(battleId) {
    const output = readDjangoShell(`
from battle import profiles
from battle.engine.state import GroundWeapon
from battle.models import Battle
from battle.persistence import dump_state
from battle.services import _catalog_weapon
battle = Battle.objects.get(pk=${battleId})
profile = profiles.for_battle(battle)
state = profile.load(battle.state_json)
for figure in state.combatants:
    state.ground_weapons.append(
        GroundWeapon(q=figure.q, r=figure.r, weapon=_catalog_weapon("broadsword"))
    )
battle.state_json = dump_state(state)
battle.save(update_fields=["state_json"])
print("LAID", len(state.ground_weapons))
`);
    expect(output).toContain('LAID 2');
}

test.describe('Weapons lying on the field', () => {
    test('the arena draws and names them, and a pick-up moves them', async ({ adminPage: page }) => {
        await createBattleViaSetup(page, {
            name: uniqueName('E2E Ground'),
            fighters: ['unarmoured duelist', 'unarmoured duelist'],
            seed: 42,
            controlMode: 'manual',
        });
        const battleId = Number(new URL(page.url()).pathname.match(/battles\/(\d+)\//)[1]);
        layBroadswords(battleId);
        await page.reload();

        const lying = page.locator('#battle-arena .arena-ground-weapon');
        await expect(lying).toHaveCount(2);
        await expect(lying.first().locator('title')).toHaveText('Lying here: Broadsword');
        await expect(lying.nth(1)).toHaveAttribute('aria-label', 'Lying here: Broadsword');

        await page.click('#advance-turn');
        await expect(page.locator('#advance-turn')).toHaveText(/^Waiting on .+'s move$/);
        const first = (await page.locator('#advance-turn').textContent())
            .replace('Waiting on ', '').replace("'s move", '').trim();
        const panel = page.locator('#turn-panel');
        const pickUp = panel.locator('.turn-option').filter({
            has: page.locator('.turn-option-name', { hasText: /^PICK UP WEAPON$/ }),
        });
        await expect(pickUp).toHaveCount(1);
        await pickUp.click();
        await panel.locator('.turn-confirm').click();

        await expect(page.locator('#advance-turn')).not.toHaveText(`Waiting on ${first}'s move`);
        const dodge = panel.locator('.turn-option').filter({
            has: page.locator('.turn-option-name', { hasText: /^DODGE$/ }),
        });
        await dodge.click();
        await panel.locator('.turn-confirm').click();

        await expect(page.locator('#battle-turn')).toHaveText('1');
        await expect(page.locator('.turn-group[data-turn="1"]'))
            .toContainText(`${first} picks up the Broadsword, dropping the Saber`);
        // The broadsword left the first hex and the saber lies there now;
        // the other duelist's broadsword is where it was.
        const names = await lying.evaluateAll(
            (nodes) => nodes.map((node) => node.getAttribute('aria-label')).sort()
        );
        expect(names).toEqual(['Lying here: Broadsword', 'Lying here: Saber']);
    });
});
