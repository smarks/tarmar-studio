/**
 * Shared front-end helpers: the page's CSRF token, the POSTs that carry it,
 * the notify/confirm pair that wraps notify.js, the inline error a field wears
 * when its own save is refused, and the keys that commit or cancel an edit.
 *
 * These three functions were hand-copied across character-management.js,
 * chat.js, autosave.js, users_list.html and character_list.html (issue #111).
 * They live here now, and base.html loads this file in <head> so every page —
 * including the ones whose <script> tags sit inside {% block content %} — has
 * the names defined before any consumer script is parsed.
 *
 * Loading model: the functions are published onto the global object, the same
 * way notify.js publishes showNotice/confirmDialog. Consumer files therefore
 * call them as bare globals in the browser and, under Node/Jest (where there
 * is no shared script scope), require('./api.js') to populate those globals.
 */
(function (root) {
    /**
     * The page's CSRF token.
     *
     * base.html puts it in a <meta name="csrf-token"> in <head>, once, for
     * every page that extends it. Before issue #338 it reached JavaScript
     * five different ways -- interpolated into a string literal in four
     * templates, carried as a json_script config key by four views, hung on
     * elements as data-csrf in four more templates, read back out of a
     * rendered form's hidden input, and aliased to a module constant -- and
     * a page that used one of them owed nothing to the others. One meta tag
     * and this function are the whole of it now.
     *
     * The hidden-input fallback is for a fragment rendered without base.html
     * (and for a test fixture that builds only a form): if the meta tag is
     * absent but a CSRF-bearing form is on the page, its token is the same
     * token.
     *
     * @returns {string} The token, or '' if the page carries neither.
     */
    function csrfToken() {
        if (typeof document === 'undefined') { return ''; }
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) { return meta.content; }
        const field = document.querySelector('[name=csrfmiddlewaretoken]');
        return field ? field.value : '';
    }

    /**
     * POST with Django's CSRF header attached.
     *
     * Returns the raw Response, not parsed JSON: callers need the response
     * object itself (deleteCharacter reads `response.redirected`, chat.js's
     * streamMessage reads `response.body.getReader()` for SSE), so a
     * JSON-only helper could not serve them.
     *
     * @param {string} url - The endpoint URL
     * @param {string} csrfToken - CSRF token
     * @param {object|FormData|null} body - Optional body. A plain object is
     *   sent as JSON; a FormData is passed through untouched so the browser
     *   can set its own multipart Content-Type (with the boundary) — setting
     *   one here would corrupt the upload.
     * @returns {Promise<Response>}
     */
    async function postWithCsrf(url, csrfToken, body = null) {
        const options = {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrfToken
            }
        };
        if (body !== null) {
            if (typeof FormData !== 'undefined' && body instanceof FormData) {
                options.body = body;
            } else {
                options.headers['Content-Type'] = 'application/json';
                options.body = JSON.stringify(body);
            }
        }
        return fetch(url, options);
    }

    /**
     * POST a JSON body with the page's token and hand back the parsed
     * answer along with whether the server accepted it.
     *
     * Four call sites had grown their own version of exactly this -- the
     * autosave field saver, the battle action poster, the character sheet's
     * DM controls and the GM panel's tabs -- and they parsed the body four
     * slightly different ways. The parse is deliberately forgiving: a 500
     * that returns an HTML error page, or a 204 with no body at all, yields
     * an empty object rather than throwing, so callers can read
     * `data.error` without guarding every access. `parseError` is the error
     * the parse raised (null when the body was JSON), for a caller that must
     * tell an empty answer from an unreadable one: battle.js's action poster
     * reports a success status with an unreadable body as a failure, in the
     * parser's own words (#338 item 1).
     *
     * How a failure is *reported* is the caller's business and stays there:
     * a battle leaves a page notice, a GM panel writes into its own error
     * line, a field turns its own border red. This only says what happened.
     *
     * Network failures still reject, as fetch does -- a caller that wants to
     * survive an offline browser wraps the call in try/catch.
     *
     * @param {string} url - The endpoint URL
     * @param {object|null} [body] - Sent as JSON.
     * @returns {Promise<{ok: boolean, status: number, statusText: string, data: object, parseError: Error|null}>}
     */
    async function postJson(url, body = null) {
        const response = await postWithCsrf(url, csrfToken(), body);
        let data = {};
        let parseError = null;
        try {
            data = await response.json();
        } catch (error) {
            // No JSON body (an HTML error page, or a bodyless response).
            parseError = error;
        }
        return {
            ok: response.ok,
            status: response.status,
            statusText: response.statusText,
            data: data || {},
            parseError
        };
    }

    /**
     * POST an edit and say whether the server kept it.
     *
     * The field saver (autosave.js) and the roster rename (battle.js) had
     * each written this out (#338 item 1): the server's `success` flag
     * decides, and an endpoint that sets no flag is judged by its status.
     * Unlike postJson, a reply that will not parse as JSON rejects, together
     * with a network failure: both callers tell the user the request did not
     * get through, rather than that the server refused the value.
     *
     * @param {string} url - The endpoint URL
     * @param {object} body - Sent as JSON.
     * @returns {Promise<{succeeded: boolean, data: object}>} Rejects on a
     *   network failure or an unreadable reply.
     */
    async function postForSuccess(url, body) {
        const response = await postWithCsrf(url, csrfToken(), body);
        const data = await response.json();
        const succeeded = data.success === true
            || (data.success === undefined && response.ok);
        return { succeeded, data };
    }

    /**
     * The keys an inline edit answers to: Enter commits by leaving the field
     * (whose blur handler saves), and Escape, where the caller can cancel,
     * cancels and leaves it.
     *
     * autosave.js and battle.js's rename each bound this themselves (#338
     * item 2). Enter in a textarea is left alone, because there it is a new
     * line (the party description, issue #339 item 17).
     *
     * @param {HTMLInputElement|HTMLTextAreaElement} input
     * @param {{onCancel?: function(): void}} [options] - Called before the
     *   field is left on Escape; without it Escape does nothing.
     */
    function bindCommitKeys(input, options) {
        const onCancel = options && options.onCancel;
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && input.tagName !== 'TEXTAREA') {
                event.preventDefault();
                input.blur();
            } else if (event.key === 'Escape' && onCancel) {
                onCancel();
                input.blur();
            }
        });
    }

    /**
     * Where the caret and the selection are in a text field, so a field
     * drawn again in its place can be given them back (issue #16): a panel
     * refetch and the battle roster's redraw both replace a field the reader
     * is typing in. None for a field that has no caret (a button, a select).
     *
     * @param {HTMLInputElement|HTMLTextAreaElement} field
     * @returns {{start: number, end: number, direction: string}|null}
     */
    function readCaret(field) {
        if (field.selectionStart === null || field.selectionStart === undefined) {
            return null;
        }
        return {
            start: field.selectionStart,
            end: field.selectionEnd,
            direction: field.selectionDirection || 'none',
        };
    }

    /**
     * The control in ``scope`` that names itself ``key`` by data-focus-key.
     *
     * A control drawn again in place of one that had the keyboard focus
     * carries the same key, so the focus can be put back on it: battle.js's
     * turn panel (#646) and the GM side panel's refetched tabs (issue #16)
     * both find it here.
     *
     * @param {ParentNode} scope
     * @param {string} key
     * @returns {HTMLElement|null}
     */
    function elementByFocusKey(scope, key) {
        return Array.prototype.find.call(
            scope.querySelectorAll('[data-focus-key]'),
            (node) => node.dataset.focusKey === key
        ) || null;
    }

    /**
     * Put back a caret readCaret took. Nothing happens without one.
     *
     * @param {HTMLInputElement|HTMLTextAreaElement} field
     * @param {{start: number, end: number, direction: string}|null} caret
     */
    function putCaret(field, caret) {
        if (!caret) { return; }
        field.setSelectionRange(caret.start, caret.end, caret.direction);
    }

    /**
     * A modifier with its sign, the way the character sheet's tiles show one:
     * `+2`, `+0`, `-3`.
     *
     * character-edit.js's formatMod wrote zero as a bare `0`, while the page
     * the server rendered and attack-panel.js wrote `+0`, so a tile changed
     * from `+0` to `0` the first time equipment repainted it (#338 item 22).
     * This is the one browser-side definition; the template uses the matching
     * |signed filter. A missing value reads as zero.
     *
     * @param {number|null|undefined} value
     * @returns {string}
     */
    function formatSigned(value) {
        const number = value === undefined || value === null ? 0 : value;
        return number >= 0 ? `+${number}` : String(number);
    }

    /**
     * In-page notice, falling back to native alert() in the Node test
     * environment (where notify.js's window.showNotice isn't loaded).
     * @param {string} message
     * @param {string} [type] - Notice type; defaults to 'error'.
     */
    function uiNotify(message, type) {
        if (typeof window !== 'undefined' && window.showNotice) {
            window.showNotice(message, type || 'error');
        } else {
            alert(message);
        }
    }

    /**
     * In-page confirm, falling back to native confirm() in the Node test
     * environment (where notify.js's window.confirmDialog isn't loaded).
     * @param {string} message
     * @param {object} [options] - Passed through to confirmDialog.
     * @returns {Promise<boolean>}
     */
    function uiConfirm(message, options) {
        if (typeof window !== 'undefined' && window.confirmDialog) {
            return window.confirmDialog(message, options);
        }
        return Promise.resolve(confirm(message));
    }

    /**
     * In-page single-line prompt, falling back to native prompt() in the
     * Node test environment (where notify.js's window.promptDialog isn't
     * loaded). Resolves with the entered text, or null when the asker backs
     * out — the same two answers prompt() gives (issue #339, item 9).
     * @param {string} message
     * @param {object} [options] - Passed through to promptDialog.
     * @returns {Promise<string|null>}
     */
    function uiPrompt(message, options) {
        if (typeof window !== 'undefined' && window.promptDialog) {
            return window.promptDialog(message, options);
        }
        const settings = options || {};
        return Promise.resolve(prompt(message, settings.defaultValue || ''));
    }

    /**
     * The persistent inline error a single field wears when its save fails.
     *
     * The field-level counterpart to uiNotify's page-level notice, and it
     * follows the same house rule: no auto-dismiss. It stays until the
     * caller clears it -- when the user re-engages the field, or when a save
     * lands.
     *
     * autosave.js and battle.js's click-to-rename had each written this,
     * character for character, down to the class name and the role (issue
     * #338 item 2). The rename's refusals have been roster state since
     * issue #16, kept across the roster's redraws; they wear the same class. `.autosave-error` keeps its name because base.css styles
     * it under that name and both callers already wore it.
     *
     * The message goes in through textContent: it is often the server's own
     * words about the value the user typed.
     *
     * @param {Element} container - The element the error hangs inside.
     * @param {string} message
     */
    function showFieldError(container, message) {
        let errorNode = container.querySelector('.autosave-error');
        if (!errorNode) {
            errorNode = document.createElement('span');
            errorNode.className = 'autosave-error';
            errorNode.setAttribute('role', 'alert');
            container.appendChild(errorNode);
        }
        errorNode.textContent = message;
    }

    /**
     * Take the inline error off a field.
     * @param {Element} container - The element passed to showFieldError.
     */
    function clearFieldError(container) {
        const errorNode = container.querySelector('.autosave-error');
        if (errorNode) { errorNode.remove(); }
    }

    /**
     * Repaint a page's chrome after its record is renamed (issue #792).
     *
     * A rename saved the name and left the heading, the page title, the
     * breadcrumb and any "Back to" link on the old one until a reload. The
     * page marks each such place `data-name-chrome`; the breadcrumb, which
     * base.html renders for every page, is named by a selector.
     *
     * @param {string} name - The saved name.
     * @param {object} [options]
     * @param {string} [options.title] - The document title to set.
     * @param {string} [options.breadcrumb] - Selector for the breadcrumb
     *     step that carries the name.
     */
    function paintNameChrome(name, options) {
        const settings = options || {};
        document.querySelectorAll('[data-name-chrome]').forEach((element) => {
            element.textContent = name;
        });
        if (settings.breadcrumb) {
            const step = document.querySelector(settings.breadcrumb);
            if (step) step.textContent = name;
        }
        if (settings.title !== undefined) document.title = settings.title;
    }

    /**
     * A configured sentence in pieces: its own words, and each {name}'s value
     * as given (#94).
     *
     * The server's words are Python format templates it checks at startup
     * (battle/wording.py, settings._page_message_from_environment): bare
     * {name} fields and the {{ and }} escapes only, which is all this reads.
     * A name the caller gives no value for is a page bug, so it throws rather
     * than printing a brace to a player.
     *
     * @param {string} sentence - e.g. "Costs {cost}."
     * @param {Object<string, *>} [values] - each name's value
     * @returns {Array<*>} the words and the values, in order
     */
    function filledPieces(sentence, values) {
        const given = values || {};
        const pieces = [];
        let from = 0;
        sentence.replace(/\{\{|\}\}|\{(\w+)\}/g, (match, name, offset) => {
            pieces.push(sentence.slice(from, offset));
            from = offset + match.length;
            if (name === undefined) {
                pieces.push(match[0]);
            } else if (Object.hasOwn(given, name)) {
                pieces.push(given[name]);
            } else {
                throw new Error(`no value for {${name}} in "${sentence}"`);
            }
            return match;
        });
        pieces.push(sentence.slice(from));
        return pieces;
    }

    /**
     * A sentence the server configured, with its {names} filled (#94).
     * @param {string} sentence - e.g. "Costs {cost}."
     * @param {Object<string, *>} [values] - each name's value
     * @returns {string}
     */
    function filledWords(sentence, values) {
        return filledPieces(sentence, values).map(String).join('');
    }

    /**
     * Append a configured sentence to ``parent``, a value that is an element
     * (a name in bold) placed as itself and the rest as text (#94).
     * @param {Element} parent
     * @param {string} sentence - e.g. "Waiting for {name} to choose an action."
     * @param {Object<string, *>} [values] - each name's value or element
     */
    function appendFilledWords(parent, sentence, values) {
        filledPieces(sentence, values).forEach((piece) => {
            const isNode = typeof piece === 'object' && piece !== null && piece.nodeType;
            if (isNode) {
                parent.append(piece);
            } else if (String(piece)) {
                parent.append(String(piece));
            }
        });
    }

    // What a field says when the request never reached the server. Both
    // callers said this sentence; they say it from here now.
    const FIELD_NETWORK_ERROR = 'Network error — try again';

    /**
     * Every page leaves or reloads itself through these two calls, never by
     * touching window.location directly.
     *
     * The jsdom that Jest 30 ships (26) will not let a test replace
     * window.location or its methods, and it does not implement navigation, so
     * a script that sets location.href cannot be tested at all (#23).
     * jest.setup.js installs mocks under this name before any script loads;
     * that is why an existing pageNavigation is kept rather than replaced.
     */
    const pageNavigation = root.pageNavigation || {
        /** Reload the current page. */
        reload() { root.location.reload(); },
        /** Go to url, as a link would. @param {string} url */
        go(url) { root.location.assign(url); },
    };

    root.csrfToken = csrfToken;
    root.showFieldError = showFieldError;
    root.clearFieldError = clearFieldError;
    root.FIELD_NETWORK_ERROR = FIELD_NETWORK_ERROR;
    root.postWithCsrf = postWithCsrf;
    root.postJson = postJson;
    root.postForSuccess = postForSuccess;
    root.bindCommitKeys = bindCommitKeys;
    root.readCaret = readCaret;
    root.putCaret = putCaret;
    root.elementByFocusKey = elementByFocusKey;
    root.formatSigned = formatSigned;
    root.uiNotify = uiNotify;
    root.uiConfirm = uiConfirm;
    root.uiPrompt = uiPrompt;
    root.paintNameChrome = paintNameChrome;
    root.filledWords = filledWords;
    root.appendFilledWords = appendFilledWords;
    root.pageNavigation = pageNavigation;

    // Export for testing (Node.js environment)
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            csrfToken, postWithCsrf, postJson, postForSuccess, bindCommitKeys, readCaret, putCaret, elementByFocusKey, formatSigned,
            uiNotify, uiConfirm, uiPrompt, paintNameChrome, pageNavigation, filledWords, appendFilledWords,
            showFieldError, clearFieldError, FIELD_NETWORK_ERROR
        };
    }
})(typeof globalThis !== 'undefined' ? globalThis : window);
