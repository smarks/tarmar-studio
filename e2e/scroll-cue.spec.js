/**
 * A sideways-scrolling table says that it scrolls (issue #63).
 *
 * Below 480 px the character sheet's Core Attributes table is its own
 * scroller. For a player each of the six cells carried the sentence that says
 * a recorded score is locked, which made the table 420 px of content in a
 * 311 px box: CON was cut mid-letter, CHR sat off to the right, and a phone
 * draws no scrollbar, so nothing said there was more. The fix was a cue, and
 * one rule gives it to every sideways scroller (base.css). Since #84 the
 * sentence is said once, under the table, and a player's table fits; a Game
 * Master's, whose cells are inputs, still scrolls. The class that carries the
 * cue is written by the templates, and by chat.js for the tables and code
 * blocks in a reply.
 *
 * The check is the same for every scroller on a page: it fits, or the edge
 * that has more to show is shadowed and the edge that has not is plain. It is
 * made on pixels, not on the stylesheet, because the stylesheet can say
 * anything and still draw nothing. A scroller that fits must draw nothing, or
 * the edge cover would show as a band against the ground it sits on. (The
 * scrollbar half of the cue cannot be seen here: headless Chromium hides all
 * scrollbars. The test pins that the rule applies.)
 */

const { test, expect, loginAs, createCharacter, TEST_USER, TEST_ADMIN } = require('./fixtures');

const PHONE = { width: 375, height: 812 };
/** Width, in pixels, of the strip at each edge that is compared. */
const EDGE_STRIP = 12;
/**
 * The most a channel may differ, in levels of 255, before two pixels are
 * counted as different. Text anti-aliases at most one level differently over
 * an image layer than over a plain fill (measured); the edge shadow is at
 * least 43 levels. The tolerance is kept to 2 so that an edge cover left in
 * the wrong ground still shows: the page's #fff on the sheet's #fafafa is
 * 5 levels, #f9f9f9 is 6 and #f7f7f7 is 8, and a tolerance above 4 passes them.
 */
const NOISE_LEVELS = 2;
/** The note that says once why a player's recorded attribute is locked (#84). */
const ATTRIBUTE_LOCKED_NOTE = '#attribute-locked-reason';
/** A width at which the strip of GM panel tabs is wider than its box. */
const NARROW_PHONE = { width: 320, height: 568 };
/** Elements that scroll their own text, not a row of content sideways. */
const FIELD_TAGS = ['INPUT', 'TEXTAREA', 'SELECT'];

/**
 * Whether two screenshots differ by more than rendering noise anywhere.
 * @param {import('@playwright/test').Page} page - Decodes the images: a browser already can.
 * @param {Buffer} first - A PNG.
 * @param {Buffer} second - A PNG of the same size.
 * @returns {Promise<boolean>}
 */
async function pixelsDiffer(page, first, second) {
    return page.evaluate(async ({ encoded, noise }) => {
        const pixelsOf = async (base64) => {
            const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
            const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
            const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
            const context = canvas.getContext('2d');
            context.drawImage(bitmap, 0, 0);
            return context.getImageData(0, 0, bitmap.width, bitmap.height).data;
        };
        const [firstPixels, secondPixels] = await Promise.all(encoded.map(pixelsOf));
        return firstPixels.some((level, index) => Math.abs(level - secondPixels[index]) > noise);
    }, { encoded: [first.toString('base64'), second.toString('base64')], noise: NOISE_LEVELS });
}

/**
 * Whether a scroller draws anything at one of its edges: the pixels of a strip
 * at that edge with the cue, against the same strip with it switched off.
 * @param {import('@playwright/test').Page} page
 * @param {import('@playwright/test').Locator} scroller
 * @param {'left'|'right'} edge
 * @returns {Promise<boolean>}
 */
async function cueDrawnAt(page, scroller, edge) {
    const box = await scroller.boundingBox();
    // Below the rounded corners: a browser anti-aliases an image layer's
    // corner a few levels differently from the fill under it, which no reader
    // can see and which would make every rounded scroller read as cued.
    const corner = await scroller.evaluate((element) => parseFloat(getComputedStyle(element).borderTopLeftRadius) || 0);
    // Whole pixels, inside the box: a strip that starts part-way into a pixel
    // row is composited differently with and without an image layer.
    const top = Math.ceil(box.y + corner);
    const left = edge === 'left' ? Math.ceil(box.x) : Math.floor(box.x + box.width) - EDGE_STRIP;
    const strip = {
        x: left,
        y: top,
        width: EDGE_STRIP,
        height: Math.floor(box.y + box.height - corner) - top,
    };
    const withCue = await page.screenshot({ clip: strip });
    await scroller.evaluate((element) => element.style.setProperty('background-image', 'none', 'important'));
    const withoutCue = await page.screenshot({ clip: strip });
    await scroller.evaluate((element) => element.style.removeProperty('background-image'));
    return pixelsDiffer(page, withCue, withoutCue);
}

/**
 * Hold every sideways scroller on the page to the rule above.
 *
 * A scroller is any drawn element whose computed overflow-x is auto or scroll
 * and which either overflows sideways now or has been given the cue. It is
 * found by its computed style, not by the class or tag the stylesheet is
 * written against, so one that was never given the cue cannot slip past by
 * lacking its class. (A box that scrolls only up and down also computes
 * overflow-x: auto; unless it overflows sideways it is not a scroller, and
 * nothing is asked of it.)
 * @param {import('@playwright/test').Page} page
 * @param {string} [within] - Selector of the part of the page to hold; a panel
 *   that lies over the rest of the page on a phone is held on its own.
 * @returns {Promise<string[]>} a label for each scroller held to the rule
 */
async function expectEveryScrollerCued(page, within = 'body') {
    const everything = page.locator(`${within} *`);
    const found = await everything.evaluateAll((elements, fieldTags) => elements
        .map((element, index) => ({
            index,
            label: `${element.tagName.toLowerCase()}.${element.className}`.replace(/\s+/g, '.'),
            overflowX: getComputedStyle(element).overflowX,
            overflowing: element.scrollWidth > element.clientWidth,
            cued: element.classList.contains('scroll-cue'),
            drawn: element.clientWidth > 0,
            field: fieldTags.includes(element.tagName),
            scrollbarHeight: getComputedStyle(element, '::-webkit-scrollbar').height,
        }))
        .filter((metrics) => metrics.drawn && !metrics.field
            && (metrics.overflowX === 'auto' || metrics.overflowX === 'scroll')
            && (metrics.overflowing || metrics.cued)), FIELD_TAGS);

    for (const metrics of found) {
        const scroller = everything.nth(metrics.index);
        const label = metrics.label;
        await scroller.scrollIntoViewIfNeeded();
        expect(metrics.cued, `${label} carries the cue class`).toBe(true);
        expect(metrics.scrollbarHeight, `${label} scrollbar`).toBe('8px');
        await scroller.evaluate((element) => { element.scrollLeft = 0; });
        if (!metrics.overflowing) {
            expect(await cueDrawnAt(page, scroller, 'left'), `${label} fits, left edge`).toBe(false);
            expect(await cueDrawnAt(page, scroller, 'right'), `${label} fits, right edge`).toBe(false);
            continue;
        }
        expect(await cueDrawnAt(page, scroller, 'left'), `${label} at the start, left edge`).toBe(false);
        expect(await cueDrawnAt(page, scroller, 'right'), `${label} at the start, right edge`).toBe(true);
        await scroller.evaluate((element) => { element.scrollLeft = element.scrollWidth; });
        expect(await cueDrawnAt(page, scroller, 'left'), `${label} at the end, left edge`).toBe(true);
        expect(await cueDrawnAt(page, scroller, 'right'), `${label} at the end, right edge`).toBe(false);
    }
    return found.map((metrics) => metrics.label);
}

test.describe('Sideways scrollers carry a cue (#63)', () => {
    test("a player's Core Attributes table fits a phone, and draws no cue", async ({ page }) => {
        await page.setViewportSize(PHONE);
        await loginAs(page, TEST_USER);
        await createCharacter(page);

        // #84: with the locked reason said once under the table, rather than
        // in each of the six cells, every attribute is in reach.
        await expect(page.locator(ATTRIBUTE_LOCKED_NOTE)).toBeVisible();
        const stats = page.locator('.stats-table');
        const fits = await stats.evaluate((element) => element.scrollWidth <= element.clientWidth);
        expect(fits, "a player's six attributes fit in 375 px").toBe(true);

        // The Core Attributes table still carries the cue, and like the
        // physical-attribute tables beside it is held to drawing nothing.
        const held = await expectEveryScrollerCued(page);
        expect(held.filter((label) => label.includes('stats-table'))).toHaveLength(1);
        expect(held.length).toBeGreaterThan(1);
    });

    test("a Game Master's Core Attributes table, whose cells are inputs, shows that it scrolls", async ({ page }) => {
        await page.setViewportSize(PHONE);
        await loginAs(page, TEST_ADMIN);
        await createCharacter(page);

        // The precondition #63 names, on the sheet it still holds for: CHR is
        // out of reach without a swipe, so the overflowing branch is checked.
        const stats = page.locator('.stats-table');
        const fits = await stats.evaluate((element) => element.scrollWidth <= element.clientWidth);
        expect(fits, "a Game Master's six attribute inputs fit in 375 px").toBe(false);

        const held = await expectEveryScrollerCued(page);
        expect(held.filter((label) => label.includes('stats-table'))).toHaveLength(1);
        expect(held.length).toBeGreaterThan(1);
    });

    test('the list pages wrap their tables in a scroller that carries the cue', async ({ adminPage: page }) => {
        await page.setViewportSize(PHONE);
        for (const listPage of ['/tarmar/characters/', '/tarmar/accounts/users/']) {
            await page.goto(listPage);
            const held = await expectEveryScrollerCued(page);
            expect(held.filter((label) => label.includes('table-wrap')), listPage).toHaveLength(1);
        }
    });

    test('a table and a code block in a Game Master reply show that they scroll', async ({
        authenticatedPage: page,
    }) => {
        await page.setViewportSize(PHONE);
        await page.goto('/tarmar/gm/');
        // The page's own renderer, with a table and a line too wide for a phone.
        const header = Array.from({ length: 8 }, (_, column) => `Column number ${column}`);
        const row = header.map((_, column) => `value ${column}`);
        const table = [header, header.map(() => '---'), row]
            .map((cells) => `| ${cells.join(' | ')} |`)
            .join('\n');
        const code = `\`\`\`\n${'a line of code that runs on '.repeat(8)}\n\`\`\``;
        await page.evaluate(
            (text) => addMessage('assistant', text, { isMarkdown: true }),
            `${table}\n\n${code}`,
        );
        await expect(page.locator('.message-content table')).toHaveCount(1);
        await expect(page.locator('.message-content pre')).toHaveCount(1);

        const held = await expectEveryScrollerCued(page, '#messages');
        for (const expected of ['table.', 'pre.']) {
            expect(held.some((label) => label.startsWith(expected)), `${expected} among ${held}`).toBe(true);
        }
    });

    test('the Game Master panel tabs show that they scroll when they overflow', async ({
        authenticatedPage: page,
    }) => {
        await page.setViewportSize(NARROW_PHONE);
        await page.goto('/tarmar/gm/');
        // The strip is behind the panel button on a phone.
        await page.click('#gm-panel-toggle');
        await expect(page.locator('#gm-panel')).toBeVisible();
        const tabs = page.locator('.gm-panel-tabs');
        // The precondition: at this width the strip has more than it shows, so
        // the check below is made on the overflowing branch, not the fitting one.
        expect(await tabs.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);

        const held = await expectEveryScrollerCued(page, '#gm-panel');
        expect(held.some((label) => label.startsWith('div.gm-panel-tabs'))).toBe(true);
    });
});
