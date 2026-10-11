/**
 * E2E: the Games Master chat's rolls answer to the character's condition
 * (issues #886, #911).
 *
 * The aide rolled a wounded character's dice as if unhurt, and could not roll
 * a dying character's survival save. A check against a score now goes
 * through an action that applies the injury table's penalty the rule's way
 * (an attribute check lowers the target); a free-form roll takes no penalty,
 * since a backwards direction in the plan once turned it into a bonus (#911);
 * and a survival save is an action the server rolls, once for each state of
 * the wounds for a player.
 * Each message is a real turn against the fake Ollama (e2e/fake-ollama.js).
 */

const { test, expect, loginAs, TEST_USER } = require('./fixtures');
const { readDjangoShell } = require('./django');

/**
 * A player's session with an injured character, a dying one and a dead one.
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
dead = create_character(user, name=${JSON.stringify(`Dead${stamp}`)}, is_alive=False, death_cause='Fell from the tower')
session.characters.add(hurt, dying, dead)
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
    test('an injured check counts the penalty and a dying character rolls one save', async ({ page }, testInfo) => {
        const stamp = `${testInfo.workerIndex}x${testInfo.repeatEachIndex}x${Date.now() % 100000}`;
        const sessionId = seedSession(stamp);
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);

        // Planned "over", which once took the point off a roll-under total:
        // the server reads no direction, so the line is the dice alone.
        await send(page, `Hurt${stamp} tests her nerve`);
        await expect(page.locator('.chat-messages .dice-roll').last())
            .toHaveText(/rolls 3d6 for a test of nerve: \[.+\] = \d+$/);

        await send(page, `Hurt${stamp} makes a constitution check`);
        await expect(page.locator('.chat-messages .dice-roll').last())
            .toContainText(/constitution check, 3d6: \[.+\] = \d+ (≤|>) 11 \(CON 12 -1 injured\): (passes|fails)/);

        await send(page, `Roll Dying${stamp}'s survival save`);
        await expect(page.locator('.chat-messages .dice-roll').last())
            .toContainText(`Dying${stamp}: survival save, 3d6: [`);

        // A player's second save against the same wounds is refused, unless
        // the first one killed them, which is also the end of the saves: the
        // dead are refused every change until revived (#932).
        await send(page, `Roll Dying${stamp}'s survival save`);
        await expect(page.locator('.chat-messages .dice-roll').last())
            .toContainText(/Not done: rolling a survival save.*(already rolled|is dead; a Game Master must bring them back to life first)/i);

        // The dead are refused every change until revived (#932), whatever
        // the dice did above: a character seeded dead takes no save.
        await send(page, `Roll Dead${stamp}'s survival save`);
        await expect(page.locator('.chat-messages .dice-roll').last())
            .toContainText(`Dead${stamp} is dead; a Game Master must bring them back to life first`);
    });
});
