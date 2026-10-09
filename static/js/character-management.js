/**
 * Shared character management utilities.
 * Provides common AJAX operations for delete, restore, and bulk actions.
 *
 * postWithCsrf/uiNotify/uiConfirm come from api.js, which base.html loads in
 * <head> — ahead of this file's <script> tag wherever a template places it.
 * Node/Jest has no shared script scope, so require api.js there; either way
 * the names end up on the global object.
 */
if (typeof module !== 'undefined' && module.exports) {
    require('./api.js');
}

/**
 * Remove a table row by ID and reload if table is empty.
 * @param {string|number} id - The row data-id value
 * @param {string} rowSelector - Optional custom row selector (default: tr[data-id])
 */
function removeRowAndCheckEmpty(id, rowSelector = 'tr[data-id]') {
    const row = document.querySelector(`tr[data-id="${id}"]`);
    if (row) row.remove();

    // If no more rows, reload to show empty message
    if (document.querySelectorAll(`tbody ${rowSelector}`).length === 0) {
        location.reload();
    }
}

/**
 * Delete a character (soft delete).
 * @param {string|number} id - Character ID
 * @param {string} url - Delete endpoint URL (caller provides this, rendered
 *   from {% url %} — this module has no template access of its own)
 * @param {string} name - Character name (for confirmation)
 * @param {string} csrfToken - CSRF token
 * @param {boolean} reload - Whether to reload page on success (default: true)
 * @param {Element} [anchor] - The button that asked, so the confirmation is
 *   drawn beside it rather than at the top of the document (issue #711).
 */
async function deleteCharacter(id, url, name, csrfToken, reload = true, anchor) {
    if (!await uiConfirm(`Delete character "${name}"?`, { danger: true, confirmLabel: "Delete", anchor })) return;

    try {
        const response = await postWithCsrf(url, csrfToken);
        if (response.redirected || response.ok) {
            if (reload) {
                location.reload();
            } else {
                removeRowAndCheckEmpty(id);
            }
        } else {
            // A refusal is a 403 and a rate-limited delete a 429; neither is a
            // redirect and neither is ok, so this branch used not to exist at
            // all and the row simply stayed there, with no notice, after the
            // user had confirmed the deletion (issue #640). restoreCharacter
            // below has always said why. The endpoint answers JSON for both,
            // but an error page would not, so a body that will not parse
            // falls back to a sentence of our own rather than being ignored.
            uiNotify(await refusalMessage(response, 'Could not delete that character'));
        }
    } catch (error) {
        uiNotify('Error: ' + error.message);
    }
}

/**
 * What a refused response says, for a notice.
 * @param {Response} response - The refusal, whose body may or may not be JSON.
 * @param {string} fallback - Said when the body carries no `error`.
 * @returns {Promise<string>}
 */
async function refusalMessage(response, fallback) {
    let data = {};
    try {
        data = await response.json();
    } catch (parseError) {
        // No JSON body — an HTML error page, or no body at all. The status
        // line is all there is, and `fallback` says it in words.
        data = {};
    }
    return (data && data.error) || fallback;
}

/**
 * Restore a soft-deleted character.
 * @param {string|number} id - Character ID
 * @param {string} url - Restore endpoint URL (caller provides this, rendered
 *   from {% url %} — this module has no template access of its own)
 * @param {string} csrfToken - CSRF token
 * @param {boolean} reload - Whether to reload page on success (default: true)
 * @param {Element} [anchor] - The button that asked (issue #711).
 */
async function restoreCharacter(id, url, csrfToken, reload = true, anchor) {
    if (!await uiConfirm('Restore this character?', { confirmLabel: 'Restore', anchor })) return;

    try {
        const response = await postWithCsrf(url, csrfToken);
        const data = await response.json();
        if (data.success) {
            if (reload) {
                location.reload();
            } else {
                removeRowAndCheckEmpty(id);
            }
        } else {
            uiNotify(data.error || 'Failed to restore character');
        }
    } catch (error) {
        uiNotify('Error: ' + error.message);
    }
}

/**
 * Bulk delete characters.
 * @param {number[]} ids - Array of character IDs
 * @param {string} csrfToken - CSRF token
 * @param {string} url - Bulk delete endpoint URL
 * @returns {Promise<{success: boolean, deleted?: number, error?: string}>}
 */
async function bulkDeleteCharacters(ids, csrfToken, url) {
    if (ids.length === 0) {
        uiNotify('No characters selected');
        return { success: false };
    }

    const confirmMsg = ids.length === 1
        ? 'Delete this character?'
        : `Delete ${ids.length} characters?`;

    if (!await uiConfirm(confirmMsg, { danger: true, confirmLabel: 'Delete' })) return { success: false };

    try {
        const response = await postWithCsrf(url, csrfToken, { ids });
        return await response.json();
    } catch (error) {
        uiNotify('Error: ' + error.message);
        return { success: false, error: error.message };
    }
}

/**
 * Setup select-all checkbox functionality for a table.
 * @param {object} config - Configuration object
 * @param {string} config.selectAllId - ID of select-all checkbox
 * @param {string} config.checkboxClass - Class of row checkboxes
 * @param {string} config.bulkActionsId - ID of bulk actions container
 * @param {string} config.countId - ID of selected count span
 * @param {string} config.visibleClass - Class to show bulk actions (default: 'visible')
 * @param {string} config.hiddenClass - Class to hide bulk actions (optional, alternative to visibleClass)
 * @param {string} config.disableButtonId - ID of button to disable when none selected (optional)
 * @param {string} config.splitAttr - Checkbox data attribute that splits the
 *   selection into groups, e.g. 'deleted' for data-deleted (optional; requires
 *   splitButtons)
 * @param {Array<{id: string, value: string}>} config.splitButtons - Buttons
 *   that act on one side of that split. Each is enabled only while at least
 *   one checked box has splitAttr === value, which overrides the plain
 *   disableButtonId rule for that button (optional)
 */
function setupSelectAll(config) {
    const {
        selectAllId,
        checkboxClass,
        bulkActionsId,
        countId,
        visibleClass = 'visible',
        hiddenClass = null,
        disableButtonId = null,
        splitAttr = null,
        splitButtons = null
    } = config;

    const selectAll = document.getElementById(selectAllId);
    const bulkActions = document.getElementById(bulkActionsId);
    const selectedCount = document.getElementById(countId);
    const disableButton = disableButtonId ? document.getElementById(disableButtonId) : null;

    if (!selectAll || !bulkActions) return;

    function updateBulkActions() {
        const checkboxes = document.querySelectorAll(`.${checkboxClass}`);
        const checked = document.querySelectorAll(`.${checkboxClass}:checked`);
        const count = checked.length;

        if (selectedCount) selectedCount.textContent = count;

        if (hiddenClass) {
            bulkActions.classList.toggle(hiddenClass, count === 0);
        } else if (!disableButton) {
            bulkActions.classList.toggle(visibleClass, count > 0);
        }

        if (disableButton) {
            disableButton.disabled = count === 0;
        }

        // Applied after the disableButton rule above, deliberately: a button
        // that appears in both places is governed by its side of the split.
        if (splitAttr && splitButtons) {
            splitButtons.forEach(({ id, value }) => {
                const button = document.getElementById(id);
                if (!button) return;
                button.disabled = !Array.from(checked)
                    .some(cb => cb.dataset[splitAttr] === value);
            });
        }

        selectAll.indeterminate = count > 0 && count < checkboxes.length;
        selectAll.checked = count === checkboxes.length && count > 0;
    }

    selectAll.addEventListener('change', function() {
        document.querySelectorAll(`.${checkboxClass}`).forEach(cb => cb.checked = this.checked);
        updateBulkActions();
    });

    document.querySelectorAll(`.${checkboxClass}`).forEach(cb => {
        cb.addEventListener('change', updateBulkActions);
    });

    // Return update function for external use
    return updateBulkActions;
}

/**
 * Get selected checkbox values, optionally filtered by a data attribute.
 * @param {string} checkboxClass - Class of checkboxes
 * @param {string|null} filterAttr - Data attribute to filter by (e.g., 'deleted')
 * @param {string|null} filterValue - Value the attribute must match
 * @returns {number[]} Array of selected IDs
 */
function getSelectedIds(checkboxClass, filterAttr = null, filterValue = null) {
    const checked = document.querySelectorAll(`.${checkboxClass}:checked`);
    return Array.from(checked)
        .filter(cb => filterAttr === null || cb.dataset[filterAttr] === filterValue)
        .map(cb => parseInt(cb.value));
}

/**
 * Wire a character table's per-row Restore and Delete buttons.
 *
 * The character list called restoreCharacter()/deleteCharacter() from inline
 * onclick attributes, while the deleted-characters page bound the same restore
 * from data attributes in its own inline script: two wirings for one button
 * (issue #338 items 18 and 19). Both tables now mark their <tbody> with
 * data-character-row-actions and each button carries what it needs:
 *
 *   <button data-id="7" data-restore-url="/…/7/restore/">Restore</button>
 *   <button data-id="7" data-delete-url="/…/7/delete/" data-name="Aldwin">Delete</button>
 *
 * The URLs are rendered by {% url %} in the template, so this module still
 * builds no paths of its own (issue #112).
 *
 * @param {Element} container - The element whose buttons to wire.
 * @param {{reload?: boolean}} [options] - Reload the page after an action
 *   (the list) or just remove the row (the deleted-characters page).
 */
function bindCharacterRowActions(container, options) {
    const reload = !options || options.reload !== false;
    container.addEventListener('click', (event) => {
        const button = event.target.closest('button[data-restore-url], button[data-delete-url]');
        if (!button || !container.contains(button)) return;
        if (button.dataset.restoreUrl) {
            restoreCharacter(button.dataset.id, button.dataset.restoreUrl, csrfToken(), reload, button);
        } else {
            deleteCharacter(
                button.dataset.id, button.dataset.deleteUrl, button.dataset.name, csrfToken(), reload,
                button
            );
        }
    });
}

// A table asks for its row buttons with data-character-row-actions; the value
// "no-reload" removes a row in place instead of reloading the page.
if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => {
        document.querySelectorAll('[data-character-row-actions]').forEach((container) => {
            bindCharacterRowActions(container, {
                reload: container.dataset.characterRowActions !== 'no-reload'
            });
        });
    });
}

// Export for testing (Node.js environment)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        removeRowAndCheckEmpty,
        deleteCharacter,
        restoreCharacter,
        bulkDeleteCharacters,
        setupSelectAll,
        getSelectedIds,
        bindCharacterRowActions
    };
}
