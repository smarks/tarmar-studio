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

function setHref(href) {
    window.location.href = href;
}

beforeEach(() => {
    fetch.mockReset();
    alert.mockClear();
    confirm.mockReset();
    confirm.mockReturnValue(true);
    window.location.reload.mockClear();
});

describe('filters', () => {
    test('showing deleted characters adds the parameter and keeps the rest', () => {
        setHref('http://testserver/tarmar/characters/?user=3');
        toggleShowDeleted(true);
        expect(window.location.href).toBe('http://testserver/tarmar/characters/?user=3&show_deleted=1');
    });

    test('hiding them again removes only that parameter', () => {
        setHref('http://testserver/tarmar/characters/?show_deleted=1&user=3');
        toggleShowDeleted(false);
        expect(window.location.href).toBe('http://testserver/tarmar/characters/?user=3');
    });

    test('filtering by a user sets it, and All Users clears it', () => {
        setHref('http://testserver/tarmar/characters/');
        filterByUser('7');
        expect(window.location.href).toBe('http://testserver/tarmar/characters/?user=7');
        filterByUser('');
        expect(window.location.href).toBe('http://testserver/tarmar/characters/');
    });

    test('the checkbox and the select are wired by initCharacterList', () => {
        document.body.innerHTML = `
            <input type="checkbox" id="show-deleted">
            <select id="user-filter"><option value="">All</option><option value="4">bo</option></select>
        `;
        setHref('http://testserver/tarmar/characters/');
        initCharacterList(document);

        const select = document.getElementById('user-filter');
        select.value = '4';
        select.dispatchEvent(new Event('change'));
        expect(window.location.href).toBe('http://testserver/tarmar/characters/?user=4');
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
        expect(window.location.href).toBe('/tarmar/characters/42/');
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
        expect(window.location.reload).not.toHaveBeenCalled();
    });

    test('Restore Selected reloads when every row came back', async () => {
        mountRows();
        fetch.mockResolvedValue({ ok: true });

        await restoreSelectedCharacters('/tarmar/characters/0/restore/');

        expect(window.location.reload).toHaveBeenCalled();
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
        expect(window.location.reload).toHaveBeenCalled();
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
