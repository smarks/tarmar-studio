# Dice Fidelity Audit

Issue #290 — every dice mechanic the reference documentation defines, traced
through three layers and verified at each seam:

- **doc→engine** — the code rolls exactly the documented spec (dice count and
  sides, modifier source, target, comparison direction, special faces).
- **engine→event** — the roll's record/payload (a `common.rolling.RollRecord`
  or the app's equivalent stored dict) carries the same specification,
  modifier, target and outcome the mechanic computed.
- **event→UI** — what the page renders is the recorded roll verbatim — faces,
  modifier, total, target — never a client-side recomputation.

Column values: **VERIFIED** (with the guarding test), **MISMATCH(#issue)**, or
**n/a** with the reason (no roll ever leaves the engine, no doc exists, no UI
displays it). The doc→engine guards parse the markdown wherever the page's
structure allows (the `test_rules_drift.py` / `test_rules_consistency.py`
pattern), so a doc edit fails a test rather than silently drifting; where a
page is prose, the documented values are pinned with a citation.

Sources audited: `reference/content/public-rules/combat/**` (including
`action-options/*` and the d20 spec proposal), `magic/*`,
`characters/*` and `characters/prior-experience/*`, `equipment/*`,
`dm-rules/magic-mechanics/*`, and the `dm-rules/gm-tools/*` generator pages.

New tests added by this audit: `characters/tests/test_dice_fidelity.py`
(creation tables, equipment tables, formula pins) and
`battle/tests/test_dice_fidelity.py` (engine roll-spec fidelity, event-payload
mirroring, spell-affinity guard). Everything else cited below already existed;
this audit verified it covers the seam it is cited for.

The sentences that state the numbers of sections E, F and G are built from the
same loaded rules as the rolls (issue #68), except those the prompt leaves for
stages 6 and 7 (see `docs/architecture.md`), so no prose can restate a number
the rules no longer hold: the sheet's help, its refusals and reasons, the
creation and edit pages' controls and formula cells, the workbench's aging and
survival notes, the extraction schema's default coin and the Characters
pane's Fatigue note (`characters/tests/test_sheet_prose_follows_the_rules.py`),
and the Games Master's prompt (`gm/test_prompt_follows_the_rules.py`). Each
test pins the default canon's text and seats invented rules; the browser test
`e2e/sheet-prose-rules.spec.js` reads the loaded rules through the Django shell
(`e2e/character-rules.js`) and holds the pages to them under both canons.

## A. d20 combat — attack-rolls.md, dex-adjustments.md, d20 spec §2–§8

| # | Mechanic (doc cite) | Dice / target / comparison | doc→engine | engine→event | event→UI |
|---|---|---|---|---|---|
| A1 | Attack roll (`attack-rolls.md:3-8`; spec §2) | 1d20 + to-hit bonus ≥ TN, roll-over | VERIFIED — `tarmar_rules.resolve_attack` via `test_combat.py` (`ToHitBonus`/`TargetNumber`/resolution tests), spec §6.1 surface locked; engine spec asserted in `battle/tests/test_dice_fidelity.py::EngineRollSpecTest` | VERIFIED — battle: `test_dice_fidelity.py::RollEventPayloadTest` (payload mirrors RollRecord); sheet: `test_attack_service.py` (die/total/TN in payload) | VERIFIED — battle log renders `event.message` verbatim (`battle.test.js` "roll event shows its message"); sheet panel prints payload only (`attack-panel.js` "computes nothing", `attack-panel.test.js`) |
| A2 | To-hit bonus: DEX `floor((DEX−10)/2)`, weapon skill +1/level uncapped, same-class half, Unusual none, STR-fit −1/point under (`attack-rolls.md:15-60`; spec §3) | modifiers on the d20 | VERIFIED — `test_combat.py` modifier tests; `test_rules_consistency.py::WeaponSkillRuleTest` parses the glossary ladder and `attack-rolls.md`'s skill row | VERIFIED — `test_attack_service.py` itemized `to_hit` breakdown | VERIFIED — `attack-panel.test.js` renders the breakdown rows from the payload |
| A3 | TN = matrix base + shield + defender dodge (min 0) (`attack-rolls.md:62-76`) | weapon-class × armour-tier | VERIFIED — `test_combat.py::SpecDriftGuardTests` (spec §6 table ↔ `MATRIX`), `test_rules_consistency.py::CombatMatrixRuleTest` (`attack-rolls.md` table ↔ `MATRIX`), `dodge_modifier` floor tested; the dodge the Game Master gives on the attack panel is held to the loaded canon's `defender_dodge` bounds (the default canon's pinned by `test_equipment_rules.py::DefaultDodgeBoundsTest`, derived from this page's minimum and the attribute ceiling; see G) | VERIFIED — `test_attack_service.py` `target_number` breakdown | VERIFIED — panel row (`attack-panel.test.js`); `e2e/attack-panel.spec.js` reads the loaded bounds and the input's limits |
| A4 | Shield TN bonuses: small/spike +1, large +2, tower +3, main-gauche +1 (`attack-rolls.md:75-76`) | added to TN | VERIFIED — `characters/tests/test_dice_fidelity.py::ShieldBonusDocTest` (doc line ↔ catalog `shield_bonus`) | VERIFIED — shield bonus looked up server-side, echoed in payload (`test_attack_service.py`) | VERIFIED — panel TN row |
| A5 | Natural 20: auto-hit, damage dice rolled twice; confirm at same TN/bonus → severe, dice three times + bleeding + Body; the damage modifier counts once either way; confirm naturals decide only confirmation (`attack-rolls.md:118-124`) | naturals on d20 | VERIFIED — `test_rules_consistency.py::CritFumbleRuleTest` (parses the page), `test_combat.py::CriticalHitTests`, `gm/tests.py` prompt multiplier guards; the modifier once since tarmar-engine 0.9.5 (#824): `battle/tests/test_combat_math.py::ExpectedDamageTest::test_crit_repetitions_roll_the_dice_twice_against_one_armour` (was 16, the modifier twice) and the engine's `test_battle_rules_pass.py::CriticalModifierOnceTest`. Flagged for Spencer in `tarmar_engine.house_rulings`: the page says the critical rolls the damage "dice" twice and the severe critical does "triple damage"; the engine reads both as dice-only. The sheet's attack panel still adds a catalog expression's own modifier per repetition (#875) | VERIFIED — battle `test_engine.py::test_natural_twenty_rolls_damage_twice` / `test_confirmed_severe_critical_reaches_body_and_bleeds`; sheet `test_attack_service.py` crit/confirm cases | VERIFIED — battle message; panel confirm row (`attack-panel.test.js`) |
| A6 | Natural 1: auto-miss + fumble, 1d6: 1–3 off-balance −2 / 4–5 drop / 6 stress, and a stressed weapon breaks on any second fumble (`attack-rolls.md:125-127`) | 1d6 subtable | VERIFIED — `CritFumbleRuleTest::test_fumble_bands_match_the_code_table` (parses page), `test_combat.py::FumbleTableTests`, gm prompt fumble guards. Since tarmar-engine 0.9.5 a dropped weapon lies in its hex and can be picked up (`q`, #780), and the stress belongs to the weapon and breaks it on any second fumble, not only a second 6 (#814): the engine's `test_battle_rules_pass.py::DroppedWeaponTest` and `::StressedWeaponTest` | VERIFIED — battle `test_engine.py` fumble tests (drop/off-balance/stress→break); sheet fumble payload | VERIFIED — battle message; panel fumble row |
| A7 | Off-balance = −2 to the next action, whatever it is (`attack-rolls.md:125`, `combat_math.OFF_BALANCE_PENALTY`) | flat modifier, once | VERIFIED — `test_engine.py::test_fumble_off_balance_penalizes_next_attack_once`; since tarmar-engine 0.9.5 (#811) the next action spends it whether it is an attack, a cast, an aim, an escape or a draw: the engine's `test_battle_rules_pass.py::OffBalanceNextActionTest` | VERIFIED — the next roll's modifier carries the −2 (same tests) | VERIFIED — message shows the reduced modifier |
| A8 | Damage = weapon dice + damage_mod − stops, floored 0; Hybrid: heavy classes vs Heavy tier apply `stops // 2` (`attack-rolls.md:129-142`; spec §8) | post-roll arithmetic | VERIFIED — `test_combat.py` damage tests; `test_attack_service.py::test_stops_come_off_the_summed_total_once` | VERIFIED — payload `damage` breakdown incl. `applied_stops`/`hybrid_bypass`; battle damage events | VERIFIED — panel armour row; battle damage message |
| A9 | Reaches Body: severe crit only (`attack-rolls.md:142`) | flag | VERIFIED — battle engine (`apply_damage` touches body only on severe, `test_engine.py`) and, since #293 (`b2089b5`), the sheet service too: `characters/attack.py:413` now reads `outcome["severe"] and final_damage > 0`, guarded by `test_attack_service.py::test_a_plain_hit_rolls_damage_once`, which asserts a plain hit does **not** reach Body | — | VERIFIED — panel shows the corrected flag verbatim |
| A10 | Situational: side +2 / rear +4; missile range bands (0–2 MH 0 … −1 per 2 MH); thrown −1/hex (`dex-adjustments.md`) | to-hit adjustments | VERIFIED — `battle/tests/test_rules_drift.py::DexAdjustmentsDriftGuard` (parses the tables). Thrown weapons are thrown (`f`) since tarmar-engine 0.9.5 and take the thrown table, −1 per hex, rather than the missile bands; the weapon lands in the target's hex (#812): the engine's `test_battle_rules_pass.py::ThrownWeaponTest` | VERIFIED — folded into the attack roll's recorded modifier (`test_dice_fidelity.py::EngineRollSpecTest`) | VERIFIED — battle message shows the folded modifier |
| A11 | Other situational rows (invisible −6, shadow −4/−6, two-weapon −4, sweeping −4, fire/body/broken ground −2, pole vs charge +2, prone crossbow +1, waiting +1/+2) (`dex-adjustments.md:9-38`) | to-hit adjustments | n/a — not modelled by any engine surface; the sheet panel's free-typed `situational` field and the GM narrate them (rules text loaded into the GM prompt). No code claims to implement them. | n/a | n/a (GM-supplied number displayed verbatim) |
| A12 | Dodge/Defend: +4 to your TN, Dodge vs missiles/thrown (disengaged), Defend vs melee (engaged) (`special-combat-situations.md:37-45`) | TN adjustment | VERIFIED — `test_rules_drift.py::SpecialSituationsDriftGuard` + `combat_math.attack_numbers` tests (`test_combat_math.py`) | VERIFIED — folded into recorded TN | VERIFIED — message TN |
| A13 | Rate of fire: bows 2 shots/turn at the Notes' adjDEX; crossbows every other / every 3rd turn, quicker at the Notes' adjDEX (`weapons.md` Missile Weapons Notes) | per-turn shot count | VERIFIED since tarmar-engine 0.9.5 (#781) — the rates are read off the Notes cell by `tarmar_engine.weapons.missile_rate_from_note`, which the engine's drift test runs over the page (`test_battle_rules_pass.py::RateOfFireTest`), and the studio seats each missile weapon through the same parser from its catalog `notes` (`battle/tests/test_services.py::SeatedFieldsTest::test_missile_rates_come_off_the_weapons_notes`). The reload belongs to the weapon, so a dropped and recovered crossbow is still unloaded | VERIFIED — each shot is its own attack roll event | VERIFIED — message |
| A14 | One Last Shot: a figure engaged with a missile weapon ready before it was engaged may fire once (`action-options.md` option l) | one attack roll | VERIFIED since tarmar-engine 0.9.5 (#776) — `l` is on the engaged menu until the shot is spent, one per engagement (coordinator's ruling; Spencer may overrule), and an archer charged before loosing takes it instead of defending: the engine's `test_battle_rules_pass.py::OneLastShotTest`; the studio's `test_engine.py::test_engaged_archer_defends_instead_of_shooting` now spends the shot first | VERIFIED — attack roll event | VERIFIED — message; the arena's turn menu offers `l` as a choice |

## B. Turn sequence — turn-sequence.md

| # | Mechanic | Dice / target | doc→engine | engine→event | event→UI |
|---|---|---|---|---|---|
| B1 | Initiative: 1d6 + adjDEX modifier per combatant, descending total, ties higher adjDEX then stable (`turn-sequence.md:5,12-18`) | 1d6 + modifier | VERIFIED — `test_rules_drift.py::TurnSequenceDriftGuard` (doc row pinned) + `test_engine.py::InitiativeTest` (own d6 each, modifier = `dex_modifier`, total orders, tie-break) + `test_dice_fidelity.py::EngineRollSpecTest::test_initiative_rolls_1d6_with_the_adjdex_modifier_and_no_target` | VERIFIED — `test_engine.py::test_initiative_roll_carries_the_adjdex_modifier` (faces/modifier/total in payload and message) | VERIFIED — `battle.test.js` renders roll messages verbatim |
| B2 | Renew Spells order DEX+INT+WIS high→low; mana = level or spell ends (`turn-sequence.md:6`) | no die | VERIFIED — `renewal_order_key` + `test_engine.py` renew tests; mana-cost drift guard (`SpellCatalogDriftGuard::test_mana_cost_equals_spell_level`) | VERIFIED — renew/lapse events | VERIFIED — messages |
| B3 | Phase-5 action order: adjDEX high→low (`turn-sequence.md:9`) | no die | VERIFIED — `phase_actions` sort; `test_engine.py` | n/a | n/a |

## C. Special combat situations & HTH — special-combat-situations.md, hand-to-hand-and-grappling.md

| # | Mechanic | Dice / target / comparison | doc→engine | engine→event | event→UI |
|---|---|---|---|---|---|
| C1 | Forced retreat: a figure that dealt physical hits (damage past the armour) and took none pushes into any clear hex, straight back first; only when no neighbour is clear, 3d6 ≤ DEX or fall, with the injury band on the save (`special-combat-situations.md:47-53`, `turn-sequence.md` row 6) | 3d6 ≤ DEX (roll-under) | VERIFIED — `TarmarForcedRetreat.blocked_save_dice/target`; `test_engine.py::test_no_retreat_hex_forces_dex_roll_and_fall` / `..._kept_feet_on_made_roll` (boxed in on the rim since the push goes in any direction, #779); spec asserted in `test_dice_fidelity.py::EngineRollSpecTest::test_blocked_retreat_saves_3d6_against_dex`. Physical hits only, never a spell (#813): `test_engine.py::ForcedRetreatTest` and the engine's `ForcedRetreatHitsTest`; the band on the save (#817): the engine's `InjuryPenaltyEverywhereTest::test_the_retreat_save_carries_the_band` | VERIFIED — retreat-save roll event carries spec/TN | VERIFIED — message |
| C2 | HTH disengage / struggle free: 4d6 ≤ effective DEX → stand + adjacent hex (`action-options.md:39`, `special-combat-situations.md:11-14`, `hand-to-hand-and-grappling.md:96-101`) | 4d6 ≤ DEX | VERIFIED — `test_rules_drift.py::GrappleDriftGuard::test_escape_roll_matches_the_page` (doc pin) + `grapple_struggle_free`, and outside a grapple `hth_disengage` (tarmar-engine #19); spec/TN asserted in `test_dice_fidelity.py::EngineRollSpecTest::test_escape_rolls_4d6_against_effective_dex` and `test_hth_disengage_rolls_4d6_against_effective_dex` | VERIFIED — escape roll event | VERIFIED — message |
| C3 | HTH +4 to hit, both sides (`action-options.md:37`, `hand-to-hand-and-grappling.md:16-18`) | flat +4 | VERIFIED — `GrappleDriftGuard::test_hth_to_hit_bonus_matches_the_page`; strike-back/squeeze via `resolve_attack` with `extra_situational` (`test_engine.py` grapple tests) | VERIFIED — folded into recorded modifier | VERIFIED — message |
| C4 | Grapple attempt: unarmed attack vs Flexible/Snare row, no weapon skill; nat 20 auto-grapple + stun, no confirm; nat 1 reworded fumble table (`hand-to-hand-and-grappling.md:44-67`) | 1d20 vs matrix row | VERIFIED — `GrappleDriftGuard::test_flexible_snare_tn_progression_matches_the_page`; `test_engine.py::test_natural_twenty_grapples_and_stuns_no_confirm_roll` + grapple fumble tests | VERIFIED — grapple events with attack_roll sequence | VERIFIED — message |
| C5 | Bare-handed damage by STR table (`special-combat-situations.md:27-35`) | 1d6±N / 2d6+1 / 3d6+1 bands | VERIFIED — `test_rules_drift.py::SpecialSituationsDriftGuard::test_bare_handed_damage_matches_special_combat_situations_md` (parses the two-column table ↔ `BARE_HANDED_DAMAGE_TABLE`) | VERIFIED — damage roll uses the band's expression (`test_engine.py` grapple/squeeze) | VERIFIED — message |
| C6 | Disengage option n / slower-enemy penalty = adjDEX difference (`special-combat-situations.md:5-9`) | modifier rule | VERIFIED since tarmar-engine 0.9.5 (#777) — a slower adjacent enemy that chose to strike the disengaging figure still strikes it, a hex away, at the adjDEX difference: the engine's `test_battle_rules_pass.py::DisengagePenaltyTest` | VERIFIED — the attack roll event carries the penalty in its modifier (same test) | VERIFIED — message shows the reduced modifier |
| C7 | Survival saves below −½ pool: 3d6 ≤ CON each turn, penalized past −full by the excess (`injury-thresholds-death.md:8-17`) | 3d6 ≤ CON − penalty (engine: penalty added to roll — arithmetically identical, and the event shows the modifier) | VERIFIED — `TarmarReactions` thresholds ↔ model semantics (`test_rules_consistency.py::InjuryThresholdRuleTest` guards model↔doc; `test_engine.py` save tests); spec/TN/modifier asserted in `test_dice_fidelity.py` | VERIFIED — survival roll event; fatal chain links the killing rolls | VERIFIED — message + `battle.test.js` fatal-chain rendering |
| C8 | −1/−2 "to all rolls" at half/low pools (`injury-thresholds-death.md:5-7`) | flat penalties | VERIFIED since #296 (`ca0f48d`, tarmar-engine v0.9.2) for the attack, casting, aim and escape rolls, via `profile.reactions.injury_penalty`, and since tarmar-engine 0.9.5 (#817) for initiative and the forced-retreat save too; guarded from the app by `battle/tests/test_dice_fidelity.py::InjuryRollPenaltyThroughTheAppTest` and in the engine by `test_battle_rules_pass.py::InjuryPenaltyEverywhereTest`. Survival saves are deliberately left alone — they carry their own penalty arithmetic (C7) | VERIFIED — the penalty is folded into the recorded roll modifier (same tests) | VERIFIED — sheet displays the bands; the battle message shows the reduced modifier |

## D. Magic — casting-spells.md, spell-descriptions.md, dm-rules/magic-mechanics/*

| # | Mechanic | Dice / target | doc→engine | engine→event | event→UI |
|---|---|---|---|---|---|
| D1 | Casting roll: 3d6 ≤ adjINT or adjWIS per spell (`casting-spells.md:5`, `mana-pool.md:7`) | 3d6 roll-under | VERIFIED for the mechanic — engine rolls `3d6` with TN = effective INT/WIS (`test_dice_fidelity.py::EngineRollSpecTest::test_casting_rolls_3d6_against_the_spell_attribute`; snapshot feeds effective attributes, `battle/services.py`) | VERIFIED — casting roll event (`test_engine.py` cast tests) | VERIFIED — message spells out `3d6 ≤ ATTR n` |
| D2 | Per-spell affinity column (`spell-descriptions.md` tables) | INT vs WIS | VERIFIED — every catalog spell goes through the doc-parse guard (`battle/tests/test_dice_fidelity.py::SpellAffinityDocTest::test_every_catalog_spell_casts_on_the_documented_attribute`). Shield (Protection) was the one exception (#291, `ca0f48d`): the doc won, `tarmar_engine/spells.py:85-92` now carries `attribute="WIS"`, and the guard's two-sided pin on the mismatch was retired with it | — | — |
| D3 | Mana cost = spell level, paid on a success and on the failing totals the canon's `casting_failure` block names; any other failure keeps it; renew or lapse (`casting-spells.md:3,11-13`; which failures spend the mana is DM-side, in the canon's `mana-pool.md` and its ruling) | no die | VERIFIED — `SpellCatalogDriftGuard`; `test_engine.py::CastingTest::test_failed_casting_roll_still_costs_mana` and `::test_an_ordinary_failed_cast_keeps_its_mana`, on invented totals (#816, tarmar-engine 0.9.5; the totals injected since tarmar-engine #46; coordinator's ruling, Spencer may overrule) | VERIFIED — cast/renew events carry mana_left | VERIFIED — message |
| D4 | Targeted spells: additional DEX roll (3d6 ≤ DEX) (`casting-spells.md:9`) | 3d6 ≤ DEX | VERIFIED — `test_engine.py::test_successful_targeted_spell_rolls_cast_aim_and_damage`; spec/TN in `test_dice_fidelity.py`. Dodging target → −4 on the caster's effective DEX is a documented engine re-mapping of the +4-TN dodge (`tarmar_engine/spells.py::DODGE_DEX_CHECK_PENALTY`), noted as adaptation, not doc | VERIFIED — aim roll event (`test_engine.py::test_dodging_target_penalizes_the_aim_roll`) | VERIFIED — message |
| D5 | Special casting rolls: the failing faces (`mana-pool.md`, dm) | special faces | VERIFIED — the canon's `casting_failure` block names the floor, the mana-losing totals and the Runaway total, and `battle/magic_rules.py` loads it into the engine's `CastingFailureRules` (tarmar-engine #46; `battle/tests/test_magic_rules.py`, whose probe checks the Tarmar canon's block against that canon's own expectations). A natural total at or above the floor fails however high the attribute: guarded by `battle/tests/test_dice_fidelity.py::CastingFumbleVerdictTest`, on the loaded canon's floor, and `battle/tests/test_magic_seating.py::CastingFailureTurnTest`, through the turn path. Deliberately **not** implemented, and not a drift row: the table's other negative effects, which need state the engine does not track (scoped in #825) | VERIFIED — the casting roll event carries the failing total | — |
| D6 | Spell damage/effect magnitudes (1d6 missile, 2d6 ball, 3d6 bolt, heal 1d6, fatigue 1d6+1, wound 1d6 to Body, Shield +1 TN, Blur −2) | engine data | n/a doc→engine — the rules name these spells but publish no combat numbers; magnitudes are declared engine adaptations (`tarmar_engine/spells.py` docstring) | VERIFIED — damage/heal rolls use the catalog expression (`test_dice_fidelity.py`, `test_engine.py`) | VERIFIED — messages |
| D7 | Mana Pool is accumulated Spell Points, not rolled at creation (`magic.md`, `mana-pool.md:3`) | no die | VERIFIED since #297 (`17597dd`) — the doc-side fix: `mana-pool.md`, `magic.md` and the magic-mechanics index dropped the secret 3d6 creation roll, and the pages now describe the implemented pool. Guarded by `characters/tests/test_rules_consistency.py::test_mana_pool_md_drops_the_secret_creation_roll`, `::test_magic_md_drops_the_secret_creation_roll`, `::test_magic_mechanics_index_drops_the_secret_summary` and `::test_free_spell_points_field_matches_the_documented_pool` | — | — |
| D8 | Push Control Roll and its outcomes (`push.md`, dm; its numbers stay in the canon) | 3d6 roll-under | n/a — no engine surface implements Push/Borrowing/Runaway/Counterspell; they are GM-table mechanics (rules text is loaded into the GM prompt for narration). Inventoried; nothing rolls them in code | n/a | n/a |
| D9 | Runaway anchor 3d6 ≤ WIS (`runaway.md`, public); a third party's intervention (`runaway.md`, dm; its numbers stay in the canon) | 3d6 roll-under | n/a — as D8 | n/a | n/a |
| D10 | Borrowing Debt Roll (`borrowing.md`, dm; its numbers stay in the canon) | 3d6 roll-under | n/a — as D8 | n/a | n/a |
| D11 | Counterspell opposed INT with a mana bonus (`counterspell.md`, dm; its numbers stay in the canon) | opposed 3d6 | n/a — as D8 | n/a | n/a |

## E. Character creation — attributes.md, wealth.md, location.md, provenance.md, derived-pools.md

Since issue #59 every number in this section is the loaded canon's, read from
its `character-rules.json` by `characters/rules.py`; the Mechanic column quotes
the public pages. The default canon's file is held to those pages by the drift
tests named below, the Tarmar canon's by its own expectations
(`characters/tests/test_rules.py`'s probe), and
`characters/tests/test_rules_seated.py` seats invented rules to prove each
caller reads the loaded file rather than a number of its own.

| # | Mechanic | Dice / target | doc→engine | engine→event | event→UI |
|---|---|---|---|---|---|
| E1 | Core attributes: 3d6 each, or 65 points across six, each 3–18 (`attributes.md:15`) | 3d6 ×6 | VERIFIED — `test_rules_consistency.py::PointBuyRuleTest` (the default canon's `attribute_roll` dice and `attributes.point_buy` ↔ the page, and the dice's range ↔ the buy's bounds, which the page says is what the dice produce); `test_rules_seated.py::RollsTest::test_the_attributes_are_rolled_on_the_canons_dice` (every attribute roll reads the loaded dice) | VERIFIED — `dice_log` records `<dice>: [faces] = n` per attribute, the dice being the canon's: `test_point_buy.py::test_roll_still_logs_the_3d6_chain` (creation's log), `test_services.py`'s roll-attributes note (`strength 3d6: [`), the seated test above under invented dice, and `e2e/character-create.spec.js` (each stored attribute equals its log total, on the loaded dice) | VERIFIED — sheet renders `character.dice_log` verbatim in a `<pre>` (`character_edit.html:742-745`) |
| E2 | Attribute modifier table 3→−5 … 18→+5 (`attributes.md:17-29`) | lookup | VERIFIED — `characters/tests/test_dice_fidelity.py::AttributeModifierDocTest` (parses the table ↔ the default canon's `attribute_modifier`, read through `calculate_attribute_modifier`); `test_skill_rules_seated.py` holds the function and the prompt's copy to the loaded table under invented rules; `gm/tests.py` guards the prompt's copy | n/a (no roll) | modifiers displayed from the same function |
| E3 | Appearance 1d6, 1 pretty / 6 ugly (`attributes.md:66`) | 1d6 | VERIFIED — `test_dice.py::test_appearance_and_weight_rolls_are_plain_d6` | VERIFIED — dice_log line | VERIFIED — dice_log `<pre>` |
| E4 | Height: 1d6 table 4'8"–6'4", reroll on 1/6 ±4" each (`attributes.md:68-77`) | exploding d6 (demon die) | VERIFIED — base table by `test_dice_fidelity.py::HeightWeightDocTest` (parses the table ↔ the default canon's `height.base_inches_by_face`, and the chain adjustments ↔ `inches_per_extra_one`/`inches_per_extra_six`), and the chain semantics since #295 (`f603664`): the **first** roll sets the base and each further 1 or 6 adjusts it, which is what the doc's table reads (the canon records the reading as a ruling for Spencer). Pinned by `test_dice.py::test_demon_die_to_height_inches`, whose docstring now states the rule | VERIFIED — dice_log shows the full explode chain (`test_dice.py::test_roll_demon_die_logged`) | VERIFIED — dice_log `<pre>` |
| E5 | Weight: choose 8–13 stones or 1d6; final = base + STR÷2 stones; stone = 14 lbs (`attributes.md:79-89`) | 1d6 | VERIFIED — `test_dice_fidelity.py::HeightWeightDocTest` (table ↔ the default canon's `weight.stones_by_face`, lbs = stones × `pounds_per_stone`, the STR divisor ↔ `strength_divisor`); `test_dice.py` weight tests (plain d6, STR rounded down) | VERIFIED — dice_log | VERIFIED — dice_log `<pre>` |
| E6 | Fatigue = CON + WIS + INT + max(DEX, STR) + 2d6 (`derived-pools.md:7`) | 2d6 component | VERIFIED — `test_dice_fidelity.py::DerivedPoolFormulaTest` (doc formula ↔ the default canon's `fatigue_roll` attributes, best-of and dice, and ↔ `Character.fatigue` through `FatigueRollRules.total`) | VERIFIED — `fatigue_roll` stored; dice_log line | VERIFIED — sheet fatigue field |
| E7 | Body = 2/3 × Fatigue, round up (`derived-pools.md:15`) | derived | VERIFIED — same test (↔ `Character.body`) | n/a | sheet body field |
| E8 | derived-pools.md's Body line names the confirmed severe critical | — | VERIFIED since #294 (`17597dd`) — the doc-side fix: the stale 3d6-era "Critical hits (roll of 3 or 4)" phrasing is gone and the page names the d20 severe-critical rule. Guarded by `characters/tests/test_rules_consistency.py::test_body_crit_line_matches_the_confirmed_natural_20_rule`, which fails if the old wording returns | — | — |
| E9 | Wealth: d100 → 0–15/16–70/71–95/96–100; Merchant coin = 100 + d100 (`wealth.md`) | 1d100 (+1d100) | VERIFIED — `test_dice_fidelity.py::WealthDocTest` (parses thresholds + fixed coin ↔ the default canon's `wealth` rows; the base-plus-dice coin via `starting_coin_for_wealth`) | VERIFIED — dice_log line spells `d100=n -> level, coin` (`test_dice.py::test_roll_wealth_logged`) | VERIFIED — dice_log `<pre>` |
| E10 | Location: d100 → 01–20/21–70/71–99/100; literacy 3d6 ≤ INT −0/−2/−4 (`location.md`) | 1d100 | VERIFIED — `test_dice_fidelity.py::LocationDocTest` (thresholds ↔ the default canon's `location.rows`); `test_rules_consistency.py::LocationEffectsRuleTest` (effects + literacy modifier + the `literacy_roll` dice + display form) | VERIFIED — dice_log line | VERIFIED — dice_log `<pre>` |
| E11 | Provenance: d100 class split 1–10/11–30/31–100 + subtables (`provenance.md`) | 1d100 ×2 | VERIFIED — `test_rules_consistency.py::ProvenanceRuleTest` (parses the class split and all three tables ↔ the default canon's `social_class`, `nobility`, `merchant` and `commoner` rows) | VERIFIED — dice_log line (`test_dice.py::test_roll_provenance_logged_all_classes`) | VERIFIED — dice_log `<pre>` |
| E12 | Mana pool wording at creation (`mana-pool.md:3`) | no die | VERIFIED since #297 (`17597dd`) — see D7 | — | — |

## F. Prior experience — survivability.md, skill-tracks.md, prior-experience.md, aging.md, skills-glossary.md

Since issue #70 every number in this section is the loaded canon's, read from
its `skill-rules.json` by `characters/skill_rules.py`: the tracks and their
d20 tables, the experience ladder, the skill cost multipliers, the survival
roll, the aging table, the attribute modifier table, and the skill and
attribute checks. The Mechanic column quotes the public pages. The default
canon's file is held to those pages by the drift tests named below, the
Tarmar canon's by its own expectations (`characters/tests/test_skill_rules.py`'s
probe), and `characters/tests/test_skill_rules_seated.py` seats invented rules
to prove each caller reads the loaded file rather than a number of its own.
The browser tests read the same file through the Django shell
(`e2e/skill-rules.js`) and type no rule number:
`e2e/character-edit.spec.js` (the workbench's tracks, survival line, aging
preview and a played year), `e2e/skill-ladder.spec.js` (the Arcane ladder),
`e2e/character-mechanics.spec.js` (the skill check's dice and natural success)
and `e2e/gm-game-master-records.spec.js` (a stated attribute check's range and
modifier limit), run under both canons.

| # | Mechanic | Dice / target | doc→engine | engine→event | event→UI |
|---|---|---|---|---|---|
| F1 | Survival: 3d6 + best attribute modifier ≥ track target; nat 18 always survives, nat 3 always dies (`survivability.md:3`) | 3d6 + mod, roll-over | VERIFIED — `test_rules_consistency.py::test_survivability_table_matches_track_data` (parses the table ↔ the default canon's `tracks`: target, XP, pay and the skill the pay rides on) + `test_survivability_states_the_survival_dice` (the canon's `survival_roll` dice and automatic totals ↔ the page); the roll reads the loaded dice and totals (`test_skill_rules_seated.py::SurvivalRollTest`, under invented rules), best-of-six in `characters/skills.py` (`test_skills.py`) | VERIFIED — `survival_check` dict stores dice/modifier/total/target/auto flags (`simulate_experience_years`) | VERIFIED — the server's `survival_roll` template filter renders the stored dict, its dice included, and the page prints the server's sentence (`test_experience_display.py`, `character-edit.test.js`); `e2e/character-edit.spec.js` reads the loaded dice and target off a played year |
| F2 | Best modifier = single highest of the six, refigured yearly (`survivability.md:21-43`) | modifier source | VERIFIED — `get_best_modifier` over all six; per-year recompute (`test_skills.py`, issue #222 tests) | VERIFIED — stored per-year modifier | VERIFIED — displayed from stored check |
| F3 | Track skill: 1d20 → position in 20-entry track table (`skill-tracks.md:3-27`) | 1d20 lookup | VERIFIED — `test_rules_consistency.py::test_skill_tracks_table_matches` (parses the full table ↔ the default canon's track `skills`); `test_skill_rules_seated.py::TracksTest` (the d20 reads the loaded tables) | VERIFIED — per-year `roll` stored | VERIFIED — "Year N: skill (rolled R)" line (template + JS mirror) |
| F4 | Repeat skill → +1 toward next level (`skill-tracks.md:29`) | no die | VERIFIED — point accumulation tests (`test_skills.py`) | VERIFIED — skills_gained | VERIFIED — sheet skills |
| F5 | XP/money per year; money = multiplier × max(1, relevant level) (`survivability.md` table + Money section) | no die | VERIFIED — table guard + money-floor logic in `simulate_experience_years` (`test_experience_workbench.py`); the XP, multiplier and pay skill are the loaded canon's (`test_skill_rules_seated.py::TracksTest`) | VERIFIED — per-year stored | VERIFIED — workbench log |
| F6 | XP → levels/skill points table (`prior-experience.md:16-33`) | no die | VERIFIED — `test_experience_levels_table_matches` (↔ the default canon's `experience_levels`); `test_skill_rules_seated.py::LadderAndCostTest` (the points and levels read the loaded ladder) | n/a | sheet |
| F7 | Aging: cumulative totals at 32/36/…/68, formula beyond (`aging.md`) | no die | VERIFIED — `test_aging_table_matches` (↔ the default canon's `aging`, its row order and first stage) + escalation-prose tests; `test_skill_rules_seated.py::AgingAndModifierTest` (the lookups read the loaded table) | VERIFIED — run's aging dict | VERIFIED — workbench |
| F8 | Skill check: 2d6 + attribute modifier + level, GM difficulty; nat 12 / nat 2 (`skills-glossary.md:12-14`); attribute check with no skill 3d6 ≤ attribute (`attack-rolls.md:13-14`) | 2d6 roll-over; 3d6 roll-under | VERIFIED since #756 and #911 — `test_skill_checks.py::GlossaryDriftTest` (parses the glossary's Governs column, the check's dice, both naturals and the focused-field bonus, and attack-rolls.md's attribute check dice ↔ the default canon's `skill_check` and `attribute_check`); the checks, the plan reader, the rules lookups and the sheet read the loaded dice, naturals, governing pairs and modifier limit (`test_skill_rules_seated.py::SkillAndAttributeChecksTest`, under invented rules) | VERIFIED — `SkillCheck.line` / `AttributeCheck.line` kept in the history (`test_skill_checks.py::test_a_player_rolls_one_check_and_it_is_in_the_history`) | VERIFIED — `static/js/mechanics.js` prints the server's check, dice included (`mechanics.test.js`); `e2e/character-mechanics.spec.js` (#756, and #70: the loaded dice, and the natural success counted a success); `e2e/gm-game-master-records.spec.js` (a stated attribute check held to the loaded dice's range and modifier limit) |
| F9 | Literacy check 3d6 ≤ INT − location penalty (`location.md:20`) | 3d6 roll-under | VERIFIED as a *recorded rule*, not a roll — creation stores the modifier and the sheet prints the check form (`LocationEffectsRuleTest::test_literacy_check_display_matches_the_published_form`); no code rolls it (GM does) | n/a | VERIFIED — display form guarded |

## G. Equipment tables and movement — weapons.md, armor-and-shields.md, gear.md, movement.md, skills-glossary.md

Since issue #75 every item in this section is the loaded canon's catalog, read
from its `equipment.json`, and every number its equipment rules, read from its
`equipment-rules.json`, both by `characters/equipment_rules.py`: the items and
their fields, the encumbrance levels, the armour restriction codes, the gaits
and their base distances, the Missile classes (which Archery covers and the
battle seats as missile weapons) and the bounds on a defender's dodge. The
Mechanic column quotes the public pages. The default
canon's files are held to those pages by the drift tests named below, the
Tarmar canon's by its own expectations
(`characters/tests/test_equipment_rules.py::TarmarEquipmentFilesTest`), and
`characters/tests/test_catalog_follows_the_canon.py` and
`characters/tests/test_equipment_rules_follow_the_canon.py` seat an invented
catalog and invented rules to prove each caller reads the loaded files rather
than an item or a number of its own. The browser tests read the same files
through the Django shell (`e2e/equipment-rules.js`) and type no catalog value
or rule number: `e2e/character-sheet-repaints.spec.js` (the picker's groups and
items, the load, the level, its penalty and the gaits, and worn armour's
restriction), `e2e/attack-panel.spec.js` (a weapon's class and damage, the
shield bonus, the dodge bounds and Archery's Missile classes) and
`e2e/character-mechanics.spec.js` (the prices), run under both canons. They
choose their items and scenarios from the loaded files and hold the page to
what the server computes for the character, restating none of its
arithmetic.

| # | Mechanic | doc→engine | engine→event | event→UI |
|---|---|---|---|---|
| G1 | Weapon damage expressions + STR requirements, every row (`weapons.md`) | VERIFIED — `characters/tests/test_dice_fidelity.py::WeaponTableDocTest` (parses every weapon table ↔ the default canon's catalog `damage`/`str_req`, the entries whose damage the Game Master decides included verbatim) | VERIFIED — attack service rolls the catalog expression and refuses the unrollable five (`test_attack_service.py`) | VERIFIED — panel shows expression + rolls |
| G2 | Armour stops + DEX penalties (`armor-and-shields.md`) | VERIFIED — `test_dice_fidelity.py::ArmourShieldTableDocTest` (parses the table ↔ the default canon's catalog `stops`/`dex_penalty`, shields included) | VERIFIED — stops echo in damage payloads | VERIFIED — panel armour row |
| G3 | Weapon-class / armour-tier / alt-class assignments (spec §5/§6) | VERIFIED — `test_combat.py` catalog-integration tests + spec drift guard; every loaded weapon class and armour tier is one the combat matrix has (`characters.E003`, `test_equipment_rules.py::CatalogClassesTest`) | — | — |
| G4 | Prices (`gear.md`, `weapons.md`, `armor-and-shields.md`) | VERIFIED — `test_rules_consistency.py`'s `EquipmentCostRuleTest`, `MissingCatalogEntriesTest`, `ToolPriceRangeRuleTest` and `CrossbowRateOfFireRuleTest` (the pages ↔ the default canon's catalog) | VERIFIED — the purse moves by the loaded price (`characters/tests/test_purse.py`) | VERIFIED — `e2e/character-mechanics.spec.js` (#757, on the loaded prices) |
| G5 | Encumbrance: each level's band as a multiple of STR, its movement penalty, the gaits it forbids and Overloaded's "Walk only, 1 hex max" (`movement.md` Encumbrance table) | VERIFIED — `test_rules_consistency.py::EncumbranceTableRuleTest` (parses the table ↔ the default canon's levels, in order; the page's "—" penalty is the ruling `overloaded_movement_penalty`) | VERIFIED — `movement_response` carries the level, its wording, its penalty, the restriction label, the gaits and the carrying capacity as the model computes them (`characters/tests/test_sheet_repaints.py::AttributeEditMovesMovementTest`); the model reads the loaded levels (`test_equipment_rules_follow_the_canon.py::TheSheetFollowsTheSeatedRulesTest`, under invented levels) | VERIFIED — the sheet's load bar and gait rows (`character-edit.test.js`); `e2e/character-sheet-repaints.spec.js` |
| G6 | Gaits: Sprint/Run/Jog/Walk at 18/12/7/4 + the movement modifier, and the worked example (`movement.md` gait table) | VERIFIED — `test_rules_consistency.py::MovementGaitRuleTest` (the table's order and base distances ↔ the default canon's `gaits`, and the worked example through the model) | VERIFIED — the battle seats the canon's gaits by place onto the engine's four (`test_equipment_rules_follow_the_canon.py::TheSeatAndTheExportFollowTheSeatedRulesTest::test_the_battle_seats_the_canon_s_gaits_by_place`); `battle.E003` requires four, and a run above 0 (`ThreeGaitCanonCheckTest`, out of process) | VERIFIED — the sheet's gait rows; `e2e/character-sheet-repaints.spec.js` |
| G7 | Armour movement restrictions, "Cannot Sprint" / "Cannot Run or Sprint" (`armor-and-shields.md`) | VERIFIED — `test_rules_consistency.py::ArmourRestrictionWordingRuleTest` (each armour's column ↔ its catalog code's gaits in the default canon's `armour_restriction_codes`, as the sheet words them) | n/a | VERIFIED — the sheet's restriction label; `e2e/character-sheet-repaints.spec.js` |
| G8 | Archery covers "both Missile classes" (`skills-glossary.md`) | VERIFIED — `test_rules_consistency.py::MissileClassesRuleTest` (the glossary's Missile classes ↔ the default canon's `missile_classes`) | VERIFIED — `resolve_weapon_skill`'s rule names it (`test_equipment_rules_follow_the_canon.py`) | VERIFIED — the attack panel's Skill row; `e2e/attack-panel.spec.js` |

## H. GM tools & generators — dm-rules/gm-tools/*

| # | Mechanic | doc→engine | engine→event | event→UI |
|---|---|---|---|---|
| H1 | Dungeons & Ruins tables (d8/d6/d8/d10/d12/d6/d10/d6) (`dungeons-ruins.md`) | VERIFIED **by construction** — `ruins/tables.py` parses the markdown itself at call time; `ruins/tests/test_tables.py::DriftGuardTests` asserts the page still publishes exactly the expected, fully-numbered tables | VERIFIED — every table roll keeps its full `RollRecord` (`ruins/generation.py::TableRoll`, `test_generation.py`) | VERIFIED — generator page shows `dN → face` per roll (`ruins/tests/test_views.py` asserts the rendered die/face) |
| H2 | Adventure hooks: 1dN uniform over the level-banded hooks (`low-experience-hooks.md`) | VERIFIED by construction — `ruins/hooks.py` parses the page; band drift guard in `test_hooks.py` | VERIFIED — `TableRoll` kept (`test_hook_generation.py`) | VERIFIED — `test_hook_views.py` asserts die → face rendering |
| H3 | GM chat `/roll` (any NdS±M) | VERIFIED — one parser (`characters.dice` → `common.rolling` → `tarmar_engine.dice`), `gm/tests.py` roll tests | VERIFIED — server returns the authoritative faces/total in the done frame (`gm/views.py`) | VERIFIED — `chat.js` renders `🎲 expr: [faces] = total` from the frame, textContent only (`chat.test.js`) |
| H3b | GM chat dice the **GM itself** asks for (issues #363, #590): a turn's plan asks for a `roll` action (`NdS±M`, a count, a purpose) and the server resolves it before the narration is written | VERIFIED — the same one parser and roll service as H3, reached through `gm.engine.roll_dice_record` → `characters.dice.roll_expression_record` → the module-default `common.rolling.Roller`; no second RNG exists on this path, so a seeded session replays (`gm/test_gm_rolls.py::ResolveRollRequestTest`) | VERIFIED — the whole `RollRecord` is kept in the frame payload and filed into the session as a hidden `ROLL_KIND` chip entry, and each roll is a `GMEvent` on the turn (`gm/test_gm_rolls.py::RollLineTest`, `GMRollTurnTest`). An expression the roll service refuses produces an error, never a number | VERIFIED — the chip text is built server-side (`gm.roll_service.dice_line`) and rendered verbatim as `textContent` both live (`chat.test.js`, issue #363 case) and on reload (`gm/test_gm_rolls.py::GMRollTurnTest`); the page formats and computes nothing |
| H4 | Hand-rolled GM tables with no generator (hook-introduction, job, NPC, faction, urban-intrigue, trade, scenario-seeds d6/d8/d10/d12 tables) | n/a — doc-only; no code rolls them (the ruin/hook generators are the only gm-tools with engines). Nothing to drift | n/a | n/a |
| H5 | Wilderness encounters (d20 terrain + d10 subtables) | n/a for this audit — the tables transcribe the vault's Pillars campaign notes, not `reference/content` (`encounters/generation.py` docstring); shape-guarded by `encounters/tests/test_generation.py` | VERIFIED — RollRecords kept | encounter page shows rolls |

## I. Classic (Melee) profile

The classic 3d6 roll-under battle mode (`tarmar_engine.classic`, reached via
`battle/classic_adaptation.py`) — including its 4d6 dodge/defend rolls and
special-roll tables — derives from the SJG *Melee/Wizard* rulebooks, which are
deliberately **not** reproduced in `reference/content` (the unification plan's
copyright quarantine). With no reference doc to audit against, its doc→engine
column is n/a here; its mechanics are guarded inside the tarmar-engine package's
own test suite, and this repo's `battle/tests/test_classic_adaptation.py` and
`test_hexarena_equivalence.py` guard the adaptation seam. The **demon die**
(exploding d6) used by character-creation height is audited at E4.

## Summary

- **Mechanics inventoried:** 66 rows (A1–A14, B1–B3, C1–C8, D1–D11, E1–E12,
  F1–F9, G1–G3, H1–H5 including H3b), plus the classic-profile note. The
  audit's own summary said 59; the rows were recounted on 2026-09-25 (#657),
  and A13 (rate of fire) and A14 (One Last Shot) were added with the
  tarmar-engine 0.9.5 pin, which also moved C6 from n/a to VERIFIED.
- **doc→engine:** 58 VERIFIED, 0 MISMATCH, 8 n/a (documented mechanics with
  no code surface — GM-adjudicated tables and checks — or no doc, for the
  copyright-quarantined classic profile). The seven mismatches the audit
  filed are all fixed and closed; see the table below for which side moved
  and what guards each row now.
- **engine→event:** every implemented mechanic VERIFIED — battle rolls flow
  through `RollRecord` into event payloads mirrored field-for-field
  (`battle/tests/test_dice_fidelity.py::RollEventPayloadTest`); creation rolls
  into `dice_log`; experience rolls into `survival_check` dicts; sheet attacks
  into the itemized service payload; generator rolls into `TableRoll`s.
- **event→UI:** every displayed mechanic VERIFIED — battle log and GM chat
  render server-built strings verbatim; the attack panel, workbench log and
  generator pages print recorded values with no client recomputation
  (`attack-panel.js` and `battle.js` are payload-only by design and by test).
- **Fixed inline in the audit PR:** nothing — every discrepancy found was a
  rules-side or semantics question and was filed rather than fixed, per the
  audit's ground rules.

## What happened to the seven mismatches

The audit closed with 43 VERIFIED and 7 MISMATCH. All seven were fixed
within days of the audit landing, and this page was brought back into line
with the code on 2026-09-25 (#657) — until then it told a reader the
opposite of the truth for those rows, which is the worst thing a document
whose job is "does the code roll what the rules say" can do.

| Issue | Row(s) | Closed by | Which side moved |
|---|---|---|---|
| #291 Shield affinity | D2 | `ca0f48d` (PR #317) | engine — Shield casts on WIS |
| #292 casting specials | D5 | `ca0f48d` (PR #317) | engine — a natural total at or above the fumble floor fails whatever the attribute (the floor is the canon's since tarmar-engine #46) |
| #293 reaches-Body flag | A9 | `b2089b5` (PR #316) | code — severe criticals only |
| #294 stale crit wording | E8 | `17597dd` (PR #313) | doc — names the d20 severe critical |
| #295 height demon die | E4 | `f603664` (PR #314) | code — the first roll is the base |
| #296 injury roll penalties | C8 | `ca0f48d` (PR #317) | engine — the band penalty reaches the rolls |
| #297 mana-pool wording | D7, E12 | `17597dd` (PR #313) | doc — the pool is accumulated Spell Points |

Three of them (#291, #292, #296) were engine-side and arrived together with
the tarmar-engine v0.9.2 pin. Every row above now names the test that guards
it, so this table is a history rather than a second source of truth: if one
of these regresses, a test fails before anyone reads this page.
