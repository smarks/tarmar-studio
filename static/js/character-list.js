/**
 * The character list page: importing a character, the show-deleted and
 * owner filters, and the bulk Delete/Restore bar (issue #338 item 19).
 *
 * All of this used to sit in three inline <script> blocks in
 * character_list.html, where Jest could not reach it. The template now carries
 * only markup; the URLs these handlers need are rendered by {% url %} into
 * data attributes:
 *
 *   #import-character-form   data-edit-url-template  (…/characters/0/)
 *   #bulk-actions            data-bulk-delete-url, data-restore-url-template
 *
 * The per-row Restore/Delete buttons are character-management.js's
 * bindCharacterRowActions, shared with the deleted-characters page.
 *
 * csrfToken/postWithCsrf/uiNotify/uiConfirm come from api.js, and
 * setupSelectAll/getSelectedIds/bulkDeleteCharacters from
 * character-management.js; the template loads both first. Node/Jest has no
 * shared script scope, so they are required there.
 */
if (typeof module !== 'undefined' && module.exports) {
    require('./api.js');
    Object.assign(globalThis, require('./character-management.js'));
}

/** Reload the list with one query parameter set, or removed when empty. */
function reloadWithParam(name, value) {
    const url = new URL(window.location.href);
    if (value) {
        url.searchParams.set(name, value);
    } else {
        url.searchParams.delete(name);
    }
    pageNavigation.go(url.toString());
}

function toggleShowDeleted(checked) {
    reloadWithParam('show_deleted', checked ? '1' : '');
}

function filterByUser(userId) {
    reloadWithParam('user', userId);
}

/**
 * Upload the import form's file, then open the new character or say why not.
 * The error line stays until the next attempt.
 */
async function importCharacter(form, errorSpan) {
    errorSpan.hidden = true;
    errorSpan.textContent = '';
    try {
        // FormData body: postWithCsrf passes it through untouched so the
        // browser sets the multipart Content-Type and boundary itself.
        const response = await postWithCsrf(form.action, csrfToken(), new FormData(form));
        const data = await response.json();
        if (response.ok && data.success) {
            // Placeholder pk of 0 stands in for the id the server just made.
            pageNavigation.go(form.dataset.editUrlTemplate.replace('/0/', `/${data.character_id}/`));
        } else {
            errorSpan.textContent = data.error || 'Import failed';
            errorSpan.hidden = false;
        }
    } catch (error) {
        errorSpan.textContent = 'Import failed: ' + error.message;
        errorSpan.hidden = false;
    }
}

async function deleteSelectedCharacters(bulkDeleteUrl) {
    const ids = getSelectedIds('character-checkbox', 'deleted', 'false');
    if (ids.length === 0) {
        uiNotify('No active characters selected', 'info');
        return;
    }
    const result = await bulkDeleteCharacters(ids, csrfToken(), bulkDeleteUrl);
    if (result.success) {
        pageNavigation.reload();
    } else if (result.error) {
        uiNotify(result.error);
    }
}

async function restoreSelectedCharacters(restoreUrlTemplate) {
    const ids = getSelectedIds('character-checkbox', 'deleted', 'true');
    if (ids.length === 0) {
        uiNotify('No deleted characters selected', 'info');
        return;
    }
    if (!await uiConfirm(`Restore ${ids.length} character(s)?`, { confirmLabel: 'Restore' })) return;

    // Each restore can fail on its own (issue #339, item 35). Count the
    // failures and say so in a notice that stays; reloading would wipe the
    // notice and hide which ones did not come back.
    let failures = 0;
    for (const id of ids) {
        try {
            // The url tag needs a concrete pk, so the template renders pk 0.
            const response = await postWithCsrf(restoreUrlTemplate.replace('/0/', `/${id}/`), csrfToken());
            if (!response.ok) failures += 1;
        } catch (error) {
            failures += 1;
        }
    }
    if (failures > 0) {
        uiNotify(`${failures} of ${ids.length} character(s) could not be restored. Reload the page to see which are still deleted.`);
        return;
    }
    pageNavigation.reload();
}

//: Marks a document whose owner filter is already listened for, so wiring
//: the page twice cannot reload it twice for one choice.
const USER_FILTER_BOUND = Symbol.for('tarmar.character-list.user-filter.bound');

/** Wire every control the page rendered; a control that is absent is skipped. */
function initCharacterList(doc) {
    const page = doc || document;

    const importForm = page.getElementById('import-character-form');
    const importToggle = page.getElementById('import-character-toggle');
    if (importToggle && importForm) {
        importToggle.addEventListener('click', () => importForm.classList.toggle('open'));
    }
    if (importForm) {
        importForm.addEventListener('submit', (event) => {
            event.preventDefault();
            importCharacter(importForm, page.getElementById('import-error'));
        });
    }

    const showDeleted = page.getElementById('show-deleted');
    if (showDeleted) {
        showDeleted.addEventListener('change', () => toggleShowDeleted(showDeleted.checked));
    }
    // The owner filter is a searchable picker (#973): a search or a page
    // turn swaps in a new <select id="user-filter">, so the listener sits on
    // the document, once, rather than on the select the page loaded with.
    // It listens in the capture phase, which every change event passes
    // through on its way down, bubbling or not.
    if (page.getElementById('user-filter') && !page[USER_FILTER_BOUND]) {
        page[USER_FILTER_BOUND] = true;
        page.addEventListener('change', (event) => {
            if (event.target && event.target.id === 'user-filter') {
                filterByUser(event.target.value);
            }
        }, true);
    }

    const bulkActions = page.getElementById('bulk-actions');
    if (!bulkActions) return;
    // A manager sees active and deleted characters in one table, so the two
    // bulk buttons key off the row's data-deleted rather than the raw count:
    // Delete needs an active row selected, Restore a deleted one.
    setupSelectAll({
        selectAllId: 'select-all',
        checkboxClass: 'character-checkbox',
        bulkActionsId: 'bulk-actions',
        countId: 'selected-count',
        disableButtonId: 'delete-selected-btn',
        splitAttr: 'deleted',
        splitButtons: [
            { id: 'restore-selected-btn', value: 'true' },
            { id: 'delete-selected-btn', value: 'false' }
        ]
    });
    const deleteSelected = page.getElementById('delete-selected-btn');
    if (deleteSelected) {
        deleteSelected.addEventListener('click', () => deleteSelectedCharacters(bulkActions.dataset.bulkDeleteUrl));
    }
    const restoreSelected = page.getElementById('restore-selected-btn');
    if (restoreSelected) {
        restoreSelected.addEventListener('click', () => restoreSelectedCharacters(bulkActions.dataset.restoreUrlTemplate));
    }
}

if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => initCharacterList(document));
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        initCharacterList,
        importCharacter,
        toggleShowDeleted,
        filterByUser,
        deleteSelectedCharacters,
        restoreSelectedCharacters
    };
}
