/**
 * The Django shell helpers hand Python exactly the script they were given
 * (issue #664).
 *
 * They used to build `manage.py shell -c "<script>"` and hand that to a shell,
 * where a double quote ends the argument and `$name`, `$(...)` and backticks
 * are live. Six callers depended on staying inside a safe subset nothing
 * enforced, and the suite was green only because no seeded value had yet
 * contained one of those characters: the next price, regex fragment or quoted
 * name would have seeded different data than the spec wrote, and the spec
 * would have asserted against it.
 *
 * These tests need no browser — they drive the helpers directly, the way the
 * setup and teardown hooks do, so they take the plain `test` rather than the
 * fixtures' gated `page`.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const { test, expect } = require('@playwright/test');
const { runDjangoShell, readDjangoShell } = require('./django');

// Every metacharacter the old double-quoted form left live, in one string.
const HOSTILE = 'a "quoted" $HOME `id` $(whoami) \\backslash\\ and \'single\'';

test.describe('Django shell helpers', () => {
    test('readDjangoShell gets back what Python printed, unmangled', () => {
        const output = readDjangoShell(`print(${JSON.stringify(HOSTILE)})`);

        expect(output).toContain(HOSTILE);
    });

    test('runDjangoShell runs the script it was given, unmangled', () => {
        const target = path.join(os.tmpdir(), `django-shell-${process.pid}.txt`);
        try {
            runDjangoShell(
                `open(${JSON.stringify(target)}, "w").write(${JSON.stringify(HOSTILE)})`
            );

            expect(fs.readFileSync(target, 'utf8')).toBe(HOSTILE);
        } finally {
            fs.rmSync(target, { force: true });
        }
    });
});
