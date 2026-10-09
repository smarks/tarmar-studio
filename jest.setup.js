require('@testing-library/jest-dom');

// Mock window.confirm
global.confirm = jest.fn(() => true);

// Mock window.alert
global.alert = jest.fn();

// Mock location.reload
delete window.location;
window.location = { reload: jest.fn() };

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
  let meta = document.querySelector('meta[name="csrf-token"]');
  if (!meta) {
    meta = document.createElement('meta');
    meta.setAttribute('name', 'csrf-token');
    document.head.appendChild(meta);
  }
  meta.setAttribute('content', 'test-token');
});
