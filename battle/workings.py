"""A working, its craft and its drain, as the engine counts them (tarmar-studio #208).

A True mage crafts a working over several turns (tarmar-engine #77): the
turns its tier takes, the mana gathered a share a turn, and what it drains
from its caster when it goes off are the canon's numbers, worked out by the
engine's ``TrueMagicRules``. The turn menu's lines
(:mod:`battle.turn_menu`), the cast stage (``battle.services.caster_magic``),
the roster card (:mod:`battle.viewmodel`) and the battle log's lines
(:mod:`battle.true_magic_narration`) all print those numbers, and each asks
for them here, so no rule of the engine's is worked out a second time and the
four cannot disagree.

Everything here needs the loaded canon to have True Magic: a True cast, a
craft and a working exist only under it, and the engine refuses each without
it.
"""

from __future__ import annotations

from dataclasses import dataclass

from tarmar_engine.magic import (
    SpellEffectKind,
    TrueMagicRules,
    WorkingEffect,
    casting_mana,
)
from tarmar_engine.spells import Spell
from tarmar_engine.state import CombatantState, CraftState

from .magic_rules import magic_rules


def true_magic() -> TrueMagicRules:
    """The loaded canon's True Magic rules.

    Raises:
        ValueError: The loaded canon has none, so nothing True Magic does
            can be on a board it runs.
        battle.magic_rules.MagicRulesError: The canon's file is missing or
            malformed.
    """
    rules = magic_rules().true_magic
    if rules is None:
        raise ValueError(
            "the loaded canon has no True Magic, so it builds no working, "
            "True cast or craft to read"
        )
    return rules


@dataclass(frozen=True)
class Drain:
    """What a casting drains from its caster when it goes off.

    Attributes:
        amount: How much it drains; 0 for a casting that drains nothing.
        pool: The pool it is taken from, as the engine names it.
        left: What that pool would then hold; it may be 0 or below, where
            the caster falls.
    """

    amount: int
    pool: str
    left: int


def casting_drain(actor: CombatantState, spell: Spell, push_mana: int) -> Drain:
    """What a True cast of ``spell``, or the working ``spell`` is, drains
    from ``actor`` when it goes off.

    The engine's own count (``TrueMagicRules.drained_mana`` and ``drain``): a
    working's whole mana, and for a True cast of a catalog spell the mana
    pushed past its cost. Asked only of a True cast or a working; any other
    casting drains nothing, and the engine takes none for it.

    Args:
        actor: The caster.
        spell: The spell True cast, or the working crafted.
        push_mana: The mana pushed past the spell's cost.

    Raises:
        ValueError: The loaded canon has no True Magic.
    """
    rules = true_magic()
    amount = rules.drain(rules.drained_mana(spell, push_mana), None)
    return Drain(
        amount=amount,
        pool=rules.drain_pool,
        left=getattr(actor, rules.drain_pool) - amount,
    )


@dataclass(frozen=True)
class CraftReading:
    """One craft, begun or about to be, as the engine counts it.

    Attributes:
        working: The working crafted.
        effect: The effect it is a tier of.
        turns: The craft actions the working takes.
        mana: Its whole mana, any pushed mana included.
        turns_done: The craft actions already taken.
        gathered: The mana gathered by them.
    """

    working: Spell
    effect: WorkingEffect
    turns: int
    mana: int
    turns_done: int
    gathered: int

    @property
    def next_turn(self) -> int:
        """Which of its turns the craft's next action is, counting from 1."""
        return self.turns_done + 1

    @property
    def kind(self) -> SpellEffectKind | None:
        """What the working does when it goes off: damage, healing, or
        ``None`` for an effect the Game Master describes."""
        return self.effect.kind


def not_yet_begun(
    spell_key: str, *, push_mana: int = 0, borrow: bool = False
) -> CraftState:
    """The craft of a working as it stands before its first craft action.

    Args:
        spell_key: The working's key.
        push_mana: The mana the craft would push past the working's tier.
        borrow: Whether the craft would borrow a share its pool cannot pay.
    """
    return CraftState(
        spell_key=spell_key,
        push_mana=push_mana,
        borrow=borrow,
        turns_done=0,
        borrowed=0,
        target_id=None,
    )


def reading(craft: CraftState) -> CraftReading:
    """A craft's working, its turns and its mana, each the engine's answer.

    Args:
        craft: A craft standing on a figure, or :func:`not_yet_begun`.

    Raises:
        KeyError: A craft whose key is not a working the loaded canon builds.
        ValueError: The loaded canon has no True Magic.
    """
    rules = true_magic()
    working = rules.working_from_key(craft.spell_key)
    effect, _tier = rules.effect_and_tier(craft.spell_key)
    return CraftReading(
        working=working,
        effect=effect,
        turns=rules.turns_for(working.level),
        mana=casting_mana(working, craft.push_mana),
        turns_done=craft.turns_done,
        gathered=rules.gathered(craft),
    )


def share_due(craft: CraftState) -> int:
    """The mana a craft's next craft action gathers (the engine's
    ``TrueMagicRules.share_due``).

    Args:
        craft: A craft with a turn still to take.

    Raises:
        KeyError: A craft whose key is not a working the loaded canon builds.
        ValueError: A craft whose every turn is taken; or the loaded canon
            has no True Magic.
    """
    return true_magic().share_due(craft)
