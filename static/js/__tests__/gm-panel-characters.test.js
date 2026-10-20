/**
 * Unit tests for gm-panel-characters.js — the Characters tab's attach and
 * detach controls (issue #341).
 *
 * The tab refetches itself after a successful change so the sheets are
 * always server-rendered, and leaves the rest of the page alone (issue #15);
 * only an attach that made the session navigates to it. These tests assert
 * the request each control makes, what follows it, and that failures land
 * in the tab's own error element (which stays until the next action — no
 * toasts).
 */

function setUpDom({ sessionId = null, withPicker = true, live = false, withCampaign = false } = {}) {
    const config = JSON.stringify({
        attachUrl: '/tarmar/gm/characters/attach/',
        detachUrl: '/tarmar/gm/characters/detach/',
        ...(live ? { charactersPanelUrlTemplate: '/tarmar/gm/sessions/{session_id}/characters/' } : {}),
        ...(live && withCampaign
            ? { campaignPanelUrlTemplate: '/tarmar/gm/sessions/{session_id}/campaign/' }
            : {}),
        sessionId,
    });
    document.body.innerHTML = `
        <script id="gm-panel-config" type="application/json">${config}<\/script>
        <section id="gm-panel-characters">
            ${withPicker ? `
            <form id="gm-character-picker">
                <select id="gm-character-select">
                    <option value="">Pick a character…</option>
                    <option value="7">Aldwin</option>
                </select>
                <button type="submit">Attach</button>
            </form>` : ''}
            <p id="gm-panel-characters-error" role="alert" hidden></p>
            <div id="gm-sheets">
                <article class="gm-sheet" data-character-id="3">
                    <button type="button" class="gm-sheet-detach" data-character-id="3">x</button>
                </article>
            </div>
        </section>
        ${withCampaign ? '<section id="gm-panel-campaign"><p id="gm-panel-campaign-error" role="alert" hidden></p><p>where: nowhere</p></section>' : ''}
    `;
}

const { resolvedJsonResponse: jsonResponse } = require('./test-helpers');

function htmlResponse(body, ok = true) {
    return Promise.resolve({ ok, statusText: ok ? 'OK' : 'Server Error', text: () => Promise.resolve(body) });
}

let panelCharacters;

// init() adds listeners to the document that nothing removes, so each is
// recorded as it is added and removed after the test: one test's tab must not
// answer the next test's events.
let documentListeners = [];

function loadTab() {
    jest.resetModules();
    panelCharacters = require('../gm-panel-characters.js');
    const addListener = document.addEventListener.bind(document);
    const spy = jest.spyOn(document, 'addEventListener').mockImplementation((type, listener, options) => {
        documentListeners.push([type, listener, options]);
        addListener(type, listener, options);
    });
    panelCharacters.init();
    spy.mockRestore();
    return panelCharacters;
}

afterEach(() => {
    documentListeners.forEach(([type, listener, options]) => {
        document.removeEventListener(type, listener, options);
    });
    documentListeners = [];
});

function flushPromises() {
    return new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
    global.fetch.mockReset();
    window.history.replaceState(null, '', '/tarmar/gm/');
});

describe('currentSessionId', () => {
    test('prefers the session in the URL over the one the page loaded with', () => {
        setUpDom({ sessionId: 4 });
        window.history.replaceState(null, '', '?session=9');
        loadTab();
        expect(panelCharacters.currentSessionId()).toBe(9);
    });

    test('falls back to the config, then to null', () => {
        setUpDom({ sessionId: 4 });
        loadTab();
        expect(panelCharacters.currentSessionId()).toBe(4);

        setUpDom();
        loadTab();
        expect(panelCharacters.currentSessionId()).toBeNull();
    });

    test('ignores a non-numeric session parameter', () => {
        setUpDom({ sessionId: 2 });
        window.history.replaceState(null, '', '?session=abc');
        loadTab();
        expect(panelCharacters.currentSessionId()).toBe(2);
    });
});

describe('attach', () => {
    test('submitting the picker posts the pick and navigates to the session', async () => {
        setUpDom();
        global.fetch.mockReturnValue(jsonResponse({ ok: true, session_id: 12, characters: [] }));
        loadTab();

        document.getElementById('gm-character-select').value = '7';
        document.getElementById('gm-character-picker').dispatchEvent(
            new Event('submit', { bubbles: true, cancelable: true }),
        );
        await flushPromises();

        expect(global.fetch).toHaveBeenCalledTimes(1);
        const [url, options] = global.fetch.mock.calls[0];
        expect(url).toBe('/tarmar/gm/characters/attach/');
        expect(options.headers['X-CSRFToken']).toBe('test-token');
        expect(JSON.parse(options.body)).toEqual({ session_id: null, character_id: 7 });
        expect(pageNavigation.go).toHaveBeenCalledWith('/tarmar/gm/?session=12');
    });

    test('an empty pick is reported in the tab and nothing is posted', () => {
        setUpDom();
        loadTab();
        document.getElementById('gm-character-picker').dispatchEvent(
            new Event('submit', { bubbles: true, cancelable: true }),
        );
        expect(global.fetch).not.toHaveBeenCalled();
        const error = document.getElementById('gm-panel-characters-error');
        expect(error.hidden).toBe(false);
        expect(error.textContent).toBe('Pick a character first.');
    });

    test('a server error stays on screen and the page does not navigate', async () => {
        setUpDom({ sessionId: 3 });
        global.fetch.mockReturnValue(
            jsonResponse({ error: 'Character is dead' }, { ok: false, status: 400, statusText: 'Bad Request' }),
        );
        loadTab();

        expect(await panelCharacters.attachCharacter('7')).toBe(false);
        const error = document.getElementById('gm-panel-characters-error');
        expect(error.hidden).toBe(false);
        expect(error.textContent).toBe('Character is dead');
        expect(pageNavigation.go).not.toHaveBeenCalled();
    });

    test('a failed response with no error text shows the status text', async () => {
        setUpDom({ sessionId: 3 });
        global.fetch.mockReturnValue(jsonResponse({}, { ok: false, status: 500, statusText: 'Server Error' }));
        loadTab();
        await panelCharacters.attachCharacter(7);
        expect(document.getElementById('gm-panel-characters-error').textContent).toBe('Server Error');
    });

    test('a network failure is reported, not swallowed', async () => {
        setUpDom({ sessionId: 3 });
        global.fetch.mockRejectedValue(new Error('offline'));
        loadTab();
        expect(await panelCharacters.attachCharacter(7)).toBe(false);
        expect(document.getElementById('gm-panel-characters-error').textContent).toBe('Request failed: offline');
    });

    test('the next action clears an earlier error', async () => {
        setUpDom({ sessionId: 3 });
        global.fetch
            .mockReturnValueOnce(jsonResponse({ error: 'nope' }, { ok: false, status: 400 }))
            .mockReturnValueOnce(jsonResponse({ ok: true, session_id: 3, characters: [] }));
        loadTab();
        await panelCharacters.attachCharacter(7);
        expect(document.getElementById('gm-panel-characters-error').hidden).toBe(false);
        await panelCharacters.attachCharacter(8);
        const error = document.getElementById('gm-panel-characters-error');
        expect(error.hidden).toBe(true);
        expect(error.textContent).toBe('');
    });
});

describe('detach', () => {
    test('clicking a sheet\'s detach button posts, and the page is not reloaded (#15)', async () => {
        setUpDom({ sessionId: 5 });
        global.fetch.mockReturnValue(jsonResponse({ ok: true, session_id: 5, characters: [] }));
        loadTab();

        document.querySelector('.gm-sheet-detach').click();
        await flushPromises();

        const [url, options] = global.fetch.mock.calls[0];
        expect(url).toBe('/tarmar/gm/characters/detach/');
        expect(JSON.parse(options.body)).toEqual({ session_id: 5, character_id: 3 });
        expect(pageNavigation.reload).not.toHaveBeenCalled();
        expect(pageNavigation.go).not.toHaveBeenCalled();
    });

    test('a click elsewhere in the sheets does nothing', () => {
        setUpDom({ sessionId: 5 });
        loadTab();
        document.querySelector('.gm-sheet').click();
        expect(global.fetch).not.toHaveBeenCalled();
    });

    test('without a session there is nothing to detach from', async () => {
        setUpDom();
        loadTab();
        expect(await panelCharacters.detachCharacter(3)).toBe(false);
        expect(global.fetch).not.toHaveBeenCalled();
        expect(document.getElementById('gm-panel-characters-error').textContent).toBe('No active session.');
    });

    test('a refused detach is reported and the page stays', async () => {
        setUpDom({ sessionId: 5 });
        global.fetch.mockReturnValue(
            jsonResponse({ error: 'GM session not found' }, { ok: false, status: 404 }),
        );
        loadTab();
        expect(await panelCharacters.detachCharacter(3)).toBe(false);
        expect(pageNavigation.reload).not.toHaveBeenCalled();
        expect(document.getElementById('gm-panel-characters-error').textContent).toBe('GM session not found');
    });
});

describe('attach and detach leave the rest of the page as it is (issue #15)', () => {
    const PANEL_URL = '/tarmar/gm/sessions/5/characters/';
    const PICKER_URL = '/tarmar/gm/characters/attachable/';
    const ATTACH_URL = '/tarmar/gm/characters/attach/';
    const DETACH_URL = '/tarmar/gm/characters/detach/';

    /** One page of the attach picker, as the picker's endpoint renders it. */
    const pickerBody = (page) => `
        <select id="gm-character-select"><option value="7">Aldwin</option></select>
        <span class="page-current">Page ${page}</span>
        <button type="button" data-picker-page="${page + 1}">Older</button>`;
    const picker = (page) => `
        <form id="gm-character-picker" data-draft-key="attach">
            <div data-picker data-picker-url="${PICKER_URL}?session=5">
                <input type="search" data-picker-search value="">
                <div data-picker-body>${pickerBody(page)}</div>
            </div>
        </form>`;
    const SHEET = `
        <article class="gm-sheet" data-character-id="3">
            <button type="button" class="gm-sheet-detach" data-character-id="3">x</button>
        </article>`;

    /**
     * The GM page with a chat page loaded through "Show earlier" (#972), a
     * Battle tab warning about a bare-handed character (#844), and the
     * Characters tab's picker on its first page. The server answers the
     * endpoint with ``answer`` and renders the tab with ``sheets`` in it.
     */
    function setUpPage({ answer, sheets }) {
        setUpDom({ sessionId: 5, live: true, withPicker: false });
        document.body.insertAdjacentHTML('beforeend', `
            <div id="messages">
                <div class="message" id="loaded-earlier">From an earlier page</div>
                <div class="message">The latest reply</div>
            </div>
            <p id="gm-panel-unarmed" role="status">Aldwin carries no weapon.</p>`);
        document.getElementById('gm-panel-characters').insertAdjacentHTML('afterbegin', picker(1));
        window.picker = require('../picker.js');
        window.picker.initPickers(document);
        window.gmPanelBattle = require('../gm-panel-battle.js');
        global.fetch.mockImplementation((url) => {
            const asked = new URL(url, 'http://localhost/');
            if (asked.pathname === ATTACH_URL || asked.pathname === DETACH_URL) {
                return jsonResponse(answer);
            }
            if (asked.pathname === PANEL_URL) {
                return htmlResponse(`${picker(1)}
                    <p id="gm-panel-characters-error" role="alert" hidden></p>
                    <div id="gm-sheets">${sheets}</div>`);
            }
            return htmlResponse(pickerBody(Number(asked.searchParams.get('page'))));
        });
        loadTab();
    }

    afterEach(() => {
        delete window.picker;
        delete window.gmPanelBattle;
    });

    /** Turn the attach picker to its second page, as the reader does. */
    async function turnPickerToPageTwo() {
        document.querySelector('[data-picker-page="2"]').click();
        await flushPromises();
        expect(document.querySelector('.page-current').textContent).toBe('Page 2');
    }

    test('a detach refetches the tab and keeps the loaded chat pages and the picker\'s page', async () => {
        setUpPage({ answer: { ok: true, session_id: 5, characters: [], bare_handed: '' }, sheets: '' });
        await turnPickerToPageTwo();

        document.querySelector('.gm-sheet-detach').click();
        for (let tick = 0; tick < 4; tick += 1) await flushPromises();

        expect(pageNavigation.reload).not.toHaveBeenCalled();
        expect(pageNavigation.go).not.toHaveBeenCalled();
        expect(document.getElementById('loaded-earlier')).not.toBeNull();
        expect(global.fetch.mock.calls.map(([url]) => url)).toContain(PANEL_URL);
        expect(document.querySelector('.gm-sheet')).toBeNull();
        expect(document.querySelector('.page-current').textContent).toBe('Page 2');
        // The detached character was the bare-handed one: the Battle tab
        // stops warning about it, from the endpoint's answer.
        const unarmed = document.getElementById('gm-panel-unarmed');
        expect(unarmed.textContent).toBe('');
        expect(unarmed.hidden).toBe(true);
    });

    test('an attach to the open session does the same, the picker staying on its page', async () => {
        setUpPage({
            answer: {
                ok: true, session_id: 5, characters: [{ id: 7, name: 'Aldwin' }],
                bare_handed: 'Aldwin carries no weapon and would fight bare-handed.',
            },
            sheets: SHEET,
        });
        document.querySelector('.gm-sheet').remove();
        await turnPickerToPageTwo();

        expect(await panelCharacters.attachCharacter(7)).toBe(true);
        for (let tick = 0; tick < 4; tick += 1) await flushPromises();

        expect(pageNavigation.go).not.toHaveBeenCalled();
        expect(pageNavigation.reload).not.toHaveBeenCalled();
        expect(document.getElementById('loaded-earlier')).not.toBeNull();
        expect(document.querySelector('.gm-sheet[data-character-id="3"]')).not.toBeNull();
        expect(document.querySelector('.page-current').textContent).toBe('Page 2');
        expect(document.getElementById('gm-panel-unarmed').textContent)
            .toBe('Aldwin carries no weapon and would fight bare-handed.');
    });

    test('a page turn on its way when the tab is fetched again is the page shown (#14)', async () => {
        setUpPage({ answer: { ok: true, session_id: 5, characters: [] }, sheets: SHEET });
        // The reader's turn to page 2 is held; every later ask is answered.
        const answerPickers = global.fetch.getMockImplementation();
        let answerTurn;
        global.fetch.mockImplementationOnce((url) => new Promise((resolve) => {
            answerTurn = () => resolve(answerPickers(url));
        }));
        const older = document.querySelector('[data-picker-page="2"]');
        older.focus();
        older.click();

        // A turn's frame fetches the tab again before the page arrives.
        await panelCharacters.refreshPanel();
        for (let tick = 0; tick < 4; tick += 1) await flushPromises();
        expect(older.isConnected).toBe(false);
        answerTurn();
        for (let tick = 0; tick < 4; tick += 1) await flushPromises();

        // The new picker was asked for page 2 and shows it, the focus on its pager.
        const asked = global.fetch.mock.calls
            .map(([url]) => new URL(url, 'http://localhost/'))
            .filter((url) => url.pathname === PICKER_URL)
            .map((url) => url.searchParams.get('page'));
        expect(asked).toEqual(['2', '2']);
        expect(document.querySelector('.page-current').textContent).toBe('Page 2');
        expect(document.activeElement).toBe(document.querySelector('[data-picker-page="3"]'));
    });
});

describe('page without the tab markup', () => {
    test('init tolerates a page with no picker, sheets or config', () => {
        document.body.innerHTML = '';
        expect(() => loadTab()).not.toThrow();
        expect(panelCharacters.currentSessionId()).toBeNull();
    });
});

describe('live refresh (issue #571)', () => {
    const FRAGMENT = `
        <form id="gm-character-picker"><select id="gm-character-select"><option value="9">Wren</option></select></form>
        <p id="gm-panel-characters-error" role="alert" hidden></p>
        <div id="gm-sheets"><article class="gm-sheet gm-draft" id="gm-draft-ledger" data-character-id="41">Twain · needs Fatigue roll</article></div>`;

    test('a procedure frame refetches the tab and swaps in what the server rendered', async () => {
        setUpDom({ sessionId: 5, live: true });
        global.fetch.mockReturnValue(htmlResponse(FRAGMENT));
        loadTab();

        document.dispatchEvent(new CustomEvent('gm-stream-procedure', { detail: {} }));
        await flushPromises();
        await flushPromises();

        expect(global.fetch).toHaveBeenCalledWith(
            '/tarmar/gm/sessions/5/characters/', { headers: { Accept: 'text/html' } },
        );
        const section = document.getElementById('gm-panel-characters');
        expect(section.querySelector('#gm-draft-ledger').textContent).toContain('needs Fatigue roll');
    });

    test('the done frame refetches it too, for the session now in the URL', async () => {
        setUpDom({ live: true });
        window.history.replaceState(null, '', '?session=8');
        global.fetch.mockReturnValue(htmlResponse(FRAGMENT));
        loadTab();

        document.dispatchEvent(new CustomEvent('gm-stream-done', { detail: {} }));
        await flushPromises();

        expect(global.fetch.mock.calls[0][0]).toBe('/tarmar/gm/sessions/8/characters/');
    });

    test('a board change in the Battle tab refetches it (#750, #751)', async () => {
        setUpDom({ sessionId: 5, live: true });
        global.fetch.mockReturnValue(htmlResponse(FRAGMENT));
        loadTab();

        document.dispatchEvent(new CustomEvent('battle-applied', { detail: { status: 'finished' } }));
        await flushPromises();
        await flushPromises();

        expect(global.fetch).toHaveBeenCalledWith(
            '/tarmar/gm/sessions/5/characters/', { headers: { Accept: 'text/html' } },
        );
    });

    test('controls in the swapped-in contents still work', async () => {
        setUpDom({ sessionId: 5, live: true });
        global.fetch.mockReturnValueOnce(htmlResponse(FRAGMENT));
        loadTab();
        await panelCharacters.refreshPanel();

        global.fetch.mockReturnValueOnce(jsonResponse({ ok: true, session_id: 5, characters: [] }));
        document.getElementById('gm-character-select').value = '9';
        document.getElementById('gm-character-picker').dispatchEvent(
            new Event('submit', { bubbles: true, cancelable: true }),
        );
        await flushPromises();

        expect(JSON.parse(global.fetch.mock.calls[1][1].body)).toEqual({ session_id: 5, character_id: 9 });
    });

    test('a search typed in the attach picker is asked again after a refresh (#864)', async () => {
        setUpDom({ sessionId: 5, live: true });
        const pickerMarkup = (value) => `
            <form id="gm-character-picker" data-draft-key="attach">
                <div data-picker data-picker-url="/tarmar/gm/characters/attachable/?session=5">
                    <input type="search" id="gm-attach-picker-search" data-picker-search value="${value}">
                    <div data-picker-body><select id="gm-character-select"></select></div>
                </div>
            </form>`;
        document.getElementById('gm-panel-characters').insertAdjacentHTML(
            'afterbegin', pickerMarkup(''),
        );
        // Typed, as the reader types: the server rendered the box empty.
        document.querySelector('[data-picker-search]').value = 'wren';
        window.picker = require('../picker.js');
        global.fetch
            .mockReturnValueOnce(htmlResponse(pickerMarkup('')))
            .mockReturnValueOnce(htmlResponse('<select id="gm-character-select"><option value="9">Wren</option></select>'));
        loadTab();

        await panelCharacters.refreshPanel();
        await flushPromises();

        const asked = new URL(global.fetch.mock.calls[1][0]);
        expect(asked.pathname).toBe('/tarmar/gm/characters/attachable/');
        expect(asked.searchParams.get('q')).toBe('wren');
        expect(asked.searchParams.get('session')).toBe('5');
        expect(document.querySelector('[data-picker-search]').value).toBe('wren');
        expect(document.getElementById('gm-character-select').textContent).toContain('Wren');
        delete window.picker;
    });

    test('refreshes asked for while one runs collapse into one more', async () => {
        setUpDom({ sessionId: 5, live: true });
        global.fetch.mockReturnValue(htmlResponse(FRAGMENT));
        loadTab();

        const first = panelCharacters.refreshPanel();
        panelCharacters.refreshPanel();
        panelCharacters.refreshPanel();
        await first;
        await flushPromises();
        await flushPromises();

        expect(global.fetch).toHaveBeenCalledTimes(2);
    });

    test('a failed refresh keeps the tab and says so', async () => {
        setUpDom({ sessionId: 5, live: true });
        global.fetch.mockReturnValueOnce(htmlResponse('', false));
        loadTab();

        expect(await panelCharacters.refreshPanel()).toBe(false);

        expect(document.querySelector('.gm-sheet-detach')).not.toBeNull();
        const error = document.getElementById('gm-panel-characters-error');
        expect(error.textContent).toContain('could not be refreshed');
    });

    test('a network failure is reported, not swallowed', async () => {
        setUpDom({ sessionId: 5, live: true });
        global.fetch.mockReturnValueOnce(Promise.reject(new Error('offline')));
        loadTab();

        expect(await panelCharacters.refreshPanel()).toBe(false);
        expect(document.getElementById('gm-panel-characters-error').textContent).toContain('offline');
    });

    test('without a session or the URL there is nothing to refresh', async () => {
        setUpDom({ live: false, sessionId: 5 });
        loadTab();

        expect(await panelCharacters.refreshPanel()).toBe(false);
        expect(global.fetch).not.toHaveBeenCalled();
    });
});

const CHARACTERS_BODY =
    '<p id="gm-panel-characters-error" role="alert" hidden></p><div id="gm-sheets"></div>';

/** Let every queued microtask and timer callback run. */
async function flush() {
    for (let i = 0; i < 3; i += 1) {
        await Promise.resolve();
        await new Promise((resolve) => setTimeout(resolve, 0));
    }
}

describe('the Campaign tab refreshes with the Characters tab (issue #579)', () => {
    /**
     * Where the party is and what state a thing is in change mid-turn exactly
     * as a character does, so the tab that shows them refetches on the same
     * frames. The box that was on a ledge and then fused to the rock is the
     * reason any of this is a record.
     */
    test('a procedure frame refetches both tabs', async () => {
        setUpDom({ sessionId: 12, live: true, withCampaign: true });
        loadTab();
        global.fetch = jest.fn((url) =>
            Promise.resolve({
                ok: true,
                text: () =>
                    Promise.resolve(
                        url.endsWith('/campaign/')
                            ? '<p>where: the well beneath Farport</p>'
                            : '<div id="gm-sheets"></div>',
                    ),
            }),
        );

        document.dispatchEvent(new Event('gm-stream-procedure'));
        await flush();

        const asked = global.fetch.mock.calls.map(([url]) => url);
        expect(asked).toContain('/tarmar/gm/sessions/12/characters/');
        expect(asked).toContain('/tarmar/gm/sessions/12/campaign/');
        expect(document.getElementById('gm-panel-campaign').innerHTML).toContain(
            'the well beneath Farport',
        );
    });

    test('a page without the campaign tab refreshes the characters anyway', async () => {
        setUpDom({ sessionId: 12, live: true });
        loadTab();
        global.fetch = jest.fn(() =>
            Promise.resolve({ ok: true, text: () => Promise.resolve(CHARACTERS_BODY) }),
        );

        document.dispatchEvent(new Event('gm-stream-done'));
        await flush();

        const asked = global.fetch.mock.calls.map(([url]) => url);
        expect(asked).toContain('/tarmar/gm/sessions/12/characters/');
        expect(asked.filter((url) => url.endsWith('/campaign/'))).toEqual([]);
    });

    test('a failed campaign refresh leaves the tab and says which tab failed', async () => {
        setUpDom({ sessionId: 12, live: true, withCampaign: true });
        loadTab();
        global.fetch = jest.fn((url) =>
            url.endsWith('/campaign/')
                ? Promise.resolve({ ok: false, statusText: 'Bad Gateway' })
                : Promise.resolve({ ok: true, text: () => Promise.resolve(CHARACTERS_BODY) }),
        );

        document.dispatchEvent(new Event('gm-stream-done'));
        await flush();

        expect(document.getElementById('gm-panel-campaign').innerHTML).toContain(
            'where: nowhere',
        );
        const error = document.getElementById('gm-panel-campaign-error');
        expect(error.textContent).toContain('campaign');
        expect(error.hidden).toBe(false);
    });
});

describe('Luck (issue #861)', () => {
    function htmlResponse(body) {
        return Promise.resolve({ ok: true, statusText: 'OK', text: () => Promise.resolve(body) });
    }

    function addLuckForm() {
        document.getElementById('gm-sheets').insertAdjacentHTML('beforeend', `
            <form class="gm-luck-form" data-luck-url="/tarmar/gm/sessions/5/luck/" data-character-id="3">
                <input type="text" class="gm-luck-roll" value="the climb">
                <button type="submit">Spend Luck</button>
            </form>`);
        return document.querySelector('.gm-luck-form');
    }

    test('a spend posts the character and the roll, then refetches the tab', async () => {
        setUpDom({ sessionId: 5, live: true });
        loadTab();
        const form = addLuckForm();
        global.fetch
            .mockReturnValueOnce(jsonResponse({ ok: true, note: 'spent Luck' }))
            .mockReturnValueOnce(htmlResponse('<p id="gm-panel-characters-error" hidden></p><p>Luck +2: 1 of 2 used this session</p>'));

        form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
        await flushPromises();
        await flushPromises();
        await flushPromises();

        const [url, options] = global.fetch.mock.calls[0];
        expect(url).toBe('/tarmar/gm/sessions/5/luck/');
        expect(JSON.parse(options.body)).toEqual({ character_id: 3, roll: 'the climb' });
        expect(global.fetch.mock.calls[1][0]).toBe('/tarmar/gm/sessions/5/characters/');
        expect(document.getElementById('gm-panel-characters').textContent)
            .toContain('1 of 2 used this session');
    });

    test('a second press while the first is in flight spends nothing', async () => {
        setUpDom({ sessionId: 5, live: true });
        loadTab();
        const form = addLuckForm();
        let answer;
        global.fetch.mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }));

        const first = panelCharacters.spendLuck(form);
        expect(form.querySelector('button').disabled).toBe(true);
        expect(await panelCharacters.spendLuck(form)).toBe(false);
        expect(global.fetch).toHaveBeenCalledTimes(1);

        global.fetch.mockReturnValueOnce(htmlResponse('<p>refreshed</p>'));
        answer({ ok: true, status: 200, json: () => Promise.resolve({ ok: true, note: 'spent' }) });
        expect(await first).toBe(true);
        expect(global.fetch).toHaveBeenCalledTimes(2);
    });

    test('a refusal stays in the tab and nothing is refetched', async () => {
        setUpDom({ sessionId: 5, live: true });
        loadTab();
        const form = addLuckForm();
        global.fetch.mockReturnValueOnce(jsonResponse(
            { ok: false, error: 'Wren has spent all 2 of this session\'s Luck' },
            { ok: false, statusText: 'Bad Request' },
        ));

        expect(await panelCharacters.spendLuck(form)).toBe(false);

        const error = document.getElementById('gm-panel-characters-error');
        expect(error.hidden).toBe(false);
        expect(error.textContent).toContain('spent all 2');
        expect(global.fetch).toHaveBeenCalledTimes(1);
    });
});

describe('the Campaign tab keeps the resolved threads the reader loaded (issue #982)', () => {
    const RESOLVED_LIST = `
        <details class="gm-campaign-resolved" open>
            <summary>4 resolved</summary>
            <ul>
                <li data-thread-id="31">thread 31</li>
                <li data-thread-id="32">thread 32</li>
            </ul>
        </details>`;

    function respondWith(campaignBody) {
        global.fetch = jest.fn((url) =>
            Promise.resolve({
                ok: true,
                statusText: 'OK',
                text: () => Promise.resolve(url.includes('/campaign/') ? campaignBody : CHARACTERS_BODY),
            }),
        );
    }

    function campaignRequests() {
        return global.fetch.mock.calls.map(([url]) => url).filter((url) => url.includes('/campaign/'));
    }

    test('the refetch names the first thread listed, the earliest page loaded', async () => {
        setUpDom({ sessionId: 12, live: true, withCampaign: true });
        document.getElementById('gm-panel-campaign').insertAdjacentHTML('beforeend', RESOLVED_LIST);
        loadTab();
        respondWith('<p>where: the well</p>');

        document.dispatchEvent(new Event('gm-stream-done'));
        await flush();

        expect(campaignRequests()).toEqual(['/tarmar/gm/sessions/12/campaign/?resolved_from=31']);
    });

    test('a list the reader had open is open again after the refetch', async () => {
        setUpDom({ sessionId: 12, live: true, withCampaign: true });
        document.getElementById('gm-panel-campaign').insertAdjacentHTML('beforeend', RESOLVED_LIST);
        loadTab();
        respondWith(RESOLVED_LIST.replace(' open>', '>'));

        await panelCharacters.refreshPanel();

        expect(document.querySelector('.gm-campaign-resolved').open).toBe(true);
    });

    test('a list the reader had closed stays closed', async () => {
        setUpDom({ sessionId: 12, live: true, withCampaign: true });
        document.getElementById('gm-panel-campaign').insertAdjacentHTML(
            'beforeend', RESOLVED_LIST.replace(' open>', '>'),
        );
        loadTab();
        respondWith(RESOLVED_LIST.replace(' open>', '>'));

        await panelCharacters.refreshPanel();

        expect(document.querySelector('.gm-campaign-resolved').open).toBe(false);
    });

    test('a tab with no resolved threads asks for the plain tab', async () => {
        setUpDom({ sessionId: 12, live: true, withCampaign: true });
        loadTab();
        respondWith('<p>where: the well</p>');

        await panelCharacters.refreshPanel();

        expect(campaignRequests()).toEqual(['/tarmar/gm/sessions/12/campaign/']);
    });

    test('the Characters tab is asked for without a cursor', async () => {
        setUpDom({ sessionId: 12, live: true, withCampaign: true });
        document.getElementById('gm-panel-campaign').insertAdjacentHTML('beforeend', RESOLVED_LIST);
        loadTab();
        respondWith('<p>where: the well</p>');

        await panelCharacters.refreshPanel();

        expect(global.fetch.mock.calls.map(([url]) => url)).toContain('/tarmar/gm/sessions/12/characters/');
    });

    describe('while the answer is on its way', () => {
        const AGAIN = RESOLVED_LIST.replace('<li data-thread-id="31">', '<li data-thread-id="29">thread 29</li><li data-thread-id="31">');

        /** A campaign request that answers when ``answer`` is called. */
        function holdCampaignAnswer() {
            let answer;
            const held = new Promise((resolve) => { answer = resolve; });
            let asked = 0;
            global.fetch = jest.fn((url) =>
                url.includes('/campaign/') && (asked += 1) === 1
                    ? held.then((body) => ({ ok: true, statusText: 'OK', text: () => Promise.resolve(body) }))
                    : Promise.resolve({
                        ok: true,
                        statusText: 'OK',
                        text: () => Promise.resolve(url.includes('/campaign/') ? asked > 1 && AGAIN : CHARACTERS_BODY),
                    }),
            );
            return answer;
        }

        function setUpResolvedTab(listMarkup) {
            setUpDom({ sessionId: 12, live: true, withCampaign: true });
            document.getElementById('gm-panel-campaign').insertAdjacentHTML('beforeend', listMarkup);
            loadTab();
        }

        test('a list the reader closes meanwhile stays closed', async () => {
            setUpResolvedTab(RESOLVED_LIST);
            const answer = holdCampaignAnswer();

            const refreshing = panelCharacters.refreshPanel();
            document.querySelector('.gm-campaign-resolved').open = false;
            answer(RESOLVED_LIST);
            await refreshing;

            expect(document.querySelector('.gm-campaign-resolved').open).toBe(false);
        });

        test('a list the reader opens meanwhile stays open', async () => {
            setUpResolvedTab(RESOLVED_LIST.replace(' open>', '>'));
            const answer = holdCampaignAnswer();

            const refreshing = panelCharacters.refreshPanel();
            document.querySelector('.gm-campaign-resolved').open = true;
            answer(RESOLVED_LIST.replace(' open>', '>'));
            await refreshing;

            expect(document.querySelector('.gm-campaign-resolved').open).toBe(true);
        });

        test('an answer to a cursor that has moved is not applied; the tab is asked again', async () => {
            setUpResolvedTab(RESOLVED_LIST);
            const answer = holdCampaignAnswer();

            const refreshing = panelCharacters.refreshPanel();
            // "Show earlier threads" puts a page above while the request is out.
            document.querySelector('.gm-campaign-resolved li[data-thread-id="31"]')
                .insertAdjacentHTML('beforebegin', '<li data-thread-id="29">thread 29</li>');
            answer(RESOLVED_LIST);
            // The second request is the one asked for the moved cursor.
            await flush();
            await flush();
            await refreshing;
            await flush();

            const campaign = campaignRequests();
            expect(campaign[0]).toBe('/tarmar/gm/sessions/12/campaign/?resolved_from=31');
            expect(campaign).toContain('/tarmar/gm/sessions/12/campaign/?resolved_from=29');
            expect(document.querySelector('li[data-thread-id="29"]')).not.toBeNull();
        });
    });
});
