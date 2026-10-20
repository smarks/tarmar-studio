/**
 * A seated player's turn menu, in a real browser (issues #775, #788).
 *
 * #775: the menu aimed each attack where the AI would, so a figure between
 * two enemies was offered one ATTACK, at the enemy the AI scored best, and
 * could not strike the other. #788: every option led with the AI's scoring
 * sheet ("score 0.178 — Strike Gray Wolf: ... P(hit) 0.100 ...").
 *
 * A duelist is placed between two others, who are played by the AI and
 * cannot walk off. The pause offers an ATTACK at each, each read as a plain
 * line with its cost; the AI's working is behind a switch that holds across
 * a reload; and the strike the player picks is the one the engine swings.
 */

const { test, expect, createBattleViaSetup, escaped } = require('./fixtures');
const { readDjangoShell } = require('./django');

/**
 * Stand the first figure between the other two, facing each other, and hand
 * the other two to the AI, rooted where they stand.
 *
 * @param {number} battleId
 * @returns {{chooser: string, foes: string[]}}
 */
function standBetween(battleId) {
    const output = readDjangoShell(`
import json
from battle import control
from battle.engine import hexes
from battle.models import Battle
from battle.persistence import dump_state
from battle.services import set_combatant_control_mode
from battle.engine.state import BattleState
battle = Battle.objects.get(pk=${battleId})
state = BattleState.from_dict(battle.state_json)
chooser, left, right = state.combatants
for figure, position in ((chooser, (0, 0)), (left, (1, 0)), (right, (-1, 0))):
    figure.position = position
for foe in (left, right):
    foe.facing = hexes.direction_towards(foe.position, (0, 0))
    foe.move_walk = foe.move_jog = foe.move_run = foe.move_sprint = 0
battle.state_json = dump_state(state)
battle.save(update_fields=["state_json"])
for foe in (left, right):
    set_combatant_control_mode(battle.pk, foe.combatant_id, control.AUTOMATIC)
print("NAMES", json.dumps({"chooser": chooser.name, "foes": [left.name, right.name]}))
`);
    return JSON.parse(output.split('NAMES ')[1]);
}

test.describe('Choosing a turn by hand', () => {
    test('each foe is its own ATTACK, read as a choice, and the pick is what swings', async ({ adminPage: page }) => {
        await createBattleViaSetup(page, {
            name: `E2E Choices ${Date.now()}`,
            fighters: ['unarmoured duelist', 'unarmoured duelist', 'unarmoured duelist'],
            seed: 42,
            controlMode: 'manual',
        });
        const battleId = Number(new URL(page.url()).pathname.match(/battles\/(\d+)\//)[1]);
        const { chooser, foes } = standBetween(battleId);
        await page.reload();

        await page.click('#advance-turn');
        await expect(page.locator('#advance-turn')).toHaveText(`Waiting on ${chooser}'s move`);
        const panel = page.locator('#turn-panel');
        const attacks = panel.locator('.turn-option').filter({
            has: page.locator('.turn-option-name', { hasText: /^ATTACK$/ }),
        });
        await expect(attacks).toHaveCount(2);
        for (const foe of foes) {
            const aimed = attacks.filter({ hasText: `Attack ${foe} with the` });
            await expect(aimed).toHaveCount(1);
            await expect(aimed.locator('.turn-option-plain'))
                .toHaveText(new RegExp(`^Attack ${escaped(foe)} with the .+: \\d+ in 20 to hit, .+ a hit\\)$`));
            await expect(aimed.locator('.turn-option-cost')).toHaveText('Costs nothing.');
        }
        // One AI pick on the whole menu, and no scoring until asked for.
        await expect(panel.locator('.turn-option-ai-pick')).toHaveCount(1);
        await expect(panel.locator('.turn-option-advice')).toHaveCount(0);

        const scoring = panel.locator('.turn-scoring-toggle');
        await expect(scoring).toHaveText("Show the AI's scoring");
        await scoring.click();
        await expect(panel.locator('.turn-scoring-toggle')).toHaveAttribute('aria-pressed', 'true');
        await expect(attacks.first().locator('.turn-option-advice')).toContainText('score ');
        await expect(attacks.first().locator('.turn-option-advice')).toContainText('P(hit)');
        // The reader's choice holds across a reload, as a setting does.
        await page.reload();
        await expect(page.locator('#turn-panel .turn-scoring-toggle'))
            .toHaveText("Hide the AI's scoring");
        await expect(page.locator('#turn-panel .turn-option-advice').first()).toBeVisible();
        await page.locator('#turn-panel .turn-scoring-toggle').click();
        await expect(page.locator('#turn-panel .turn-option-advice')).toHaveCount(0);

        // Strike the foe the AI did not pick.
        const notPicked = attacks.filter({ hasNot: page.locator('.turn-option-ai-pick') });
        const plainLine = await notPicked.first().locator('.turn-option-plain').textContent();
        const target = foes.find((foe) => plainLine.startsWith(`Attack ${foe} `));
        expect(target).toBeTruthy();
        await notPicked.first().click();
        await panel.locator('.turn-confirm').click();

        await expect(page.locator('.battle-event.event-decision', {
            hasText: `${chooser} chooses ATTACK (j): chosen by`,
        })).toContainText(`Strike ${target}:`);
        // And the engine swings at that foe: the chooser's attack line
        // names it (the engine's "<attacker> attacks <defender> with ...").
        await expect(page.locator('.battle-event.event-action', {
            hasText: `${chooser} attacks `,
        }).first()).toContainText(`${chooser} attacks ${target}`);
    });
});
