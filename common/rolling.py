"""The single die-roll service (issue #175).

Every die the project rolls comes through :class:`Roller`, and every roll is
describable as a :class:`RollRecord` — faces, modifier, total, and what the
roll was for. ``characters/dice.py`` keeps its existing public API but draws
its randomness from a module-default ``Roller``; ``gm/engine.py`` reaches the
same path through ``characters.dice.roll_expression``.

Pure Python on purpose: no Django imports anywhere in this module's import
chain, because ``gm/engine.py`` (imported standalone by the offline CLI)
depends on it transitively.
"""

import random
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol

# The dice-notation grammar lives in the shared tarmar-engine package (it
# was vendored there from this module in #240 milestone 1 and folded back
# onto one source in milestone 2). The pattern and bounds are re-exported so
# historic imports keep working.
from tarmar_engine.dice import (  # noqa: F401  (re-exported names)
    DICE_SPECIFICATION_PATTERN,
    MAX_DICE_COUNT,
    MAX_DIE_SIDES,
    MIN_DICE_COUNT,
    MIN_DIE_SIDES,
    parse_dice_expression,
)


def parse_dice_specification(specification: str) -> tuple[int, int, int]:
    """Parse a dice specification like ``3d6`` or ``2d6-2``.

    The project's single dice-notation parser (issues #105 and #175): weapon
    damage rolls, the GM's ``/roll``, and :meth:`Roller.roll` all come through
    here. ``characters.dice.parse_dice_expression`` is a re-export of this
    function, so the historic error messages are load-bearing — the shared
    ``tarmar_engine.dice`` parser this delegates to keeps them verbatim.

    Args:
        specification: A dice specification in ``NdS`` or ``NdS±M`` form.
            Case-insensitive; surrounding whitespace is ignored.

    Returns:
        A ``(count, sides, modifier)`` tuple.

    Raises:
        ValueError: If the specification does not parse, or if the die count
            or side count falls outside the package's bounds.
    """
    return parse_dice_expression(specification)


def standard_dice_specification(specification: str) -> str:
    """The one way of writing a dice specification: ``NdS``, or ``NdS±M``.

    No whitespace, a lower-case ``d``, no leading zeros, and no ``+0``, so
    ``" 2D6 "``, ``"02d06"`` and ``"2d6+0"`` are all ``"2d6"``. A caller that
    stores dice (a canon rules file, issue #59) compares a written
    specification with this to refuse any other spelling.

    Args:
        specification: A dice specification :func:`parse_dice_specification`
            accepts.

    Returns:
        The specification in standard form.

    Raises:
        ValueError: If the specification does not parse or is out of bounds.
    """
    count, sides, modifier = parse_dice_specification(specification)
    return f"{count}d{sides}{modifier:+d}" if modifier else f"{count}d{sides}"


def dice_total_range(specification: str) -> tuple[int, int]:
    """The lowest and highest totals a dice specification can roll.

    Args:
        specification: ``NdS`` or ``NdS±M``, e.g. ``"2d6"``.

    Returns:
        ``(lowest, highest)``: every die showing 1, and every die showing its
        top face, each with the modifier added.

    Raises:
        ValueError: If the specification does not parse or is out of bounds.
    """
    count, sides, modifier = parse_dice_specification(specification)
    return count + modifier, count * sides + modifier


@dataclass(frozen=True)
class RollRecord:
    """One resolved die roll, complete enough to display or replay.

    Attributes:
        purpose: What the roll was for, e.g. ``"attack"``, ``"damage"``,
            ``"casting"``, ``"survival"``.
        specification: The normalized dice specification, e.g. ``"1d20"``,
            ``"3d6"``, ``"2d6+3"``.
        faces: The individual die faces, in the order rolled.
        modifier: The flat modifier applied to the sum of the faces — the
            specification's own ``±M`` plus any caller-supplied modifier.
        total: ``sum(faces) + modifier``.
        target_number: The number the total was compared against, when the
            roll had one.
        outcome: The resolved result when the caller knows it, e.g. ``"hit"``,
            ``"miss"``, ``"critical"``, ``"fumble"``, ``"success"``.
    """

    purpose: str
    specification: str
    faces: tuple[int, ...]
    modifier: int
    total: int
    target_number: int | None
    outcome: str | None


class _RandintSource(Protocol):
    """Anything with ``randint`` — a ``random.Random`` or the module itself."""

    def randint(self, a: int, b: int, /) -> int: ...


#: The source an unseeded :class:`Roller` draws from inside
#: :func:`pinned_dice`, or ``None`` outside it. A ``ContextVar`` rather than a
#: module global so pinning one request's dice cannot reach a request being
#: served on another thread of the same process.
_PINNED_SOURCE: ContextVar[random.Random | None] = ContextVar(
    "pinned_dice_source", default=None
)


@contextmanager
def pinned_dice(seed: int) -> Iterator[None]:
    """Make every unseeded roll inside the block come from ``Random(seed)``.

    The browser tests' way to fix a roll the page makes through the server
    (issue #959): ``common.middleware.PinnedDiceMiddleware`` wraps one request
    in this when a test asks for it, and that middleware is installed only in
    a DEBUG process started with ``E2E_DICE_SEEDING`` (``settings.py`` refuses
    the switch otherwise). Pure Python, like the rest of this module.

    A :class:`Roller` built with its own seed keeps drawing from that seed;
    only the module-global fallback is replaced. The source is fresh on every
    entry, so the same seed around the same calls gives the same faces.

    Args:
        seed: Seeds the private ``random.Random`` the block's rolls draw from.

    Yields:
        Nothing; the pin holds until the block exits, however it exits.
    """
    token = _PINNED_SOURCE.set(random.Random(seed))
    try:
        yield
    finally:
        _PINNED_SOURCE.reset(token)


class Roller:
    """Rolls dice from one randomness source and records every roll.

    Args:
        seed: When given, faces come from a private ``random.Random(seed)``,
            so the same seed reproduces the same rolls (the seeding hook the
            battle simulator, issue #176, needs). When ``None``, faces come
            from the module-global ``random`` at call time, so ``random.seed()``
            and test patches of ``<module>.random.randint`` keep working for
            the module-default rollers the existing call sites use -- except
            inside :func:`pinned_dice`, which supplies the source instead.
        on_roll: Called with each :class:`RollRecord` as it is produced, so a
            caller (e.g. a battle engine) can capture every roll without the
            roller knowing anything about persistence.
    """

    def __init__(
        self,
        seed: int | None = None,
        on_roll: Callable[[RollRecord], None] | None = None,
    ) -> None:
        self._seeded_source: random.Random | None = (
            random.Random(seed) if seed is not None else None
        )
        self._on_roll = on_roll

    def roll(
        self,
        specification: str,
        *,
        purpose: str,
        modifier: int = 0,
        target_number: int | None = None,
        outcome: str | None = None,
    ) -> RollRecord:
        """Roll a dice specification and return the full record.

        Args:
            specification: ``NdS`` or ``NdS±M``, e.g. ``"2d6+3"``. A ``±M``
                embedded in the specification and the ``modifier`` argument
                are summed into the record's single ``modifier``.
            purpose: What the roll is for (recorded verbatim).
            modifier: Flat modifier added on top of the specification's own.
            target_number: The number the total is being compared against,
                when the caller has one.
            outcome: The resolved result, when the caller already knows it.

        Returns:
            The completed :class:`RollRecord`. The ``on_roll`` callback, when
            set, has already seen it.

        Raises:
            ValueError: If the specification does not parse or is out of
                bounds (see :func:`parse_dice_specification`).
        """
        count, sides, specification_modifier = parse_dice_specification(specification)
        total_modifier = specification_modifier + modifier
        source = self._randomness_source()
        faces = tuple(source.randint(1, sides) for _ in range(count))
        record = RollRecord(
            purpose=purpose,
            specification=specification.strip().lower(),
            faces=faces,
            modifier=total_modifier,
            total=sum(faces) + total_modifier,
            target_number=target_number,
            outcome=outcome,
        )
        if self._on_roll is not None:
            self._on_roll(record)
        return record

    def _randomness_source(self) -> _RandintSource:
        """The source this roll draws from, resolved at call time.

        Its own seed first; then a :func:`pinned_dice` block's source; then
        the module-global ``random``, read now rather than at construction so
        ``random.seed()`` and patches of ``random.randint`` reach it.
        """
        if self._seeded_source is not None:
            return self._seeded_source
        pinned_source = _PINNED_SOURCE.get()
        if pinned_source is not None:
            return pinned_source
        return random
