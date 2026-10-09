/**
 * E2E for a character's background and the skill its Origin grants
 * (issue #754).
 *
 * The chat half is a real turn through the server against the fake Ollama
 * (e2e/fake-ollama.js): "roll up <name>" plans roll_attributes for a new
 * character. Before #754 the session saved the model's Village, Commoner and
 * Moderate with no skill from the Origin and no dice in the log; now it rolls
 * origin, provenance and wealth when it makes the character, as the creation
 * page does, and grants the Origin's skill. A player's second roll is refused;
 * a Game Master's runs.
 *
 * The sheet half: the Origin's skill follows an Origin change with no reload,
 * and an Origin nobody rolled is marked and offered its roll (issue #858).
 */

const {
    test, expect, loginAs, createCharacter, TEST_USER,
} = require('./fixtures');
const { runDjangoShell } = require('./django');

// location.md: the skills each Origin's grant may be held in ("Village: Street
// smarts OR survival"); Consult GM grants none. A reroll from Rural to Village
// keeps Survival as the Village grant, so Village accepts either.
const ORIGIN_SKILLS = {
    City: /Street Smarts/,
    Village: /Street Smarts|Survival/,
    Rural: /Survival/,
};

/** Roll up a character in a new session, and the pane's live entry for it. */
async function rollUp(page, name) {
    await page.goto('/tarmar/gm/');
    await page.click('#gm-panel-tab-characters');
    await page.fill('#user-input', `Roll up ${name}`);
    await page.click('#send-btn');
    // The pane fills in as the turn runs, with no reload (issue #571).
    const draft = page.locator('#gm-panel-characters .gm-draft', {
        has: page.locator('.gm-sheet-name', { hasText: name }),
    });
    await expect(draft).toHaveCount(1, { timeout: 30000 });
    await expect(page.locator('.message.assistant').last()).toContainText('nothing stirs', { timeout: 30000 });
    return draft;
}

/** The background parts the pane shows, each asserted present first. */
async function paneBackground(draft) {
    const background = draft.locator('.gm-draft-background');
    const wealth = draft.locator('.gm-draft-wealth');
    // Present before anything is asserted of their text: a missing element
    // must fail here, not pass a negative assertion.
    await expect(background).toHaveCount(1);
    await expect(wealth).toHaveCount(1);
    return { background, wealth };
}

/** The character's sheet: its Origin, dice log and skills. */
async function openSheet(page, draft) {
    await page.goto(await draft.locator('.gm-sheet-name a').getAttribute('href'));
    const origin = await page.locator('[data-field="location"] select').inputValue();
    return { origin, diceLog: page.locator('#dice-log'), skills: page.locator('#skills-list') };
}

async function expectOriginSkill(skills, origin) {
    const held = ORIGIN_SKILLS[origin];
    if (held) {
        await expect(skills).toContainText(held);
    }
}

async function send(page, message) {
    await page.fill('#user-input', message);
    await page.click('#send-btn');
}

test.describe('GM chat — a character built in chat rolls its background', () => {
    test('the pane and the sheet show a rolled background and the Origin skill', async ({ page }, testInfo) => {
        const name = `Wren${testInfo.workerIndex}${Date.now()}`;
        await loginAs(page, TEST_USER);

        const draft = await rollUp(page, name);
        const { background, wealth } = await paneBackground(draft);
        await expect(background).not.toContainText('not rolled');
        await expect(wealth).not.toContainText('not rolled');

        const sheet = await openSheet(page, draft);
        await expect(sheet.diceLog).toContainText('Provenance: d100=');
        await expect(sheet.diceLog).toContainText('Location: d100=');
        await expect(sheet.diceLog).toContainText('Location effects:');
        await expect(sheet.diceLog).toContainText('Wealth: d100=');
        await expectOriginSkill(sheet.skills, sheet.origin);
        await expect(page.locator('#origin-not-rolled')).toBeHidden();
    });

    test("a player's second roll is refused and the background stands", async ({ page }, testInfo) => {
        const name = `Tamsin${testInfo.workerIndex}${Date.now()}`;
        await loginAs(page, TEST_USER);
        const draft = await rollUp(page, name);
        const { background, wealth } = await paneBackground(draft);
        const before = [await background.textContent(), await wealth.textContent()];

        await send(page, `Reroll ${name}'s background`);

        await expect(page.locator('.chat-messages .dice-roll').last()).toContainText(
            'only a Game Master can roll it again', { timeout: 30000 },
        );
        await expect(background).toHaveText(before[0]);
        await expect(wealth).toHaveText(before[1]);
    });

    test("a Game Master's reroll runs, and the pane and the sheet follow it", async ({ adminPage: page }, testInfo) => {
        const name = `Gorn${testInfo.workerIndex}${Date.now()}`;
        const draft = await rollUp(page, name);
        await paneBackground(draft);

        await send(page, `Reroll ${name}'s background`);

        const chip = page.locator('.chat-messages .dice-roll').last();
        await expect(chip).toContainText('rolled provenance: Provenance: d100=', { timeout: 30000 });
        await expect(chip).toContainText('rolled the origin: Location: d100=');
        await expect(chip).toContainText('rolled wealth: Wealth: d100=');
        // The Origin the reroll landed on, as the chip reports it.
        const rolled = (await chip.textContent()).match(/Location: d100=\d+ -> ([A-Za-z ]+?)(;|$)/)[1].trim();
        const { background } = await paneBackground(draft);
        await expect(background).toContainText(rolled);

        const sheet = await openSheet(page, draft);
        expect(sheet.origin).toBe(rolled);
        // Two rolls of each table: the one it was made with, and the reroll.
        await expect(sheet.diceLog).toContainText(/Provenance: d100=[\s\S]*Provenance: d100=/);
        await expect(sheet.diceLog).toContainText(/Wealth: d100=[\s\S]*Wealth: d100=/);
        await expectOriginSkill(sheet.skills, rolled);
    });
});

test.describe('Character sheet — the Origin and its skill (issue #754)', () => {
    test('changing the Origin moves its skill in the list, with no reload', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await createCharacter(page);
        const id = Number(page.url().match(/\/characters\/(\d+)\//)[1]);
        // A City character holding the City's point, before literacy is rolled.
        runDjangoShell(
            'from characters.models import Character; '
            + `Character.objects.filter(pk=${id}).update(location="City", `
            + 'skills={"street smarts": 1}, origin_skill="street smarts", '
            + 'literacy_roll=None, literacy_rolled=False, literacy_ruling=None)'
        );
        await page.reload();
        let reloaded = false;
        page.on('load', () => { reloaded = true; });
        const skills = page.locator('#skills-list');
        await expect(skills).toContainText('Street Smarts');

        await page.locator('[data-field="location"] select').selectOption('Rural');

        await expect(skills).toContainText('Survival');
        await expect(skills).not.toContainText('Street Smarts');
        await expect(page.locator('#allocated-points')).toHaveText('1');
        expect(reloaded).toBe(false);
    });

    test('an Origin nobody rolled is marked, and its player rolls it once', async ({ page }) => {
        await loginAs(page, TEST_USER);
        await createCharacter(page);
        const id = Number(page.url().match(/\/characters\/(\d+)\//)[1]);
        // A character a session started before #754, as migration 0021 left it.
        runDjangoShell(
            'from characters.models import Character; '
            + `Character.objects.filter(pk=${id}).update(location="Village", `
            + 'location_recorded=False, skills={}, origin_skill="", '
            + 'literacy_roll=None, literacy_rolled=False, literacy_ruling=None)'
        );
        await page.reload();
        await expect(page.locator('#origin-not-rolled')).toBeVisible();

        await page.locator('#roll-origin-btn').click();

        await expect(page.locator('#roll-origin-btn')).toHaveCount(0);
        await expect(page.locator('#origin-not-rolled')).toBeHidden();
        await expect(page.locator('#dice-log')).toContainText('Location: d100=');
        const origin = await page.locator('[data-field="location"] select').inputValue();
        await expectOriginSkill(page.locator('#skills-list'), origin);
    });
});
