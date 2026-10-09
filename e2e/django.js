/**
 * Shared helper for the Playwright hooks that talk to Django.
 *
 * The setup and teardown hooks operate on the same dev database that
 * `./run dev` serves, so they need the same DEBUG=true environment. Without it
 * settings.py refuses to boot ("SECRET_KEY must be set in the environment when
 * DEBUG=False") and globalSetup kills the entire suite before a single test
 * runs.
 *
 * The two shell helpers pass their script as one argv element, never through a
 * shell (issue #664). They used to interpolate it into a double-quoted `sh`
 * word, where `"` ends the argument and `$name`, `$(...)` and a backtick are
 * all live; six callers were relying on an unenforced safe subset, and the
 * first seeded string carrying a quote, a price or a regex fragment would have
 * seeded something other than what the spec wrote. Callers may now put any
 * string in a script — `JSON.stringify(value)` is a Python literal too.
 */

const { execFileSync, execSync } = require('child_process');

// What Django needs to boot against the dev database (see above).
const djangoEnvironment = () => ({ ...process.env, DEBUG: 'true' });

/**
 * Run `manage.py <args>`, with args as a literal command line.
 *
 * This one still goes through a shell, so its argument must be written out in
 * the call (`'migrate --noinput'`) and never built from a value. Anything
 * carrying a value belongs in runDjangoShell / readDjangoShell below.
 * @param {string} args
 */
function runDjangoCommand(args) {
    execSync(`uv run python manage.py ${args}`, {
        stdio: 'inherit',
        cwd: process.cwd(),
        env: djangoEnvironment()
    });
}

/**
 * Run a Python script in the Django shell, with its output on this process's
 * stdout (several callers read the seeding output in a failed run's log).
 * @param {string} script
 */
function runDjangoShell(script) {
    execFileSync('uv', ['run', 'python', 'manage.py', 'shell', '-c', script], {
        stdio: 'inherit',
        cwd: process.cwd(),
        env: djangoEnvironment(),
    });
}

/**
 * Run a Django shell script and return what it printed, for a spec that has
 * to read a value back out of the database (issue #339, item 31).
 * @param {string} script
 * @returns {string}
 */
function readDjangoShell(script) {
    return execFileSync('uv', ['run', 'python', 'manage.py', 'shell', '-c', script], {
        cwd: process.cwd(),
        encoding: 'utf8',
        env: djangoEnvironment(),
    });
}

module.exports = { runDjangoCommand, runDjangoShell, readDjangoShell };
