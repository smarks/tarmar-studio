/**
 * E2E for playing the arena without an account (#305).
 *
 * What only a real browser can prove: that a visitor who has answered no
 * passphrase and holds no session can land on the arena, assemble a
 * muster, start a battle and advance a turn — and that the three things a
 * guest may not do are *visible and explained* on the page rather than
 * silently missing, which is the half of the requirement no server test
 * can check.
 *
 * These tests deliberately do NOT use the shared `page` fixture: it
 * submits the passphrase for every test, which is exactly the barrier
 * under examination. Each one opens its own clean browser context.
 */

const { refusal } = require('./refusals');
const { test, expect } = require('./fixtures');
const { uniqueName } = require('./unique-name');
const { startCoverage, stopCoverage } = require('./coverage');
const { E2E_GUEST_BATTLE_PREFIX, resetGuestBattleRate } = require('./cleanup');

/** A page in a brand-new context: no gate cookie, no login, no guest key. */
async function openAsGuest(browser) {
    const context = await browser.newContext();
    const page = await context.newPage();
    await startCoverage(page);
    return { context, page };
}

test.describe('Guest arena', () => {
    // Every page here is 127.0.0.1, and the guest rate limit allows five new
    // battles an hour per IP, so a repeat run spent the allowance on its sixth
    // battle and then tested the limit instead of the arena (issue #882). Each
    // test gets a fresh window; the limit keeps its own server test.
    test.beforeEach(() => {
        resetGuestBattleRate();
    });

    test('a visitor with no account and no passphrase plays a battle', async ({ browser }, testInfo) => {
        test.setTimeout(120000);
        const { context, page } = await openAsGuest(browser);
        const battleName = uniqueName(`${E2E_GUEST_BATTLE_PREFIX}Bout`);

        // The arena opens on arrival — no gate form anywhere on the way.
        await page.goto('/tarmar/battles/');
        await expect(page.locator('input[name="passphrase"]')).toHaveCount(0);
        await expect(page.locator('h1')).toHaveText('Battle Royale Simulator');
        await expect(page.locator('.guest-standing-title')).toHaveText(
            'You are playing as a guest'
        );
        await expect(page.locator('.guest-standing')).toContainText(
            refusal('battle', 'guest_notes', 'keep')
        );

        // New battle is offered, not hidden.
        await page.click('.battle-list-masthead button[type="submit"]');
        await page.waitForURL(/\/tarmar\/battles\/setup\/\d+\/$/);
        const setupUrl = page.url();

        // The limits are on the page, at the controls they apply to: a
        // disabled control, the reason, and a way to sign in.
        const characterLimit = page.locator('#guest-limit-characters');
        await expect(characterLimit).toBeVisible();
        await expect(characterLimit).toContainText(
            refusal('battle', 'guest_notes', 'saved_characters')
        );
        await expect(characterLimit.locator('a')).toHaveText('Sign in');
        await expect(
            page.locator('button:has-text("Seat a saved character")')
        ).toBeDisabled();
        const partyLimit = page.locator('#guest-limit-parties');
        await expect(partyLimit).toBeVisible();
        await expect(partyLimit).toContainText(refusal('battle', 'guest_notes', 'parties'));
        await expect(
            page.locator('button:has-text("Import as a team")')
        ).toBeDisabled();

        // Muster two generated fighters and march.
        await page.click('.setup-fighter-grid form:has(input[value="archer"]) button');
        await page.waitForURL(setupUrl);
        await page.click('.setup-fighter-grid form:has(input[value="wizard"]) button');
        await page.waitForURL(setupUrl);
        await expect(page.locator('.muster-entry')).toHaveCount(2);
        await page.fill('input[name="name"]', battleName);
        await page.click('.setup-begin');
        await page.waitForURL(/\/tarmar\/battles\/\d+\/$/);
        await expect(page.locator('h1')).toHaveText(battleName);

        // And the guest runs their own battle: the turn advances and the
        // log fills in.
        await expect(page.locator('#advance-turn')).toBeVisible();
        await page.click('#advance-turn');
        await expect(page.locator('#battle-turn')).toHaveText('1');
        await expect(page.locator('.turn-group')).toHaveCount(1);

        await stopCoverage(page, testInfo.title);
        await context.close();
    });

    test('a guest lobby offers a generated fighter and explains what it will not offer', async ({ browser }, testInfo) => {
        test.setTimeout(120000);
        const { context, page } = await openAsGuest(browser);
        const battleName = uniqueName(`${E2E_GUEST_BATTLE_PREFIX}Seat`);

        await page.goto('/tarmar/battles/');
        await page.click('.battle-list-masthead button[type="submit"]');
        await page.waitForURL(/\/tarmar\/battles\/setup\/\d+\/$/);
        const setupUrl = page.url();
        await page.click('.setup-fighter-grid form:has(input[value="archer"]) button');
        await page.waitForURL(setupUrl);
        await page.click('.setup-fighter-grid form:has(input[value="wizard"]) button');
        await page.waitForURL(setupUrl);
        await page.click('form:has(input[value="open_seat"]) button');
        await page.waitForURL(setupUrl);
        await page.fill('input[name="name"]', battleName);
        await page.click('.setup-begin');
        await page.waitForURL(/\/tarmar\/battles\/\d+\/$/);

        // The lobby names the limit and still lets the guest sit down.
        await page.click('.seat-claim summary');
        const claimLimit = page.locator('#guest-limit-claim');
        await expect(claimLimit).toBeVisible();
        await expect(claimLimit).toContainText(
            refusal('battle', 'guest_notes', 'claim_character')
        );
        await expect(page.locator('select[name="character_id"]')).toHaveCount(0);
        await page.locator('.seat-claim select[name="archetype"]').selectOption('archer');
        await page.click('.seat-claim button:has-text("Join with a generated fighter")');
        await page.waitForURL(/\/tarmar\/battles\/\d+\/$/);
        await expect(page.locator('.lobby-ready-chip')).toHaveCount(1);

        await stopCoverage(page, testInfo.title);
        await context.close();
    });
});
