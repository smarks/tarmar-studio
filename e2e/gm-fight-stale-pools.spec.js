/**
 * E2E for a record-writing fight whose records moved under it (issue #842).
 *
 * A fight that writes to the character records (a Games Master session's
 * fight, or a battle a Game Master runs) used to fight on from the pools it
 * paused with, whatever healing the Game Master had recorded since. It is now
 * refused, the page says why for as long as it is so, and a Game Master takes
 * the pools from the records. Coordinator's ruling under the standing rule;
 * Spencer may overrule.
 */

const { test, expect, loginAs, TEST_ADMIN } = require('./fixtures');
const { readDjangoShell } = require('./django');

/** Heal a character on the record, as a Game Master's sheet would. */
function heal(characterId) {
    readDjangoShell(`
from characters.models import Character
Character.objects.filter(pk=${characterId}).update(fatigue_damage=0, body_damage=0)
`);
}

/** A Game Master's paused fight whose character the fight has hurt. */
function seedHurtFight(name) {
    const output = readDjangoShell(`
from django.contrib.auth import get_user_model
from battle.models import Battle
from battle.services import advance_turn, create_battle
from characters.models import Character
from common.test_helpers import create_character
user = get_user_model().objects.get(username=${JSON.stringify(TEST_ADMIN.username)})
found = None
for seed in range(1, 40):
    character = create_character(user, name=${JSON.stringify(name)})
    battle = create_battle(user, name=${JSON.stringify(`${name} duel`)}, count=1, seed=seed, character_ids=(character.pk,))
    for _turn in range(6):
        battle, _events = advance_turn(battle.pk)
        character.refresh_from_db()
        if battle.status in (Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE):
            break
        if character.fatigue_damage or character.body_damage:
            found = battle
            break
    if found:
        break
print('BATTLE', found.pk, 'CHARACTER', character.pk)
`);
    const match = output.match(/BATTLE (\d+) CHARACTER (\d+)/);
    expect(match, `no battle in: ${output}`).not.toBeNull();
    return { battleId: match[1], characterId: match[2] };
}

/** Wound a character on the record, as a fight elsewhere would. */
function wound(characterId) {
    readDjangoShell(`
from characters.models import Character
Character.objects.filter(pk=${characterId}).update(fatigue_damage=1)
`);
}

/** A Game Master's manual fight, paused on a choice he makes. */
function seedPausedManualFight(name) {
    const output = readDjangoShell(`
from django.contrib.auth import get_user_model
from battle import control
from battle.models import Battle
from battle.services import advance_turn, create_battle
from common.test_helpers import create_character
user = get_user_model().objects.get(username=${JSON.stringify(TEST_ADMIN.username)})
character = create_character(user, name=${JSON.stringify(name)})
battle = create_battle(user, name=${JSON.stringify(`${name} duel`)}, count=1, seed=3, character_ids=(character.pk,), control_mode=control.MANUAL)
battle, _events = advance_turn(battle.pk)
assert battle.status == Battle.STATUS_AWAITING, battle.status
print('BATTLE', battle.pk, 'CHARACTER', character.pk)
`);
    const match = output.match(/BATTLE (\d+) CHARACTER (\d+)/);
    expect(match, `no battle in: ${output}`).not.toBeNull();
    return { battleId: match[1], characterId: match[2] };
}

test.describe('Battle page — a fight whose records moved under it', () => {
    test('says so, holds the turn, and the Game Master takes the pools from the records', async ({ page }, testInfo) => {
        const name = `Aldo${testInfo.workerIndex}x${Date.now() % 100000}`;
        const { battleId, characterId } = seedHurtFight(name);
        heal(characterId);
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/battles/${battleId}/`);

        const notice = page.locator('#battle-stale-pools');
        await expect(notice).toBeVisible();
        await expect(notice).toContainText(`${name}: the record's pools have changed`);
        await expect(page.locator('#advance-turn')).toBeDisabled();
        await expect(page.locator('#advance-turn')).toHaveText(/Pools changed on the records/);
        await page.screenshot({ path: testInfo.outputPath('stale-pools.png'), fullPage: true });

        // The notice stays until something changes the state it reads.
        await page.reload();
        await expect(notice).toBeVisible();

        await page.click('#resnapshot-pools');
        await page.waitForURL(new RegExp(`/tarmar/battles/${battleId}/$`));
        await expect(page.locator('#battle-stale-pools')).toHaveCount(0);
        await expect(page.locator('#advance-turn')).toBeEnabled();
        await expect(page.locator('#battle-log')).toContainText('took the pools from the character records');
    });

    test('a page open when the records move gets the notice and the control from the refusal', async ({ page }, testInfo) => {
        const name = `Alda${testInfo.workerIndex}x${Date.now() % 100000}`;
        const { battleId, characterId } = seedHurtFight(name);
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/battles/${battleId}/`);
        await expect(page.locator('#battle-stale-pools')).toHaveCount(0);

        heal(characterId);
        await page.click('#advance-turn');

        await expect(page.locator('#battle-stale-pools')).toContainText(`${name}: the record's pools have changed`);
        await expect(page.locator('#advance-turn')).toBeDisabled();
        await page.click('#resnapshot-pools');
        await page.waitForURL(new RegExp(`/tarmar/battles/${battleId}/$`));
        await expect(page.locator('#battle-stale-pools')).toHaveCount(0);
        await expect(page.locator('#advance-turn')).toBeEnabled();
    });

    test('a choice made when the records moved gets the notice and the control from its refusal (#112)', async ({ page }, testInfo) => {
        const name = `Aldc${testInfo.workerIndex}x${Date.now() % 100000}`;
        const { battleId, characterId } = seedPausedManualFight(name);
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/battles/${battleId}/`);
        await expect(page.locator('#battle-stale-pools')).toHaveCount(0);
        const panel = page.locator('#turn-panel');
        await expect(panel.locator('.turn-option').first()).toBeVisible();

        wound(characterId);
        await panel.locator('.turn-option').first().click();
        // The Game Master's poll brings the notice too while the move is
        // awaited (#892), so the act's own answer is what is checked.
        const answered = page.waitForResponse((response) => (
            response.url().endsWith(`/tarmar/battles/${battleId}/act/`)
            && response.request().method() === 'POST'
        ));
        await panel.locator('.turn-confirm').click();
        const response = await answered;

        expect(response.status()).toBe(409);
        expect((await response.json()).stale_html).toContain('id="resnapshot-pools"');
        await expect(page.locator('#battle-stale-pools')).toContainText(`${name}: the record's pools have changed`);
        await expect(page.locator('#resnapshot-pools')).toBeVisible();
        await expect(page.locator('#advance-turn')).toBeDisabled();
        await expect(page.locator('#advance-turn')).toHaveText(/Pools changed on the records/);
        // Rendered as a refusal, not reported as a failed request: a page
        // that only the poll repaints says "Sending the action failed".
        await expect(page.locator('#notice-region .notice-text', {
            hasText: 'Sending the action failed',
        })).toHaveCount(0);
    });
});
