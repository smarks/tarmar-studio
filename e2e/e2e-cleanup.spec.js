/**
 * The suite's own cleanup reaches what no user delete does (issues #879, #882).
 *
 * No browser: these drive the helpers the teardown and the guest spec call,
 * the way e2e/django-shell.spec.js drives the shell helpers, so they take the
 * plain `test` rather than the fixtures' gated `page`.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const { test, expect } = require('@playwright/test');
const { readDjangoShell } = require('./django');
const {
    E2E_GUEST_BATTLE_PREFIX,
    deleteGuestTestBattles,
    resetGuestBattleRate,
} = require('./cleanup');
const { removeMailDirectory } = require('./mail');

test.describe('E2E cleanup', () => {
    test('the teardown deletes the guest spec battles and keeps everything else', () => {
        // A prefix of this test's own, still under the guest spec's, so a
        // guest test running beside this one keeps its battle.
        const prefix = `${E2E_GUEST_BATTLE_PREFIX}Cleanup ${process.pid}-${Date.now()} `;
        const name = JSON.stringify(prefix);
        const made = readDjangoShell(`
from django.contrib.auth import get_user_model
from battle.models import Battle, BattleDraft
owner = get_user_model().objects.filter(username='admin').first()
guest = Battle.objects.create(name=${name} + 'bout', seed=1, guest_key='e2e-cleanup')
draft = BattleDraft.objects.create(name=${name} + 'draft', guest_key='e2e-cleanup')
owned = Battle.objects.create(name=${name} + 'owned', seed=1, created_by=owner)
developer = Battle.objects.create(name='Developer bout ' + ${name}, seed=1, guest_key='e2e-cleanup')
print('IDS', guest.pk, draft.pk, owned.pk, developer.pk)
`);
        const ids = made.match(/IDS (\d+) (\d+) (\d+) (\d+)/).slice(1);

        deleteGuestTestBattles(prefix);

        const left = readDjangoShell(`
from battle.models import Battle, BattleDraft
print('LEFT',
      Battle.objects.filter(pk=${ids[0]}).exists(),
      BattleDraft.objects.filter(pk=${ids[1]}).exists(),
      Battle.objects.filter(pk=${ids[2]}).exists(),
      Battle.objects.filter(pk=${ids[3]}).exists())
Battle.objects.filter(pk__in=[${ids[2]}, ${ids[3]}]).delete()
`);
        // The guest battle and draft are gone; a signed-in account's battle
        // and a developer's own guest battle stay.
        expect(left).toContain('LEFT False False True True');
    });

    test('a guest test starts with a fresh guest-battle rate window', () => {
        const probe = readDjangoShell(`
from datetime import timedelta
from django.utils import timezone
from battle.limits import GUEST_BATTLES_PER_WINDOW, GUEST_RATE_KEY_PREFIX
from common.models import RateLimitCounter
from common.rate_limit import charge, current_count
key = GUEST_RATE_KEY_PREFIX + '203.0.113.9'
for _ in range(GUEST_BATTLES_PER_WINDOW):
    charge(key, 3600)
other = RateLimitCounter.objects.create(
    key='e2e-cleanup-other', count=3, expires_at=timezone.now() + timedelta(hours=1)
)
print('BEFORE', current_count(key))
`);
        expect(probe).toContain('BEFORE 5');

        resetGuestBattleRate();

        const after = readDjangoShell(`
from battle.limits import GUEST_RATE_KEY_PREFIX
from common.models import RateLimitCounter
from common.rate_limit import current_count
print('AFTER', current_count(GUEST_RATE_KEY_PREFIX + '203.0.113.9'),
      current_count('e2e-cleanup-other'))
RateLimitCounter.objects.filter(key='e2e-cleanup-other').delete()
`);
        // The guest window is open again; any other limiter's count stands.
        expect(after).toContain('AFTER 0 3');
    });

    test('removeMailDirectory removes the directory and what is in it, and a '
        + 'missing one is not an error (issue #17)', () => {
        // A directory of this test's own: the run's real one may hold a
        // message another worker's spec is about to take.
        const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'tarmar-mail-removal-test-'));
        fs.writeFileSync(path.join(directory, '20261014-120000-message.log'), 'To: a@b.c\n');

        removeMailDirectory(directory);
        expect(fs.existsSync(directory)).toBe(false);

        // A run that sent no mail never made the directory; that is not an error.
        expect(() => removeMailDirectory(directory)).not.toThrow();
    });
});
