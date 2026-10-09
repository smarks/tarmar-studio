/**
 * The shared Jest helpers (#338 item 26). Several suites fake their fetch
 * responses with these, so a helper that quietly changed shape would make
 * those suites pass or fail for the wrong reason.
 */

const { jsonResponse, resolvedJsonResponse, flush } = require('./test-helpers');

describe('jsonResponse', () => {
    test('is a successful response whose json() resolves to the body', async () => {
        const response = jsonResponse({ saved: true });

        expect(response.ok).toBe(true);
        expect(response.status).toBe(200);
        await expect(response.json()).resolves.toEqual({ saved: true });
    });

    test('a failed response reports status 400', () => {
        const response = jsonResponse({ error: 'no' }, { ok: false });

        expect(response.ok).toBe(false);
        expect(response.status).toBe(400);
    });
});

describe('resolvedJsonResponse', () => {
    test('resolves to a response carrying the given status and text', async () => {
        const response = await resolvedJsonResponse(
            { error: 'gone' }, { ok: false, status: 404, statusText: 'Not Found' }
        );

        expect(response).toMatchObject({ ok: false, status: 404, statusText: 'Not Found' });
        await expect(response.json()).resolves.toEqual({ error: 'gone' });
    });

    test('defaults to a 200 OK', async () => {
        const response = await resolvedJsonResponse([]);

        expect(response).toMatchObject({ ok: true, status: 200, statusText: 'OK' });
    });
});

describe('flush', () => {
    test('waits until already-queued callbacks have run', async () => {
        let ran = false;
        setTimeout(() => { ran = true; }, 0);

        await flush();

        expect(ran).toBe(true);
    });
});
