/**
 * Axe over the pages the UI review found violations on (issues #696, #699,
 * #701, #707, #709, #715).
 *
 * The review measured the app with `@axe-core/playwright` and found 21
 * critical violations on the character editor alone, 2 more on the list, one
 * on the Games Master page, and a contrast failure in the header and footer of
 * every page tested. Every suite was green throughout, because nothing in the
 * suite could measure any of it. This spec is that measurement, so the count
 * cannot climb back without a red run.
 *
 * It asserts zero violations rather than a budget: the app is at zero on these
 * pages now, and a budget is a place to hide the next one. A page whose design
 * genuinely needs an exception should carry it as a named, reasoned disable
 * here rather than as a number.
 *
 * Run against the pre-fix markup these fail: the editor reports 16 `label`
 * and 5 `select-name`, the list 2 `label`, the Games Master page
 * `aria-required-children`, and every page `color-contrast` plus
 * `link-in-text-block` on the footer.
 */

const { refusal } = require('./refusals');
const AxeBuilder = require('@axe-core/playwright').default;
const {
    test, expect, loginAs, createNamedCharacter, TEST_USER, TEST_ADMIN,
    TEST_WATCHER, postAsGameMaster, characterIdFromUrl, createBattleViaSetup,
} = require('./fixtures');
const { readDjangoShell } = require('./django');

//: The rule sets the review used, and the ones WCAG 2.1 AA is made of.
const WCAG_AA = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

//: The three widths the review walked, and the three this must hold at.
const VIEWPORTS = [
    { name: 'desktop', width: 1440, height: 900 },
    { name: 'laptop', width: 1024, height: 768 },
    { name: 'phone', width: 390, height: 844 },
];

/**
 * Every wcag2a/aa + wcag21a/aa violation on the page as it stands.
 * @param {import('@playwright/test').Page} page
 * @returns {Promise<Array>} axe's violations, worst first as axe orders them.
 */
async function violations(page) {
    const results = await new AxeBuilder({ page }).withTags(WCAG_AA).analyze();
    return results.violations;
}

/**
 * A failure message that names what broke rather than only a count.
 * @param {Array} found - axe violations.
 * @returns {string}
 */
function describe(found) {
    return found
        .map((violation) => {
            const where = violation.nodes
                .map((node) => node.target.join(' '))
                .slice(0, 4)
                .join(', ');
            return `${violation.id} [${violation.impact}] x${violation.nodes.length}: ${where}`;
        })
        .join('\n');
}

test.describe('the character sheet is readable by assistive technology', () => {
    test('the editor has no axe violations at any of the three widths', async ({ page }, testInfo) => {
        test.setTimeout(120000);
        const name = `Axe Editor ${testInfo.workerIndex}${Date.now()}`;
        await loginAs(page, TEST_USER);
        await createNamedCharacter(page, name);
        const editorUrl = page.url();

        for (const viewport of VIEWPORTS) {
            await page.setViewportSize({ width: viewport.width, height: viewport.height });
            await page.goto(editorUrl);
            const found = await violations(page);
            expect(found, `${viewport.name}:\n${describe(found)}`).toEqual([]);
        }
    });

    test('every attribute box and select carries its own name', async ({ page }, testInfo) => {
        const name = `Axe Named ${testInfo.workerIndex}${Date.now()}`;
        // A Game Master's sheet, where every one of these is a control; a
        // player's attributes, provenance and wealth are text (issue #748).
        await loginAs(page, TEST_ADMIN);
        await createNamedCharacter(page, name);

        // The six core attributes took their heading from a sibling table
        // cell, so a screen reader read "edit, 12" six times over (#696).
        for (const [field, label] of [
            ['strength', 'Strength'],
            ['charisma', 'Charisma'],
            ['provenance_class', 'Social Class'],
            ['location', 'Origin'],
            ['wealth_level', 'Wealth Level'],
        ]) {
            const control = page.locator(`.editable-field[data-field="${field}"] input, .editable-field[data-field="${field}"] select`);
            await expect(control, field).toHaveAttribute('aria-label', label);
        }
    });

    test('the character list has no axe violations with a row on it', async ({ page }, testInfo) => {
        const name = `Axe List ${testInfo.workerIndex}${Date.now()}`;
        await loginAs(page, TEST_USER);
        await createNamedCharacter(page, name);
        await page.goto('/tarmar/characters/');

        const found = await violations(page);

        expect(found, describe(found)).toEqual([]);
    });
});

test.describe('the chrome and the Games Master page', () => {
    test('the signed-in chrome passes contrast on a plain page', async ({ page }) => {
        // .identity-since was #999 on white at 11.2px (2.84:1) and the
        // footer's links were #666 inside #888 with no underline (1.61:1),
        // on every page in the app (#709).
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/');

        const found = await violations(page);

        expect(found, describe(found)).toEqual([]);
    });

    test('the Games Master page has no axe violations', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/gm/');

        const found = await violations(page);

        expect(found, describe(found)).toEqual([]);
    });

    test('the panel tab strip fits its own controls', async ({ page }) => {
        // Four groups sharing 311px with flex: 1 gave each 78px, so the "+"
        // for Campaign was drawn over the final "n" and the "−" belonging to
        // Characters was not drawn at all (#699).
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/gm/');
        await page.waitForSelector('.gm-panel-tab-group');

        const clipped = await page.evaluate(() => {
            const groups = [...document.querySelectorAll('.gm-panel-tab-group')];
            return groups
                .map((group) => {
                    const tab = group.querySelector('.gm-panel-tab-button');
                    const split = group.querySelector('.gm-panel-split');
                    if (!tab || !split) return null;
                    const faults = [];
                    // The label ran under the split button: the "+" for
                    // Campaign was drawn on top of the final "n".
                    if (tab.scrollWidth > tab.clientWidth + 1) {
                        faults.push(`label clipped by ${Math.round(tab.scrollWidth - tab.clientWidth)}px`);
                    }
                    // And the "−" belonging to Characters was not drawn at
                    // all, because the group had no room left for it.
                    if (split.getBoundingClientRect().width < 1) {
                        faults.push('split control has no width');
                    }
                    return faults.length
                        ? `${tab.textContent.trim()}: ${faults.join(', ')}`
                        : null;
                })
                .filter(Boolean);
        });

        expect(clipped).toEqual([]);
    });

    test('the bestiary passes axe for the DM who can open it', async ({ page }) => {
        await loginAs(page, TEST_ADMIN);
        await page.goto('/tarmar/gm/bestiary/');

        const found = await violations(page);

        expect(found, describe(found)).toEqual([]);
    });
});

//: One session, so the chat header renders its rename field (#737).
const SEED_SESSION_FOR_FOCUS = `
from django.contrib.auth import get_user_model
from gm.models import GMSession
user = get_user_model().objects.get(username='${TEST_USER.username}')
session = GMSession.objects.create(user=user, name='Focus Ring Session')
print('SESSION', session.pk)
`.trim().replace(/\n/g, '; ');

/**
 * The focus indicator a control renders when a keyboard user reaches it.
 *
 * The Tab press is what makes the focus a keyboard focus: Chromium matches
 * `:focus-visible` on a scripted focus when the last interaction was a key,
 * and does not when it was a click. Tabbing all the way to a control on the
 * editor would take fifty presses and would say nothing extra.
 *
 * @param {import('@playwright/test').Page} page
 * @param {string} selector - the control to focus.
 * @returns {Promise<{focusVisible: boolean, outline: number, shadow: string}>}
 *     whether `:focus-visible` matched, the outline's width in px (0 when the
 *     style is `none`), and the computed box-shadow.
 */
async function keyboardFocusIndicator(page, selector) {
    await page.keyboard.press('Tab');
    await page.locator(selector).first().focus();
    const measured = await page.evaluate((target) => {
        const element = document.activeElement;
        const style = getComputedStyle(element);
        return {
            isTarget: element.matches(target),
            focusVisible: element.matches(':focus-visible'),
            outline: style.outlineStyle === 'none' ? 0 : parseFloat(style.outlineWidth),
            shadow: style.boxShadow,
        };
    }, selector);
    expect(measured.isTarget, `${selector} did not take focus`).toBe(true);
    return measured;
}

/**
 * Assert a control shows a keyboard user where the focus is (#701, #737).
 *
 * Either an outline with width, or a box-shadow ring for the one control that
 * draws its own. A border tint is not an indicator: that is the swap #701
 * rejected sitewide, and it is what every rule this covers substituted.
 *
 * @param {import('@playwright/test').Page} page
 * @param {string} selector - the control to focus.
 */
async function expectFocusVisible(page, selector) {
    const measured = await keyboardFocusIndicator(page, selector);
    expect(measured.focusVisible, `${selector} does not match :focus-visible`).toBe(true);
    expect(
        measured.outline > 0 || (measured.shadow && measured.shadow !== 'none'),
        `${selector}: outline ${measured.outline}px, box-shadow ${measured.shadow}`
    ).toBe(true);
}

/*
 * #737: seven `outline: none` declarations outlived the sitewide rule #701
 * installed -- two in chat.css, one of them written into the `:focus-visible`
 * rule itself so the ring was removed from exactly the state it exists for,
 * and five in character-edit.css that had never been read. Six are gone; the
 * seventh stays because the skill name draws its own teal ring.
 *
 * Axe does not measure focus visibility, so the zero-violation tests above
 * were green throughout. These press a key and then read the computed style,
 * which is the only thing that can tell a ring from a border tint.
 */
test.describe('a keyboard user can see where the focus is', () => {
    test('the Games Master page shows the ring on the controls that suppressed it', async ({ page }) => {
        test.setTimeout(120000);
        // The rename field is only rendered for a session that exists, so the
        // page is opened on a seeded one.
        const seeded = readDjangoShell(SEED_SESSION_FOR_FOCUS).match(/SESSION (\d+)/);
        expect(seeded).not.toBeNull();
        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/gm/?session=${seeded[1]}`);

        // The choice buttons appear when a GM reply ends in a numbered list
        // (#342). The stream is stubbed, as e2e/gm-choices.spec.js stubs it,
        // because the e2e run has no Ollama.
        const reply = 'The gate is barred.\n\n1. Knock\n2. Climb the wall';
        await page.route('**/tarmar/gm/stream/', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'text/event-stream',
                body: [
                    `data: ${JSON.stringify({ token: reply })}`,
                    '',
                    `data: ${JSON.stringify({ done: true, full_response: reply })}`,
                    '',
                    '',
                ].join('\n'),
            });
        });
        await page.fill('#user-input', 'I approach the gate.');
        await page.click('#send-btn');
        await expect(page.locator('.choice-btn').first()).toBeVisible();

        // A choice button is a <button>, which base.css's
        // input/select/textarea rule does not reach, so chat.css gives it the
        // same gold ring by name.
        await expectFocusVisible(page, '.choice-btn');

        // The session rename field in the chat header: an <input>, so
        // base.css's sitewide rule paints it once chat.css stops suppressing.
        await page.locator('.session-name-input').waitFor({ state: 'visible' });
        await expectFocusVisible(page, '.session-name-input');
    });

    test('the character editor shows the ring on the fields that suppressed it', async ({ page, browser }, testInfo) => {
        test.setTimeout(120000);
        // A Game Master's sheet, which carries every kind of field (#748).
        await loginAs(page, TEST_ADMIN);
        await createNamedCharacter(page, `Axe Focus ${testInfo.workerIndex}${Date.now()}`);

        for (const selector of [
            '.editable-field input',
            '.editable-field select',
            '[data-field="total_xp"] input',
        ]) {
            await expectFocusVisible(page, selector);
        }

        // The equipment row's notes field is drawn by character-edit.js once
        // there is something carried, so it needs a row first. Gear is bought
        // now (issue #757) and a rolled Rich character has no coin, so a Game
        // Master gives one first.
        const given = await postAsGameMaster(
            browser, `/tarmar/characters/${characterIdFromUrl(page)}/coin/`, { amount: 1 }
        );
        expect(given.status).toBe(200);
        await page.selectOption('#equipment-category', 'gear');
        await page.selectOption('#equipment-item', 'candle');
        await page.locator('#add-equipment-btn').click();
        await expect(page.locator('.equipment-row')).toHaveCount(1);

        await expectFocusVisible(page, '.equipment-row .item-notes input');
    });
});

test.describe('the way from signing in to the journal (issues #786, #827, #828, #924, #952)', () => {
    test('the journal page and its Play buttons pass at every width', async ({ page }, testInfo) => {
        test.setTimeout(120000);
        await loginAs(page, TEST_USER);
        await createNamedCharacter(page, `Axe Home ${testInfo.workerIndex}${Date.now()}`);

        for (const viewport of VIEWPORTS) {
            await page.setViewportSize({ width: viewport.width, height: viewport.height });
            await page.goto('/tarmar/journal/');
            await expect(page.locator('#journal .play-with-gm').first()).toBeVisible();
            const found = await violations(page);
            expect(found, `${viewport.name}:\n${describe(found)}`).toEqual([]);
        }
    });

    test('the empty journal passes too', async ({ page }) => {
        await loginAs(page, TEST_WATCHER);
        await page.goto('/tarmar/journal/');
        await expect(page.locator('#journal-first-character')).toBeVisible();

        const found = await violations(page);

        expect(found, describe(found)).toEqual([]);
    });

    for (const [role, account] of [['a player', TEST_USER], ['a Game Master', TEST_ADMIN]]) {
        test(`the creation page and its name fields pass at every width for ${role}`, async ({ page }) => {
            test.setTimeout(120000);
            await loginAs(page, account);
            for (const viewport of VIEWPORTS) {
                await page.setViewportSize({ width: viewport.width, height: viewport.height });
                await page.goto('/tarmar/characters/create/');
                await expect(page.locator('#roll-name')).toBeVisible();
                const found = await violations(page);
                expect(found, `${viewport.name}:\n${describe(found)}`).toEqual([]);
            }
        });
    }

    test('a refused name on the creation page passes', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/characters/create/');
        await page.fill('#roll-name', 'Wren\u{1F525}');
        await page.click('button:has-text("Roll a New Character")');
        await expect(page.locator('#point-buy-server-error')).toBeVisible();

        const found = await violations(page);

        expect(found, describe(found)).toEqual([]);
    });

    test("the sheet's Play with the Game Master control passes at every width", async ({ page }, testInfo) => {
        test.setTimeout(120000);
        await loginAs(page, TEST_USER);
        await createNamedCharacter(page, `Axe Play ${testInfo.workerIndex}${Date.now()}`);
        const sheetUrl = page.url();

        for (const viewport of VIEWPORTS) {
            await page.setViewportSize({ width: viewport.width, height: viewport.height });
            await page.goto(sheetUrl);
            await expect(page.locator('form.play-with-gm')).toBeVisible();
            const found = await new AxeBuilder({ page })
                .withTags(WCAG_AA)
                .include('form.play-with-gm')
                .analyze();
            expect(found.violations, `${viewport.name}:\n${describe(found.violations)}`).toEqual([]);
        }
    });
});

test.describe('Battle roster — a downed figure and the notes on its card (issue #16)', () => {
    /**
     * The roster's contrast, checked by axe, and what it measured for each
     * piece of text on the downed card, kept as annotations on the test.
     */
    async function rosterContrast(page, testInfo, step) {
        const results = await new AxeBuilder({ page })
            .include('#battle-roster')
            .withRules(['color-contrast'])
            .analyze();
        const unmeasured = (results.incomplete.find((rule) => rule.id === 'color-contrast') || { nodes: [] }).nodes;
        unmeasured.forEach((node) => {
            testInfo.annotations.push({
                type: `contrast ${step} unmeasured`,
                description: `${node.target.join(' ')} ${(node.any[0] && node.any[0].message) || ''}`,
            });
        });
        const measured = (results.passes.find((rule) => rule.id === 'color-contrast') || { nodes: [] }).nodes;
        measured.forEach((node) => {
            const data = node.any[0] && node.any[0].data;
            testInfo.annotations.push({
                type: `contrast ${step}`,
                description: `${node.target.join(' ')} ${data ? `${data.contrastRatio}:1 (${data.fgColor} on ${data.bgColor})` : ''}`,
            });
        });
        return results.violations;
    }

    test('passes color-contrast with a "renamed meanwhile" note and a refusal on it', async ({ page }, testInfo) => {
        await loginAs(page, TEST_ADMIN);
        // A battle awaiting the DM's move, so his page polls the state.
        await createBattleViaSetup(page, {
            name: `E2E Down contrast ${Date.now()}`, fighters: 3, seed: 7, controlMode: 'manual',
        });
        const battleId = new URL(page.url()).pathname.match(/battles\/(\d+)\//)[1];
        await page.click('#advance-turn');
        const awaited = (await page.locator('#turn-panel .turn-prompt strong').textContent()).trim();

        // One figure the turn is not waiting on goes down, on the record.
        const cards = page.locator('#battle-roster [data-combatant]');
        const downedCard = cards.filter({ hasNotText: awaited }).first();
        const downedId = await downedCard.getAttribute('data-combatant');
        const otherName = (await page
            .locator(`#battle-roster [data-combatant]:not([data-combatant="${downedId}"]) .combatant-name`)
            .first().textContent()).trim();
        readDjangoShell(`
from battle import profiles
from battle.models import Battle
from battle.services import dump_state
battle = Battle.objects.get(pk=${battleId})
state = profiles.for_battle(battle).load(battle.state_json)
figure = next(each for each in state.combatants if each.combatant_id == ${downedId})
figure.conscious = False
battle.state_json = dump_state(state)
battle.save()
print('DOWN')
`);
        await page.reload();
        const down = page.locator(`#battle-roster [data-combatant="${downedId}"]`);
        await expect(down).toHaveClass(/is-down/);

        // A rename begun on it, and the figure renamed meanwhile elsewhere:
        // the next poll redraws the roster with the note under the field.
        await down.locator('.combatant-name').click();
        await page.locator('.combatant-rename-input').fill('Down and out');
        await down.evaluate((node) => { node.dataset.beforePoll = 'yes'; });
        readDjangoShell(`
from battle.services import rename_combatant, set_battle_control_mode
rename_combatant(${battleId}, ${downedId}, 'Renamed Elsewhere')
set_battle_control_mode(${battleId}, 'manual')
print('DONE')
`);
        await expect(page.locator('#battle-roster [data-before-poll]')).toHaveCount(0);
        await expect(down.locator('.rename-changed-note')).toBeVisible();
        const withChangedNote = await rosterContrast(page, testInfo, 'renamed-meanwhile');
        expect(withChangedNote, describe(withChangedNote)).toEqual([]);

        // Refused: the name another figure already has.
        await page.locator('.combatant-rename-input').fill(otherName);
        await page.keyboard.press('Enter');
        await expect(down.locator('.roster-rename-note')).toContainText(
            refusal('battle', 'services', 'name_taken')
        );
        const withRefusal = await rosterContrast(page, testInfo, 'refusal');
        expect(withRefusal, describe(withRefusal)).toEqual([]);
    });
});
