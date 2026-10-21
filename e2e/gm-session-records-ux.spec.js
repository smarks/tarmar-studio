/**
 * E2E for the GM session's records and page (batch D2, 2026-09-29).
 *
 * - #838: a /roll reads back as the command and its dice after a reload.
 * - #829: the session's name and the rail's count follow the first reply.
 * - #830: /help lists /rename, and /review only for a Game Master.
 * - #831: a session fight is named from its sides, on one line in the pane.
 * - #785: a session fight's end is told in the pane and in the chat.
 * - #848, #872: a chat-built character's card carries what an attached one does.
 * - #789, #790: the Game Master adds and corrects records by hand; a player cannot.
 * - #791: the fold's recap is shown where the fold is, and corrected by hand.
 * - #16: a correction being typed survives the turn's refetch of the tab.
 *
 * Turns are played against the fake Ollama (e2e/fake-ollama.js). The fights
 * are a Game Master's, whose typed foes are built as typed.
 */

const { test, expect, loginAs, TEST_USER, TEST_ADMIN, chooseToAttach } = require('./fixtures');
const { uniqueName } = require('./unique-name');
const { runDjangoShell, readDjangoShell } = require('./django');

/** A character for ``owner`` whose first turn on the board is their last. */
function seedDyingCharacter(owner, name) {
    runDjangoShell(`
from django.contrib.auth import get_user_model
from characters.models import Character

owner = get_user_model().objects.get(username=${JSON.stringify(owner)})
character = Character.objects.create(
    user=owner, name=${JSON.stringify(name)},
    strength=12, dexterity=12, intelligence=12, wisdom=12, constitution=12,
    charisma=12, appearance_roll='3', height_roll='3', weight_roll='3',
    fatigue_roll=7,
)
character.fatigue_damage = character.fatigue + 5
character.body_damage = character.body + 200
character.save()
`);
}

/** A session for ``owner`` built by ``script`` (Python, ``session`` in scope). */
function seedSession(owner, name, script) {
    const output = readDjangoShell(`
from django.contrib.auth import get_user_model
from gm.models import GMSession
from gm.testing import run_actions
user = get_user_model().objects.get(username=${JSON.stringify(owner)})
session = GMSession.objects.create(user=user, name=${JSON.stringify(name)}, mode='aide')
${script}
print('SESSION', session.pk)
`);
    const match = output.match(/SESSION (\d+)/);
    expect(match).not.toBeNull();
    return match[1];
}

async function send(page, line) {
    await page.fill('#user-input', line);
    await page.press('#user-input', 'Enter');
    await expect(page.locator('.message.assistant').last()).toContainText('nothing stirs', { timeout: 30000 });
}

test.describe('GM session — the chat reads back as it was played', () => {
    test('/roll shows the command and its dice after a reload (#838)', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/gm/');
        await page.fill('#user-input', '/roll 3d6');
        await page.press('#user-input', 'Enter');
        const liveChip = page.locator('#messages .dice-roll');
        await expect(liveChip).toHaveText(/^🎲 3d6: \[\d, \d, \d\] = \d+$/, { timeout: 30000 });
        const rolled = await liveChip.textContent();
        await expect(page).toHaveURL(/session=\d+/);
        await expect(page.locator('#send-btn')).toBeEnabled({ timeout: 30000 });

        await page.reload();

        await expect(page.locator('.message.user .message-content')).toHaveText(['/roll 3d6']);
        await expect(page.locator('#messages .dice-roll')).toHaveText([rolled]);
        await expect(page.locator('#messages')).not.toContainText('Please adjudicate');
        await expect(page.locator('#session-name')).toHaveValue('/roll 3d6');
    });

    test('the name and the rail follow the first reply, with no reload (#829)', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/gm/');
        await chooseToAttach(page, 'Aldwin the Bold');
        await page.click('#gm-character-attach');
        await page.waitForURL(/\/tarmar\/gm\/\?session=\d+$/);
        const sessionId = new URL(page.url()).searchParams.get('session');
        const entry = page.locator(`#session-list .session-item[data-id="${sessionId}"]`);
        await expect(page.locator('#session-name')).toHaveValue('New Session');
        await expect(entry.locator('.session-count')).toHaveText('0 msgs');

        await send(page, 'I walk into the tavern and look for work.');

        await expect(page.locator('#session-name')).toHaveValue('I walk into the tavern and look for work.');
        await expect(entry.locator('.session-name')).toHaveText('I walk into the tavern and look for work.');
        await expect(entry.locator('.session-count')).toHaveText('2 msgs');
    });

    test('/help is written for the account reading it (#830)', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/gm/');
        await page.fill('#user-input', '/help');
        await page.press('#user-input', 'Enter');
        const help = page.locator('#messages .message.assistant').last();
        await expect(help).toContainText('/rename <name>');
        await expect(help).toContainText('Rename this session');
        await expect(help).not.toContainText('/review');
    });

    test('/help offers /review to a Game Master (#830)', async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        await page.goto('/tarmar/gm/');
        await page.fill('#user-input', '/help');
        await page.press('#user-input', 'Enter');
        await expect(page.locator('#messages .message.assistant').last()).toContainText('/review');
    });
});

test.describe('GM session — a fight on the board', () => {
    test('is named from its sides and says how it ended, in the pane and the chat (#831, #785)', async ({ page }, testInfo) => {
        const name = uniqueName('Dain');
        seedDyingCharacter(TEST_ADMIN.username, name);
        await loginAs(page, TEST_ADMIN);
        await page.goto('/tarmar/gm/');
        await chooseToAttach(page, name);
        await page.click('#gm-character-attach');
        await page.waitForURL(/\/tarmar\/gm\/\?session=\d+$/);

        await page.fill('#user-input', '/fight a gray wolf');
        await page.press('#user-input', 'Enter');
        await expect(page.locator('#gm-battle')).toBeVisible();

        // Named from its sides, and on one line however narrow the pane.
        const battleName = page.locator('.gm-battle-name');
        await expect(battleName).toHaveText(`${name} vs Gray Wolf`);
        await expect(battleName).toHaveCSS('white-space', 'nowrap');
        const box = await battleName.boundingBox();
        expect(box.height).toBeLessThan(40);
        await expect(page.locator('#gm-battle-verdict')).toBeHidden();

        // One turn: the survival save cannot be made, and the fight is over.
        await page.click('#advance-turn');
        await expect(page.locator('#battle-status')).toHaveText('Finished');

        // Told at once, without a message from the player or a reload.
        const verdict = page.locator('#gm-battle-verdict');
        await expect(verdict).toBeVisible();
        await expect(verdict).toContainText('The fight is over: The foes stand victorious');
        await expect(verdict).toContainText(`${name}: dead`);
        await expect(verdict).not.toContainText('Foes stands');
        const notice = page.locator('#messages .message.notice[data-battle-verdict]');
        await expect(notice).toHaveCount(1);
        await expect(notice).toContainText(`${name}: dead`);
        // The app speaking, not the Game Master (#339 item 11).
        await expect(notice.locator('.message-role')).toHaveText('Notice');
        await page.screenshot({ path: testInfo.outputPath('fight-verdict.png'), fullPage: true });

        // It is state, not a toast: a reload shows it once, where it was.
        await page.reload();
        await expect(page.locator('#messages .message.notice[data-battle-verdict]')).toHaveCount(1);
        await page.click('#gm-panel-tab-battle');
        await expect(page.locator('#gm-battle-verdict')).toContainText('The foes stand victorious');
    });
});

test.describe('GM session — the Characters pane draws every character one way', () => {
    test('a chat-built character carries Physical, Mental, purse and literacy where an attached one does (#848, #872)', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/gm/');
        await chooseToAttach(page, 'Aldwin the Bold');
        await page.click('#gm-character-attach');
        await page.waitForURL(/\/tarmar\/gm\/\?session=\d+$/);
        await page.click('#gm-panel-tab-characters');
        await send(page, 'Roll up Pellam');

        const built = page.locator('#gm-panel-characters .gm-draft', {
            has: page.locator('.gm-sheet-name', { hasText: 'Pellam' }),
        });
        const attached = page.locator('#gm-panel-characters .gm-sheet', {
            has: page.locator('.gm-sheet-name', { hasText: 'Aldwin the Bold' }),
        });
        await expect(built).toHaveCount(1);
        const builtLabels = await built.locator('dt').allTextContents();
        const attachedLabels = await attached.locator('dt').allTextContents();
        expect(builtLabels).toEqual(attachedLabels);
        for (const label of ['Physical', 'Mental', 'Purse', 'Literacy']) {
            expect(builtLabels).toContain(label);
        }
        expect(builtLabels.indexOf('Literacy')).toBe(builtLabels.indexOf('Background') + 1);
        await expect(built.locator('.gm-sheet-purse')).toHaveText(/gp|Consult GM/);
    });
});

test.describe('GM session — records the Game Master sets by hand', () => {
    test('he adds a ruling and a thread, corrects a person, and closes the thread (#789, #790)', async ({ page }) => {
        const sessionId = seedSession(TEST_ADMIN.username, 'Hand records E2E', `
run_actions(session, {'action': 'record_npc', 'name': 'Brannoc', 'where': 'the wrong shrine'})
`);
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);
        await page.click('#gm-panel-tab-campaign');
        const campaign = page.locator('#gm-panel-campaign');

        // A ruling, by hand; the model's list and the pane are the same rows.
        await campaign.locator('#gm-add-ruling summary').click();
        await campaign.locator('#gm-add-ruling input[name="topic"]').fill('critical hits');
        await campaign.locator('#gm-add-ruling textarea[name="text"]').fill('a natural 20 just crits');
        await campaign.locator('#gm-add-ruling button[type="submit"]').click();
        const rulings = page.locator('#gm-table-rulings li');
        await expect(rulings).toHaveCount(1);
        await expect(rulings.first()).toContainText('critical hits: a natural 20 just crits');
        await expect(rulings.first()).toContainText(`Set by hand by ${TEST_ADMIN.username}`);
        // The refetch has landed and the add form is free again: a hold left
        // on its key would keep it disabled (#142).
        await expect(campaign.locator('#gm-add-ruling button[type="submit"]')).toBeEnabled();

        // A wrong "where", corrected.
        const brannoc = campaign.locator('li[data-entity-id]', { hasText: 'Brannoc' });
        await expect(brannoc).toContainText('at the wrong shrine');
        await brannoc.locator('summary', { hasText: 'Correct' }).click();
        await brannoc.locator('input[name="where"]').fill('the shrine of Qasha');
        await brannoc.locator('button[type="submit"]').click();
        const corrected = page.locator('#gm-panel-campaign li[data-entity-id]', { hasText: 'Brannoc' });
        await expect(corrected).toContainText('at the shrine of Qasha');
        await expect(corrected).toContainText('person');

        // A thread, opened with its number shown, then closed.
        await campaign.locator('#gm-open-thread summary').click();
        await campaign.locator('#gm-open-thread textarea[name="text"]').fill('who took the iron box');
        await campaign.locator('#gm-open-thread button[type="submit"]').click();
        const thread = page.locator('#gm-panel-campaign li[data-thread-id]', { hasText: 'who took the iron box' });
        await expect(thread.locator('.gm-thread-number')).toHaveText(/^thread \d+$/);
        await thread.locator('summary', { hasText: 'Resolve' }).click();
        await thread.locator('input[name="resolution"]').fill('Brannoc had it');
        await thread.getByRole('button', { name: 'Close the thread' }).click();
        await expect(page.locator('#gm-panel-campaign .gm-campaign-resolved')).toContainText('1 resolved');
        await expect(page.locator('#gm-panel-campaign-error')).toBeHidden();
    });

    test('a player reads the records and has no controls for them (#789, #790)', async ({ page }) => {
        const sessionId = seedSession(TEST_USER.username, 'Player records E2E', `
run_actions(session, {'action': 'record_npc', 'name': 'Vane'}, {'action': 'open_thread', 'text': 'a favour owed'})
`);
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);
        await page.click('#gm-panel-tab-campaign');
        const campaign = page.locator('#gm-panel-campaign');
        await expect(campaign).toContainText('Vane');
        await expect(campaign.locator('.gm-thread-number')).toHaveText(/^thread \d+$/);
        await expect(campaign.locator('.gm-hand-form')).toHaveCount(0);
        await expect(campaign.locator('.gm-hand-action')).toHaveCount(0);
    });

    test('the fold is shown where it happened, and he corrects the recap (#791)', async ({ page }) => {
        const sessionId = seedSession(TEST_ADMIN.username, 'Folded E2E', `
history = []
for turn in range(6):
    history.append({'role': 'user', 'content': f'We go deeper {turn}.'})
    history.append({'role': 'assistant', 'content': f'Room {turn} is empty.'})
session.history = history
session.summary = 'The fake Game Master considers it. The crypt is quiet, and nothing stirs.'
session.summary_covers = 4
session.save()
`);
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);

        const recap = page.locator('#gm-fold-recap');
        await expect(recap).toBeVisible();
        await expect(recap).toContainText('Recap so far');
        await expect(recap).toContainText('The 4 messages above are now read by the Game Master as this recap');
        // Between the last folded message and the first kept one.
        const order = await page.locator('#messages > *').evaluateAll((nodes) => nodes.map((node) => node.id || node.textContent.replace(/\s+/g, ' ').trim()));
        const at = order.indexOf('gm-fold-recap');
        expect(order[at - 1]).toContain('Room 1 is empty.');
        expect(order[at + 1]).toContain('We go deeper 2.');

        await recap.locator('summary').click();
        await recap.locator('textarea[name="text"]').fill('The party went down into the crypt and found four empty rooms.\n\nNothing stirred.');
        await recap.locator('button[type="submit"]').click();
        // Its paragraphs kept (review of #791).
        await expect(page.locator('#gm-fold-recap-text')).toHaveText('The party went down into the crypt and found four empty rooms.\n\nNothing stirred.');
        await expect(page.locator('#gm-fold-recap-status')).toContainText(`Set by hand by ${TEST_ADMIN.username}`);

        await page.reload();
        await expect(page.locator('#gm-fold-recap-text')).toHaveText('The party went down into the crypt and found four empty rooms.\n\nNothing stirred.');
        const transcript = await page.request.get(`/tarmar/gm/sessions/${sessionId}/transcript/`);
        const text = await transcript.text();
        expect(text).toContain('[fold] ENTRIES #0-#3 ARE FOLDED');
        expect(text).toContain('RECORDS SET BY HAND');
        expect(text).toContain('found four empty rooms');
    });
});

test.describe('GM session — a correction being typed survives a turn (#16)', () => {
    /**
     * Mark the Campaign tab's contents as they are now, so the spec can wait
     * for the turn's refetch to have replaced them -- an event, not a sleep.
     */
    async function markCampaignContents(page) {
        await page.locator('#gm-panel-campaign .gm-campaign-where').evaluate((node) => {
            node.dataset.beforeRefetch = 'yes';
        });
    }

    async function campaignRefetched(page) {
        await expect(page.locator('#gm-panel-campaign [data-before-refetch]')).toHaveCount(0, { timeout: 30000 });
    }

    function seedThreads(texts) {
        return seedSession(TEST_ADMIN.username, uniqueName('Correct mid-turn E2E'), texts
            .map((text) => `run_actions(session, {'action': 'open_thread', 'text': ${JSON.stringify(text)}})`)
            .join('\n'));
    }

    async function openCorrect(page, threadText, typed) {
        const thread = page.locator('#gm-panel-campaign li[data-thread-id]', { hasText: threadText });
        await thread.locator('summary', { hasText: 'Correct' }).click();
        const field = thread.locator('textarea[name="text"]');
        await field.fill(typed);
        return thread;
    }

    test('the form stays open with the text typed through a turn', async ({ page }) => {
        const sessionId = seedThreads(['who took the iron box', 'the debt to Vane']);
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);
        await page.click('#gm-panel-tab-campaign');
        const thread = await openCorrect(page, 'who took the iron box', 'who took the iron box, and why');
        await markCampaignContents(page);

        await send(page, 'We wait by the shrine.');
        await expect(page.locator('#send-btn')).toBeEnabled({ timeout: 30000 });
        await campaignRefetched(page);

        await expect(thread.locator('details.gm-hand-edit').first()).toHaveAttribute('open', '');
        await expect(thread.locator('textarea[name="text"]')).toHaveValue('who took the iron box, and why');
        await expect(page.locator('#gm-panel-campaign [data-drafts-lost]')).toHaveCount(0);
    });

    test('typing goes on in the same field after a refetch lands mid-word', async ({ page }) => {
        const sessionId = seedThreads(['who took the iron box']);
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);
        await page.click('#gm-panel-tab-campaign');
        const thread = await openCorrect(page, 'who took the iron box', 'who stole the');
        await markCampaignContents(page);

        // The frame chat.js dispatches for each procedure of a running turn.
        await page.evaluate(() => document.dispatchEvent(new CustomEvent('gm-stream-procedure', { detail: {} })));
        await campaignRefetched(page);
        await page.keyboard.type(' iron box');

        await expect(thread.locator('textarea[name="text"]')).toHaveValue('who stole the iron box');
        await expect(thread.locator('textarea[name="text"]')).toBeFocused();
    });

    test('a draft whose thread the turn closed is shown, not dropped', async ({ page }) => {
        const sessionId = seedThreads(['who took the iron box', 'the debt to Vane']);
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);
        await page.click('#gm-panel-tab-campaign');
        await openCorrect(page, 'who took the iron box', 'who took the iron box, and why');
        await markCampaignContents(page);
        // The record changes on the server, as a turn's resolve_thread would.
        runDjangoShell(`
from gm.models import StoryThread
StoryThread.objects.filter(session_id=${sessionId}, text='who took the iron box').update(resolved=True)
`);

        await send(page, 'We wait by the shrine.');
        await campaignRefetched(page);

        const notice = page.locator('#gm-panel-campaign [data-drafts-lost]');
        await expect(notice).toContainText('no longer in this list');
        await expect(notice.locator('li strong')).toHaveText(/^Correct thread \d+$/);
        await expect(notice.locator('dd')).toHaveText('who took the iron box, and why');
        // Gone from the open threads; it is listed with the resolved ones.
        await expect(page.locator('#gm-campaign-threads-group > ul > li[data-thread-id]', { hasText: 'who took the iron box' })).toHaveCount(0);
        await expect(page.locator('#gm-campaign-threads-group .gm-campaign-resolved')).toContainText('who took the iron box');

        await notice.getByRole('button', { name: /^Dismiss/ }).click();
        await expect(notice).toHaveCount(0);
    });
});
