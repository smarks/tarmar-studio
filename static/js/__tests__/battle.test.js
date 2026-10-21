/**
 * Unit tests for battle.js — the battle detail page's arena/roster/log
 * rendering and the Advance turn wiring.
 */

const { flush } = require('./test-helpers');
// The turn panel's own words (#94), as the page's #battle-turn-panel-words
// carries them, in the shape of the server's fixed test words
// (battle/tests/rulings_words.py): each sentence is its own key followed by
// the {names} it fills, so a test proves which sentence was used and what
// filled it, never the shipped wording. A Python test holds the file to the
// server's FIXED_WORDS.
const PANEL_WORDS = require('./fixtures/turn-panel-words.json');

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

// The stale-pools notice as views.advance_control and _stale_pools.html
// render it: the stale state's key in data-record, and a form whose CSRF
// token Django masks afresh on every render (#134). ``nextPath`` is the path
// of the endpoint that rendered it, which battle.js points back at the page.
function staleNotice(record, csrfToken, nextPath = '/tarmar/battles/1/state/') {
    return `<p id="battle-stale-pools" data-record='${record}'>Aldwin moved.</p>`
        + '<form method="post" action="/tarmar/battles/1/resnapshot/">'
        + `<input type="hidden" name="csrfmiddlewaretoken" value="${csrfToken}">`
        + `<input type="hidden" name="next" value="${nextPath}">`
        + '<button id="resnapshot-pools">Take the pools from the records</button></form>';
}

const ALDWIN_STALE = '{"moved": ["Aldwin"], "resnapshot_url": "/tarmar/battles/1/resnapshot/"}';

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

// A wired page polls until the page goes; this file's jsdom outlives every
// page, so each test's page stops its polls when the test ends (#25).
afterEach(() => battle.stopPagePolls());

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
                stale_html: staleNotice(ALDWIN_STALE, 'a-token', '/tarmar/battles/1/advance/'),
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
            respond(staleNotice(ALDWIN_STALE, 'a-token', '/tarmar/battles/1/advance/'))
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

    // A Game Master's ruling panels (#867, #37), as the server renders them:
    // the record in data-record, and a form whose next is the endpoint's path.
    function rulingPanel(panelId, record, line) {
        return `<div id="${panelId}" data-record='${record}'>`
            + `<p class="ruling-line">${line}</p>`
            + '<form><input type="hidden" name="next" value="/tarmar/battles/1/advance/"></form>'
            + '</div>';
    }

    function respondWithRulings(rulingsHtml) {
        return {
            ok: true,
            status: 200,
            json: async () => ({
                status: 'paused',
                turn: 1,
                view: sampleState([sampleCombatant()]),
                events: [],
                rulings: {},
                rulings_html: rulingsHtml,
            }),
        };
    }

    test('an advance repaints the hand-to-hand-by-agreement panel from its record (#867)', async () => {
        mountDetailPage();
        document.body.insertAdjacentHTML(
            'beforeend', rulingPanel('battle-hth-agreement', '{}', 'No pair is marked.')
        );
        battle.initBattlePage();
        require('../api.js');
        global.fetch = jest.fn().mockResolvedValue(respondWithRulings({
            'battle-hth-agreement': rulingPanel(
                'battle-hth-agreement', '{"whole_battle": true}', 'Every pair may agree.'
            ),
        }));

        document.getElementById('advance-turn').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });

        const panel = document.getElementById('battle-hth-agreement');
        expect(panel.querySelector('.ruling-line').textContent).toBe('Every pair may agree.');
        // Back to the page the panel is on, never the endpoint that answered.
        expect(panel.querySelector('input[name="next"]').value)
            .toBe(`${window.location.pathname}${window.location.search}`);
    });

    test('a repainted rulings panel is written in the viewer\'s zone (#62)', async () => {
        mountDetailPage();
        document.body.insertAdjacentHTML(
            'beforeend', rulingPanel('battle-magic-bounds', '[]', 'No bound is lifted.')
        );
        battle.initBattlePage();
        require('../api.js');
        global.applyLocalTimes = jest.fn();
        const lifted = '[{"bound": "channel", "combatant_id": 1}]';
        global.fetch = jest.fn().mockResolvedValue(respondWithRulings({
            'battle-magic-bounds': rulingPanel(
                'battle-magic-bounds', lifted, 'Channel lifted for Aldwin.'
            ),
        }));

        document.getElementById('advance-turn').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });

        const repainted = document.getElementById('battle-magic-bounds');
        expect(global.applyLocalTimes).toHaveBeenCalledWith(repainted);
        delete global.applyLocalTimes;
    });

    test('an advance repaints the magic-bounds panel, and leaves an unchanged one alone (#37)', async () => {
        mountDetailPage();
        document.body.insertAdjacentHTML(
            'beforeend', rulingPanel('battle-magic-bounds', '[]', 'No bound is lifted.')
        );
        battle.initBattlePage();
        require('../api.js');
        const lifted = '[{"bound": "channel", "combatant_id": 1}]';
        global.fetch = jest.fn().mockResolvedValue(respondWithRulings({
            'battle-magic-bounds': rulingPanel(
                'battle-magic-bounds', lifted, 'Channel lifted for Aldwin.'
            ),
        }));

        document.getElementById('advance-turn').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });
        const repainted = document.getElementById('battle-magic-bounds');
        expect(repainted.querySelector('.ruling-line').textContent)
            .toBe('Channel lifted for Aldwin.');

        // The same record again (a fresh render differs only in its CSRF
        // token) is no change: the element on the page stays.
        expect(battle.rulingPanelChanges({
            'battle-magic-bounds': rulingPanel('battle-magic-bounds', lifted, 'Other.'),
        })).toHaveLength(0);
        // Looked up within the scope the page was wired with, as the arena is.
        const elsewhere = document.createElement('section');
        expect(battle.repaintRulings({ 'battle-magic-bounds': '' }, '/here', elsewhere))
            .toBe(0);
        expect(document.getElementById('battle-magic-bounds')).not.toBeNull();
        expect(battle.repaintRulings({ 'battle-magic-bounds': '' }, '/here')).toBe(1);
        expect(document.getElementById('battle-magic-bounds')).toBeNull();
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

    describe('a rename being typed when the roster is drawn again (issue #16)', () => {
        function startTyping(roster, typed) {
            roster.querySelector('.combatant-name').click();
            const input = roster.querySelector('.combatant-rename-input');
            input.value = typed;
            input.setSelectionRange(2, 4);
            return input;
        }

        test('goes on in the redrawn card with the text, focus and caret', () => {
            const { roster, state, options } = rosterWithOptions();
            global.fetch = jest.fn();
            const typed = startTyping(roster, 'Kestra the Grey');

            // A poll brings another seat's move: the roster is drawn again.
            battle.renderRoster(roster, state, options);
            // The browser blurs a focused field it removes; that blur saved
            // the half-typed name.
            typed.dispatchEvent(new FocusEvent('blur'));

            const input = roster.querySelector('.combatant-rename-input');
            expect(input).not.toBe(typed);
            expect(input.value).toBe('Kestra the Grey');
            expect(document.activeElement).toBe(input);
            expect([input.selectionStart, input.selectionEnd]).toEqual([2, 4]);
            expect(global.fetch).not.toHaveBeenCalled();
            expect(options.onRenamed).not.toHaveBeenCalled();
        });

        test('a rename already saving is not opened again', () => {
            const { roster, state, options } = rosterWithOptions();
            global.fetch = jest.fn(() => new Promise(() => {}));
            require('../api.js');
            const typed = startTyping(roster, 'Kestra the Grey');
            typed.blur();
            expect(global.fetch).toHaveBeenCalledTimes(1);

            battle.renderRoster(roster, state, options);

            expect(roster.querySelector('.combatant-rename-input')).toBeNull();
            expect(roster.querySelector('.combatant-name').textContent).toBe('Kestra Duskmere');
        });

        test('a figure no longer this viewer\'s to rename shows what was typed', () => {
            const { roster, state, options } = rosterWithOptions();
            global.fetch = jest.fn();
            startTyping(roster, 'Kestra the Grey');

            battle.renderRoster(roster, state, options, { 1: { can_rename: false } });

            expect(roster.querySelector('.combatant-rename-input')).toBeNull();
            expect(roster.querySelector('.autosave-error').textContent).toContain('Kestra the Grey');
            expect(global.fetch).not.toHaveBeenCalled();
            // The next poll's redraw keeps it, now that it is roster state.
            battle.renderRoster(roster, state, options, { 1: { can_rename: false } });
            const kept = roster.querySelector('[data-combatant="1"] .roster-rename-note');
            expect(kept.textContent).toContain('can no longer be renamed');
            expect(kept.textContent).toContain('Kestra the Grey');
            expect(kept.hasAttribute('role')).toBe(false);
        });

        test('a figure renamed meanwhile says so by the field; the draft stays', () => {
            const { roster, state, options } = rosterWithOptions();
            global.fetch = jest.fn();
            startTyping(roster, 'Kestra the Grey');
            const renamed = sampleState([sampleCombatant({ name: 'Kestra of the Pass' })]);

            battle.renderRoster(roster, renamed, options);

            expect(roster.querySelector('.combatant-rename-input').value).toBe('Kestra the Grey');
            const note = roster.querySelector('.rename-changed-note');
            expect(note.textContent).toBe('Renamed meanwhile to "Kestra of the Pass".');
            expect(note.getAttribute('role')).toBe('status');

            battle.renderRoster(roster, renamed, options);
            expect(roster.querySelector('.rename-changed-note').hasAttribute('role')).toBe(false);
            expect(roster.querySelector('.combatant-rename-input').value).toBe('Kestra the Grey');

            // Escape ends the edit, and the note with it.
            roster.querySelector('.combatant-rename-input')
                .dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
            expect(roster.querySelector('.rename-changed-note')).toBeNull();
            expect(global.fetch).not.toHaveBeenCalled();
        });

        test('a figure gone from the fight leaves the typed name above the roster', () => {
            const { roster, options } = rosterWithOptions();
            global.fetch = jest.fn();
            startTyping(roster, 'Kestra the Grey');
            const without = sampleState([sampleCombatant({ combatant_id: 2, name: 'Bran' })]);

            battle.renderRoster(roster, without, options);
            battle.renderRoster(roster, without, options);

            const block = roster.querySelector(':scope > .roster-notes');
            expect(block.textContent).toContain('Kestra Duskmere is no longer in this fight');
            expect(block.textContent).toContain('Kestra the Grey');
            expect(global.fetch).not.toHaveBeenCalled();
        });

        test('a refusal that answers after a redraw is shown on the card now drawn, and stays', async () => {
            const { roster, state, options } = rosterWithOptions();
            let answer;
            global.fetch = jest.fn(() => new Promise((resolve) => { answer = resolve; }));
            require('../api.js');
            const typed = startTyping(roster, 'Taken Name');
            typed.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
            battle.renderRoster(roster, state, options);

            answer({ ok: false, json: async () => ({ success: false, error: 'Another combatant already has that name' }) });
            await flush();

            const note = () => roster.querySelector('[data-combatant="1"] .roster-rename-note');
            expect(note().textContent).toContain('already has that name');
            expect(note().textContent).toContain('Taken Name');
            battle.renderRoster(roster, state, options);
            expect(note().textContent).toContain('already has that name');
        });

        test('Dismiss takes a note away and leaves the focus on the roster', () => {
            const { roster, state, options } = rosterWithOptions();
            global.fetch = jest.fn();
            startTyping(roster, 'Kestra the Grey');
            battle.renderRoster(roster, state, options, { 1: { can_rename: false } });

            roster.querySelector('.roster-note-dismiss').click();

            expect(roster.querySelector('.roster-rename-note')).toBeNull();
            expect(document.activeElement).toBe(roster);
            battle.renderRoster(roster, state, options, { 1: { can_rename: false } });
            expect(roster.querySelector('.roster-rename-note')).toBeNull();
        });

        test('Dismiss moves the focus to the next note\'s Dismiss', () => {
            document.body.innerHTML = '<div id="roster"></div>';
            const roster = document.getElementById('roster');
            const state = sampleState([
                sampleCombatant({ combatant_id: 1, name: 'Kestra' }),
                sampleCombatant({ combatant_id: 2, name: 'Bran' }),
            ]);
            const options = { url: '/tarmar/battles/1/rename/', csrf: 'tok', onRenamed: jest.fn() };
            const locked = { 1: { can_rename: false }, 2: { can_rename: false } };
            battle.renderRoster(roster, state, options);
            [1, 2].forEach((id) => {
                roster.querySelector(`[data-combatant="${id}"] .combatant-name`).click();
                roster.querySelector('.combatant-rename-input').value = `typed ${id}`;
                battle.renderRoster(roster, state, options, locked);
                battle.renderRoster(roster, state, options);
            });
            expect(roster.querySelectorAll('.roster-note-dismiss')).toHaveLength(2);

            roster.querySelectorAll('.roster-note-dismiss')[0].click();

            expect(document.activeElement).toBe(roster.querySelector('.roster-note-dismiss'));
        });

        test('renaming the figure again clears its note', () => {
            const { roster, state, options } = rosterWithOptions();
            global.fetch = jest.fn();
            startTyping(roster, 'Kestra the Grey');
            battle.renderRoster(roster, state, options, { 1: { can_rename: false } });
            battle.renderRoster(roster, state, options);

            roster.querySelector('.combatant-name').click();

            expect(roster.querySelector('.roster-rename-note')).toBeNull();
        });
    });

    describe('the keyboard focus through a redraw (issue #16 review)', () => {
        const DETAIL = { groups: [{ label: 'Attributes', rows: [{ label: 'STR', value: '13', note: '' }] }] };

        function roster2() {
            document.body.innerHTML = '<input id="elsewhere"><div id="roster"></div>';
            const roster = document.getElementById('roster');
            const state = sampleState([
                sampleCombatant({ combatant_id: 1, name: 'Kestra', detail: DETAIL }),
                sampleCombatant({ combatant_id: 2, name: 'Bran' }),
            ]);
            const options = { url: '/tarmar/battles/1/rename/', csrf: 'tok', onRenamed: jest.fn() };
            battle.renderRoster(roster, state, options);
            return { roster, state, options };
        }

        function redrawKeeps(selector, seatMap) {
            const { roster, state, options } = roster2();
            battle.renderRoster(roster, state, options, seatMap);
            const before = roster.querySelector(selector);
            before.focus();
            expect(document.activeElement).toBe(before);

            battle.renderRoster(roster, state, options, seatMap);

            const after = roster.querySelector(selector);
            expect(after).not.toBe(before);
            expect(document.activeElement).toBe(after);
        }

        test('a figure\'s name keeps it', () => {
            redrawKeeps('[data-combatant="2"] .combatant-name');
        });

        test('the attributes summary keeps it', () => {
            redrawKeeps('[data-combatant="1"] .combatant-detail summary');
        });

        test('a seat hand-off keeps it', () => {
            redrawKeeps('[data-combatant="1"] .seat-hand-off', { 1: { take_seat_url: '/tarmar/battles/1/seat/take/' } });
        });

        test('a fatal-roll link keeps it', () => {
            document.body.innerHTML = '<div id="roster"></div>';
            const roster = document.getElementById('roster');
            const state = sampleState([
                sampleCombatant({ combatant_id: 1, alive: false, conscious: false, fatal_chain: [7, 9] }),
            ]);
            battle.renderRoster(roster, state);
            roster.querySelectorAll('.fatal-chain a')[1].focus();

            battle.renderRoster(roster, state);

            expect(document.activeElement).toBe(roster.querySelectorAll('.fatal-chain a')[1]);
        });

        test('a note\'s Dismiss keeps it', () => {
            const { roster, state, options } = roster2();
            roster.querySelector('[data-combatant="1"] .combatant-name').click();
            roster.querySelector('.combatant-rename-input').value = 'typed';
            battle.renderRoster(roster, state, options, { 1: { can_rename: false } });
            const dismiss = roster.querySelector('.roster-note-dismiss');
            dismiss.focus();

            battle.renderRoster(roster, state, options);

            expect(roster.querySelector('.roster-note-dismiss')).not.toBe(dismiss);
            expect(document.activeElement).toBe(roster.querySelector('.roster-note-dismiss'));
        });

        test('a note painted for a refusal leaves the focus on the Dismiss it had', async () => {
            const { roster, state, options } = roster2();
            roster.querySelector('[data-combatant="1"] .combatant-name').click();
            roster.querySelector('.combatant-rename-input').value = 'typed';
            battle.renderRoster(roster, state, options, { 1: { can_rename: false } });
            global.fetch = jest.fn().mockResolvedValue({
                ok: false, json: async () => ({ success: false, error: 'Another combatant already has that name' }),
            });
            require('../api.js');
            roster.querySelector('[data-combatant="2"] .combatant-name').click();
            const input = roster.querySelector('.combatant-rename-input');
            input.value = 'Kestra';
            input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
            roster.querySelector('[data-focus-key="note-dismiss-1"]').focus();

            await flush();

            expect(roster.querySelectorAll('.roster-note-dismiss')).toHaveLength(2);
            expect(document.activeElement.dataset.focusKey).toBe('note-dismiss-1');
        });

        test('a control whose figure has gone hands it to the roster', () => {
            const { roster, options } = roster2();
            roster.querySelector('[data-combatant="2"] .combatant-name').focus();

            battle.renderRoster(roster, sampleState([sampleCombatant({ combatant_id: 1, name: 'Kestra' })]), options);

            expect(document.activeElement).toBe(roster);
        });

        test('focus elsewhere on the page is left there', () => {
            const { roster, state, options } = roster2();
            const elsewhere = document.getElementById('elsewhere');
            elsewhere.focus();

            battle.renderRoster(roster, state, options);

            expect(document.activeElement).toBe(elsewhere);
        });
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
        battle.renderTurnPanel(panel, null, { words: PANEL_WORDS });
        expect(panel.hidden).toBe(true);
        expect(panel.textContent).toBe('');
    });

    test('your turn renders every option with advice and the AI pick tag', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, samplePending(), { words: PANEL_WORDS,  selectedIndex: null });
        expect(panel.hidden).toBe(false);
        expect(panel.textContent).toContain('turn_panel.your_move Kestra Duskmere 3');
        const options = panel.querySelectorAll('.turn-option');
        expect(options).toHaveLength(2);
        expect(options[0].textContent).toContain('MOVE');
        expect(options[0].textContent).toContain('turn_panel.advice Close the gap 0.5');
        // The higher-scored ATTACK carries the AI pick tag; MOVE does not.
        expect(options[1].querySelector('.turn-option-ai-pick').textContent)
            .toBe('turn_panel.ai_pick');
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
        battle.renderTurnPanel(panel, describedPending(), { words: PANEL_WORDS,  selectedIndex: null });
        const options = panel.querySelectorAll('.turn-option');
        expect(options).toHaveLength(3);
        expect(options[0].querySelector('.turn-option-plain').textContent)
            .toBe('Attack Gray Wolf with bare hands: 2 in 20 to hit, '
                + '1d6-1 less 1 armour (about 2 a hit)');
        expect(options[0].querySelector('.turn-option-cost').textContent)
            .toBe('turn_panel.costs_nothing');
        expect(options[2].querySelector('.turn-option-cost').textContent)
            .toBe('turn_panel.costs 1 Fatigue');
        // The scoring is not on the rows until the reader asks for it.
        expect(panel.querySelector('.turn-option-advice')).toBeNull();
        const toggle = panel.querySelector('.turn-scoring-toggle');
        expect(toggle.textContent).toBe('turn_panel.show_scoring');
        expect(toggle.getAttribute('aria-pressed')).toBe('false');
    });

    test('the scoring switch shows every row its score and working', () => {
        const panel = panelNode();
        const onToggleScoring = jest.fn();
        battle.renderTurnPanel(panel, describedPending(), { words: PANEL_WORDS,
            selectedIndex: null, showScoring: true, onToggleScoring,
        });
        const advice = panel.querySelectorAll('.turn-option-advice');
        expect(advice).toHaveLength(3);
        expect(advice[0].textContent)
            .toBe('turn_panel.advice Strike Gray Wolf: bare hands ... 0.178');
        const toggle = panel.querySelector('.turn-scoring-toggle');
        expect(toggle.getAttribute('aria-pressed')).toBe('true');
        expect(toggle.textContent).toBe('turn_panel.hide_scoring');
        toggle.click();
        expect(onToggleScoring).toHaveBeenCalled();
    });

    test('the AI pick is the option the server marked, not the best score', () => {
        // The Dire Wolf strike outscores the AI's own choice; the server's
        // mark is what the AI chose (#775).
        const panel = panelNode();
        battle.renderTurnPanel(panel, describedPending(), { words: PANEL_WORDS,  selectedIndex: null });
        const options = panel.querySelectorAll('.turn-option');
        expect(options[2].querySelector('.turn-option-ai-pick')).not.toBeNull();
        expect(options[1].querySelector('.turn-option-ai-pick')).toBeNull();
        expect(battle.topCandidateIndex(describedPending().candidates)).toBe(2);
    });

    test('each target of an attack is its own row with its own target', () => {
        const panel = panelNode();
        const onConfirm = jest.fn();
        battle.renderTurnPanel(panel, describedPending(), { words: PANEL_WORDS,
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
        battle.renderTurnPanel(panel, samplePending(), { words: PANEL_WORDS,
            selectedIndex: null, onSelect,
        });
        expect(panel.querySelector('.turn-confirm').disabled).toBe(true);
        panel.querySelectorAll('.turn-option')[1].click();
        expect(onSelect).toHaveBeenCalledWith(1);

        battle.renderTurnPanel(panel, samplePending(), { words: PANEL_WORDS,  selectedIndex: 1 });
        const options = panel.querySelectorAll('.turn-option');
        expect(options[1].getAttribute('aria-pressed')).toBe('true');
        expect(options[0].getAttribute('aria-pressed')).toBe('false');
        expect(panel.querySelector('.turn-confirm').disabled).toBe(false);
    });

    test('confirm hands the selected candidate to onConfirm', () => {
        const panel = panelNode();
        const onConfirm = jest.fn();
        battle.renderTurnPanel(panel, samplePending(), { words: PANEL_WORDS,
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
            panel, samplePending({ is_you: false }), { words: PANEL_WORDS, isDm: false }
        );
        expect(panel.querySelector('.turn-waiting').innerHTML)
            .toBe('turn_panel.waiting_played <strong>Kestra Duskmere</strong> playeruser');
        expect(panel.querySelector('.turn-option')).toBeNull();
        expect(panel.querySelector('.turn-ai-choose')).toBeNull();
    });

    test('the DM gets the let-the-AI-choose escape hatch', () => {
        const panel = panelNode();
        const onAiChoose = jest.fn();
        battle.renderTurnPanel(
            panel, samplePending({ is_you: false }), { words: PANEL_WORDS, isDm: true, onAiChoose }
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
            <script id="battle-turn-panel-words" type="application/json">${JSON.stringify(PANEL_WORDS)}</script>
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
        expect(panel.textContent).toContain('turn_panel.waiting_played');
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
        expect(panel.textContent).toContain('turn_panel.your_move');
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

    // A choice resumes the turn, which a record-writing fight whose records
    // moved refuses as the advance does (#842): the 409 carries the notice,
    // the Game Master's control and the readout, rendered as the advance's
    // refusal is (#112).
    const stalePending = {
        combatant_id: 1, turn: 1, display_name: 'Aldwin',
        player: 'warden', is_you: true,
        candidates: [{ letter: 'j', name: 'ATTACK', score: 1.9,
                       rationale: 'advice', target_id: 2, spell_key: '' }],
    };

    function refuseStale(staleHtml, advance) {
        global.fetch = jest.fn().mockResolvedValue({
            ok: false,
            status: 409,
            json: async () => ({
                error: "Aldwin: the record's pools have changed",
                advance,
                stale_html: staleHtml,
            }),
        });
        require('../api.js');
        global.uiNotify = jest.fn();
    }

    async function confirmChoice() {
        const panel = document.getElementById('turn-panel');
        panel.querySelector('.turn-option').click();
        panel.querySelector('.turn-confirm').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });
        return panel;
    }

    test('a stale-pools refusal of a choice shows the notice and the control (#112)', async () => {
        mountAwaitingPage(stalePending);
        document.body.insertAdjacentHTML(
            'beforeend',
            '<button id="advance-turn" data-url="/tarmar/battles/1/advance/">'
            + "Waiting on Aldwin's move</button>"
            + '<div id="battle-stale-slot"></div>'
        );
        battle.initBattlePage();
        refuseStale(
            staleNotice(ALDWIN_STALE, 'a-token', '/tarmar/battles/1/act/'),
            { label: 'Pools changed on the records', enabled: false }
        );

        const panel = await confirmChoice();

        expect(document.getElementById('battle-stale-pools').textContent).toContain('Aldwin');
        expect(document.getElementById('resnapshot-pools')).not.toBeNull();
        // Back to the page the choice was made on, never the act endpoint.
        expect(document.querySelector('#battle-stale-slot input[name="next"]').value)
            .toBe(`${window.location.pathname}${window.location.search}`);
        expect(document.getElementById('advance-turn').textContent)
            .toBe('Pools changed on the records');
        expect(document.getElementById('advance-turn').disabled).toBe(true);
        expect(global.uiNotify).not.toHaveBeenCalled();
        // The menu stays, so the choice can be made again once the pools
        // are taken from the records.
        expect(panel.querySelector('.turn-option')).not.toBeNull();
    });

    test('a stale-pools refusal with no notice for this viewer says why (#112)', async () => {
        // A player who does not run the battle gets no readout and no
        // control (views.advance_control), so the refusal's words are all
        // there is to show.
        mountAwaitingPage(stalePending);
        document.body.insertAdjacentHTML('beforeend', '<div id="battle-stale-slot"></div>');
        battle.initBattlePage();
        refuseStale('', null);

        await confirmChoice();

        expect(document.getElementById('battle-stale-pools')).toBeNull();
        expect(global.uiNotify).toHaveBeenCalledWith(
            "Sending the action failed (HTTP 409: Aldwin: the record's pools have changed)."
        );
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
            <script id="battle-turn-panel-words" type="application/json">${JSON.stringify(PANEL_WORDS)}</script>
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
        battle.renderTurnPanel(panel, twoOptionPending(), { words: PANEL_WORDS,  selectedIndex: 0 });
        expect(document.activeElement).toBe(outside);
    });

    test('a panel with nothing pending forgets where focus was', () => {
        const panel = mountAwaitingPage(twoOptionPending());
        panel.querySelectorAll('.turn-option')[0].focus();
        battle.renderTurnPanel(panel, null, { words: PANEL_WORDS });
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
            <script id="battle-turn-panel-words" type="application/json">${JSON.stringify(PANEL_WORDS)}</script>
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
                poll(staleNotice(ALDWIN_STALE, 'a-token'))
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

    function mountStaleGameMasterPage(record) {
        const pending = {
            combatant_id: 4, turn: 3, display_name: 'Duel 2 Archer',
            player: 'playeruser', is_you: false, candidates: [],
        };
        mountAwaitingPage(pending, 'data-is-dm="true"');
        document.body.insertAdjacentHTML(
            'beforeend',
            `<div id="battle-stale-slot">${staleNotice(record, 'first-token')}</div>`
            + '<script id="battle-status-labels" type="application/json">'
            + `${JSON.stringify(STATUS_CHOICES)}</script>`
        );
        battle.initBattlePage();
        return (staleHtml, turn = 0) => ({
            ok: true,
            json: async () => ({
                status: 'awaiting', turn,
                view: sampleState([sampleCombatant()]),
                events: [], turns_html: [], seats: [], pending,
                advance: { label: 'Pools changed on the records', enabled: false },
                stale_html: staleHtml,
            }),
        });
    }

    test('polls with the same stale state leave the Game Master\'s control in place (#134)', async () => {
        jest.useFakeTimers();
        try {
            const poll = mountStaleGameMasterPage(ALDWIN_STALE);
            const control = document.getElementById('resnapshot-pools');
            const token = () => document.querySelector(
                '#battle-stale-slot input[name="csrfmiddlewaretoken"]'
            ).value;

            // Only the token differs, as it does between any two renders.
            global.fetch = jest.fn().mockResolvedValue(
                poll(staleNotice(ALDWIN_STALE, 'second-token'))
            );
            await jest.advanceTimersByTimeAsync(2100);
            expect(global.fetch).toHaveBeenCalled();
            expect(document.getElementById('resnapshot-pools')).toBe(control);
            // The kept form takes the newest token, which a sign-in elsewhere
            // may have turned over, and still returns to this page.
            expect(token()).toBe('second-token');
            expect(document.querySelector('#battle-stale-slot input[name="next"]').value)
                .toBe(`${window.location.pathname}${window.location.search}`);

            // A poll that changed something else is applied, and the notice
            // is still left alone.
            global.fetch = jest.fn().mockResolvedValue(
                poll(staleNotice(ALDWIN_STALE, 'third-token'), 1)
            );
            await jest.advanceTimersByTimeAsync(2100);
            expect(document.getElementById('battle-turn').textContent).toBe('1');
            expect(document.getElementById('resnapshot-pools')).toBe(control);
            expect(token()).toBe('third-token');
        } finally {
            jest.useRealTimers();
        }
    });

    test('a poll whose stale state changed replaces the notice and its control (#134)', async () => {
        jest.useFakeTimers();
        try {
            const poll = mountStaleGameMasterPage(ALDWIN_STALE);
            const control = document.getElementById('resnapshot-pools');
            const bothStale = '{"moved": ["Aldwin", "Brynn"], '
                + '"resnapshot_url": "/tarmar/battles/1/resnapshot/"}';

            global.fetch = jest.fn().mockResolvedValue(
                poll(staleNotice(bothStale, 'second-token'))
            );
            await jest.advanceTimersByTimeAsync(2100);

            expect(document.getElementById('battle-stale-pools').dataset.record)
                .toBe(bothStale);
            expect(document.getElementById('resnapshot-pools')).not.toBe(control);
            // Back to the page it is on, never the poll endpoint.
            expect(document.querySelector('#battle-stale-slot input[name="next"]').value)
                .toBe(`${window.location.pathname}${window.location.search}`);
        } finally {
            jest.useRealTimers();
        }
    });

    test('a poll that brings a changed ruling repaints its panel (#37)', async () => {
        jest.useFakeTimers();
        try {
            const pending = {
                combatant_id: 4, turn: 3, display_name: 'Duel 2 Archer',
                player: 'playeruser', is_you: false, candidates: [],
            };
            mountAwaitingPage(pending, 'data-is-dm="true"');
            document.body.insertAdjacentHTML(
                'beforeend',
                '<div id="battle-magic-bounds" data-record="[]"><p>No bound is lifted.</p></div>'
                + '<script id="battle-status-labels" type="application/json">'
                + `${JSON.stringify(STATUS_CHOICES)}</script>`
            );
            battle.initBattlePage();
            // Nothing else about the battle changed: only the ruling did.
            global.fetch = jest.fn().mockResolvedValue({
                ok: true,
                json: async () => ({
                    status: 'awaiting', turn: 0,
                    view: sampleState([sampleCombatant()]),
                    events: [], turns_html: [], seats: [], pending,
                    rulings_html: {
                        'battle-magic-bounds': '<div id="battle-magic-bounds" '
                            + `data-record='[{"bound": "channel"}]'>`
                            + '<p>Channel lifted for Aldwin.</p></div>',
                    },
                }),
            });

            await jest.advanceTimersByTimeAsync(2100);

            expect(document.getElementById('battle-magic-bounds').textContent)
                .toContain('Channel lifted for Aldwin.');
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
        battle.renderTurnPanel(panel, castingPending(), { words: PANEL_WORDS,  selectedIndex: null });
        const options = panel.querySelectorAll('.turn-option');
        // MOVE, DODGE and one cast group — not one row per spell.
        expect(options).toHaveLength(3);
        const cast = panel.querySelector('.turn-option-cast');
        expect(cast).not.toBeNull();
        expect(cast.textContent).toContain('turn_panel.cast_option');
        expect(cast.textContent).toContain('turn_panel.pool 10 4 Mana');
        // The AI's favourite is a cast, so the group wears the tag.
        expect(cast.querySelector('.turn-option-ai-pick')).not.toBeNull();
        // Closed by default: no spell list until the player declares a cast.
        expect(panel.querySelector('.turn-cast-stage')).toBeNull();
    });

    test('declaring a cast opens the stage without choosing a spell', () => {
        const panel = panelNode();
        const onCastOpen = jest.fn();
        battle.renderTurnPanel(panel, castingPending(), { words: PANEL_WORDS,
            selectedIndex: null, onCastOpen,
        });
        panel.querySelector('.turn-option-cast').click();
        expect(onCastOpen).toHaveBeenCalled();

        battle.renderTurnPanel(panel, castingPending(), { words: PANEL_WORDS,
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
        battle.renderTurnPanel(panel, castingPending(), { words: PANEL_WORDS,
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
        expect(spells[0].querySelector('.turn-option-ai-pick').textContent)
            .toBe('turn_panel.ai_pick');
    });

    test('picking a spell selects its candidate and ungates the confirm', () => {
        const panel = panelNode();
        const onSelect = jest.fn();
        battle.renderTurnPanel(panel, castingPending(), { words: PANEL_WORDS,
            selectedIndex: null, castOpen: true, onSelect,
        });
        panel.querySelectorAll('.turn-spell')[1].click();
        // Shield is candidate index 2 in the pending menu.
        expect(onSelect).toHaveBeenCalledWith(2);

        const onConfirm = jest.fn();
        battle.renderTurnPanel(panel, castingPending(), { words: PANEL_WORDS,
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
        battle.renderTurnPanel(panel, castingPending(), { words: PANEL_WORDS,
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
            <script id="battle-turn-panel-words" type="application/json">${JSON.stringify(PANEL_WORDS)}</script>
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
            <script id="battle-turn-panel-words" type="application/json">${JSON.stringify(PANEL_WORDS)}</script>
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
        battle.renderTurnPanel(panel, classicCastingPending(), { words: PANEL_WORDS,
            selectedIndex: null, castOpen: true,
        });
        const cast = panel.querySelector('.turn-option-cast');
        expect(cast.textContent).toContain('turn_panel.pool 12 12 ST');
        expect(cast.textContent).not.toContain('Mana');
        expect(panel.querySelector('.turn-cast-pool').textContent)
            .toContain('turn_panel.pool 12 12 ST');
        expect(panel.querySelector('.turn-cast-pool').textContent)
            .not.toContain('Mana');
    });

    test('one spell aimed at two foes gets a button each (#263)', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, classicCastingPending(), { words: PANEL_WORDS,
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
        battle.renderTurnPanel(panel, classicCastingPending(), { words: PANEL_WORDS,
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
        battle.renderTurnPanel(panel, classicCastingPending(), { words: PANEL_WORDS,
            selectedIndex: null,
        });
        expect(panel.querySelector('.turn-option-cast').textContent)
            .toContain('turn_panel.casts_ready 2');
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
        battle.renderTurnPanel(panel, pending, { words: PANEL_WORDS,  selectedIndex: null });
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
        battle.renderTurnPanel(panel, pending, { words: PANEL_WORDS,  selectedIndex: null });
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
        <script id="battle-turn-panel-words" type="application/json">${JSON.stringify(PANEL_WORDS)}</script>
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
            .map((button) => button.textContent.replace(PANEL_WORDS.ai_pick, ''));
    }

    test('a pushed copy is no spell of its own: one row, one count', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, pushPending(), { words: PANEL_WORDS,
            selectedIndex: null, castOpen: true,
        });
        expect(panel.querySelectorAll('.turn-spell')).toHaveLength(2);
        expect(panel.querySelector('.turn-option-cast').textContent)
            .toContain('turn_panel.casts_ready 2');
    });

    test('the push control offers exactly the amounts on the menu', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, pushPending(), { words: PANEL_WORDS,
            selectedIndex: null, castOpen: true,
        });
        // One control, beside the cast that has pushed copies; Shield has none.
        expect(panel.querySelectorAll('.turn-push')).toHaveLength(1);
        expect(amounts(panel)).toEqual([
            'turn_panel.push_none', 'turn_panel.push_amount 1', 'turn_panel.push_amount 2',
        ]);
        expect(panel.querySelector('.turn-push-label').textContent)
            .toBe('turn_panel.push_label_channel 3');
        expect(panel.querySelector('.turn-push').getAttribute('aria-label'))
            .toBe('turn_panel.push_group Fire Missile');
    });

    test('the AI pick stays on the plain cast the AI chose', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, pushPending(), { words: PANEL_WORDS,
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
        battle.renderTurnPanel(panel, pending, { words: PANEL_WORDS,
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
        battle.renderTurnPanel(panel, pushPending(), { words: PANEL_WORDS,
            selectedIndex: null, castOpen: true, onSelect,
        });
        panel.querySelectorAll('.turn-spell')[0].click();
        expect(onSelect).toHaveBeenCalledWith(1);
        battle.renderTurnPanel(panel, pushPending(), { words: PANEL_WORDS,
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
        battle.renderTurnPanel(panel, pushPending(), { words: PANEL_WORDS,
            selectedIndex: 1, castOpen: true, onSelect,
        });
        panel.querySelectorAll('.turn-push-amount')[2].click();
        expect(onSelect).toHaveBeenCalledWith(4);

        battle.renderTurnPanel(panel, pushPending(), { words: PANEL_WORDS,
            selectedIndex: 4, castOpen: true,
        });
        expect(panel.querySelectorAll('.turn-spell')[0]
            .getAttribute('aria-pressed')).toBe('true');
        expect(panel.querySelectorAll('.turn-push-amount')[2]
            .getAttribute('aria-pressed')).toBe('true');
        expect(panel.querySelector('.turn-push-cost').textContent)
            .toBe('turn_panel.costs 3 mana (1 and 2 pushed)');
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
        battle.renderTurnPanel(panel, pending, { words: PANEL_WORDS,  selectedIndex: null, castOpen: true });
        expect(panel.querySelector('.turn-option-cast').textContent)
            .toContain('turn_panel.ready_borrowing 1 turn_panel.casts_ready 0');
        expect(panel.querySelectorAll('.turn-borrow')).toHaveLength(1);
    });

    test('the borrowing control lists the menu\'s copies, in the server\'s numbers', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, borrowPending(), { words: PANEL_WORDS,
            selectedIndex: null, castOpen: true,
        });
        // Shield is affordable (its pushed copy borrows); only Lightning
        // Bolt is paid for by borrowing alone.
        expect(panel.querySelector('.turn-option-cast').textContent)
            .toContain('turn_panel.ready_borrowing 1 turn_panel.casts_ready_one');
        expect(panel.querySelector('.turn-cast-pool').textContent)
            .toBe('turn_panel.stage_pool turn_panel.pool_owing 10 2 1 Mana');
        const group = boltGroup(panel);
        const bolt = group.querySelector('.turn-spell');
        // Not castable on its own: the row stays disabled with the reason.
        expect(bolt.disabled).toBe(true);
        expect(bolt.querySelector('.turn-spell-note').textContent)
            .toBe('needs 3 mana, 1 left: borrow the 2 it lacks');
        expect(group.querySelector('.turn-push')).toBeNull();
        const choices = Array.from(group.querySelectorAll('.turn-borrow-amount'));
        expect(choices.map((button) => button.textContent))
            .toEqual(['turn_panel.borrow_amount 2', 'turn_panel.borrow_pushed 3 1']);
        expect(group.querySelector('.turn-borrow-label').textContent)
            .toBe('turn_panel.borrow_label');
        expect(choices.map((button) => button.dataset.borrowed)).toEqual(['2', '3']);
        // Nothing is borrowed until the player picks.
        expect(choices.every((button) => button.getAttribute('aria-pressed') === 'false'))
            .toBe(true);
        // Shield sits beside a borrowing copy of its push, with no plain push.
        const shieldGroup = Array.from(panel.querySelectorAll('.turn-spell-group'))
            .find((node) => node.textContent.includes('Shield'));
        expect(shieldGroup.querySelector('.turn-push')).toBeNull();
        expect(Array.from(shieldGroup.querySelectorAll('.turn-borrow-amount'))
            .map((button) => button.textContent)).toEqual(['turn_panel.borrow_pushed 1 1']);
    });

    test('a borrowing copy selects its cast and shows what it costs', () => {
        const panel = panelNode();
        const onSelect = jest.fn();
        battle.renderTurnPanel(panel, borrowPending(), { words: PANEL_WORDS,
            selectedIndex: null, castOpen: true, onSelect,
        });
        boltGroup(panel).querySelector('.turn-borrow-amount').click();
        expect(onSelect).toHaveBeenCalledWith(4);
        battle.renderTurnPanel(panel, borrowPending(), { words: PANEL_WORDS,
            selectedIndex: 4, castOpen: true,
        });
        const group = boltGroup(panel);
        expect(group.querySelector('.turn-spell').getAttribute('aria-pressed')).toBe('true');
        expect(group.querySelector('.turn-borrow-amount').getAttribute('aria-pressed'))
            .toBe('true');
        expect(group.querySelector('.turn-borrow-cost').textContent)
            .toBe('turn_panel.costs 3 mana; borrows the 2 you lack');
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

describe('a Runaway on the board (tarmar-engine #30)', () => {
    // A caster whose spell has run away, as the server describes it: the
    // card's line is battle.viewmodel._tarmar_runaway's, and the anchors are
    // the engine's menu rows with the server's plain line and cost.
    const runawayLine = 'Runaway Fire Missile: has fed for 2 turns; it takes 3 a turn from mana';

    function anchorPending() {
        return {
            combatant_id: 4,
            turn: 3,
            display_name: 'Ysolde Fenner',
            player: 'playeruser',
            is_you: true,
            candidates: [
                { letter: 'k', name: 'DEFEND', score: 0.3, rationale: 'Hold',
                  target_id: null, spell_key: '', ai_choice: false,
                  plain: 'Defend', cost: '' },
                { letter: 'anchor', name: 'Anchor Fire Missile', score: 10,
                  rationale: 'anchor roll 2d8 vs WIS', target_id: 4, anchor_target: 4,
                  spell_key: '', ai_choice: true,
                  plain: 'Take back control of your runaway Fire Missile: 2d8 at or under your WIS',
                  cost: '' },
                { letter: 'anchor', name: "Anchor Bram's Shield", score: 0,
                  rationale: 'Pay 2 mana', target_id: 7, anchor_target: 7,
                  spell_key: '', ai_choice: false,
                  plain: 'Help Bram take back their runaway Shield: 2d10 at or under your INT',
                  cost: '2 mana, paid whether it holds or not' },
            ],
        };
    }

    test('the card shows the server\'s Runaway line, and no line without one', () => {
        document.body.innerHTML = '<div id="roster"></div>';
        const roster = document.getElementById('roster');
        battle.renderRoster(roster, sampleState([
            sampleCombatant({
                runaway: { spell_key: 'fire_missile', line: runawayLine },
            }),
            sampleCombatant({ combatant_id: 2, runaway: null }),
        ]));
        const cards = roster.querySelectorAll('.combatant-card');
        const line = cards[0].querySelector('.combatant-runaway');
        expect(line.textContent).toBe(runawayLine);
        expect(line.dataset.spell).toBe('fire_missile');
        expect(cards[1].querySelector('.combatant-runaway')).toBeNull();
    });

    test('each anchor is a row with the server\'s line and cost', () => {
        document.body.innerHTML = '<div id="turn-panel" hidden></div>';
        const panel = document.getElementById('turn-panel');
        battle.renderTurnPanel(panel, anchorPending(), { words: PANEL_WORDS,  selectedIndex: null });
        const rows = panel.querySelectorAll('.turn-option-anchor');
        expect(rows).toHaveLength(2);
        expect(rows[0].querySelector('.turn-option-plain').textContent)
            .toBe('Take back control of your runaway Fire Missile: 2d8 at or under your WIS');
        expect(rows[0].querySelector('.turn-option-cost').textContent)
            .toBe('turn_panel.costs_nothing');
        expect(rows[0].querySelector('.turn-option-ai-pick')).not.toBeNull();
        expect(rows[1].querySelector('.turn-option-cost').textContent)
            .toBe('turn_panel.costs 2 mana, paid whether it holds or not');
        // Not an anchor: DEFEND carries no anchor class.
        expect(panel.querySelectorAll('.turn-option')).toHaveLength(3);
    });

    test('confirming an anchor sends whose Runaway it takes back', async () => {
        const panel = mountPage(anchorPending());
        panel.querySelectorAll('.turn-option-anchor')[1].click();
        panel.querySelector('.turn-confirm').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });
        expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({
            combatant_id: 4, turn: 3, letter: 'anchor', target_id: 7,
            spell_key: '', anchor_target: 7,
        });
    });

    test('confirming any other option sends no anchor', async () => {
        const panel = mountPage(anchorPending());
        panel.querySelector('.turn-option').click();
        panel.querySelector('.turn-confirm').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });
        expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({
            combatant_id: 4, turn: 3, letter: 'k', target_id: null, spell_key: '',
        });
    });
});

describe('counterspell on the board (tarmar-engine #31)', () => {
    // A wizard facing an enemy caster, as the server describes it: the
    // counters are the engine's menu rows (one per commitment the canon
    // allows), each with the server's plain line and cost, and an escalating
    // cast carries ``escalate`` with the server's line. Every word and
    // number here is invented.
    const contestLine = 'Fire Missile countered once by Ysolde; cast it again at Ysolde to press it';

    function counterPending() {
        return {
            combatant_id: 4,
            turn: 2,
            display_name: 'Ysolde Fenner',
            player: 'playeruser',
            is_you: true,
            candidates: [
                { letter: 'k', name: 'DEFEND', score: 0.3, rationale: 'Hold',
                  target_id: null, spell_key: '', ai_choice: true,
                  plain: 'Defend', cost: '' },
                { letter: 'counter', name: 'COUNTER SPELL Bram (2 MANA)', score: 0,
                  rationale: '+1 on the opposed roll', target_id: 7, target_name: 'Bram',
                  counter_mana: 2, spell_key: '', ai_choice: false,
                  plain: "Counter Bram's spell this turn, committing 2 mana",
                  cost: '2 mana, spent whether Bram casts or not' },
                { letter: 'counter', name: 'COUNTER SPELL Bram (1 MANA)', score: 0,
                  rationale: '+0 on the opposed roll', target_id: 7, target_name: 'Bram',
                  counter_mana: 1, spell_key: '', ai_choice: false,
                  plain: "Counter Bram's spell this turn, committing 1 mana",
                  cost: '1 mana, spent whether Bram casts or not' },
                { letter: 'counter', name: 'COUNTER SPELL Odo (1 MANA)', score: 0,
                  rationale: '+0 on the opposed roll', target_id: 8, target_name: 'Odo',
                  counter_mana: 1, spell_key: '', ai_choice: false,
                  plain: "Counter Odo's spell this turn, committing 1 mana",
                  cost: '1 mana, spent whether Odo casts or not' },
            ],
        };
    }

    function escalationPending() {
        return {
            combatant_id: 7,
            turn: 3,
            display_name: 'Bram',
            player: 'playeruser',
            is_you: true,
            candidates: [
                { letter: 'k', name: 'DEFEND', score: 0.3, rationale: 'Hold',
                  target_id: null, spell_key: '', ai_choice: true,
                  plain: 'Defend', cost: '' },
                { letter: 'h', name: 'CAST SPELL Fire Missile', score: 0,
                  rationale: 'Press the countered Fire Missile again', target_id: 4,
                  spell_key: 'fire_missile', escalate: true, ai_choice: false,
                  plain: 'Press your countered Fire Missile again at Ysolde',
                  cost: '2 mana; a counter against it means another opposed roll' },
            ],
            magic: {
                pool_label: 'Mana', pool: 6, max_pool: 10,
                spells: [
                    { key: 'fire_missile', target_id: 4, name: 'Fire Missile',
                      cost: 'Elemental — 2 mana', note: '74% on 3d6',
                      castable: true, reason: '', channel: null,
                      borrowable: false, shortfall: 0 },
                ],
            },
        };
    }

    function panelNode() {
        document.body.innerHTML = '<div id="turn-panel" hidden></div>';
        return document.getElementById('turn-panel');
    }

    test('the card shows the server\'s contest line, and none without', () => {
        document.body.innerHTML = '<div id="roster"></div>';
        const roster = document.getElementById('roster');
        battle.renderRoster(roster, sampleState([
            sampleCombatant({
                contest: { spell_key: 'fire_missile', turns: 1, line: contestLine },
            }),
            sampleCombatant({ combatant_id: 2, contest: null }),
        ]));
        const cards = roster.querySelectorAll('.combatant-card');
        expect(cards[0].querySelector('.combatant-contest').textContent).toBe(contestLine);
        expect(cards[0].querySelector('.combatant-contest').dataset.spell)
            .toBe('fire_missile');
        expect(cards[1].querySelector('.combatant-contest')).toBeNull();
    });

    test('each enemy caster gets one counter control with the menu\'s amounts', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, counterPending(), { words: PANEL_WORDS,  selectedIndex: null });
        const controls = panel.querySelectorAll('.turn-counter');
        expect(controls).toHaveLength(2);
        expect(controls[0].getAttribute('aria-label')).toBe('turn_panel.counter_group Bram');
        expect(controls[0].querySelector('.turn-counter-label').textContent)
            .toBe('turn_panel.counter_label Bram');
        // Least commitment first, whatever order the menu listed them in.
        const amounts = Array.from(controls[0].querySelectorAll('.turn-counter-amount'));
        expect(amounts.map((button) => button.textContent))
            .toEqual(['turn_panel.counter_amount 1', 'turn_panel.counter_amount 2']);
        expect(amounts.map((button) => button.dataset.counterMana)).toEqual(['1', '2']);
        expect(amounts.every((button) => button.getAttribute('aria-pressed') === 'false'))
            .toBe(true);
        expect(controls[1].dataset.caster).toBe('8');
        // A counter is not a plain row: DEFEND is the only one.
        expect(panel.querySelectorAll('.turn-option')).toHaveLength(1);
        expect(panel.querySelector('.turn-counter-cost')).toBeNull();
    });

    test('a picked commitment is pressed and shows the server\'s cost', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, counterPending(), { words: PANEL_WORDS,  selectedIndex: 1 });
        const bram = panel.querySelector('.turn-counter');
        const pressed = bram.querySelector('[aria-pressed="true"]');
        expect(pressed.textContent).toBe('turn_panel.counter_amount 2');
        expect(bram.querySelector('.turn-counter-cost').textContent)
            .toBe('turn_panel.costs 2 mana, spent whether Bram casts or not');
        expect(panel.querySelector('.turn-confirm').disabled).toBe(false);
    });

    test('confirming a counter sends its caster and its commitment', async () => {
        const panel = mountPage(counterPending());
        panel.querySelectorAll('.turn-counter')[0]
            .querySelectorAll('.turn-counter-amount')[1].click();
        panel.querySelector('.turn-confirm').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });
        expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({
            combatant_id: 4, turn: 2, letter: 'counter', target_id: 7,
            spell_key: '', counter_mana: 2,
        });
    });

    test('confirming any other option sends no counter', async () => {
        const panel = mountPage(counterPending());
        panel.querySelector('.turn-option').click();
        panel.querySelector('.turn-confirm').click();
        await new Promise((resolve) => { setTimeout(resolve, 0); });
        expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({
            combatant_id: 4, turn: 2, letter: 'k', target_id: null, spell_key: '',
        });
    });

    test('an escalating cast reads as pressing the countered spell again', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, escalationPending(), { words: PANEL_WORDS,
            selectedIndex: null, castOpen: true,
        });
        const spell = panel.querySelector('.turn-spell');
        expect(spell.classList.contains('is-escalation')).toBe(true);
        expect(spell.querySelector('.turn-spell-escalate').textContent)
            .toBe('Press your countered Fire Missile again at Ysolde');
    });

    test('a countered spell only borrowing pays for is marked as pressed again', () => {
        const pending = escalationPending();
        pending.candidates[1] = Object.assign(pending.candidates[1], {
            name: 'CAST SPELL Fire Missile (BORROW 1 MANA)', borrow: true, borrowed: 1,
            plain: 'Press your countered Fire Missile again at Ysolde, borrowing the 1 mana you lack',
        });
        Object.assign(pending.magic.spells[0], {
            castable: false, reason: 'needs 2 mana, 1 left: borrow the 1 it lacks',
            borrowable: true, shortfall: 1,
        });
        pending.magic.pool = 1;
        const panel = panelNode();
        battle.renderTurnPanel(panel, pending, { words: PANEL_WORDS,  selectedIndex: null, castOpen: true });
        const spell = panel.querySelector('.turn-spell');
        expect(spell.disabled).toBe(true);
        expect(spell.classList.contains('is-escalation')).toBe(true);
        expect(spell.querySelector('.turn-spell-escalate').textContent)
            .toBe('Press your countered Fire Missile again at Ysolde, borrowing the 1 mana you lack');
        expect(panel.querySelectorAll('.turn-borrow-amount')).toHaveLength(1);
    });

    test('counter buttons carry their commitment and no push', () => {
        const panel = panelNode();
        battle.renderTurnPanel(panel, counterPending(), { words: PANEL_WORDS,  selectedIndex: null });
        const amounts = Array.from(panel.querySelectorAll('.turn-counter-amount'));
        expect(amounts.every((button) => button.dataset.pushMana === undefined)).toBe(true);
    });

    test('a cast that does not escalate carries no escalation line', () => {
        const pending = escalationPending();
        delete pending.candidates[1].escalate;
        const panel = panelNode();
        battle.renderTurnPanel(panel, pending, { words: PANEL_WORDS,  selectedIndex: null, castOpen: true });
        expect(panel.querySelector('.turn-spell-escalate')).toBeNull();
        expect(panel.querySelector('.turn-spell').classList.contains('is-escalation'))
            .toBe(false);
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
            <script id="battle-turn-panel-words" type="application/json">${JSON.stringify(PANEL_WORDS)}</script>
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
            <script id="battle-turn-panel-words" type="application/json">${JSON.stringify(PANEL_WORDS)}</script>
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
            .toContain('turn_panel.pick_hex 3');
        arena.querySelector('[data-hex="0,1"]').dispatchEvent(new Event('click'));
        expect(panel.querySelector('.turn-destination-line').textContent)
            .toBe('turn_panel.moving_to (0, 1)');
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

describe('the poll follows the pause, and only forwards (#9)', () => {
    const figureA = sampleCombatant({ combatant_id: 1, name: 'Duel 1 Archer' });
    const figureB = sampleCombatant({ combatant_id: 2, name: 'Duel 2 Mage' });
    const pauseFor = (combatantId, displayName, isYou) => ({
        combatant_id: combatantId, turn: 1, display_name: displayName,
        player: isYou ? '' : 'playeruser', is_you: isYou,
        candidates: isYou ? [{ letter: 'D', name: 'DODGE', target_id: null }] : [],
    });

    /** A page awaiting `pending` on turn 0, its panel polling the state URL. */
    function mountAwaitingPage(pending, extraAttributes = '') {
        document.body.innerHTML = `
            <strong id="battle-status">Awaiting players</strong>
            <span id="battle-turn">0</span>
            <div id="battle-roster"></div>
            <svg id="battle-arena"></svg>
            <div id="battle-log"></div>
            <div id="turn-panel" hidden ${extraAttributes}
                 data-act-url="/tarmar/battles/1/act/"
                 data-state-url="/tarmar/battles/1/state/"></div>
            <script id="battle-view" type="application/json">${JSON.stringify(sampleState([figureA, figureB]))}</script>
            <script id="battle-seats" type="application/json">[]</script>
            <script id="battle-status-labels" type="application/json">${JSON.stringify(STATUS_CHOICES)}</script>
            <script id="battle-pending" type="application/json">${JSON.stringify(pending)}</script>
            <script id="battle-turn-panel-words" type="application/json">${JSON.stringify(PANEL_WORDS)}</script>
        `;
        battle.initBattlePage();
        return document.getElementById('turn-panel');
    }

    /** The state endpoint's answer while the turn is still paused. */
    const stillAwaiting = (pending) => ({
        status: 'awaiting', turn: 0, view: sampleState([figureA, figureB]),
        events: [], turns_html: [], seats: [], pending,
    });

    test('a poll that only moves the pause to the next figure repaints the panel', async () => {
        // One player's choice resumed the turn, and it paused again on
        // another player's figure. The partial turn rolled back, so no
        // event was written and the turn and status are as they were: who
        // is awaited is the only thing that changed.
        jest.useFakeTimers();
        try {
            const panel = mountAwaitingPage(pauseFor(1, 'Duel 1 Archer', false));
            expect(panel.querySelector('.turn-waiting').textContent)
                .toContain('Duel 1 Archer');
            global.fetch = jest.fn().mockResolvedValue({
                ok: true,
                json: async () => stillAwaiting(pauseFor(2, 'Duel 2 Mage', true)),
            });

            await jest.advanceTimersByTimeAsync(2100);

            expect(global.fetch).toHaveBeenCalled();
            expect(panel.querySelector('.turn-prompt').textContent)
                .toContain('Duel 2 Mage');
            expect(panel.querySelector('.turn-waiting')).toBeNull();
        } finally {
            jest.useRealTimers();
        }
    });

    test('a poll answer the act overtook is dropped, not drawn or read as a restart', async () => {
        // The Game Master's page polls while a move is awaited. A poll asked
        // before the choice was sent answers with the battle before it; the
        // act's own answer, which committed the turn, is applied first.
        jest.useFakeTimers();
        try {
            pageNavigation.reload.mockClear();
            const awaited = pauseFor(1, 'Duel 1 Archer', true);
            const panel = mountAwaitingPage(awaited, 'data-is-dm="true"');
            require('../api.js');
            let answerThePoll;
            const committed = {
                status: 'paused', turn: 1,
                view: Object.assign(sampleState([figureA, figureB]), { next_sequence: 9 }),
                events: [], seats: [], pending: null,
                turns_html: [{
                    turn: 1,
                    html: '<section class="turn-group" data-turn="1"><ul class="turn-events"></ul></section>',
                }],
            };
            global.fetch = jest.fn((url) => {
                if (String(url).includes('/state/')) {
                    return new Promise((resolve) => {
                        answerThePoll = () => resolve({
                            ok: true, json: async () => stillAwaiting(awaited),
                        });
                    });
                }
                return Promise.resolve({
                    ok: true, status: 200, json: async () => committed,
                });
            });

            await jest.advanceTimersByTimeAsync(2100);
            expect(answerThePoll).toBeDefined();
            panel.querySelector('.turn-option').click();
            panel.querySelector('.turn-confirm').click();
            await jest.advanceTimersByTimeAsync(0);
            expect(document.querySelector('.turn-group[data-turn="1"]')).not.toBeNull();
            expect(panel.hidden).toBe(true);

            answerThePoll();
            await jest.advanceTimersByTimeAsync(0);

            expect(pageNavigation.reload).not.toHaveBeenCalled();
            expect(panel.hidden).toBe(true);
            expect(panel.querySelector('.turn-prompt')).toBeNull();
            expect(document.getElementById('battle-turn').textContent).toBe('1');
        } finally {
            jest.useRealTimers();
        }
    });

    test('a poll answer the act overtook does not bring back the earlier pause', async () => {
        // The Game Master answers for A and the turn pauses again on B, in
        // the same turn: nothing but who is awaited changed. A poll asked
        // before the choice was sent still shows A, and would redraw A's
        // menu over B's if it were applied after the act.
        jest.useFakeTimers();
        try {
            pageNavigation.reload.mockClear();
            const awaitingA = pauseFor(1, 'Duel 1 Archer', true);
            const awaitingB = pauseFor(2, 'Duel 2 Mage', true);
            const panel = mountAwaitingPage(awaitingA, 'data-is-dm="true"');
            require('../api.js');
            let answerThePoll;
            global.fetch = jest.fn((url) => {
                if (String(url).includes('/state/')) {
                    return new Promise((resolve) => {
                        answerThePoll = () => resolve({
                            ok: true, json: async () => stillAwaiting(awaitingA),
                        });
                    });
                }
                return Promise.resolve({
                    ok: true, status: 200, json: async () => stillAwaiting(awaitingB),
                });
            });

            await jest.advanceTimersByTimeAsync(2100);
            expect(answerThePoll).toBeDefined();
            panel.querySelector('.turn-option').click();
            panel.querySelector('.turn-confirm').click();
            await jest.advanceTimersByTimeAsync(0);
            expect(panel.querySelector('.turn-prompt').textContent)
                .toContain('Duel 2 Mage');

            answerThePoll();
            await jest.advanceTimersByTimeAsync(0);

            expect(panel.querySelector('.turn-prompt').textContent)
                .toContain('Duel 2 Mage');
            expect(panel.textContent).not.toContain('Duel 1 Archer');
            expect(pageNavigation.reload).not.toHaveBeenCalled();
        } finally {
            jest.useRealTimers();
        }
    });
});

describe('stopping the page polls (#25)', () => {
    test('ends the battle page poll', async () => {
        jest.useFakeTimers();
        try {
            mountPage(null);
            await jest.advanceTimersByTimeAsync(2100);
            expect(global.fetch).toHaveBeenCalledTimes(1);

            battle.stopPagePolls();
            await jest.advanceTimersByTimeAsync(6100);
            expect(global.fetch).toHaveBeenCalledTimes(1);
        } finally {
            jest.useRealTimers();
        }
    });

    test('ends the lobby poll', async () => {
        jest.useFakeTimers();
        try {
            document.body.innerHTML = `
                <div id="battle-lobby" data-state-url="/tarmar/battles/1/state/"></div>
                <script id="battle-seats" type="application/json">[]</script>
            `;
            global.fetch = jest.fn().mockResolvedValue({
                ok: true, json: async () => ({ status: 'lobby', seats: [] }),
            });
            battle.initLobbyPage();
            await jest.advanceTimersByTimeAsync(2100);
            expect(global.fetch).toHaveBeenCalledTimes(1);

            battle.stopPagePolls();
            await jest.advanceTimersByTimeAsync(6100);
            expect(global.fetch).toHaveBeenCalledTimes(1);
            expect(pageNavigation.reload).not.toHaveBeenCalled();
        } finally {
            jest.useRealTimers();
        }
    });
});

describe('the turn panel writes only the server\'s words (#94)', () => {
    test('a page whose panel lost its words block says so as it starts', () => {
        document.body.innerHTML = `
            <svg id="battle-arena"></svg>
            <div id="turn-panel" hidden></div>
            <script id="battle-view" type="application/json">${JSON.stringify(sampleState([sampleCombatant()]))}</script>
        `;
        expect(() => battle.initBattlePage()).toThrow('#battle-turn-panel-words');
    });

    // Every word the panel draws around the menu's lines is the server's
    // [turn_panel]. Under the fixed words a word written back into
    // battle.js shows as itself instead of its key, so each panel below is
    // drawn and every key must turn up in what a reader sees or hears.
    function seen(panel) {
        // Each piece of text on its own line, so one word's key never runs
        // into the next.
        const texts = [];
        const walker = document.createTreeWalker(panel, NodeFilter.SHOW_TEXT);
        while (walker.nextNode()) { texts.push(walker.currentNode.nodeValue); }
        const labels = Array.from(panel.querySelectorAll('[aria-label]'))
            .map((node) => node.getAttribute('aria-label'));
        return [...texts, ...labels].join('\n');
    }

    function draw(pending, options) {
        document.body.innerHTML = '<div id="turn-panel" hidden></div>';
        const panel = document.getElementById('turn-panel');
        battle.renderTurnPanel(panel, pending, { words: PANEL_WORDS, ...options });
        return seen(panel);
    }

    const waitingFor = (player) => ({
        combatant_id: 1, turn: 2, display_name: 'Bram', player, is_you: false,
        candidates: [],
    });

    function choosing(candidates, magic) {
        return {
            combatant_id: 1, turn: 2, display_name: 'Bram', player: 'sam', is_you: true,
            candidates, magic,
        };
    }

    const moves = [
        { letter: 'a', name: 'MOVE', score: 0.2, rationale: 'Close', target_id: null,
          spell_key: '', plain: 'Move', cost: '', destinations: [[0, 1], [1, 0]] },
        { letter: 'j', name: 'ATTACK', score: 0.9, rationale: 'Strike', target_id: 2,
          spell_key: '', plain: 'Attack', cost: '1 Fatigue', ai_choice: true },
    ];

    const cast = (spell, extra) => ({
        letter: 'h', name: `CAST SPELL ${spell}`, score: 0, rationale: 'cast',
        target_id: 2, spell_key: spell, plain: '', cost: '1 mana', ...extra,
    });
    const row = (spell, extra) => ({
        key: spell, target_id: 2, name: spell, cost: 'Elemental — 1 mana', note: '',
        castable: true, reason: '', channel: null, ...extra,
    });

    test('every panel word turns up, and only as the server wrote it', () => {
        const drawn = [
            draw(waitingFor(''), { isDm: true }),
            draw(waitingFor('sam'), {}),
            draw(choosing(moves), { selectedIndex: 0, showScoring: true, destination: [0, 1] }),
            draw(choosing(moves), { selectedIndex: 0, showScoring: false }),
            // Two casts: one with a push and no Channel, one pressed again
            // with no line of its own; a third only borrowing pays for, with
            // no reason given for it on its own; the caster owes mana.
            draw(choosing([
                cast('missile'),
                cast('missile', { push_mana: 1 }),
                cast('bolt', { escalate: true }),
                cast('shield', { borrow: true, borrowed: 2 }),
                cast('shield', { borrow: true, borrowed: 3, push_mana: 1 }),
            ], {
                pool_label: 'Mana', pool: 1, max_pool: 10, owed: 2,
                spells: [row('missile'), row('bolt'), row('shield', { castable: false })],
            }), { castOpen: true, selectedIndex: 1 }),
            // One cast, pushed under a Channel.
            draw(choosing([cast('missile'), cast('missile', { push_mana: 1 })], {
                pool_label: 'Mana', pool: 4, max_pool: 10,
                spells: [row('missile', { channel: 3 })],
            }), { castOpen: true }),
            // A counter whose caster the menu does not name.
            draw(choosing([
                { letter: 'counter', name: 'COUNTER', score: 0, rationale: 'counter',
                  target_id: 7, counter_mana: 1, spell_key: '', cost: '1 mana' },
            ]), { selectedIndex: 0 }),
        ].join('\n');
        Object.keys(PANEL_WORDS).forEach((key) => {
            // Whole keys: "costs" is not found inside "costs_nothing".
            const drawnKey = new RegExp(`turn_panel\\.${key}(?!\\w)`);
            expect([key, drawnKey.test(drawn)]).toEqual([key, true]);
        });
    });
});

/**
 * A battle that went back under the page (#9, #89): a restart made elsewhere.
 * The arena's poll announces it on the arena; a page that can draw the arena
 * again in place (the GM chat's Battle tab) cancels the announcement and does,
 * and only a page where nothing cancels it reloads.
 */
describe('a battle that went back under the page (#89)', () => {
    /** An arena in `holder`, on turn 3, polling as a player's page does. */
    function mountArenaOnTurnThree(holder) {
        holder.innerHTML = `
            <strong id="battle-status">Awaiting players</strong>
            <span id="battle-turn">3</span>
            <div id="battle-roster"></div>
            <svg id="battle-arena"></svg>
            <div id="battle-log"></div>
            <div id="turn-panel" hidden
                 data-act-url="/tarmar/battles/1/act/"
                 data-state-url="/tarmar/battles/1/state/"></div>
            <script id="battle-view" type="application/json">${JSON.stringify(sampleState([sampleCombatant()]))}</script>
            <script id="battle-seats" type="application/json">[]</script>
            <script id="battle-pending" type="application/json">null</script>
            <script id="battle-turn-panel-words" type="application/json">${JSON.stringify(PANEL_WORDS)}</script>
        `;
        battle.initBattlePage(holder);
        require('../api.js');
    }

    /** The state endpoint answering with the battle back at its start. */
    function answerRestarted() {
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            json: async () => ({
                status: 'paused', turn: 0, view: sampleState([sampleCombatant()]),
                events: [], seats: [], pending: null,
            }),
        });
    }

    test('a page where nothing draws the arena again reloads', async () => {
        jest.useFakeTimers();
        try {
            document.body.innerHTML = '<main id="page"></main>';
            mountArenaOnTurnThree(document.getElementById('page'));
            answerRestarted();
            const heard = jest.fn();
            document.addEventListener(battle.REWOUND_EVENT, heard);

            await jest.advanceTimersByTimeAsync(2100);

            expect(heard).toHaveBeenCalledTimes(1);
            expect(heard.mock.calls[0][0].target.id).toBe('battle-arena');
            expect(pageNavigation.reload).toHaveBeenCalledTimes(1);
            document.removeEventListener(battle.REWOUND_EVENT, heard);
        } finally {
            jest.useRealTimers();
        }
    });

    test('a page that draws it again in place cancels the reload', async () => {
        jest.useFakeTimers();
        try {
            document.body.innerHTML = '<section id="gm-panel-battle"></section>';
            const tab = document.getElementById('gm-panel-battle');
            mountArenaOnTurnThree(tab);
            answerRestarted();
            const drawAgain = jest.fn((event) => event.preventDefault());
            tab.addEventListener(battle.REWOUND_EVENT, drawAgain);

            await jest.advanceTimersByTimeAsync(2100);

            expect(drawAgain).toHaveBeenCalledTimes(1);
            expect(pageNavigation.reload).not.toHaveBeenCalled();
            expect(pageNavigation.go).not.toHaveBeenCalled();
        } finally {
            jest.useRealTimers();
        }
    });

    test('an answer that lands after the arena was drawn again reloads nothing', async () => {
        jest.useFakeTimers();
        try {
            document.body.innerHTML = '<section id="gm-panel-battle"></section>';
            const tab = document.getElementById('gm-panel-battle');
            mountArenaOnTurnThree(tab);
            let answer;
            global.fetch = jest.fn(() => new Promise((resolve) => { answer = resolve; }));
            const heard = jest.fn();
            document.addEventListener(battle.REWOUND_EVENT, heard);

            // The tick's request goes out; the tab is drawn again meanwhile.
            await jest.advanceTimersByTimeAsync(2100);
            tab.replaceChildren();
            answer({
                ok: true,
                json: async () => ({
                    status: 'paused', turn: 0, view: sampleState([sampleCombatant()]),
                    events: [], seats: [], pending: null,
                }),
            });
            await jest.advanceTimersByTimeAsync(6100);

            expect(heard).not.toHaveBeenCalled();
            expect(pageNavigation.reload).not.toHaveBeenCalled();
            expect(global.fetch).toHaveBeenCalledTimes(1);
            document.removeEventListener(battle.REWOUND_EVENT, heard);
        } finally {
            jest.useRealTimers();
        }
    });

    test('an arena drawn again in its place stops its poll', async () => {
        jest.useFakeTimers();
        try {
            document.body.innerHTML = '<section id="gm-panel-battle"></section>';
            const tab = document.getElementById('gm-panel-battle');
            mountArenaOnTurnThree(tab);
            global.fetch = jest.fn().mockResolvedValue({
                ok: true,
                json: async () => ({
                    status: 'awaiting', turn: 3, view: sampleState([sampleCombatant()]),
                    events: [], seats: [], pending: null,
                }),
            });
            await jest.advanceTimersByTimeAsync(2100);
            expect(global.fetch).toHaveBeenCalledTimes(1);

            // The tab's contents drawn again: this arena is off the page.
            tab.replaceChildren();
            await jest.advanceTimersByTimeAsync(6100);

            expect(global.fetch).toHaveBeenCalledTimes(1);
        } finally {
            jest.useRealTimers();
        }
    });
});
