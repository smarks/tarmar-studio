/**
 * Unit tests for battle.js — the battle detail page's arena/roster/log
 * rendering and the Advance turn wiring.
 */

const { flush } = require('./test-helpers');

let battle;

// What {% battle_status_labels %} embeds: Battle.STATUS_CHOICES, as the page
// hands them to battle.js (#338 item 20).
const STATUS_CHOICES = {
    lobby: 'Lobby',
    setup: 'Setup',
    paused: 'Paused',
    awaiting: 'Awaiting players',
    finished: 'Finished',
    stalemate: 'Stalemate',
};

/**
 * One combatant as the server's view model describes it (battle/viewmodel.py):
 * position and footprint in axial cells, facing as the step to the faced hex,
 * pools by name, and the roster line already written.
 */
function sampleCombatant(overrides = {}) {
    const pools = overrides.pools || [
        { key: 'fatigue', label: 'fatigue', current: 20, maximum: 40 },
        { key: 'body', label: 'body', current: 27, maximum: 27 },
    ];
    const base = Object.assign({
        combatant_id: 1,
        name: 'Duel 1 Armoured Fighter',
        q: 0,
        r: 0,
        footprint: [[0, 0]],
        facing_vector: [1, 0],
        alive: true,
        conscious: true,
        is_beast: false,
        fatal_chain: [],
    }, overrides, { pools });
    // The status line is the server's (viewmodel._status); mirrored here so a
    // fixture that downs a combatant cannot forget to say so.
    if (base.status === undefined) {
        base.status = !base.alive ? 'Dead'
            : !base.conscious ? 'Unconscious'
            : 'armoured fighter — Broadsword';
    }
    return base;
}

/** A radius-1 pointy-top field: the origin and its six neighbours. */
const SAMPLE_CELLS = [
    [0, 0], [1, 0], [1, -1], [0, -1], [-1, 0], [-1, 1], [0, 1],
];

function sampleState(combatants) {
    return {
        profile: 'tarmar',
        turn: 1,
        next_sequence: 1,
        arena: { orientation: 'pointy', cells: SAMPLE_CELLS },
        combatants,
    };
}

beforeEach(() => {
    jest.resetModules();
    document.body.innerHTML = '';
    battle = require('../battle.js');
});

describe('geometry helpers', () => {
    test('axialToPixel maps the origin to (0, 0)', () => {
        expect(battle.axialToPixel(0, 0, 'pointy')).toEqual({ x: 0, y: 0 });
        expect(battle.axialToPixel(0, 0, 'flat')).toEqual({ x: 0, y: 0 });
    });

    test('axialToPixel moves right along +q in both orientations', () => {
        const pointy = battle.axialToPixel(2, 0, 'pointy');
        expect(pointy.x).toBeGreaterThan(0);
        expect(pointy.y).toBe(0);
        const flat = battle.axialToPixel(2, 0, 'flat');
        expect(flat.x).toBeGreaterThan(0);
    });

    test('a flat-top column steps down, a pointy-top row steps across', () => {
        // The one thing orientation changes: which axis the offset runs on.
        expect(battle.axialToPixel(0, 1, 'flat').x).toBe(0);
        expect(battle.axialToPixel(0, 1, 'pointy').x).toBeGreaterThan(0);
    });

    test('hexCorners returns six points in either orientation', () => {
        expect(battle.hexCorners(0, 0, 10, 'pointy').split(' ')).toHaveLength(6);
        expect(battle.hexCorners(0, 0, 10, 'flat').split(' ')).toHaveLength(6);
    });

    test('a pointy hex is wider than tall and a flat one taller than wide', () => {
        expect(battle.hexHalfSize('pointy').x)
            .toBeLessThan(battle.hexHalfSize('pointy').y);
        expect(battle.hexHalfSize('flat').x)
            .toBeGreaterThan(battle.hexHalfSize('flat').y);
    });

    test('arenaViewBox frames the cells it is given', () => {
        // Four numbers, and a box wide enough to hold the far cell.
        const box = battle.arenaViewBox([[0, 0], [3, 0]], 'pointy')
            .split(' ').map(Number);
        expect(box).toHaveLength(4);
        const far = battle.axialToPixel(3, 0, 'pointy');
        expect(box[0]).toBeLessThan(0);
        expect(box[0] + box[2]).toBeGreaterThan(far.x);
    });

    test('arenaViewBox falls back rather than dividing by an empty field', () => {
        // A lobby battle has no cells yet; the panel still needs a viewBox.
        expect(battle.arenaViewBox([], 'pointy')).toBe('0 0 100 100');
    });

    test('poolFraction clamps to [0, 1] and handles zero maxima', () => {
        expect(battle.poolFraction(5, 10)).toBe(0.5);
        expect(battle.poolFraction(-3, 10)).toBe(0);
        expect(battle.poolFraction(15, 10)).toBe(1);
        expect(battle.poolFraction(5, 0)).toBe(0);
    });

    test('shortName drops the battle-name prefix', () => {
        expect(battle.shortName('Big Brawl 3 Archer')).toBe('3 Archer');
        expect(battle.shortName('Unprefixed')).toBe('Unprefixed');
    });

    test('optionLetter shows the rules letter for the engine variant keys', () => {
        // tarmar-engine 0.9.5's keys: Sprint is MOVE (a); a yielded option
        // keeps its own letter; everything else is printed as it comes.
        expect(battle.optionLetter('sprint')).toBe('a');
        expect(battle.optionLetter('sprint_yield')).toBe('a');
        expect(battle.optionLetter('a_yield')).toBe('a');
        expect(battle.optionLetter('b_yield')).toBe('b');
        expect(battle.optionLetter('c_yield')).toBe('c');
        expect(battle.optionLetter('l')).toBe('l');
        expect(battle.optionLetter('hold_still')).toBe('hold_still');
    });

    test('a beast name without the prefix pattern passes through whole', () => {
        expect(battle.shortName('Cave Bear')).toBe('Cave Bear');
    });

    test('a footprint is painted as sent, not re-derived', () => {
        // The engine works out which hexes a multi-hex figure covers
        // (battle/engine/hexes.py); this page paints that list. The geometry
        // itself is pinned in battle/tests/test_viewmodel.py.
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        battle.renderArena(svg, sampleState([
            sampleCombatant({ footprint: [[0, 0], [-1, 0], [0, -1]] }),
        ]));
        expect(svg.querySelectorAll('.footprint-hex')).toHaveLength(3);
    });
});

describe('renderArena', () => {
    test('draws a marker with facing wedge and bars per living combatant', () => {
        document.body.innerHTML = '<svg id="arena"></svg>';
        const svg = document.getElementById('arena');
        battle.renderArena(svg, sampleState([
            sampleCombatant(),
            sampleCombatant({ combatant_id: 2, q: 2, r: -1, alive: false }),
        ]));
        const markers = svg.querySelectorAll('.arena-combatant');
        expect(markers).toHaveLength(1); // the dead one is not drawn
        expect(markers[0].querySelector('.facing-wedge')).not.toBeNull();
        expect(markers[0].querySelector('.pool-fatigue')).not.toBeNull();
        expect(markers[0].querySelector('.pool-body')).not.toBeNull();
        expect(markers[0].querySelector('text').textContent)
            .toBe('1 Armoured Fighter');
    });

    test('re-render replaces previous content (state-driven)', () => {
        document.body.innerHTML = '<svg id="arena"></svg>';
        const svg = document.getElementById('arena');
        battle.renderArena(svg, sampleState([sampleCombatant()]));
        battle.renderArena(svg, sampleState([sampleCombatant()]));
        expect(svg.querySelectorAll('.arena-combatant')).toHaveLength(1);
    });

    test('a multi-hex beast paints its whole footprint', () => {
        document.body.innerHTML = '<svg id="arena"></svg>';
        const svg = document.getElementById('arena');
        battle.renderArena(svg, sampleState([
            sampleCombatant({
                combatant_id: 5, name: 'Cave Bear', status: 'beast — Claws',
                is_beast: true, q: 1, r: 0,
                footprint: [[1, 0], [0, 0], [0, 1]],
            }),
        ]));
        const marker = svg.querySelector('.arena-combatant');
        expect(marker.classList.contains('is-beast')).toBe(true);
        expect(marker.querySelectorAll('.footprint-hex')).toHaveLength(3);
        expect(marker.querySelector('circle').getAttribute('fill'))
            .toBe('#8a5a2b');
        expect(marker.querySelector('text').textContent).toBe('Cave Bear');
    });

    test('a one-hex character paints no footprint hexes', () => {
        document.body.innerHTML = '<svg id="arena"></svg>';
        const svg = document.getElementById('arena');
        battle.renderArena(svg, sampleState([sampleCombatant()]));
        expect(svg.querySelectorAll('.footprint-hex')).toHaveLength(0);
        expect(svg.querySelector('.arena-combatant circle').getAttribute('fill'))
            .toBe('#4a6da7');
    });
});

describe('ground weapons (#878)', () => {
    test('each hex with a weapon lying in it is drawn and named', () => {
        document.body.innerHTML = '<svg id="arena"></svg>';
        const svg = document.getElementById('arena');
        battle.renderArena(svg, Object.assign(sampleState([sampleCombatant()]), {
            ground_weapons: [
                { q: 1, r: 0, weapons: ['Broadsword', 'Small Bow'] },
                { q: -1, r: 1, weapons: ['Dagger'] },
            ],
        }));
        const lying = svg.querySelectorAll('.arena-ground-weapon');
        expect(lying).toHaveLength(2);
        expect(lying[0].getAttribute('data-q')).toBe('1');
        expect(lying[0].querySelector('title').textContent)
            .toBe('Lying here: Broadsword, Small Bow');
        expect(lying[0].getAttribute('aria-label'))
            .toBe('Lying here: Broadsword, Small Bow');
        expect(lying[0].querySelector('.ground-weapon-count').textContent).toBe('2');
        expect(lying[1].querySelector('title').textContent).toBe('Lying here: Dagger');
        expect(lying[1].querySelector('.ground-weapon-count')).toBeNull();
        // Drawn after the figures, so a figure on the hex never covers it.
        expect(svg.lastElementChild.classList.contains('arena-ground-weapon'))
            .toBe(true);
    });

    test('a view without ground weapons draws none', () => {
        document.body.innerHTML = '<svg id="arena"></svg>';
        const svg = document.getElementById('arena');
        battle.renderArena(svg, sampleState([sampleCombatant()]));
        expect(svg.querySelectorAll('.arena-ground-weapon')).toHaveLength(0);
    });

    test('groundWeaponLabel lists the weapons in the order they lie', () => {
        expect(battle.groundWeaponLabel({ weapons: ['Mace', 'Dagger'] }))
            .toBe('Lying here: Mace, Dagger');
    });
});

describe('renderRoster', () => {
    test('shows pools and marks the unconscious', () => {
        document.body.innerHTML = '<div id="roster"></div>';
        const roster = document.getElementById('roster');
        battle.renderRoster(roster, sampleState([
            sampleCombatant(),
            sampleCombatant({ combatant_id: 2, conscious: false }),
        ]));
        const cards = roster.querySelectorAll('.combatant-card');
        expect(cards).toHaveLength(2);
        expect(cards[0].textContent).toContain('fatigue 20/40');
        expect(cards[0].textContent).toContain('Broadsword');
        expect(cards[1].classList.contains('is-down')).toBe(true);
        expect(cards[1].textContent).toContain('Unconscious');
    });

    // The contrast audit exempts .pool-bar's fill as decorative (issue #732,
    // common/css_contrast.DECORATIVE_COMPONENTS). That exemption is only
    // honest while the bar holds no text, so this holds the markup to it: the
    // fill is an empty span sized by its width, and the numbers are the
    // sibling .pool-label, which the audit measures on the card's own ground.
    test('a pool bar is an empty fill; the reading is the label above it', () => {
        document.body.innerHTML = '<div id="roster"></div>';
        const roster = document.getElementById('roster');
        battle.renderRoster(roster, sampleState([sampleCombatant()]));
        const card = roster.querySelector('.combatant-card');

        const fills = card.querySelectorAll('.pool-bar span');
        expect(fills.length).toBe(2);
        fills.forEach((fill) => {
            expect(fill.textContent).toBe('');
            expect(fill.childNodes.length).toBe(0);
            expect(fill.style.width).not.toBe('');
        });
        expect(card.querySelector('.pool-bar').textContent).toBe('');
        expect(card.querySelector('.pool-label').textContent).toBe('fatigue 20/40');
    });

    test('a dead combatant links its fatal roll chain', () => {
        document.body.innerHTML = '<div id="roster"></div>';
        const roster = document.getElementById('roster');
        battle.renderRoster(roster, sampleState([
            sampleCombatant({ alive: false, fatal_chain: [41, 42, 57] }),
        ]));
        const links = roster.querySelectorAll('.fatal-chain a');
        expect(links).toHaveLength(3);
        expect(links[0].getAttribute('href')).toBe('#event-41');
        expect(links[2].textContent).toBe('#57');
    });
});

// The event markup itself is rendered by battle/_event.html and
// battle/_turn_group.html and pinned in battle/tests/test_event_partials.py
// (#504). What stays here is the insertion: fragments shaped like that
// partial land in the right turn section, once each.
function turnFragment(turn, events) {
    const items = events.map(({ sequence, type = 'phase', text }) => (
        `<li id="event-${sequence}" class="battle-event event-${type}">${text}</li>`
    )).join('');
    return {
        turn,
        html: `
            <section class="turn-group" data-turn="${turn}">
                <h3>Turn ${turn}</h3>
                <ol class="turn-events">${items}</ol>
            </section>
        `,
    };
}

describe('appendTurns', () => {
    test('appends a new turn section and drops the empty notice', () => {
        document.body.innerHTML = '<div id="log"><p id="battle-log-empty">none</p></div>';
        const log = document.getElementById('log');
        battle.appendTurns(log, [
            turnFragment(1, [
                { sequence: 1, text: 'Phase 1: Initiative' },
                { sequence: 2, type: 'roll', text: 'A initiative 1d6: [4] = 4' },
            ]),
        ]);
        expect(document.getElementById('battle-log-empty')).toBeNull();
        const section = log.querySelector('.turn-group[data-turn="1"]');
        expect(section.querySelector('h3').textContent).toBe('Turn 1');
        expect(section.querySelectorAll('.battle-event')).toHaveLength(2);
        expect(section.querySelector('#event-2').className).toContain('event-roll');
    });

    test('appends into an existing turn section without duplicating it', () => {
        document.body.innerHTML = '<div id="log"></div>';
        const log = document.getElementById('log');
        battle.appendTurns(log, [turnFragment(1, [{ sequence: 1, text: 'Phase 1' }])]);
        battle.appendTurns(log, [turnFragment(1, [{ sequence: 2, text: 'Phase 2' }])]);
        expect(log.querySelectorAll('.turn-group')).toHaveLength(1);
        expect(Array.from(log.querySelectorAll('.battle-event'), (item) => item.id))
            .toEqual(['event-1', 'event-2']);
    });

    test('keeps turns in the order sent, each in its own section', () => {
        document.body.innerHTML = '<div id="log"></div>';
        const log = document.getElementById('log');
        battle.appendTurns(log, [
            turnFragment(1, [{ sequence: 1, text: 'end of 1' }]),
            turnFragment(2, [{ sequence: 2, text: 'start of 2' }]),
        ]);
        expect(Array.from(log.querySelectorAll('.turn-group'), (group) => group.dataset.turn))
            .toEqual(['1', '2']);
    });

    test('inserts the markup as sent, links and details included', () => {
        document.body.innerHTML = '<div id="log"></div>';
        const log = document.getElementById('log');
        battle.appendTurns(log, [turnFragment(2, [{
            sequence: 90,
            type: 'death',
            text: 'B dies <span class="fatal-chain">(fatal rolls: '
                + '<a href="#event-80">#80</a>, <a href="#event-81">#81</a>)</span>',
        }])]);
        const links = log.querySelectorAll('#event-90 .fatal-chain a');
        expect(links).toHaveLength(2);
        expect(links[1].getAttribute('href')).toBe('#event-81');
    });

    test('an empty batch leaves the log and its empty notice alone', () => {
        document.body.innerHTML = '<div id="log"><p id="battle-log-empty">none</p></div>';
        const log = document.getElementById('log');
        battle.appendTurns(log, []);
        expect(document.getElementById('battle-log-empty')).not.toBeNull();
    });
});

describe('initBattlePage', () => {
    function mountDetailPage(status = 'paused') {
        document.body.innerHTML = `
            <strong id="battle-status">Paused</strong>
            <script id="battle-status-labels" type="application/json">${JSON.stringify(STATUS_CHOICES)}</script>
            <span id="battle-turn">0</span>
            <button id="advance-turn" data-url="/tarmar/battles/1/advance/"></button>
            <div id="battle-roster"></div>
            <svg id="battle-arena"></svg>
            <div id="battle-log"></div>
            <script id="battle-view" type="application/json">${JSON.stringify(sampleState([sampleCombatant()]))}</script>
        `;
        return status;
    }

    test('is a no-op on pages without the battle markup', () => {
        document.body.innerHTML = '<main></main>';
        expect(() => battle.initBattlePage()).not.toThrow();
    });

    test('renders arena and roster from the embedded state on load', () => {
        mountDetailPage();
        battle.initBattlePage();
        expect(document.querySelectorAll('.arena-combatant')).toHaveLength(1);
        expect(document.querySelectorAll('.combatant-card')).toHaveLength(1);
    });

    test('wires an arena inside the container it is given, once (#345)', () => {
        document.body.innerHTML = `
            <main></main>
            <section id="gm-panel-battle" hidden>
                <strong id="battle-status">Paused</strong>
                <span id="battle-turn">0</span>
                <div id="battle-roster"></div>
                <svg id="battle-arena"></svg>
                <div id="battle-log"></div>
                <script id="battle-view" type="application/json">${JSON.stringify(sampleState([sampleCombatant()]))}</script>
            </section>
        `;
        const container = document.getElementById('gm-panel-battle');
        battle.initBattlePage(container);
        expect(container.querySelectorAll('.arena-combatant')).toHaveLength(1);
        expect(container.querySelectorAll('.combatant-card')).toHaveLength(1);
        expect(document.getElementById('battle-arena').dataset.wired).toBe('true');
        // The document-wide pass that runs at load finds the same arena and
        // leaves it alone rather than binding its handlers twice.
        battle.initBattlePage();
        expect(container.querySelectorAll('.combatant-card')).toHaveLength(1);
    });

    test('advance click posts, appends events, re-renders, updates status', async () => {
        mountDetailPage();
        battle.initBattlePage();
        const newView = sampleState([
            sampleCombatant({
                pools: [
                    { key: 'fatigue', label: 'fatigue', current: 12, maximum: 40 },
                    { key: 'body', label: 'body', current: 27, maximum: 27 },
                ],
            }),
        ]);
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            json: async () => ({
                status: 'finished',
                turn: 1,
                view: newView,
                events: [
                    { turn: 1, phase: 1, sequence: 1, event_type: 'phase', payload: {}, message: 'Phase 1: Initiative' },
                ],
                turns_html: [turnFragment(1, [{ sequence: 1, text: 'Phase 1: Initiative' }])],
            }),
        });
        require('../api.js');
        const applied = [];
        document.addEventListener('battle-applied', (event) => applied.push(event.detail));

        document.getElementById('advance-turn').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });

        expect(global.fetch).toHaveBeenCalledWith(
            '/tarmar/battles/1/advance/', expect.objectContaining({ method: 'POST' })
        );
        // Announced, so the GM chat's Characters tab refetches (#750, #751).
        expect(applied).toEqual([{ status: 'finished' }]);
        expect(document.querySelector('.turn-group[data-turn="1"]')).not.toBeNull();
        expect(document.getElementById('battle-status').textContent).toBe('Finished');
        expect(document.getElementById('battle-turn').textContent).toBe('1');
        expect(document.getElementById('advance-turn').disabled).toBe(true);
        expect(document.querySelector('.combatant-card').textContent)
            .toContain('fatigue 12/40');
    });

    test('advance failure surfaces the server detail and re-enables the button', async () => {
        mountDetailPage();
        battle.initBattlePage();
        global.fetch = jest.fn().mockResolvedValue({
            ok: false,
            status: 500,
            json: async () => ({ error: 'RuntimeError: state_json missing footprint' }),
        });
        require('../api.js');
        global.uiNotify = jest.fn();

        document.getElementById('advance-turn').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });

        expect(global.uiNotify).toHaveBeenCalledWith(
            'Advancing the turn failed (HTTP 500: RuntimeError: state_json missing footprint).'
        );
        expect(document.getElementById('advance-turn').disabled).toBe(false);
    });

    test('a stale-pools refusal shows the notice and the control without a reload (#842)', async () => {
        mountDetailPage();
        document.body.insertAdjacentHTML('beforeend', '<div id="battle-stale-slot"></div>');
        battle.initBattlePage();
        global.fetch = jest.fn().mockResolvedValue({
            ok: false,
            status: 409,
            json: async () => ({
                error: "Aldwin: the record's pools have changed",
                advance: { label: 'Pools changed on the records', enabled: false },
                stale_html: '<p id="battle-stale-pools">Aldwin: the record changed.</p>'
                    + '<form><input type="hidden" name="next" value="/tarmar/battles/1/advance/">'
                    + '<button id="resnapshot-pools">Take the pools from the records</button></form>',
            }),
        });
        require('../api.js');
        global.uiNotify = jest.fn();

        document.getElementById('advance-turn').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });

        expect(document.getElementById('battle-stale-pools').textContent).toContain('Aldwin');
        expect(document.getElementById('resnapshot-pools')).not.toBeNull();
        // Back to the page it was pressed on, never the advance endpoint.
        expect(document.querySelector('#battle-stale-slot input[name="next"]').value)
            .toBe(`${window.location.pathname}${window.location.search}`);
        expect(document.getElementById('advance-turn').disabled).toBe(true);
        expect(document.getElementById('advance-turn').textContent).toBe('Pools changed on the records');
        expect(global.uiNotify).not.toHaveBeenCalled();
    });

    test('a response carrying the stale notice shows it, and one without clears it (#842)', async () => {
        mountDetailPage();
        document.body.insertAdjacentHTML('beforeend', '<div id="battle-stale-slot"></div>');
        battle.initBattlePage();
        const respond = (staleHtml) => ({
            ok: true,
            status: 200,
            json: async () => ({
                status: 'awaiting',
                turn: 1,
                view: sampleState([sampleCombatant()]),
                events: [],
                advance: { label: 'Pools changed on the records', enabled: false },
                stale_html: staleHtml,
            }),
        });
        require('../api.js');
        global.fetch = jest.fn().mockResolvedValue(
            respond('<p id="battle-stale-pools">Aldwin moved.</p>')
        );

        document.getElementById('advance-turn').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });
        expect(document.getElementById('battle-stale-pools')).not.toBeNull();

        global.fetch = jest.fn().mockResolvedValue(respond(''));
        document.getElementById('advance-turn').disabled = false;
        document.getElementById('advance-turn').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });
        expect(document.getElementById('battle-stale-pools')).toBeNull();
    });

    test('the chip takes its label from the page, not from battle.js', async () => {
        // #338 item 20: battle.js used to restate Battle.STATUS_CHOICES.
        mountDetailPage();
        battle.initBattlePage();
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            status: 200,
            json: async () => ({
                status: 'awaiting', turn: 1, view: sampleState([sampleCombatant()]), events: [],
            }),
        });
        require('../api.js');

        document.getElementById('advance-turn').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });

        expect(document.getElementById('battle-status').textContent).toBe('Awaiting players');
    });

    test('an HTML error page leaves just the status in the notice', async () => {
        mountDetailPage();
        battle.initBattlePage();
        global.fetch = jest.fn().mockResolvedValue({
            ok: false,
            status: 502,
            json: async () => { throw new SyntaxError('Unexpected token <'); },
        });
        require('../api.js');
        global.uiNotify = jest.fn();

        document.getElementById('advance-turn').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });

        expect(global.uiNotify).toHaveBeenCalledWith('Advancing the turn failed (HTTP 502).');
    });

    test('an accepted advance with an unreadable body is still reported as a failure', async () => {
        // #338 item 1: the poster now goes through postJson, which forgives an
        // unreadable body; the page must not treat that as a turn to render.
        mountDetailPage();
        battle.initBattlePage();
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            status: 200,
            json: async () => { throw new SyntaxError('Unexpected end of JSON input'); },
        });
        require('../api.js');
        global.uiNotify = jest.fn();

        document.getElementById('advance-turn').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });

        expect(global.uiNotify).toHaveBeenCalledWith(
            'Advancing the turn failed (Unexpected end of JSON input).'
        );
        expect(document.getElementById('advance-turn').disabled).toBe(false);
    });
});

describe('inline rename', () => {
    function rosterWithOptions(onRenamed) {
        document.body.innerHTML = '<div id="roster"></div>';
        const roster = document.getElementById('roster');
        const state = sampleState([
            sampleCombatant({ name: 'Kestra Duskmere' }),
        ]);
        const options = {
            url: '/tarmar/battles/1/rename/',
            csrf: 'tok',
            onRenamed: onRenamed || jest.fn(),
        };
        battle.renderRoster(roster, state, options);
        return { roster, state, options };
    }

    test('names are plain text without rename options', () => {
        document.body.innerHTML = '<div id="roster"></div>';
        const roster = document.getElementById('roster');
        battle.renderRoster(roster, sampleState([sampleCombatant()]));
        expect(roster.querySelector('.combatant-name')).toBeNull();
    });

    test('a figure the server says this viewer may not rename is plain text (#762)', () => {
        document.body.innerHTML = '<div id="roster"></div>';
        const roster = document.getElementById('roster');
        const state = sampleState([sampleCombatant({ combatant_id: 1 })]);
        const options = { url: '/tarmar/battles/1/rename/', csrf: 'tok', onRenamed: jest.fn() };
        battle.renderRoster(roster, state, options, { 1: { can_rename: false } });
        expect(roster.querySelector('.combatant-name')).toBeNull();
        battle.renderRoster(roster, state, options, { 1: { can_rename: true } });
        expect(roster.querySelector('.combatant-name')).not.toBeNull();
    });

    test('clicking the name swaps in an input holding the full name', () => {
        const { roster } = rosterWithOptions();
        roster.querySelector('.combatant-name').click();
        const input = roster.querySelector('.combatant-rename-input');
        expect(input).not.toBeNull();
        expect(input.value).toBe('Kestra Duskmere');
    });

    test('Enter saves through the endpoint and hands the payload to onRenamed', async () => {
        const onRenamed = jest.fn();
        const { roster } = rosterWithOptions(onRenamed);
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ success: true, name: 'Renamed Hero', view: sampleState([]) }),
        });
        require('../api.js');

        roster.querySelector('.combatant-name').click();
        const input = roster.querySelector('.combatant-rename-input');
        input.value = 'Renamed Hero';
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
        await flush();

        expect(global.fetch).toHaveBeenCalledWith(
            '/tarmar/battles/1/rename/',
            expect.objectContaining({
                method: 'POST',
                body: JSON.stringify({ combatant_id: 1, name: 'Renamed Hero' }),
            })
        );
        expect(onRenamed).toHaveBeenCalledWith(
            expect.objectContaining({ success: true, name: 'Renamed Hero' })
        );
    });

    test('Escape cancels the edit without saving', () => {
        const { roster, options } = rosterWithOptions();
        global.fetch = jest.fn();

        roster.querySelector('.combatant-name').click();
        const input = roster.querySelector('.combatant-rename-input');
        input.value = 'Discarded Draft';
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

        expect(global.fetch).not.toHaveBeenCalled();
        expect(options.onRenamed).not.toHaveBeenCalled();
        expect(roster.querySelector('.combatant-rename-input')).toBeNull();
        expect(roster.querySelector('.combatant-name').textContent)
            .toBe('Kestra Duskmere');
    });

    test('an unchanged or blank name never posts', () => {
        const { roster } = rosterWithOptions();
        global.fetch = jest.fn();

        roster.querySelector('.combatant-name').click();
        const input = roster.querySelector('.combatant-rename-input');
        input.value = '   ';
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));

        expect(global.fetch).not.toHaveBeenCalled();
        expect(roster.querySelector('.combatant-name').textContent)
            .toBe('Kestra Duskmere');
    });

    test('a server rejection shows a persistent inline error and reverts', async () => {
        const { roster, options } = rosterWithOptions();
        global.fetch = jest.fn().mockResolvedValue({
            ok: false,
            json: async () => ({
                success: false,
                error: 'Another combatant already has that name',
            }),
        });
        require('../api.js');

        roster.querySelector('.combatant-name').click();
        const input = roster.querySelector('.combatant-rename-input');
        input.value = 'Taken Name';
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
        await flush();

        const error = roster.querySelector('.autosave-error');
        expect(error).not.toBeNull();
        expect(error.textContent).toContain('already has that name');
        expect(options.onRenamed).not.toHaveBeenCalled();
        expect(roster.querySelector('.combatant-name').textContent)
            .toBe('Kestra Duskmere');
    });

    test('a network failure shows a persistent inline error and reverts', async () => {
        const { roster } = rosterWithOptions();
        global.fetch = jest.fn().mockRejectedValue(new Error('offline'));
        require('../api.js');

        roster.querySelector('.combatant-name').click();
        const input = roster.querySelector('.combatant-rename-input');
        input.value = 'Unreachable';
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
        await flush();

        expect(roster.querySelector('.autosave-error')).not.toBeNull();
        expect(roster.querySelector('.combatant-name').textContent)
            .toBe('Kestra Duskmere');
    });

    test('initBattlePage wires renames into the arena and roster from server state', async () => {
        document.body.innerHTML = `
            <strong id="battle-status">Paused</strong>
            <span id="battle-turn">0</span>
            <button id="advance-turn" data-url="/tarmar/battles/1/advance/"></button>
            <div id="battle-roster" data-rename-url="/tarmar/battles/1/rename/"></div>
            <svg id="battle-arena"></svg>
            <div id="battle-log"></div>
            <script id="battle-view" type="application/json">${JSON.stringify(sampleState([sampleCombatant({ name: 'Kestra Duskmere' })]))}</script>
        `;
        battle.initBattlePage();
        const renamedView = sampleState([
            sampleCombatant({ name: 'Torin Blackbriar' }),
        ]);
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            json: async () => ({
                success: true, name: 'Torin Blackbriar', view: renamedView,
            }),
        });
        require('../api.js');

        document.querySelector('.combatant-name').click();
        const input = document.querySelector('.combatant-rename-input');
        input.value = 'Torin Blackbriar';
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
        await flush();

        expect(document.querySelector('.combatant-name').textContent)
            .toBe('Torin Blackbriar');
        expect(document.querySelector('#battle-arena text').textContent)
            .toBe('Torin Blackbriar');
    });
});

describe('restart', () => {
    function battlePage() {
        document.body.innerHTML = `
            <strong id="battle-status">Paused</strong>
            <span id="battle-turn">3</span>
            <button id="advance-turn" data-url="/tarmar/battles/1/advance/"></button>
            <div class="battle-restart-group" id="restart-group" data-confirming="false">
                <button id="restart-battle" data-url="/tarmar/battles/1/restart/"></button>
                <button id="restart-confirm"></button>
                <button id="restart-cancel"></button>
            </div>
            <div id="battle-roster"></div>
            <svg id="battle-arena"></svg>
            <div id="battle-log">
                <section class="turn-group" data-turn="1"><h3>Turn 1</h3><ol class="turn-events"><li>hit</li></ol></section>
            </div>
            <script id="battle-view" type="application/json">${JSON.stringify(sampleState([sampleCombatant()]))}</script>
        `;
        battle.initBattlePage();
    }

    test('resetLog empties the log and inserts the notice the server sent', () => {
        document.body.innerHTML = '<div id="log"><section class="turn-group"></section></div>';
        const log = document.getElementById('log');
        battle.resetLog(log, '\n<p id="battle-log-empty">Server notice <em>as sent</em></p>\n');
        expect(log.querySelector('.turn-group')).toBeNull();
        expect(log.innerHTML)
            .toBe('<p id="battle-log-empty">Server notice <em>as sent</em></p>');
    });

    test('Restart asks in place before it fires', () => {
        battlePage();
        global.fetch = jest.fn();
        expect(document.getElementById('restart-group').dataset.confirming)
            .toBe('false');

        document.getElementById('restart-battle').click();

        expect(document.getElementById('restart-group').dataset.confirming)
            .toBe('true');
        expect(global.fetch).not.toHaveBeenCalled();
    });

    test('cancelling leaves the battle and the log alone', () => {
        battlePage();
        global.fetch = jest.fn();
        document.getElementById('restart-battle').click();
        document.getElementById('restart-cancel').click();

        expect(document.getElementById('restart-group').dataset.confirming)
            .toBe('false');
        expect(global.fetch).not.toHaveBeenCalled();
        expect(document.querySelector('.turn-group')).not.toBeNull();
    });

    test('confirming clears the log and rewinds the readouts', async () => {
        battlePage();
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            json: async () => ({
                status: 'setup',
                turn: 0,
                view: sampleState([sampleCombatant()]),
                events: [],
                turns_html: [],
                empty_log_html: '<p id="battle-log-empty">Notice from the server.</p>\n',
            }),
        });
        require('../api.js');

        document.getElementById('restart-battle').click();
        document.getElementById('restart-confirm').click();
        await flush();

        expect(global.fetch.mock.calls[0][0]).toBe('/tarmar/battles/1/restart/');
        expect(document.querySelector('.turn-group')).toBeNull();
        expect(document.getElementById('battle-log').innerHTML)
            .toBe('<p id="battle-log-empty">Notice from the server.</p>');
        expect(document.getElementById('battle-turn').textContent).toBe('0');
        expect(document.getElementById('battle-status').textContent).toBe('Setup');
        expect(document.getElementById('restart-group').dataset.confirming)
            .toBe('false');
    });

    test('a rejected restart keeps the log and says why', async () => {
        battlePage();
        global.fetch = jest.fn().mockResolvedValue({
            ok: false,
            status: 400,
            json: async () => ({ error: 'no starting snapshot' }),
        });
        require('../api.js');
        window.showNotice = jest.fn();

        document.getElementById('restart-battle').click();
        document.getElementById('restart-confirm').click();
        await flush();

        expect(document.querySelector('.turn-group')).not.toBeNull();
        expect(document.getElementById('battle-turn').textContent).toBe('3');
        expect(window.showNotice).toHaveBeenCalledWith(
            expect.stringContaining('Restarting the battle failed'), 'error'
        );
    });

    test('a finished battle disables Advance after a response says so', async () => {
        battlePage();
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            json: async () => ({
                status: 'finished',
                turn: 4,
                view: sampleState([sampleCombatant()]),
                events: [],
            }),
        });
        require('../api.js');

        document.getElementById('advance-turn').click();
        await flush();

        expect(document.getElementById('advance-turn').disabled).toBe(true);
    });

    test('a stalemate disables Advance and labels the chip (#289)', async () => {
        battlePage();
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            json: async () => ({
                status: 'stalemate',
                turn: 30,
                view: sampleState([sampleCombatant()]),
                events: [],
            }),
        });
        require('../api.js');

        document.getElementById('advance-turn').click();
        await flush();

        expect(document.getElementById('advance-turn').disabled).toBe(true);
        expect(document.getElementById('battle-status').textContent).toBe('Stalemate');
    });
});

describe('status and seat helpers (#250)', () => {
    test('statusLabel maps server codes to the labels the page embeds', () => {
        // The labels are Battle.STATUS_CHOICES from {% battle_status_labels %}
        // (#338 item 20); battle.js no longer keeps its own table.
        expect(battle.statusLabel('awaiting', STATUS_CHOICES)).toBe('Awaiting players');
        expect(battle.statusLabel('lobby', STATUS_CHOICES)).toBe('Lobby');
        expect(battle.statusLabel('stalemate', STATUS_CHOICES)).toBe('Stalemate');
        // A code the page did not label still reads, capitalised.
        expect(battle.statusLabel('mystery', STATUS_CHOICES)).toBe('Mystery');
        expect(battle.statusLabel('paused')).toBe('Paused');
    });

    test('seatsById keys seat summaries by combatant id', () => {
        const map = battle.seatsById([
            { combatant_id: 3, player: 'sam' },
            { combatant_id: 5, player: '' },
        ]);
        expect(map[3].player).toBe('sam');
        expect(map[5].player).toBe('');
    });

    test('topCandidateIndex picks the highest score, first on ties', () => {
        expect(battle.topCandidateIndex([
            { score: 0.4 }, { score: 1.9 }, { score: 1.9 },
        ])).toBe(1);
        expect(battle.topCandidateIndex([{ score: 0 }])).toBe(0);
    });

    test('lobbyEditing spots open claim forms and focused controls', () => {
        document.body.innerHTML = `
            <div id="battle-lobby">
              <details><summary>Claim</summary></details>
              <select id="inside"></select>
            </div>
            <input id="outside">
        `;
        const lobby = document.getElementById('battle-lobby');
        expect(battle.lobbyEditing(lobby, null)).toBe(false);
        expect(battle.lobbyEditing(lobby, document.getElementById('outside')))
            .toBe(false);
        expect(battle.lobbyEditing(lobby, document.getElementById('inside')))
            .toBe(true);
        lobby.querySelector('details').setAttribute('open', '');
        expect(battle.lobbyEditing(lobby, null)).toBe(true);
    });
});

describe('roster controller badges (#250)', () => {
    test('a player-controlled combatant names its player', () => {
        document.body.innerHTML = '<div id="roster"></div>';
        const container = document.getElementById('roster');
        battle.renderRoster(
            container,
            sampleState([
                sampleCombatant({ combatant_id: 1 }),
                sampleCombatant({ combatant_id: 2, name: 'Duel 2 Archer' }),
            ]),
            undefined,
            {
                1: { combatant_id: 1, player: 'sam', is_you: false },
                2: { combatant_id: 2, player: '', is_you: false },
            }
        );
        const cards = container.querySelectorAll('.combatant-card');
        expect(cards[0].textContent).toContain('Played by sam');
        expect(cards[1].textContent).not.toContain('Played by');
    });

    test('your own combatant reads "Played by you"', () => {
        document.body.innerHTML = '<div id="roster"></div>';
        const container = document.getElementById('roster');
        battle.renderRoster(
            container,
            sampleState([sampleCombatant({ combatant_id: 1 })]),
            undefined,
            { 1: { combatant_id: 1, player: 'sam', is_you: true } }
        );
        expect(container.textContent).toContain('Played by you');
    });
});

describe('renderTurnPanel (#250)', () => {
    function samplePending(overrides = {}) {
        return Object.assign({
            combatant_id: 2,
            turn: 3,
            display_name: 'Kestra Duskmere',
            player: 'playeruser',
            is_you: true,
            candidates: [
                { letter: 'a', name: 'MOVE', score: 0.5,
                  rationale: 'Close the gap', target_id: 1, spell_key: '' },
                { letter: 'j', name: 'ATTACK', score: 1.9,
                  rationale: 'P(hit) 60%', target_id: 1, spell_key: '' },
            ],
        }, overrides);
    }

    function panelNode() {
        document.body.innerHTML = '<div id="turn-panel" hidden></div>';
        return document.getElementById('turn-panel');
    }

    test('no pending decision hides the panel', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, null, {});
        expect(panel.hidden).toBe(true);
        expect(panel.textContent).toBe('');
    });

    test('your turn renders every option with advice and the AI pick tag', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, samplePending(), { selectedIndex: null });
        expect(panel.hidden).toBe(false);
        expect(panel.textContent).toContain('Your move');
        expect(panel.textContent).toContain('Kestra Duskmere');
        const options = panel.querySelectorAll('.turn-option');
        expect(options).toHaveLength(2);
        expect(options[0].textContent).toContain('MOVE');
        expect(options[0].textContent).toContain('score 0.5 — Close the gap');
        // The higher-scored ATTACK carries the AI pick tag; MOVE does not.
        expect(options[1].querySelector('.turn-option-ai-pick')).not.toBeNull();
        expect(options[0].querySelector('.turn-option-ai-pick')).toBeNull();
    });

    /** A Tarmar menu as the server describes it (#788), two targets (#775). */
    function describedPending() {
        return samplePending({
            candidates: [
                { letter: 'j', name: 'ATTACK', score: 0.178, score_text: '0.178',
                  rationale: 'Strike Gray Wolf: bare hands ...', target_id: 1,
                  spell_key: '', ai_choice: false,
                  plain: 'Attack Gray Wolf with bare hands: 2 in 20 to hit, '
                      + '1d6-1 less 1 armour (about 2 a hit)',
                  cost: '', target_name: 'Gray Wolf' },
                { letter: 'j', name: 'ATTACK', score: 0.2, score_text: '0.200',
                  rationale: 'Strike Dire Wolf: bare hands ...', target_id: 4,
                  spell_key: '', ai_choice: false,
                  plain: 'Attack Dire Wolf with bare hands: 3 in 20 to hit, '
                      + '1d6-1 less 1 armour (about 1 a hit)',
                  cost: '', target_name: 'Dire Wolf' },
                { letter: 'a', name: 'MOVE', score: 0.5, score_text: '0.500',
                  rationale: 'Close the gap', target_id: 1, spell_key: '',
                  ai_choice: true, plain: 'Run up to 6 hexes toward Gray Wolf',
                  cost: '1 Fatigue', target_name: 'Gray Wolf' },
            ],
        });
    }

    test('a described option leads with its plain line and cost, not the score', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, describedPending(), { selectedIndex: null });
        const options = panel.querySelectorAll('.turn-option');
        expect(options).toHaveLength(3);
        expect(options[0].querySelector('.turn-option-plain').textContent)
            .toBe('Attack Gray Wolf with bare hands: 2 in 20 to hit, '
                + '1d6-1 less 1 armour (about 2 a hit)');
        expect(options[0].querySelector('.turn-option-cost').textContent)
            .toBe('Costs nothing.');
        expect(options[2].querySelector('.turn-option-cost').textContent)
            .toBe('Costs 1 Fatigue.');
        // The scoring is not on the rows until the reader asks for it.
        expect(panel.querySelector('.turn-option-advice')).toBeNull();
        const toggle = panel.querySelector('.turn-scoring-toggle');
        expect(toggle.textContent).toBe("Show the AI's scoring");
        expect(toggle.getAttribute('aria-pressed')).toBe('false');
    });

    test('the scoring switch shows every row its score and working', () => {
        const panel = panelNode();
        const onToggleScoring = jest.fn();
        battle.renderTurnPanel(panel, describedPending(), {
            selectedIndex: null, showScoring: true, onToggleScoring,
        });
        const advice = panel.querySelectorAll('.turn-option-advice');
        expect(advice).toHaveLength(3);
        expect(advice[0].textContent).toBe('score 0.178 — Strike Gray Wolf: bare hands ...');
        const toggle = panel.querySelector('.turn-scoring-toggle');
        expect(toggle.getAttribute('aria-pressed')).toBe('true');
        expect(toggle.textContent).toBe("Hide the AI's scoring");
        toggle.click();
        expect(onToggleScoring).toHaveBeenCalled();
    });

    test('the AI pick is the option the server marked, not the best score', () => {
        // The Dire Wolf strike outscores the AI's own choice; the server's
        // mark is what the AI chose (#775).
        const panel = panelNode();
        battle.renderTurnPanel(panel, describedPending(), { selectedIndex: null });
        const options = panel.querySelectorAll('.turn-option');
        expect(options[2].querySelector('.turn-option-ai-pick')).not.toBeNull();
        expect(options[1].querySelector('.turn-option-ai-pick')).toBeNull();
        expect(battle.topCandidateIndex(describedPending().candidates)).toBe(2);
    });

    test('each target of an attack is its own row with its own target', () => {
        const panel = panelNode();
        const onConfirm = jest.fn();
        battle.renderTurnPanel(panel, describedPending(), {
            selectedIndex: 1, onConfirm,
        });
        panel.querySelector('.turn-confirm').click();
        expect(onConfirm).toHaveBeenCalledWith(
            expect.objectContaining({ letter: 'j', target_id: 4 })
        );
    });

    test('selection state and confirm gating are data-driven', () => {
        const panel = panelNode();
        const onSelect = jest.fn();
        battle.renderTurnPanel(panel, samplePending(), {
            selectedIndex: null, onSelect,
        });
        expect(panel.querySelector('.turn-confirm').disabled).toBe(true);
        panel.querySelectorAll('.turn-option')[1].click();
        expect(onSelect).toHaveBeenCalledWith(1);

        battle.renderTurnPanel(panel, samplePending(), { selectedIndex: 1 });
        const options = panel.querySelectorAll('.turn-option');
        expect(options[1].getAttribute('aria-pressed')).toBe('true');
        expect(options[0].getAttribute('aria-pressed')).toBe('false');
        expect(panel.querySelector('.turn-confirm').disabled).toBe(false);
    });

    test('confirm hands the selected candidate to onConfirm', () => {
        const panel = panelNode();
        const onConfirm = jest.fn();
        battle.renderTurnPanel(panel, samplePending(), {
            selectedIndex: 1, onConfirm,
        });
        panel.querySelector('.turn-confirm').click();
        expect(onConfirm).toHaveBeenCalledWith(
            expect.objectContaining({ letter: 'j', target_id: 1 })
        );
    });

    test('someone else\'s turn shows a waiting notice, not the menu', () => {
        const panel = panelNode();
        battle.renderTurnPanel(
            panel, samplePending({ is_you: false }), { isDm: false }
        );
        expect(panel.textContent).toContain('Waiting for Kestra Duskmere');
        expect(panel.textContent).toContain('played by playeruser');
        expect(panel.querySelector('.turn-option')).toBeNull();
        expect(panel.querySelector('.turn-ai-choose')).toBeNull();
    });

    test('the DM gets the let-the-AI-choose escape hatch', () => {
        const panel = panelNode();
        const onAiChoose = jest.fn();
        battle.renderTurnPanel(
            panel, samplePending({ is_you: false }), { isDm: true, onAiChoose }
        );
        const aiButton = panel.querySelector('.turn-ai-choose');
        expect(aiButton).not.toBeNull();
        aiButton.click();
        expect(onAiChoose).toHaveBeenCalled();
    });
});

describe('turn panel wiring on the battle page (#250)', () => {
    function mountAwaitingPage(pending) {
        document.body.innerHTML = `
            <strong id="battle-status">Awaiting players</strong>
            <span id="battle-turn">0</span>
            <div id="battle-roster"></div>
            <svg id="battle-arena"></svg>
            <div id="battle-log"></div>
            <div id="turn-panel" hidden
                 data-act-url="/tarmar/battles/1/act/"
                 data-state-url="/tarmar/battles/1/state/"
                ></div>
            <script id="battle-view" type="application/json">${JSON.stringify(sampleState([sampleCombatant()]))}</script>
            <script id="battle-seats" type="application/json">[]</script>
            <script id="battle-pending" type="application/json">${JSON.stringify(pending)}</script>
        `;
    }

    test('a spectator page renders the waiting notice and no controls', () => {
        // The spectator flow (#250): pending is_you false, no isDm flag, no
        // rename url — the page mounts read-only, options and escape hatch
        // absent.
        mountAwaitingPage({
            combatant_id: 1, turn: 1, display_name: 'Duel 1 Armoured Fighter',
            player: 'playeruser', is_you: false, candidates: [],
        });
        battle.initBattlePage();
        const panel = document.getElementById('turn-panel');
        expect(panel.hidden).toBe(false);
        expect(panel.textContent).toContain('Waiting for');
        expect(panel.querySelector('.turn-option')).toBeNull();
        expect(panel.querySelector('.turn-ai-choose')).toBeNull();
        expect(document.querySelector('.combatant-name')).toBeNull();
    });

    test('a pending decision for you renders the menu on load', () => {
        mountAwaitingPage({
            combatant_id: 1, turn: 1, display_name: 'Duel 1 Armoured Fighter',
            player: 'playeruser', is_you: true,
            candidates: [{ letter: 'a', name: 'MOVE', score: 0.5,
                           rationale: 'advice', target_id: null, spell_key: '' }],
        });
        battle.initBattlePage();
        const panel = document.getElementById('turn-panel');
        expect(panel.hidden).toBe(false);
        expect(panel.textContent).toContain('Your move');
    });

    test('confirming posts the choice and applies the completed turn', async () => {
        mountAwaitingPage({
            combatant_id: 1, turn: 1, display_name: 'Duel 1 Armoured Fighter',
            player: 'playeruser', is_you: true,
            candidates: [{ letter: 'j', name: 'ATTACK', score: 1.9,
                           rationale: 'advice', target_id: 2, spell_key: '' }],
        });
        battle.initBattlePage();
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            json: async () => ({
                status: 'paused',
                turn: 1,
                view: sampleState([sampleCombatant({
                    pools: [
                        { key: 'fatigue', label: 'fatigue', current: 9, maximum: 40 },
                        { key: 'body', label: 'body', current: 27, maximum: 27 },
                    ],
                })]),
                events: [{ turn: 1, phase: 1, sequence: 1, event_type: 'phase',
                           payload: {}, message: 'Phase 1: Initiative' }],
                turns_html: [turnFragment(1, [{ sequence: 1, text: 'Phase 1: Initiative' }])],
                seats: [],
                pending: null,
            }),
        });
        require('../api.js');

        const panel = document.getElementById('turn-panel');
        panel.querySelector('.turn-option').click();
        panel.querySelector('.turn-confirm').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });

        expect(global.fetch).toHaveBeenCalledWith(
            '/tarmar/battles/1/act/', expect.objectContaining({ method: 'POST' })
        );
        const sent = JSON.parse(global.fetch.mock.calls[0][1].body);
        // The body names the pause it answered (#644). Without that the
        // server applies the choice to whoever is awaited when it lands —
        // and this assertion used to pass while it did.
        expect(sent).toEqual({
            combatant_id: 1, turn: 1, letter: 'j', target_id: 2, spell_key: '',
        });
        expect(panel.hidden).toBe(true);
        expect(document.getElementById('battle-status').textContent).toBe('Paused');
        expect(document.querySelector('.turn-group[data-turn="1"]')).not.toBeNull();
    });
});

describe('the turn panel keeps keyboard focus (#646)', () => {
    // The action menu is the one form a seated player must use to take a
    // turn, and every selection empties the panel and rebuilds every
    // button. The button the person just activated left the document, so
    // focus fell back to <body> and a keyboard user had to tab through the
    // whole regenerated menu to reach "Take this action" — on every
    // keystroke that changed anything. The restart pair in the same file
    // restores focus deliberately; this was the omission beside it.
    function mountAwaitingPage(pending, extraAttributes = '') {
        document.body.innerHTML = `
            <strong id="battle-status">Awaiting players</strong>
            <span id="battle-turn">0</span>
            <div id="battle-roster"></div>
            <svg id="battle-arena"></svg>
            <div id="battle-log"></div>
            <div id="turn-panel" hidden ${extraAttributes}
                 data-act-url="/tarmar/battles/1/act/"
                 data-state-url="/tarmar/battles/1/state/"
                ></div>
            <script id="battle-view" type="application/json">${JSON.stringify(sampleState([sampleCombatant()]))}</script>
            <script id="battle-seats" type="application/json">[]</script>
            <script id="battle-pending" type="application/json">${JSON.stringify(pending)}</script>
        `;
        battle.initBattlePage();
        return document.getElementById('turn-panel');
    }

    function twoOptionPending() {
        return {
            combatant_id: 1, turn: 1, display_name: 'Kestra Duskmere',
            player: 'playeruser', is_you: true,
            candidates: [
                { letter: 'a', name: 'MOVE', score: 0.5,
                  rationale: 'Close the gap', target_id: 1, spell_key: '' },
                { letter: 'c', name: 'DODGE', score: 0.9,
                  rationale: 'Nothing in reach', target_id: null, spell_key: '' },
            ],
        };
    }

    test('selecting an option leaves focus on that option', () => {
        const panel = mountAwaitingPage(twoOptionPending());
        const second = panel.querySelectorAll('.turn-option')[1];
        second.focus();
        second.click();
        // The button that was clicked is gone; its replacement holds focus.
        expect(document.activeElement).not.toBe(document.body);
        expect(document.activeElement.dataset.focusKey).toBe('option-1');
        expect(document.activeElement.getAttribute('aria-pressed')).toBe('true');
    });

    test('deselecting the same option keeps focus on it too', () => {
        const panel = mountAwaitingPage(twoOptionPending());
        const first = panel.querySelectorAll('.turn-option')[0];
        first.focus();
        first.click();
        document.activeElement.click();
        expect(document.activeElement.dataset.focusKey).toBe('option-0');
        expect(document.activeElement.getAttribute('aria-pressed')).toBe('false');
    });

    test('focus outside the panel is never stolen by a repaint', () => {
        const panel = mountAwaitingPage(twoOptionPending());
        const first = panel.querySelectorAll('.turn-option')[0];
        first.focus();
        first.click();
        const outside = document.createElement('button');
        document.body.appendChild(outside);
        outside.focus();
        battle.renderTurnPanel(panel, twoOptionPending(), { selectedIndex: 0 });
        expect(document.activeElement).toBe(outside);
    });

    test('a panel with nothing pending forgets where focus was', () => {
        const panel = mountAwaitingPage(twoOptionPending());
        panel.querySelectorAll('.turn-option')[0].focus();
        battle.renderTurnPanel(panel, null, {});
        expect(panel.dataset.lastFocusKey).toBeUndefined();
    });

    // The cast stage doubles the problem: opening it, picking a spell and
    // standing down each rebuild the whole panel, and two of those three
    // remove the very control that was focused.
    function castPending() {
        return {
            combatant_id: 1, turn: 1, display_name: 'Ilmar the Grey',
            player: 'playeruser', is_you: true,
            candidates: [
                { letter: 'a', name: 'MOVE', score: 0.4,
                  rationale: 'Close the gap', target_id: null, spell_key: '' },
                { letter: 'h', name: 'CAST SPELL Fire Missile', score: 2.0,
                  rationale: 'chosen', target_id: 9, spell_key: 'fire_missile' },
            ],
            magic: {
                pool_label: 'Mana', pool: 8, max_pool: 10,
                spells: [
                    { key: 'fire_missile', target_id: 9, name: 'Fire Missile',
                      cost: 'Missile — 2 mana', note: '50% on 3d6',
                      castable: true, reason: '' },
                ],
            },
        };
    }

    test('opening the cast stage leaves focus on the CAST SPELL option', () => {
        const panel = mountAwaitingPage(castPending());
        const castOption = panel.querySelector('.turn-option-cast');
        castOption.focus();
        castOption.click();
        expect(panel.querySelector('.turn-cast-stage')).not.toBeNull();
        expect(document.activeElement.dataset.focusKey).toBe('cast');
    });

    test('picking a spell leaves focus on that spell row', () => {
        const panel = mountAwaitingPage(castPending());
        panel.querySelector('.turn-option-cast').click();
        const spell = panel.querySelector('.turn-spell');
        spell.focus();
        spell.click();
        expect(document.activeElement.dataset.focusKey)
            .toBe(spell.dataset.focusKey);
        expect(document.activeElement.getAttribute('aria-pressed')).toBe('true');
    });

    test('standing down lands on the option that opened the stage', () => {
        const panel = mountAwaitingPage(castPending());
        panel.querySelector('.turn-option-cast').click();
        const standDown = panel.querySelector('.turn-cast-standdown');
        standDown.focus();
        standDown.click();
        // The stand-down button and the whole stage are gone; the reader
        // belongs on the option that opened it, not back at the top.
        expect(panel.querySelector('.turn-cast-stage')).toBeNull();
        expect(document.activeElement.dataset.focusKey).toBe('cast');
    });
});

describe('a submitted choice names its pause (#644)', () => {
    function mountAwaitingPage(pending, extraAttributes = '') {
        document.body.innerHTML = `
            <strong id="battle-status">Awaiting players</strong>
            <span id="battle-turn">0</span>
            <div id="battle-roster"></div>
            <svg id="battle-arena"></svg>
            <div id="battle-log"></div>
            <div id="turn-panel" hidden ${extraAttributes}
                 data-act-url="/tarmar/battles/1/act/"
                 data-state-url="/tarmar/battles/1/state/"
                ></div>
            <script id="battle-view" type="application/json">${JSON.stringify(sampleState([sampleCombatant()]))}</script>
            <script id="battle-seats" type="application/json">[]</script>
            <script id="battle-pending" type="application/json">${JSON.stringify(pending)}</script>
        `;
    }

    test("a poll that brings the stale-pools notice repaints the Game Master's page (#892)", async () => {
        jest.useFakeTimers();
        try {
            mountAwaitingPage({
                combatant_id: 4, turn: 3, display_name: 'Duel 2 Archer',
                player: 'playeruser', is_you: false, candidates: [],
            }, 'data-is-dm="true"');
            document.body.insertAdjacentHTML(
                'beforeend',
                '<div id="battle-stale-slot"></div>'
                + '<script id="battle-status-labels" type="application/json">'
                + `${JSON.stringify(STATUS_CHOICES)}</script>`
            );
            battle.initBattlePage();
            const poll = (staleHtml) => ({
                ok: true,
                json: async () => ({
                    status: 'awaiting', turn: 0,
                    view: sampleState([sampleCombatant()]),
                    events: [], turns_html: [], seats: [],
                    pending: {
                        combatant_id: 4, turn: 3, display_name: 'Duel 2 Archer',
                        player: 'playeruser', is_you: false, candidates: [],
                    },
                    advance: { label: 'Pools changed on the records', enabled: false },
                    stale_html: staleHtml,
                }),
            });
            // Nothing else about the battle changed: status, turn and events
            // are as the page has them.
            global.fetch = jest.fn().mockResolvedValue(
                poll('<p id="battle-stale-pools">Aldwin moved.</p>')
            );

            await jest.advanceTimersByTimeAsync(2100);

            expect(global.fetch).toHaveBeenCalled();
            expect(document.getElementById('battle-stale-pools')).not.toBeNull();

            global.fetch = jest.fn().mockResolvedValue(poll(''));
            await jest.advanceTimersByTimeAsync(2100);
            expect(document.getElementById('battle-stale-pools')).toBeNull();
        } finally {
            jest.useRealTimers();
        }
    });

    test('handing the choice to the AI names the figure it is for', async () => {
        mountAwaitingPage({
            combatant_id: 4, turn: 3, display_name: 'Duel 2 Archer',
            player: 'playeruser', is_you: false, candidates: [],
        }, 'data-is-dm="true"');
        battle.initBattlePage();
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            json: async () => ({
                status: 'paused', turn: 3,
                view: sampleState([sampleCombatant()]),
                events: [], turns_html: [], seats: [], pending: null,
            }),
        });
        require('../api.js');

        document.querySelector('.turn-ai-choose').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });

        const sent = JSON.parse(global.fetch.mock.calls[0][1].body);
        expect(sent).toEqual({ combatant_id: 4, turn: 3, ai: true });
    });

    test('a refused submission leaves the arena holding the reason', async () => {
        mountAwaitingPage({
            combatant_id: 1, turn: 1, display_name: 'Duel 1 Armoured Fighter',
            player: 'playeruser', is_you: true,
            candidates: [{ letter: 'c', name: 'DODGE', score: 0.9,
                           rationale: 'advice', target_id: null, spell_key: '' }],
        });
        battle.initBattlePage();
        require('../api.js');
        require('../notify.js');
        global.fetch = jest.fn().mockResolvedValue({
            ok: false,
            status: 400,
            json: async () => ({
                error: 'That choice was made for Duel 2 Archer on turn 1, but '
                    + "the battle is waiting on Duel 1 Armoured Fighter's turn 2.",
            }),
        });

        const panel = document.getElementById('turn-panel');
        panel.querySelector('.turn-option').click();
        panel.querySelector('.turn-confirm').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });

        // Persistent, dismissible, from state — not a toast (house rule).
        const notice = document.querySelector('#notice-region .notice-error');
        expect(notice).not.toBeNull();
        expect(notice.textContent).toContain('the battle is waiting on');
        expect(notice.querySelector('.notice-dismiss')).not.toBeNull();
        // The menu is still there to choose from again.
        expect(panel.querySelector('.turn-option')).not.toBeNull();
    });
});

describe('the wizard cast flow (#250)', () => {
    // A caster's pending menu: two plain options and two cast candidates,
    // plus the `magic` block the server publishes for the cast stage — the
    // known spells including the ones that cannot be cast right now.
    function castingPending(overrides = {}) {
        return Object.assign({
            combatant_id: 4,
            turn: 2,
            display_name: 'Ysolde Fenner',
            player: 'playeruser',
            is_you: true,
            candidates: [
                { letter: 'a', name: 'MOVE', score: 0.4,
                  rationale: 'Close the gap', target_id: null, spell_key: '' },
                { letter: 'h', name: 'CAST SPELL Fire Missile', score: 2.4,
                  rationale: 'P(land) 61% x 3.5 damage', target_id: 9,
                  spell_key: 'fire_missile' },
                { letter: 'h', name: 'CAST SPELL Shield', score: 0.9,
                  rationale: 'P(cast) 84%, defensive buff', target_id: null,
                  spell_key: 'shield' },
                { letter: 'c', name: 'DODGE', score: 0.2,
                  rationale: '1 missile threat(s)', target_id: null, spell_key: '' },
            ],
            magic: {
                pool_label: 'Mana',
                pool: 4,
                max_pool: 10,
                spells: [
                    { key: 'fire_missile', target_id: 9, name: 'Fire Missile',
                      cost: 'Elemental — 1 mana',
                      note: '61% on 3d6 \u2264 INT, then a DEX roll to land it',
                      castable: true, reason: '' },
                    { key: 'shield', target_id: null, name: 'Shield',
                      cost: 'Protection — 1 mana',
                      note: '84% on 3d6 \u2264 INT',
                      castable: true, reason: '' },
                    { key: 'lightning_bolt', target_id: 9,
                      name: 'Lightning Bolt', cost: 'Elemental — 5 mana',
                      note: '61% on 3d6 \u2264 INT, then a DEX roll to land it',
                      castable: false, reason: 'needs 5 mana, 4 left' },
                ],
            },
        }, overrides);
    }

    function panelNode() {
        document.body.innerHTML = '<div id="turn-panel" hidden></div>';
        return document.getElementById('turn-panel');
    }

    test('every cast candidate collapses into one CAST SPELL option', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, castingPending(), { selectedIndex: null });
        const options = panel.querySelectorAll('.turn-option');
        // MOVE, DODGE and one cast group — not one row per spell.
        expect(options).toHaveLength(3);
        const cast = panel.querySelector('.turn-option-cast');
        expect(cast).not.toBeNull();
        expect(cast.textContent).toContain('CAST SPELL');
        expect(cast.textContent).toContain('Mana 4 of 10');
        // The AI's favourite is a cast, so the group wears the tag.
        expect(cast.querySelector('.turn-option-ai-pick')).not.toBeNull();
        // Closed by default: no spell list until the player declares a cast.
        expect(panel.querySelector('.turn-cast-stage')).toBeNull();
    });

    test('declaring a cast opens the stage without choosing a spell', () => {
        const panel = panelNode();
        const onCastOpen = jest.fn();
        battle.renderTurnPanel(panel, castingPending(), {
            selectedIndex: null, onCastOpen,
        });
        panel.querySelector('.turn-option-cast').click();
        expect(onCastOpen).toHaveBeenCalled();

        battle.renderTurnPanel(panel, castingPending(), {
            selectedIndex: null, castOpen: true,
        });
        const stage = panel.querySelector('.turn-cast-stage');
        expect(stage).not.toBeNull();
        expect(panel.querySelector('.turn-option-cast')
            .getAttribute('aria-pressed')).toBe('true');
        // The gate: a declared cast with no spell picked cannot be taken.
        expect(panel.querySelector('.turn-confirm').disabled).toBe(true);
        // Nothing is pre-selected — the pick is the player's, always.
        stage.querySelectorAll('.turn-spell').forEach((spell) => {
            expect(spell.getAttribute('aria-pressed')).toBe('false');
        });
    });

    test('the stage lists known spells, the unavailable ones with the reason', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, castingPending(), {
            selectedIndex: null, castOpen: true,
        });
        const spells = panel.querySelectorAll('.turn-spell');
        expect(spells).toHaveLength(3);
        expect(spells[0].textContent).toContain('Fire Missile');
        expect(spells[0].textContent).toContain('1 mana');
        expect(spells[0].textContent).toContain('61%');
        expect(spells[0].disabled).toBe(false);
        const bolt = spells[2];
        expect(bolt.textContent).toContain('Lightning Bolt');
        expect(bolt.textContent).toContain('needs 5 mana, 4 left');
        expect(bolt.disabled).toBe(true);
        // The AI's advice reaches the spell it favours, as advice only.
        expect(spells[0].querySelector('.turn-option-ai-pick')).not.toBeNull();
    });

    test('picking a spell selects its candidate and ungates the confirm', () => {
        const panel = panelNode();
        const onSelect = jest.fn();
        battle.renderTurnPanel(panel, castingPending(), {
            selectedIndex: null, castOpen: true, onSelect,
        });
        panel.querySelectorAll('.turn-spell')[1].click();
        // Shield is candidate index 2 in the pending menu.
        expect(onSelect).toHaveBeenCalledWith(2);

        const onConfirm = jest.fn();
        battle.renderTurnPanel(panel, castingPending(), {
            selectedIndex: 2, castOpen: true, onConfirm,
        });
        expect(panel.querySelectorAll('.turn-spell')[1]
            .getAttribute('aria-pressed')).toBe('true');
        const confirm = panel.querySelector('.turn-confirm');
        expect(confirm.disabled).toBe(false);
        confirm.click();
        expect(onConfirm).toHaveBeenCalledWith(
            expect.objectContaining({ letter: 'h', spell_key: 'shield' })
        );
    });

    test('standing down closes the stage and clears the cast', () => {
        const panel = panelNode();
        const onCastClose = jest.fn();
        battle.renderTurnPanel(panel, castingPending(), {
            selectedIndex: 2, castOpen: true, onCastClose,
        });
        const standDown = panel.querySelector('.turn-cast-standdown');
        expect(standDown).not.toBeNull();
        standDown.click();
        expect(onCastClose).toHaveBeenCalled();
    });

    test('the page drives the whole cast flow through to the post', async () => {
        // End to end through initBattlePage: declare, pick, confirm — and
        // the posted body carries the spell the player chose.
        document.body.innerHTML = `
            <strong id="battle-status">Awaiting players</strong>
            <span id="battle-turn">1</span>
            <div id="battle-roster"></div>
            <svg id="battle-arena"></svg>
            <div id="battle-log"></div>
            <div id="turn-panel" hidden
                 data-act-url="/tarmar/battles/1/act/"
                 data-state-url="/tarmar/battles/1/state/"
                ></div>
            <script id="battle-view" type="application/json">${JSON.stringify(sampleState([sampleCombatant()]))}</script>
            <script id="battle-seats" type="application/json">[]</script>
            <script id="battle-pending" type="application/json">${JSON.stringify(castingPending())}</script>
        `;
        battle.initBattlePage();
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            json: async () => ({
                status: 'paused', turn: 2,
                view: sampleState([sampleCombatant()]),
                events: [], seats: [], pending: null,
            }),
        });
        require('../api.js');

        const panel = document.getElementById('turn-panel');
        panel.querySelector('.turn-option-cast').click();
        expect(panel.querySelector('.turn-confirm').disabled).toBe(true);
        panel.querySelectorAll('.turn-spell')[0].click();
        expect(panel.querySelector('.turn-confirm').disabled).toBe(false);
        panel.querySelector('.turn-confirm').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });

        expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({
            combatant_id: 4, turn: 2,
            letter: 'h', target_id: 9, spell_key: 'fire_missile',
        });
    });

    test('closing the stage drops the spell the player had picked', () => {
        document.body.innerHTML = `
            <strong id="battle-status">Awaiting players</strong>
            <span id="battle-turn">1</span>
            <div id="battle-roster"></div>
            <svg id="battle-arena"></svg>
            <div id="battle-log"></div>
            <div id="turn-panel" hidden
                 data-act-url="/tarmar/battles/1/act/"
                 data-state-url="/tarmar/battles/1/state/"
                ></div>
            <script id="battle-view" type="application/json">${JSON.stringify(sampleState([sampleCombatant()]))}</script>
            <script id="battle-seats" type="application/json">[]</script>
            <script id="battle-pending" type="application/json">${JSON.stringify(castingPending())}</script>
        `;
        battle.initBattlePage();
        const panel = document.getElementById('turn-panel');
        panel.querySelector('.turn-option-cast').click();
        panel.querySelectorAll('.turn-spell')[0].click();
        panel.querySelector('.turn-cast-standdown').click();
        expect(panel.querySelector('.turn-cast-stage')).toBeNull();
        // No hidden selection survives the stand-down.
        expect(panel.querySelector('.turn-confirm').disabled).toBe(true);
    });

    // A classic wizard's pending menu (#263): one pool named ST, and the
    // SAME spell offered at each figure it can be aimed at — the shape that
    // used to collapse to one button and cast at the wrong foe.
    function classicCastingPending(overrides = {}) {
        return Object.assign(castingPending(), {
            candidates: [
                { letter: 'a', name: 'MOVE', score: 0.4,
                  rationale: 'Close the gap', target_id: null, spell_key: '' },
                { letter: 'h', name: 'CAST SPELL Magic Fist', score: 2.0,
                  rationale: 'chosen', target_id: '2', spell_key: 'magic_fist' },
                { letter: 'h', name: 'CAST SPELL Magic Fist', score: 0,
                  rationale: 'not chosen', target_id: '3',
                  spell_key: 'magic_fist' },
            ],
            magic: {
                pool_label: 'ST',
                pool: 12,
                max_pool: 12,
                spells: [
                    { key: 'magic_fist', target_id: '2',
                      name: 'Magic Fist \u2192 Bram', cost: 'Missile — 3 ST',
                      note: '50% on 3d6 \u2264 adjDX 10, before the range to aim',
                      castable: true, reason: '' },
                    { key: 'magic_fist', target_id: '3',
                      name: 'Magic Fist \u2192 Cyn', cost: 'Missile — 3 ST',
                      note: '50% on 3d6 \u2264 adjDX 10, before the range to aim',
                      castable: true, reason: '' },
                    { key: 'staff', target_id: null, name: 'Staff',
                      cost: 'Special — 5 ST', note: '', castable: false,
                      reason: 'nothing this spell can be cast at' },
                ],
            },
        }, overrides);
    }

    test('the pool is printed under the name the server gave it', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, classicCastingPending(), {
            selectedIndex: null, castOpen: true,
        });
        const cast = panel.querySelector('.turn-option-cast');
        expect(cast.textContent).toContain('ST 12 of 12');
        expect(cast.textContent).not.toContain('Mana');
        expect(panel.querySelector('.turn-cast-pool').textContent)
            .toContain('ST 12 of 12');
        expect(panel.querySelector('.turn-cast-pool').textContent)
            .not.toContain('Mana');
    });

    test('one spell aimed at two foes gets a button each (#263)', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, classicCastingPending(), {
            selectedIndex: null, castOpen: true,
        });
        const spells = panel.querySelectorAll('.turn-spell');
        expect(spells).toHaveLength(3);
        // Both aimings are live and distinct — the collapse this fixes made
        // one of them dead and the other cast at whichever came last.
        expect(spells[0].textContent).toContain('Magic Fist \u2192 Bram');
        expect(spells[1].textContent).toContain('Magic Fist \u2192 Cyn');
        expect(spells[0].disabled).toBe(false);
        expect(spells[1].disabled).toBe(false);
        expect(spells[2].disabled).toBe(true);
        expect(spells[2].textContent)
            .toContain('nothing this spell can be cast at');
    });

    test('each aiming selects its own candidate, not the last one', () => {
        const panel = panelNode();
        const onSelect = jest.fn();
        battle.renderTurnPanel(panel, classicCastingPending(), {
            selectedIndex: null, castOpen: true, onSelect,
        });
        const spells = panel.querySelectorAll('.turn-spell');
        spells[0].click();
        spells[1].click();
        // Candidate 1 is Magic Fist at Bram, candidate 2 at Cyn.
        expect(onSelect.mock.calls.map((call) => call[0])).toEqual([1, 2]);
    });

    test('the cast option counts casts, not spells (#263)', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, classicCastingPending(), {
            selectedIndex: null,
        });
        expect(panel.querySelector('.turn-option-cast').textContent)
            .toContain('2 casts ready');
    });

    test('a non-caster keeps the plain menu, no cast group', () => {
        const panel = panelNode();
        const pending = castingPending({
            candidates: [
                { letter: 'a', name: 'MOVE', score: 0.4, rationale: 'advice',
                  target_id: null, spell_key: '' },
            ],
            magic: null,
        });
        battle.renderTurnPanel(panel, pending, { selectedIndex: null });
        expect(panel.querySelector('.turn-option-cast')).toBeNull();
        expect(panel.querySelectorAll('.turn-option')).toHaveLength(1);
    });

    test('a caster with no castable spell this turn gets no cast group', () => {
        // Engaged in hand-to-hand, say: the letter is not on the menu even
        // though the wizard still knows spells.
        const panel = panelNode();
        const pending = castingPending({
            candidates: [
                { letter: 't', name: 'HTH ATTACK', score: 1.1, rationale: 'advice',
                  target_id: 9, spell_key: '' },
            ],
        });
        battle.renderTurnPanel(panel, pending, { selectedIndex: null });
        expect(panel.querySelector('.turn-option-cast')).toBeNull();
    });
});

/** The battle page around a pending menu, its act endpoint answering. */
function mountPage(pending) {
    document.body.innerHTML = `
        <strong id="battle-status">Awaiting players</strong>
        <span id="battle-turn">1</span>
        <div id="battle-roster"></div>
        <svg id="battle-arena"></svg>
        <div id="battle-log"></div>
        <div id="turn-panel" hidden
             data-act-url="/tarmar/battles/1/act/"
             data-state-url="/tarmar/battles/1/state/"
            ></div>
        <script id="battle-view" type="application/json">${JSON.stringify(sampleState([sampleCombatant()]))}</script>
        <script id="battle-seats" type="application/json">[]</script>
        <script id="battle-pending" type="application/json">${JSON.stringify(pending)}</script>
    `;
    battle.initBattlePage();
    global.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
            status: 'paused', turn: 2,
            view: sampleState([sampleCombatant()]),
            events: [], seats: [], pending: null,
        }),
    });
    require('../api.js');
    return document.getElementById('turn-panel');
}

describe('pushing mana into a cast (#960)', () => {
    // A caster's menu as the engine builds it with Push rules: the plain
    // Fire Missile (the AI's pick), then one pushed copy per amount the
    // rules allow, last on the menu and never the AI's.
    function pushPending() {
        return {
            combatant_id: 4,
            turn: 2,
            display_name: 'Ysolde Fenner',
            player: 'playeruser',
            is_you: true,
            candidates: [
                { letter: 'a', name: 'MOVE', score: 0.4,
                  rationale: 'Close the gap', target_id: null, spell_key: '' },
                { letter: 'h', name: 'CAST SPELL Fire Missile', score: 2.4,
                  rationale: 'P(land) 61%', target_id: 9,
                  spell_key: 'fire_missile', ai_choice: true },
                { letter: 'h', name: 'CAST SPELL Shield', score: 0.9,
                  rationale: 'buff', target_id: null, spell_key: 'shield' },
                { letter: 'h', name: 'CAST SPELL Fire Missile (PUSH 1 MANA)',
                  score: 0, rationale: 'Control Roll', target_id: 9,
                  spell_key: 'fire_missile', push_mana: 1,
                  cost: '2 mana (1 and 1 pushed)' },
                { letter: 'h', name: 'CAST SPELL Fire Missile (PUSH 2 MANA)',
                  score: 0, rationale: 'Control Roll', target_id: 9,
                  spell_key: 'fire_missile', push_mana: 2,
                  cost: '3 mana (1 and 2 pushed)' },
            ],
            magic: {
                pool_label: 'Mana', pool: 4, max_pool: 10,
                spells: [
                    { key: 'fire_missile', target_id: 9, name: 'Fire Missile',
                      cost: 'Elemental — 1 mana', note: '61% on 3d6',
                      castable: true, reason: '', channel: 3 },
                    { key: 'shield', target_id: null, name: 'Shield',
                      cost: 'Protection — 1 mana', note: '84% on 3d6',
                      castable: true, reason: '', channel: 2 },
                ],
            },
        };
    }

    function panelNode() {
        document.body.innerHTML = '<div id="turn-panel" hidden></div>';
        return document.getElementById('turn-panel');
    }

    function amounts(panel) {
        return Array.from(panel.querySelectorAll('.turn-push-amount'))
            .map((button) => button.textContent.replace('AI pick', ''));
    }

    test('a pushed copy is no spell of its own: one row, one count', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, pushPending(), {
            selectedIndex: null, castOpen: true,
        });
        expect(panel.querySelectorAll('.turn-spell')).toHaveLength(2);
        expect(panel.querySelector('.turn-option-cast').textContent)
            .toContain('2 casts ready');
    });

    test('the push control offers exactly the amounts on the menu', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, pushPending(), {
            selectedIndex: null, castOpen: true,
        });
        // One control, beside the cast that has pushed copies; Shield has none.
        expect(panel.querySelectorAll('.turn-push')).toHaveLength(1);
        expect(amounts(panel)).toEqual(['None', '+1 mana', '+2 mana']);
        expect(panel.querySelector('.turn-push-label').textContent)
            .toBe('Push more mana (Channel 3):');
    });

    test('the AI pick stays on the plain cast the AI chose', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, pushPending(), {
            selectedIndex: null, castOpen: true,
        });
        const missile = panel.querySelectorAll('.turn-spell')[0];
        expect(missile.querySelector('.turn-option-ai-pick')).not.toBeNull();
        const tagged = Array.from(panel.querySelectorAll('.turn-push-amount'))
            .filter((button) => button.querySelector('.turn-option-ai-pick'))
            .map((button) => button.dataset.pushMana);
        expect(tagged).toEqual(['0']);
    });

    test('an AI that picked a pushed copy is tagged on that amount', () => {
        const pending = pushPending();
        pending.candidates[1].ai_choice = false;
        pending.candidates[4].ai_choice = true;
        const panel = panelNode();
        battle.renderTurnPanel(panel, pending, {
            selectedIndex: null, castOpen: true,
        });
        const missile = panel.querySelectorAll('.turn-spell')[0];
        expect(missile.querySelector('.turn-option-ai-pick')).toBeNull();
        const tagged = Array.from(panel.querySelectorAll('.turn-push-amount'))
            .filter((button) => button.querySelector('.turn-option-ai-pick'))
            .map((button) => button.dataset.pushMana);
        expect(tagged).toEqual(['2']);
        // The CAST SPELL option still says the AI would cast.
        expect(panel.querySelector('.turn-option-cast .turn-option-ai-pick'))
            .not.toBeNull();
    });

    test('picking the spell selects the plain cast: no push by default', () => {
        const panel = panelNode();
        const onSelect = jest.fn();
        battle.renderTurnPanel(panel, pushPending(), {
            selectedIndex: null, castOpen: true, onSelect,
        });
        panel.querySelectorAll('.turn-spell')[0].click();
        expect(onSelect).toHaveBeenCalledWith(1);
        battle.renderTurnPanel(panel, pushPending(), {
            selectedIndex: 1, castOpen: true,
        });
        const pressed = Array.from(panel.querySelectorAll('.turn-push-amount'))
            .filter((button) => button.getAttribute('aria-pressed') === 'true')
            .map((button) => button.dataset.pushMana);
        expect(pressed).toEqual(['0']);
        expect(panel.querySelector('.turn-push-cost')).toBeNull();
    });

    test('an amount selects its copy and shows what it costs', () => {
        const panel = panelNode();
        const onSelect = jest.fn();
        battle.renderTurnPanel(panel, pushPending(), {
            selectedIndex: 1, castOpen: true, onSelect,
        });
        panel.querySelectorAll('.turn-push-amount')[2].click();
        expect(onSelect).toHaveBeenCalledWith(4);

        battle.renderTurnPanel(panel, pushPending(), {
            selectedIndex: 4, castOpen: true,
        });
        expect(panel.querySelectorAll('.turn-spell')[0]
            .getAttribute('aria-pressed')).toBe('true');
        expect(panel.querySelectorAll('.turn-push-amount')[2]
            .getAttribute('aria-pressed')).toBe('true');
        expect(panel.querySelector('.turn-push-cost').textContent)
            .toBe('Costs 3 mana (1 and 2 pushed).');
        expect(panel.querySelector('.turn-confirm').disabled).toBe(false);
    });

    test('confirming a pushed cast sends the amount picked', async () => {
        const panel = mountPage(pushPending());
        panel.querySelector('.turn-option-cast').click();
        panel.querySelectorAll('.turn-spell')[0].click();
        panel.querySelectorAll('.turn-push-amount')[1].click();
        panel.querySelector('.turn-confirm').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });
        expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({
            combatant_id: 4, turn: 2, letter: 'h', target_id: 9,
            spell_key: 'fire_missile', push_mana: 1,
        });
    });

    test('a cast with no push picked sends no push_mana', async () => {
        const panel = mountPage(pushPending());
        panel.querySelector('.turn-option-cast').click();
        panel.querySelectorAll('.turn-spell')[0].click();
        panel.querySelectorAll('.turn-push-amount')[2].click();
        panel.querySelectorAll('.turn-push-amount')[0].click();
        panel.querySelector('.turn-confirm').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });
        expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({
            combatant_id: 4, turn: 2, letter: 'h', target_id: 9,
            spell_key: 'fire_missile',
        });
    });
});

describe('borrowing mana for a cast (tarmar-engine #28)', () => {
    // A caster with 1 mana, as the engine builds the menu with Borrowing:
    // Shield (1 mana) is castable and pushable into borrowing; Lightning
    // Bolt (3 mana) has no plain cast, only borrowing copies. Every number
    // the control prints is the server's: ``borrowed`` and ``cost``.
    function borrowPending() {
        return {
            combatant_id: 4,
            turn: 2,
            display_name: 'Ysolde Fenner',
            player: 'playeruser',
            is_you: true,
            candidates: [
                { letter: 'a', name: 'MOVE', score: 0.4, ai_choice: true,
                  rationale: 'Close the gap', target_id: null, spell_key: '' },
                { letter: 'h', name: 'CAST SPELL Shield', score: 0.9,
                  rationale: 'buff', target_id: null, spell_key: 'shield' },
                { letter: 'h', name: 'CAST SPELL Shield (PUSH 1 MANA, BORROW 1 MANA)',
                  score: 0, rationale: 'debt roll', target_id: null,
                  spell_key: 'shield', push_mana: 1, borrow: true, borrowed: 1,
                  cost: '2 mana (1 and 1 pushed); borrows the 1 you lack' },
                { letter: 'h', name: 'CAST SPELL Lightning Bolt (PUSH 1 MANA, BORROW 3 MANA)',
                  score: 0, rationale: 'debt roll', target_id: 9,
                  spell_key: 'lightning_bolt', push_mana: 1, borrow: true, borrowed: 3,
                  cost: '4 mana (3 and 1 pushed); borrows the 3 you lack' },
                { letter: 'h', name: 'CAST SPELL Lightning Bolt (BORROW 2 MANA)',
                  score: 0, rationale: 'debt roll', target_id: 9,
                  spell_key: 'lightning_bolt', borrow: true, borrowed: 2,
                  cost: '3 mana; borrows the 2 you lack' },
            ],
            magic: {
                pool_label: 'Mana', pool: 1, max_pool: 10, owed: 2,
                spells: [
                    { key: 'shield', target_id: null, name: 'Shield',
                      cost: 'Protection — 1 mana', note: '84% on 3d6',
                      castable: true, reason: '', channel: null,
                      borrowable: false, shortfall: 0 },
                    { key: 'lightning_bolt', target_id: 9, name: 'Lightning Bolt',
                      cost: 'Elemental — 3 mana', note: '50% on 3d6',
                      castable: false, reason: 'needs 3 mana, 1 left: borrow the 2 it lacks',
                      channel: null, borrowable: true, shortfall: 2 },
                ],
            },
        };
    }

    function panelNode() {
        document.body.innerHTML = '<div id="turn-panel" hidden></div>';
        return document.getElementById('turn-panel');
    }

    function boltGroup(panel) {
        return Array.from(panel.querySelectorAll('.turn-spell-group')).find(
            (group) => group.textContent.includes('Lightning Bolt')
        );
    }

    test('a cast only borrowing pays for still opens the stage', () => {
        const pending = borrowPending();
        // No affordable cast at all: Shield gone, only borrowing copies left.
        pending.candidates = pending.candidates.filter(
            (candidate) => candidate.spell_key !== 'shield'
        );
        const panel = panelNode();
        battle.renderTurnPanel(panel, pending, { selectedIndex: null, castOpen: true });
        expect(panel.querySelector('.turn-option-cast').textContent)
            .toContain('0 casts ready, 1 by borrowing');
        expect(panel.querySelectorAll('.turn-borrow')).toHaveLength(1);
    });

    test('the borrowing control lists the menu\'s copies, in the server\'s numbers', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, borrowPending(), {
            selectedIndex: null, castOpen: true,
        });
        // Shield is affordable (its pushed copy borrows); only Lightning
        // Bolt is paid for by borrowing alone.
        expect(panel.querySelector('.turn-option-cast').textContent)
            .toContain('1 cast ready, 1 by borrowing');
        expect(panel.querySelector('.turn-cast-pool').textContent)
            .toBe('Mana 1 of 10, owing 2. Pick a spell to cast.');
        const group = boltGroup(panel);
        const bolt = group.querySelector('.turn-spell');
        // Not castable on its own: the row stays disabled with the reason.
        expect(bolt.disabled).toBe(true);
        expect(bolt.querySelector('.turn-spell-note').textContent)
            .toBe('needs 3 mana, 1 left: borrow the 2 it lacks');
        expect(group.querySelector('.turn-push')).toBeNull();
        const choices = Array.from(group.querySelectorAll('.turn-borrow-amount'));
        expect(choices.map((button) => button.textContent))
            .toEqual(['Borrow 2 mana', '+1 mana, borrow 3']);
        expect(choices.map((button) => button.dataset.borrowed)).toEqual(['2', '3']);
        // Nothing is borrowed until the player picks.
        expect(choices.every((button) => button.getAttribute('aria-pressed') === 'false'))
            .toBe(true);
        // Shield sits beside a borrowing copy of its push, with no plain push.
        const shieldGroup = Array.from(panel.querySelectorAll('.turn-spell-group'))
            .find((node) => node.textContent.includes('Shield'));
        expect(shieldGroup.querySelector('.turn-push')).toBeNull();
        expect(Array.from(shieldGroup.querySelectorAll('.turn-borrow-amount'))
            .map((button) => button.textContent)).toEqual(['+1 mana, borrow 1']);
    });

    test('a borrowing copy selects its cast and shows what it costs', () => {
        const panel = panelNode();
        const onSelect = jest.fn();
        battle.renderTurnPanel(panel, borrowPending(), {
            selectedIndex: null, castOpen: true, onSelect,
        });
        boltGroup(panel).querySelector('.turn-borrow-amount').click();
        expect(onSelect).toHaveBeenCalledWith(4);
        battle.renderTurnPanel(panel, borrowPending(), {
            selectedIndex: 4, castOpen: true,
        });
        const group = boltGroup(panel);
        expect(group.querySelector('.turn-spell').getAttribute('aria-pressed')).toBe('true');
        expect(group.querySelector('.turn-borrow-amount').getAttribute('aria-pressed'))
            .toBe('true');
        expect(group.querySelector('.turn-borrow-cost').textContent)
            .toBe('Costs 3 mana; borrows the 2 you lack.');
        expect(panel.querySelector('.turn-confirm').disabled).toBe(false);
    });

    test('confirming a borrowing copy sends borrow and its push', async () => {
        const panel = mountPage(borrowPending());
        panel.querySelector('.turn-option-cast').click();
        boltGroup(panel).querySelectorAll('.turn-borrow-amount')[1].click();
        panel.querySelector('.turn-confirm').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });
        expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({
            combatant_id: 4, turn: 2, letter: 'h', target_id: 9,
            spell_key: 'lightning_bolt', push_mana: 1, borrow: true,
        });
    });

    test('confirming a plain cast sends no borrow', async () => {
        const panel = mountPage(borrowPending());
        panel.querySelector('.turn-option-cast').click();
        Array.from(panel.querySelectorAll('.turn-spell'))
            .find((button) => button.textContent.includes('Shield')).click();
        panel.querySelector('.turn-confirm').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });
        expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({
            combatant_id: 4, turn: 2, letter: 'h', target_id: null, spell_key: 'shield',
        });
    });
});

describe('teams (#275)', () => {
    test('teamOrderOf lists teams by first appearance, skipping the free pool', () => {
        const view = sampleState([
            sampleCombatant({ combatant_id: 1, team: 'Red' }),
            sampleCombatant({ combatant_id: 2, team: '' }),
            sampleCombatant({ combatant_id: 3, team: 'Blue' }),
            sampleCombatant({ combatant_id: 4, team: 'Red' }),
        ]);
        expect(battle.teamOrderOf(view)).toEqual(['Red', 'Blue']);
    });

    test('teamTint is stable per team and null for the free pool', () => {
        const order = ['Red', 'Blue'];
        expect(battle.teamTint(order, 'Red')).toBe(battle.teamTint(order, 'Red'));
        expect(battle.teamTint(order, 'Red')).not.toBe(battle.teamTint(order, 'Blue'));
        expect(battle.teamTint(order, '')).toBeNull();
        expect(battle.teamTint(order, 'Nobody')).toBeNull();
    });

    test('arena markers wear the team tint and carry data-team', () => {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        document.body.appendChild(svg);
        const view = sampleState([
            sampleCombatant({ combatant_id: 1, team: 'Red' }),
            sampleCombatant({ combatant_id: 2, q: 1, r: 0, team: '' }),
        ]);
        battle.renderArena(svg, view);
        const tint = battle.teamTint(['Red'], 'Red');
        const teamed = svg.querySelector('[data-combatant="1"] circle');
        const free = svg.querySelector('[data-combatant="2"] circle');
        expect(teamed.getAttribute('fill')).toBe(tint);
        // The free pool keeps the historic character blue.
        expect(free.getAttribute('fill')).toBe('#4a6da7');
        expect(svg.querySelector('[data-combatant="1"]').dataset.team).toBe('Red');
    });

    test('a teamless roster renders flat, exactly as before teams', () => {
        const roster = document.createElement('div');
        document.body.appendChild(roster);
        battle.renderRoster(roster, sampleState([
            sampleCombatant({ combatant_id: 1 }),
            sampleCombatant({ combatant_id: 2 }),
        ]));
        expect(roster.querySelectorAll('.roster-team-head')).toHaveLength(0);
        expect(roster.querySelectorAll('.combatant-card')).toHaveLength(2);
    });

    test('a teamed roster groups under headers, free pool last', () => {
        const roster = document.createElement('div');
        document.body.appendChild(roster);
        battle.renderRoster(roster, sampleState([
            sampleCombatant({ combatant_id: 1, team: 'Red' }),
            sampleCombatant({ combatant_id: 2, team: '' }),
            sampleCombatant({ combatant_id: 3, team: 'Red' }),
        ]));
        const heads = [...roster.querySelectorAll('.roster-team-head')];
        expect(heads.map((head) => head.textContent)).toEqual(
            ['Red', 'Free-for-all']
        );
        // The Red header wears the team's swatch; the free pool none.
        expect(heads[0].querySelector('.roster-team-swatch')).not.toBeNull();
        expect(heads[1].querySelector('.roster-team-swatch')).toBeNull();
        // Cards follow their headers: Red's two, then the free one.
        const order = [...roster.children].map((node) =>
            node.classList.contains('roster-team-head')
                ? node.textContent
                : node.dataset.combatant
        );
        expect(order).toEqual(['Red', '1', '3', 'Free-for-all', '2']);
    });
});

/**
 * The attribute panel on a roster card (#302).
 *
 * Everything it shows was grouped and formatted server-side; these pin the
 * two things the browser is responsible for — drawing what it was handed,
 * and keeping an opened panel open across the poll's re-render, which is
 * what stops it being a transient popup.
 */
describe('the combatant attribute panel (#302)', () => {
    const SAMPLE_DETAIL = {
        groups: [
            {
                label: 'Attributes',
                rows: [
                    { label: 'STR', value: '13', note: '' },
                    {
                        label: 'DEX',
                        value: '11',
                        note: 'armour penalty included',
                    },
                ],
            },
            {
                label: 'Attack',
                rows: [{ label: 'Weapon', value: 'Broadsword', note: '' }],
            },
        ],
    };

    function rosterWith(combatants, openDetail) {
        const roster = document.createElement('div');
        document.body.appendChild(roster);
        battle.renderRoster(
            roster, sampleState(combatants), undefined, {}, openDetail
        );
        return roster;
    }

    test('a combatant with no panel gets no disclosure at all', () => {
        const roster = rosterWith([sampleCombatant()]);
        expect(roster.querySelector('.combatant-detail')).toBeNull();
    });

    test('the panel draws every group and row the server sent', () => {
        const roster = rosterWith([
            sampleCombatant({ detail: SAMPLE_DETAIL }),
        ]);
        const panel = roster.querySelector('.combatant-detail');
        expect(panel).not.toBeNull();
        expect(panel.querySelector('summary').textContent)
            .toBe('Full attributes');
        expect([...panel.querySelectorAll('.detail-group h4')]
            .map((head) => head.textContent)).toEqual(['Attributes', 'Attack']);
        const terms = [...panel.querySelectorAll('dt')].map((t) => t.textContent);
        expect(terms).toEqual(['STR', 'DEX', 'Weapon']);
        const values = [...panel.querySelectorAll('.detail-value')]
            .map((node) => node.textContent);
        expect(values).toEqual(['13', '11', 'Broadsword']);
    });

    test("a row's note rides under its value, not in place of it", () => {
        const roster = rosterWith([
            sampleCombatant({ detail: SAMPLE_DETAIL }),
        ]);
        const notes = [...roster.querySelectorAll('.detail-note')];
        expect(notes).toHaveLength(1);
        expect(notes[0].textContent)
            .toBe('armour penalty included');
    });

    test('a panel starts closed, and starts open if it was left open', () => {
        expect(rosterWith([sampleCombatant({ detail: SAMPLE_DETAIL })])
            .querySelector('.combatant-detail').open).toBe(false);
        expect(rosterWith(
            [sampleCombatant({ detail: SAMPLE_DETAIL })], new Set([1])
        ).querySelector('.combatant-detail').open).toBe(true);
    });

    test('opening one records it so a re-render leaves it open', () => {
        const openDetail = new Set();
        const roster = rosterWith(
            [sampleCombatant({ detail: SAMPLE_DETAIL })], openDetail
        );
        const panel = roster.querySelector('.combatant-detail');
        panel.open = true;
        panel.dispatchEvent(new Event('toggle'));
        expect([...openDetail]).toEqual([1]);

        // What the two-second poll does: repaint the whole roster.
        battle.renderRoster(
            roster,
            sampleState([sampleCombatant({ detail: SAMPLE_DETAIL })]),
            undefined,
            {},
            openDetail
        );
        expect(roster.querySelector('.combatant-detail').open).toBe(true);
    });

    test('closing one forgets it, so it stays closed', () => {
        const openDetail = new Set([1]);
        const roster = rosterWith(
            [sampleCombatant({ detail: SAMPLE_DETAIL })], openDetail
        );
        const panel = roster.querySelector('.combatant-detail');
        panel.open = false;
        panel.dispatchEvent(new Event('toggle'));
        expect(openDetail.size).toBe(0);
    });

    test('only a readable figure is marked selectable on the map', () => {
        const svg = document.createElement('svg');
        document.body.appendChild(svg);
        battle.renderArena(svg, sampleState([
            sampleCombatant({ combatant_id: 1, detail: SAMPLE_DETAIL }),
            sampleCombatant({ combatant_id: 2, q: 1, r: 0 }),
        ]));
        const markers = [...svg.querySelectorAll('.arena-combatant')];
        expect(markers[0].classList.contains('is-selectable')).toBe(true);
        expect(markers[1].classList.contains('is-selectable')).toBe(false);
    });

    test('clicking a figure on the map opens its roster panel', () => {
        // jsdom implements no layout, so it has no scrollIntoView. The
        // panel opening is the behaviour under test; the scroll is comfort.
        Element.prototype.scrollIntoView = jest.fn();
        document.body.innerHTML = `
            <strong id="battle-status">Paused</strong>
            <span id="battle-turn">0</span>
            <div id="battle-roster"></div>
            <svg id="battle-arena"></svg>
            <div id="battle-log"></div>
            <script id="battle-view" type="application/json">${JSON.stringify(
                sampleState([sampleCombatant({ detail: SAMPLE_DETAIL })])
            )}</script>
        `;
        battle.initBattlePage();
        const panel = document.querySelector('.combatant-detail');
        expect(panel.open).toBe(false);
        const marker = document.querySelector('.arena-combatant circle');
        marker.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        expect(panel.open).toBe(true);
    });
});


describe('initDiceToggle', () => {
    /**
     * #301: dice are always written and always in the DOM. The checkbox
     * only decides whether they are on screen, and it remembers the choice
     * rather than resetting on every load.
     */
    function mount() {
        document.body.innerHTML = `
            <input type="checkbox" id="battle-log-dice" checked>
            <div id="battle-log">
                <li class="battle-event event-roll">A attack 1d20: [14]</li>
            </div>`;
        return document.getElementById('battle-log');
    }

    beforeEach(() => { window.localStorage.clear(); });

    test('dice are shown by default', () => {
        const log = mount();
        battle.initDiceToggle(log);
        expect(log.classList.contains('dice-hidden')).toBe(false);
        expect(document.getElementById('battle-log-dice').checked).toBe(true);
    });

    test('unchecking hides the roll lines without removing them', () => {
        const log = mount();
        battle.initDiceToggle(log);
        const toggle = document.getElementById('battle-log-dice');
        toggle.checked = false;
        toggle.dispatchEvent(new Event('change'));
        expect(log.classList.contains('dice-hidden')).toBe(true);
        expect(log.querySelectorAll('.event-roll')).toHaveLength(1);
        expect(window.localStorage.getItem('battle.showDiceRolls')).toBe('hidden');
    });

    test('the stored choice is restored on the next load', () => {
        window.localStorage.setItem('battle.showDiceRolls', 'hidden');
        const log = mount();
        battle.initDiceToggle(log);
        expect(log.classList.contains('dice-hidden')).toBe(true);
        expect(document.getElementById('battle-log-dice').checked).toBe(false);
    });

    test('a page without the checkbox is left alone', () => {
        document.body.innerHTML = '<div id="battle-log"></div>';
        const log = document.getElementById('battle-log');
        expect(() => battle.initDiceToggle(log)).not.toThrow();
        expect(log.classList.contains('dice-hidden')).toBe(false);
    });
});

/**
 * Control modes on the page (#308).
 *
 * The browser's whole job here is to *show* the server's answer. It does
 * not re-derive the battle-default-versus-override precedence, and it does
 * not work out the Advance button's label from a status string — both of
 * those live in Python, beside the rules that produce them. What is tested
 * is that the page says what the server said, and that a payload from
 * before the readout existed still leaves a usable button.
 */
describe('control modes', () => {
    function seat(overrides = {}) {
        return Object.assign({
            combatant_id: 1,
            display_name: 'Redcap',
            is_open_seat: false,
            kind: 'character',
            is_saved_character: false,
            player: '',
            is_you: false,
            is_ready: false,
            team: '',
            control_mode: '',
            effective_control_mode: 'automatic',
            controller: 'the AI',
            control_line: 'Automatic — the AI plays it',
            you_decide: false,
            can_set_control: true,
        }, overrides);
    }

    function mountPage() {
        document.body.innerHTML = `
            <strong id="battle-status">Paused</strong>
            <span id="battle-turn">0</span>
            <button id="advance-turn" data-url="/tarmar/battles/1/advance/">Advance turn</button>
            <div id="battle-roster"></div>
            <svg id="battle-arena"></svg>
            <div id="battle-log"></div>
            <div id="turn-panel" data-act-url="/act/" data-state-url="/state/" data-is-dm="true" hidden></div>
            <script id="battle-view" type="application/json">${JSON.stringify(sampleState([sampleCombatant()]))}</script>
        `;
    }

    test('a roster card carries the server sentence about who decides', () => {
        document.body.innerHTML = '<div id="roster"></div>';
        const roster = document.getElementById('roster');
        battle.renderRoster(
            roster,
            sampleState([sampleCombatant()]),
            undefined,
            { 1: seat({
                effective_control_mode: 'manual',
                control_line: 'Manual — played by spencer',
            }) }
        );
        const line = roster.querySelector('.combatant-controller');
        expect(line.textContent).toBe('Manual — played by spencer');
        expect(line.dataset.mode).toBe('manual');
    });

    test('your own manual figure is named as yours', () => {
        document.body.innerHTML = '<div id="roster"></div>';
        const roster = document.getElementById('roster');
        battle.renderRoster(
            roster,
            sampleState([sampleCombatant()]),
            undefined,
            { 1: seat({
                effective_control_mode: 'manual',
                control_line: 'Manual — played by spencer',
                you_decide: true,
            }) }
        );
        expect(roster.querySelector('.combatant-controller').textContent)
            .toBe('Manual — played by you');
    });

    test('an automatic combatant says the AI plays it', () => {
        document.body.innerHTML = '<div id="roster"></div>';
        const roster = document.getElementById('roster');
        battle.renderRoster(
            roster, sampleState([sampleCombatant()]), undefined, { 1: seat() }
        );
        expect(roster.querySelector('.combatant-controller').textContent)
            .toBe('Automatic — the AI plays it');
    });

    test('the advance button takes its label and state from the server', async () => {
        mountPage();
        battle.initBattlePage();
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            json: async () => ({
                status: 'awaiting',
                turn: 0,
                view: sampleState([sampleCombatant()]),
                events: [],
                seats: [seat()],
                pending: null,
                control_mode: 'automatic',
                advance: {
                    label: "Waiting on Redcap's move",
                    enabled: false,
                    waiting_on: 'Redcap',
                },
            }),
        });
        require('../api.js');

        document.getElementById('advance-turn').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });

        const button = document.getElementById('advance-turn');
        expect(button.textContent).toBe("Waiting on Redcap's move");
        expect(button.disabled).toBe(true);
    });

    test('a payload with no readout falls back to the status rule', async () => {
        mountPage();
        battle.initBattlePage();
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            json: async () => ({
                status: 'paused',
                turn: 1,
                view: sampleState([sampleCombatant()]),
                events: [],
                seats: [seat()],
                pending: null,
            }),
        });
        require('../api.js');

        document.getElementById('advance-turn').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });

        const button = document.getElementById('advance-turn');
        expect(button.disabled).toBe(false);
        expect(button.textContent).toBe('Advance turn');
    });
});

describe('seat hand-offs on the roster (issue #853)', () => {
    test('a card offers exactly the hand-off the server names for this viewer', () => {
        document.body.innerHTML = '<div id="roster"></div>';
        const roster = document.getElementById('roster');
        const state = sampleState([
            sampleCombatant({ combatant_id: 1, name: 'Aldwin' }),
            sampleCombatant({ combatant_id: 2, name: 'Mira' }),
            sampleCombatant({ combatant_id: 3, name: 'Wolf' }),
        ]);
        battle.renderRoster(roster, state, undefined, {
            1: {
                control_line: 'Manual — played by alice',
                take_seat_url: '/tarmar/battles/1/seats/1/take/',
                return_seat_url: '',
            },
            2: {
                control_line: 'Manual — played by warden',
                take_seat_url: '',
                return_seat_url: '/tarmar/battles/1/seats/2/return/',
            },
            3: { control_line: 'Automatic — played by the AI' },
        });
        const button = (id) => roster.querySelector(
            `[data-combatant="${id}"] .seat-hand-off`
        );
        expect(button(1).textContent).toBe('Take this seat (owner absent)');
        expect(button(1).dataset.seatUrl).toBe('/tarmar/battles/1/seats/1/take/');
        expect(button(1).dataset.seatHandOff).toBe('take');
        expect(button(2).textContent).toBe('Give the seat back to its owner');
        expect(button(2).dataset.seatHandOff).toBe('return');
        expect(button(3)).toBeNull();
    });
});

describe('the hex picker (#819, #866)', () => {
    const MOVE_WITH_HEXES = {
        letter: 'd', name: 'DROP', score: 0, rationale: 'advice',
        target_id: null, spell_key: '', plain: 'Drop prone', cost: '',
        destinations: [[1, 0], [0, 1], [-1, 0]],
    };

    function mountAwaitingPage(pending, seats = []) {
        document.body.innerHTML = `
            <strong id="battle-status">Awaiting players</strong>
            <span id="battle-turn">0</span>
            <div id="battle-roster"></div>
            <svg id="battle-arena"></svg>
            <div id="battle-log"></div>
            <div id="turn-panel" hidden
                 data-act-url="/tarmar/battles/1/act/"
                 data-state-url="/tarmar/battles/1/state/"
                ></div>
            <script id="battle-view" type="application/json">${JSON.stringify(sampleState([sampleCombatant()]))}</script>
            <script id="battle-seats" type="application/json">${JSON.stringify(seats)}</script>
            <script id="battle-pending" type="application/json">${JSON.stringify(pending)}</script>
        `;
    }

    function pendingWith(candidates) {
        return {
            combatant_id: 1, turn: 1, display_name: 'Duel 1 Armoured Fighter',
            player: 'playeruser', is_you: true, candidates,
        };
    }

    test('the hex helpers', () => {
        expect(battle.sameHex([1, 2], [1, 2])).toBe(true);
        expect(battle.sameHex([1, 2], [2, 1])).toBe(false);
        expect(battle.sameHex([1, 2], null)).toBe(false);
        expect(battle.hexLabel([-1, 3])).toBe('(-1, 3)');
    });

    test('renderArena draws a button per hex, the chosen one marked', () => {
        document.body.innerHTML = '<svg id="arena"></svg>';
        const svg = document.getElementById('arena');
        const onPick = jest.fn();
        battle.renderArena(svg, sampleState([sampleCombatant()]), {
            hexes: [[1, 0], [0, 1]], chosen: [0, 1], onPick,
        });
        const cells = svg.querySelectorAll('.arena-destination');
        expect(cells).toHaveLength(2);
        expect(cells[0].getAttribute('aria-label')).toBe('Move to hex (1, 0)');
        expect(cells[1].classList.contains('is-chosen')).toBe(true);
        expect(cells[1].getAttribute('aria-pressed')).toBe('true');
        expect(svg.querySelectorAll('.arena-destination-mark')).toHaveLength(1);
        cells[0].dispatchEvent(new Event('click'));
        cells[1].dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
        cells[1].dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }));
        expect(onPick.mock.calls).toEqual([[[1, 0]], [[0, 1]]]);
    });

    test('no picker, no hexes', () => {
        document.body.innerHTML = '<svg id="arena"></svg>';
        const svg = document.getElementById('arena');
        battle.renderArena(svg, sampleState([sampleCombatant()]));
        expect(svg.querySelector('.arena-destinations')).toBeNull();
    });

    test('selecting a placing option offers its hexes; picking one sends it', async () => {
        mountAwaitingPage(pendingWith([MOVE_WITH_HEXES]));
        battle.initBattlePage();
        const arena = document.getElementById('battle-arena');
        const panel = document.getElementById('turn-panel');
        expect(arena.querySelector('.arena-destination')).toBeNull();
        panel.querySelector('.turn-option').click();
        expect(arena.querySelectorAll('.arena-destination')).toHaveLength(3);
        expect(panel.querySelector('.turn-destination-line').textContent)
            .toContain('Pick one of the 3 marked hexes');
        arena.querySelector('[data-hex="0,1"]').dispatchEvent(new Event('click'));
        expect(panel.querySelector('.turn-destination-line').textContent)
            .toBe('Moving to hex (0, 1).');
        expect(arena.querySelector('.is-chosen').getAttribute('data-hex')).toBe('0,1');
        // Clearing leaves the option making its own move.
        panel.querySelector('.turn-destination-clear').click();
        expect(arena.querySelector('.is-chosen')).toBeNull();
        arena.querySelector('[data-hex="-1,0"]').dispatchEvent(new Event('click'));
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            json: async () => ({
                status: 'paused', turn: 1, view: sampleState([sampleCombatant()]),
                events: [], turns_html: [], seats: [], pending: null,
            }),
        });
        require('../api.js');
        panel.querySelector('.turn-confirm').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });
        const sent = JSON.parse(global.fetch.mock.calls[0][1].body);
        expect(sent.destination).toEqual([-1, 0]);
        expect(arena.querySelector('.arena-destination')).toBeNull();
    });

    test('a picked hex keeps keyboard focus through the repaint (#646)', () => {
        mountAwaitingPage(pendingWith([MOVE_WITH_HEXES]));
        battle.initBattlePage();
        document.querySelector('#turn-panel .turn-option').click();
        const cell = document.querySelector('[data-hex="0,1"]');
        cell.focus();
        cell.dispatchEvent(new KeyboardEvent('keydown', { key: ' ' }));
        const picked = document.querySelector('[data-hex="0,1"]');
        expect(picked).not.toBe(cell); // the board was repainted
        expect(picked.classList.contains('is-chosen')).toBe(true);
        expect(document.activeElement).toBe(picked);
        // Picking it again unpicks it, and focus still stays on the board.
        picked.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
        expect(document.activeElement.getAttribute('data-hex')).toBe('0,1');
        expect(document.querySelector('.is-chosen')).toBeNull();
    });

    test('changing the option drops the picked hex', () => {
        mountAwaitingPage(pendingWith([
            MOVE_WITH_HEXES,
            { letter: 'k', name: 'DEFEND', score: 0.3, rationale: 'advice',
              target_id: null, spell_key: '' },
        ]));
        battle.initBattlePage();
        const panel = document.getElementById('turn-panel');
        const arena = document.getElementById('battle-arena');
        panel.querySelectorAll('.turn-option')[0].click();
        arena.querySelector('[data-hex="1,0"]').dispatchEvent(new Event('click'));
        panel.querySelectorAll('.turn-option')[1].click();
        expect(arena.querySelector('.arena-destination')).toBeNull();
        expect(panel.querySelector('.turn-destination')).toBeNull();
        panel.querySelectorAll('.turn-option')[0].click();
        expect(arena.querySelector('.is-chosen')).toBeNull();
    });

    test('a hex the engine refused is a notice for whoever chose it', () => {
        const events = [
            { actor: 'Mine', payload: { destination_refused: [1, 0] },
              message: 'Mine cannot move to (1, 0): (1, 0) is not clear' },
            { actor: 'Theirs', payload: { destination_refused: [2, 0] }, message: 'no' },
            { actor: 'Mine', payload: {}, message: 'Mine attacks' },
        ];
        const seats = [
            { display_name: 'Mine', you_decide: true },
            { display_name: 'Theirs', you_decide: false },
        ];
        expect(battle.refusedMoveNotices(events, seats))
            .toEqual(['Mine cannot move to (1, 0): (1, 0) is not clear']);
        expect(battle.refusedMoveNotices(undefined, undefined)).toEqual([]);
    });

    test('the page posts the refusal as a notice that stays', async () => {
        mountAwaitingPage(pendingWith([MOVE_WITH_HEXES]), [
            { combatant_id: 1, display_name: 'Duel 1 Armoured Fighter', you_decide: true },
        ]);
        require('../notify.js');
        battle.initBattlePage();
        const panel = document.getElementById('turn-panel');
        panel.querySelector('.turn-option').click();
        document.querySelector('[data-hex="1,0"]').dispatchEvent(new Event('click'));
        const refusal = 'Duel 1 Armoured Fighter cannot move to (1, 0): (1, 0) is not clear';
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            json: async () => ({
                status: 'paused', turn: 1, view: sampleState([sampleCombatant()]),
                events: [{ turn: 1, phase: 3, sequence: 4, event_type: 'info',
                           actor: 'Duel 1 Armoured Fighter',
                           payload: { destination_refused: [1, 0] }, message: refusal }],
                turns_html: [],
                seats: [{ combatant_id: 1, display_name: 'Duel 1 Armoured Fighter',
                          you_decide: true }],
                pending: null,
            }),
        });
        require('../api.js');
        panel.querySelector('.turn-confirm').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });
        expect(document.querySelector('.notice-text').textContent).toBe(refusal);
    });
});
