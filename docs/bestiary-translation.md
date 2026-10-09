# Bestiary translation: TFT → Tarmar

How creature stat blocks from *The Fantasy Trip Bestiary* (Steve Jackson
Games) become Tarmar `Beast` rows. The executable form of every rule below is
`bestiary/translation.py`; the committed fixture
`bestiary/fixtures/beasts.json` is **generated** from
`bestiary/data/tft_source_stats.json` by `manage.py build_beast_fixture`, and
`bestiary/tests/test_fixture.py` drift-guards fixture ↔ code. Change the
mapping or the source data, re-run the command, commit both.

## Provenance and copyright

- `bestiary/data/tft_source_stats.json` holds the book's **numeric stats**
  (ST/DX/IQ/MA), condensed mechanical notation for attacks, abilities, and
  armour, and a `page` citation per creature. Where the book prints a range
  (`ST 30-60`) the integer midpoint is stored and the range is preserved in
  the beast's special-abilities line as `Source ST 30-60`.
- Every `description` in `bestiary/data/descriptions.json` is **original
  prose written for this project**. The book's text is never copied.
- Two book entries are deliberately excluded: *Goo* (ST ∞ — a hazard, not a
  stat block) and *Gralls* (no single stat block; "ST: see p. 86").

## Attribute mapping

| Tarmar | Source | Rule |
| ------ | ------ | ---- |
| STR | TFT ST | direct (range → midpoint), clamped ≥ 1 |
| CON | TFT ST | ST is TFT's health *and* muscle, so it feeds both |
| DEX | TFT DX | direct, clamped ≥ 1 |
| INT | TFT IQ | direct, clamped ≥ 1 |
| WIS | — | per-category cunning table (`CATEGORY_WISDOM`): vermin 3, plants 2, mammals/birds 5, reptiles/sea 4, constructs 3, undead 10, elementals/legendary 8, dragons 12 |
| CHA | — | per-category presence table (`CATEGORY_CHARISMA`), same idea |

## Pools

Fatigue and body use the **Character formulas** so beasts land on the same
scale the injury-threshold rules assume:

- `fatigue = CON + WIS + INT + max(DEX, STR) + 7` — exactly
  `Character.fatigue` with the 2d6 component pinned to its midpoint (7) so
  the mapping stays deterministic.
- `body = ceil(fatigue * 2/3)` — exactly `Character.body`.

The alternative (direct ST scaling, TFT-style) was rejected because Tarmar
damage, armour stops, and injury thresholds are all tuned against
Character-sized pools; TFT-scale pools would make every beast glass.

## Movement

TFT MA is hexes per turn and stays hexes per turn. Compound values keep the
**ground** figure as `movement` and preserve the whole value as a
special-abilities note: `MA 8/20 (walking/flying)` → `movement=8` plus the
note. MA ranges collapse to the midpoint. Non-numeric MA (a rooted plant, a
water-only swimmer) becomes `movement=0` with the original text preserved.

## Natural attacks → weapon classes

`attacks` text is condensed notation (`Bite (2d+1); sting (1d-1, rear
hexes)`), split on `;` into `{name, damage, weapon_class}`. TFT dice are d6,
so `2d+1` is stored as `2d6+1`. The class comes from a keyword table
(first match wins, default **Striking**):

- bite / sting / beak / fang / spine / tusk … → **Piercing**
- gore / horn / charge / stab → **Thrusting**
- constrict / tentacle / grab / engulf / smother / gobble / arms → **Flexible / Snare**
- breath / spit / spray / gas / pod / shot / drop / razor feathers → **Missile — Bows**
- everything else (claw, kick, trample, slam, hands …) → **Striking**

A beast with **ST ≥ 30** swings with real mass: its Striking/Thrusting
attacks upgrade to **Heavy Striking / Heavy Thrusting** (which also engages
the §8 hybrid armour-bypass rule in `characters.combat`).

## Armour

The book's `Armor: <hide> stops N hits` lines map to `stops = N`, and the
tier follows the equipment catalog's banding: 0 → None, 1-2 → Light,
3-4 → Medium, 5+ → Heavy. Conditional armour ("if attacked from the front
hex") keeps the condition in the special-abilities line.

## Size in hexes

The book's explicit figure sizes win ("A 3-hex (triangular counter)
creature"). Otherwise ST bands, calibrated against the book's own examples
(horses and bears 1-2 hexes, adult dragons 4, an elder dragon megahex-sized):

| ST | hexes |
| -- | ----- |
| ≤ 20 | 1 |
| 21-39 | 2 |
| 40-59 | 3 |
| 60-99 | 4 |
| ≥ 100 | 7 |

## Categories

The book's table-of-contents sections group into eleven browsable families
(`SECTION_CATEGORY`): Insects & Vermin, Mammals, Birds, Reptiles &
Amphibians, Sea Creatures, Dragons & Dragonkin, Elementals, Undead &
Spirits, Plants, Constructs, Legendary. Slimes file under vermin; hydras,
wyverns, and the tatzelwurm under dragonkin; folklore one-offs (sphinx,
troll, mothman …) under Legendary.

## Generated variants

GM generation (`bestiary/generation.py`, the catalog page's generate form,
and `manage.py generate_beast`) picks a catalog beast — exact name, random
within a category, or random within a difficulty band by base ST (easy ≤ 9,
moderate 10-24, hard 25-59, deadly 60+) — then varies each stat independently
within ±15%, re-derives the pools through the same formulas above, re-reads
the armour tier from the varied stops, and saves the result with
`is_generated=True` and `base_beast` pointing at the original.
