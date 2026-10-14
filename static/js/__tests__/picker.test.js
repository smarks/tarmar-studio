/**
 * Searchable pickers (picker.js, issue #864): typing asks the server for the
 * rows that match, the pager asks it for another page, and the answer
 * replaces the picker's body. A failed search says so and stays.
 */

function loadPicker() {
    let moduleExports;
    jest.isolateModules(() => {
        moduleExports = require('../picker.js');
    });
    return moduleExports;
}

/** A picker as templates/_picker.html renders it, inside a form. */
function buildPicker() {
    document.body.innerHTML = `
        <form id="host-form">
            <div class="picker" id="a-picker" data-picker
                 data-picker-url="/tarmar/things/picker/?session=4">
                <input type="search" data-picker-search>
                <div class="picker-body" data-picker-body>
                    <ul>
                        <li data-filter-text="morwen dmuser">Morwen</li>
                        <li data-filter-text="aldwin player">Aldwin</li>
                    </ul>
                    <p data-picker-status>2 characters, newest first</p>
                    <nav class="pagination">
                        <button type="button" data-picker-page="" disabled>Newer</button>
                        <span class="page-current">Page 1 of 2</span>
                        <button type="button" data-picker-page="2">Older</button>
                    </nav>
                </div>
            </div>
        </form>
    `;
    return document.getElementById('a-picker');
}

/** A fetch that answers each call with the next body in ``bodies``. */
function answering(...bodies) {
    const calls = [];
    global.fetch = jest.fn((url) => {
        calls.push(new URL(url));
        const body = bodies.shift();
        if (body instanceof Error) return Promise.reject(body);
        return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(body) });
    });
    return calls;
}

/** Let the queued promise callbacks run. */
async function settle() {
    for (let turn = 0; turn < 5; turn += 1) {
        // eslint-disable-next-line no-await-in-loop
        await Promise.resolve();
    }
}

beforeEach(() => {
    jest.useFakeTimers();
});

afterEach(() => {
    jest.useRealTimers();
    delete global.fetch;
});

test('typing asks the endpoint for the first page matching it, keeping its own query', async () => {
    const picker = buildPicker();
    const calls = answering('<ul><li>Wren</li></ul><p data-picker-status>1 character</p>');
    const { SEARCH_DELAY_MS } = loadPicker();
    const input = picker.querySelector('[data-picker-search]');

    input.value = ' wren ';
    input.dispatchEvent(new Event('input'));
    expect(global.fetch).not.toHaveBeenCalled();
    jest.advanceTimersByTime(SEARCH_DELAY_MS);
    await settle();

    expect(calls).toHaveLength(1);
    expect(calls[0].pathname).toBe('/tarmar/things/picker/');
    expect(calls[0].searchParams.get('q')).toBe('wren');
    expect(calls[0].searchParams.get('page')).toBe('1');
    expect(calls[0].searchParams.get('session')).toBe('4');
    expect(picker.querySelector('[data-picker-body]').textContent).toContain('Wren');
});

test('the rows on screen narrow at once while the server is asked', () => {
    const picker = buildPicker();
    answering('<ul></ul>');
    global.rosterFilter = require('../roster-filter.js');
    loadPicker();
    const input = picker.querySelector('[data-picker-search]');

    input.value = 'ald';
    input.dispatchEvent(new Event('input'));

    const rows = picker.querySelectorAll('li');
    expect(rows[0].hidden).toBe(true);
    expect(rows[1].hidden).toBe(false);
    delete global.rosterFilter;
});

test('Enter searches at once and does not submit the form the picker sits in', async () => {
    const picker = buildPicker();
    const calls = answering('<p data-picker-status>none</p>');
    loadPicker();
    const submitted = jest.fn((event) => event.preventDefault());
    document.getElementById('host-form').addEventListener('submit', submitted);
    const input = picker.querySelector('[data-picker-search]');
    input.value = 'kell';

    const enter = new KeyboardEvent('keydown', { key: 'Enter', cancelable: true });
    input.dispatchEvent(enter);
    await settle();

    expect(enter.defaultPrevented).toBe(true);
    expect(calls).toHaveLength(1);
    expect(calls[0].searchParams.get('q')).toBe('kell');
    expect(submitted).not.toHaveBeenCalled();
});

test('the pager fetches the page it names and keeps the focus on the pager', async () => {
    const picker = buildPicker();
    const calls = answering(`
        <p data-picker-status>2 characters</p>
        <nav class="pagination">
            <button type="button" data-picker-page="1">Newer</button>
            <span class="page-current">Page 2 of 2</span>
            <button type="button" data-picker-page="" disabled>Older</button>
        </nav>`);
    loadPicker();

    picker.querySelectorAll('[data-picker-page]')[1].click();
    await settle();

    expect(calls[0].searchParams.get('page')).toBe('2');
    expect(picker.querySelector('.page-current').textContent).toBe('Page 2 of 2');
    // "Older" is disabled on the last page, so the focus moves to "Newer".
    expect(document.activeElement.textContent).toBe('Newer');
});

test('after a page loads, a click on a row is the row\'s, not the pager\'s (issue #16)', async () => {
    // The picker notes the page it shows on itself; the pager's click
    // handler finds its buttons by data-picker-page, so that note must not
    // be one -- or every click inside the picker reads as a page turn.
    const picker = buildPicker();
    const calls = answering(`
        <ul><li><button type="button" class="row-add">Add Morwen</button></li></ul>
        <nav class="pagination">
            <button type="button" data-picker-page="1">Newer</button>
            <button type="button" data-picker-page="" disabled>Older</button>
        </nav>`);
    loadPicker();
    picker.querySelectorAll('[data-picker-page]')[1].click();
    await settle();
    expect(calls).toHaveLength(1);

    const click = new MouseEvent('click', { bubbles: true, cancelable: true });
    picker.querySelector('.row-add').dispatchEvent(click);
    await settle();

    expect(click.defaultPrevented).toBe(false);
    expect(calls).toHaveLength(1);
});

test('a disabled pager button asks for nothing', () => {
    const picker = buildPicker();
    answering();
    loadPicker();

    picker.querySelectorAll('[data-picker-page]')[0].click();

    expect(global.fetch).not.toHaveBeenCalled();
});

test('only the latest search paints: a slow earlier answer is dropped', async () => {
    const picker = buildPicker();
    let releaseFirst;
    global.fetch = jest.fn()
        .mockImplementationOnce(() => new Promise((resolve) => {
            releaseFirst = () => resolve({ ok: true, status: 200, text: () => Promise.resolve('<p>first</p>') });
        }))
        .mockImplementationOnce(() => Promise.resolve(
            { ok: true, status: 200, text: () => Promise.resolve('<p>second</p>') },
        ));
    const { loadPage } = loadPicker();

    const first = loadPage(picker, 1);
    await loadPage(picker, 1);
    releaseFirst();
    await first;

    expect(picker.querySelector('[data-picker-body]').textContent).toBe('second');
});

test('a failed search says so in the status line and leaves the rows', async () => {
    const picker = buildPicker();
    global.fetch = jest.fn(() => Promise.resolve({ ok: false, status: 500 }));
    const { loadPage } = loadPicker();

    await loadPage(picker, 1);

    expect(picker.querySelector('[data-picker-status]').textContent)
        .toContain('did not reach the server (the server answered 500)');
    expect(picker.querySelectorAll('li')).toHaveLength(2);
});

test('a network failure is reported the same way', async () => {
    const picker = buildPicker();
    answering(new Error('offline'));
    const { loadPage } = loadPicker();

    await loadPage(picker, 1);

    expect(picker.querySelector('[data-picker-status]').textContent).toContain('(offline)');
});

test('initPickers wires markup swapped in later, once', async () => {
    document.body.innerHTML = '<div id="slot"></div>';
    const { initPickers } = loadPicker();
    const slot = document.getElementById('slot');
    slot.innerHTML = `
        <div data-picker data-picker-url="/x/">
            <input data-picker-search><div data-picker-body></div>
        </div>`;
    const calls = answering('<p>found</p>');

    initPickers(slot);
    initPickers(slot);
    const input = slot.querySelector('[data-picker-search]');
    input.value = 'a';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', cancelable: true }));
    await settle();

    expect(calls).toHaveLength(1);
    expect(slot.textContent).toContain('found');
});
