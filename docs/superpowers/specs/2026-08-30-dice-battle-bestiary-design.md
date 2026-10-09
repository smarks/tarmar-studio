# Dice recording, battle-royale simulator, and bestiary — design

Spencer's request (2026-08-30), tracked as issues #175, #176, #177. Written by
claude-2e9b6218 after a full architecture survey. Spencer was not available for
interactive review; decisions below are recorded with their reasoning so any of
them can be reversed cheaply at PR review.

## Context findings that shape the design

- `characters/combat.py` is a complete, pure, spec-guarded d20 resolution
  library with **zero production callers**. There is no combat engine, no
  persistent game state, no spatial code, no magic subsystem, no AI policy,
  and no monster data anywhere in the repo.
- Three disconnected dice implementations exist: `characters/dice.py`
  (module-global `random`, `*_logged` variants return prose strings),
  `characters/skills.py:roll_2d6`, and `gm/engine.py:roll_dice`.
- Turn structure, action options, facing/engagement, and casting rules are
  fully specified in `reference/content/public-rules/combat/` and
  `reference/content/public-rules/magic/casting-spells.md`. The engine must be
  built against those documents, drift-guarded the way `test_combat.py` guards
  the §6 matrix.
- `Character` stores no mutable combat state (fatigue/body/position are
  derived maxima or absent). Battle state must live elsewhere.
- Process constraints: 96% Python coverage ratchet (new apps must be added to
  `--cov=` and `[tool.coverage.run] source`), JS coverage ratchet, pre-commit
  runs the full Playwright suite (use `SKIP=playwright-e2e` locally; CI runs
  it), DRY is mandatory.

## Workstream 1 — single roll service and roll display (#175)

**New module `common/rolling.py`** — pure Python, no Django imports (the GM
CLI imports through `gm/engine.py` which must stay framework-free).

```python
@dataclass(frozen=True)
class RollRecord:
    purpose: str          # e.g. "attack", "damage", "casting", "survival"
    specification: str    # e.g. "1d20", "3d6", "2d6+3"
    faces: tuple[int, ...]
    modifier: int
    total: int            # sum(faces) + modifier
    target_number: int | None
    outcome: str | None   # e.g. "hit", "miss", "critical", "fumble", "success"

class Roller:
    def __init__(self, seed: int | None = None, on_roll: Callable[[RollRecord], None] | None = None): ...
    def roll(self, specification: str, *, purpose: str, modifier: int = 0,
             target_number: int | None = None, outcome: str | None = None) -> RollRecord: ...
```

- Internally a `random.Random(seed)` instance — the seeding hook #176 needs.
- `on_roll` lets a caller (the battle engine) capture every record without the
  roller knowing about persistence.
- `characters/dice.py` and `gm/engine.py:roll_dice` delegate their randomness
  to a `Roller` (module-default unseeded instance keeps existing APIs and
  tests working). `characters/skills.py:roll_2d6` folds into `dice.py`. No
  behavioural change to character creation.

**UI fixes shipped with it:**
- GM chat: `gm/views.py` already sends `done_data["roll"]` (expression, faces,
  total); `static/js/chat.js` drops it. Render the faces in the transcript.
- Experience-term display: per-year `survival_check` dice faces are stored in
  `experience_log` but never rendered (the top-level key is clobbered each
  year). Render each year's 2d6 faces in both the server template and the
  JS live-append path, identically.
- Death provenance for experience deaths: the death modal and deceased banner
  gain the fatal roll (faces, modifier, total vs target) pulled from the last
  year of the last term.

Battle-roll display belongs to workstream 2, built on this service.

## Workstream 2 — battle app: engine, AI, simulator UI (#176)

**New Django app `battle/`.**

### Models
- `Battle` — name, `seed` (int), status (`setup/paused/finished`),
  `turn_number`, `state_json` (full serialized engine state), `created_by` FK,
  timestamps. Battles are DM/staff-only in v1.
- `BattleCombatant` — battle FK, optional `character` FK, optional `beast` FK
  (nullable until #177 lands; part two), display name, team/free-for-all tag.
  Mutable per-turn state lives in `state_json`, not columns; this table exists
  for provenance and list display.
- `BattleEvent` — battle FK, `turn`, `phase`, `sequence`, `event_type`
  (`roll/decision/action/movement/damage/status/death/phase/info`), `actor`
  (display label), `payload` JSON, `message` (pre-rendered human line, server
  computes display per repo convention). Every RollRecord becomes a `roll`
  event; every AI choice becomes a `decision` event with the scored
  candidates it considered.

### Engine (`battle/engine/`)
Pure dataclasses + functions, DB-free, `SimpleTestCase`-testable. Consumes a
`Roller`; emits events through a sink callback. Implements the six phases from
`turn-sequence.md`: initiative (1d6/side), renew spells, initial movement,
final movement, actions in adjusted-DEX order, forced retreat. Attack
resolution calls the existing `characters/combat.py` functions unchanged.

**Spatial layer:** axial hex coordinates on an open arena (configurable
radius), distance, facing (front/side/rear derived from relative position),
engagement on adjacency, straight-line movement toward chosen target with
gait fatigue costs from `movement.md`, megahex-based missile ranges from
`dex-adjustments.md`. Explicitly out of scope for v1: terrain, obstacles,
line-of-sight blockers, multi-hex figures (arrives with beasts), HTH actions
t–v, jousting. Each exclusion noted in the code where the rules doc covers it.

**Magic (minimal, rules-faithful):** `battle/spells.py`, data-driven catalog
of 6–10 combat-relevant spells drawn from the repo's own
`schools-of-magic.md` list. Casting per `casting-spells.md`: 3d6 ≤ INT or
WIS, mana cost = spell level (mana pool = mental pool for v1 — flag this
choice loudly in the PR if `casting-spells.md` says otherwise; the rules doc
wins), DEX roll for targeted spells, renewal in phase 2.

**AI policy (`battle/policy.py`):** utility-based. Scores the legal actions
from `action-options.md` for the actor's state using
`combat.hit_probability`, expected damage, own fatigue/body, and range. Every
choice logs a decision event: candidates, scores, one-line rationale each,
chosen action. No learning, no search deeper than one turn.

**Character generation (`battle/generation.py`):** builds real `Character`
rows (owned by the battle creator, names prefixed with the battle name,
`is_played=False`, soft-deleted when the battle is deleted) via
`roll_all_character_stats()`, then applies an archetype: armoured fighter,
unarmoured duelist, unarmed brawler, archer, wizard, fighter-mage. Archetype
picks equipment from `characters/fixtures/equipment.json` (respecting STR
requirements), allocates skill points, and gives wizards varied spell
loadouts. A battle of N combatants draws archetypes round-robin with
variation so every mix appears.

### UI (`battle/templates/battle/`, `static/js/battle.js`)
Server-rendered + vanilla JS per house idiom (`chat.js`/`autosave.js`
patterns, `notify.js` for messages, nothing transient, no framework).
- Battle list/create page: N, archetype mix, optional seed.
- Battle detail: SVG hex arena (positions, facing, names, body/fatigue bars)
  rendered from server state; event log grouped by turn with every roll
  (faces, modifier, total vs TN, outcome) and every decision (chosen action +
  expandable candidate scores); **Advance turn** button POSTs, server runs
  exactly one full turn inside a transaction, responds with new events +
  state, JS appends. Page reload reproduces everything from the DB (state-
  driven). A dead combatant's entry links the fatal roll chain (the death
  event references the event ids of the to-hit, damage, and threshold rolls).
- CLI: `tarmar simulate --n 8 --seed 42 [--step]` subcommand and a `./run
  simulate` entry, sharing the engine, printing the same event lines.

### Testing
- Engine: seeded golden-battle tests (same seed ⇒ identical event stream),
  invariant checks (dead actors act no more, fatigue/body never negative,
  every roll event has faces summing to total − modifier), per-phase unit
  tests, drift guards against `turn-sequence.md` phase order and the
  `action-options.md` action letters.
- Views: standard `BaseTestCase` coverage.
- e2e: one Playwright spec — create battle, advance two turns, assert roll
  faces and a decision rationale are visible.

## Workstream 3 — bestiary app and beast generation (#177)

**New Django app `bestiary/`.**

- `Beast` model: name, slug, `category` (from the TFT table of contents
  sections: ants, bears, canines, dragons, elementals, felines, undead, …),
  `description` (**original prose written for this project — never the
  book's text**), Tarmar-native stats (STR/DEX/INT/WIS/CON/CHA), fatigue and
  body pools, `armour_tier` + `stops`, `attacks` JSON
  (`[{name, damage, weapon_class}]`), `special_abilities` text,
  `size_hexes`, `movement`, `source_page` (citation into the TFT Bestiary),
  `is_generated` flag + `base_beast` FK for GM-generated variants.
- **Translation layer** (`bestiary/translation.py`), documented in
  `docs/bestiary-translation.md`: TFT ST feeds STR and CON, DX→DEX, IQ→INT
  (WIS fixed low for animals, higher for cunning/undead types), MA→movement
  hexes; natural attacks map onto the closest Tarmar weapon class; hide/scale
  notes map onto armour tiers/stops. The mapping is code + a table, and the
  fixture is generated by it from the parsed stat data, so re-running the
  translation is reproducible.
- Fixture `bestiary/fixtures/beasts.json` generated from the parsed TFT
  catalog (~200 creatures) + original descriptions. Drift guard: a test
  asserts the fixture round-trips through the translation layer.
- **GM tooling:** DM-gated pages under `/tarmar/gm/bestiary/` — browse/filter
  the catalog by category, and a generate action: exact beast by name, random
  from a category, or random by difficulty band; generated variants get
  bounded stat variation (±15%) and a name, saved with `is_generated=True`.
  Management command `generate_beast` for scripted use.

## Part two — beasts in the battle royale (follow-up PR)

`BattleCombatant.beast` FK activates: a Beast adapts into the engine's
combatant dataclass (natural attacks as weapon entries, its armour tier/stops,
its movement), multi-hex engagement per `movement.md` for `size_hexes > 1`.
Battle create page gains "add beasts". Depends on workstreams 2 and 3.

## PR plan and integration

1. `175-die-roll-service` → PR for #175 (this doc rides with it).
2. `177-bestiary` → PR for #177 (independent).
3. `176-battle-royale-simulator` → PR for #176; merges the roll-service
   commit from branch 1 early so the engine consumes the real `Roller`.
4. Beasts-in-battle PR after 2 and 3 merge.

Spencer merges all PRs; nothing here deploys itself. Every branch keeps the
coverage ratchets green and stages files explicitly (never `git add -A`).
