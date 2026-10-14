/**
 * The interval guard jest.setup.js puts around every test (#25).
 *
 * The guard fails a test that begins while an interval an earlier test
 * started is still running. These tests run in order, so each one after the
 * first also shows the guard let the test before it go: had it counted an
 * interval that was ended, this test would have failed before it ran.
 */

describe('the interval guard', () => {
    // Started at collection, outside any test, the named way: it runs on
    // through every test here and the guard clears it when the file ends.
    let suiteTicks = 0;
    startSuiteInterval(() => { suiteTicks += 1; }, 1);

    test('clearTimeout ends an interval, as browsers allow', async () => {
        let ticks = 0;
        const id = setInterval(() => { ticks += 1; }, 1);

        clearTimeout(id);
        await new Promise((resolve) => { setTimeout(resolve, 20); });

        expect(ticks).toBe(0);
    });

    test('and the guard no longer counts it, so the next test starts', async () => {
        // The suite interval has run all along without failing a test.
        await new Promise((resolve) => { setTimeout(resolve, 5); });
        expect(suiteTicks).toBeGreaterThan(0);
    });

    test('clearInterval ends one a test started', () => {
        const id = setInterval(() => {}, 1000);
        clearInterval(id);
    });

    test('so the test after that starts too', () => {
        expect(suiteTicks).toBeGreaterThan(0);
    });
});
