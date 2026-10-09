/**
 * E2E tests for reference rules tables.
 *
 * The prior-experience skill-track table is nine columns wide. It used to sit
 * inside a horizontally scrolling wrapper, which hid most of those columns.
 * These tests assert the whole table is on screen at every width: no sideways
 * scrolling anywhere, and every column present either as a real column or, on
 * a phone, as a labelled line in the restacked row.
 */

const { test, expect } = require('./fixtures');

const SKILL_TRACKS_PATH =
    '/tarmar/reference/public-rules/characters/prior-experience/skill-tracks/';
const TRACK_COLUMNS = [
    'Year',
    'Merchant',
    'Campaigner',
    'Laborer',
    'Magic',
    'Underworld',
    'Civil Service',
    'Craft',
    'Hunter/Gatherer',
];

/**
 * Widest horizontal overflow in the rules content, in pixels.
 *
 * Covers the tables themselves, anything wrapping them, and the page as a
 * whole — the three places a reintroduced scrollbar could hide columns.
 */
async function widestHorizontalOverflow(page) {
    return page.evaluate(() => {
        const suspects = [
            document.documentElement,
            ...document.querySelectorAll('.content, .content *'),
        ];
        return suspects.reduce(
            (worst, element) =>
                Math.max(worst, element.scrollWidth - element.clientWidth),
            0
        );
    });
}

test.describe('Reference rules tables', () => {
    test('skill-track table is annotated with its columns', async ({ page }) => {
        await page.goto(SKILL_TRACKS_PATH);
        const table = page.locator('.content table').first();
        await expect(table).toHaveAttribute('data-columns', '9');
        for (const columnName of TRACK_COLUMNS) {
            const labelled = table.locator(`td[data-label="${columnName}"]`);
            expect(await labelled.count()).toBeGreaterThan(0);
        }
    });

    test('nothing scrolls sideways on a wide desktop', async ({ page }) => {
        await page.setViewportSize({ width: 1440, height: 900 });
        await page.goto(SKILL_TRACKS_PATH);
        expect(await widestHorizontalOverflow(page)).toBeLessThanOrEqual(1);
    });

    test('every column heading is visible on a wide desktop', async ({ page }) => {
        await page.setViewportSize({ width: 1440, height: 900 });
        await page.goto(SKILL_TRACKS_PATH);
        const headings = page.locator('.content table').first().locator('th');
        await expect(headings).toHaveCount(TRACK_COLUMNS.length);
        for (let index = 0; index < TRACK_COLUMNS.length; index += 1) {
            await expect(headings.nth(index)).toBeVisible();
        }
    });

    test('nothing scrolls sideways in the awkward tablet band', async ({ page }) => {
        await page.setViewportSize({ width: 820, height: 1100 });
        await page.goto(SKILL_TRACKS_PATH);
        expect(await widestHorizontalOverflow(page)).toBeLessThanOrEqual(1);
    });

    test('rows restack with their column names on a phone', async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.goto(SKILL_TRACKS_PATH);

        expect(await widestHorizontalOverflow(page)).toBeLessThanOrEqual(1);

        const firstCell = page
            .locator('.content table td[data-label="Hunter/Gatherer"]')
            .first();
        await expect(firstCell).toBeVisible();

        // The column name is rendered by ::before from data-label.
        const renderedLabel = await firstCell.evaluate(
            (cell) => getComputedStyle(cell, '::before').content
        );
        expect(renderedLabel).toContain('Hunter/Gatherer');
    });

    test('narrow tables restack too and keep every value', async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.goto(
            '/tarmar/reference/public-rules/characters/prior-experience/'
        );
        const levelsTable = page.locator('.content table').first();
        await expect(levelsTable).toHaveAttribute('data-columns', '3');
        // Ten experience levels, three cells each, all still rendered.
        await expect(levelsTable.locator('td')).toHaveCount(30);
        expect(await widestHorizontalOverflow(page)).toBeLessThanOrEqual(1);
    });
});
