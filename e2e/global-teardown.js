/**
 * Playwright global teardown - removes test users after E2E tests complete.
 */

const { runDjangoShell } = require('./django');
const { allWorkerAccounts } = require('./accounts');
const { deleteGuestTestBattles } = require('./cleanup');

/**
 * @param {import('@playwright/test').FullConfig} config - the same `workers`
 *     global-setup seeded a copy of each account for (issue #620).
 */
async function globalTeardown(config) {
    console.log('\n[E2E Teardown] Cleaning up test users...');

    const seeded = allWorkerAccounts(config.workers).map(account => account.username);
    // Single-quoted Python literals; JSON.stringify would do as well now that
    // the script reaches Python as one argv element (issue #664).
    const usernames = `[${[...seeded, 'newuser', 'testbrowser'].map(name => `'${name}'`).join(', ')}]`;
    const script = `
from django.contrib.auth import get_user_model
from django.db.models import Q
User = get_user_model()

# Delete test users by exact name or pattern
deleted, details = User.objects.filter(
    Q(username__in=${usernames}) |
    Q(username__startswith='testuser_')  # Registration test users
).delete()
print(f'Deleted {deleted} object(s): {details}')
`;

    runDjangoShell(script);

    // A guest's battle has no user to go with (issue #879).
    deleteGuestTestBattles();

    console.log('[E2E Teardown] Cleanup complete.\n');
}

module.exports = globalTeardown;
