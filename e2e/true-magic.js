/**
 * True Magic on the battle board, set up for a spec (tarmar-studio #208).
 *
 * A True mage is a saved character with a level in the loaded canon's True
 * skill: a generated fighter never holds one, so a spec seeds the character
 * for its player and brings it to an open seat. Everything a spec needs of
 * the canon's True Magic (the skill, the workings, their turns, mana and
 * drain) is read from the running server here, so the specs type no number
 * or name of any canon's True Magic, and hold under whichever canon the
 * server loads.
 *
 * Turns are pinned the way e2e/battle-push.spec.js pins its Debt Roll: before
 * the turns, the Django shell dry-runs them exactly as the server will (the
 * same profile, roller and decider, the mage's craft recorded as the stage
 * offers it) and sets the battle's seed to the first one whose turns go the
 * way the spec needs. The spec's assertions on the real log are what prove
 * the pin took.
 */

const { expect, seedCharacterFromExport, searchPicker } = require('./fixtures');
const { readDjangoJson, runDjangoShell } = require('./django');

/** The True level a seeded mage holds, and the spell it knows by its recipe. */
const TRUE_LEVEL = 2;
const KNOWN_SPELL = 'fire_missile';
/** The seeded mage's Mana Pool. */
const MAGE_MANA = 10;

/**
 * The loaded canon's True Magic, or null under a canon with none.
 *
 * ``skill`` is the True skill's key, ``skillPoints`` what TRUE_LEVEL costs
 * in it and ``tierCap`` the highest tier it crafts; ``spellPoints`` what
 * level 1 in KNOWN_SPELL costs. ``otherElement`` is KNOWN_SPELL's rung of
 * the first other element's ladder, which a True mage may True cast
 * (``spells.element_siblings``; null when there is none). ``damaging`` is the
 * first effect that damages and needs no grant, at the lowest tier the mage
 * may craft and pay for whose craft takes more than one turn (null when no
 * effect or no tier does), and
 * ``granted`` the first effect that needs a grant, at tier 1 (null when none
 * does); each as the board names it: its key, name, tier, turns and mana.
 * @returns {{skill: string, skillLabel: string, skillPoints: number,
 *     tierCap: number, spellPoints: number,
 *     otherElement: {key: string, name: string} | null,
 *     damaging: {effect: string, label: string, key: string, name: string,
 *         tier: number, turns: number, mana: number} | null,
 *     granted: {effect: string, label: string, key: string, name: string,
 *         tier: number, turns: number, mana: number} | null} | null}
 */
function trueMagicFacts() {
    return readDjangoJson(`
import json
from tarmar_engine.magic import SpellEffectKind
from tarmar_engine.spells import element_siblings, get_spell
from battle.magic_rules import magic_rules
from characters.skills import points_for_level
rules = magic_rules()
true_magic = rules.true_magic
def at_tier(effect, tier):
    working = true_magic.working(effect.key, tier)
    return {
        "effect": effect.key, "label": effect.label, "key": working.key, "name": working.name,
        "tier": tier, "turns": true_magic.turns_for(tier), "mana": working.level,
    }
def crafted_over_turns(effect):
    tier = next((
        tier for tier in range(1, true_magic.tier_cap(${TRUE_LEVEL}) + 1)
        if true_magic.turns_for(tier) > 1 and true_magic.working(effect.key, tier).level <= ${MAGE_MANA}
    ), None)
    return None if tier is None else at_tier(effect, tier)
if true_magic is None:
    facts = None
else:
    skill = rules.channel.derivations[true_magic.channel_kind].source_skill
    damaging = next((
        effect for effect in true_magic.effects
        if effect.kind is SpellEffectKind.DAMAGE and not effect.needs_grant
    ), None)
    granted = next((effect for effect in true_magic.effects if effect.needs_grant), None)
    other_element = next(iter(element_siblings(get_spell(${JSON.stringify(KNOWN_SPELL)}))), None)
    facts = {
        "skill": skill,
        "skillLabel": true_magic.skill_label,
        "skillPoints": points_for_level(${TRUE_LEVEL}, skill),
        "tierCap": true_magic.tier_cap(${TRUE_LEVEL}),
        "spellPoints": points_for_level(1),
        "otherElement": None if other_element is None else {
            "key": other_element.key, "name": other_element.name,
        },
        "damaging": None if damaging is None else crafted_over_turns(damaging),
        "granted": None if granted is None else at_tier(granted, 1),
    }
print("TRUE_MAGIC " + json.dumps(facts))
`, 'TRUE_MAGIC');
}

/**
 * Give ``account`` a True mage named ``name``: TRUE_LEVEL in the True skill,
 * KNOWN_SPELL at level 1 and a Mana Pool of MAGE_MANA. Seeded as
 * seedCharacterFromExport seeds, since only a Game Master starts the True
 * skill (tarmar-studio #208) and a player's import keeps none of it.
 * @param {import('@playwright/test').Page} page - left on the mage's sheet
 * @param {{username: string}} account
 * @param {string} name
 * @param {ReturnType<typeof trueMagicFacts>} facts
 * @returns {Promise<string>} the character's id
 */
async function seedATrueMage(page, account, name, facts) {
    return seedCharacterFromExport(page, account, {
        _format: 'tarmar-character',
        _version: 1,
        data: {
            name,
            strength: 10,
            dexterity: 12,
            intelligence: 14,
            wisdom: 16,
            constitution: 12,
            charisma: 12,
            appearance_roll: '3',
            height_roll: '3',
            weight_roll: '3',
            fatigue_roll: 7,
            provenance_class: 'commoner',
            provenance_detail: 'Laborer',
            location: 'Village',
            wealth_level: 'Moderate',
            starting_coin: 100,
            skills: { [facts.skill]: facts.skillPoints },
            spells: { [KNOWN_SPELL]: facts.spellPoints },
            free_skill_points: 0,
            free_spell_points: MAGE_MANA,
            equipment: [],
            pack_dropped: false,
            total_xp: 1500,
            age: 20,
            experience_terms: 1,
            experience_log: [],
            money_earned: 0,
            aging_str_penalty: 0,
            aging_dex_penalty: 0,
            aging_int_penalty: 0,
            aging_wis_penalty: 0,
            aging_con_penalty: 0,
            is_alive: true,
            death_cause: '',
        },
    });
}

/**
 * Claim the lobby's first open seat with the viewer's saved character
 * ``name``, found through the claim's picker, and ready it. The shape of
 * e2e/wizard-stage.js's claimAWizard, for a character rather than a
 * generated fighter.
 * @param {import('@playwright/test').Page} page - the claimant's page, on the battle
 * @param {string} name
 * @returns {Promise<string>} the seated figure's name
 */
async function claimWithCharacter(page, name) {
    const open = page.locator('.lobby-seat.is-open').first();
    await open.locator('summary:has-text("Claim this seat")').click();
    const search = open.locator('.picker-search');
    await searchPicker(page, `#${await search.getAttribute('id')}`, name);
    await open.locator('select[name="character_id"]').selectOption({ label: name });
    await open.locator('button:has-text("Join with this character")').click();
    const mine = page.locator('.lobby-seat.is-you');
    await expect(mine).toBeVisible();
    await mine.locator('button:has-text("I\'m ready")').click();
    await expect(mine.locator('.lobby-ready-chip.is-ready')).toBeVisible();
    return (await mine.locator('.lobby-seat-name').textContent()).trim();
}

/**
 * Python that finds the battle's mage, named ``mage_name``, and its one
 * foe, and that dry-runs turns as the server runs them: ``menu_of`` the
 * mage's menu on a copy of the board, and ``play`` one turn of it with the
 * mage's choice recorded, which advances ``battle.turn_number`` as a
 * committed turn does so the next turn's roller is the server's too. The
 * battle is changed in memory only, until the caller saves it.
 * @param {number} battleId
 * @param {string} mageName
 * @returns {string}
 */
function boardScript(battleId, mageName) {
    return `
import json
from battle import profiles
from battle.models import Battle
from battle.persistence import dump_state
from battle.services import PlayerInputNeeded, _human_decider, manual_controllers
battle = Battle.objects.get(pk=${battleId})
profile = profiles.for_battle(battle)
controllers = manual_controllers(battle)
board = profile.load(battle.state_json)
(mage,) = [figure for figure in board.combatants if figure.name == ${JSON.stringify(mageName)}]
(foe,) = [figure for figure in board.combatants if figure is not mage]
mage_id = profile.actor_id(mage)
def figure_in(state, figure):
    return next(each for each in state.combatants if each.combatant_id == figure.combatant_id)
def menu_of(state):
    try:
        profile.run_turn(profile.load(dump_state(state)), profile.roller(), _human_decider(profile, controllers, {}))
    except PlayerInputNeeded as needed:
        return needed.candidates
    raise SystemExit("the turn asked the mage nothing")
def play(state, chosen):
    events = profile.run_turn(
        state, profile.roller(),
        _human_decider(profile, controllers, {str(mage_id): chosen.to_payload()}),
    )
    battle.turn_number = profile.turn_number(state)
    return [event.get("payload", {}) for event in events]
`;
}

/**
 * Stand the battle's mage at the centre and its one foe at ``foeAt``, the
 * foe unable to move when ``foeStill``; and, when ``drainedBy`` names a
 * working, set the mage's drain pool, on the board and on its record, to
 * what crafting it drains, so the drain as it goes off takes the pool to 0.
 * @param {number} battleId
 * @param {string} mageName
 * @param {{foeAt: [number, number], foeStill?: boolean, drainedBy?: string}} options
 */
function placeTheMage(battleId, mageName, { foeAt, foeStill = false, drainedBy = null }) {
    runDjangoShell(`${boardScript(battleId, mageName)}
from battle import workings
from battle.magic_rules import loaded_spell
mage.position = (0, 0)
foe.position = (${foeAt[0]}, ${foeAt[1]})
if ${foeStill ? 'True' : 'False'}:
    foe.move_walk = foe.move_jog = foe.move_run = foe.move_sprint = 0
working = ${drainedBy === null ? 'None' : JSON.stringify(drainedBy)}
if working is not None:
    drain = workings.casting_drain(mage, loaded_spell(working), 0)
    setattr(mage, drain.pool, drain.amount)
    # The fight writes to the mage's record, and goes on only from pools the
    # record holds (#842), so the record is set to the same.
    character = battle.combatants.get(pk=mage_id).character
    fatigue, body = character.pools()
    whole = {"fatigue": fatigue, "body": body}[drain.pool]
    setattr(character, f"{drain.pool}_damage", whole - drain.amount)
    character.save(update_fields=[f"{drain.pool}_damage"])
battle.state_json = dump_state(board)
battle.save(update_fields=["state_json"])
`);
}

/**
 * What a spec wants of the mage's turns, as Python asks it of the dry run's
 * payloads and its board: ``kept``, a hit on the crafting mage that the roll
 * keeps the craft through; ``felled``, a working going off with a success and
 * its drain felling the mage; ``drained``, a casting that drains the mage.
 */
const TURN_OUTCOMES = {
    kept: 'any(payload.get("craft_interrupted") and payload.get("held") for payload in payloads)',
    felled: 'any(payload.get("working") and payload.get("success") for payload in payloads) and not figure_in(trial, mage).conscious',
    drained: 'any(payload.get("reason") == DRAIN_REASON for payload in payloads)',
};

/**
 * Pin the battle's turns so the mage, taking the same choice at its foe for
 * ``turns`` turns from the next one, sees ``outcome`` (TURN_OUTCOMES): the
 * first seed from 1 whose dry run does, saved as the battle's seed. The
 * choice is the menu's option of ``spell``, a spell or a working, at the foe,
 * paid from the pool: a True cast when ``trueCast``, pushed by ``push``. A
 * craft's continuation is that same option, so one choice serves every turn
 * of a craft.
 * @param {number} battleId
 * @param {string} mageName
 * @param {{spell: string, trueCast?: boolean, push?: number, turns?: number}} choice
 * @param {keyof TURN_OUTCOMES} outcome
 * @returns {{seed: number, roll: {natural: number, target: number} | null,
 *     drained: number | null}} the seed; the interruption roll's total and
 *     target when a hit forced one; and what the first drain took
 */
function pinTheMagesTurns(
    battleId, mageName, { spell, trueCast = false, push = 0, turns = 1 }, outcome
) {
    return readDjangoJson(`${boardScript(battleId, mageName)}
from tarmar_engine.engine import DRAIN_REASON
def chosen_from(state):
    chosen = next((
        candidate for candidate in menu_of(state)
        if candidate.spell_key == ${JSON.stringify(spell)}
        and bool(candidate.true_cast) == ${trueCast ? 'True' : 'False'}
        and (candidate.push_mana or 0) == ${Number(push)}
        and not candidate.borrow
        and candidate.target_id == foe.combatant_id
    ), None)
    if chosen is None:
        raise SystemExit("the mage's menu offers no such option: " + ${JSON.stringify(JSON.stringify({ spell, trueCast, push }))})
    return chosen
turn_number = battle.turn_number
for seed in range(1, 500):
    battle.seed = seed
    battle.turn_number = turn_number
    trial = profile.load(battle.state_json)
    payloads = []
    for _turn in range(${Number(turns)}):
        payloads += play(trial, chosen_from(trial))
    if ${TURN_OUTCOMES[outcome]}:
        break
else:
    raise SystemExit("no seed under 500 gives the turns the spec needs")
roll = next((payload for payload in payloads if payload.get("craft_interrupted")), None)
drain = next((payload for payload in payloads if payload.get("reason") == DRAIN_REASON), None)
Battle.objects.filter(pk=battle.pk).update(seed=seed)
print("TURNS_SEED " + json.dumps({
    "seed": seed,
    "roll": None if roll is None else {"natural": roll["natural"], "target": roll["target"]},
    "drained": None if drain is None else drain["cost"],
}))
`, 'TURNS_SEED');
}

module.exports = {
    trueMagicFacts,
    seedATrueMage,
    claimWithCharacter,
    placeTheMage,
    pinTheMagesTurns,
};
