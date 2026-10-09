/**
 * The users list offers no Delete button a DM cannot use (issue #632).
 *
 * The endpoint used to accept a DM's deletion of a role admin, which on a site
 * with one Admin account left nobody who could grant the role back. The server
 * refuses that now; this spec is about the other half — the page rendering what
 * the server will accept, rather than a button that answers "Permission
 * denied" when pressed.
 *
 * The DM signed in here is seeded by the spec rather than taken from
 * accounts.js: the shared `admin` account holds both roles, and a DM *without*
 * the Admin role is exactly what this test needs. Its name carries the worker
 * index, like every other per-worker account, so parallel runs do not share it.
 */

const { test, expect, TEST_ADMIN, TEST_USER, loginAs } = require('./fixtures');
const { runDjangoShell } = require('./django');

const PARALLEL_INDEX = Number(process.env.TEST_PARALLEL_INDEX || 0);
const DM_ONLY = {
    username: `dmonly-w${PARALLEL_INDEX}`,
    password: 'dmonlypass123',
};

/** The users-table row whose Username cell links to `username`. */
function rowFor(page, username) {
    return page.locator('.users-table tbody tr').filter({
        has: page.locator(`td.cell-name a:text-is("${username}")`),
    });
}

test.beforeAll(() => {
    runDjangoShell(`
from django.contrib.auth import get_user_model
User = get_user_model()
User.objects.filter(username='${DM_ONLY.username}').delete()
dungeon_master = User.objects.create_user(
    username='${DM_ONLY.username}',
    password='${DM_ONLY.password}',
)
dungeon_master.is_dm = True
dungeon_master.is_role_admin = False
dungeon_master.save()
print('seeded', dungeon_master.username)
`);
});

test.afterAll(() => {
    runDjangoShell(`
from django.contrib.auth import get_user_model
get_user_model().objects.filter(username='${DM_ONLY.username}').delete()
`);
});

test.describe('Users list delete buttons', () => {
    test('a DM is offered no Delete button on an Admin account', async ({ page }) => {
        await loginAs(page, DM_ONLY);
        await page.goto('/tarmar/accounts/users/');

        const adminRow = rowFor(page, TEST_ADMIN.username);
        await expect(adminRow).toHaveCount(1);
        await expect(adminRow.locator('.delete-btn-outline')).toHaveCount(0);

        // ...while an account with no Admin role still carries one, so the
        // absence above is the guard and not an empty page.
        const playerRow = rowFor(page, TEST_USER.username);
        await expect(playerRow).toHaveCount(1);
        await expect(playerRow.locator('.delete-btn-outline')).toHaveCount(1);
    });

    test('an Admin is offered the Delete button on every other account', async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        await page.goto('/tarmar/accounts/users/');

        const dmRow = rowFor(page, DM_ONLY.username);
        await expect(dmRow).toHaveCount(1);
        await expect(dmRow.locator('.delete-btn-outline')).toHaveCount(1);
    });
});
