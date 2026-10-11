/**
 * E2E: a Game Master's character picker lists his players' characters
 * (issue #759).
 *
 * The attach endpoint has always let a Game Master attach a character he may
 * edit, and /fight needs one attached; the Characters tab said "No characters
 * to attach" all the same, because it listed only his own. It now groups the
 * players' characters by owner. A player's picker is unchanged.
 *
 * The spec seeds its own Game Master and player so no other spec's accounts
 * or characters show up in the lists it reads, named for the worker and the
 * repeat so no other copy of this spec's do either (issue #889).
 */

const { test, expect, loginAs, chooseToAttach } = require('./fixtures');
const { runDjangoShell } = require('./django');
const { specAccount, deleteSpecAccountsScript } = require('./accounts');

// This worker's and this repeat's own accounts and characters (issue #889).
// A Game Master's picker lists every player's characters, so the names carry
// the same suffix: another copy's "Picker Wren" would otherwise show here.
const masterFor = (testInfo) => specAccount('pickermaster', 'pickermaster-pass-759', testInfo);
const playerFor = (testInfo) => specAccount('pickerplayer', 'pickerplayer-pass-759', testInfo);
let MASTER;
let PLAYER;
let WREN;
let KTHAL;

test.describe.configure({ mode: 'serial' });

test.describe('GM panel — a Game Master attaches his players’ characters', () => {
    test.beforeEach(({}, testInfo) => {
        MASTER = masterFor(testInfo);
        PLAYER = playerFor(testInfo);
        const suffix = `w${testInfo.parallelIndex}r${testInfo.repeatEachIndex}`;
        WREN = `Picker Wren ${suffix}`;
        KTHAL = `Picker Kthal ${suffix}`;
        runDjangoShell(`
from django.contrib.auth import get_user_model
from characters.models import Character
from common.test_helpers import create_character
from gm.models import GMSession
User = get_user_model()
master, _ = User.objects.get_or_create(username=${JSON.stringify(MASTER.username)})
master.is_dm = True
master.set_password(${JSON.stringify(MASTER.password)})
master.save()
player, _ = User.objects.get_or_create(username=${JSON.stringify(PLAYER.username)})
player.is_dm = False
player.set_password(${JSON.stringify(PLAYER.password)})
player.save()
GMSession.objects.filter(user__in=[master, player]).delete()
Character.objects.filter(user__in=[master, player]).delete()
create_character(player, name=${JSON.stringify(WREN)})
create_character(master, name=${JSON.stringify(KTHAL)})
`);
    });

    test.afterAll(({}, testInfo) => {
        runDjangoShell(deleteSpecAccountsScript([masterFor(testInfo), playerFor(testInfo)]));
    });

    test('the picker groups the players’ characters by owner and attaches one', async ({ page }) => {
        await loginAs(page, MASTER);
        await page.goto('/tarmar/gm/');

        const select = page.locator('#gm-character-select');
        await expect(select).toBeEnabled();
        await expect(select.locator('optgroup[label="Your characters"] option')).toHaveText([KTHAL]);
        await expect(select.locator(`optgroup[label="${PLAYER.username}"] option`)).toHaveText([WREN]);

        await chooseToAttach(page, WREN);
        await page.click('#gm-character-attach');

        await page.waitForURL(/\/tarmar\/gm\/\?session=\d+$/);
        await expect(page.locator('.gm-sheet .gm-sheet-name')).toHaveText(WREN);
        await expect(select.locator('option', { hasText: WREN })).toHaveCount(0);
    });

    test('a player’s picker still lists only their own characters', async ({ page }) => {
        await loginAs(page, PLAYER);
        await page.goto('/tarmar/gm/');

        const select = page.locator('#gm-character-select');
        await expect(select.locator('option', { hasText: WREN })).toHaveCount(1);
        await expect(select.locator('option', { hasText: KTHAL })).toHaveCount(0);
        await expect(select.locator('optgroup')).toHaveCount(0);
    });
});
