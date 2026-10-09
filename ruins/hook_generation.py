"""Roll a party-matched adventure hook (issue #276).

The matching rule, stated once: every hook on the reference page carries a
``**Levels:**`` band, and a party rolls as the level nearest its roster's
average (``experience_summary["average_level"]`` rounded half-up, floor 1).
The generator filters the page's hooks to those whose band contains that
level and rolls one die — ``1dN`` over the N eligible hooks, uniform — so
the whole roll is displayed and a seed reproduces it, exactly as the ruin
generator does. A party above every published band gets no silent fallback:
:class:`NoHooksForLevel` says so, and the callers point at Scenario Seeds.
"""

import math
from dataclasses import dataclass

from common.rolling import Roller
from ruins.generation import TableRoll
from ruins.hooks import HookEntry, load_hooks

# Every level band the markdown publishes. The drift guard in
# tests/test_hooks.py asserts the page's bands still union to exactly this,
# so a band typo'd to 3 (or every level-2 hook deleted) fails loudly instead
# of silently changing what the generator can match.
EXPECTED_HOOK_LEVELS = (1, 2)


class NoHooksForLevel(ValueError):
    """The page publishes no hooks banded for the requested level."""

    def __init__(self, level: int, published_levels: tuple[int, ...]):
        self.level = level
        self.published_levels = published_levels
        published = ", ".join(str(value) for value in published_levels)
        super().__init__(
            f"No hooks are banded for level {level}; the page covers "
            f"levels {published}."
        )


@dataclass(frozen=True)
class GeneratedHook:
    """One rolled hook, with the roll preserved for display."""

    seed: int | None
    level: int
    eligible_count: int
    roll: TableRoll
    hook: HookEntry


def published_levels() -> tuple[int, ...]:
    """Every level at least one hook is banded for, ascending."""
    levels: set[int] = set()
    for hook in load_hooks():
        levels.update(hook.levels)
    return tuple(sorted(levels))


def matched_level(average_level: float) -> int:
    """The level a party rolls as: its average, rounded half-up, floor 1."""
    return max(1, math.floor(average_level + 0.5))


def eligible_hooks(level: int) -> tuple[HookEntry, ...]:
    """The hooks whose band contains ``level``, in page order."""
    return tuple(hook for hook in load_hooks() if level in hook.levels)


def generate_hook(level: int, seed: int | None = None) -> GeneratedHook:
    """Roll one hook banded for ``level``.

    Args:
        level: The experience level to match (a party's
            :func:`matched_level`, or a hand-picked band).
        seed: When given, the same seed and level reproduce the same hook.

    Raises:
        NoHooksForLevel: If no published hook is banded for ``level``.
    """
    eligible = eligible_hooks(level)
    if not eligible:
        raise NoHooksForLevel(level, published_levels())
    roller = Roller(seed=seed)
    record = roller.roll(f"1d{len(eligible)}", purpose=f"Hook (level {level})")
    face = record.faces[0]
    hook = eligible[face - 1]
    return GeneratedHook(
        seed=seed,
        level=level,
        eligible_count=len(eligible),
        roll=TableRoll(
            table_title=f"Hook (level {level})",
            die=f"d{len(eligible)}",
            face=face,
            result=hook.title,
            record=record,
        ),
        hook=hook,
    )
