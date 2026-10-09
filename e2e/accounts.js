/**
 * The seeded test accounts, and the copy of each that a worker signs in as.
 *
 * Playwright runs every worker in its own process and sets
 * TEST_PARALLEL_INDEX there, 0..workers-1. Each worker gets its own copy of
 * each account, seeded by global-setup, because a Games Master turn started
 * by a first message has no session yet, and a page opened without one
 * resumes its user's newest such turn (issue #515). With one shared account
 * a page in one worker adopted the turn another worker had just started and
 * showed that test's characters (issue #620). Worker 0 keeps the plain
 * names, so a single-worker run, and every seeding script that names
 * `testuser` or `admin` through these constants, is unchanged.
 */

const BASE_ACCOUNTS = {
    user: {
        username: 'testuser',
        password: 'testpass123',
        is_admin: false,
        is_dm: false,
    },
    admin: {
        username: 'admin',
        password: 'adminpass123',
        is_admin: true,
        is_dm: true,
    },
    // No roles and no seats: the spectator.
    watcher: {
        username: 'watcher',
        password: 'watchpass123',
        is_admin: false,
        is_dm: false,
    },
};

/**
 * The copy of `account` that the worker at `parallelIndex` signs in as.
 * @param {{username: string, password: string, is_admin: boolean, is_dm: boolean}} account
 * @param {number} parallelIndex
 */
function workerAccount(account, parallelIndex) {
    const username = parallelIndex === 0
        ? account.username
        : `${account.username}-w${parallelIndex}`;
    return { ...account, username };
}

/**
 * Every copy of `account` that a run with `workerCount` workers can sign in
 * as: what global-setup seeds and global-teardown removes.
 * @param {{username: string, password: string, is_admin: boolean, is_dm: boolean}} account
 * @param {number} workerCount
 */
function workerCopies(account, workerCount) {
    return Array.from(
        { length: workerCount },
        (_unused, parallelIndex) => workerAccount(account, parallelIndex)
    );
}

/**
 * Every account a run with `workerCount` workers can sign in as.
 * @param {number} workerCount
 */
function allWorkerAccounts(workerCount) {
    return Object.values(BASE_ACCOUNTS).flatMap(
        account => workerCopies(account, workerCount)
    );
}

/**
 * A spec's own account for one test, named for the worker and the repeat
 * (issue #889).
 *
 * A spec that signs in as an account of its own and resets it in a hook
 * cannot share that account between copies of itself: `--repeat-each` runs
 * the copies in parallel across workers, and one copy's reset deleted the
 * session another copy was mid-turn in. Only one test runs in a worker at a
 * time, so the worker and the repeat together name an account no running
 * copy shares.
 * @param {string} baseUsername - the spec's own name for the account.
 * @param {string} password
 * @param {{parallelIndex: number, repeatEachIndex: number}} testInfo
 * @returns {{username: string, password: string}}
 */
function specAccount(baseUsername, password, testInfo) {
    return {
        username: `${baseUsername}-w${testInfo.parallelIndex}r${testInfo.repeatEachIndex}`,
        password,
    };
}

/**
 * The Django shell lines that delete spec accounts and everything they own,
 * for a spec's afterAll. The names reach Python as JSON literals.
 * @param {{username: string}[]} accounts
 * @returns {string}
 */
function deleteSpecAccountsScript(accounts) {
    const usernames = JSON.stringify(accounts.map(account => account.username));
    return `
from django.contrib.auth import get_user_model
from battle.models import Battle
users = get_user_model().objects.filter(username__in=${usernames})
Battle.objects.filter(created_by__in=users).delete()
users.delete()
`;
}

module.exports = {
    BASE_ACCOUNTS,
    workerAccount,
    workerCopies,
    allWorkerAccounts,
    specAccount,
    deleteSpecAccountsScript,
};
