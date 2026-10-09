/**
 * Unit tests for mechanics.js (issues #753, #756, #757, #774).
 *
 * The module's contract: post a block's named controls to the URL its
 * data-mechanics-form names, print the server's skill check without
 * computing any of it, reload for a record change, and leave a refusal in
 * place beside the control.
 */

const { jsonResponse, flush } = require('./test-helpers');

const CONFIG = {
    skillCheckUrl: '/tarmar/characters/7/skill-check/',
    coinUrl: '/tarmar/characters/7/coin/',
};

/** A check shaped like the endpoint's, with numbers the module must not redo. */
function checkPayload(overrides = {}) {
    return {
        skill: 'stealth', level: 2, attribute: 'dexterity', attribute_score: 15,
        attribute_modifier: 2, substituted: false, faces: [5, 4], stated: false,
        dice_total: 9, total: 99, difficulty: 10, outcome: 'success', natural: '',
        ...overrides,
    };
}

function pageHtml() {
    return `
        <script id="mechanics-config" type="application/json">${JSON.stringify(CONFIG)}<\/script>
        <div id="check" data-mechanics-form="skillCheckUrl" data-result="check-result">
            <select name="skill"><option value="stealth" selected>Stealth</option></select>
            <input name="difficulty" value="10">
            <input name="total" value="">
            <button class="mechanics-submit" id="roll">Roll</button>
            <span class="mechanics-error" hidden></span>
            <p id="check-result" hidden></p>
        </div>
        <div id="coin" data-mechanics-form="coinUrl" data-reload="true">
            <input name="amount" value="25">
            <button class="mechanics-submit" id="give">Give</button>
            <span class="mechanics-error" hidden></span>
        </div>`;
}

let reload;

async function load(response) {
    jest.resetModules();
    document.body.innerHTML = pageHtml();
    fetch.mockImplementation(() => Promise.resolve(response));
    require('../mechanics.js');
    await flush();
}

beforeEach(() => {
    fetch.mockReset();
    reload = jest.fn();
    delete window.location;
    window.location = { reload };
});

test('posts the named controls, leaving blanks out', async () => {
    await load(jsonResponse({ success: true, check: checkPayload() }));
    document.getElementById('roll').click();
    await flush();
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe(CONFIG.skillCheckUrl);
    expect(JSON.parse(options.body)).toEqual({ skill: 'stealth', difficulty: '10' });
});

test("prints the server's check without recomputing the total", async () => {
    await load(jsonResponse({ success: true, check: checkPayload() }));
    document.getElementById('roll').click();
    await flush();
    const result = document.getElementById('check-result');
    expect(result.hidden).toBe(false);
    expect(result.textContent).toBe(
        'stealth: 2d6 [5, 4] = 9 +2 DEX (15) +2 level = 99 vs 10: success'
    );
    expect(reload).not.toHaveBeenCalled();
});

test("a player's roll with no difficulty prints the total and no outcome", async () => {
    await load(jsonResponse({
        success: true,
        check: checkPayload({ difficulty: null, outcome: '', total: 13 }),
    }));
    document.getElementById('roll').click();
    await flush();
    expect(document.getElementById('check-result').textContent).toBe(
        'stealth: 2d6 [5, 4] = 9 +2 DEX (15) +2 level = 13'
    );
});

test('names a focus bonus before the injury penalty, so the line adds up', async () => {
    await load(jsonResponse({
        success: true,
        check: checkPayload({
            focus_bonus: 1, injury_penalty: -1, difficulty: null, outcome: '', total: 13,
        }),
    }));
    document.getElementById('roll').click();
    await flush();
    expect(document.getElementById('check-result').textContent).toBe(
        'stealth: 2d6 [5, 4] = 9 +2 DEX (15) +2 level +1 focus -1 injured = 13'
    );
});

test("says when the check was a Game Master's call past the rules", async () => {
    await load(jsonResponse({
        success: true,
        check: checkPayload({ game_master_call: 'unconscious', outcome: 'failure', total: 8 }),
    }));
    document.getElementById('roll').click();
    await flush();
    expect(document.getElementById('check-result').textContent).toBe(
        "stealth: 2d6 [5, 4] = 9 +2 DEX (15) +2 level = 8 vs 10: failure (a Game Master's call: unconscious)"
    );
});

test('names the injury penalty the server took (issue #840)', async () => {
    await load(jsonResponse({
        success: true,
        check: checkPayload({ injury_penalty: -2, difficulty: null, outcome: '', total: 11 }),
    }));
    document.getElementById('roll').click();
    await flush();
    expect(document.getElementById('check-result').textContent).toBe(
        'stealth: 2d6 [5, 4] = 9 +2 DEX (15) +2 level -2 injured = 11'
    );
});

test('names a natural and a stated roll', async () => {
    await load(jsonResponse({
        success: true,
        check: checkPayload({ stated: true, faces: [], dice_total: 2, outcome: 'failure', natural: 'natural 2' }),
    }));
    document.getElementById('roll').click();
    await flush();
    expect(document.getElementById('check-result').textContent).toContain(
        '2d6 stated by a Game Master = 2'
    );
    expect(document.getElementById('check-result').textContent).toContain('(natural 2)');
});

test('a record change reloads the page the server renders', async () => {
    await load(jsonResponse({ success: true, purse: '125' }));
    document.getElementById('give').click();
    await flush();
    expect(fetch.mock.calls[0][0]).toBe(CONFIG.coinUrl);
    expect(reload).toHaveBeenCalled();
});

test('a refusal stays beside the control', async () => {
    await load(jsonResponse({ success: false, error: 'Only a Game Master can give or take coin' }, { ok: false }));
    document.getElementById('give').click();
    await flush();
    const error = document.querySelector('#coin .mechanics-error');
    expect(error.hidden).toBe(false);
    expect(error.textContent).toBe('Only a Game Master can give or take coin');
    expect(reload).not.toHaveBeenCalled();
    expect(document.getElementById('give').disabled).toBe(false);
});
