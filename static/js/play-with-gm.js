/**
 * "Play with the Game Master" (issues #827, #828).
 *
 * The sheet and the home page render gm/_play_with_gm.html: a form carrying
 * the character's id, the attach endpoint and the chat page's address, and on
 * the sheet a choice between a new session and one of the viewer's own. This
 * posts that choice to gm:characters_attach -- which starts a session when it
 * is given none -- and opens the session the answer names.
 *
 * The endpoint speaks JSON only, so the template renders each form hidden
 * and this reveals it: a browser without the script shows no control rather
 * than one that silently reloads the page. A repaint that swaps in a fresh
 * slot (gm/_play_with_gm.html, id "play-with-gm-<character id>") calls
 * window.revealPlayWithGameMaster on it, which also wires the slot's session
 * picker (issue #973): a search box that asks the server for a page of the
 * viewer's sessions, so the select holds one page rather than every session.
 *
 * A refusal or a lost request is written into the form's own error line and
 * stays there until the next attempt: nothing here appears or goes away on a
 * timer.
 *
 * postJson comes from api.js, which base.html loads in <head>.
 */
(function (root) {
    'use strict';

    const NETWORK_ERROR =
        'The request did not reach the server. Check the connection and try again.';

    /**
     * The chat page opened on one session.
     * @param {string} chatUrl - The chat page's address.
     * @param {number|string} sessionId
     * @returns {string}
     */
    function sessionUrl(chatUrl, sessionId) {
        return `${chatUrl}?session=${encodeURIComponent(sessionId)}`;
    }

    /**
     * Say why the character did not go to the table, until the next attempt.
     * @param {HTMLFormElement} form
     * @param {string} message
     */
    function showRefusal(form, message) {
        const line = form.querySelector('.play-with-gm-error');
        line.textContent = message;
        line.hidden = false;
    }

    /**
     * Attach the form's character to the chosen session, or a new one, and
     * open it.
     * @param {HTMLFormElement} form - A form.play-with-gm.
     * @returns {Promise<boolean>} Whether the character reached a session.
     */
    async function takeToTable(form) {
        const button = form.querySelector('button[type="submit"]');
        const choice = form.querySelector('select[name="session_id"]');
        const line = form.querySelector('.play-with-gm-error');
        line.hidden = true;
        line.textContent = '';
        button.disabled = true;

        let answer;
        try {
            answer = await root.postJson(form.dataset.attachUrl, {
                session_id: choice && choice.value ? Number(choice.value) : null,
                character_id: Number(form.dataset.characterId),
            });
        } catch (error) {
            button.disabled = false;
            showRefusal(form, NETWORK_ERROR);
            return false;
        }
        if (!answer.ok || !answer.data.session_id) {
            button.disabled = false;
            showRefusal(
                form,
                answer.data.error
                    ? `The Game Master could not take this character: ${answer.data.error}.`
                    : `The server did not take the request (${answer.status} ${answer.statusText}).`
            );
            return false;
        }
        root.pageNavigation.go(sessionUrl(form.dataset.chatUrl, answer.data.session_id));
        return true;
    }

    //: Marks a document already listening, so loading this file twice (two
    //: includes, or a test's fresh require) cannot post one submit twice.
    const BOUND = Symbol.for('tarmar.play-with-gm.bound');

    /**
     * Show the play-with-gm forms under `container`, now that the script
     * that drives them is here, and wire the sheet's session picker in them
     * (issue #973; picker.js, which the sheet loads beside this).
     * @param {ParentNode} container - The document, or a repainted slot.
     */
    function revealPlayWithGameMaster(container) {
        container.querySelectorAll('form.play-with-gm').forEach((form) => {
            form.hidden = false;
        });
        if (root.picker) {
            root.picker.initPickers(container);
        }
    }

    /**
     * Send every play-with-gm form on the page through takeToTable, once.
     * @param {Document} doc
     */
    function bindPlayWithGameMaster(doc) {
        revealPlayWithGameMaster(doc);
        if (doc[BOUND]) { return; }
        doc[BOUND] = true;
        doc.addEventListener('submit', (event) => {
            const form = event.target.closest && event.target.closest('form.play-with-gm');
            if (!form) { return; }
            event.preventDefault();
            takeToTable(form);
        });
    }

    root.revealPlayWithGameMaster = revealPlayWithGameMaster;

    if (typeof document !== 'undefined') {
        bindPlayWithGameMaster(document);
    }

    // Export for testing (Node.js environment)
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            takeToTable, sessionUrl, bindPlayWithGameMaster, revealPlayWithGameMaster, NETWORK_ERROR,
        };
    }
})(typeof window !== 'undefined' ? window : globalThis);
