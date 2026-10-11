/**
 * E2E: a new session can continue the last one's campaign (issue #761).
 *
 * Every campaign record belongs to one session, so a new session used to open
 * with none of the last one's: no place, nobody met, no open thread, no
 * house rule. The new-session page now offers to continue the last played
 * session's campaign, and nothing carries over unless that control is used.
 *
 * The spec seeds its own Game Master with one played session holding a
 * record of each kind, so no other spec's sessions are offered; named for the
 * worker and the repeat, so no other copy of this spec's either (issue #889).
 */

const { test, expect, loginAs } = require('./fixtures');
const { runDjangoShell } = require('./django');
const { specAccount, deleteSpecAccountsScript } = require('./accounts');

/** This worker's and this repeat's own Game Master (issue #889). */
const masterFor = (testInfo) => specAccount('campaignkeeper', 'campaignkeeper-pass-761', testInfo);
let MASTER;

/** @param {{username: string, password: string}} master */
const seedFor = (master) => `
from django.contrib.auth import get_user_model
from gm.models import CampaignState, GMSession, SceneEntity, StoryThread, SupplyItem, TableRuling
user, _ = get_user_model().objects.get_or_create(username=${JSON.stringify(master.username)})
user.is_dm = True
user.set_password(${JSON.stringify(master.password)})
user.save()
GMSession.objects.filter(user=user).delete()
session = GMSession.objects.create(user=user, name='The well beneath Farport', mode='play', summary='The party took the iron box and owe Mother Vane a favour.', summary_covers=1)
session.history = [{'role': 'user', 'content': 'We climb down the well.'}, {'role': 'assistant', 'content': 'The water is cold.'}]
session.save()
CampaignState.objects.create(session=session, place='Farport', day=3)
SceneEntity.objects.create(session=session, kind='person', name='Brannoc', where='the harbour', condition='wary')
SceneEntity.objects.create(session=session, kind='thing', name='iron box', condition='sealed')
StoryThread.objects.create(session=session, text='a favour owed to Mother Vane')
SupplyItem.objects.create(session=session, name='torches', amount='1')
TableRuling.objects.create(session=session, topic='criticals', text='a natural 20 just crits')
`;

test.describe.configure({ mode: 'serial' });

test.describe('GM chat — continuing a campaign in a new session', () => {
    test.beforeEach(({}, testInfo) => {
        MASTER = masterFor(testInfo);
        runDjangoShell(seedFor(MASTER));
    });

    test.afterAll(({}, testInfo) => {
        runDjangoShell(deleteSpecAccountsScript([masterFor(testInfo)]));
    });

    test('the new-session page offers the last campaign, and continuing carries it', async ({ page }) => {
        await loginAs(page, MASTER);
        await page.goto('/tarmar/gm/');

        const offer = page.locator('#gm-campaign-continue');
        await expect(offer).toBeVisible();
        await expect(offer).toContainText('The well beneath Farport');
        await expect(offer).toContainText('1 person');
        await expect(offer).toContainText('the story so far');

        await page.click('#gm-campaign-continue-button');

        await expect(page).toHaveURL(/\/tarmar\/gm\/\?session=\d+$/);
        const continued = page.locator('#gm-session-continued');
        await expect(continued).toContainText('Continues the campaign of');
        await expect(continued.locator('a')).toHaveText('The well beneath Farport');
        // Continuing calls no model; the recap is written on the first turn.
        await expect(continued).toContainText('a recap of it is written when you send your first message');
        // The recap is the model's; the reader is told it is there, not shown it.
        await expect(page.locator('.chat-messages')).not.toContainText('owe Mother Vane a favour');

        await page.click('#gm-panel-tab-campaign');
        const campaign = page.locator('#gm-panel-campaign');
        await expect(campaign).toContainText('Farport');
        await expect(campaign).toContainText('Brannoc');
        await expect(campaign).toContainText('iron box');
        await expect(campaign).toContainText('a favour owed to Mother Vane');
        await expect(campaign).toContainText('torches');
        // With the table's other records in this tab since #789.
        await expect(page.locator('#gm-table-rulings')).toContainText('a natural 20 just crits');

        // The first message writes the recap, then the turn reads it.
        await page.fill('#user-input', 'We return to Farport.');
        await page.click('#send-btn');
        await expect(page.locator('.message.assistant').last()).toContainText('The crypt is quiet', { timeout: 30000 });
        // The words stream before the turn is saved; Send comes back only once
        // the stream has closed, after the done frame the server sends once the
        // turn is saved (issue #910).
        await expect(page.locator('#send-btn')).toBeEnabled({ timeout: 30000 });

        // A reload keeps it: the line is the session's own state.
        await page.reload();
        await expect(page.locator('#gm-session-continued')).toContainText('The well beneath Farport');
        await expect(page.locator('#gm-session-continued')).toContainText('a recap of the whole of it opens this conversation');
        // The recap is the model's; the reader sees the one exchange only.
        await expect(page.locator('.message.user .message-content')).toHaveText(['We return to Farport.']);
        await expect(page.locator('.message.assistant:not(.notice)')).toHaveCount(1);
    });

    test('a session started by typing carries nothing over', async ({ page }) => {
        await loginAs(page, MASTER);
        await page.goto('/tarmar/gm/');
        await page.fill('#user-input', 'A new game on a new road.');
        await page.click('#send-btn');
        await expect(page.locator('.message.assistant')).toContainText('The crypt is quiet', { timeout: 30000 });
        await expect(page).toHaveURL(/session=\d+/);

        await expect(page.locator('#gm-session-continued')).toHaveCount(0);
        await page.click('#gm-panel-tab-campaign');
        await expect(page.locator('#gm-panel-campaign')).not.toContainText('Brannoc');
    });
});
