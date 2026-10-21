/**
 * The turn panel keeps keyboard focus (#646), in a real browser.
 *
 * The action menu is the one form a seated player must use to take a turn,
 * and every selection empties the panel and rebuilds every button. The
 * button the person just activated left the document, so focus fell back to
 * <body>: a keyboard user had to tab forward through the whole regenerated
 * menu to reach "Take this action", and every further keystroke that
 * changed the selection put them back at the top.
 *
 * jsdom proves the focus bookkeeping; this proves the thing that matters to
 * a person — that the menu can be driven from the keyboard alone, with no
 * pointer at any step, from arriving at the panel to the turn committing.
 */

const {
    test, expect, createBattleViaSetup, escaped,
} = require('./fixtures');
const { uniqueName } = require('./unique-name');
const { menuWords, sentencePattern } = require('./turn-menu');

const focusKeyOfActiveElement = (page) =>
    page.evaluate(() => (document.activeElement || {}).dataset?.focusKey || null);

/** A pattern matching `text` and nothing longer, so "Duel 1" is not "Duel 10". */
const exactly = (text) => new RegExp(`^\\s*${escaped(text)}\\s*$`);

test.describe('Taking a turn from the keyboard', () => {
    test('a selection keeps focus, and the whole turn is reachable by Tab',
        async ({ adminPage }) => {
            test.setTimeout(120000);
            await createBattleViaSetup(adminPage, {
                name: uniqueName('E2E Turn focus'),
                fighters: 3,
                seed: 42,
                controlMode: 'manual',
            });

            await adminPage.click('#advance-turn');
            const panel = adminPage.locator('#turn-panel');
            await expect(panel).toContainText(new RegExp(sentencePattern(menuWords().panel.your_move)));

            // Reach the second option with the keyboard only, then activate
            // it the way a keyboard user does.
            await panel.locator('.turn-option').first().focus();
            await adminPage.keyboard.press('Tab');
            expect(await focusKeyOfActiveElement(adminPage)).toBe('option-1');
            await adminPage.keyboard.press('Enter');

            // The button that was activated has been replaced; its
            // replacement holds focus and shows the selection.
            expect(await focusKeyOfActiveElement(adminPage)).toBe('option-1');
            await expect(panel.locator('.turn-option').nth(1))
                .toHaveAttribute('aria-pressed', 'true');

            // Changing the selection keeps the reader where they were
            // rather than returning them to the top of the menu.
            await adminPage.keyboard.press('Shift+Tab');
            await adminPage.keyboard.press('Enter');
            expect(await focusKeyOfActiveElement(adminPage)).toBe('option-0');
            await adminPage.keyboard.press('Enter');
            expect(await focusKeyOfActiveElement(adminPage)).toBe('option-0');

            // Every figure in this battle is played by hand, so the turn
            // pauses again on each one in turn. Answer every pause from the
            // keyboard alone until the turn commits — no pointer touches
            // the menu at any point, which is what the bug made impossible:
            // the confirm button is a short walk from the option in hand
            // rather than a walk from <body> through the whole menu.
            const turnGroup = adminPage.locator('.turn-group[data-turn="1"]');
            const actPath = new URL(
                await panel.getAttribute('data-act-url'), adminPage.url()
            ).pathname;
            for (let pause = 0; pause < 6; pause += 1) {
                if (await turnGroup.count()) { break; }
                await expect(panel.locator('.turn-prompt')).toBeVisible();
                const awaited = (
                    await panel.locator('.turn-prompt strong').textContent()
                ).trim();
                await panel.locator('.turn-option').first().focus();
                // Select it, whichever way the last repaint left it.
                if (await panel.locator('.turn-confirm').isDisabled()) {
                    await adminPage.keyboard.press('Enter');
                }
                expect(await focusKeyOfActiveElement(adminPage)).toBe('option-0');
                await expect(panel.locator('.turn-confirm')).toBeEnabled();
                await panel.locator('.turn-confirm').focus();
                const answered = adminPage.waitForResponse((response) => (
                    response.request().method() === 'POST'
                    && new URL(response.url()).pathname === actPath
                ));
                await adminPage.keyboard.press('Enter');
                // Wait for the server's answer, then for the page to show
                // it: either the turn committed, or it paused on the next
                // figure. While the action is in flight the confirm button
                // is disabled, which is not the same thing.
                //
                // One locator, asked in one query, says which (#9). Reading
                // the prompt's count and then its text was two queries: an
                // answer that committed the turn in between emptied the
                // panel, and the text read then waited for a prompt that
                // never came back, for as long as the wait allowed.
                await answered;
                const nextFigure = panel.locator('.turn-prompt strong')
                    .filter({ hasNotText: exactly(awaited) });
                await expect(turnGroup.or(nextFigure).first()).toBeVisible();
            }

            await expect(turnGroup).toBeVisible();
            await expect(adminPage.locator('#battle-turn')).toHaveText('1');
        });
});
