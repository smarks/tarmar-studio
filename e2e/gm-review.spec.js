/**
 * E2E for sending a GM session to Claude for review (issue #402).
 *
 * No model and no Mac Studio: the reviewer's side is played through the
 * same manage.py command it uses over ssh. What is checked is what only a
 * browser shows — the Game Master's button and its in-page question, the
 * notice that says where the findings will be, the review page before and
 * after the findings land, and that a player is never offered the button.
 */

const { execSync } = require('child_process');
const { test, expect, loginAs, TEST_USER, TEST_ADMIN } = require('./fixtures');

function djangoShell(script) {
    return execSync(`uv run python manage.py shell -c "${script.replace(/"/g, '\\"')}"`, {
        cwd: process.cwd(),
        encoding: 'utf8',
        env: { ...process.env, DEBUG: 'true' },
    });
}

function manage(args, input) {
    return execSync(`uv run python manage.py ${args}`, {
        cwd: process.cwd(),
        encoding: 'utf8',
        input,
        env: { ...process.env, DEBUG: 'true' },
    });
}

function sessionIdFrom(output) {
    const match = output.match(/SESSION (\d+)/);
    expect(match, `no session id in: ${output}`).not.toBeNull();
    return match[1];
}

const seedFor = (username) => `
from django.contrib.auth import get_user_model
from gm.models import GMSession
user = get_user_model().objects.get(username='${username}')
session = GMSession.objects.create(user=user, name='Review E2E')
session.history = [{'role': 'user', 'content': 'Build the twins.'}, {'role': 'assistant', 'content': 'Both twins are built.'}]
session.save()
print('SESSION', session.pk)
`.trim().replace(/\n/g, '; ');

test.describe('GM chat — send to Claude', () => {
    test('a Game Master sends the session, and the review page shows the findings once they land', async ({ page }) => {
        const sessionId = sessionIdFrom(djangoShell(seedFor(TEST_ADMIN.username)));
        await loginAs(page, TEST_ADMIN);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);

        await page.locator('#gm-review-button').click();
        const ask = page.locator('.notice-prompt');
        await expect(ask).toContainText('What should Claude look at?');
        await ask.locator('.notice-prompt-input').fill('the twins merged');
        await ask.locator('button:has-text("Send to Claude")').click();

        const sent = page.getByText(/Sent to Claude as review #\d+/);
        await expect(sent).toBeVisible();
        const reviewId = (await sent.textContent()).match(/review #(\d+)/)[1];

        await page.goto(`/tarmar/gm/reviews/${reviewId}/`);
        await expect(page.locator('#gm-review-note')).toHaveText('the twins merged');
        await expect(page.locator('#gm-review-waiting')).toBeVisible();

        manage(`gm_review claim ${reviewId} --runner e2e`);
        manage(`gm_review complete ${reviewId}`, '1. **The twins merged.** Both runs landed on one draft.\n');
        await page.reload();

        await expect(page.locator('#gm-review-status')).toHaveText('Reviewed');
        await expect(page.locator('#gm-review-findings strong')).toHaveText('The twins merged.');
        await expect(page.locator('.gm-review-discuss')).toContainText(`discuss GM review ${reviewId}`);
    });

    test('a player is not offered the button', async ({ page }) => {
        const sessionId = sessionIdFrom(djangoShell(seedFor(TEST_USER.username)));
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);

        await expect(page.locator('#gm-transcript-button')).toBeVisible();
        await expect(page.locator('#gm-review-button')).toHaveCount(0);
    });
});

// A pending review and a finished one, sent by the named user (issue #572).
const seedReviewsFor = (username, tag) => `
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.utils import timezone
from gm.models import GMReviewRequest, GMSession
user = get_user_model().objects.get(username='${username}')
session = GMSession.objects.create(user=user, name='Reviews list E2E ${tag}')
pending = GMReviewRequest.objects.create(session=session, requested_by=user, note='pending ${tag}', snapshot={})
done = GMReviewRequest.objects.create(session=session, requested_by=user, note='done ${tag}', snapshot={}, status='claimed', runner='e2e-runner', claimed_at=timezone.now() - timedelta(minutes=3))
done.complete('1. **Reviewed ${tag}.**')
print('REVIEWS', pending.pk, done.pk, session.pk)
`.trim().replace(/\n/g, '; ');

test.describe('GM reviews list', () => {
    test('lists a waiting and a finished review, and a row opens its review', async ({ page }) => {
        const tag = `t${Date.now()}`;
        const output = djangoShell(seedReviewsFor(TEST_ADMIN.username, tag));
        const [, pendingId, doneId, sessionId] = output.match(/REVIEWS (\d+) (\d+) (\d+)/);
        await loginAs(page, TEST_ADMIN);

        await page.goto(`/tarmar/gm/?session=${sessionId}`);
        await page.locator('#gm-reviews-link').click();
        await expect(page).toHaveURL(/\/tarmar\/gm\/reviews\/$/);

        const pendingRow = page.locator(`tr[data-review-id="${pendingId}"]`);
        const doneRow = page.locator(`tr[data-review-id="${doneId}"]`);
        await expect(pendingRow).toContainText(`pending ${tag}`);
        await expect(pendingRow).toContainText('Waiting for Claude');
        await expect(doneRow).toContainText(`done ${tag}`);
        await expect(doneRow).toContainText('Reviewed');
        await expect(doneRow).toContainText('e2e-runner');
        await expect(doneRow.locator('.gm-reviews-session')).toHaveAttribute('href', `/tarmar/gm/?session=${sessionId}`);
        await expect(page.locator('#gm-reviews-watching')).toBeVisible();

        await page.locator('#gm-reviews-status').selectOption('done');
        await page.getByRole('button', { name: 'Filter' }).click();
        await expect(page).toHaveURL(/status=done/);
        await expect(pendingRow).toHaveCount(0);

        await doneRow.locator('.gm-reviews-link').click();
        await expect(page).toHaveURL(new RegExp(`/tarmar/gm/reviews/${doneId}/$`));
        await expect(page.locator('#gm-review-findings strong')).toHaveText(`Reviewed ${tag}.`);

        await page.locator('#gm-review-all-link').click();
        await expect(page).toHaveURL(/\/tarmar\/gm\/reviews\/$/);
    });

    test('a waiting review\'s row updates itself once Claude claims it', async ({ page }) => {
        const tag = `w${Date.now()}`;
        const output = djangoShell(seedReviewsFor(TEST_ADMIN.username, tag));
        const [, pendingId] = output.match(/REVIEWS (\d+) (\d+) (\d+)/);
        await page.clock.install();
        await loginAs(page, TEST_ADMIN);
        await page.goto('/tarmar/gm/reviews/');
        const pendingRow = page.locator(`tr[data-review-id="${pendingId}"]`);
        await expect(pendingRow).toContainText('Waiting for Claude');

        manage(`gm_review claim ${pendingId} --runner e2e-poll`);
        await page.clock.runFor(16000);

        await expect(pendingRow).toContainText('Claude is reviewing it');
        await expect(pendingRow).toContainText('e2e-poll');
    });

    test('a player is refused the list', async ({ page }) => {
        await loginAs(page, TEST_USER);
        const response = await page.goto('/tarmar/gm/reviews/');
        expect(response.status()).toBe(403);
    });
});
