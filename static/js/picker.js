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
 */
(function (root) {
    /** How long typing must pause before the server is asked. */
    const SEARCH_DELAY_MS = 200;

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
        const input = picker.querySelector('[data-picker-search]');
        const body = picker.querySelector('[data-picker-body]');
        const url = new URL(picker.dataset.pickerUrl, root.document.baseURI);
        url.searchParams.set('q', input ? input.value.trim() : '');
        url.searchParams.set('page', String(page));
        // Only the latest request may paint: a slow answer to an earlier
        // query must not replace the answer to the one typed since.
        const ticket = String(Number(picker.dataset.pickerTicket || '0') + 1);
        picker.dataset.pickerTicket = ticket;
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
            if (picker.dataset.pickerTicket !== ticket) return;
            showFailure(picker, error);
            return;
        }
        if (picker.dataset.pickerTicket !== ticket) return;
        body.innerHTML = html;
        // The page shown and the search it is a page of, so a refetch of
        // what holds the picker can ask for it again instead of falling back
        // to the first (issue #16). Not data-picker-page: that names the
        // pager's buttons, and the click handler finds them by it.
        picker.dataset.pickerShownPage = String(page);
        picker.dataset.pickerShownQuery = url.searchParams.get('q');
        if (focusDirection) focusPager(picker, focusDirection);
    }

    /**
     * The page a picker shows, as the page to ask for again when what holds
     * it is refetched (issue #16) or a row on it is deleted (issue #15): the
     * page last loaded, while the search box still holds the search it was
     * loaded for. A page of an earlier search is no page of the one typed
     * since, so then the first; and a picker never paged shows the first.
     *
     * @param {HTMLElement} picker - The `[data-picker]` element.
     * @returns {string}
     */
    function shownPage(picker) {
        const search = picker.querySelector('[data-picker-search]');
        const typed = search ? search.value.trim() : '';
        const sameSearch = (picker.dataset.pickerShownQuery || '') === typed;
        return sameSearch ? picker.dataset.pickerShownPage || '1' : '1';
    }

    /** Write a failed search into the picker's status line, to stay. */
    function showFailure(picker, error) {
        const status = picker.querySelector('[data-picker-status]');
        if (!status) return;
        status.textContent = `The search did not reach the server (${error.message}); `
            + 'type again to retry.';
    }

    /** Put the focus back on the pager after it turned a page. */
    function focusPager(picker, direction) {
        const buttons = picker.querySelectorAll('[data-picker-page]');
        if (!buttons.length) return;
        const wanted = direction === 'previous' ? buttons[0] : buttons[buttons.length - 1];
        const other = direction === 'previous' ? buttons[buttons.length - 1] : buttons[0];
        (wanted.disabled ? other : wanted).focus();
    }

    /** Narrow the rows already on screen, while the server is asked. */
    function narrowOnScreen(picker, query) {
        if (!root.rosterFilter) return;
        const body = picker.querySelector('[data-picker-body]');
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
        const input = picker.querySelector('[data-picker-search]');
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
            const button = event.target.closest('[data-picker-page]');
            if (!button || button.disabled || !button.dataset.pickerPage) return;
            event.preventDefault();
            const buttons = Array.from(picker.querySelectorAll('[data-picker-page]'));
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
        initPickers, initPicker, loadPage, shownPage, SEARCH_DELAY_MS,
    };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
    root.picker = api;
}(typeof window !== 'undefined' ? window : globalThis));
