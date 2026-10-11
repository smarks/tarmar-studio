/**
 * Searchable pickers (issue #864).
 *
 * The pickers rendered every row they could offer; paging them broke them,
 * because a filter that only hid rows on screen could not reach a row on
 * page 2. Spencer's ruling, 2026-10-09: a search box that queries the server
 * (name, owner), results paged at PAGE_SIZE, newest first so a just-made
 * character is on page one.
 *
 * This spec's player has more characters than one page holds. The newest is
 * on page one, the oldest is not, typing finds the oldest, the pager reaches
 * it too, and the Add, Attach and select controls act on what the search
 * found. Its own accounts (issue #889), so the counts are its own.
 *
 * Issue #973 added the sheet's "Take <name> to" session select and the
 * character list's owner filter to the pickers; issue #971 took disbanded
 * parties out of the battle setup and hook generator pickers.
 */

const { test, expect, loginAs, searchPicker, chooseToAttach } = require('./fixtures');
const { runDjangoShell, readDjangoShell } = require('./django');
const { specAccount, deleteSpecAccountsScript } = require('./accounts');

const PAGE_SIZE = 50;
const MADE = PAGE_SIZE + 5;
const playerFor = (testInfo) => specAccount('pickershelf', 'pickershelf-pass-864', testInfo);
let PLAYER;
let PREFIX;

/** The name of the ``number``th character made, oldest first. */
function nameOf(number) {
    return `${PREFIX} ${String(number).padStart(3, '0')}`;
}

test.describe.configure({ mode: 'serial' });

test.describe('Searchable pickers reach every row (#864)', () => {
    test.beforeEach(({}, testInfo) => {
        PLAYER = playerFor(testInfo);
        PREFIX = `Shelf w${testInfo.parallelIndex}r${testInfo.repeatEachIndex}`;
        runDjangoShell(`
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.utils import timezone
from characters.models import Character
from common.test_helpers import create_character
from gm.models import GMSession
from parties.models import Party
User = get_user_model()
player, _ = User.objects.get_or_create(username=${JSON.stringify(PLAYER.username)})
player.is_dm = False
player.set_password(${JSON.stringify(PLAYER.password)})
player.save()
GMSession.objects.filter(user=player).delete()
Party.objects.filter(owner=player).delete()
Character.objects.filter(user=player).delete()
start = timezone.now() - timedelta(days=1)
for number in range(${MADE}):
    made = create_character(player, name=f"${PREFIX} {number:03d}")
    Character.objects.filter(pk=made.pk).update(created_at=start + timedelta(minutes=number))
Party.objects.create(name=${JSON.stringify(`${PREFIX} Party`)}, owner=player)
`);
    });

    test.afterAll(({}, testInfo) => {
        runDjangoShell(deleteSpecAccountsScript([playerFor(testInfo)]));
    });

    test('the party shelf finds a character on page two by typing, and adds it', async ({ page }) => {
        await loginAs(page, PLAYER);
        await page.goto('/tarmar/parties/');
        await page.locator('a', { hasText: `${PREFIX} Party` }).first().click();
        await page.waitForURL(/\/tarmar\/parties\/\d+\//);

        // The shelf also offers everyone's public characters, so the spec
        // narrows to its own by their shared name first: 55 of them, two
        // pages, newest first, the oldest not on page one.
        const rows = page.locator('.party-addable-row');
        await searchPicker(page, '#party-character-filter', PREFIX);
        await expect(rows).toHaveCount(PAGE_SIZE);
        await expect(rows.first()).toContainText(nameOf(MADE - 1));
        await expect(page.locator('.party-addable-row', { hasText: nameOf(0) })).toHaveCount(0);
        await expect(page.locator('#party-character-picker .page-current')).toHaveText('Page 1 of 2');

        await searchPicker(page, '#party-character-filter', nameOf(0));
        await expect(rows).toHaveCount(1);
        await rows.first().locator('button:has-text("Add")').click();
        await page.waitForURL(/\/tarmar\/parties\/\d+\/$/);
        await expect(page.locator('.party-member', { hasText: nameOf(0) }).first()).toBeVisible();
    });

    test('the pager turns to page two from the keyboard', async ({ page }) => {
        await loginAs(page, PLAYER);
        await page.goto('/tarmar/parties/');
        await page.locator('a', { hasText: `${PREFIX} Party` }).first().click();
        await page.waitForURL(/\/tarmar\/parties\/\d+\//);

        await searchPicker(page, '#party-character-filter', PREFIX);
        const older = page.locator('#party-character-picker button', { hasText: 'Older' });
        await older.focus();
        await page.keyboard.press('Enter');
        await expect(page.locator('#party-character-picker .page-current')).toHaveText('Page 2 of 2');
        await expect(page.locator('.party-addable-row', { hasText: nameOf(0) })).toHaveCount(1);
        // The focus stays on the pager: Older is spent, so it is on Newer.
        await expect(page.locator('#party-character-picker button', { hasText: 'Newer' })).toBeFocused();
    });

    test('the GM attach picker finds the oldest character by typing and attaches it', async ({ page }) => {
        await loginAs(page, PLAYER);
        await page.goto('/tarmar/gm/');
        const select = page.locator('#gm-character-select');
        await expect(select.locator('option', { hasText: nameOf(MADE - 1) })).toHaveCount(1);
        await expect(select.locator('option', { hasText: nameOf(0) })).toHaveCount(0);

        await chooseToAttach(page, nameOf(0));
        await page.click('#gm-character-attach');
        await page.waitForURL(/\/tarmar\/gm\/\?session=\d+$/);
        await expect(page.locator('.gm-sheet .gm-sheet-name')).toHaveText(nameOf(0));
    });

    test('at phone width the session picker fits its bar: no overlap, no sideways scroll', async ({ page }) => {
        await page.setViewportSize({ width: 375, height: 812 });
        await loginAs(page, PLAYER);
        await page.goto('/tarmar/gm/');
        const box = async (selector) => {
            const found = await page.locator(selector).first().boundingBox();
            expect(found, `${selector} is laid out`).not.toBeNull();
            return found;
        };
        const bottom = (rect) => rect.y + rect.height;
        const right = (rect) => rect.x + rect.width;

        const trail = await box('nav.breadcrumb');
        const bar = await box('.mobile-session-bar');
        const select = await box('#mobile-session-select');
        const search = await box('#session-menu-picker-search');
        const newButton = await box('.mobile-session-bar > .btn >> text=+ New');
        const panelButton = await box('#gm-panel-toggle');
        const mode = await box('#gm-mode-indicator');

        // The bar sits between the trail and the chat, overlapping neither.
        expect(bottom(trail)).toBeLessThanOrEqual(bar.y + 0.5);
        expect(bottom(bar)).toBeLessThanOrEqual(mode.y + 0.5);
        // Every control of the bar lies inside it.
        for (const control of [select, search, newButton, panelButton]) {
            expect(control.y).toBeGreaterThanOrEqual(bar.y - 0.5);
            expect(bottom(control)).toBeLessThanOrEqual(bottom(bar) + 0.5);
            expect(right(control)).toBeLessThanOrEqual(375);
        }
        // The select shares the first row with the buttons and fills the rest.
        expect(select.y).toBeLessThan(search.y);
        expect(right(select)).toBeLessThanOrEqual(newButton.x);
        expect(select.width).toBeGreaterThan(375 / 2);
        // The search box takes its own row below, the width of the bar.
        expect(search.y).toBeGreaterThanOrEqual(bottom(select));
        expect(search.width).toBeGreaterThan(375 / 2);

        const scroll = await page.evaluate(() => ({
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth,
        }));
        expect(scroll.scrollWidth).toBeLessThanOrEqual(scroll.clientWidth);

        // Typing in it still searches.
        await searchPicker(page, '#session-menu-picker-search', 'no such session');
        await expect(page.locator('#session-menu-picker [data-picker-status]'))
            .toHaveText('No sessions match “no such session”.');
    });

    test('at phone width a Game Master\'s command bar wraps: both review controls stay on screen (#981)', async ({ adminPage }) => {
        await adminPage.setViewportSize({ width: 375, height: 812 });
        await adminPage.goto('/tarmar/gm/');
        // Only a Game Master has these two, the last controls in the bar.
        for (const selector of ['#gm-review-button', '#gm-reviews-link']) {
            const control = adminPage.locator(selector);
            await expect(control).toBeVisible();
            const box = await control.boundingBox();
            expect(box, `${selector} is laid out`).not.toBeNull();
            expect(box.x).toBeGreaterThanOrEqual(0);
            expect(box.x + box.width).toBeLessThanOrEqual(375);
        }
        const scroll = await adminPage.evaluate(() => ({
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth,
        }));
        expect(scroll.scrollWidth).toBe(scroll.clientWidth);
    });

    test('a search that matches nothing says so in the picker', async ({ page }) => {
        await loginAs(page, PLAYER);
        await page.goto('/tarmar/gm/');
        await searchPicker(page, '#gm-attach-picker-search', 'nobody by this name');
        await expect(page.locator('#gm-attach-picker [data-picker-status]'))
            .toHaveText('No characters match “nobody by this name”.');
        await expect(page.locator('#gm-character-select')).toBeDisabled();
    });

    test('the sheet\'s session picker finds an old session by typing and plays there (#973)', async ({ page }) => {
        const printed = readDjangoShell(`
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.utils import timezone
from characters.models import Character
from gm.models import GMSession
player = get_user_model().objects.get(username=${JSON.stringify(PLAYER.username)})
start = timezone.now() - timedelta(days=2)
sessions = []
for number in range(${MADE}):
    session = GMSession.objects.create(name=f"${PREFIX} night {number:03d}", user=player)
    GMSession.objects.filter(pk=session.pk).update(updated_at=start + timedelta(minutes=number))
    sessions.append(session.pk)
character = Character.objects.get(user=player, name=${JSON.stringify(nameOf(MADE - 1))})
print("SEEDED", character.pk, sessions[0])
`);
        const match = printed.match(/SEEDED (\d+) (\d+)/);
        expect(match, `no ids in: ${printed}`).not.toBeNull();
        const seeded = { character: match[1], oldest: match[2] };
        const nightOf = (number) => `${PREFIX} night ${String(number).padStart(3, '0')}`;
        await loginAs(page, PLAYER);
        await page.goto(`/tarmar/characters/${seeded.character}/`);
        const select = page.locator(`#play-with-gm-session-${seeded.character}`);
        // Page one: "A new session", then the 50 most recently played.
        await expect(select.locator('option')).toHaveCount(PAGE_SIZE + 1);
        await expect(select.locator('option').nth(1)).toHaveText(nightOf(MADE - 1));
        await expect(select.locator('option', { hasText: nightOf(0) })).toHaveCount(0);

        await searchPicker(page, `#play-with-gm-search-${seeded.character}`, nightOf(0));
        await expect(select.locator('option')).toHaveCount(2);
        await select.selectOption(String(seeded.oldest));
        await page.locator('form.play-with-gm button:has-text("Play with the Game Master")').click();
        await page.waitForURL(new RegExp(`/tarmar/gm/\\?session=${seeded.oldest}$`));
        await expect(page.locator('.gm-sheet .gm-sheet-name')).toHaveText(nameOf(MADE - 1));
    });

    test('the character list\'s owner filter finds an owner by typing and filters by them (#973)', async ({ adminPage }) => {
        await adminPage.goto('/tarmar/characters/');
        await searchPicker(adminPage, '#owner-filter-search', PLAYER.username);
        const filter = adminPage.locator('#user-filter');
        await expect(filter.locator('option', { hasText: PLAYER.username })).toHaveCount(1);
        await filter.selectOption({ label: PLAYER.username });
        await adminPage.waitForURL(/[?&]user=\d+/);
        // The list is the player's, and the filter still names them.
        await expect(adminPage.locator('.character-table tbody tr').first()).toContainText(nameOf(MADE - 1));
        await expect(adminPage.locator('#user-filter option:checked')).toHaveText(PLAYER.username);
    });

    test('a disbanded party is offered by neither battle setup nor the hook generator (#971)', async ({ adminPage }) => {
        runDjangoShell(`
from django.contrib.auth import get_user_model
from parties.models import Party
player = get_user_model().objects.get(username=${JSON.stringify(PLAYER.username)})
Party.objects.create(name=${JSON.stringify(`${PREFIX} Disbanded`)}, owner=player, is_deleted=True)
`);
        await adminPage.goto('/tarmar/battles/');
        await adminPage.click('.battle-list-masthead button[type="submit"]');
        await adminPage.waitForURL(/\/tarmar\/battles\/setup\/\d+\/$/);
        await searchPicker(adminPage, '#setup-party-picker-search', PREFIX);
        const setupOptions = adminPage.locator('.setup-party-form select[name="party_id"] option');
        await expect(setupOptions).toHaveCount(1);
        await expect(setupOptions.first()).toContainText(`${PREFIX} Party`);

        await adminPage.goto('/tarmar/gm/hooks/');
        await searchPicker(adminPage, '#hook-party-picker-search', PREFIX);
        const hookOptions = adminPage.locator('#hook-form select[name="party"] option');
        await expect(hookOptions.filter({ hasText: `${PREFIX} Party` })).toHaveCount(1);
        await expect(hookOptions.filter({ hasText: `${PREFIX} Disbanded` })).toHaveCount(0);
    });
});
