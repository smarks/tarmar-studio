/**
 * Unit tests for users-list.js: the users list's Delete buttons, which used to
 * be an inline script in users_list.html (#338 item 18).
 */

const { initUsersList, deleteUser } = require('../users-list.js');
const { flush } = require('./test-helpers');

function mountUsers() {
    document.body.innerHTML = `
        <table class="users-table"><tbody>
            <tr data-user-id="9"><td>ada</td><td>
                <button class="delete-btn-outline" data-user-id="9" data-username="ada"
                        data-delete-url="/tarmar/accounts/users/9/delete/">Delete</button>
            </td></tr>
            <tr data-user-id="10"><td>bo</td><td></td></tr>
        </tbody></table>
    `;
    return document.querySelector('.delete-btn-outline');
}

beforeEach(() => {
    fetch.mockReset();
    alert.mockClear();
    confirm.mockReset();
    confirm.mockReturnValue(true);
});

describe('deleting a user', () => {
    test('asks by name, posts to the rendered URL, and takes the row away', async () => {
        const button = mountUsers();
        fetch.mockResolvedValue({ json: () => Promise.resolve({ success: true }) });

        await deleteUser(button, document);

        expect(confirm).toHaveBeenCalledWith('Delete user "ada"?');
        expect(fetch).toHaveBeenCalledWith(
            '/tarmar/accounts/users/9/delete/',
            expect.objectContaining({ method: 'POST', headers: { 'X-CSRFToken': 'test-token' } })
        );
        expect(document.querySelector('tr[data-user-id="9"]')).toBeNull();
        expect(document.querySelector('tr[data-user-id="10"]')).not.toBeNull();
    });

    test('a cancelled confirmation sends nothing', async () => {
        const button = mountUsers();
        confirm.mockReturnValue(false);

        await deleteUser(button, document);

        expect(fetch).not.toHaveBeenCalled();
    });

    test('a refusal leaves the row and shows the server\'s reason', async () => {
        const button = mountUsers();
        fetch.mockResolvedValue({ json: () => Promise.resolve({ success: false, error: 'Cannot delete yourself' }) });

        await deleteUser(button, document);

        expect(alert).toHaveBeenCalledWith('Cannot delete yourself');
        expect(document.querySelector('tr[data-user-id="9"]')).not.toBeNull();
    });

    test('a failed request says the delete failed', async () => {
        const button = mountUsers();
        fetch.mockRejectedValue(new Error('offline'));

        await deleteUser(button, document);

        expect(alert).toHaveBeenCalledWith('Failed to delete user');
    });

    test('initUsersList wires the table so a click on Delete deletes', async () => {
        const button = mountUsers();
        fetch.mockResolvedValue({ json: () => Promise.resolve({ success: true }) });
        initUsersList(document);

        button.click();
        await flush();
        await flush();

        expect(document.querySelector('tr[data-user-id="9"]')).toBeNull();
    });

    test('a page without the table is left alone', () => {
        document.body.innerHTML = '<main></main>';
        expect(() => initUsersList(document)).not.toThrow();
    });
});
