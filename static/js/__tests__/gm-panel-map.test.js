/**
 * Unit tests for gm-panel-map.js — the GM side panel's Map tab (issue #343).
 *
 * Under Jest the file does not self-initialise, so each test builds the tab's
 * markup (as gm/_panel_map.html renders it), sets #gm-map-config and the
 * URL, and calls init() itself.
 */

const MAPS = [
    { slug: 'maja', title: 'Maja', image: '/static/images/maja-island.png' },
    { slug: 'solberg', title: 'Solberg', image: '/static/images/solberg-island.png' },
    { slug: 'old-qasha', title: 'Old Qasha', image: '/static/images/old-qasha-island.png' },
    { slug: 'maja-solberg', title: 'Maja & Solberg', image: '/static/images/maja-solberg-map.png' },
    { slug: 'world', title: 'The World', image: '/static/images/world-map-overview.png' },
];

function setUpDom({ config = { sessionId: null, storyMap: null }, search = '' } = {}) {
    window.location.search = search;
    const options = MAPS.map(
        (map) =>
            `<option value="${map.slug}" data-title="${map.title}" data-image-url="${map.image}">${map.title}</option>`,
    ).join('');
    document.body.innerHTML = `
        <section id="gm-panel-map" data-panel-container="map" hidden>
            <div class="gm-panel-picker">
                <select id="gm-map-select" data-default-slug="maja-solberg">${options}</select>
                <label for="gm-map-follow"><input type="checkbox" id="gm-map-follow" checked> Follow the story</label>
            </div>
            <div class="gm-map-controls">
                <button type="button" id="gm-map-zoom-out">-</button>
                <button type="button" id="gm-map-zoom-in">+</button>
                <button type="button" id="gm-map-reset">Reset</button>
                <span id="gm-map-zoom-level">100%</span>
            </div>
            <div id="gm-map-viewport"><img id="gm-map-image" src="" alt=""></div>
            <p id="gm-map-caption">
                <span id="gm-map-caption-title"></span>
                <span id="gm-map-caption-place" hidden></span>
            </p>
            <p id="gm-map-unmapped" hidden></p>
            <script id="gm-map-config" type="application/json">${JSON.stringify(config)}</script>
        </section>
    `;
    // jsdom lays nothing out; give the viewport and image a size so the
    // clamping and centre-zoom arithmetic has something to work with.
    const viewport = document.getElementById('gm-map-viewport');
    const image = document.getElementById('gm-map-image');
    Object.defineProperty(viewport, 'clientWidth', { value: 300, configurable: true });
    Object.defineProperty(viewport, 'clientHeight', { value: 200, configurable: true });
    Object.defineProperty(image, 'clientWidth', { value: 300, configurable: true });
    Object.defineProperty(image, 'clientHeight', { value: 400, configurable: true });
    viewport.getBoundingClientRect = () => ({ left: 0, top: 0, width: 300, height: 200 });
}

let gmPanelMap;

function loadTab() {
    jest.resetModules();
    gmPanelMap = require('../gm-panel-map.js');
    gmPanelMap.init();
    return gmPanelMap;
}

function image() {
    return document.getElementById('gm-map-image');
}

function captionTitle() {
    return document.getElementById('gm-map-caption-title').textContent;
}

function captionPlace() {
    return document.getElementById('gm-map-caption-place');
}

function unmappedNote() {
    return document.getElementById('gm-map-unmapped');
}

function doneFrame(map) {
    document.dispatchEvent(new CustomEvent('gm-stream-done', { detail: { done: true, map } }));
}

beforeEach(() => {
    window.localStorage.clear();
});

describe('first map shown', () => {
    test('opens on the map the picker names as its default (issue #918)', () => {
        setUpDom();
        document.getElementById('gm-map-select').setAttribute('data-default-slug', 'old-qasha');
        loadTab();
        expect(gmPanelMap.currentSlug()).toBe('old-qasha');
    });

    test('opens on the first map when the picker names no default', () => {
        setUpDom();
        document.getElementById('gm-map-select').removeAttribute('data-default-slug');
        loadTab();
        expect(gmPanelMap.currentSlug()).toBe(MAPS[0].slug);
    });

    test('defaults to the regional chart with no place in the caption', () => {
        setUpDom();
        loadTab();
        expect(gmPanelMap.currentSlug()).toBe('maja-solberg');
        expect(document.getElementById('gm-map-select').value).toBe('maja-solberg');
        expect(image().getAttribute('src')).toBe('/static/images/maja-solberg-map.png');
        expect(image().alt).toBe('Map of Maja & Solberg');
        expect(captionTitle()).toBe('Maja & Solberg');
        expect(captionPlace().hidden).toBe(true);
        expect(gmPanelMap.isFollowing()).toBe(true);
    });

    test('opens on the map the story last named, with the place in the caption', () => {
        setUpDom({
            config: {
                sessionId: 7,
                storyMap: { slug: 'solberg', title: 'Solberg', image_url: '/x.png', place: 'Seaside' },
            },
        });
        loadTab();
        expect(gmPanelMap.currentSlug()).toBe('solberg');
        expect(image().getAttribute('src')).toBe('/static/images/solberg-island.png');
        expect(captionTitle()).toBe('Solberg');
        expect(captionPlace().hidden).toBe(false);
        expect(captionPlace().textContent).toBe('— Seaside');
    });

    test('prefers the map remembered for this session over the story map', () => {
        window.localStorage.setItem('gm-map:7', JSON.stringify({ slug: 'world', place: null, follow: true }));
        setUpDom({
            config: {
                sessionId: 7,
                storyMap: { slug: 'solberg', title: 'Solberg', image_url: '/x.png', place: 'Seaside' },
            },
        });
        loadTab();
        expect(gmPanelMap.currentSlug()).toBe('world');
        expect(captionPlace().hidden).toBe(true);
    });

    test('ignores the story map while following is off', () => {
        window.localStorage.setItem('gm-map:7', JSON.stringify({ follow: false }));
        setUpDom({
            config: {
                sessionId: 7,
                storyMap: { slug: 'solberg', title: 'Solberg', image_url: '/x.png', place: 'Seaside' },
            },
        });
        loadTab();
        expect(gmPanelMap.currentSlug()).toBe('maja-solberg');
        expect(gmPanelMap.isFollowing()).toBe(false);
        expect(document.getElementById('gm-map-follow').checked).toBe(false);
    });

    test('falls back to the default when the remembered slug is not a map', () => {
        window.localStorage.setItem('gm-map:new', JSON.stringify({ slug: 'atlantis', place: 'Nowhere' }));
        setUpDom();
        loadTab();
        expect(gmPanelMap.currentSlug()).toBe('maja-solberg');
    });

    test('shrugs off unreadable stored state', () => {
        window.localStorage.setItem('gm-map:new', '{not json');
        setUpDom();
        loadTab();
        expect(gmPanelMap.currentSlug()).toBe('maja-solberg');
    });

    test('does nothing on a page without the tab', () => {
        document.body.innerHTML = '<div id="messages"></div>';
        expect(() => loadTab()).not.toThrow();
        expect(gmPanelMap.currentSlug()).toBeNull();
    });
});

describe('the picker', () => {
    test('switches the map by hand and clears the place', () => {
        setUpDom({
            config: { sessionId: 3, storyMap: { slug: 'maja', title: 'Maja', image_url: '/m.png', place: 'Majahelm' } },
        });
        loadTab();
        expect(captionPlace().textContent).toBe('— Majahelm');
        const select = document.getElementById('gm-map-select');
        select.value = 'old-qasha';
        select.dispatchEvent(new Event('change'));
        expect(gmPanelMap.currentSlug()).toBe('old-qasha');
        expect(image().getAttribute('src')).toBe('/static/images/old-qasha-island.png');
        expect(captionTitle()).toBe('Old Qasha');
        expect(captionPlace().hidden).toBe(true);
        expect(JSON.parse(window.localStorage.getItem('gm-map:3'))).toEqual({
            slug: 'old-qasha',
            place: null,
            party: false,
            follow: true,
        });
    });

    test('showMap refuses an unknown slug and leaves the map alone', () => {
        setUpDom();
        loadTab();
        expect(gmPanelMap.showMap('atlantis', 'Nowhere')).toBe(false);
        expect(gmPanelMap.currentSlug()).toBe('maja-solberg');
    });

    test('the storage key follows the session id in the URL over the page config', () => {
        setUpDom({ config: { sessionId: 3, storyMap: null }, search: '?session=12' });
        loadTab();
        expect(gmPanelMap.storageKey()).toBe('gm-map:12');
        setUpDom({ config: { sessionId: null, storyMap: null }, search: '?session=abc' });
        loadTab();
        expect(gmPanelMap.storageKey()).toBe('gm-map:new');
    });
});

describe('following the party (issue #579)', () => {
    const PARTY_AT_FARPORT = {
        slug: 'maja',
        title: 'Maja',
        image_url: '/static/images/maja-island.png',
        place: 'the well beneath Farport',
        party: true,
        mapped: true,
    };

    test('opens on the chart the party stands on, ahead of the story map', () => {
        setUpDom({
            config: {
                sessionId: 7,
                partyMap: PARTY_AT_FARPORT,
                storyMap: { slug: 'solberg', title: 'Solberg', image_url: '/x.png', place: 'Seaside' },
            },
        });
        loadTab();
        expect(gmPanelMap.currentSlug()).toBe('maja');
        expect(captionPlace().textContent).toBe('— the party is at the well beneath Farport');
        expect(unmappedNote().hidden).toBe(true);
    });

    test('a party nowhere on a chart is said, and the chart left alone', () => {
        setUpDom({
            config: {
                sessionId: 7,
                partyMap: { place: "Grandma's cellar", party: true, mapped: false },
                storyMap: null,
            },
        });
        loadTab();
        expect(gmPanelMap.currentSlug()).toBe('maja-solberg');
        expect(unmappedNote().hidden).toBe(false);
        expect(unmappedNote().textContent).toBe(
            "The party is at Grandma's cellar, which is not on any map.",
        );
    });

    test('a done frame moves the tab with the party and marks them on it', () => {
        setUpDom();
        loadTab();
        expect(gmPanelMap.handleDoneFrame({ done: true, map: PARTY_AT_FARPORT })).toBe(true);
        expect(gmPanelMap.currentSlug()).toBe('maja');
        expect(captionPlace().textContent).toBe('— the party is at the well beneath Farport');
    });

    test('a turn that moves them off the charts says so and keeps the chart up', () => {
        setUpDom();
        loadTab();
        gmPanelMap.handleDoneFrame({ done: true, map: PARTY_AT_FARPORT });
        const moved = gmPanelMap.handleDoneFrame({
            done: true,
            map: { place: "Grandma's cellar", party: true, mapped: false },
        });
        expect(moved).toBe(false);
        expect(gmPanelMap.currentSlug()).toBe('maja');
        expect(unmappedNote().hidden).toBe(false);
        expect(unmappedNote().textContent).toContain("Grandma's cellar");
    });

    test('the note goes as soon as the party is somewhere charted again', () => {
        setUpDom();
        loadTab();
        gmPanelMap.showUnmapped("Grandma's cellar");
        gmPanelMap.handleDoneFrame({ done: true, map: PARTY_AT_FARPORT });
        expect(unmappedNote().hidden).toBe(true);
        expect(unmappedNote().textContent).toBe('');
    });

    test('an unmapped party is ignored while following is off', () => {
        window.localStorage.setItem('gm-map:7', JSON.stringify({ follow: false }));
        setUpDom({
            config: {
                sessionId: 7,
                partyMap: { place: "Grandma's cellar", party: true, mapped: false },
                storyMap: null,
            },
        });
        loadTab();
        expect(unmappedNote().hidden).toBe(true);
    });
});

describe('following the story', () => {
    test('a done frame naming a place switches the map and marks the place', () => {
        setUpDom();
        loadTab();
        doneFrame({ slug: 'maja', title: 'Maja', image_url: '/static/images/maja-island.png', place: 'Majahelm' });
        expect(gmPanelMap.currentSlug()).toBe('maja');
        expect(image().getAttribute('src')).toBe('/static/images/maja-island.png');
        expect(captionTitle()).toBe('Maja');
        expect(captionPlace().textContent).toBe('— Majahelm');
        expect(document.getElementById('gm-map-select').value).toBe('maja');
    });

    test('a hand-picked map sticks until the next frame that names a place', () => {
        setUpDom();
        loadTab();
        gmPanelMap.showMap('world', null);
        doneFrame(undefined);
        expect(gmPanelMap.currentSlug()).toBe('world');
        doneFrame({ slug: 'solberg', title: 'Solberg', image_url: '/s.png', place: 'Hurhelm' });
        expect(gmPanelMap.currentSlug()).toBe('solberg');
    });

    test('frames are ignored while following is off, and the setting is remembered', () => {
        setUpDom({ config: { sessionId: 5, storyMap: null } });
        loadTab();
        const follow = document.getElementById('gm-map-follow');
        follow.checked = false;
        follow.dispatchEvent(new Event('change'));
        expect(gmPanelMap.isFollowing()).toBe(false);
        expect(JSON.parse(window.localStorage.getItem('gm-map:5')).follow).toBe(false);
        expect(gmPanelMap.handleDoneFrame({ done: true, map: { slug: 'maja', place: 'Truth' } })).toBe(false);
        expect(gmPanelMap.currentSlug()).toBe('maja-solberg');
        follow.checked = true;
        follow.dispatchEvent(new Event('change'));
        expect(gmPanelMap.handleDoneFrame({ done: true, map: { slug: 'maja', place: 'Truth' } })).toBe(true);
        expect(gmPanelMap.currentSlug()).toBe('maja');
    });

    test('a frame with a malformed map is ignored', () => {
        setUpDom();
        loadTab();
        expect(gmPanelMap.handleDoneFrame({ done: true, map: { slug: 42 } })).toBe(false);
        expect(gmPanelMap.handleDoneFrame({ done: true, map: { slug: 'atlantis' } })).toBe(false);
        expect(gmPanelMap.handleDoneFrame(null)).toBe(false);
        expect(gmPanelMap.currentSlug()).toBe('maja-solberg');
    });

    test('the place is rendered as text, never markup', () => {
        setUpDom();
        loadTab();
        doneFrame({ slug: 'maja', place: '<img src=x onerror=alert(1)>' });
        expect(captionPlace().querySelector('img')).toBeNull();
        expect(captionPlace().textContent).toBe('— <img src=x onerror=alert(1)>');
    });
});

describe('pan and zoom', () => {
    test('the buttons zoom around the centre and reset restores the fit', () => {
        setUpDom();
        loadTab();
        document.getElementById('gm-map-zoom-in').click();
        expect(gmPanelMap.view.scale).toBeCloseTo(1.5);
        expect(document.getElementById('gm-map-zoom-level').textContent).toBe('150%');
        expect(image().style.transform).toBe('translate(-75px, -50px) scale(1.5)');
        document.getElementById('gm-map-zoom-out').click();
        expect(gmPanelMap.view.scale).toBeCloseTo(1);
        expect(image().style.transform).toBe('translate(0px, 0px) scale(1)');
        document.getElementById('gm-map-zoom-in').click();
        document.getElementById('gm-map-reset').click();
        expect(image().style.transform).toBe('translate(0px, 0px) scale(1)');
    });

    test('zoom is bounded between 100% and 600%', () => {
        setUpDom();
        loadTab();
        gmPanelMap.zoomBy(0.5, 0, 0);
        expect(gmPanelMap.view.scale).toBe(1);
        gmPanelMap.zoomBy(100, 0, 0);
        expect(gmPanelMap.view.scale).toBe(6);
    });

    test('the wheel zooms around the pointer', () => {
        setUpDom();
        loadTab();
        const viewport = document.getElementById('gm-map-viewport');
        const wheel = new WheelEvent('wheel', { deltaY: -100, clientX: 100, clientY: 50, cancelable: true });
        viewport.dispatchEvent(wheel);
        expect(wheel.defaultPrevented).toBe(true);
        expect(gmPanelMap.view.scale).toBeCloseTo(1.1);
        expect(gmPanelMap.view.translateX).toBeCloseTo(-10);
        expect(gmPanelMap.view.translateY).toBeCloseTo(-5);
        viewport.dispatchEvent(new WheelEvent('wheel', { deltaY: 100, clientX: 100, clientY: 50, cancelable: true }));
        expect(gmPanelMap.view.scale).toBeCloseTo(1);
    });

    test('dragging pans within the image bounds', () => {
        setUpDom();
        loadTab();
        const viewport = document.getElementById('gm-map-viewport');
        gmPanelMap.zoomBy(2, 0, 0);
        viewport.dispatchEvent(new MouseEvent('pointerdown', { button: 0, clientX: 100, clientY: 100 }));
        expect(viewport.classList.contains('gm-map-dragging')).toBe(true);
        viewport.dispatchEvent(new MouseEvent('pointermove', { clientX: 60, clientY: 70 }));
        expect(gmPanelMap.view.translateX).toBe(-40);
        expect(gmPanelMap.view.translateY).toBe(-30);
        // Past the image's edge the pan stops at the edge.
        viewport.dispatchEvent(new MouseEvent('pointermove', { clientX: 500, clientY: 900 }));
        expect(gmPanelMap.view.translateX).toBe(0);
        expect(gmPanelMap.view.translateY).toBe(0);
        viewport.dispatchEvent(new MouseEvent('pointermove', { clientX: -900, clientY: -900 }));
        expect(gmPanelMap.view.translateX).toBe(-300);
        expect(gmPanelMap.view.translateY).toBe(-600);
        viewport.dispatchEvent(new MouseEvent('pointerup'));
        expect(viewport.classList.contains('gm-map-dragging')).toBe(false);
        viewport.dispatchEvent(new MouseEvent('pointermove', { clientX: 0, clientY: 0 }));
        expect(gmPanelMap.view.translateX).toBe(-300);
    });

    test('a secondary button press does not start a drag', () => {
        setUpDom();
        loadTab();
        const viewport = document.getElementById('gm-map-viewport');
        viewport.dispatchEvent(new MouseEvent('pointerdown', { button: 2, clientX: 10, clientY: 10 }));
        expect(viewport.classList.contains('gm-map-dragging')).toBe(false);
        viewport.dispatchEvent(new MouseEvent('pointerup'));
    });

    test('switching maps resets the view', () => {
        setUpDom();
        loadTab();
        gmPanelMap.zoomBy(3, 0, 0);
        gmPanelMap.showMap('world', null);
        expect(gmPanelMap.view.scale).toBe(1);
        expect(image().style.transform).toBe('translate(0px, 0px) scale(1)');
        // Showing the same map again keeps the zoom.
        gmPanelMap.zoomBy(2, 0, 0);
        gmPanelMap.showMap('world', 'Navel');
        expect(gmPanelMap.view.scale).toBe(2);
    });
});
