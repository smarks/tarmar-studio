/**
 * A Game Master grants a True mage a working (tarmar-studio #208).
 *
 * A canon's True Magic may give a working an effect that needs a grant: only
 * a figure the fight's Game Master has named the working for may craft it.
 * What only a real browser proves: the Game Master's panel grants it through
 * a real form, which posts the CSRF token and the page it came from as the
 * other ruling forms do; the grant is then offered on the cast stage of that
 * figure alone, marked as granted, and not on another True mage's; the
 * withdrawal takes it off again; and a player is shown the record but no
 * form, and is refused a post built from the Game Master's form.
 *
 * Two True mages face one generated fighter: the test user's, seated in the
 * open seat, and the Game Master's own, seated at setup and run by the AI.
 * Both are saved characters with a level in the True skill
 * (e2e/true-magic.js). The working, its effect and its name are read from the
 * running server, and the panel's words are the configured ones
 * (config/battle-rulings.toml, e2e/rulings.js), so the spec holds under any
 * canon whose True Magic has an effect that needs a grant, and skips under
 * one without.
 *
 * An axe pass runs over the grant control, with a grant on it, and over the
 * cast stage the grant appears on.
 */

const { test, expect, TEST_USER, TEST_ADMIN, escaped } = require('./fixtures');
const { uniqueName } = require('./unique-name');
const { stopCoverage } = require('./coverage');
const { rulingWords } = require('./rulings');
const { sentencePattern } = require('./turn-menu');
const { openTheWizardsStage } = require('./wizard-stage');
const { violations, describe } = require('./axe');
const { trueMagicFacts, seedATrueMage, claimWithCharacter } = require('./true-magic');

/**
 * Text that is ``sentence`` filled, each named field by the text given for
 * it and any other by anything.
 * @param {string} sentence
 * @param {Object<string, string>} fills
 * @returns {RegExp}
 */
function filled(sentence, fills) {
    const patterns = Object.fromEntries(
        Object.entries(fills).map(([name, value]) => [name, escaped(String(value))])
    );
    return new RegExp(sentencePattern(sentence, patterns));
}

/**
 * Submit one of the grant panel's forms by pressing ``button``, and return
 * the fields it posted, read off the request the browser sent.
 * @param {import('@playwright/test').Page} page
 * @param {import('@playwright/test').Locator} button
 * @returns {Promise<URLSearchParams>}
 */
async function postedBy(page, button) {
    const [request] = await Promise.all([
        page.waitForRequest((sent) => sent.method() === 'POST'
            && new URL(sent.url()).pathname.endsWith('/working-grants/')),
        button.click(),
    ]);
    await page.waitForLoadState('load');
    return new URLSearchParams(request.postData());
}

test.describe('Granting a working', () => {
    test('a Game Master grants a figure a working, which only that figure may craft, then withdraws it; a player cannot', async ({ adminPage, browser }, testInfo) => {
        test.setTimeout(150000);
        const facts = trueMagicFacts();
        test.skip(
            facts === null || facts.granted === null,
            "the loaded canon's True Magic has no working that needs a grant"
        );
        const working = facts.granted;
        const { turn_panel: panel, working_grants: said } = rulingWords();
        const playersMage = uniqueName('E2E Granted Mage');
        const gameMastersMage = uniqueName('E2E Other Mage');
        await seedATrueMage(adminPage, TEST_USER, playersMage, facts);
        await seedATrueMage(adminPage, TEST_ADMIN, gameMastersMage, facts);

        let battleId = 0;
        const playerPage = await openTheWizardsStage(adminPage, browser, uniqueName('E2E Grant'), {
            characterNames: [gameMastersMage],
            claim: (page) => claimWithCharacter(page, playersMage),
            beforeTurn: (id) => { battleId = id; },
        });
        const battlePath = `/tarmar/battles/${battleId}/`;
        const grantedTier = playerPage.locator(
            `#turn-panel .turn-craft-tier[data-working="${working.key}"]`
        );
        /**
         * The player's cast stage, as the server now offers it. A ruling made
         * while a move is awaited clears the pause, so the Game Master
         * advances and the turn replays to the player's move under it.
         */
        async function reopenTheStage() {
            await expect(adminPage.locator('#advance-turn')).toBeEnabled();
            await adminPage.click('#advance-turn');
            await expect(adminPage.locator('#battle-status')).toHaveText('Awaiting players');
            await playerPage.reload();
            await playerPage.locator('#turn-panel .turn-option-cast').click();
            await expect(playerPage.locator('#turn-panel .turn-craft')).toBeVisible();
        }

        // Nothing granted: the player reads that, with no form, and is not
        // offered the working.
        const playersPanel = playerPage.locator('#battle-working-grants');
        await expect(playersPanel.locator('.working-grants-line')).toHaveText(said.none_granted);
        await expect(playersPanel.locator('form')).toHaveCount(0);
        await expect(playerPage.locator('#turn-panel .turn-craft')).toBeVisible();
        await expect(grantedTier).toHaveCount(0);

        // The Game Master grants it to his own mage, through the real form.
        const gameMastersPanel = adminPage.locator('#battle-working-grants');
        const grantForm = gameMastersPanel.locator('.working-grant-form');
        await grantForm.locator('select').first().selectOption({ label: gameMastersMage });
        await grantForm.locator('select').nth(1).selectOption({ label: working.label });
        await grantForm.locator('input[type="number"]').fill('1');
        const posted = await postedBy(
            adminPage, grantForm.getByRole('button', { name: said.grant_button })
        );
        expect(posted.get('csrfmiddlewaretoken')).toBeTruthy();
        expect(posted.get('next')).toBe(battlePath);
        const grants = gameMastersPanel.locator('.working-grant');
        await expect(grants).toHaveCount(1);
        await expect(grants.first()).toContainText(filled(said.grant_line, {
            working: working.name, name: gameMastersMage,
        }));
        const events = adminPage.locator('.battle-event');
        await expect(events.filter({
            hasText: filled(said.log_granted, { name: gameMastersMage, working: working.name }),
        })).toHaveCount(1);

        // Granted to the other mage, it is not the player's to craft.
        await reopenTheStage();
        await expect(grantedTier).toHaveCount(0);
        await expect(playersPanel.locator('.working-grant')).toHaveCount(1);

        // Granted to the player's mage too, it is offered there, marked.
        await grantForm.locator('select').first().selectOption({ label: playersMage });
        await grantForm.locator('select').nth(1).selectOption({ label: working.label });
        await grantForm.locator('input[type="number"]').fill('1');
        await postedBy(adminPage, grantForm.getByRole('button', { name: said.grant_button }));
        await expect(grants).toHaveCount(2);
        await reopenTheStage();
        await expect(grantedTier).toHaveCount(1);
        await expect(grantedTier.locator('.turn-craft-granted')).toHaveText(panel.craft_granted);
        const onTheStage = await violations(playerPage, { include: '#turn-panel' });
        expect(onTheStage, describe(onTheStage)).toEqual([]);
        const onTheControl = await violations(adminPage, { include: '#battle-working-grants' });
        expect(onTheControl, describe(onTheControl)).toEqual([]);

        // The player reads both grants and has no form to change either,
        // and a post built from the Game Master's own form, for a tier not
        // yet granted, is refused and records nothing.
        await expect(playersPanel.locator('.working-grant')).toHaveCount(2);
        await expect(playersPanel.locator('form')).toHaveCount(0);
        await grantForm.locator('input[type="number"]').fill('2');
        const fields = await grantForm.evaluate((form) => Array.from(
            form.querySelectorAll('[name]'), (field) => [field.name, field.value]
        ).filter(([name]) => name !== 'csrfmiddlewaretoken'));
        const refused = await playerPage.evaluate(async ({ path, formFields }) => {
            const body = new URLSearchParams(formFields);
            body.set('csrfmiddlewaretoken', document.querySelector('meta[name="csrf-token"]').content);
            const response = await fetch(path, { method: 'POST', body });
            return response.status;
        }, { path: `${battlePath}working-grants/`, formFields: fields });
        expect(refused).toBe(403);
        await playerPage.reload();
        await expect(playersPanel.locator('.working-grant')).toHaveCount(2);

        // The Game Master withdraws the player's grant through its own form.
        const playersGrant = grants.filter({ hasText: playersMage });
        const withdrawn = await postedBy(adminPage, playersGrant.getByRole('button', {
            name: new RegExp(`^${escaped(said.withdraw_button)}: .*${escaped(playersMage)}`),
        }));
        expect(withdrawn.get('csrfmiddlewaretoken')).toBeTruthy();
        expect(withdrawn.get('next')).toBe(battlePath);
        await expect(grants).toHaveCount(1);
        await expect(grants.first()).toContainText(gameMastersMage);
        await expect(events.filter({
            hasText: filled(said.log_withdrawn, { name: playersMage, working: working.name }),
        })).toHaveCount(1);
        await reopenTheStage();
        await expect(grantedTier).toHaveCount(0);

        await stopCoverage(playerPage, `${testInfo.title} (player context)`);
        await playerPage.context().close();
    });
});
