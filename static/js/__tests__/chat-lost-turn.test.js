/**
 * A GM turn the page lost sight of still arrives (issue #515).
 *
 * The server runs a started turn to the end whether or not the page is
 * listening. When the stream drops after the server named the turn, the page
 * says the turn is still running, asks after it, and shows the session once
 * it is saved. A page loaded while a turn runs does the same.
 */

function setUpDom(config = {}) {
    const chatConfig = JSON.stringify({
        streamUrl: '/tarmar/gm/stream/',
        saveUrl: '/tarmar/gm/save/',
        deleteUrl: '/tarmar/gm/delete/',
        turnUrlTemplate: '/tarmar/gm/turns/{turn_id}/',
        turnPollMs: 0,
        sessionId: 7,
        mode: 'play',
        ...config,
    });
    document.body.innerHTML = `
        <script id="chat-config" type="application/json">${chatConfig}<\/script>
        <div id="messages"></div>
        <p id="gm-status" hidden></p>
        <p id="gm-turn-running" hidden></p>
        <input id="user-input">
        <button id="send-btn"></button>
    `;
}

function loadChat(config) {
    jest.resetModules();
    setUpDom(config);
    global.marked = require('../marked.min.js').marked;
    global.DOMPurify = require('../dompurify.min.js');
    return require('../chat.js');
}

/** A stream response that delivers `chunks`, then fails as Safari does. */
function droppedStream(chunks, turnId) {
    const encoder = new TextEncoder();
    let index = 0;
    return {
        ok: true,
        status: 200,
        headers: { get: (name) => (name === 'X-GM-Turn' ? turnId : null) },
        body: {
            getReader: () => ({
                read: () => {
                    if (index < chunks.length) {
                        return Promise.resolve({ done: false, value: encoder.encode(chunks[index++]) });
                    }
                    return Promise.reject(new TypeError('Load failed'));
                },
            }),
        },
    };
}

function turnState(data) {
    return { ok: true, status: 200, json: () => Promise.resolve(data) };
}

async function settle() {
    for (let tick = 0; tick < 20; tick += 1) {
        await new Promise((resolve) => setTimeout(resolve, 0));
    }
}

beforeEach(() => {
    fetch.mockReset();
    alert.mockClear();
    window.location.pathname = '/tarmar/gm/';
    window.location.href = undefined;
});

describe('a stream lost after the turn started', () => {
    test('says the turn is still running, not that it failed', async () => {
        const chat = loadChat();
        let finished = false;
        fetch.mockResolvedValueOnce(droppedStream(['data: {"token":"The crypt"}\n\n'], '41'));
        // Running until the assertions are done, then finished, so the page's
        // wait ends here instead of polling into the next test.
        fetch.mockImplementation(() => Promise.resolve(
            turnState({ state: finished ? 'finished' : 'running', session_id: 7 })));

        document.getElementById('user-input').value = 'I search the crypt';
        chat.handleSubmit({ preventDefault() {} });
        await settle();

        const line = document.getElementById('gm-turn-running');
        expect(line.hidden).toBe(false);
        expect(line.textContent).toContain('your turn is still running');
        expect(line.textContent).toContain('The reply will appear here');
        expect(alert).not.toHaveBeenCalled();
        // The player's message stays in the transcript and is not handed back.
        expect(document.querySelector('.message.user').textContent).toContain('I search the crypt');
        expect(document.getElementById('user-input').value).toBe('');
        // Nothing can be sent into the session while the turn runs.
        expect(document.getElementById('send-btn').disabled).toBe(true);
        expect(fetch.mock.calls[1][0]).toBe('/tarmar/gm/turns/41/');

        finished = true;
        await settle();
        expect(window.location.href).toBe('/tarmar/gm/?session=7');
    });

    test('shows the session once the turn has finished', async () => {
        const chat = loadChat();
        fetch.mockResolvedValueOnce(droppedStream([], '41'));
        fetch.mockResolvedValueOnce(turnState({ state: 'running', session_id: 7 }));
        fetch.mockResolvedValueOnce(turnState({ state: 'finished', session_id: 7 }));

        await chat.streamMessage({ session_id: 7, message: 'I search the crypt' });
        await settle();

        expect(window.location.href).toBe('/tarmar/gm/?session=7');
        expect(document.getElementById('gm-turn-running').hidden).toBe(true);
    });

    test('a turn that failed on the server hands the message back', async () => {
        const chat = loadChat();
        document.getElementById('user-input').value = 'I search the crypt';
        fetch.mockResolvedValueOnce(droppedStream([], '41'));
        fetch.mockResolvedValueOnce(turnState({ state: 'failed', session_id: 7, error: 'the model went away' }));

        chat.handleSubmit({ preventDefault() {} });
        await settle();

        expect(alert.mock.calls[0][0]).toContain('That turn did not finish: the model went away.');
        expect(document.getElementById('user-input').value).toBe('I search the crypt');
        expect(document.getElementById('send-btn').disabled).toBe(false);
        expect(document.getElementById('gm-turn-running').hidden).toBe(true);
    });

    test('a stream that never started is still a broken connection', async () => {
        const chat = loadChat();
        fetch.mockRejectedValueOnce(new TypeError('Load failed'));

        await chat.streamMessage({ session_id: 7, message: 'I search the crypt' });

        expect(alert.mock.calls[0][0]).toContain('The connection to the Game Master broke');
        expect(document.getElementById('gm-turn-running').hidden).toBe(true);
    });
});

describe('a page loaded while its turn runs', () => {
    test('says so plainly, then picks up the finished turn', async () => {
        fetch.mockResolvedValueOnce(turnState({ state: 'running', session_id: 7 }));
        fetch.mockResolvedValueOnce(turnState({ state: 'finished', session_id: 7 }));

        loadChat({ runningTurn: { id: 52, message: '5 years of campaigning' } });

        const line = document.getElementById('gm-turn-running');
        expect(line.hidden).toBe(false);
        expect(line.textContent).toContain('still working on your last message ("5 years of campaigning")');
        expect(document.getElementById('send-btn').disabled).toBe(true);

        await settle();

        expect(fetch.mock.calls[0][0]).toBe('/tarmar/gm/turns/52/');
        expect(window.location.href).toBe('/tarmar/gm/?session=7');
    });

    test('keeps asking when the network is still down', async () => {
        fetch.mockRejectedValueOnce(new TypeError('Load failed'));
        fetch.mockResolvedValueOnce({ ok: false, status: 502, json: () => Promise.resolve({}) });
        fetch.mockResolvedValueOnce(turnState({ state: 'finished', session_id: 7 }));
        const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});

        loadChat({ runningTurn: { id: 52, message: 'Hi' } });
        await settle();

        expect(warn).toHaveBeenCalledTimes(2);
        expect(window.location.href).toBe('/tarmar/gm/?session=7');
        warn.mockRestore();
    });
});

describe('a blank page and a session-less turn (issue #650)', () => {
    beforeEach(() => {
        window.sessionStorage.clear();
    });

    test('a blank page with no turn of its own is usable, not locked', async () => {
        loadChat({ sessionId: null, runningTurn: null });
        await settle();

        expect(fetch).not.toHaveBeenCalled();
        expect(document.getElementById('send-btn').disabled).toBe(false);
        expect(document.getElementById('gm-turn-running').hidden).toBe(true);
    });

    test('the tab that sent the turn remembers it and waits for it on reload', async () => {
        const chat = loadChat({ sessionId: null, runningTurn: null });
        fetch.mockResolvedValueOnce(droppedStream(['data: {"token":"The box"}\n\n'], '41'));
        fetch.mockImplementation(() => Promise.resolve(turnState({ state: 'running', session_id: null })));
        document.getElementById('user-input').value = 'I open the iron box';
        chat.handleSubmit({ preventDefault() {} });
        await settle();

        expect(JSON.parse(window.sessionStorage.getItem('gmOwnSessionlessTurn')))
            .toEqual({ id: '41', message: 'I open the iron box' });

        // The same tab, reloaded: the server offers nothing, the tab does.
        fetch.mockReset();
        fetch.mockImplementation(() => Promise.resolve(turnState({ state: 'running', session_id: null })));
        loadChat({ sessionId: null, runningTurn: null });
        await settle();

        expect(document.getElementById('gm-turn-running')).toHaveProperty('hidden', false);
        expect(document.getElementById('gm-turn-running').textContent)
            .toContain('I open the iron box');
        expect(document.getElementById('send-btn').disabled).toBe(true);
    });

    test('a different tab is not locked by it and is not sent to its session', async () => {
        // What the other tab stored is its own; this one has nothing.
        window.sessionStorage.clear();
        loadChat({ sessionId: null, runningTurn: null });
        await settle();

        expect(document.getElementById('send-btn').disabled).toBe(false);
        expect(window.location.href).toBeUndefined();
    });

    test('a tab coming back to a turn that already landed is taken to its session', async () => {
        window.sessionStorage.setItem('gmOwnSessionlessTurn',
                                      JSON.stringify({ id: '41', message: 'I open the iron box' }));
        fetch.mockImplementation(() => Promise.resolve(turnState({ state: 'finished', session_id: 12 })));
        loadChat({ sessionId: null, runningTurn: null });
        await settle();

        expect(window.location.href).toBe('/tarmar/gm/?session=12');
        expect(window.sessionStorage.getItem('gmOwnSessionlessTurn')).toBeNull();
    });

    test('a tab coming back to a turn that is gone recovers instead of locking', async () => {
        window.sessionStorage.setItem('gmOwnSessionlessTurn',
                                      JSON.stringify({ id: '41', message: 'I open the iron box' }));
        fetch.mockImplementation(() => Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) }));
        loadChat({ sessionId: null, runningTurn: null });
        await settle();

        expect(document.getElementById('send-btn').disabled).toBe(false);
        expect(window.sessionStorage.getItem('gmOwnSessionlessTurn')).toBeNull();
        expect(window.location.href).toBeUndefined();
    });

    test('a turn that has made its session is the server\'s to resume, not the tab\'s', async () => {
        const chat = loadChat({ sessionId: null, runningTurn: null });
        fetch.mockResolvedValueOnce(droppedStream(
            ['data: {"session_id":12}\n\n', 'data: {"token":"The box"}\n\n'], '41'));
        fetch.mockImplementation(() => Promise.resolve(turnState({ state: 'running', session_id: 12 })));
        document.getElementById('user-input').value = 'I open the iron box';
        chat.handleSubmit({ preventDefault() {} });
        await settle();

        expect(window.sessionStorage.getItem('gmOwnSessionlessTurn')).toBeNull();
    });
});

describe('a remembered turn the page cannot reach (issue #650)', () => {
    beforeEach(() => {
        window.sessionStorage.clear();
    });

    test('a network failure leaves the page usable and keeps the turn for later', async () => {
        window.sessionStorage.setItem('gmOwnSessionlessTurn',
                                      JSON.stringify({ id: '41', message: 'I open the iron box' }));
        fetch.mockImplementation(() => Promise.reject(new TypeError('Load failed')));
        loadChat({ sessionId: null, runningTurn: null });
        await settle();

        expect(document.getElementById('send-btn').disabled).toBe(false);
        expect(JSON.parse(window.sessionStorage.getItem('gmOwnSessionlessTurn')))
            .toEqual({ id: '41', message: 'I open the iron box' });
    });
});
