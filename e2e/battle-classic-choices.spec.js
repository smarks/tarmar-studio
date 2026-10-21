/**
 * A seated player's classic Melee turn, read as choices (issue #890).
 *
 * #788 gave a Tarmar figure's options a plain line and a cost, with the AI's
 * scoring behind a switch; a classic figure's menu still led every option
 * with the classic policy's tactic label, and the switch never appeared. The
 * classic options now carry the same fields, from the classic figures' own
 * numbers (adjDX on 3d6, the weapon's dice less the armour).
 */

const { test, expect, createBattleViaSetup, escaped } = require('./fixtures');
const { chosenBy } = require('./rulings');
const { readDjangoShell } = require('./django');
const { menuWords, menuLine, sentencePattern } = require('./turn-menu');

/**
 * Stand the two classic figures side by side, facing each other, so the
 * first to choose is offered an ATTACK.
 * @param {number} battleId
 * @returns {{names: string[]}}
 */
function standFaceToFace(battleId) {
    const output = readDjangoShell(`
import json
from hexarena.hex import Hex
from battle.models import Battle
from battle.persistence import dump_state, load_state
battle = Battle.objects.get(pk=${battleId})
game = load_state(battle.state_json, battle.rules_profile)
first, second = game.figures
first.position, second.position = Hex(2, 2), Hex(2, 3)
layout = game.arena.layout
first.facing = layout.direction_to(first.position, second.position)
second.facing = layout.direction_to(second.position, first.position)
battle.state_json = dump_state(game)
battle.save(update_fields=["state_json"])
print("NAMES", json.dumps([figure.name for figure in game.figures]))
`);
    return { names: JSON.parse(output.split('NAMES ')[1]) };
}

test.describe('Choosing a classic turn by hand', () => {
    test('each option reads as a choice, and the scoring is behind the switch', async ({ adminPage: page }) => {
        await createBattleViaSetup(page, {
            name: `E2E Classic Choices ${Date.now()}`,
            fighters: 2,
            seed: 890,
            controlMode: 'manual',
            rulesProfile: 'classic-melee',
        });
        const battleId = Number(new URL(page.url()).pathname.match(/battles\/(\d+)\//)[1]);
        const { names } = standFaceToFace(battleId);
        await page.reload();

        await page.click('#advance-turn');
        await expect(page.locator('#advance-turn')).toHaveText(/^Waiting on .+'s move$/);
        const panel = page.locator('#turn-panel');
        const options = panel.locator('.turn-option');
        await expect(options.first()).toBeVisible();

        // Every option has a plain line and a cost; none leads with the
        // classic policy's working until the reader asks for it.
        const count = await options.count();
        expect(count).toBeGreaterThan(1);
        await expect(panel.locator('.turn-option-plain')).toHaveCount(count);
        await expect(panel.locator('.turn-option-cost')).toHaveCount(count);
        await expect(panel.locator('.turn-option-advice')).toHaveCount(0);
        await expect(panel).not.toContainText('not chosen by the classic policy');

        // The ATTACK names its foe, its odds on 3d6 and its damage.
        const attack = options.filter({
            has: page.locator('.turn-option-name', { hasText: /^ATTACK$/ }),
        });
        await expect(attack).toHaveCount(1);
        // The configured words: the attack, its odds on 3d6 (or 4d6 against
        // a defender) and the damage the weapon does.
        const { shared, classic } = menuWords();
        const odds = sentencePattern(classic.odds, {
            needed: '\\d+', dice: '[34]d6', chance: '\\d+',
        });
        await expect(attack.locator('.turn-option-plain')).toHaveText(
            menuLine(shared.with_odds, {
                line: sentencePattern(shared.attack, {
                    aimed: `(?:${names.map(escaped).join('|')})`,
                }),
                odds: `(?:${[classic.odds_damage, classic.odds_damage_armoured]
                    .map((sentence) => sentencePattern(sentence, { odds }))
                    .join('|')})`,
            })
        );
        await expect(attack.locator('.turn-option-cost'))
            .toHaveText(menuLine(menuWords().panel.costs_nothing));
        await expect(panel.locator('.turn-option-ai-pick')).toHaveCount(1);

        // The switch shows the classic policy's working, and holds on reload.
        const scoring = panel.locator('.turn-scoring-toggle');
        await expect(scoring).toHaveText(menuLine(menuWords().panel.show_scoring));
        await scoring.click();
        await expect(panel.locator('.turn-option-advice')).toHaveCount(count);
        await expect(panel.locator('.turn-option-advice').first())
            .toHaveText(menuLine(menuWords().panel.advice));
        await page.reload();
        await expect(page.locator('#turn-panel .turn-scoring-toggle'))
            .toHaveText(menuLine(menuWords().panel.hide_scoring));
        await page.locator('#turn-panel .turn-scoring-toggle').click();

        // The choices still play: each figure picks ATTACK, the turn runs,
        // and the log credits the picks to the player.
        // Each figure's panel is waited for by name: after the first pick the
        // panel is redrawn for the second, and a click on the first one's
        // panel as it is replaced left Confirm disabled.
        const waitingOn = page.locator('#advance-turn');
        for (let pick = 0; pick < 2; pick += 1) {
            // Server-rendered after the reload, so read it without its padding.
            await expect(waitingOn).toContainText(/Waiting on .+'s move/);
            const chooser = (await waitingOn.textContent()).match(/Waiting on (.+)'s move/)[1];
            await page.locator('#turn-panel .turn-option').filter({
                has: page.locator('.turn-option-name', { hasText: /^ATTACK$/ }),
            }).click();
            await expect(page.locator('#turn-panel .turn-confirm')).toBeEnabled();
            await page.click('#turn-panel .turn-confirm');
            await expect(waitingOn).not.toContainText(`Waiting on ${chooser}'s move`);
        }
        await expect(page.locator('#battle-turn')).toHaveText('1');
        await expect(page.locator('.event-decision', { hasText: chosenBy() })).toHaveCount(2);
    });
});
