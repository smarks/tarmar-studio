/**
 * A True mage crafts a working on the battle board (tarmar-studio #208).
 *
 * What only a real browser proves about the craft: the seated mage's cast
 * stage carries the craft control, a card per tier under the canon's label
 * and tier cap, and picking a tier ungates the confirm; the craft then
 * stands on the mage's roster card turn after turn, and the menu offers its
 * continuation as one option naming the working and "turn k of n"; a hit on
 * the mage while it crafts is answered by a roll, logged with its total and
 * target; and the working goes off at the end of its last turn, its drain
 * logged and, when the drain takes the pool to 0, the mage felled by it.
 *
 * The mage is a saved character with a level in the True skill, since a
 * generated fighter never holds one (e2e/true-magic.js). Every number and
 * name of the canon's True Magic is read from the running server, and every
 * line the spec reads is the configured sentence (config/battle-rulings.toml,
 * e2e/rulings.js) filled with what the board names, so the spec holds under
 * any canon with True Magic. The working is the first damaging one, at the
 * lowest tier whose craft takes more than one turn; under a canon with no
 * True Magic, or none such, the spec skips. The turns are
 * pinned by dry-running them in the Django shell (pinTheMagesTurns); the log
 * assertions are what prove the pin took.
 *
 * An axe pass runs over the craft control and over the continuation, the two
 * controls a turn panel shows only in play.
 */

const { test, expect, TEST_USER, escaped } = require('./fixtures');
const { uniqueName } = require('./unique-name');
const { stopCoverage } = require('./coverage');
const { rulingWords, chosenBy } = require('./rulings');
const { sentencePattern } = require('./turn-menu');
const { openTheWizardsStage } = require('./wizard-stage');
const { violations, describe } = require('./axe');
const {
    trueMagicFacts, seedATrueMage, claimWithCharacter, placeTheMage, pinTheMagesTurns,
} = require('./true-magic');

/** Why every test here skips under a canon with no True Magic. */
const NO_TRUE_MAGIC = 'the loaded canon has no True Magic, so no figure crafts a working';
/** Why they skip under one whose damaging workings the mage crafts in one turn. */
const NO_LONG_CRAFT = 'no damaging working the mage may craft takes more than one turn, so no craft stands from turn to turn';

/**
 * A line of the page that is ``sentence`` filled, each named field by the
 * text given for it and any other by anything.
 * @param {string} sentence - a configured sentence
 * @param {Object<string, string|number>} fills
 * @returns {RegExp}
 */
function filled(sentence, fills) {
    const patterns = Object.fromEntries(
        Object.entries(fills).map(([name, value]) => [name, escaped(String(value))])
    );
    return new RegExp(sentencePattern(sentence, patterns));
}

test.describe('Crafting a working', () => {
    /** The player's page, closed after each test. */
    let playerPage = null;

    test.afterEach(async () => {
        if (playerPage) { await playerPage.context().close(); }
        playerPage = null;
    });

    test('a True mage crafts a working over its turns, and the drain as it goes off fells the mage', async ({ adminPage, browser }, testInfo) => {
        test.setTimeout(180000);
        const facts = trueMagicFacts();
        test.skip(facts === null, NO_TRUE_MAGIC);
        test.skip(facts.damaging === null, NO_LONG_CRAFT);
        const working = facts.damaging;
        const { turn_panel: panel, true_magic: said, true_magic_log: log } = rulingWords();
        const mageName = uniqueName('E2E True Mage');
        await seedATrueMage(adminPage, TEST_USER, mageName, facts);

        // The foe stands off where it cannot reach the mage, so nothing
        // breaks the craft; the mage's drain pool holds what the working
        // drains, so its drain fells the mage.
        playerPage = await openTheWizardsStage(adminPage, browser, uniqueName('E2E Craft'), {
            claim: (page) => claimWithCharacter(page, mageName),
            beforeTurn: (battleId) => {
                placeTheMage(battleId, mageName, {
                    foeAt: [4, 0], foeStill: true, drainedBy: working.key,
                });
                pinTheMagesTurns(
                    battleId, mageName, { spell: working.key, turns: working.turns }, 'felled'
                );
            },
        });
        const confirm = playerPage.locator('#turn-panel .turn-confirm');

        // The craft control: the canon's label and cap, and a card per tier
        // the menu offers, none pressed and the confirm refused until one is.
        const control = playerPage.locator('#turn-panel .turn-cast-stage .turn-craft');
        await expect(control.locator('.turn-craft-label')).toHaveText(
            filled(panel.craft_label, { skill: facts.skillLabel, tier_cap: facts.tierCap })
        );
        const tier = control.locator(`.turn-craft-tier[data-working="${working.key}"]`);
        await expect(tier).toHaveAttribute('aria-pressed', 'false');
        await expect(tier.locator('.turn-spell-name')).toHaveText(
            filled(panel.craft_tier, { tier: working.tier })
        );
        await expect(confirm).toBeDisabled();
        await tier.click();
        await expect(tier).toHaveAttribute('aria-pressed', 'true');
        const figure = control.locator('.turn-craft-target-figure[aria-pressed="true"]');
        const foeName = (await figure.textContent()).trim();
        await expect(confirm).toBeEnabled();
        const atTheCraft = await violations(playerPage, { include: '#turn-panel' });
        expect(atTheCraft, describe(atTheCraft)).toEqual([]);
        await confirm.click();

        // Turn 1: the craft begins, credited to the player, and stands on
        // the mage's card.
        await expect(playerPage.locator('#battle-turn')).toHaveText('1');
        await expect(playerPage.locator('.event-decision', {
            hasText: chosenBy(TEST_USER.username),
        })).toHaveCount(1);
        const events = playerPage.locator('.battle-event');
        await expect(events.filter({
            hasText: filled(log.craft_begun, {
                name: mageName, working: working.name, turn: 1, turns: working.turns,
            }),
        })).toHaveCount(1);
        const mageCard = playerPage.locator('.combatant-card', { hasText: mageName });
        const standing = mageCard.locator('.combatant-crafting');
        await expect(standing).toHaveText(filled(said.card_naming, {
            working: working.name, target: foeName, done: 1, turns: working.turns,
        }));

        // Each turn after: one continuation, naming the working and its turn.
        for (let turn = 2; turn <= working.turns; turn += 1) {
            await adminPage.click('#advance-turn');
            const continuation = playerPage.locator('#turn-panel .turn-option-craft');
            await expect(continuation).toHaveCount(1, { timeout: 15000 });
            await expect(continuation).toHaveAttribute('data-working', working.key);
            await expect(continuation.locator('.turn-option-plain')).toHaveText(
                filled(said.craft_continue, {
                    working: working.name, turn, turns: working.turns,
                })
            );
            await expect(playerPage.locator('#turn-panel .turn-craft-tier')).toHaveCount(0);
            if (turn === 2) {
                const atTheContinuation = await violations(playerPage, { include: '#turn-panel' });
                expect(atTheContinuation, describe(atTheContinuation)).toEqual([]);
            }
            await continuation.click();
            await expect(continuation).toHaveAttribute('aria-pressed', 'true');
            await confirm.click();
            await expect(playerPage.locator('#battle-turn')).toHaveText(String(turn));
            await expect(events.filter({
                hasText: filled(log.craft_continued, {
                    name: mageName, working: working.name, turn, turns: working.turns,
                }),
            })).toHaveCount(1);
        }

        // The last turn ends with the working going off, its drain, and the
        // mage felled by it; the craft no longer stands.
        await expect(events.filter({
            hasText: filled(log.completed, { name: mageName, working: working.name }),
        })).toHaveCount(1);
        await expect(events.filter({
            hasText: filled(log.drain, { name: mageName }),
        })).toHaveCount(1);
        await expect(events.filter({
            hasText: filled(log.drain_collapse, { name: mageName }),
        })).toHaveCount(1);
        await expect(mageCard).toHaveClass(/is-down/);
        await expect(standing).toHaveCount(0);

        await stopCoverage(playerPage, `${testInfo.title} (player context)`);
    });

    test('a hit on a crafting mage is answered by a roll, and the craft kept goes on', async ({ adminPage, browser }, testInfo) => {
        test.setTimeout(150000);
        const facts = trueMagicFacts();
        test.skip(facts === null, NO_TRUE_MAGIC);
        test.skip(facts.damaging === null, NO_LONG_CRAFT);
        const working = facts.damaging;
        const { true_magic: said, true_magic_log: log } = rulingWords();
        const mageName = uniqueName('E2E True Mage');
        await seedATrueMage(adminPage, TEST_USER, mageName, facts);

        // The foe stands beside the mage, where its weapon reaches.
        let roll = null;
        playerPage = await openTheWizardsStage(adminPage, browser, uniqueName('E2E Craft hit'), {
            claim: (page) => claimWithCharacter(page, mageName),
            beforeTurn: (battleId) => {
                placeTheMage(battleId, mageName, { foeAt: [1, 0] });
                ({ roll } = pinTheMagesTurns(battleId, mageName, { spell: working.key }, 'kept'));
            },
        });
        const tier = playerPage.locator(
            `#turn-panel .turn-craft-tier[data-working="${working.key}"]`
        );
        await tier.click();
        await playerPage.click('#turn-panel .turn-confirm');

        // The hit's roll, with the total and target the dry run read, and the
        // craft kept through it.
        await expect(playerPage.locator('#battle-turn')).toHaveText('1');
        await expect(playerPage.locator('.event-roll', { hasText: 'concentration' }))
            .toContainText(`= ${roll.natural}`);
        await expect(playerPage.locator('.battle-event').filter({
            hasText: filled(log.craft_kept, {
                name: mageName, working: working.name,
                natural: roll.natural, target: roll.target,
            }),
        })).toHaveCount(1);
        const standing = playerPage.locator('.combatant-card', { hasText: mageName })
            .locator('.combatant-crafting');
        await expect(standing).toHaveText(filled(said.card_naming, {
            working: working.name, done: 1, turns: working.turns,
        }));

        // The next turn offers the craft's continuation.
        await adminPage.click('#advance-turn');
        await expect(playerPage.locator('#turn-panel .turn-option-craft .turn-option-plain'))
            .toHaveText(filled(said.craft_continue, {
                working: working.name, turn: 2, turns: working.turns,
            }), { timeout: 15000 });

        await stopCoverage(playerPage, `${testInfo.title} (player context)`);
    });
});
