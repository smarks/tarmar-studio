/**
 * E2E for a Game Master bringing a character back and removing one (issue #403).
 *
 * No Ollama: the changes are run through gm.testing.run_actions -- the same
 * executor a played turn's plan runs (issue #590) --
 * on a seeded session holding session 29's twins, the sister dead in her
 * fourth magic year. What is checked is what the Game Master sees afterwards
 * in the Characters pane.
 */

const { execSync } = require('child_process');
const { test, expect, loginAs, TEST_ADMIN } = require('./fixtures');

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

const seedTwins = (action) => `
from unittest.mock import patch
from django.contrib.auth import get_user_model
from gm import roster
from gm.models import GMSession
from gm.testing import run_actions
user = get_user_model().objects.get(username='${TEST_ADMIN.username}')
skill = patch('characters.skills.roll_d20', return_value=1)
live = patch('characters.skills.roll_survival_dice', return_value=(6, 6, 6, 18))
die = patch('characters.skills.roll_survival_dice', return_value=(1, 1, 1, 3))
session = GMSession.objects.create(user=user, name='Revive E2E')
skill.start()
run_actions(session, {'action': 'record_attributes', 'new_character': {'label': 'Twin Male'}, 'all': 18}, {'action': 'record_attributes', 'new_character': {'label': 'Twin Female'}, 'all': 18})
ids = {entry.label: entry.character.pk for entry in roster.roster(session)}
live.start()
run_actions(session, {'action': 'play_years', 'character_id': ids['Twin Male'], 'track': 'underworld', 'years': 5})
live.stop()
die.start()
run_actions(session, {'action': 'play_years', 'character_id': ids['Twin Female'], 'track': 'magic', 'years': 5})
die.stop()
skill.stop()
${action ? `run_actions(session, {'action': '${action}', 'character_id': ids['Twin Female']})` : 'pass'}
print('SESSION', session.pk)
`.trim().replace(/\n/g, '; ');

async function openCharacters(page, sessionId) {
    await page.goto(`/tarmar/gm/?session=${sessionId}`);
    await page.click('#gm-panel-tab-characters');
}

test.describe('GM panel — a Game Master bringing a character back or removing one', () => {
    test('the dead twin shows as dead until she is brought back', async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        const dead = sessionIdFrom(djangoShell(seedTwins('')));
        await openCharacters(page, dead);
        await expect(page.locator('.gm-draft.gm-sheet-dead')).toHaveCount(1);
        await expect(page.locator('.gm-draft.gm-sheet-dead .gm-sheet-name')).toHaveText('Twin Female');

        const revived = sessionIdFrom(djangoShell(seedTwins('revive_character')));
        await openCharacters(page, revived);
        await expect(page.locator('.gm-draft')).toHaveCount(2);
        await expect(page.locator('.gm-draft.gm-sheet-dead')).toHaveCount(0);
    });

    test('a removed twin is gone from the pane', async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        const removed = sessionIdFrom(djangoShell(seedTwins('remove_character')));
        await openCharacters(page, removed);

        await expect(page.locator('.gm-draft')).toHaveCount(1);
        await expect(page.locator('.gm-draft .gm-sheet-name')).toHaveText('Twin Male');
    });
});
