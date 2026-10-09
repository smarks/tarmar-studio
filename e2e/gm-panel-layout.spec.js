/**
 * E2E tests for the GM panel's width, its stack of panes and popping a pane
 * out into its own window (issue #351).
 *
 * These are the things a unit test cannot see: that dragging the handle
 * really moves the boundary and the chat reflows, that two panes share the
 * panel's height, and that the pop-out control lands on the page it names.
 * Starting a fight with named foes needs no Ollama (see
 * gm-panel-battle.spec.js), so the Battle pane has a real board to pop out.
 */

const { test, expect, loginAs, TEST_USER } = require('./fixtures');

/** The panel's rendered width, in CSS pixels. */
async function panelWidth(page) {
    const box = await page.locator('#gm-panel').boundingBox();
    return box.width;
}

/**
 * Drag the panel's grab handle sideways.
 * @param {import('@playwright/test').Page} page
 * @param {number} deltaX - Pixels to move; negative widens the panel.
 */
async function dragHandle(page, deltaX) {
    const handle = page.locator('#gm-panel-resize');
    const box = await handle.boundingBox();
    const startX = box.x + box.width / 2;
    const startY = box.y + 200;
    await page.mouse.move(startX, startY);
    await page.mouse.down();
    await page.mouse.move(startX + deltaX, startY, { steps: 10 });
    await page.mouse.up();
}

test.describe('GM panel — width', () => {
    test.beforeEach(async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/gm/');
    });

    test('the handle widens the panel, the chat reflows, and the width survives a reload', async ({ page }, testInfo) => {
        await expect(page.locator('#gm-panel')).toBeVisible();
        expect(await panelWidth(page)).toBeCloseTo(320, 0);
        const chatBefore = (await page.locator('.chat-container').boundingBox()).width;

        await dragHandle(page, -260);
        const widened = await panelWidth(page);
        expect(widened).toBeGreaterThan(560);
        // The chat column gave up exactly what the panel took.
        const chatAfter = (await page.locator('.chat-container').boundingBox()).width;
        expect(chatBefore - chatAfter).toBeCloseTo(widened - 320, 0);
        // And it is still a chat: the input and the messages have room.
        await expect(page.locator('#user-input')).toBeVisible();
        expect(chatAfter).toBeGreaterThan(420);
        await page.screenshot({ path: testInfo.outputPath('panel-wide.png'), fullPage: false });

        await page.reload();
        expect(await panelWidth(page)).toBeCloseTo(widened, 0);
    });

    test('the panel will not swallow the chat, and double-click resets it', async ({ page }, testInfo) => {
        // Far past the edge of the window: the clamp holds it at half.
        await dragHandle(page, -2000);
        const viewport = page.viewportSize();
        expect(await panelWidth(page)).toBeLessThanOrEqual(viewport.width / 2 + 1);

        // And it cannot be squeezed below its minimum either.
        await dragHandle(page, 2000);
        expect(await panelWidth(page)).toBeCloseTo(280, 0);
        await page.screenshot({ path: testInfo.outputPath('panel-narrow.png'), fullPage: false });

        await page.locator('#gm-panel-resize').dblclick();
        expect(await panelWidth(page)).toBeCloseTo(320, 0);
    });

    test('the handle works from the keyboard', async ({ page }) => {
        await page.locator('#gm-panel-resize').focus();
        await page.keyboard.press('ArrowLeft');
        await page.keyboard.press('ArrowLeft');
        expect(await panelWidth(page)).toBeCloseTo(352, 0);
        await page.keyboard.press('Home');
        expect(await panelWidth(page)).toBeCloseTo(320, 0);
    });

    test('there is nothing to drag on a phone', async ({ page }) => {
        await page.setViewportSize({ width: 600, height: 900 });
        await page.click('#gm-panel-toggle');
        await expect(page.locator('#gm-panel')).toBeVisible();
        await expect(page.locator('#gm-panel-resize')).toBeHidden();
    });
});

test.describe('GM panel — panes', () => {
    test.beforeEach(async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/gm/');
    });

    test('a second pane opens below the first, collapses, and comes back after a reload', async ({ page }, testInfo) => {
        const characters = page.locator('.gm-pane[data-pane="characters"]');
        const map = page.locator('.gm-pane[data-pane="map"]');
        await expect(characters).toBeVisible();
        await expect(map).toBeHidden();

        // The strip's split control gives the Map a pane of its own.
        await page.locator('#gm-panel-tab-map').locator('..').locator('.gm-panel-split').click();
        await expect(characters).toBeVisible();
        await expect(map).toBeVisible();
        await expect(page.locator('.gm-pane-divider')).toHaveCount(1);
        // Stacked, not overlapping: the map sits below the sheets.
        const charactersBox = await characters.boundingBox();
        const mapBox = await map.boundingBox();
        expect(mapBox.y).toBeGreaterThanOrEqual(charactersBox.y + charactersBox.height - 1);
        await page.screenshot({ path: testInfo.outputPath('panel-two-panes.png'), fullPage: false });

        // The divider gives the map most of the height.
        const dividerBox = await page.locator('.gm-pane-divider').boundingBox();
        await page.mouse.move(dividerBox.x + dividerBox.width / 2, dividerBox.y + dividerBox.height / 2);
        await page.mouse.down();
        await page.mouse.move(dividerBox.x + dividerBox.width / 2, dividerBox.y - 150, { steps: 10 });
        await page.mouse.up();
        const shrunk = (await characters.boundingBox()).height;
        expect(shrunk).toBeLessThan(charactersBox.height - 100);

        // Collapsing leaves the title bar and nothing else.
        await characters.locator('.gm-pane-collapse').click();
        await expect(characters.locator('.gm-pane-body')).toBeHidden();
        await expect(characters.locator('.gm-pane-title')).toBeVisible();

        await page.reload();
        await expect(page.locator('.gm-pane[data-pane="map"]')).toBeVisible();
        await expect(page.locator('.gm-pane[data-pane="characters"] .gm-pane-body')).toBeHidden();

        // Closing from the title bar makes it a tab again.
        await page.locator('.gm-pane[data-pane="characters"] .gm-pane-close').click();
        await expect(page.locator('.gm-pane[data-pane="characters"]')).toBeHidden();
        await expect(page.locator('.gm-pane[data-pane="map"]')).toBeVisible();
    });

    test('clicking a tab still shows that one alone', async ({ page }) => {
        await page.locator('#gm-panel-tab-battle').locator('..').locator('.gm-panel-split').click();
        await expect(page.locator('.gm-pane[data-pane="battle"]')).toBeVisible();
        await page.click('#gm-panel-tab-map');
        await expect(page.locator('.gm-pane[data-pane="map"]')).toBeVisible();
        await expect(page.locator('.gm-pane[data-pane="battle"]')).toBeHidden();
        await expect(page.locator('.gm-pane[data-pane="characters"]')).toBeHidden();
    });
});

test.describe('GM panel — pop-out', () => {
    test.beforeEach(async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/gm/');
    });

    test('the Map pane opens the map page, and says so until it is brought back', async ({ page, context }) => {
        await page.click('#gm-panel-tab-map');
        const mapPane = page.locator('.gm-pane[data-pane="map"]');
        await expect(mapPane).toBeVisible();

        const [mapWindow] = await Promise.all([
            context.waitForEvent('page'),
            mapPane.locator('.gm-pane-popout').click(),
        ]);
        await mapWindow.waitForLoadState();
        expect(mapWindow.url()).toContain('/tarmar/gm/map/');
        // It opens on the chart the pane was showing.
        expect(mapWindow.url()).toContain('map=');
        await expect(mapWindow.locator('#gm-map-image')).toBeVisible();
        await expect(mapWindow.locator('#gm-map-select')).toBeVisible();

        // The pane says where it went; nothing faded, it is state.
        await expect(mapPane.locator('.gm-pane-detached')).toBeVisible();
        await expect(mapPane.locator('#gm-panel-map')).toBeHidden();
        await page.reload();
        await expect(page.locator('.gm-pane[data-pane="map"] .gm-pane-detached')).toBeVisible();

        await page.locator('.gm-pane[data-pane="map"] .gm-pane-return').click();
        await expect(page.locator('.gm-pane[data-pane="map"] .gm-pane-detached')).toBeHidden();
        await expect(page.locator('#gm-panel-map')).toBeVisible();
        await mapWindow.close().catch(() => {});
    });

    test('the Characters pane opens the sheet on show, and the Battle pane the board', async ({ page, context }) => {
        // Nothing attached, no fight: neither pane has anywhere to go, and
        // each says why rather than opening a blank window.
        await expect(page.locator('.gm-pane[data-pane="characters"] .gm-pane-popout')).toBeDisabled();

        await page.selectOption('#gm-character-select', { label: 'Aldwin the Bold' });
        await page.click('#gm-character-attach');
        await page.waitForURL(/\/tarmar\/gm\/\?session=\d+$/);

        const [sheetWindow] = await Promise.all([
            context.waitForEvent('page'),
            page.locator('.gm-pane[data-pane="characters"] .gm-pane-popout').click(),
        ]);
        await sheetWindow.waitForLoadState();
        expect(sheetWindow.url()).toMatch(/\/tarmar\/characters\/\d+\/$/);
        await sheetWindow.close();
        await page.locator('.gm-pane[data-pane="characters"] .gm-pane-return').click();

        // Take the fight to the board, then send it to its own page.
        await page.fill('#user-input', '/fight two gray wolves');
        await page.press('#user-input', 'Enter');
        await expect(page.locator('#gm-battle')).toBeVisible();
        const battlePane = page.locator('.gm-pane[data-pane="battle"]');
        const [battleWindow] = await Promise.all([
            context.waitForEvent('page'),
            battlePane.locator('.gm-pane-popout').click(),
        ]);
        await battleWindow.waitForLoadState();
        expect(battleWindow.url()).toMatch(/\/tarmar\/battles\/\d+\/$/);
        await expect(battleWindow.locator('#battle-arena')).toBeVisible();
        await expect(battlePane.locator('.gm-pane-detached')).toBeVisible();
        await battleWindow.close();
    });
});
