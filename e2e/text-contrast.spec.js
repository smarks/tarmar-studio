/**
 * The two emphasis colours, measured where a browser actually paints them
 * (issue #730).
 *
 * `common/css_contrast.py` reads the sheets and models the cascade; it found
 * these eighteen pairings, and the unit suite holds them. What it cannot do is
 * resolve a real ancestor: it takes `.setup-stage-numeral` for a top-level
 * selector with nothing painted behind it, and it never sees that an equipped
 * item's label stands inside a row inside a panel. So the numbers it reports
 * are a model of the page, not the page.
 *
 * This measures the page: the computed text colour of a rendered element
 * against the nearest ancestor that actually paints a ground, at the three
 * widths the UI review walked. Against the old values it fails -- the gold at
 * 3.41:1, the green at 3.30:1 -- which is the point of having it.
 *
 * Axe covers the same ground for a page as a whole (`e2e/accessibility.spec.js`)
 * but only for text it can see: an equipped item and a muster index need a
 * character with gear and a battle with fighters, so neither appears on any
 * page axe visits there.
 */

const {
    test, expect, loginAs, createNamedCharacter, TEST_USER, TEST_ADMIN,
} = require('./fixtures');
const { readDjangoShell } = require('./django');

//: WCAG 2.x AA for body text, the threshold common/css_contrast.py holds to.
const AA_NORMAL_TEXT = 4.5;

//: The three widths the UI review walked (#716), as accessibility.spec.js uses.
const VIEWPORTS = [
    { name: 'desktop', width: 1440, height: 900 },
    { name: 'laptop', width: 1024, height: 768 },
    { name: 'phone', width: 390, height: 844 },
];

/**
 * The rendered contrast of the first visible match for `selector`.
 *
 * The ground is the nearest ancestor painting a non-transparent background,
 * which is what a browser composites the text onto.
 *
 * @param {import('@playwright/test').Page} page
 * @param {string} selector - a CSS selector for the text to measure. A
 *     "::placeholder" suffix measures that pseudo-element's colour instead of
 *     the element's own, which is the only way to read a placeholder: it has
 *     no node of its own to query (issue #732).
 * @returns {Promise<{ratio: number, colour: string, ground: string}>}
 */
async function renderedContrast(page, selector) {
    return page.evaluate((selectorWithPseudo) => {
        const [target, pseudo] = selectorWithPseudo.split('::');
        const channels = (value) => value.match(/[\d.]+/g).slice(0, 3).map(Number);
        const luminance = (rgb) => {
            const linear = rgb.map((channel) => {
                const fraction = channel / 255;
                return fraction <= 0.03928
                    ? fraction / 12.92
                    : ((fraction + 0.055) / 1.055) ** 2.4;
            });
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
        };
        const element = [...document.querySelectorAll(target)]
            .find((candidate) => candidate.getClientRects().length > 0);
        if (!element) {
            throw new Error(`nothing visible matches ${target}`);
        }
        const alphaOf = (value) => (
            value.startsWith('rgba') ? Number(value.match(/[\d.]+/g)[3]) : 1
        );
        const colour = getComputedStyle(element, pseudo ? `::${pseudo}` : null).color;
        let ground = 'rgb(255, 255, 255)';
        for (let node = element; node; node = node.parentElement) {
            const painted = getComputedStyle(node).backgroundColor;
            if (alphaOf(painted) > 0) {
                ground = painted;
                break;
            }
        }
        // Translucent text is composited onto its ground before it is read,
        // and a browser's own placeholder colour is translucent. Taking its
        // channels as opaque measured rgba(0, 0, 0, 0.54) as pure black --
        // 21:1, a pass for text that renders mid-grey (issue #732).
        const over = (foreground, behind) => {
            const alpha = alphaOf(foreground);
            const front = channels(foreground);
            const back = channels(behind);
            return front.map((value, index) => alpha * value + (1 - alpha) * back[index]);
        };
        const lighter = Math.max(luminance(over(colour, ground)), luminance(channels(ground)));
        const darker = Math.min(luminance(over(colour, ground)), luminance(channels(ground)));
        return { ratio: (lighter + 0.05) / (darker + 0.05), colour, ground };
    }, selector);
}

/**
 * The visible element a selector names, with any "::pseudo" suffix dropped.
 *
 * @param {string} selector - as passed to {@link renderedContrast}.
 * @returns {string} the selector a Playwright locator can wait on.
 */
function elementOf(selector) {
    return selector.split('::')[0];
}

/**
 * Assert every selector clears AA on the page as it stands, at all three widths.
 *
 * @param {import('@playwright/test').Page} page
 * @param {string} url - the page to re-open at each width.
 * @param {string[]} selectors - the text to measure.
 * @param {(page: import('@playwright/test').Page) => Promise<void>} [prepare] -
 *     run after each load, for text that only exists once something is opened.
 */
async function expectReadableAtEveryWidth(page, url, selectors, prepare) {
    for (const viewport of VIEWPORTS) {
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await page.goto(url);
        if (prepare) {
            await prepare(page);
        }
        for (const selector of selectors) {
            // An equipment row is drawn by character-edit.js after the load,
            // so the measurement waits for the text rather than racing it.
            await page.locator(elementOf(selector)).first().waitFor({ state: 'visible' });
            const measured = await renderedContrast(page, selector);
            expect(
                measured.ratio,
                `${viewport.name} ${selector}: ${measured.colour} on ${measured.ground}`
            ).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
        }
    }
}

test('the gold the battle pages write in reads on the ground behind it', async ({ page }, testInfo) => {
    test.setTimeout(120000);
    // .setup-stage-numeral, .muster-index and .control-default were
    // --colour-accent-gold-strong (#a8871f), 3.41:1 on white: a gold picked to
    // fill a chip, printed as text.
    await loginAs(page, TEST_USER);
    await page.goto('/tarmar/battles/');
    await page.click('.battle-list-masthead button[type="submit"]');
    await page.waitForURL(/\/tarmar\/battles\/setup\/\d+\/$/);
    const setupUrl = page.url();
    // A muster index needs somebody mustered, and the battle will not begin
    // with one fighter, so the setup page is measured with two on it.
    for (const archetype of ['armoured fighter', 'archer']) {
        await page.click(`.setup-fighter-grid form:has(input[value="${archetype}"]) button`);
        await page.waitForURL(/\/setup\/\d+\/$/);
    }

    await expectReadableAtEveryWidth(page, setupUrl, ['.setup-stage-numeral', '.muster-index']);

    await page.fill('input[name="name"]', `Contrast Gold ${testInfo.workerIndex}${Date.now()}`);
    await page.fill('input[name="seed"]', '7');
    await page.click('.setup-begin');
    await page.waitForURL(/\/tarmar\/battles\/\d+\/$/);

    await expectReadableAtEveryWidth(page, page.url(), ['.control-default']);
});

test('the green an equipped item is marked with reads on the row behind it', async ({ page }, testInfo) => {
    test.setTimeout(120000);
    // .equipment-row .item-equipped was --colour-sheet-positive (#16a34a),
    // 3.30:1 on the white row.
    // A Game Master, who can give the armour: gear is bought now (issue
    // #757), and a rolled purse can be too small for any armour.
    await loginAs(page, TEST_ADMIN);
    await createNamedCharacter(page, `Contrast Green ${testInfo.workerIndex}${Date.now()}`);
    const editorUrl = page.url();
    // Armour, because the equipped marker is what the green is printed on.
    await page.selectOption('#equipment-category', 'armor');
    await page.selectOption('#equipment-item', 'leather_armor');
    await page.locator('#add-free').check();
    await page.locator('#add-equipment-btn').click();
    await expect(page.locator('.equipment-row')).toHaveCount(1);

    await expectReadableAtEveryWidth(page, editorUrl, ['.equipment-row .item-equipped']);
});

/*
 * The three pieces of live text #732 settled, measured the same way. Each was
 * a decision about a background rather than about a text colour, which is why
 * #730 left them: white on the teal fill at 3.62:1 in two places, and the
 * chat box's placeholder grey at 2.85:1.
 */

test('white on the teal fill reads, on an open help summary', async ({ page }, testInfo) => {
    test.setTimeout(120000);
    // --colour-accent-teal was #4a90a2: 3.62:1 under --colour-surface, which
    // is the 3:1 WCAG allows for large text, and neither place that carries
    // it is large. This one is the disclosure's '?' at 12px bold.
    //
    // The other place is the pager's current-page chip, measured by the test
    // below on the bestiary catalog. It could not be measured anywhere until
    // #735 moved the pager's rules into base.css: the fill was written in
    // character-list.css, so the chip only painted on the character list,
    // which needs fifty-one characters to show a second page -- past what a
    // spec can seed against the import limiter.
    await loginAs(page, TEST_USER);
    await createNamedCharacter(page, `Contrast Teal ${testInfo.workerIndex}${Date.now()}`);

    await expectReadableAtEveryWidth(
        page,
        page.url(),
        ['details.help[open] > summary'],
        async (opened) => {
            await opened.locator('details.help > summary').first().click();
            await expect(opened.locator('details.help[open]').first()).toBeVisible();
        }
    );
});

test('the pager chip is a filled chip on a list other than the character list', async ({ page }) => {
    test.setTimeout(120000);
    // #735: `.pagination .page-current`'s fill was written in
    // character-list.css, which only the character list links, so the five
    // other templates that include `templates/_pagination.html` drew the chip
    // as bare text. Measured on the bestiary catalog, which pages at 24 and
    // therefore needs nothing seeded.
    //
    // The fill is asserted before the ratio, and it is what makes this test
    // fail against the old sheets: an unpainted chip is the page's own ink on
    // the page's own ground, which reads at 12.6:1. A contrast check alone
    // would have called the defect a pass.
    await loginAs(page, TEST_ADMIN);
    await page.goto('/tarmar/gm/bestiary/');
    await expect(page.locator('.pagination .page-current').first()).toBeVisible();
    const teal = asRgb(await tokenValue(page, '--colour-accent-teal'));

    for (const viewport of VIEWPORTS) {
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await page.goto('/tarmar/gm/bestiary/');
        await page.locator('.pagination .page-current').first().waitFor({ state: 'visible' });
        const measured = await renderedContrast(page, '.pagination .page-current');
        expect(measured.ground, `${viewport.name}: the chip paints no fill of its own`)
            .toBe(teal);
        expect(
            measured.ratio,
            `${viewport.name}: ${measured.colour} on ${measured.ground}`
        ).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
    }
});

//: One session in the sidebar, so the row and its delete button render.
const SEED_SESSION_FOR_CONTRAST = `
from django.contrib.auth import get_user_model
from gm.models import GMSession
user = get_user_model().objects.get(username='${TEST_USER.username}')
session = GMSession.objects.create(user=user, name='Contrast Delete Button')
print('SESSION', session.pk)
`.trim().replace(/\n/g, '; ');

test('the sidebar delete button reads at rest, not only under the pointer', async ({ page }) => {
    test.setTimeout(120000);
    // #736: the button paints no ground at rest, so its :hover ground was the
    // only one the sheet audit had on file and the 4.77:1 of the hover was the
    // only pairing reported. The resting colour was --colour-ink-disabled on
    // the sidebar's own ground: 2.71:1, on a control that deletes a session.
    //
    // Measured with no pointer over it, which is the state a reader reads it
    // in. Desktop and laptop only: the sidebar is display:none below 768px,
    // and a hidden element has no rendered colour to measure.
    expect(readDjangoShell(SEED_SESSION_FOR_CONTRAST)).toMatch(/SESSION \d+/);
    await loginAs(page, TEST_USER);

    for (const viewport of VIEWPORTS.filter((each) => each.width >= 768)) {
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await page.goto('/tarmar/gm/');
        await page.locator('.session-delete-btn').first().waitFor({ state: 'visible' });
        const measured = await renderedContrast(page, '.session-delete-btn');
        expect(
            measured.ratio,
            `${viewport.name}: ${measured.colour} on ${measured.ground}`
        ).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
    }
});

/**
 * The value a colour token resolves to on the page, as the browser sees it.
 *
 * @param {import('@playwright/test').Page} page
 * @param {string} token - a custom property name, e.g. "--colour-ink".
 * @returns {Promise<string>} the declared value, trimmed.
 */
async function tokenValue(page, token) {
    const value = await page.evaluate(
        (name) => getComputedStyle(document.documentElement)
            .getPropertyValue(name).trim(),
        token
    );
    expect(value, `base.css defines no ${token}`).toMatch(/^#[0-9a-f]{6}$/i);
    return value;
}

/**
 * The computed colour of a selector's pseudo-element, as a browser string.
 *
 * @param {import('@playwright/test').Page} page
 * @param {string} selector - "<element>::<pseudo>".
 * @returns {Promise<string>} e.g. "rgb(118, 118, 118)".
 */
async function pseudoColour(page, selector) {
    return page.evaluate((selectorWithPseudo) => {
        const [target, pseudo] = selectorWithPseudo.split('::');
        return getComputedStyle(document.querySelector(target), `::${pseudo}`).color;
    }, selector);
}

/**
 * A "#rrggbb" value as the "rgb(r, g, b)" string getComputedStyle returns.
 *
 * @param {string} hex - "#rrggbb".
 * @returns {string}
 */
function asRgb(hex) {
    const digits = hex.replace('#', '');
    const channel = (index) => parseInt(digits.slice(index * 2, index * 2 + 2), 16);
    return `rgb(${channel(0)}, ${channel(1)}, ${channel(2)})`;
}

test('the chat box placeholder reads on the field it sits in', async ({ page }) => {
    // Two defects in one line. The colour was --colour-ink-disabled (#999),
    // 2.85:1 on the field's white ground; and the rule said `.chat-input
    // input` while the box has been a textarea since #716, so it reached
    // nothing and the field took the browser's own colours. Only the browser
    // can tell those apart -- the sheet audit reported #999, a colour nothing
    // ever painted -- so the sheet reaching the field is asserted first and
    // the ratio second. Chromium's own placeholder happens to clear AA, so
    // the ratio alone would have passed over a rule that applied to nothing.
    await loginAs(page, TEST_USER);
    await page.goto('/tarmar/gm/');
    await page.locator('#user-input').waitFor({ state: 'visible' });

    expect(await pseudoColour(page, '#user-input::placeholder'))
        .toBe(asRgb(await tokenValue(page, '--colour-ink-placeholder')));

    // The rule that reaches the field must not bring `outline: none` with it:
    // that is the declaration #701 took out of base.css, and this is the page
    // that complaint named. The box is autofocused, so the ring is on it.
    const focusRing = await page.evaluate(() => {
        const style = getComputedStyle(document.querySelector('#user-input'));
        return { style: style.outlineStyle, width: style.outlineWidth };
    });
    expect(focusRing.style).not.toBe('none');
    expect(parseFloat(focusRing.width)).toBeGreaterThan(0);

    await expectReadableAtEveryWidth(page, '/tarmar/gm/', ['#user-input::placeholder']);
});
