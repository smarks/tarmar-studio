/**
 * E2E: a sheet left open on a character who has since died refuses its
 * changes in words (issue #932).
 *
 * The sheet hides the spending controls for a dead character (#903), but a
 * page opened while the character lived still offers them, and the server is
 * the boundary: every write refuses the dead until a Game Master brings them
 * back. Real server, no routed responses.
 */

const { test, expect, loginAs, TEST_USER } = require('./fixtures');
const { uniqueStamp } = require('./unique-name');
const { readDjangoShell, runDjangoShell } = require('./django');

test.describe('A dead character\'s sheet', () => {
    test('allocating a point is refused with the reason, and nothing is spent', async ({ page }) => {
        const name = `Doomed${uniqueStamp()}`;
        const output = readDjangoShell(`
from django.contrib.auth import get_user_model
from characters.models import Character
from common.test_helpers import CHARACTER_DEFAULTS
user = get_user_model().objects.get(username='${TEST_USER.username}')
values = dict(CHARACTER_DEFAULTS, name='${name}', free_skill_points=1)
print('CHARACTER', Character.objects.create(user=user, **values).pk)
`.trim().replace(/\n/g, '; '));
        const match = output.match(/CHARACTER (\d+)/);
        expect(match, `no character id in: ${output}`).not.toBeNull();
        const characterId = match[1];

        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/characters/${characterId}/`);
        await expect(page.locator('#add-skill-btn')).toBeVisible();

        // The character dies while the page is open (a Game Master's
        // mark-dead elsewhere).
        runDjangoShell(
            `from characters.models import Character; Character.objects.filter(pk=${characterId}).update(is_alive=False, death_cause='Fell from the tower')`,
        );

        await page.locator('#new-skill-name').fill('Stealth');
        await page.locator('#add-skill-btn').click();
        await expect(page.locator('.notice .notice-text').first())
            .toContainText(`${name} is dead; a Game Master must bring them back to life first`);

        const stored = readDjangoShell(
            `from characters.models import Character; c = Character.objects.get(pk=${characterId}); print('STORED', c.free_skill_points, sorted(c.skills or {}))`,
        );
        expect(stored).toContain('STORED 1');
        expect(stored).not.toMatch(/stealth/i);
    });
});
