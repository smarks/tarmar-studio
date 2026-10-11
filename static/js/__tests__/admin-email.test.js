/**
 * Unit tests for admin-email.js: an Admin setting another account's email
 * address from its profile (#954). The save itself is autosave.js's, so
 * saveField is a stand-in here that marks the container the way it does.
 */

const { initAdminEmail } = require('../admin-email.js');

function mountEmailField(value = 'old@example.test') {
    document.body.innerHTML = `
        <div class="field" id="profile-email" data-field="email">
            <input type="email" id="id_email" value="${value}">
            <button type="button" id="email-set-button">Set address</button>
        </div>
    `;
    return {
        container: document.getElementById('profile-email'),
        input: document.getElementById('id_email'),
        button: document.getElementById('email-set-button'),
    };
}

function setUp({ succeeds }) {
    const elements = mountEmailField();
    const saveField = jest.fn(async () => {
        elements.container.classList.add(succeeds ? 'saved' : 'error');
    });
    const updateOriginalValue = jest.fn();
    const reload = jest.fn();
    initAdminEmail({ saveField, updateOriginalValue, reload });
    return { ...elements, saveField, updateOriginalValue, reload };
}

test('remembers the drawn address so a refusal can put it back', () => {
    const { updateOriginalValue } = setUp({ succeeds: true });
    expect(updateOriginalValue).toHaveBeenCalledWith('email', 'old@example.test');
});

test('the button saves the trimmed address and reloads on success', async () => {
    const { input, button, saveField, reload } = setUp({ succeeds: true });
    input.value = '  new@example.test ';
    button.click();
    await Promise.resolve();
    await Promise.resolve();
    expect(saveField).toHaveBeenCalledWith('email', 'new@example.test');
    expect(reload).toHaveBeenCalledTimes(1);
    expect(button.disabled).toBe(true);
});

test('a refusal leaves the page as it is, button usable again', async () => {
    const { input, button, reload } = setUp({ succeeds: false });
    input.value = 'new@example.test';
    button.click();
    await Promise.resolve();
    await Promise.resolve();
    expect(reload).not.toHaveBeenCalled();
    expect(button.disabled).toBe(false);
});

test('Enter in the field sets the address, not blur', async () => {
    const { input, saveField } = setUp({ succeeds: true });
    input.value = 'new@example.test';
    input.dispatchEvent(new Event('blur'));
    expect(saveField).not.toHaveBeenCalled();
    const enter = new KeyboardEvent('keydown', { key: 'Enter', cancelable: true });
    input.dispatchEvent(enter);
    expect(enter.defaultPrevented).toBe(true);
    expect(saveField).toHaveBeenCalledWith('email', 'new@example.test');
});

test('a second Enter while the first save is out posts nothing', async () => {
    const { input, saveField, reload } = setUp({ succeeds: true });
    input.value = 'new@example.test';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    await Promise.resolve();
    await Promise.resolve();
    expect(saveField).toHaveBeenCalledTimes(1);
    expect(reload).toHaveBeenCalledTimes(1);
});

test('does nothing on a page without the set button', () => {
    document.body.innerHTML = '<input type="email" id="id_email" readonly>';
    const updateOriginalValue = jest.fn();
    initAdminEmail({ saveField: jest.fn(), updateOriginalValue });
    expect(updateOriginalValue).not.toHaveBeenCalled();
});
