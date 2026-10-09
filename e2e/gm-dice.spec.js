/**
 * E2E for the dice the GM asks the server to roll (issue #363).
 *
 * No Ollama: the session's history is seeded through gm.roll_service and
 * gm.turn_engine.history -- the same code a played turn calls -- with the
 * faces pinned, and the page is then read at that session's URL. What is
 * being checked is that a roll the GM made survives a reload as the same
 * visible chip a /roll produces.
 */

const { execSync } = require('child_process');
const { test, expect, loginAs, TEST_USER } = require('./fixtures');

function djangoShell(script) {
    return execSync(`uv run python manage.py shell -c "${script.replace(/"/g, '\\"')}"`, {
        cwd: process.cwd(),
        encoding: 'utf8',
        env: { ...process.env, DEBUG: 'true' },
    });
}

function sessionIdFrom(output) {
    const match = output.match(/SESSION (\d+)/);
    expect(match, `no session id in: ${output}`).not.toBeNull();
    return match[1];
}

// One played turn: the player speaks, the GM asks for 2d6, the server rolls
// it (faces pinned at 4 and 3), and the GM answers from the result. The
// patcher is started and stopped rather than used as a `with` block: the
// script is flattened onto one line for `shell -c`, which takes statements.
const SEED_ROLL = `
from unittest.mock import patch
from django.contrib.auth import get_user_model
from gm.models import GMSession
from gm.roll_service import RollRequest, resolve_roll_request, roll_frame_payload
from gm.turn_engine.history import roll_entry
user = get_user_model().objects.get(username='${TEST_USER.username}')
faces = patch('common.rolling.random.randint', side_effect=[4, 3])
faces.start()
resolution = resolve_roll_request(RollRequest(expression='2d6', purpose='the patrol reaction'))
faces.stop()
history = [{'role': 'user', 'content': 'The patrol spots me'}, roll_entry(roll_frame_payload(resolution)), {'role': 'assistant', 'content': 'They lower their spears.'}]
print('SESSION', GMSession.objects.create(user=user, name='Dice E2E', history=history).pk)
`.trim().replace(/\n/g, '; ');

test.describe('GM chat — dice the GM rolled', () => {
    test('a reloaded session shows the real faces, not the block written for the model', async ({ page }) => {
        const sessionId = sessionIdFrom(djangoShell(SEED_ROLL));

        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);

        const diceLine = page.locator('.chat-messages .dice-roll');
        await expect(diceLine).toHaveCount(1);
        await expect(diceLine).toContainText('2d6');
        await expect(diceLine).toContainText('[4, 3] = 7');

        // The player's turn and the GM's answer are both still there, and the
        // model-only block is not.
        await expect(page.locator('.chat-messages')).toContainText('The patrol spots me');
        await expect(page.locator('.chat-messages')).toContainText('They lower their spears.');
        await expect(page.locator('.chat-messages')).not.toContainText('Server dice');
    });
});
