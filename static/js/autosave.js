/**
 * Auto-save functionality for form fields.
 *
 * Usage:
 *   initAutosave({
 *     updateUrl: '/api/update/',
 *     onSuccess: (field, value, data) => { ... },  // optional callback
 *     validate: (field, value) => true | false | 'why', // optional validation;
 *                                                    // a string is shown as the error
 *     buildBody: (field, value) => ({ ... }),      // optional request body
 *   });
 *
 * buildBody exists because not every autosaving field posts to a generic
 * update-field endpoint (issue #339, item 10): the GM chat's session name
 * saves through gm_save, which is addressed by session id rather than by
 * field name. Rather than give the chat page a second, near-identical
 * autosave loop, the one loop lets a caller say what the body looks like.
 * The default is the {field, value} shape every existing caller sends.
 *
 * HTML structure:
 *   <div data-field="fieldname">
 *     <input class="autosave" ...>
 *   </div>
 *
 * postWithCsrf and csrfToken come from api.js, which base.html loads in
 * <head> — ahead of this file's <script> tag wherever a template places it.
 * Node/Jest has no shared script scope, so require api.js there; either way
 * the names end up on the global object.
 *
 * The token used to be an option every caller passed in, which made this the
 * fourth of five routes the CSRF token took to reach JavaScript (issue #338).
 * It comes from the page now, like everywhere else.
 *
 * The save goes through api.js's postForSuccess rather than postJson because
 * this saver draws a line postJson deliberately does not: a body that will
 * not parse as JSON is reported as a network error, not as the server
 * refusing the value. battle.js's roster rename draws the same line, and the
 * two share the helper (#338 item 1); Enter-to-commit is api.js's
 * bindCommitKeys, shared the same way (#338 item 2).
 */
if (typeof module !== 'undefined' && module.exports) {
    require('./api.js');
}

function initAutosave(options) {
    const { updateUrl, onSuccess, validate } = options;
    const buildBody = options.buildBody || ((field, value) => ({ field, value }));
    const originalValues = {};

    // Store original values
    document.querySelectorAll('.autosave').forEach(input => {
        const container = input.closest('[data-field]');
        if (!container) return;
        const field = container.dataset.field;
        if (input.type === 'checkbox') {
            originalValues[field] = input.checked;
        } else {
            originalValues[field] = input.value;
        }
    });

    // One field's saves are sent one at a time: the next waits for the one
    // before it to settle (issue #660). Nothing used to serialise them, and
    // the success callback was the only writer of originalValues, so a player
    // who blurred, refocused and blurred again quickly had two POSTs
    // outstanding; if the first answered last, originalValues ended up holding
    // the older value. The next blur then re-posted an unchanged value, and a
    // refusal reverted the input to a value the server did not have -- exactly
    // the state the "saved"/"error" marking exists to rule out. Serialising
    // also settles what the server holds: the writes land in the order the
    // player made them, so the last one sent is the one on the row.
    const pendingSave = {};

    function saveField(field, value) {
        const container = document.querySelector(`[data-field="${field}"]`);
        if (!container) return;

        // Marked when the request goes, not when it is queued, so a waiting
        // save does not clear the mark of the one in flight.
        const send = () => {
            container.classList.remove('saved', 'error');
            container.classList.add('saving');
            clearError(container);
            return postForSuccess(updateUrl, buildBody(field, value));
        };
        const inFlight = pendingSave[field];
        const attempt = (inFlight ? inFlight.then(send) : send())
        .then(({ succeeded, data }) => {
            container.classList.remove('saving');
            // data.success is the primary signal, the status the fallback for
            // endpoints that don't set it (postForSuccess decides). Either
            // failure path renders the inline error message — the user
            // shouldn't have to guess why the value didn't stick.
            if (succeeded) {
                container.classList.add('saved');
                originalValues[field] = value;

                if (onSuccess) {
                    onSuccess(field, value, data);
                }
                // No auto-dismiss: the "saved" state persists (state-driven) until
                // the field is edited again, when saveField() clears it.
            } else {
                container.classList.add('error');
                showError(container, data && data.error ? data.error : 'Save failed');
                revertField(container, field);
            }
        })
        .catch(() => {
            container.classList.remove('saving');
            container.classList.add('error');
            showError(container, FIELD_NETWORK_ERROR);
            revertField(container, field);
        });
        // `attempt` never rejects -- the catch above is the last link -- so a
        // failed save does not strand the next one behind a rejected chain.
        // The tail is dropped once it settles, so a field with nothing in
        // flight posts straight away rather than waiting a turn of the loop.
        pendingSave[field] = attempt;
        attempt.then(() => {
            if (pendingSave[field] === attempt) delete pendingSave[field];
        });
        return attempt;
    }

    // The inline error itself is api.js's showFieldError/clearFieldError --
    // battle.js's click-to-rename had written the same span, the same class
    // and the same role (issue #338 item 2). Clearing it here also drops the
    // container's `error` class, which is this module's own state and not
    // part of the shared idiom.
    const showError = showFieldError;

    // What a refused value says when its validator gives no reason of its own.
    const INVALID_VALUE_MESSAGE = 'That value cannot be saved.';

    function clearError(container) {
        clearFieldError(container);
        container.classList.remove('error');
    }

    function revertField(container, field) {
        const input = container.querySelector('input, select, textarea');
        if (!input) return;
        if (input.type === 'checkbox') {
            input.checked = originalValues[field];
        } else {
            input.value = originalValues[field];
        }
    }

    // Attach event listeners
    document.querySelectorAll('.autosave').forEach(input => {
        const container = input.closest('[data-field]');
        if (!container) return;
        const field = container.dataset.field;
        const isSelect = input.tagName === 'SELECT';

        // Clear stale error when the user re-engages the field to fix it.
        input.addEventListener('focus', () => clearError(container));
        input.addEventListener('input', () => clearError(container));

        if (input.type === 'checkbox') {
            input.addEventListener('change', function() {
                saveField(field, this.checked);
            });
        } else if (isSelect) {
            // Select elements save on change
            input.addEventListener('change', function() {
                const currentValue = this.value;
                if (currentValue !== originalValues[field]) {
                    saveField(field, currentValue);
                }
            });
        } else {
            // Text/number inputs save on blur
            input.addEventListener('blur', function() {
                const currentValue = this.value.trim();
                if (currentValue !== originalValues[field]) {
                    const verdict = validate ? validate(field, currentValue, this) : true;
                    if (verdict !== true) {
                        // Refused before saving: put the value back and say why,
                        // in the field's own error slot (issue #339, item 23). It
                        // used to revert with no word at all.
                        this.value = originalValues[field];
                        container.classList.add('error');
                        showError(
                            container,
                            typeof verdict === 'string' && verdict ? verdict : INVALID_VALUE_MESSAGE,
                        );
                    } else {
                        saveField(field, currentValue);
                    }
                }
            });

            // Save on Enter key -- except in a textarea, where Enter is a new
            // line (the party description, issue #339 item 17).
            bindCommitKeys(input);
        }
    });

    // Return helpers for external use
    return {
        saveField,
        updateOriginalValue: (field, value) => { originalValues[field] = String(value); }
    };
}

// Export for testing (Node.js environment)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { initAutosave };
}
