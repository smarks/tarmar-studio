/**
 * E2E tests for the character ledger in the GM chat's Characters tab (issue #356).
 *
 * No Ollama: each session's draft is seeded straight onto it through
 * gm.ledger's own rules functions -- the same code path a played turn uses --
 * and the page is then read at that session's URL. What is being checked is
 * that the pane renders the server's numbers, so a disagreement between the
 * GM's prose and the truth is visible rather than argued about.
 *
 * The seeded case is the one issue #356 was filed over: seven years of
 * experience with one skill point spent. The rules price seven campaigner
 * years at five earned points, so the pane must read four free -- not the two
 * the GM asserted in Spencer's session, and not the six he corrected it to.
 */

const { execSync } = require('child_process');
const { test, expect, loginAs, TEST_USER, TEST_ADMIN } = require('./fixtures');

/**
 * Run a Django shell script and return its stdout.
 *
 * `runDjangoShell` in ./django.js inherits stdio, which is what its callers
 * want; these tests need the session id the script prints back, so they
 * capture instead. Same DEBUG=true environment, for the same reason.
 */
function djangoShell(script) {
    return execSync(`uv run python manage.py shell -c "${script.replace(/"/g, '\\"')}"`, {
        cwd: process.cwd(),
        encoding: 'utf8',
        env: { ...process.env, DEBUG: 'true' },
    });
}

function sessionIdFrom(output) {
    const match = output.match(/SESSION (\d+)/);
    expect(match, `no session id in: ${output}`).not.toBeNull();
    return match[1];
}

// Built through gm.testing.run_actions -- the executor a turn's plan runs --
// on the character's record (issues #571, #590), so the pane is fed by the rules
// rather than by hand-written numbers. The prior-experience dice are pinned
// so the run cannot end in death partway (a campaigner's survival target
// is 9, and a death would cut the seven years short). The patchers are
// started and stopped rather than used as a `with` block: the script is
// flattened onto one line for `shell -c`, which takes statements, not blocks.
const SEED_DRAFT = `
from unittest.mock import patch
from django.contrib.auth import get_user_model
from gm.ledger import DRAFT_DEFAULTS
from gm.testing import run_actions, session_with_character
user = get_user_model().objects.get(username='${TEST_USER.username}')
draft = dict(DRAFT_DEFAULTS)
draft.update(name='Wren', strength=13, dexterity=12, intelligence=11, wisdom=10, constitution=14, charisma=9, fatigue_roll=7)
session = session_with_character(user, draft, name='Ledger E2E')
wren = session.attached_characters().get().pk
survival = patch('characters.skills.roll_survival_dice', return_value=(6, 6, 6, 18))
skill_roll = patch('characters.skills.roll_d20', return_value=1)
survival.start()
skill_roll.start()
run_actions(session, {'action': 'play_years', 'character_id': wren, 'track': 'campaigner', 'years': 7})
survival.stop()
skill_roll.stop()
run_actions(session, {'action': 'spend_skill_points', 'character_id': wren, 'skill': 'broadsword', 'points': 1})
print('SESSION', session.pk)
`.trim().replace(/\n/g, '; ');

const SEED_EMPTY = `
from django.contrib.auth import get_user_model
from gm.models import GMSession
user = get_user_model().objects.get(username='${TEST_USER.username}')
print('SESSION', GMSession.objects.create(user=user, name='No Ledger E2E').pk)
`.trim().replace(/\n/g, '; ');

test.describe('GM panel — the character ledger', () => {
    test('the pane shows the draft, its free points and how they were earned', async ({ page }, testInfo) => {
        const sessionId = sessionIdFrom(djangoShell(SEED_DRAFT));

        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);
        await page.click('#gm-panel-tab-characters');

        const ledger = page.locator('#gm-draft-ledger');
        await expect(ledger).toBeVisible();
        await expect(ledger).toContainText('Wren');
        // A record from its first change (issue #571): complete once its
        // attributes and Fatigue roll are in, and it links to its sheet.
        await expect(ledger).toContainText('Complete');

        // The number the whole issue is about: five earned, one spent, four free.
        await expect(page.locator('#gm-draft-free-points')).toHaveText('4 free');
        const account = ledger.locator('.gm-draft-account');
        await expect(account).toContainText('Earned from XP thresholds');
        await expect(account).toContainText('Spent');

        // Pools and years come from Character's own properties, not the model.
        await expect(ledger).toContainText('Fatigue');
        await expect(ledger).toContainText('7 years of experience');

        await expect(ledger.locator('.gm-sheet-name a')).toHaveAttribute('href', /\/tarmar\/characters\/\d+\/$/);

        await page.screenshot({ path: testInfo.outputPath('gm-panel-ledger.png'), fullPage: true });
    });

    test('a session with no draft shows no ledger', async ({ page }) => {
        const sessionId = sessionIdFrom(djangoShell(SEED_EMPTY));

        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);
        await page.click('#gm-panel-tab-characters');
        await expect(page.locator('#gm-draft-ledger')).toHaveCount(0);
    });
});

// Twins, as a Game Master asked for them in session 28 (issue #390): two
// characters in one session, each built through the plan executor and each
// labelled with what the conversation first called them.
// A Game Master's twins: stated scores are his to record, and the executor
// reads the role off the account, so a player's account can no longer be
// handed the authority by the seed (second review of #837).
const SEED_TWINS = `
from django.contrib.auth import get_user_model
from gm.models import GMSession
from gm.testing import run_actions
user = get_user_model().objects.get(username='${TEST_ADMIN.username}')
session = GMSession.objects.create(user=user, name='Twins E2E')
run_actions(session, {'action': 'record_attributes', 'new_character': {'name': 'Twain', 'label': 'the brother'}, 'all': 18}, {'action': 'record_attributes', 'new_character': {'name': 'Sister', 'label': 'the sister'}, 'all': 12}, is_game_master=True)
print('SESSION', session.pk)
`.trim().replace(/\n/g, '; ');

test.describe('GM panel — several characters in progress', () => {
    test('each twin is its own draft, under its own heading', async ({ page }) => {
        const sessionId = sessionIdFrom(djangoShell(SEED_TWINS));

        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);
        await page.click('#gm-panel-tab-characters');

        const drafts = page.locator('.gm-draft');
        await expect(drafts).toHaveCount(2);
        // Two records, in the order they were started, each renamed in place.
        await expect(page.locator('#gm-draft-ledger .gm-sheet-name')).toHaveText('Twain (the brother)');
        await expect(drafts.nth(1).locator('.gm-sheet-name')).toHaveText('Sister (the sister)');
        await expect(page.locator('#gm-draft-free-points')).toHaveCount(1);
        // Rolled as each was made (#859); "needs Fatigue roll" here pinned
        // the older design, which left it for a later plan.
        await expect(drafts.nth(0)).toContainText('Complete');

        // Their numbers stay their own: 18s on his sheet, 12s on hers.
        await expect(drafts.nth(1).locator('.gm-sheet-attributes')).toContainText('12');
        await expect(drafts.nth(1).locator('.gm-sheet-attributes')).not.toContainText('18');
        await expect(drafts.nth(0).locator('.gm-sheet-attributes')).toContainText('18');
    });
});
