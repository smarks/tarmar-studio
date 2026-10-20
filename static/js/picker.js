/**
 * Searchable pickers (issue #864): the search box asks the server.
 *
 * Every picker on a page is a `[data-picker]` element rendered from
 * templates/_picker.html: a search box, and a body holding one page of the
 * picker's rows, how many match, and a pager. Typing asks the picker's
 * endpoint (`data-picker-url`) for the body that matches what was typed, and
 * the pager asks it for another page; the answer replaces the body. The
 * server renders the body for the page's first load and for each search from
 * the same template, so nothing here builds a row.
 *
 * While the answer is on its way, rows already on screen that carry
 * `data-filter-text` are narrowed at once (roster-filter.js, when the page
 * loads it), so typing finds a row on this page without waiting; the server's
 * answer then brings in the rows from every other page.
 *
 * A failed search says so in the picker's status line, where it stays until
 * the next search succeeds; nothing here dismisses itself.
 *
 * What holds a picker can be fetched again while a page is on its way (the
 * GM page's Characters tab, while a turn runs), replacing the picker. The
 * page asked for is recorded on the picker before the request goes, so the
 * refetch asks the new picker for the page the reader turned to, and the
 * answer to the old picker is dropped unwritten (issue #14).
 */
(function (root) {
    /** How long typing must pause before the server is asked. */
    const SEARCH_DELAY_MS = 200;
    /**
     * Fired on a picker's body each time an answer is painted into it, for a
     * page that has to finish what the server's markup cannot (issue #62).
     */
    const PAINTED_EVENT = 'picker:painted';

    /** A picker's search box, its body, and its pager's buttons. */
    const SEARCH_BOX = '[data-picker-search]';
    const PICKER_BODY = '[data-picker-body]';
    const PAGER_BUTTON = '[data-picker-page]';

    /**
     * Ask a picker's endpoint for one page of results and show it.
     *
     * @param {HTMLElement} picker - The `[data-picker]` element.
     * @param {number|string} page - The page to fetch.
     * @param {string} [focusDirection] - After a pager press, which pager
     *   button ("previous" or "next") keeps the focus.
     * @returns {Promise<void>}
     */
    async function loadPage(picker, page, focusDirection) {
        const input = picker.querySelector(SEARCH_BOX);
        const url = new URL(picker.dataset.pickerUrl, root.document.baseURI);
        url.searchParams.set('q', input ? input.value.trim() : '');
        url.searchParams.set('page', String(page));
        // Only the latest request may paint: a slow answer to an earlier
        // query must not replace the answer to the one typed since.
        const ticket = String(Number(picker.dataset.pickerTicket || '0') + 1);
        picker.dataset.pickerTicket = ticket;
        // The page on its way, so a refetch of what holds the picker while it
        // is out asks for it again rather than for the page it replaces.
        picker.dataset.pickerAskedPage = String(page);
        picker.dataset.pickerAskedQuery = url.searchParams.get('q');
        let html;
        try {
            const response = await root.fetch(url.toString(), {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                credentials: 'same-origin',
            });
            if (!response.ok) {
                throw new Error(`the server answered ${response.status}`);
            }
            html = await response.text();
        } catch (error) {
            // A picker replaced meanwhile is not the reader's any more.
            if (!picker.isConnected || picker.dataset.pickerTicket !== ticket) return;
            delete picker.dataset.pickerAskedPage;
            delete picker.dataset.pickerAskedQuery;
            showFailure(picker, error);
            return;
        }
        if (!picker.isConnected || picker.dataset.pickerTicket !== ticket) return;
        delete picker.dataset.pickerAskedPage;
        delete picker.dataset.pickerAskedQuery;
        const body = picker.querySelector(PICKER_BODY);
        // The pager keeps the focus only from where the press left it: on
        // the pager, or lost with it (on the page's body, or on what holds
        // the picker, where a refetch puts a focus it could not place).
        const focused = root.document.activeElement;
        const focusFollows = Boolean(focusDirection) && (!focused
            || focused === root.document.body
            || body.contains(focused)
            || focused.contains(picker));
        body.innerHTML = html;
        // Rows arrive as <time> in UTC; local-time.js writes them in the
        // viewer's zone, as it did the rows the page loaded with (issue #62).
        if (typeof root.applyLocalTimes === 'function') root.applyLocalTimes(body);
        body.dispatchEvent(new root.CustomEvent(PAINTED_EVENT, { bubbles: true }));
        // The page shown and the search it is a page of, so a refetch of
        // what holds the picker can ask for it again instead of falling back
        // to the first (issue #16). Not data-picker-page: that names the
        // pager's buttons, and the click handler finds them by it.
        picker.dataset.pickerShownPage = String(page);
        picker.dataset.pickerShownQuery = url.searchParams.get('q');
        if (focusFollows) focusPager(picker, focusDirection);
    }

    /**
     * The page a picker shows, as the page to ask for again when what holds
     * it is refetched (issue #16) or a row on it is deleted (issue #15): the
     * page on its way, if the reader asked for one (issue #14), else the
     * page last loaded, while the search box still holds the search it was
     * asked for. A page of an earlier search is no page of the one typed
     * since, so then the first; and a picker never paged shows the first.
     *
     * @param {HTMLElement} picker - The `[data-picker]` element.
     * @returns {string}
     */
    function shownPage(picker) {
        const search = picker.querySelector(SEARCH_BOX);
        const typed = search ? search.value.trim() : '';
        const { dataset } = picker;
        if ('pickerAskedPage' in dataset && dataset.pickerAskedQuery === typed) {
            return dataset.pickerAskedPage;
        }
        const sameSearch = (dataset.pickerShownQuery || '') === typed;
        return sameSearch ? dataset.pickerShownPage || '1' : '1';
    }

    /** Write a failed search into the picker's status line, to stay. */
    function showFailure(picker, error) {
        const status = picker.querySelector('[data-picker-status]');
        if (!status) return;
        status.textContent = `The search did not reach the server (${error.message}); `
            + 'type again to retry.';
    }

    /**
     * Put the focus back on the pager after it turned a page: the button in
     * the direction pressed, else the other one, else the search box, so a
     * page with no pager to press does not leave the focus on the page's body.
     */
    function focusPager(picker, direction) {
        const buttons = picker.querySelectorAll(PAGER_BUTTON);
        const first = buttons[0];
        const last = buttons[buttons.length - 1];
        const pager = direction === 'previous' ? [first, last] : [last, first];
        [...pager, picker.querySelector(SEARCH_BOX)].some((candidate) => {
            if (!candidate || candidate.disabled) return false;
            candidate.focus();
            return root.document.activeElement === candidate;
        });
    }

    /** Narrow the rows already on screen, while the server is asked. */
    function narrowOnScreen(picker, query) {
        if (!root.rosterFilter) return;
        const body = picker.querySelector(PICKER_BODY);
        root.rosterFilter.filterCharacters(body, query);
    }

    /**
     * Wire one picker: typing searches, Enter searches at once (and never
     * submits the form the picker sits in), the pager turns pages.
     *
     * @param {HTMLElement} picker - The `[data-picker]` element.
     */
    function initPicker(picker) {
        if (picker.dataset.pickerReady === 'yes') return;
        picker.dataset.pickerReady = 'yes';
        const input = picker.querySelector(SEARCH_BOX);
        let timer = null;
        if (input) {
            input.addEventListener('input', () => {
                narrowOnScreen(picker, input.value);
                clearTimeout(timer);
                timer = setTimeout(() => loadPage(picker, 1), SEARCH_DELAY_MS);
            });
            input.addEventListener('keydown', (event) => {
                if (event.key !== 'Enter') return;
                event.preventDefault();
                clearTimeout(timer);
                loadPage(picker, 1);
            });
        }
        picker.addEventListener('click', (event) => {
            const button = event.target.closest(PAGER_BUTTON);
            if (!button || button.disabled || !button.dataset.pickerPage) return;
            event.preventDefault();
            const buttons = Array.from(picker.querySelectorAll(PAGER_BUTTON));
            const direction = buttons.indexOf(button) === 0 ? 'previous' : 'next';
            loadPage(picker, button.dataset.pickerPage, direction);
        });
    }

    /**
     * Wire every picker inside `scope` not wired yet. A page that swaps in
     * markup holding a picker (the GM panel's Characters tab) calls it again.
     *
     * @param {ParentNode} [scope]
     */
    function initPickers(scope) {
        (scope || root.document).querySelectorAll('[data-picker]').forEach(initPicker);
    }

    if (typeof document !== 'undefined') {
        initPickers(document);
    }

    const api = {
        initPickers, initPicker, loadPage, shownPage, SEARCH_DELAY_MS, PAINTED_EVENT,
    };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
    root.picker = api;
}(typeof window !== 'undefined' ? window : globalThis));
