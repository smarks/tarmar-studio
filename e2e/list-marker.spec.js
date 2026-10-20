/**
 * A numbered line's number sits beside its first line (issue #61).
 *
 * The battle log is an ordered list, and a decision's Forecast is the summary
 * of a <details> inside its item. That <details> was an inline-block, whose
 * baseline is its own last line, and an outside list marker sits on the
 * baseline of the item's first line box: so a forecast that wrapped read
 * "7. 2.837", its number beside the closing figure, and an opened one put the
 * number beside its last candidate.
 *
 * Checked on pixels, at the width it was seen at: the marker's ink is found
 * by drawing the item with and without its marker, and must lie within the
 * item's first line. The chat's numbered and bulleted lists in a Game Master
 * reply are held to the same rule; they were swept for #61 and found right,
 * and this keeps them so.
 */

const { test, expect, createBattleViaSetup } = require('./fixtures');
const { differingRows } = require('./pixels');

const DESKTOP = { width: 1440, height: 2000 };
/** How far left of an item its marker is looked for, in pixels. */
const MARKER_GUTTER = 40;

/**
 * Scroll the item to the top of the box that scrolls it, leaving the page
 * where it is (a sticky bar at the page's top would cover a line scrolled up
 * to it), and say where its first line and its marker are.
 * @param {import('@playwright/test').Page} page
 * @param {import('@playwright/test').Locator} item - An <li>.
 * @returns {Promise<{lines: number, firstLine: {top: number, bottom: number}, marker: {top: number, bottom: number} | null}>}
 */
async function markerAndFirstLine(page, item) {
    const geometry = await item.evaluate((element) => {
        const scrolls = (box) => /(auto|scroll)/.test(getComputedStyle(box).overflowY);
        let scroller = element.parentElement;
        while (scroller && scroller !== document.body && !scrolls(scroller)) {
            scroller = scroller.parentElement;
        }
        let visibleBottom = window.innerHeight;
        if (scroller && scroller !== document.body) {
            scroller.scrollTop += element.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
            visibleBottom = Math.min(visibleBottom, scroller.getBoundingClientRect().bottom);
        }
        // Every piece of text the item draws, as the boxes its lines make.
        const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, {
            acceptNode: (node) => (node.textContent.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
        });
        // Only boxes inside the item as drawn: a closed <details> still
        // reports boxes for the candidates it hides, which would count lines
        // nobody sees.
        const box = element.getBoundingClientRect();
        const drawn = (rect) => rect.width > 0 && rect.height > 0
            && rect.top >= box.top - 1 && rect.bottom <= box.bottom + 1;
        const textBoxes = [];
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            const range = document.createRange();
            range.selectNodeContents(node);
            textBoxes.push(...[...range.getClientRects()].filter(drawn));
        }
        // The first line is every box beside the first one: a line that opens
        // with `code` is that box and the plain text after it.
        const opening = textBoxes[0];
        const firstLineBoxes = textBoxes.filter((rect) => rect.top < opening.bottom && rect.bottom > opening.top);
        const firstLine = {
            top: Math.min(...firstLineBoxes.map((rect) => rect.top)),
            bottom: Math.max(...firstLineBoxes.map((rect) => rect.bottom)),
        };
        return {
            left: box.left,
            top: box.top,
            bottom: Math.min(box.bottom, visibleBottom),
            lines: new Set(textBoxes.map((rect) => Math.round(rect.top))).size,
            firstLine,
        };
    });
    const clipLeft = Math.max(0, Math.floor(geometry.left) - MARKER_GUTTER);
    const clip = {
        x: clipLeft,
        y: Math.ceil(geometry.top),
        width: Math.floor(geometry.left) - clipLeft,
        height: Math.floor(geometry.bottom) - Math.ceil(geometry.top),
    };
    const withMarker = await page.screenshot({ clip });
    await item.evaluate((element) => element.style.setProperty('list-style-type', 'none', 'important'));
    const withoutMarker = await page.screenshot({ clip });
    await item.evaluate((element) => element.style.removeProperty('list-style-type'));
    const rows = await differingRows(page, withMarker, withoutMarker);
    return {
        lines: geometry.lines,
        firstLine: geometry.firstLine,
        marker: rows.length ? { top: clip.y + rows[0], bottom: clip.y + rows[rows.length - 1] } : null,
    };
}

/**
 * Hold an item's marker to its first line.
 * @param {import('@playwright/test').Page} page
 * @param {import('@playwright/test').Locator} item - An <li> that wraps.
 * @param {string} label - Names the item in a failure.
 */
async function expectMarkerOnFirstLine(page, item, label) {
    const { lines, firstLine, marker } = await markerAndFirstLine(page, item);
    expect(lines, `${label} wraps, so there is a last line to drift to`).toBeGreaterThan(1);
    expect(marker, `${label} has a marker drawn beside it`).not.toBeNull();
    expect(marker.top, `${label} marker top against its first line`).toBeGreaterThanOrEqual(Math.floor(firstLine.top));
    expect(marker.bottom, `${label} marker bottom against its first line`).toBeLessThanOrEqual(Math.ceil(firstLine.bottom));
}

test.describe('A list marker sits beside its item\'s first line (#61)', () => {
    test('a wrapped Forecast in the battle log, closed and opened', async ({ adminPage: page }) => {
        await page.setViewportSize(DESKTOP);
        await createBattleViaSetup(page, { name: `E2E Markers ${Date.now()}`, fighters: 2, seed: 42 });
        await page.click('#advance-turn');
        await expect(page.locator('.turn-group[data-turn="1"]')).toBeVisible();

        const decision = page.locator('.turn-events > .event-decision').first();
        await expect(decision).toContainText('Forecast —');
        await expectMarkerOnFirstLine(page, decision, 'the closed forecast');

        await decision.locator('summary').click();
        await expect(decision.locator('details')).toHaveAttribute('open', '');
        await expectMarkerOnFirstLine(page, decision, 'the opened forecast');
    });

    test('numbered and bulleted lines in a Game Master reply', async ({ authenticatedPage: page }) => {
        await page.setViewportSize(DESKTOP);
        await page.goto('/tarmar/gm/');
        const longLine = 'long enough to wrap onto a second line in the bubble, '.repeat(4);
        const reply = [
            `1. A numbered line ${longLine}`,
            `2. \`code\` at the start of a numbered line ${longLine}`,
            `    - a nested bullet ${longLine}`,
            '',
            `- a bullet with **bold words** ${longLine}`,
        ].join('\n');
        // The page's own renderer, as a reply arrives.
        await page.evaluate((text) => addMessage('assistant', text, { isMarkdown: true }), reply);

        const items = page.locator('.message-content li');
        await expect(items).toHaveCount(4);
        for (let index = 0; index < 4; index += 1) {
            await expectMarkerOnFirstLine(page, items.nth(index), `reply line ${index + 1}`);
        }
    });
});
