# d20 Combat Resolution — Design Spec (Proposal)

> **Status:** Proposal / draft. The live combat system is still 3d6 roll-under
> (`reference/content/public-rules/combat/`). Nothing here is wired into the GM
> prompts yet. This document is the intended **single source of truth** for the
> d20 conversion; if adopted, `gm/services.py`, `cli/Modelfile`, and the
> public-rules files should quote/reference this, not paraphrase it.

## 1. Goals

1. **Roll-over, high-is-good.** A natural 20 is the best possible result, a
   natural 1 the worst — the D&D feel players expect.
2. **DEX moves the modifiers; a weapon × armour matrix sets the actual
   difficulty of landing a blow.** Two separate number sources, as designed.
3. **Hard to hit anything without skill or bonuses.** A raw d20 with no
   modifiers should miss far more than it lands against any armoured target.
   The harder-to-wield weapons should punish the unskilled hardest.
4. **Armour becomes a real decision.** Each armour tier invites a specific
   counter-weapon. No universally-best weapon.

## 2. Core resolution

```
to-hit total = d20
             + DEX modifier        (attacker's aim)
             + STR-fit modifier    (wieldiness; penalty if under str_req)
             + weapon skill         (the main "bonus" players invest in)
             + situational mods     (flanking, prone, range — re-signed from the
                                     existing DEX-adjustment table)

HIT if   to-hit total ≥ Target Number (TN)

TN = matrix_base(weapon_class, armour_tier)   ← the weapon-vs-armour table
   + shield_bonus
   + defender's DEX-dodge modifier
```

- **Natural 20** → auto-hit regardless of TN, and a critical (see §7).
- **Natural 1** → auto-miss, and a fumble (see §7).

DEX never touches the TN's base difficulty — it only feeds the attacker's aim
and the defender's dodge. The base difficulty is *entirely* the weapon-vs-armour
interaction. That's the split you asked for.

## 3. Modifier formulas (tune knobs)

All of these are deliberately stingy so that an unskilled, unbonused attacker
sits well below the TNs in §6.

| Modifier | Formula | Notes |
|---|---|---|
| **DEX modifier** | `floor((effective_DEX − 10) / 2)` | DEX 10 → +0, 14 → +2, 18 → +4. Uses *effective* DEX, so armour's `dex_penalty` already drags down a heavily-armoured attacker's aim. Knob: switch to `/3` to make it even harder. |
| **STR-fit** | meet `str_req` → +0; otherwise **−1 to hit per point of STR shortfall** (see §3.1) | Strength buys *access*, not accuracy — exceeding the requirement gives no bonus. |
| **Weapon skill** | **+1 per skill level**, no cap, with the weapon the skill was taken for; **floor(level / 2)** with another weapon of the same class; +0 with any other class | The primary lever. Turns "flailing" into "connecting." Where character investment lives. |
| **Defender DEX-dodge** | `floor((defender_effective_DEX − 10) / 2)`, min 0 | Added to TN. A nimble *unarmoured* duelist is still hard to hit; a knight in plate dodges little (armour cut his effective DEX) but leans on the matrix base instead. |
| **Situational** | port the existing DEX-adjustment table | Re-sign for roll-over: what *lowered* the roll-under target now *adds* to the attacker's d20 (flank, prone, blind target, point-blank) or *adds to TN* (cover, target defending, long range). |

### 3.1 Under-strength wielding rule

> **You can always swing a weapon you're not strong enough for — you just do it
> badly.** If your **effective STR is below the weapon's `str_req`, you take a
> to-hit penalty equal to the shortfall**: one point of penalty for every point
> you're under.
>
> `STR-fit penalty = min(0, effective_STR − str_req)`
>
> Examples: STR 12 with a Broadsword (`str_req` 12) → no penalty. STR 12 with a
> Great Hammer (`str_req` 14) → **−2 to hit**. STR 10 with a Battle Axe
> (`str_req` 15) → **−5 to hit**.

The penalty hits **to-hit only**, and it stacks on top of everything else (the
high matrix base, the weapon being two-handed, armour's DEX drag). That's the
whole reason a too-heavy weapon feels like a bad idea rather than an impossible
one: a desperate weakling *can* grab the fallen knight's great hammer, but
they'll flail with it until they miss. Knob: bump to −2/point for a harsher game
(§11).

**Why "hard without bonuses" falls out of this:** a green fighter (DEX 10, no
skill, meets str_req) attacks with a flat `d20` vs a base TN of 13–22. Even
against an *unarmoured* foe at TN 11–14 he's at 35–50%; against mail (TN 16+) he
needs a 16+ on the die — under 25%. Only skill (+1 per level), a good DEX, and
position pull him into reliable-hit territory.

## 4. Weapon classes

Seven classes, defined by **how the attack motion defeats (or fails against)
armour** — not by weapon shape. This is the axis the matrix cares about.

| Class | Character | Anti-armour behaviour |
|---|---|---|
| **Piercing** | light, quick points | trivial vs soft, near-useless vs plate (no leverage to punch through) |
| **Striking** | 1-handed swung cut/blunt | the "even across armour" middle — your broadsword class |
| **Thrusting** | reach/braced points, 1–2H light | concentrates force on a point; holds up vs medium, fades vs heavy |
| **Heavy Striking** | 2-handed crush/cleave | high floor everywhere (unwieldy), but momentum defeats plate — the anti-armour answer *if* you have the skill + STR |
| **Heavy Thrusting** | braced/mounted polearms & lances | leverage + momentum punch through the heaviest armour |
| **Missile — Bows** | drawn bows, slings, thrown | fade hard vs heavy armour |
| **Missile — Crossbows** | crossbows | mechanical power; flat vs armour, punches plate |
| **Flexible / Snare** | whip, net, lasso, bola | control weapons; poor vs anything rigid, GM-adjudicated |

(Flexible/Snare folded under Missile for the matrix where thrown, otherwise its
own row.)

## 5. Weapon → class assignments (all 41)

| Class | Weapons (`item_id`) |
|---|---|
| **Piercing** | `dagger`, `rapier` |
| **Striking** | `saber`, `shortsword`, `broadsword`, `bastard_sword_1h`, `club`, `hatchet`, `hammer`, `mace`, `small_ax`, `war_ax`, `morningstar`, `quarterstaff`, `cestus`, `nunchaku`, `torch_weapon` |
| **Thrusting** | `spear_1h`, `spear_2h`, `trident`, `javelin` (in melee) |
| **Heavy Striking** | `bastard_sword_2h`, `two_handed_sword`, `great_sword`, `great_hammer`, `battle_axe` |
| **Heavy Thrusting** | `halberd`, `pike`, `pike_axe`, `cavalry_lance` |
| **Missile — Bows** | `small_bow`, `horse_bow`, `longbow`, `sling`, `thrown_rock`, `javelin` (thrown), `boomerang` |
| **Missile — Crossbows** | `light_crossbow`, `heavy_crossbow` |
| **Flexible / Snare** | `whip`, `net`, `lasso`, `bola` |

`arrows_20` / `quarrels_20` are ammunition, not weapons — no class.
`javelin` carries two classes (Thrusting in melee, Bows-tier when thrown); the
GM picks by how it's used.

## 6. The matrix — base Target Number

Armour tiers map to your real catalog by `stops`:

| Tier | Catalog items (`stops`) |
|---|---|
| **None** | unarmoured (0) |
| **Light** | `cloth_armor` (1), `leather_armor` (2) |
| **Medium** | `chainmail` (3) |
| **Heavy** | `half_plate` (4), `plate_armor` (5), `fine_plate` (6) |

**Base Target Number (no modifiers applied):**

| Class | None | Light | Medium | Heavy |
|---|---|---|---|---|
| **Piercing** | 11 | 14 | 18 | 22 |
| **Striking** | 13 | 14 | 16 | 18 |
| **Thrusting** | 12 | 14 | 16 | 19 |
| **Heavy Striking** | 14 | 14 | 15 | 16 |
| **Heavy Thrusting** | 14 | 14 | 15 | 15 |
| **Missile — Bows** | 12 | 14 | 17 | 20 |
| **Missile — Crossbows** | 13 | 14 | 15 | 16 |
| **Flexible / Snare** | 13 | 16 | 19 | 22 |

Reading the design into the numbers:

- **Nothing is a gimme.** Every base ≥ 11, so even bare-skinned a no-bonus
  attacker is at best a coin flip. "Hard without skill or bonuses" — done.
- **TN 21–22 means you *cannot* connect on a raw die.** A dagger vs full plate
  (22) literally requires a bonus to be possible — you must be skilled, find a
  gap (situational), or roll a natural 20. Exactly right for a dagger on plate.
- **Piercing** is the steep diagonal: 11 → 22. Devastatingly easy on the soft,
  futile on the hard.
- **Striking** (broadsword) is the flat middle (13 → 18): doesn't much care what
  you're wearing.
- **Heavy Striking / Heavy Thrusting** have the **highest floor** (14
  everywhere) — they punish the unskilled hardest, per your ask — but they
  barely degrade against heavy armour (16 / 15). With the skill *and* the STR to
  wield them, they're the plate-cracking answer. Without it: base 14 **plus** the
  §3.1 STR-fit penalty is a near-guaranteed whiff.
- **Crossbows** stay flat and punch plate (16); **bows** fade hard (20).

**Shield bonus (added to TN):** `small_shield`/`spike_shield` +1,
`large_shield` +2, `tower_shield` +3, `main_gauche` +1 (parry).

### 6.1 Reference: the hit-chance surface

Exact hit probabilities at a clean baseline — attacker meets `str_req` (STR-fit
+0), effective DEX 10 (aim +0), no shield, average defender (dodge +0). These
are the **pure matrix + skill** floor; DEX, position, and the §3.1 penalty shift
a real attacker off them. The grids below step by two skill levels, a flat
+10% each (a single d20 point is worth 5%). Generated from `characters.combat`
and locked by `test_combat.py`.

**No skill (+0):**

| class | None | Light | Medium | Heavy |
|---|---|---|---|---|
| Piercing | 50% | 35% | 15% | 5% |
| Striking | 40% | 35% | 25% | 15% |
| Thrusting | 45% | 35% | 25% | 10% |
| Heavy Striking | 35% | 35% | 30% | 25% |
| Heavy Thrusting | 35% | 35% | 30% | 30% |
| Missile — Bows | 45% | 35% | 20% | 5% |
| Missile — Crossbows | 40% | 35% | 30% | 25% |
| Flexible / Snare | 40% | 25% | 10% | 5% |

**Skill level 2 (+2):**

| class | None | Light | Medium | Heavy |
|---|---|---|---|---|
| Piercing | 60% | 45% | 25% | 5% |
| Striking | 50% | 45% | 35% | 25% |
| Thrusting | 55% | 45% | 35% | 20% |
| Heavy Striking | 45% | 45% | 40% | 35% |
| Heavy Thrusting | 45% | 45% | 40% | 40% |
| Missile — Bows | 55% | 45% | 30% | 15% |
| Missile — Crossbows | 50% | 45% | 40% | 35% |
| Flexible / Snare | 50% | 35% | 20% | 5% |

**Skill level 4 (+4):**

| class | None | Light | Medium | Heavy |
|---|---|---|---|---|
| Piercing | 70% | 55% | 35% | 15% |
| Striking | 60% | 55% | 45% | 35% |
| Thrusting | 65% | 55% | 45% | 30% |
| Heavy Striking | 55% | 55% | 50% | 45% |
| Heavy Thrusting | 55% | 55% | 50% | 50% |
| Missile — Bows | 65% | 55% | 40% | 25% |
| Missile — Crossbows | 60% | 55% | 50% | 45% |
| Flexible / Snare | 60% | 45% | 30% | 15% |

**Skill level 6 (+6):**

| class | None | Light | Medium | Heavy |
|---|---|---|---|---|
| Piercing | 80% | 65% | 45% | 25% |
| Striking | 70% | 65% | 55% | 45% |
| Thrusting | 75% | 65% | 55% | 40% |
| Heavy Striking | 65% | 65% | 60% | 55% |
| Heavy Thrusting | 65% | 65% | 60% | 60% |
| Missile — Bows | 75% | 65% | 50% | 35% |
| Missile — Crossbows | 70% | 65% | 60% | 55% |
| Flexible / Snare | 70% | 55% | 40% | 25% |

## 7. Critical hits & fumbles

Preserves the *flavour* of the old 3d6 crit tables (triple damage, bleeding,
dropped/broken weapons) while moving them onto the d20's natural extremes — and
keeps the nastiest results rare so combat doesn't become a meat-grinder.

**Natural 20 — auto-hit + critical:**
- Damage dice rolled **twice** (not max — keeps variance).
- **Confirm for a severe crit:** immediately roll a *second* d20 to-hit against
  the same TN. If it also hits → **triple damage + bleeding**, and the blow
  reaches **Body** as well as Fatigue (the old "roll of 3/4" result). This
  layering keeps severe crits roughly as rare as the bell curve made them, since
  they now require a 20 *and* a confirm.

**Natural 1 — auto-miss + fumble.** Roll 1d6:
1–3 off-balance (−2 to your next action) · 4–5 drop weapon · 6 weapon takes
stress (breaks on a second fumble). Mirrors the old 17/18 drop-/break-weapon
outcomes.

## 8. Armour's two jobs — CHOSEN: Hybrid

Armour carries `dex_penalty` (a cost) and `stops` (damage reduction), and this
spec adds a third effect (harder to hit via the matrix). To stop all three from
stacking into an unkillable knight, the **adopted rule is the Hybrid**:

> **Armour both raises the attacker's Target Number (the matrix) AND, on a hit,
> its `stops` still subtract from damage — EXCEPT a Heavy Striking / Heavy
> Thrusting weapon against a Heavy-armour target ignores half the `stops`**
> (`stops // 2` applied), because impact carries through plate.

```
damage = weapon dice + damage_mod − applied_stops   (floored at 0)
applied_stops = stops // 2  if  weapon ∈ {Heavy Striking, Heavy Thrusting} and tier == Heavy
              = stops       otherwise
```

Damage applies to Fatigue first; a severe crit reaches Body. Implemented as
`characters.combat.damage_after_armour` and tested in `test_combat.py`.

The two alternatives that were rejected: **(a) Matrix-only** — drop/soften
`stops` so armour's whole job is the to-hit matrix; cleaner but armour feels
less tangible. **(b) Keep both at full strength** — plate dodges *and* fully
soaks; strongest for "armour matters" but risks armoured-vs-armoured slogs. The
Hybrid keeps `stops` (per the goal of making armour appealing) while letting the
designated anti-armour weapons still crack plate. Tuning dial if needed: how
much `stops` the heavy weapons bypass (`// 2` → more or less).

## 9. Worked examples

**Green recruit, dagger, vs an unarmoured thug.** DEX 10 (+0), untrained (+0),
meets str_req. TN = 11 (Piercing/None) + 0 dodge = 11. Needs 11+ on the die →
**50%.** Even the easiest case is a coin flip without skill.

**Same recruit, dagger, vs a knight in plate.** TN = 22 + dodge ~0 = 22.
`d20 + 0` tops out at 20 → **impossible** except a natural 20. The dagger is the
wrong tool; he needs to find a gap (situational +) or a different weapon.

**Sergeant, broadsword (Striking, skill level 4 = +4, DEX 14 +2), vs chainmail
foe (DEX 12, dodge +1).** TN = 16 + 1 = 17. `d20 + 4 + 2 = d20 + 6` ≥ 17 → needs
11+ → **50%.** Skill + DEX turns a TN-17 wall into a fair fight.

**Veteran, great hammer (Heavy Striking, skill level 6 = +6, STR 16 meets req 14, DEX 12
+1), vs the same plated knight (Heavy tier).** TN = 16 + dodge 0 = 16.
`d20 + 6 + 1 = d20 + 7` ≥ 16 → needs 9+ → **60%**, *and* under the Hybrid §8 rule
ignores half the plate's `stops`. The plate-cracker works — but only because he
has the skill *and* the STR. A weakling (STR 11 vs the hammer's `str_req` 14 →
−3 STR-fit), untrained, attacking the same knight: `d20 + 0 + 1 − 3` ≥ 16 →
needs an 18+ on the die → **15%**, basically a flail-and-pray. Same weapon, same
target — the difference is entirely strength and skill. "Especially hard with
the harder-to-wield weapons" — exactly.

## 10. Implementation status

The deterministic core is **built and tested** in the codebase. Resolution math
is data + pure functions; narration still lives with the GM.

**Done (shipped in `characters/`):**

1. ✅ `weapon_class` on every weapon, `armour_tier` on every armour,
   `shield_bonus` on every shield — `characters/fixtures/equipment.json`
   (now the canon's `equipment.json`, #75) (§5/§6).
   Javelin also carries `alt_class` (Thrusting in melee, Missile thrown).
2. ✅ **Source of truth = `characters/combat.py`** — the §6 `MATRIX` plus the
   modifier functions (`dex_modifier`, `skill_bonus`, `strength_fit_penalty`,
   `to_hit_bonus`, `target_number`, `hit_probability`, `resolve_attack`).
   (A Python module, not a JSON fixture — matches the `skills.py` convention.)
3. ✅ Catalog lookups `get_weapon_class` / `get_armour_tier` / `get_shield_bonus`
   in `characters/equipment.py`.
4. ✅ `Character.dex_modifier` and `Character.dodge_modifier` properties.
5. ✅ `characters/tests/test_combat.py` — modifier math, the §3.1 rule, the
   locked §6.1 balance surface, catalog integration, and a **drift guard** that
   this doc's §6 table matches `combat.MATRIX`.

**Go-live cutover — DONE:**

6. ✅ §8 armour rule chosen (**Hybrid**) and implemented in
   `combat.damage_after_armour`, with tests.
7. ✅ `character_export/export_utils.py` surfaces the d20 combat aim/dodge
   (`dex_modifier` / `dodge_modifier`).
8. ✅ GM prompt (`gm/engine.py`, shared by web + CLI) now teaches d20 combat;
   `cli/Modelfile` and the `public-rules/combat/` files rewritten to the d20
   system; the old `3d6 ≤ DEX` attack resolution is gone. General skill/save/
   spell checks deliberately remain `3d6 ≤ attribute`. A `gm/tests.py` drift
   guard locks the prompt's matrix to `combat.MATRIX`.
9. ✅ §7 naturals implemented in `combat.resolve_attack` (issue #105), which
   until then set only boolean `critical` / `fumble` flags while this spec, the
   rules page and the GM prompt all promised more. It now takes keyword-only
   `confirm_roll` and `fumble_roll` dice and returns `damage_multiplier`
   (`CRIT_DAMAGE_ROLLS` = 2, `SEVERE_CRIT_DAMAGE_ROLLS` = 3), `severe`,
   `bleeding`, `confirm` and `fumble_detail`, with `FUMBLE_TABLE` /
   `fumble_result` for the 1d6 subtable. Drift guards:
   `test_rules_consistency.CritFumbleRuleTest` (code vs `attack-rolls.md`) and
   `gm/tests.py`'s fumble-range and crit-multiplier guards (GM prompt +
   `cli/Modelfile`).

**What §7 does not do yet.** The multiplier is a *roll count*: the weapon's
damage expression is rolled twice (three times when confirmed) and armour's
`stops` come off the summed total **once**, so §8's `damage_after_armour` is
unchanged. `bleeding`, the −2 off-balance and weapon stress are reported as
flags and labels only — nothing is persisted and nothing ticks down, because
there is no live Fatigue/Body tracking and weapons carry no fumble counter.
The GM adjudicates those.

**Wired into the app — DONE (issue #101):**

10. ✅ **The engine has a production caller.** `characters/attack.py`'s
    `resolve(character, params, roller=dice)` is the orchestrator: it validates
    the request, looks the Target Number up in `MATRIX`, sums the to-hit via
    `to_hit_bonus` (`situational` included — the parameter #101 flagged as
    never exercised), rolls through `characters.dice`, calls `resolve_attack`,
    then rolls the damage expression `damage_multiplier` times and subtracts
    `stops` **once** via `damage_after_armour`. Dice are injected, so every
    branch is unit-tested against exact numbers rather than patched randomness
    (`characters/tests/test_attack_service.py`).
11. ✅ **A player-facing surface.** `POST /tarmar/characters/<pk>/attack/`
    (`characters.views.character_resolve_attack`, url name `attack`) and the
    character sheet's **Attack Resolution** panel, with
    `static/js/attack-panel.js` formatting the response and deriving nothing.
    The sheet also finally shows `dex_modifier` and `dodge_modifier`, which
    item 4 computed and item 7 exported but no page displayed.
12. ✅ **The result persists.** `Character.combat_log` (capped at
    `MAX_COMBAT_LOG_ENTRIES` = 20, excluded from `EXPORTABLE_FIELDS` exactly as
    `history` is) holds the server's own summary of each resolved attack, and
    the sheet renders it newest-first. A result that vanished on reload would
    be transient UI, which this project's rules forbid.

So the d20 system is now live **both** at the table and in the app: the GM
narrates d20 attacks from prompts that quote the code's own numbers, and a
player can resolve one deterministically on their sheet and read the whole
breakdown.

**What the app surface still does not do.**

- **The defender is described, not modelled.** There is no enemy or NPC entity,
  so the panel takes four GM-supplied fields (armour tier, stops, shield,
  dodge) rather than reading a target. A player therefore supplies information
  a player may not be entitled to; v1 keeps the existing permission model
  (owner / DM / admin) and labels those fields "GM supplies".
- **`stops` is a typed number.** The catalog's tiers do not map to a single
  stops value (Light is 1 *or* 2; Heavy is 4, 5 or 6), so the tier alone cannot
  produce it. A "pick the defender's armour item" select would derive both,
  the way the shield select already derives its bonus. Follow-up.
- **The GM chat cannot call it.** The response payload is deliberately shaped
  like the structure a `build_attack_message()` would inject, exactly as
  `/roll` → `build_roll_message` does, but no `/attack` command exists yet.
- **Nothing is applied.** Damage, bleeding, off-balance and weapon stress are
  reported for the GM to adjudicate. Live Fatigue/Body tracking and per-weapon
  fumble state remain unbuilt.
- **The weapon list is the saved sheet.** Adding a weapon through the equipment
  editor does not extend the panel's select until the page is reloaded.
- **Five weapons cannot be resolved at all.** Club, cestus, lasso, torch
  (weapon) and bola carry a `weapon_class` but no rollable damage expression —
  "varies", "by STR", "1d6 fire", or none at all. The panel omits them and
  names them in a footnote; the endpoint refuses one that arrives anyway.
  Giving them real expressions is a rules decision, not a code one.
- **The panel uses the selected weapon's own modifiers.** `Character.weapon_to_hit_mod`
  and `weapon_damage_mod` sum across *every* weapon in the inventory, so the
  tiles above the panel can disagree with it. That is a pre-existing display
  bug in those properties, filed separately; the panel deliberately does not
  inherit it.

## 11. Open knobs to decide before building

- §8 armour stacking (1 / 2 / 3) — **biggest balance lever.**
- DEX modifier divisor (`/2` vs `/3`).
- Under-strength penalty rate (§3.1): −1/point (default) vs −2/point.
- Skill bonus ladder (+2/+4/+6 vs steeper).
- ~~Whether severe crits need the confirm roll (§7) or trigger on the bare 20.~~
  **Decided (issue #105): the confirm roll is required**, and it is rolled at the
  same TN with the same bonus. Naturals on the confirm only decide confirmation
  — a natural 1 is a plain failure to confirm (no second fumble), a natural 20
  confirms automatically.
- Exact situational re-signs ported from the existing DEX-adjustment table.
