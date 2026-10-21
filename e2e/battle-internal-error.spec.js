/**
 * E2E for an internal failure under a player's choice (issue #113).
 *
 * The act endpoint caught only a ValueError, so a RuntimeError, KeyError or
 * TypeError from the engine as the choice resumed the turn answered with
 * Django's HTML 500 page, and the page could only say "HTTP 500". Every
 * JSON-only battle endpoint now answers any such failure with the fixed
 * sentence, keeping the error's own text in the log.
 */

const { refusal } = require('./refusals');
const { test, expect, loginAs, TEST_ADMIN } = require('./fixtures');
const { readDjangoShell } = require('./django');

/** A Game Master's manual fight, paused on a choice he makes. */
function seedPausedManualFight(name) {
    const output = readDjangoShell(`
from django.contrib.auth import get_user_model
from battle import control
from battle.models import Battle
from battle.services import advance_turn, create_battle
user = get_user_model().objects.get(username=${JSON.stringify(TEST_ADMIN.username)})
battle = create_battle(user, name=${JSON.stringify(name)}, count=3, seed=7, control_mode=control.MANUAL)
battle, _events = advance_turn(battle.pk)
assert battle.status == Battle.STATUS_AWAITING, battle.status
print('BATTLE', battle.pk)
`);
    const battle = output.match(/BATTLE (\d+)/);
    expect(battle, `no battle in: ${output}`).not.toBeNull();
    return battle[1];
}

/** Store a figure the engine cannot fight with: one with no Fatigue. */
function breakFirstFigure(battleId) {
    readDjangoShell(`
from battle.models import Battle
battle = Battle.objects.get(pk=${battleId})
state = battle.state_json
state['combatants'][0]['fatigue'] = None
Battle.objects.filter(pk=battle.pk).update(state_json=state)
`);
}

test.describe('Battle page — an internal failure under a choice', () => {
    test('is reported in the fixed sentence, not as a bare status (#113)', async ({ page }, testInfo) => {
        const name = `E2E internal ${testInfo.workerIndex}x${Date.now() % 100000}`;
        const battleId = seedPausedManualFight(name);
        const message = refusal('common', 'decorators', 'request_failed');
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/battles/${battleId}/`);
        const panel = page.locator('#turn-panel');
        await expect(panel.locator('.turn-option').first()).toBeVisible();

        breakFirstFigure(battleId);
        await panel.locator('.turn-option').first().click();
        const answered = page.waitForResponse((response) => (
            response.url().endsWith(`/tarmar/battles/${battleId}/act/`)
            && response.request().method() === 'POST'
        ));
        await panel.locator('.turn-confirm').click();
        const response = await answered;

        expect(response.status()).toBe(500);
        expect(response.headers()['content-type']).toContain('application/json');
        expect((await response.json()).error).toBe(message);
        await expect(page.locator('#notice-region .notice-text', {
            hasText: `Sending the action failed (HTTP 500: ${message})`,
        })).toBeVisible();
    });
});
