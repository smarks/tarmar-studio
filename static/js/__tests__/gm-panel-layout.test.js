/**
 * Unit tests for gm-panel-layout.js — the GM panel's width, its stack of
 * panes and popping a pane out into its own window (issue #351).
 *
 * The file is loaded alongside gm-panel.js exactly as the page loads them
 * (layout first, then the tab registry that hands the display over), so
 * these tests exercise the real wiring: a tab click shows one pane, the
 * strip's "+" adds a second, and everything the viewer arranges comes back
 * from localStorage on the next load.
 *
 * Widths and heights are arithmetic, and jsdom lays nothing out, so the
 * maths is tested through the pure functions the drag handlers call.
 */

function setUpDom({ battleUrl = '/tarmar/battle/4/', characters = [] } = {}) {
    const sheets = characters
        .map(
            (character) =>
                `<article class="gm-sheet" data-popout-item data-popout-url="/tarmar/characters/${character.pk}/" data-popout-label="${character.name}">${character.name}</article>`,
        )
        .join('');
    document.body.innerHTML = `
        <div class="chat-layout">
            <aside class="sidebar" id="sidebar"></aside>
            <aside class="gm-panel" id="gm-panel">
                <div class="gm-panel-tabs" role="tablist">
                    <button type="button" id="gm-panel-tab-characters" data-panel-tab="characters" aria-controls="gm-panel-characters">Characters</button>
                    <button type="button" id="gm-panel-tab-map" data-panel-tab="map" aria-controls="gm-panel-map">Map</button>
                    <button type="button" id="gm-panel-tab-battle" data-panel-tab="battle" aria-controls="gm-panel-battle">Battle</button>
                </div>
                <section id="gm-panel-characters" data-panel-container="characters"
                         data-popout-empty="Attach a character to open a sheet in its own window" hidden>${sheets}</section>
                <section id="gm-panel-map" data-panel-container="map" data-popout-url="/tarmar/gm/map/" hidden></section>
                <section id="gm-panel-battle" data-panel-container="battle"
                         ${battleUrl ? `data-popout-url="${battleUrl}"` : 'data-popout-empty="No fight on the board to open yet"'} hidden></section>
            </aside>
        </div>
    `;
}

let layout;
let gmPanel;

function load() {
    jest.resetModules();
    layout = require('../gm-panel-layout.js');
    gmPanel = require('../gm-panel.js');
    gmPanel.init();
    return layout;
}

function pane(name) {
    return document.querySelector(`.gm-pane[data-pane="${name}"]`);
}

function splitButton(name) {
    return document.getElementById(`gm-panel-tab-${name}`).parentNode.querySelector('.gm-panel-split');
}

function titleButton(name, className) {
    return pane(name).querySelector(`.${className}`);
}

function storedPanes() {
    return JSON.parse(window.localStorage.getItem('gm-panel-panes'));
}

function setViewportWidth(width) {
    Object.defineProperty(window, 'innerWidth', { value: width, configurable: true, writable: true });
}

beforeEach(() => {
    window.localStorage.clear();
    setViewportWidth(1440);
    delete window.gmPanelLayout;
});

describe('width maths', () => {
    test('clamps to the minimum, to half the viewport and to a readable chat', () => {
        setUpDom();
        load();
        // Half of 1440 is 720; a 420px chat beside a 260px sidebar leaves 760.
        expect(layout.clampWidth(2000, 1440, 260)).toBe(720);
        expect(layout.clampWidth(500, 1440, 260)).toBe(500);
        expect(layout.clampWidth(100, 1440, 260)).toBe(280);
        // On a narrower window the chat's 420px is what bites first.
        expect(layout.clampWidth(2000, 1000, 260)).toBe(320);
        // Narrower still than either rule can satisfy: the panel keeps its
        // minimum rather than collapsing to nothing.
        expect(layout.clampWidth(600, 700, 260)).toBe(280);
        expect(layout.maxWidth(1440, 0)).toBe(720);
    });

    test('a width that no longer fits the viewport is clamped on load', () => {
        window.localStorage.setItem('gm-panel-width', '900');
        setViewportWidth(900);
        setUpDom();
        load();
        // Half of 900 is 450, and 900 - 420 leaves 480: 450 wins.
        expect(layout.width()).toBe(450);
        expect(document.getElementById('gm-panel').style.width).toBe('450px');
    });

    test('a missing or corrupt stored width falls back to the default', () => {
        setUpDom();
        load();
        expect(layout.width()).toBe(320);
        window.localStorage.setItem('gm-panel-width', 'wide please');
        setUpDom();
        load();
        expect(layout.width()).toBe(320);
    });

    test('the handle resizes with the arrow keys and resets on Home', () => {
        setUpDom();
        load();
        const handle = document.getElementById('gm-panel-resize');
        expect(handle.getAttribute('role')).toBe('separator');
        expect(handle.tabIndex).toBe(0);
        handle.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
        expect(layout.width()).toBe(336);
        handle.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
        expect(layout.width()).toBe(320);
        handle.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
        handle.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
        expect(layout.width()).toBe(320);
        expect(handle.getAttribute('aria-valuenow')).toBe('320');
        // An unhandled key leaves the width alone.
        handle.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true }));
        expect(layout.width()).toBe(320);
    });

    test('dragging the handle moves the boundary and stores where it stopped', () => {
        setUpDom();
        load();
        const handle = document.getElementById('gm-panel-resize');
        // jsdom has no PointerEvent; the listeners take any event carrying
        // the coordinates, which is all the handlers read.
        handle.dispatchEvent(new MouseEvent('pointerdown', { clientX: 900, bubbles: true }));
        handle.dispatchEvent(new MouseEvent('pointermove', { clientX: 800, bubbles: true }));
        expect(layout.width()).toBe(420);
        expect(document.body.classList.contains('gm-panel-resizing')).toBe(true);
        // Mid-drag the width is not written; the drag's end is the choice.
        expect(window.localStorage.getItem('gm-panel-width')).toBeNull();
        handle.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }));
        expect(document.body.classList.contains('gm-panel-resizing')).toBe(false);
        expect(window.localStorage.getItem('gm-panel-width')).toBe('420');
        // A drag with the other mouse button is not a drag.
        handle.dispatchEvent(new MouseEvent('pointerdown', { button: 2, clientX: 900, bubbles: true }));
        handle.dispatchEvent(new MouseEvent('pointermove', { clientX: 500, bubbles: true }));
        expect(layout.width()).toBe(420);
    });

    test('a dragged width is remembered and read back on the next load', () => {
        setUpDom();
        load();
        layout.setWidth(480);
        expect(window.localStorage.getItem('gm-panel-width')).toBe('480');
        setUpDom();
        load();
        expect(layout.width()).toBe(480);
        expect(document.getElementById('gm-panel').style.width).toBe('480px');
    });

    test('double-clicking the handle goes back to the default width', () => {
        setUpDom();
        load();
        layout.setWidth(600);
        document.getElementById('gm-panel-resize').dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
        expect(layout.width()).toBe(320);
    });
});

describe('pane heights', () => {
    test('a divider drag moves the boundary and keeps the pair total', () => {
        setUpDom();
        load();
        const [above, below] = layout.dividedWeights(1, 1, 300, 300, 60);
        expect(above).toBeCloseTo(1.2);
        expect(below).toBeCloseTo(0.8);
        expect(above + below).toBeCloseTo(2);
    });

    test('neither pane can be dragged away entirely', () => {
        setUpDom();
        load();
        expect(layout.dividedWeights(1, 1, 300, 300, 1000)[1]).toBeCloseTo(0.15);
        expect(layout.dividedWeights(1, 1, 300, 300, -1000)[0]).toBeCloseTo(0.15);
    });

    test('dragging the divider hands height from one pane to the other', () => {
        setUpDom();
        load();
        layout.openPane('map');
        [pane('characters'), pane('map')].forEach((box) => {
            Object.defineProperty(box, 'offsetHeight', { value: 200, configurable: true });
        });
        const divider = document.querySelector('.gm-pane-divider');
        divider.dispatchEvent(new MouseEvent('pointerdown', { clientY: 300, bubbles: true }));
        divider.dispatchEvent(new MouseEvent('pointermove', { clientY: 340, bubbles: true }));
        expect(document.body.classList.contains('gm-panel-resizing-rows')).toBe(true);
        expect(Number(pane('characters').style.flexGrow)).toBeGreaterThan(1);
        expect(Number(pane('map').style.flexGrow)).toBeLessThan(1);
        divider.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }));
        expect(document.body.classList.contains('gm-panel-resizing-rows')).toBe(false);
        expect(storedPanes().settings.characters.weight).toBeGreaterThan(1);
        // The right mouse button is not a drag here either.
        const before = pane('characters').style.flexGrow;
        divider.dispatchEvent(new MouseEvent('pointerdown', { button: 2, clientY: 300, bubbles: true }));
        divider.dispatchEvent(new MouseEvent('pointermove', { clientY: 100, bubbles: true }));
        expect(pane('characters').style.flexGrow).toBe(before);
    });

    test('the divider takes the arrow keys too', () => {
        setUpDom();
        load();
        layout.openPane('map');
        [pane('characters'), pane('map')].forEach((box) => {
            Object.defineProperty(box, 'offsetHeight', { value: 200, configurable: true });
        });
        const divider = document.querySelector('.gm-pane-divider');
        divider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
        expect(Number(pane('characters').style.flexGrow)).toBeLessThan(1);
        const shrunk = pane('characters').style.flexGrow;
        divider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
        expect(Number(pane('characters').style.flexGrow)).toBeGreaterThan(Number(shrunk));
        // An unhandled key changes nothing, and the pair's total is
        // untouched throughout: only the boundary between them moved.
        divider.dispatchEvent(new KeyboardEvent('keydown', { key: 'x', bubbles: true }));
        const stored = storedPanes().settings;
        expect(stored.characters.weight + stored.map.weight).toBeCloseTo(2);
    });

    test('a drag before the panes have a height changes nothing', () => {
        setUpDom();
        load();
        expect(layout.dividedWeights(1, 1, 0, 0, 40)).toEqual([1, 1]);
    });
});

describe('the stored arrangement', () => {
    test('nothing stored opens the tab gm-panel.js remembered', () => {
        window.localStorage.setItem('gm-panel-tab', 'map');
        setUpDom();
        load();
        expect(layout.openPanes()).toEqual(['map']);
        expect(pane('map').hidden).toBe(false);
        expect(pane('battle').hidden).toBe(true);
    });

    test('a corrupt value falls back to a single pane', () => {
        window.localStorage.setItem('gm-panel-panes', '{not json');
        setUpDom();
        load();
        expect(layout.openPanes()).toEqual(['characters']);
        expect(layout.parsePanes(['map'], '{not json')).toBeNull();
    });

    test('an unusable shape falls back too', () => {
        setUpDom();
        load();
        expect(layout.parsePanes(['map'], null)).toBeNull();
        expect(layout.parsePanes(['map'], '{"open": "map"}')).toBeNull();
        expect(layout.parsePanes(['map'], '{"open": []}')).toBeNull();
        // Panes this page does not have are dropped, and a list of only
        // those is no arrangement at all.
        expect(layout.parsePanes(['map'], '{"open": ["ghost"]}')).toBeNull();
        const parsed = layout.parsePanes(['map', 'battle'], '{"open": ["battle", "ghost", "battle"]}');
        expect(parsed.open).toEqual(['battle']);
        expect(parsed.settings.map).toEqual({ weight: 1, collapsed: false, detached: false });
    });

    test('an arrangement comes back on the next load, order and heights and all', () => {
        setUpDom();
        load();
        gmPanel.selectTab('characters');
        layout.openPane('battle');
        expect(layout.openPanes()).toEqual(['characters', 'battle']);
        const stored = storedPanes();
        stored.settings.battle.weight = 3;
        stored.settings.characters.collapsed = true;
        window.localStorage.setItem('gm-panel-panes', JSON.stringify(stored));

        setUpDom();
        load();
        expect(layout.openPanes()).toEqual(['characters', 'battle']);
        expect(pane('battle').style.flexGrow).toBe('3');
        expect(pane('characters').classList.contains('gm-pane-collapsed')).toBe(true);
        expect(pane('characters').querySelector('.gm-pane-body').hidden).toBe(true);
    });

    test('storage that throws leaves a working panel', () => {
        const getItem = jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
            throw new Error('blocked');
        });
        const setItem = jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
            throw new Error('blocked');
        });
        try {
            setUpDom();
            load();
            expect(layout.openPanes()).toEqual(['characters']);
            expect(layout.openPane('map')).toBe(true);
            expect(layout.openPanes()).toEqual(['characters', 'map']);
            expect(layout.width()).toBe(320);
        } finally {
            getItem.mockRestore();
            setItem.mockRestore();
        }
    });
});

describe('the panes', () => {
    test('every tab gets a pane box with a title bar', () => {
        setUpDom();
        load();
        expect(document.querySelectorAll('.gm-pane').length).toBe(3);
        expect(pane('battle').querySelector('.gm-pane-name').textContent).toBe('Battle');
        // The tab's own section moved into the pane's body, markup intact.
        expect(pane('map').querySelector('#gm-panel-map')).not.toBeNull();
    });

    test('clicking a tab shows that one alone', () => {
        setUpDom();
        load();
        layout.openPane('battle');
        document.getElementById('gm-panel-tab-map').click();
        expect(layout.openPanes()).toEqual(['map']);
        expect(document.getElementById('gm-panel-tab-map').getAttribute('aria-pressed')).toBe('true');
        expect(document.getElementById('gm-panel-tab-battle').getAttribute('aria-pressed')).toBe('false');
        expect(window.localStorage.getItem('gm-panel-tab')).toBe('map');
    });

    test('the strip control adds a second pane and closes it again', () => {
        setUpDom();
        load();
        expect(splitButton('battle').getAttribute('aria-pressed')).toBe('false');
        splitButton('battle').click();
        expect(layout.openPanes()).toEqual(['characters', 'battle']);
        expect(pane('battle').hidden).toBe(false);
        expect(splitButton('battle').getAttribute('aria-pressed')).toBe('true');
        expect(splitButton('battle').getAttribute('aria-label')).toBe('Close the Battle pane');
        // A divider sits between the two, and only between them.
        expect(document.querySelectorAll('.gm-pane-divider').length).toBe(1);
        splitButton('battle').click();
        expect(layout.openPanes()).toEqual(['characters']);
        expect(document.querySelectorAll('.gm-pane-divider').length).toBe(0);
    });

    test('the last pane open cannot be closed', () => {
        setUpDom();
        load();
        expect(titleButton('characters', 'gm-pane-close').disabled).toBe(true);
        expect(splitButton('characters').disabled).toBe(true);
        expect(layout.closePane('characters')).toBe(false);
        expect(layout.openPanes()).toEqual(['characters']);
        layout.openPane('map');
        expect(titleButton('characters', 'gm-pane-close').disabled).toBe(false);
        titleButton('characters', 'gm-pane-close').click();
        expect(layout.openPanes()).toEqual(['map']);
    });

    test('a pane collapses to its title bar and expands again', () => {
        setUpDom();
        load();
        layout.openPane('map');
        const collapse = titleButton('map', 'gm-pane-collapse');
        collapse.click();
        expect(pane('map').classList.contains('gm-pane-collapsed')).toBe(true);
        expect(collapse.getAttribute('aria-expanded')).toBe('false');
        expect(pane('map').style.flexGrow).toBe('0');
        expect(pane('map').style.flexBasis).toBe('auto');
        expect(storedPanes().settings.map.collapsed).toBe(true);
        collapse.click();
        expect(pane('map').classList.contains('gm-pane-collapsed')).toBe(false);
        expect(pane('map').style.flexGrow).toBe('1');
    });

    test('a collapsed or popped-out pane keeps no height and grows no divider', () => {
        window.open = jest.fn(() => null);
        setUpDom();
        load();
        layout.openPane('battle');
        expect(document.querySelectorAll('.gm-pane-divider').length).toBe(1);
        layout.toggleCollapsed('characters');
        expect(document.querySelectorAll('.gm-pane-divider').length).toBe(0);
        expect(pane('characters').style.flexGrow).toBe('0');
        layout.toggleCollapsed('characters');
        layout.popOut('battle');
        expect(document.querySelectorAll('.gm-pane-divider').length).toBe(0);
        expect(pane('battle').style.flexGrow).toBe('0');
        expect(pane('characters').style.flexGrow).toBe('1');
    });

    test('activate() from another script reveals a pane without closing the rest', () => {
        setUpDom();
        load();
        layout.openPane('map');
        layout.toggleCollapsed('map');
        // What gm-panel-battle.js's Fight button does.
        gmPanel.activate('battle');
        expect(layout.openPanes()).toEqual(['characters', 'map', 'battle']);
        gmPanel.activate('map');
        expect(pane('map').classList.contains('gm-pane-collapsed')).toBe(false);
        expect(layout.reveal('ghost')).toBe(false);
        expect(layout.openPane('ghost')).toBe(false);
        expect(layout.showOnly('ghost')).toBe(false);
        expect(layout.toggleCollapsed('ghost')).toBe(false);
    });

    test('a closed pane keeps its box in the page for its own script', () => {
        setUpDom();
        load();
        // gm-panel-map.js finds its elements by id at load; a pane that is
        // merely closed must still be there to be found.
        expect(document.getElementById('gm-panel-map')).not.toBeNull();
        expect(document.getElementById('gm-panel-battle')).not.toBeNull();
    });
});

describe('pop-out', () => {
    let opened;

    beforeEach(() => {
        opened = { closed: false, opener: window, focus: jest.fn(), close: jest.fn() };
        window.open = jest.fn(() => opened);
    });

    test('a pane opens its own page in a window named for it', () => {
        setUpDom();
        load();
        layout.openPane('battle');
        titleButton('battle', 'gm-pane-popout').click();
        expect(window.open).toHaveBeenCalledWith('/tarmar/battle/4/', 'gm-panel-battle');
        // The opener is severed rather than passed as a feature, which
        // would have turned the named window into a fresh one every time.
        expect(opened.opener).toBeNull();
        expect(opened.focus).toHaveBeenCalled();
    });

    test('the popped-out pane says so until it is brought back', () => {
        setUpDom();
        load();
        layout.openPane('battle');
        titleButton('battle', 'gm-pane-popout').click();
        const notice = pane('battle').querySelector('.gm-pane-detached');
        expect(notice.hidden).toBe(false);
        expect(notice.textContent).toContain('Battle is open in another window.');
        expect(document.getElementById('gm-panel-battle').hidden).toBe(true);
        expect(titleButton('battle', 'gm-pane-popout').disabled).toBe(true);
        expect(storedPanes().settings.battle.detached).toBe(true);

        // It is state, not a message: a reload finds the pane as it was.
        setUpDom();
        load();
        expect(pane('battle').querySelector('.gm-pane-detached').hidden).toBe(false);

        pane('battle').querySelector('.gm-pane-return').click();
        expect(pane('battle').querySelector('.gm-pane-detached').hidden).toBe(true);
        expect(document.getElementById('gm-panel-battle').hidden).toBe(false);
        expect(storedPanes().settings.battle.detached).toBe(false);
    });

    test('bringing a pane back closes the window this page opened', () => {
        setUpDom();
        load();
        layout.openPane('battle');
        titleButton('battle', 'gm-pane-popout').click();
        layout.bringBack('battle');
        expect(opened.close).toHaveBeenCalled();
    });

    test('the Characters pane opens the sheet on show', () => {
        setUpDom({ characters: [{ pk: 7, name: 'Aldwin the Bold' }, { pk: 9, name: 'Mira Quickhand' }] });
        load();
        const target = layout.popoutTarget('characters');
        expect(target.url).toBe('/tarmar/characters/7/');
        expect(target.label).toBe('Aldwin the Bold');
        expect(titleButton('characters', 'gm-pane-popout').title).toBe(
            'Open Aldwin the Bold in its own window',
        );
    });

    test('a pane with nothing to open says why, and opens nothing', () => {
        setUpDom({ battleUrl: null });
        load();
        layout.openPane('battle');
        const control = titleButton('battle', 'gm-pane-popout');
        expect(control.disabled).toBe(true);
        expect(control.title).toBe('No fight on the board to open yet');
        expect(layout.popoutTarget('battle')).toBeNull();
        expect(layout.popOut('battle')).toBe(false);
        expect(layout.popOut('ghost')).toBe(false);
        expect(window.open).not.toHaveBeenCalled();
        // With no characters attached the Characters pane says its own line.
        expect(titleButton('characters', 'gm-pane-popout').title).toBe(
            'Attach a character to open a sheet in its own window',
        );
    });

    test('a tab drawn again in place has its control painted, and the focus stays (#89)', () => {
        setUpDom({ battleUrl: null });
        load();
        layout.openPane('battle');
        layout.openPane('characters');
        const control = titleButton('battle', 'gm-pane-popout');
        expect(control.disabled).toBe(true);
        const typing = document.createElement('textarea');
        document.getElementById('gm-panel-characters').append(typing);
        typing.focus();

        // A fight drawn into the tab: it has a page to open now.
        const battle = document.getElementById('gm-panel-battle');
        battle.setAttribute('data-popout-url', '/tarmar/battle/4/');
        battle.removeAttribute('data-popout-empty');
        layout.paintPopouts();

        expect(control.disabled).toBe(false);
        expect(control.title).toBe('Open Battle in its own window');
        expect(document.activeElement).toBe(typing);
    });

    test('a window the browser refused leaves the pane detached all the same', () => {
        window.open = jest.fn(() => null);
        setUpDom();
        load();
        layout.openPane('battle');
        expect(layout.popOut('battle')).toBe(true);
        expect(pane('battle').querySelector('.gm-pane-detached').hidden).toBe(false);
        layout.bringBack('battle');
        expect(pane('battle').querySelector('.gm-pane-detached').hidden).toBe(true);
    });
});

describe('without the layout', () => {
    test('gm-panel.js on its own still switches tabs', () => {
        setUpDom();
        jest.resetModules();
        const panel = require('../gm-panel.js');
        panel.init();
        expect(document.querySelectorAll('.gm-pane').length).toBe(0);
        expect(panel.activate('map')).toBe(true);
        expect(document.getElementById('gm-panel-map').hidden).toBe(false);
        expect(document.getElementById('gm-panel-characters').hidden).toBe(true);
        expect(panel.selectTab('battle')).toBe(true);
        expect(document.getElementById('gm-panel-battle').hidden).toBe(false);
        expect(panel.selectTab('ghost')).toBe(false);
    });

    test('the layout does nothing without a panel to build on', () => {
        document.body.innerHTML = '';
        jest.resetModules();
        const standalone = require('../gm-panel-layout.js');
        expect(standalone.init({ tabEntries: () => [] })).toBe(false);
        expect(standalone.isActive()).toBe(false);
    });
});
