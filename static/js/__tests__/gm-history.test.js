/**
 * Unit tests for gm-history.js: the GM page's "Show earlier" controls (issue
 * #972). The server renders the control and each page; these check that the
 * page above replaces the control, that what the reader was looking at keeps
 * its place on screen, that focus moves on, and that a failure stays beside
 * the control (no transient UI).
 */

// The vendored build chat.js sanitizes with; the stand-in below uses it too.
const DOMPurify = require('../dompurify.min.js');

let history;

function htmlResponse(body, { ok = true, status = 200, statusText = 'OK' } = {}) {
    return Promise.resolve({ ok, status, statusText, text: () => Promise.resolve(body) });
}

function setUpDom() {
    document.body.innerHTML = `
        <div class="chat-messages" id="messages" style="overflow-y: auto">
            <div class="gm-history-earlier" data-earlier>
                <p>2 earlier turns not shown.</p>
                <button type="button" id="earlier" data-earlier-url="/tarmar/gm/sessions/3/history/?before=2">Show earlier turns</button>
                <p data-earlier-error role="alert" hidden></p>
            </div>
            <div class="message user" id="first-shown"><div class="message-content">Player move 3.</div></div>
        </div>
        <ul id="resolved">
            <li class="gm-history-earlier" data-earlier>
                <button type="button" id="earlier-threads" data-earlier-url="/tarmar/gm/sessions/3/threads/resolved/?before=9">Show earlier threads</button>
                <span data-earlier-error role="alert" hidden></span>
            </li>
            <li data-thread-id="9">Settled matter 3</li>
        </ul>
    `;
}

/** Give the message list a scroll height that grows with what it holds. */
function measureMessages(messages) {
    Object.defineProperty(messages, 'scrollHeight', {
        configurable: true,
        get: () => messages.querySelectorAll('.message').length * 100,
    });
    Object.defineProperty(messages, 'clientHeight', { configurable: true, get: () => 50 });
    // jsdom lays nothing out, so scrollTop is a plain number here.
    Object.defineProperty(messages, 'scrollTop', { configurable: true, writable: true, value: 0 });
}

beforeEach(() => {
    jest.resetModules();
    global.fetch.mockReset();
    setUpDom();
    // Stands in for chat.js's renderMarkdown, keeping its contract: what it
    // returns has been through DOMPurify, as the real one's always has.
    window.renderMarkdown = (text) => DOMPurify.sanitize(`<p>${text.toUpperCase()}</p>`);
    history = require('../gm-history.js');
});

afterEach(() => {
    delete window.renderMarkdown;
});

test('the page above replaces the control, its replies rendered, above what was shown', async () => {
    global.fetch.mockReturnValue(htmlResponse(`
        <div class="message user"><div class="message-content">Player move 1.</div></div>
        <div class="message assistant"><div class="message-content" data-markdown>reply one</div></div>
    `));

    await expect(history.loadEarlier(document.getElementById('earlier'))).resolves.toBe(true);

    expect(global.fetch).toHaveBeenCalledWith(
        '/tarmar/gm/sessions/3/history/?before=2', { headers: { Accept: 'text/html' } },
    );
    const messages = document.getElementById('messages');
    expect(messages.querySelector('[data-earlier]')).toBeNull();
    const texts = Array.from(messages.querySelectorAll('.message-content')).map((node) => node.textContent);
    expect(texts).toEqual(['Player move 1.', 'REPLY ONE', 'Player move 3.']);
    expect(messages.querySelector('[data-markdown]').innerHTML).toBe('<p>REPLY ONE</p>');
});

test('what the reader was looking at keeps its place on screen', async () => {
    const messages = document.getElementById('messages');
    measureMessages(messages);
    global.fetch.mockReturnValue(htmlResponse(
        '<div class="message user"><div class="message-content">1</div></div>'
        + '<div class="message assistant"><div class="message-content">1</div></div>',
    ));

    await history.loadEarlier(document.getElementById('earlier'));

    // Two messages of 100 went in above: the view moved down by 200.
    expect(messages.scrollTop).toBe(200);
});

test('focus moves to the next control when there are more pages', async () => {
    global.fetch.mockReturnValue(htmlResponse(`
        <div class="gm-history-earlier" data-earlier>
            <button type="button" id="next" data-earlier-url="/tarmar/gm/sessions/3/history/?before=1">Show earlier turns</button>
            <p data-earlier-error role="alert" hidden></p>
        </div>
        <div class="message user"><div class="message-content">Player move 2.</div></div>
    `));

    await history.loadEarlier(document.getElementById('earlier'));

    expect(document.activeElement.id).toBe('next');
    expect(document.querySelectorAll('#messages [data-earlier]')).toHaveLength(1);
});

test('focus moves to the first entry loaded when nothing is left above', async () => {
    global.fetch.mockReturnValue(htmlResponse(
        '<div class="message user" id="loaded"><div class="message-content">Player move 1.</div></div>',
    ));

    await history.loadEarlier(document.getElementById('earlier'));

    expect(document.activeElement.id).toBe('loaded');
    expect(document.activeElement.getAttribute('tabindex')).toBe('-1');
});

test('a list control is replaced by list items', async () => {
    global.fetch.mockReturnValue(htmlResponse(
        '<li data-thread-id="7">Settled matter 1</li><li data-thread-id="8">Settled matter 2</li>',
    ));

    await history.loadEarlier(document.getElementById('earlier-threads'));

    const items = Array.from(document.querySelectorAll('#resolved > li')).map((item) => item.dataset.threadId);
    expect(items).toEqual(['7', '8', '9']);
});

test('a refusal stays beside the control, which stays to try again', async () => {
    global.fetch.mockReturnValue(htmlResponse('', { ok: false, status: 404, statusText: 'Not Found' }));
    const button = document.getElementById('earlier');

    await expect(history.loadEarlier(button)).resolves.toBe(false);

    const error = document.querySelector('#messages [data-earlier-error]');
    expect(error.hidden).toBe(false);
    expect(error.textContent).toBe('The earlier page could not be loaded: Not Found');
    expect(button.isConnected).toBe(true);
    expect(button.disabled).toBe(false);

    global.fetch.mockReturnValue(htmlResponse('<div class="message user"><div class="message-content">1</div></div>'));
    await expect(history.loadEarlier(button)).resolves.toBe(true);
});

test('a network failure says so, and the next attempt clears it', async () => {
    global.fetch.mockReturnValue(Promise.reject(new Error('offline')));
    const button = document.getElementById('earlier');

    await history.loadEarlier(button);

    const error = document.querySelector('#messages [data-earlier-error]');
    expect(error.textContent).toBe('The earlier page could not be loaded: offline');

    let resolveFetch;
    global.fetch.mockReturnValue(new Promise((resolve) => { resolveFetch = resolve; }));
    const pending = history.loadEarlier(button);
    expect(error.hidden).toBe(true);
    // A second press while one is loading asks nothing more.
    await expect(history.loadEarlier(button)).resolves.toBe(false);
    expect(global.fetch).toHaveBeenCalledTimes(2);
    resolveFetch({ ok: true, status: 200, statusText: 'OK', text: () => Promise.resolve('') });
    await pending;
});

test('the control works from the keyboard: a click on the button, delegated', async () => {
    global.fetch.mockReturnValue(htmlResponse('<div class="message user"><div class="message-content">1</div></div>'));
    history.init();

    document.getElementById('earlier').click();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(global.fetch).toHaveBeenCalledTimes(1);
});

/**
 * The Campaign tab is fetched again while a turn runs, carrying the earliest
 * page loaded (issue #982). When that refetch lands while "Show earlier
 * threads" is still on its way, the control pressed has been replaced by the
 * server's copy of it; the page must go in place of that copy, not into the
 * control that left the page (issue #14).
 */
describe('a refetch of the Campaign tab lands while an earlier page is on its way (#14)', () => {
    const EARLIER_URL = '/tarmar/gm/sessions/12/threads/resolved/?before=31';
    const CAMPAIGN_URL = '/tarmar/gm/sessions/12/campaign/?resolved_from=31';
    /** The resolved threads as the server renders them from thread 31 on. */
    const RESOLVED = `
        <p id="gm-panel-campaign-error" role="alert" hidden></p>
        <details class="gm-campaign-resolved" open>
            <summary>4 resolved</summary>
            <ul>
                <li class="gm-history-earlier" data-earlier>
                    <button type="button" class="show-earlier" data-focus-key="earlier-resolved" data-earlier-url="${EARLIER_URL}">Show earlier threads</button>
                    <span data-earlier-error role="alert" hidden></span>
                </li>
                <li data-thread-id="31">thread 31</li>
                <li data-thread-id="32">thread 32</li>
            </ul>
        </details>`;
    const EARLIER_PAGE = '<li data-thread-id="29">thread 29</li><li data-thread-id="30">thread 30</li>';

    let panel;
    let answerEarlier;

    /**
     * The GM page's Campaign tab on session 12, the tab's refetch answered at
     * once with ``refetched``, the earlier page held until answerEarlier() is
     * called, and any other page answered from ``pages`` by its URL.
     */
    function setUpTab({ refetched = RESOLVED, pages = {} } = {}) {
        const config = JSON.stringify({
            campaignPanelUrlTemplate: '/tarmar/gm/sessions/{session_id}/campaign/',
            sessionId: 12,
        });
        document.body.innerHTML = `
            <script id="gm-panel-config" type="application/json">${config}<\/script>
            <section id="gm-panel-campaign">${RESOLVED}</section>
            <input id="user-input">`;
        const earlier = new Promise((resolve) => { answerEarlier = resolve; });
        global.fetch.mockImplementation((url) => {
            if (url === EARLIER_URL) return earlier;
            return htmlResponse(url in pages ? pages[url] : refetched);
        });
        panel = require('../gm-panel-characters.js');
    }

    function shownThreads() {
        return Array.from(document.querySelectorAll('#gm-panel-campaign li[data-thread-id]'))
            .map((item) => item.dataset.threadId);
    }

    afterEach(() => { delete window.gmPanelDrafts; delete window.gmPanelCharacters; });

    test('the page goes in place of the control the refetch put there', async () => {
        setUpTab();
        const pressed = document.querySelector('.show-earlier');

        const loading = history.loadEarlier(pressed);
        await panel.refreshPanel();
        // The refetch asked from the same thread and replaced the control.
        expect(global.fetch.mock.calls.map(([url]) => url)).toContain(CAMPAIGN_URL);
        expect(pressed.isConnected).toBe(false);
        answerEarlier(htmlResponse(EARLIER_PAGE));

        await expect(loading).resolves.toBe(true);
        expect(shownThreads()).toEqual(['29', '30', '31', '32']);
        expect(document.querySelector('#gm-panel-campaign [data-earlier]')).toBeNull();
    });

    test('a failure is said beside the control on the page', async () => {
        setUpTab();

        const loading = history.loadEarlier(document.querySelector('.show-earlier'));
        await panel.refreshPanel();
        answerEarlier(htmlResponse('', { ok: false, status: 500, statusText: 'Server Error' }));

        await expect(loading).resolves.toBe(false);
        const error = document.querySelector('#gm-panel-campaign [data-earlier-error]');
        expect(error.hidden).toBe(false);
        expect(error.textContent).toBe('The earlier page could not be loaded: Server Error');
    });

    test('pressing the new control meanwhile asks once and puts the page in once', async () => {
        setUpTab();

        const first = history.loadEarlier(document.querySelector('.show-earlier'));
        await panel.refreshPanel();
        const second = history.loadEarlier(document.querySelector('.show-earlier'));
        answerEarlier(htmlResponse(EARLIER_PAGE));

        await expect(first).resolves.toBe(true);
        await expect(second).resolves.toBe(true);
        expect(global.fetch.mock.calls.filter(([url]) => url === EARLIER_URL)).toHaveLength(1);
        expect(shownThreads()).toEqual(['29', '30', '31', '32']);
    });

    test('a list fetched again from another thread loads the page its control asks for', async () => {
        // Thread 31 was reopened meanwhile: the list now starts at 32, and
        // its control asks for the page above 32.
        const fromThirtyTwo = '/tarmar/gm/sessions/12/threads/resolved/?before=32';
        setUpTab({
            refetched: RESOLVED
                .replace(EARLIER_URL, fromThirtyTwo)
                .replace('<li data-thread-id="31">thread 31</li>', ''),
            pages: {
                [fromThirtyTwo]: '<li data-thread-id="29">thread 29</li><li data-thread-id="30">thread 30</li>',
            },
        });

        const loading = history.loadEarlier(document.querySelector('.show-earlier'));
        await panel.refreshPanel();
        answerEarlier(htmlResponse('<li data-thread-id="29">stale</li><li data-thread-id="30">stale</li>'));

        await expect(loading).resolves.toBe(true);
        expect(global.fetch.mock.calls.map(([url]) => url)).toContain(fromThirtyTwo);
        expect(shownThreads()).toEqual(['29', '30', '32']);
        expect(document.getElementById('gm-panel-campaign').textContent).not.toContain('stale');
    });

    test('a page handed on to the new control is not what a later ask for the first page waits on', async () => {
        const fromThirtyTwo = '/tarmar/gm/sessions/12/threads/resolved/?before=32';
        setUpTab({
            refetched: RESOLVED
                .replace(EARLIER_URL, fromThirtyTwo)
                .replace('<li data-thread-id="31">thread 31</li>', ''),
        });
        // The page above 32 is held, so the handed-on load is still out.
        const answerOthers = global.fetch.getMockImplementation();
        let answerThirtyTwo;
        global.fetch.mockImplementation((url) => (url === fromThirtyTwo
            ? new Promise((resolve) => { answerThirtyTwo = resolve; })
            : answerOthers(url)));

        const first = history.loadEarlier(document.querySelector('.show-earlier'));
        await panel.refreshPanel();
        answerEarlier(htmlResponse('<li data-thread-id="29">stale</li>'));
        for (let tick = 0; tick < 6; tick += 1) await Promise.resolve();
        expect(global.fetch.mock.calls.map(([url]) => url)).toContain(fromThirtyTwo);

        // A control asking for the first page again, elsewhere on the page.
        document.body.insertAdjacentHTML('beforeend', `
            <ul id="other"><li data-earlier>
                <button type="button" id="again" data-earlier-url="${EARLIER_URL}">Show earlier threads</button>
                <span data-earlier-error role="alert" hidden></span>
            </li></ul>`);
        const again = history.loadEarlier(document.getElementById('again'));

        expect(global.fetch.mock.calls.filter(([url]) => url === EARLIER_URL)).toHaveLength(2);
        answerThirtyTwo(htmlResponse('<li data-thread-id="30">thread 30</li>'));
        await expect(first).resolves.toBe(true);
        await expect(again).resolves.toBe(true);
        expect(document.getElementById('other').textContent).toContain('stale');
    });

    test('a page landing after the reader moved on leaves the focus where it is', async () => {
        setUpTab();

        const loading = history.loadEarlier(document.querySelector('.show-earlier'));
        await panel.refreshPanel();
        const messageBox = document.getElementById('user-input');
        messageBox.focus();
        answerEarlier(htmlResponse(EARLIER_PAGE));

        await expect(loading).resolves.toBe(true);
        expect(shownThreads()).toEqual(['29', '30', '31', '32']);
        expect(document.activeElement).toBe(messageBox);
    });

    test('a page landing while the focus is still on the control takes it along', async () => {
        setUpTab();

        const loading = history.loadEarlier(document.querySelector('.show-earlier'));
        await panel.refreshPanel();
        // The reader is on the control the refetch put there.
        document.querySelector('.show-earlier').focus();
        answerEarlier(htmlResponse(EARLIER_PAGE));

        await expect(loading).resolves.toBe(true);
        expect(document.activeElement.dataset.threadId).toBe('29');
    });
});
