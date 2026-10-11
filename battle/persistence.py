"""Which rules profile's state is in ``Battle.state_json`` — and how to read it.

The engine's profile seam runs each profile over its *own* state type: the
Tarmar profile's six-phase turn advances a
:class:`tarmar_engine.state.BattleState`, and the classic Melee profile's
four-phase turn advances a ``tarmar_engine.classic.state.GameState``. Both
carry the same ``to_dict``/``from_dict`` snapshot contract as of
tarmar-engine v0.6.0, which is what made a classic battle storable here at
all — this app serves battles out of the database, re-snapshotting
``Battle.state_json`` every turn.

Writing needs no dispatch: a state knows how to serialize itself. *Reading*
does — a bare dict cannot say which class should rebuild it — so a battle row
records its profile (``Battle.rules_profile``) and :func:`load_state` picks
the state type from that name. Guessing would be worse than failing: the
wrong state type would resolve the rest of the battle under the wrong rules.

Everything above storage asks its questions through ``battle.profiles``,
the app-side seam that answers them per profile (#251 slice 4); this module
is only the snapshot half.

Per the unification plan's copyright note, the SJG-derived classic subpackage
is quarantined; this module reaches it lazily, inside the call that asks for
a classic snapshot, so a Tarmar-only import chain never pulls it in.
"""

from __future__ import annotations

from tarmar_engine.profile import CLASSIC_MELEE_NAME, TARMAR

from .engine.state import BattleState

#: The six-phase Tarmar profile — every battle this app has ever stored.
TARMAR_PROFILE: str = TARMAR.name
#: Classic *The Fantasy Trip: Melee*, the unification plan's second profile.
CLASSIC_PROFILE: str = CLASSIC_MELEE_NAME

#: ``Battle.rules_profile``'s choices: the profiles whose state this app can
#: actually persist and reload. A profile the engine registers but that has no
#: snapshot contract has no business on a battle row.
PROFILE_CHOICES: list[tuple[str, str]] = [
    (TARMAR_PROFILE, "Tarmar"),
    (CLASSIC_PROFILE, "Classic Melee"),
]

#: Where each profile's snapshot keeps its roster. The field validator sees
#: the value alone, never the row, so it cannot dispatch on the recorded
#: profile — it reads whichever of these keys is present instead.
ROSTER_KEYS: tuple[str, ...] = ("combatants", "figures")


def storable_profile(profile_name: str) -> str:
    """The named profile if this app can persist it, otherwise Tarmar.

    The lenient counterpart to :func:`_known`, for the one caller that has a
    profile *name* from presentation config rather than from a stored row:
    ``battle_setup_create`` seeds a new draft from the front door's
    ``Theme.default_rules_profile`` (#332). The theme names a profile as a
    plain string because ``common`` may not import ``battle``, so an
    unrecognised name is possible in a way it is not for a battle row, and
    the answer is the same one ``theme_for_host`` gives an unknown theme
    key: serve the ordinary default rather than break the page. Raising
    would 500 the create button on every host whose theme carried a typo.
    """
    if profile_name in dict(PROFILE_CHOICES):
        return profile_name
    return TARMAR_PROFILE


def _known(profile_name: str) -> str:
    """The name back, or ``ValueError`` naming what this app can persist."""
    if profile_name in dict(PROFILE_CHOICES):
        return profile_name
    raise ValueError(
        f"unknown rules profile {profile_name!r}: this app can load "
        f"{TARMAR_PROFILE!r} and {CLASSIC_PROFILE!r} snapshots"
    )


def dump_state(state) -> dict:
    """Snapshot any profile's state for ``Battle.state_json``.

    Profile-agnostic on purpose: both state types answer ``to_dict``, and a
    state always knows its own shape. The name exists so call sites read as
    the seam's other half rather than reaching past it.
    """
    return state.to_dict()


def tarmar_state(snapshot: dict) -> BattleState:
    """Rebuild a Tarmar snapshot, filling what one written earlier lacks.

    A figure snapshotted before issue #960 carries no ``spell_skill_levels``
    (or carries the engine's empty default), and the engine reads a missing
    spell at level 0, so a Channel derived from it would allow no cast at
    all. Its ``spell_mastery`` is the same reading of the same record (a
    spell's mastery is its skill level, ``battle.services.snapshot_character``),
    so an entry with no spell skill levels takes its mastery's. A figure
    snapshotted since has the two equal, so the fill changes nothing for it.

    Args:
        snapshot: A Tarmar ``state_json``, or the lobby's assembled one.

    Returns:
        The engine's state.
    """
    combatants = []
    for entry in snapshot.get("combatants", []):
        if not entry.get("spell_skill_levels"):
            entry = {
                **entry,
                "spell_skill_levels": dict(entry.get("spell_mastery") or {}),
            }
        combatants.append(entry)
    return BattleState.from_dict({**snapshot, "combatants": combatants})


def load_state(snapshot: dict, profile_name: str):
    """Rebuild the state in ``snapshot``, as the named profile's state type.

    Args:
        snapshot: A dict written by :func:`dump_state`.
        profile_name: The battle's ``rules_profile``.

    Raises:
        ValueError: for a profile this app cannot persist.
    """
    if _known(profile_name) == TARMAR_PROFILE:
        return tarmar_state(snapshot)
    # Lazy, and only here: the classic subpackage is SJG-derived and
    # quarantined from every Tarmar-canon import chain.
    from tarmar_engine.classic.state import GameState

    return GameState.from_dict(snapshot)


def roster_entries(snapshot: dict) -> list:
    """The combatant/figure entries in a snapshot, whichever profile wrote it.

    Empty for a snapshot holding neither key — an open battle still in its
    lobby has ``{}`` here, and that is not an error.
    """
    for key in ROSTER_KEYS:
        if key in snapshot:
            return snapshot[key]
    return []
