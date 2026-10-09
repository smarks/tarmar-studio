/**
 * Helpers shared by the Jest suites (#338 item 26).
 *
 * Several suites carried their own copies of these. They live here so a
 * change to how a fake response looks is made once. Jest only runs files
 * matching *.test.js, so this module is never collected as a suite.
 */

/**
 * A fake fetch Response carrying JSON, as the modules under test read it.
 * @param {*} data - the body `json()` resolves to
 * @param {{ok?: boolean}} [options] - a non-ok response reports status 400
 */
function jsonResponse(data, { ok = true } = {}) {
    return { ok, status: ok ? 200 : 400, json: () => Promise.resolve(data) };
}

/**
 * The same fake response already wrapped in a resolved promise, for suites
 * that hand it straight to a `fetch` mock and need to set the status text.
 * @param {*} body - the body `json()` resolves to
 * @param {{ok?: boolean, status?: number, statusText?: string}} [options]
 */
function resolvedJsonResponse(body, { ok = true, status = 200, statusText = 'OK' } = {}) {
    return Promise.resolve({ ok, status, statusText, json: () => Promise.resolve(body) });
}

/** Let every already-queued promise callback run before asserting. */
function flush() {
    return new Promise((resolve) => setTimeout(resolve, 0));
}

module.exports = { jsonResponse, resolvedJsonResponse, flush };
