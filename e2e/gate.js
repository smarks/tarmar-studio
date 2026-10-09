/**
 * The passphrase gate, as the browser tests and dev scripts meet it.
 *
 * The dev passphrase itself is declared once in Python (common/gate.py
 * DEV_GATE_PASSPHRASE); this is its single JavaScript counterpart, so a change
 * there has one place to follow here rather than one per spec (issue #337).
 */

/** The dev server's gate passphrase; mirrors common/gate.py. */
const GATE_PASSPHRASE = 'mellon';

/**
 * Answer the gate on whatever page and origin the page is currently showing.
 *
 * Does nothing when no gate form is present. Waits for the gate form itself to
 * go rather than for a URL change: the gate renders at whatever path was
 * asked for, so the path never says whether the visitor is still behind it.
 * @param {import('@playwright/test').Page} page
 */
async function passGateOnCurrentPage(page) {
    const gateForm = page.locator('form input[name="passphrase"]');
    if (await gateForm.count() > 0) {
        await gateForm.fill(GATE_PASSPHRASE);
        await page.click('button[type="submit"]');
        await gateForm.waitFor({ state: 'detached' });
    }
}

module.exports = { GATE_PASSPHRASE, passGateOnCurrentPage };
