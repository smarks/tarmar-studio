/**
 * Unit tests for gm-panel-drafts.js (issue #16): what the reader has open or
 * has typed in a GM side panel tab is carried through the tab's refetch.
 *
 * The refetch is driven as a turn drives it, through gm-panel-characters.js
 * on a `gm-stream-procedure` or `gm-stream-done` event, and a hand edit is
 * saved through gm-records.js, so these check the path the page runs: a
 * Correct form open with a draft keeps it, its focus and its caret; a draft
 * whose record has gone is shown, not dropped; a saved form is not.
 */

const { resolvedJsonResponse: jsonResponse } = require('./test-helpers');

const CAMPAIGN_URL = '/tarmar/gm/sessions/12/campaign/';
const CHARACTERS_URL = '/tarmar/gm/sessions/12/characters/';

/** One open thread as _panel_campaign_body.html renders it for a Game Master. */
function threadRow(id, text) {
    return `
        <li data-thread-id="${id}">
            <span class="gm-campaign-name">${text}</span>
            <details class="gm-hand-edit">
                <summary>Correct</summary>
                <form class="gm-hand-form" data-url="/tarmar/gm/sessions/12/threads/" data-change="edit"
                      data-draft-key="thread-${id}" data-draft-label="Correct thread ${id}">
                    <input type="hidden" name="thread_id" value="${id}">
                    <label>Thread <textarea name="text" rows="2">${text}</textarea></label>
                    <button type="submit">Save the thread</button>
                </form>
            </details>
            <details class="gm-hand-edit">
                <summary>Resolve</summary>
                <form class="gm-hand-form" data-url="/tarmar/gm/sessions/12/threads/" data-change="resolve"
                      data-draft-key="resolve-thread-${id}" data-draft-label="Resolve thread ${id}">
                    <input type="hidden" name="thread_id" value="${id}">
                    <label>How it ended <input type="text" name="resolution"></label>
                    <button type="submit">Close the thread</button>
                </form>
            </details>
        </li>`;
}

/** The Campaign tab's contents with these open threads, in this order. */
function campaignBody(threads) {
    return `
        <p class="gm-panel-error" id="gm-panel-campaign-error" role="alert" hidden></p>
        <ul class="gm-campaign-list">${threads.map(([id, text]) => threadRow(id, text)).join('')}</ul>
        <p class="gm-panel-status" id="gm-panel-campaign-status" role="status" hidden></p>`;
}

/** The Characters tab's contents: one sheet with its Luck control. */
const CHARACTERS_BODY = `
    <p class="gm-panel-error" id="gm-panel-characters-error" role="alert" hidden></p>
    <div id="gm-sheets">
        <form class="gm-luck-form" data-luck-url="/tarmar/gm/sessions/12/luck/" data-character-id="3"
              data-draft-key="luck-3" data-draft-label="Spend Luck for Wren">
            <span class="gm-luck-count">Luck +2: 0 of 2 used this session</span>
            <label class="sr-only" for="gm-luck-roll-3">Roll the Luck is added to</label>
            <input type="text" id="gm-luck-roll-3" class="gm-luck-roll">
            <button type="submit">Spend Luck</button>
        </form>
    </div>`;

let campaignAnswer;
let characters;
let records;
let documentListeners = [];

function setUpPage(threads) {
    const config = JSON.stringify({
        charactersPanelUrlTemplate: '/tarmar/gm/sessions/{session_id}/characters/',
        campaignPanelUrlTemplate: '/tarmar/gm/sessions/{session_id}/campaign/',
        sessionId: 12,
    });
    document.body.innerHTML = `
        <script id="gm-panel-config" type="application/json">${config}<\/script>
        <section id="gm-panel-characters">${CHARACTERS_BODY}</section>
        <section id="gm-panel-campaign">${campaignBody(threads)}</section>`;
    campaignAnswer = campaignBody(threads);
    global.fetch = jest.fn((url) => Promise.resolve({
        ok: true,
        statusText: 'OK',
        text: () => Promise.resolve(url.startsWith(CAMPAIGN_URL) ? campaignAnswer : CHARACTERS_BODY),
    }));
    jest.resetModules();
    characters = require('../gm-panel-characters.js');
    records = require('../gm-records.js');
    const addListener = document.addEventListener.bind(document);
    const spy = jest.spyOn(document, 'addEventListener').mockImplementation((type, listener, options) => {
        documentListeners.push([type, listener, options]);
        addListener(type, listener, options);
    });
    characters.init();
    spy.mockRestore();
}

afterEach(() => {
    documentListeners.forEach(([type, listener, options]) => {
        document.removeEventListener(type, listener, options);
    });
    documentListeners = [];
});

/** Let the refetch's fetch, its text and the replacement run. */
async function flush() {
    for (let i = 0; i < 4; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 0));
    }
}

/** A turn's procedure frame, as chat.js reports it, and the refetch it starts. */
async function procedureFrame() {
    document.dispatchEvent(new CustomEvent('gm-stream-procedure', { detail: {} }));
    await flush();
}

function form(key) {
    return document.querySelector(`form[data-draft-key="${key}"]`);
}

/** Open a form's <details> and type into one of its fields, as the reader does. */
function openAndType(key, fieldName, text) {
    const target = form(key);
    target.closest('details').open = true;
    const field = target.querySelector(`[name="${fieldName}"]`);
    field.focus();
    field.value = text;
    return field;
}

function lostNotice() {
    return document.querySelector('#gm-panel-campaign [data-drafts-lost]');
}

describe('a Correct form open with a draft survives a refetch (issue #16)', () => {
    test('it is open again, with the same text, focus and caret', async () => {
        setUpPage([[5, 'who took the iron box'], [6, 'the debt to Vane']]);
        const field = openAndType('thread-5', 'text', 'who took the iron box, and why');
        field.setSelectionRange(4, 8);
        const before = field;

        await procedureFrame();

        const after = form('thread-5').querySelector('[name="text"]');
        expect(after).not.toBe(before);
        expect(form('thread-5').closest('details').open).toBe(true);
        expect(after.value).toBe('who took the iron box, and why');
        expect(document.activeElement).toBe(after);
        expect([after.selectionStart, after.selectionEnd]).toEqual([4, 8]);
        // The thread beside it, which the reader did not touch, is as rendered.
        expect(form('thread-6').closest('details').open).toBe(false);
        expect(lostNotice()).toBeNull();
    });

    test('it is found by its record, not by where it stood in the list', async () => {
        setUpPage([[5, 'who took the iron box'], [6, 'the debt to Vane']]);
        openAndType('thread-6', 'text', 'the debt to Vane, now doubled');
        // The turn opens a thread that sorts above both.
        campaignAnswer = campaignBody([[7, 'the bell at midnight'], [5, 'who took the iron box'], [6, 'the debt to Vane']]);

        await procedureFrame();

        expect(form('thread-6').querySelector('[name="text"]').value).toBe('the debt to Vane, now doubled');
        expect(form('thread-6').closest('details').open).toBe(true);
        expect(form('thread-7').querySelector('[name="text"]').value).toBe('the bell at midnight');
        expect(form('thread-7').closest('details').open).toBe(false);
    });

    test('the done frame carries it as well', async () => {
        setUpPage([[5, 'who took the iron box']]);
        openAndType('resolve-thread-5', 'resolution', 'Brannoc had it');

        document.dispatchEvent(new CustomEvent('gm-stream-done', { detail: {} }));
        await flush();

        expect(form('resolve-thread-5').querySelector('[name="resolution"]').value).toBe('Brannoc had it');
        expect(form('resolve-thread-5').closest('details').open).toBe(true);
    });

    test('a form only opened takes the record as the turn left it', async () => {
        setUpPage([[5, 'who took the iron box']]);
        form('thread-5').closest('details').open = true;
        campaignAnswer = campaignBody([[5, 'who took the iron box from the shrine']]);

        await procedureFrame();

        expect(form('thread-5').closest('details').open).toBe(true);
        expect(form('thread-5').querySelector('[name="text"]').value).toBe('who took the iron box from the shrine');
    });

    test('a form the reader opened from the keyboard keeps the focus on its summary', async () => {
        setUpPage([[5, 'who took the iron box']]);
        const summary = form('thread-5').closest('details').querySelector('summary');
        summary.closest('details').open = true;
        summary.focus();

        await procedureFrame();

        const fresh = form('thread-5').closest('details').querySelector('summary');
        expect(fresh).not.toBe(summary);
        expect(document.activeElement).toBe(fresh);
    });
});

describe('a draft whose record has gone is kept and shown (issue #16)', () => {
    test('the tab says so and shows what was typed', async () => {
        setUpPage([[5, 'who took the iron box'], [6, 'the debt to Vane']]);
        openAndType('thread-5', 'text', 'who took the iron box, and why');
        // The turn resolves thread 5: it leaves the open list.
        campaignAnswer = campaignBody([[6, 'the debt to Vane']]);

        await procedureFrame();

        const notice = lostNotice();
        expect(notice).not.toBeNull();
        expect(notice.getAttribute('role')).toBe('alert');
        expect(notice.textContent).toContain('no longer in this list');
        const item = notice.querySelector('[data-draft-lost="thread-5"]');
        expect(item.querySelector('strong').textContent).toBe('Correct thread 5');
        expect(item.querySelector('dt').textContent).toBe('Thread');
        expect(item.querySelector('dd').textContent).toBe('who took the iron box, and why');
    });

    test('it stays through the next refetch, unannounced, until dismissed', async () => {
        setUpPage([[5, 'who took the iron box'], [6, 'the debt to Vane']]);
        openAndType('thread-5', 'text', 'who took the iron box, and why');
        campaignAnswer = campaignBody([[6, 'the debt to Vane']]);
        await procedureFrame();

        await procedureFrame();

        const notice = lostNotice();
        expect(notice.querySelector('dd').textContent).toBe('who took the iron box, and why');
        expect(notice.hasAttribute('role')).toBe(false);

        notice.querySelector('button').click();
        expect(lostNotice()).toBeNull();
        await procedureFrame();
        expect(lostNotice()).toBeNull();
    });

    test('a form only opened, with nothing typed, is not reported', async () => {
        setUpPage([[5, 'who took the iron box']]);
        form('thread-5').closest('details').open = true;
        campaignAnswer = campaignBody([]);

        await procedureFrame();

        expect(lostNotice()).toBeNull();
    });

    test('what was typed is written as text, never as markup', async () => {
        setUpPage([[5, 'who took the iron box']]);
        openAndType('thread-5', 'text', '<img src=x onerror="window.injected=1">');
        campaignAnswer = campaignBody([]);

        await procedureFrame();

        expect(lostNotice().querySelector('dd').textContent).toBe('<img src=x onerror="window.injected=1">');
        expect(lostNotice().querySelector('img')).toBeNull();
    });
});

describe('a saved form is not carried (issue #16)', () => {
    test('a correction saved is closed and holds the record as saved', async () => {
        setUpPage([[5, 'who took the iron box']]);
        openAndType('thread-5', 'text', 'who stole the iron box');
        global.fetch.mockImplementationOnce(() => jsonResponse({ ok: true, message: 'Corrected thread 5' }));
        campaignAnswer = campaignBody([[5, 'who stole the iron box']]);

        await expect(records.submitForm(form('thread-5'))).resolves.toBe(true);
        await flush();

        expect(form('thread-5').closest('details').open).toBe(false);
        expect(form('thread-5').querySelector('[name="text"]').value).toBe('who stole the iron box');
        expect(lostNotice()).toBeNull();
    });

    test('a thread resolved by hand raises no notice for its own resolution', async () => {
        setUpPage([[5, 'who took the iron box']]);
        openAndType('resolve-thread-5', 'resolution', 'Brannoc had it');
        global.fetch.mockImplementationOnce(() => jsonResponse({ ok: true, message: 'Resolved thread 5' }));
        campaignAnswer = campaignBody([]);

        await records.submitForm(form('resolve-thread-5'));
        await flush();

        expect(lostNotice()).toBeNull();
    });

    test('a save that lands after a turn refetched the form still empties the form now shown', async () => {
        setUpPage([[5, 'who took the iron box']]);
        openAndType('thread-5', 'text', 'who stole the iron box');
        const sent = form('thread-5');
        let answer;
        global.fetch.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve; }));
        const saving = records.submitForm(sent);
        await procedureFrame();
        expect(sent.isConnected).toBe(false);
        expect(form('thread-5').querySelector('[name="text"]').value).toBe('who stole the iron box');

        campaignAnswer = campaignBody([[5, 'who stole the iron box']]);
        answer({ ok: true, status: 200, json: () => Promise.resolve({ ok: true, message: 'Corrected thread 5' }) });
        await saving;
        await flush();

        expect(form('thread-5').closest('details').open).toBe(false);
        expect(lostNotice()).toBeNull();
    });

    test('a refused save keeps the draft through the next refetch', async () => {
        setUpPage([[5, 'who took the iron box']]);
        openAndType('thread-5', 'text', 'who stole the iron box');
        global.fetch.mockImplementationOnce(() => jsonResponse(
            { ok: false, error: 'too long' }, { ok: false, statusText: 'Bad Request' },
        ));

        await expect(records.submitForm(form('thread-5'))).resolves.toBe(false);
        await procedureFrame();

        expect(form('thread-5').closest('details').open).toBe(true);
        expect(form('thread-5').querySelector('[name="text"]').value).toBe('who stole the iron box');
    });
});

describe('the Characters tab carries typing the same way (issue #16 sweep)', () => {
    test('a Luck note being typed keeps its text and focus through a refetch', async () => {
        setUpPage([]);
        const note = document.getElementById('gm-luck-roll-3');
        note.focus();
        note.value = 'the climb';

        await procedureFrame();

        const fresh = document.getElementById('gm-luck-roll-3');
        expect(fresh).not.toBe(note);
        expect(fresh.value).toBe('the climb');
        expect(document.activeElement).toBe(fresh);
    });

    test('a search being typed in the attach picker keeps its focus and caret (#864)', async () => {
        setUpPage([]);
        const picker = `
            <form class="gm-panel-picker" id="gm-character-picker" data-draft-key="attach" data-draft-label="Attach a character">
                <div data-picker data-picker-url="/tarmar/gm/characters/attachable/?session=12">
                    <input type="search" id="gm-attach-picker-search" data-picker-search value="">
                    <div data-picker-body></div>
                </div>
            </form>`;
        document.getElementById('gm-panel-characters').insertAdjacentHTML('afterbegin', picker);
        global.fetch.mockImplementation((url) => Promise.resolve({
            ok: true,
            statusText: 'OK',
            text: () => Promise.resolve(url.startsWith(CHARACTERS_URL) ? picker + CHARACTERS_BODY : campaignAnswer),
        }));
        const search = document.getElementById('gm-attach-picker-search');
        search.focus();
        search.value = 'wre';

        await procedureFrame();

        const fresh = document.getElementById('gm-attach-picker-search');
        expect(fresh).not.toBe(search);
        expect(fresh.value).toBe('wre');
        expect(document.activeElement).toBe(fresh);
        expect([fresh.selectionStart, fresh.selectionEnd]).toEqual([3, 3]);
    });

    test('a Luck note spent is not put back after the spend', async () => {
        setUpPage([]);
        document.getElementById('gm-luck-roll-3').value = 'the climb';
        global.fetch.mockImplementationOnce(() => jsonResponse({ ok: true, note: 'spent Luck' }));

        await expect(characters.spendLuck(document.querySelector('.gm-luck-form'))).resolves.toBe(true);

        expect(document.getElementById('gm-luck-roll-3').value).toBe('');
        expect(document.querySelector('#gm-panel-characters [data-drafts-lost]')).toBeNull();
    });

    test('a Luck note whose character has gone is shown in the Characters tab', async () => {
        setUpPage([]);
        document.getElementById('gm-luck-roll-3').value = 'the climb';
        global.fetch.mockImplementation((url) => Promise.resolve({
            ok: true,
            statusText: 'OK',
            text: () => Promise.resolve(url.startsWith(CHARACTERS_URL)
                ? '<p id="gm-panel-characters-error" hidden></p><div id="gm-sheets"></div>'
                : campaignAnswer),
        }));

        await procedureFrame();

        const notice = document.querySelector('#gm-panel-characters [data-drafts-lost]');
        expect(notice.querySelector('strong').textContent).toBe('Spend Luck for Wren');
        expect(notice.querySelector('dt').textContent).toBe('Roll the Luck is added to');
        expect(notice.querySelector('dd').textContent).toBe('the climb');
    });
});
