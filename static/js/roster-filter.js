/**
 * Shared as-you-type filter for character shelves (battle setup #270,
 * the parties add-member picker #274).
 *
 * Since #864 the shelves are searchable pickers whose search asks the
 * server (picker.js); picker.js calls `filterCharacters` to narrow the rows
 * already on screen while that answer is on its way. The declarative
 * wiring, any `<input data-filter-list="<ul id>">` present at load, still
 * works for a list that is whole on the page.
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
