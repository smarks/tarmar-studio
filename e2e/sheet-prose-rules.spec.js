// @ts-check
/**
 * The sheet's sentences carry the loaded canon's rules numbers (issue #68).
 *
 * One test per surface: the creation page, the sheet's controls and formula
 * cells, its help, the workbench's aging note (server-rendered, then
 * repainted by the script), and the Games Master's Characters pane. Every
 * number is read from the loaded rules through the Django shell, never typed,
 * so the specs hold under the default canon and the Tarmar canon alike. The
 * refusals' words are held to the same numbers in
 * character-rolled-values.spec.js.
 */

const {
    test, expect, loginAs, createCharacter, TEST_USER,
} = require('./fixtures');
const { runDjangoShell, readDjangoShell } = require('./django');
const { loadedCharacterRules } = require('./character-rules');
const { loadedSkillRules } = require('./skill-rules');

/** The character id from the editor's URL. */
function characterId(page) {
    return Number(page.url().match(/\/characters\/(\d+)\//)[1]);
}

/**
 * The body of the help disclosure for ``key``.
 * @param {import('@playwright/test').Page} page
 * @param {string} key
 */
function helpBody(page, key) {
    const label = key.replace(/_/g, ' ');
    return page.locator(`details.help:has(summary[aria-label="Help for ${label}"]) .help-body`).first();
}

test.describe('The sheet states the loaded canon\'s numbers (#68)', () => {
    test('the creation page rolls the canon\'s attribute dice', async ({ page }) => {
        const rules = loadedCharacterRules();
        await loginAs(page, TEST_USER);
        await page.goto('/tarmar/characters/create/');
        await expect(page.locator('h2', { hasText: /^Roll / })).toHaveText(`Roll ${rules.attribute_dice}`);
        await expect(page.getByText(
            `Roll ${rules.attribute_dice} for each of the six core attributes, in order.`
        )).toBeVisible();
    });

    test('the sheet\'s point buy, roll controls and Fatigue cells carry the canon\'s numbers', async ({ page }) => {
        const rules = loadedCharacterRules();
        await loginAs(page, TEST_USER);
        await createCharacter(page, 'Prose Rollwright');
        const id = characterId(page);
        // Nothing recorded yet, as a character started in a Games Master
        // session is: the point buy and every roll are open.
        runDjangoShell(
            'from characters.models import Character; '
            + `Character.objects.filter(pk=${id}).update(strength=None, dexterity=None, `
            + 'intelligence=None, wisdom=None, constitution=None, charisma=None, '
            + 'fatigue_roll=None, literacy_roll=None, literacy_rolled=False)'
        );
        await page.reload();

        const buy = rules.point_buy;
        await expect(page.locator('#point-buy-note')).toHaveText(
            `Type a missing score from ${buy.minimum} to ${buy.maximum}: typed scores are a `
            + `${buy.total}-point buy, so the six must total ${buy.total}. Or roll the missing `
            + `ones, ${rules.attribute_dice} each. Each score locks once recorded.`
        );
        await expect(page.locator('#roll-attributes-btn')).toHaveText(
            `Roll attributes (${rules.attribute_dice} each)`
        );
        await expect(page.locator('#roll-fatigue-btn')).toHaveText(`Roll Fatigue (${rules.fatigue.dice})`);
        const fatigueRow = page.locator('tr', { has: page.locator('#fatigue') });
        await expect(fatigueRow.locator('td.formula')).toHaveText(rules.fatigue.formula);
        const fatigueRollRow = page.locator('tr', { has: page.locator('#fatigue-roll') });
        await expect(fatigueRollRow.locator('td.formula')).toHaveText(`${rules.fatigue.dice}, rolled once`);
        await expect(page.locator('#roll-literacy-btn')).toHaveText(`Roll literacy (${rules.literacy_dice})`);
    });

    test('the sheet\'s help states the canon\'s creation dice and checks', async ({ page }) => {
        const rules = loadedCharacterRules();
        const skills = loadedSkillRules();
        await loginAs(page, TEST_USER);
        await createCharacter(page, 'Prose Helpwright');

        await expect(helpBody(page, 'strength')).toContainText(
            `either rolled ${rules.attribute_dice} in order or bought by dividing `
            + `${rules.point_buy.total} points among them.`
        );
        await expect(helpBody(page, 'fatigue')).toContainText(
            `Formula: ${rules.fatigue.help_formula}.`
        );
        const check = skills.skill_check;
        await expect(helpBody(page, 'skill_check')).toContainText(
            `Roll ${check.dice} + the governing attribute's modifier + your skill level`
        );
        await expect(helpBody(page, 'skill_check')).toContainText(
            `A natural ${check.natural_success} always succeeds`
        );
        await expect(helpBody(page, 'skill_check')).toContainText(
            `${skills.attribute_check.dice} ≤ the attribute instead.`
        );
    });

    test('the workbench states the canon\'s aging rate and survival naturals, rendered and repainted', async ({ page }) => {
        const { aging } = loadedCharacterRules();
        const survival = loadedSkillRules().survival_roll;
        await loginAs(page, TEST_USER);
        await createCharacter(page, 'Prose Agewright');
        const id = characterId(page);
        runDjangoShell(
            'from characters.models import Character; '
            + `Character.objects.filter(pk=${id}).update(age=${aging.horizon_age})`
        );
        await page.reload();

        const note = `Continues to escalate past age ${aging.horizon_age} (roughly -1 to `
            + `${aging.physical.join('/')} every ${aging.stage_years_words} years, plus -1 to `
            + `${aging.mental.join('/')} every ${aging.mental_years_words}).`;
        const preview = page.locator('#workbench-aging-preview');
        await expect(preview).toHaveText(note);
        // A track change repaints the preview in the script, from the
        // server's words in the page config.
        const track = page.locator('#track-select');
        const options = await track.locator('option').evaluateAll(
            (found) => found.map((option) => option.getAttribute('value'))
        );
        const current = await track.inputValue();
        await track.selectOption(options.find((value) => value !== current));
        await expect(preview).toHaveText(note);

        // The survival roll's automatic totals, named as the dice's ends are.
        const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        await expect(page.locator('.workbench-note')).toHaveText(new RegExp(
            `^${escape(survival.death_name)} always kills?; `
            + `${escape(survival.survival_name)} always survives?\\.$`, 'i'
        ));
    });

    test('the Games Master\'s Characters pane names the canon\'s Fatigue dice', async ({ page }) => {
        const rules = loadedCharacterRules();
        const output = readDjangoShell(
            'from django.contrib.auth import get_user_model; '
            + 'from gm.ledger import draft_defaults; '
            + 'from gm.testing import session_with_character; '
            + `user = get_user_model().objects.get(username=${JSON.stringify(TEST_USER.username)}); `
            + 'draft = draft_defaults(); '
            + "draft.update(name='Prose Fatiguewright', strength=12, dexterity=12, intelligence=12, "
            + 'wisdom=12, constitution=12, charisma=12, fatigue_roll=None); '
            + "print('SESSION', session_with_character(user, draft, name='Prose Ledger E2E').pk)"
        );
        const sessionId = output.match(/SESSION (\d+)/)[1];

        await loginAs(page, TEST_USER);
        await page.goto(`/tarmar/gm/?session=${sessionId}`);
        await expect(page.locator('.gm-sheet-fatigue .gm-draft-unset').first()).toHaveAttribute(
            'title', `Needs the ${rules.fatigue.dice} Fatigue roll first`
        );
    });
});
