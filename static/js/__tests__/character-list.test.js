/**
 * Unit tests for character-list.js: the character list page's import form,
 * filters and bulk bar, which used to be inline scripts (#338 item 19).
 */

const {
    initCharacterList,
    importCharacter,
    toggleShowDeleted,
    filterByUser,
    deleteSelectedCharacters,
    restoreSelectedCharacters
} = require('../character-list.js');
const { flush } = require('./test-helpers');

// The page's own URL, for the code that reads it; jsdom's origin is
// http://localhost, so only the path and query are set.
function setPath(pathAndQuery) {
    window.history.replaceState(null, '', pathAndQuery);
}

beforeEach(() => {
    fetch.mockReset();
    alert.mockClear();
    confirm.mockReset();
    confirm.mockReturnValue(true);
});

describe('filters', () => {
    test('showing deleted characters adds the parameter and keeps the rest', () => {
        setPath('/tarmar/characters/?user=3');
        toggleShowDeleted(true);
        expect(pageNavigation.go).toHaveBeenLastCalledWith('http://localhost/tarmar/characters/?user=3&show_deleted=1');
    });

    test('hiding them again removes only that parameter', () => {
        setPath('/tarmar/characters/?show_deleted=1&user=3');
        toggleShowDeleted(false);
        expect(pageNavigation.go).toHaveBeenLastCalledWith('http://localhost/tarmar/characters/?user=3');
    });

    test('filtering by a user sets it, and All Users clears it', () => {
        setPath('/tarmar/characters/');
        filterByUser('7');
        expect(pageNavigation.go).toHaveBeenLastCalledWith('http://localhost/tarmar/characters/?user=7');
        setPath('/tarmar/characters/?user=7');
        filterByUser('');
        expect(pageNavigation.go).toHaveBeenLastCalledWith('http://localhost/tarmar/characters/');
    });

    test('the checkbox and the select are wired by initCharacterList', () => {
        document.body.innerHTML = `
            <input type="checkbox" id="show-deleted">
            <select id="user-filter"><option value="">All</option><option value="4">bo</option></select>
        `;
        setPath('/tarmar/characters/');
        initCharacterList(document);

        const select = document.getElementById('user-filter');
        select.value = '4';
        select.dispatchEvent(new Event('change'));
        expect(pageNavigation.go).toHaveBeenLastCalledWith('http://localhost/tarmar/characters/?user=4');
    });
});

describe('the owner filter is a searchable picker (#973)', () => {
    test('a select a search swapped in still filters the list', () => {
        document.body.innerHTML = `
            <div data-picker><div data-picker-body>
                <select id="user-filter"><option value="">All</option><option value="4">bo</option></select>
            </div></div>
        `;
        setPath('/tarmar/characters/');
        initCharacterList(document);
        // What picker.js does with a search's answer: a new body, a new select.
        document.querySelector('[data-picker-body]').innerHTML =
            '<select id="user-filter"><option value="">All</option><option value="9">cy</option></select>';

        const select = document.getElementById('user-filter');
        select.value = '9';
        select.dispatchEvent(new Event('change', { bubbles: true }));
        expect(pageNavigation.go).toHaveBeenLastCalledWith('http://localhost/tarmar/characters/?user=9');
    });

    test('wiring the page twice reloads once per choice', () => {
        document.body.innerHTML = '<select id="user-filter"><option value="">All</option><option value="4">bo</option></select>';
        setPath('/tarmar/characters/');
        initCharacterList(document);
        initCharacterList(document);
        const select = document.getElementById('user-filter');
        select.value = '4';
        // reloadWithParam builds the new address once per reload.
        const built = jest.spyOn(URL.prototype, 'toString');
        select.dispatchEvent(new Event('change', { bubbles: true }));
        expect(built).toHaveBeenCalledTimes(1);
        built.mockRestore();
        expect(pageNavigation.go).toHaveBeenLastCalledWith('http://localhost/tarmar/characters/?user=4');
    });
});

describe('importing a character', () => {
    function mountImport() {
        document.body.innerHTML = `
            <button id="import-character-toggle">Import Character</button>
            <form id="import-character-form" action="/tarmar/characters/import/"
                  data-edit-url-template="/tarmar/characters/0/">
                <input type="file" name="file">
            </form>
            <span id="import-error" hidden></span>
        `;
        return {
            form: document.getElementById('import-character-form'),
            errorSpan: document.getElementById('import-error'),
        };
    }

    test('the toggle opens and closes the form', () => {
        const { form } = mountImport();
        initCharacterList(document);
        document.getElementById('import-character-toggle').click();
        expect(form.classList.contains('open')).toBe(true);
        document.getElementById('import-character-toggle').click();
        expect(form.classList.contains('open')).toBe(false);
    });

    test('a successful import opens the new character through the rendered template', async () => {
        const { form, errorSpan } = mountImport();
        fetch.mockResolvedValue({ ok: true, json: () => Promise.resolve({ success: true, character_id: 42 }) });

        await importCharacter(form, errorSpan);

        expect(fetch).toHaveBeenCalledWith(
            expect.stringContaining('/tarmar/characters/import/'),
            expect.objectContaining({ method: 'POST' })
        );
        expect(pageNavigation.go).toHaveBeenCalledWith('/tarmar/characters/42/');
        expect(errorSpan.hidden).toBe(true);
    });

    test('a refused import shows the server\'s reason in the error line', async () => {
        const { form, errorSpan } = mountImport();
        fetch.mockResolvedValue({ ok: false, json: () => Promise.resolve({ success: false, error: 'Not a Tarmar export' }) });

        await importCharacter(form, errorSpan);

        expect(errorSpan.hidden).toBe(false);
        expect(errorSpan.textContent).toBe('Not a Tarmar export');
    });

    test('a failed request says the import failed and why', async () => {
        const { form, errorSpan } = mountImport();
        fetch.mockRejectedValue(new Error('offline'));

        await importCharacter(form, errorSpan);

        expect(errorSpan.textContent).toBe('Import failed: offline');
        expect(errorSpan.hidden).toBe(false);
    });
});

describe('the bulk bar', () => {
    function mountRows() {
        document.body.innerHTML = `
            <input type="checkbox" class="character-checkbox" value="1" data-deleted="false" checked>
            <input type="checkbox" class="character-checkbox" value="2" data-deleted="true" checked>
            <input type="checkbox" class="character-checkbox" value="3" data-deleted="true" checked>
        `;
    }

    test('Restore Selected counts the rows that did not come back and keeps the page', async () => {
        mountRows();
        fetch
            .mockResolvedValueOnce({ ok: true })
            .mockResolvedValueOnce({ ok: false });

        await restoreSelectedCharacters('/tarmar/characters/0/restore/');

        expect(fetch).toHaveBeenNthCalledWith(1, '/tarmar/characters/2/restore/', expect.anything());
        expect(fetch).toHaveBeenNthCalledWith(2, '/tarmar/characters/3/restore/', expect.anything());
        expect(alert).toHaveBeenCalledWith(
            '1 of 2 character(s) could not be restored. Reload the page to see which are still deleted.'
        );
        expect(pageNavigation.reload).not.toHaveBeenCalled();
    });

    test('Restore Selected reloads when every row came back', async () => {
        mountRows();
        fetch.mockResolvedValue({ ok: true });

        await restoreSelectedCharacters('/tarmar/characters/0/restore/');

        expect(pageNavigation.reload).toHaveBeenCalled();
    });

    test('Delete Selected with no active row selected says so', async () => {
        document.body.innerHTML = '<input type="checkbox" class="character-checkbox" value="2" data-deleted="true" checked>';

        await deleteSelectedCharacters('/tarmar/characters/bulk-delete/');

        expect(alert).toHaveBeenCalledWith('No active characters selected');
        expect(fetch).not.toHaveBeenCalled();
    });

    test('Delete Selected posts the active ids and reloads on success', async () => {
        mountRows();
        fetch.mockResolvedValue({ json: () => Promise.resolve({ success: true, deleted: 1 }) });

        await deleteSelectedCharacters('/tarmar/characters/bulk-delete/');

        expect(fetch).toHaveBeenCalledWith(
            '/tarmar/characters/bulk-delete/',
            expect.objectContaining({ body: JSON.stringify({ ids: [1] }) })
        );
        expect(pageNavigation.reload).toHaveBeenCalled();
    });

    test('initCharacterList wires the bulk buttons from the bar\'s data attributes', async () => {
        document.body.innerHTML = `
            <div id="bulk-actions" data-bulk-delete-url="/bulk/" data-restore-url-template="/tarmar/characters/0/restore/">
                <span id="selected-count">0</span>
                <button id="delete-selected-btn" disabled></button>
                <button id="restore-selected-btn" disabled></button>
            </div>
            <input type="checkbox" id="select-all">
            <input type="checkbox" class="character-checkbox" value="5" data-deleted="true">
        `;
        fetch.mockResolvedValue({ ok: true });
        initCharacterList(document);

        const box = document.querySelector('.character-checkbox');
        box.checked = true;
        box.dispatchEvent(new Event('change'));
        expect(document.getElementById('restore-selected-btn').disabled).toBe(false);

        document.getElementById('restore-selected-btn').click();
        await flush();
        await flush();

        expect(fetch).toHaveBeenCalledWith('/tarmar/characters/5/restore/', expect.anything());
    });
});
