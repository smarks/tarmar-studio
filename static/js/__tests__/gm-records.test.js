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
