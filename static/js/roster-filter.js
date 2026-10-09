/**
 * Shared as-you-type filter for character shelves (battle setup #270,
 * the parties add-member picker #274).
 *
 * One function does the filtering; wiring comes in two flavours:
 * declaratively, any `<input data-filter-list="<ul id>">` present at load
 * is wired automatically (the parties page needs no page script of its
 * own), and programmatically via `filterCharacters` for pages that wire
 * inside their own init (battle-setup.js).
 */
(function (root) {
    /**
     * Show only the rows whose data-filter-text contains the query
     * (case-insensitive); returns how many stayed visible.
     */
    function filterCharacters(listElement, query) {
        const needle = (query || '').trim().toLowerCase();
        let shown = 0;
        listElement.querySelectorAll('[data-filter-text]').forEach((row) => {
            const matches = !needle || row.dataset.filterText.includes(needle);
            row.hidden = !matches;
            if (matches) { shown += 1; }
        });
        return shown;
    }

    /** Wire every input[data-filter-list] to the list its attribute names. */
    function initRosterFilters() {
        document.querySelectorAll('input[data-filter-list]').forEach((input) => {
            const listElement = document.getElementById(input.dataset.filterList);
            if (!listElement) { return; }
            input.addEventListener('input', () => {
                filterCharacters(listElement, input.value);
            });
        });
    }

    if (typeof document !== 'undefined') {
        initRosterFilters();
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { filterCharacters, initRosterFilters };
    }
    root.rosterFilter = { filterCharacters, initRosterFilters };
}(typeof window !== 'undefined' ? window : globalThis));
