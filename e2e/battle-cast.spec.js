/**
 * E2E test for the wizard cast flow (issue #250).
 *
 * What only a real browser proves about the gate: a seated wizard's action
 * menu carries ONE cast option rather than a row per spell; declaring it
 * opens the spell stage in place (no modal, nothing transient) with the
 * confirm still refused; the spells the wizard cannot afford are listed
 * with the reason instead of hidden; picking one ungates the confirm; and
 * standing down puts the wizard back to the plain menu with no lingering
 * selection. Then the cast is taken, logged, and survives a reload.
 *
 * True casts (tarmar-studio #208), in the last two tests: a True mage, a
 * saved character with a level in the True skill (e2e/true-magic.js), is
 * offered beside its own spell's recipe cast a True cast of that spell's rung
 * on every other element's ladder, each row marked as a True cast. Cast
 * plain, one in another element is logged as a True cast and drains nothing;
 * cast deeper, by a push, it drains the mage as it goes off. The spells,
 * workings and numbers are read from the running server, and the turn of the
 * deeper cast is pinned by dry-running it (pinTheMagesTurns), so both tests
 * hold under any canon with True Magic and skip under one with none.
 */

const {
    test, expect, TEST_USER, loginAs, submitPassphrase, createBattleViaSetup, battleRowOnList,
    escaped,
} = require('./fixtures');
const { uniqueName } = require('./unique-name');
const { chosenBy, rulingWords } = require('./rulings');
const { startCoverage, stopCoverage } = require('./coverage');
const { readDjangoShell } = require('./django');
const { menuWords, menuLine, sentencePattern } = require('./turn-menu');
const { openTheWizardsStage } = require('./wizard-stage');
const {
    trueMagicFacts, seedATrueMage, claimWithCharacter, placeTheMage, pinTheMagesTurns,
} = require('./true-magic');

/**
 * Stand the battle's wizard at the centre, one foe two hexes off and the
 * other four hexes the other way, neither able to move; print the two foes'
 * ids and names, nearest first.
 *
 * @param {number} battleId
 * @returns {{id: number, name: string}[]} the foes, nearest first
 */
function placeTheWizardBetweenTwoFoes(battleId) {
    const output = readDjangoShell(`
import json
from battle.models import Battle
from battle.persistence import dump_state, load_state
battle = Battle.objects.get(pk=${battleId})
state = load_state(battle.state_json, battle.rules_profile)
wizard = next(figure for figure in state.combatants if figure.spells)
foes = [figure for figure in state.combatants if figure is not wizard]
assert len(foes) == 2, foes
wizard.position = (0, 0)
for foe, cell in zip(foes, [(2, 0), (-4, 0)], strict=True):
    foe.position = cell
    foe.move_walk = foe.move_jog = foe.move_run = foe.move_sprint = 0
battle.state_json = dump_state(state)
battle.save(update_fields=["state_json"])
print("FOES " + json.dumps([{"id": foe.combatant_id, "name": foe.name} for foe in foes]))
`);
    return JSON.parse(output.match(/^FOES (.*)$/m)[1]);
}

/**
 * The paused battle's menu entry the AI would take, as the server marked it,
 * with its spell's name when it is a cast.
 *
 * @param {number} battleId
 * @returns {{spell_key?: string, spell_name?: string, target_id?: number}}
 */
function theAiPick(battleId) {
    const output = readDjangoShell(`
import json
from battle.models import Battle
from battle.spells import get_spell
pending = Battle.objects.get(pk=${battleId}).pending_input_json
pick = next(entry for entry in pending["candidates"] if entry["ai_choice"])
if pick.get("spell_key"):
    pick["spell_name"] = get_spell(pick["spell_key"]).name
print("AI_PICK " + json.dumps(pick))
`);
    return JSON.parse(output.match(/^AI_PICK (.*)$/m)[1]);
}

/**
 * The cast the battle's decision event recorded for the wizard.
 *
 * @param {number} battleId
 * @returns {{spell_key: string, target_id: number}}
 */
function theRecordedCast(battleId) {
    const output = readDjangoShell(`
import json, string
from battle.models import BattleEvent
from battle.wording import words
credit = next(text for text, *_ in string.Formatter().parse(words().services.chosen_by) if text.strip())
decision = BattleEvent.objects.filter(
    battle_id=${battleId}, event_type="decision", message__contains=credit.strip()
).get()
chosen = decision.payload["chosen"]
print("CHOSEN " + json.dumps({"spell_key": chosen["spell_key"], "target_id": chosen["target_id"]}))
`);
    return JSON.parse(output.match(/^CHOSEN (.*)$/m)[1]);
}

test.describe('Wizard cast flow', () => {
    test('a seated wizard declares, inspects, stands down, then casts', async ({ adminPage, browser }, testInfo) => {
        test.setTimeout(120000);
        const battleName = uniqueName('E2E Cast');

        // A battle with one AI opponent and one seat for the wizard.
        await createBattleViaSetup(adminPage, {
            name: battleName, fighters: 1, seed: 11, playerSeats: 1,
        });
        await expect(adminPage.locator('#battle-status')).toHaveText('Lobby');

        // The player claims the seat with a generated wizard and readies up.
        const playerContext = await browser.newContext();
        const playerPage = await playerContext.newPage();
        await startCoverage(playerPage);
        await submitPassphrase(playerPage);
        await loginAs(playerPage, TEST_USER);
        const listRow = await battleRowOnList(playerPage, battleName);
        await listRow.locator('a', { hasText: battleName }).first().click();
        await playerPage.click('summary:has-text("Claim this seat")');
        await playerPage.selectOption('select[name="archetype"]', 'wizard');
        await playerPage.click('button:has-text("Join with a generated fighter")');
        // Wait for the joined lobby before Ready, or the click can land on
        // the page the join is replacing (issue #493).
        await expect(playerPage.locator('.lobby-seat.is-you')).toBeVisible();
        await playerPage.click('button:has-text("I\'m ready")');
        await expect(playerPage.locator('.lobby-ready-chip.is-ready')).toBeVisible();

        // The DM starts and advances into the wizard's turn.
        await expect(adminPage.locator('.lobby-ready-chip.is-ready'))
            .toBeVisible({ timeout: 15000 });
        await adminPage.click('.battle-start-form button[type="submit"]');
        await expect(playerPage.locator('#battle-arena'))
            .toBeVisible({ timeout: 15000 });
        await adminPage.click('#advance-turn');
        await expect(adminPage.locator('#battle-status'))
            .toHaveText('Awaiting players');

        // One cast option, not one per spell — and it names the mana pool.
        const castOption = playerPage.locator('#turn-panel .turn-option-cast');
        await expect(castOption).toBeVisible({ timeout: 15000 });
        await expect(castOption).toHaveCount(1);
        await expect(castOption).toContainText(menuWords().panel.cast_option);
        await expect(castOption).toContainText(menuWords().stage.mana_pool);
        await expect(playerPage.locator('#turn-panel .turn-cast-stage'))
            .toHaveCount(0);

        // Declaring the cast opens the stage; the confirm stays refused
        // until a spell is explicitly picked, and nothing is pre-selected.
        await castOption.click();
        const stage = playerPage.locator('#turn-panel .turn-cast-stage');
        await expect(stage).toBeVisible();
        await expect(playerPage.locator('#turn-panel .turn-confirm')).toBeDisabled();
        await expect(stage.locator('.turn-spell[aria-pressed="true"]')).toHaveCount(0);
        // The wizard's whole hand is on show, castable or not.
        await expect(stage.locator('.turn-spell').first()).toBeVisible();

        // Standing down leaves no selection behind.
        await stage.locator('.turn-spell:not(:disabled)').first().click();
        await expect(playerPage.locator('#turn-panel .turn-confirm')).toBeEnabled();
        await playerPage.click('#turn-panel .turn-cast-standdown');
        await expect(playerPage.locator('#turn-panel .turn-cast-stage')).toHaveCount(0);
        await expect(playerPage.locator('#turn-panel .turn-confirm')).toBeDisabled();

        // Declare again, pick a spell, and cast it for real.
        await castOption.click();
        const spell = stage.locator('.turn-spell:not(:disabled)').first();
        const spellName = (
            await spell.locator('.turn-spell-name').textContent()
        ).trim();
        await spell.click();
        await expect(spell).toHaveAttribute('aria-pressed', 'true');
        await playerPage.click('#turn-panel .turn-confirm');

        // The turn completes and the log credits the player's cast.
        await expect(playerPage.locator('#battle-turn')).toHaveText('1');
        const decision = playerPage.locator('.event-decision', {
            hasText: chosenBy(TEST_USER.username),
        });
        await expect(decision).toBeVisible();
        await expect(decision).toContainText(spellName);

        // A reload reproduces the cast from the database.
        await playerPage.reload();
        await expect(
            playerPage.locator('.event-decision', { hasText: spellName })
        ).toBeVisible();

        await stopCoverage(playerPage, `${testInfo.title} (player context)`);
        await playerContext.close();
    });

    test('a wizard between two foes casts at the far one (tarmar-engine #21)', async ({ adminPage, browser }, testInfo) => {
        test.setTimeout(120000);
        let battleId = 0;
        let foes = [];
        const playerPage = await openTheWizardsStage(
            adminPage, browser, uniqueName('E2E Cast two foes'), {
                fighters: 2,
                beforeTurn: (id) => {
                    battleId = id;
                    foes = placeTheWizardBetweenTwoFoes(id);
                },
            }
        );
        const [near, far] = foes;
        const stage = playerPage.locator('#turn-panel .turn-cast-stage');
        await expect(stage).toBeVisible();

        // One Fire Missile row per foe, each naming its own, nearest first,
        // in the configured words (#145).
        const rowAt = (spell, target) => menuLine(menuWords().stage.row_at, {
            spell: escaped(spell), target: escaped(target),
        });
        const missileRows = stage.locator('.turn-spell', {
            has: playerPage.locator('.turn-spell-name', {
                hasText: new RegExp(`^${sentencePattern(menuWords().stage.row_at, {
                    spell: escaped('Fire Missile'),
                })}$`),
            }),
        });
        await expect(missileRows.locator('.turn-spell-name')).toHaveText([
            rowAt('Fire Missile', near.name),
            rowAt('Fire Missile', far.name),
        ]);

        // The AI's own pick, as the server marked it, is a cast at the near
        // foe (Lightning Bolt, on these seeds), and the tag sits on that
        // cast's own row and on no other: not on either Fire Missile row.
        const aiPick = theAiPick(battleId);
        expect(aiPick.spell_key).toBeTruthy();
        expect(aiPick.target_id).toBe(near.id);
        const tagged = stage.locator('.turn-spell', {
            has: playerPage.locator('.turn-option-ai-pick'),
        });
        await expect(tagged).toHaveCount(1);
        await expect(tagged.locator('.turn-spell-name'))
            .toHaveText(rowAt(aiPick.spell_name, near.name));
        await expect(missileRows.locator('.turn-option-ai-pick')).toHaveCount(0);

        // Cast at the far foe.
        await missileRows.nth(1).click();
        await expect(missileRows.nth(1)).toHaveAttribute('aria-pressed', 'true');
        await playerPage.click('#turn-panel .turn-confirm');
        await expect(playerPage.locator('#battle-turn')).toHaveText('1');
        const decision = playerPage.locator('.event-decision', {
            hasText: chosenBy(TEST_USER.username),
        });
        await expect(decision).toContainText('Fire Missile');
        await expect(decision).toContainText(`on ${far.name}`);
        expect(theRecordedCast(battleId)).toEqual({
            spell_key: 'fire_missile', target_id: far.id,
        });

        await stopCoverage(playerPage, `${testInfo.title} (player context)`);
        await playerPage.context().close();
    });
    test.describe('True casts (tarmar-studio #208)', () => {
        /** Why both skip when the catalog holds no other element's rung. */
        const NO_OTHER_ELEMENT = "the catalog has no other element's rung of Fire Missile to True cast";

        /**
         * A True mage seated against one foe two hexes off that cannot
         * move, with its cast stage open on the first turn.
         *
         * @param {import('@playwright/test').Page} adminPage
         * @param {import('@playwright/test').Browser} browser
         * @param {ReturnType<typeof trueMagicFacts>} facts
         * @param {(battleId: number, mageName: string) => void} [pin] - pins
         *     the turn, once the figures stand
         * @returns {Promise<{playerPage: import('@playwright/test').Page, mageName: string}>}
         */
        async function openTheTrueMagesStage(adminPage, browser, facts, pin) {
            const mageName = uniqueName('E2E True Caster');
            await seedATrueMage(adminPage, TEST_USER, mageName, facts);
            const playerPage = await openTheWizardsStage(
                adminPage, browser, uniqueName('E2E True cast'), {
                    claim: (page) => claimWithCharacter(page, mageName),
                    beforeTurn: (battleId) => {
                        placeTheMage(battleId, mageName, { foeAt: [2, 0], foeStill: true });
                        if (pin) { pin(battleId, mageName); }
                    },
                }
            );
            return { playerPage, mageName };
        }

        /**
         * The battle log's line for a True cast of ``spell`` by ``mageName``,
         * the cast's own line in the configured words around it.
         * @returns {RegExp}
         */
        function trueCastLine(mageName, spell) {
            return new RegExp(sentencePattern(rulingWords().true_magic_log.true_cast, {
                line: `${escaped(mageName)} casts ${escaped(spell)}.*`,
            }));
        }

        test('a True mage True casts a spell in another element, which drains nothing', async ({ adminPage, browser }, testInfo) => {
            test.setTimeout(120000);
            const facts = trueMagicFacts();
            test.skip(facts === null, 'the loaded canon has no True Magic, so no figure True casts');
            test.skip(facts.otherElement === null, NO_OTHER_ELEMENT);
            const { otherElement } = facts;
            const { playerPage, mageName } = await openTheTrueMagesStage(adminPage, browser, facts);
            const stage = playerPage.locator('#turn-panel .turn-cast-stage');

            // The mage's own spell has its recipe row and a True cast row;
            // the spell of another element has only the True cast, marked so.
            const rowsOf = (name, kind) => stage.locator(kind, {
                has: playerPage.locator('.turn-spell-name', { hasText: name }),
            });
            const recipe = '.turn-spell:not(.turn-true-cast)';
            const trueCast = '.turn-spell.turn-true-cast';
            await expect(rowsOf('Fire Missile', recipe)).toHaveCount(1);
            await expect(rowsOf('Fire Missile', trueCast)).toHaveCount(1);
            await expect(rowsOf(otherElement.name, recipe)).toHaveCount(0);
            const other = rowsOf(otherElement.name, trueCast);
            await expect(other).toHaveCount(1);
            await expect(other.locator('.turn-spell-true')).toHaveText(
                menuWords().panel.true_cast_tag
            );

            await other.click();
            await expect(other).toHaveAttribute('aria-pressed', 'true');
            await playerPage.click('#turn-panel .turn-confirm');

            await expect(playerPage.locator('#battle-turn')).toHaveText('1');
            await expect(playerPage.locator('.event-decision', {
                hasText: chosenBy(TEST_USER.username),
            })).toContainText(otherElement.name);
            await expect(playerPage.locator('.battle-event').filter({
                hasText: trueCastLine(mageName, otherElement.name),
            })).toHaveCount(1);
            await expect(playerPage.locator('.battle-event').filter({
                hasText: new RegExp(sentencePattern(rulingWords().true_magic_log.drain, {
                    name: escaped(mageName),
                })),
            })).toHaveCount(0);

            await stopCoverage(playerPage, `${testInfo.title} (player context)`);
            await playerPage.context().close();
        });

        test('a True mage casts a spell deeper by a push, and the push drains it', async ({ adminPage, browser }, testInfo) => {
            test.setTimeout(120000);
            const facts = trueMagicFacts();
            test.skip(facts === null, 'the loaded canon has no True Magic, so no figure True casts');
            test.skip(facts.otherElement === null, NO_OTHER_ELEMENT);
            const { otherElement } = facts;
            const push = 1;
            let drained = null;
            const { playerPage, mageName } = await openTheTrueMagesStage(
                adminPage, browser, facts, (battleId, name) => {
                    ({ drained } = pinTheMagesTurns(battleId, name, {
                        spell: otherElement.key, trueCast: true, push,
                    }, 'drained'));
                }
            );
            const group = playerPage.locator('#turn-panel .turn-cast-stage .turn-spell-group', {
                has: playerPage.locator('.turn-true-cast .turn-spell-name', {
                    hasText: otherElement.name,
                }),
            });
            await group.locator('.turn-true-cast').click();
            // "None", then the amounts.
            await group.locator('.turn-push-amount').nth(push).click();
            await playerPage.click('#turn-panel .turn-confirm');

            await expect(playerPage.locator('#battle-turn')).toHaveText('1');
            await expect(playerPage.locator('.battle-event').filter({
                hasText: trueCastLine(mageName, otherElement.name),
            })).toHaveCount(1);
            const { drain } = rulingWords().true_magic_log;
            await expect(playerPage.locator('.battle-event').filter({
                hasText: new RegExp(sentencePattern(drain, {
                    name: escaped(mageName), cost: String(drained),
                })),
            })).toHaveCount(1);

            await stopCoverage(playerPage, `${testInfo.title} (player context)`);
            await playerPage.context().close();
        });
    });
});
