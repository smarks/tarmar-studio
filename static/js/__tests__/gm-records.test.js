/**
 * Unit tests for gm-records.js: the Game Master's hand edits of a session's
 * records (issues #789-#791). The server decides who may and what changes;
 * these check what the page sends, that it shows the record as the server
 * holds it afterwards, and that a refusal stays in the tab's error line.
 */

const { resolvedJsonResponse: jsonResponse, flush } = require('./test-helpers');

let records;

function setUpDom() {
    document.body.innerHTML = `
        <section id="gm-panel-campaign">
            <p id="gm-panel-campaign-error" hidden></p>
            <form class="gm-hand-form" id="edit-person" data-url="/tarmar/gm/sessions/3/entities/" data-change="save">
                <input type="hidden" name="kind" value="person">
                <input type="hidden" name="entity_id" value="9">
                <input name="name" value="Brannoc">
                <input name="where" value="">
                <button type="submit">Save</button>
            </form>
            <button type="button" class="gm-hand-action" id="reopen" data-url="/tarmar/gm/sessions/3/threads/" data-change="reopen" data-thread-id="4">Reopen</button>
            <p id="gm-panel-campaign-status" hidden></p>
        </section>
        <section id="gm-fold-recap">
            <div id="gm-fold-recap-text">Old recap.</div>
            <p id="gm-fold-recap-status" hidden></p>
            <form class="gm-hand-form" id="gm-fold-recap-form" data-url="/tarmar/gm/sessions/3/recap/" data-change="edit">
                <textarea name="text">The party  went
down.</textarea>
                <button type="submit">Save</button>
            </form>
            <p id="gm-fold-recap-error" hidden></p>
        </section>
    `;
}

beforeEach(() => {
    jest.resetModules();
    global.fetch.mockReset();
    setUpDom();
    window.gmPanelCharacters = { refreshPanel: jest.fn(() => Promise.resolve(true)) };
    records = require('../gm-records.js');
});

afterEach(() => {
    delete window.gmPanelCharacters;
});

test('a form sends every named field and the change, then refetches the tab', async () => {
    global.fetch.mockReturnValue(jsonResponse({ ok: true, message: 'Corrected person 9', set_by: 'Set by hand by gm: person 9 corrected' }));

    await expect(records.submitForm(document.getElementById('edit-person'))).resolves.toBe(true);

    expect(global.fetch.mock.calls[0][0]).toBe('/tarmar/gm/sessions/3/entities/');
    expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({
        kind: 'person', entity_id: '9', name: 'Brannoc', where: '', change: 'save',
    });
    expect(window.gmPanelCharacters.refreshPanel).toHaveBeenCalled();
    const status = document.getElementById('gm-panel-campaign-status');
    expect(status.hidden).toBe(false);
    expect(status.textContent).toContain('Set by hand by gm');
});

test('a button sends its record id under the key the endpoint reads', async () => {
    global.fetch.mockReturnValue(jsonResponse({ ok: true, message: 'Reopened thread 4', set_by: '' }));

    await records.pressButton(document.getElementById('reopen'));

    expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({ thread_id: '4', change: 'reopen' });
});

test('a recap refused while a turn runs says so and stays', async () => {
    global.fetch.mockReturnValue(jsonResponse(
        { error: 'The Game Master is still answering a message in this session' },
        { ok: false, status: 400, statusText: 'Bad Request' },
    ));

    await expect(records.submitForm(document.getElementById('gm-fold-recap-form'))).resolves.toBe(false);

    const error = document.getElementById('gm-fold-recap-error');
    expect(error.hidden).toBe(false);
    expect(error.textContent).toContain('still answering');
    expect(document.getElementById('gm-fold-recap-text').textContent).toBe('Old recap.');
});

test('a refusal stays in the error line and nothing is refetched', async () => {
    global.fetch.mockReturnValue(jsonResponse(
        { error: 'Only a Game Master sets the records at this table by hand' },
        { ok: false, status: 403, statusText: 'Forbidden' },
    ));

    await expect(records.submitForm(document.getElementById('edit-person'))).resolves.toBe(false);

    const error = document.getElementById('gm-panel-campaign-error');
    expect(error.hidden).toBe(false);
    expect(error.textContent).toContain('Only a Game Master');
    expect(window.gmPanelCharacters.refreshPanel).not.toHaveBeenCalled();
});

test('a corrected recap replaces the one shown, as the server stores it', async () => {
    global.fetch.mockReturnValue(jsonResponse({
        ok: true,
        message: 'Recap corrected',
        set_by: 'Set by hand by gm: recap corrected',
        after: { summary: 'The party  went\ndown.', summary_covers: 4 },
    }));

    await records.submitForm(document.getElementById('gm-fold-recap-form'));

    // Its lines kept, as saved (review of #791).
    expect(document.getElementById('gm-fold-recap-text').textContent).toBe('The party  went\ndown.');
    expect(document.getElementById('gm-fold-recap-status').textContent).toBe('Set by hand by gm: recap corrected');
});

test('submitting a form through the page is delegated', async () => {
    records.init();
    global.fetch.mockReturnValue(jsonResponse({ ok: true, message: 'x', set_by: '' }));

    document.getElementById('edit-person').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await flush();

    expect(global.fetch).toHaveBeenCalled();
});

describe('one press sends one request (#142)', () => {
    const RULINGS_URL = '/tarmar/gm/sessions/3/rulings/';

    /** The Campaign tab's "Add a ruling" form, as the tab renders it. */
    function addRulingForm() {
        return `<form class="gm-hand-form" data-url="${RULINGS_URL}" data-change="add" data-draft-key="add-ruling">
            <input name="topic" value="Falling">
            <button type="submit">Add a ruling</button>
        </form>`;
    }

    /** A ruling's Retire button, as the tab renders it. */
    function retireButton() {
        return `<button type="button" class="gm-hand-action" data-url="${RULINGS_URL}" data-change="retire" data-ruling-id="5" data-focus-key="retire-ruling-5">Retire</button>`;
    }

    function campaignTab() {
        return document.getElementById('gm-panel-campaign');
    }

    /** Answer the request on its way, and let what follows it run. */
    function heldAnswer() {
        let answer;
        global.fetch.mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }));
        return async () => {
            answer({ ok: true, status: 200, json: () => Promise.resolve({ ok: true, message: 'Added', set_by: '' }) });
            await flush();
        };
    }

    function sentTo(url) {
        return global.fetch.mock.calls.filter(([called]) => called === url).length;
    }

    test('a second submit while the first is out sends nothing, and the button is held', async () => {
        campaignTab().insertAdjacentHTML('beforeend', addRulingForm());
        const form = campaignTab().querySelector('[data-draft-key="add-ruling"]');
        const release = heldAnswer();

        const first = records.submitForm(form);
        expect(form.querySelector('button').disabled).toBe(true);
        await expect(records.submitForm(form)).resolves.toBe(false);
        expect(sentTo(RULINGS_URL)).toBe(1);

        await release();
        await expect(first).resolves.toBe(true);
        expect(form.querySelector('button').disabled).toBe(false);
    });

    test('the form a refetch put in its place is held too, and sends nothing', async () => {
        campaignTab().insertAdjacentHTML('beforeend', addRulingForm());
        const form = campaignTab().querySelector('[data-draft-key="add-ruling"]');
        const release = heldAnswer();

        const first = records.submitForm(form);
        // The tab is fetched again while the ruling is out: a fresh form
        // under the same draft key, painted as gm-panel-characters.js does.
        form.remove();
        campaignTab().insertAdjacentHTML('beforeend', addRulingForm());
        const fresh = campaignTab().querySelector('[data-draft-key="add-ruling"]');
        records.paintHeld(campaignTab());
        expect(fresh.querySelector('button').disabled).toBe(true);
        await expect(records.submitForm(fresh)).resolves.toBe(false);
        expect(sentTo(RULINGS_URL)).toBe(1);

        await release();
        await first;
        expect(fresh.querySelector('button').disabled).toBe(false);
    });

    test('a second press of a record button while the first is out sends nothing', async () => {
        campaignTab().insertAdjacentHTML('beforeend', retireButton());
        const button = campaignTab().querySelector('[data-focus-key="retire-ruling-5"]');
        const release = heldAnswer();

        const first = records.pressButton(button);
        expect(button.disabled).toBe(true);
        await expect(records.pressButton(button)).resolves.toBe(false);
        expect(sentTo(RULINGS_URL)).toBe(1);

        await release();
        await expect(first).resolves.toBe(true);
        expect(button.disabled).toBe(false);
    });

    test('a double click on the page sends one request', async () => {
        records.init();
        campaignTab().insertAdjacentHTML('beforeend', retireButton());
        const button = campaignTab().querySelector('[data-focus-key="retire-ruling-5"]');
        const release = heldAnswer();

        // A disabled button takes no click in a browser; dispatching the
        // event reaches the handler anyway, so the key is what is tested.
        button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        await flush();
        expect(sentTo(RULINGS_URL)).toBe(1);
        await release();
    });

    test('another record is not held by this one', async () => {
        campaignTab().insertAdjacentHTML('beforeend', addRulingForm() + retireButton());
        const release = heldAnswer();
        const first = records.submitForm(campaignTab().querySelector('[data-draft-key="add-ruling"]'));
        const button = campaignTab().querySelector('[data-focus-key="retire-ruling-5"]');

        expect(button.disabled).toBe(false);
        global.fetch.mockReturnValueOnce(jsonResponse({ ok: true, message: 'Retired', set_by: '' }));
        await expect(records.pressButton(button)).resolves.toBe(true);
        expect(sentTo(RULINGS_URL)).toBe(2);
        await release();
        await first;
    });

    test('a refused change frees the control for another press', async () => {
        campaignTab().insertAdjacentHTML('beforeend', addRulingForm());
        const form = campaignTab().querySelector('[data-draft-key="add-ruling"]');
        global.fetch.mockReturnValue(jsonResponse(
            { error: 'A ruling needs a topic' }, { ok: false, status: 400, statusText: 'Bad Request' },
        ));

        await expect(records.submitForm(form)).resolves.toBe(false);
        expect(form.querySelector('button').disabled).toBe(false);
        await records.submitForm(form);
        expect(sentTo(RULINGS_URL)).toBe(2);
    });

    test('a removal the Game Master declines frees the button', async () => {
        campaignTab().insertAdjacentHTML('beforeend', '<button type="button" class="gm-hand-action" '
            + 'data-url="/tarmar/gm/sessions/3/entities/" data-change="remove" data-entity-id="9" '
            + 'data-focus-key="remove-person-9" data-confirm="Remove Brannoc?">Remove</button>');
        const button = campaignTab().querySelector('[data-focus-key="remove-person-9"]');
        window.uiConfirm = jest.fn(() => Promise.resolve(false));
        try {
            await expect(records.pressButton(button)).resolves.toBe(false);
        } finally {
            delete window.uiConfirm;
        }
        expect(global.fetch).not.toHaveBeenCalled();
        expect(button.disabled).toBe(false);
    });

    test('a control that names no record is refused before anything is sent', async () => {
        // Every such control would share one key and hold all the others.
        campaignTab().insertAdjacentHTML('beforeend',
            `<button type="button" class="gm-hand-action" data-url="${RULINGS_URL}" data-change="retire">Retire</button>`);
        const button = campaignTab().querySelector('.gm-hand-action:not([id])');

        await expect(records.pressButton(button)).rejects.toThrow('names no record');
        expect(global.fetch).not.toHaveBeenCalled();
    });

    test('a button the server rendered disabled stays so once the request ends', async () => {
        campaignTab().insertAdjacentHTML('beforeend', addRulingForm());
        const form = campaignTab().querySelector('[data-draft-key="add-ruling"]');
        form.querySelector('button').disabled = true;
        global.fetch.mockReturnValueOnce(jsonResponse({ ok: true, message: 'Added', set_by: '' }));

        await records.submitForm(form);
        expect(form.querySelector('button').disabled).toBe(true);
    });
});
