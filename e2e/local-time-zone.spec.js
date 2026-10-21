/**
 * A time on a page is the time where the reader is, not UTC (issue #62).
 *
 * The server renders every timestamp as a <time> element holding the instant,
 * and static/js/local-time.js writes it in the browser's zone. The tester saw
 * the battle list say "Created 2026-10-15 02:50", the server's UTC fallback,
 * beside a header that said "Signed in since Oct 14, 2026, 10:58 PM". These
 * specs fix the browser's zone with Playwright's timezoneId, read the instant
 * the server holds through the Django shell, and check what the page says
 * against it in that zone. They also cover the places a time reached a reader
 * in UTC by another road: the session rail and the small-screen session menu
 * after a search has repainted them, and the date the rail's delete control
 * names in its label and its confirmation.
 *
 * The zones are far from UTC, one on the half hour, so a page that left the
 * instant in UTC, or rounded the zone to whole hours, shows a different clock.
 */

const {
    test, expect, loginAs, createBattleViaSetup, searchPicker, TEST_ADMIN, TEST_USER,
} = require('./fixtures');
const { uniqueName, uniqueStamp } = require('./unique-name');
const { readDjangoJson } = require('./django');

const ZONES = ['America/New_York', 'Asia/Kolkata'];
/** An instant just after UTC midnight: the date differs in New York. */
const SESSION_STARTED = '2026-10-15T02:50:00+00:00';

/**
 * What the page should say for an instant in a zone. The test's statement of
 * the display: month name, day, year, and for a datetime the time of day.
 * Spaces are compared as one kind because the browser and Node may write the
 * space before "AM" differently.
 * @param {string} instant - ISO-8601.
 * @param {string} timeZone - IANA zone.
 * @param {'datetime'|'date'} style
 * @returns {string}
 */
function wordsFor(instant, timeZone, style) {
    const options = {
        timeZone, year: 'numeric', month: 'short', day: '2-digit',
        ...(style === 'datetime' ? { hour: '2-digit', minute: '2-digit' } : {}),
    };
    return new Intl.DateTimeFormat('en-US', options).format(new Date(instant)).replace(/\s/g, ' ');
}

/** The text of a locator with every kind of space made an ordinary one. */
async function spokenText(locator) {
    return (await locator.innerText()).replace(/\s/g, ' ').trim();
}

for (const timeZone of ZONES) {
    test.describe(`in ${timeZone}`, () => {
        // The page writes in the browser's locale; the test states its words in this one.
        test.use({ timezoneId: timeZone, locale: 'en-US' });

        test('the battle list and the header show the instants in the reader\'s zone', async ({ page }) => {
            await loginAs(page, TEST_ADMIN);
            const battleName = uniqueName(`E2E Zone ${timeZone.replace('/', ' ')}`);
            await createBattleViaSetup(page, { name: battleName, fighters: 2, seed: 42 });
            const held = readDjangoJson(`
import json
from battle.models import Battle
from django.contrib.auth import get_user_model
battle = Battle.objects.get(name=${JSON.stringify(battleName)})
user = get_user_model().objects.get(username=${JSON.stringify(TEST_ADMIN.username)})
print('HELD', json.dumps({'created': battle.created_at.isoformat(), 'signedIn': user.last_login.isoformat()}))
`.trim(), 'HELD');

            await page.goto('/tarmar/battles/');
            const row = page.locator('tr', { hasText: battleName });
            const created = row.locator('time.local-time');
            await expect(created).toHaveAttribute('datetime', held.created);
            expect(await spokenText(created)).toBe(wordsFor(held.created, timeZone, 'datetime'));
            // Not the server's UTC fallback, whose shape is "Y-m-d H:i UTC".
            expect(await spokenText(created)).not.toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC$/);

            const since = page.locator('.identity-since time.local-time');
            await expect(since).toHaveAttribute('datetime', held.signedIn);
            expect(await spokenText(since)).toBe(wordsFor(held.signedIn, timeZone, 'datetime'));
        });

        test('without the script the list says UTC, so the fallback is not read as local time', async ({ page }) => {
            await loginAs(page, TEST_ADMIN);
            const battleName = uniqueName(`E2E Fallback ${timeZone.replace('/', ' ')}`);
            await createBattleViaSetup(page, { name: battleName, fighters: 2, seed: 42 });
            const created = readDjangoJson(`
import json
from battle.models import Battle
print('HELD', json.dumps(Battle.objects.get(name=${JSON.stringify(battleName)}).created_at.isoformat()))
`.trim(), 'HELD');
            // The script never arrives (blocked, or the page is read before it runs).
            await page.route('**/local-time.js', (route) => route.abort());

            await page.goto('/tarmar/battles/');
            const cell = page.locator('tr', { hasText: battleName }).locator('time.local-time');

            // The UTC clock of the instant, with its zone named: in these
            // zones the reader's own clock is a different one.
            const utcClock = `${created.slice(0, 10)} ${created.slice(11, 16)} UTC`;
            expect(await spokenText(cell)).toBe(utcClock);
        });

        test('a session a turn creates gets the server\'s rail row, in the reader\'s zone', async ({ adminPage: page }) => {
            const name = `Vane${uniqueStamp()}`;
            await page.goto('/tarmar/gm/');
            // A real turn against the fake Ollama; the session is made as it runs.
            await page.fill('#user-input', `We meet ${name} at the shrine`);
            await page.click('#send-btn');
            await expect(page.locator('.message.assistant').last()).toContainText('nothing stirs', { timeout: 30000 });

            const row = page.locator('#session-list .session-item').first();
            const sessionId = await row.getAttribute('data-id');
            const created = readDjangoJson(`
import json
from gm.models import GMSession
print('HELD', json.dumps(GMSession.objects.get(pk=${Number(sessionId)}).created_at.isoformat()))
`.trim(), 'HELD');
            const date = wordsFor(created, timeZone, 'date');

            await expect(row.locator('time.local-time')).toHaveAttribute('datetime', created);
            await expect(row.locator('time.local-time')).toHaveText(date);
            await expect(row.locator('.session-count')).toHaveText(/^\d+ msgs?$/);
            await expect(page.locator(`#session-delete-${sessionId}`)).toHaveAccessibleName(
                new RegExp(`^Delete session .+ started ${date}$`),
            );

            // The small-screen menu gets the session too, with the reader's date.
            await page.setViewportSize({ width: 375, height: 812 });
            const option = page.locator(`#mobile-session-select option[value="${sessionId}"]`);
            await expect(option).toHaveText(new RegExp(`^.+ \u2014 ${date} \\(\\d+\\)$`));
            await expect(option).toHaveJSProperty('selected', true);
            await page.setViewportSize({ width: 1280, height: 720 });

            // A later turn in the session adds no second row and no second
            // option, and the count is worded by the server.
            const before = await row.locator('.session-count').innerText();
            await page.fill('#user-input', 'We look around the shrine');
            await page.click('#send-btn');
            await expect(row.locator('.session-count')).not.toHaveText(before, { timeout: 30000 });
            await expect(row.locator('.session-count')).toHaveText(/^\d+ msgs$/);
            await expect(page.locator(`#session-list .session-item[data-id="${sessionId}"]`)).toHaveCount(1);
            await expect(page.locator(`#mobile-session-select option[value="${sessionId}"]`)).toHaveCount(1);
        });

        test('the session rail, the small-screen menu and the delete prompt say the reader\'s date, also after a search', async ({ page }) => {
            await loginAs(page, TEST_USER);
            const sessionName = uniqueName(`Zoned ${timeZone.replace('/', ' ')}`);
            const sessionId = readDjangoJson(`
import json
from django.contrib.auth import get_user_model
from gm.models import GMSession
user = get_user_model().objects.get(username=${JSON.stringify(TEST_USER.username)})
session = GMSession.objects.create(user=user, name=${JSON.stringify(sessionName)})
GMSession.objects.filter(pk=session.pk).update(created_at=${JSON.stringify(SESSION_STARTED)})
print('SESSION', json.dumps(session.pk))
`.trim(), 'SESSION');
            const date = wordsFor(SESSION_STARTED, timeZone, 'date');
            // The reader's date, and not the server's: the New York date is
            // the 14th, the UTC date the 15th.
            if (timeZone === 'America/New_York') expect(date).toContain('14');

            const railDate = page.locator(`#session-date-${sessionId}`);
            const menuOption = page.locator(`#mobile-session-select option[value="${sessionId}"]`);
            const deleteButton = page.locator(`#session-delete-${sessionId}`);

            async function expectMenuLabelLocal() {
                await expect(menuOption).toHaveText(`${sessionName} \u2014 ${date} (0)`);
            }
            async function expectEveryDateLocal() {
                await expectMenuLabelLocal();
                await expect(railDate.locator('time.local-time')).toHaveText(date);
                await expect(deleteButton).toHaveAccessibleName(
                    `Delete session ${sessionName} started ${date}`,
                );
            }

            await page.goto('/tarmar/gm/');
            await expectEveryDateLocal();

            // A search paints the rail again from the server's markup, which
            // is UTC until the page writes it.
            await searchPicker(page, '#session-rail-picker-search', sessionName);
            await expectEveryDateLocal();
            // So does a search of the small-screen menu, which is the one on
            // show once the window is that narrow.
            await page.setViewportSize({ width: 375, height: 812 });
            await searchPicker(page, '#session-menu-picker-search', sessionName);
            await expectMenuLabelLocal();
            await page.setViewportSize({ width: 1280, height: 720 });

            await deleteButton.click();
            await expect(page.locator('.notice-confirm .notice-text')).toContainText(
                `"${sessionName}", started ${date}? This can't be undone.`,
            );
            await page.locator('.notice-confirm .confirm-actions .button.secondary').click();
        });
    });
}
