/**
 * Shared character-shelf filter (roster-filter.js): the filtering itself,
 * and the declarative data-filter-list wiring the parties page relies on.
 */

function loadRosterFilter() {
    let moduleExports;
    jest.isolateModules(() => {
        moduleExports = require('../roster-filter.js');
    });
    return moduleExports;
}

function buildList() {
    document.body.innerHTML = `
        <input type="search" id="party-character-filter" data-filter-list="party-addable-list">
        <ul id="party-addable-list">
            <li data-filter-text="morwen dmuser"></li>
            <li data-filter-text="aldwin playeruser"></li>
            <li data-filter-text="alda keeper"></li>
        </ul>
    `;
    return document.getElementById('party-addable-list');
}

describe('filterCharacters', () => {
    test('an empty query shows every row', () => {
        const list = buildList();
        const { filterCharacters } = loadRosterFilter();
        expect(filterCharacters(list, '')).toBe(3);
        expect(list.querySelectorAll('[hidden]')).toHaveLength(0);
    });

    test('matches case-insensitively with surrounding whitespace ignored', () => {
        const list = buildList();
        const { filterCharacters } = loadRosterFilter();
        expect(filterCharacters(list, '  ALD ')).toBe(2);
    });

    test('matches on the owner name half of the filter text', () => {
        const list = buildList();
        const { filterCharacters } = loadRosterFilter();
        expect(filterCharacters(list, 'keeper')).toBe(1);
    });

    test('clearing the query unhides previously hidden rows', () => {
        const list = buildList();
        const { filterCharacters } = loadRosterFilter();
        filterCharacters(list, 'morwen');
        expect(filterCharacters(list, '')).toBe(3);
        expect(list.querySelectorAll('[hidden]')).toHaveLength(0);
    });
});

describe('declarative wiring', () => {
    test('an input with data-filter-list filters its list as you type', () => {
        const list = buildList();
        loadRosterFilter(); // module load wires inputs present in the DOM
        const input = document.getElementById('party-character-filter');
        input.value = 'aldwin';
        input.dispatchEvent(new Event('input'));
        const hidden = [...list.querySelectorAll('[data-filter-text]')].map(
            (row) => row.hidden
        );
        expect(hidden).toEqual([true, false, true]);
    });

    test('an input naming a missing list is left unwired, not an error', () => {
        document.body.innerHTML =
            '<input data-filter-list="no-such-list">';
        const { initRosterFilters } = loadRosterFilter();
        expect(() => initRosterFilters()).not.toThrow();
    });
});
