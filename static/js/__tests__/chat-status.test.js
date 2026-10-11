/**
 * What the working line says while the GM composes a step (issue #569).
 *
 * "The Game Master is working: writing" used to stay up for a minute at a
 * time, because the server holds a marker's characters back and no token
 * frame goes out while a rules step is being written. The server now sends
 * `status` frames, and this page shows them in place of "writing": the step
 * being prepared, the step that ran, and "thinking" with the seconds the turn
 * has been going once a pass falls quiet.
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

/**
 * A stream whose frames this test sends one at a time.
 *
 * The in-between states are the whole subject, so the test has to be able to
 * look at the page between one frame and the next.
 */
function controlledStream() {
    const encoder = new TextEncoder();
    const waiting = [];
    let resolveRead = null;
    let closed = false;

    const reader = {
        read: () => new Promise((resolve) => {
            if (waiting.length) {
                resolve({ done: false, value: encoder.encode(waiting.shift()) });
            } else if (closed) {
                resolve({ done: true });
            } else {
                resolveRead = resolve;
            }
        }),
    };

    function deliver(text) {
        if (resolveRead) {
            const resolve = resolveRead;
            resolveRead = null;
            resolve({ done: false, value: encoder.encode(text) });
        } else {
            waiting.push(text);
        }
    }

    return {
        response: {
            ok: true,
            status: 200,
            headers: { get: () => null },
            body: { getReader: () => reader },
        },
        send(frame) {
            deliver(`data: ${JSON.stringify(frame)}\n\n`);
        },
        end() {
            closed = true;
            if (resolveRead) {
                const resolve = resolveRead;
                resolveRead = null;
                resolve({ done: true });
            }
        },
    };
}

async function settle() {
    for (let tick = 0; tick < 20; tick += 1) {
        await new Promise((resolve) => setTimeout(resolve, 0));
    }
}

function statusLine() {
    return document.getElementById('gm-status');
}

beforeEach(() => {
    fetch.mockReset();
    alert.mockClear();
    window.history.replaceState(null, '', '/tarmar/gm/');
});

describe('the working line through one turn', () => {
    test('writing, then the step being prepared, then the step, then thinking', async () => {
        const chat = loadChat();
        const stream = controlledStream();
        fetch.mockResolvedValueOnce(stream.response);

        const turn = chat.streamMessage({ session_id: 7, message: 'I test my nerve' });
        await settle();
        expect(statusLine().hidden).toBe(false);
        expect(statusLine().textContent).toBe('The Game Master is working');

        stream.send({ token: 'The warden weighs it. ' });
        await settle();
        expect(statusLine().textContent).toBe('The Game Master is working: writing');

        // The marker is being written now: nothing more will be shown until
        // the step has run, and the line says what the step is.
        stream.send({ status: 'preparing 5 years on the underworld track' });
        await settle();
        expect(statusLine().textContent)
            .toBe('The Game Master is working: preparing 5 years on the underworld track');

        stream.send({ procedure: { label: '5 years on the underworld track', lines: ['⚙️ 5 years'] } });
        await settle();
        expect(statusLine().textContent)
            .toBe('The Game Master is working: 5 years on the underworld track');

        // The next pass is quiet: the turn is alive, and says how long it has
        // been going.
        stream.send({ status: 'thinking', elapsed: 40 });
        await settle();
        expect(statusLine().textContent).toBe('The Game Master is working: thinking, 40s');

        stream.send({ done: true, full_response: 'The warden steps aside.' });
        stream.end();
        await turn;

        expect(statusLine().hidden).toBe(true);
        expect(statusLine().textContent).toBe('');
    });

    test('the seconds never count backwards', async () => {
        const chat = loadChat();
        const stream = controlledStream();
        fetch.mockResolvedValueOnce(stream.response);

        const turn = chat.streamMessage({ session_id: 7, message: 'I wait' });
        await settle();
        stream.send({ status: 'thinking', elapsed: 40 });
        await settle();
        stream.send({ status: 'thinking', elapsed: 12 });
        await settle();

        expect(statusLine().textContent).toBe('The Game Master is working: thinking, 40s');

        stream.send({ status: 'thinking', elapsed: 51 });
        await settle();
        expect(statusLine().textContent).toBe('The Game Master is working: thinking, 51s');

        stream.end();
        await turn;
    });

    test('a status with no seconds on it names the step alone', async () => {
        const chat = loadChat();
        const stream = controlledStream();
        fetch.mockResolvedValueOnce(stream.response);

        const turn = chat.streamMessage({ session_id: 7, message: 'I test my nerve' });
        await settle();
        stream.send({ status: 'thinking', elapsed: 40 });
        await settle();
        stream.send({ status: 'preparing 3d6 for a test of nerve' });
        await settle();

        expect(statusLine().textContent)
            .toBe('The Game Master is working: preparing 3d6 for a test of nerve');

        stream.end();
        await turn;
    });

    test('a status this page does not know is never rendered as markup', async () => {
        const chat = loadChat();
        const stream = controlledStream();
        fetch.mockResolvedValueOnce(stream.response);

        const turn = chat.streamMessage({ session_id: 7, message: 'I test my nerve' });
        await settle();
        stream.send({ status: '<img src=x onerror="alert(1)">preparing something new' });
        await settle();

        expect(statusLine().querySelector('img')).toBeNull();
        expect(statusLine().innerHTML).not.toContain('<img');
        expect(statusLine().textContent).toContain('<img src=x onerror="alert(1)">');
        expect(alert).not.toHaveBeenCalled();

        stream.end();
        await turn;
    });

    test('a turn the budget cut short clears the line and keeps its notices', async () => {
        const chat = loadChat();
        const stream = controlledStream();
        fetch.mockResolvedValueOnce(stream.response);

        const turn = chat.streamMessage({ session_id: 7, message: 'I test my nerve' });
        await settle();
        stream.send({ token: 'The warden weighs it. ' });
        stream.send({ status: 'preparing 3d6 for a test of nerve' });
        await settle();
        stream.send({
            error: 'The Game Master took too long.',
            notices: ['This turn was cut short. What it had already worked out was kept.'],
        });
        await settle();
        await turn;

        expect(statusLine().hidden).toBe(true);
        expect(statusLine().textContent).toBe('');
        const notice = document.querySelector('.grounding-notice');
        expect(notice.textContent).toContain('cut short');
    });
});
