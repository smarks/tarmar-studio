/** @type {import('jest').Config} */
module.exports = {
  testEnvironment: 'jsdom',
  roots: ['<rootDir>/static/js'],
  testMatch: ['**/__tests__/**/*.test.js'],
  setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],
  moduleFileExtensions: ['js'],
  collectCoverageFrom: [
    'static/js/**/*.js',
    '!static/js/**/__tests__/**',
    // Vendored, unmodified third-party bundles — not ours to hold to a unit
    // coverage bar. Their thousands of minified branches would swamp the
    // global threshold below even though every first-party file has a real
    // test suite.
    '!static/js/*.min.js'
  ],
  // Real unit-test coverage gate, enforced when coverage is collected
  // (CI runs `npm test -- --coverage` — see .github/workflows/ci.yml).
  // Scoped to first-party code only (see the *.min.js exclusion above); all
  // six app files (api.js, autosave.js, character-management.js, chat.js,
  // notify.js, point-buy.js) have dedicated test suites, so this floor is
  // set a bit higher than a from-scratch gate would be — but it's still
  // derived without running Jest locally (it hangs on some dev machines),
  // so it stays conservative. CI's first run reports the real numbers;
  // ratchet this up to match once it does.
  //
  // Not to be confused with the top-level "jsCoverageThreshold" key in
  // package.json, which is a separate ratchet over Playwright/nyc *e2e*
  // coverage (see scripts/update_js_coverage_threshold.py) — that one is
  // still live and untouched by this gate.
  coverageThreshold: {
    global: {
      statements: 65,
      branches: 50,
      functions: 60,
      lines: 65,
    },
  },
  verbose: true
};
