/**
 * Shared test fixtures and helpers for Playwright E2E tests.
 */

const { GATE_PASSPHRASE, passGateOnCurrentPage } = require('./gate');
const { test: base, expect } = require('@playwright/test');
const { startCoverage, stopCoverage } = require('./coverage');
const { BASE_ACCOUNTS, workerAccount } = require('./accounts');
const { readDjangoShell } = require('./django');

// baseURL is 127.0.0.1; the classic arena is the same server under another
// name. Chromium resolves any *.localhost name to loopback with no DNS, so the
// second front door can be driven before its real hostname exists. Cookies
// are per-origin: a page moving there must pass the gate and sign in again.
const ARENA_ORIGIN = `http://melee.localhost:${process.env.E2E_PORT || '8000'}`;


// This worker's copy of each seeded account (issue #620; see accounts.js).
// Playwright sets TEST_PARALLEL_INDEX in every worker process; a spec that
// seeds rows for one of these users must name it through these constants.
const PARALLEL_INDEX = Number(process.env.TEST_PARALLEL_INDEX || 0);
const TEST_USER = workerAccount(BASE_ACCOUNTS.user, PARALLEL_INDEX);
const TEST_ADMIN = workerAccount(BASE_ACCOUNTS.admin, PARALLEL_INDEX);
// Seeded by global-setup with no roles and no seats: the spectator.
const TEST_WATCHER = workerAccount(BASE_ACCOUNTS.watcher, PARALLEL_INDEX);

/**
 * Submit the passphrase gate so subsequent requests are authenticated.
 * @param {import('@playwright/test').Page} page
 */
async function submitPassphrase(page) {
    await page.goto('/');
    await passGateOnCurrentPage(page);
}

/**
 * Extended test fixture with login helper and coverage collection.
 */
const test = base.extend({
    // Auto-collect coverage for all tests
    page: async ({ page }, use, testInfo) => {
        await startCoverage(page);
        // Pass through the passphrase gate once per test
        await submitPassphrase(page);
        await use(page);
        await stopCoverage(page, testInfo.title);
    },

    // Authenticated page fixture
    authenticatedPage: async ({ page }, use) => {
        await loginAs(page, TEST_USER);
        await use(page);
    },

    // Admin authenticated page fixture
    adminPage: async ({ page }, use) => {
        await loginAs(page, TEST_ADMIN);
        await use(page);
    }
});

/**
 * Log in as a specific user.
 * @param {import('@playwright/test').Page} page
 * @param {{username: string, password: string}} user
 * @param {{origin?: string}} [options] - sign in at another front door, e.g.
 *     ARENA_ORIGIN. Empty means the default baseURL.
 */
async function loginAs(page, user, { origin = '' } = {}) {
    await page.goto(`${origin}/tarmar/accounts/login/`);
    await page.fill('input[name="username"]', user.username);
    await page.fill('input[name="password"]', user.password);
    await page.click('button[type="submit"]');
    // Wait for redirect to complete
    await page.waitForURL(url => !url.pathname.includes('/login'));
}

/**
 * Log out the current user.
 * @param {import('@playwright/test').Page} page
 */
async function logout(page) {
    // Open menu first
    await page.click('#menuToggle');
    // Logging out is a POST, so the menu's control is a button that submits
    // the hidden logout form — there is no logout anchor to click.
    await page.click('.menu button[form="logout-form"]');
    // The form only renders for a signed-in user, so its absence is the
    // signal that the POST landed and the new page has rendered.
    await page.locator('#logout-form').waitFor({ state: 'detached' });
}

/**
 * Give an account a character with a history -- years, skills, gear -- and
 * open its sheet.
 *
 * Specs used to bring such a character in through the list page's import
 * form. A player's import now keeps only what a player sets on the sheet
 * (issue #744), so a player cannot import one; this writes the file's fields as they stand, through
 * the importer's own validation and the one create path, for the account the
 * spec plays as.
 * @param {import('@playwright/test').Page} page
 * @param {{username: string}} account
 * @param {object} payload - A tarmar-character export document.
 * @returns {Promise<string>} The new character's id.
 */
async function seedCharacterFromExport(page, account, payload) {
    const output = readDjangoShell(`
import json
from django.contrib.auth import get_user_model
from characters import services
from characters.import_service import validate_import_payload
imported = validate_import_payload(json.loads(${JSON.stringify(JSON.stringify(payload))}))
user = get_user_model().objects.get(username=${JSON.stringify(account.username)})
print('CHARACTER', services.create_character(user, imported.name, imported.fields).pk)
`);
    const match = output.match(/CHARACTER (\d+)/);
    expect(match, `no character id in: ${output}`).not.toBeNull();
    await page.goto(`/tarmar/characters/${match[1]}/`);
    return match[1];
}

/**
 * Create a character via the UI, rolling 3d6 for the core attributes.
 * The creation page asks for the name (issue #786); this gives it one.
 * @param {import('@playwright/test').Page} page
 * @param {string} [characterName] - The name typed on the roll card.
 */
async function createCharacter(page, characterName = 'E2E Hero') {
    await page.goto('/tarmar/characters/');
    await page.click('a:has-text("Create New Character")');
    await page.waitForURL('**/characters/create/');
    // The roll card asks for the name (issue #786); a player must give one.
    await page.fill('#roll-name', characterName);
    await page.click('button:has-text("Roll a New Character")');
    // Wait for character to be created and redirected
    await page.waitForURL(/\/characters\/\d+\//);
}

/**
 * Create a character on this page's account and rename it, so a test can find
 * it by an unmistakable name later. Ends on the character editor.
 * @param {import('@playwright/test').Page} page
 * @param {string} characterName
 */
async function createNamedCharacter(page, characterName) {
    await createCharacter(page);
    await page.fill('#id_name', characterName);
    await page.locator('#id_name').blur();
    await expect(page.locator('.name-field')).toHaveClass(/saved/);
}

// The archetype cycle as the setup page's fighter buttons offer it, in the
// same order generation cycles through, so a plain numeric `fighters` count
// reproduces the old create form's round-robin roster exactly.
const SETUP_ARCHETYPES = [
    'armoured fighter',
    'unarmoured duelist',
    'brawler',
    'archer',
    'wizard',
    'fighter-mage',
];

/**
 * Create a battle by driving the staged setup flow (#270/#271); ends on the
 * battle detail page (lobby or arena).
 *
 * @param {import('@playwright/test').Page} page - a DM's page
 * @param {object} options
 * @param {string} options.name - battle name (required)
 * @param {string|number} [options.seed] - deterministic seed
 * @param {number|string[]} [options.fighters] - generated fighters: an array
 *     of archetype names for per-slot control, or a count that draws the
 *     archetype cycle round-robin like the old form's `count` did
 * @param {number} [options.playerSeats] - open seats to add
 * @param {string[]} [options.beasts] - catalog beast slugs
 * @param {string} [options.rulesProfile] - e.g. 'classic-melee'
 * @param {string[]} [options.characterNames] - saved characters to seat,
 *     matched by their visible names on the setup page's shelf
 * @param {boolean} [options.autoStart]
 * @param {string} [options.origin] - drive the flow at another front door,
 *     e.g. 'http://melee.localhost:8000'. Empty means the default baseURL.
 *     Cookies are per-origin, so the caller passes a page already gated and
 *     signed in *there*.
 */
/**
 * The row for ``battleName`` on the battle list, turning pages until it is
 * there (#795). The list is paged at 50, newest first, and a full run makes
 * battles on every worker, so a battle a spec made a moment ago is not
 * certain to be on page 1. Starts from ``listUrl`` (a filter such as
 * ``?show=deleted`` keeps across pages) and returns the row locator; when no
 * page has the row it returns the last page's empty locator, so the
 * caller's own expectation fails naming the battle. The pages are read one
 * after another, not at once: a battle another worker deletes (or makes)
 * meanwhile shifts every later row by one, so a row can move across the
 * page boundary between reads and be missed.
 */
async function battleRowOnList(page, battleName, listUrl = '/tarmar/battles/') {
    await page.goto(listUrl);
    const row = page.locator('tbody tr', { hasText: battleName });
    for (;;) {
        if (await row.count() > 0) { return row; }
        const next = page.locator('.pagination a[rel="next"]');
        if (await next.count() === 0) { return row; }
        await Promise.all([page.waitForNavigation(), next.click()]);
    }
}

async function createBattleViaSetup(page, options) {
    const {
        name,
        seed,
        fighters = 0,
        playerSeats = 0,
        beasts = [],
        rulesProfile = '',
        characterNames = [],
        autoStart = false,
        controlMode = '',
        origin = '',
    } = options;
    await page.goto(`${origin}/tarmar/battles/`);
    await page.click('.battle-list-masthead button[type="submit"]');
    await page.waitForURL(/\/tarmar\/battles\/setup\/\d+\/$/);

    const archetypes = Array.isArray(fighters)
        ? fighters
        : Array.from(
            { length: fighters },
            (_unused, index) => SETUP_ARCHETYPES[index % SETUP_ARCHETYPES.length]
        );
    for (const archetype of archetypes) {
        await page.click(
            `.setup-fighter-grid form:has(input[value="${archetype}"]) button`
        );
        await page.waitForURL(/\/setup\/\d+\/$/);
    }
    for (const characterName of characterNames) {
        await page.click(
            `.setup-character:has-text(${JSON.stringify(characterName)}) button:has-text("Seat")`
        );
        await page.waitForURL(/\/setup\/\d+\/$/);
    }
    for (const slug of beasts) {
        const beastForm = page.locator('.setup-beast-form').first();
        await beastForm.locator('select[name="slug"]').selectOption(slug);
        await beastForm.locator('button').click();
        await page.waitForURL(/\/setup\/\d+\/$/);
    }
    for (let seat = 0; seat < playerSeats; seat += 1) {
        await page.click('button:has-text("Add an open seat")');
        await page.waitForURL(/\/setup\/\d+\/$/);
    }

    await page.fill('input[name="name"]', name);
    if (seed !== undefined) {
        await page.fill('input[name="seed"]', String(seed));
    }
    if (rulesProfile) {
        await page.selectOption('select[name="rules_profile"]', rulesProfile);
    }
    // Who decides (#308) — the battle-level default the muster is created with.
    if (controlMode) {
        await page.selectOption('select[name="control_mode"]', controlMode);
    }
    if (autoStart) {
        await page.check('input[name="auto_start"]');
    }
    await page.click('.setup-begin');
    await page.waitForURL(/\/tarmar\/battles\/\d+\/$/);
}

/**
 * POST one JSON request as this worker's Game Master, from a context of its
 * own, and return {status, data}. For seeding what only a Game Master may
 * give -- coin, mana, a free item -- without a player importing it (the
 * #757 review: a player's import is not a way to grant anything).
 * @param {import('@playwright/test').Browser} browser
 * @param {string} path - e.g. `/tarmar/characters/7/coin/`
 * @param {object} body
 */
async function postAsGameMaster(browser, path, body) {
    const context = await browser.newContext();
    try {
        const gmPage = await context.newPage();
        await submitPassphrase(gmPage);
        await loginAs(gmPage, TEST_ADMIN);
        return await gmPage.evaluate(async ({ url, payload }) => {
            // The CSRF cookie is HttpOnly; base.html's meta tag carries it.
            const token = document.querySelector('meta[name="csrf-token"]').content;
            const response = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
                body: JSON.stringify(payload),
            });
            return { status: response.status, data: await response.json() };
        }, { url: path, payload: body });
    } finally {
        await context.close();
    }
}

/** The character id in the current page's /characters/<id>/ URL. */
function characterIdFromUrl(page) {
    return page.url().match(/\/characters\/(\d+)\//)[1];
}

/**
 * Type into a searchable picker (issue #864) and wait for the server's
 * answer to land, so what the spec does next acts on the rows that match
 * rather than on the page the search is about to replace.
 * @param {import('@playwright/test').Page} page
 * @param {string} searchSelector - the picker's search box
 * @param {string} text - what to search for
 */
async function searchPicker(page, searchSelector, text) {
    const box = page.locator(searchSelector);
    await box.fill(text);
    const picker = page.locator('[data-picker]', { has: box });
    await expect(picker.locator('[data-picker-status]')).toContainText(`\u201c${text}\u201d`);
}

/**
 * Choose a character in the GM panel's attach picker by name: search for it,
 * then select it, as a reader with more characters than one page would.
 * @param {import('@playwright/test').Page} page
 * @param {string} name
 */
async function chooseToAttach(page, name) {
    await searchPicker(page, '#gm-attach-picker-search', name);
    await page.selectOption('#gm-character-select', { label: name });
}

/** The canon name the "World:" line prints when no TARMAR_CANON_DIR is set. */
const DEFAULT_CANON = 'default canon';

/**
 * The loaded canon's name, as the page in hand prints it on its "World:"
 * line (the GM chat and the reference pages carry one): "Tarmar canon" or
 * "default canon". A spec whose numbers belong to one canon reads it and
 * skips under the other rather than assuming which one the server runs.
 * @param {import('@playwright/test').Page} page - on a page with the line
 * @returns {Promise<string>}
 */
async function loadedCanon(page) {
    const line = await page.locator('.canon-line').textContent();
    return line.replace('World:', '').trim();
}

module.exports = {
    DEFAULT_CANON,
    loadedCanon,
    ARENA_ORIGIN,
    battleRowOnList,
    GATE_PASSPHRASE,
    passGateOnCurrentPage,
    test,
    expect,
    TEST_USER,
    TEST_ADMIN,
    TEST_WATCHER,
    loginAs,
    logout,
    createCharacter,
    createNamedCharacter,
    createBattleViaSetup,
    seedCharacterFromExport,
    submitPassphrase,
    postAsGameMaster,
    characterIdFromUrl,
    searchPicker,
    chooseToAttach,
};
