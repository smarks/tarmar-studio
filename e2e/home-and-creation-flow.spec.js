/**
 * From signing in to sitting at the Game Master's table (issues #786, #827,
 * #828), in a real browser at the three widths the UI review walked.
 *
 * - #786: the creation page never asked for a name, so every character was
 *   called after its account. Both cards now ask; a player must answer, a
 *   Game Master may leave it blank as before.
 * - #827: after signing in the home page offered nothing of the player's own.
 *   It now lists their characters, sessions and battles.
 * - #828: the sheet had no way to take the character to the Game Master. It
 *   now attaches the character to a new or chosen session and opens it.
 *
 * Each viewport's page is saved as a screenshot beside the run's results, so
 * the layout at 1440, 1024 and 390 can be read, not only asserted.
 */

const {
    test, expect, loginAs, createCharacter, TEST_USER, TEST_ADMIN, TEST_WATCHER,
} = require('./fixtures');

const CREATE_URL = '/tarmar/characters/create/';
const HOME_URL = '/tarmar/';

const VIEWPORTS = [
    { name: 'desktop', width: 1440, height: 900 },
    { name: 'laptop', width: 1024, height: 768 },
    { name: 'phone', width: 390, height: 844 },
];

/**
 * A name nobody else's run will use.
 * @param {string} prefix
 * @param {import('@playwright/test').TestInfo} testInfo
 */
function uniqueName(prefix, testInfo) {
    return `${prefix} ${testInfo.workerIndex}${Date.now()}`;
}

/**
 * Visit `url` at each width, check nothing scrolls sideways, and keep a
 * full-page screenshot of each.
 * @param {import('@playwright/test').Page} page
 * @param {string} url
 * @param {string} label - Names the screenshot files.
 * @param {import('@playwright/test').TestInfo} testInfo
 * @param {function(): Promise<void>} [check] - Run at each width.
 */
async function atEveryWidth(page, url, label, testInfo, check = async () => {}) {
    for (const viewport of VIEWPORTS) {
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await page.goto(url);
        await check();
        const overflow = await page.evaluate(
            () => document.documentElement.scrollWidth - document.documentElement.clientWidth
        );
        expect(overflow, `${label} scrolls sideways at ${viewport.width}px`).toBeLessThanOrEqual(0);
        const path = testInfo.outputPath(`${label}-${viewport.width}.png`);
        await page.screenshot({ path, fullPage: true });
        await testInfo.attach(`${label}-${viewport.width}`, { path, contentType: 'image/png' });
    }
    await page.setViewportSize({ width: VIEWPORTS[0].width, height: VIEWPORTS[0].height });
}

test.describe('the creation page asks for a name (#786)', () => {
    test('a player names the character before it is rolled', async ({ page }, testInfo) => {
        await loginAs(page, TEST_USER);
        await atEveryWidth(page, CREATE_URL, 'create-player', testInfo, async () => {
            await expect(page.locator('#roll-name')).toBeVisible();
            await expect(page.locator('#point-buy-name')).toBeVisible();
            await expect(page.locator('label[for="roll-name"]')).toHaveText('Name');
        });

        await page.goto(CREATE_URL);
        // Without a name the browser holds the roll back; nothing is created.
        await page.click('button:has-text("Roll a New Character")');
        await expect(page).toHaveURL(new RegExp(`${CREATE_URL}$`));
        expect(await page.locator('#roll-name').evaluate((input) => input.validity.valueMissing)).toBe(true);

        const name = uniqueName('Named', testInfo);
        await page.fill('#roll-name', name);
        await page.click('button:has-text("Roll a New Character")');

        await expect(page).toHaveURL(/\/characters\/\d+\/$/);
        await expect(page.locator('#id_name')).toHaveValue(name);
        await expect(page).toHaveTitle(name);
    });

    test('a name the sheet would refuse comes back with the reason and the rest kept', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto(CREATE_URL);

        await page.fill('#point-buy-name', 'Wren\u{1F525}');
        await page.locator('#point-buy-form input[name="visibility"][value="public"]').check();
        await page.click('#point-buy-submit');

        const refusal = page.locator('#point-buy-server-error');
        await expect(refusal).toContainText('That character was not created.');
        await expect(refusal).toContainText('Name may contain letters, numbers, spaces');
        await expect(page).toHaveURL(new RegExp(`${CREATE_URL}$`));
        await expect(page.locator('#point-buy-name')).toHaveValue('Wren\u{1F525}');
        await expect(page.locator('#point-buy-form input[name="visibility"][value="public"]')).toBeChecked();
    });

    test('a Game Master may still leave it blank and gets the account name', async ({ adminPage: page }, testInfo) => {
        await atEveryWidth(page, CREATE_URL, 'create-game-master', testInfo, async () => {
            await expect(page.locator('#roll-name')).not.toHaveAttribute('required', '');
            await expect(page.locator('.name-choice-note').first()).toContainText(TEST_ADMIN.username);
        });

        await page.goto(CREATE_URL);
        await page.click('button:has-text("Roll a New Character")');

        await expect(page).toHaveURL(/\/characters\/\d+\/$/);
        await expect(page.locator('#id_name')).toHaveValue(TEST_ADMIN.username);
    });
});

test.describe('the home page shows the player their table (#827)', () => {
    test('a player with no characters is offered a first one', async ({ page }, testInfo) => {
        await loginAs(page, TEST_WATCHER);
        await atEveryWidth(page, HOME_URL, 'home-empty', testInfo, async () => {
            await expect(page.locator('#your-table-heading')).toHaveText('Your table');
            await expect(page.locator('#your-table-first-character')).toBeVisible();
        });

        await page.click('#your-table-first-character');
        await expect(page).toHaveURL(new RegExp(`${CREATE_URL}$`));
    });

    test('a player finds their character and plays it from the home page', async ({ page }, testInfo) => {
        test.setTimeout(90000);
        await loginAs(page, TEST_USER);
        // Long enough to wrap at 390px, where the button must keep its column.
        const name = uniqueName('Home of the Northern Marches', testInfo);
        await createCharacter(page, name);
        const sheetPath = new URL(page.url()).pathname;

        await atEveryWidth(page, HOME_URL, 'home-table', testInfo, async () => {
            const row = page.locator('.your-table-character', { hasText: name });
            const link = row.locator(`a[href="${sheetPath}"]`);
            const play = row.getByRole('button', { name: `Play ${name} with the Game Master` });
            await expect(link).toHaveText(name);
            await expect(row.locator('.status-badge')).toHaveText('Ready');
            await expect(play).toBeVisible();
            // The button sits beside the name, never on a line of its own.
            const linkBox = await link.boundingBox();
            const playBox = await play.boundingBox();
            expect(playBox.x).toBeGreaterThan(linkBox.x + 20);
            expect(playBox.y).toBeLessThan(linkBox.y + linkBox.height);
        });

        await page.goto(HOME_URL);
        await page.getByRole('button', { name: `Play ${name} with the Game Master` }).click();

        await page.waitForURL(/\/tarmar\/gm\/\?session=\d+$/);
        const sessionId = new URL(page.url()).searchParams.get('session');
        await expect(page.locator('.gm-sheet-name')).toHaveText(name);

        // The session it started is now the newest on the home page.
        await page.goto(HOME_URL);
        await expect(
            page.locator(`#your-table a[href="/tarmar/gm/?session=${sessionId}"]`)
        ).toBeVisible();
    });
});

test.describe('the sheet takes the character to the Game Master (#828)', () => {
    test('to a new session, then a second character to the same one', async ({ page }, testInfo) => {
        test.setTimeout(90000);
        await loginAs(page, TEST_USER);
        const first = uniqueName('Sheet', testInfo);
        await createCharacter(page, first);
        const firstSheet = page.url();

        await atEveryWidth(page, firstSheet, 'sheet-play', testInfo, async () => {
            await expect(page.locator('form.play-with-gm')).toBeVisible();
            await expect(page.getByLabel(`Take ${first} to`)).toHaveValue('');
        });

        await page.goto(firstSheet);
        await page.getByRole('button', { name: 'Play with the Game Master' }).click();
        await page.waitForURL(/\/tarmar\/gm\/\?session=\d+$/);
        const sessionId = new URL(page.url()).searchParams.get('session');
        await expect(page.locator('.gm-sheet-name')).toHaveText(first);

        // Back on the sheet, that session says it already holds the character.
        await page.goto(firstSheet);
        await expect(
            page.locator(`form.play-with-gm option[value="${sessionId}"]`)
        ).toContainText('(already playing here)');

        // A second character joins the chosen session rather than a new one.
        const second = uniqueName('Sheet Two', testInfo);
        await createCharacter(page, second);
        await page.getByLabel(`Take ${second} to`).selectOption(sessionId);
        await page.getByRole('button', { name: 'Play with the Game Master' }).click();

        await page.waitForURL(new RegExp(`/tarmar/gm/\\?session=${sessionId}$`));
        await expect(page.locator('.gm-sheet-name')).toHaveText([first, second]);
    });

    test('a refusal stays on the sheet until the next attempt', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await createCharacter(page);
        await page.route('**/tarmar/gm/characters/attach/', (route) => route.fulfill({
            status: 400,
            contentType: 'application/json',
            body: JSON.stringify({ error: 'Character is dead' }),
        }));

        await page.getByRole('button', { name: 'Play with the Game Master' }).click();

        const refusal = page.locator('.play-with-gm-error');
        await expect(refusal).toHaveText('The Game Master could not take this character: Character is dead.');
        await expect(page).toHaveURL(/\/characters\/\d+\/$/);
        // No timer takes it away.
        await page.waitForTimeout(3000);
        await expect(refusal).toBeVisible();
        await expect(page.getByRole('button', { name: 'Play with the Game Master' })).toBeEnabled();
    });
});
