/**
 * Pinned dice for a roll the page makes through the server (issue #959).
 *
 * A spec cannot patch the dev server the way a Django shell seed patches
 * roll_survival_dice, so it names a seed on the request instead: the
 * X-E2E-Dice-Seed header, which common.middleware.PinnedDiceMiddleware turns
 * into a seeded source for every unseeded roll in that one request.
 * playwright.config.js starts the server with E2E_DICE_SEEDING=true, the
 * switch settings.py refuses unless DEBUG is on. A server started without it
 * ignores the header and rolls real dice, so a spec using this must assert the
 * faces its seed gives; that assertion is what proves the pin took.
 */

/** The request header the middleware reads, as common.middleware spells it. */
const PINNED_DICE_HEADER = 'X-E2E-Dice-Seed';

/**
 * Send `seed` with every request this page makes to `urlPattern`.
 * @param {import('@playwright/test').Page} page
 * @param {string | RegExp} urlPattern - The roll endpoint, as page.route takes it.
 * @param {number} seed - The seed whose faces the spec asserts.
 */
async function pinDice(page, urlPattern, seed) {
    await page.route(urlPattern, (route) => route.continue({
        headers: { ...route.request().headers(), [PINNED_DICE_HEADER]: String(seed) },
    }));
}

module.exports = { PINNED_DICE_HEADER, pinDice };
