/**
 * Unit tests for mechanics.js (issues #753, #756, #757, #774, #18).
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
    castSpellUrl: '/tarmar/characters/7/cast-spell/',
};

/** A check shaped like the endpoint's, with numbers the module must not redo. */
function checkPayload(overrides = {}) {
    return {
        skill: 'stealth', level: 2, attribute: 'dexterity', attribute_score: 15,
        attribute_modifier: 2, substituted: false, dice: '2d6', faces: [5, 4], stated: false,
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
        <div id="cast" data-mechanics-form="castSpellUrl" data-result="cast-result">
            <select name="spell"><option value="heal" selected>Heal</option></select>
            <input name="total" value="">
            <button class="mechanics-submit" id="cast-button">Cast</button>
            <span class="mechanics-error" hidden></span>
            <p id="cast-result" hidden></p>
        </div>
        <div id="coin" data-mechanics-form="coinUrl" data-reload="true">
            <input name="amount" value="25">
            <button class="mechanics-submit" id="give">Give</button>
            <span class="mechanics-error" hidden></span>
        </div>`;
}

async function load(response) {
    jest.resetModules();
    document.body.innerHTML = pageHtml();
    fetch.mockImplementation(() => Promise.resolve(response));
    require('../mechanics.js');
    await flush();
}

beforeEach(() => {
    fetch.mockReset();
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
    expect(pageNavigation.reload).not.toHaveBeenCalled();
});

test("prints the dice the server says the check was rolled on (issue #70)", async () => {
    await load(jsonResponse({
        success: true,
        check: checkPayload({ dice: '3d4', faces: [1, 4, 4], difficulty: null, outcome: '', total: 13 }),
    }));
    document.getElementById('roll').click();
    await flush();
    expect(document.getElementById('check-result').textContent).toBe(
        'stealth: 3d4 [1, 4, 4] = 9 +2 DEX (15) +2 level = 13'
    );
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
    expect(pageNavigation.reload).toHaveBeenCalled();
});

test('a refusal stays beside the control', async () => {
    await load(jsonResponse({ success: false, error: 'Only a Game Master can give or take coin' }, { ok: false }));
    document.getElementById('give').click();
    await flush();
    const error = document.querySelector('#coin .mechanics-error');
    expect(error.hidden).toBe(false);
    expect(error.textContent).toBe('Only a Game Master can give or take coin');
    expect(pageNavigation.reload).not.toHaveBeenCalled();
    expect(document.getElementById('give').disabled).toBe(false);
});

/** A cast shaped like the endpoint's; the module prints its line and nothing else. */
function castPayload(overrides = {}) {
    return {
        spell: 'heal', spell_name: 'Heal', succeeded: true, total: 6, ceiling: 99,
        line: 'casts Heal: 3d6: [3, 2, 1] = 6 ≤ 99 (WIS 99): cast; 2 mana spent, 4 left',
        ...overrides,
    };
}

test("a cast in the story prints the server's line and stays on the page (#18)", async () => {
    await load(jsonResponse({ success: true, cast: castPayload() }));
    document.getElementById('cast-button').click();
    await flush();
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe(CONFIG.castSpellUrl);
    expect(JSON.parse(options.body)).toEqual({ spell: 'heal' });
    const result = document.getElementById('cast-result');
    expect(result.hidden).toBe(false);
    expect(result.textContent).toBe(castPayload().line);
    expect(result.className).toBe('cast-spell-result success');
    expect(pageNavigation.reload).not.toHaveBeenCalled();
});

test('the next cast replaces the line, a failure marked as one (#18)', async () => {
    await load(jsonResponse({ success: true, cast: castPayload() }));
    document.getElementById('cast-button').click();
    await flush();
    const failed = castPayload({ succeeded: false, line: 'casts Heal: 3d6: [6, 6, 6] = 18 > 10 (WIS 10): fails' });
    fetch.mockImplementation(() => Promise.resolve(jsonResponse({ success: true, cast: failed })));
    document.getElementById('cast-button').click();
    await flush();
    const result = document.getElementById('cast-result');
    expect(result.textContent).toBe(failed.line);
    expect(result.className).toBe('cast-spell-result failure');
});

test('a refused cast leaves the last line and says why beside the control (#18)', async () => {
    await load(jsonResponse({ success: true, cast: castPayload() }));
    document.getElementById('cast-button').click();
    await flush();
    fetch.mockImplementation(() => Promise.resolve(jsonResponse(
        { success: false, error: 'Heal costs 2 mana and the Pool holds 0' }, { ok: false }
    )));
    document.getElementById('cast-button').click();
    await flush();
    expect(document.querySelector('#cast .mechanics-error').textContent)
        .toBe('Heal costs 2 mana and the Pool holds 0');
    expect(document.getElementById('cast-result').textContent).toBe(castPayload().line);
});

test("a Game Master's stated total is sent with its cast and then cleared (#18)", async () => {
    await load(jsonResponse({ success: true, cast: castPayload() }));
    document.querySelector('#cast [name="total"]').value = '9';
    document.getElementById('cast-button').click();
    await flush();
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ spell: 'heal', total: '9' });
    expect(document.querySelector('#cast [name="total"]').value).toBe('');
});
