/**
 * Record names made by e2e/unique-name.js differ across workers (issue #141).
 *
 * Specs named records from the clock alone, and two workers reaching the
 * same millisecond made two rows of one name. No browser: these drive the
 * helper directly, as e2e/django-shell.spec.js drives the shell helpers, so
 * they take the plain `test` rather than the fixtures' gated `page`.
 */

const fs = require('fs');
const path = require('path');
const { test, expect } = require('@playwright/test');
const { uniqueName } = require('./unique-name');

const HELPER = require.resolve('./unique-name');
const FROZEN_AT = 1792542383495;
const CLOCK_RULE = 'the clock is reserved to e2e/unique-name.js: name a record with '
    + 'uniqueName or uniqueStamp, and time with performance.now()';

/**
 * Run `body` with the clock stopped at one millisecond, each "worker" a fresh
 * copy of the helper module as a separate worker process would load it.
 * @param {(loadWorker: () => typeof import('./unique-name')) => void} body
 */
function inOneMillisecond(body) {
    const realNow = Date.now;
    const loaded = require.cache[HELPER];
    Date.now = () => FROZEN_AT;
    try {
        body(() => {
            delete require.cache[HELPER];
            return require(HELPER);
        });
    } finally {
        Date.now = realNow;
        // Put this worker's own copy back, so its clock guard carries on.
        require.cache[HELPER] = loaded;
    }
}

test.describe('Unique record names', () => {
    test('two workers in the same millisecond get different names', () => {
        inOneMillisecond((loadWorker) => {
            const first = loadWorker().uniqueStamp({ workerIndex: 0, repeatEachIndex: 0 });
            const second = loadWorker().uniqueStamp({ workerIndex: 1, repeatEachIndex: 0 });

            expect(first).not.toBe(second);
        });
    });

    test('two repeats of a test in the same millisecond get different names', () => {
        inOneMillisecond((loadWorker) => {
            const first = loadWorker().uniqueStamp({ workerIndex: 2, repeatEachIndex: 0 });
            const second = loadWorker().uniqueStamp({ workerIndex: 2, repeatEachIndex: 1 });

            expect(first).not.toBe(second);
        });
    });

    test('one worker naming twice in the same millisecond gets different names', () => {
        inOneMillisecond((loadWorker) => {
            const worker = loadWorker();
            const testInfo = { workerIndex: 3, repeatEachIndex: 0 };

            expect(worker.uniqueStamp(testInfo)).not.toBe(worker.uniqueStamp(testInfo));
        });
    });

    test('a name carries the running test\'s worker and repeat, in word characters', () => {
        const { workerIndex, repeatEachIndex } = test.info();

        // The fake Game Master reads a name out of the chat with \w+.
        expect(uniqueName('E2E Probe')).toMatch(
            new RegExp(`^E2E Probe w${workerIndex}r${repeatEachIndex}t[0-9a-z]+$`)
        );
    });

    test('no e2e file reads the clock but the helper', () => {
        // Every clock read in e2e/ named a record when #141 was fixed, so the
        // clock is the helper's alone: a record name comes from it, and code
        // that times something uses performance.now() instead.
        const directory = __dirname;
        const offenders = fs.readdirSync(directory)
            .filter((file) => file.endsWith('.js') && file !== path.basename(HELPER))
            .flatMap((file) => fs.readFileSync(path.join(directory, file), 'utf8')
                .split('\n')
                .map((line, index) => ({ line, at: `${file}:${index + 1}` }))
                .filter(({ line }) => /Date\.now\(\)/.test(line))
                .map(({ at, line }) => `${at}: ${line.trim()}`));

        expect(offenders, CLOCK_RULE).toEqual([]);
    });
});
