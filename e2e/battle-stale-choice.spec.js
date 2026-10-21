/**
 * A turn choice belongs to one figure on one turn (#644), in a real browser.
 *
 * The bug this pins: the submission carried only the option, so the server
 * read *which* combatant it was for out of its own pending record. A body
 * composed while the battle awaited A and arriving after the pause had moved
 * to B was recorded as B's move whenever the option was legal for B too —
 * which the generic options (DODGE, DEFEND, DISENGAGE) nearly always are.
 *
 * Winning that race by hand in a browser would be a coin flip, so the stale
 * body is produced deliberately: the page's own fetch is wrapped and the
 * outgoing `combatant_id` swapped for another figure in the same battle.
 * Everything after that is the real thing — the real endpoint, the real
 * refusal, and the page's own error path — which is the point, because what
 * this checks is that the refusal reaches the reader instead of the battle
 * quietly recording somebody else's move.
 */

const { test, expect, createBattleViaSetup } = require('./fixtures');
const { menuWords, sentencePattern } = require('./turn-menu');

test.describe('A turn choice names the pause it was made for', () => {
    test('a submission for another figure is refused and the arena says so',
        async ({ adminPage }) => {
            await createBattleViaSetup(adminPage, {
                name: `E2E Stale choice ${Date.now()}`,
                fighters: 3,
                seed: 7,
                controlMode: 'manual',
            });

            await adminPage.click('#advance-turn');
            const panel = adminPage.locator('#turn-panel');
            await expect(panel).toContainText(new RegExp(sentencePattern(menuWords().panel.your_move)));

            // Another figure in this battle — a combatant id the server can
            // resolve to a name, so the refusal reads as the reader's own
            // roster rather than as a number.
            const awaitedName = (await panel.locator('.turn-prompt strong')
                .textContent()).trim();
            const otherId = await adminPage.evaluate((awaited) => {
                const cards = Array.from(
                    document.querySelectorAll('#battle-roster [data-combatant]')
                );
                const other = cards.find(
                    (card) => !card.textContent.includes(awaited)
                );
                return other ? Number(other.dataset.combatant) : null;
            }, awaitedName);
            expect(otherId).not.toBeNull();

            // Rewrite the outgoing choice so it names that other figure —
            // the shape a second tab or a lagging poll produces on its own.
            await adminPage.evaluate((id) => {
                const realFetch = window.fetch.bind(window);
                window.fetch = (url, options) => {
                    if (options && options.method === 'POST'
                            && String(url).includes('/act/')) {
                        const body = JSON.parse(options.body);
                        body.combatant_id = id;
                        return realFetch(url, Object.assign({}, options, {
                            body: JSON.stringify(body),
                        }));
                    }
                    return realFetch(url, options);
                };
            }, otherId);

            await panel.locator('.turn-option').first().click();
            await panel.locator('.turn-confirm').click();

            // The reason stands on the page until it is dismissed — a
            // notice, not a flash (the project's no-transient-UI rule).
            const notice = adminPage.locator('#notice-region .notice-error');
            await expect(notice).toContainText('the battle is waiting on');
            await expect(notice).toContainText(awaitedName);
            await expect(notice.locator('.notice-dismiss')).toBeVisible();

            // And nothing was recorded: the battle is still on the same
            // pause, offering the same person the same menu.
            await expect(adminPage.locator('#battle-status'))
                .toHaveText('Awaiting players');
            await expect(adminPage.locator('#battle-turn')).toHaveText('0');
            await expect(panel).toContainText(awaitedName);
            await expect(adminPage.locator('#advance-turn'))
                .toHaveText(`Waiting on ${awaitedName}'s move`);
        });
});
