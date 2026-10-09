// @ts-check
const { defineConfig, devices } = require('@playwright/test');

// E2E_PORT lets two worktrees run their suites at once, each against its own
// dev server and its own db.sqlite3. With one fixed port the second run's
// reuseExistingServer quietly tested the first worktree's code.
const port = process.env.E2E_PORT || '8000';
const origin = `http://127.0.0.1:${port}`;
// Never 11434: the suite must not reach a real Ollama (issue #515).
const fakeOllamaPort = String(Number(port) + 1000);

module.exports = defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  // Locally at most 4 (issue #432). Every spec logs in from 127.0.0.1, and the
  // login limiter charges an attempt before the password is checked, so more
  // than ten logins hashing at once read as ten failures and a correct login
  // gets 429. Playwright's default (one worker per core, 14 on the Mac Studio)
  // did exactly that. The limiter is left as it is on purpose; see #432.
  workers: process.env.CI ? 1 : 4,
  reporter: 'list',
  globalSetup: './e2e/global-setup.js',
  globalTeardown: './e2e/global-teardown.js',
  use: {
    baseURL: origin,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: [
    // A stand-in for Ollama (issue #515), so a spec can play a whole GM turn
    // through the server. The dev server below is pointed at it and never at
    // a real model. Offset from the app's port so parallel worktrees differ.
    {
      command: `node e2e/fake-ollama.js ${fakeOllamaPort}`,
      url: `http://127.0.0.1:${fakeOllamaPort}/api/version`,
      reuseExistingServer: !process.env.CI,
      timeout: 10000,
    },
    {
      command: `./run dev 127.0.0.1:${port}`,
      url: origin,
      reuseExistingServer: !process.env.CI,
      timeout: 30000,
      env: { OLLAMA_BASE_URL: `http://127.0.0.1:${fakeOllamaPort}` },
    },
  ],
});
