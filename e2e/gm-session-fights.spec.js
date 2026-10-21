/**
 * E2E for session fights and the records around them (batch d3), each driven
 * in a real browser:
 *
 * - #853: /fight seats a player's character to the player; the fight's own
 *   Game Master takes an absent owner's seat and the owner takes it back,
 *   each in the log.
 * - #871: the owner's sheet lists the Game Master's session holding the
 *   character, links to the fight, and withdraws it; the figure leaves the
 *   live fight.
 * - #868: the Battle tab's Fight form opens again when the fight ends,
 *   without a reload, and the bare-handed line (#844) goes with the fight.
 * - #854: a Game Master's restart leaves a character dead on the record dead
 *   on the board, and the log says why.
 * - #839: a dying character's survival save is rolled from the sheet, once
 *   for these wounds.
 * - #861: Luck is spent once per session per level from the Characters tab.
 * - #844: a character with no weapon is named on the sheet and in the Battle
 *   tab, and buying one clears the sheet's line without a reload.
 * - #840/#863: an injured character's rolls name the penalty; an illiterate
 *   one is refused a Literacy check.
 *
 * Every test seeds its own Game Master, player and characters, named for the
 * worker, the repeat and the moment, so a run with --repeat-each finds only
 * its own rows in every list it reads (the review's finding 4).
 */

const { refusal } = require('./refusals');
const { test, expect, loginAs, submitPassphrase, chooseToAttach } = require('./fixtures');
const { readDjangoShell, runDjangoShell } = require('./django');
const { battleLine, rulingWords } = require('./rulings');
const { uniqueStamp } = require('./unique-name');

const WORKER = Number(process.env.TEST_PARALLEL_INDEX || 0);
// This worker's accounts only: another worker may still be using its own.
const ACCOUNT_PREFIXES = [`d3master-p${WORKER}-`, `d3player-p${WORKER}-`];
const DAGGER = "[{'item_id': 'dagger', 'quantity': 1}]";

/**
 * This test's own accounts and names.
 */
function runFor() {
    const stamp = `p${WORKER}-${uniqueStamp()}`;
    const master = { username: `d3master-${stamp}`, password: 'd3master-pass-853' };
    const player = { username: `d3player-${stamp}`, password: 'd3player-pass-853' };
    /** A character name no other test or repeat uses. */
    const named = (name) => `${name} ${stamp}`;

    /**
     * Create both accounts, then run `characters` (Python lines that may use
     * `master`, `player`, `create_character` and `name(...)`), printing the id
     * of each character variable it binds.
     * @param {string} characters
     * @returns {Record<string, string>} Character ids by the variable name.
     */
    function seed(characters) {
        const output = readDjangoShell(`
from django.contrib.auth import get_user_model
from characters.models import Character
from common.test_helpers import create_character
User = get_user_model()
master = User.objects.create_user(username=${JSON.stringify(master.username)}, password=${JSON.stringify(master.password)}, is_dm=True)
player = User.objects.create_user(username=${JSON.stringify(player.username)}, password=${JSON.stringify(player.password)})
def name(base):
    return base + ' ' + ${JSON.stringify(stamp)}
${characters}
for variable, value in list(locals().items()):
    if isinstance(value, Character):
        print('ID', variable, value.pk)
`);
        const ids = {};
        for (const [, variable, id] of output.matchAll(/^ID (\w+) (\d+)$/gm)) {
            ids[variable] = id;
        }
        return ids;
    }
    return { master, player, named, seed };
}

/** Past any survival save: the board's phase 6 kills them on turn 1. */
const DOOMED = (variable) => `
${variable}.fatigue_damage = ${variable}.fatigue + 5
${variable}.body_damage = ${variable}.body + 200
${variable}.save()`;

/**
 * Attach characters from the picker, then put `/fight <foes>` on the board.
 *
 * The first attach, from a blank chat, makes the session and the page goes
 * to it: that one waits for its load. Every later attach is to the session
 * the page is on and shows in place (#15): the Characters tab is fetched
 * again and the page is not reloaded, so there is no load to wait for. Each
 * then waits for the sheet the server renders from the record (#942), which
 * is on the page only once the attach is saved, so `/fight` never goes in
 * while an attach is still posting. A URL wait could not tell the attaches
 * apart: from the second on, the page is already at `?session=N`.
 * @param {import('@playwright/test').Page} page
 * @param {string[]} names
 * @param {string} foes
 */
async function attachAndFight(page, names, foes) {
    for (const name of names) {
        await chooseToAttach(page, name);
        const sessionUrl = new URL(page.url()).searchParams.has('session') ? page.url() : null;
        if (sessionUrl) {
            await page.click('#gm-character-attach');
        } else {
            await Promise.all([
                page.waitForEvent('load'),
                page.click('#gm-character-attach'),
            ]);
        }
        await expect(page.locator('.gm-sheet-name', { hasText: name })).toHaveCount(1);
        await expect(page).toHaveURL(sessionUrl || /\/tarmar\/gm\/\?session=\d+$/);
    }
    await page.fill('#user-input', `/fight ${foes}`);
    await page.press('#user-input', 'Enter');
    await expect(page.locator('#gm-battle')).toBeVisible();
}

/** A second browser, signed in as `account`. */
async function secondPage(browser, account) {
    const context = await browser.newContext();
    const page = await context.newPage();
    await submitPassphrase(page);
    await loginAs(page, account);
    return { context, page };
}

function card(page, name) {
    return page.locator('#battle-roster .combatant-card', { hasText: name });
}

test.describe('Session fights and the records around them', () => {
    test.afterAll(() => {
        // Every account this worker made here, and with them their characters
        // and sessions; nothing else starts with these prefixes.
        runDjangoShell(`
from django.contrib.auth import get_user_model
from django.db.models import Q
from battle.models import Battle
users = get_user_model().objects.filter(${ACCOUNT_PREFIXES.map((prefix) => `Q(username__startswith=${JSON.stringify(prefix)})`).join(' | ')})
Battle.objects.filter(created_by__in=users).delete()
users.delete()
`);
    });

    test('#853/#871: the owner plays their character, the Game Master stands in only while they are away, and the owner withdraws it', async ({ page, browser }) => {
        const run = runFor();
        const aldwinName = run.named('Seat Aldwin');
        const ids = run.seed(`aldwin = create_character(player, name=name('Seat Aldwin'), equipment=${DAGGER})`);
        await loginAs(page, run.master);
        await page.goto('/tarmar/gm/');
        await attachAndFight(page, [aldwinName], 'a gray wolf');

        const ownManualLine = battleLine('control', 'manual_line', {
            owner: battleLine('control', 'you'),
        });
        const aldwin = card(page, aldwinName);
        await expect(aldwin.locator('.combatant-controller'))
            .toHaveText(battleLine('control', 'manual_line', { owner: run.player.username }));
        await aldwin.getByRole('button', { name: 'Take this seat (owner absent)' }).click();
        await expect(aldwin.locator('.combatant-controller')).toHaveText(ownManualLine);
        await expect(page.locator('#battle-log'))
            .toContainText(battleLine('services', 'seat_taken', {
                game_master: run.master.username, name: aldwinName, owner: run.player.username,
            }));

        const { context, page: player } = await secondPage(browser, run.player);
        try {
            await player.goto(`/tarmar/characters/${ids.aldwin}/`);
            const holding = player.locator('#sessions-holding');
            await expect(holding).toContainText(`run by ${run.master.username}`);
            await holding.getByRole('link', { name: `The fight is on the board: play ${aldwinName}'s turns` }).click();
            await player.waitForURL(/\/tarmar\/battles\/\d+\/$/);
            const mine = card(player, aldwinName);
            // The player is offered no take, only the way back.
            await expect(mine.getByRole('button', { name: 'Take this seat (owner absent)' })).toHaveCount(0);
            await mine.getByRole('button', { name: 'Give the seat back to its owner' }).click();
            await expect(mine.locator('.combatant-controller')).toHaveText(ownManualLine);
            await expect(player.locator('#battle-log'))
                .toContainText(battleLine('services', 'seat_returned', {
                    name: aldwinName, owner: run.player.username,
                }));

            await player.goto(`/tarmar/characters/${ids.aldwin}/`);
            await Promise.all([
                player.waitForEvent('load'),
                player.getByRole('button', { name: 'Withdraw from this session' }).click(),
            ]);
            await expect(player.locator('#sessions-holding')).toHaveCount(0);
        } finally {
            await context.close();
        }
        // The withdrawal wins: the figure left the fight, the session says so.
        await page.reload();
        await expect(page.locator('#battle-log'))
            .toContainText(battleLine('services', 'leaves_fight', {
                name: aldwinName, owner: run.player.username,
            }));
        await expect(card(page, aldwinName)).toHaveCount(0);
        await expect(page.locator('.message.assistant').last())
            .toContainText(`${run.player.username} withdrew ${aldwinName} from this session`);
        await page.click('#gm-panel-tab-characters');
        await expect(page.locator('.gm-sheet', { hasText: aldwinName })).toHaveCount(0);
    });

    test('#868: the Fight form opens again when the fight ends, without a reload', async ({ page }) => {
        const run = runFor();
        const dainName = run.named('Form Dain');
        run.seed(`dain = create_character(master, name=name('Form Dain'), equipment=[])${DOOMED('dain')}`);
        await loginAs(page, run.master);
        await page.goto('/tarmar/gm/');
        await attachAndFight(page, [dainName], 'a gray wolf');
        await expect(page.locator('#gm-fight-start')).toBeDisabled();
        await expect(page.locator('#gm-panel-tab-battle')).toHaveClass(/is-live/);
        // Dain carries nothing (#844): the board says so while he is on it.
        await expect(page.locator('#gm-panel-unarmed'))
            .toHaveText(battleLine('services', 'bare_handed_one', { names: dainName }));

        await page.click('#advance-turn');
        await expect(page.locator('#battle-status')).toHaveText('Finished');
        await expect(page.locator('#gm-fight-start')).toBeEnabled();
        await expect(page.locator('#gm-fight-foes')).toBeEnabled();
        await expect(page.locator('#gm-panel-tab-battle')).not.toHaveClass(/is-live/);
        await expect(page.locator('#gm-panel-unarmed')).toBeHidden();

        // The form posts again: with Dain dead there is nobody to fight.
        await page.fill('#gm-fight-foes', 'a gray wolf');
        await page.click('#gm-fight-start');
        await expect(page.locator('#gm-panel-battle-error'))
            .toHaveText(refusal('gm', 'fight', 'attach_living_character'));
    });

    test('#89: a ruling posted from the Battle tab shows there without a reload', async ({ page }) => {
        const run = runFor();
        const miraName = run.named('Ruling Mira');
        run.seed(`mira = create_character(master, name=name('Ruling Mira'), equipment=${DAGGER})`);
        await loginAs(page, run.master);
        await page.goto('/tarmar/gm/');
        await attachAndFight(page, [miraName], 'a gray wolf');
        const sessionUrl = page.url();
        await page.evaluate(() => { window.issue89NotReloaded = true; });

        // The form posted as an ordinary form, and the server answered with
        // the whole GM page again; it posts as a script caller now, and the
        // tab is drawn from the record.
        const words = rulingWords().hth_agreement;
        const ruling = page.locator('#battle-hth-agreement');
        await expect(ruling.locator('.agreement-line')).toHaveText(words.none_marked);
        await ruling.getByRole('button', { name: words.mark_whole_battle }).click();

        await expect(ruling.locator('.agreement-line')).toHaveText(words.whole_battle_line);
        await expect(ruling.getByRole('button', { name: words.lift_whole_battle })).toBeVisible();
        await expect(page.locator('#gm-panel-battle-error')).toBeHidden();
        expect(page.url()).toBe(sessionUrl);
        expect(await page.evaluate(() => window.issue89NotReloaded === true)).toBe(true);
        // The board drawn again is wired: Advance still plays.
        await page.click('#advance-turn');
        await expect(page.locator('#turn-panel')).toContainText(miraName);
    });

    test('#854: a restart leaves the dead dead on the board, and the log says why', async ({ page }) => {
        const run = runFor();
        const dainName = run.named('Restart Dain');
        const miraName = run.named('Restart Mira');
        run.seed(`
dain = create_character(master, name=name('Restart Dain'), equipment=${DAGGER})${DOOMED('dain')}
mira = create_character(master, name=name('Restart Mira'), equipment=${DAGGER})`);
        await loginAs(page, run.master);
        await page.goto('/tarmar/gm/');
        await attachAndFight(page, [dainName, miraName], 'a gray wolf');

        await page.click('#advance-turn');
        const panel = page.locator('#turn-panel');
        await expect(panel).toContainText(miraName);
        await panel.locator('.turn-option').first().click();
        await panel.locator('.turn-confirm').click();
        await expect(page.locator('#battle-turn')).toHaveText('1');
        await expect(card(page, dainName)).toHaveClass(/is-down/);

        await page.click('#restart-battle');
        await page.click('#restart-confirm');
        await expect(page.locator('#battle-turn')).toHaveText('0');
        await expect(card(page, dainName)).toHaveClass(/is-down/);
        await expect(card(page, miraName)).not.toHaveClass(/is-down/);
        await expect(page.locator('#battle-log'))
            .toContainText(battleLine('services', 'stays_dead', { names: dainName }));
        await page.reload();
        await expect(card(page, dainName)).toHaveClass(/is-down/);
    });

    test('#839: a dying character rolls the survival save from the sheet, once for these wounds', async ({ page }) => {
        const run = runFor();
        const ids = run.seed(`
dain = create_character(player, name=name('Save Dain'), constitution=18)
dain.body_damage = dain.body + (dain.body + 1) // 2
dain.save()`);
        await loginAs(page, run.player);
        await page.goto(`/tarmar/characters/${ids.dain}/`);
        const button = page.locator('#roll-survival-save-btn');
        await expect(button).toHaveText('Roll the survival save (3d6 ≤ 18)');
        await button.click();
        await Promise.all([
            page.waitForEvent('load'),
            page.locator('.notice-confirm').getByRole('button', { name: 'Roll the save' }).click(),
        ]);
        await expect(page.locator('#dice-log')).toContainText('Survival save at Fatigue');
        await expect(page.locator('#recent-changes')).toContainText('survival save, 3d6');
        // CON 18: this save always holds, and the next waits on the pools.
        await expect(page.locator('#roll-survival-save-btn')).toHaveCount(0);
        await expect(page.locator('#survival-save-refused'))
            .toContainText('The survival save for these wounds is already rolled');
    });

    test('#861: Luck is spent once per session per level', async ({ page }) => {
        const run = runFor();
        const wrenName = run.named('Lucky Wren');
        run.seed(`wren = create_character(player, name=name('Lucky Wren'), skills={'luck': 1}, equipment=${DAGGER})`);
        await loginAs(page, run.player);
        await page.goto('/tarmar/gm/');
        await chooseToAttach(page, wrenName);
        await page.click('#gm-character-attach');
        await page.waitForURL(/\/tarmar\/gm\/\?session=\d+$/);
        await page.click('#gm-panel-tab-characters');

        const luck = page.locator('.gm-luck-form');
        await expect(luck.locator('.gm-luck-count')).toHaveText('Luck +1: 0 of 1 used this session');
        await luck.locator('.gm-luck-roll').fill('the climb');
        await luck.getByRole('button', { name: 'Spend Luck' }).click();
        await expect(page.locator('.gm-luck-count')).toHaveText('Luck +1: 1 of 1 used this session');
        await page.locator('.gm-luck-form').getByRole('button', { name: 'Spend Luck' }).click();
        await expect(page.locator('#gm-panel-characters-error')).toContainText(
            refusal('gm', 'luck', 'luck_spent', { name: wrenName, level: 1 })
        );
        await page.reload();
        await page.click('#gm-panel-tab-characters');
        await expect(page.locator('.gm-luck-count')).toHaveText('Luck +1: 1 of 1 used this session');
    });

    test('#844: a character with no weapon is named, and buying one clears the sheet', async ({ page }) => {
        const run = runFor();
        const wrenName = run.named('Bare Wren');
        const tamName = run.named('Bare Tam');
        const ids = run.seed(`
wren = create_character(player, name=name('Bare Wren'), equipment=[], starting_coin=100, wealth_recorded=True)
tam = create_character(player, name=name('Bare Tam'), equipment=[])`);
        await loginAs(page, run.player);
        await page.goto(`/tarmar/characters/${ids.wren}/`);
        await expect(page.locator('#gear-step'))
            .toContainText(battleLine('services', 'unarmed_one', { names: wrenName }));
        await page.locator('#equipment-category').selectOption('weapons');
        await page.locator('#equipment-item').selectOption('dagger');
        await page.locator('#add-equipment-btn').click();
        await expect(page.locator('.equipment-row', { hasText: 'Dagger' })).toBeVisible();
        await expect(page.locator('#gear-step')).toHaveCount(0);

        await page.goto('/tarmar/gm/');
        await chooseToAttach(page, tamName);
        await page.click('#gm-character-attach');
        await page.waitForURL(/\/tarmar\/gm\/\?session=\d+$/);
        await page.click('#gm-panel-tab-battle');
        await expect(page.locator('#gm-panel-unarmed'))
            .toHaveText(battleLine('services', 'unarmed_attached', {
                unarmed: battleLine('services', 'unarmed_one', { names: tamName }),
            }));
    });

    test('#840/#863: an injured character\'s rolls name the penalty, and the illiterate read nothing', async ({ page }) => {
        const run = runFor();
        const ids = run.seed(`
scribe = create_character(player, name=name('Hurt Scribe'), intelligence=10, skills={'literacy': 3},
                          literacy_roll=18, literacy_rolled=True, location_recorded=True,
                          equipment=${DAGGER})
scribe.fatigue_damage = scribe.fatigue - 4
scribe.save()`);
        await loginAs(page, run.player);
        await page.goto(`/tarmar/characters/${ids.scribe}/`);

        const check = page.locator('#skill-check');
        await expect(check.locator('#skill-check-skill option[value="literacy"]'))
            .toContainText('not known to read');
        await check.locator('#skill-check-skill').selectOption('literacy');
        await check.locator('#skill-check-btn').click();
        await expect(check.locator('.mechanics-error')).toContainText('cannot read');

        await check.locator('#skill-check-skill').selectOption('stealth');
        await check.locator('#skill-check-btn').click();
        await expect(page.locator('#skill-check-result')).toContainText('-2 injured = ');

        await page.locator('#attack-resolve-btn').click();
        await expect(
            page.locator('#attack-breakdown li:has(.attack-label:text-is("To hit")) .attack-value').first()
        ).toContainText('-2 injured');
    });
});
