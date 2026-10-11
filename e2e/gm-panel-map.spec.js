/**
 * E2E test for the GM chat's side panel — the Map tab (issue #343).
 *
 * No Ollama is needed: the tab is driven by hand through the picker and the
 * zoom buttons, and the narration path is exercised by dispatching the same
 * `gm-stream-done` event chat.js raises when a reply's done frame arrives.
 * The server-side detector that fills that frame has its own Django tests.
 */

const {
    test, expect, loginAs, loadedCanon, TEST_USER,
} = require('./fixtures');

const MAJA_FRAME = {
    done: true,
    map: { slug: 'maja', title: 'Maja', image_url: '/static/images/maja-island.png', place: 'Majahelm' },
};

// What each canon's map registry offers (issue #918). The page names the
// loaded canon in its "World:" line; the specs read it and pick their place
// names from the matching entry rather than assuming the Tarmar canon.
const CANON_MAPS = {
    'Tarmar canon': {
        place: 'Farport',
        chart: 'maja',
        image: '/static/images/maja-island.png',
        region: 'maja-solberg',
        regionTitle: 'Maja & Solberg',
        regionImage: '/static/images/maja-solberg-map.png',
        mapCount: 5,
    },
    'default canon': {
        place: 'Brinemouth',
        chart: 'corran-reach',
        image: '/static/images/default-canon/corran-reach-map.svg',
        region: 'corran-reach',
        regionTitle: 'The Corran Reach',
        regionImage: '/static/images/default-canon/corran-reach-map.svg',
        mapCount: 1,
    },
};

test.describe('GM panel — Map tab', () => {
    test.beforeEach(async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/gm/');
        await page.click('#gm-panel-tab-map');
    });

    test('shows the regional chart and zooms', async ({ page }) => {
        const canon = CANON_MAPS[await loadedCanon(page)];
        await expect(page.locator('#gm-panel-tab-map')).toHaveAttribute('aria-pressed', 'true');
        const tab = page.locator('#gm-panel-map');
        await expect(tab).toBeVisible();

        const image = page.locator('#gm-map-image');
        const select = page.locator('#gm-map-select');
        const captionTitle = page.locator('#gm-map-caption-title');
        const captionPlace = page.locator('#gm-map-caption-place');

        // Every map is offered; the regional chart is up first and loads.
        await expect(select.locator('option')).toHaveCount(canon.mapCount);
        await expect(select).toHaveValue(canon.region);
        await expect(image).toHaveAttribute('src', canon.regionImage);
        await expect(captionTitle).toHaveText(canon.regionTitle);
        await expect(captionPlace).toBeHidden();
        await expect.poll(() => image.evaluate((element) => element.naturalWidth)).toBeGreaterThan(0);

        // Zoom in, then reset.
        await page.click('#gm-map-zoom-in');
        await expect(page.locator('#gm-map-zoom-level')).toHaveText('150%');
        await expect(page.locator('#gm-map-image')).toHaveCSS('transform', /matrix\(1\.5, 0, 0, 1\.5, /);
        await page.click('#gm-map-reset');
        await expect(page.locator('#gm-map-zoom-level')).toHaveText('100%');
    });

    test('switches by hand and follows the story', async ({ page }) => {
        test.skip(
            (await loadedCanon(page)) !== 'Tarmar canon',
            "switching and following need more than one chart; the default canon has one",
        );
        await expect(page.locator('#gm-panel-tab-map')).toHaveAttribute('aria-pressed', 'true');
        const tab = page.locator('#gm-panel-map');
        await expect(tab).toBeVisible();

        const image = page.locator('#gm-map-image');
        const select = page.locator('#gm-map-select');
        const captionTitle = page.locator('#gm-map-caption-title');
        const captionPlace = page.locator('#gm-map-caption-place');

        // By hand.
        await select.selectOption('old-qasha');
        await expect(image).toHaveAttribute('src', '/static/images/old-qasha-island.png');
        await expect(captionTitle).toHaveText('Old Qasha');

        // The narration names a place: the map follows and the caption marks it.
        await page.evaluate((frame) => {
            document.dispatchEvent(new CustomEvent('gm-stream-done', { detail: frame }));
        }, MAJA_FRAME);
        await expect(select).toHaveValue('maja');
        await expect(image).toHaveAttribute('src', '/static/images/maja-island.png');
        await expect(captionTitle).toHaveText('Maja');
        await expect(captionPlace).toHaveText('— Majahelm');

        // Following off: the same frame changes nothing, and the choice survives a reload.
        await page.uncheck('#gm-map-follow');
        await select.selectOption('world');
        await page.evaluate((frame) => {
            document.dispatchEvent(new CustomEvent('gm-stream-done', { detail: frame }));
        }, MAJA_FRAME);
        await expect(select).toHaveValue('world');
        await page.reload();
        await page.click('#gm-panel-tab-map');
        await expect(page.locator('#gm-map-select')).toHaveValue('world');
        await expect(page.locator('#gm-map-follow')).not.toBeChecked();
    });
});

test.describe('GM panel — the Map tab follows the party (issue #579)', () => {
    test('a real turn moves the map, and an unmapped place is said instead', async ({ adminPage: page }) => {
        await page.goto('/tarmar/gm/');
        const canon = CANON_MAPS[await loadedCanon(page)];
        await page.click('#gm-panel-tab-map');
        await expect(page.locator('#gm-map-select')).toHaveValue(canon.region);
        let reloaded = false;
        page.on('load', () => { reloaded = true; });

        // A turn whose plan records where the party is. The reply names no
        // place at all, so anything that moves is reading the record.
        await page.fill('#user-input', `We walk to ${canon.place}.`);
        await page.click('#send-btn');
        await expect(page.locator('#gm-map-select')).toHaveValue(canon.chart, { timeout: 30000 });
        await expect(page.locator('#gm-map-image')).toHaveAttribute('src', canon.image);
        await expect(page.locator('#gm-map-caption-place')).toHaveText(`— the party is at ${canon.place}`);
        await expect(page.locator('#gm-map-unmapped')).toBeHidden();
        expect(reloaded).toBe(false);

        // The Campaign tab reads the same record the map just followed.
        await page.click('#gm-panel-tab-campaign');
        await expect(page.locator('#gm-panel-campaign')).toContainText(canon.place);

        // Somewhere the gazetteer never named: said plainly, and the chart
        // of the ground around them is left up rather than blanked.
        await page.click('#gm-panel-tab-map');
        await page.fill('#user-input', "We walk to Grandma's cellar.");
        await page.click('#send-btn');
        await expect(page.locator('#gm-map-unmapped')).toHaveText(
            "The party is at Grandma's cellar, which is not on any map.",
            { timeout: 30000 },
        );
        await expect(page.locator('#gm-map-select')).toHaveValue(canon.chart);

        // And a reload opens on the same reading of the same record.
        await page.reload();
        await page.click('#gm-panel-tab-map');
        await expect(page.locator('#gm-map-unmapped')).toBeVisible();
        await expect(page.locator('#gm-map-unmapped')).toContainText("Grandma's cellar");
    });
});
