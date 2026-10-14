require('@testing-library/jest-dom');

// Mock window.confirm
global.confirm = jest.fn(() => true);

// Mock window.alert
global.alert = jest.fn();

// Pages navigate only through api.js's pageNavigation, which keeps these mocks
// when it loads: jsdom will not let a test replace window.location, and it
// does not implement navigation (#23). A test that needs the page at a given
// URL sets it with history.replaceState, which jsdom does support.
global.pageNavigation = { reload: jest.fn(), go: jest.fn() };

// A script that navigates around pageNavigation reaches jsdom's own
// navigation, which only reports "Not implemented" on console.error; the test
// it ran in fails here instead of passing with a warning. jsdom says nothing
// when href is set to the URL the page is already at, so that one case is
// left to the tests' assertions on pageNavigation.go.
let navigationAroundTheSeam = null;
const reportError = console.error;
console.error = (...args) => {
  const said = args.map((arg) => String((arg && arg.message) || arg)).join(' ');
  if (said.includes('Not implemented: navigation')) navigationAroundTheSeam = said;
  reportError(...args);
};
afterEach(() => {
  const said = navigationAroundTheSeam;
  navigationAroundTheSeam = null;
  if (said) throw new Error(`A script navigated without pageNavigation: ${said}`);
});

// chat.js warns each time it fails to ask after a running GM turn. A test
// that means to reach that path spies on console.warn with mockImplementation
// and counts the calls; a pass-through spy still hands the warning on to this
// guard. One that reaches the guard has a wait the test did not start, most
// likely one an earlier test left running, which goes on answering this
// test's fetches (#25). The test fails here instead of passing with the
// warning.
let unexpectedTurnWarning = null;
const reportWarning = console.warn;
const warnThroughTurnGuard = (...args) => {
  const said = args.map(String).join(' ');
  if (said.includes('Could not ask after GM turn') && !unexpectedTurnWarning) {
    unexpectedTurnWarning = said;
  }
  reportWarning(...args);
};
console.warn = warnThroughTurnGuard;
afterEach(() => {
  const said = unexpectedTurnWarning;
  unexpectedTurnWarning = null;
  // A test that failed before restoring its console.warn spy would leave it
  // in place, silencing every later warning before it reached this guard.
  console.warn = warnThroughTurnGuard;
  if (said) throw new Error(`A GM turn wait warned where no test expected it: ${said}`);
});

// A page's poll is a setInterval that runs until the page goes. Jest keeps
// one jsdom for a whole file, so a poll a test leaves running fetches with a
// later test's mock (#25), or would once the file runs long enough for it to
// fire. So every interval a test starts must be cleared before the next test
// begins. That is checked as the next test starts, after the suite's own
// afterEach teardown has run, and again when the file ends; the leftovers are
// cleared, so one leak fails one test, not every test after it.
//
// An interval started outside any test (at the top of a file, or in a
// beforeAll) runs through the file by design and is checked only when the
// file ends, as "started in setup". That check runs after every describe's
// afterAll but before the file's top-level afterAll hooks, because a setup
// file's hooks are declared first. So clear one in a describe's afterAll, or
// start it with startSuiteInterval below, the named way to keep an interval
// for the whole file: it is never counted, and is cleared when the file ends.
//
// clearTimeout ends an interval too, as browsers allow. Modern fake timers
// replace setInterval while they are on, so their intervals are not counted,
// and put this wrapper back after. Legacy fake timers
// (useFakeTimers({ legacyFakeTimers: true })) put back jsdom's own instead,
// so the guard counts nothing for the rest of that file.
const startInterval = global.setInterval;
const endInterval = global.clearInterval;
const endTimeout = global.clearTimeout;
const runningIntervals = new Map(); // id -> the test that started it, or null
const suiteIntervals = new Set();
global.setInterval = (...args) => {
  const id = startInterval(...args);
  runningIntervals.set(id, expect.getState().currentTestName || null);
  return id;
};
function forgetInterval(id) {
  runningIntervals.delete(id);
  suiteIntervals.delete(id);
}
global.clearInterval = (id) => {
  forgetInterval(id);
  return endInterval(id);
};
global.clearTimeout = (id) => {
  forgetInterval(id);
  return endTimeout(id);
};
/** setInterval for one a file means to keep running through all its tests. */
global.startSuiteInterval = (...args) => {
  const id = startInterval(...args);
  suiteIntervals.add(id);
  return id;
};
function failOnIntervalsLeftRunning(counted) {
  const left = [...runningIntervals].filter(([, startedBy]) => counted(startedBy));
  if (!left.length) return;
  for (const [id] of left) {
    endInterval(id);
    runningIntervals.delete(id);
  }
  const startedBy = [...new Set(left.map(([, test]) => test || 'started in setup, outside any test'))];
  throw new Error(`An interval was left running, started by: ${startedBy.join('; ')}`);
}
beforeEach(() => failOnIntervalsLeftRunning((startedBy) => startedBy !== null));
afterAll(() => {
  for (const id of suiteIntervals) endInterval(id);
  suiteIntervals.clear();
  failOnIntervalsLeftRunning(() => true);
});

// Mock fetch
global.fetch = jest.fn();

// jest-environment-jsdom doesn't provide TextEncoder/TextDecoder on its
// global (a known jsdom gap). chat.js's SSE stream parser constructs a
// TextDecoder, so tests that exercise it need these present.
const { TextEncoder, TextDecoder } = require('util');
global.TextEncoder = global.TextEncoder || TextEncoder;
global.TextDecoder = global.TextDecoder || TextDecoder;

// Every page that extends base.html carries the CSRF token in a <meta> tag,
// and api.js's csrfToken() reads it from there (issue #338). Install it the
// same way for every test, so a suite exercising a POST gets the token its
// page would really have rather than each fixture inventing a transport.
beforeEach(() => {
  global.pageNavigation.reload.mockClear();
  global.pageNavigation.go.mockClear();
  let meta = document.querySelector('meta[name="csrf-token"]');
  if (!meta) {
    meta = document.createElement('meta');
    meta.setAttribute('name', 'csrf-token');
    document.head.appendChild(meta);
  }
  meta.setAttribute('content', 'test-token');
});
