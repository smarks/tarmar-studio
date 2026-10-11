"""One shape for the battle page, whichever profile wrote the state.

Slice 6 (#251) left a classic battle **creatable, storable, playable and
logged, but not watchable**. The reason was geometric, not cosmetic: the arena
and roster panels are painted in the browser, and the browser had learned
exactly one state shape — Tarmar's ``combatants`` on an axial hex disc, with
``fatigue``/``body`` pools and a direction *index* read against the Tarmar
engine's own direction table. A classic snapshot is ``figures`` on a bounded
rectangular field of flat-top offset hexes, damaged on ST, facing by
hexarena's direction table, which numbers its steps differently.

Two ways to close that. Branch the renderer on the profile — 1,040 lines of
``battle.js`` and its unit suite learning a second state shape, a second
geometry and a second injury model. Or compute **one view model on the
server**, which is what this module does and what the rest of this app already
does with display (the event log is server-rendered; the command bar's options
come from the policy, not from JS reading state).

What the page is handed, and therefore what it no longer works out:

* **the arena as cells**, not a radius — the browser drew the Tarmar disc by
  re-implementing ``hexes.py``'s disc rule; now it draws the hexes it is given
* **axial coordinates for both profiles** — classic's 1-based ``(col, row)``
  offsets are converted here, through the arena's own ``HexLayout``, so the
  page keeps one coordinate system
* **an orientation** — pointy-top for Tarmar, flat-top for classic, which is
  the printed *Melee* map's own orientation. It is one layout parameter
  rather than a second renderer, and carrying it means a classic board is
  not silently rotated 30° to fit the Tarmar layout.
* **facing as a step**, not an index — a direction index is meaningless
  without the table it indexes, and the two profiles' tables disagree. The
  axial step to the faced hex means the same thing in both.
* **the footprint** — the engine already computes which hexes a multi-hex
  figure covers; ``battle.js`` had a second copy of that geometry, commented
  as "mirroring battle/engine/hexes.py", which is exactly the duplication the
  unification work exists to remove.
* **pools by name** — Tarmar's fatigue and body, classic's ST. Forcing a
  classic figure's ST into a ``body`` field would have been the cheap answer
  and a false one: classic has no second pool to leave empty.
* **the roster card's status line**, computed where the vocabulary lives.
* **the weapons lying on the field** (#878), one entry per hex with the
  names of what lies there, so a player offered PICK UP WEAPON (q) can see
  where each weapon is and which one it is.

* **the attribute panel** (#302) — the sheet-level numbers a DM consults
  mid-fight, grouped and already formatted as label/value rows, so one
  renderer draws a Tarmar combatant's STR/DEX/INT/WIS/CON and a classic
  figure's ST/DX/IQ/adjDX without knowing either rules set. It is built
  from the *state*, which is the whole point: a combatant is a snapshot
  taken at seat time, and the ``Character`` row it came from may have been
  edited since.

What it deliberately does **not** carry: pixels. Hex size, colours and the
viewBox stay in the stylesheet and the renderer, because those are the
page's business and change with the design, not with the rules.

Nor does it decide **who** may see the attribute panel. That is
``battle.access.detail_visible_ids``, and it arrives here as a set of
combatant ids. The default is the empty set — a caller that forgets to ask
shows nobody's numbers rather than everybody's.
"""

from __future__ import annotations

from .classic_adaptation import classic_team
from .engine import hexes
from .engine.state import BattleState, CombatantState
from .persistence import CLASSIC_PROFILE, TARMAR_PROFILE

#: Pointy-top hexes — vertices up and down, rows offset. The Tarmar arena.
POINTY = "pointy"
#: Flat-top hexes — vertices left and right, columns offset. The classic map.
FLAT = "flat"


def _arena_disc(radius: int) -> list[list[int]]:
    """Every axial cell within ``radius`` of the origin, in a stable order."""
    return [
        [q, r]
        for q in range(-radius, radius + 1)
        for r in range(-radius, radius + 1)
        if abs(q) + abs(r) + abs(q + r) <= 2 * radius
    ]


def _pools(*entries: tuple[str, str, int, int]) -> list[dict]:
    return [
        {"key": key, "label": label, "current": current, "maximum": maximum}
        for key, label, current, maximum in entries
    ]


def _status(alive: bool, conscious: bool, standing_line: str) -> str:
    """The roster card's one line: the worst true thing about this combatant."""
    if not alive:
        return "Dead"
    if not conscious:
        return "Unconscious"
    return standing_line


#: What the card prints for a gait the figure may not use, as the sheet does.
BARRED_GAIT = "n/a"


def _tarmar_gait_rows(combatant: CombatantState) -> list[dict]:
    """Walk, Jog, Run and Sprint as the sheet shows them, n/a where barred.

    The seat (``battle.services._seated_gaits``) gives a barred Run or Sprint
    as 0, which the engine reads as barred (#778, #818). A barred Jog is
    seated as the Walk distance, because a figure that may not jog charges
    and dodges at its walk ("Jog or less"); every load that bars jogging bars
    running too (equipment.py's heavy and overloaded rows), so a Jog equal to
    the Walk with no Run is a barred Jog.
    """
    jog_barred = combatant.move_run == 0 and combatant.move_jog <= combatant.move_walk
    return [
        _row("Walk", str(combatant.move_walk)),
        _row("Jog", BARRED_GAIT, "charges and dodges at the walk")
        if jog_barred
        else _row("Jog", str(combatant.move_jog)),
        _row("Run", str(combatant.move_run) if combatant.move_run else BARRED_GAIT),
        _row(
            "Sprint",
            str(combatant.move_sprint) if combatant.move_sprint else BARRED_GAIT,
        ),
    ]


def _row(label: str, value: str, note: str = "") -> dict:
    """One label/value line of the attribute panel.

    ``note`` is the small print under the value — where a number needs a
    word to be read correctly (a DEX that already has the armour penalty
    in it) rather than a number the reader can take at face value.
    """
    return {"label": label, "value": value, "note": note}


def _group(label: str, rows: list[dict]) -> list[dict]:
    """A panel section, or nothing at all when it has no rows to show.

    Returned as a list so the caller can splat it: a combatant with no
    magic gets no Magic heading, rather than an empty one that makes the
    reader look for what is missing.
    """
    return [{"label": label, "rows": rows}] if rows else []


def _signed(value: int) -> str:
    """``+2`` / ``-1`` / ``+0`` — a modifier reads wrong without its sign."""
    return f"{value:+d}"


#: Which way up each profile's hexes sit, by profile name.
ORIENTATIONS = {TARMAR_PROFILE: POINTY, CLASSIC_PROFILE: FLAT}


def empty_view(profile_name: str, *, turn: int) -> dict:
    """A battle with nothing to draw yet — a lobby, before the state exists.

    An empty arena is the truthful answer there: the roster rows are claimed
    seats, not figures on a field, and inventing a field would draw a fight
    that has not been set up.
    """
    return {
        "profile": profile_name,
        "turn": turn,
        "next_sequence": 1,
        "arena": {"orientation": ORIENTATIONS[profile_name], "cells": []},
        "combatants": [],
        "ground_weapons": [],
    }


# ---------------------------------------------------------------------------
# Tarmar
# ---------------------------------------------------------------------------


def _tarmar_reach(weapon) -> str:
    """How this weapon reaches its target, in one word."""
    if weapon.is_missile:
        return "Missile"
    if weapon.is_thrown:
        return "Thrown"
    return "Melee"


def _tarmar_detail(combatant: CombatantState) -> dict:
    """The sheet-level numbers this Tarmar combatant is being fought with.

    Grouped the way a DM reads them mid-fight rather than the way the
    dataclass stores them: who it is, what it hits with, what stops a
    blow, how far it moves, and — only for a combatant that has any —
    what it can cast.
    """
    weapon = combatant.weapon
    attack = [
        _row("Weapon", weapon.name),
        _row("Damage", weapon.damage),
        _row("Class", weapon.weapon_class),
        _row("Skill", f"level {combatant.weapon_skill_level}"),
        _row("To hit", _signed(combatant.dex_bonus), "on every attack roll"),
        _row("Reach", _tarmar_reach(weapon)),
    ]
    if weapon.str_req:
        attack.insert(3, _row("STR needed", str(weapon.str_req)))
    movement = _tarmar_gait_rows(combatant)
    if combatant.size_hexes > 1:
        movement.append(_row("Size", f"{combatant.size_hexes} hexes"))
    magic = []
    if combatant.max_mana or combatant.spells or combatant.active_spells:
        magic.append(_row("Mana", f"{combatant.mana} / {combatant.max_mana}"))
        if combatant.spells:
            magic.append(_row("Spells", ", ".join(combatant.spells)))
        if combatant.active_spells:
            magic.append(_row("In effect", ", ".join(combatant.active_spells)))
    return {
        "groups": [
            {
                "label": "Attributes",
                "rows": [
                    _row("STR", str(combatant.strength)),
                    _row(
                        "DEX",
                        str(combatant.dexterity),
                        "armour penalty included",
                    ),
                    _row("INT", str(combatant.intelligence)),
                    _row("WIS", str(combatant.wisdom)),
                    _row("CON", str(combatant.constitution)),
                ],
            },
            {"label": "Attack", "rows": attack},
            {
                "label": "Defence",
                "rows": [
                    _row("Armour", combatant.armour_tier or "None"),
                    _row("Stops", str(combatant.stops)),
                    _row(
                        "Shield",
                        _signed(combatant.shield_bonus)
                        if combatant.shield_bonus
                        else "None",
                    ),
                ],
            },
            {"label": "Movement", "rows": movement},
            *_group("Magic", magic),
        ],
    }


def _tarmar_combatant(combatant: CombatantState, detail_ids: frozenset[int]) -> dict:
    faced = hexes.add((combatant.q, combatant.r), combatant.facing)
    return {
        "combatant_id": combatant.combatant_id,
        "name": combatant.name,
        "alive": combatant.alive,
        "conscious": combatant.conscious,
        "q": combatant.q,
        "r": combatant.r,
        "footprint": [list(cell) for cell in combatant.footprint],
        "facing_vector": [faced[0] - combatant.q, faced[1] - combatant.r],
        "is_beast": combatant.is_beast,
        # Empty = free-for-all; the page groups the roster and tints the
        # arena marker by this (#275).
        "team": combatant.team,
        "status": _status(
            combatant.alive,
            combatant.conscious,
            f"{combatant.archetype} — {combatant.weapon.name}",
        ),
        "pools": _pools(
            ("fatigue", "fatigue", combatant.fatigue, combatant.max_fatigue),
            ("body", "body", combatant.body, combatant.max_body),
        ),
        # The rolls that killed it, for the roster card's links into the log.
        "fatal_chain": list(combatant.fatal_chain),
        # The attribute panel, for a viewer allowed this combatant's
        # numbers; ``None`` — not an empty panel — for everyone else, so
        # the page draws no disclosure at all rather than an empty one.
        "detail": (
            _tarmar_detail(combatant) if combatant.combatant_id in detail_ids else None
        ),
    }


def _ground_weapons(state: BattleState) -> list[dict]:
    """The weapons lying on the field, grouped by hex (issue #878).

    tarmar-engine 0.9.5 leaves a dropped, fumbled or thrown weapon in its hex
    (``BattleState.ground_weapons``) and offers PICK UP WEAPON (q) to a figure
    beside one. Each entry is a hex and the names of the weapons in it, in
    the order they came to lie there; hexes are in board order.
    """
    by_hex: dict[tuple[int, int], list[str]] = {}
    for lying in state.ground_weapons:
        by_hex.setdefault((lying.q, lying.r), []).append(lying.weapon.name)
    return [
        {"q": q, "r": r, "weapons": names} for (q, r), names in sorted(by_hex.items())
    ]


def tarmar_view(
    state: BattleState, *, next_sequence: int, detail_ids: frozenset[int] = frozenset()
) -> dict:
    """The page's view of a six-phase Tarmar battle."""
    return {
        "profile": TARMAR_PROFILE,
        "turn": state.turn,
        "next_sequence": next_sequence,
        "arena": {
            "orientation": POINTY,
            "cells": _arena_disc(state.arena_radius),
        },
        "combatants": [
            _tarmar_combatant(combatant, detail_ids) for combatant in state.combatants
        ],
        "ground_weapons": _ground_weapons(state),
    }


# ---------------------------------------------------------------------------
# Classic
# ---------------------------------------------------------------------------


def _classic_id(figure) -> int:
    """The row pk a classic figure's uid *is* (``profiles.CLASSIC_UID_IS_ROW_PK``)."""
    try:
        return int(figure.uid)
    except (TypeError, ValueError) as bad_uid:
        raise ValueError(
            f"classic figure {figure.name!r} has uid {figure.uid!r}, which is not "
            f"a BattleCombatant row pk (see CLASSIC_UID_IS_ROW_PK)"
        ) from bad_uid


def _classic_position(figure):
    """Where this figure stands, refusing by name if it stands nowhere.

    ``Figure.position`` is optional in the package — a figure exists before it
    is placed — but every figure in a battle this app built was placed at
    creation. A state that arrives otherwise is broken rather than
    unrenderable, so it says which figure, instead of failing inside the
    hex layout.
    """
    if figure.position is None:
        raise ValueError(
            f"classic figure {figure.name!r} stands on no hex, so it cannot be "
            f"drawn; a battle's figures are placed when it is created"
        )
    return figure.position


def _classic_reach(weapon) -> str:
    """How a classic weapon reaches, from its kind and its reach in hexes."""
    kind = str(weapon.kind).capitalize()
    if weapon.reach > 1:
        return f"{kind}, {weapon.reach} hexes"
    return kind


def _classic_detail(figure) -> dict:
    """The same panel over a classic *Melee* figure, in classic vocabulary.

    ST/DX/IQ rather than STR/DEX/INT, one movement allowance rather than
    three rates, and adjDX said out loud — it is the number every to-hit
    roll is actually made against, and it is not the DX printed above it.
    """
    weapon = figure.ready_weapon
    if weapon is None:
        attack = [_row("Weapon", "Unarmed")]
    else:
        attack = [
            _row("Weapon", weapon.name),
            _row("Damage", str(weapon.damage)),
            _row("Reach", _classic_reach(weapon)),
        ]
        if weapon.min_strength:
            attack.insert(2, _row("ST needed", str(weapon.min_strength)))
        if weapon.two_handed:
            attack.append(_row("Hands", "Two-handed"))
    movement = [_row("Move", f"{figure.movement_allowance} hexes")]
    if figure.size > 1:
        movement.append(_row("Size", f"{figure.size} hexes"))
    magic = []
    if figure.spells_known or figure.active_spells:
        if figure.spells_known:
            magic.append(_row("Spells", ", ".join(figure.spells_known)))
        if figure.active_spells:
            magic.append(_row("In effect", ", ".join(figure.active_spells)))
        magic.append(_row("Staff", "Yes" if figure.has_staff else "No"))
    return {
        "groups": [
            {
                "label": "Attributes",
                "rows": [
                    _row("ST", str(figure.strength)),
                    _row("DX", str(figure.dexterity)),
                    _row("IQ", str(figure.intelligence)),
                    _row(
                        "Adjusted DX",
                        str(figure.base_adj_dx),
                        "armour and ready shield included",
                    ),
                ],
            },
            {"label": "Attack", "rows": attack},
            {
                "label": "Defence",
                "rows": [
                    _row("Armour", figure.armor.name),
                    _row("Stops", str(figure.armor.stops)),
                    _row(
                        "Shield",
                        f"{figure.shield.name} (stops {figure.shield.stops})"
                        if figure.shield.stops
                        else figure.shield.name,
                    ),
                ],
            },
            {"label": "Movement", "rows": movement},
            *_group("Magic", magic),
        ],
    }


def _classic_figure(figure, layout, detail_ids: frozenset[int]) -> dict:
    """One classic figure in the page's coordinates, pools and vocabulary."""
    position = _classic_position(figure)
    cube_x, _cube_y, cube_z = layout.to_cube(position)
    faced_x, _faced_y, faced_z = layout.to_cube(
        layout.neighbor(position, figure.facing)
    )
    weapon = figure.ready_weapon
    kind = figure.char_class or figure.race.value
    return {
        "combatant_id": _classic_id(figure),
        "name": figure.name,
        "alive": not figure.is_dead,
        "conscious": not figure.collapsed and not figure.is_dead,
        "q": cube_x,
        "r": cube_z,
        # Multi-hex classic figures (the giant, p.20) work out their own
        # footprint from the layout, exactly as the Tarmar engine does.
        "footprint": [
            [cube[0], cube[2]]
            for cube in (layout.to_cube(cell) for cell in figure.footprint(layout))
        ],
        "facing_vector": [faced_x - cube_x, faced_z - cube_z],
        # Classic has no beast/character split — every combatant is a Figure.
        "is_beast": False,
        # The team's name, decoded from the side key (#275, #930): a
        # teamless figure's side is its uid, a team's carries a prefix.
        "team": classic_team(figure),
        "status": _status(
            not figure.is_dead,
            not figure.collapsed and not figure.is_dead,
            f"{kind} — {weapon.name}" if weapon else f"{kind} — unarmed",
        ),
        "pools": _pools(("st", "ST", figure.current_st, figure.strength)),
        # The classic runner logs lines rather than roll records, so a dead
        # figure has no chain of rolls to cite. Empty, not absent: the card
        # asks every combatant the same question.
        "fatal_chain": [],
        "detail": (
            _classic_detail(figure) if _classic_id(figure) in detail_ids else None
        ),
    }


def _classic_ground_weapons(state, layout) -> list[dict]:
    """The classic field's dropped weapons, in the Tarmar view's shape (#878).

    A classic figure drops its weapon on a 17 and may take one up again with
    PICK UP (q), so the same question -- where is it, and which is it --
    arises on the classic map. The engine keeps ``(hex, weapon)`` pairs in
    ``GameState.dropped``; the hex crosses to axial as the figures' do.
    """
    by_hex: dict[tuple[int, int], list[str]] = {}
    for hex_position, weapon in state.dropped:
        cube = layout.to_cube(hex_position)
        by_hex.setdefault((cube[0], cube[2]), []).append(weapon.name)
    return [
        {"q": q, "r": r, "weapons": names} for (q, r), names in sorted(by_hex.items())
    ]


def classic_view(
    state,
    *,
    turn: int,
    next_sequence: int,
    detail_ids: frozenset[int] = frozenset(),
) -> dict:
    """The page's view of a classic *Melee* battle.

    The SJG-derived classic subpackage stays quarantined: nothing here imports
    it. The state carries its own arena and its own layout, so this reads
    geometry off the object it was handed.

    ``turn`` is passed in rather than read off ``state.turn_number`` (#661).
    The classic engine's counter names the turn *about to be* played, while
    everything in this app — ``Battle.turn_number``, the payload's own
    ``turn``, the event log's top group — names the last one completed.
    Publishing the raw counter put one response's ``view["turn"]`` a turn
    ahead of its own ``turn``, and left the two profiles disagreeing about
    what the field meant. ``BattleProfile.turn_number`` is the one home of
    that conversion, and the caller is the profile.

    Args:
        state: The classic ``GameState``.
        turn: The last turn completed, from the profile.
        next_sequence: The battle-global sequence the next event will take.
        detail_ids: Combatants this viewer may see full attributes for.
    """
    layout = state.arena.layout
    return {
        "profile": CLASSIC_PROFILE,
        "turn": turn,
        "next_sequence": next_sequence,
        "arena": {
            "orientation": FLAT,
            "cells": [
                [cube[0], cube[2]]
                for cube in (layout.to_cube(cell) for cell in state.arena.all_hexes())
            ],
        },
        "combatants": [
            _classic_figure(figure, layout, detail_ids) for figure in state.figures
        ],
        "ground_weapons": _classic_ground_weapons(state, layout),
    }
