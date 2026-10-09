/**
 * Unit tests for api.js — the page's CSRF token, the two POSTs that carry it,
 * and the notify/confirm pair (issues #111 and #338).
 *
 * These three functions were previously hand-copied across
 * character-management.js, chat.js, autosave.js and two templates, so the
 * request they build is now the single shape every POST on the site sends:
 * these tests pin it down header-for-header.
 */

const {
    csrfToken, postWithCsrf, postJson, postForSuccess, bindCommitKeys, formatSigned,
    uiNotify, uiConfirm, uiPrompt,
    showFieldError, clearFieldError, FIELD_NETWORK_ERROR
} = require('../api.js');

describe('csrfToken', () => {
    // jest.setup.js installs the <meta name="csrf-token"> tag every page
    // that extends base.html carries, so each test here starts from a real
    // page's arrangement and then takes pieces away.
    afterEach(() => {
        document.body.innerHTML = '';
    });

    test('reads the token base.html puts in the head', () => {
        expect(csrfToken()).toBe('test-token');
    });

    test('falls back to a rendered form field when there is no meta tag', () => {
        document.querySelector('meta[name="csrf-token"]').remove();
        document.body.innerHTML =
            '<input type="hidden" name="csrfmiddlewaretoken" value="form-token">';

        expect(csrfToken()).toBe('form-token');
    });

    test('prefers the meta tag over a form field when both are present', () => {
        document.body.innerHTML =
            '<input type="hidden" name="csrfmiddlewaretoken" value="form-token">';

        expect(csrfToken()).toBe('test-token');
    });

    test('answers with the empty string when the page carries neither', () => {
        document.querySelector('meta[name="csrf-token"]').remove();

        expect(csrfToken()).toBe('');
    });
});

describe('postJson', () => {
    beforeEach(() => {
        fetch.mockClear();
    });

    test('posts the body with the page token and returns the parsed answer', async () => {
        fetch.mockResolvedValue({
            ok: true,
            status: 200,
            statusText: 'OK',
            json: () => Promise.resolve({ success: true, level: 3 })
        });

        const result = await postJson('/update/', { field: 'name' });

        expect(fetch).toHaveBeenCalledWith('/update/', {
            method: 'POST',
            headers: {
                'X-CSRFToken': 'test-token',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ field: 'name' })
        });
        expect(result).toEqual({
            ok: true,
            status: 200,
            statusText: 'OK',
            data: { success: true, level: 3 },
            parseError: null
        });
    });

    test('reports a refusal along with whatever the server explained', async () => {
        fetch.mockResolvedValue({
            ok: false,
            status: 400,
            statusText: 'Bad Request',
            json: () => Promise.resolve({ error: 'Cause of death is required' })
        });

        const { ok, status, data } = await postJson('/mark-dead/', {});

        expect(ok).toBe(false);
        expect(status).toBe(400);
        expect(data.error).toBe('Cause of death is required');
    });

    test('gives an empty object when the body will not parse as JSON', async () => {
        // A 500 that returns an HTML error page, or a bodyless 204. Callers
        // read data.error without guarding, so this must not throw.
        fetch.mockResolvedValue({
            ok: false,
            status: 500,
            statusText: 'Server Error',
            json: () => Promise.reject(new Error('Unexpected token <'))
        });

        const { ok, data, parseError } = await postJson('/act/', {});

        expect(ok).toBe(false);
        expect(data).toEqual({});
        expect(parseError.message).toBe('Unexpected token <');
    });

    test('says the body was unreadable even when the status was a success', async () => {
        fetch.mockResolvedValue({
            ok: true,
            status: 200,
            statusText: 'OK',
            json: () => Promise.reject(new Error('Unexpected end of JSON input'))
        });

        const { ok, data, parseError } = await postJson('/act/', {});

        expect(ok).toBe(true);
        expect(data).toEqual({});
        expect(parseError.message).toBe('Unexpected end of JSON input');
    });

    test('propagates a network failure, as fetch does', async () => {
        fetch.mockRejectedValue(new Error('Network error'));

        await expect(postJson('/act/', {})).rejects.toThrow('Network error');
    });
});

describe('postForSuccess', () => {
    beforeEach(() => {
        fetch.mockClear();
    });

    test('posts with the page token and trusts the server\'s success flag', async () => {
        fetch.mockResolvedValue({
            ok: true,
            json: () => Promise.resolve({ success: true, name: 'Kestra' })
        });

        const result = await postForSuccess('/rename/', { name: 'Kestra' });

        expect(fetch).toHaveBeenCalledWith('/rename/', {
            method: 'POST',
            headers: {
                'X-CSRFToken': 'test-token',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ name: 'Kestra' })
        });
        expect(result).toEqual({ succeeded: true, data: { success: true, name: 'Kestra' } });
    });

    test('a false flag is a refusal even on a 200', async () => {
        fetch.mockResolvedValue({
            ok: true,
            json: () => Promise.resolve({ success: false, error: 'Taken' })
        });

        const { succeeded, data } = await postForSuccess('/rename/', {});

        expect(succeeded).toBe(false);
        expect(data.error).toBe('Taken');
    });

    test('without a flag the status decides', async () => {
        fetch.mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({}) });
        fetch.mockResolvedValueOnce({ ok: false, json: () => Promise.resolve({}) });

        expect((await postForSuccess('/save/', {})).succeeded).toBe(true);
        expect((await postForSuccess('/save/', {})).succeeded).toBe(false);
    });

    test('an unreadable reply rejects, as a network failure does', async () => {
        fetch.mockResolvedValueOnce({
            ok: false,
            json: () => Promise.reject(new Error('Unexpected token <'))
        });
        await expect(postForSuccess('/save/', {})).rejects.toThrow('Unexpected token <');

        fetch.mockRejectedValueOnce(new Error('offline'));
        await expect(postForSuccess('/save/', {})).rejects.toThrow('offline');
    });
});

describe('formatSigned', () => {
    test('signs a modifier the way the sheet renders it', () => {
        expect(formatSigned(3)).toBe('+3');
        expect(formatSigned(0)).toBe('+0');
        expect(formatSigned(-2)).toBe('-2');
    });

    test('a missing value reads as zero', () => {
        expect(formatSigned(undefined)).toBe('+0');
        expect(formatSigned(null)).toBe('+0');
    });
});

describe('bindCommitKeys', () => {
    function press(element, key) {
        const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
        element.dispatchEvent(event);
        return event;
    }

    test('Enter leaves an input, so its blur handler saves', () => {
        document.body.innerHTML = '<input type="text">';
        const input = document.querySelector('input');
        const blur = jest.spyOn(input, 'blur');
        bindCommitKeys(input);

        const event = press(input, 'Enter');

        expect(blur).toHaveBeenCalledTimes(1);
        expect(event.defaultPrevented).toBe(true);
    });

    test('Enter in a textarea is a new line, not a commit', () => {
        document.body.innerHTML = '<textarea></textarea>';
        const textarea = document.querySelector('textarea');
        const blur = jest.spyOn(textarea, 'blur');
        bindCommitKeys(textarea);

        const event = press(textarea, 'Enter');

        expect(blur).not.toHaveBeenCalled();
        expect(event.defaultPrevented).toBe(false);
    });

    test('Escape cancels first and then leaves the field', () => {
        document.body.innerHTML = '<input type="text">';
        const input = document.querySelector('input');
        const order = [];
        jest.spyOn(input, 'blur').mockImplementation(() => order.push('blur'));
        bindCommitKeys(input, { onCancel: () => order.push('cancel') });

        press(input, 'Escape');

        expect(order).toEqual(['cancel', 'blur']);
    });

    test('Escape does nothing where the caller cannot cancel', () => {
        document.body.innerHTML = '<input type="text">';
        const input = document.querySelector('input');
        const blur = jest.spyOn(input, 'blur');
        bindCommitKeys(input);

        press(input, 'Escape');

        expect(blur).not.toHaveBeenCalled();
    });
});

describe('postWithCsrf', () => {
    beforeEach(() => {
        fetch.mockClear();
        fetch.mockResolvedValue({ ok: true });
    });

    test('sends only the CSRF header when there is no body', async () => {
        await postWithCsrf('/test-url/', 'test-token');

        expect(fetch).toHaveBeenCalledWith('/test-url/', {
            method: 'POST',
            headers: {
                'X-CSRFToken': 'test-token'
            }
        });
    });

    test('adds the JSON content type and a serialized body when given an object', async () => {
        await postWithCsrf('/test-url/', 'test-token', { id: 123 });

        expect(fetch).toHaveBeenCalledWith('/test-url/', {
            method: 'POST',
            headers: {
                'X-CSRFToken': 'test-token',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ id: 123 })
        });
    });

    test('serializes an empty object rather than treating it as "no body"', async () => {
        // Only an explicit null means "no body" — {} is a legitimate payload.
        await postWithCsrf('/test-url/', 'test-token', {});

        const [, options] = fetch.mock.calls[0];
        expect(options.body).toBe('{}');
        expect(options.headers['Content-Type']).toBe('application/json');
    });

    test('passes a FormData body through without a Content-Type', async () => {
        // The browser writes multipart/form-data plus the boundary itself;
        // setting a Content-Type here would corrupt the character import
        // upload (character_list.html).
        const formData = new FormData();
        formData.append('file', 'contents');

        await postWithCsrf('/import/', 'test-token', formData);

        expect(fetch).toHaveBeenCalledWith('/import/', {
            method: 'POST',
            headers: {
                'X-CSRFToken': 'test-token'
            },
            body: formData
        });
        const [, options] = fetch.mock.calls[0];
        expect(options.headers['Content-Type']).toBeUndefined();
        expect(options.body).toBe(formData);
    });

    test('resolves to the raw Response, not parsed JSON', async () => {
        // Callers need the Response itself: deleteCharacter reads
        // response.redirected and chat.js's streamMessage reads response.body.
        const response = { ok: true, redirected: false, body: 'stream' };
        fetch.mockResolvedValue(response);

        await expect(postWithCsrf('/test-url/', 'test-token')).resolves.toBe(response);
    });

    test('propagates a network failure to the caller', async () => {
        fetch.mockRejectedValue(new Error('Network error'));

        await expect(postWithCsrf('/test-url/', 'test-token'))
            .rejects.toThrow('Network error');
    });
});

describe('uiNotify', () => {
    beforeEach(() => {
        alert.mockClear();
        delete window.showNotice;
    });

    afterEach(() => {
        delete window.showNotice;
    });

    test('routes through notify.js when showNotice is loaded', () => {
        window.showNotice = jest.fn();

        uiNotify('Saved', 'info');

        expect(window.showNotice).toHaveBeenCalledWith('Saved', 'info');
        expect(alert).not.toHaveBeenCalled();
    });

    test('defaults the notice type to error', () => {
        window.showNotice = jest.fn();

        uiNotify('Boom');

        expect(window.showNotice).toHaveBeenCalledWith('Boom', 'error');
    });

    test('falls back to alert when notify.js is absent', () => {
        uiNotify('Boom');

        expect(alert).toHaveBeenCalledWith('Boom');
    });
});

describe('uiConfirm', () => {
    beforeEach(() => {
        confirm.mockClear();
        confirm.mockReturnValue(true);
        delete window.confirmDialog;
    });

    afterEach(() => {
        delete window.confirmDialog;
    });

    test('routes through notify.js when confirmDialog is loaded', async () => {
        window.confirmDialog = jest.fn(() => Promise.resolve(false));

        const result = await uiConfirm('Delete?', { danger: true });

        expect(window.confirmDialog).toHaveBeenCalledWith('Delete?', { danger: true });
        expect(result).toBe(false);
        expect(confirm).not.toHaveBeenCalled();
    });

    test('falls back to a promise-wrapped confirm() when notify.js is absent', async () => {
        await expect(uiConfirm('Delete?')).resolves.toBe(true);
        expect(confirm).toHaveBeenCalledWith('Delete?');
    });

    test('reports a cancelled native confirm as false', async () => {
        confirm.mockReturnValue(false);

        await expect(uiConfirm('Delete?')).resolves.toBe(false);
    });
});

describe('uiPrompt', () => {
    // The in-page single-line prompt (issue #339, item 9). Same two answers
    // the browser's prompt() gives — the text, or null — so a call site
    // reads the same after the swap.
    afterEach(() => {
        delete window.promptDialog;
    });

    test('asks through notify.js when the page has it', async () => {
        window.promptDialog = jest.fn(() => Promise.resolve('Deep Wells'));

        await expect(uiPrompt('Name:', { defaultValue: 'x' }))
            .resolves.toBe('Deep Wells');
        expect(window.promptDialog).toHaveBeenCalledWith('Name:', { defaultValue: 'x' });
    });

    test('falls back to the native prompt where notify.js is absent', async () => {
        global.prompt = jest.fn(() => 'typed');

        await expect(uiPrompt('Name:', { defaultValue: '3d6' })).resolves.toBe('typed');
        expect(prompt).toHaveBeenCalledWith('Name:', '3d6');
    });

    test('reports a cancelled native prompt as null', async () => {
        global.prompt = jest.fn(() => null);

        await expect(uiPrompt('Name:')).resolves.toBeNull();
    });
});

describe('showFieldError / clearFieldError', () => {
    let container;

    beforeEach(() => {
        document.body.innerHTML = '<div class="field"><input></div>';
        container = document.querySelector('.field');
    });

    test('hangs one persistent, announced error inside the field', () => {
        showFieldError(container, 'Name is required');

        const error = container.querySelector('.autosave-error');
        expect(error.textContent).toBe('Name is required');
        expect(error.getAttribute('role')).toBe('alert');
    });

    test('replaces the message rather than stacking a second span', () => {
        showFieldError(container, 'First');
        showFieldError(container, 'Second');

        expect(container.querySelectorAll('.autosave-error')).toHaveLength(1);
        expect(container.querySelector('.autosave-error').textContent).toBe('Second');
    });

    test('renders a hostile server message as text, never as markup', () => {
        showFieldError(container, '<img src=x onerror=alert(1)>');

        expect(container.querySelector('img')).toBeNull();
        expect(container.querySelector('.autosave-error').textContent)
            .toBe('<img src=x onerror=alert(1)>');
    });

    test('does not auto-dismiss — it stands until it is cleared', async () => {
        jest.useFakeTimers();
        showFieldError(container, 'Still here');
        jest.advanceTimersByTime(60000);
        expect(container.querySelector('.autosave-error')).not.toBeNull();
        jest.useRealTimers();
    });

    test('clearing takes it away, and clearing nothing is harmless', () => {
        showFieldError(container, 'Gone in a moment');
        clearFieldError(container);
        expect(container.querySelector('.autosave-error')).toBeNull();

        expect(() => clearFieldError(container)).not.toThrow();
    });

    test('one sentence for a request that never reached the server', () => {
        expect(FIELD_NETWORK_ERROR).toBe('Network error — try again');
    });
});

describe('global publication', () => {
    test('publishes the helpers on the global object for classic script tags', () => {
        // Consumer files (character-management.js, chat.js, autosave.js) and
        // inline template scripts call these as bare globals — base.html loads
        // api.js in <head> so the names exist before any of them parse.
        expect(globalThis.csrfToken).toBe(csrfToken);
        expect(globalThis.postWithCsrf).toBe(postWithCsrf);
        expect(globalThis.postJson).toBe(postJson);
        expect(globalThis.postForSuccess).toBe(postForSuccess);
        expect(globalThis.bindCommitKeys).toBe(bindCommitKeys);
        expect(globalThis.formatSigned).toBe(formatSigned);
        expect(globalThis.showFieldError).toBe(showFieldError);
        expect(globalThis.clearFieldError).toBe(clearFieldError);
        expect(globalThis.uiNotify).toBe(uiNotify);
        expect(globalThis.uiConfirm).toBe(uiConfirm);
        expect(globalThis.uiPrompt).toBe(uiPrompt);
    });
});

describe('paintNameChrome (issue #792)', () => {
    test('writes the name into every marked place, the breadcrumb step and the title', () => {
        const { paintNameChrome } = require('../api.js');
        document.body.innerHTML = `
            <nav class="breadcrumb"><a href="/parties/4/">Old</a></nav>
            <h1>Edit <span data-name-chrome>Old</span></h1>
            <a>Back to <span data-name-chrome>Old</span></a>`;
        paintNameChrome('New Band', { title: 'Edit New Band', breadcrumb: '.breadcrumb a[href="/parties/4/"]' });
        expect([...document.querySelectorAll('[data-name-chrome]')].map((el) => el.textContent))
            .toEqual(['New Band', 'New Band']);
        expect(document.querySelector('.breadcrumb a').textContent).toBe('New Band');
        expect(document.title).toBe('Edit New Band');
    });

    test('leaves the title alone when none is given, and tolerates a missing breadcrumb', () => {
        const { paintNameChrome } = require('../api.js');
        document.title = 'Kept';
        document.body.innerHTML = '<span data-name-chrome>Old</span>';
        paintNameChrome('New', { breadcrumb: '.breadcrumb a' });
        expect(document.title).toBe('Kept');
        expect(document.querySelector('[data-name-chrome]').textContent).toBe('New');
    });
});
