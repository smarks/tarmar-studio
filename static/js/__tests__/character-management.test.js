/**
 * Unit tests for character-management.js
 */

const {
    getSelectedIds,
    setupSelectAll,
    bulkDeleteCharacters,
    deleteCharacter,
    restoreCharacter,
    bindCharacterRowActions
} = require('../character-management.js');
const { flush } = require('./test-helpers');

// postWithCsrf moved to api.js (issue #111); its own tests live in api.test.js.

describe('deleteCharacter / restoreCharacter URLs', () => {
    beforeEach(() => {
        fetch.mockClear();
        confirm.mockClear();
        confirm.mockReturnValue(true);
    });

    test('deleteCharacter posts to the URL the caller provides', async () => {
        fetch.mockResolvedValue({ ok: true });
        await deleteCharacter(123, '/tarmar/characters/123/delete/', 'Aldwin', 'token', false);
        expect(fetch).toHaveBeenCalledWith(
            '/tarmar/characters/123/delete/',
            expect.objectContaining({ method: 'POST' })
        );
    });

    test('a refused delete says why and leaves the row (#640)', async () => {
        document.body.innerHTML = '<table><tbody><tr data-id="123"></tr></tbody></table>';
        window.location.reload.mockClear();
        fetch.mockResolvedValue({
            ok: false, redirected: false, status: 403,
            json: () => Promise.resolve({ error: 'Permission denied' }),
        });

        await deleteCharacter(123, '/tarmar/characters/123/delete/', 'Aldwin', 'token', false);

        expect(alert).toHaveBeenCalledWith('Permission denied');
        expect(document.querySelector('tr[data-id="123"]')).not.toBeNull();
        expect(window.location.reload).not.toHaveBeenCalled();
    });

    test('a rate-limited delete with no readable body still says something', async () => {
        document.body.innerHTML = '<table><tbody><tr data-id="123"></tr></tbody></table>';
        fetch.mockResolvedValue({
            ok: false, redirected: false, status: 429,
            json: () => Promise.reject(new SyntaxError('not JSON')),
        });

        await deleteCharacter(123, '/tarmar/characters/123/delete/', 'Aldwin', 'token', false);

        expect(alert).toHaveBeenCalledWith('Could not delete that character');
        expect(document.querySelector('tr[data-id="123"]')).not.toBeNull();
    });

    test('restoreCharacter posts to the URL the caller provides', async () => {
        fetch.mockResolvedValue({ json: () => Promise.resolve({ success: true }) });
        await restoreCharacter(123, '/tarmar/characters/123/restore/', 'token', false);
        expect(fetch).toHaveBeenCalledWith(
            '/tarmar/characters/123/restore/',
            expect.objectContaining({ method: 'POST' })
        );
    });
});

describe('getSelectedIds', () => {
    beforeEach(() => {
        document.body.innerHTML = `
            <input type="checkbox" class="char-cb" value="1" checked data-deleted="false">
            <input type="checkbox" class="char-cb" value="2" data-deleted="false">
            <input type="checkbox" class="char-cb" value="3" checked data-deleted="true">
            <input type="checkbox" class="char-cb" value="4" checked data-deleted="false">
        `;
    });

    test('returns all checked checkbox values as integers', () => {
        const ids = getSelectedIds('char-cb');
        expect(ids).toEqual([1, 3, 4]);
    });

    test('filters by data attribute when specified', () => {
        const activeIds = getSelectedIds('char-cb', 'deleted', 'false');
        expect(activeIds).toEqual([1, 4]);
    });

    test('filters deleted items when specified', () => {
        const deletedIds = getSelectedIds('char-cb', 'deleted', 'true');
        expect(deletedIds).toEqual([3]);
    });

    test('returns empty array when no checkboxes checked', () => {
        document.body.innerHTML = `
            <input type="checkbox" class="char-cb" value="1" data-deleted="false">
            <input type="checkbox" class="char-cb" value="2" data-deleted="false">
        `;
        const ids = getSelectedIds('char-cb');
        expect(ids).toEqual([]);
    });
});

describe('setupSelectAll', () => {
    beforeEach(() => {
        document.body.innerHTML = `
            <input type="checkbox" id="select-all">
            <div id="bulk-actions">
                <span id="count">0</span>
                <button id="delete-btn" disabled>Delete</button>
            </div>
            <input type="checkbox" class="char-cb" value="1">
            <input type="checkbox" class="char-cb" value="2">
            <input type="checkbox" class="char-cb" value="3">
        `;
    });

    test('updates count when checkbox is selected', () => {
        setupSelectAll({
            selectAllId: 'select-all',
            checkboxClass: 'char-cb',
            bulkActionsId: 'bulk-actions',
            countId: 'count',
            disableButtonId: 'delete-btn'
        });

        const checkbox = document.querySelector('.char-cb');
        checkbox.checked = true;
        checkbox.dispatchEvent(new Event('change'));

        expect(document.getElementById('count').textContent).toBe('1');
    });

    test('enables button when checkbox selected', () => {
        setupSelectAll({
            selectAllId: 'select-all',
            checkboxClass: 'char-cb',
            bulkActionsId: 'bulk-actions',
            countId: 'count',
            disableButtonId: 'delete-btn'
        });

        const deleteBtn = document.getElementById('delete-btn');
        expect(deleteBtn.disabled).toBe(true);

        const checkbox = document.querySelector('.char-cb');
        checkbox.checked = true;
        checkbox.dispatchEvent(new Event('change'));

        expect(deleteBtn.disabled).toBe(false);
    });

    test('select-all checks all checkboxes', () => {
        setupSelectAll({
            selectAllId: 'select-all',
            checkboxClass: 'char-cb',
            bulkActionsId: 'bulk-actions',
            countId: 'count',
            disableButtonId: 'delete-btn'
        });

        const selectAll = document.getElementById('select-all');
        selectAll.checked = true;
        selectAll.dispatchEvent(new Event('change'));

        const checkboxes = document.querySelectorAll('.char-cb');
        checkboxes.forEach(cb => {
            expect(cb.checked).toBe(true);
        });
        expect(document.getElementById('count').textContent).toBe('3');
    });

    test('disables button when all unchecked', () => {
        setupSelectAll({
            selectAllId: 'select-all',
            checkboxClass: 'char-cb',
            bulkActionsId: 'bulk-actions',
            countId: 'count',
            disableButtonId: 'delete-btn'
        });

        // Check one
        const checkbox = document.querySelector('.char-cb');
        checkbox.checked = true;
        checkbox.dispatchEvent(new Event('change'));
        expect(document.getElementById('delete-btn').disabled).toBe(false);

        // Uncheck it
        checkbox.checked = false;
        checkbox.dispatchEvent(new Event('change'));
        expect(document.getElementById('delete-btn').disabled).toBe(true);
    });
});

describe('setupSelectAll with an active-vs-deleted split', () => {
    // The manager's character list mixes active and deleted rows in one table,
    // so Delete and Restore each need their own side of data-deleted rather
    // than the plain "anything selected" rule (issue #111).
    const splitConfig = {
        selectAllId: 'select-all',
        checkboxClass: 'char-cb',
        bulkActionsId: 'bulk-actions',
        countId: 'count',
        disableButtonId: 'delete-btn',
        splitAttr: 'deleted',
        splitButtons: [
            { id: 'restore-btn', value: 'true' },
            { id: 'delete-btn', value: 'false' }
        ]
    };

    beforeEach(() => {
        document.body.innerHTML = `
            <input type="checkbox" id="select-all">
            <div id="bulk-actions">
                <span id="count">0</span>
                <button id="delete-btn" disabled>Delete</button>
                <button id="restore-btn" disabled>Restore</button>
            </div>
            <input type="checkbox" class="char-cb" value="1" data-deleted="false">
            <input type="checkbox" class="char-cb" value="2" data-deleted="true">
        `;
    });

    function check(value) {
        const checkbox = document.querySelector(`.char-cb[value="${value}"]`);
        checkbox.checked = true;
        checkbox.dispatchEvent(new Event('change'));
        return checkbox;
    }

    test('selecting an active row enables Delete but not Restore', () => {
        setupSelectAll(splitConfig);
        check('1');

        expect(document.getElementById('delete-btn').disabled).toBe(false);
        expect(document.getElementById('restore-btn').disabled).toBe(true);
    });

    test('selecting a deleted row enables Restore but not Delete', () => {
        setupSelectAll(splitConfig);
        check('2');

        expect(document.getElementById('restore-btn').disabled).toBe(false);
        // The count is 1, so the plain disableButtonId rule would have enabled
        // Delete — the split has to win over it.
        expect(document.getElementById('delete-btn').disabled).toBe(true);
    });

    test('select-all enables both buttons when the table holds each kind', () => {
        setupSelectAll(splitConfig);

        const selectAll = document.getElementById('select-all');
        selectAll.checked = true;
        selectAll.dispatchEvent(new Event('change'));

        expect(document.getElementById('delete-btn').disabled).toBe(false);
        expect(document.getElementById('restore-btn').disabled).toBe(false);
        expect(document.getElementById('count').textContent).toBe('2');
    });

    test('deselecting everything disables both buttons again', () => {
        setupSelectAll(splitConfig);

        const selectAll = document.getElementById('select-all');
        selectAll.checked = true;
        selectAll.dispatchEvent(new Event('change'));
        selectAll.checked = false;
        selectAll.dispatchEvent(new Event('change'));

        expect(document.getElementById('delete-btn').disabled).toBe(true);
        expect(document.getElementById('restore-btn').disabled).toBe(true);
    });

    test('a split button missing from the page is skipped, not fatal', () => {
        // The non-manager list renders no Restore button at all.
        document.getElementById('restore-btn').remove();
        setupSelectAll(splitConfig);

        expect(() => check('1')).not.toThrow();
        expect(document.getElementById('delete-btn').disabled).toBe(false);
    });
});

describe('bulkDeleteCharacters', () => {
    beforeEach(() => {
        fetch.mockClear();
        confirm.mockClear();
        alert.mockClear();
    });

    test('shows alert and returns early when no ids provided', async () => {
        const result = await bulkDeleteCharacters([], 'token', '/bulk-delete/');

        expect(alert).toHaveBeenCalledWith('No characters selected');
        expect(result).toEqual({ success: false });
        expect(fetch).not.toHaveBeenCalled();
    });

    test('shows singular confirm message for single item', async () => {
        confirm.mockReturnValue(false);
        await bulkDeleteCharacters([1], 'token', '/bulk-delete/');

        expect(confirm).toHaveBeenCalledWith('Delete this character?');
    });

    test('shows plural confirm message for multiple items', async () => {
        confirm.mockReturnValue(false);
        await bulkDeleteCharacters([1, 2, 3], 'token', '/bulk-delete/');

        expect(confirm).toHaveBeenCalledWith('Delete 3 characters?');
    });

    test('returns early if user cancels confirmation', async () => {
        confirm.mockReturnValue(false);
        const result = await bulkDeleteCharacters([1, 2], 'token', '/bulk-delete/');

        expect(result).toEqual({ success: false });
        expect(fetch).not.toHaveBeenCalled();
    });

    test('makes request with ids when confirmed', async () => {
        confirm.mockReturnValue(true);
        fetch.mockResolvedValue({
            json: () => Promise.resolve({ success: true, deleted: 2 })
        });

        const result = await bulkDeleteCharacters([1, 2], 'token', '/bulk-delete/');

        expect(fetch).toHaveBeenCalledWith('/bulk-delete/', {
            method: 'POST',
            headers: {
                'X-CSRFToken': 'token',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ ids: [1, 2] })
        });
        expect(result).toEqual({ success: true, deleted: 2 });
    });

    test('handles fetch errors gracefully', async () => {
        confirm.mockReturnValue(true);
        fetch.mockRejectedValue(new Error('Network error'));

        const result = await bulkDeleteCharacters([1], 'token', '/bulk-delete/');

        expect(alert).toHaveBeenCalledWith('Error: Network error');
        expect(result).toEqual({ success: false, error: 'Network error' });
    });
});

describe('bindCharacterRowActions', () => {
    function mountTable() {
        document.body.innerHTML = `
            <table><tbody id="rows">
                <tr data-id="4"><td>
                    <button class="restore-btn" data-id="4" data-restore-url="/tarmar/characters/4/restore/">Restore</button>
                </td></tr>
                <tr data-id="5"><td>
                    <button class="delete-btn" data-id="5" data-delete-url="/tarmar/characters/5/delete/" data-name="Mira">Delete</button>
                </td></tr>
                <tr data-id="6"><td><button id="plain">Other</button></td></tr>
            </tbody></table>
        `;
        return document.getElementById('rows');
    }

    beforeEach(() => {
        fetch.mockReset();
        confirm.mockReset();
        confirm.mockReturnValue(true);
        window.location.reload.mockClear();
    });

    test('Restore posts to the button\'s rendered URL and reloads', async () => {
        bindCharacterRowActions(mountTable());
        fetch.mockResolvedValue({ json: () => Promise.resolve({ success: true }) });

        document.querySelector('.restore-btn').click();
        await flush();
        await flush();

        expect(confirm).toHaveBeenCalledWith('Restore this character?');
        expect(fetch).toHaveBeenCalledWith('/tarmar/characters/4/restore/', expect.objectContaining({ method: 'POST' }));
        expect(window.location.reload).toHaveBeenCalled();
    });

    test('Delete asks by name and, without reload, removes just that row', async () => {
        bindCharacterRowActions(mountTable(), { reload: false });
        fetch.mockResolvedValue({ ok: true });

        document.querySelector('.delete-btn').click();
        await flush();
        await flush();

        expect(confirm).toHaveBeenCalledWith('Delete character "Mira"?');
        expect(fetch).toHaveBeenCalledWith('/tarmar/characters/5/delete/', expect.objectContaining({ method: 'POST' }));
        expect(document.querySelector('tr[data-id="5"]')).toBeNull();
        expect(window.location.reload).not.toHaveBeenCalled();
    });

    test('a button that carries no action URL is ignored', async () => {
        bindCharacterRowActions(mountTable());

        document.getElementById('plain').click();
        await flush();

        expect(confirm).not.toHaveBeenCalled();
        expect(fetch).not.toHaveBeenCalled();
    });
});
