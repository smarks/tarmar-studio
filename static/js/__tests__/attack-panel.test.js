/**
 * Unit tests for attack-panel.js (issue #101).
 *
 * The panel's contract is narrow and worth pinning precisely: it must send the
 * form's values verbatim, and it must print the server's numbers without
 * deriving any of its own. So these tests assert on two things — the exact
 * body that reaches fetch (via postWithCsrf, so this covers the wire format),
 * and the exact text rendered from a hand-written payload. A regression where
 * the panel started recomputing a total would show up here as a number that no
 * longer matches the (deliberately inconsistent) figures in the fixture.
 */

const CONFIG = {
    attackUrl: '/tarmar/characters/7/attack/',
};

/** A resolved-attack payload shaped like the endpoint's. */
function attackPayload(overrides = {}) {
    return {
        weapon: { index: 0, item_id: 'saber', name: 'Saber', weapon_class: 'Striking' },
        skill: {
            name: 'weapon (saber)', level: 3, weapon_id: 'saber',
            rule: 'full: the weapon it was taken for',
        },
        defender: { armour_tier: 'Medium', shield_bonus: 2, stops: 3, dodge: 1 },
        target_number: { base: 16, shield_bonus: 2, defender_dodge: 1, total: 19 },
        to_hit: {
            dex_modifier: 2,
            skill_bonus: 3,
            strength_fit_penalty: -1,
            situational: 0,
            weapon_mod: 1,
            total: 5,
        },
        attack_roll: {
            die: 17, log: 'd20: 17', total: 22,
            outcome: 'hit', hit: true, critical: false, fumble: false,
        },
        confirm: null,
        damage: {
            rolls: [4, 3], multiplier: 1, raw_total: 7, weapon_mod: 0,
            stops: 3, applied_stops: 3, hybrid_bypass: false, final: 4,
        },
        fumble: null,
        severe: false,
        bleeding: false,
        // A plain hit applies to Fatigue first; only a severe critical
        // reaches Body (issue #293). The default fixture is an ordinary hit,
        // so reaches_body is false here — see the dedicated severe-critical
        // test below for the true case.
        reaches_body: false,
        dice_log: ['attack d20: 17 +5 = 22 vs TN 19 -> hit', 'damage 2d6-2: [4, 3] = 7'],
        ...overrides,
    };
}

const { jsonResponse, flush } = require('./test-helpers');

/** The parts of character_edit.html's attack panel the module touches. */
function panelHtml(config, { withForm = true } = {}) {
    const form = `
        <div class="attack-form" id="attack-form">
            <select id="attack-weapon"><option value="0" selected>Saber</option><option value="2">Great Hammer</option></select>
            <select id="attack-armour-tier"><option value="None" selected>None</option><option value="Medium">Medium</option></select>
            <input type="number" id="attack-stops" value="0">
            <select id="attack-shield"><option value="" selected>None</option><option value="large_shield">Large Shield</option></select>
            <input type="number" id="attack-dodge" value="0">
            <input type="number" id="attack-situational" value="0">
            <button type="button" id="attack-resolve-btn">Resolve Attack</button>
        </div>
        <div class="attack-result" id="attack-result" hidden>
            <h3 id="attack-outcome"></h3>
            <ul class="attack-breakdown" id="attack-breakdown"></ul>
            <ol class="attack-dice-log" id="attack-dice-log"></ol>
        </div>`;
    return `
        <script id="attack-panel-config" type="application/json">${JSON.stringify(config)}<\/script>
        ${withForm ? form : '<p id="attack-no-weapons">No catalog weapon.</p>'}`;
}

/** Build the page, then require the module — the browser's order. */
async function loadPanel({ config = {}, options = {}, response } = {}) {
    jest.resetModules();
    document.body.innerHTML = panelHtml({ ...CONFIG, ...config }, options);
    fetch.mockImplementation(() =>
        Promise.resolve(response || jsonResponse({ success: true, attack: attackPayload() }))
    );
    require('../attack-panel.js');
    await flush();
}

/** Click Resolve and let the handler's promise settle. */
async function resolve() {
    document.getElementById('attack-resolve-btn').click();
    await flush();
}

/** The rendered breakdown as a {label: value} map. */
function breakdown() {
    const rows = {};
    document.querySelectorAll('#attack-breakdown li').forEach((item) => {
        rows[item.querySelector('.attack-label').textContent] =
            item.querySelector('.attack-value').textContent;
    });
    return rows;
}

beforeEach(() => {
    fetch.mockReset();
    alert.mockClear();
});

describe('binding', () => {
    test('does nothing when the panel rendered no form', async () => {
        await loadPanel({ options: { withForm: false } });
        expect(document.getElementById('attack-form')).toBeNull();
        expect(fetch).not.toHaveBeenCalled();
    });

    test('does not fetch until Resolve is clicked', async () => {
        await loadPanel();
        expect(fetch).not.toHaveBeenCalled();
    });
});

describe('the request', () => {
    test('posts every field to the configured URL with the CSRF token', async () => {
        await loadPanel();
        document.getElementById('attack-weapon').value = '2';
        document.getElementById('attack-armour-tier').value = 'Medium';
        document.getElementById('attack-stops').value = '3';
        document.getElementById('attack-shield').value = 'large_shield';
        document.getElementById('attack-dodge').value = '1';
        document.getElementById('attack-situational').value = '-2';
        await resolve();

        const [url, options] = fetch.mock.calls[0];
        expect(url).toBe(CONFIG.attackUrl);
        expect(options.method).toBe('POST');
        expect(options.headers['X-CSRFToken']).toBe('test-token');
        // No skill field: the server resolves the skill from the record
        // (issue #753), so the panel has nothing to say about it.
        expect(JSON.parse(options.body)).toEqual({
            weapon_index: '2',
            armour_tier: 'Medium',
            defender_stops: '3',
            shield_item_id: 'large_shield',
            defender_dodge: '1',
            situational: '-2',
        });
    });

    test('re-enables the button after a resolve so a second roll is possible', async () => {
        await loadPanel();
        await resolve();
        expect(document.getElementById('attack-resolve-btn').disabled).toBe(false);
    });
});

describe('rendering', () => {
    test('names the skill the server applied, with its rule', async () => {
        await loadPanel();
        await resolve();
        expect(breakdown().Skill).toBe(
            'weapon (saber) level 3 (full: the weapon it was taken for)'
        );
    });

    test('says untrained when no skill applied', async () => {
        await loadPanel({
            response: jsonResponse({
                success: true,
                attack: attackPayload({
                    skill: { name: null, level: 0, weapon_id: null, rule: 'untrained' },
                }),
            }),
        });
        await resolve();
        expect(breakdown().Skill).toBe('untrained');
    });

    test('the result box stays hidden until the first roll', async () => {
        await loadPanel();
        expect(document.getElementById('attack-result').hidden).toBe(true);
        await resolve();
        expect(document.getElementById('attack-result').hidden).toBe(false);
    });

    test('prints the server totals rather than recomputing them', async () => {
        await loadPanel();
        await resolve();
        const rows = breakdown();
        expect(rows['Target Number']).toBe('16 base + 2 shield + 1 dodge = 19');
        expect(rows['To hit']).toBe('+2 DEX +3 skill -1 strength +1 weapon +0 situational = +5');
        expect(rows['Attack roll']).toBe('d20 17 +5 = 22 vs 19');
        expect(document.getElementById('attack-outcome').textContent).toBe('hit');
    });

    test('names the injury penalty when the record carries one (issue #840)', async () => {
        const payload = attackPayload();
        payload.to_hit.injury_penalty = -2;
        payload.to_hit.total = 3;
        await loadPanel({ response: jsonResponse({ success: true, attack: payload }) });
        await resolve();
        expect(breakdown()['To hit']).toBe(
            '+2 DEX +3 skill -1 strength +1 weapon +0 situational -2 injured = +3'
        );
    });

    test('the server total wins even when it disagrees with the parts', async () => {
        /* The panel must not "fix" a total by re-adding the components — if it
           ever did, this deliberately impossible payload would render 5. */
        const payload = attackPayload();
        payload.to_hit.total = 99;
        await loadPanel({ response: jsonResponse({ success: true, attack: payload }) });
        await resolve();
        expect(breakdown()['To hit']).toContain('= +99');
        expect(breakdown()['Attack roll']).toContain('+99');
    });

    test('shows the damage rolls, the stops applied and what got through', async () => {
        await loadPanel();
        await resolve();
        const rows = breakdown();
        expect(rows['Damage dice']).toBe('[4, 3] × 1 roll(s) +0 = 7');
        expect(rows['Armour']).toBe('3 of 3 stops applied');
        expect(rows['Damage through']).toBe('4');
        expect(rows['Reaches Body']).toBe('no');
    });

    test('names the hybrid bypass when the server flags it', async () => {
        const payload = attackPayload();
        payload.damage.hybrid_bypass = true;
        payload.damage.applied_stops = 1;
        await loadPanel({ response: jsonResponse({ success: true, attack: payload }) });
        await resolve();
        expect(breakdown()['Armour']).toBe('1 of 3 stops applied (hybrid bypass)');
    });

    test('renders the confirmation line on a confirmed severe critical', async () => {
        const payload = attackPayload({
            confirm: { die: 18, log: 'd20: 18', total: 23, hit: true },
            severe: true,
            bleeding: true,
            // Only a severe critical reaches Body (issue #293).
            reaches_body: true,
        });
        payload.attack_roll.outcome = 'severe critical';
        payload.damage.multiplier = 3;
        await loadPanel({ response: jsonResponse({ success: true, attack: payload }) });
        await resolve();
        const rows = breakdown();
        expect(rows.Confirmation).toBe('d20 18 = 23 — confirmed');
        expect(rows.Severe).toBeDefined();
        expect(rows.Bleeding).toBeDefined();
        expect(rows['Reaches Body']).toBe('yes');
    });

    test('says so when the confirmation fails', async () => {
        const payload = attackPayload({
            confirm: { die: 2, log: 'd20: 2', total: 7, hit: false },
        });
        await loadPanel({ response: jsonResponse({ success: true, attack: payload }) });
        await resolve();
        expect(breakdown().Confirmation).toBe('d20 2 = 7 — not confirmed');
    });

    test('renders the fumble subtable result and its effect', async () => {
        const payload = attackPayload({
            fumble: { die: 6, log: '1d6: 6', key: 'weapon_stress', label: 'Weapon takes stress', effect: 'Breaks on a second fumble' },
        });
        payload.attack_roll.outcome = 'fumble';
        payload.damage.multiplier = 0;
        await loadPanel({ response: jsonResponse({ success: true, attack: payload }) });
        await resolve();
        expect(breakdown().Fumble).toBe('d6 6 — Weapon takes stress (Breaks on a second fumble)');
    });

    test('a fumble band with no printed effect renders no parenthetical', async () => {
        const payload = attackPayload({
            fumble: { die: 4, log: '1d6: 4', key: 'drop_weapon', label: 'Drop weapon', effect: '' },
        });
        await loadPanel({ response: jsonResponse({ success: true, attack: payload }) });
        await resolve();
        expect(breakdown().Fumble).toBe('d6 4 — Drop weapon');
    });

    test('a miss shows no damage rows at all', async () => {
        const payload = attackPayload();
        payload.attack_roll = { die: 3, log: 'd20: 3', total: 8, outcome: 'miss', hit: false, critical: false, fumble: false };
        payload.damage = { rolls: [], multiplier: 0, raw_total: 0, weapon_mod: 0, stops: 3, applied_stops: 3, hybrid_bypass: false, final: 0 };
        payload.reaches_body = false;
        await loadPanel({ response: jsonResponse({ success: true, attack: payload }) });
        await resolve();
        const rows = breakdown();
        expect(rows['Damage dice']).toBeUndefined();
        expect(rows['Reaches Body']).toBeUndefined();
    });

    test('lists every dice-log line the server sent', async () => {
        await loadPanel();
        await resolve();
        const lines = [...document.querySelectorAll('#attack-dice-log li')].map((item) => item.textContent);
        expect(lines).toEqual(attackPayload().dice_log);
    });

    test('a hostile weapon name is text, never markup', async () => {
        const payload = attackPayload({
            fumble: { die: 1, log: '1d6: 1', key: 'off_balance', label: '<img src=x onerror=alert(1)>', effect: '' },
        });
        await loadPanel({ response: jsonResponse({ success: true, attack: payload }) });
        await resolve();
        expect(document.querySelector('#attack-breakdown img')).toBeNull();
        expect(breakdown().Fumble).toContain('<img src=x onerror=alert(1)>');
    });

    test('a second roll replaces the first rather than appending to it', async () => {
        await loadPanel();
        await resolve();
        await resolve();
        expect(document.querySelectorAll('#attack-breakdown li').length).toBe(breakdownRowCount());
    });

    function breakdownRowCount() {
        return Object.keys(breakdown()).length;
    }
});

describe('errors', () => {
    test('shows the server error message and renders nothing', async () => {
        await loadPanel({
            response: jsonResponse({ error: 'That item is not a weapon' }, { ok: false }),
        });
        await resolve();
        expect(alert).toHaveBeenCalledWith('That item is not a weapon');
        expect(document.getElementById('attack-result').hidden).toBe(true);
    });

    test('falls back to a generic message when the body carries none', async () => {
        await loadPanel({ response: jsonResponse({}, { ok: false }) });
        await resolve();
        expect(alert).toHaveBeenCalledWith('Could not resolve the attack.');
    });

    test('treats a 200 without success as a failure', async () => {
        await loadPanel({ response: jsonResponse({ success: false, error: 'nope' }) });
        await resolve();
        expect(alert).toHaveBeenCalledWith('nope');
    });

    test('surfaces a network failure instead of hanging the button', async () => {
        await loadPanel();
        fetch.mockImplementation(() => Promise.reject(new Error('offline')));
        await resolve();
        expect(alert).toHaveBeenCalledWith('Could not resolve the attack: offline');
        expect(document.getElementById('attack-resolve-btn').disabled).toBe(false);
    });
});

describe('Recent attacks (issue #792)', () => {
    test('a resolved attack puts its line at the top of the log and hides the empty line', async () => {
        const entry = '<li class="combat-log-entry"><span class="combat-log-weapon">Saber</span></li>';
        await loadPanel({
            response: jsonResponse({ success: true, attack: attackPayload(), combat_log_entry_html: entry }),
        });
        document.body.insertAdjacentHTML('beforeend', `
            <ul class="combat-log-list" id="combat-log-list">
                <li class="combat-log-entry"><span class="combat-log-weapon">Great Hammer</span></li>
            </ul>
            <p class="no-combat-log">No attacks resolved yet.</p>`);
        await resolve();

        const weapons = [...document.querySelectorAll('.combat-log-entry .combat-log-weapon')]
            .map((span) => span.textContent);
        expect(weapons).toEqual(['Saber', 'Great Hammer']);
        expect(document.querySelector('.no-combat-log').hidden).toBe(true);
    });

    test('the live log keeps no more lines than the record does', async () => {
        const entry = '<li class="combat-log-entry"><span class="combat-log-weapon">Saber</span></li>';
        await loadPanel({
            config: { maxCombatLogEntries: 2 },
            response: jsonResponse({ success: true, attack: attackPayload(), combat_log_entry_html: entry }),
        });
        document.body.insertAdjacentHTML('beforeend', `
            <ul id="combat-log-list">
                <li class="combat-log-entry">old one</li><li class="combat-log-entry">old two</li>
            </ul>`);
        await resolve();
        const lines = [...document.querySelectorAll('#combat-log-list li')].map((li) => li.textContent);
        expect(lines).toEqual(['Saber', 'old one']);
    });

    test('a refused attack adds no line', async () => {
        await loadPanel({
            response: jsonResponse({ success: false, error: 'not complete' }, { ok: false }),
        });
        document.body.insertAdjacentHTML('beforeend',
            '<ul id="combat-log-list"></ul><p class="no-combat-log">No attacks resolved yet.</p>');
        await resolve();
        expect(document.querySelectorAll('#combat-log-list li')).toHaveLength(0);
        expect(document.querySelector('.no-combat-log').hidden).toBe(false);
    });
});
