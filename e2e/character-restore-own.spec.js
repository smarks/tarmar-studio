/**
 * E2E for a player restoring their own deleted character (issue #339, item 15).
 *
 * A player could see their own deleted rows under "Show deleted" but only a
 * manager could bring one back. What only a browser proves: the owner's row
 * offers Restore, the app's own confirmation asks, and the character is back
 * among the active ones afterwards.
 */

const { test, expect, loginAs, TEST_USER } = require('./fixtures');
const { readDjangoShell } = require('./django');

function seedDeletedCharacter(name) {
    const output = readDjangoShell(`
from django.contrib.auth import get_user_model
from django.utils import timezone
from characters.models import Character
user = get_user_model().objects.get(username='${TEST_USER.username}')
character = Character.objects.create(user=user, name='${name}', strength=10, dexterity=10, intelligence=10, wisdom=10, constitution=10, charisma=10, appearance_roll='3', height_roll='4', weight_roll='3', fatigue_roll=7, is_deleted=True, deleted_at=timezone.now())
print('CHARACTER', character.pk)
`.trim().replace(/\n/g, '; '));
    const match = output.match(/CHARACTER (\d+)/);
    expect(match, `no character id in: ${output}`).not.toBeNull();
    return match[1];
}

test('a player restores their own deleted character from the list', async ({ page }) => {
    const name = `Restore Own ${Date.now()}`;
    const characterId = seedDeletedCharacter(name);
    await loginAs(page, TEST_USER);

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
