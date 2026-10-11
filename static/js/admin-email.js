/**
 * An Admin sets another account's email address from its profile (#954).
 *
 * The address is not autosaved on blur: setting it sends a confirmation link
 * to the new address and a notice to the old one, so it waits for the Set
 * button (or Enter in the field). The save goes through autosave.js's
 * saveField, which posts to the manager path and shows a refusal inline,
 * where it stays until the next Set. A save that succeeds
 * reloads the page, so the unconfirmed state is read back from the account
 * rather than painted here.
 *
 * @param {object} options
 * @param {function(string, string): Promise<void>} options.saveField - From initAutosave.
 * @param {function(string, string): void} options.updateOriginalValue - From initAutosave.
 */
function initAdminEmail({ saveField, updateOriginalValue }) {
    const input = document.getElementById('id_email');
    const button = document.getElementById('email-set-button');
    const container = input && input.closest('[data-field="email"]');
    if (!input || !button || !container) return;

    // A refusal puts the field back to this, the address the page was drawn with.
    updateOriginalValue('email', input.value);

    async function setAddress() {
        // Disabled while a save is out, so two quick Enters post once.
        if (button.disabled) return;
        button.disabled = true;
        await saveField('email', input.value.trim());
        if (container.classList.contains('saved')) {
            pageNavigation.reload();
        } else {
            button.disabled = false;
        }
    }

    button.addEventListener('click', setAddress);
    input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            setAddress();
        }
    });
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { initAdminEmail };
}
