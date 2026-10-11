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

const { test, expect, loginAs, TEST_ADMIN, TEST_USER } = require('./fixtures');
const { readDjangoShell } = require('./django');

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

async function send(page, message) {
    await page.fill('#user-input', message);
    await page.click('#send-btn');
    await expect(page.locator('.message.assistant').last()).toContainText('nothing stirs', { timeout: 30000 });
}

test.describe('GM chat — a Game Master records deaths, XP, coin and skill checks', () => {
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
        await expect(refused).toContainText('only the Game Master marks a death, awards XP');

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
