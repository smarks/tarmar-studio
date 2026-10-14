/**
 * A file that breaks jest.setup.js's leak guards on purpose (#25).
 *
 * Not a suite of the project's own: jest-setup.test.js runs it in a separate
 * Jest and checks which of these tests the guards fail, and with what. The
 * tests run in the order written; that order is the subject.
 */

// Started outside any test, with plain setInterval rather than
// startSuiteInterval: the guard leaves it to the end of the file and fails
// the file there, naming it as started in setup.
setInterval(() => {}, 1000);

test('leaks an interval', () => {
    setInterval(() => {}, 1000);
});

test('begins after the leak', () => {});

test('ends its interval with clearTimeout', () => {
    clearTimeout(setInterval(() => {}, 1000));
});

test('begins after clearTimeout', () => {});

test('prints a turn warning it did not silence', () => {
    console.warn('Could not ask after GM turn 99: planted by the guard fixture');
});

test('begins after the warning', () => {});
