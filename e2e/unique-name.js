/**
 * The one way a spec makes a record name no other test can share (issue #141).
 *
 * Specs used to name records from `Date.now()` alone. The admin account sees
 * every worker's records, so two workers reaching the same millisecond made
 * two rows of one name, and a test counting or finding rows by that name
 * failed: "E2E Once 1792542383495" was found twice in 2 of 30 runs on 4
 * workers while the server had made one battle per start.
 *
 * The stamp carries the worker index, which no two live workers share, and
 * the repeat index, so a `--repeat-each` copy is told apart in a failure; the
 * time is kept strictly increasing within a worker, so two names made in the
 * same millisecond still differ, and it tells this run's rows from an earlier
 * run's in the same database. It is letters and digits only, so a name made
 * from it still matches `\w+` where the fake Game Master reads a name out of
 * the chat.
 */

const { test } = require('@playwright/test');

// The last time this worker process put in a stamp, in milliseconds.
let lastStampedAt = 0;

/**
 * A stamp unique to this call across every worker of the run.
 *
 * Interpolate it into a record name: `E2E Lobby ${uniqueStamp()}`.
 * @param {Pick<import('@playwright/test').TestInfo, 'workerIndex' | 'repeatEachIndex'>} [testInfo]
 *   The running test's info; the current test's by default.
 * @returns {string} For example `w3r0tmh0k2x1c`.
 */
function uniqueStamp(testInfo = test.info()) {
    const now = Date.now();
    lastStampedAt = now > lastStampedAt ? now : lastStampedAt + 1;
    return `w${testInfo.workerIndex}r${testInfo.repeatEachIndex}t${lastStampedAt.toString(36)}`;
}

/**
 * A record name no other test of the run can share: `prefix`, a space, a stamp.
 *
 * Where the name needs another shape (no space, a username, one stamp across
 * several names), interpolate {@link uniqueStamp} instead.
 * @param {string} prefix - Says which test made the record, e.g. `E2E Lobby`.
 * @returns {string} For example `E2E Lobby w3r0tmh0k2x1c`.
 */
function uniqueName(prefix) {
    return `${prefix} ${uniqueStamp()}`;
}

module.exports = { uniqueName, uniqueStamp };
