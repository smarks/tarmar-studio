/**
 * Playwright global setup - creates test users before E2E tests run.
 */

const { runDjangoCommand, runDjangoShell } = require('./django');
const { BASE_ACCOUNTS, workerCopies, allWorkerAccounts } = require('./accounts');

/**
 * @param {import('@playwright/test').FullConfig} config - its `workers` is
 *     the resolved count, so every worker finds its own copy of each account
 *     (issue #620).
 */
async function globalSetup(config) {
    // CI starts from a bare checkout with no dev database, so migrate before
    // touching any model.
    console.log('\n[E2E Setup] Applying migrations...');
    runDjangoCommand('migrate --noinput');

    console.log(`[E2E Setup] Creating test users for ${config.workers} worker(s)...`);

    for (const user of allWorkerAccounts(config.workers)) {
        const script = `
from django.contrib.auth import get_user_model
User = get_user_model()

# Delete if exists (clean slate)
User.objects.filter(username='${user.username}').delete()

# Create user
u = User.objects.create_user(
    username='${user.username}',
    password='${user.password}'
)
u.is_role_admin = ${user.is_admin ? 'True' : 'False'}
u.is_dm = ${user.is_dm ? 'True' : 'False'}
u.save()
print(f'Created user: ${user.username}')
`;

        try {
            runDjangoShell(script);
        } catch (error) {
            console.error(`Failed to create user ${user.username}:`, error.message);
            throw error;
        }
    }

    console.log('[E2E Setup] Test users created successfully.');

    clearRateLimits();

    for (const owner of workerCopies(BASE_ACCOUNTS.user, config.workers)) {
        seedCharacters(owner.username);
    }

    seedBeasts();

    console.log('[E2E Setup] Setup complete.\n');
}

// Rate-limit counters live in the persistent dev database (RateLimitCounter
// rows and DatabaseCache entries), so they survive across runs. The login
// limiter's fixed 300s window outlives several back-to-back suite runs, and
// each run leaks one charge from auth.spec's deliberate wrong-password test —
// stack a few runs plus the parallel workers' in-flight charges and legitimate
// logins start drawing 429s. Each run starts from a clean slate instead, the
// same way the unit tests cache.clear() in setUp.
function clearRateLimits() {
    console.log('[E2E Setup] Clearing rate-limit counters...');

    const script = `
from django.core.cache import cache

from common.models import RateLimitCounter

deleted, _ = RateLimitCounter.objects.all().delete()
cache.clear()
print(f'Cleared {deleted} rate-limit counter(s) and the cache')
`;

    try {
        runDjangoShell(script);
    } catch (error) {
        console.error('Failed to clear rate limits:', error.message);
        throw error;
    }
}

// Seed characters owned by a worker's copy of `testuser`. Without these,
// every character-dependent spec hits its `if (!hasCharacters) test.skip()`
// guard and silently passes while asserting nothing. We seed two living
// characters and one soft-deleted one (so the deleted-row tests run too), and
// assert the count so a seeding failure breaks the run loudly instead of
// reviving the silent skips.
function seedCharacters(ownerUsername) {
    console.log(`[E2E Setup] Seeding characters for ${ownerUsername}...`);

    const script = `
from django.contrib.auth import get_user_model
from django.utils import timezone
from characters.models import Character

User = get_user_model()
owner = User.objects.get(username='${ownerUsername}')
Character.objects.filter(user=owner).delete()  # clean slate

common = dict(
    strength=12, dexterity=12, intelligence=12, wisdom=12,
    constitution=12, charisma=12,
    appearance_roll='3', height_roll='3', weight_roll='3', fatigue_roll=7,
)
for character_name in ['Aldwin the Bold', 'Mira Quickhand']:
    Character.objects.create(user=owner, name=character_name, **common)

deleted = Character.objects.create(user=owner, name='Old Ghost', **common)
deleted.is_deleted = True
deleted.deleted_at = timezone.now()
deleted.save()

count = Character.objects.filter(user=owner).count()
assert count == 3, f'expected 3 seeded characters, got {count}'
print(f'Seeded {count} characters for ${ownerUsername}')
`;

    try {
        runDjangoShell(script);
    } catch (error) {
        console.error('Failed to seed characters:', error.message);
        throw error;
    }
}

// Load the bestiary catalog and clear generated variants left by earlier
// runs, so the bestiary spec always starts from the canonical 239 beasts.
function seedBeasts() {
    console.log('[E2E Setup] Loading bestiary fixture...');

    const script = `
from bestiary.models import Beast
Beast.objects.filter(is_generated=True).delete()
`;

    try {
        runDjangoShell(script);
        runDjangoCommand('loaddata beasts');
    } catch (error) {
        console.error('Failed to seed beasts:', error.message);
        throw error;
    }
}

module.exports = globalSetup;
