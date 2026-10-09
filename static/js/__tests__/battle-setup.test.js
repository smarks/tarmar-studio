/**
 * Battle setup page JS (#270/#271): the terms autosave wiring and the
 * saved-character filter. The page's real state lives on the server-side
 * draft; these test the two client conveniences layered on top.
 */

function loadSetupModule() {
    let moduleExports;
    jest.isolateModules(() => {
        moduleExports = require('../battle-setup.js');
    });
    return moduleExports;
}

describe('filterCharacters', () => {
    beforeEach(() => {
        document.body.innerHTML = `
            <ul id="setup-character-list">
                <li data-filter-text="morwen dmuser"></li>
                <li data-filter-text="aldwin playeruser"></li>
                <li data-filter-text="aldwyn playeruser"></li>
            </ul>
        `;
    });

    test('an empty query shows every row', () => {
        const { filterCharacters } = loadSetupModule();
        const list = document.getElementById('setup-character-list');
        expect(filterCharacters(list, '')).toBe(3);
        expect(list.querySelectorAll('[hidden]')).toHaveLength(0);
    });

    test('matches on name, case-insensitively', () => {
        const { filterCharacters } = loadSetupModule();
        const list = document.getElementById('setup-character-list');
        expect(filterCharacters(list, '  ALD ')).toBe(2);
        const rows = list.querySelectorAll('li');
        expect(rows[0].hidden).toBe(true);
        expect(rows[1].hidden).toBe(false);
        expect(rows[2].hidden).toBe(false);
    });

    test('matches on the owning player too', () => {
        const { filterCharacters } = loadSetupModule();
        const list = document.getElementById('setup-character-list');
        expect(filterCharacters(list, 'dmuser')).toBe(1);
    });

    test('a cleared query brings hidden rows back', () => {
        const { filterCharacters } = loadSetupModule();
        const list = document.getElementById('setup-character-list');
        filterCharacters(list, 'morwen');
        expect(filterCharacters(list, '')).toBe(3);
        expect(list.querySelectorAll('[hidden]')).toHaveLength(0);
    });
});

describe('initBattleSetup', () => {
    beforeEach(() => {
        fetch.mockClear();
        fetch.mockResolvedValue({
            ok: true,
            json: () => Promise.resolve({ success: true }),
        });
        document.body.innerHTML = `
            <p id="setup-save-note" data-state="idle"
               data-update-url="/tarmar/battles/setup/7/update/"
               data-csrf="test-token"></p>
            <label data-field="name">
                <input class="autosave" type="text" value="Old name">
            </label>
            <label data-field="auto_start">
                <input class="autosave" type="checkbox">
            </label>
            <input id="character-filter" type="search" data-filter-list="setup-character-list">
            <ul id="setup-character-list">
                <li data-filter-text="morwen dmuser"></li>
                <li data-filter-text="aldwin playeruser"></li>
            </ul>
        `;
    });

    test('a terms field saves to the draft endpoint on blur', () => {
        loadSetupModule();
        const input = document.querySelector('[data-field="name"] input');
        input.value = 'Pit Fight';
        input.dispatchEvent(new Event('blur'));
        expect(fetch).toHaveBeenCalledWith(
            '/tarmar/battles/setup/7/update/',
            expect.objectContaining({
                method: 'POST',
                headers: expect.objectContaining({ 'X-CSRFToken': 'test-token' }),
                body: JSON.stringify({ field: 'name', value: 'Pit Fight' }),
            })
        );
    });

    test('the auto-start checkbox saves on change', () => {
        loadSetupModule();
        const checkbox = document.querySelector('[data-field="auto_start"] input');
        checkbox.checked = true;
        checkbox.dispatchEvent(new Event('change'));
        expect(fetch).toHaveBeenCalledWith(
            '/tarmar/battles/setup/7/update/',
            expect.objectContaining({
                body: JSON.stringify({ field: 'auto_start', value: true }),
            })
        );
    });

    test('a failed save renders a persistent inline error', async () => {
        fetch.mockResolvedValue({
            ok: false,
            json: () => Promise.resolve({ error: 'Seed must be a whole number' }),
        });
        loadSetupModule();
        const container = document.querySelector('[data-field="name"]');
        const input = container.querySelector('input');
        input.value = 'Rejected';
        input.dispatchEvent(new Event('blur'));
        await new Promise((resolve) => { setTimeout(resolve, 0); });
        expect(container.classList.contains('error')).toBe(true);
        expect(container.querySelector('.autosave-error').textContent).toBe(
            'Seed must be a whole number'
        );
    });

    // The page wires this input declaratively, through roster-filter.js's
    // data-filter-list, the same way the parties page does (#338 item 16);
    // battle-setup.js no longer adds a listener of its own.
    test('typing in the filter narrows the shelf', () => {
        loadSetupModule();
        const filter = document.getElementById('character-filter');
        filter.value = 'aldwin';
        filter.dispatchEvent(new Event('input'));
        const rows = document.querySelectorAll('#setup-character-list li');
        expect(rows[0].hidden).toBe(true);
        expect(rows[1].hidden).toBe(false);
    });

    test('does nothing on pages without the setup markup', () => {
        document.body.innerHTML = '<p>another page</p>';
        expect(() => loadSetupModule()).not.toThrow();
        expect(fetch).not.toHaveBeenCalled();
    });
});

describe('guardSingleStart (issue #832)', () => {
    function startForm(disabled) {
        document.body.innerHTML = `
            <form id="setup-start-form" method="post" action="/tarmar/battles/setup/7/start/">
                <button type="submit" class="setup-begin"${disabled ? ' disabled' : ''}>
                    Begin the battle
                </button>
            </form>
        `;
        return document.getElementById('setup-start-form');
    }

    function submit(form) {
        const event = new Event('submit', { cancelable: true });
        form.dispatchEvent(event);
        return event;
    }

    function pageshow(persisted) {
        const event = new Event('pageshow');
        event.persisted = persisted;
        window.dispatchEvent(event);
    }

    test('the first press goes through and disables the button', () => {
        const form = startForm(false);
        loadSetupModule();
        const first = submit(form);
        expect(first.defaultPrevented).toBe(false);
        expect(form.querySelector('button').disabled).toBe(true);
    });

    test('a second press while the first is in flight is not sent', () => {
        const form = startForm(false);
        loadSetupModule();
        submit(form);
        const second = submit(form);
        expect(second.defaultPrevented).toBe(true);
    });

    test('a page restored from the back-forward cache can be pressed again', () => {
        const form = startForm(false);
        loadSetupModule();
        submit(form);
        pageshow(true);
        expect(form.querySelector('button').disabled).toBe(false);
        expect(submit(form).defaultPrevented).toBe(false);
    });

    test('a fresh page load leaves an in-flight press alone', () => {
        const form = startForm(false);
        loadSetupModule();
        submit(form);
        pageshow(false);
        expect(form.querySelector('button').disabled).toBe(true);
    });

    test('a stopped submission can be pressed again', () => {
        const navigation = new EventTarget();
        window.navigation = navigation;
        try {
            const form = startForm(false);
            loadSetupModule();
            submit(form);
            navigation.dispatchEvent(new Event('navigateerror'));
            expect(form.querySelector('button').disabled).toBe(false);
            expect(submit(form).defaultPrevented).toBe(false);
        } finally {
            delete window.navigation;
        }
    });

    describe('without the Navigation API', () => {
        beforeEach(() => {
            delete window.navigation;
            jest.useFakeTimers();
        });
        afterEach(() => {
            jest.useRealTimers();
        });

        test('a second press inside the re-arm window is not sent', () => {
            const form = startForm(false);
            const { START_REARM_MS } = loadSetupModule();
            submit(form);
            jest.advanceTimersByTime(START_REARM_MS - 1);
            expect(form.querySelector('button').disabled).toBe(true);
            expect(submit(form).defaultPrevented).toBe(true);
        });

        test('the button can be pressed again after the re-arm window', () => {
            const form = startForm(false);
            const { START_REARM_MS } = loadSetupModule();
            submit(form);
            jest.advanceTimersByTime(START_REARM_MS);
            expect(form.querySelector('button').disabled).toBe(false);
            expect(submit(form).defaultPrevented).toBe(false);
        });

        test('Esc, which stops the navigation, re-arms the button at once', () => {
            const form = startForm(false);
            loadSetupModule();
            submit(form);
            document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
            expect(form.querySelector('button').disabled).toBe(false);
            expect(submit(form).defaultPrevented).toBe(false);
        });

        test('another key leaves the press held', () => {
            const form = startForm(false);
            loadSetupModule();
            submit(form);
            document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
            expect(submit(form).defaultPrevented).toBe(true);
        });
    });

    test('a button the server rendered disabled stays disabled on restore', () => {
        const form = startForm(true);
        loadSetupModule();
        submit(form);
        pageshow(true);
        expect(form.querySelector('button').disabled).toBe(true);
    });
});
