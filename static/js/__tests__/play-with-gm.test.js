/**
 * Unit tests for play-with-gm.js (issues #827, #828): the sheet's and the home
 * page's way to take a character to the Game Master.
 */

const { resolvedJsonResponse, flush } = require('./test-helpers');

function renderForm({ sessions = null } = {}) {
    const choice = sessions === null ? '' : `
        <label for="play-with-gm-session-7">Take Wren to</label>
        <select id="play-with-gm-session-7" name="session_id">
            <option value="">A new session</option>
            ${sessions.map((pk) => `<option value="${pk}">Session ${pk}</option>`).join('')}
        </select>`;
    document.body.innerHTML = `
        <form class="play-with-gm" data-character-id="7"
              data-attach-url="/tarmar/gm/characters/attach/"
              data-chat-url="/tarmar/gm/">
            ${choice}
            <button type="submit" class="button">Play with the Game Master</button>
            <p class="play-with-gm-error" role="alert" hidden></p>
        </form>`;
    return document.querySelector('form.play-with-gm');
}

const errorLine = () => document.querySelector('.play-with-gm-error');
const button = () => document.querySelector('button[type="submit"]');

describe('play-with-gm.js', () => {
    let takeToTable;
    let sessionUrl;
    let NETWORK_ERROR;

    beforeEach(() => {
        jest.resetModules();
        document.body.innerHTML = '';
        global.fetch = jest.fn();
        require('../api.js');
        ({ takeToTable, sessionUrl, NETWORK_ERROR } = require('../play-with-gm.js'));
    });

    test('a new session is asked for with no session id, then opened', async () => {
        const form = renderForm();
        global.fetch.mockReturnValue(resolvedJsonResponse({ ok: true, session_id: 42, characters: [] }));

        const reached = await takeToTable(form);

        expect(reached).toBe(true);
        const [url, options] = global.fetch.mock.calls[0];
        expect(url).toBe('/tarmar/gm/characters/attach/');
        expect(JSON.parse(options.body)).toEqual({ session_id: null, character_id: 7 });
        expect(pageNavigation.go).toHaveBeenCalledWith('/tarmar/gm/?session=42');
    });

    test('a chosen session is sent by its id', async () => {
        const form = renderForm({ sessions: [3, 5] });
        form.querySelector('select').value = '5';
        global.fetch.mockReturnValue(resolvedJsonResponse({ ok: true, session_id: 5, characters: [] }));

        await takeToTable(form);

        expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({ session_id: 5, character_id: 7 });
        expect(pageNavigation.go).toHaveBeenCalledWith('/tarmar/gm/?session=5');
    });

    test('a refusal stays on the form in the server\'s words and nothing opens', async () => {
        const form = renderForm();
        global.fetch.mockReturnValue(resolvedJsonResponse(
            { error: 'Character is dead' }, { ok: false, status: 400, statusText: 'Bad Request' }
        ));

        const reached = await takeToTable(form);

        expect(reached).toBe(false);
        expect(pageNavigation.go).not.toHaveBeenCalled();
        expect(errorLine().hidden).toBe(false);
        expect(errorLine().textContent).toBe('The Game Master could not take this character: Character is dead.');
        expect(button().disabled).toBe(false);
        // No timer takes it away.
        await new Promise((resolve) => setTimeout(resolve, 20));
        expect(errorLine().hidden).toBe(false);
    });

    test('an answer with no reason names the status', async () => {
        const form = renderForm();
        global.fetch.mockReturnValue(Promise.resolve({
            ok: false, status: 502, statusText: 'Bad Gateway',
            json: () => Promise.reject(new Error('not JSON')),
        }));

        await takeToTable(form);

        expect(errorLine().textContent).toBe('The server did not take the request (502 Bad Gateway).');
    });

    test('a lost request says so and leaves the button live', async () => {
        const form = renderForm();
        global.fetch.mockReturnValue(Promise.reject(new TypeError('Failed to fetch')));

        const reached = await takeToTable(form);

        expect(reached).toBe(false);
        expect(errorLine().textContent).toBe(NETWORK_ERROR);
        expect(button().disabled).toBe(false);
    });

    test('the next attempt clears the last refusal first', async () => {
        const form = renderForm();
        global.fetch.mockReturnValueOnce(Promise.reject(new TypeError('Failed to fetch')));
        await takeToTable(form);
        let settle;
        global.fetch.mockReturnValueOnce(new Promise((resolve) => { settle = resolve; }));

        const second = takeToTable(form);

        expect(errorLine().hidden).toBe(true);
        expect(errorLine().textContent).toBe('');
        expect(button().disabled).toBe(true);
        settle({ ok: true, status: 200, statusText: 'OK', json: () => Promise.resolve({ ok: true, session_id: 9 }) });
        await second;
    });

    test('submitting the form goes through the page-wide handler', async () => {
        renderForm();
        global.fetch.mockReturnValue(resolvedJsonResponse({ error: 'Character not found' }, { ok: false, status: 404 }));
        const form = document.querySelector('form.play-with-gm');

        const submitted = new Event('submit', { bubbles: true, cancelable: true });
        form.dispatchEvent(submitted);
        await flush();
        await flush();

        expect(submitted.defaultPrevented).toBe(true);
        expect(global.fetch).toHaveBeenCalledTimes(1);
        expect(errorLine().textContent).toContain('Character not found');
    });

    test('a submit from any other form is left alone', () => {
        document.body.innerHTML = '<form id="other"><button type="submit">Go</button></form>';
        const submitted = new Event('submit', { bubbles: true, cancelable: true });

        document.getElementById('other').dispatchEvent(submitted);

        expect(submitted.defaultPrevented).toBe(false);
        expect(global.fetch).not.toHaveBeenCalled();
    });

    test('the forms the page rendered hidden are revealed once the script runs', () => {
        document.body.innerHTML = `
            <div id="play-with-gm-7"><form class="play-with-gm" hidden></form></div>
            <form class="other" hidden></form>`;
        jest.resetModules();
        require('../play-with-gm.js');

        expect(document.querySelector('form.play-with-gm').hidden).toBe(false);
        expect(document.querySelector('form.other').hidden).toBe(true);
    });

    test('a repainted slot is revealed through the published function', () => {
        const slot = document.createElement('div');
        slot.innerHTML = '<form class="play-with-gm" hidden></form>';
        document.body.appendChild(slot);

        window.revealPlayWithGameMaster(slot);

        expect(slot.querySelector('form').hidden).toBe(false);
    });

    test('a revealed slot\'s session picker is wired by picker.js (#973)', () => {
        require('../picker.js');
        const slot = document.createElement('div');
        slot.innerHTML = `
            <form class="play-with-gm" hidden>
                <div class="picker" data-picker data-picker-url="/tarmar/gm/sessions/play-picker/?character=7">
                    <input type="search" data-picker-search>
                    <div data-picker-body></div>
                </div>
            </form>`;
        document.body.appendChild(slot);
        const picker = slot.querySelector('[data-picker]');
        expect(picker.dataset.pickerReady).toBeUndefined();

        window.revealPlayWithGameMaster(slot);

        expect(picker.dataset.pickerReady).toBe('yes');
    });

    test('a page without picker.js still reveals its forms', () => {
        delete window.picker;
        const slot = document.createElement('div');
        slot.innerHTML = '<form class="play-with-gm" hidden><div data-picker></div></form>';
        document.body.appendChild(slot);

        window.revealPlayWithGameMaster(slot);

        expect(slot.querySelector('form').hidden).toBe(false);
        expect(slot.querySelector('[data-picker]').dataset.pickerReady).toBeUndefined();
    });

    test('sessionUrl escapes the id', () => {
        expect(sessionUrl('/tarmar/gm/', 'a b')).toBe('/tarmar/gm/?session=a%20b');
    });
});
