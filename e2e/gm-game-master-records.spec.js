/**
 * E2E for what a Game Master records by saying so in the Games Master chat
 * (issues #802, #843).
 *
 * An aide told "Gorn died" or "give them 500 XP" could record neither, a
 * skill check asked for in play was whatever the model said it rolled, and
 * none of it reached the character's sheet. Each message here is a real turn
 * through the server against the fake Ollama (e2e/fake-ollama.js), whose plan
 * names the character by the id the state block lists; what is checked is
 * the chip the page shows and the record the Characters pane reads.
 */

const { refusal } = require('./refusals');
const { test, expect, loginAs, TEST_ADMIN, TEST_USER } = require('./fixtures');
const { readDjangoShell } = require('./django');
const { loadedSkillRules } = require('./skill-rules');

/** A session of ``account`` holding one complete character of theirs. */
function seedSession(name, account = TEST_ADMIN, mode = 'aide') {
    const output = readDjangoShell(`
from django.contrib.auth import get_user_model
from common.test_helpers import create_character
from gm.models import GMSession
user = get_user_model().objects.get(username=${JSON.stringify(account.username)})
session = GMSession.objects.create(user=user, name=${JSON.stringify(`${name} records`)}, mode=${JSON.stringify(mode)})
character = create_character(user, name=${JSON.stringify(name)}, starting_coin=100, skills={'stealth': 3})
session.characters.add(character)
print('SESSION', session.pk, 'CHARACTER', character.pk)
`);
    const match = output.match(/SESSION (\d+) CHARACTER (\d+)/);
    expect(match, `no ids in: ${output}`).not.toBeNull();
    return { sessionId: match[1], characterId: match[2] };
}

/**
 * Send ``message`` and wait for this turn's own reply, then for the turn to be
 * saved: ``.last()`` alone would match the previous turn's reply.
 */
async function send(page, message) {
    const replies = page.locator('.message.assistant');
    const before = await replies.count();
    await page.fill('#user-input', message);
    await page.click('#send-btn');
    await expect(replies.nth(before)).toContainText('nothing stirs', { timeout: 30000 });
    // Send comes back only once the stream has closed, after the done frame
    // the server sends once the turn is saved (issue #910).
    await expect(page.locator('#send-btn')).toBeEnabled({ timeout: 30000 });
}

/**
 * Why the session's latest turn refused its plan, from the turn's journal:
 * every ``plan_rejected`` event's errors, joined.
 */
function latestPlanRefusals(sessionId) {
    const output = readDjangoShell(`
import json
from gm.models import GMEvent, GMTurnRecord
turn = GMTurnRecord.objects.filter(session_id=${Number(sessionId)}).latest('started_at', 'pk')
errors = [
    error
    for event in GMEvent.objects.filter(turn=turn, kind='plan_rejected')
    for error in event.payload.get('errors', [])
]
print('REFUSALS ' + json.dumps(errors))
`);
    const line = output.split('\n').find((text) => text.startsWith('REFUSALS '));
    return JSON.parse(line.slice('REFUSALS '.length)).join(' ');
}

test.describe('GM chat — a Game Master records deaths, XP, coin and skill checks', () => {
    test('a stated attribute check is held to the canon\'s dice and modifier limit (#70)', async ({ page }, testInfo) => {
        // Read from the loaded canon, so this holds under either canon.
        const { attribute_check: check } = loadedSkillRules();
        const name = `Wren${testInfo.workerIndex}x${Date.now() % 100000}`;
        const { sessionId } = seedSession(name);
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);
        const checks = page.locator('.chat-messages .dice-roll', { hasText: 'wisdom check, ' });

        // The dice's highest total and the widest modifier are taken.
        await send(page, `${name} makes a wisdom check at ${check.maximum} with ${check.modifier_limit}`);
        await expect(checks).toHaveCount(1);
        await expect(checks.last()).toContainText(
            `wisdom check, ${check.dice} stated by a Game Master = ${check.maximum}`
        );
        await expect(checks.last()).toContainText(`+${check.modifier_limit} modifier set by a Game Master`);
        expect(latestPlanRefusals(sessionId)).toBe('');

        // One past either is refused, and the journal says why: the plan is
        // read again, and no check is rolled.
        const totalRefusal = `total must be a whole number from ${check.minimum} to ${check.maximum}`;
        const modifierRefusal =
            `modifier must be a whole number from -${check.modifier_limit} to ${check.modifier_limit}`;
        for (const [total, modifier, refusal] of [
            [check.maximum + 1, 0, totalRefusal],
            [check.minimum - 1, 0, totalRefusal],
            [check.minimum, check.modifier_limit + 1, modifierRefusal],
        ]) {
            await send(page, `${name} makes a wisdom check at ${total} with ${modifier}`);
            expect(latestPlanRefusals(sessionId), `${total} with ${modifier}`).toContain(refusal);
            await expect(checks).toHaveCount(1);
        }
        // The lowest total but one, which no refused message used, with the
        // widest modifier the other way, is taken.
        await send(page, `${name} makes a wisdom check at ${check.minimum + 1} with -${check.modifier_limit}`);
        expect(latestPlanRefusals(sessionId)).toBe('');
        await expect(checks).toHaveCount(2);
        await expect(checks.last()).toContainText(
            `${check.dice} stated by a Game Master = ${check.minimum + 1}`
        );
        await expect(checks.last()).toContainText(`-${check.modifier_limit} modifier set by a Game Master`);
    });

    test('each is a chip in the chat and a change on the record', async ({ page }, testInfo) => {
        const name = `Gorn${testInfo.workerIndex}x${Date.now() % 100000}`;
        const { sessionId, characterId } = seedSession(name);
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);

        await send(page, `Award 500 xp to ${name}`);
        await expect(page.locator('.chat-messages .dice-roll').last()).toContainText('awarded 500 XP');

        await send(page, `Give ${name} 30 gold`);
        await expect(page.locator('.chat-messages .dice-roll').last()).toContainText('received 30 gp');

        await send(page, `Check ${name}'s stealth against 12`);
        const check = page.locator('.chat-messages .dice-roll').last();
        await expect(check).toContainText('stealth check: 2d6');
        await expect(check).toContainText('vs 12');

        await send(page, `${name} fell from the tower`);
        await expect(page.locator('.chat-messages .dice-roll').last()).toContainText('marked dead: fell from the tower');
        await page.screenshot({ path: testInfo.outputPath('game-master-records.png'), fullPage: true });

        // The record the sheet reads holds all four.
        const record = readDjangoShell(`
from characters.models import Character
from characters.purse import format_purse
c = Character.objects.get(pk=${characterId})
print('RECORD', c.total_xp, format_purse(c), c.is_alive, c.death_cause)
`);
        expect(record).toContain('RECORD 500 130 gp False fell from the tower');

        // The words stream before the turn is saved; Send comes back only once
        // the stream has closed, after the done frame the server sends once the
        // turn is saved (issue #910).
        await expect(page.locator('#send-btn')).toBeEnabled({ timeout: 30000 });
        // The Characters pane says so without a reload of anything but the page.
        await page.reload();
        await page.click('#gm-panel-tab-characters');
        await expect(page.locator('.gm-sheet', { hasText: name }).locator('.gm-sheet-status'))
            .toContainText('fell from the tower');
    });

    test('a player asking for the same records is told only the Game Master can', async ({ page }, testInfo) => {
        const name = `Wren${testInfo.workerIndex}x${Date.now() % 100000}`;
        const { sessionId, characterId } = seedSession(name, TEST_USER, 'play');
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);

        await send(page, `Award 500 xp to ${name}`);
        const refused = page.locator('.chat-messages .dice-roll').last();
        await expect(refused).toContainText('Not done: awarding XP');
        await expect(refused).toContainText(
            refusal('gm', 'policy', 'game_master_records')
        );

        const record = readDjangoShell(`
from characters.models import Character
c = Character.objects.get(pk=${characterId})
print('RECORD', c.total_xp, c.is_alive)
`);
        expect(record).toContain('RECORD 0 True');
    });
});

test.describe('GM chat — a refused name on a first message (issue #908)', () => {
    test('is a refusal chip, and the turn lands in a session', async ({ page }) => {
        // The refused name rolled the new session back with it: the stream
        // raised, and the page showed an error instead of the turn.
        await loginAs(page, TEST_ADMIN);
        await page.goto('/tarmar/gm/');
        await send(page, 'Make the twins in brackets, all 18s');
        await expect(page.locator('.chat-messages .dice-roll').last())
            .toContainText('Name may contain letters, numbers');
        await expect(page).toHaveURL(/session=\d+/);
        // The words stream before the turn is saved; Send comes back only once
        // the stream has closed, after the done frame the server sends once the
        // turn is saved (issue #910).
        await expect(page.locator('#send-btn')).toBeEnabled({ timeout: 30000 });
        await page.reload();
        await expect(page.locator('.message.user .message-content').last())
            .toHaveText('Make the twins in brackets, all 18s');
    });
});
