/**
 * Unit tests for gm-panel-battle.js — the Battle tab's fight controls
 * (issue #345).
 *
 * The tab posts to the fight endpoint and reloads the page on the session,
 * so the board is always server-rendered; these tests assert the request
 * each control makes, the tab it brings forward, the navigation that
 * follows, and that a failure lands in the tab's own error line (which
 * stays until the next attempt — no toasts). The board itself is
 * battle.js's and is tested there.
 */

function setUpDom({ sessionId = null, withForm = true, withBattle = false } = {}) {
    const config = JSON.stringify({
        attachUrl: '/tarmar/gm/characters/attach/',
        detachUrl: '/tarmar/gm/characters/detach/',
        sessionId,
    });
    document.body.innerHTML = `
        <script id="gm-panel-config" type="application/json">${config}<\/script>
        <button type="button" id="gm-fight-button">Fight</button>
        <button type="button" id="gm-panel-tab-battle">Battle</button>
        <aside id="gm-panel">
            <section id="gm-panel-battle" data-panel-container="battle" hidden>
                ${withForm ? `
                <form id="gm-fight-form" data-fight-url="/tarmar/gm/fight/">
                    <input type="text" id="gm-fight-foes">
                    <button type="submit" id="gm-fight-start">Fight</button>
                </form>` : ''}
                <p id="gm-panel-battle-status" role="status" hidden></p>
                <p id="gm-panel-battle-error" role="alert" hidden></p>
                ${withBattle ? '<div id="gm-battle" data-battle-id="4"></div>' : ''}
            </section>
        </aside>
    `;
}

const { resolvedJsonResponse: jsonResponse } = require('./test-helpers');

let panelBattle;

function loadTab() {
    jest.resetModules();
    panelBattle = require('../gm-panel-battle.js');
    panelBattle.init();
    return panelBattle;
}

function flushPromises() {
    return new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
    global.fetch.mockReset();
    window.location.pathname = '/tarmar/gm/';
    window.location.search = '';
    window.location.href = '';
    window.gmPanel = {
        activate: jest.fn(),
        setOpen: jest.fn(),
        currentSessionId: (fallback) => {
            const fromUrl = new URLSearchParams(window.location.search).get('session');
            return fromUrl ? Number(fromUrl) : fallback;
        },
    };
});

afterEach(() => {
    delete window.gmPanel;
});

describe('startFight', () => {
    test('posts the named foes with the session and reloads on the fight', async () => {
        setUpDom({ sessionId: 12 });
        loadTab();
        global.fetch.mockReturnValue(jsonResponse({
            ok: true, session_id: 12, battle_id: 4, battle_name: 'Quest: fight',
        }));

        const done = panelBattle.startFight('two gray wolves');
        expect(window.gmPanel.activate).toHaveBeenCalledWith('battle');
        expect(window.gmPanel.setOpen).toHaveBeenCalledWith(true);
        expect(document.getElementById('gm-panel-battle-status').textContent).toBe('Setting the board…');
        expect(document.getElementById('gm-fight-start').disabled).toBe(true);
        expect(document.getElementById('gm-fight-button').disabled).toBe(true);
        expect(await done).toBe(true);

        expect(global.fetch).toHaveBeenCalledTimes(1);
        const [url, options] = global.fetch.mock.calls[0];
        expect(url).toBe('/tarmar/gm/fight/');
        expect(options.method).toBe('POST');
        expect(options.headers['X-CSRFToken']).toBe('test-token');
        expect(JSON.parse(options.body)).toEqual({ session_id: 12, foes: 'two gray wolves' });
        expect(document.getElementById('gm-panel-battle-status').textContent).toContain('Quest: fight');
        expect(window.location.href).toBe('/tarmar/gm/?session=12');
    });

    test('the session in the URL wins over the one the page loaded with', async () => {
        setUpDom({ sessionId: null });
        window.location.search = '?session=31';
        loadTab();
        global.fetch.mockReturnValue(jsonResponse({ ok: true, session_id: 31, battle_id: 1, battle_name: 'x' }));
        await panelBattle.startFight('rat');
        expect(JSON.parse(global.fetch.mock.calls[0][1].body).session_id).toBe(31);
    });

    test('empty foes ask the GM, and say so while waiting', async () => {
        setUpDom({ sessionId: 12 });
        loadTab();
        global.fetch.mockReturnValue(jsonResponse({ ok: true, session_id: 12, battle_id: 4, battle_name: 'x' }));
        const done = panelBattle.startFight('   ');
        expect(document.getElementById('gm-panel-battle-status').textContent).toBe('Asking the GM who you face…');
        await done;
        expect(JSON.parse(global.fetch.mock.calls[0][1].body).foes).toBe('');
    });

    test('with no session nothing is posted and the tab says why', async () => {
        setUpDom({ sessionId: null });
        loadTab();
        expect(await panelBattle.startFight('wolf')).toBe(false);
        expect(global.fetch).not.toHaveBeenCalled();
        const error = document.getElementById('gm-panel-battle-error');
        expect(error.hidden).toBe(false);
        expect(error.textContent).toBe('Attach a character or send a message first.');
        expect(window.location.href).toBe('');
    });

    test('a refusal stays in the tab and the controls come back', async () => {
        setUpDom({ sessionId: 12 });
        loadTab();
        global.fetch.mockReturnValue(jsonResponse(
            { error: 'Attach a living character before starting a fight' },
            { ok: false, status: 400, statusText: 'Bad Request' },
        ));
        expect(await panelBattle.startFight('wolf')).toBe(false);
        const error = document.getElementById('gm-panel-battle-error');
        expect(error.hidden).toBe(false);
        expect(error.textContent).toBe('Attach a living character before starting a fight');
        expect(document.getElementById('gm-panel-battle-status').hidden).toBe(true);
        expect(document.getElementById('gm-fight-start').disabled).toBe(false);
        expect(document.getElementById('gm-fight-foes').disabled).toBe(false);
        expect(window.location.href).toBe('');
    });

    test('a network failure is reported the same way', async () => {
        setUpDom({ sessionId: 12 });
        loadTab();
        global.fetch.mockReturnValue(Promise.reject(new Error('offline')));
        expect(await panelBattle.startFight('wolf')).toBe(false);
        expect(document.getElementById('gm-panel-battle-error').textContent).toBe('Request failed: offline');
    });

    test('a page without the fight form cannot post', async () => {
        setUpDom({ sessionId: 12, withForm: false });
        loadTab();
        expect(await panelBattle.startFight('wolf')).toBe(false);
        expect(global.fetch).not.toHaveBeenCalled();
        expect(document.getElementById('gm-panel-battle-error').textContent).toBe('This page has no fight endpoint.');
    });

    test('works without gm-panel.js loaded', async () => {
        delete window.gmPanel;
        setUpDom({ sessionId: 12 });
        loadTab();
        global.fetch.mockReturnValue(jsonResponse({ ok: true, session_id: 12, battle_id: 4, battle_name: 'x' }));
        expect(await panelBattle.startFight('wolf')).toBe(true);
        expect(panelBattle.currentSessionId()).toBe(12);
    });
});

describe('controls', () => {
    test('submitting the form starts a fight with the typed foes', async () => {
        setUpDom({ sessionId: 12 });
        loadTab();
        global.fetch.mockReturnValue(jsonResponse({ ok: true, session_id: 12, battle_id: 4, battle_name: 'x' }));
        document.getElementById('gm-fight-foes').value = 'a dire wolf';
        const event = new Event('submit', { cancelable: true });
        document.getElementById('gm-fight-form').dispatchEvent(event);
        expect(event.defaultPrevented).toBe(true);
        await flushPromises();
        expect(JSON.parse(global.fetch.mock.calls[0][1].body).foes).toBe('a dire wolf');
    });

    test('the command bar Fight button asks the GM', async () => {
        setUpDom({ sessionId: 12 });
        loadTab();
        global.fetch.mockReturnValue(jsonResponse({ ok: true, session_id: 12, battle_id: 4, battle_name: 'x' }));
        document.getElementById('gm-fight-button').click();
        await flushPromises();
        expect(JSON.parse(global.fetch.mock.calls[0][1].body).foes).toBe('');
    });

    test('showing the tab no longer touches the panel width (issue #351)', async () => {
        setUpDom({ sessionId: 12, withBattle: true });
        loadTab();
        const panel = document.getElementById('gm-panel');
        const container = document.getElementById('gm-panel-battle');
        container.hidden = false;
        await flushPromises();
        // The width belongs to the viewer now, dragged on the panel's
        // handle and kept in localStorage; no tab widens it.
        expect(panel.className).toBe('');
        expect(panel.style.width).toBe('');
    });
});

/**
 * How a fight ended, in the tab and in the chat (issue #785). The verdict
 * used to exist only in a hidden entry the model read on the player's next
 * message; the tab now asks the server to file it the moment the board says
 * the fight is over.
 */
describe('the verdict when the fight ends', () => {
    function setUpVerdictDom() {
        setUpDom({ sessionId: 12, withBattle: true });
        document.getElementById('gm-fight-form')
            .setAttribute('data-verdict-url', '/tarmar/gm/fight/verdict/');
        document.getElementById('gm-battle').insertAdjacentHTML(
            'beforeend',
            '<p id="gm-battle-verdict" hidden></p>',
        );
        document.body.insertAdjacentHTML('beforeend', '<div id="messages"></div>');
    }

    const VERDICT = 'The fight is over: The foes stand victorious — Gray Wolf left on the field.\nAldwin: unconscious; Fatigue 0/12, Body 3/10.';

    test('shows the verdict in the tab and once in the chat', async () => {
        setUpVerdictDom();
        loadTab();
        global.fetch.mockReturnValue(jsonResponse({ ok: true, verdict: VERDICT, battle_id: 4, notice: '4:90' }));

        await expect(panelBattle.showVerdict()).resolves.toBe(true);

        // Read back, never filed from the page (review of #785).
        expect(global.fetch.mock.calls[0][0]).toBe('/tarmar/gm/fight/verdict/?session_id=12');
        expect(global.fetch.mock.calls[0][1].method).toBeUndefined();
        const pane = document.getElementById('gm-battle-verdict');
        expect(pane.hidden).toBe(false);
        expect(pane.textContent).toBe(VERDICT);
        const notices = document.querySelectorAll('#messages [data-battle-verdict="4:90"]');
        expect(notices).toHaveLength(1);
        expect(notices[0].classList.contains('notice')).toBe(true);
        expect(notices[0].querySelector('.message-content').textContent).toBe(VERDICT);
        // The app speaking, not a GM bubble (#339 item 11).
        expect(notices[0].querySelector('.message-role').textContent).toBe('Notice');

        // Asked again -- another poll, another advance -- it is not doubled.
        await panelBattle.showVerdict();
        expect(document.querySelectorAll('#messages [data-battle-verdict="4:90"]')).toHaveLength(1);
    });

    test('an ending after a restart is a second notice; the first stays', async () => {
        setUpVerdictDom();
        loadTab();
        global.fetch.mockReturnValue(jsonResponse({ ok: true, verdict: VERDICT, battle_id: 4, notice: '4:90' }));
        await panelBattle.showVerdict();
        global.fetch.mockReturnValue(jsonResponse({
            ok: true, verdict: 'After the restart, the fight is over: x.', battle_id: 4, notice: '4:120',
        }));

        await panelBattle.showVerdict();

        const notices = document.querySelectorAll('#messages [data-battle-verdict]');
        expect(Array.from(notices).map((n) => n.dataset.battleVerdict)).toEqual(['4:90', '4:120']);
        expect(document.getElementById('gm-battle-verdict').textContent)
            .toBe('After the restart, the fight is over: x.');
    });

    test('a notice the page already rendered is not added again', async () => {
        setUpVerdictDom();
        document.getElementById('messages').innerHTML =
            '<div class="message assistant notice" data-battle-verdict="4"></div>';
        loadTab();
        global.fetch.mockReturnValue(jsonResponse({ ok: true, verdict: VERDICT, battle_id: 4, notice: '4:90' }));

        await panelBattle.showVerdict();

        expect(document.querySelectorAll('#messages [data-battle-verdict="4:90"]')).toHaveLength(1);
    });

    test('a fight still going shows nothing', async () => {
        setUpVerdictDom();
        loadTab();
        global.fetch.mockReturnValue(jsonResponse({ ok: true, verdict: null, battle_id: null, notice: null }));

        await expect(panelBattle.showVerdict()).resolves.toBe(false);

        expect(document.getElementById('gm-battle-verdict').hidden).toBe(true);
        expect(document.getElementById('messages').children).toHaveLength(0);
    });

    test('a refusal stays in the tab\'s error line', async () => {
        setUpVerdictDom();
        loadTab();
        global.fetch.mockReturnValue(
            jsonResponse({ error: 'GM session not found' }, { ok: false, status: 404, statusText: 'Not Found' }),
        );

        await expect(panelBattle.showVerdict()).resolves.toBe(false);

        const error = document.getElementById('gm-panel-battle-error');
        expect(error.hidden).toBe(false);
        expect(error.textContent).toContain('GM session not found');
    });

    test('the board ending the fight asks for the verdict', async () => {
        setUpVerdictDom();
        loadTab();
        global.fetch.mockReturnValue(jsonResponse({ ok: true, verdict: VERDICT, battle_id: 4, notice: '4:90' }));

        document.dispatchEvent(new CustomEvent('battle-applied', { detail: { status: 'finished' } }));
        await flushPromises();
        await flushPromises();

        expect(global.fetch.mock.calls.map((call) => call[0])).toContain('/tarmar/gm/fight/verdict/?session_id=12');
        expect(document.getElementById('gm-battle-verdict').textContent).toBe(VERDICT);
    });

    test('a turn that leaves the fight going asks for nothing', async () => {
        setUpVerdictDom();
        loadTab();

        document.dispatchEvent(new CustomEvent('battle-applied', { detail: { status: 'running' } }));
        await flushPromises();

        expect(global.fetch).not.toHaveBeenCalled();
    });
});

describe('the Fight form repaints from the board (issue #868)', () => {
    function disabledForm() {
        setUpDom({ sessionId: 12, withBattle: true });
        const input = document.getElementById('gm-fight-foes');
        const button = document.getElementById('gm-fight-start');
        input.disabled = true;
        button.disabled = true;
        button.title = 'A fight is already under way';
        const tab = document.getElementById('gm-panel-tab-battle');
        tab.classList.add('is-live');
        tab.title = 'A fight is under way';
        return { input, button, tab };
    }

    function applied(detail) {
        document.dispatchEvent(new CustomEvent('battle-applied', { detail }));
    }

    test('a fight that ends opens the form without a reload', () => {
        const { input, button, tab } = disabledForm();
        loadTab();
        applied({ status: 'finished', live: false });
        expect(input.disabled).toBe(false);
        expect(button.disabled).toBe(false);
        expect(button.hasAttribute('title')).toBe(false);
        expect(tab.classList.contains('is-live')).toBe(false);
        expect(tab.hasAttribute('title')).toBe(false);
    });

    test('a fight still on keeps it shut, and says why', () => {
        const { input, button, tab } = disabledForm();
        loadTab();
        applied({ status: 'paused', live: true });
        expect(input.disabled).toBe(true);
        expect(button.disabled).toBe(true);
        expect(button.title).toBe('A fight is already under way');
        expect(tab.classList.contains('is-live')).toBe(true);
        expect(tab.title).toBe('A fight is under way');
    });

    test('a response without live leaves the form as it is', () => {
        const { input, button } = disabledForm();
        loadTab();
        applied({ status: 'finished' });
        applied(undefined);
        expect(input.disabled).toBe(true);
        expect(button.disabled).toBe(true);
    });

    test('painting a page without the form does nothing', () => {
        setUpDom({ withForm: false });
        loadTab();
        expect(() => panelBattle.paintFightForm(false)).not.toThrow();
        expect(() => panelBattle.paintFightForm(true)).not.toThrow();
    });
});

describe('the bare-handed line repaints from the board (#844 review)', () => {
    function withLine(text) {
        setUpDom({ sessionId: 12, withBattle: true });
        document.getElementById('gm-panel-battle').insertAdjacentHTML(
            'afterbegin', `<p id="gm-panel-unarmed">${text}</p>`
        );
        return document.getElementById('gm-panel-unarmed');
    }

    function applied(detail) {
        document.dispatchEvent(new CustomEvent('battle-applied', { detail }));
    }

    test('a figure that picks a weapon up, or a fight that ends, clears it', () => {
        const line = withLine('Bare is on the board with no weapon, fighting bare-handed.');
        loadTab();
        applied({ status: 'paused', live: true, bareHanded: '' });
        expect(line.hidden).toBe(true);
        expect(line.textContent).toBe('');
    });

    test('the server sentence is painted as it stands', () => {
        const line = withLine('');
        line.hidden = true;
        loadTab();
        applied({ status: 'paused', live: true, bareHanded: 'Tam is on the board with no weapon, fighting bare-handed.' });
        expect(line.hidden).toBe(false);
        expect(line.textContent).toBe('Tam is on the board with no weapon, fighting bare-handed.');
    });

    test('an older payload without it leaves the line alone, and no line is no error', () => {
        const line = withLine('Bare is on the board.');
        loadTab();
        applied({ status: 'paused', live: true });
        expect(line.textContent).toBe('Bare is on the board.');
        line.remove();
        expect(() => panelBattle.paintBareHanded('x')).not.toThrow();
    });
});
