/**
 * Unit tests for character-edit.js (issue #63).
 *
 * This module was ~1,100 lines of inline <script> in
 * characters/templates/characters/character_edit.html, which meant Jest could
 * not reach any of it: skill allocation, the experience-term modal and its
 * live log append, the equipment editor, the escaping that issue #120 fixed.
 * The e2e suite touched a slice of it; nothing else did.
 *
 * These tests exercise the extracted module the way the browser loads it: the
 * page's DOM and its #character-edit-config json_script block exist first,
 * api.js and autosave.js have already published their globals, then the module
 * is required and binds its handlers at that moment. `fetch` is mocked per
 * endpoint (the module reaches the network through postWithCsrf, which builds
 * a real fetch call, so asserting on fetch asserts on the wire format too).
 */

const CATALOG = {
    weapons: {
        dagger: { name: 'Dagger', category: 'weapons', weight: 1, cost: 2, damage: '1d4' },
        sword: { name: 'Sword', category: 'weapons', weight: 5, cost: 20, damage: '1d8' },
    },
    armor: {
        leather: { name: 'Leather Armor', category: 'armor', weight: 15, cost: 10, stops: 2 },
    },
    shields: {
        buckler: { name: 'Buckler', category: 'shield', weight: 3, cost: 5, stops: 1 },
    },
    gear: {
        rope: { name: 'Rope', category: 'gear', weight: 10, cost: 1 },
        // Deliberately hostile: this name lands in data-item-name, an
        // aria-label and the item-name span, all via innerHTML.
        cursed: { name: 'Rope "of\' <b>doom</b>', category: 'gear', weight: 2, cost: 1 },
    },
};

const CONFIG_DEFAULTS = {
    updateFieldUrl: '/tarmar/characters/7/update-field/',
    addYearsUrl: '/tarmar/characters/7/add-years/',
    trackLabels: { civil_service: 'Civil Service', merchant: 'Merchant', hunter_gatherer: 'Hunter/Gatherer' },
    allocateUrl: '/tarmar/characters/7/allocate-skill/',
    renameSkillUrl: '/tarmar/characters/7/rename-skill/',
    currentAge: 20,
    currentYears: 4,
    defaultRunYears: 1,
    maxRunYears: 20,
    survivalDice: '3d6',
    survivalTargets: { merchant: 4, campaigner: 6, laborer: 5 },
    agingByAge: { 36: { str: -1, dex: -1 }, 40: { str: -1, dex: -1, con: -1 } },
    agingHorizonAge: 96,
    agingEscalationNote: 'Continues to escalate past age 96 (roughly -1 to STR/DEX/CON every four years, plus -1 to INT/WIS every eight).',
    catalogUrl: '/tarmar/characters/equipment/catalog/',
    equipmentAddUrl: '/tarmar/characters/7/equipment/add/',
    equipmentUpdateUrl: '/tarmar/characters/7/equipment/update/',
    equipmentRemoveUrl: '/tarmar/characters/7/equipment/remove/',
    equipmentBulkRemoveUrl: '/tarmar/characters/7/equipment/bulk-remove/',
    equipmentTogglePackUrl: '/tarmar/characters/7/equipment/toggle-pack/',
    initialEquipment: [],
    packDropped: false,
    markDeadUrl: '/tarmar/characters/7/mark-dead/',
    reviveUrl: '/tarmar/characters/7/revive/',
    rollFatigueUrl: '/tarmar/characters/7/roll-fatigue/',
    rollLiteracyUrl: '/tarmar/characters/7/roll-literacy/',
    ruleLiteracyUrl: '/tarmar/characters/7/rule-literacy/',
    setPoolsUrl: '/tarmar/characters/7/set-pools/',
    canEdit: true,
    // The fixture is a Game Master's sheet: inputs for rolled values and item
    // modifiers. The player's shape is exercised below (issues #748, #752).
    canSetItemModifiers: true,
    setVisibilityUrl: '/tarmar/characters/7/set-visibility/',
    // Numbers no input would carry by chance, so a test that finds them
    // proves the script read them from here (issue #928).
    textLimits: {
        name: 101, skill_name: 61, item_name: 102, item_notes: 203,
        item_damage: 21, death_cause: 207, coin_reason: 103,
    },
    // Bounds no input would carry by chance either (issue #938).
    numberLimits: {
        equipment_quantity: 998, equipment_weight: 1003, equipment_modifier: 11,
        armor_stops: 12, armor_dex_penalty: 13, check_difficulty: 41,
        check_total_minimum: 3, check_total_maximum: 14, mana_grant: 101,
        defender_dodge_minimum: 2, defender_dodge_maximum: 15, situational: 21,
    },
};

/** A json-ish Response stand-in for the mocked fetch. */
const { jsonResponse, flush } = require('./test-helpers');

/**
 * Route fetch by URL: the catalog GET always resolves to CATALOG, everything
 * else takes the next queued response (or an empty success).
 */
function routeFetch(queue) {
    fetch.mockImplementation((url) => {
        if (url === CONFIG_DEFAULTS.catalogUrl) {
            return Promise.resolve(jsonResponse(CATALOG));
        }
        const next = queue.shift();
        return next
            ? Promise.resolve(next)
            : Promise.resolve(jsonResponse({ success: true }));
    });
}

/** Let queued microtasks (and the module's async init) run to completion. */
/** The parts of character_edit.html the module touches. */
function pageHtml(config) {
    return `
        <script id="character-edit-config" type="application/json">${JSON.stringify(config)}<\/script>

        <p class="dm-actions">
            <button type="button" id="mark-dead-btn" data-alive-only>Mark Dead</button>
            <button type="button" id="revive-btn" data-dead-only hidden>Revive</button>
        </p>

        <p class="visibility-control">
            <span id="visibility-badge" class="status-badge visibility-private">Private</span>
            <label class="visibility-toggle">
                <input type="checkbox" id="visibility-toggle">
                Public — any registered user can view this character
            </label>
        </p>

        <h1 class="editable-field name-field" data-field="name">
            <input type="text" id="id_name" value="Aldwin" class="autosave">
        </h1>
        <div class="editable-field" data-field="starting_coin"><input type="number" value="100" class="autosave"></div>
        <div class="editable-field" data-field="strength"><input type="number" value="12" class="autosave"></div>
        <div class="editable-field" data-field="dexterity"><input type="number" value="12" class="autosave"></div>
        <div class="editable-field" data-field="constitution"><input type="number" value="12" class="autosave"></div>

        <span id="fatigue">30</span><span id="body">20</span>
        <span id="fatigue-roll">7</span>
        <button type="button" id="roll-fatigue-btn">Roll Fatigue (2d6)</button>
        <button type="button" class="sheet-roll-btn" id="roll-wealth-btn" data-roll-kind="wealth" data-roll-url="/tarmar/characters/7/roll/wealth/">Roll wealth (d100)</button>
        <div class="editable-field" data-field="location">
            <select class="autosave" aria-label="Origin">
                <option value="City">City</option>
                <option value="Village" selected>Village</option>
                <option value="Rural">Rural</option>
                <option value="Consult GM">Consult GM</option>
            </select>
        </div>
        <span class="formula" id="origin-fixed-note" hidden>The Origin is fixed once literacy is rolled; only a Game Master can change it.</span>
        <span id="literacy-check">3d6 ≤ INT − 2</span>
        <span id="literacy-result">Not rolled</span>
        <button type="button" id="roll-literacy-btn">Roll literacy (3d6)</button>
        <span id="literacy-ruling-control" hidden>
            <select id="literacy-ruling-select" aria-label="Game Master's ruling">
                <option value="">Undecided</option>
                <option value="literate">Literate</option>
                <option value="illiterate">Illiterate</option>
            </select>
        </span>
        <span id="physical">18</span><span id="mental">18</span>
        <span id="height_display">(5'10")</span><span id="weight_lbs">170</span>
        <div id="injury-thresholds"><table class="injury-table"><tbody><tr>
            <td id="injury-fatigue-minus_one_at">15</td>
            <td id="injury-body-unconscious_at">0</td>
        </tr></tbody></table></div>

        <span id="character-level">2</span>
        <div class="editable-field" data-field="total_xp"><input type="number" value="1500" class="autosave"></div>
        <span id="character-age">20</span>
        <span id="experience-years">4</span>
        <span id="money-earned">200 gp</span>
        <span id="free-points">2</span>
        <span id="spell-points">0</span>
        <span id="allocated-points">3</span>

        <div id="workbench-card">
            <div class="workbench-card">
                <p class="workbench-eyebrow" id="workbench-eyebrow">NEXT — YEAR 5</p>
                <p class="workbench-copy">Each year rolls its own skill and its own survival check.</p>
                <div class="workbench-track">
                    <select id="track-select">
                        <option value="random">Random</option>
                        <option value="merchant" selected>Merchant</option>
                        <option value="campaigner">Campaigner</option>
                        <option value="laborer">Laborer</option>
                    </select>
                    <input type="number" id="years-input" value="1" min="1" max="20" step="1">
                </div>
                <dl class="workbench-preview">
                    <div class="workbench-preview-row"><dt>Age</dt><dd id="workbench-age-preview">20 → 21</dd></div>
                    <div class="workbench-preview-row"><dt>Survival</dt><dd id="workbench-survival-preview">3d6 + best attribute modifier ≥ 4</dd></div>
                    <div class="workbench-preview-row"><dt>Aging</dt><dd id="workbench-aging-preview">STR -1, DEX -1 at age 36</dd></div>
                </dl>
                <button id="roll-years-btn" class="button workbench-roll-btn">Roll 1 year</button>
            </div>
        </div>

        <div class="skills-list" id="skills-list">
            <div class="skill-row" data-skill="sword">
                <span class="skill-name" contenteditable="true" data-original="sword">Sword</span>
                <span class="skill-level">Level II</span>
                <span class="skill-points">(3 pts)</span>
                <button class="allocate-btn button secondary" data-skill="sword">+</button>
            </div>
        </div>
        <div class="add-skill-control">
            <input type="text" id="new-skill-name">
            <button id="add-skill-btn">Allocate Point</button>
        </div>

        <div id="combat-stats">
            <div class="value" id="armor-stops">0</div>
            <div class="value" id="armor-dex-penalty">0</div>
            <div class="value" id="dex-combat-mod">+0</div>
            <div class="value" id="dodge-modifier">+0</div>
            <div class="value" id="weapon-to-hit">+0</div>
            <div class="value" id="weapon-damage">+0</div>
            <div class="value" id="weapon-critical">+0</div>
        </div>

        <div class="encumbrance-bar" id="encumbrance-bar">
            <span id="carried-weight">0</span>
            <span id="max-unencumbered">12</span>
            <span class="level-badge load-1" id="encumbrance-level">Unencumbered</span>
            <span class="penalty" id="movement-penalty"></span>
            <span class="penalty" id="movement-restriction"></span>
            <input type="checkbox" id="pack-dropped">
            <span id="pack-weight">0</span>
        </div>

        <div class="gait-readout" id="gait-readout">
            <span id="movement-modifier">+0</span>
            <span class="gait" id="gait-walk"><span class="gait-value">10</span></span>
            <span class="gait" id="gait-run"><span class="gait-value">30</span></span>
        </div>

        <div class="equipment-controls">
            <input type="search" id="equipment-filter">
            <select id="equipment-sort">
                <option value="" selected>Default</option>
                <option value="name">Name</option>
                <option value="weight_asc">Weight up</option>
                <option value="weight_desc">Weight down</option>
                <option value="category">Category</option>
            </select>
            <button type="button" id="equipment-bulk-delete" disabled>
                Delete Selected (<span id="equipment-bulk-count">0</span>)
            </button>
        </div>
        <div class="equipment-list" id="equipment-list"></div>

        <div id="attack-panel">
            <div class="attack-form" id="attack-form" hidden>
                <select id="attack-weapon"></select>
            </div>
            <p id="attack-no-weapons">No weapon here can be resolved.</p>
            <p id="attack-gm-adjudicated" hidden>Not listed: <span id="attack-gm-adjudicated-names"></span></p>
        </div>

        <div class="add-equipment-control">
            <select id="equipment-category">
                <option value="" selected>Category...</option>
                <option value="weapons">Weapons</option>
                <option value="gear">Gear</option>
            </select>
            <select id="equipment-item" disabled><option value="">Select item...</option></select>
            <input type="checkbox" id="add-in-pack">
            <button id="add-equipment-btn" disabled>Add Item</button>
            <button id="add-custom-btn">Add Custom...</button>
        </div>

        <div id="custom-item-panel" class="inline-panel" style="display: none;">
            <div class="panel-content">
                <input type="text" id="custom-name">
                <select id="custom-category">
                    <option value="gear" selected>Gear</option>
                    <option value="weapons">Weapon</option>
                    <option value="armor">Armor</option>
                    <option value="shield">Shield</option>
                </select>
                <input type="number" id="custom-weight" value="0">
                <input type="number" id="custom-cost" value="0">
                <div id="custom-weapon-fields" style="display: none;">
                    <input type="text" id="custom-damage" value="1d6">
                </div>
                <div id="custom-armor-fields" style="display: none;">
                    <input type="number" id="custom-stops" value="0">
                    <input type="number" id="custom-dex-penalty" value="0">
                </div>
                <input type="checkbox" id="custom-in-pack">
                <input type="text" id="custom-notes">
                <button id="custom-cancel-btn">Cancel</button>
                <button id="custom-add-btn">Add Item</button>
            </div>
        </div>

        <div class="experience-history-column stats-section" id="experience-history-section">
            <div id="experience-log">
                <p class="no-history">No terms yet. Roll the first term to build this character's past.</p>
            </div>
        </div>

        <template id="death-card-template">
            <div class="death-card">
                <h3><span data-name-chrome></span> died</h3>
                <p class="death-cause"></p>
                <p class="fatal-roll" hidden></p>
                <p class="death-note">A DM can resurrect this character.</p>
            </div>
        </template>

        <form method="post" id="delete-character-form">
            <input type="hidden" name="csrfmiddlewaretoken" value="form-token">
            <button type="submit">Delete Character</button>
        </form>
    `;
}

/**
 * The panels createPanel() opened (issue #190 replaced the overlay modals
 * with persistent panels in the notice region).
 */
function openedModals() {
    return [...document.querySelectorAll('.notice-panel')];
}

// jsdom does not implement HTMLElement.contentEditable, so it reads undefined
// and the delegated rename handler's `contentEditable === 'true'` guard could
// never pass. Reflect the attribute, which is what a browser does.
if (!('contentEditable' in HTMLElement.prototype)) {
    Object.defineProperty(HTMLElement.prototype, 'contentEditable', {
        get() {
            return this.getAttribute('contenteditable') || 'inherit';
        },
        configurable: true,
    });
}

let characterEdit;
const { FIELD_NETWORK_ERROR, formatSigned } = require('../api.js');

/**
 * Build the page, publish the globals its <script> tags would have published,
 * then require the module — the same order the browser uses.
 * @param {object} [config] - Values merged into the config block.
 * @param {object[]} [queue] - Responses the non-catalog fetches consume.
 */
async function loadPage(config = {}, queue = []) {
    jest.resetModules();
    document.body.innerHTML = pageHtml({ ...CONFIG_DEFAULTS, ...config });
    // autosave.js's <script> tag defines initAutosave as a page global; Node
    // has no shared script scope, so hand it over explicitly.
    global.initAutosave = require('../autosave.js').initAutosave;
    routeFetch(queue);
    characterEdit = require('../character-edit.js');
    await flush();
    return characterEdit;
}

beforeEach(() => {
    fetch.mockReset();
    alert.mockClear();
    confirm.mockClear();
    confirm.mockReturnValue(true);
    localStorage.clear();
});

describe('escapeHtml', () => {
    beforeEach(async () => { await loadPage(); });

    test('escapes every character that can break out of markup or an attribute', () => {
        expect(characterEdit.escapeHtml('<a href="x" onclick=\'y\'>&</a>')).toBe(
            '&lt;a href=&quot;x&quot; onclick=&#39;y&#39;&gt;&amp;&lt;/a&gt;'
        );
    });

    test('escapes the ampersand first, so nothing double-decodes', () => {
        // &lt; must survive as a literal, not become <.
        expect(characterEdit.escapeHtml('&lt;script&gt;')).toBe(
            '&amp;lt;script&amp;gt;'
        );
    });

    test('quotes are escaped, which the pre-#120 textContent trick did not do', () => {
        expect(characterEdit.escapeHtml('" onmouseover="alert(1)')).not.toContain('"');
        expect(characterEdit.escapeHtml("' onmouseover='alert(1)")).not.toContain("'");
    });

    test('null and undefined become the empty string, not "null"', () => {
        expect(characterEdit.escapeHtml(null)).toBe('');
        expect(characterEdit.escapeHtml(undefined)).toBe('');
    });

    test('non-strings are stringified', () => {
        expect(characterEdit.escapeHtml(42)).toBe('42');
        expect(characterEdit.escapeHtml(0)).toBe('0');
        expect(characterEdit.escapeHtml(false)).toBe('false');
    });

    test('an escaped value round-trips back to the original as text', () => {
        const raw = '<img src=x onerror="alert(1)">O\'Brien & Sons';
        const host = document.createElement('div');
        host.innerHTML = `<span title="${characterEdit.escapeHtml(raw)}">${characterEdit.escapeHtml(raw)}</span>`;
        const span = host.querySelector('span');
        expect(span.textContent).toBe(raw);
        expect(span.getAttribute('title')).toBe(raw);
        expect(host.querySelector('img')).toBeNull();
    });
});

// What buildAllocateButton and buildSkillRow promised about a skill row -- the
// + button's rules, escaping, the rename affordances, title case -- is the
// server partial's job now (characters/_skill_row.html), and
// characters/tests/test_skill_and_term_partials.py holds it there (#338 item 3).

describe('page load', () => {
    test('renders the initial equipment from the config block', async () => {
        await loadPage({
            initialEquipment: [
                { item_id: 'sword', quantity: 2 },
                { item_id: 'rope', quantity: 1, in_pack: true, notes: 'coiled' },
            ],
        });

        const rows = document.querySelectorAll('#equipment-list .equipment-row');
        expect(rows).toHaveLength(2);
        expect(rows[0].querySelector('.item-name').textContent).toBe('Sword');
        // 5 lbs each, quantity 2.
        expect(rows[0].querySelector('.item-weight').textContent).toBe('10.0 lbs');
        expect(rows[1].classList.contains('in-pack')).toBe(true);
        expect(rows[1].querySelector('[data-field="notes"]').value).toBe('coiled');
    });

    test('fetches the catalog exactly once for the initial render', async () => {
        await loadPage({ initialEquipment: [{ item_id: 'rope' }] });
        const catalogCalls = fetch.mock.calls.filter(
            ([url]) => url === CONFIG_DEFAULTS.catalogUrl
        );
        expect(catalogCalls).toHaveLength(1);
    });

    test('shows the empty-state message when there is no equipment', async () => {
        await loadPage({ initialEquipment: [] });
        expect(document.querySelector('#equipment-list .no-equipment')).not.toBeNull();
    });

    test('escapes a hostile catalog name everywhere it lands in the row', async () => {
        await loadPage({ initialEquipment: [{ item_id: 'cursed', quantity: 1 }] });
        const row = document.querySelector('.equipment-row');
        expect(row.querySelector('b')).toBeNull();
        expect(row.querySelector('.item-name').textContent).toBe(CATALOG.gear.cursed.name);
        expect(row.dataset.itemName).toBe(CATALOG.gear.cursed.name.toLowerCase());
        expect(row.querySelector('.row-select').getAttribute('aria-label')).toBe(
            `Select ${CATALOG.gear.cursed.name} for bulk delete`
        );
    });

    test('a custom item is labelled and priced from its own fields', async () => {
        await loadPage({
            initialEquipment: [
                { item_id: 'custom', name: 'Heirloom Blade', category: 'weapons', weight: 4, quantity: 1 },
            ],
        });
        const row = document.querySelector('.equipment-row');
        expect(row.querySelector('.item-name').textContent).toContain('Heirloom Blade');
        expect(row.querySelector('.item-name').innerHTML).toContain('<em>(custom)</em>');
        expect(row.querySelector('.item-weight').textContent).toBe('4.0 lbs');
        // Weapons get the to-hit / damage / crit modifier fields.
        expect(row.querySelector('[data-field="to_hit_mod"]')).not.toBeNull();
    });

    // This used to assert a shield row had no equipped control at all, which
    // was issue #804: with no way to say which shield is in use, every shield
    // counted. A shield row now carries the one "In use" choice (a radio,
    // one shield at a time); armour keeps its checkbox.
    test('armor rows carry an equipped checkbox, shields an "In use" choice', async () => {
        await loadPage({
            initialEquipment: [{ item_id: 'leather' }, { item_id: 'buckler' }],
            shieldInUseIndex: 1,
        });
        const [armorRow, shieldRow] = document.querySelectorAll('.equipment-row');
        expect(armorRow.querySelector('[data-field="equipped"]').type).toBe('checkbox');
        expect(armorRow.querySelector('[data-field="stops_mod"]')).not.toBeNull();
        const inUse = shieldRow.querySelector('[data-field="equipped"]');
        expect(inUse.type).toBe('radio');
        expect(inUse.checked).toBe(true);
        expect(shieldRow.querySelector('[data-field="dex_mod"]')).not.toBeNull();
    });

    test('a dropped pack marks the rows that are inside it', async () => {
        await loadPage({
            packDropped: true,
            initialEquipment: [{ item_id: 'rope', in_pack: true }, { item_id: 'dagger' }],
        });
        const [packed, carried] = document.querySelectorAll('.equipment-row');
        expect(packed.classList.contains('pack-dropped')).toBe(true);
        expect(carried.classList.contains('pack-dropped')).toBe(false);
    });

    test('restores the sort persisted in localStorage', async () => {
        localStorage.setItem('tarmar.equipment.sort', 'weight_desc');
        await loadPage({
            initialEquipment: [{ item_id: 'dagger' }, { item_id: 'leather' }],
        });
        expect(document.getElementById('equipment-sort').value).toBe('weight_desc');
        const names = [...document.querySelectorAll('.item-name')].map((n) => n.textContent);
        expect(names).toEqual(['Leather Armor', 'Dagger']);
    });
});

describe('equipment sorting and filtering', () => {
    const EQUIPMENT = [
        { item_id: 'rope' },      // Rope, gear, 10
        { item_id: 'dagger' },    // Dagger, weapons, 1
        { item_id: 'leather' },   // Leather Armor, armor, 15
    ];

    async function loadSorted(sortValue) {
        await loadPage({ initialEquipment: EQUIPMENT });
        const select = document.getElementById('equipment-sort');
        select.value = sortValue;
        select.dispatchEvent(new Event('change'));
        await flush();
        return [...document.querySelectorAll('.item-name')].map((n) => n.textContent);
    }

    test('default order is entry order', async () => {
        await loadPage({ initialEquipment: EQUIPMENT });
        expect([...document.querySelectorAll('.item-name')].map((n) => n.textContent)).toEqual([
            'Rope', 'Dagger', 'Leather Armor',
        ]);
    });

    test('sorts by name', async () => {
        expect(await loadSorted('name')).toEqual(['Dagger', 'Leather Armor', 'Rope']);
    });

    test('sorts by weight ascending and descending', async () => {
        expect(await loadSorted('weight_asc')).toEqual(['Dagger', 'Rope', 'Leather Armor']);
        expect(await loadSorted('weight_desc')).toEqual(['Leather Armor', 'Rope', 'Dagger']);
    });

    test('sorts by category, then name within it', async () => {
        expect(await loadSorted('category')).toEqual(['Leather Armor', 'Rope', 'Dagger']);
    });

    test('sorting keeps each row bound to its original index', async () => {
        await loadSorted('name');
        const byName = {};
        document.querySelectorAll('.equipment-row').forEach((row) => {
            byName[row.querySelector('.item-name').textContent] = row.dataset.index;
        });
        // Rope was entry 0 and must still delete entry 0 after the re-sort.
        expect(byName.Rope).toBe('0');
        expect(byName.Dagger).toBe('1');
        expect(byName['Leather Armor']).toBe('2');
    });

    test('the sort choice is persisted for the next page load', async () => {
        await loadSorted('name');
        expect(localStorage.getItem('tarmar.equipment.sort')).toBe('name');
    });

    test('filtering hides the rows that do not match', async () => {
        await loadPage({ initialEquipment: EQUIPMENT });
        const filter = document.getElementById('equipment-filter');
        filter.value = 'rope';
        filter.dispatchEvent(new Event('input'));

        const hidden = [...document.querySelectorAll('.equipment-row')].filter((r) =>
            r.classList.contains('is-hidden')
        );
        expect(hidden).toHaveLength(2);
        expect(characterEdit.applyEquipmentFilter()).toBe(1);
    });

    test('an empty filter shows everything again', async () => {
        await loadPage({ initialEquipment: EQUIPMENT });
        const filter = document.getElementById('equipment-filter');
        filter.value = 'rope';
        filter.dispatchEvent(new Event('input'));
        filter.value = '';
        filter.dispatchEvent(new Event('input'));
        expect(document.querySelectorAll('.equipment-row.is-hidden')).toHaveLength(0);
    });

    test('the bulk-delete button counts only visible, checked rows', async () => {
        await loadPage({ initialEquipment: EQUIPMENT });
        const button = document.getElementById('equipment-bulk-delete');
        const counter = document.getElementById('equipment-bulk-count');
        expect(button.disabled).toBe(true);

        const boxes = document.querySelectorAll('.row-select');
        boxes[0].checked = true;
        boxes[1].checked = true;
        boxes[0].dispatchEvent(new Event('change', { bubbles: true }));
        expect(counter.textContent).toBe('2');
        expect(button.disabled).toBe(false);

        // Filtering to Rope hides the other checked row, so it stops counting.
        const filter = document.getElementById('equipment-filter');
        filter.value = 'rope';
        filter.dispatchEvent(new Event('input'));
        expect(counter.textContent).toBe('1');
    });
});

describe('findItemInCatalog and formatSigned', () => {
    beforeEach(async () => { await loadPage(); });

    test('finds an item in any of the four catalog sections', () => {
        expect(characterEdit.findItemInCatalog(CATALOG, 'dagger').name).toBe('Dagger');
        expect(characterEdit.findItemInCatalog(CATALOG, 'leather').name).toBe('Leather Armor');
        expect(characterEdit.findItemInCatalog(CATALOG, 'buckler').name).toBe('Buckler');
        expect(characterEdit.findItemInCatalog(CATALOG, 'rope').name).toBe('Rope');
    });

    test('returns null for an unknown id rather than throwing', () => {
        expect(characterEdit.findItemInCatalog(CATALOG, 'nonesuch')).toBeNull();
        expect(characterEdit.findItemInCatalog({}, 'dagger')).toBeNull();
    });

    test('signs a modifier, and leaves zero unsigned', () => {
        // character-edit.js's own formatMod wrote zero as "0" while the
        // server-rendered tiles said "+0"; api.js's formatSigned is the one
        // formatter now, matching the page (#338 item 22).
        expect(formatSigned(2)).toBe('+2');
        expect(formatSigned(-2)).toBe('-2');
        expect(formatSigned(0)).toBe('+0');
        expect(formatSigned(undefined)).toBe('+0');
    });

    test('annotates an unknown item with its raw id and zero weight', () => {
        const [annotated] = characterEdit.annotateForSort([{ item_id: 'ghost' }], CATALOG);
        expect(annotated.name).toBe('ghost');
        expect(annotated.weight).toBe(0);
        expect(annotated.category).toBe('');
    });
});

describe('updateEquipmentDisplay', () => {
    const RESPONSE = {
        success: true,
        carried_weight: 22,
        pack_weight: 10,
        // A canon's key of two words: the badge prints the server's wording.
        encumbrance_level: 'half laden', encumbrance_label: 'Half Laden',
        encumbrance_badge_classes: 'load-3',
        movement_penalty: -2,
        movement_restriction_label: 'No running',
        movement_modifier: -1,
        gait_readout: [
            { key: 'walk', na: false, distance: 8 },
            { key: 'run', na: true, distance: 0 },
        ],
        armor_stops: 2,
        armor_dex_penalty: -1,
        dex_modifier: 2,
        dodge_modifier: 0,
        weapon_to_hit_mod: 1,
        weapon_damage_mod: 0,
        weapon_critical_mod: -1,
        pack_dropped: true,
        equipment: [{ item_id: 'dagger' }],
    };

    beforeEach(async () => { await loadPage(); });

    test('writes the encumbrance bar, badge and penalties', async () => {
        characterEdit.updateEquipmentDisplay(RESPONSE);
        await flush();

        expect(document.getElementById('carried-weight').textContent).toBe('22');
        expect(document.getElementById('pack-weight').textContent).toBe('10');
        const badge = document.getElementById('encumbrance-level');
        expect(badge.textContent).toBe('Half Laden');
        // The classes are the server's, by the level's place (issue #75).
        expect(badge.className).toBe('level-badge load-3');
        expect(document.getElementById('movement-penalty').textContent).toBe('-2 movement');
        expect(document.getElementById('movement-penalty').style.display).toBe('');
        expect(document.getElementById('movement-restriction').textContent).toBe('No running');
    });

    test('hides the penalty and restriction when there are none', async () => {
        characterEdit.updateEquipmentDisplay({
            ...RESPONSE,
            movement_penalty: 0,
            movement_restriction_label: '',
        });
        await flush();
        expect(document.getElementById('movement-penalty').style.display).toBe('none');
        expect(document.getElementById('movement-restriction').style.display).toBe('none');
    });

    test('writes the server-computed gaits, marking the unavailable ones', async () => {
        characterEdit.updateEquipmentDisplay(RESPONSE);
        await flush();

        expect(document.getElementById('movement-modifier').textContent).toBe('-1');
        const walk = document.getElementById('gait-walk');
        expect(walk.querySelector('.gait-value').textContent).toBe('8');
        expect(walk.classList.contains('gait-na')).toBe(false);
        const run = document.getElementById('gait-run');
        expect(run.querySelector('.gait-value').textContent).toBe('n/a');
        expect(run.classList.contains('gait-na')).toBe(true);
    });

    test('signs the combat stats and classes them positive or negative', async () => {
        characterEdit.updateEquipmentDisplay(RESPONSE);
        await flush();

        expect(document.getElementById('armor-stops').textContent).toBe('2');
        expect(document.getElementById('armor-dex-penalty').className).toBe('value negative');
        expect(document.getElementById('weapon-to-hit').textContent).toBe('+1');
        expect(document.getElementById('weapon-to-hit').className).toBe('value positive');
        // A zero modifier keeps the "+0" the page loaded with (#338 item 22).
        expect(document.getElementById('weapon-damage').textContent).toBe('+0');
        expect(document.getElementById('weapon-damage').className).toBe('value');
        expect(document.getElementById('weapon-critical').textContent).toBe('-1');
        expect(document.getElementById('weapon-critical').className).toBe('value negative');
    });

    test('restates aim and dodge, which nothing used to repaint (#634)', async () => {
        characterEdit.updateEquipmentDisplay(RESPONSE);
        await flush();

        expect(document.getElementById('dex-combat-mod').textContent).toBe('+2');
        expect(document.getElementById('dex-combat-mod').className).toBe('value positive');
        // Dodge never goes below 0, and prints its plus like the template.
        expect(document.getElementById('dodge-modifier').textContent).toBe('+0');
        expect(document.getElementById('dodge-modifier').className).toBe('value');
    });

    test('an unknown aim or dodge is an em dash, not a number (#630)', async () => {
        characterEdit.updateEquipmentDisplay({
            ...RESPONSE, dex_modifier: null, dodge_modifier: null,
        });
        await flush();

        expect(document.getElementById('dex-combat-mod').textContent).toBe('\u2014');
        expect(document.getElementById('dex-combat-mod').className).toBe('value');
        expect(document.getElementById('dodge-modifier').textContent).toBe('\u2014');
    });

    test('a response carrying neither leaves both tiles as they were', async () => {
        document.getElementById('dex-combat-mod').textContent = '+3';
        const { dex_modifier, dodge_modifier, ...withoutThem } = RESPONSE;
        characterEdit.updateEquipmentDisplay(withoutThem);
        await flush();

        expect(document.getElementById('dex-combat-mod').textContent).toBe('+3');
    });

    test('syncs the pack-dropped checkbox from the server response', async () => {
        characterEdit.updateEquipmentDisplay(RESPONSE);
        await flush();
        expect(document.getElementById('pack-dropped').checked).toBe(true);
    });

    test('refreshes the attack weapon select from the server list (#339 item 24)', async () => {
        characterEdit.updateEquipmentDisplay({
            ...RESPONSE,
            attack_weapons: [
                { index: 1, item_id: 'saber', name: 'Saber', weapon_class: 'Striking', damage: '2d6-2' },
                { index: 3, item_id: 'dagger', name: 'Dagger', weapon_class: 'Piercing', damage: '1d6-1' },
            ],
            attack_gm_adjudicated_weapons: ['Club'],
        });
        await flush();

        const options = [...document.querySelectorAll('#attack-weapon option')];
        expect(options.map((option) => option.value)).toEqual(['1', '3']);
        expect(options[1].textContent).toBe('Dagger (Piercing, 1d6-1)');
        expect(options[1].dataset.itemId).toBe('dagger');
        expect(document.getElementById('attack-form').hidden).toBe(false);
        expect(document.getElementById('attack-no-weapons').hidden).toBe(true);
        expect(document.getElementById('attack-gm-adjudicated').hidden).toBe(false);
        expect(document.getElementById('attack-gm-adjudicated-names').textContent).toBe('Club');
    });

    test('keeps the chosen weapon selected when it survives the refresh', async () => {
        // The saber is in hand (#846); the player's pick of the dagger holds.
        const weapons = [
            { index: 0, item_id: 'saber', name: 'Saber', weapon_class: 'Striking', damage: '2d6-2', in_hand: true },
            { index: 1, item_id: 'dagger', name: 'Dagger', weapon_class: 'Piercing', damage: '1d6-1' },
        ];
        characterEdit.refreshAttackWeapons({ attack_weapons: weapons });
        document.getElementById('attack-weapon').value = '1';
        characterEdit.refreshAttackWeapons({ attack_weapons: weapons, attack_gm_adjudicated_weapons: [] });
        expect(document.getElementById('attack-weapon').value).toBe('1');
    });

    test('hides the form and shows the note when no weapon is left', async () => {
        characterEdit.refreshAttackWeapons({
            attack_weapons: [{ index: 0, item_id: 'saber', name: 'Saber', weapon_class: 'Striking', damage: '2d6-2' }],
        });
        characterEdit.refreshAttackWeapons({ attack_weapons: [], attack_gm_adjudicated_weapons: [] });

        expect(document.querySelectorAll('#attack-weapon option')).toHaveLength(0);
        expect(document.getElementById('attack-form').hidden).toBe(true);
        expect(document.getElementById('attack-no-weapons').hidden).toBe(false);
        expect(document.getElementById('attack-gm-adjudicated').hidden).toBe(true);
    });

    test('a response without attack weapons leaves the panel alone', async () => {
        characterEdit.refreshAttackWeapons({});
        expect(document.getElementById('attack-form').hidden).toBe(true);
    });

    test('rebuilds the equipment list from the response', async () => {
        characterEdit.updateEquipmentDisplay(RESPONSE);
        await flush();
        expect(document.querySelectorAll('.equipment-row')).toHaveLength(1);
        expect(document.querySelector('.item-name').textContent).toBe('Dagger');
    });
});

describe('equipment mutations', () => {
    const OK = {
        success: true,
        carried_weight: 1, pack_weight: 0, encumbrance_level: 'unencumbered', encumbrance_label: 'Unencumbered', encumbrance_badge_classes: 'load-1',
        movement_penalty: 0, movement_restriction_label: '', movement_modifier: 0,
        gait_readout: [], armor_stops: 0, armor_dex_penalty: 0,
        weapon_to_hit_mod: 0, weapon_damage_mod: 0, weapon_critical_mod: 0,
        pack_dropped: false, equipment: [{ item_id: 'dagger' }],
    };

    test('the category picker fills the item list, sorted by name', async () => {
        await loadPage();
        const category = document.getElementById('equipment-category');
        category.value = 'weapons';
        category.dispatchEvent(new Event('change'));
        await flush();

        const itemSelect = document.getElementById('equipment-item');
        expect(itemSelect.disabled).toBe(false);
        expect([...itemSelect.options].map((o) => o.textContent)).toEqual([
            'Select item...', 'Dagger (1 lbs)', 'Sword (5 lbs)',
        ]);
    });

    test('clearing the category disables the item picker and the add button', async () => {
        await loadPage();
        const category = document.getElementById('equipment-category');
        category.value = 'weapons';
        category.dispatchEvent(new Event('change'));
        await flush();
        category.value = '';
        category.dispatchEvent(new Event('change'));
        await flush();

        expect(document.getElementById('equipment-item').disabled).toBe(true);
        expect(document.getElementById('add-equipment-btn').disabled).toBe(true);
    });

    test('adding an item posts the id and pack flag with the CSRF header', async () => {
        await loadPage({}, [jsonResponse(OK)]);
        const itemSelect = document.getElementById('equipment-item');
        itemSelect.innerHTML = '<option value="dagger" selected>Dagger</option>';
        itemSelect.dispatchEvent(new Event('change'));
        document.getElementById('add-in-pack').checked = true;
        document.getElementById('add-equipment-btn').click();
        await flush();

        expect(fetch).toHaveBeenCalledWith(CONFIG_DEFAULTS.equipmentAddUrl, {
            method: 'POST',
            headers: { 'X-CSRFToken': 'test-token', 'Content-Type': 'application/json' },
            // No "Give free" box on a player's page, so the gift flag is false
            // (issue #757; the server decides who may give anyway).
            body: JSON.stringify({ item_id: 'dagger', in_pack: true, free: false }),
        });
        expect(document.querySelector('.item-name').textContent).toBe('Dagger');
    });

    test("a purchase writes the server's purse into both readouts", async () => {
        await loadPage({}, [jsonResponse({ ...OK, purse: '90' })]);
        document.body.insertAdjacentHTML(
            'beforeend',
            '<strong id="purse">100</strong><strong id="equipment-purse">100</strong>'
        );
        const itemSelect = document.getElementById('equipment-item');
        itemSelect.innerHTML = '<option value="dagger" selected>Dagger</option>';
        itemSelect.dispatchEvent(new Event('change'));
        document.getElementById('add-equipment-btn').click();
        await flush();

        expect(document.getElementById('purse').textContent).toBe('90');
        expect(document.getElementById('equipment-purse').textContent).toBe('90');
    });

    test('a rejected add surfaces the server error and leaves the list alone', async () => {
        await loadPage({}, [jsonResponse({ success: false, error: 'Too heavy' })]);
        const itemSelect = document.getElementById('equipment-item');
        itemSelect.innerHTML = '<option value="dagger" selected>Dagger</option>';
        itemSelect.dispatchEvent(new Event('change'));
        document.getElementById('add-equipment-btn').click();
        await flush();

        expect(alert).toHaveBeenCalledWith('Too heavy');
        expect(document.querySelector('.no-equipment')).not.toBeNull();
        // The choice is still in the selects, so the retry is one click: the
        // button used to be disabled here, and only the item select's change
        // listener re-enables it, so the whole picker had to be re-driven
        // (issue #659).
        expect(document.getElementById('add-equipment-btn').disabled).toBe(false);
    });

    test('a successful add leaves the button waiting for the next pick (#659)', async () => {
        await loadPage({}, [jsonResponse(OK)]);
        const itemSelect = document.getElementById('equipment-item');
        itemSelect.innerHTML = '<option value="dagger" selected>Dagger</option>';
        itemSelect.dispatchEvent(new Event('change'));
        document.getElementById('add-equipment-btn').click();
        await flush();

        // The form was reset, so nothing is selected to add.
        expect(document.getElementById('add-equipment-btn').disabled).toBe(true);
    });

    test('a network failure on add says so and leaves the button usable (#659)', async () => {
        await loadPage();
        fetch.mockRejectedValueOnce(new TypeError('Failed to fetch'));
        const itemSelect = document.getElementById('equipment-item');
        itemSelect.innerHTML = '<option value="dagger" selected>Dagger</option>';
        itemSelect.dispatchEvent(new Event('change'));
        document.getElementById('add-equipment-btn').click();
        await flush();

        expect(alert).toHaveBeenCalledWith('Error: Failed to fetch');
        expect(document.getElementById('add-equipment-btn').disabled).toBe(false);
    });

    test('editing a row field posts the index and just that field', async () => {
        await loadPage({ initialEquipment: [{ item_id: 'dagger', quantity: 1 }] }, [jsonResponse(OK)]);
        const qty = document.querySelector('[data-field="quantity"]');
        qty.value = '3';
        qty.dispatchEvent(new Event('change', { bubbles: true }));
        await flush();

        expect(fetch).toHaveBeenCalledWith(CONFIG_DEFAULTS.equipmentUpdateUrl, {
            method: 'POST',
            headers: { 'X-CSRFToken': 'test-token', 'Content-Type': 'application/json' },
            body: JSON.stringify({ index: 0, quantity: 3 }),
        });
    });

    test('a saved row field is marked saved on the re-rendered row (#339 item 22)', async () => {
        await loadPage(
            { initialEquipment: [{ item_id: 'dagger', quantity: 1 }] },
            [jsonResponse({ ...OK, equipment: [{ item_id: 'dagger', quantity: 3 }] })]
        );
        const qty = document.querySelector('[data-field="quantity"]');
        qty.value = '3';
        qty.dispatchEvent(new Event('change', { bubbles: true }));
        await flush();
        await flush();

        const fresh = document.querySelector('.equipment-row[data-index="0"] [data-field="quantity"]');
        expect(fresh.value).toBe('3');
        expect(fresh.parentElement.classList.contains('saved')).toBe(true);
        expect(alert).not.toHaveBeenCalled();
    });

    test('a refused row field shows the reason beside it and puts the value back', async () => {
        await loadPage(
            { initialEquipment: [{ item_id: 'dagger', quantity: 2 }] },
            [jsonResponse({ success: false, error: 'Quantity too large' }, { ok: false })]
        );
        const qty = document.querySelector('[data-field="quantity"]');
        qty.value = '5000';
        qty.dispatchEvent(new Event('change', { bubbles: true }));
        await flush();

        expect(alert).not.toHaveBeenCalled();
        expect(qty.parentElement.classList.contains('error')).toBe(true);
        expect(qty.parentElement.querySelector('.autosave-error').textContent).toBe('Quantity too large');
        expect(qty.value).toBe('2');
    });

    test('a row checkbox that fails to reach the server is unticked again', async () => {
        await loadPage({ initialEquipment: [{ item_id: 'dagger' }] });
        fetch.mockImplementation(() => Promise.reject(new Error('offline')));
        const pack = document.querySelector('[data-field="in_pack"]');
        pack.checked = true;
        pack.dispatchEvent(new Event('change', { bubbles: true }));
        await flush();

        expect(pack.checked).toBe(false);
        expect(pack.parentElement.querySelector('.autosave-error').textContent).toBe(FIELD_NETWORK_ERROR);
    });

    test('a checkbox field posts a boolean, not a string', async () => {
        await loadPage({ initialEquipment: [{ item_id: 'dagger' }] }, [jsonResponse(OK)]);
        const pack = document.querySelector('[data-field="in_pack"]');
        pack.checked = true;
        pack.dispatchEvent(new Event('change', { bubbles: true }));
        await flush();

        expect(JSON.parse(fetch.mock.calls.at(-1)[1].body)).toEqual({ index: 0, in_pack: true });
    });

    test('removing a row posts its index', async () => {
        await loadPage({ initialEquipment: [{ item_id: 'dagger' }, { item_id: 'rope' }] }, [
            jsonResponse(OK),
        ]);
        document.querySelectorAll('.remove-btn')[1].click();
        await flush();

        expect(confirm).toHaveBeenCalledWith('Remove Rope?');
        expect(JSON.parse(fetch.mock.calls.at(-1)[1].body)).toEqual({ index: 1 });
    });

    test('removing a row does nothing when the confirmation is declined', async () => {
        await loadPage({ initialEquipment: [{ item_id: 'dagger' }, { item_id: 'rope' }] });
        confirm.mockReturnValue(false);
        const callsBefore = fetch.mock.calls.length;
        document.querySelectorAll('.remove-btn')[1].click();
        await flush();

        expect(fetch.mock.calls.length).toBe(callsBefore);
        expect(document.querySelectorAll('.remove-btn').length).toBe(2);
    });

    test('toggling the pack posts with no body at all', async () => {
        await loadPage({}, [jsonResponse(OK)]);
        const toggle = document.getElementById('pack-dropped');
        toggle.checked = true;
        toggle.dispatchEvent(new Event('change'));
        await flush();

        expect(fetch).toHaveBeenCalledWith(CONFIG_DEFAULTS.equipmentTogglePackUrl, {
            method: 'POST',
            headers: { 'X-CSRFToken': 'test-token' },
        });
    });

    test('a failed pack toggle puts the checkbox back where it was', async () => {
        await loadPage({}, [jsonResponse({ success: false, error: 'Nope' })]);
        const toggle = document.getElementById('pack-dropped');
        toggle.checked = true;
        toggle.dispatchEvent(new Event('change'));
        await flush();

        expect(alert).toHaveBeenCalledWith('Nope');
        expect(toggle.checked).toBe(false);
    });

    test('bulk delete confirms, then posts the selected indices', async () => {
        await loadPage(
            { initialEquipment: [{ item_id: 'dagger' }, { item_id: 'rope' }, { item_id: 'leather' }] },
            [jsonResponse(OK)]
        );
        const boxes = document.querySelectorAll('.row-select');
        boxes[0].checked = true;
        boxes[2].checked = true;
        boxes[0].dispatchEvent(new Event('change', { bubbles: true }));
        document.getElementById('equipment-bulk-delete').click();
        await flush();

        expect(confirm).toHaveBeenCalledWith('Delete 2 selected item(s)?');
        expect(JSON.parse(fetch.mock.calls.at(-1)[1].body)).toEqual({ indices: [0, 2] });
    });

    test('bulk delete does nothing when the confirmation is declined', async () => {
        confirm.mockReturnValue(false);
        await loadPage({ initialEquipment: [{ item_id: 'dagger' }] });
        document.querySelector('.row-select').checked = true;
        document.querySelector('.row-select').dispatchEvent(new Event('change', { bubbles: true }));
        fetch.mockClear();
        document.getElementById('equipment-bulk-delete').click();
        await flush();

        expect(fetch).not.toHaveBeenCalled();
    });

    test('bulk delete with nothing selected never asks and never posts', async () => {
        await loadPage({ initialEquipment: [{ item_id: 'dagger' }] });
        fetch.mockClear();
        document.getElementById('equipment-bulk-delete').click();
        await flush();

        expect(confirm).not.toHaveBeenCalled();
        expect(fetch).not.toHaveBeenCalled();
    });
});

describe('custom item panel', () => {
    test('opens on the Add Custom button and closes on Cancel', async () => {
        await loadPage();
        const panel = document.getElementById('custom-item-panel');
        document.getElementById('add-custom-btn').click();
        // A block in the page, not a centred overlay (issue #190).
        expect(panel.style.display).toBe('block');

        document.getElementById('custom-cancel-btn').click();
        expect(panel.style.display).toBe('none');
    });

    test('a click on the panel does not discard the form being filled in', async () => {
        await loadPage();
        const panel = document.getElementById('custom-item-panel');
        document.getElementById('add-custom-btn').click();

        panel.dispatchEvent(new Event('click', { bubbles: true }));
        expect(panel.style.display).toBe('block');
    });

    test('shows the damage field for weapons and the armour fields for armour', async () => {
        await loadPage();
        const category = document.getElementById('custom-category');
        const weaponFields = document.getElementById('custom-weapon-fields');
        const armorFields = document.getElementById('custom-armor-fields');

        category.value = 'weapons';
        category.dispatchEvent(new Event('change'));
        expect(weaponFields.style.display).toBe('block');
        expect(armorFields.style.display).toBe('none');

        category.value = 'shield';
        category.dispatchEvent(new Event('change'));
        expect(weaponFields.style.display).toBe('none');
        expect(armorFields.style.display).toBe('flex');
    });

    test('refuses to submit a nameless item', async () => {
        await loadPage();
        fetch.mockClear();
        document.getElementById('custom-add-btn').click();
        await flush();

        expect(alert).toHaveBeenCalledWith('Please enter an item name');
        expect(fetch).not.toHaveBeenCalled();
    });

    test('posts a custom weapon with its damage string', async () => {
        await loadPage({}, [jsonResponse({
            success: true, carried_weight: 0, pack_weight: 0,
            encumbrance_level: 'unencumbered', encumbrance_label: 'Unencumbered', encumbrance_badge_classes: 'load-1', movement_penalty: 0,
            movement_restriction_label: '', movement_modifier: 0, gait_readout: [],
            armor_stops: 0, armor_dex_penalty: 0, weapon_to_hit_mod: 0,
            weapon_damage_mod: 0, weapon_critical_mod: 0, pack_dropped: false,
            equipment: [],
        })]);
        document.getElementById('custom-name').value = '  Heirloom Blade  ';
        document.getElementById('custom-category').value = 'weapons';
        document.getElementById('custom-weight').value = '4.5';
        document.getElementById('custom-cost').value = '30';
        document.getElementById('custom-damage').value = '1d10';
        document.getElementById('custom-notes').value = 'family sword';
        document.getElementById('custom-add-btn').click();
        await flush();

        expect(JSON.parse(fetch.mock.calls.at(-1)[1].body)).toEqual({
            is_custom: true,
            name: 'Heirloom Blade',
            category: 'weapons',
            weight: 4.5,
            cost: 30,
            in_pack: false,
            notes: 'family sword',
            damage: '1d10',
        });
        expect(document.getElementById('custom-item-panel').style.display).toBe('none');
    });

    test('posts stops and dex penalty for custom armour', async () => {
        await loadPage({}, [jsonResponse({ success: false, error: 'nope' })]);
        document.getElementById('custom-name').value = 'Scale';
        document.getElementById('custom-category').value = 'armor';
        document.getElementById('custom-stops').value = '3';
        document.getElementById('custom-dex-penalty').value = '-2';
        document.getElementById('custom-add-btn').click();
        await flush();

        const body = JSON.parse(fetch.mock.calls.at(-1)[1].body);
        expect(body.stops).toBe(3);
        expect(body.dex_penalty).toBe(-2);
        expect(body.damage).toBeUndefined();
        // A rejection keeps the modal open so the values can be corrected.
        expect(alert).toHaveBeenCalledWith('nope');
    });

    test('resetCustomForm puts every field back to its default', async () => {
        await loadPage();
        document.getElementById('custom-name').value = 'x';
        document.getElementById('custom-category').value = 'armor';
        document.getElementById('custom-weight').value = '9';
        document.getElementById('custom-in-pack').checked = true;
        document.getElementById('custom-notes').value = 'note';

        characterEdit.resetCustomForm();

        expect(document.getElementById('custom-name').value).toBe('');
        expect(document.getElementById('custom-category').value).toBe('gear');
        expect(document.getElementById('custom-weight').value).toBe('0');
        expect(document.getElementById('custom-damage').value).toBe('1d6');
        expect(document.getElementById('custom-in-pack').checked).toBe(false);
        expect(document.getElementById('custom-notes').value).toBe('');
        expect(document.getElementById('custom-weapon-fields').style.display).toBe('none');
    });
});

describe('updateSkillsUI', () => {
    // The list as characters/_skills_list.html renders it (#338 item 3).
    const SKILLS_HTML = `
            <div class="skill-row" data-skill="sword">
                <span class="skill-name" contenteditable="true" role="textbox" aria-label="Rename skill" data-original="sword">Sword</span>
                <span class="skill-level">Level II (+1)</span>
                <span class="skill-points">(5 pts)</span>
                <button class="allocate-btn button secondary" data-skill="sword">+</button>
            </div>
            <div class="skill-row" data-skill="arcane">
                <span class="skill-name" contenteditable="true" role="textbox" aria-label="Rename skill" data-original="arcane">Arcane</span>
                <span class="skill-level">Level I (+2)</span>
                <span class="skill-points">(4 pts)</span>
                <button class="allocate-btn button secondary" data-skill="arcane">+</button>
            </div>`;
    const CHARACTER = {
        age: 24,
        experience_years: 2,
        total_xp: 2500,
        level: 3,
        money_earned: 450,
        free_skill_points: 1,
        free_spell_points: 2,
        total_allocated_points: 8,
        is_alive: true,
        skills: {
            sword: { points: 5, display: 'II (+1)' },
            arcane: { points: 4, display: 'I (+2)' },
        },
        skills_html: SKILLS_HTML,
    };

    beforeEach(async () => { await loadPage(); });

    test('writes every summary figure the header shows', () => {
        characterEdit.updateSkillsUI(CHARACTER);
        expect(document.getElementById('character-age').textContent).toBe('24');
        expect(document.getElementById('experience-years').textContent).toBe('2');
        expect(document.querySelector('[data-field="total_xp"] input').value).toBe('2500');
        expect(document.getElementById('character-level').textContent).toBe('3');
        expect(document.getElementById('money-earned').textContent).toBe('450 gp');
        expect(document.getElementById('free-points').textContent).toBe('1');
        expect(document.getElementById('spell-points').textContent).toBe('2');
        expect(document.getElementById('allocated-points').textContent).toBe('8');
    });

    test('repaints the attributes and the derived pair a run moves (#638)', async () => {
        // A run can grant +1 STR/DEX/CON and can cross an aging stage; the
        // response has carried the attributes since #163 and nothing read
        // them, so these five cells stayed at their pre-run values.
        characterEdit.updateSkillsUI({
            ...CHARACTER, strength: 13, dexterity: 14, constitution: 11,
            physical: 19, mental: 17,
        });

        expect(document.querySelector('[data-field="strength"] input').value).toBe('13');
        expect(document.querySelector('[data-field="dexterity"] input').value).toBe('14');
        expect(document.querySelector('[data-field="constitution"] input').value).toBe('11');
        expect(document.getElementById('physical').textContent).toBe('19');
        expect(document.getElementById('mental').textContent).toBe('17');
    });

    test('a repainted attribute is not re-posted on the next blur (#638)', async () => {
        characterEdit.updateSkillsUI({ ...CHARACTER, strength: 13 });
        fetch.mockClear();

        const input = document.querySelector('[data-field="strength"] input');
        input.dispatchEvent(new Event('blur'));
        await flush();

        expect(fetch).not.toHaveBeenCalled();
    });

    test('an allocation response, which carries none of them, leaves them alone', () => {
        characterEdit.updateSkillsUI(CHARACTER);

        expect(document.querySelector('[data-field="strength"] input').value).toBe('12');
        expect(document.getElementById('physical').textContent).toBe('18');
    });

    test('puts the server-rendered list in place, rows and order as sent', () => {
        characterEdit.updateSkillsUI(CHARACTER);
        const rows = document.querySelectorAll('#skills-list .skill-row');
        expect([...rows].map((r) => r.dataset.skill)).toEqual(['sword', 'arcane']);
        expect(rows[1].querySelector('.skill-level').textContent).toBe('Level I (+2)');
        expect(rows[1].querySelector('.skill-points').textContent).toBe('(4 pts)');
    });

    test('an empty list from the server replaces the old rows', () => {
        characterEdit.updateSkillsUI({
            ...CHARACTER,
            skills: {},
            skills_html: '<p class="no-skills">No skills yet. Roll experience years to gain skills.</p>',
        });
        expect(document.querySelector('#skills-list .no-skills')).not.toBeNull();
        expect(document.querySelectorAll('.skill-row')).toHaveLength(0);
    });

    test('a response without the rendered list leaves the rows alone', () => {
        const before = document.getElementById('skills-list').innerHTML;
        const { skills_html: _unused, ...withoutList } = CHARACTER;
        characterEdit.updateSkillsUI(withoutList);
        expect(document.getElementById('skills-list').innerHTML).toBe(before);
    });

    test('hides the add control once points run out', () => {
        characterEdit.updateSkillsUI({ ...CHARACTER, free_skill_points: 0 });
        expect(document.querySelector('.add-skill-control').style.display).toBe('none');
    });

    test('a dead character is offered no add control even with points left', () => {
        characterEdit.updateSkillsUI({ ...CHARACTER, is_alive: false });
        expect(document.querySelector('.add-skill-control').style.display).toBe('none');
    });

    test('tolerates a page with no spell-points element', () => {
        document.getElementById('spell-points').remove();
        expect(() => characterEdit.updateSkillsUI(CHARACTER)).not.toThrow();
    });
});

describe('allocateSkillPoint', () => {
    /** The row as characters/_skill_row.html renders it, attributes escaped. */
    function rowHtml(name, title, display, points, { withButton = true } = {}) {
        const attribute = name.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
        const button = withButton
            ? `<button class="allocate-btn button secondary" data-skill="${attribute}">+</button>`
            : '';
        return `<div class="skill-row" data-skill="${attribute}">
            <span class="skill-name" contenteditable="true" role="textbox" aria-label="Rename skill" data-original="${attribute}">${title}</span>
            <span class="skill-level">Level ${display}</span>
            <span class="skill-points">(${points} pts)</span>
            ${button}
        </div>`;
    }

    test('replaces an existing row with the one the server rendered', async () => {
        await loadPage({}, [jsonResponse({
            success: true, skill: 'sword', display: 'III', points: 6,
            free_skill_points: 1, total_allocated_points: 6,
            row_html: rowHtml('sword', 'Sword', 'III', 6),
        })]);
        document.querySelector('.allocate-btn').click();
        await flush();

        const rows = document.querySelectorAll('.skill-row[data-skill="sword"]');
        expect(rows).toHaveLength(1);
        expect(rows[0].querySelector('.skill-level').textContent).toBe('Level III');
        expect(rows[0].querySelector('.skill-points').textContent).toBe('(6 pts)');
        expect(document.getElementById('free-points').textContent).toBe('1');
        expect(document.getElementById('allocated-points').textContent).toBe('6');
        expect(rows[0].querySelector('.allocate-btn')).not.toBeNull();
    });

    test('posts the skill name with the CSRF header', async () => {
        await loadPage({}, [jsonResponse({
            success: true, skill: 'sword', display: 'III', points: 6,
            free_skill_points: 1, total_allocated_points: 6,
            row_html: rowHtml('sword', 'Sword', 'III', 6),
        })]);
        document.querySelector('.allocate-btn').click();
        await flush();

        expect(fetch).toHaveBeenCalledWith(CONFIG_DEFAULTS.allocateUrl, {
            method: 'POST',
            headers: { 'X-CSRFToken': 'test-token', 'Content-Type': 'application/json' },
            body: JSON.stringify({ skill: 'sword' }),
        });
    });

    test('adds the rendered row for a skill the character did not have', async () => {
        await loadPage({}, [jsonResponse({
            success: true, skill: 'shield parry', display: 'I', points: 1,
            free_skill_points: 1, total_allocated_points: 4,
            row_html: rowHtml('shield parry', 'Shield Parry', 'I', 1),
        })]);
        const input = document.getElementById('new-skill-name');
        input.value = '  shield parry  ';
        document.getElementById('add-skill-btn').click();
        await flush();

        const row = document.querySelector('.skill-row[data-skill="shield parry"]');
        expect(row).not.toBeNull();
        expect(row.querySelector('.skill-name').textContent).toBe('Shield Parry');
        expect(row.querySelector('.skill-level').textContent).toBe('Level I');
        expect(input.value).toBe('');
    });

    test('the Enter key in the new-skill box allocates too', async () => {
        await loadPage({}, [jsonResponse({
            success: true, skill: 'stealth', display: 'I', points: 1,
            free_skill_points: 1, total_allocated_points: 4,
            row_html: rowHtml('stealth', 'Stealth', 'I', 1),
        })]);
        const input = document.getElementById('new-skill-name');
        input.value = 'stealth';
        input.dispatchEvent(new KeyboardEvent('keypress', { key: 'Enter', bubbles: true }));
        await flush();

        expect(document.querySelector('.skill-row[data-skill="stealth"]')).not.toBeNull();
    });

    test('an empty new-skill box posts nothing', async () => {
        await loadPage();
        fetch.mockClear();
        document.getElementById('new-skill-name').value = '   ';
        document.getElementById('add-skill-btn').click();
        await flush();

        expect(fetch).not.toHaveBeenCalled();
    });

    test('finds the row for a skill whose name would break a CSS selector', async () => {
        // Regression for issue #120: CSS.escape, not escapeHtml, on the lookup.
        const nasty = 'sword "of" doom';
        await loadPage();
        characterEdit.updateSkillsUI({
            age: 20, experience_years: 4, total_xp: 0, free_skill_points: 2,
            total_allocated_points: 1, is_alive: true,
            skills: { [nasty]: { points: 1, display: 'I' } },
            skills_html: rowHtml(nasty, 'Sword "of" Doom', 'I', 1),
        });
        routeFetch([jsonResponse({
            success: true, skill: nasty, display: 'II', points: 2,
            free_skill_points: 1, total_allocated_points: 2,
            row_html: rowHtml(nasty, 'Sword "of" Doom', 'II', 2),
        })]);
        await characterEdit.allocateSkillPoint(nasty);

        const rows = document.querySelectorAll('.skill-row');
        expect(rows).toHaveLength(1); // replaced in place, not duplicated
        expect(rows[0].querySelector('.skill-level').textContent).toBe('Level II');
    });

    test('strips every + button and the add control at zero free points', async () => {
        await loadPage({}, [jsonResponse({
            success: true, skill: 'sword', display: 'III', points: 6,
            free_skill_points: 0, total_allocated_points: 6,
            row_html: rowHtml('sword', 'Sword', 'III', 6, { withButton: false }),
        })]);
        document.querySelector('.allocate-btn').click();
        await flush();

        expect(document.querySelectorAll('.allocate-btn')).toHaveLength(0);
        expect(document.querySelector('.add-skill-control').style.display).toBe('none');
    });

    test('keeps the + buttons and the add control for a Game Master at zero (#770)', async () => {
        await loadPage({ canGrantSkillPoints: true }, [jsonResponse({
            success: true, skill: 'sword', display: 'III', points: 6,
            free_skill_points: 0, total_allocated_points: 6,
            row_html: rowHtml('sword', 'Sword', 'III', 6),
        })]);
        document.querySelector('.allocate-btn').click();
        await flush();

        expect(document.querySelectorAll('.allocate-btn').length).toBeGreaterThan(0);
        expect(document.querySelector('.add-skill-control').style.display).not.toBe('none');
    });

    test('replaces the no-skills placeholder with the first real row', async () => {
        await loadPage();
        document.getElementById('skills-list').innerHTML =
            '<p class="no-skills">No skills yet. Roll experience years to gain skills.</p>';
        routeFetch([jsonResponse({
            success: true, skill: 'sword', display: 'I', points: 1,
            free_skill_points: 1, total_allocated_points: 1,
            row_html: rowHtml('sword', 'Sword', 'I', 1),
        })]);
        await characterEdit.allocateSkillPoint('sword');

        expect(document.querySelector('.no-skills')).toBeNull();
        expect(document.querySelectorAll('.skill-row')).toHaveLength(1);
    });

    test('surfaces a server error and changes nothing', async () => {
        await loadPage({}, [jsonResponse({ success: false, error: 'No free points' })]);
        document.querySelector('.allocate-btn').click();
        await flush();

        expect(alert).toHaveBeenCalledWith('No free points');
        expect(document.getElementById('free-points').textContent).toBe('2');
    });

    test('surfaces a network failure', async () => {
        await loadPage();
        fetch.mockImplementation(() => Promise.reject(new Error('offline')));
        await characterEdit.allocateSkillPoint('sword');
        expect(alert).toHaveBeenCalledWith('Error: offline');
    });

    test('a click that is not on a + button is ignored', async () => {
        await loadPage();
        fetch.mockClear();
        document.querySelector('.skill-name').dispatchEvent(
            new Event('click', { bubbles: true })
        );
        await flush();
        expect(fetch).not.toHaveBeenCalled();
    });
});

describe('renameSkill', () => {
    test('posts the old and new names with the page CSRF token', async () => {
        await loadPage({}, [jsonResponse({ success: true, skill: 'long sword', display: 'II' })]);
        const label = document.querySelector('.skill-name');
        label.textContent = 'Long Sword';
        label.dispatchEvent(new Event('focusout', { bubbles: true }));
        await flush();

        expect(fetch).toHaveBeenCalledWith(CONFIG_DEFAULTS.renameSkillUrl, {
            method: 'POST',
            headers: { 'X-CSRFToken': 'test-token', 'Content-Type': 'application/json' },
            body: JSON.stringify({ old_name: 'sword', new_name: 'Long Sword' }),
        });
    });

    test('rewrites the row, its data attributes and the + button on success', async () => {
        await loadPage({}, [jsonResponse({ success: true, skill: 'long sword', display: 'II' })]);
        const label = document.querySelector('.skill-name');
        label.textContent = 'Long Sword';
        label.dispatchEvent(new Event('focusout', { bubbles: true }));
        await flush();

        const row = document.querySelector('.skill-row');
        expect(row.dataset.skill).toBe('long sword');
        expect(label.dataset.original).toBe('long sword');
        expect(label.textContent).toBe('Long Sword');
        expect(row.querySelector('.allocate-btn').dataset.skill).toBe('long sword');
    });

    test('takes the re-priced level the server sends (Arcane, issue #154)', async () => {
        await loadPage({}, [jsonResponse({ success: true, skill: 'arcane', display: 'I' })]);
        const label = document.querySelector('.skill-name');
        label.textContent = 'Arcane';
        label.dispatchEvent(new Event('focusout', { bubbles: true }));
        await flush();

        expect(document.querySelector('.skill-level').textContent).toBe('Level I');
    });

    test('an unchanged name posts nothing and restores the display casing', async () => {
        await loadPage();
        fetch.mockClear();
        const label = document.querySelector('.skill-name');
        label.textContent = 'SWORD';
        label.dispatchEvent(new Event('focusout', { bubbles: true }));
        await flush();

        expect(fetch).not.toHaveBeenCalled();
        expect(label.textContent).toBe('Sword');
    });

    test('an emptied name posts nothing and restores the old one', async () => {
        await loadPage();
        fetch.mockClear();
        const label = document.querySelector('.skill-name');
        label.textContent = '   ';
        label.dispatchEvent(new Event('focusout', { bubbles: true }));
        await flush();

        expect(fetch).not.toHaveBeenCalled();
        expect(label.textContent).toBe('Sword');
    });

    test('a rejected rename reverts the label and says why', async () => {
        await loadPage({}, [jsonResponse({ success: false, error: 'Name taken' })]);
        const label = document.querySelector('.skill-name');
        label.textContent = 'Claymore';
        label.dispatchEvent(new Event('focusout', { bubbles: true }));
        await flush();

        // Beside the name, not a page notice (issue #339, item 22).
        expect(alert).not.toHaveBeenCalled();
        expect(label.classList.contains('error')).toBe(true);
        expect(document.querySelector('.skill-row .autosave-error').textContent).toBe('Name taken');
        expect(label.textContent).toBe('Sword');
        expect(document.querySelector('.skill-row').dataset.skill).toBe('sword');
    });

    test('a successful rename marks the name saved, and editing again clears it', async () => {
        await loadPage({}, [jsonResponse({ success: true, skill: 'long sword', display: 'II' })]);
        const label = document.querySelector('.skill-name');
        label.textContent = 'Long Sword';
        label.dispatchEvent(new Event('focusout', { bubbles: true }));
        await flush();

        expect(label.classList.contains('saved')).toBe(true);
        expect(label.classList.contains('saving')).toBe(false);
        label.dispatchEvent(new Event('focusin', { bubbles: true }));
        expect(label.classList.contains('saved')).toBe(false);
    });

    test('focusing a name after a refusal clears the inline error', async () => {
        await loadPage({}, [jsonResponse({ success: false, error: 'Name taken' })]);
        const label = document.querySelector('.skill-name');
        label.textContent = 'Claymore';
        label.dispatchEvent(new Event('focusout', { bubbles: true }));
        await flush();
        label.dispatchEvent(new Event('focusin', { bubbles: true }));

        expect(document.querySelector('.skill-row .autosave-error')).toBeNull();
        expect(label.classList.contains('error')).toBe(false);
    });

    test('a network failure reverts the label too', async () => {
        await loadPage();
        fetch.mockImplementation(() => Promise.reject(new Error('offline')));
        const label = document.querySelector('.skill-name');
        label.textContent = 'Claymore';
        label.dispatchEvent(new Event('focusout', { bubbles: true }));
        await flush();

        expect(alert).not.toHaveBeenCalled();
        expect(document.querySelector('.skill-row .autosave-error').textContent).toBe(FIELD_NETWORK_ERROR);
        expect(label.textContent).toBe('Sword');
    });

    test('Escape cancels the edit without posting', async () => {
        await loadPage();
        fetch.mockClear();
        const label = document.querySelector('.skill-name');
        label.textContent = 'Claymore';
        label.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        await flush();

        expect(label.textContent).toBe('Sword');
        expect(fetch).not.toHaveBeenCalled();
    });

    test('Enter commits by blurring the field', async () => {
        await loadPage();
        const label = document.querySelector('.skill-name');
        const blur = jest.spyOn(label, 'blur');
        label.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
        expect(blur).toHaveBeenCalled();
    });

    test('a keydown outside a skill name is ignored', async () => {
        await loadPage();
        expect(() =>
            document.getElementById('skills-list').dispatchEvent(
                new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })
            )
        ).not.toThrow();
    });
});

describe('the experience workbench', () => {
    const TERM = {
        years_requested: 4,
        track: 'merchant',
        // `text` is the sentence the server wrote for this check
        // (dice_display.survival_roll). Since issue #338 the browser prints
        // it rather than building its own; these fixtures carry what the
        // add-years endpoint really sends, and
        // characters/tests/test_experience_display.py holds the sentences
        // themselves to their dice.
        years: [
            { year: 5, age: 21, skill: 'haggling', roll: 4,
              survival_check: { dice: [2, 3, 1], roll: 6, modifier: 1, total: 7, target: 4, success: true,
                text: '3d6 [2, 3, 1] + 1 = 7 vs 4' } },
            { year: 6, age: 22, skill: 'sword', roll: 6,
              survival_check: { dice: [6, 2, 5], roll: 13, modifier: 1, total: 14, target: 4, success: true,
                text: '3d6 [6, 2, 5] + 1 = 14 vs 4' } },
            { year: 7, age: 23, skill: 'riding', roll: 2,
              survival_check: { dice: [4, 4, 2], roll: 10, modifier: 1, total: 11, target: 4, success: true,
                text: '3d6 [4, 4, 2] + 1 = 11 vs 4' } },
            { year: 8, age: 24, skill: 'haggling', roll: 5,
              survival_check: { dice: [3, 4, 6], roll: 13, modifier: -2, total: 11, target: 4, success: true,
                text: '3d6 [3, 4, 6] - 2 = 11 vs 4' } },
        ],
        survival_check: { dice: [3, 4, 6], roll: 13, modifier: -2, total: 11, target: 4, success: true,
            text: '3d6 [3, 4, 6] - 2 = 11 vs 4' },
        aging: { str: -1, dex: 0 },
        xp_awarded: 1000,
        free_points_awarded: 2,
        money_earned: 250,
    };
    const CHARACTER = {
        age: 24, experience_years: 8, total_xp: 2500, level: 3, money_earned: 450,
        free_skill_points: 2, free_spell_points: 0, total_allocated_points: 8,
        is_alive: true, skills: { haggling: { points: 2, display: 'I' } },
        skills_html: '<div class="skill-row" data-skill="haggling"><span class="skill-name">Haggling</span>'
            + '<span class="skill-level">Level I</span><span class="skill-points">(2 pts)</span></div>',
    };

    /**
     * A history entry as characters/_term_entry.html renders it. The wording
     * of each line is the partial's (characters/tests/test_skill_and_term_partials.py
     * holds it); these tests check the log shows what the server sent.
     */
    function entryHtml(summary, lines, { aging = '' } = {}) {
        const items = lines.map((line) => `<li>${line}</li>`).join('');
        const agingLine = aging ? `<p class="aging-warning">Aging penalties applied: ${aging}</p>` : '';
        return `<details class="term-entry" open><summary>${summary}</summary>`
            + `<div class="term-details"><ul>${items}</ul>${agingLine}`
            + '<p>+1000 XP, +2 free skill points</p></div></details>';
    }
    const RUN_HTML = entryHtml('Merchant, Age 21–24', [
        'Year 5: haggling (rolled 4) — survival 3d6 [2, 3, 1] + 1 = 7 vs 4: survived',
        'Year 6: sword (rolled 6) — survival 3d6 [6, 2, 5] + 1 = 14 vs 4: survived',
        'Year 7: riding (rolled 2) — survival 3d6 [4, 4, 2] + 1 = 11 vs 4: survived',
        'Year 8: haggling (rolled 5) — survival 3d6 [3, 4, 6] - 2 = 11 vs 4: survived',
    ], { aging: 'STR -1' });

    /** Click the roll button. There is no confirmation step (issue #217). */
    async function rollTerm() {
        document.getElementById('roll-years-btn').click();
        await flush();
    }

    describe('the preview', () => {
        test('updates the age, survival target and aging line when the track changes', async () => {
            await loadPage();
            const select = document.getElementById('track-select');
            select.value = 'campaigner';
            select.dispatchEvent(new Event('change'));

            expect(document.getElementById('workbench-age-preview').textContent).toBe('20 → 21');
            // Campaigner's survival target from the config's rules table.
            expect(document.getElementById('workbench-survival-preview').textContent).toBe(
                '3d6 + best attribute modifier ≥ 6'
            );
        });

        test('random shows the generic survival wording instead of a number', async () => {
            await loadPage();
            const select = document.getElementById('track-select');
            select.value = 'random';
            select.dispatchEvent(new Event('change'));

            expect(document.getElementById('workbench-survival-preview').textContent).toBe(
                '3d6 + best attribute modifier (target varies by track)'
            );
        });

        test('names the age the next aging hit lands at', async () => {
            await loadPage();
            document.getElementById('track-select').dispatchEvent(new Event('change'));
            // agingByAge from CONFIG_DEFAULTS: the first change past age 20.
            expect(document.getElementById('workbench-aging-preview').textContent).toBe(
                'STR -1, DEX -1 at age 36'
            );
        });

        test('skips a change point the character is already past', async () => {
            await loadPage({ currentAge: 38 });
            document.getElementById('track-select').dispatchEvent(new Event('change'));

            expect(document.getElementById('workbench-aging-preview').textContent).toBe(
                'STR -1, DEX -1, CON -1 at age 40'
            );
        });

        test('past the precomputed horizon, shows the general escalation note instead of going blank', async () => {
            // A very old character is past every precomputed change point --
            // previously this left the aging line blank, understating a real
            // penalty (issue #163). The note is the server's, built from the
            // loaded canon (issue #68): invented words here, so a sentence
            // typed into the script would not pass.
            const note = 'Continues to escalate past age 96 (roughly -1 to STR/DEX/CON every five years, plus -1 to INT/WIS every 15).';
            await loadPage({ currentAge: 100, agingEscalationNote: note });
            document.getElementById('track-select').dispatchEvent(new Event('change'));

            expect(document.getElementById('workbench-aging-preview').textContent).toBe(note);
        });

        test('says so when nothing further lands before the horizon', async () => {
            // Below the horizon, but past every change point the config carries.
            await loadPage({ currentAge: 50, agingByAge: {} });
            document.getElementById('track-select').dispatchEvent(new Event('change'));

            expect(document.getElementById('workbench-aging-preview').textContent).toBe(
                'No further aging before age 96'
            );
        });

        test('the eyebrow and button both name the years about to be rolled', async () => {
            await loadPage({ currentYears: 4 });
            document.getElementById('track-select').dispatchEvent(new Event('change'));

            expect(document.getElementById('workbench-eyebrow').textContent).toBe('NEXT — YEAR 5');
            expect(document.getElementById('roll-years-btn').textContent).toBe('Roll 1 year');
        });

        test('a multi-year run names its whole span and pluralizes the button', async () => {
            await loadPage({ currentYears: 4 });
            const yearsInput = document.getElementById('years-input');
            yearsInput.value = '3';
            yearsInput.dispatchEvent(new Event('input'));

            expect(document.getElementById('workbench-eyebrow').textContent).toBe('NEXT — YEARS 5–7');
            expect(document.getElementById('roll-years-btn').textContent).toBe('Roll 3 years');
            expect(document.getElementById('workbench-age-preview').textContent).toBe('20 → 23');
        });

        test('a blank or out-of-range year count falls back to something true', async () => {
            await loadPage();
            const yearsInput = document.getElementById('years-input');
            for (const [entered, expected] of [['', 'Roll 1 year'], ['0', 'Roll 1 year'], ['999', 'Roll 20 years']]) {
                yearsInput.value = entered;
                yearsInput.dispatchEvent(new Event('input'));
                expect(document.getElementById('roll-years-btn').textContent).toBe(expected);
            }
        });
    });

    test('clicking the roll button posts the track and year count, with no confirmation step', async () => {
        await loadPage({}, [jsonResponse({ success: true, run_result: TERM, run_html: RUN_HTML, character: CHARACTER })]);
        await rollTerm();

        expect(fetch).toHaveBeenCalledWith(CONFIG_DEFAULTS.addYearsUrl, {
            method: 'POST',
            headers: { 'X-CSRFToken': 'test-token', 'Content-Type': 'application/json' },
            body: JSON.stringify({ track: 'merchant', years: 1 }),
        });
    });

    test('the button disables and reads Rolling... while the request is in flight', async () => {
        let resolveFetch;
        await loadPage();
        fetch.mockImplementation((url) => {
            if (url === CONFIG_DEFAULTS.catalogUrl) return Promise.resolve(jsonResponse(CATALOG));
            return new Promise((resolve) => { resolveFetch = resolve; });
        });
        const btn = document.getElementById('roll-years-btn');
        btn.click();
        await Promise.resolve();

        expect(btn.disabled).toBe(true);
        expect(btn.textContent).toBe('Rolling...');

        resolveFetch(jsonResponse({ success: true, run_result: TERM, run_html: RUN_HTML, character: CHARACTER }));
        await flush();
    });

    test('the summary figures and skill rows follow the response', async () => {
        await loadPage({}, [jsonResponse({ success: true, run_result: TERM, run_html: RUN_HTML, character: CHARACTER })]);
        await rollTerm();

        expect(document.getElementById('character-age').textContent).toBe('24');
        expect(document.getElementById('experience-years').textContent).toBe('8');
        expect(document.querySelector('.skill-row').dataset.skill).toBe('haggling');
    });

    test('after success the preview advances to the years still to come', async () => {
        await loadPage({}, [jsonResponse({ success: true, run_result: TERM, run_html: RUN_HTML, character: CHARACTER })]);
        await rollTerm();

        const btn = document.getElementById('roll-years-btn');
        expect(btn.disabled).toBe(false);
        expect(btn.textContent).toBe('Roll 1 year');
        expect(document.getElementById('workbench-eyebrow').textContent).toBe('NEXT — YEAR 9');
        // The response's age (24), not the age at the time of the click (20).
        expect(document.getElementById('workbench-age-preview').textContent).toBe('24 → 25');
    });

    test('the term is prepended to the history log, open and expanded', async () => {
        await loadPage({}, [jsonResponse({ success: true, run_result: TERM, run_html: RUN_HTML, character: CHARACTER })]);
        await rollTerm();

        const entry = document.querySelector('#experience-log .term-entry');
        expect(entry).not.toBeNull();
        expect(entry.open).toBe(true);
        expect(document.querySelector('.no-history')).toBeNull();
        const text = entry.textContent.replace(/\s+/g, ' ');
        expect(text).toContain('Merchant, Age 21–24');
        // Every year carries its own survival dice (issue #175). The entry is
        // the server's rendering; the partial's tests hold the wording.
        expect(text).toContain('Year 5: haggling (rolled 4) — survival 3d6 [2, 3, 1] + 1 = 7 vs 4: survived');
        // A malus reads as a subtraction, never as "+ -2" (issue #222).
        expect(text).toContain('Year 8: haggling (rolled 5) — survival 3d6 [3, 4, 6] - 2 = 11 vs 4: survived');
        expect(text).toContain('Aging penalties applied: STR -1');
    });

    test('a triple-ones year is labeled always fatal, not a beatable roll (issue #223)', async () => {
        const fatalYear = {
            year: 5, age: 21, skill: 'haggling', roll: 4,
            survival_check: { dice: [1, 1, 1], roll: 3, modifier: 3, total: 6, target: 4,
                success: false, auto_death: true, auto_success: false,
                text: '3d6 [1, 1, 1] + 3 = 6 vs 4 (triple ones — always fatal)' },
        };
        const fatalTerm = { ...TERM, years: [fatalYear] };
        await loadPage({}, [jsonResponse({ success: true, run_result: fatalTerm, run_html: entryHtml('Merchant, Age 21', ['Year 5: haggling (rolled 4) — survival 3d6 [1, 1, 1] + 3 = 6 vs 4 (triple ones — always fatal): died']), character: CHARACTER })]);
        await rollTerm();

        const text = document.querySelector('#experience-log .term-entry').textContent.replace(/\s+/g, ' ');
        expect(text).toContain('3d6 [1, 1, 1] + 3 = 6 vs 4 (triple ones — always fatal): died');
    });

    test('a stored 2d6 check still renders as 2d6, with its own natural names', async () => {
        // Checks recorded before issue #223 are still in players' logs; the
        // formatter reads the dice it was given rather than relabelling them.
        const legacyYear = {
            year: 5, age: 21, skill: 'haggling', roll: 4,
            survival_check: { dice: [6, 6], roll: 12, modifier: 0, total: 12, target: 14,
                success: true, auto_death: false, auto_success: true,
                text: '2d6 [6, 6] + 0 = 12 vs 14 (boxcars — always survives)' },
        };
        const legacyTerm = { ...TERM, years: [legacyYear] };
        await loadPage({}, [jsonResponse({ success: true, run_result: legacyTerm, run_html: entryHtml('Merchant, Age 21', ['Year 5: haggling (rolled 4) — survival 2d6 [6, 6] + 0 = 12 vs 14 (boxcars — always survives): survived']), character: CHARACTER })]);
        await rollTerm();

        const text = document.querySelector('#experience-log .term-entry').textContent.replace(/\s+/g, ' ');
        expect(text).toContain('2d6 [6, 6] + 0 = 12 vs 14 (boxcars — always survives)');
    });

    test('a one-year run shows its single age, not a 21-21 range (issue #217)', async () => {
        const shortTerm = { ...TERM, years: [TERM.years[0]] };
        await loadPage({}, [jsonResponse({ success: true, run_result: shortTerm, run_html: entryHtml('Merchant, Age 21', ['Year 5: haggling (rolled 4) — survival 3d6 [2, 3, 1] + 1 = 7 vs 4: survived']), character: CHARACTER })]);
        await rollTerm();

        const summary = document.querySelector('#experience-log .term-entry summary').textContent;
        expect(summary).toContain('Merchant, Age 21');
        expect(summary).not.toContain('21–21');
    });

    test('a second run lands above the first, and closes it', async () => {
        await loadPage({}, [
            jsonResponse({ success: true, run_result: TERM, run_html: RUN_HTML, character: CHARACTER }),
            jsonResponse({
                success: true,
                run_result: {
                    ...TERM,
                    years: TERM.years.map((year) => ({ ...year, age: year.age + 4 })),
                },
                run_html: entryHtml('Merchant, Age 25–28', ['Year 9: haggling (rolled 4)']),
                character: { ...CHARACTER, experience_years: 12, age: 28 },
            }),
        ]);
        await rollTerm();
        await rollTerm();

        const entries = document.querySelectorAll('#experience-log .term-entry');
        expect(entries).toHaveLength(2);
        expect(entries[0].querySelector('summary').textContent).toContain('Age 25–28');
        expect(entries[0].open).toBe(true);
        expect(entries[1].querySelector('summary').textContent).toContain('Age 21–24');
        expect(entries[1].open).toBe(false);
    });

    test('fatigue, body and injury thresholds refresh in place from the response', async () => {
        // Aging can move STR/DEX/CON, which moves these the same way editing
        // an attribute field does (issue #163).
        await loadPage({}, [jsonResponse({
            success: true,
            run_result: TERM,
            character: {
                ...CHARACTER,
                fatigue: 27,
                body: 17,
                injury_thresholds_html:
                    '<table class="injury-table"><tbody><tr>'
                    + '<td id="injury-fatigue-minus_one_at">12</td>'
                    + '<td id="injury-body-unconscious_at">-2</td>'
                    + '</tr></tbody></table>',
            },
        })]);
        await rollTerm();

        expect(document.getElementById('fatigue').textContent).toBe('27');
        expect(document.getElementById('body').textContent).toBe('17');
        expect(document.getElementById('injury-fatigue-minus_one_at').textContent).toBe('12');
        expect(document.getElementById('injury-body-unconscious_at').textContent).toBe('-2');
    });

    test('the encumbrance/movement readout refreshes in place from the response', async () => {
        // STR sets carrying capacity, so aging can shift encumbrance and the
        // combat mods riding on it too -- same response shape the equipment
        // endpoints use, handed to the same updateEquipmentDisplay renderer
        // (issue #163).
        await loadPage({}, [jsonResponse({
            success: true,
            run_result: TERM,
            character: CHARACTER,
            movement: {
                success: true,
                carried_weight: 22,
                pack_weight: 10,
                encumbrance_level: 'medium', encumbrance_label: 'Medium',
                encumbrance_badge_classes: 'load-3',
                movement_penalty: -2,
                movement_restriction_label: 'No running',
                movement_modifier: -1,
                gait_readout: [{ key: 'walk', na: false, distance: 8 }],
                armor_stops: 0,
                armor_dex_penalty: 0,
                weapon_to_hit_mod: 0,
                weapon_damage_mod: 0,
                weapon_critical_mod: 0,
                pack_dropped: false,
                equipment: [],
            },
        })]);
        await rollTerm();

        expect(document.getElementById('carried-weight').textContent).toBe('22');
        const badge = document.getElementById('encumbrance-level');
        expect(badge.textContent).toBe('Medium');
        expect(document.getElementById('movement-penalty').textContent).toBe('-2 movement');
    });

    test('the movement payload, which carries no repaint, leaves the sheet as the response painted it', async () => {
        // The response carries sheet_repaint once, at the top; the movement
        // payload handed to updateEquipmentDisplay has none of its keys
        // (issue #933). Its paintSheet call leaves alone every part whose
        // key is absent, so it does not undo what the top-level one painted.
        await loadPage({}, [jsonResponse({
            success: true,
            run_result: TERM,
            run_html: RUN_HTML,
            character: CHARACTER,
            is_alive: true,
            movement: { success: true, carried_weight: 22, pack_dropped: false, equipment: [] },
        })]);
        await rollTerm();

        expect(document.getElementById('mark-dead-btn').hidden).toBe(false);
        expect(document.getElementById('carried-weight').textContent).toBe('22');
    });

    test('a response with no movement data does not error', async () => {
        // Belt-and-suspenders: older/mocked responses without the new key
        // should not break the rest of the success path.
        await loadPage({}, [jsonResponse({ success: true, run_result: TERM, run_html: RUN_HTML, character: CHARACTER })]);
        await expect(rollTerm()).resolves.not.toThrow();
    });

    test('nothing in this flow ever reloads the page', async () => {
        await loadPage({}, [jsonResponse({ success: true, run_result: TERM, run_html: RUN_HTML, character: CHARACTER })]);
        await rollTerm();

        expect(pageNavigation.reload).not.toHaveBeenCalled();
    });

    test('a term with no aging leaves the penalties line out', async () => {
        const { aging, ...noAging } = TERM;
        await loadPage({}, [jsonResponse({ success: true, run_result: noAging, run_html: entryHtml('Merchant, Age 21–24', ['Year 5: haggling (rolled 4)']), character: CHARACTER })]);
        await rollTerm();

        expect(document.querySelector('.term-entry').textContent).not.toContain(
            'Aging penalties applied'
        );
    });

    test('a term whose aging is all zeroes also leaves it out', async () => {
        await loadPage({}, [jsonResponse({
            success: true,
            run_result: { ...TERM, aging: { str: 0, dex: 0 } },
            run_html: entryHtml('Merchant, Age 21–24', ['Year 5: haggling (rolled 4)']),
            character: CHARACTER,
        })]);
        await rollTerm();

        expect(document.querySelector('.term-entry').textContent).not.toContain(
            'Aging penalties applied'
        );
    });

    test('a year without a stored survival check renders without a dice fragment', async () => {
        // Legacy experience logs predate per-year checks; the line still
        // renders, just without the survival tail.
        const bareYear = { year: 1, age: 21, skill: 'haggling', roll: 4 };
        await loadPage({}, [jsonResponse({
            success: true,
            run_result: { ...TERM, years: [bareYear, ...TERM.years.slice(1)] },
            run_html: entryHtml('Merchant, Age 21–24', ['Year 1: haggling (rolled 4)', 'Year 6: sword (rolled 6)']),
            character: CHARACTER,
        })]);
        await rollTerm();

        const text = document.querySelector('#experience-log .term-entry').textContent;
        expect(text).toContain('Year 1: haggling (rolled 4)');
        expect(text).not.toContain('Year 1: haggling (rolled 4) — survival');
    });

    test('a rejected roll surfaces the error and restores the button, history unchanged', async () => {
        await loadPage({}, [jsonResponse({ success: false, error: 'Too old' })]);
        await rollTerm();

        const btn = document.getElementById('roll-years-btn');
        expect(alert).toHaveBeenCalledWith('Too old');
        expect(btn.disabled).toBe(false);
        expect(btn.textContent).toBe('Roll 1 year');
        expect(document.querySelector('#experience-log .term-entry')).toBeNull();
    });

    test('a network failure is reported and the button recovers', async () => {
        await loadPage();
        fetch.mockImplementation((url) =>
            url === CONFIG_DEFAULTS.catalogUrl
                ? Promise.resolve(jsonResponse(CATALOG))
                : Promise.reject(new Error('offline'))
        );
        await rollTerm();

        expect(alert).toHaveBeenCalledWith('Error: offline');
        expect(document.getElementById('roll-years-btn').disabled).toBe(false);
    });

        test('an underscore track renders its human label in the history summary (issue #221)', async () => {
        const run = { ...TERM, track: 'civil_service' };
        await loadPage({}, [jsonResponse({ success: true, run_result: run, run_html: entryHtml('Civil Service, Age 21–24', ['Year 5: haggling (rolled 4)']), character: CHARACTER })]);
        await rollTerm();

        const summary = document.querySelector('#experience-log .term-entry summary').textContent;
        expect(summary).toContain('Civil Service');
        expect(summary).not.toContain('Civil_service');
    });

describe('death', () => {
        const fatalCheck = { dice: [1, 2], roll: 3, modifier: 1, total: 4, target: 6, success: false,
            text: '2d6 [1, 2] + 1 = 4 vs 6' };
        const FATAL_TERM = {
            ...TERM,
            years: [TERM.years[0], { ...TERM.years[1], survival_check: fatalCheck }],
            survival_check: fatalCheck,
        };
        const DEAD_CHARACTER = { ...CHARACTER, is_alive: false, death_cause: 'Died on a merchant road' };

        test('swaps the workbench card for the death card, matching the template fragment', async () => {
            // A fatal term stops at the year that killed the character; its
            // last year's survival_check is the fatal roll (issue #175).
            await loadPage({}, [jsonResponse({ success: true, run_result: FATAL_TERM, run_html: entryHtml('Merchant, Age 21–22', ['Year 5: haggling (rolled 4) — survival 3d6 [2, 3, 1] + 1 = 7 vs 4: survived', 'Year 6: sword (rolled 6) — survival 2d6 [1, 2] + 1 = 4 vs 6: died']), character: DEAD_CHARACTER, name: 'Aldwin', is_alive: false })]);
            await rollTerm();

            const card = document.querySelector('#workbench-card .death-card');
            expect(card).not.toBeNull();
            expect(card.querySelector('h3').textContent).toBe('Aldwin died');
            expect(card.querySelector('.death-cause').textContent).toBe('Died on a merchant road');
            expect(card.querySelector('.fatal-roll').textContent).toBe('Fatal roll: 2d6 [1, 2] + 1 = 4 vs 6');
            expect(card.querySelector('.fatal-roll').hidden).toBe(false);
            expect(card.textContent).toContain('A DM can resurrect this character.');
            // No roll button, no confirmation, no reload -- death is a state.
            expect(document.getElementById('roll-years-btn')).toBeNull();
        });

        test('the history log still marks the fatal year', async () => {
            await loadPage({}, [jsonResponse({ success: true, run_result: FATAL_TERM, run_html: entryHtml('Merchant, Age 21–22', ['Year 5: haggling (rolled 4) — survival 3d6 [2, 3, 1] + 1 = 7 vs 4: survived', 'Year 6: sword (rolled 6) — survival 2d6 [1, 2] + 1 = 4 vs 6: died']), character: DEAD_CHARACTER, name: 'Aldwin', is_alive: false })]);
            await rollTerm();

            const entry = document.querySelector('#experience-log .term-entry');
            expect(entry.textContent).toContain('survival 2d6 [1, 2] + 1 = 4 vs 6: died');
        });

        test('a death with no fatal check (defensive) omits the fatal-roll line', async () => {
            await loadPage({}, [jsonResponse({
                success: true,
                run_result: { ...TERM, years: [{ ...TERM.years[0], survival_check: undefined }] },
                character: DEAD_CHARACTER,
            })]);
            await rollTerm();

            expect(document.querySelector('#workbench-card .fatal-roll')).toBeNull();
        });

        test('never reloads the page', async () => {
            await loadPage({}, [jsonResponse({ success: true, run_result: FATAL_TERM, run_html: entryHtml('Merchant, Age 21–22', ['Year 5: haggling (rolled 4) — survival 3d6 [2, 3, 1] + 1 = 7 vs 4: survived', 'Year 6: sword (rolled 6) — survival 2d6 [1, 2] + 1 = 4 vs 6: died']), character: DEAD_CHARACTER, name: 'Aldwin', is_alive: false })]);
            await rollTerm();

            expect(pageNavigation.reload).not.toHaveBeenCalled();
        });
    });
});

describe('createPanel', () => {
    beforeEach(async () => {
        await loadPage();
        // base.html loads notify.js on every page, and createPanel puts its
        // panels in the notice region that script owns. The suites above
        // deliberately leave it out to exercise the alert/confirm fallback.
        require('../notify.js');
    });

    // notify.js publishes onto the shared jsdom window, which outlives
    // jest.resetModules — leave it behind and the later suites would take the
    // in-page path instead of the alert/confirm fallback they are testing.
    afterEach(() => {
        delete window.noticeRegion;
        delete window.showNotice;
        delete window.confirmDialog;
        delete window.promptDialog;
        delete window.initConfirmForms;
    });

    test('appends a panel to the notice region and focuses inside it', () => {
        const panel = characterEdit.createPanel('<button id="only">OK</button>');
        expect(panel.parentElement.id).toBe('notice-region');
        expect(panel.classList.contains('notice-panel')).toBe(true);
        expect(document.activeElement.id).toBe('only');
        panel.remove();
    });

    test('is not a modal — no overlay, no aria-modal, no focus trap', () => {
        const panel = characterEdit.createPanel(
            '<button id="first">A</button><button id="last">B</button>'
        );
        expect(panel.getAttribute('role')).toBe('group');
        expect(panel.getAttribute('aria-modal')).toBeNull();
        expect(document.querySelector('.modal')).toBeNull();

        // Tab is the browser's to handle: nothing wraps focus back inside.
        document.getElementById('last').focus();
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' }));
        expect(document.activeElement.id).toBe('last');
        panel.remove();
    });

    test('Escape elsewhere on the page leaves the panel standing', () => {
        const panel = characterEdit.createPanel('<button id="only">OK</button>');
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
        expect(document.body.contains(panel)).toBe(true);
        panel.remove();
    });

    test('a tone and label ride through to the panel', () => {
        const panel = characterEdit.createPanel('<p>Gone</p>', {
            tone: 'error', label: 'Character died',
        });
        expect(panel.classList.contains('notice-error')).toBe(true);
        expect(panel.getAttribute('aria-label')).toBe('Character died');
        panel.remove();
    });

    test('two panels stack rather than replacing each other', () => {
        const first = characterEdit.createPanel('<p>One</p>');
        const second = characterEdit.createPanel('<p>Two</p>');
        expect(document.querySelectorAll('.notice-panel')).toHaveLength(2);
        first.remove();
        second.remove();
    });
});

describe('the visibility toggle (issue #273)', () => {
    test('checking it posts is_public true and updates the badge in place', async () => {
        await loadPage({}, [jsonResponse({ success: true, is_public: true })]);
        const toggle = document.getElementById('visibility-toggle');
        toggle.checked = true;
        toggle.dispatchEvent(new Event('change'));
        await flush();

        expect(fetch).toHaveBeenCalledWith(CONFIG_DEFAULTS.setVisibilityUrl, {
            method: 'POST',
            headers: { 'X-CSRFToken': 'test-token', 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_public: true }),
        });
        const badge = document.getElementById('visibility-badge');
        expect(badge.textContent).toBe('Public');
        expect(badge.classList.contains('visibility-public')).toBe(true);
        expect(badge.classList.contains('visibility-private')).toBe(false);
        // State-driven update, not a reload.
        expect(pageNavigation.reload).not.toHaveBeenCalled();
        expect(toggle.disabled).toBe(false);
    });

    test('unchecking it posts is_public false and flips the badge back', async () => {
        await loadPage({}, [jsonResponse({ success: true, is_public: false })]);
        const badge = document.getElementById('visibility-badge');
        badge.textContent = 'Public';
        badge.className = 'status-badge visibility-public';
        const toggle = document.getElementById('visibility-toggle');
        toggle.checked = false;
        toggle.dispatchEvent(new Event('change'));
        await flush();

        expect(badge.textContent).toBe('Private');
        expect(badge.classList.contains('visibility-private')).toBe(true);
    });

    test('a network failure reverts the checkbox and re-enables it (#643)', async () => {
        await loadPage();
        fetch.mockRejectedValueOnce(new TypeError('Failed to fetch'));
        const toggle = document.getElementById('visibility-toggle');
        toggle.checked = true;
        toggle.dispatchEvent(new Event('change'));
        await flush();

        // The browser flipped the box before the handler ran, and the handler
        // used to die at the rejected fetch: a box reading "Public" beside a
        // badge reading "Private", disabled for good, with nothing said.
        expect(toggle.checked).toBe(false);
        expect(toggle.disabled).toBe(false);
        expect(alert).toHaveBeenCalledWith('Network error — try again');
        expect(document.getElementById('visibility-badge').textContent).toBe('Private');
    });

    test('a refused flip reverts the checkbox and says why', async () => {
        await loadPage({}, [jsonResponse({ success: false, error: 'Permission denied' }, { ok: false })]);
        const toggle = document.getElementById('visibility-toggle');
        toggle.checked = true;
        toggle.dispatchEvent(new Event('change'));
        await flush();

        expect(toggle.checked).toBe(false);
        expect(alert).toHaveBeenCalledWith('Permission denied');
        expect(document.getElementById('visibility-badge').textContent).toBe('Private');
    });
});

describe('read-only equipment rendering (issue #273)', () => {
    const gear = [
        { item_id: 'dagger', quantity: 2, in_pack: true, notes: 'boot knife' },
        { item_id: 'rope', quantity: 1 },
    ];

    test('a read-only viewer gets static rows with no controls', async () => {
        await loadPage({ canEdit: false, initialEquipment: gear });
        const list = document.getElementById('equipment-list');
        expect(list.textContent).toContain('Dagger');
        expect(list.textContent).toContain('Qty: 2');
        expect(list.textContent).toContain('In pack');
        expect(list.textContent).toContain('boot knife');
        expect(list.textContent).toContain('Rope');
        // The facts, but none of the affordances.
        expect(list.querySelectorAll('input, button, select').length).toBe(0);
    });

    test('an editable viewer still gets the full rows', async () => {
        await loadPage({ canEdit: true, initialEquipment: gear });
        const list = document.getElementById('equipment-list');
        expect(list.querySelectorAll('.remove-btn').length).toBe(2);
        expect(list.querySelectorAll('input[data-field="quantity"]').length).toBe(2);
    });
});

describe('text limits come from the server (issue #928)', () => {
    afterEach(() => { delete window.promptDialog; });

    test("an equipment row's notes input carries the server's limit", async () => {
        await loadPage({ initialEquipment: [{ item_id: 'rope', quantity: 1 }] });

        const notes = document.querySelector('#equipment-list [data-field="notes"]');
        expect(notes.getAttribute('maxlength')).toBe(
            String(CONFIG_DEFAULTS.textLimits.item_notes),
        );
    });

    test("the cause-of-death prompt carries the server's limit", async () => {
        await loadPage();
        window.promptDialog = jest.fn(() => Promise.resolve(null));
        document.getElementById('mark-dead-btn').click();
        await flush();

        expect(window.promptDialog).toHaveBeenCalledWith(
            'Cause of death:',
            expect.objectContaining({ maxLength: CONFIG_DEFAULTS.textLimits.death_cause }),
        );
    });
});

describe('number bounds come from the server (issue #938)', () => {
    test("an equipment row's quantity input stops at the server's bound", async () => {
        await loadPage({ initialEquipment: [{ item_id: 'rope', quantity: 1 }] });

        const quantity = document.querySelector('#equipment-list [data-field="quantity"]');
        expect(quantity.getAttribute('max')).toBe(
            String(CONFIG_DEFAULTS.numberLimits.equipment_quantity),
        );
    });

    test("a weapon's modifier inputs stop at the server's bound both ways", async () => {
        await loadPage({ initialEquipment: [{ item_id: 'dagger', quantity: 1 }] });

        const bound = CONFIG_DEFAULTS.numberLimits.equipment_modifier;
        const modifiers = document.querySelectorAll(
            '#equipment-list [data-field$="_mod"]',
        );
        expect(modifiers.length).toBe(3);
        modifiers.forEach((input) => {
            expect(input.getAttribute('min')).toBe(String(-bound));
            expect(input.getAttribute('max')).toBe(String(bound));
        });
    });
});

describe('equipment markup escapes what it is handed (CodeQL js/xss-through-dom)', () => {
    // The config's limits and the stored items reach the row markup as data;
    // a value that is not the number it should be must stay text, never
    // become an element or an attribute.
    const hostile = '"><img src=x id=injected>';

    test('limits and item values that are not numbers stay inside their attributes', async () => {
        await loadPage({
            canSetItemModifiers: true,
            textLimits: { ...CONFIG_DEFAULTS.textLimits, item_notes: hostile },
            numberLimits: {
                ...CONFIG_DEFAULTS.numberLimits,
                equipment_quantity: hostile,
                equipment_modifier: hostile,
            },
            initialEquipment: [{ item_id: 'dagger', quantity: hostile, to_hit_mod: hostile }],
        });

        const list = document.getElementById('equipment-list');
        expect(list.querySelector('#injected')).toBeNull();
        expect(list.querySelector('[data-field="notes"]').getAttribute('maxlength')).toBe(hostile);
        expect(list.querySelector('[data-field="quantity"]').getAttribute('max')).toBe(hostile);
        expect(list.querySelector('[data-field="to_hit_mod"]').getAttribute('max')).toBe(hostile);
    });

    test('a read-only row shows a quantity that is not a number as text', async () => {
        await loadPage({
            canEdit: false,
            initialEquipment: [{ item_id: 'rope', quantity: hostile }],
        });

        const list = document.getElementById('equipment-list');
        expect(list.querySelector('#injected')).toBeNull();
        expect(list.textContent).toContain(`Qty: ${hostile}`);
    });
});

describe('DM mark dead and revive', () => {
    test('mark dead prompts for a cause and posts it', async () => {
        global.prompt = jest.fn(() => '  Fell down a well  ');
        await loadPage({}, [jsonResponse({ success: true })]);
        document.getElementById('mark-dead-btn').click();
        await flush();

        expect(fetch).toHaveBeenCalledWith(CONFIG_DEFAULTS.markDeadUrl, {
            method: 'POST',
            headers: { 'X-CSRFToken': 'test-token', 'Content-Type': 'application/json' },
            body: JSON.stringify({ cause: 'Fell down a well' }),
        });
        expect(pageNavigation.reload).toHaveBeenCalled();
    });

    test('a cancelled prompt posts nothing', async () => {
        global.prompt = jest.fn(() => null);
        await loadPage();
        fetch.mockClear();
        document.getElementById('mark-dead-btn').click();
        await flush();

        expect(fetch).not.toHaveBeenCalled();
    });

    test('a blank cause is refused before any request', async () => {
        global.prompt = jest.fn(() => '   ');
        await loadPage();
        fetch.mockClear();
        document.getElementById('mark-dead-btn').click();
        await flush();

        expect(alert).toHaveBeenCalledWith('Cause of death is required');
        expect(fetch).not.toHaveBeenCalled();
    });

    test('a rejected mark-dead re-enables the button', async () => {
        global.prompt = jest.fn(() => 'x');
        await loadPage({}, [jsonResponse({ success: false, error: 'Already dead' }, { ok: false })]);
        const btn = document.getElementById('mark-dead-btn');
        btn.click();
        await flush();

        expect(alert).toHaveBeenCalledWith('Already dead');
        expect(btn.disabled).toBe(false);
    });

    test('a network failure on mark dead re-enables the button and says so (#643)', async () => {
        global.prompt = jest.fn(() => 'x');
        await loadPage();
        fetch.mockRejectedValueOnce(new TypeError('Failed to fetch'));
        const btn = document.getElementById('mark-dead-btn');
        btn.click();
        await flush();

        expect(alert).toHaveBeenCalledWith('Network error — try again');
        expect(btn.disabled).toBe(false);
    });

    test('revive confirms, posts an empty body and reloads', async () => {
        await loadPage({}, [jsonResponse({ success: true })]);
        document.getElementById('revive-btn').click();
        await flush();

        expect(confirm).toHaveBeenCalledWith('Revive this character?');
        expect(fetch).toHaveBeenCalledWith(CONFIG_DEFAULTS.reviveUrl, {
            method: 'POST',
            headers: { 'X-CSRFToken': 'test-token', 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        });
        expect(pageNavigation.reload).toHaveBeenCalled();
    });

    test('a network failure on revive re-enables the button and says so (#643)', async () => {
        await loadPage();
        fetch.mockRejectedValueOnce(new TypeError('Failed to fetch'));
        const btn = document.getElementById('revive-btn');
        btn.click();
        await flush();

        expect(alert).toHaveBeenCalledWith('Network error — try again');
        expect(btn.disabled).toBe(false);
        expect(pageNavigation.reload).not.toHaveBeenCalled();
    });

    test('a declined revive posts nothing', async () => {
        confirm.mockReturnValue(false);
        await loadPage();
        fetch.mockClear();
        document.getElementById('revive-btn').click();
        await flush();

        expect(fetch).not.toHaveBeenCalled();
    });

    test('rolling Fatigue writes the roll, the pools and the readout (#636)', async () => {
        await loadPage({}, [jsonResponse({
            success: true,
            note: 'Fatigue roll (2d6) rolled 3 and 4 = 7',
            fatigue_roll: 7,
            fatigue: 31,
            body: 21,
            injury_thresholds_html: '<table class="injury-table"></table>',
            can_roll_fatigue: false,
        })]);

        document.getElementById('roll-fatigue-btn').click();
        await flush();

        expect(fetch).toHaveBeenCalledWith(
            '/tarmar/characters/7/roll-fatigue/',
            expect.objectContaining({ method: 'POST' })
        );
        expect(document.getElementById('fatigue-roll').textContent).toBe('7');
        expect(document.getElementById('fatigue').textContent).toBe('31');
        expect(document.getElementById('body').textContent).toBe('21');
        // A player has had their one roll (#590), so the control goes.
        expect(document.getElementById('roll-fatigue-btn')).toBeNull();
    });

    test('a Game Master keeps the control after rolling', async () => {
        await loadPage({}, [jsonResponse({
            success: true, note: 'rolled', fatigue_roll: 9, can_roll_fatigue: true,
        })]);

        document.getElementById('roll-fatigue-btn').click();
        await flush();

        const button = document.getElementById('roll-fatigue-btn');
        expect(button).not.toBeNull();
        expect(button.disabled).toBe(false);
    });

    test('a refused Fatigue roll says why and leaves the button usable', async () => {
        await loadPage({}, [jsonResponse(
            { error: 'The Fatigue roll is already recorded' }, { status: 400 }
        )]);

        document.getElementById('roll-fatigue-btn').click();
        await flush();

        expect(alert).toHaveBeenCalledWith('The Fatigue roll is already recorded');
        expect(document.getElementById('roll-fatigue-btn').disabled).toBe(false);
    });

    test('postJson survives a response with no JSON body', async () => {
        await loadPage();
        fetch.mockImplementation(() =>
            Promise.resolve({ ok: false, json: () => Promise.reject(new Error('not json')) })
        );
        const result = await characterEdit.postJson('/whatever/', {});
        expect(result).toMatchObject({ ok: false, data: {} });
    });
});

describe('current Fatigue and Body (#751)', () => {
    /** Put a Game Master's pool inputs into the readout, as the partial renders them. */
    function addPoolInputs() {
        document.getElementById('injury-thresholds').insertAdjacentHTML(
            'afterbegin',
            '<div id="current-pools">'
            + '<input type="number" class="current-pool-input" id="current-fatigue" data-pool="fatigue" value="30">'
            + '<input type="number" class="current-pool-input" id="current-body" data-pool="body" value="20">'
            + '</div>'
        );
    }

    test('a changed pool is saved when it loses focus, and the readout repainted', async () => {
        await loadPage({}, [jsonResponse({
            success: true,
            note: 'set by the Game Master to Fatigue 12/30, Body 20/20 (-1 to all rolls)',
            injury_thresholds_html: '<p id="current-pools-state">-1 to all rolls</p>',
        })]);
        addPoolInputs();
        fetch.mockClear();
        const input = document.getElementById('current-fatigue');
        input.value = '12';
        input.dispatchEvent(new Event('change', { bubbles: true }));
        await flush();

        expect(fetch).toHaveBeenCalledWith(
            '/tarmar/characters/7/set-pools/',
            expect.objectContaining({ method: 'POST', body: JSON.stringify({ fatigue: '12' }) })
        );
        // The server's re-render replaces the readout, inputs and all.
        expect(document.getElementById('current-pools-state').textContent)
            .toBe('-1 to all rolls');
        expect(document.getElementById('current-fatigue')).toBeNull();
    });

    test('a set Mana repaints the Magic section too', async () => {
        await loadPage({}, [jsonResponse({
            success: true,
            note: 'set by the Game Master to Mana 4/5',
            injury_thresholds_html: '<p id="current-pools-state">Unhurt</p>',
            current_mana: 4,
        })]);
        document.body.insertAdjacentHTML(
            'beforeend', '<strong id="mana-current">1</strong>'
        );
        document.getElementById('injury-thresholds').insertAdjacentHTML(
            'afterbegin',
            '<input type="number" class="current-pool-input" id="current-mana" data-pool="mana" value="1">'
        );
        const input = document.getElementById('current-mana');
        input.value = '4';
        input.dispatchEvent(new Event('change', { bubbles: true }));
        await flush();

        expect(document.getElementById('mana-current').textContent).toBe('4');
    });

    test('a refusal says why and leaves the input usable', async () => {
        await loadPage({}, [jsonResponse(
            { error: 'Only a Game Master can set current Fatigue and Body' },
            { status: 400 }
        )]);
        addPoolInputs();
        const input = document.getElementById('current-body');
        input.value = '5';
        input.dispatchEvent(new Event('change', { bubbles: true }));
        await flush();

        expect(alert).toHaveBeenCalledWith(
            'Only a Game Master can set current Fatigue and Body'
        );
        expect(input.disabled).toBe(false);
        // Back to what the record holds, not the refused number.
        expect(input.value).toBe('20');
    });

    test('a change anywhere else in the readout posts nothing', async () => {
        await loadPage();
        fetch.mockClear();
        document.getElementById('injury-thresholds').insertAdjacentHTML(
            'beforeend', '<input type="text" id="unrelated">'
        );
        document.getElementById('unrelated')
            .dispatchEvent(new Event('change', { bubbles: true }));
        await flush();

        expect(fetch).not.toHaveBeenCalled();
    });

    test('a network failure says so and leaves the input usable', async () => {
        await loadPage();
        addPoolInputs();
        fetch.mockImplementation(() => Promise.reject(new Error('offline')));
        const input = document.getElementById('current-fatigue');
        input.value = '3';
        input.dispatchEvent(new Event('change', { bubbles: true }));
        await flush();

        expect(alert).toHaveBeenCalledWith(FIELD_NETWORK_ERROR);
        expect(input.disabled).toBe(false);
        expect(input.value).toBe('30');
    });
});

describe('the literacy row (#740, #741)', () => {
    /** A response carrying every literacy key, as the server builds it. */
    function literacy(overrides = {}) {
        return {
            location_literacy_check: '3d6 ≤ INT − 4',
            location_literacy_modifier: 4,
            literacy_roll: null,
            is_literate: null,
            literacy_result: 'Not rolled',
            can_roll_literacy: true,
            can_change_origin: true,
            literacy_ruling: null,
            can_rule_literacy: false,
            ...overrides,
        };
    }

    /** Pick an Origin the way a player does: the select autosaves on change. */
    async function chooseOrigin(value) {
        const select = document.querySelector('[data-field="location"] select');
        select.value = value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
        await flush();
    }

    test('the keys are the ones the server sends', async () => {
        await loadPage();
        expect(characterEdit.LITERACY_RESPONSE_KEYS).toEqual([
            'location_literacy_check',
            'location_literacy_modifier',
            'literacy_roll',
            'is_literate',
            'literacy_result',
            'can_roll_literacy',
            'can_change_origin',
            'literacy_ruling',
            'can_rule_literacy',
        ]);
    });

    /** The row as a Game Master sees it at a Consult GM origin (#743). */
    function consultGm(overrides = {}) {
        return literacy({
            location_literacy_check: 'Consult GM',
            location_literacy_modifier: null,
            can_roll_literacy: false,
            can_rule_literacy: true,
            ...overrides,
        });
    }

    test('Consult GM shows a Game Master the ruling control with the ruling in it (#743)', async () => {
        await loadPage();
        routeFetch([
            jsonResponse({ success: true, ...consultGm({ literacy_ruling: true }) }),
            jsonResponse({ success: true, ...literacy() }),
        ]);

        await chooseOrigin('Consult GM');
        expect(document.getElementById('literacy-ruling-control').hidden).toBe(false);
        expect(document.getElementById('literacy-ruling-select').value).toBe('literate');

        await chooseOrigin('Rural');
        expect(document.getElementById('literacy-ruling-control').hidden).toBe(true);
        expect(document.getElementById('literacy-ruling-select').value).toBe('');
    });

    test('choosing a ruling posts it and paints the outcome with its provenance', async () => {
        await loadPage();
        routeFetch([
            jsonResponse({ success: true, ...consultGm() }),
            jsonResponse({
                success: true,
                note: 'ruled illiterate by the Game Master',
                ...consultGm({
                    literacy_ruling: false,
                    is_literate: false,
                    literacy_result: "Illiterate (Game Master's ruling)",
                }),
            }),
        ]);
        await chooseOrigin('Consult GM');

        const select = document.getElementById('literacy-ruling-select');
        select.value = 'illiterate';
        select.dispatchEvent(new Event('change', { bubbles: true }));
        await flush();

        expect(fetch).toHaveBeenCalledWith(
            CONFIG_DEFAULTS.ruleLiteracyUrl,
            expect.objectContaining({ body: JSON.stringify({ literate: false }) })
        );
        expect(document.getElementById('literacy-result').textContent)
            .toBe("Illiterate (Game Master's ruling)");
        expect(select.value).toBe('illiterate');
    });

    test('withdrawing the ruling posts null', async () => {
        await loadPage();
        routeFetch([
            jsonResponse({ success: true, ...consultGm({ literacy_ruling: true }) }),
            jsonResponse({ success: true, note: 'withdrawn', ...consultGm() }),
        ]);
        await chooseOrigin('Consult GM');

        const select = document.getElementById('literacy-ruling-select');
        select.value = '';
        select.dispatchEvent(new Event('change', { bubbles: true }));
        await flush();

        expect(fetch).toHaveBeenCalledWith(
            CONFIG_DEFAULTS.ruleLiteracyUrl,
            expect.objectContaining({ body: JSON.stringify({ literate: null }) })
        );
        expect(select.value).toBe('');
    });

    test('a refused ruling says why and puts the select back', async () => {
        await loadPage();
        routeFetch([
            jsonResponse({ success: true, ...consultGm({ literacy_ruling: true }) }),
            jsonResponse({ error: 'Only a Game Master rules on literacy' }, { ok: false }),
        ]);
        await chooseOrigin('Consult GM');

        const select = document.getElementById('literacy-ruling-select');
        select.value = 'illiterate';
        select.dispatchEvent(new Event('change', { bubbles: true }));
        await flush();

        expect(alert).toHaveBeenCalledWith('Only a Game Master rules on literacy');
        expect(select.value).toBe('literate');
        expect(select.disabled).toBe(false);
    });

    test('changing the Origin repaints the check without a reload (#740)', async () => {
        await loadPage();
        routeFetch([jsonResponse({ success: true, ...literacy() })]);

        await chooseOrigin('Rural');

        expect(fetch).toHaveBeenCalledWith(
            CONFIG_DEFAULTS.updateFieldUrl,
            expect.objectContaining({ body: JSON.stringify({ field: 'location', value: 'Rural' }) })
        );
        expect(document.getElementById('literacy-check').textContent).toBe('3d6 ≤ INT − 4');
        expect(document.getElementById('literacy-result').textContent).toBe('Not rolled');
        expect(document.getElementById('roll-literacy-btn').hidden).toBe(false);
        expect(pageNavigation.reload).not.toHaveBeenCalled();
    });

    test('Consult GM hides the control, and another origin brings it back', async () => {
        await loadPage();
        routeFetch([
            jsonResponse({
                success: true,
                ...literacy({
                    location_literacy_check: 'Consult GM',
                    location_literacy_modifier: null,
                    can_roll_literacy: false,
                }),
            }),
            jsonResponse({ success: true, ...literacy() }),
        ]);

        await chooseOrigin('Consult GM');
        expect(document.getElementById('literacy-check').textContent).toBe('Consult GM');
        expect(document.getElementById('roll-literacy-btn').hidden).toBe(true);

        await chooseOrigin('Rural');
        expect(document.getElementById('roll-literacy-btn').hidden).toBe(false);
    });

    test('a refused Origin says why beside the select and puts it back', async () => {
        await loadPage();
        const reason = 'The Origin is fixed once literacy is rolled; only a Game Master can change it.';
        routeFetch([jsonResponse({ error: reason }, { ok: false })]);

        const select = document.querySelector('[data-field="location"] select');
        select.value = 'City';
        select.dispatchEvent(new Event('change', { bubbles: true }));
        await flush();

        // Inline and persistent: the field's own error slot, not a toast.
        const error = document.querySelector('[data-field="location"] .autosave-error');
        expect(error.textContent).toBe(reason);
        expect(select.value).toBe('Village');
        expect(document.getElementById('literacy-check').textContent).toBe('3d6 ≤ INT − 2');
    });

    test('a Game Master\'s Origin change paints the new roll it brought', async () => {
        await loadPage();
        routeFetch([jsonResponse({
            success: true,
            ...literacy({
                location_literacy_check: '3d6 ≤ INT',
                location_literacy_modifier: 0,
                literacy_roll: 12,
                is_literate: false,
                literacy_result: 'Illiterate (rolled 12 > 10)',
                can_roll_literacy: true,
            }),
        })]);

        await chooseOrigin('City');

        expect(document.getElementById('literacy-check').textContent).toBe('3d6 ≤ INT');
        expect(document.getElementById('literacy-result').textContent)
            .toBe('Illiterate (rolled 12 > 10)');
        expect(document.querySelector('[data-field="location"] select').disabled).toBe(false);
    });

    test('a response without every literacy key leaves the row alone', async () => {
        await loadPage();
        routeFetch([jsonResponse({ success: true, location_literacy_check: 'stray' })]);

        const input = document.getElementById('id_name');
        input.value = 'Aldwin IV';
        input.dispatchEvent(new Event('blur'));
        await flush();

        expect(document.getElementById('literacy-check').textContent).toBe('3d6 ≤ INT − 2');
    });

    test('rolling posts to the roll URL and paints the recorded outcome (#741)', async () => {
        await loadPage({}, [jsonResponse({
            success: true,
            note: 'rolled the literacy check (3d6 ≤ INT − 2), 3d6: [3, 3, 3] = 9; 9 > 8, illiterate',
            ...literacy({
                location_literacy_check: '3d6 ≤ INT − 2',
                location_literacy_modifier: 2,
                literacy_roll: 9,
                is_literate: false,
                literacy_result: 'Illiterate (rolled 9 > 10 − 2)',
                can_roll_literacy: false,
                can_change_origin: false,
            }),
        })]);

        document.getElementById('roll-literacy-btn').click();
        await flush();

        expect(fetch).toHaveBeenCalledWith(
            '/tarmar/characters/7/roll-literacy/',
            expect.objectContaining({ method: 'POST' })
        );
        expect(document.getElementById('literacy-result').textContent)
            .toBe('Illiterate (rolled 9 > 10 − 2)');
        // A player has had their one roll (#590), so the control hides.
        expect(document.getElementById('roll-literacy-btn').hidden).toBe(true);
        expect(alert).toHaveBeenCalledWith(expect.stringContaining('9 > 8, illiterate'));
        // And their Origin is now fixed: the select locks with the reason
        // beside it, the state the server renders on a reload.
        expect(document.querySelector('[data-field="location"] select').disabled).toBe(true);
        expect(document.getElementById('origin-fixed-note').hidden).toBe(false);
    });

    test.each([
        ['roll-literacy-btn', { note: 'rolled the literacy check', ...literacy() }],
        ['roll-fatigue-btn', { note: 'Fatigue roll (2d6) rolled 3 and 4 = 7', fatigue_roll: 7 }],
    ])('a roll from %s is reported as a success, not an error', async (buttonId, body) => {
        await loadPage({}, [jsonResponse({ success: true, ...body })]);
        // notify.js is what the page has; the suites here otherwise take the
        // alert fallback, which drops the type.
        window.showNotice = jest.fn();
        try {
            document.getElementById(buttonId).click();
            await flush();

            expect(window.showNotice).toHaveBeenCalledWith(body.note, 'success');
        } finally {
            delete window.showNotice;
        }
    });

    test('a Game Master keeps a usable control after rolling', async () => {
        await loadPage({}, [jsonResponse({
            success: true,
            note: 'rolled',
            ...literacy({
                literacy_roll: 5,
                is_literate: true,
                literacy_result: 'Literate (rolled 5 ≤ 10 − 4)',
            }),
        })]);

        document.getElementById('roll-literacy-btn').click();
        await flush();

        const button = document.getElementById('roll-literacy-btn');
        expect(button.hidden).toBe(false);
        expect(button.disabled).toBe(false);
        expect(document.getElementById('literacy-result').textContent)
            .toBe('Literate (rolled 5 ≤ 10 − 4)');
        // A Game Master's Origin stays open.
        expect(document.querySelector('[data-field="location"] select').disabled).toBe(false);
        expect(document.getElementById('origin-fixed-note').hidden).toBe(true);
    });

    test('a refused roll says why and leaves the row as it was', async () => {
        await loadPage({}, [jsonResponse(
            { error: 'The literacy roll is already recorded' }, { ok: false }
        )]);

        document.getElementById('roll-literacy-btn').click();
        await flush();

        expect(alert).toHaveBeenCalledWith('The literacy roll is already recorded');
        expect(document.getElementById('literacy-result').textContent).toBe('Not rolled');
        expect(document.getElementById('roll-literacy-btn').disabled).toBe(false);
    });

    test('a network failure says so and leaves the control usable', async () => {
        await loadPage();
        fetch.mockImplementation(() => Promise.reject(new Error('offline')));

        document.getElementById('roll-literacy-btn').click();
        await flush();

        expect(alert).toHaveBeenCalledWith(FIELD_NETWORK_ERROR);
        expect(document.getElementById('roll-literacy-btn').disabled).toBe(false);
    });
});

describe('delete character confirmation', () => {
    test('asks before submitting, then submits', async () => {
        await loadPage();
        const form = document.getElementById('delete-character-form');
        form.submit = jest.fn();
        form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
        await flush();

        expect(confirm).toHaveBeenCalledWith('Delete this character? You can restore it later from the character list (Show deleted).');
        expect(form.submit).toHaveBeenCalled();
    });

    test('a declined confirmation leaves the form unsubmitted', async () => {
        confirm.mockReturnValue(false);
        await loadPage();
        const form = document.getElementById('delete-character-form');
        form.submit = jest.fn();
        form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
        await flush();

        expect(form.submit).not.toHaveBeenCalled();
    });
});

describe('autosave wiring', () => {
    test('a successful save writes back every derived value the response carries', async () => {
        await loadPage();
        routeFetch([jsonResponse({
            success: true,
            fatigue: 33, body: 22, physical: 19, mental: 20,
            dex_modifier: 3, dodge_modifier: 3,
            height_display: "6'0\"", weight_lbs: 180, starting_coin: 250, level: 4,
            // Every response carries the saved name (sheet_repaint, #903).
            name: 'Aldwin the Bolder',
            injury_thresholds_html:
                '<table class="injury-table"><tbody><tr>'
                + '<td id="injury-fatigue-minus_one_at">16</td>'
                + '</tr></tbody></table>',
        })]);

        const input = document.getElementById('id_name');
        input.value = 'Aldwin the Bolder';
        input.dispatchEvent(new Event('blur'));
        await flush();

        expect(document.getElementById('fatigue').textContent).toBe('33');
        expect(document.getElementById('body').textContent).toBe('22');
        expect(document.getElementById('physical').textContent).toBe('19');
        expect(document.getElementById('mental').textContent).toBe('20');
        // Editing DEX moves both combat tiles; nothing repainted them (#634).
        expect(document.getElementById('dex-combat-mod').textContent).toBe('+3');
        expect(document.getElementById('dodge-modifier').textContent).toBe('+3');
        expect(document.getElementById('height_display').textContent).toBe("(6'0\")");
        expect(document.getElementById('weight_lbs').textContent).toBe('180');
        expect(document.getElementById('character-level').textContent).toBe('4');
        expect(
            document.querySelector('[data-field="starting_coin"] input').value
        ).toBe('250');
        expect(document.title).toBe('Aldwin the Bolder');
    });

    test('a Game Master\'s XP save repaints the points, the level and the skills (#874)', async () => {
        await loadPage({ canGrantSkillPoints: true });
        document.getElementById('free-points').textContent = '0';
        document.querySelector('.add-skill-control').style.display = 'none';
        routeFetch([jsonResponse({
            success: true, level: 3, free_skill_points: 2, total_allocated_points: 5,
            skills_html: '<div class="skill-row" data-skill="perception">Perception</div>',
        })]);

        const input = document.querySelector('[data-field="total_xp"] input');
        input.value = '3000';
        input.dispatchEvent(new Event('blur'));
        await flush();

        expect(document.getElementById('free-points').textContent).toBe('2');
        expect(document.getElementById('character-level').textContent).toBe('3');
        expect(document.getElementById('allocated-points').textContent).toBe('5');
        expect(document.querySelector('#skills-list [data-skill="perception"]')).not.toBeNull();
        expect(document.querySelector('.add-skill-control').style.display).toBe('flex');
    });

    test('the injury readout is swapped whole for the server\'s rendering (#639)', async () => {
        // Cell-by-cell writing could only update a table that was already
        // there. An incomplete character has no thresholds and renders a line
        // saying so, and recording the last attribute crosses that line
        // without a reload -- so the server sends the block and the page
        // swaps it.
        await loadPage();
        routeFetch([jsonResponse({
            success: true,
            injury_thresholds_html:
                '<p class="injury-unknown" id="injury-unknown">Recorded once the '
                + 'attributes and the Fatigue roll are in.</p>',
        })]);

        const input = document.getElementById('id_name');
        input.value = 'Aldwin II';
        input.dispatchEvent(new Event('blur'));
        await flush();

        expect(document.querySelector('.injury-table')).toBeNull();
        expect(document.getElementById('injury-unknown').textContent).toContain(
            'Recorded once the attributes'
        );
    });

    test('a response carrying no injury readout leaves the one on the page', async () => {
        await loadPage();
        routeFetch([jsonResponse({ success: true, fatigue: 31 })]);

        const input = document.getElementById('id_name');
        input.value = 'Aldwin III';
        input.dispatchEvent(new Event('blur'));
        await flush();

        expect(document.getElementById('injury-fatigue-minus_one_at').textContent).toBe('15');
    });

    test('an emptied name never reaches the server', async () => {
        await loadPage();
        fetch.mockClear();
        const input = document.getElementById('id_name');
        input.value = '';
        input.dispatchEvent(new Event('blur'));
        await flush();

        expect(fetch).not.toHaveBeenCalled();
    });

    test('autosave posts to the config URL with the config token', async () => {
        await loadPage();
        routeFetch([jsonResponse({ success: true })]);
        const input = document.getElementById('id_name');
        input.value = 'Renamed';
        input.dispatchEvent(new Event('blur'));
        await flush();

        expect(fetch).toHaveBeenCalledWith(CONFIG_DEFAULTS.updateFieldUrl, {
            method: 'POST',
            headers: { 'X-CSRFToken': 'test-token', 'Content-Type': 'application/json' },
            body: JSON.stringify({ field: 'name', value: 'Renamed' }),
        });
    });
});


describe("a player's sheet: rolled and awarded values (issues #748, #749, #752)", () => {
    test('a read-only value repaints as text, an unset coin as Consult GM', async () => {
        await loadPage();
        document.querySelector('[data-field="total_xp"]').outerHTML =
            '<span class="value" data-readonly-field="total_xp">1500</span>';
        document.querySelector('[data-field="starting_coin"]').outerHTML =
            '<span class="value" data-readonly-field="starting_coin">100</span>';

        characterEdit.paintFieldInput('total_xp', 2500);
        characterEdit.paintFieldInput('starting_coin', null, 'Consult GM');

        expect(document.querySelector('[data-readonly-field="total_xp"]').textContent).toBe('2500');
        expect(document.querySelector('[data-readonly-field="starting_coin"]').textContent)
            .toBe('Consult GM');
    });

    test('a player sees an item\'s modifiers as text with who set them, and no inputs', async () => {
        await loadPage({
            canSetItemModifiers: false,
            initialEquipment: [
                { item_id: 'sword', to_hit_mod: 2, damage_mod: -1, modifiers_set_by: 'gm-one' },
                { item_id: 'leather', stops_mod: 1, equipped: true },
                { item_id: 'dagger' },
            ],
        });
        const [sword, leather, dagger] = document.querySelectorAll('.equipment-row');

        for (const field of ['to_hit_mod', 'damage_mod', 'critical_mod', 'stops_mod', 'dex_mod']) {
            expect(document.querySelector(`[data-field="${field}"]`)).toBeNull();
        }
        expect(sword.querySelector('.item-mods-granted').textContent)
            .toBe('Hit +2, Dmg -1 (set by gm-one, a Game Master)');
        expect(leather.querySelector('.item-mods-granted').textContent)
            .toBe('Stops +1 (not recorded who set them)');
        // The player still equips their own armour.
        expect(leather.querySelector('[data-field="equipped"]').checked).toBe(true);
        expect(dagger.querySelector('.item-mods-granted')).toBeNull();
    });

    test('a roll control posts to its own URL and reloads onto the saved result', async () => {
        await loadPage({}, [jsonResponse({ success: true, note: 'rolled wealth', can_roll: false })]);

        document.getElementById('roll-wealth-btn').click();
        await flush();

        const [url, init] = fetch.mock.calls.find(([called]) => called.includes('/roll/'));
        expect(url).toBe('/tarmar/characters/7/roll/wealth/');
        expect(init.method).toBe('POST');
        expect(pageNavigation.reload).toHaveBeenCalled();
    });

    test('a Game Master is asked before a roll replaces a recorded value', async () => {
        await loadPage({}, [jsonResponse({ success: true, note: 'rolled', can_roll: true })]);
        const button = document.getElementById('roll-wealth-btn');
        button.dataset.confirm = 'Roll wealth again?';
        confirm.mockReturnValueOnce(false);

        button.click();
        await flush();

        expect(confirm).toHaveBeenCalledWith('Roll wealth again?');
        expect(fetch.mock.calls.some(([url]) => url.includes('/roll/'))).toBe(false);

        button.click();
        await flush();
        expect(pageNavigation.reload).toHaveBeenCalled();
    });

    test('a network failure on a roll says so and leaves the control usable', async () => {
        await loadPage();
        fetch.mockImplementation((url) => (url.includes('/roll/')
            ? Promise.reject(new Error('offline'))
            : Promise.resolve(jsonResponse(CATALOG))));
        const button = document.getElementById('roll-wealth-btn');

        button.click();
        await flush();

        expect(button.disabled).toBe(false);
        expect(pageNavigation.reload).not.toHaveBeenCalled();
    });

    test('the roll controls follow the offers a field update returns', async () => {
        await loadPage();
        const button = document.getElementById('roll-wealth-btn');

        characterEdit.paintSheetRolls({
            wealth: { available: true, confirm: 'Roll wealth again?' },
            attributes: { available: false, confirm: '' },
        });
        expect(button.hidden).toBe(false);
        expect(button.dataset.confirm).toBe('Roll wealth again?');

        characterEdit.paintSheetRolls({ wealth: { available: false, confirm: '' } });
        expect(button.hidden).toBe(true);
        expect(button.dataset.confirm).toBeUndefined();

        characterEdit.paintSheetRolls(undefined);
        expect(button.hidden).toBe(true);
    });

    test('a bought score turns into text and its reason once saved', async () => {
        await loadPage({}, [jsonResponse({ success: true, locked_reason: 'Only a Game Master now.' })]);
        const input = document.querySelector('[data-field="strength"] input');

        input.value = '13';
        input.dispatchEvent(new Event('change', { bubbles: true }));
        input.dispatchEvent(new Event('blur'));
        await flush();

        expect(document.querySelector('[data-field="strength"]')).toBeNull();
        expect(document.querySelector('[data-readonly-field="strength"]').textContent).toBe('13');
        expect(document.querySelector('[data-locked-field="strength"]').textContent)
            .toBe('Only a Game Master now.');
    });

    test('a survival save swapped in with the injury block is wired too (issue #839)', async () => {
        await loadPage({}, [jsonResponse({ success: true, note: 'survival save' })]);
        characterEdit.refreshFatigueBodyInjury({
            injury_thresholds_html: '<button type="button" class="sheet-roll-btn"'
                + ' id="roll-survival-save-btn" data-roll-kind="survival_save"'
                + ' data-roll-url="/tarmar/characters/7/survival-save/"'
                + ' data-confirm="Roll the save?" data-confirm-label="Roll the save">'
                + 'Roll the survival save</button>',
        });
        const button = document.getElementById('roll-survival-save-btn');
        expect(button).not.toBeNull();

        button.click();
        await flush();

        expect(confirm).toHaveBeenCalledWith('Roll the save?');
        expect(fetch.mock.calls.some(([url]) => url === '/tarmar/characters/7/survival-save/'))
            .toBe(true);
        expect(pageNavigation.reload).toHaveBeenCalled();
    });

    test('a refused roll says why and leaves the control usable', async () => {
        await loadPage({}, [
            jsonResponse({ success: false, error: 'Wealth is already recorded' }, { ok: false }),
        ]);
        const button = document.getElementById('roll-wealth-btn');

        button.click();
        await flush();

        expect(pageNavigation.reload).not.toHaveBeenCalled();
        expect(button.disabled).toBe(false);
    });
});

describe('the sheet repaints what a change moves (issues #763-#766, #792, #797, #798, #804, #805, #846)', () => {
    /** Parts of character_edit.html the older fixture above does not carry. */
    function addSheetParts() {
        document.body.insertAdjacentHTML('beforeend', `
            <nav class="breadcrumb"><ol><li><a href="/tarmar/characters/">Characters</a></li>
                <li><span aria-current="page">Aldwin</span></li></ol></nav>
            <div id="sheet-completion"><p class="incomplete-marks" id="incomplete-marks">
                <span class="status-badge incomplete-mark">needs Fatigue roll</span></p></div>
            <p class="formula" id="point-buy-note">Type a missing score</p>
            <div class="summary-item" id="spell-points-item" hidden><span id="spell-points-x"></span></div>
            <strong id="mana-pool">0</strong>
            <div class="learn-spell needs-free-points" hidden></div>
            <div class="skill-check" id="skill-check" hidden>
                <select id="skill-check-skill"><option value="sword">Sword (DEX)</option>
                    <option value="stealth">Stealth (DEX) — untrained</option></select>
            </div>
            <p class="attack-incomplete" id="attack-incomplete">Attacks are resolved once this
                character is complete (<span class="incomplete-marks-list">needs Fatigue roll</span>).</p>
            <div id="attack-ready" hidden></div>
            <div id="dice-log" hidden><pre id="dice-log-text"></pre></div>
            <div id="recent-changes" hidden><table><tbody id="recent-changes-rows"></tbody></table></div>
        `);
    }

    const COMPLETE = {
        is_complete: true,
        completion_html: '<p class="export-links"><a href="/pdf">Export PDF</a></p>',
        incomplete_marks_html: '',
        is_alive: true,
        free_skill_points: 0,
        free_spell_points: 0,
        skill_check_options_html: '<option value="sword">Sword (DEX)</option>',
        recent_changes_html: '<tr><td>now</td><td>fatigue_roll</td><td></td><td>8</td><td>sam</td></tr>',
        dice_log: 'Fatigue: 2d6 [3, 5] = 8',
    };

    test('an STR edit repaints encumbrance, movement and carrying capacity (#763)', async () => {
        await loadPage();
        routeFetch([jsonResponse({
            success: true, physical: 16,
            carried_weight: 30, pack_weight: 0, lightest_load_limit: '14',
            encumbrance_level: 'medium', encumbrance_label: 'Medium', encumbrance_badge_classes: 'load-3', movement_penalty: -2,
            movement_restriction_label: 'Cannot Sprint', movement_modifier: 2,
            gait_readout: [{ key: 'walk', na: false, distance: 7 }, { key: 'run', na: true, distance: null }],
            armor_stops: 0, armor_dex_penalty: 0, dex_modifier: 1, dodge_modifier: 1,
            weapon_to_hit_mod: 0, weapon_damage_mod: 0, weapon_critical_mod: 0,
        })]);
        const input = document.querySelector('[data-field="strength"] input');
        input.value = '14';
        input.dispatchEvent(new Event('blur'));
        await flush();

        expect(document.getElementById('max-unencumbered').textContent).toBe('14');
        expect(document.getElementById('encumbrance-level').textContent).toBe('Medium');
        expect(document.getElementById('movement-penalty').textContent).toBe('-2 movement');
        expect(document.getElementById('movement-restriction').textContent).toBe('Cannot Sprint');
        expect(document.getElementById('movement-modifier').textContent).toBe('+2');
        expect(document.querySelector('#gait-walk .gait-value').textContent).toBe('7');
        expect(document.querySelector('#gait-run .gait-value').textContent).toBe('n/a');
    });

    test('an incomplete character\'s null values paint as the page renders them (#766)', async () => {
        await loadPage();
        const response = {
            success: true, equipment: [{ item_id: 'rope', quantity: 1 }], pack_dropped: false,
            carried_weight: 10, pack_weight: 0, lightest_load_limit: null,
            encumbrance_level: null, encumbrance_label: null, encumbrance_badge_classes: 'unknown', movement_penalty: 0,
            movement_restriction_label: '', movement_modifier: null,
            gait_readout: [{ key: 'walk', na: true, distance: null }, { key: 'run', na: true, distance: null }],
            armor_stops: 0, armor_dex_penalty: 0, dex_modifier: null, dodge_modifier: null,
            weapon_to_hit_mod: 0, weapon_damage_mod: 0, weapon_critical_mod: 0,
            attack_weapons: [], attack_gm_adjudicated_weapons: [],
        };
        await characterEdit.updateEquipmentDisplay(response);
        await flush();

        const badge = document.getElementById('encumbrance-level');
        expect(badge.textContent).toBe('—');
        expect(badge.className).toBe('level-badge unknown');
        expect(document.getElementById('movement-modifier').textContent).toBe('—');
        const walk = document.getElementById('gait-walk');
        expect(walk.querySelector('.gait-value').textContent).toBe('—');
        expect(walk.classList.contains('gait-unknown')).toBe(true);
        expect(walk.classList.contains('gait-na')).toBe(false);
        expect(document.getElementById('max-unencumbered').textContent).toBe('—');
        // The list rendered: the item is on the page, not lost behind a throw.
        expect(document.querySelectorAll('.equipment-row')).toHaveLength(1);

        characterEdit.refreshFatigueBodyInjury({ fatigue: null, body: null });
        characterEdit.paintText('weight_lbs', null);
        expect(document.getElementById('fatigue').textContent).toBe('—');
        expect(document.getElementById('body').textContent).toBe('—');
        expect(document.getElementById('weight_lbs').textContent).toBe('—');
    });

    test('rolling the Fatigue that completes a character shows exports, attacks and the check (#764)', async () => {
        await loadPage({}, [jsonResponse({
            success: true, note: 'rolled', fatigue_roll: 8, fatigue: 30, body: 20,
            can_roll_fatigue: false, ...COMPLETE,
        })]);
        addSheetParts();
        document.getElementById('roll-fatigue-btn').click();
        await flush();

        expect(document.querySelector('#sheet-completion .export-links')).not.toBeNull();
        expect(document.getElementById('incomplete-marks')).toBeNull();
        expect(document.getElementById('attack-incomplete').hidden).toBe(true);
        expect(document.getElementById('attack-ready').hidden).toBe(false);
        expect(document.getElementById('skill-check').hidden).toBe(false);
        expect(document.getElementById('dice-log').hidden).toBe(false);
        expect(document.getElementById('dice-log-text').textContent).toBe('Fatigue: 2d6 [3, 5] = 8');
        expect(document.getElementById('recent-changes').hidden).toBe(false);
        expect(document.getElementById('recent-changes-rows').textContent).toContain('fatigue_roll');
    });

    test('a field update that leaves a part missing repaints the marks it names (#764)', async () => {
        await loadPage();
        addSheetParts();
        characterEdit.paintSheet({
            is_complete: false,
            completion_html: '<p class="incomplete-marks" id="incomplete-marks"><span>needs attributes</span></p>',
            incomplete_marks_html: '<span>needs attributes</span>',
            point_buy_open: false,
        });
        expect(document.getElementById('incomplete-marks').textContent).toContain('needs attributes');
        expect(document.querySelector('.incomplete-marks-list').textContent).toBe('needs attributes');
        expect(document.getElementById('attack-ready').hidden).toBe(true);
        expect(document.getElementById('point-buy-note').hidden).toBe(true);
    });

    test('a rename replaces the skill check\'s options and keeps the choice when it survives (#765)', async () => {
        await loadPage({}, [jsonResponse({
            success: true, skill: 'blade', display: 'II', points: 3,
            ...COMPLETE,
            skill_check_options_html: '<option value="blade">Blade (DEX)</option>'
                + '<option value="stealth">Stealth (DEX) — untrained</option>',
        })]);
        addSheetParts();
        document.getElementById('skill-check-skill').value = 'stealth';
        const name = document.querySelector('.skill-name');
        name.textContent = 'Blade';
        await characterEdit.renameSkill(name);

        const options = [...document.querySelectorAll('#skill-check-skill option')].map((o) => o.value);
        expect(options).toEqual(['blade', 'stealth']);
        expect(document.getElementById('skill-check-skill').value).toBe('stealth');
    });

    test('a rename that joins two skills repaints the whole list (#792 sweep)', async () => {
        await loadPage({}, [jsonResponse({
            success: true, skill: 'survival', display: 'I', points: 2,
            ...COMPLETE,
            skills_html: '<div class="skill-row" data-skill="survival">Survival (2 pts)</div>',
        })]);
        addSheetParts();
        const name = document.querySelector('.skill-name');
        name.textContent = 'Survival';
        await characterEdit.renameSkill(name);

        const rows = document.querySelectorAll('#skills-list .skill-row');
        expect(rows).toHaveLength(1);
        expect(rows[0].textContent).toBe('Survival (2 pts)');
    });

    test('spell points a run grants move the Mana in hand (#792 sweep)', async () => {
        await loadPage();
        document.body.insertAdjacentHTML('beforeend', '<strong id="mana-current">0</strong>');
        characterEdit.paintSheet({ free_skill_points: 0, free_spell_points: 2, current_mana: 2 });
        expect(document.getElementById('mana-current').textContent).toBe('2');
    });

    test('a run that earns the first points shows the controls that spend them (#792)', async () => {
        await loadPage();
        addSheetParts();
        document.querySelector('.add-skill-control').style.display = 'none';
        characterEdit.paintSheet({ free_skill_points: 1, free_spell_points: 2, is_alive: true });

        expect(document.getElementById('free-points').textContent).toBe('1');
        expect(document.querySelector('.add-skill-control').style.display).toBe('flex');
        expect(document.querySelector('.learn-spell').hidden).toBe(false);
        expect(document.getElementById('spell-points-item').hidden).toBe(false);
        expect(document.getElementById('spell-points').textContent).toBe('2');
        expect(document.getElementById('mana-pool').textContent).toBe('2');

        // A death in the same run hides the new-skill control again.
        characterEdit.paintSheet({ free_skill_points: 1, is_alive: false });
        expect(document.querySelector('.add-skill-control').style.display).toBe('none');
    });

    test('renaming the character repaints the title and the breadcrumb (#792)', async () => {
        await loadPage();
        addSheetParts();
        routeFetch([jsonResponse({ success: true, name: 'Brannoc' })]);
        const input = document.getElementById('id_name');
        input.value = 'Brannoc';
        input.dispatchEvent(new Event('blur'));
        await flush();

        expect(document.title).toBe('Brannoc');
        expect(document.querySelector('.breadcrumb [aria-current="page"]').textContent).toBe('Brannoc');
    });

    test('a failed catalog load says so, still lists the items, and a retry recovers (#797)', async () => {
        jest.resetModules();
        document.body.innerHTML = pageHtml({
            ...CONFIG_DEFAULTS, initialEquipment: [{ item_id: 'rope', quantity: 2 }],
        });
        global.initAutosave = require('../autosave.js').initAutosave;
        let catalogOk = false;
        fetch.mockImplementation((url) => {
            if (url === CONFIG_DEFAULTS.catalogUrl) {
                return Promise.resolve(catalogOk
                    ? jsonResponse(CATALOG)
                    : jsonResponse('<html>login</html>', { ok: false }));
            }
            return Promise.resolve(jsonResponse({ success: true }));
        });
        characterEdit = require('../character-edit.js');
        await flush();

        const notice = document.getElementById('equipment-catalog-error');
        expect(notice).not.toBeNull();
        expect(notice.getAttribute('role')).toBe('alert');
        const row = document.querySelector('.equipment-row');
        expect(row.querySelector('.item-name').textContent).toContain('rope');

        const category = document.getElementById('equipment-category');
        category.value = 'weapons';
        category.dispatchEvent(new Event('change'));
        await flush();
        expect(document.getElementById('equipment-item').disabled).toBe(true);

        catalogOk = true;
        document.getElementById('equipment-catalog-retry').click();
        await flush();
        expect(document.getElementById('equipment-catalog-error')).toBeNull();
        expect(document.querySelector('.equipment-row .item-name').textContent).toContain('Rope');
    });

    test('a refused new-skill allocation keeps the name the player typed (#798)', async () => {
        await loadPage({}, [jsonResponse({ success: false, error: 'No free skill points available' })]);
        const input = document.getElementById('new-skill-name');
        input.value = 'Herbalism';
        await characterEdit.submitNewSkill();
        expect(input.value).toBe('Herbalism');
        expect(alert).toHaveBeenCalledWith('No free skill points available');
    });

    test('an accepted new-skill allocation clears the input (#798)', async () => {
        await loadPage({}, [jsonResponse({
            success: true, skill: 'herbalism', free_skill_points: 0, total_allocated_points: 4,
            row_html: '<div class="skill-row" data-skill="herbalism"></div>',
        })]);
        const input = document.getElementById('new-skill-name');
        input.value = 'Herbalism';
        await characterEdit.submitNewSkill();
        expect(input.value).toBe('');
    });

    test('readying a weapon posts the choice, and the Attack panel follows the hand (#846)', async () => {
        await loadPage({
            initialEquipment: [{ item_id: 'dagger' }, { item_id: 'sword' }],
            weaponInHandIndex: 0,
        });
        const radios = document.querySelectorAll('#equipment-list input[name="held-weapons"]');
        expect(radios).toHaveLength(2);
        expect(radios[0].checked).toBe(true);

        routeFetch([jsonResponse({
            success: true, equipment: [{ item_id: 'dagger' }, { item_id: 'sword', equipped: true }],
            weapon_in_hand_index: 1, shield_in_use_index: null,
            attack_weapons: [
                { index: 0, item_id: 'dagger', name: 'Dagger', weapon_class: 'Light', damage: '1d4', in_hand: false },
                { index: 1, item_id: 'sword', name: 'Sword', weapon_class: 'Medium', damage: '1d8', in_hand: true },
            ],
            attack_gm_adjudicated_weapons: [],
            weapon_to_hit_mod: 2,
        })]);
        radios[1].checked = true;
        radios[1].dispatchEvent(new Event('change', { bubbles: true }));
        await flush();

        const posted = JSON.parse(fetch.mock.calls.at(-1)[1].body);
        expect(posted).toEqual({ index: 1, equipped: true });
        const after = document.querySelectorAll('#equipment-list input[name="held-weapons"]');
        expect(after[1].checked).toBe(true);
        const select = document.getElementById('attack-weapon');
        expect(select.value).toBe('1');
        expect(select.selectedOptions[0].textContent).toContain('in hand');
        expect(document.getElementById('weapon-to-hit').textContent).toBe('+2');
    });

    test('a purchase and a run repaint the Wealth table\'s spent and earned lines (#792)', async () => {
        await loadPage();
        document.body.insertAdjacentHTML('beforeend',
            '<span id="coin-adjustment">0 gp</span><span id="wealth-earned">200 gp</span><strong id="purse">300 gp</strong>');
        await characterEdit.updateEquipmentDisplay({
            equipment: [], purse: '280 gp', coin_adjustment: '-20',
            attack_weapons: [], attack_gm_adjudicated_weapons: [],
        });
        expect(document.getElementById('coin-adjustment').textContent).toBe('-20 gp');
        expect(document.getElementById('purse').textContent).toBe('280 gp');

        characterEdit.updateSkillsUI({
            age: 21, free_skill_points: 0, total_allocated_points: 3, money_earned: 260,
        });
        expect(document.getElementById('wealth-earned').textContent).toBe('260 gp');
        expect(document.getElementById('money-earned').textContent).toBe('260 gp');
    });

    test('Recent Changes rows are written in the viewer\'s own time zone (#700 kept)', async () => {
        await loadPage();
        addSheetParts();
        window.applyLocalTimes = jest.fn();
        characterEdit.paintSheet({ recent_changes_html: '<tr><td><time class="local-time">x</time></td></tr>' });
        expect(window.applyLocalTimes).toHaveBeenCalledWith(document.getElementById('recent-changes-rows'));
        delete window.applyLocalTimes;
    });

    test('an equipment edit keeps the Attack panel\'s pick; a new weapon in hand is followed (#846)', async () => {
        await loadPage({
            initialEquipment: [{ item_id: 'dagger' }, { item_id: 'sword' }],
            weaponInHandIndex: 0,
        });
        const weapons = (inHand) => [
            { index: 0, item_id: 'dagger', name: 'Dagger', weapon_class: 'Light', damage: '1d4', in_hand: inHand === 0 },
            { index: 1, item_id: 'sword', name: 'Sword', weapon_class: 'Medium', damage: '1d8', in_hand: inHand === 1 },
        ];
        const response = (inHand) => ({
            equipment: [{ item_id: 'dagger' }, { item_id: 'sword' }],
            weapon_in_hand_index: inHand, attack_weapons: weapons(inHand), attack_gm_adjudicated_weapons: [],
        });
        await characterEdit.updateEquipmentDisplay(response(0));
        const select = document.getElementById('attack-weapon');
        expect(select.value).toBe('0');
        select.value = '1';
        // A notes edit, say: the hand did not change, the pick holds.
        await characterEdit.updateEquipmentDisplay(response(0));
        expect(select.value).toBe('1');
        select.value = '0';
        await characterEdit.updateEquipmentDisplay(response(1));
        expect(select.value).toBe('1');
    });

    test('the allocate controls stay at no points only where the server offers a grant (#770)', async () => {
        await loadPage({ canGrantSkillPoints: true });
        characterEdit.paintSheet({ free_skill_points: 0, is_alive: true });
        expect(document.querySelector('.add-skill-control').style.display).toBe('flex');
        await loadPage({ canGrantSkillPoints: false });
        characterEdit.paintSheet({ free_skill_points: 0, is_alive: true });
        expect(document.querySelector('.add-skill-control').style.display).toBe('none');
    });

    test('a shield beside a two-handed weapon says why it does not count (#804)', async () => {
        await loadPage({
            initialEquipment: [{ item_id: 'sword' }, { item_id: 'buckler' }],
            weaponInHandIndex: 0,
            shieldInUseIndex: null,
            shieldIdleReason: 'Not in use: the weapon in hand takes both hands, so no shield counts',
        });
        const shieldRow = document.querySelectorAll('.equipment-row')[1];
        expect(shieldRow.querySelector('.item-held-note').textContent).toContain('both hands');
        expect(shieldRow.querySelector('input[name="held-shield"]').checked).toBe(false);
    });

    test('a Game Master\'s purchase before the wealth roll leaves a lasting notice (#851)', async () => {
        await loadPage();
        await characterEdit.updateEquipmentDisplay({
            equipment: [], attack_weapons: [], attack_gm_adjudicated_weapons: [],
            notice: 'Bought before the wealth roll, from the placeholder starting coin',
        });
        expect(alert).toHaveBeenCalledWith('Bought before the wealth roll, from the placeholder starting coin');
    });

    test('a wealth change repaints the purse and the spent line (#757)', async () => {
        await loadPage();
        document.body.insertAdjacentHTML('beforeend',
            '<span id="coin-adjustment">0 gp</span><strong id="purse">100 gp</strong>');
        routeFetch([jsonResponse({ success: true, purse: '40 gp', coin_adjustment: '0' })]);
        const input = document.getElementById('id_name');
        input.value = 'Aldwin the Poor';
        input.dispatchEvent(new Event('blur'));
        await flush();
        expect(document.getElementById('purse').textContent).toBe('40 gp');
        expect(document.getElementById('coin-adjustment').textContent).toBe('0 gp');
    });

    test('an unknown height reads "—", not an empty pair of brackets (#805)', async () => {
        await loadPage();
        routeFetch([jsonResponse({ success: true, height_display: '' })]);
        const input = document.getElementById('id_name');
        input.value = 'Aldwin the Tall';
        input.dispatchEvent(new Event('blur'));
        await flush();
        expect(document.getElementById('height_display').textContent).toBe('(—)');
    });
});

describe('the states a reload used to be needed for (#903)', () => {
    /** The parts of the sheet this issue marks, as the templates render them. */
    function addLifeParts() {
        document.body.insertAdjacentHTML('beforeend', `
            <nav class="breadcrumb"><ol><li><span aria-current="page">Aldwin</span></li></ol></nav>
            <div class="play-with-gm-slot" id="play-with-gm-7">
                <form class="play-with-gm" data-character-id="7" data-attach-url="/gm/attach/"
                      data-chat-url="/gm/"><label>Take Aldwin to</label>
                    <button type="submit">Play with the Game Master</button>
                    <p class="play-with-gm-error" hidden></p></form>
            </div>
            <section id="sessions-holding"><span><span data-name-chrome>Aldwin</span> is in
                <strong>Kell's table</strong></span>
                <a class="sessions-holding-fight">play <span data-name-chrome>Aldwin</span>'s turns</a></section>
            <div class="magic-controls" id="magic-controls" data-alive-only>
                <div class="learn-spell needs-free-points"></div>
            </div>
            <datalist id="skill-name-suggestions"><option value="Sword"></option></datalist>
        `);
    }

    /** An empty slot: the attach would be refused (dead or incomplete). */
    const EMPTY_SLOT = '<div class="play-with-gm-slot" id="play-with-gm-7">\n</div>';
    /** The slot with its form, rendered hidden as the partial renders it. */
    const OFFERED_SLOT = '<div class="play-with-gm-slot" id="play-with-gm-7">'
        + '<form class="play-with-gm" data-character-id="7" hidden><label>Take Brannoc to</label>'
        + '<button type="submit">Play with the Game Master</button></form></div>';

    const FATAL_RUN = {
        success: true,
        run_result: { years: [{ year: 5, survival_check: { text: '3d6 [1, 1, 1] - 5 = -2 vs 9' } }] },
        run_html: '<details class="term-entry"><summary>Campaigner, Age 21</summary></details>',
        character: {
            age: 21, experience_years: 5, total_xp: 1200, level: 1, money_earned: 200,
            free_skill_points: 1, free_spell_points: 0, total_allocated_points: 3,
            is_alive: false, death_cause: 'Died on campaign',
        },
        // sheet_repaint's keys, as the add-years response carries them.
        name: 'Aldwin',
        is_alive: false,
        free_skill_points: 1,
        play_with_gm_html: EMPTY_SLOT,
    };

    test('a fatal run shows Revive, hides Mark Dead and the magic controls, and empties the slot', async () => {
        await loadPage({}, [jsonResponse(FATAL_RUN)]);
        addLifeParts();
        expect(document.getElementById('revive-btn').hidden).toBe(true);

        document.getElementById('roll-years-btn').click();
        await flush();

        expect(document.getElementById('mark-dead-btn').hidden).toBe(true);
        expect(document.getElementById('revive-btn').hidden).toBe(false);
        expect(document.getElementById('magic-controls').hidden).toBe(true);
        expect(document.querySelector('#play-with-gm-7 form')).toBeNull();
        expect(document.querySelector('#workbench-card .death-card h3').textContent).toBe('Aldwin died');
        expect(pageNavigation.reload).not.toHaveBeenCalled();
    });

    test('the death card names the saved character, not the text in the name input', async () => {
        await loadPage({}, [jsonResponse({ ...FATAL_RUN, name: 'Aldwin the Late' })]);
        document.getElementById('id_name').value = 'half-typed';

        document.getElementById('roll-years-btn').click();
        await flush();

        expect(document.querySelector('#workbench-card .death-card h3').textContent)
            .toBe('Aldwin the Late died');
    });

    test('a living record shows the living controls and hides Revive', async () => {
        await loadPage();
        addLifeParts();
        characterEdit.paintSheet({ is_alive: false });
        characterEdit.paintSheet({ is_alive: true });

        expect(document.getElementById('mark-dead-btn').hidden).toBe(false);
        expect(document.getElementById('revive-btn').hidden).toBe(true);
        expect(document.getElementById('magic-controls').hidden).toBe(false);
    });

    test('a response without is_alive leaves the life controls alone', async () => {
        await loadPage();
        addLifeParts();
        characterEdit.paintSheet({ free_skill_points: 0 });

        expect(document.getElementById('mark-dead-btn').hidden).toBe(false);
        expect(document.getElementById('revive-btn').hidden).toBe(true);
    });

    test('a rename paints the saved name into every marked place, the title and the breadcrumb', async () => {
        await loadPage();
        addLifeParts();
        document.getElementById('workbench-card').innerHTML =
            '<div class="death-card"><h3><span data-name-chrome>Aldwin</span> died</h3></div>';
        // The server answers with the name it saved, which wins over the typing.
        routeFetch([jsonResponse({ success: true, name: 'Brannoc', play_with_gm_html: OFFERED_SLOT })]);
        const input = document.getElementById('id_name');
        input.value = '  Brannoc  ';
        input.dispatchEvent(new Event('blur'));
        await flush();

        expect(document.title).toBe('Brannoc');
        expect(document.querySelector('.breadcrumb [aria-current="page"]').textContent).toBe('Brannoc');
        expect([...document.querySelectorAll('[data-name-chrome]')].map((element) => element.textContent))
            .toEqual(['Brannoc', 'Brannoc', 'Brannoc']);
        expect(document.querySelector('#play-with-gm-7 label').textContent).toBe('Take Brannoc to');
    });

    test('completing the character in place offers the slot, revealed as at load', async () => {
        await loadPage();
        require('../play-with-gm.js');
        document.body.insertAdjacentHTML('beforeend', EMPTY_SLOT);

        characterEdit.paintSheet({ play_with_gm_html: OFFERED_SLOT });

        const form = document.querySelector('#play-with-gm-7 form.play-with-gm');
        expect(form).not.toBeNull();
        expect(form.hidden).toBe(false);
        expect(document.querySelectorAll('.play-with-gm-slot')).toHaveLength(1);
    });

    test('a response that leaves the slot as it was keeps the choice, the refusal and focus', async () => {
        await loadPage();
        require('../play-with-gm.js');
        const slotHtml = '<div class="play-with-gm-slot" id="play-with-gm-7">\n'
            + '<form class="play-with-gm" data-character-id="7" data-attach-url="/gm/attach/"'
            + ' data-chat-url="/gm/" hidden>\n'
            + '<label for="play-with-gm-session-7">Take Aldwin to</label>\n'
            + '<select id="play-with-gm-session-7" name="session_id">\n'
            + '<option value="">A new session</option>\n'
            + '<option value="12">Kell table</option>\n'
            + '</select>\n'
            + '<button type="submit" class="button">Play with the Game Master</button>\n'
            + '<p class="play-with-gm-error" role="alert" hidden></p>\n'
            + '</form>\n</div>';
        document.body.insertAdjacentHTML('beforeend', slotHtml);
        window.revealPlayWithGameMaster(document);
        const slot = document.getElementById('play-with-gm-7');
        const choice = document.getElementById('play-with-gm-session-7');
        choice.value = '12';
        const refusal = slot.querySelector('.play-with-gm-error');
        refusal.textContent = 'The Game Master could not take this character: Character is dead.';
        refusal.hidden = false;
        choice.focus();

        characterEdit.paintSheet({ play_with_gm_html: slotHtml });

        expect(document.getElementById('play-with-gm-7')).toBe(slot);
        expect(choice.value).toBe('12');
        expect(refusal.hidden).toBe(false);
        expect(refusal.textContent).toContain('Character is dead');
        expect(document.activeElement).toBe(choice);

        // A slot the record changed is still swapped in.
        characterEdit.paintSheet({ play_with_gm_html: slotHtml.replace('Take Aldwin to', 'Take Brannoc to') });
        expect(document.querySelector('#play-with-gm-7 label').textContent).toBe('Take Brannoc to');
    });

    describe('the slot\'s session picker (#973)', () => {
        const pickerSlot = (options) => '<div class="play-with-gm-slot" id="play-with-gm-7">\n'
            + '<form class="play-with-gm" data-character-id="7" data-attach-url="/gm/attach/"'
            + ' data-chat-url="/gm/" hidden>\n'
            + '<label for="play-with-gm-session-7">Take Aldwin to</label>\n'
            + '<div class="picker" id="play-with-gm-picker-7" data-picker'
            + ' data-picker-url="/gm/sessions/play-picker/?character=7">'
            + '<input type="search" id="play-with-gm-search-7" class="picker-search" data-picker-search value="">'
            + '<div class="picker-body" data-picker-body>'
            + '<select id="play-with-gm-session-7" name="session_id">'
            + '<option value="">A new session</option>' + options
            + '</select></div></div>\n'
            + '<button type="submit" class="button">Play with the Game Master</button>\n'
            + '<p class="play-with-gm-error" role="alert" hidden></p>\n'
            + '</form>\n</div>';
        const FIRST_PAGE = '<option value="12">Kell table</option>';

        async function mountPickerSlot() {
            await loadPage();
            require('../picker.js');
            require('../play-with-gm.js');
            document.body.insertAdjacentHTML('beforeend', pickerSlot(FIRST_PAGE));
            window.revealPlayWithGameMaster(document);
            return document.getElementById('play-with-gm-7');
        }

        test('a wired picker the viewer has not searched is not swapped for the same record', async () => {
            const slot = await mountPickerSlot();
            expect(slot.querySelector('[data-picker]').dataset.pickerReady).toBe('yes');

            characterEdit.paintSheet({ play_with_gm_html: pickerSlot(FIRST_PAGE) });

            expect(document.getElementById('play-with-gm-7')).toBe(slot);
        });

        test('a search the viewer made survives a repaint of the same record', async () => {
            const slot = await mountPickerSlot();
            const picker = slot.querySelector('[data-picker]');
            // What picker.js leaves after a search: a ticket and another page.
            picker.dataset.pickerTicket = '1';
            picker.querySelector('[data-picker-body]').innerHTML =
                '<select id="play-with-gm-session-7" name="session_id">'
                + '<option value="">A new session</option><option value="3">Old barrow</option></select>';

            characterEdit.paintSheet({ play_with_gm_html: pickerSlot(FIRST_PAGE) });

            expect(document.getElementById('play-with-gm-7')).toBe(slot);
            expect(slot.querySelector('option[value="3"]')).not.toBeNull();
        });

        test('a record change is still swapped in after a search, and its picker wired', async () => {
            const slot = await mountPickerSlot();
            slot.querySelector('[data-picker]').dataset.pickerTicket = '1';

            characterEdit.paintSheet({
                play_with_gm_html: pickerSlot(FIRST_PAGE).replace('Take Aldwin to', 'Take Brannoc to'),
            });

            const fresh = document.getElementById('play-with-gm-7');
            expect(fresh).not.toBe(slot);
            expect(fresh.querySelector('label').textContent).toBe('Take Brannoc to');
            expect(fresh.querySelector('[data-picker]').dataset.pickerReady).toBe('yes');
        });

        test('an unsearched picker takes the sessions the record now has', async () => {
            const slot = await mountPickerSlot();

            characterEdit.paintSheet({
                play_with_gm_html: pickerSlot(`<option value="15">New night</option>${FIRST_PAGE}`),
            });

            expect(document.getElementById('play-with-gm-7')).not.toBe(slot);
            expect(document.querySelector('#play-with-gm-7 option[value="15"]')).not.toBeNull();
        });
    });

    test('a page without the slot ignores the slot key', async () => {
        await loadPage();
        expect(() => characterEdit.paintSheet({ play_with_gm_html: OFFERED_SLOT })).not.toThrow();
        expect(document.getElementById('play-with-gm-7')).toBeNull();
    });

    test('a skill allocated in place joins the add-skill type-ahead', async () => {
        await loadPage();
        addLifeParts();
        characterEdit.paintSheet({
            skill_name_suggestions_html: '<option value="Sword"></option><option value="Weaving"></option>',
        });

        expect([...document.querySelectorAll('#skill-name-suggestions option')].map((option) => option.value))
            .toEqual(['Sword', 'Weaving']);
    });

    test('a run joining a full history drops the oldest, as the record does', async () => {
        await loadPage({ maxExperienceLogEntries: 2 });
        const log = document.getElementById('experience-log');
        log.innerHTML = '<details class="term-entry" id="newer"></details>'
            + '<details class="term-entry" id="oldest"></details>';

        characterEdit.prependRunToLog('<details class="term-entry" id="newest"></details>');

        expect([...log.querySelectorAll('.term-entry')].map((entry) => entry.id))
            .toEqual(['newest', 'newer']);
    });
});
