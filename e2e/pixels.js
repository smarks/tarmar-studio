/**
 * Reading what a page drew, for a spec that has to prove a thing is painted
 * where it belongs rather than that a stylesheet says so.
 *
 * Two screenshots of one clip are compared: the page as it is, and the page
 * with the one thing under test switched off. The rows where they differ are
 * the rows that thing paints. Comparing against the page itself, not against
 * a stored image, keeps the check free of fonts, themes and platforms.
 */

/**
 * The most a colour channel may differ, in levels of 255, before two pixels
 * count as different. Measured noise between two screenshots of an unchanged
 * clip is at most one level; anything a reader can see is tens of levels.
 */
const NOISE_LEVELS = 2;

/**
 * The rows of a clip in which two screenshots of it differ.
 * @param {import('@playwright/test').Page} page - Decodes the images: a browser already can.
 * @param {Buffer} first - A PNG.
 * @param {Buffer} second - A PNG of the same clip.
 * @returns {Promise<number[]>} row offsets from the clip's top, ascending
 */
async function differingRows(page, first, second) {
    return page.evaluate(async ({ encoded, noise }) => {
        const pixelsOf = async (base64) => {
            const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
            const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
            const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
            const context = canvas.getContext('2d');
            context.drawImage(bitmap, 0, 0);
            return { levels: context.getImageData(0, 0, bitmap.width, bitmap.height).data, width: bitmap.width };
        };
        const [before, after] = await Promise.all(encoded.map(pixelsOf));
        const channelsPerRow = before.width * 4;
        const rows = new Set();
        before.levels.forEach((level, index) => {
            if (Math.abs(level - after.levels[index]) > noise) {
                rows.add(Math.floor(index / channelsPerRow));
            }
        });
        return [...rows].sort((upper, lower) => upper - lower);
    }, { encoded: [first.toString('base64'), second.toString('base64')], noise: NOISE_LEVELS });
}

module.exports = { NOISE_LEVELS, differingRows };
