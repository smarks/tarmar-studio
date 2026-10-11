/**
 * Unit tests for gm-history.js: the GM page's "Show earlier" controls (issue
 * #972). The server renders the control and each page; these check that the
 * page above replaces the control, that what the reader was looking at keeps
 * its place on screen, that focus moves on, and that a failure stays beside
 * the control (no transient UI).
 */

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
    window.renderMarkdown = (text) => `<p>${text.toUpperCase()}</p>`;
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
