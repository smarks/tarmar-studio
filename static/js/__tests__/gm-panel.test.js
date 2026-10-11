/**
 * Unit tests for gm-panel.js — the GM side panel's tab strip (issue #341).
 *
 * The file registers whatever tabs the markup declares (data-panel-tab on
 * a button, data-panel-container on its container) and exposes
 * window.gmPanel for tabs built from script. Under Jest it does not
 * self-initialise, so each test builds the DOM and calls init() itself.
 */

function setUpDom({ tabs = ['characters', 'map'], toggle = true } = {}) {
    const buttons = tabs
        .map(
            (name) =>
                `<button type="button" id="gm-panel-tab-${name}" data-panel-tab="${name}" aria-controls="gm-panel-${name}">${name}</button>`,
        )
        .join('');
    const containers = tabs
        .map((name) => `<section id="gm-panel-${name}" data-panel-container="${name}" hidden>${name} body</section>`)
        .join('');
    document.body.innerHTML = `
        ${toggle ? '<button type="button" id="gm-panel-toggle" aria-expanded="false">Panel</button>' : ''}
        <aside id="gm-panel">
            <div role="tablist">${buttons}</div>
            ${containers}
        </aside>
    `;
}

let gmPanel;

function loadPanel() {
    jest.resetModules();
    gmPanel = require('../gm-panel.js');
    gmPanel.init();
    return gmPanel;
}

beforeEach(() => {
    window.localStorage.clear();
});

describe('tab discovery', () => {
    test('registers every data-panel-tab button and shows the first', () => {
        setUpDom();
        loadPanel();
        expect(gmPanel.tabNames()).toEqual(['characters', 'map']);
        expect(gmPanel.activeTab()).toBe('characters');
        expect(document.getElementById('gm-panel-characters').hidden).toBe(false);
        expect(document.getElementById('gm-panel-map').hidden).toBe(true);
        expect(document.getElementById('gm-panel-tab-characters').getAttribute('aria-pressed')).toBe('true');
        expect(document.getElementById('gm-panel-tab-map').getAttribute('aria-pressed')).toBe('false');
    });

    test('clicking a tab button switches the visible container', () => {
        setUpDom();
        loadPanel();
        document.getElementById('gm-panel-tab-map').click();
        expect(gmPanel.activeTab()).toBe('map');
        expect(document.getElementById('gm-panel-map').hidden).toBe(false);
        expect(document.getElementById('gm-panel-characters').hidden).toBe(true);
        expect(document.getElementById('gm-panel-tab-map').getAttribute('aria-pressed')).toBe('true');
    });

    test('reopens the tab the viewer used last', () => {
        setUpDom();
        loadPanel();
        gmPanel.activate('map');
        expect(window.localStorage.getItem('gm-panel-tab')).toBe('map');

        setUpDom();
        loadPanel();
        expect(gmPanel.activeTab()).toBe('map');
    });

    test('a remembered tab that no longer exists falls back to the first', () => {
        window.localStorage.setItem('gm-panel-tab', 'battle');
        setUpDom();
        loadPanel();
        expect(gmPanel.activeTab()).toBe('characters');
    });

    test('falls back to data-panel-container when aria-controls is missing', () => {
        document.body.innerHTML = `
            <button type="button" data-panel-tab="map">Map</button>
            <section data-panel-container="map" hidden>map</section>
        `;
        loadPanel();
        expect(gmPanel.tabNames()).toEqual(['map']);
        expect(document.querySelector('[data-panel-container="map"]').hidden).toBe(false);
    });

    test('a button with no container is skipped', () => {
        document.body.innerHTML = '<button type="button" data-panel-tab="ghost">Ghost</button>';
        loadPanel();
        expect(gmPanel.tabNames()).toEqual([]);
        expect(gmPanel.activeTab()).toBeNull();
    });
});

describe('registerTab', () => {
    test('a tab added from script joins the strip hidden and can be activated', () => {
        setUpDom({ tabs: ['characters'] });
        loadPanel();
        const button = document.createElement('button');
        const container = document.createElement('section');
        document.getElementById('gm-panel').append(button, container);

        gmPanel.registerTab('battle', button, container);
        expect(gmPanel.tabNames()).toEqual(['characters', 'battle']);
        expect(container.hidden).toBe(true);
        // No role at all: a plain button, since several panes can be open at
        // once and the strip was never a tablist (issue #707).
        expect(button.getAttribute('role')).toBeNull();
        expect(button.getAttribute('aria-pressed')).toBe('false');
        // A region, not a tabpanel: the strip is a button group (issue #707).
        expect(container.getAttribute('role')).toBe('region');

        button.click();
        expect(gmPanel.activeTab()).toBe('battle');
        expect(container.hidden).toBe(false);
        expect(document.getElementById('gm-panel-characters').hidden).toBe(true);
    });

    test('rejects a registration missing any of its three parts', () => {
        setUpDom({ tabs: [] });
        loadPanel();
        expect(() => gmPanel.registerTab('x', null, document.body)).toThrow(/needs a name/);
    });

    test('activate returns false for an unknown tab and changes nothing', () => {
        setUpDom();
        loadPanel();
        expect(gmPanel.activate('nope')).toBe(false);
        expect(gmPanel.activeTab()).toBe('characters');
    });
});

describe('small-screen toggle', () => {
    test('the Panel button opens and closes the panel', () => {
        setUpDom();
        loadPanel();
        const panel = document.getElementById('gm-panel');
        const toggle = document.getElementById('gm-panel-toggle');

        toggle.click();
        expect(panel.classList.contains('gm-panel-open')).toBe(true);
        expect(toggle.getAttribute('aria-expanded')).toBe('true');

        toggle.click();
        expect(panel.classList.contains('gm-panel-open')).toBe(false);
        expect(toggle.getAttribute('aria-expanded')).toBe('false');
    });

    test('setOpen forces a state', () => {
        setUpDom();
        loadPanel();
        expect(gmPanel.setOpen(true)).toBe(true);
        expect(gmPanel.setOpen(true)).toBe(true);
        expect(document.getElementById('gm-panel').classList.contains('gm-panel-open')).toBe(true);
        expect(gmPanel.setOpen(false)).toBe(false);
    });

    test('setOpen without a panel in the page is a no-op', () => {
        document.body.innerHTML = '';
        loadPanel();
        expect(gmPanel.setOpen()).toBe(false);
    });

    test('storage that throws does not break the panel', () => {
        setUpDom();
        const getItem = jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
            throw new Error('blocked');
        });
        const setItem = jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
            throw new Error('blocked');
        });
        try {
            loadPanel();
            expect(gmPanel.activeTab()).toBe('characters');
            expect(gmPanel.activate('map')).toBe(true);
        } finally {
            getItem.mockRestore();
            setItem.mockRestore();
        }
    });
});

describe('currentSessionId', () => {
    test('reads the session from the URL ahead of the page id', () => {
        setUpDom();
        loadPanel();
        window.history.replaceState(null, '', '?session=42');
        expect(gmPanel.currentSessionId(7)).toBe(42);
        window.history.replaceState(null, '', '?session=abc');
        expect(gmPanel.currentSessionId(7)).toBe(7);
        window.history.replaceState(null, '', window.location.pathname);
        expect(gmPanel.currentSessionId(null)).toBeNull();
    });
});
