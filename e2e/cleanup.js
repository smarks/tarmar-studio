/**
 * What the suite leaves behind that no user delete reaches (issues #879, #882).
 *
 * The teardown removes the seeded accounts, and every battle a signed-in spec
 * makes goes with its account through `created_by`. A guest's battle has no
 * `created_by` (#305), so the guest spec's battles outlived every run. And the
 * guest rate limit counts every new guest battle from 127.0.0.1, so a repeat
 * run of the guest spec spent the hour's allowance on its sixth battle and
 * then tested the rate limit instead of the arena.
 *
 * Both scripts are narrow on purpose: a developer's own guest battles in the
 * same dev database carry no `E2E Guest ` name and survive, and only the
 * guest-battle rate key is cleared, never the login or gate counters. The
 * limit itself keeps its own server test (battle/tests/test_guest_play.py).
 */

const { runDjangoShell } = require('./django');

/** Every battle the guest spec makes is named with this prefix. */
const E2E_GUEST_BATTLE_PREFIX = 'E2E Guest ';

/**
 * The Python that deletes guest battles and drafts named with `namePrefix`.
 * @param {string} namePrefix
 */
function guestBattleCleanupScript(namePrefix) {
    const prefix = JSON.stringify(namePrefix);
    return `
from battle.models import Battle, BattleDraft

battles, _ = Battle.objects.filter(
    created_by__isnull=True, name__startswith=${prefix}
).delete()
drafts, _ = BattleDraft.objects.filter(
    created_by__isnull=True, name__startswith=${prefix}
).delete()
print(f'Deleted {battles} guest battle object(s) and {drafts} guest draft object(s)')
`;
}

/** The Python that resets the guest-battle rate window for every client IP. */
function guestRateResetScript() {
    return `
from battle.limits import GUEST_RATE_KEY_PREFIX
from common.models import RateLimitCounter

RateLimitCounter.objects.filter(key__startswith=GUEST_RATE_KEY_PREFIX).delete()
`;
}

/**
 * Delete the guest spec's battles and drafts (issue #879).
 *
 * @param {string} [namePrefix] - Narrower than the default only in the
 *     cleanup's own spec, which must not delete a guest test's battle while
 *     that test is running beside it.
 */
function deleteGuestTestBattles(namePrefix = E2E_GUEST_BATTLE_PREFIX) {
    runDjangoShell(guestBattleCleanupScript(namePrefix));
}

/** Give the next guest test a fresh rate window (issue #882). */
function resetGuestBattleRate() {
    runDjangoShell(guestRateResetScript());
}

module.exports = {
    E2E_GUEST_BATTLE_PREFIX,
    deleteGuestTestBattles,
    resetGuestBattleRate,
};
