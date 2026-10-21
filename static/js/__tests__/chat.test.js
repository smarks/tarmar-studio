/**
 * Unit tests for chat.js markdown sanitization (issue #141).
 *
 * chat.js pipes marked.parse() output straight into innerHTML. Assistant
 * message content comes from the Ollama model and is influenceable via
 * prompt injection in user-supplied text, so it must never reach innerHTML
 * unsanitized. These tests exercise the renderMarkdown() helper directly
 * against the real vendored marked/DOMPurify builds (no mocking either
 * library — a mock could hide a real sanitizer regression).
 *
 * chat.js runs top-level DOM setup (querySelectorAll('[data-markdown]'),
 * getElementById('messages'), and a trailing scrollToBottom() call) the
 * moment it is required, exactly as it does when loaded via <script> in
 * chat.html. So the DOM — and the marked/DOMPurify globals chat.js expects
 * a <script> tag to have already provided — must exist *before* requiring
 * it, same as in the browser.
 */

/**
 * The DOM chat.js expects on load, including the #chat-config json_script
 * block gm/chat.html renders (issue #63) — chat.js reads its endpoints, CSRF
 * token and active session id from there, so it must exist before require().
 * @param {object} [config] - Values merged into the config block.
 */
function setUpDom(config = {}) {
    const chatConfig = JSON.stringify({
        streamUrl: '/tarmar/gm/stream/',
        saveUrl: '/tarmar/gm/save/',
        deleteUrl: '/tarmar/gm/delete/',
            sessionId: null,
        mode: 'play',
        ...config,
    });
    document.body.innerHTML = `
        <script id="chat-config" type="application/json">${chatConfig}<\/script>
        <div id="messages"></div>
        <input id="user-input">
        <button id="send-btn"></button>
    `;
}

let renderMarkdown;

beforeAll(() => {
    setUpDom();
    // Mirrors the <script> load order in gm/templates/gm/chat.html: marked,
    // then dompurify, then chat.js.
    global.marked = require('../marked.min.js').marked;
    global.DOMPurify = require('../dompurify.min.js');
    ({ renderMarkdown } = require('../chat.js'));
});

describe('renderMarkdown', () => {
    test('strips an onerror handler from an injected <img>', () => {
        const html = renderMarkdown('<img src=x onerror=alert(1)>gotcha');
        expect(html).not.toMatch(/onerror/i);
        expect(html).not.toMatch(/alert\(1\)/);
    });

    test('drops <script> tags and their content entirely', () => {
        const html = renderMarkdown('before<script>alert(document.cookie)</script>after');
        expect(html).not.toMatch(/<script/i);
        expect(html).not.toMatch(/alert/);
        expect(html).toContain('before');
        expect(html).toContain('after');
    });

    test('strips onclick and other inline event handler attributes', () => {
        const html = renderMarkdown('<div onclick="alert(1)">click me</div>');
        expect(html).not.toMatch(/onclick/i);
        expect(html).not.toMatch(/alert\(1\)/);
    });

    test('neutralizes a javascript: URI in a link', () => {
        const html = renderMarkdown('[click me](javascript:alert(1))');
        expect(html).not.toMatch(/javascript:/i);
    });

    test('adds rel=noopener when a raw link carries target=_blank', () => {
        const html = renderMarkdown('<a href="https://example.com" target="_blank">go</a>');
        expect(html).toContain('rel="noopener noreferrer"');
    });

    test('still renders bold, italic, and inline code', () => {
        const html = renderMarkdown('**bold** and _italic_ and `code`');
        expect(html).toContain('<strong>bold</strong>');
        expect(html).toContain('<em>italic</em>');
        expect(html).toContain('<code>code</code>');
    });

    test('still renders lists', () => {
        const html = renderMarkdown('- one\n- two\n- three');
        expect(html).toContain('<ul>');
        expect(html).toContain('<li>one</li>');
        expect(html).toContain('<li>two</li>');
        expect(html).toContain('<li>three</li>');
    });

    test('still renders fenced code blocks', () => {
        const html = renderMarkdown('```\nconst x = 1;\n```');
        // The block now carries the sideways-scroll cue class (issue #63).
        expect(html).toContain('<pre class="scroll-cue">');
        expect(html).toContain('const x = 1;');
    });

    test('still renders a plain markdown link', () => {
        const html = renderMarkdown('[Tarmar Studio](https://example.com)');
        expect(html).toContain('<a href="https://example.com">Tarmar Studio</a>');
    });

    // Issue #168: the allowlist omitted <input> and align, so GFM task
    // lists and aligned tables were silently stripped down to plain text.
    test('renders a GFM task list as inert, disabled checkboxes', () => {
        const html = renderMarkdown('- [ ] todo\n- [x] done');
        expect(html).toContain('<li><input disabled="disabled" type="checkbox"> todo</li>');
        expect(html).toContain('<li><input checked="" disabled="disabled" type="checkbox"> done</li>');
    });

    test('keeps GFM table column alignment', () => {
        const html = renderMarkdown('| a | b |\n|:--|--:|\n| 1 | 2 |');
        expect(html).toContain('<th align="left">a</th>');
        expect(html).toContain('<th align="right">b</th>');
        expect(html).toContain('<td align="left">1</td>');
        expect(html).toContain('<td align="right">2</td>');
    });

    // Issue #63: a table or a code block wider than the bubble scrolls
    // sideways, and base.css draws the edge cue for anything with this class.
    test('marks a reply\'s tables and code blocks as sideways scrollers', () => {
        const container = document.createElement('div');
        container.innerHTML = renderMarkdown(
            '| a | b |\n|--|--|\n| 1 | 2 |\n\n```\nlong code\n```\n\nplain words'
        );
        expect(container.querySelector('table').classList.contains('scroll-cue')).toBe(true);
        expect(container.querySelector('pre').classList.contains('scroll-cue')).toBe(true);
        expect(container.querySelector('p').classList.contains('scroll-cue')).toBe(false);
    });

    // The task-list allowlist entry is only safe because of the
    // afterSanitizeAttributes hook that enforces checkbox+disabled on any
    // surviving <input> — these cases make sure that hook actually holds
    // the line rather than relying on the static allowlist alone.
    test('strips a raw <input type="text"> instead of letting it through as a form field', () => {
        const html = renderMarkdown('<input type="text" name="evil">');
        expect(html).not.toMatch(/<input/i);
    });

    test('strips both tag and handler from an <input type="checkbox" onclick="...">', () => {
        const html = renderMarkdown('<input type="checkbox" onclick="alert(1)">');
        expect(html).not.toMatch(/onclick/i);
        expect(html).not.toMatch(/alert\(1\)/);
        // onclick is stripped by the attribute allowlist regardless, but the
        // checkbox itself is legitimate GFM output — confirm it survives
        // disabled, rather than asserting the whole tag is gone.
        expect(html).toMatch(/<input[^>]*type="checkbox"[^>]*disabled/i);
    });

    test('forces disabled onto an <input type="checkbox"> with no disabled attribute', () => {
        const html = renderMarkdown('<input type="checkbox" checked>');
        expect(html).toMatch(/<input[^>]*disabled/i);
    });

    test('drops an <input> with no type attribute at all', () => {
        const html = renderMarkdown('<input name="evil">');
        expect(html).not.toMatch(/<input/i);
    });
});

describe('deleteSession', () => {
    let deleteSession;

    beforeEach(() => {
        jest.resetModules();
        // 42 is the active session, so deleting it is the redirect case.
        setUpDom({ sessionId: 42 });
        global.marked = require('../marked.min.js').marked;
        global.DOMPurify = require('../dompurify.min.js');
        ({ deleteSession } = require('../chat.js'));

        fetch.mockClear();
        confirm.mockClear();
        confirm.mockReturnValue(true);
        alert.mockClear();
        window.history.replaceState(null, '', '/tarmar/gm/');
    });

    test('does nothing when the user cancels the confirmation', async () => {
        confirm.mockReturnValue(false);
        await deleteSession(42, 'Test Session');
        expect(fetch).not.toHaveBeenCalled();
    });

    test('posts session_id to DELETE_URL with the CSRF header when confirmed', async () => {
        fetch.mockResolvedValue({ ok: true, json: () => Promise.resolve({ ok: true }) });
        await deleteSession(42, 'Test Session');
        expect(fetch).toHaveBeenCalledWith('/tarmar/gm/delete/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': 'test-token',
            },
            body: JSON.stringify({ session_id: 42 }),
        });
    });

    test('redirects to the base chat URL when the active session is deleted', async () => {
        fetch.mockResolvedValue({ ok: true, json: () => Promise.resolve({ ok: true }) });
        await deleteSession(42, 'Test Session'); // 42 is the config's sessionId
        expect(pageNavigation.go).toHaveBeenCalledWith('/tarmar/gm/');
    });

    test('neither reloads nor redirects when a non-active session is deleted (#15)', async () => {
        fetch.mockResolvedValue({ ok: true, json: () => Promise.resolve({ ok: true }) });
        await deleteSession(99, 'Other Session'); // not the config's sessionId
        expect(pageNavigation.reload).not.toHaveBeenCalled();
        expect(pageNavigation.go).not.toHaveBeenCalled();
    });

    test('shows an error notice and does not navigate when the server rejects the delete', async () => {
        fetch.mockResolvedValue({
            ok: false,
            statusText: 'Bad Request',
            json: () => Promise.resolve({ error: 'No session_id' }),
        });
        await deleteSession(42, 'Test Session');
        expect(alert).toHaveBeenCalledWith('Delete failed: No session_id');
        expect(pageNavigation.reload).not.toHaveBeenCalled();
    });

    test('shows an error notice on network failure', async () => {
        fetch.mockRejectedValue(new Error('Network error'));
        await deleteSession(42, 'Test Session');
        expect(alert).toHaveBeenCalledWith('Delete failed: Network error');
    });
});

/**
 * Deleting another session leaves the page as it is (issue #15): a reload
 * threw away every earlier page of the chat the reader had loaded (#972).
 * The session leaves the rail and the menu, and each picker asks the server
 * again for the page it shows.
 */
describe('deleting another session', () => {
    const RAIL_URL = '/tarmar/gm/sessions/rail/';
    const MENU_URL = '/tarmar/gm/sessions/menu/';
    let chatModule;

    /** The page on session ``sessionId``, with sessions 42 and 99 listed. */
    function setUpPage({ sessionId = 42, active = '?active=42', extra = '' } = {}) {
        const chatConfig = JSON.stringify({
            streamUrl: '/tarmar/gm/stream/',
            saveUrl: '/tarmar/gm/save/',
            deleteUrl: '/tarmar/gm/delete/',
            sessionId,
            mode: 'play',
        });
        document.body.innerHTML = `
            <script id="chat-config" type="application/json">${chatConfig}<\/script>
            <div data-picker id="session-menu-picker" data-picker-url="${MENU_URL}${active}">
                <input type="search" data-picker-search>
                <div data-picker-body>
                    <select id="mobile-session-select">
                        <option value="42">The crypt</option><option value="99">The road</option>
                    </select>
                </div>
            </div>
            <div data-picker id="session-rail-picker" data-picker-url="${RAIL_URL}${active}">
                <input type="search" data-picker-search>
                <div data-picker-body>
                    <ul id="session-list">
                        <li class="session-item active" data-id="42">The crypt</li>
                        <li class="session-item" data-id="99">The road</li>
                    </ul>
                    <p data-picker-status>2 sessions</p>
                </div>
            </div>
            ${extra}
            <div id="messages">
                <div class="message user" id="loaded-earlier"><div class="message-content">From an earlier page</div></div>
                <div class="message assistant"><div class="message-content" data-markdown>The latest reply</div></div>
            </div>
            <input id="user-input">
            <button id="send-btn"></button>
        `;
        jest.resetModules();
        global.marked = require('../marked.min.js').marked;
        global.DOMPurify = require('../dompurify.min.js');
        window.picker = require('../picker.js');
        chatModule = require('../chat.js');
        confirm.mockReturnValue(true);
        fetch.mockReset();
        fetch.mockImplementation((url) => {
            const asked = new URL(url, 'http://localhost/');
            if (asked.pathname === '/tarmar/gm/delete/') {
                return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
            }
            const body = asked.pathname === RAIL_URL
                ? `<ul id="session-list"><li class="session-item" data-id="7">Page ${asked.searchParams.get('page')}</li></ul>`
                : '<select id="mobile-session-select"><option value="7">The ford</option></select>';
            return Promise.resolve({ ok: true, text: () => Promise.resolve(body) });
        });
    }

    /** What the session pickers asked their endpoints for. */
    function pickerAsks() {
        return fetch.mock.calls
            .map(([url]) => new URL(url, 'http://localhost/'))
            .filter((asked) => asked.pathname === RAIL_URL || asked.pathname === MENU_URL);
    }

    async function settle() {
        for (let tick = 0; tick < 6; tick += 1) {
            await new Promise((resolve) => setTimeout(resolve, 0));
        }
    }

    afterEach(() => { delete window.picker; });

    test('takes it off the rail and the menu, and keeps the chat pages loaded', async () => {
        setUpPage();
        // Held, so what the page shows between the delete and the refetch
        // can be read.
        fetch.mockImplementationOnce(() => Promise.resolve({
            ok: true, json: () => Promise.resolve({ ok: true }),
        }));
        const pickerAnswers = [];
        const answerPickers = fetch.getMockImplementation();
        fetch.mockImplementation((url) => new Promise((resolve) => {
            pickerAnswers.push(() => resolve(answerPickers(url)));
        }));

        await chatModule.deleteSession(99, 'The road');

        expect(pageNavigation.reload).not.toHaveBeenCalled();
        expect(pageNavigation.go).not.toHaveBeenCalled();
        expect(document.querySelector('.session-item[data-id="99"]')).toBeNull();
        expect(document.querySelector('#mobile-session-select option[value="99"]')).toBeNull();
        expect(document.querySelector('.session-item[data-id="42"]')).not.toBeNull();
        expect(document.getElementById('loaded-earlier')).not.toBeNull();
        pickerAnswers.forEach((answer) => answer());
        await settle();
        expect(document.getElementById('loaded-earlier')).not.toBeNull();
    });

    test('each picker asks again for the page it shows, keeping the open session pinned', async () => {
        setUpPage();
        document.getElementById('session-rail-picker').dataset.pickerShownPage = '3';
        document.getElementById('session-rail-picker').dataset.pickerShownQuery = '';

        await chatModule.deleteSession(99, 'The road');
        await settle();

        const rail = pickerAsks().find((asked) => asked.pathname === RAIL_URL);
        const menu = pickerAsks().find((asked) => asked.pathname === MENU_URL);
        expect(rail.searchParams.get('page')).toBe('3');
        expect(rail.searchParams.get('active')).toBe('42');
        expect(menu.searchParams.get('page')).toBe('1');
        expect(document.querySelector('#session-list').textContent).toContain('Page 3');
    });

    test('drops the line saying the open session continues the deleted one', async () => {
        setUpPage({
            extra: '<p class="session-continued" id="gm-session-continued" data-continued-from="99">Continues the campaign of The road</p>',
        });
        await chatModule.deleteSession(99, 'The road');
        expect(document.getElementById('gm-session-continued')).toBeNull();
    });

    test('keeps that line when it names another session', async () => {
        setUpPage({
            extra: '<p class="session-continued" id="gm-session-continued" data-continued-from="5">Continues the campaign of The ford</p>',
        });
        await chatModule.deleteSession(99, 'The road');
        expect(document.getElementById('gm-session-continued')).not.toBeNull();
    });

    test('asks the server again for an offer to continue the deleted session', async () => {
        // The offer is shown only on a page with no history, so the reload
        // loses nothing loaded; the server picks the session to offer.
        setUpPage({
            sessionId: null,
            active: '',
            extra: `<form id="gm-campaign-continue"><input type="hidden" name="session_id" value="99"></form>`,
        });
        await chatModule.deleteSession(99, 'The road');
        expect(pageNavigation.reload).toHaveBeenCalledTimes(1);
    });

    test('leaves an offer to continue another session alone, without a reload', async () => {
        setUpPage({
            sessionId: null,
            active: '',
            extra: `<form id="gm-campaign-continue"><input type="hidden" name="session_id" value="5"></form>`,
        });
        await chatModule.deleteSession(99, 'The road');
        await settle();
        expect(pageNavigation.reload).not.toHaveBeenCalled();
        expect(pageNavigation.go).not.toHaveBeenCalled();
        expect(document.querySelector('#gm-campaign-continue input').value).toBe('5');
        expect(document.querySelector('.session-item[data-id="99"]')).toBeNull();
    });

    test('a session the first message made stays pinned in the pickers it asks', async () => {
        setUpPage({ sessionId: null, active: '' });
        const encoder = new TextEncoder();
        const frames = [
            'data: {"session_id":12,"session_name":"The ford"}\n\n',
            'data: {"done":true,"full_response":"You wade in."}\n\n',
        ].map((frame) => encoder.encode(frame));
        const answerOthers = fetch.getMockImplementation();
        fetch.mockImplementationOnce(() => Promise.resolve({
            ok: true,
            status: 200,
            body: {
                getReader: () => ({
                    read: () => Promise.resolve(frames.length
                        ? { done: false, value: frames.shift() }
                        : { done: true, value: undefined }),
                }),
            },
        }));
        fetch.mockImplementation(answerOthers);

        await chatModule.streamMessage({ message: 'I wade into the ford' });
        await chatModule.deleteSession(99, 'The road');
        await settle();

        expect(pickerAsks()).toHaveLength(2);
        pickerAsks().forEach((asked) => expect(asked.searchParams.get('active')).toBe('12'));
    });
});

describe('streamMessage', () => {
    let streamMessage;
    let chatModule;

    /**
     * Builds a fetch Response stand-in whose body is a ReadableStream-style
     * reader yielding the given raw SSE text in one or more chunks (encoded
     * with TextEncoder, mirroring what the real fetch body reader hands
     * chat.js).
     */
    function streamResponse(chunks, opts = {}) {
        const encoder = new TextEncoder();
        const encoded = chunks.map((c) => encoder.encode(c));
        let i = 0;
        return {
            ok: opts.ok !== false,
            status: opts.status || (opts.ok === false ? 400 : 200),
            statusText: opts.statusText || 'OK',
            json: opts.json || (() => Promise.resolve({})),
            body: {
                getReader: () => ({
                    read: jest.fn(() => {
                        if (i < encoded.length) {
                            return Promise.resolve({ done: false, value: encoded[i++] });
                        }
                        return Promise.resolve({ done: true, value: undefined });
                    }),
                }),
            },
        };
    }

    /**
     * Type `text` and hand handleSubmit the event chat.html's form gives it.
     * @param {string} text - what the player typed
     * @returns {object} a stand-in submit event
     */
    function submitEvent(text) {
        document.getElementById('user-input').value = text;
        return { preventDefault() {} };
    }

    /** Let the send's promise chain run to completion. */
    async function flushStream() {
        for (let tick = 0; tick < 6; tick += 1) {
            await new Promise((resolve) => setTimeout(resolve, 0));
        }
    }

    beforeEach(() => {
        jest.resetModules();
        setUpDom();
        global.marked = require('../marked.min.js').marked;
        global.DOMPurify = require('../dompurify.min.js');
        chatModule = require('../chat.js');
        ({ streamMessage } = chatModule);

        fetch.mockClear();
        alert.mockClear();
    });

    test('every turn carries the mode the page is showing (issue #372)', async () => {
        // It only matters on the turn that creates a session — the server
        // reads an existing session's mode off its own row — but it rides on
        // every send rather than being conditionally assembled at each of the
        // half-dozen call sites that build a body.
        jest.resetModules();
        setUpDom({ mode: 'aide' });
        global.marked = require('../marked.min.js').marked;
        global.DOMPurify = require('../dompurify.min.js');
        const { streamMessage: send } = require('../chat.js');
        fetch.mockClear();
        fetch.mockResolvedValue(streamResponse([
            'data: {"done":true,"full_response":"Hi"}\n\n',
        ]));

        await send({ session_id: null, message: 'hello' });

        const body = JSON.parse(fetch.mock.calls[0][1].body);
        expect(body.mode).toBe('aide');
        expect(body.message).toBe('hello');
    });

    test('shows a thinking indicator that is removed once the first token arrives', async () => {
        fetch.mockResolvedValue(streamResponse([
            'data: {"token":"Hi"}\n\n',
            'data: {"done":true,"full_response":"Hi"}\n\n',
        ]));

        const promise = streamMessage({ message: 'hello' });
        // Indicator appears immediately, before the fetch promise resolves.
        expect(document.getElementById('thinking')).not.toBeNull();

        await promise;
        expect(document.getElementById('thinking')).toBeNull();
    });

    test('accumulates streamed tokens and renders the final full_response', async () => {
        fetch.mockResolvedValue(streamResponse([
            'data: {"token":"Hel"}\n\n',
            'data: {"token":"lo"}\n\n',
            'data: {"done":true,"full_response":"Hello, adventurer"}\n\n',
        ]));

        await streamMessage({ message: 'hi' });

        const content = document.querySelector('.message.assistant .message-content');
        expect(content).not.toBeNull();
        expect(content.innerHTML).toContain('Hello, adventurer');
    });

    test('reassembles an SSE line split across chunk boundaries', async () => {
        // The JSON frame is cut mid-token; chat.js must buffer the incomplete
        // trailing line and complete it once the rest of the chunk arrives.
        fetch.mockResolvedValue(streamResponse([
            'data: {"tok',
            'en":"Buffered"}\n\ndata: {"done":true,"full_response":"Buffered"}\n\n',
        ]));

        await streamMessage({ message: 'hi' });

        const content = document.querySelector('.message.assistant .message-content');
        expect(content.innerHTML).toContain('Buffered');
    });

    test('ignores the keep-alive comments a slow turn is padded with (issue #415)', async () => {
        fetch.mockResolvedValue(streamResponse([
            ': keep-alive\n\n',
            ': keep-alive\n\n',
            'data: {"token":"The gate"}\n\n',
            ': keep-alive\n\n',
            'data: {"done":true,"full_response":"The gate is shut."}\n\n',
        ]));

        await streamMessage({ message: 'I knock' });

        const content = document.querySelector('.message.assistant .message-content');
        expect(content.innerHTML).toContain('The gate is shut.');
        expect(content.textContent).not.toContain('keep-alive');
        expect(alert).not.toHaveBeenCalled();
    });

    test('skips a malformed JSON frame and keeps processing later frames', async () => {
        fetch.mockResolvedValue(streamResponse([
            'data: {not valid json}\n\n',
            'data: {"done":true,"full_response":"still works"}\n\n',
        ]));

        await streamMessage({ message: 'hi' });

        const content = document.querySelector('.message.assistant .message-content');
        expect(content.innerHTML).toContain('still works');
    });

    // A failed turn is a notice, not a GM bubble, and the player's own
    // message comes back to them (issue #339, item 11). window.showNotice is
    // notify.js's, absent under Jest, so api.js's uiNotify falls through to
    // alert() — which jest.setup.js mocks. That is what these read.

    test('a server-reported error mid-stream is a notice, not a GM turn', async () => {
        fetch.mockResolvedValue(streamResponse([
            'data: {"error":"model unavailable"}\n\n',
        ]));

        await streamMessage({ message: 'hi' });

        expect(document.querySelector('.message.assistant')).toBeNull();
        expect(alert.mock.calls[0][0]).toContain('model unavailable');
        expect(document.getElementById('thinking')).toBeNull();
        expect(document.getElementById('send-btn').disabled).toBe(false);
        expect(document.getElementById('user-input').disabled).toBe(false);
    });

    test('an error frame with no reason is still a failure (issue #663)', async () => {
        // An exception raised with no arguments has an empty str(), so the
        // stream used to carry {"error": ""}. `if (data.error)` read that as
        // no failure, and no done frame follows one: the thinking line
        // stayed on screen for good while the server had recorded the turn
        // as failed.
        fetch.mockResolvedValue(streamResponse([
            'data: {"error":""}\n\n',
        ]));

        await streamMessage({ message: 'hi' });

        expect(document.getElementById('thinking')).toBeNull();
        expect(document.getElementById('send-btn').disabled).toBe(false);
        expect(alert.mock.calls[0][0]).toContain('stopped without saying why');
    });

    test('a turn cut short by the budget says what the server kept (issue #553)', async () => {
        // The reply is gone, but the twelve recordings and two runs of years
        // the server had already resolved stand. They belong in the chat,
        // under the chips, not only in the failure banner.
        fetch.mockResolvedValue(streamResponse([
            'data: {"procedure":{"kind":"record_attributes","label":"strength recorded as 18",'
                + '"ran":true,"lines":["strength recorded as 18"]}}\n\n',
            'data: {"token":"The twins stand "}\n\n',
            'data: {"error":"That turn was too slow.",'
                + '"notices":["That turn was cut short before the Game Master finished '
                + 'writing. What the server had already worked out for it is recorded and '
                + 'stands: strength recorded as 18."]}\n\n',
        ]));

        await streamMessage({ message: 'twins, all 18s' });

        const notice = document.querySelector('.grounding-notice');
        expect(notice).not.toBeNull();
        expect(notice.textContent).toContain('cut short');
        expect(notice.textContent).toContain('strength recorded as 18');
        expect(notice.getAttribute('role')).toBe('status');
        // The failure itself is still reported, and the chips are still there.
        expect(alert.mock.calls[0][0]).toContain('too slow');
        expect(document.querySelector('.dice-roll').textContent)
            .toContain('strength recorded as 18');
    });

    test('a cut-short turn with nothing kept shows no notice (issue #553)', async () => {
        fetch.mockResolvedValue(streamResponse([
            'data: {"error":"That turn was too slow."}\n\n',
        ]));

        await streamMessage({ message: 'hi' });

        expect(document.querySelector('.grounding-notice')).toBeNull();
        expect(alert.mock.calls[0][0]).toContain('too slow');
    });

    test('a refused turn hands the typed message back to the player', async () => {
        fetch.mockResolvedValue(streamResponse([], {
            ok: false,
            statusText: 'Bad Request',
            json: () => Promise.resolve({ error: 'no message provided' }),
        }));

        chatModule.handleSubmit(submitEvent('a torch, and quietly'));
        await flushStream();

        expect(document.querySelector('.message.user')).toBeNull();
        expect(document.getElementById('user-input').value)
            .toBe('a torch, and quietly');
        expect(alert.mock.calls[0][0]).toContain('no message provided');
        expect(document.getElementById('send-btn').disabled).toBe(false);
    });

    test('a non-JSON error body reads as a status, not a parse error', async () => {
        fetch.mockResolvedValue(streamResponse([], {
            ok: false,
            status: 502,
            statusText: 'Bad Gateway',
            json: () => Promise.reject(new SyntaxError('Unexpected token <')),
        }));

        await streamMessage({ message: 'hi' });

        expect(alert.mock.calls[0][0]).toContain('502');
        expect(alert.mock.calls[0][0]).not.toContain('Unexpected token');
    });

    test('a broken connection is a notice and the send controls come back', async () => {
        fetch.mockRejectedValue(new Error('network down'));

        await streamMessage({ message: 'hi' });

        expect(document.querySelector('.message.assistant')).toBeNull();
        expect(alert.mock.calls[0][0]).toContain('network down');
        expect(document.getElementById('send-btn').disabled).toBe(false);
    });

    // A failed turn can be sent again from its notice (issue #410). notify.js
    // is not loaded under Jest, so window.showNotice is stood in by the same
    // element it builds, for the Retry button to go into.
    function stubNotices() {
        window.showNotice = (message) => {
            const notice = document.createElement('div');
            notice.className = 'notice';
            const text = document.createElement('span');
            text.className = 'notice-text';
            text.textContent = message;
            const dismiss = document.createElement('button');
            dismiss.className = 'notice-dismiss';
            notice.append(text, dismiss);
            document.body.appendChild(notice);
            return notice;
        };
    }

    test('a broken connection offers Retry, which sends the same turn again', async () => {
        stubNotices();
        fetch.mockRejectedValueOnce(new TypeError('Load failed'));
        fetch.mockResolvedValueOnce(streamResponse([
            'data: {"done":true,"full_response":"The gate opens."}\n\n',
        ]));

        chatModule.handleSubmit(submitEvent('I knock on the gate'));
        await flushStream();

        const notice = document.querySelector('.notice');
        expect(notice.textContent).toContain('Load failed');
        const retry = notice.querySelector('.notice-retry');
        expect(retry.textContent).toBe('Retry');

        retry.click();
        await flushStream();

        const body = JSON.parse(fetch.mock.calls[1][1].body);
        expect(body.message).toBe('I knock on the gate');
        expect(body.retry).toBe(true);
        expect(document.querySelector('.notice')).toBeNull();
        expect(document.getElementById('user-input').value).toBe('');
        expect(document.querySelector('.message.assistant .message-content').innerHTML)
            .toContain('The gate opens.');
        delete window.showNotice;
    });

    test('a retry goes to the session a procedure created before the break', async () => {
        stubNotices();
        fetch.mockResolvedValueOnce(streamResponse([
            'data: {"procedure":{"lines":["Year 1"]},"session_id":41}\n\n',
            'data: {"error":"model unavailable"}\n\n',
        ]));
        fetch.mockResolvedValueOnce(streamResponse([
            'data: {"done":true,"full_response":"The year is over."}\n\n',
        ]));

        chatModule.handleSubmit(submitEvent('Play the first year'));
        await flushStream();
        document.querySelector('.notice-retry').click();
        await flushStream();

        const body = JSON.parse(fetch.mock.calls[1][1].body);
        expect(body.session_id).toBe(41);
        expect(body.retry).toBe(true);
        delete window.showNotice;
    });

    test('a failure part-way through keeps what already streamed', async () => {
        fetch.mockResolvedValue(streamResponse([
            'data: {"token":"You push the door and "}\n\n',
            'data: {"error":"model unavailable"}\n\n',
        ]));

        chatModule.handleSubmit(submitEvent('open the door'));
        await flushStream();

        const content = document.querySelector('.message.assistant .message-content');
        expect(content.innerHTML).toContain('You push the door');
        // The player's line stays too: the reply is on screen, and deleting
        // the question it answers would be a second loss.
        expect(document.querySelector('.message.user')).not.toBeNull();
        expect(document.getElementById('user-input').value).toBe('');
        expect(alert.mock.calls[0][0]).toContain('model unavailable');
    });

    test('updates sessionId and the URL when the final frame carries one', async () => {
        window.history.replaceState(null, '', '/tarmar/gm/');
        const replaceStateSpy = jest.spyOn(window.history, 'replaceState').mockImplementation(() => {});

        fetch.mockResolvedValue(streamResponse([
            'data: {"done":true,"full_response":"ok","session_id":99}\n\n',
        ]));

        await streamMessage({ message: 'hi' });

        expect(chatModule.sessionId).toBe(99);
        expect(replaceStateSpy).toHaveBeenCalled();
        const urlArg = replaceStateSpy.mock.calls[0][2];
        expect(String(urlArg)).toContain('session=99');

        replaceStateSpy.mockRestore();
    });

    test('renders the actual dice faces from a /roll done frame (issue #175)', async () => {
        fetch.mockResolvedValue(streamResponse([
            'data: {"token":"A fine throw."}\n\n',
            'data: {"done":true,"full_response":"A fine throw.",'
                + '"roll":{"expression":"3d6","rolls":"4, 2, 6","total":12}}\n\n',
        ]));

        await streamMessage({ message: '3d6', command: 'roll' });

        const rollLine = document.querySelector('.message.assistant .dice-roll');
        expect(rollLine).not.toBeNull();
        expect(rollLine.textContent).toBe('🎲 3d6: [4, 2, 6] = 12');
        // The narration still renders after the dice line.
        const content = document.querySelector('.message.assistant .message-content');
        expect(content.innerHTML).toContain('A fine throw.');
        expect(rollLine.compareDocumentPosition(content) & Node.DOCUMENT_POSITION_FOLLOWING)
            .toBeTruthy();
    });

    test('a roll expression is rendered as text, not markup', async () => {
        // The expression echoes user input; a malicious value must not become
        // HTML in the transcript.
        fetch.mockResolvedValue(streamResponse([
            'data: {"done":true,"full_response":"ok",'
                + '"roll":{"expression":"<img src=x onerror=alert(1)>","rolls":"1","total":1}}\n\n',
        ]));

        await streamMessage({ message: 'x', command: 'roll' });

        const rollLine = document.querySelector('.dice-roll');
        expect(rollLine.querySelector('img')).toBeNull();
        expect(rollLine.textContent).toContain('<img src=x onerror=alert(1)>');
    });

    test('shows the dice the GM asked the server to roll (issue #363)', async () => {
        fetch.mockResolvedValue(streamResponse([
            'data: {"token":"The patrol leader eyes you."}\n\n',
            'data: {"gm_roll":{"expression":"2d6","purpose":"reaction","roller":"",'
                + '"rolls":"4, 3","total":7,"line":"🎲 GM rolls 2d6 for reaction: [4, 3] = 7"}}\n\n',
            'data: {"token":"They lower their spears."}\n\n',
            'data: {"done":true,"full_response":"The patrol leader eyes you.\\n\\nThey lower their spears."}\n\n',
        ]));

        await streamMessage({ message: 'the patrol spots me' });

        const rollLine = document.querySelector('.message.assistant .dice-roll');
        expect(rollLine).not.toBeNull();
        expect(rollLine.textContent).toBe('🎲 GM rolls 2d6 for reaction: [4, 3] = 7');

        // Prose, then the dice, then the prose that used them.
        const blocks = [...document.querySelectorAll('.message.assistant .message-content')];
        expect(blocks[0].innerHTML).toContain('The patrol leader eyes you.');
        expect(blocks[blocks.length - 1].innerHTML).toContain('They lower their spears.');
        expect(rollLine.compareDocumentPosition(blocks[0]) & Node.DOCUMENT_POSITION_PRECEDING)
            .toBeTruthy();

        // The narration is not also re-rendered as one block, which would
        // print the first half twice.
        const shown = document.querySelector('.message.assistant').textContent;
        expect(shown.match(/eyes you/g)).toHaveLength(1);
    });

    test('shows a persistent notice when a grounding block was dropped (issue #374)', async () => {
        // The turn survived without the ledger — which is the whole point of
        // degrading instead of 500ing — but the player has to be told the GM
        // answered less grounded than usual, or an invented number reads as
        // real. It is the server speaking, not the Game Master, so it is not a
        // GM bubble; and nothing takes it away on a timer.
        fetch.mockResolvedValue(streamResponse([
            'data: {"done":true,"full_response":"I cannot see the ledger.",'
                + '"notices":["The character ledger could not be attached to this turn."]}\n\n',
        ]));

        await streamMessage({ message: 'what is my STR?' });

        const notice = document.querySelector('.message.assistant .grounding-notice');
        expect(notice).not.toBeNull();
        expect(notice.textContent).toContain('character ledger');
        expect(notice.getAttribute('role')).toBe('status');
        // The GM's own words are still shown, in their own block.
        const content = document.querySelector('.message.assistant .message-content');
        expect(content.innerHTML).toContain('I cannot see the ledger.');
    });

    test('an ordinary turn shows no grounding notice', async () => {
        fetch.mockResolvedValue(streamResponse([
            'data: {"done":true,"full_response":"You set off north."}\n\n',
        ]));

        await streamMessage({ message: 'north' });

        expect(document.querySelector('.grounding-notice')).toBeNull();
    });

    test('a grounding notice is rendered as text, not markup', async () => {
        fetch.mockResolvedValue(streamResponse([
            'data: {"done":true,"full_response":"ok",'
                + '"notices":["<img src=x onerror=alert(1)>"]}\n\n',
        ]));

        await streamMessage({ message: 'hi' });

        const notice = document.querySelector('.grounding-notice');
        expect(notice.querySelector('img')).toBeNull();
        expect(notice.textContent).toContain('<img src=x onerror=alert(1)>');
    });

    test('a GM dice line is rendered as text, not markup', async () => {
        fetch.mockResolvedValue(streamResponse([
            'data: {"gm_roll":{"line":"<img src=x onerror=alert(1)>"}}\n\n',
            'data: {"done":true,"full_response":"ok"}\n\n',
        ]));

        await streamMessage({ message: 'hi' });

        const rollLine = document.querySelector('.dice-roll');
        expect(rollLine.querySelector('img')).toBeNull();
        expect(rollLine.textContent).toContain('<img src=x onerror=alert(1)>');
    });

    test('shows every line of a rules procedure the server ran (issue #370)', async () => {
        fetch.mockResolvedValue(streamResponse([
            'data: {"token":"The year turns."}\n\n',
            'data: {"procedure":{"kind":"add_experience_years","ran":true,"lines":['
                + '"\u2699\ufe0f campaigner year 1 survival: 3d6 [6, 6, 6] = 18, modifier +0, '
                + 'total 18 vs target 9 (survive on 9 or more), natural 18 \u2014 automatic '
                + 'survival \u2014 SURVIVED"]}}\n\n',
            'data: {"token":"You come through it."}\n\n',
            'data: {"done":true,"full_response":"The year turns.\\n\\nYou come through it."}\n\n',
        ]));

        await streamMessage({ message: 'a year as a campaigner' });

        const chip = document.querySelector('.message.assistant .dice-roll');
        expect(chip).not.toBeNull();
        expect(chip.textContent).toContain('SURVIVED');
        expect(chip.textContent).toContain('target 9');

        // Prose, then the outcome, then the prose that narrates it.
        const blocks = [...document.querySelectorAll('.message.assistant .message-content')];
        expect(blocks[0].innerHTML).toContain('The year turns.');
        expect(blocks[blocks.length - 1].innerHTML).toContain('You come through it.');
        expect(chip.compareDocumentPosition(blocks[0]) & Node.DOCUMENT_POSITION_PRECEDING)
            .toBeTruthy();
        const shown = document.querySelector('.message.assistant').textContent;
        expect(shown.match(/The year turns/g)).toHaveLength(1);
    });

    test('a procedure that did not run still shows its chip', async () => {
        // A failure must be as visible as a success, never only prose.
        fetch.mockResolvedValue(streamResponse([
            'data: {"procedure":{"ran":false,"lines":["\u2699\ufe0f 1 year on the campaigner '
                + 'track was not run: the draft is dead"],"error":"the draft is dead"}}\n\n',
            'data: {"done":true,"full_response":"ok"}\n\n',
        ]));

        await streamMessage({ message: 'another year' });

        expect(document.querySelector('.dice-roll').textContent).toContain('was not run');
    });

    test('a procedure line is rendered as text, not markup', async () => {
        fetch.mockResolvedValue(streamResponse([
            'data: {"procedure":{"lines":["<img src=x onerror=alert(1)>"]}}\n\n',
            'data: {"done":true,"full_response":"ok"}\n\n',
        ]));

        await streamMessage({ message: 'hi' });

        const chip = document.querySelector('.dice-roll');
        expect(chip.querySelector('img')).toBeNull();
        expect(chip.textContent).toContain('<img src=x onerror=alert(1)>');
    });

    test('adds no dice line when the done frame carries no roll', async () => {
        fetch.mockResolvedValue(streamResponse([
            'data: {"done":true,"full_response":"just talk"}\n\n',
        ]));

        await streamMessage({ message: 'hi' });

        expect(document.querySelector('.dice-roll')).toBeNull();
    });

    test('announces the done frame on the document for the panel tabs (issue #343)', async () => {
        fetch.mockResolvedValue(streamResponse([
            'data: {"token":"Off to Majahelm."}\n\n',
            'data: {"done":true,"full_response":"Off to Majahelm.","session_id":9,"map":{"slug":"maja","title":"Maja","image_url":"/static/images/maja-island.png","place":"Majahelm"}}\n\n',
        ]));
        const heard = [];
        document.addEventListener('gm-stream-done', (event) => heard.push(event.detail));

        await streamMessage({ message: 'go' });

        expect(heard).toHaveLength(1);
        expect(heard[0].map).toEqual({
            slug: 'maja',
            title: 'Maja',
            image_url: '/static/images/maja-island.png',
            place: 'Majahelm',
        });
        // Dispatched after the session id landed in the URL.
        expect(chatModule.sessionId).toBe(9);
    });

    test('announces each procedure frame so the Characters tab can refresh (issue #571)', async () => {
        fetch.mockResolvedValue(streamResponse([
            'data: {"procedure":{"kind":"record_attributes","label":"all six attributes","lines":["⚙️ Twain: all six attributes recorded as 18"],"ran":true},"session_id":9}\n\n',
            'data: {"done":true,"full_response":"Ready.","session_id":9}\n\n',
        ]));
        const heard = [];
        document.addEventListener('gm-stream-procedure', (event) => heard.push(event.detail));

        await streamMessage({ message: 'twins' });

        expect(heard).toHaveLength(1);
        expect(heard[0].procedure.kind).toBe('record_attributes');
    });

    test('sends the request body and CSRF header to STREAM_URL', async () => {
        fetch.mockResolvedValue(streamResponse([
            'data: {"done":true,"full_response":"ok"}\n\n',
        ]));

        await streamMessage({ session_id: null, message: 'hi' });

        expect(fetch).toHaveBeenCalledWith('/tarmar/gm/stream/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': 'test-token',
            },
            body: JSON.stringify({ session_id: null, message: 'hi', mode: 'play' }),
        });
    });
});

describe('markdown rendering on page load', () => {
    test('sanitizes pre-rendered assistant history (data-markdown elements)', () => {
        jest.resetModules();
        setUpDom();

        // Set via textContent, not innerHTML: Django's {{ msg.content }}
        // auto-escapes into the page source, so the browser parses it into a
        // single text node holding this exact literal string — jsdom must
        // see the same thing, not a pre-parsed <img> element, or the test
        // would stop exercising the code path chat.js actually runs on load.
        const container = document.createElement('div');
        container.className = 'message-content';
        container.setAttribute('data-markdown', '');
        container.textContent = '<img src=x onerror=alert(1)>Welcome, adventurer.';
        document.getElementById('messages').appendChild(container);

        global.marked = require('../marked.min.js').marked;
        global.DOMPurify = require('../dompurify.min.js');
        require('../chat.js');

        const rendered = document.querySelector('[data-markdown]').innerHTML;
        expect(rendered).not.toMatch(/onerror/i);
        expect(rendered).not.toMatch(/alert\(1\)/);
        expect(rendered).toContain('Welcome, adventurer.');
    });
});

// ---------- GM choice prompts (issue #342) ----------

describe('parseChoicePrompt', () => {
    let parseChoicePrompt;

    beforeAll(() => {
        jest.resetModules();
        setUpDom();
        global.marked = require('../marked.min.js').marked;
        global.DOMPurify = require('../dompurify.min.js');
        ({ parseChoicePrompt } = require('../chat.js'));
    });

    test('returns the options of a reply that ends in a dotted numbered list', () => {
        const reply = 'The guard blocks the gate.\n\n1. Bribe the guard\n2. Sneak past\n3. Draw steel';
        expect(parseChoicePrompt(reply)).toEqual(['Bribe the guard', 'Sneak past', 'Draw steel']);
    });

    test('accepts the "1)" marker style too', () => {
        expect(parseChoicePrompt('Choose:\n1) Left\n2) Right')).toEqual(['Left', 'Right']);
    });

    test('tolerates trailing whitespace, trailing blank lines and indented items', () => {
        const reply = 'Pick.\n  1. Left   \n  2. Right\t\n\n   \n';
        expect(parseChoicePrompt(reply)).toEqual(['Left', 'Right']);
    });

    test('keeps the option text verbatim, including inline markdown', () => {
        expect(parseChoicePrompt('1. **Bribe** the guard\n2. Run')).toEqual(['**Bribe** the guard', 'Run']);
    });

    // A final "Something else?" is the bar's own escape button, not an option
    // (issue #384).
    test('drops a final item that only offers something else', () => {
        const reply = 'The inn is warm.\n\n1. Head for the bar\n2. Find a corner\n3. Ask the innkeeper\n4. Something else?';
        expect(parseChoicePrompt(reply)).toEqual(['Head for the bar', 'Find a corner', 'Ask the innkeeper']);
    });

    test('recognises the escape item however it is worded or wrapped', () => {
        for (const escape of ['**Something else?**', 'Anything else you would like to do', 'Or something else', 'Other', 'Describe your own action']) {
            expect(parseChoicePrompt(`1. Left\n2. Right\n3. ${escape}`)).toEqual(['Left', 'Right']);
        }
    });

    test('keeps an option that merely mentions something else', () => {
        expect(parseChoicePrompt('1. Left\n2. Ask about something else entirely... later\n3. Wait'))
            .toEqual(['Left', 'Ask about something else entirely... later', 'Wait']);
        expect(parseChoicePrompt('1. Left\n2. Otherworldly gate')).toEqual(['Left', 'Otherworldly gate']);
    });

    test('one real option and an escape item is not a choice prompt', () => {
        expect(parseChoicePrompt('1. Wait\n2. Something else?')).toEqual([]);
    });

    test('rejects a numbered list that is followed by more prose', () => {
        const reply = '1. Bribe the guard\n2. Sneak past\n\nWhat do you do?';
        expect(parseChoicePrompt(reply)).toEqual([]);
    });

    test('rejects a numbered list in the middle of the reply', () => {
        const reply = 'Your inventory:\n1. Rope\n2. Lantern\n\nThe corridor stretches on into darkness.';
        expect(parseChoicePrompt(reply)).toEqual([]);
    });

    // The reply is the model's, and a player can steer it. These pin what the
    // parser reads at the edges of the pattern, then hold it to a second on
    // replies of 100,000 spaces, which took the old code 6 to 25 s on each
    // case that is not marked "guard" (issue #44).
    test('reads the text from its first to its last non-space character', () => {
        expect(parseChoicePrompt('1.   Left  door \t \n2. \u00a0Right\u3000\n')).toEqual(['Left  door', 'Right']);
        expect(parseChoicePrompt('1. A\n2. B C   D   ')).toEqual(['A', 'B C   D']);
        expect(parseChoicePrompt('1. A\r\n2. B\r\n')).toEqual(['A', 'B']);
    });

    test('does not read a line with nothing after its marker', () => {
        expect(parseChoicePrompt('1. A\n2.   \n')).toEqual([]);
        expect(parseChoicePrompt('1. A\n2.')).toEqual([]);
    });

    test('does not read a line that breaks inside its text', () => {
        expect(parseChoicePrompt('1. A\n2. B\rC')).toEqual([]);
    });

    test('stays fast on replies with long runs of whitespace', () => {
        const spaces = ' '.repeat(100000);
        const crafted = {
            'spaces inside the last option': `1. A\n2. B${spaces}C`,
            'spaces inside the last option, then a carriage return': `1. A\n2. B${spaces}\rC`,
            'spaces inside the prose': `Look.${spaces}Think.\n1. A\n2. B`,
            'spaces between the options': `1. A\n${spaces}\n2. B`,
            'guard: spaces after the last option': `1. A\n2. B${spaces}`,
            'a leading run of spaces': `${spaces}1. A\n2. B`,
        };
        for (const [label, reply] of Object.entries(crafted)) {
            const started = Date.now();
            parseChoicePrompt(reply);
            expect({ label, seconds: (Date.now() - started) / 1000 < 1 }).toEqual({ label, seconds: true });
        }
    });

    test('rejects a single numbered line', () => {
        expect(parseChoicePrompt('You may only:\n1. Wait')).toEqual([]);
    });

    test('rejects a list that does not start at 1', () => {
        expect(parseChoicePrompt('2. Sneak past\n3. Draw steel')).toEqual([]);
    });

    test('rejects a list whose numbers are out of order or repeated', () => {
        expect(parseChoicePrompt('1. A\n3. B\n2. C')).toEqual([]);
        expect(parseChoicePrompt('1. A\n1. B')).toEqual([]);
    });

    test('rejects a list with mixed "." and ")" markers', () => {
        expect(parseChoicePrompt('1. A\n2) B')).toEqual([]);
    });

    test('rejects items separated by blank lines (a loose list)', () => {
        expect(parseChoicePrompt('1. A\n\n2. B')).toEqual([]);
    });

    test('rejects a numbered line with no text after the marker', () => {
        expect(parseChoicePrompt('1. A\n2. ')).toEqual([]);
        expect(parseChoicePrompt('1. A\n2.')).toEqual([]);
    });

    test('rejects a decimal number or a year that only looks like a marker', () => {
        expect(parseChoicePrompt('It costs\n1.5 gold\n2. Nothing')).toEqual([]);
        expect(parseChoicePrompt('In the year\n1. \n2. of the Splinters')).toEqual([]);
    });

    test('rejects plain prose, bullet lists, empty and non-string input', () => {
        expect(parseChoicePrompt('The gate is shut.')).toEqual([]);
        expect(parseChoicePrompt('- Left\n- Right')).toEqual([]);
        expect(parseChoicePrompt('')).toEqual([]);
        expect(parseChoicePrompt(undefined)).toEqual([]);
    });
});

describe('choice bar', () => {
    let streamMessage;
    let CHOICE_LABEL_MAX_LENGTH;
    let CHOICE_OTHER_LABEL;

    const CHOICE_REPLY = 'The guard blocks the gate.\n\n1. **Bribe** the guard\n2. Sneak past\n3. Draw steel';

    /** One-frame SSE response whose done frame carries `fullResponse`. */
    function doneFrameResponse(fullResponse) {
        const encoder = new TextEncoder();
        const frame = `data: ${JSON.stringify({ done: true, full_response: fullResponse })}\n\n`;
        const encoded = [encoder.encode(frame)];
        let index = 0;
        return {
            ok: true,
            statusText: 'OK',
            json: () => Promise.resolve({}),
            body: {
                getReader: () => ({
                    read: () => {
                        if (index < encoded.length) {
                            return Promise.resolve({ done: false, value: encoded[index++] });
                        }
                        return Promise.resolve({ done: true, value: undefined });
                    },
                }),
            },
        };
    }

    function loadChat() {
        global.marked = require('../marked.min.js').marked;
        global.DOMPurify = require('../dompurify.min.js');
        ({ streamMessage, CHOICE_LABEL_MAX_LENGTH, CHOICE_OTHER_LABEL } = require('../chat.js'));
    }

    /** Streams a reply that ends in three choices and returns the rendered bar. */
    async function streamChoiceReply(fullResponse = CHOICE_REPLY) {
        fetch.mockResolvedValueOnce(doneFrameResponse(fullResponse));
        await streamMessage({ session_id: null, message: 'I approach the gate' });
        return document.querySelector('.choice-bar');
    }

    function pressKey(key, target = document.body, init = {}) {
        const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init });
        target.dispatchEvent(event);
        return event;
    }

    beforeEach(() => {
        jest.resetModules();
        setUpDom();
        loadChat();
        fetch.mockClear();
    });

    test('renders one button per option plus "Something else…" once the reply is done', async () => {
        const bar = await streamChoiceReply();
        expect(bar).not.toBeNull();
        // Inside the GM message wrapper, under its content.
        expect(bar.parentElement.classList.contains('assistant')).toBe(true);
        const buttons = bar.querySelectorAll('button');
        expect(buttons).toHaveLength(4);
        expect(buttons[0].querySelector('.choice-label').textContent).toBe('Bribe the guard');
        expect(buttons[1].querySelector('.choice-label').textContent).toBe('Sneak past');
        expect(buttons[2].querySelector('.choice-label').textContent).toBe('Draw steel');
        expect(buttons[3].textContent).toBe(CHOICE_OTHER_LABEL);
        expect(buttons[3].textContent).toBe('Something else…');
        buttons.forEach((button) => expect(button.type).toBe('button'));
    });

    test('renders no bar when the reply does not end in a numbered list', async () => {
        const bar = await streamChoiceReply('The gate is shut. What do you do?');
        expect(bar).toBeNull();
    });

    test('option text is rendered as text, never as markup', async () => {
        const bar = await streamChoiceReply('Pick:\n1. <img src=x onerror=alert(1)>\n2. <b>bold</b>');
        expect(bar.querySelector('img')).toBeNull();
        expect(bar.querySelector('b')).toBeNull();
        expect(bar.querySelectorAll('.choice-btn')[0].textContent).toContain('<img src=x onerror=alert(1)>');
    });

    test('trims a long label to about 80 characters but keeps the full text in the title', async () => {
        const longOption = 'Walk '.repeat(30).trim(); // 149 characters
        const bar = await streamChoiceReply(`Pick:\n1. ${longOption}\n2. Stay`);
        const first = bar.querySelector('.choice-btn');
        const label = first.querySelector('.choice-label').textContent;
        expect(label.length).toBeLessThanOrEqual(CHOICE_LABEL_MAX_LENGTH);
        expect(label.endsWith('…')).toBe(true);
        expect(first.title).toBe(longOption);
        // A short label is not truncated and carries no title.
        expect(bar.querySelectorAll('.choice-btn')[1].title).toBe('');
    });

    test('clicking an option sends its number and plain text, echoes it, and removes the bar', async () => {
        const bar = await streamChoiceReply();
        fetch.mockResolvedValueOnce(doneFrameResponse('You slip the guard a coin.'));

        bar.querySelectorAll('.choice-btn')[0].click();
        await new Promise((resolve) => setTimeout(resolve, 0));

        // The bar went the moment the pick was echoed as the player's message.
        expect(document.querySelector('.choice-bar')).toBeNull();
        const userMessages = document.querySelectorAll('.message.user .message-content');
        expect(userMessages[userMessages.length - 1].textContent).toBe('1. Bribe the guard');
        expect(fetch).toHaveBeenLastCalledWith('/tarmar/gm/stream/', expect.objectContaining({
            body: JSON.stringify({ session_id: null, message: '1. Bribe the guard', mode: 'play' }),
        }));
    });

    test('"Something else…" dismisses the bar and focuses the input without sending', async () => {
        const bar = await streamChoiceReply();
        const input = document.getElementById('user-input');
        input.blur();
        fetch.mockClear();

        bar.querySelector('.choice-btn-other').click();

        expect(document.querySelector('.choice-bar')).toBeNull();
        expect(document.activeElement).toBe(input);
        expect(fetch).not.toHaveBeenCalled();
    });

    test('a number key sends that option while the input is empty', async () => {
        await streamChoiceReply();
        fetch.mockResolvedValueOnce(doneFrameResponse('You draw your blade.'));

        const event = pressKey('3', document.getElementById('user-input'));
        await new Promise((resolve) => setTimeout(resolve, 0));

        expect(event.defaultPrevented).toBe(true);
        expect(document.querySelector('.choice-bar')).toBeNull();
        expect(fetch).toHaveBeenLastCalledWith('/tarmar/gm/stream/', expect.objectContaining({
            body: JSON.stringify({ session_id: null, message: '3. Draw steel', mode: 'play' }),
        }));
    });

    test('a number key is ordinary typing once the input holds text', async () => {
        await streamChoiceReply();
        fetch.mockClear();
        const input = document.getElementById('user-input');
        input.value = 'I have 2';

        const event = pressKey('2', input);

        expect(event.defaultPrevented).toBe(false);
        expect(document.querySelector('.choice-bar')).not.toBeNull();
        expect(fetch).not.toHaveBeenCalled();
    });

    test('a number key beyond N, a modifier chord, or typing in another field does nothing', async () => {
        await streamChoiceReply();
        fetch.mockClear();

        expect(pressKey('4').defaultPrevented).toBe(false);
        expect(pressKey('2', document.body, { metaKey: true }).defaultPrevented).toBe(false);

        const otherField = document.createElement('input');
        document.body.appendChild(otherField);
        expect(pressKey('2', otherField).defaultPrevented).toBe(false);

        expect(document.querySelector('.choice-bar')).not.toBeNull();
        expect(fetch).not.toHaveBeenCalled();
    });

    test('a typed message removes the bar too', async () => {
        await streamChoiceReply();
        fetch.mockResolvedValueOnce(doneFrameResponse('Noted.'));
        const input = document.getElementById('user-input');
        input.value = 'I shout for help';

        // chat.html wires the form's onsubmit to handleSubmit(event); that
        // function is a script-scope global in the browser but module-scoped
        // under Jest, so drive the same path every send takes: a new message
        // is added, and adding one clears the bar.
        await streamMessage({ session_id: null, message: input.value });
        expect(document.querySelector('.choice-bar')).toBeNull();
    });

    test('only the newest bar exists: a second choice reply replaces the first', async () => {
        await streamChoiceReply();
        await streamChoiceReply('Again:\n1. North\n2. South');
        const bars = document.querySelectorAll('.choice-bar');
        expect(bars).toHaveLength(1);
        expect(bars[0].querySelectorAll('.choice-btn')).toHaveLength(3);
    });

    describe('on reload of a saved session', () => {
        function historyMessage(role, content) {
            const wrapper = document.createElement('div');
            wrapper.className = `message ${role}`;
            const roleDiv = document.createElement('div');
            roleDiv.className = 'message-role';
            roleDiv.textContent = role === 'user' ? 'You' : 'GM';
            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';
            if (role === 'assistant') contentDiv.setAttribute('data-markdown', '');
            // textContent, as Django's auto-escaped {{ msg.content }} parses.
            contentDiv.textContent = content;
            wrapper.append(roleDiv, contentDiv);
            document.getElementById('messages').appendChild(wrapper);
        }

        beforeEach(() => {
            jest.resetModules();
            setUpDom({ sessionId: 7 });
        });

        test('re-derives the bar when the last message is a GM choice prompt', () => {
            historyMessage('user', 'I approach the gate');
            historyMessage('assistant', CHOICE_REPLY);
            loadChat();

            const bar = document.querySelector('.message.assistant .choice-bar');
            expect(bar).not.toBeNull();
            expect(bar.querySelectorAll('.choice-btn')).toHaveLength(4);
            // The reply itself still rendered as markdown.
            expect(document.querySelector('.message.assistant .message-content ol')).not.toBeNull();
        });

        test('renders no bar when the player spoke last', () => {
            historyMessage('assistant', CHOICE_REPLY);
            historyMessage('user', '2. Sneak past');
            loadChat();

            expect(document.querySelector('.choice-bar')).toBeNull();
        });

        test('renders no bar for an empty history', () => {
            loadChat();
            expect(document.querySelector('.choice-bar')).toBeNull();
        });
    });
});

describe('No /character command (issue #571)', () => {
    let chatModule;

    function streamResponse(chunks) {
        const encoder = new TextEncoder();
        const encoded = chunks.map((c) => encoder.encode(c));
        let i = 0;
        return {
            ok: true,
            statusText: 'OK',
            json: () => Promise.resolve({}),
            body: {
                getReader: () => ({
                    read: () => Promise.resolve(
                        i < encoded.length
                            ? { done: false, value: encoded[i++] }
                            : { done: true, value: undefined }
                    ),
                }),
            },
        };
    }

    beforeEach(() => {
        jest.resetModules();
        setUpDom({ sessionId: 5 });
        global.marked = require('../marked.min.js').marked;
        global.DOMPurify = require('../dompurify.min.js');
        chatModule = require('../chat.js');
        fetch.mockClear();
        window.history.replaceState(null, '', '/tarmar/gm/?session=5');
        jest.spyOn(window.history, 'replaceState').mockImplementation(() => {});
    });

    afterEach(() => {
        window.history.replaceState.mockRestore();
    });

    test('a done frame is always rendered as a reply, never a save notice', async () => {
        fetch.mockResolvedValue(streamResponse([
            `data: ${JSON.stringify({ done: true, full_response: '**Kestrel**', session_id: 5, character: { id: 3 } })}\n\n`,
        ]));

        await chatModule.streamMessage({ session_id: 5, message: 'hi' });

        const message = document.querySelector('.message.assistant');
        expect(message.classList.contains('notice')).toBe(false);
        expect(message.querySelector('strong')).not.toBeNull();
        expect(pageNavigation.go).not.toHaveBeenCalled();
    });

    test('an error frame is a notice and reloads nothing', async () => {
        fetch.mockResolvedValue(streamResponse([
            'data: {"error":"The character was not saved: Name cannot be empty"}\n\n',
        ]));

        await chatModule.streamMessage({ session_id: 5, command: 'character' });

        // Not a GM bubble (issue #339, item 11): the refusal is the app's,
        // and the transcript is the Game Master's.
        expect(document.querySelector('.message.assistant')).toBeNull();
        expect(alert.mock.calls[0][0]).toContain('Name cannot be empty');
        expect(pageNavigation.go).not.toHaveBeenCalled();
        expect(pageNavigation.reload).not.toHaveBeenCalled();
    });

    test('an ordinary done frame still renders markdown', async () => {
        fetch.mockResolvedValue(streamResponse([
            'data: {"done":true,"full_response":"**bold**","session_id":5}\n\n',
        ]));
        await chatModule.streamMessage({ session_id: 5, message: 'hi' });
        const message = document.querySelector('.message.assistant');
        expect(message.classList.contains('notice')).toBe(false);
        expect(message.querySelector('strong')).not.toBeNull();
        expect(pageNavigation.go).not.toHaveBeenCalled();
    });

    test('/help no longer lists it: every change is saved as it happens', () => {
        const input = document.getElementById('user-input');
        input.value = '/help';
        chatModule.handleSubmit({ preventDefault() {} });
        expect(document.querySelector('.message.assistant').textContent).not.toContain('/character');
    });
});

/**
 * Naming a session (issue #339, item 10).
 *
 * The name is a field on the page that autosaves on blur; the sidebar entry
 * and the mobile picker are updated in place rather than by reloading, which
 * is what used to throw away the confirmation and anything half-typed.
 */
describe('session name', () => {
    let chatModule;

    function setUpChromeWithName() {
        const chatConfig = JSON.stringify({
            streamUrl: '/tarmar/gm/stream/',
            saveUrl: '/tarmar/gm/save/',
            deleteUrl: '/tarmar/gm/delete/',
            csrfToken: 'test-token',
            sessionId: 7,
        });
        document.body.innerHTML = `
            <main></main>
            <script id="chat-config" type="application/json">${chatConfig}<\/script>
            <ul id="session-list">
                <li class="session-item" data-id="7">
                    <a href="?session=7"><span class="session-name">New Session</span></a>
                </li>
            </ul>
            <select id="mobile-session-select">
                <option value="7" data-message-count="4">New Session (4)</option>
            </select>
            <div class="session-name-field" data-field="name">
                <input type="text" id="session-name" class="autosave session-name-input"
                       value="New Session" data-session-id="7">
            </div>
            <div id="messages"></div>
            <input id="user-input">
            <button id="send-btn"></button>
        `;
    }

    /** Let the save's promise chain run. */
    async function settle() {
        for (let tick = 0; tick < 6; tick += 1) {
            await new Promise((resolve) => setTimeout(resolve, 0));
        }
    }

    beforeEach(() => {
        jest.resetModules();
        setUpChromeWithName();
        global.marked = require('../marked.min.js').marked;
        global.DOMPurify = require('../dompurify.min.js');
        chatModule = require('../chat.js');
        fetch.mockClear();
        alert.mockClear();
    });

    test('a rename shows everywhere the name is shown, without a reload', async () => {
        fetch.mockResolvedValue({
            ok: true,
            json: () => Promise.resolve({ ok: true, name: 'The Salt Road' }),
        });

        await chatModule.renameSession('The Salt Road');

        expect(document.querySelector('.session-item .session-name').textContent)
            .toBe('The Salt Road');
        expect(document.querySelector('#mobile-session-select option').textContent)
            .toBe('The Salt Road (4)');
        expect(document.getElementById('session-name').value).toBe('The Salt Road');
        expect(pageNavigation.reload).not.toHaveBeenCalled();
    });

    test('a finished turn shows the name and count the server now holds (#829)', () => {
        const entry = document.querySelector('.session-item[data-id="7"]');
        const counted = document.createElement('span');
        counted.className = 'session-count';
        counted.textContent = '0 msgs';
        entry.querySelector('a').append(counted);

        chatModule.refreshSessionAfterTurn(7, 'I walk into the tavern.', 2, '2 msgs');

        expect(entry.querySelector('.session-name').textContent)
            .toBe('I walk into the tavern.');
        expect(counted.textContent).toBe('2 msgs');
        expect(document.getElementById('session-name').value)
            .toBe('I walk into the tavern.');
        expect(document.querySelector('#mobile-session-select option').textContent)
            .toBe('I walk into the tavern. (2)');

        // The wording is the server's, so the page writes whatever it sends.
        chatModule.refreshSessionAfterTurn(7, 'I walk into the tavern.', 1, '1 msg');
        expect(counted.textContent).toBe('1 msg');
    });

    test('a count without the server\'s wording leaves the rail\'s words alone (#62)', () => {
        const entry = document.querySelector('.session-item[data-id="7"]');
        const counted = document.createElement('span');
        counted.className = 'session-count';
        counted.textContent = '0 msgs';
        entry.querySelector('a').append(counted);

        chatModule.refreshSessionAfterTurn(7, '', 3);

        expect(counted.textContent).toBe('0 msgs');
    });

    test('a count alone keeps the picker\'s name (#829)', () => {
        chatModule.refreshSessionAfterTurn(7, '', 3);
        expect(document.querySelector('#mobile-session-select option').textContent)
            .toBe('New Session (3)');
    });

    test('a name being typed is not overwritten by a finished turn (#829)', () => {
        const field = document.getElementById('session-name');
        field.focus();
        field.value = 'Mine';

        chatModule.refreshSessionAfterTurn(7, 'I walk into the tavern.', 2);

        expect(field.value).toBe('Mine');
    });

    test('a name is written as text, never as markup', () => {
        chatModule.applySessionName(7, '<img src=x onerror=alert(1)>');
        const entry = document.querySelector('.session-item .session-name');
        expect(entry.querySelector('img')).toBeNull();
        expect(entry.textContent).toBe('<img src=x onerror=alert(1)>');
    });

    test('a refused rename says so and changes nothing on the page', async () => {
        fetch.mockResolvedValue({
            ok: false,
            statusText: 'Bad Request',
            json: () => Promise.resolve({ error: 'A session needs a name' }),
        });

        await expect(chatModule.renameSession('')).resolves.toBe(false);
        expect(alert.mock.calls[0][0]).toContain('A session needs a name');
        expect(document.querySelector('.session-item .session-name').textContent)
            .toBe('New Session');
    });

    test('the Rename button sends the player to the field rather than a dialog', async () => {
        await chatModule.promptSave();
        expect(document.activeElement.id).toBe('session-name');
        expect(fetch).not.toHaveBeenCalled();
    });

    test('/save with a name renames straight away', async () => {
        fetch.mockResolvedValue({
            ok: true,
            json: () => Promise.resolve({ ok: true, name: 'Deep Wells' }),
        });

        await chatModule.promptSave('Deep Wells');

        expect(fetch.mock.calls[0][0]).toBe('/tarmar/gm/save/');
        expect(JSON.parse(fetch.mock.calls[0][1].body))
            .toEqual({ session_id: 7, name: 'Deep Wells' });
        expect(document.querySelector('.session-item .session-name').textContent)
            .toBe('Deep Wells');
    });

    test('the field autosaves on blur', async () => {
        fetch.mockResolvedValue({
            ok: true,
            json: () => Promise.resolve({ ok: true, name: 'Deep Wells' }),
        });
        const field = document.getElementById('session-name');
        field.value = 'Deep Wells';
        field.dispatchEvent(new Event('blur'));
        await settle();

        expect(JSON.parse(fetch.mock.calls[0][1].body))
            .toEqual({ session_id: 7, name: 'Deep Wells' });
        expect(document.querySelector('.session-item .session-name').textContent)
            .toBe('Deep Wells');
        expect(document.querySelector('[data-field="name"]').classList.contains('saved'))
            .toBe(true);
    });

    test('with no session yet, the button explains instead of failing', async () => {
        jest.resetModules();
        document.body.innerHTML = `
            <main></main>
            <script id="chat-config" type="application/json">${JSON.stringify({
                streamUrl: '/s/', saveUrl: '/save/', deleteUrl: '/d/',
                csrfToken: 't', sessionId: null,
            })}<\/script>
            <div id="messages"></div>
            <input id="user-input">
            <button id="send-btn"></button>
        `;
        const fresh = require('../chat.js');
        alert.mockClear();
        fetch.mockClear();

        await fresh.promptSave();

        expect(fetch).not.toHaveBeenCalled();
        expect(alert.mock.calls[0][0]).toContain('no session yet');
        expect(document.querySelector('.message')).toBeNull();
    });
});

describe('saving the transcript (issues #389, #397)', () => {
    // Fetched and saved from the page on every press, so each one is a real
    // request whose outcome the page knows. Assigning the URL to the location,
    // which is what this did first, worked once and then did nothing in Safari.
    let chatModule;
    let clickSpy;

    const FILENAME = 'gm-session-5-the-broken-wheel-20260913-001706.txt';

    function loadChat(config) {
        jest.resetModules();
        setUpDom(config);
        global.marked = require('../marked.min.js').marked;
        global.DOMPurify = require('../dompurify.min.js');
        chatModule = require('../chat.js');
        fetch.mockReset();
        alert.mockClear();
        URL.createObjectURL = jest.fn(() => 'blob:transcript');
        URL.revokeObjectURL = jest.fn();
        clickSpy = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    }

    function transcriptResponse() {
        return {
            ok: true,
            status: 200,
            headers: { get: (name) => (name.toLowerCase() === 'content-disposition' ? `attachment; filename="${FILENAME}"` : null) },
            blob: () => Promise.resolve(new Blob(['GM SESSION TRANSCRIPT'])),
        };
    }

    afterEach(() => {
        clickSpy.mockRestore();
    });

    test('every press fetches the file again, saves it, and says so', async () => {
        loadChat({ sessionId: 5, transcriptUrlTemplate: '/tarmar/gm/sessions/{session_id}/transcript/' });
        fetch.mockResolvedValue(transcriptResponse());

        await chatModule.downloadTranscript();
        await chatModule.downloadTranscript();

        expect(fetch).toHaveBeenCalledTimes(2);
        expect(fetch).toHaveBeenCalledWith('/tarmar/gm/sessions/5/transcript/', { credentials: 'same-origin' });
        expect(clickSpy).toHaveBeenCalledTimes(2);
        expect(alert.mock.calls.map((call) => call[0])).toEqual([
            `Transcript saved as ${FILENAME}.`,
            `Transcript saved as ${FILENAME}.`,
        ]);
        expect(document.querySelector('.message')).toBeNull();
    });

    test('the button and the typed command both save it', async () => {
        loadChat({ sessionId: 5, transcriptUrlTemplate: '/tarmar/gm/sessions/{session_id}/transcript/' });
        fetch.mockResolvedValue(transcriptResponse());
        const input = document.getElementById('user-input');

        chatModule.sendCommand('transcript');
        input.value = '/transcript';
        chatModule.handleSubmit({ preventDefault() {} });
        await new Promise((resolve) => setTimeout(resolve, 0));

        expect(fetch).toHaveBeenCalledTimes(2);
        expect(input.value).toBe('');
        expect(document.querySelector('.message.user')).toBeNull();
    });

    test('a download the server refuses says so instead of claiming success', async () => {
        loadChat({ sessionId: 5, transcriptUrlTemplate: '/tarmar/gm/sessions/{session_id}/transcript/' });
        fetch.mockResolvedValue({ ok: false, status: 404, headers: { get: () => null } });

        await chatModule.downloadTranscript();

        expect(clickSpy).not.toHaveBeenCalled();
        expect(alert.mock.calls[0][0]).toBe('The transcript could not be downloaded: the server answered 404.');
    });

    test('a broken connection says so', async () => {
        loadChat({ sessionId: 5, transcriptUrlTemplate: '/tarmar/gm/sessions/{session_id}/transcript/' });
        fetch.mockRejectedValue(new Error('offline'));

        await chatModule.downloadTranscript();

        expect(alert.mock.calls[0][0]).toBe('The transcript could not be downloaded: offline.');
    });

    test('with no session yet it says there is nothing to download, and fetches nothing', async () => {
        loadChat({ sessionId: null, transcriptUrlTemplate: '/tarmar/gm/sessions/{session_id}/transcript/' });

        await chatModule.downloadTranscript();

        expect(alert.mock.calls[0][0]).toContain('nothing to download yet');
        expect(fetch).not.toHaveBeenCalled();
    });

    test('a session this page started has a transcript without a reload (issue #436)', async () => {
        loadChat({ sessionId: null, transcriptUrlTemplate: '/tarmar/gm/sessions/{session_id}/transcript/' });
        const encoder = new TextEncoder();
        const frames = [encoder.encode('data: {"done":true,"full_response":"Both twins are built.","session_id":41}\n\n')];
        fetch.mockResolvedValueOnce({
            ok: true,
            status: 200,
            body: { getReader: () => ({ read: () => Promise.resolve(frames.length ? { done: false, value: frames.shift() } : { done: true }) }) },
        });
        await chatModule.streamMessage({ session_id: null, message: 'Build the twins.' });
        fetch.mockResolvedValueOnce(transcriptResponse());

        await chatModule.downloadTranscript();

        expect(fetch).toHaveBeenLastCalledWith('/tarmar/gm/sessions/41/transcript/', { credentials: 'same-origin' });
        expect(alert.mock.calls.map((call) => call[0])).toContain(`Transcript saved as ${FILENAME}.`);
    });

    test('/help lists the command', () => {
        loadChat({ sessionId: 5, transcriptUrlTemplate: '/tarmar/gm/sessions/{session_id}/transcript/' });
        const input = document.getElementById('user-input');
        input.value = '/help';

        chatModule.handleSubmit({ preventDefault() {} });

        expect(document.getElementById('messages').textContent).toContain('/transcript');
    });
});

describe('while the GM is working (issue #398)', () => {
    // A turn that runs procedures can go a minute without new text. The page
    // says what is happening for the whole turn, and the box stays open for
    // the player's next move; only sending waits.
    let chatModule;

    function loadChat() {
        jest.resetModules();
        setUpDom({ sessionId: 5, transcriptUrlTemplate: '/tarmar/gm/sessions/{session_id}/transcript/' });
        document.body.insertAdjacentHTML('beforeend', '<p id="gm-status" hidden></p>');
        global.marked = require('../marked.min.js').marked;
        global.DOMPurify = require('../dompurify.min.js');
        chatModule = require('../chat.js');
        fetch.mockReset();
        alert.mockClear();
    }

    /** A response whose frames arrive only when the test pushes them. */
    function heldStream() {
        const encoder = new TextEncoder();
        const queue = [];
        let waiting = null;
        function deliver(item) {
            if (waiting) {
                const resolve = waiting;
                waiting = null;
                resolve(item);
            } else {
                queue.push(item);
            }
        }
        return {
            push: (frame) => deliver({ done: false, value: encoder.encode(frame) }),
            end: () => deliver({ done: true, value: undefined }),
            response: {
                ok: true,
                statusText: 'OK',
                json: () => Promise.resolve({}),
                body: {
                    getReader: () => ({
                        read: () => (queue.length
                            ? Promise.resolve(queue.shift())
                            : new Promise((resolve) => { waiting = resolve; })),
                    }),
                },
            },
        };
    }

    async function settle() {
        for (let tick = 0; tick < 5; tick += 1) {
            await new Promise((resolve) => setTimeout(resolve, 0));
        }
    }

    test('the status runs from send to done and names what the server is doing', async () => {
        loadChat();
        const stream = heldStream();
        fetch.mockResolvedValue(stream.response);
        const status = document.getElementById('gm-status');

        const turn = chatModule.streamMessage({ message: 'Build the twins' });
        await settle();

        expect(status.hidden).toBe(false);
        expect(status.textContent).toBe('The Game Master is working');
        expect(document.getElementById('send-btn').disabled).toBe(true);
        expect(document.getElementById('user-input').disabled).toBe(false);

        stream.push('data: {"procedure":{"label":"5 years on the magic track for Sister","ran":true,"lines":["⚙️ magic year 1"]}}\n\n');
        await settle();
        expect(status.textContent).toBe('The Game Master is working: 5 years on the magic track for Sister');

        stream.push('data: {"gm_roll":{"line":"🎲 2d6 = 7"}}\n\n');
        await settle();
        expect(status.textContent).toBe('The Game Master is working: rolling dice');

        stream.push('data: {"token":"Both twins are built."}\n\n');
        await settle();
        expect(status.textContent).toBe('The Game Master is working: writing');

        stream.push('data: {"done":true,"full_response":"Both twins are built."}\n\n');
        stream.end();
        await turn;

        expect(status.hidden).toBe(true);
        expect(status.textContent).toBe('');
        expect(document.getElementById('send-btn').disabled).toBe(false);
    });

    test('a failed turn clears the status too', async () => {
        loadChat();
        const stream = heldStream();
        fetch.mockResolvedValue(stream.response);
        const status = document.getElementById('gm-status');

        const turn = chatModule.streamMessage({ message: 'hi' });
        await settle();
        stream.push('data: {"error":"model unavailable"}\n\n');
        stream.end();
        await turn;
        await settle();

        expect(status.hidden).toBe(true);
        expect(alert.mock.calls[0][0]).toContain('model unavailable');
    });

    test('the next move can be typed mid-turn, and Enter keeps it rather than sending', async () => {
        loadChat();
        const stream = heldStream();
        fetch.mockResolvedValue(stream.response);
        const input = document.getElementById('user-input');

        chatModule.streamMessage({ message: 'I knock' });
        await settle();
        input.value = 'Then I wait by the wall';
        chatModule.handleSubmit({ preventDefault() {} });

        expect(fetch).toHaveBeenCalledTimes(1);
        expect(input.value).toBe('Then I wait by the wall');
    });

    test('a failed turn does not overwrite a next move already typed', async () => {
        loadChat();
        const stream = heldStream();
        fetch.mockResolvedValue(stream.response);
        const input = document.getElementById('user-input');

        chatModule.sendCommand('state');
        await settle();
        input.value = 'my next move';
        stream.push('data: {"error":"model unavailable"}\n\n');
        stream.end();
        await settle();

        expect(input.value).toBe('my next move');
    });

    test('the transcript can be saved while the GM is still answering', async () => {
        loadChat();
        const stream = heldStream();
        fetch.mockResolvedValueOnce(stream.response);
        chatModule.streamMessage({ message: 'I knock' });
        await settle();
        URL.createObjectURL = jest.fn(() => 'blob:transcript');
        URL.revokeObjectURL = jest.fn();
        const click = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
        fetch.mockResolvedValueOnce({
            ok: true,
            status: 200,
            headers: { get: () => 'attachment; filename="gm-session-5.txt"' },
            blob: () => Promise.resolve(new Blob(['x'])),
        });

        chatModule.sendCommand('transcript');
        await settle();

        expect(fetch).toHaveBeenLastCalledWith('/tarmar/gm/sessions/5/transcript/', { credentials: 'same-origin' });
        expect(click).toHaveBeenCalledTimes(1);
        click.mockRestore();
    });
});

describe('sending the session to Claude for review (issue #402)', () => {
    let chatModule;

    function loadChat(config) {
        jest.resetModules();
        setUpDom(config);
        global.marked = require('../marked.min.js').marked;
        global.DOMPurify = require('../dompurify.min.js');
        chatModule = require('../chat.js');
        fetch.mockReset();
        alert.mockClear();
    }

    function sentBody(call) {
        return JSON.parse(call[1].body);
    }

    test('a Game Master typing /review sends the session with the note and is told where the findings go', async () => {
        loadChat({ sessionId: 5, reviewUrl: '/tarmar/gm/reviews/' });
        fetch.mockResolvedValue({ ok: true, json: () => Promise.resolve({ ok: true, review_id: 12, url: '/tarmar/gm/reviews/12/' }) });
        const input = document.getElementById('user-input');
        input.value = '/review the twins merged';

        chatModule.handleSubmit({ preventDefault() {} });
        await new Promise((resolve) => setTimeout(resolve, 0));
        await new Promise((resolve) => setTimeout(resolve, 0));

        expect(fetch.mock.calls[0][0]).toBe('/tarmar/gm/reviews/');
        expect(sentBody(fetch.mock.calls[0])).toEqual({ session_id: 5, note: 'the twins merged' });
        expect(input.value).toBe('');
        expect(document.querySelector('.message.user')).toBeNull();
        expect(alert.mock.calls[0][0]).toBe('Sent to Claude as review #12. The findings will appear at /tarmar/gm/reviews/12/ once Claude has reviewed it.');
    });

    test('the button asks what to look at, then sends it', async () => {
        loadChat({ sessionId: 5, reviewUrl: '/tarmar/gm/reviews/' });
        global.prompt = jest.fn(() => 'the death went unnoticed');
        fetch.mockResolvedValue({ ok: true, json: () => Promise.resolve({ ok: true, review_id: 13, url: '/tarmar/gm/reviews/13/' }) });

        chatModule.sendCommand('review');
        for (let tick = 0; tick < 4; tick += 1) {
            await new Promise((resolve) => setTimeout(resolve, 0));
        }

        expect(sentBody(fetch.mock.calls[0])).toEqual({ session_id: 5, note: 'the death went unnoticed' });
    });

    test('a player is told it is for Game Masters, and nothing is sent', async () => {
        // The page says the server's sentence (#94), under a fixed word.
        loadChat({ sessionId: 5, reviewUrl: null, reviewGameMasterOnly: 'views.game_master_only' });

        await chatModule.sendForReview('anything');

        expect(fetch).not.toHaveBeenCalled();
        expect(alert.mock.calls[0][0]).toBe('views.game_master_only');
    });

    test('with no session yet there is nothing to send', async () => {
        loadChat({ sessionId: null, reviewUrl: '/tarmar/gm/reviews/' });

        await chatModule.sendForReview('anything');

        expect(fetch).not.toHaveBeenCalled();
        expect(alert.mock.calls[0][0]).toContain('nothing to send yet');
    });

    test('a refusal from the server is said plainly', async () => {
        loadChat({ sessionId: 5, reviewUrl: '/tarmar/gm/reviews/' });
        fetch.mockResolvedValue({ ok: false, status: 403, statusText: 'Forbidden', json: () => Promise.resolve({ error: 'views.game_master_only' }) });

        await chatModule.sendForReview('x');

        expect(alert.mock.calls[0][0]).toBe('The session could not be sent to Claude: views.game_master_only');
    });

    test('/help lists the command', () => {
        loadChat({ sessionId: 5, reviewUrl: '/tarmar/gm/reviews/' });
        const input = document.getElementById('user-input');
        input.value = '/help';

        chatModule.handleSubmit({ preventDefault() {} });

        expect(document.getElementById('messages').textContent).toContain('/review');
    });
});

describe('/help is written for the account reading it (issue #830)', () => {
    function helpFor(config) {
        jest.resetModules();
        setUpDom(config);
        global.marked = require('../marked.min.js').marked;
        global.DOMPurify = require('../dompurify.min.js');
        const chat = require('../chat.js');
        document.getElementById('user-input').value = '/help';
        chat.handleSubmit({ preventDefault() {} });
        return document.getElementById('messages').textContent;
    }

    test('a player is not offered /review', () => {
        const help = helpFor({ sessionId: 5 });
        expect(help).not.toContain('/review');
        expect(help).not.toContain('Game Masters');
    });

    test('a Game Master is', () => {
        expect(helpFor({ sessionId: 5, reviewUrl: '/tarmar/gm/reviews/' })).toContain('/review');
    });

    test('the rename row is named after the Rename button', () => {
        const help = helpFor({ sessionId: 5 });
        expect(help).toContain('/rename <name>');
        expect(help).toContain('Rename this session');
        expect(help).not.toContain('Save current session');
    });

    test('/rename renames as /save does', async () => {
        jest.resetModules();
        setUpDom({ sessionId: 5, csrfToken: 't' });
        global.marked = require('../marked.min.js').marked;
        global.DOMPurify = require('../dompurify.min.js');
        const chat = require('../chat.js');
        fetch.mockResolvedValue({
            ok: true,
            json: () => Promise.resolve({ ok: true, name: 'Deep Wells' }),
        });
        fetch.mockClear();
        document.getElementById('user-input').value = '/rename Deep Wells';
        chat.handleSubmit({ preventDefault() {} });
        await new Promise((resolve) => setTimeout(resolve, 0));
        expect(fetch.mock.calls[0][0]).toBe('/tarmar/gm/save/');
        expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ session_id: 5, name: 'Deep Wells' });
    });
});

describe('a delete confirmation names the session date (#705)', () => {
    let deleteSession;

    beforeEach(() => {
        jest.resetModules();
        setUpDom({ sessionId: 42 });
        global.marked = require('../marked.min.js').marked;
        global.DOMPurify = require('../dompurify.min.js');
        ({ deleteSession } = require('../chat.js'));
        confirm.mockClear();
        confirm.mockReturnValue(false);
    });

    test('two sessions with the same name are told apart by their date', async () => {
        await deleteSession(1, 'I walk into the crypt.', 'Sep 25, 2026');
        await deleteSession(2, 'I walk into the crypt.', 'Sep 21, 2026');

        const asked = confirm.mock.calls.map((call) => call[0]);
        expect(asked[0]).toContain('started Sep 25, 2026');
        expect(asked[1]).toContain('started Sep 21, 2026');
        expect(asked[0]).not.toEqual(asked[1]);
    });

    test('the question reads as one sentence, with no comma before its question mark', async () => {
        await deleteSession(1, 'I walk into the crypt.', 'Sep 25, 2026');

        expect(confirm).toHaveBeenCalledWith(
            'Delete "I walk into the crypt.", started Sep 25, 2026? This can\'t be undone.'
        );
    });

    test('a caller with no date still asks a sensible question', async () => {
        await deleteSession(3, 'A lone session');

        expect(confirm.mock.calls[0][0]).toBe('Delete "A lone session"? This can\'t be undone.');
    });
});

describe('localiseSessionOptions (issue #62)', () => {
    const SESSION_MENU = `
        <select id="mobile-session-select">
            <option value="">Sessions…</option>
            <option value="7" data-name="Rat cellar" data-message-count="3"
                    data-started="Oct 15, 2026" data-started-at="2026-10-15T02:50:00+00:00">Rat cellar — Oct 15, 2026 (3)</option>
        </select>
    `;
    const SERVER_LABEL = 'Rat cellar \u2014 Oct 15, 2026 (3)';
    const LOCAL_LABEL = 'Rat cellar \u2014 Oct 14, 2026 (3)';
    let localiseSessionOptions;

    /** Load chat.js on the page as the server renders it, with a renderer or none. */
    function loadChat(renderer) {
        jest.resetModules();
        setUpDom();
        document.body.insertAdjacentHTML('beforeend', SESSION_MENU);
        global.marked = require('../marked.min.js').marked;
        global.DOMPurify = require('../dompurify.min.js');
        if (renderer) window.formatLocalTime = renderer;
        ({ localiseSessionOptions } = require('../chat.js'));
    }

    afterEach(() => {
        delete window.formatLocalTime;
    });

    test('runs when the page loads, putting the date in the viewer\'s zone into the label', () => {
        const renderer = jest.fn(() => 'Oct 14, 2026');

        loadChat(renderer);

        const option = document.querySelector('option[value="7"]');
        expect(renderer).toHaveBeenCalledWith('2026-10-15T02:50:00+00:00', 'date');
        expect(option.textContent).toBe(LOCAL_LABEL);
        expect(option.dataset.started).toBe('Oct 14, 2026');
    });

    test('can be run again over options the server painted later', () => {
        loadChat(null);
        expect(document.querySelector('option[value="7"]').textContent).toBe(SERVER_LABEL);
        window.formatLocalTime = jest.fn(() => 'Oct 14, 2026');

        localiseSessionOptions(document);

        expect(document.querySelector('option[value="7"]').textContent).toBe(LOCAL_LABEL);
    });

    test('leaves the placeholder and any option without an instant alone', () => {
        loadChat(null);
        document.querySelector('option[value="7"]').removeAttribute('data-started-at');
        window.formatLocalTime = jest.fn(() => 'Oct 14, 2026');

        localiseSessionOptions(document);

        expect(document.querySelector('option[value=""]').textContent).toBe('Sessions…');
        expect(document.querySelector('option[value="7"]').textContent).toBe(SERVER_LABEL);
    });

    test('keeps the server\'s words when the renderer cannot read the instant', () => {
        loadChat(jest.fn(() => null));

        expect(document.querySelector('option[value="7"]').textContent).toBe(SERVER_LABEL);
    });

    test('keeps the server\'s words when local-time.js never loaded', () => {
        loadChat(null);

        expect(document.querySelector('option[value="7"]').textContent).toBe(SERVER_LABEL);
    });
});

describe('showNewSessionInRail (issue #62)', () => {
    let showNewSessionInRail;
    let deleteSession;

    /** The row gm/_session_rail_row.html writes for a session, as the server sends it. */
    const SERVER_ROW = `<li class="session-item active" data-id="9">
        <a href="?session=9">
            <span class="session-name" id="session-name-9">Fresh start</span>
            <span class="session-meta"><span id="session-date-9"><time class="local-time" datetime="2026-10-15T02:50:00+00:00" data-local-time-style="date">Oct 15, 2026 UTC</time></span> &middot; <span class="session-count">2 msgs</span></span>
        </a>
        <span class="sr-only" id="session-started-9">started</span>
        <button type="button" class="session-delete-btn" id="session-delete-9" aria-label="Delete session"
                aria-labelledby="session-delete-9 session-name-9 session-started-9 session-date-9" title="Delete session"
                onclick="deleteSession(9, 'Fresh start', document.getElementById('session-date-9').textContent)">&times;</button>
    </li>`;

    /** The option gm/_session_menu_option.html writes for the same session. */
    const SERVER_OPTION = `<option value="9" data-message-count="2" data-name="Fresh start"
        data-started="Oct 15, 2026" data-started-at="2026-10-15T02:50:00+00:00" selected>Fresh start &mdash; Oct 15, 2026 (2)</option>`;

    beforeEach(() => {
        jest.resetModules();
        setUpDom();
        document.body.insertAdjacentHTML('beforeend', `
            <select id="mobile-session-select">
                <option value="">Sessions…</option>
                <option value="3">Older</option>
            </select>
            <ul id="session-list"><li class="session-item" data-id="3"><a href="?session=3">Older</a></li></ul>
            <p class="session-name-none">A new session takes its name from your first message</p>
        `);
        global.marked = require('../marked.min.js').marked;
        global.DOMPurify = require('../dompurify.min.js');
        window.applyLocalTimes = jest.fn();
        ({ showNewSessionInRail, deleteSession } = require('../chat.js'));
        // The row's onclick names it as a browser's script does: a global.
        window.deleteSession = deleteSession;
    });

    afterEach(() => {
        delete window.applyLocalTimes;
        delete window.deleteSession;
    });

    test('puts the server\'s row at the head of the rail, untouched', () => {
        showNewSessionInRail(9, { name: 'Fresh start', rowHtml: SERVER_ROW });

        const rows = document.querySelectorAll('#session-list > li');
        expect(rows).toHaveLength(2);
        expect(rows[0].dataset.id).toBe('9');
        expect(rows[0].outerHTML).toBe(
            new DOMParser().parseFromString(SERVER_ROW, 'text/html').body.firstElementChild.outerHTML
        );
        expect(rows[1].dataset.id).toBe('3');
    });

    test('asks local-time.js to write the new row\'s time in the viewer\'s zone', () => {
        showNewSessionInRail(9, { name: 'Fresh start', rowHtml: SERVER_ROW });

        expect(window.applyLocalTimes).toHaveBeenCalledTimes(1);
        expect(window.applyLocalTimes).toHaveBeenCalledWith(document.querySelector('[data-id="9"]'));
    });

    test('takes the message count and the labels from the server\'s row, not from the page', () => {
        showNewSessionInRail(9, { name: 'Fresh start', rowHtml: SERVER_ROW });

        expect(document.querySelector('[data-id="9"] .session-count').textContent).toBe('2 msgs');
        expect(document.getElementById('session-delete-9').getAttribute('aria-labelledby'))
            .toBe('session-delete-9 session-name-9 session-started-9 session-date-9');
    });

    test('does not add a row the rail already has', () => {
        showNewSessionInRail(9, { name: 'Fresh start', rowHtml: SERVER_ROW });
        showNewSessionInRail(9, { name: 'Fresh start', rowHtml: SERVER_ROW });

        expect(document.querySelectorAll('#session-list > li[data-id="9"]')).toHaveLength(1);
        expect(window.applyLocalTimes).toHaveBeenCalledTimes(1);
    });

    test('swaps the "new session" line for the name field', () => {
        showNewSessionInRail(9, { name: 'Fresh start', rowHtml: SERVER_ROW });

        expect(document.querySelector('.session-name-none')).toBeNull();
        const field = document.getElementById('session-name');
        expect(field.value).toBe('Fresh start');
        expect(field.dataset.sessionId).toBe('9');
    });

    test('puts the server\'s option into the small-screen menu, after its prompt', () => {
        showNewSessionInRail(9, { name: 'Fresh start', rowHtml: SERVER_ROW, optionHtml: SERVER_OPTION });

        const options = [...document.querySelectorAll('#mobile-session-select option')];
        expect(options.map((option) => option.value)).toEqual(['', '9', '3']);
        expect(options[1].selected).toBe(true);
        expect(options[1].dataset.messageCount).toBe('2');
    });

    test('writes the menu option\'s date in the viewer\'s zone like the others', () => {
        window.formatLocalTime = jest.fn(() => 'Oct 14, 2026');

        showNewSessionInRail(9, { name: 'Fresh start', rowHtml: SERVER_ROW, optionHtml: SERVER_OPTION });

        const option = document.querySelector('#mobile-session-select option[value="9"]');
        expect(window.formatLocalTime).toHaveBeenCalledWith('2026-10-15T02:50:00+00:00', 'date');
        expect(option.textContent).toBe('Fresh start \u2014 Oct 14, 2026 (2)');
        delete window.formatLocalTime;
    });

    test('does not add a menu option the menu already has', () => {
        showNewSessionInRail(9, { name: 'Fresh start', rowHtml: SERVER_ROW, optionHtml: SERVER_OPTION });
        document.querySelector('#session-list > li[data-id="9"]').remove();

        showNewSessionInRail(9, { name: 'Fresh start', rowHtml: SERVER_ROW, optionHtml: SERVER_OPTION });

        expect(document.querySelectorAll('#mobile-session-select option[value="9"]')).toHaveLength(1);
    });

    test('adds no option when the frame carried none', () => {
        showNewSessionInRail(9, { name: 'Fresh start', rowHtml: SERVER_ROW });

        expect(document.querySelectorAll('#mobile-session-select option')).toHaveLength(2);
    });

    test('adds no row, and nothing that is not the server\'s, when the frame carried none', () => {
        showNewSessionInRail(9, { name: 'Fresh start' });

        expect(document.querySelectorAll('#session-list > li')).toHaveLength(1);
        expect(window.applyLocalTimes).not.toHaveBeenCalled();
    });

    test('names the date the rail shows when the delete control is pressed', async () => {
        showNewSessionInRail(9, { name: 'Fresh start', rowHtml: SERVER_ROW });
        confirm.mockClear();
        confirm.mockReturnValue(false);

        document.getElementById('session-delete-9').click();
        await Promise.resolve();

        expect(confirm).toHaveBeenCalledWith(
            `Delete "Fresh start", started Oct 15, 2026 UTC? This can't be undone.`
        );
    });
});

/**
 * The mode control changes the mode in place (issue #89). It posted an
 * ordinary form the server answered with a redirect, a reload that threw
 * away the earlier turns the reader had loaded. It posts the same form as a
 * script caller now and paints the page from the mode the server stored,
 * from the words each option carries.
 */
describe('switching the mode (issues #372, #89)', () => {
    let chatModule;

    function setUpModeControl(sessionIdValue) {
        const chatConfig = JSON.stringify({
            streamUrl: '/tarmar/gm/stream/',
            saveUrl: '/tarmar/gm/save/',
            deleteUrl: '/tarmar/gm/delete/',
            sessionId: sessionIdValue,
            mode: 'aide',
        });
        document.body.innerHTML = `
            <script id="chat-config" type="application/json">${chatConfig}<\/script>
            <div class="gm-mode-indicator" id="gm-mode-indicator">
                <span class="gm-mode-name">GM aide</span>
                <span class="gm-mode-note">Answers what you ask and stops.</span>
                <form class="gm-mode-switch" method="post" action="/tarmar/gm/mode/">
                    <input type="hidden" name="csrfmiddlewaretoken" value="form-token">
                    <select id="gm-mode-select" name="mode">
                        <option value="play" data-description="Runs the game for you." data-placeholder="What do you do?" data-opening="Say what you'd like to do.">Game Master</option>
                        <option value="aide" data-description="Answers what you ask and stops." data-placeholder="Ask the aide" data-opening="Ask a rules question." selected>GM aide</option>
                    </select>
                </form>
            </div>
            <div id="messages">
                <div class="empty-state"><p id="gm-mode-opening">Ask a rules question.</p></div>
                <div class="message user" data-history-turn="0" id="loaded-earlier"></div>
            </div>
            <textarea id="user-input" placeholder="Ask the aide"></textarea>
            <button id="send-btn"></button>
        `;
        jest.resetModules();
        global.marked = require('../marked.min.js').marked;
        global.DOMPurify = require('../dompurify.min.js');
        chatModule = require('../chat.js');
    }

    function choose(mode) {
        const select = document.getElementById('gm-mode-select');
        select.value = mode;
        select.dispatchEvent(new Event('change'));
    }

    async function settle() {
        for (let tick = 0; tick < 6; tick += 1) {
            await new Promise((resolve) => setTimeout(resolve, 0));
        }
    }

    beforeEach(() => {
        fetch.mockReset();
        window.history.replaceState(null, '', '/tarmar/gm/?session=7');
    });

    test('posts the form for the session and paints the stored mode without a reload', async () => {
        // The form carries no session id, as one rendered before this page's
        // first message made the session: the id is the one the page knows.
        setUpModeControl(7);
        const earlier = document.getElementById('loaded-earlier');
        fetch.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ ok: true, mode: 'play' }) });

        choose('play');
        await settle();

        const [url, options] = fetch.mock.calls[0];
        expect(url).toBe('http://localhost/tarmar/gm/mode/');
        expect(options.method).toBe('POST');
        expect(options.headers['X-Requested-With']).toBe('XMLHttpRequest');
        expect(options.body.get('mode')).toBe('play');
        expect(options.body.get('session_id')).toBe('7');
        expect(document.querySelector('.gm-mode-name').textContent).toBe('Game Master');
        expect(document.querySelector('.gm-mode-note').textContent).toBe('Runs the game for you.');
        expect(document.getElementById('user-input').placeholder).toBe('What do you do?');
        expect(document.getElementById('gm-mode-opening').textContent).toBe('Say what you\'d like to do.');
        expect(document.getElementById('gm-mode-select').disabled).toBe(false);
        expect(earlier.isConnected).toBe(true);
        expect(pageNavigation.go).not.toHaveBeenCalled();
        expect(pageNavigation.reload).not.toHaveBeenCalled();
        expect(window.location.search).toBe('?session=7');
    });

    test('with no session yet the choice rides in the address', async () => {
        setUpModeControl(null);
        window.history.replaceState(null, '', '/tarmar/gm/');
        fetch.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ ok: true, mode: 'play' }) });

        choose('play');
        await settle();

        expect(fetch.mock.calls[0][1].body.has('session_id')).toBe(false);
        // No session yet: the choice rides in the address, as the redirect put it.
        expect(window.location.search).toBe('?mode=play');
    });

    test('the mode a turn creating the session sends is the one now shown', async () => {
        setUpModeControl(null);
        window.history.replaceState(null, '', '/tarmar/gm/');
        fetch.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ ok: true, mode: 'play' }) });
        choose('play');
        await settle();
        fetch.mockRejectedValueOnce(new TypeError('offline'));

        await chatModule.streamMessage({ message: 'Hello' });

        expect(JSON.parse(fetch.mock.calls[1][1].body).mode).toBe('play');
    });

    test('a refusal says so and puts back the mode in force', async () => {
        setUpModeControl(7);
        fetch.mockResolvedValueOnce({
            ok: false, status: 404, statusText: 'Not Found',
            json: async () => ({ error: 'GM session not found' }),
        });

        choose('play');
        await settle();

        expect(alert).toHaveBeenCalledWith('The mode could not be changed: GM session not found');
        expect(document.getElementById('gm-mode-select').value).toBe('aide');
        expect(document.querySelector('.gm-mode-name').textContent).toBe('GM aide');
        expect(document.getElementById('user-input').placeholder).toBe('Ask the aide');
        expect(pageNavigation.reload).not.toHaveBeenCalled();
    });
});

/**
 * The conversation as stored, put in place (issue #89): the turns from the
 * last one the page holds, in place of that turn and of what the page wrote
 * live after it; what it wrote after asking stays below.
 */
describe('showConversationAsStored (#89)', () => {
    let chatModule;

    function turn(number, said) {
        return `<div class="message user" data-history-turn="${number}"><div class="message-content">${said}</div></div>`;
    }

    beforeEach(() => {
        fetch.mockReset();
        const chatConfig = JSON.stringify({
            streamUrl: '/tarmar/gm/stream/',
            saveUrl: '/tarmar/gm/save/',
            deleteUrl: '/tarmar/gm/delete/',
            historyUrlTemplate: '/tarmar/gm/sessions/{session_id}/history/',
            sessionId: 7,
            mode: 'play',
        });
        document.body.innerHTML = `
            <script id="chat-config" type="application/json">${chatConfig}<\/script>
            <div id="messages">
                <div data-earlier id="control"><button data-earlier-url="/x?before=0">Show earlier</button></div>
                ${turn(1, 'one')}${turn(2, 'two')}
                <div class="message user" id="live">typed live</div>
            </div>
            <input id="user-input">
            <button id="send-btn"></button>
        `;
        jest.resetModules();
        global.marked = require('../marked.min.js').marked;
        global.DOMPurify = require('../dompurify.min.js');
        chatModule = require('../chat.js');
    });

    test('replaces the last turn and what followed it, keeping the control and the turns above', async () => {
        const control = document.getElementById('control');
        const first = document.querySelector('[data-history-turn="1"]');
        fetch.mockResolvedValueOnce({
            ok: true, status: 200,
            text: async () => `${turn(2, 'two')}<div class="message assistant" data-history-turn="2">`
                + '<div class="message-content" data-markdown>**Bold** reply</div></div>',
        });

        await expect(chatModule.showConversationAsStored()).resolves.toBe(true);

        expect(fetch.mock.calls[0][0]).toBe('/tarmar/gm/sessions/7/history/?since=2');
        const messages = document.getElementById('messages');
        expect(messages.firstElementChild).toBe(control);
        expect(first.isConnected).toBe(true);
        expect(document.getElementById('live')).toBeNull();
        expect(messages.querySelectorAll('[data-history-turn="2"]')).toHaveLength(2);
        // Replies render as on a load, through the sanitizing renderer.
        expect(messages.querySelector('strong').textContent).toBe('Bold');
    });

    test('what the page writes while it asks stays below what comes back', async () => {
        let answer;
        fetch.mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }));

        const shown = chatModule.showConversationAsStored();
        const later = document.createElement('div');
        later.id = 'later';
        document.getElementById('messages').append(later);
        answer({ ok: true, status: 200, text: async () => turn(2, 'two') + turn(3, 'three') });
        await shown;

        const messages = document.getElementById('messages');
        expect(messages.lastElementChild).toBe(later);
        expect(document.getElementById('live')).toBeNull();
        expect(messages.querySelector('[data-history-turn="3"]')).not.toBeNull();
    });

    test('a turn sent meanwhile stays when what was last has gone', async () => {
        // The element last when the page asked (here the live message) is
        // taken away before the answer comes, and a turn is sent meanwhile.
        let answer;
        fetch.mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }));

        const shown = chatModule.showConversationAsStored();
        document.getElementById('live').remove();
        const sent = document.createElement('div');
        sent.id = 'sent-meanwhile';
        document.getElementById('messages').append(sent);
        answer({ ok: true, status: 200, text: async () => turn(2, 'two') + turn(3, 'three') });
        await shown;

        const messages = document.getElementById('messages');
        expect(messages.lastElementChild).toBe(sent);
        expect(messages.querySelector('[data-history-turn="3"]').nextElementSibling).toBe(sent);
        expect(messages.querySelectorAll('[data-history-turn="2"]')).toHaveLength(1);
    });

    test('an earlier page loaded meanwhile stays above what comes back', async () => {
        let answer;
        fetch.mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }));

        const shown = chatModule.showConversationAsStored();
        const control = document.getElementById('control');
        control.insertAdjacentHTML('afterend', turn(0, 'zero'));
        control.remove();
        answer({ ok: true, status: 200, text: async () => turn(2, 'two') });
        await shown;

        const turns = Array.from(document.getElementById('messages').children)
            .map((child) => child.dataset.historyTurn);
        expect(turns).toEqual(['0', '1', '2']);
    });

    test('a failure leaves the page as it was', async () => {
        fetch.mockResolvedValueOnce({ ok: false, status: 502, statusText: 'Bad Gateway' });

        await expect(chatModule.showConversationAsStored()).rejects.toThrow('Bad Gateway');

        expect(document.getElementById('live')).not.toBeNull();
        expect(document.querySelectorAll('[data-history-turn]')).toHaveLength(2);
    });
});
