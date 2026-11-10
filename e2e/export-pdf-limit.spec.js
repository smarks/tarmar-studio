/**
 * E2E: one account may ask for only so many PDF exports a minute (issue #174).
 *
 * A PDF is rendered on the request thread by WeasyPrint, so a loop of exports
 * could hold a thread. The server now counts an account's PDF exports and
 * answers the one past CHARACTER_EXPORT_PDF_MAX_PER_MINUTE with 429.
 *
 * What only the running server proves: the real endpoint, asked from a
 * signed-in browser, serves the allowance and refuses the next. The allowance
 * and the paths are read from the server, not typed here. The limiter keys on
 * the account, so this worker's spec account cannot touch another worker's
 * allowance, and its counter is released in `finally`.
 *
 * Skipped where WeasyPrint's native libraries are not installed (the unit
 * tests skip the render for the same reason): without them every export is
 * the server failing, not a PDF, and a count of failures proves nothing about
 * the limit on renders.
 */

const { test, expect, loginAs } = require('./fixtures');
const { runDjangoShell, readDjangoJson } = require('./django');
const { specAccount, deleteSpecAccountsScript } = require('./accounts');

const exporterFor = (testInfo) => specAccount('pdfexporter', 'pdfexporter-pass-174', testInfo);

test.describe('Character export — the PDF allowance', () => {
    let exporter;

    test.beforeEach(({}, testInfo) => {
        exporter = exporterFor(testInfo);
        runDjangoShell(`
from django.contrib.auth import get_user_model
user, _created = get_user_model().objects.get_or_create(username=${JSON.stringify(exporter.username)})
user.set_password(${JSON.stringify(exporter.password)})
user.save()
`);
    });

    test.afterAll(({}, testInfo) => {
        runDjangoShell(deleteSpecAccountsScript([exporterFor(testInfo)]));
    });

    test('the allowance is served and the export past it is refused', async ({ page }) => {
        test.setTimeout(120000);
        // One shell run says whether the server can render at all, makes the
        // account a complete character to export, and reads the allowance,
        // the export's path and the limiter's key for this account. The dev
        // server is started by ./run, which gives it the library path
        // WeasyPrint needs on macOS; this shell is not, so the question is put
        // to a process started with that same environment.
        const exportSettings = readDjangoJson(`
import json
import subprocess
import sys
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from character_export.views import PDF_EXPORT_RATE_LIMIT_KEY
from common.decorators import signed_in_rate_limit_key
from common.test_helpers import create_character
from scripts.gate import build_step_environment
renders = subprocess.run(
    [sys.executable, "-c", "import weasyprint"],
    env=build_step_environment(debug=True),
    capture_output=True,
).returncode == 0
user = get_user_model().objects.get(username=${JSON.stringify(exporter.username)})
character = create_character(user, name="Export Hero")
print("export " + json.dumps({
    "renders": renders,
    "allowance": settings.CHARACTER_EXPORT_PDF_MAX_PER_MINUTE,
    "path": reverse("character_export:export_pdf", args=[character.pk]),
    "counterKey": signed_in_rate_limit_key(PDF_EXPORT_RATE_LIMIT_KEY, user.pk),
}))
`, 'export');
        test.skip(!exportSettings.renders, 'WeasyPrint\'s native libraries are not installed on this machine');

        try {
            await loginAs(page, exporter);
            for (let exportNumber = 0; exportNumber < exportSettings.allowance; exportNumber += 1) {
                const served = await page.request.get(exportSettings.path);
                expect(served.status(), `export ${exportNumber}`).toBe(200);
                expect(served.headers()['content-type']).toBe('application/pdf');
            }

            const refused = await page.request.get(exportSettings.path);
            expect(refused.status()).toBe(429);
        } finally {
            runDjangoShell(`
from django.core.cache import cache
cache.delete(${JSON.stringify(exportSettings.counterKey)})
print("Released the PDF allowance for ${exporter.username}")
`);
        }
    });
});
