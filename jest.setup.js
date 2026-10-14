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
// that means to reach that path spies on console.warn and counts the calls;
// one that reaches the real console has a wait it did not start, most likely
// one an earlier test left running, which goes on answering this test's
// fetches (#25). The test fails here instead of passing with the warning.
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
