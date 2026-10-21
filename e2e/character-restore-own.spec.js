/**
 * E2E for a player restoring their own deleted character (issue #339, item 15).
 *
 * A player could see their own deleted rows under "Show deleted" but only a
 * manager could bring one back. What only a browser proves: the owner's row
 * offers Restore, the app's own confirmation asks, and the character is back
 * among the active ones afterwards.
 */

const { test, expect, loginAs } = require('./fixtures');
const { uniqueName } = require('./unique-name');
const { readDjangoShell, runDjangoShell } = require('./django');
const { specAccount, deleteSpecAccountsScript } = require('./accounts');

// The player's own account, named for the worker and the repeat (issue #889).
// Not the shared testuser: the list pages at 50 with deleted rows last, and a
// single-worker run has every battle spec give testuser characters first, so
// its deleted rows fell to page 2 and this test found nothing (issue #949).
// Other accounts' public characters still list ahead of the deleted row; few
// specs make one, and none before this file in a single-worker run.
const restorerFor = (testInfo) => specAccount('restorer', 'restorer-pass-949', testInfo);

/**
 * Create the player's account afresh with one deleted character.
 * @param {{username: string, password: string}} player
 * @param {string} name
 * @returns {string} the character's id
 */
function seedDeletedCharacter(player, name) {
    const output = readDjangoShell(`
from django.contrib.auth import get_user_model
from django.utils import timezone
from common.test_helpers import create_character
User = get_user_model()
User.objects.filter(username=${JSON.stringify(player.username)}).delete()
user = User.objects.create_user(username=${JSON.stringify(player.username)}, password=${JSON.stringify(player.password)})
character = create_character(user, name=${JSON.stringify(name)})
character.is_deleted = True
character.deleted_at = timezone.now()
character.save()
print('CHARACTER', character.pk)
`);
    const match = output.match(/CHARACTER (\d+)/);
    expect(match, `no character id in: ${output}`).not.toBeNull();
    return match[1];
}

test.afterAll(({}, testInfo) => {
    runDjangoShell(deleteSpecAccountsScript([restorerFor(testInfo)]));
});

test('a player restores their own deleted character from the list', async ({ page }, testInfo) => {
    const player = restorerFor(testInfo);
    const name = uniqueName('Restore Own');
    const characterId = seedDeletedCharacter(player, name);
    await loginAs(page, player);

    await page.goto('/tarmar/characters/?show_deleted=1');
    const row = page.locator('tr', { hasText: name });
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: 'Restore' }).click();

    const question = page.locator('.notice-confirm');
    await expect(question).toContainText('Restore this character?');
    await question.locator('button:has-text("Restore")').click();

    // The restore reloads the list; wait for the reloaded row, which offers
    // Delete instead of Restore, before navigating, or the two navigations race.
    const restoredRow = page.locator('tr', { hasText: name });
    await expect(restoredRow.getByRole('button', { name: 'Restore' })).toHaveCount(0);
    await expect(restoredRow.getByRole('button', { name: 'Delete' })).toBeVisible();

    // Restored: the list without deleted rows shows it, linked to its sheet.
    await page.goto('/tarmar/characters/');
    await expect(page.locator(`a[href="/tarmar/characters/${characterId}/"]`)).toHaveText(name);
});
