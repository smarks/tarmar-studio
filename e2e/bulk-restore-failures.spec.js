/**
 * E2E for restoring several deleted characters at once (issue #339, item 35).
 *
 * "Restore Selected" posted one restore per character and reloaded whatever
 * happened, so a failure vanished with the page. It now counts failures and
 * keeps a notice on screen instead of reloading.
 */

const { test, expect } = require('./fixtures');
const { uniqueStamp } = require('./unique-name');
const { readDjangoShell } = require('./django');

test('a restore that fails is reported and the page does not reload', async ({ adminPage: page }) => {
    // A dedicated owner, so the list can be filtered to exactly these two rows.
    // The character list pages at 50 and an admin sees everyone's characters,
    // so rows seeded under a shared account can land past page 1 in a full run.
    const stamp = uniqueStamp();
    const ownerName = `e2e_restore_${stamp}`;
    const names = [`E2E Gone A ${stamp}`, `E2E Gone B ${stamp}`];
    const seeded = readDjangoShell(`
from django.contrib.auth import get_user_model
from common.test_helpers import create_character
owner = get_user_model().objects.create_user(username='${ownerName}', password='unused-Password-9')
for name in ${JSON.stringify(names).replace(/"/g, "'")}:
    character = create_character(owner, name=name)
    character.is_deleted = True
    character.save()
print('OWNER', owner.pk)
`);
    const ownerId = seeded.match(/OWNER (\d+)/)[1];

    try {
        await page.goto(`/tarmar/characters/?show_deleted=1&user=${ownerId}`);
        for (const name of names) {
            await page.locator('tr', { hasText: name }).locator('.character-checkbox').check();
        }

        let restoreCalls = 0;
        await page.route('**/tarmar/characters/*/restore/', async (route) => {
            restoreCalls += 1;
            if (restoreCalls === 1) {
                await route.fulfill({ status: 500, contentType: 'application/json', body: '{"success": false}' });
            } else {
                await route.continue();
            }
        });

        const urlBefore = page.url();
        await page.click('#restore-selected-btn');
        await page.locator('.notice-confirm button', { hasText: 'Restore' }).click();

        await expect(page.locator('.notice-error')).toContainText('1 of 2 character(s) could not be restored');
        expect(page.url()).toBe(urlBefore);
        expect(restoreCalls).toBe(2);
    } finally {
        readDjangoShell(`
from django.contrib.auth import get_user_model
get_user_model().objects.filter(username='${ownerName}').delete()
`);
    }
});
