/**
 * E2E: a held seat belongs to its holder (issue #180), and a lobby seat can be
 * renamed (issue #181).
 *
 * Spencer's decision of 2026-11-10: a battle's maker may not take over or
 * rename a figure another player is seated in; only a Game Master account
 * may override. What only the running server proves: a real lobby, made by a
 * player and joined by another, in which the holder renames their own seat
 * from the lobby (which answered 500 before #181) and sees the name on the
 * page; the maker is refused the same rename, and the server offers the
 * maker no rename on that seat (the flag battle.js draws the click from); and
 * a Game Master's rename of the same seat is taken.
 *
 * The accounts are this spec's own (issue #889): a player who makes the
 * lobby, a player who holds the seat, and a Game Master, made in the Django
 * shell for this worker and this repeat and deleted afterwards with their
 * battles and the holder's generated fighter. The suite's shared accounts
 * stay untouched, so a spec that expects TEST_WATCHER's journal empty
 * (home-and-creation-flow) does not depend on whether this one ran first.
 * Every request is made from the page it belongs to, with that page's CSRF
 * token, so the browser's own session and cookies are what the server judges.
 */

const { test, expect, loginAs, submitPassphrase, createBattleViaSetup } = require('./fixtures');
const { runDjangoShell } = require('./django');
const { specAccount, deleteSpecAccountsScript } = require('./accounts');
const { uniqueName } = require('./unique-name');

const makerFor = (testInfo) => specAccount('seatmaker', 'seatmaker-pass-180', testInfo);
const holderFor = (testInfo) => specAccount('seatholder', 'seatholder-pass-180', testInfo);
const gameMasterFor = (testInfo) => specAccount('seatmaster', 'seatmaster-pass-180', testInfo);

/** The three accounts, made afresh; the Game Master carries the DM role. */
function createAccountsScript(maker, holder, gameMaster) {
    return `
from django.contrib.auth import get_user_model
User = get_user_model()
for username, password, is_dm in [
    (${JSON.stringify(maker.username)}, ${JSON.stringify(maker.password)}, False),
    (${JSON.stringify(holder.username)}, ${JSON.stringify(holder.password)}, False),
    (${JSON.stringify(gameMaster.username)}, ${JSON.stringify(gameMaster.password)}, True),
]:
    user, _created = User.objects.get_or_create(username=username)
    user.set_password(password)
    user.is_dm = is_dm
    user.save()
`;
}

test.beforeEach(({}, testInfo) => {
    runDjangoShell(createAccountsScript(makerFor(testInfo), holderFor(testInfo), gameMasterFor(testInfo)));
});

test.afterAll(({}, testInfo) => {
    runDjangoShell(deleteSpecAccountsScript([makerFor(testInfo), holderFor(testInfo), gameMasterFor(testInfo)]));
});

/** POST JSON from the page, as battle.js does, with the page's CSRF token. */
async function postFromPage(page, path, body) {
    return page.evaluate(async ({ path, body }) => {
        const token = document.querySelector('meta[name="csrf-token"]').content;
        const response = await fetch(path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token, 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin',
            body: JSON.stringify(body),
        });
        let data = null;
        try { data = await response.json(); } catch (error) { data = null; }
        return { status: response.status, data };
    }, { path, body });
}

/** The seats as the server offers them to this page's viewer. */
async function seatsFor(page, battleId) {
    const response = await page.request.get(`/tarmar/battles/${battleId}/state/`);
    expect(response.status()).toBe(200);
    return (await response.json()).seats;
}

test('the holder renames their lobby seat; the maker may not, a Game Master may', async ({ browser }, testInfo) => {
    test.setTimeout(90000);
    const maker = makerFor(testInfo);
    const holder = holderFor(testInfo);
    const gameMaster = gameMasterFor(testInfo);
    const makerContext = await browser.newContext();
    const holderContext = await browser.newContext();
    const gameMasterContext = await browser.newContext();
    try {
        const makerPage = await makerContext.newPage();
        await submitPassphrase(makerPage);
        await loginAs(makerPage, maker);
        const battleName = uniqueName('Held seat');
        await createBattleViaSetup(makerPage, { name: battleName, fighters: 1, playerSeats: 1 });
        const battleId = Number(makerPage.url().match(/\/battles\/(\d+)\//)[1]);

        // The holder joins with a generated fighter.
        const holderPage = await holderContext.newPage();
        await submitPassphrase(holderPage);
        await loginAs(holderPage, holder);
        await holderPage.goto(`/tarmar/battles/${battleId}/`);
        await holderPage.click('summary:has-text("Claim this seat")');
        await holderPage.selectOption('select[name="archetype"]', 'archer');
        await holderPage.click('button:has-text("Join with a generated fighter")');
        await expect(holderPage.locator('.lobby-seat.is-you')).toBeVisible();

        const seats = await seatsFor(holderPage, battleId);
        const held = seats.find((seat) => seat.is_you);
        const fighter = seats.find((seat) => !seat.is_open_seat);
        expect(held).toBeTruthy();
        expect(held.can_rename).toBe(true);
        expect(fighter.can_rename).toBe(false);

        // #181: renamed from the lobby, and the lobby shows the name.
        const holderName = uniqueName('Archer');
        const renamed = await postFromPage(holderPage, `/tarmar/battles/${battleId}/rename/`, {
            combatant_id: held.combatant_id, name: holderName,
        });
        expect(renamed.status).toBe(200);
        await holderPage.reload();
        await expect(holderPage.locator('.lobby-seat.is-you')).toContainText(holderName);

        // #180: the maker is offered no rename on the held seat and is
        // refused when they post one anyway; their own fighter stays theirs.
        const makerSeats = await seatsFor(makerPage, battleId);
        expect(makerSeats.find((seat) => seat.combatant_id === held.combatant_id).can_rename).toBe(false);
        expect(makerSeats.find((seat) => seat.combatant_id === fighter.combatant_id).can_rename).toBe(true);
        const takenOver = await postFromPage(makerPage, `/tarmar/battles/${battleId}/rename/`, {
            combatant_id: held.combatant_id, name: 'Taken Over',
        });
        expect(takenOver.status).toBe(403);
        const controlled = await postFromPage(makerPage, `/tarmar/battles/${battleId}/combatants/${held.combatant_id}/control/`, {
            control_mode: 'automatic',
        });
        expect(controlled.status).toBe(403);
        await makerPage.reload();
        await expect(makerPage.locator('.lobby-seat', { hasText: holderName })).toBeVisible();

        // A Game Master overrides.
        const gameMasterPage = await gameMasterContext.newPage();
        await submitPassphrase(gameMasterPage);
        await loginAs(gameMasterPage, gameMaster);
        await gameMasterPage.goto(`/tarmar/battles/${battleId}/`);
        const ruled = await postFromPage(gameMasterPage, `/tarmar/battles/${battleId}/rename/`, {
            combatant_id: held.combatant_id, name: `${holderName} (ruled)`,
        });
        expect(ruled.status).toBe(200);
    } finally {
        await makerContext.close();
        await holderContext.close();
        await gameMasterContext.close();
    }
});
