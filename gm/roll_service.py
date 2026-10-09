"""Rolling the dice a Games Master turn asked for (issues #363, #590).

A plan's ``roll`` action (:mod:`gm.turn_engine.plan`) becomes a
:class:`RollRequest`, rolled through ``common.rolling`` -- the project's one
roll service, reached the way every other GM roll reaches it, via
:func:`gm.engine.roll_dice_record` -- so there is no second source of
randomness. The faces go to the player as a dice line and to the model as a
result.

A request the roll service refuses is never turned into a number: the
resolution carries the error, and the player and the model are both told no
dice fell.

Django-free on purpose, like :mod:`gm.engine`.
"""

from dataclasses import dataclass, replace

from common.rolling import RollRecord

from .engine import roll_dice_record

__all__ = [
    "RollRequest",
    "RollResolution",
    "dice_line",
    "resolve_roll_request",
    "resolve_roll_series",
    "roll_frame_payload",
]


@dataclass(frozen=True)
class RollRequest:
    """One roll the GM asked the server to make.

    Attributes:
        expression: The dice notation, verbatim -- ``common.rolling`` decides
            whether it is rollable.
        purpose: What the roll is for; ``""`` when nothing was said.
        roller: Who rolls; ``""`` when nobody was named.
        times: How many separate rolls of ``expression``. Each is rolled,
            shown and reported on its own; this is never a bigger dice pool.
        injury_penalty: The roller's injury-table penalty (-1 or -2 to all
            rolls, ``characters.skill_checks.injury_penalty``), or 0 (#886).
        direction: ``"over"`` when a higher total is better, ``"under"``
            when the roll must come in at or under a score, or ``""`` when
            the plan did not say, in which case the penalty is named and not
            applied: which way it counts depends on the roll.
        game_master_call: What a Game Master called the roll past, named on
            its line (an unconscious roller), or ``""``.
    """

    expression: str
    purpose: str = ""
    roller: str = ""
    times: int = 1
    injury_penalty: int = 0
    direction: str = ""
    game_master_call: str = ""

    def counts_as(self, total: int) -> int | None:
        """What ``total`` counts as with the injury penalty, or None.

        None when there is no penalty, or no direction to apply it in. "-1
        to all rolls" makes a roll worse whichever way it is read: a roll
        over a number loses the point, and a roll that must come in under a
        score counts a point higher.
        """
        if not self.injury_penalty or self.direction not in ("over", "under"):
            return None
        if self.direction == "over":
            return total + self.injury_penalty
        return total - self.injury_penalty

    @property
    def label(self) -> str:
        """A short human phrase for the request, for the player's dice line."""
        parts = [self.expression]
        if self.purpose:
            parts.append(f"for {self.purpose}")
        if self.roller:
            parts.append(f"({self.roller})")
        return " ".join(parts)


@dataclass(frozen=True)
class RollResolution:
    """What the server made of one roll the GM asked for.

    Exactly one of ``record`` and ``error`` is set.

    Attributes:
        request: The request.
        record: The roll the service made, or ``None`` when it refused.
        error: Why the service refused, or ``""`` when it rolled.
    """

    request: RollRequest
    record: RollRecord | None = None
    error: str = ""

    @property
    def rolled(self) -> bool:
        """Whether real dice actually fell."""
        return self.record is not None


def resolve_roll_request(request: RollRequest) -> RollResolution:
    """Roll ``request`` through the project's roll service.

    Returns:
        The resolution: a :class:`common.rolling.RollRecord` when the
        expression is rollable, or the roll service's own error when it is
        not. No exception escapes and nothing is invented.
    """
    purpose = request.purpose or "gm"
    try:
        record = roll_dice_record(request.expression, purpose=purpose)
    except ValueError as error:
        return RollResolution(request=request, error=str(error))
    return RollResolution(request=request, record=record)


def resolve_roll_series(request: RollRequest) -> list[RollResolution]:
    """Every roll one request asked for, in order (issue #376).

    ``times`` *separate* rolls of the same expression, never one larger pool:
    six 3d6 give six totals in 3..18, while one 6d6 gives a single total
    with no honest way to cut it into six.

    Returns:
        One :class:`RollResolution` per roll, always at least one, each for a
        single-roll request.
    """
    single = replace(request, times=1)
    return [resolve_roll_request(single) for _ in range(max(1, request.times))]


def _faces_text(record: RollRecord) -> str:
    """The faces of ``record`` as ``"4, 3"``."""
    return ", ".join(str(face) for face in record.faces)


def dice_line(resolution: RollResolution) -> str:
    """The one-line dice chip the player reads for ``resolution``.

    Built server-side and carried in the frame as ``line``, so the live turn
    and a reloaded session render the identical string.

    Returns:
        The chip text, e.g.
        ``"🎲 GM rolls 2d6 for the patrol's reaction: [4, 3] = 7"``.
    """
    request = resolution.request
    who = request.roller or "GM"
    what = f" for {request.purpose}" if request.purpose else ""
    if resolution.record is None:
        return (
            f"🎲 {who} asked for {request.expression}{what}, but no dice were "
            f"rolled: {resolution.error}"
        )
    record = resolution.record
    line = (
        f"🎲 {who} rolls {record.specification}{what}: "
        f"[{_faces_text(record)}] = {record.total}"
    )
    if request.injury_penalty:
        counted = request.counts_as(record.total)
        if counted is not None:
            line += (
                f", {request.injury_penalty:+d} injured on a roll "
                f"{request.direction}: counts as {counted}"
            )
        else:
            line += (
                f"; injured, {request.injury_penalty:+d} to all rolls, not "
                "applied: the plan did not say whether this roll is made over "
                "or under a number"
            )
    if request.game_master_call:
        line += f" (called by the Game Master: {request.game_master_call})"
    return line


def roll_frame_payload(resolution: RollResolution) -> dict[str, object]:
    """The dice line as the SSE frame and the page both want it.

    Returns:
        ``expression``/``purpose``/``roller``/``line`` always;
        ``rolls``/``total`` when dice fell; ``error`` when they did not.
    """
    request = resolution.request
    payload: dict[str, object] = {
        "expression": request.expression,
        "purpose": request.purpose,
        "roller": request.roller,
        "line": dice_line(resolution),
    }
    if resolution.record is None:
        payload["error"] = resolution.error
        return payload
    payload["expression"] = resolution.record.specification
    payload["rolls"] = _faces_text(resolution.record)
    payload["total"] = resolution.record.total
    if request.injury_penalty:
        # The dice's own total stays ``total``; what it counts as is its own
        # key, set only when the penalty was applied (#886).
        payload["injury_penalty"] = request.injury_penalty
        counted = request.counts_as(resolution.record.total)
        if counted is not None:
            payload["counts_as"] = counted
    return payload
