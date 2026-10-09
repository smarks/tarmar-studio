/**
 * Updates the preferred contact dropdown based on filled contact fields.
 * - Shows only options for filled contact methods
 * - Auto-selects if only one contact method is filled
 * - Disables dropdown if 0 or 1 contact methods filled
 *
 * @param {Function} onChangeCallback - Optional callback when value changes (for auto-save)
 */
function initPreferredContact(onChangeCallback) {
    const emailInput = document.getElementById('id_email');
    const phoneInput = document.getElementById('id_phone');
    const discordInput = document.getElementById('id_discord');
    const preferredSelect = document.getElementById('id_preferred_contact');

    if (!emailInput || !phoneInput || !discordInput || !preferredSelect) {
        return;
    }

    function updatePreferredContact() {
        const contacts = [];

        if (emailInput.value.trim()) contacts.push('email');
        if (phoneInput.value.trim()) contacts.push('phone');
        if (discordInput.value.trim()) contacts.push('discord');

        const options = preferredSelect.querySelectorAll('option');
        options.forEach(opt => {
            if (opt.value === '') {
                opt.style.display = '';
            } else {
                opt.style.display = contacts.includes(opt.value) ? '' : 'none';
            }
        });

        const oldValue = preferredSelect.value;
        if (contacts.length === 0) {
            preferredSelect.disabled = true;
            preferredSelect.value = '';
        } else if (contacts.length === 1) {
            preferredSelect.disabled = true;
            preferredSelect.value = contacts[0];
        } else {
            preferredSelect.disabled = false;
            if (!contacts.includes(preferredSelect.value)) {
                preferredSelect.value = '';
            }
        }

        if (oldValue !== preferredSelect.value && onChangeCallback) {
            onChangeCallback('preferred_contact', preferredSelect.value);
        }
    }

    emailInput.addEventListener('input', updatePreferredContact);
    phoneInput.addEventListener('input', updatePreferredContact);
    discordInput.addEventListener('input', updatePreferredContact);

    // A disabled field is left out of a form submission, so the contact this
    // chose for a player with only one method never reached the server and
    // registration saved none (issue #339, item 31). Re-enable it as the form
    // posts; the profile page saves on change and has no such submit.
    if (preferredSelect.form) {
        preferredSelect.form.addEventListener('submit', () => {
            preferredSelect.disabled = false;
        });
    }

    updatePreferredContact();
}
