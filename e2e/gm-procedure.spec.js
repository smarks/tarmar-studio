/**
 * E2E for the rules procedures the server runs for the GM (issue #370).
 *
 * No Ollama: the session's draft and history are seeded through
 * gm.testing.run_actions -- the executor a played turn's plan runs (issue #590) --
 * with the survival dice pinned on a natural 18, the roll that started this
 * issue by being narrated as a death. What is being checked is that the
 * outcome survives a reload as a visible chip carrying the dice, the target
 * and the verdict, and that the result sentence kept with it stays unread.
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

// One played turn: the player buys a first campaigner year, the server runs
// it with the survival dice pinned at three sixes, and the GM narrates the
// outcome it was handed. The patchers are started and stopped rather than
// used as `with` blocks: the script is flattened onto one line for
// `shell -c`, which takes statements.
const SEED_PROCEDURE = `
from unittest.mock import patch
from django.contrib.auth import get_user_model
from gm.ledger import draft_defaults
from gm.testing import run_actions, session_with_character
from gm.turn_engine.history import procedure_entry
user = get_user_model().objects.get(username='${TEST_USER.username}')
draft = draft_defaults()
draft.update({'name': 'Kael', 'strength': 13, 'dexterity': 12, 'intelligence': 11, 'wisdom': 10, 'constitution': 13, 'charisma': 9, 'fatigue_roll': 7})
session = session_with_character(user, draft, name='Procedure E2E')
dice = patch('characters.skills.roll_survival_dice', return_value=(6, 6, 6, 18))
skill = patch('characters.skills.roll_d20', return_value=1)
dice.start()
skill.start()
(result,) = run_actions(session, {'action': 'play_years', 'character_id': session.attached_characters().get().pk, 'track': 'campaigner', 'years': 1})
dice.stop()
skill.stop()
session.history = [{'role': 'user', 'content': 'I take a year as a campaigner'}, procedure_entry(result.result, result.procedure), {'role': 'assistant', 'content': 'You came through the year whole.'}]
session.save()
print('SESSION', session.pk)
`.trim().replace(/\n/g, '; ');

test.describe('GM chat — procedures the server ran', () => {
    test('a reloaded session shows the survival the code computed, not a verdict in prose', async ({ page }) => {
        const sessionId = sessionIdFrom(djangoShell(SEED_PROCEDURE));

        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);

        const chip = page.locator('.chat-messages .dice-roll');
        await expect(chip).toHaveCount(1);
        await expect(chip).toContainText('3d6 [6, 6, 6] = 18');
        await expect(chip).toContainText('target 9');
        await expect(chip).toContainText('automatic survival');
        await expect(chip).toContainText('SURVIVED');
        await expect(chip).not.toContainText('DIED');

        // The player's turn and the GM's answer are both still there, and the
        // model-only block is not.
        await expect(page.locator('.chat-messages')).toContainText('I take a year as a campaigner');
        await expect(page.locator('.chat-messages')).toContainText('You came through the year whole.');
        await expect(page.locator('.chat-messages')).not.toContainText('Survival check: 3d6 rolled');
    });
});
