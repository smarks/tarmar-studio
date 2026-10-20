/**
 * The roster card's seat hand-offs (#853), as issue #60 found them:
 *
 * - The owner of a saved character nobody sits in was offered "Give the seat
 *   back to its owner", because an empty seat's player differs from every
 *   owner. It is offered only when someone else holds the seat.
 * - The button did not fit its card at 1280 and 1440 px: `nowrap` pushed it
 *   past the card's edge and under the next card. Both hand-offs, the
 *   owner's return and the Game Master's take, are measured against their
 *   card at those widths and two narrower ones.
 *
 * Every row is seeded through the Django shell and named for the worker, the
 * repeat and the moment, so a run with --repeat-each finds only its own.
 */

const { test, expect, submitPassphrase, loginAs } = require('./fixtures');
const { readDjangoJson, runDjangoShell } = require('./django');
const { startCoverage, stopCoverage } = require('./coverage');

const WORKER = Number(process.env.TEST_PARALLEL_INDEX || 0);
const ACCOUNT_PREFIX = `seat60-w${WORKER}r`;
// The widths the walkthrough measured the overflow at, and the two below
// them where the old take button stuck out further still (50 px at 1024).
const VIEWPORTS = [
    { width: 1280, height: 800 },
    { width: 1440, height: 900 },
    { width: 1024, height: 768 },
    { width: 768, height: 1024 },
];
const RETURN_LABEL = 'Give the seat back to its owner';
const TAKE_LABEL = 'Take this seat (owner absent)';

/**
 * Seed a Game Master and a newcomer with two characters: one in the
 * newcomer's own battle, nobody in its seat; one in the Game Master's
 * battle, seated to its owner and then taken by the Game Master.
 * @param {import('@playwright/test').TestInfo} testInfo
 */
function seed(testInfo) {
    const stamp = `${ACCOUNT_PREFIX}${testInfo.repeatEachIndex}t${Date.now() % 1000000}`;
    const master = { username: `${stamp}-gm`, password: 'seat60-master-pass' };
    const owner = { username: `${stamp}-new`, password: 'seat60-owner-pass' };
    const seeded = readDjangoJson(`
import json
from django.contrib.auth import get_user_model
from battle import control
from battle.services import create_battle, take_seat
from common.test_helpers import create_character
User = get_user_model()
master = User.objects.create_user(username=${JSON.stringify(master.username)}, password=${JSON.stringify(master.password)}, is_dm=True)
owner = User.objects.create_user(username=${JSON.stringify(owner.username)}, password=${JSON.stringify(owner.password)}, is_player=True)
wren = create_character(owner, name=${JSON.stringify(`Wren ${stamp}`)})
sable = create_character(owner, name=${JSON.stringify(`Sable ${stamp}`)})
own = create_battle(owner, name=${JSON.stringify(`First fight ${stamp}`)}, count=1, seed=1, character_ids=(wren.pk,), auto_start=True)
held = create_battle(master, name=${JSON.stringify(`Stand in ${stamp}`)}, count=1, seed=1, character_ids=(sable.pk,), auto_start=True)
# Seated to its owner as a session's fight seats it (gm/fight.py), then
# the Game Master stands in for the absent owner.
held.combatants.filter(character=sable).update(player_id=owner.pk, control_mode=control.MANUAL)
take_seat(held.pk, held.combatants.get(character=sable).pk, game_master=master)
print("seeded " + json.dumps({"own": own.pk, "held": held.pk}))
`, 'seeded');
    return { master, owner, stamp, ...seeded };
}

function card(page, name) {
    return page.locator('#battle-roster .combatant-card', { hasText: name });
}

/**
 * The hand-off button sits wholly inside its card at each width.
 * @param {import('@playwright/test').Page} page
 * @param {import('@playwright/test').Locator} holder - the card
 * @param {string} label - the button's name
 */
async function expectFitsItsCard(page, holder, label) {
    for (const viewport of VIEWPORTS) {
        await page.setViewportSize(viewport);
        const button = holder.getByRole('button', { name: label });
        await expect(button).toBeVisible();
        const cardBox = await holder.boundingBox();
        const buttonBox = await button.boundingBox();
        expect(cardBox).not.toBeNull();
        expect(buttonBox).not.toBeNull();
        if (!cardBox || !buttonBox) { return; }
        const where = `${label} at ${viewport.width} px`;
        expect(buttonBox.x, where).toBeGreaterThanOrEqual(cardBox.x);
        expect(buttonBox.x + buttonBox.width, where)
            .toBeLessThanOrEqual(cardBox.x + cardBox.width);
    }
}

test.describe('Seat hand-offs on the roster card (#60)', () => {
    test.afterAll(() => {
        // This worker's accounts only, and the battles and characters they made.
        runDjangoShell(`
from django.contrib.auth import get_user_model
from battle.models import Battle
users = get_user_model().objects.filter(username__startswith=${JSON.stringify(ACCOUNT_PREFIX)})
Battle.objects.filter(created_by__in=users).delete()
users.delete()
`);
    });

    test('the owner is offered their seat back only when someone else holds it, and each hand-off fits its card', async ({ page, browser }, testInfo) => {
        const run = seed(testInfo);
        await loginAs(page, run.owner);

        // The newcomer's own battle: nobody else sits in Wren's seat.
        await page.goto(`/tarmar/battles/${run.own}/`);
        const wren = card(page, `Wren ${run.stamp}`);
        await expect(wren).toBeVisible();
        await expect(wren.locator('.seat-hand-off')).toHaveCount(0);

        // The Game Master's battle: he holds Sable's seat, so the owner may
        // take it back, and the button stays inside the card.
        await page.goto(`/tarmar/battles/${run.held}/`);
        const sable = card(page, `Sable ${run.stamp}`);
        await expectFitsItsCard(page, sable, RETURN_LABEL);
        await sable.getByRole('button', { name: RETURN_LABEL }).click();
        await expect(sable.getByRole('button', { name: RETURN_LABEL })).toHaveCount(0);

        // Back with its owner, the Game Master's take fits its card too.
        const context = await browser.newContext();
        try {
            const master = await context.newPage();
            await startCoverage(master);
            await submitPassphrase(master);
            await loginAs(master, run.master);
            await master.goto(`/tarmar/battles/${run.held}/`);
            await expectFitsItsCard(master, card(master, `Sable ${run.stamp}`), TAKE_LABEL);
            await stopCoverage(master, `${testInfo.title} (Game Master context)`);
        } finally {
            await context.close();
        }
    });
});
