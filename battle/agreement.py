"""A Game Master's "HTH by agreement" ruling on a battle (issue #867).

hand-to-hand-and-grappling.md lets a figure enter hand-to-hand when the enemy
has its back to a wall, is down, is slower, is attacked from the rear, "or
they simply agree". The engine reads two agreements off the board (an enemy
closing in, two bare-handed figures). Anything beyond them is the table's
call. Spencer's ruling of 2026-10-09: "a Game Master marks a battle, or a
pair, as 'HTH by agreement'; players alone still need the engine's entry
condition."

The ruling lives on the battle (``Battle.hth_agreement_json``), the one
source of truth, in this shape::

    {"whole_battle": bool, "pairs": [[low_id, high_id], ...]}

Ids are ``BattleCombatant`` pks, which are the Tarmar engine's combatant
ids. Before every turn the Tarmar profile writes the record onto the engine
state (:func:`fill`): a marked battle lists every enemy for every figure, a
marked pair lists each figure for the other. The engine reads either list
(``CombatantState.hth_agreed_with``, tarmar-engine v0.9.10). Only the
fight's runner sets the record, while that account is a Game Master
(``battle.services.runs_the_seats``, the rule a seat hand-off follows): a
session fight's owner, any other battle's maker, so a bystander Game Master
cannot mark another's fight and a player's own practice battle cannot be
marked (coordinator's ruling under the standing rule; Spencer may overrule).
Every change writes one line to the battle log.
"""

from __future__ import annotations

from dataclasses import dataclass

from tarmar_engine.state import BattleState

#: The phrase the page and the log use for the ruling.
RULING_NAME = "hand-to-hand by agreement"

#: Why only a Game Master may change the record.
GAME_MASTER_ONLY = (
    "Only the Game Master running this fight marks it, or a pair in it, as "
    "hand-to-hand by agreement; players alone still need one of the rules' ways in"
)


@dataclass(frozen=True)
class Agreement:
    """The ruling as stored: the whole battle, and the pairs marked singly."""

    whole_battle: bool = False
    pairs: frozenset[tuple[int, int]] = frozenset()

    @classmethod
    def of(cls, battle) -> Agreement:
        """The battle's recorded ruling (empty for a battle with none)."""
        raw = battle.hth_agreement_json or {}
        return cls(
            whole_battle=bool(raw.get("whole_battle")),
            pairs=frozenset(
                pair_key(first, second) for first, second in raw.get("pairs", [])
            ),
        )

    def to_json(self) -> dict:
        """The stored shape; ``{}`` when nothing is marked."""
        if not self.whole_battle and not self.pairs:
            return {}
        return {
            "whole_battle": self.whole_battle,
            "pairs": [list(pair) for pair in sorted(self.pairs)],
        }


def pair_key(first: int, second: int) -> tuple[int, int]:
    """One spelling for a pair, whichever figure is named first."""
    low, high = sorted((int(first), int(second)))
    return (low, high)


def _enemies(one, other) -> bool:
    """Do these two figures fight on opposite sides (the engine's rule)?"""
    if one.combatant_id == other.combatant_id:
        return False
    return not one.team or other.team != one.team


def fill(state: BattleState, agreement: Agreement) -> None:
    """Write the ruling onto every figure's ``hth_agreed_with``.

    Overwrites what the state held, so a pair the Game Master unmarks stops
    agreeing on the very next turn. Both figures of a pair list each other,
    although the engine reads either list.
    """
    for figure in state.combatants:
        if agreement.whole_battle:
            partners = {
                other.combatant_id
                for other in state.combatants
                if _enemies(figure, other)
            }
        else:
            partners = set()
        for first, second in agreement.pairs:
            if figure.combatant_id == first:
                partners.add(second)
            elif figure.combatant_id == second:
                partners.add(first)
        figure.hth_agreed_with = sorted(partners)


def describe(agreement: Agreement, names: dict[int, str]) -> str:
    """The ruling in one sentence, for the page; ``""`` when nothing is marked."""
    if agreement.whole_battle:
        return (
            "A Game Master rules every pair of enemies in this battle may "
            "enter hand-to-hand by agreement."
        )
    if not agreement.pairs:
        return ""
    listed = "; ".join(pair_names(pair, names) for pair in sorted(agreement.pairs))
    return f"A Game Master rules these pairs may enter hand-to-hand by agreement: {listed}."


def pair_names(pair: tuple[int, int], names: dict[int, str]) -> str:
    first, second = pair
    return f"{names.get(first, '?')} and {names.get(second, '?')}"


def change_message(
    *,
    whole_battle: bool | None,
    pair: tuple[int, int] | None,
    agreed: bool,
    names: dict[int, str],
    by: str,
) -> str:
    """The battle-log line for one change to the ruling."""
    if whole_battle is not None:
        if agreed:
            return (
                f"{by} rules the whole battle {RULING_NAME}: every pair of "
                "enemies may now enter hand-to-hand"
            )
        return (
            f"{by} lifts {RULING_NAME} for the whole battle: pairs need one of "
            "the rules' ways in, or a pair marked singly"
        )
    if pair is None:
        raise ValueError("A change names the whole battle or a pair")
    who = pair_names(pair, names)
    if agreed:
        return f"{by} rules {who} {RULING_NAME}: they may enter hand-to-hand"
    return f"{by} lifts {RULING_NAME} for {who}"
