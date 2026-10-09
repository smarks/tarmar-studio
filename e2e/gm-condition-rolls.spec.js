/**
 * E2E: the Games Master chat's rolls answer to the character's condition
 * (issue #886).
 *
 * The aide rolled a wounded character's dice as if unhurt, and could not roll
 * a dying character's survival save. A roll the plan reads under a score now
 * counts the injury table's penalty against it, and a survival save is an
 * action the server rolls, once for each state of the wounds for a player.
 * Each message is a real turn against the fake Ollama (e2e/fake-ollama.js).
 */

const { test, expect, loginAs, TEST_USER } = require('./fixtures');
const { readDjangoShell } = require('./django');

/**
 * A player's session with an injured character and a dying one.
 * @param {string} stamp
 */
function seedSession(stamp) {
    const output = readDjangoShell(`
import math
from django.contrib.auth import get_user_model
from common.test_helpers import create_character
from gm.models import GMSession
user = get_user_model().objects.get(username=${JSON.stringify(TEST_USER.username)})
session = GMSession.objects.create(user=user, name=${JSON.stringify(`Condition ${stamp}`)}, mode='play')
hurt = create_character(user, name=${JSON.stringify(`Hurt${stamp}`)}, constitution=12)
hurt.fatigue_damage = hurt.fatigue - hurt.fatigue // 2
hurt.save()
dying = create_character(user, name=${JSON.stringify(`Dying${stamp}`)}, constitution=12)
dying.body_damage = dying.body + math.ceil(dying.body / 2)
dying.save()
session.characters.add(hurt, dying)
print('SESSION', session.pk)
`);
    const match = output.match(/SESSION (\d+)/);
    expect(match, `no id in: ${output}`).not.toBeNull();
    return match[1];
}

async function send(page, message) {
    await page.fill('#user-input', message);
    await page.click('#send-btn');
    await expect(page.locator('.message.assistant').last()).toContainText('nothing stirs', { timeout: 30000 });
}

test.describe('GM chat — rolls and the character\'s wounds', () => {
    test('an injured roll counts the penalty and a dying character rolls one save', async ({ page }, testInfo) => {
        const stamp = `${testInfo.workerIndex}x${testInfo.repeatEachIndex}x${Date.now() % 100000}`;
        const sessionId = seedSession(stamp);
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);

        await send(page, `Hurt${stamp} tests her nerve`);
        await expect(page.locator('.chat-messages .dice-roll').last())
            .toHaveText(/rolls 3d6 for a test of nerve: \[.+\] = (\d+), -1 injured on a roll under: counts as \d+/);

        await send(page, `Roll Dying${stamp}'s survival save`);
        await expect(page.locator('.chat-messages .dice-roll').last())
            .toContainText(`Dying${stamp}: survival save, 3d6: [`);

        // A player's second save against the same wounds is refused, unless
        // the first one killed them, which is also the end of the saves.
        await send(page, `Roll Dying${stamp}'s survival save`);
        await expect(page.locator('.chat-messages .dice-roll').last())
            .toContainText(/Not done: rolling a survival save.*(already rolled|no survival save)/i);
    });
});
