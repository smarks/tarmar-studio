"""A Game Master's grant of a working to a figure on a battle (tarmar-studio #208).

A canon's True Magic rules let a working have an effect that needs a grant
(``magic-rules.json``'s ``true_magic.effects[].needs_grant``): only a figure a
Game Master has named the working for may craft it. Players are never held to
less: no player records a grant. The grant is a ruling on the battle, as a
lifted bound is (``battle.magic_bounds``, issue #37).

The ruling lives on the battle (``Battle.workings_granted_json``), the one
source of truth, as a list of grants in this shape::

    [{"combatant_id": int, "working": str, "by": str, "by_id": int, "at": str}]

``working`` is a working's key as the engine writes it
(``TrueMagicRules.working_key``: its effect and its tier), so a grant names
one tier of one effect. The rest is the record every ruling for single
figures keeps (:class:`battle.figure_rulings.FigureRulings`). Who may change
it is the lifted bounds' answer: the fight's runner, while that account is a
Game Master (``battle.services.runs_the_seats``), through
``battle.services.set_working_granted``. Every change writes one line to the
battle log.

The engine does the rest (tarmar-engine v0.9.22): before every turn, and
before the cast stage is drawn, the Tarmar profile writes the record onto each
figure's ``CombatantState.granted_workings`` (:func:`fill`); the engine's menu
then offers that figure the craft, and the turn runs it. A grant is not spent
by a craft: it stands until the Game Master withdraws it (the engine's
``grant_stands_until_withdrawn`` reading). A grant names a figure, not a
seat: a seat given back to the lobby loses its grants
(``battle.services.release_seat``), so whoever claims it next has none.

A grant past the figure's tier cap is recorded all the same
(:func:`past_tier_cap`), and takes effect only once the engine no longer
holds the figure to that cap (:func:`waits_on_channel`). Both are the
engine's own answers, asked of the figure as the engine reads it
(:func:`engine_figures`); no rule of the engine's is written here a second
time. The refusal of a figure with no level in the True skill asks the engine
likewise (:func:`grant_refusal`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tarmar_engine.magic import MagicRules
from tarmar_engine.state import BattleState, CombatantState

from . import magic_bounds
from .figure_rulings import COMBATANT_ID, FigureRulings
from .magic_rules import loaded_spell, magic_rules
from .persistence import tarmar_state
from .wording import words

if TYPE_CHECKING:
    from .models import Battle

#: The stored name of what a grant grants: a working's key.
WORKING = "working"

#: The keys of a change's log-line payload (:func:`change_payload`).
RECORD = "workings_granted"
GRANTED = "granted"
PAST_TIER_CAP = "past_tier_cap"


class WorkingGrants(FigureRulings):
    """The ruling as stored: every grant on the battle, each one working
    granted to one figure with who granted it and when, a figure's grants in
    the order they were made."""

    battle_field = "workings_granted_json"
    subject_field = WORKING


def builds(working_key: str, rules: MagicRules) -> bool:
    """Whether ``rules`` build the working a key names.

    The engine refuses a figure whose ``granted_workings`` holds any other
    key (``MagicRules.granted``), so no other key is granted or written onto
    a figure.

    Args:
        working_key: A working's key, as asked for or as recorded.
        rules: The magic rules asked.
    """
    if rules.true_magic is None:
        return False
    try:
        rules.true_magic.effect_and_tier(working_key)
    except KeyError:
        # The engine's answer for a key these rules do not build.
        return False
    return True


def engine_figures(battle: Battle) -> dict[int, CombatantState]:
    """Each figure of a battle as the engine reads it, with the bounds the
    battle's record lifts for it.

    Once the fight has a board the figures are read off it; in a lobby, off
    the seats' own snapshots. Only the record sets a figure's skill levels
    (``battle.services.snapshot_character``), so the two agree. The lifted
    bounds are the battle's ruling as it stands
    (:func:`battle.magic_bounds.fill`), not as the last turn left it on the
    board, so what the engine answers for a figure here is what its next menu
    will.

    Args:
        battle: A Tarmar ``Battle``.

    Returns:
        ``{BattleCombatant pk: figure}``. A seat nobody has claimed and a
        figure taken off the board are not in it.
    """
    if battle.state_json:
        snapshot = battle.state_json
    else:
        snapshot = {
            "combatants": [
                row.snapshot_json
                for row in battle.combatants.all()
                if row.snapshot_json
            ]
        }
    state = tarmar_state(snapshot)
    magic_bounds.fill(state, magic_bounds.LiftedBounds.of(battle))
    return {figure.combatant_id: figure for figure in state.combatants}


def grant_refusal(
    working_key: str, figure: CombatantState | None, name: str
) -> str | None:
    """Why a working may not be granted to a figure, in the configured words.

    Asked of a grant alone: a grant already recorded may always be withdrawn,
    whatever the canon has since become.

    Args:
        working_key: The working's key.
        figure: The figure as the engine reads it (:func:`engine_figures`),
            or ``None`` for a seat nobody has claimed.
        name: The figure's name, for the refusal.

    Returns:
        The refusal, or ``None`` when the loaded canon has True Magic and
        builds the working, its effect needs a grant, and the figure has a
        level in the True skill (the engine's ``MagicRules.true_level``).
    """
    grant_words = words().working_grants
    rules = magic_rules()
    true_magic = rules.true_magic
    if true_magic is None:
        return grant_words.refuse_no_true_magic
    if not builds(working_key, rules):
        return grant_words.refuse_working
    effect, _tier = true_magic.effect_and_tier(working_key)
    if not effect.needs_grant:
        return grant_words.refuse_needs_no_grant.format(
            working=working_label(working_key)
        )
    if figure is None or rules.true_level(figure) <= 0:
        return grant_words.refuse_no_true_level.format(
            name=name, skill=true_magic.skill_label
        )
    return None


def past_tier_cap(figure: CombatantState, working_key: str) -> bool:
    """Whether a working's tier is past a figure's own tier cap.

    The engine's answer (``MagicRules.past_tier_cap``): the cap the figure's
    level in the True skill gives, whether or not it is lifted. A grant past
    it is recorded, and waits (:func:`waits_on_channel`).

    Args:
        figure: The figure as the engine reads it (:func:`engine_figures`).
        working_key: The key of a working the loaded canon builds.

    Raises:
        KeyError: A working's key the loaded canon's True Magic rules do not
            build.
    """
    return magic_rules().past_tier_cap(figure, loaded_spell(working_key))


def waits_on_channel(figure: CombatantState, working_key: str) -> bool:
    """Whether a grant takes effect only once Channel is lifted for the figure.

    True for a working past the figure's tier cap (:func:`past_tier_cap`)
    while the engine holds the figure to that cap (``MagicRules.tier_cap``,
    which is ``None`` for a figure whose cap a Game Master's ruling has
    lifted). Which ruling lifts the cap is the engine's to say (its
    ``channel_lift_lifts_the_tier_cap`` reading), and it offers the craft
    only then.

    Args:
        figure: The figure as the engine reads it, the battle's lifted bounds
            on it (:func:`engine_figures`).
        working_key: The key of a working the loaded canon builds.

    Raises:
        KeyError: A working's key the loaded canon's True Magic rules do not
            build.
    """
    return (
        past_tier_cap(figure, working_key)
        and magic_rules().tier_cap(figure) is not None
    )


def working_label(working_key: str) -> str:
    """The words a working is named in: the name the engine builds for it
    under the loaded canon, its effect and its tier, or the configured words
    for a key the canon no longer builds.

    Args:
        working_key: A working's key, as asked for or as recorded.
    """
    if builds(working_key, magic_rules()):
        return loaded_spell(working_key).name
    return words().working_grants.unbuilt_working.format(key=working_key)


def change_message(*, working_key: str, granted: bool, name: str, by: str) -> str:
    """The battle-log line for one change to the ruling.

    Args:
        working_key: The working granted or withdrawn.
        granted: Granted (True) or withdrawn (False).
        name: The figure's name.
        by: The Game Master, as the log names him.
    """
    grant_words = words().working_grants
    sentence = grant_words.log_granted if granted else grant_words.log_withdrawn
    return sentence.format(by=by, name=name, working=working_label(working_key))


def change_payload(
    record: WorkingGrants,
    *,
    combatant_id: int,
    working_key: str,
    granted: bool,
    figure: CombatantState | None = None,
) -> dict:
    """The battle-log line's payload for one change to the ruling.

    Args:
        record: The ruling as it stands after the change.
        combatant_id: The figure's ``BattleCombatant`` pk.
        working_key: The working granted or withdrawn.
        granted: Granted (True) or withdrawn (False).
        figure: For a grant, the figure as the engine reads it
            (:func:`engine_figures`).

    Returns:
        The record, the figure, the working and whether it was granted; and,
        for a grant, whether the working is past the figure's tier cap
        (:func:`past_tier_cap`), so waits on Channel being lifted for it
        (:func:`waits_on_channel`).

    Raises:
        KeyError: A grant of a working's key the loaded canon's True Magic
            rules do not build.
    """
    payload = {
        RECORD: record.to_json(),
        COMBATANT_ID: combatant_id,
        WORKING: working_key,
        GRANTED: granted,
    }
    if figure is not None:
        payload[PAST_TIER_CAP] = past_tier_cap(figure, working_key)
    return payload


def fill(state: BattleState, record: WorkingGrants) -> None:
    """Write the ruling onto every figure's ``granted_workings``.

    The engine reads the field (tarmar-engine v0.9.22,
    ``MagicRules.granted``) for the menu and the craft, and never changes it.
    Overwrites what the state held, so a grant withdrawn stops being offered
    on the very next turn, as :func:`battle.magic_bounds.fill` does for its
    ruling. A grant the loaded canon no longer builds stays on the record
    and off the figure, since the engine refuses a figure that lists one.

    Args:
        state: The board.
        record: The battle's recorded grants.
    """
    rules = magic_rules()
    granted = [entry for entry in record.in_order() if builds(entry.subject, rules)]
    for figure in state.combatants:
        figure.granted_workings = [
            entry.subject
            for entry in granted
            if entry.combatant_id == figure.combatant_id
        ]
