/**
 * The leak guards jest.setup.js puts around every test (#25).
 *
 * Whether a guard fails a test is only seen from outside the file it runs
 * in, so the failing cases run a fixture that leaks on purpose in a separate
 * Jest, with this project's own config and setup, and read its results.
 */
const { spawnSync } = require('child_process');
const path = require('path');

const wait = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

describe('intervals in jsdom', () => {
    let suiteTicks = 0;
    startSuiteInterval(() => { suiteTicks += 1; }, 1);

    test('clearTimeout ends an interval, as browsers allow', async () => {
        let ticks = 0;
        const id = setInterval(() => { ticks += 1; }, 1);

        clearTimeout(id);
        await wait(20);

        expect(ticks).toBe(0);
    });

    test('an interval started with startSuiteInterval runs on through the tests', async () => {
        const before = suiteTicks;
        await wait(20);
        expect(suiteTicks).toBeGreaterThan(before);
    });
});

describe('the guards, run against a file that leaks', () => {
    const repoRoot = path.resolve(__dirname, '../../..');
    let results;
    let fileFailure;

    beforeAll(() => {
        const run = spawnSync(process.execPath, [
            require.resolve('jest/bin/jest'),
            '--config', path.join(repoRoot, 'jest.config.js'),
            '--testMatch', '**/__tests__/fixtures/guard-leaks.fixture.js',
            '--coverage=false',
            '--json',
        ], { cwd: repoRoot, encoding: 'utf8', timeout: 60000 });
        let report;
        try {
            if (run.error) throw run.error;
            if (run.signal) throw new Error('it was killed');
            report = JSON.parse(run.stdout);
        } catch (err) {
            throw new Error(`The fixture's Jest gave no report (${err.message}; `
                + `status ${run.status}, signal ${run.signal}):\n${run.stderr}`, { cause: err });
        }
        const [file] = report.testResults;
        fileFailure = file.message;
        results = Object.fromEntries(file.assertionResults.map(
            (result) => [result.title, { status: result.status, said: result.failureMessages.join('\n') }]));
    }, 60000);

    test('fail the test that begins while an earlier test\'s interval runs, naming that test', () => {
        expect(results['leaks an interval'].status).toBe('passed');
        expect(results['begins after the leak'].status).toBe('failed');
        expect(results['begins after the leak'].said)
            .toContain('An interval was left running, started by: leaks an interval');
    });

    test('let an interval go once clearTimeout has ended it', () => {
        expect(results['ends its interval with clearTimeout'].status).toBe('passed');
        expect(results['begins after clearTimeout'].status).toBe('passed');
    });

    test('fail a test that prints a turn warning it did not silence, quoting it', () => {
        expect(results['prints a turn warning it did not silence'].status).toBe('failed');
        expect(results['prints a turn warning it did not silence'].said).toContain(
            'A GM turn wait warned where no test expected it: '
            + 'Could not ask after GM turn 99: planted by the guard fixture');
        expect(results['begins after the warning'].status).toBe('passed');
    });

    test('fail the file when an interval started in setup is still running at its end', () => {
        expect(fileFailure)
            .toContain('An interval was left running, started by: started in setup, outside any test');
    });
});
