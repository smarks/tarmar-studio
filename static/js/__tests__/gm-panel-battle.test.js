/**
 * Unit tests for gm-panel-battle.js — the Battle tab's fight controls
 * (issue #345).
 *
 * The tab posts to the fight endpoint and then draws itself again as the
 * server renders it, in place (issue #89), so the board is always
 * server-rendered and the rest of the page -- the earlier turns the reader
 * loaded -- stays; these tests assert the request each control makes, the
 * tab it brings forward, the drawing that follows with no reload, and that
 * a failure lands in the tab's own error line (which stays until the next
 * attempt — no toasts). The board itself is battle.js's and is tested there.
 */

const BATTLE_PANEL_URL = '/tarmar/gm/sessions/{session_id}/battle/';

function setUpDom({ sessionId = null, withForm = true, withBattle = false } = {}) {
    const config = JSON.stringify({
        attachUrl: '/tarmar/gm/characters/attach/',
        detachUrl: '/tarmar/gm/characters/detach/',
        battlePanelUrlTemplate: BATTLE_PANEL_URL,
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

/** An answer carrying HTML, as gm:battle_panel's. */
function htmlResponse(html, { ok = true, status = 200, statusText = 'OK' } = {}) {
    return Promise.resolve({ ok, status, statusText, text: () => Promise.resolve(html) });
}

/**
 * The Battle tab as gm/_panel_battle.html renders it for a fight on the board:
 * the form closed while it is on, a ruling form posting back to the address
 * it was rendered under, and the pop-out pointing at the battle's page.
 */
function renderedTab({ live = true } = {}) {
    return `
        <section class="gm-panel-tab gm-panel-battle" id="gm-panel-battle" data-panel-container="battle"
                 data-popout-url="/tarmar/battles/4/" hidden>
            <form id="gm-fight-form" data-fight-url="/tarmar/gm/fight/" data-live="${live}">
                <input type="text" id="gm-fight-foes"${live ? ' disabled' : ''}>
                <button type="submit" id="gm-fight-start"${live ? ' disabled' : ''}>Fight</button>
            </form>
            <p id="gm-panel-battle-status" role="status" hidden></p>
            <p id="gm-panel-battle-error" role="alert" hidden></p>
            <div id="gm-battle" data-battle-id="4">
                <form method="post" action="/tarmar/battles/4/hth-agreement/" class="agreement-battle-form">
                    <input type="hidden" name="csrfmiddlewaretoken" value="form-token">
                    <input type="hidden" name="next" value="/tarmar/gm/sessions/12/battle/">
                    <input type="hidden" name="scope" value="battle">
                    <button type="submit" name="agreed" value="1" id="agree" data-focus-key="hth-whole-battle">Agree</button>
                </form>
            </div>
        </section>`;
}

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
    window.history.replaceState(null, '', '/tarmar/gm/');
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
    test('posts the named foes with the session and draws the fight in place', async () => {
        setUpDom({ sessionId: 12 });
        loadTab();
        global.fetch.mockReturnValue(jsonResponse({
            ok: true, session_id: 12, battle_id: 4, battle_name: 'Quest: fight',
        }));
        global.fetch.mockReturnValueOnce(jsonResponse({
            ok: true, session_id: 12, battle_id: 4, battle_name: 'Quest: fight',
        }));
        global.fetch.mockReturnValueOnce(htmlResponse(renderedTab()));

        const done = panelBattle.startFight('two gray wolves');
        expect(window.gmPanel.activate).toHaveBeenCalledWith('battle');
        expect(window.gmPanel.setOpen).toHaveBeenCalledWith(true);
        expect(document.getElementById('gm-panel-battle-status').textContent).toBe('Setting the board…');
        expect(document.getElementById('gm-fight-start').disabled).toBe(true);
        expect(document.getElementById('gm-fight-button').disabled).toBe(true);
        expect(await done).toBe(true);

        expect(global.fetch).toHaveBeenCalledTimes(2);
        const [url, options] = global.fetch.mock.calls[0];
        expect(url).toBe('/tarmar/gm/fight/');
        expect(options.method).toBe('POST');
        expect(options.headers['X-CSRFToken']).toBe('test-token');
        expect(JSON.parse(options.body)).toEqual({ session_id: 12, foes: 'two gray wolves' });
        expect(global.fetch.mock.calls[1][0]).toBe('/tarmar/gm/sessions/12/battle/');
        expect(document.getElementById('gm-battle')).not.toBeNull();
        expect(pageNavigation.go).not.toHaveBeenCalled();
        expect(pageNavigation.reload).not.toHaveBeenCalled();
    });

    test('the session in the URL wins over the one the page loaded with', async () => {
        setUpDom({ sessionId: null });
        window.history.replaceState(null, '', '?session=31');
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
        expect(pageNavigation.go).not.toHaveBeenCalled();
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
        expect(pageNavigation.go).not.toHaveBeenCalled();
    });

    test('a network failure is reported the same way', async () => {
        setUpDom({ sessionId: 12 });
        loadTab();
        global.fetch.mockReturnValue(Promise.reject(new Error('offline')));
        expect(await panelBattle.startFight('wolf')).toBe(false);
        expect(document.getElementById('gm-panel-battle-error').textContent).toBe('Request failed: offline');
    });

    test('a proxy timeout page is reported in words with its status (issue #936)', async () => {
        setUpDom({ sessionId: 12 });
        loadTab();
        global.fetch.mockReturnValue(Promise.resolve({
            ok: false,
            status: 504,
            statusText: 'Gateway Time-out',
            json: () => Promise.reject(new SyntaxError('Unexpected token < in JSON')),
        }));
        expect(await panelBattle.startFight('wolf')).toBe(false);
        const error = document.getElementById('gm-panel-battle-error');
        expect(error.hidden).toBe(false);
        expect(error.textContent).toBe(
            'The server took too long to answer (HTTP 504). Try the fight again in a moment.',
        );
        expect(document.getElementById('gm-panel-battle-status').hidden).toBe(true);
        expect(document.getElementById('gm-fight-start').disabled).toBe(false);
        expect(pageNavigation.go).not.toHaveBeenCalled();
    });

    test('any other answer that is not JSON names its status', async () => {
        setUpDom({ sessionId: 12 });
        loadTab();
        global.fetch.mockReturnValue(Promise.resolve({
            ok: false,
            status: 502,
            statusText: 'Bad Gateway',
            json: () => Promise.reject(new SyntaxError('Unexpected token <')),
        }));
        expect(await panelBattle.startFight('wolf')).toBe(false);
        expect(document.getElementById('gm-panel-battle-error').textContent).toBe(
            'The server answered with an error (HTTP 502). Try the fight again in a moment.',
        );
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
        // A submit bubbles, as a browser's does: the tab listens on its section.
        const event = new Event('submit', { cancelable: true, bubbles: true });
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

/**
 * The tab drawn again in place (issue #89). A fight started from the page, a
 * restart made elsewhere and a ruling posted from the board each used to
 * reload the page, and the reload threw away the earlier turns the reader had
 * loaded. The tab is drawn from the server's rendering instead, inside the
 * section that stays, and nothing else on the page is touched.
 */
describe('the tab drawn again in place (#89)', () => {
    let section;

    /** The page on session 12 with an earlier page of the chat loaded. */
    function setUpSession({ withBoard = false } = {}) {
        setUpDom({ sessionId: 12 });
        window.history.replaceState(null, '', '/tarmar/gm/?session=12');
        if (withBoard) {
            const holder = document.createElement('template');
            holder.innerHTML = renderedTab().trim();
            document.getElementById('gm-panel-battle')
                .replaceChildren(...holder.content.firstElementChild.childNodes);
        }
        document.body.insertAdjacentHTML('beforeend', `
            <div id="messages"><div class="message user" data-history-turn="0" id="loaded-earlier"></div></div>`);
        window.battlePage = { initBattlePage: jest.fn(), REWOUND_EVENT: 'battle-rewound' };
        window.gmPanelLayout = { isActive: () => true, paintPopouts: jest.fn() };
        window.showConversationAsStored = jest.fn(() => Promise.resolve(true));
        loadTab();
        section = document.getElementById('gm-panel-battle');
    }

    afterEach(() => {
        delete window.battlePage;
        delete window.gmPanelLayout;
        delete window.showConversationAsStored;
    });

    test('a fight puts the server\'s board in the tab and its lines in the chat', async () => {
        setUpSession();
        const earlier = document.getElementById('loaded-earlier');
        global.fetch.mockReturnValueOnce(jsonResponse({
            ok: true, session_id: 12, battle_id: 4, battle_name: 'Quest: fight',
        }));
        global.fetch.mockReturnValueOnce(htmlResponse(renderedTab()));

        expect(await panelBattle.startFight('two gray wolves')).toBe(true);

        // The same section, holding the server's rendering of the tab.
        expect(document.getElementById('gm-panel-battle')).toBe(section);
        expect(section.querySelector('#gm-battle')).not.toBeNull();
        expect(section.getAttribute('data-popout-url')).toBe('/tarmar/battles/4/');
        expect(section.hidden).toBe(true);
        // Its forms go back to this page, not to the fragment's address.
        expect(section.querySelector('input[name="next"]').value).toBe('/tarmar/gm/?session=12');
        // battle.js wires the new arena; the pane's chrome is painted again.
        expect(window.battlePage.initBattlePage).toHaveBeenCalledWith(section);
        expect(window.gmPanelLayout.paintPopouts).toHaveBeenCalled();
        // The form is the server's, shut while the fight is on, and the tab lit.
        expect(document.getElementById('gm-fight-start').disabled).toBe(true);
        expect(document.getElementById('gm-panel-tab-battle').classList.contains('is-live')).toBe(true);
        expect(document.getElementById('gm-fight-button').disabled).toBe(false);
        // The fight's lines come into the chat as stored, and the page stays.
        expect(window.showConversationAsStored).toHaveBeenCalledTimes(1);
        expect(earlier.isConnected).toBe(true);
        expect(pageNavigation.go).not.toHaveBeenCalled();
        expect(pageNavigation.reload).not.toHaveBeenCalled();
    });

    test('a board that cannot be drawn says so, and the fight still shows as on', async () => {
        setUpSession();
        global.fetch.mockReturnValueOnce(jsonResponse({
            ok: true, session_id: 12, battle_id: 4, battle_name: 'Quest: fight',
        }));
        global.fetch.mockReturnValueOnce(htmlResponse('', { ok: false, status: 500, statusText: 'Server Error' }));

        expect(await panelBattle.startFight('wolf')).toBe(true);

        const error = document.getElementById('gm-panel-battle-error');
        expect(error.hidden).toBe(false);
        expect(error.textContent).toBe('The board changed, but the page could not show it: Server Error');
        expect(document.getElementById('gm-panel-battle-status').hidden).toBe(true);
        expect(document.getElementById('gm-fight-start').disabled).toBe(true);
        expect(document.getElementById('gm-panel-tab-battle').classList.contains('is-live')).toBe(true);
        expect(pageNavigation.go).not.toHaveBeenCalled();
        expect(pageNavigation.reload).not.toHaveBeenCalled();
    });

    test('a restart made elsewhere draws the tab again, not the page', async () => {
        setUpSession({ withBoard: true });
        global.fetch.mockReturnValueOnce(htmlResponse(renderedTab({ live: false })));

        const rewound = new CustomEvent('battle-rewound', { bubbles: true, cancelable: true });
        section.querySelector('#gm-battle').dispatchEvent(rewound);
        await flushPromises();
        await flushPromises();

        expect(rewound.defaultPrevented).toBe(true);
        expect(global.fetch.mock.calls[0][0]).toBe('/tarmar/gm/sessions/12/battle/');
        expect(window.battlePage.initBattlePage).toHaveBeenCalledWith(section);
        expect(document.getElementById('gm-fight-start').disabled).toBe(false);
        // A restart writes nothing into the chat.
        expect(window.showConversationAsStored).not.toHaveBeenCalled();
        expect(pageNavigation.reload).not.toHaveBeenCalled();
    });

    test('a ruling posts as a script caller and draws the tab again', async () => {
        setUpSession({ withBoard: true });
        global.fetch.mockReturnValueOnce(jsonResponse({ status: 'paused', turn: 1 }));
        global.fetch.mockReturnValueOnce(htmlResponse(renderedTab()));

        const form = section.querySelector('.agreement-battle-form');
        form.requestSubmit(document.getElementById('agree'));
        await flushPromises();
        await flushPromises();

        const [url, options] = global.fetch.mock.calls[0];
        expect(url).toBe('http://localhost/tarmar/battles/4/hth-agreement/');
        expect(options.method).toBe('POST');
        expect(options.headers['X-Requested-With']).toBe('XMLHttpRequest');
        expect(options.body.get('scope')).toBe('battle');
        expect(options.body.get('agreed')).toBe('1');
        expect(global.fetch.mock.calls[1][0]).toBe('/tarmar/gm/sessions/12/battle/');
        expect(pageNavigation.go).not.toHaveBeenCalled();
        expect(pageNavigation.reload).not.toHaveBeenCalled();
    });

    test('the ruling\'s button waits for the redraw, and the focus comes back to it', async () => {
        setUpSession({ withBoard: true });
        let answerDraw;
        global.fetch.mockReturnValueOnce(jsonResponse({ status: 'paused', turn: 1 }));
        global.fetch.mockReturnValueOnce(new Promise((resolve) => { answerDraw = resolve; }));

        const button = document.getElementById('agree');
        button.focus();
        section.querySelector('.agreement-battle-form').requestSubmit(button);
        await flushPromises();
        await flushPromises();
        // Recorded, the tab on its way: the button is still held.
        expect(global.fetch).toHaveBeenCalledTimes(2);
        expect(button.disabled).toBe(true);

        answerDraw({ ok: true, status: 200, text: () => Promise.resolve(renderedTab()) });
        await flushPromises();
        await flushPromises();

        const drawn = document.getElementById('agree');
        expect(drawn).not.toBe(button);
        expect(drawn.disabled).toBe(false);
        expect(document.activeElement).toBe(drawn);
    });

    test('a refused ruling stays in the tab\'s error line and draws nothing', async () => {
        setUpSession({ withBoard: true });
        global.fetch.mockReturnValueOnce(jsonResponse(
            { error: 'Only the Game Master running this fight can rule on it' },
            { ok: false, status: 403, statusText: 'Forbidden' },
        ));

        section.querySelector('.agreement-battle-form').requestSubmit();
        await flushPromises();

        expect(global.fetch).toHaveBeenCalledTimes(1);
        const error = document.getElementById('gm-panel-battle-error');
        expect(error.hidden).toBe(false);
        expect(error.textContent).toBe('Only the Game Master running this fight can rule on it');
    });

    test('draws asked for while one is on its way collapse into one more', async () => {
        setUpSession({ withBoard: true });
        let answerFirst;
        global.fetch.mockReturnValueOnce(new Promise((resolve) => { answerFirst = resolve; }));
        global.fetch.mockReturnValueOnce(htmlResponse(renderedTab()));

        const first = panelBattle.drawTab();
        panelBattle.drawTab();
        panelBattle.drawTab();
        answerFirst({ ok: true, status: 200, text: () => Promise.resolve(renderedTab()) });
        await first;
        await flushPromises();
        await flushPromises();

        expect(global.fetch).toHaveBeenCalledTimes(2);
    });
});
