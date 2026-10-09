/**
 * Unit tests for autosave.js
 */

const { initAutosave } = require('../autosave.js');

describe('initAutosave', () => {
    let fetchMock;

    beforeEach(() => {
        fetch.mockClear();
        fetchMock = fetch.mockResolvedValue({
            json: () => Promise.resolve({ success: true })
        });

        document.body.innerHTML = `
            <div data-field="name">
                <input class="autosave" type="text" value="Original Name">
            </div>
            <div data-field="email">
                <input class="autosave" type="text" value="test@example.com">
            </div>
            <div data-field="active">
                <input class="autosave" type="checkbox" checked>
            </div>
            <div data-field="role">
                <select class="autosave">
                    <option value="player" selected>Player</option>
                    <option value="dm">DM</option>
                </select>
            </div>
        `;
    });

    test('stores original values on init', () => {
        initAutosave({
            updateUrl: '/update/'
        });

        // Original values stored internally - verify by checking fields haven't changed
        const nameInput = document.querySelector('[data-field="name"] input');
        expect(nameInput.value).toBe('Original Name');
    });

    test('saves text field on blur when value changed', async () => {
        initAutosave({
            updateUrl: '/update/'
        });

        const nameInput = document.querySelector('[data-field="name"] input');
        nameInput.value = 'New Name';
        nameInput.dispatchEvent(new Event('blur'));

        // Wait for async operations
        await new Promise(resolve => setTimeout(resolve, 0));

        expect(fetch).toHaveBeenCalledWith('/update/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': 'test-token'
            },
            body: JSON.stringify({ field: 'name', value: 'New Name' })
        });
    });

    test('does not save text field when value unchanged', async () => {
        initAutosave({
            updateUrl: '/update/'
        });

        const nameInput = document.querySelector('[data-field="name"] input');
        // Value unchanged
        nameInput.dispatchEvent(new Event('blur'));

        await new Promise(resolve => setTimeout(resolve, 0));

        expect(fetch).not.toHaveBeenCalled();
    });

    test('adds saving class during save', async () => {
        let resolvePromise;
        fetch.mockImplementation(() => new Promise(resolve => {
            resolvePromise = () => resolve({
                json: () => Promise.resolve({ success: true })
            });
        }));

        initAutosave({
            updateUrl: '/update/'
        });

        const container = document.querySelector('[data-field="name"]');
        const nameInput = container.querySelector('input');
        nameInput.value = 'New Name';
        nameInput.dispatchEvent(new Event('blur'));

        // Should have saving class while request is pending
        expect(container.classList.contains('saving')).toBe(true);

        // Resolve the fetch
        resolvePromise();
        await new Promise(resolve => setTimeout(resolve, 0));

        expect(container.classList.contains('saving')).toBe(false);
    });

    test('adds saved class on success', async () => {
        initAutosave({
            updateUrl: '/update/'
        });

        const container = document.querySelector('[data-field="name"]');
        const nameInput = container.querySelector('input');
        nameInput.value = 'New Name';
        nameInput.dispatchEvent(new Event('blur'));

        await new Promise(resolve => setTimeout(resolve, 0));

        expect(container.classList.contains('saved')).toBe(true);
    });

    test('adds error class on failure', async () => {
        fetch.mockResolvedValue({
            ok: true,
            json: () => Promise.resolve({ success: false })
        });

        initAutosave({
            updateUrl: '/update/'
        });

        const container = document.querySelector('[data-field="name"]');
        const nameInput = container.querySelector('input');
        nameInput.value = 'New Name';
        nameInput.dispatchEvent(new Event('blur'));

        await new Promise(resolve => setTimeout(resolve, 0));

        expect(container.classList.contains('error')).toBe(true);
    });

    test('reverts value on error', async () => {
        fetch.mockResolvedValue({
            ok: true,
            json: () => Promise.resolve({ success: false })
        });

        initAutosave({
            updateUrl: '/update/'
        });

        const nameInput = document.querySelector('[data-field="name"] input');
        nameInput.value = 'New Name';
        nameInput.dispatchEvent(new Event('blur'));

        await new Promise(resolve => setTimeout(resolve, 0));

        expect(nameInput.value).toBe('Original Name');
    });

    test('treats non-2xx response without success field as failure', async () => {
        fetch.mockResolvedValue({
            ok: false,
            json: () => Promise.resolve({ error: 'Forbidden' })
        });

        initAutosave({
            updateUrl: '/update/'
        });

        const container = document.querySelector('[data-field="name"]');
        const nameInput = container.querySelector('input');
        nameInput.value = 'New Name';
        nameInput.dispatchEvent(new Event('blur'));
        await new Promise(resolve => setTimeout(resolve, 0));

        expect(container.classList.contains('error')).toBe(true);
        expect(container.querySelector('.autosave-error').textContent).toBe('Forbidden');
    });

    test('shows server error message inline next to field on validation failure', async () => {
        fetch.mockResolvedValue({
            ok: false,
            json: () => Promise.resolve({ success: false, error: 'Name cannot contain emoji' })
        });

        initAutosave({
            updateUrl: '/update/'
        });

        const container = document.querySelector('[data-field="name"]');
        const nameInput = container.querySelector('input');
        nameInput.value = '🎲';
        nameInput.dispatchEvent(new Event('blur'));

        await new Promise(resolve => setTimeout(resolve, 0));

        const errorEl = container.querySelector('.autosave-error');
        expect(errorEl).not.toBeNull();
        expect(errorEl.textContent).toBe('Name cannot contain emoji');
        expect(errorEl.getAttribute('role')).toBe('alert');
    });

    test('shows generic error when server omits message', async () => {
        fetch.mockResolvedValue({
            ok: false,
            json: () => Promise.resolve({ success: false })
        });

        initAutosave({
            updateUrl: '/update/'
        });

        const container = document.querySelector('[data-field="name"]');
        const nameInput = container.querySelector('input');
        nameInput.value = 'New Name';
        nameInput.dispatchEvent(new Event('blur'));

        await new Promise(resolve => setTimeout(resolve, 0));

        expect(container.querySelector('.autosave-error').textContent).toBe('Save failed');
    });

    test('shows network error on fetch rejection', async () => {
        fetch.mockRejectedValue(new Error('boom'));

        initAutosave({
            updateUrl: '/update/'
        });

        const container = document.querySelector('[data-field="name"]');
        const nameInput = container.querySelector('input');
        nameInput.value = 'New Name';
        nameInput.dispatchEvent(new Event('blur'));

        await new Promise(resolve => setTimeout(resolve, 0));

        expect(container.querySelector('.autosave-error').textContent).toBe('Network error — try again');
    });

    test('error message is removed when user starts editing again', async () => {
        fetch.mockResolvedValue({
            ok: false,
            json: () => Promise.resolve({ success: false, error: 'Too long' })
        });

        initAutosave({
            updateUrl: '/update/'
        });

        const container = document.querySelector('[data-field="name"]');
        const nameInput = container.querySelector('input');
        nameInput.value = 'New Name';
        nameInput.dispatchEvent(new Event('blur'));

        await new Promise(resolve => setTimeout(resolve, 0));
        expect(container.querySelector('.autosave-error')).not.toBeNull();

        nameInput.dispatchEvent(new Event('input'));
        expect(container.querySelector('.autosave-error')).toBeNull();
        expect(container.classList.contains('error')).toBe(false);
    });

    test('successful save after error clears the error indicator', async () => {
        // First call: failure.
        fetch.mockResolvedValueOnce({
            ok: false,
            json: () => Promise.resolve({ success: false, error: 'bad' })
        });
        // Second call: success.
        fetch.mockResolvedValueOnce({
            ok: true,
            json: () => Promise.resolve({ success: true })
        });

        initAutosave({
            updateUrl: '/update/'
        });

        const container = document.querySelector('[data-field="name"]');
        const nameInput = container.querySelector('input');
        nameInput.value = 'First';
        nameInput.dispatchEvent(new Event('blur'));
        await new Promise(resolve => setTimeout(resolve, 0));
        expect(container.querySelector('.autosave-error')).not.toBeNull();

        nameInput.value = 'Second';
        nameInput.dispatchEvent(new Event('blur'));
        await new Promise(resolve => setTimeout(resolve, 0));
        expect(container.querySelector('.autosave-error')).toBeNull();
        expect(container.classList.contains('saved')).toBe(true);
    });

    test('saves checkbox on change', async () => {
        initAutosave({
            updateUrl: '/update/'
        });

        const checkbox = document.querySelector('[data-field="active"] input');
        checkbox.checked = false;
        checkbox.dispatchEvent(new Event('change'));

        await new Promise(resolve => setTimeout(resolve, 0));

        expect(fetch).toHaveBeenCalledWith('/update/', expect.objectContaining({
            body: JSON.stringify({ field: 'active', value: false })
        }));
    });

    test('saves select on change', async () => {
        initAutosave({
            updateUrl: '/update/'
        });

        const select = document.querySelector('[data-field="role"] select');
        select.value = 'dm';
        select.dispatchEvent(new Event('change'));

        await new Promise(resolve => setTimeout(resolve, 0));

        expect(fetch).toHaveBeenCalledWith('/update/', expect.objectContaining({
            body: JSON.stringify({ field: 'role', value: 'dm' })
        }));
    });

    test('calls onSuccess callback when provided', async () => {
        const onSuccess = jest.fn();

        initAutosave({
            updateUrl: '/update/',
            onSuccess
        });

        const nameInput = document.querySelector('[data-field="name"] input');
        nameInput.value = 'New Name';
        nameInput.dispatchEvent(new Event('blur'));

        await new Promise(resolve => setTimeout(resolve, 0));

        expect(onSuccess).toHaveBeenCalledWith('name', 'New Name', { success: true });
    });

    test('validates field when validate function provided', async () => {
        const validate = jest.fn().mockReturnValue(false);

        initAutosave({
            updateUrl: '/update/',
            validate
        });

        const nameInput = document.querySelector('[data-field="name"] input');
        nameInput.value = 'Invalid';
        nameInput.dispatchEvent(new Event('blur'));

        await new Promise(resolve => setTimeout(resolve, 0));

        expect(validate).toHaveBeenCalledWith('name', 'Invalid', nameInput);
        expect(fetch).not.toHaveBeenCalled();
        expect(nameInput.value).toBe('Original Name');
        // A refusal is said, not silent (issue #339, item 23).
        const container = document.querySelector('[data-field="name"]');
        expect(container.classList.contains('error')).toBe(true);
        expect(container.textContent).toContain('That value cannot be saved.');
    });

    test('a validator that gives a reason has it shown beside the field', async () => {
        initAutosave({
            updateUrl: '/update/',
            validate: (field, value) => (field === 'name' && !value ? 'A character needs a name.' : true),
        });

        const nameInput = document.querySelector('[data-field="name"] input');
        nameInput.value = '';
        nameInput.dispatchEvent(new Event('blur'));

        const container = document.querySelector('[data-field="name"]');
        expect(fetch).not.toHaveBeenCalled();
        expect(nameInput.value).toBe('Original Name');
        expect(container.textContent).toContain('A character needs a name.');
    });

    test('a reply that is not JSON is reported as a network error, not a refusal', async () => {
        // A 500 that returns Django's HTML error page. The saver says the
        // request did not get through, as it did before postForSuccess (#338).
        fetch.mockResolvedValue({
            ok: false,
            status: 500,
            json: () => Promise.reject(new Error('Unexpected token <'))
        });
        initAutosave({ updateUrl: '/update/' });

        const container = document.querySelector('[data-field="name"]');
        const nameInput = container.querySelector('input');
        nameInput.value = 'New Name';
        nameInput.dispatchEvent(new Event('blur'));
        await new Promise(resolve => setTimeout(resolve, 0));
        await new Promise(resolve => setTimeout(resolve, 0));

        expect(container.querySelector('.autosave-error').textContent).toBe('Network error — try again');
        expect(nameInput.value).toBe('Original Name');
    });

    test('saves on Enter key press', async () => {
        initAutosave({
            updateUrl: '/update/'
        });

        const nameInput = document.querySelector('[data-field="name"] input');

        // Focus the input first
        nameInput.focus();
        nameInput.value = 'New Name';

        // Dispatch Enter key which calls blur()
        const enterEvent = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true });
        nameInput.dispatchEvent(enterEvent);

        // The blur triggered by Enter will then trigger the save
        await new Promise(resolve => setTimeout(resolve, 0));

        expect(fetch).toHaveBeenCalled();
    });

    test('returns saveField function for external use', () => {
        const result = initAutosave({
            updateUrl: '/update/'
        });

        expect(typeof result.saveField).toBe('function');
    });

    test('a textarea reverts on error and keeps Enter for new lines (#339 item 17)', async () => {
        document.body.insertAdjacentHTML('beforeend', `
            <div data-field="description">
                <textarea class="autosave">Old words</textarea>
            </div>
        `);
        fetch.mockResolvedValue({
            ok: false,
            json: () => Promise.resolve({ success: false, error: 'Too long' })
        });
        initAutosave({ updateUrl: '/update/' });

        const textarea = document.querySelector('[data-field="description"] textarea');
        textarea.focus();
        const blurSpy = jest.spyOn(textarea, 'blur');
        const enterEvent = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
        textarea.dispatchEvent(enterEvent);
        expect(blurSpy).not.toHaveBeenCalled();
        expect(enterEvent.defaultPrevented).toBe(false);

        textarea.value = 'New words';
        textarea.dispatchEvent(new Event('blur'));
        await new Promise(resolve => setTimeout(resolve, 0));
        await new Promise(resolve => setTimeout(resolve, 0));

        const container = document.querySelector('[data-field="description"]');
        expect(container.classList.contains('error')).toBe(true);
        expect(container.textContent).toContain('Too long');
        expect(textarea.value).toBe('Old words');
    });
});

describe('two saves of the same field (issue #660)', () => {
    beforeEach(() => {
        fetch.mockReset();
        document.body.innerHTML = `
            <div data-field="name">
                <input class="autosave" type="text" value="Original Name">
            </div>
        `;
    });

    /**
     * A fetch mock whose responses settle in the order the test chooses.
     * @param {number[]} order - indexes of the calls, in the order they answer
     * @returns {{settle: () => void}} - settles them all, in that order
     */
    function outOfOrderFetch(order) {
        const settlers = [];
        fetch.mockImplementation(() => new Promise((resolve) => {
            settlers.push(() => resolve({ json: () => Promise.resolve({ success: true }) }));
        }));
        return {
            settle: () => order.forEach((index) => settlers[index] && settlers[index]()),
        };
    }

    test('the second is not sent until the first has answered', async () => {
        const responses = outOfOrderFetch([0, 1]);
        initAutosave({ updateUrl: '/update/' });
        const input = document.querySelector('[data-field="name"] input');

        input.value = 'First';
        input.dispatchEvent(new Event('blur'));
        input.value = 'Second';
        input.dispatchEvent(new Event('blur'));
        await new Promise((resolve) => setTimeout(resolve, 0));

        // Nothing used to hold the second back, so both were outstanding at
        // once and the server could answer them in either order.
        expect(fetch).toHaveBeenCalledTimes(1);
        expect(JSON.parse(fetch.mock.calls[0][1].body).value).toBe('First');

        responses.settle();
        await new Promise((resolve) => setTimeout(resolve, 0));

        expect(fetch).toHaveBeenCalledTimes(2);
        expect(JSON.parse(fetch.mock.calls[1][1].body).value).toBe('Second');
    });

    test('a refusal after two saves reverts to what the server holds', async () => {
        // The first save succeeds, the second is refused: the input must go
        // back to the first value, which is the one on the row -- not to the
        // value the page loaded with, which the old code would have written
        // when the two answers crossed.
        fetch
            .mockResolvedValueOnce({ json: () => Promise.resolve({ success: true }) })
            .mockResolvedValueOnce({
                ok: false,
                json: () => Promise.resolve({ success: false, error: 'No' }),
            });
        initAutosave({ updateUrl: '/update/' });
        const input = document.querySelector('[data-field="name"] input');

        input.value = 'First';
        input.dispatchEvent(new Event('blur'));
        await new Promise((resolve) => setTimeout(resolve, 0));
        input.value = 'Second';
        input.dispatchEvent(new Event('blur'));
        await new Promise((resolve) => setTimeout(resolve, 0));

        expect(input.value).toBe('First');
        expect(document.querySelector('[data-field="name"]').classList.contains('error')).toBe(true);
    });

    test('a failed save does not strand the next one behind it', async () => {
        fetch
            .mockRejectedValueOnce(new TypeError('Failed to fetch'))
            .mockResolvedValueOnce({ json: () => Promise.resolve({ success: true }) });
        initAutosave({ updateUrl: '/update/' });
        const input = document.querySelector('[data-field="name"] input');

        input.value = 'First';
        input.dispatchEvent(new Event('blur'));
        input.value = 'Second';
        input.dispatchEvent(new Event('blur'));
        await new Promise((resolve) => setTimeout(resolve, 0));

        expect(fetch).toHaveBeenCalledTimes(2);
        expect(document.querySelector('[data-field="name"]').classList.contains('saved')).toBe(true);
    });
});
