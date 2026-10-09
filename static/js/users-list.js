/**
 * The users list's Delete buttons (issue #338 item 18).
 *
 * This lived in an inline <script> in users_list.html, where Jest could not
 * reach it. Each button carries data-user-id, data-username and
 * data-delete-url (rendered by {% url %}); the endpoint answers JSON with
 * success or an error.
 *
 * csrfToken/postWithCsrf/uiNotify/uiConfirm come from api.js, which base.html
 * loads in <head>. Node/Jest has no shared script scope, so require it there.
 */
if (typeof module !== 'undefined' && module.exports) {
    require('./api.js');
}

/** Ask, then delete the user behind one button and take their row away. */
async function deleteUser(button, doc) {
    const page = doc || document;
    const { userId, username, deleteUrl } = button.dataset;
    if (!await uiConfirm('Delete user "' + username + '"?', { danger: true, confirmLabel: 'Delete' })) {
        return;
    }
    try {
        const response = await postWithCsrf(deleteUrl, csrfToken());
        const data = await response.json();
        if (data.success) {
            const row = page.querySelector('tr[data-user-id="' + userId + '"]');
            if (row) row.remove();
        } else {
            uiNotify(data.error || 'Failed to delete user');
        }
    } catch (error) {
        uiNotify('Failed to delete user');
    }
}

function initUsersList(doc) {
    const page = doc || document;
    const table = page.querySelector('.users-table');
    if (!table) return;
    table.addEventListener('click', (event) => {
        const button = event.target.closest('.delete-btn-outline');
        if (button) deleteUser(button, page);
    });
}

if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => initUsersList(document));
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { initUsersList, deleteUser };
}
