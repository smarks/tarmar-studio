# Prior-experience workbench (issue #217)

The prior-experience flow on the character editor keeps dialog thinking after
the de-modaling pass: a floating aging-warning confirm (Cancel/Proceed), a
floating term-result panel with a Close button mid-page, and a death panel
whose Close reloads. The redesign replaces events with state.

## Principles

1. **Consequences before action.** Everything the confirm panel used to say
   is permanently visible *before* the click, so the click needs no
   confirmation.
2. **Results are state.** A rolled term renders as the newest entry of
   Experience History — exactly what a reload would show. No result panel.
3. **Death is a state.** The workbench becomes the death card, matching the
   server render for a dead character. No Close, no reload.
4. **Cause next to effect.** The history lives beside the workbench, not at
   the bottom of the page.

## Layout

Inside the existing "Skills & Experience" `stats-section` of
`characters/templates/characters/character_edit.html`:

```
┌─ Skills & Experience ────────────────────────────────────────────────┐
│  skills-summary row (Level / XP / Age / Terms / … — unchanged)       │
│                                                                      │
│  ┌─ experience-workbench (grid, 2 cols ≥900px, stacked below) ─────┐ │
│  │ ┌─ workbench card (~340px) ─┐  ┌─ Experience history (flex) ──┐ │ │
│  │ │ TERM N eyebrow            │  │ Term N-1: Merchant (22-26)   │ │ │
│  │ │ Track picker (select)     │  │   [expanded: newest term]    │ │ │
│  │ │ ── consequence preview ── │  │   Year 1: haggling (roll 9)  │ │ │
│  │ │ Age 22 → 26               │  │     survival 2d6[3,4]+1=8 ≥ 6│ │ │
│  │ │ Survival: 2d6+mod ≥ 6     │  │   … + 800 XP, +2 free points │ │ │
│  │ │ Aging: none this term     │  │ Term N-2: Laborer (18-22) ▸  │ │ │
│  │ │ [ Roll Term N (age → 26)] │  │ Term N-3: … ▸                │ │ │
│  │ └───────────────────────────┘  └──────────────────────────────┘ │ │
│  └──────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  skills-list + allocation controls (unchanged, full width below)     │
└──────────────────────────────────────────────────────────────────────┘
```

- The standalone "Experience History" `stats-section` at the bottom of the
  page moves into the grid's right column (its `<h2>` becomes an `<h3>`-level
  heading inside the section; drop the old section entirely — no duplicate).
- History order flips to **newest first**; the newest term's `<details>` is
  rendered `open`, older ones closed. `appendTermToLog` becomes
  `prependTermToLog`: insert at the top, `open`, and close the previously
  open entry. Empty state: "No terms yet. Roll the first term to build this
  character's past." in the history column.
- Grid: `display: grid; grid-template-columns: minmax(300px, 340px) 1fr;
  gap: 1.5rem;` collapsing to one column under 900px (workbench first).
  Follow the CSS-variable/theme idioms already in the character-edit styles.

## Workbench card

- Eyebrow line: `NEXT — TERM {{ N }}` (small caps/utility style consistent
  with existing labels). For a fresh character N=1.
- Track `<select>` (existing options, keep `Random` default).
- Consequence preview (a `<dl>` or label/value rows, house style):
  - **Age** — `{{ age }} → {{ age+4 }}`
  - **Survival** — `2d6 + best attribute modifier ≥ {{ target }}` per
    selected track; Random shows "target varies by track".
  - **Aging** — the per-attribute penalties for term N from
    `AGING_PENALTIES`, e.g. `STR −1, CON −1`; "None this term" when none;
    past the horizon, the escalation sentence already in the code (#163).
  - Preview updates on track change (pure DOM, from the existing
    `SKILLS_CONFIG` / `AGING_PENALTIES` / `SURVIVAL_TARGETS` data).
- Button, full card width: `Roll Term {{ N }}` with the rolling state
  `Rolling…` (existing disabled pattern). One click, no confirm — the
  preview above it IS the informed consent. After success it relabels to
  `Roll Term {{ N+1 }}` and the preview advances.
- Card copy is player-facing and plain: "Rolling a term plays four years:
  each year rolls a skill and a survival check." One sentence, under the
  eyebrow.

## Death state

- Server-rendered (`{% if not character.is_alive %}`): the workbench card's
  place holds a death card — heading "{{ name }} died", the cause, the fatal
  roll line (existing `survival_roll` filter), and "A DM can resurrect this
  character." No buttons. Keep the existing `.death-banner` styling family
  (or refine it) — one card, not a banner plus a card.
- On AJAX death: JS replaces the workbench card's content with the same
  markup (build one shared template string or a hidden server-rendered
  fragment — prefer a hidden server-rendered fragment `<template id=
  "death-card-template">` filled with cause/fatal roll by JS, so markup
  lives in one place). A reload must show the identical card.

## JS deletions

Remove from `static/js/character-edit.js` (and their tests):
`showAgingConfirmPanel`, `showTermResultPanel` (locate it — it exists near
the other panel helpers), `showDeathPanel`, and every `location.reload()`
in this flow. `createPanel` stays only if other flows still use it; if the
custom-item/editor flows are its only remaining users, leave them alone.

## What already updates and must keep updating

After a roll: `updateSkillsUI`, `refreshFatigueBodyInjury`,
`updateEquipmentDisplay`, `SKILLS_CONFIG.currentAge/currentTerms` — all
unchanged. Add: workbench preview re-render (new age, next term number,
next aging penalties).

## Tests

- **Jest** (`static/js/__tests__/character-edit.test.js`): update/replace
  panel tests — preview renders the selected track's target and term
  penalties; roll success prepends an open entry and closes the previous;
  death response swaps the workbench card and matches the template fragment;
  no reload calls remain.
- **Django** (`characters/tests/`): editor page renders the workbench
  (eyebrow, preview values for term N, button label), history newest-first
  with the first entry open, and the death card (no roll button) for a dead
  character.
- **Playwright** (`e2e/character-edit.spec.js`): update the existing
  prior-experience scenarios — roll a term via the workbench (no confirm
  step), the new entry appears at the top expanded, reload shows the same
  page state.

## Non-goals

Skills allocation UI, XP editing, equipment section, GM chat: untouched.
No new dependencies, no new endpoints — the existing add-term endpoint
already returns everything the history entry needs.
