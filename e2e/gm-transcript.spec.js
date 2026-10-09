/**
 * E2E for downloading a GM session's transcript (issue #389).
 *
 * No Ollama: a played session and its turn record are seeded through the
 * models directly. What is being checked is the part only a browser can
 * show — that the Transcript button and the /transcript command both hand
 * the player a file, that the file carries the hidden entries marked as
 * hidden, and that the chat stays on screen while it downloads.
 */

const fs = require('fs');
const { execSync } = require('child_process');
const { test, expect, loginAs, TEST_USER } = require('./fixtures');

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

const SEED_TRANSCRIPT = `
from django.contrib.auth import get_user_model
from gm.models import GMPromptSnapshot, GMSession, GMTurnRecord
user = get_user_model().objects.get(username='${TEST_USER.username}')
session = GMSession.objects.create(user=user, name='Transcript E2E')
session.history = [{'role': 'user', 'content': 'Canon: the Broken Wheel is an inn.', 'kind': 'canon'}, {'role': 'user', 'content': 'I push open the door.'}, {'role': 'assistant', 'content': 'The common room falls quiet.'}]
session.save()
prompt = GMPromptSnapshot.objects.create(prompt_hash='e2e-transcript-' + str(session.pk), text='E2E SYSTEM PROMPT')
GMTurnRecord.objects.create(session=session, user=user, outcome='ok', mode='play', model='e2e-model', prompt=prompt, user_message='I push open the door.', history_start=0, history_end=3)
print('SESSION', session.pk)
`.trim().replace(/\n/g, '; ');

async function downloadedText(download) {
    return fs.readFileSync(await download.path(), 'utf8');
}

test.describe('GM chat — transcript download', () => {
    test('the Transcript button saves the whole session, hidden entries marked', async ({ page }) => {
        const sessionId = sessionIdFrom(djangoShell(SEED_TRANSCRIPT));
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);
        await expect(page.locator('.message.user')).toContainText('I push open the door.');

        const [download] = await Promise.all([
            page.waitForEvent('download'),
            page.locator('#gm-transcript-button').click(),
        ]);

        expect(download.suggestedFilename()).toMatch(new RegExp(`^gm-session-${sessionId}-transcript-e2e-\\d{8}-\\d{6}\\.txt$`));
        const text = await downloadedText(download);
        expect(text).toContain('GM SESSION TRANSCRIPT');
        expect(text).toContain('I push open the door.');
        expect(text).toContain('[#0] USER · canon · HIDDEN, model only');
        expect(text).toContain('TURN 1 ·');
        expect(text).toContain('model e2e-model');
        expect(text).toContain('E2E SYSTEM PROMPT');
        await expect(page.getByText(/Transcript saved as gm-session-/)).toBeVisible();

        // The page did not navigate away: the conversation is still there.
        await expect(page).toHaveURL(new RegExp(`session=${sessionId}`));
        await expect(page.locator('.message.assistant').last()).toContainText('The common room falls quiet.');
    });

    test('it saves again on every press, not only the first (issue #397)', async ({ page }) => {
        const sessionId = sessionIdFrom(djangoShell(SEED_TRANSCRIPT));
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);
        const requests = [];
        page.on('request', (request) => {
            if (request.url().includes('/transcript/')) requests.push(request.url());
        });

        for (let attempt = 1; attempt <= 3; attempt += 1) {
            const [download] = await Promise.all([
                page.waitForEvent('download'),
                page.locator('#gm-transcript-button').click(),
            ]);
            expect(await downloadedText(download)).toContain('GM SESSION TRANSCRIPT');
        }

        expect(requests).toHaveLength(3);
        await expect(page.getByText(/Transcript saved as gm-session-/)).toHaveCount(3);
    });

    test('typing /transcript downloads the same file and adds nothing to the chat', async ({ page }) => {
        const sessionId = sessionIdFrom(djangoShell(SEED_TRANSCRIPT));
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);
        const userMessages = await page.locator('.message.user').count();

        for (let attempt = 1; attempt <= 2; attempt += 1) {
            const [download] = await Promise.all([
                page.waitForEvent('download'),
                (async () => {
                    await page.locator('#user-input').fill('/transcript');
                    await page.locator('#user-input').press('Enter');
                })(),
            ]);
            expect(await downloadedText(download)).toContain('The common room falls quiet.');
        }
        await expect(page.locator('.message.user')).toHaveCount(userMessages);
    });
});
