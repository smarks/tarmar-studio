// @ts-check
const { defineConfig, devices } = require('@playwright/test');
const { E2E_MAIL_DIRECTORY, FILE_EMAIL_BACKEND } = require('./e2e/mail');

// E2E_PORT lets two worktrees run their suites at once, each against its own
// dev server and its own db.sqlite3. With one fixed port the second run's
// reuseExistingServer quietly tested the first worktree's code.
const port = process.env.E2E_PORT || '8000';
const origin = `http://127.0.0.1:${port}`;
// Never 11434: the suite must not reach a real Ollama (issue #515).
const fakeOllamaPort = String(Number(port) + 1000);
// With E2E_PORT set, never reuse a server this run did not start (issue
// #966): a port another worktree's gate or suite holds would otherwise be
// tested in its place. Playwright then refuses to start on a taken port. A
// run on the default port may still reuse the developer's own dev server.
const reuseExistingServer = !process.env.CI && !process.env.E2E_PORT;

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
      reuseExistingServer,
      timeout: 10000,
    },
    {
      command: `./run dev 127.0.0.1:${port}`,
      url: origin,
      reuseExistingServer,
      timeout: 30000,
      // Mail goes to files a spec can read (e2e/mail.js, issue #929), not to
      // the console the run cannot see. E2E_DICE_SEEDING lets a spec pin one
      // request's dice (e2e/dice.js, issue #959); settings.py refuses it
      // unless DEBUG is on, which `./run dev` sets.
      env: {
        OLLAMA_BASE_URL: `http://127.0.0.1:${fakeOllamaPort}`,
        EMAIL_BACKEND: FILE_EMAIL_BACKEND,
        EMAIL_FILE_PATH: E2E_MAIL_DIRECTORY,
        E2E_DICE_SEEDING: 'true',
      },
    },
  ],
});
