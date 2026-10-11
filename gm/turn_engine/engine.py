"""A Games Master turn as plan → run → narrate (issue #590).

One turn is a small state machine:

GROUNDING
    The state block is built from the records: who is speaking, and the
    session's characters by id with their key figures.
PLANNING
    The model is asked for a JSON plan. A reply that is not a plan is sent
    back once with its errors; a second failure means no actions, said so.
EXECUTING
    Each action runs, in order, through the :class:`Executor`, which changes
    records only through :mod:`characters.services`. Refused actions are
    reported, never run.
NARRATING
    The model is asked to narrate the results, and its tokens stream out.
COMMITTING
    The turn's messages are saved.
DONE / FAILED
    The turn's end, either way.

Both model calls send the same system prompt and history, byte for byte;
only the last message differs, so the model's prefix cache holds (see
:mod:`gm.turn_engine.messages`). The engine knows nothing of HTTP: it yields
:class:`~gm.turn_engine.events.TurnEvent` objects, recorded to a sink as they
happen, and the SSE view relays them.
"""

import hashlib
import logging
import time
from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from common.services.sse import ThinkingFilter

from . import events
from .events import EventSink, TurnEvent
from .messages import PLAN_UNREADABLE, narrate_message, plan_message, replan_message
from .model import KeepAliveTokens, Messages, ModelPort
from .narration import (
    PlanLeakFilter,
    figures_raised,
    with_open_choices,
    with_record_figure,
)
from .plan import PlannedAction, PlanReading, read_plan
from .policy import TurnPolicy
from .state import CampaignBlock, RosterLine, state_block

logger = logging.getLogger(__name__)

#: How long the model may be silent before the engine reports it is waiting.
DEFAULT_KEEPALIVE_SECONDS = 10.0

#: What the page is told while each state runs (issue #569).
STATUS_PLANNING = "planning"
STATUS_WRITING = "writing"


class TurnState(StrEnum):
    """Where a turn is."""

    GROUNDING = "grounding"
    PLANNING = "planning"
    EXECUTING = "executing"
    NARRATING = "narrating"
    COMMITTING = "committing"
    DONE = "done"
    FAILED = "failed"


class TurnBudgetExceeded(Exception):
    """The turn outran its time budget; whatever already ran stands."""


@contextmanager
def _cut_off_is_out_of_time() -> Iterator[None]:
    """Turn a model call cut off at the turn's deadline into the budget running out.

    A port raises ``TimeoutError`` when the deadline it was given passes
    (:meth:`ModelPort.stream`), whether at the call or mid-stream, and the
    only deadline the engine gives is the turn's (issue #904).
    """
    try:
        yield
    except TimeoutError as out_of_time:
        raise TurnBudgetExceeded from out_of_time


@dataclass(frozen=True)
class ActionResult:
    """What one action did, as the narration and the page need it.

    Attributes:
        result: One plain sentence for the narrate message's results.
        ran: Whether the action changed or rolled anything.
        note: What the rules did, for the turn record; ``""`` when refused.
        error: Why it did not run; ``""`` when it ran.
        procedure: The chip frame the page shows for a character change.
        rolls: The dice-line frames for dice it rolled.
    """

    result: str
    ran: bool
    note: str = ""
    error: str = ""
    procedure: dict[str, Any] | None = None
    rolls: tuple[dict[str, Any], ...] = ()


class Executor(Protocol):
    """Runs a plan's actions against the records."""

    def begin(self, actions: Sequence[PlannedAction]) -> None:
        """Read the plan about to run, before its first action runs."""
        ...

    def label(self, action: PlannedAction) -> str:
        """What the page is told while ``action`` runs."""
        ...

    def run(
        self, action: PlannedAction, record: Callable[[TurnEvent], None]
    ) -> ActionResult:
        """Run ``action``, calling ``record`` with its events as they happen.

        An event reporting a change is recorded inside the transaction that
        makes the change.
        """
        ...

    def roster(self) -> list[RosterLine]:
        """The session's characters as they stand now."""
        ...

    def rulings(self) -> list[str]:
        """The table's active rulings as state-block lines (issue #600)."""
        ...

    def campaign(self) -> CampaignBlock:
        """Where the party is, who and what is in play (issue #579)."""
        ...

    def refused(self) -> list[str]:
        """What this session already tried and was refused (issue #614)."""
        ...

    def settled(self) -> dict[str, str]:
        """Actions refused for a reason that cannot change (issue #614)."""
        ...


@dataclass(frozen=True)
class TurnRequest:
    """Everything a turn needs from the view.

    Attributes:
        policy: The turn's policy.
        prefix: The system message and the history before this turn's last
            message, as the model reads them. Sent unchanged on every call.
        latest: The message being answered.
        plan: Whether to plan; ``False`` for a turn whose results are already
            known (a ``/roll``, or a retry of a turn whose actions ran).
        known_results: Results settled before the turn, for such a turn.
        commit: Saves the turn's narration; returns the session id.
    """

    policy: TurnPolicy
    prefix: Messages
    latest: str
    commit: Callable[[str], int]
    plan: bool = True
    known_results: tuple[str, ...] = ()


@dataclass
class TurnOutcome:
    """How a turn ended.

    Attributes:
        narration: The narration saved as the Game Master's reply.
        session_id: The session it was saved to.
        actions: The plan's actions, refused ones included.
        results: What each action did, in order.
        plan_unreadable: Whether planning failed twice, so nothing ran.
    """

    narration: str = ""
    session_id: int | None = None
    actions: tuple[PlannedAction, ...] = ()
    results: list[ActionResult] = field(default_factory=list)
    plan_unreadable: bool = False


def _prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _roster_keys(lines: Sequence[RosterLine]) -> tuple[set[int], dict[str, int]]:
    """The ids a plan may name, and the names a new character may not take.

    Names are casefolded; where two characters share a name (a rename can do
    that), the first listed is the one offered (issue #856).
    """
    held_names: dict[str, int] = {}
    for line in lines:
        held_names.setdefault(line.name.casefold(), line.character_id)
    return {line.character_id for line in lines}, held_names


class TurnEngine:
    """Runs one turn through its states.

    Args:
        model: The model port.
        sink: Where events are recorded as they happen.
        executor: Runs the plan's actions.
        started_at: ``time.monotonic()`` when the turn began.
        budget_seconds: How long the whole turn may take.
        keepalive_seconds: How long the model may be silent before the
            engine yields a ``waiting`` event.
    """

    def __init__(
        self,
        model: ModelPort,
        sink: EventSink,
        executor: Executor,
        *,
        started_at: float,
        budget_seconds: float,
        keepalive_seconds: float = DEFAULT_KEEPALIVE_SECONDS,
    ) -> None:
        self.model = model
        self.sink = sink
        self.executor = executor
        self.started_at = started_at
        self.budget_seconds = budget_seconds
        self.keepalive_seconds = keepalive_seconds
        self.state = TurnState.GROUNDING
        self._logged_prefix: Messages | None = None
        #: Set when the model wrote a plan into its narration (issue #603).
        self.narration_leaked_a_plan = False
        #: The plan it wrote there, which the turn runs rather than drops.
        self.leaked_plan_text = ""
        #: Whether this turn has already run a plan its narration carried.
        self.followed_up = False

    # -- events -----------------------------------------------------------

    def _emit(self, event: TurnEvent) -> TurnEvent:
        self.sink.record(event)
        return event

    def _enter(self, state: TurnState, **details: Any) -> TurnEvent:
        self.state = state
        return self._emit(TurnEvent(events.STATE, {"state": state.value, **details}))

    def _status(self, words: str) -> TurnEvent:
        return TurnEvent(events.STATUS, {"status": words}, persist=False)

    def elapsed(self) -> float:
        """Seconds since the turn began."""
        return time.monotonic() - self.started_at

    @property
    def deadline(self) -> float:
        """The ``time.monotonic()`` at which the turn's budget runs out."""
        return self.started_at + self.budget_seconds

    def _check_budget(self) -> None:
        if self.elapsed() > self.budget_seconds:
            raise TurnBudgetExceeded

    # -- model calls ------------------------------------------------------

    def _request_payload(self, call: str, messages: Messages, prefix: Messages) -> dict:
        """What a model call sent, stored once per turn in full.

        The system prompt is stored by hash (the turn record keeps its text).
        A later call whose prefix matches the first call's is stored as that
        fact plus its own tail, so a turn does not keep the whole history
        twice, and a prefix that drifted is visible in the log.
        """
        payload: dict[str, Any] = {
            "call": call,
            "model": self.model.name,
            "options": dict(self.model.options),
        }
        tail = messages[len(prefix) :]
        if self._logged_prefix is not None and prefix == self._logged_prefix:
            payload["prefix"] = "unchanged"
            payload["prefix_messages"] = len(prefix)
            payload["messages"] = tail
            return payload
        stored = []
        for message in messages:
            if message.get("role") == "system":
                stored.append(
                    {
                        "role": "system",
                        "prompt_sha256": _prompt_hash(message["content"]),
                    }
                )
            else:
                stored.append(message)
        payload["prefix_messages"] = len(prefix)
        payload["messages"] = stored
        self._logged_prefix = list(prefix)
        return payload

    def _call(
        self,
        call: str,
        messages: Messages,
        prefix: Messages,
        *,
        status: str,
        stream_tokens: bool,
    ) -> Generator[TurnEvent, None, str]:
        """Send one model call; yield its tokens or waits; return its text."""
        yield self._emit(
            TurnEvent(
                events.MODEL_REQUEST, self._request_payload(call, messages, prefix)
            )
        )
        raw_parts: list[str] = []
        shown_parts: list[str] = []
        thinking = ThinkingFilter()
        # Only the narrated call reaches the player, and only it can leak a
        # plan into prose (issue #603).
        leak = PlanLeakFilter() if stream_tokens else None
        # The call ends at the turn's deadline even while a read waits on a
        # stream that stalled, and whatever ends the turn early hangs it up
        # when ``tokens`` is closed, so it does not hold the model past the
        # turn once its response has begun (issue #904).
        with _cut_off_is_out_of_time():
            tokens = KeepAliveTokens(
                self.model.stream(messages, stop_at=self.deadline),
                self.keepalive_seconds,
            )
            try:
                for token in tokens:
                    self._check_budget()
                    if token is None:
                        yield TurnEvent(
                            events.WAITING,
                            {"status": status, "elapsed": int(self.elapsed())},
                            persist=False,
                        )
                        continue
                    raw_parts.append(token)
                    visible = thinking.feed(token)
                    if visible is None:
                        continue
                    if leak is not None:
                        visible = leak.feed(visible)
                        if visible is None:
                            continue
                    shown_parts.append(visible)
                    if stream_tokens:
                        yield TurnEvent(events.TOKEN, {"token": visible}, persist=False)
            finally:
                tokens.close()
        if leak is not None:
            held = leak.close()
            if held:
                shown_parts.append(held)
                yield TurnEvent(events.TOKEN, {"token": held}, persist=False)
            if leak.leaked:
                self.narration_leaked_a_plan = True
                self.leaked_plan_text = leak.removed
                logger.warning(
                    "GM wrote a plan into its narration; %d characters withheld "
                    "from the player (issue #603)",
                    len(leak.removed),
                )
        raw = "".join(raw_parts)
        yield self._emit(TurnEvent(events.MODEL_RESPONSE, {"call": call, "raw": raw}))
        return "".join(shown_parts)

    # -- states -----------------------------------------------------------

    def _plan(
        self,
        request: TurnRequest,
        state: str,
        lines: Sequence[RosterLine],
        thread_ids: Sequence[int],
    ) -> Generator[TurnEvent, None, PlanReading]:
        roster_ids, held_names = _roster_keys(lines)
        yield self._enter(TurnState.PLANNING)
        yield self._status(STATUS_PLANNING)
        prefix = request.prefix
        asked = [*prefix, plan_message(state, request.latest, request.policy)]
        reply = yield from self._call(
            "plan", asked, prefix, status=STATUS_PLANNING, stream_tokens=False
        )
        settled = self.executor.settled()
        reading = read_plan(
            reply,
            roster_ids=roster_ids,
            policy=request.policy,
            settled=settled,
            thread_ids=thread_ids,
            held_names=held_names,
        )
        if not reading.ok:
            yield self._emit(
                TurnEvent(
                    events.PLAN_REJECTED, {"attempt": 1, "errors": list(reading.errors)}
                )
            )
            asked = [
                *asked,
                {"role": "assistant", "content": reply},
                replan_message(reading.errors),
            ]
            reply = yield from self._call(
                "replan", asked, prefix, status=STATUS_PLANNING, stream_tokens=False
            )
            reading = read_plan(
                reply,
                roster_ids=roster_ids,
                policy=request.policy,
                settled=settled,
                thread_ids=thread_ids,
                held_names=held_names,
            )
            if not reading.ok:
                yield self._emit(
                    TurnEvent(
                        events.PLAN_REJECTED,
                        {"attempt": 2, "errors": list(reading.errors)},
                    )
                )
                return reading
        yield self._emit(
            TurnEvent(
                events.PLAN,
                {"actions": [action.as_payload() for action in reading.actions]},
            )
        )
        return reading

    def _execute(
        self, actions: Sequence[PlannedAction], outcome: TurnOutcome
    ) -> Generator[TurnEvent]:
        yield self._enter(TurnState.EXECUTING, actions=len(actions))
        self.executor.begin(actions)
        for action in actions:
            self._check_budget()
            yield self._status(self.executor.label(action))
            pending: list[TurnEvent] = []

            def record(event: TurnEvent, pending: list[TurnEvent] = pending) -> None:
                self.sink.record(event)
                pending.append(event)

            result = self.executor.run(action, record)
            outcome.results.append(result)
            yield from pending

    def _narrate(
        self, request: TurnRequest, results: Sequence[str]
    ) -> Generator[TurnEvent, None, str]:
        """Write the reply the player reads, from the results so far."""
        yield self._enter(TurnState.NARRATING)
        yield self._status(STATUS_WRITING)
        rulings = self.executor.rulings()
        narrate_state = state_block(
            request.policy,
            self.executor.roster(),
            rulings,
            self.executor.campaign(),
            self.executor.refused(),
        )
        prefix = request.prefix
        return (
            yield from self._call(
                "narrate",
                [
                    *prefix,
                    narrate_message(
                        narrate_state,
                        list(results),
                        request.latest,
                        request.policy,
                        has_rulings=bool(rulings),
                    ),
                ],
                prefix,
                status=STATUS_WRITING,
                stream_tokens=True,
            )
        )

    def _leaked_plan(
        self,
        request: TurnRequest,
        lines: Sequence[RosterLine],
        thread_ids: Sequence[int],
    ) -> tuple[PlannedAction, ...]:
        """The actions the narration asked for, when it wrote a plan (#603).

        A plan written during narration is the model saying it wants to act
        and reaching for the only channel left to it. Suppressing the text
        keeps the machinery off the player's screen; running the actions is
        what keeps the turn from recording nothing.

        Returns an empty tuple when nothing leaked, when what leaked does not
        read as a plan, when the turn has already done its one follow-up, or
        when the turn does not plan at all. A turn whose results are already
        known -- a ``/roll``, or a Retry of a turn whose actions ran before it
        broke off -- must not act again: its actions stood, and a plan its
        narration carried would run them a second time across the break
        (issue #782).

        Args:
            request: The turn being answered.
            lines: The session's characters as they stand after the first
                plan ran, so a character it started can be named (issue
                #834), and not started a second time (issue #856).
            thread_ids: The open threads as they stand after the first plan
                ran, so a thread it opened can be closed and one it closed is
                not offered again (issue #834).
        """
        if not self.narration_leaked_a_plan or self.followed_up:
            return ()
        self.followed_up = True
        leaked = self.leaked_plan_text
        self.narration_leaked_a_plan = False
        self.leaked_plan_text = ""
        if not request.plan:
            logger.warning(
                "GM narration carried a plan on a turn that does not plan; "
                "dropped, %d characters (issue #782)",
                len(leaked),
            )
            return ()
        roster_ids, held_names = _roster_keys(lines)
        reading = read_plan(
            leaked,
            roster_ids=roster_ids,
            policy=request.policy,
            settled=self.executor.settled(),
            thread_ids=thread_ids,
            held_names=held_names,
        )
        if not reading.ok or not reading.actions:
            logger.info("GM narration carried a plan that did not read; dropped")
            return ()
        self._emit(
            TurnEvent(
                events.PLAN,
                {
                    "actions": [action.as_payload() for action in reading.actions],
                    "from": "narration",
                },
            )
        )
        return tuple(reading.actions)

    def run(self, request: TurnRequest) -> Generator[TurnEvent, None, TurnOutcome]:
        """Run the turn, yielding its events; returns how it ended.

        Raises:
            TurnBudgetExceeded: The turn ran out of time. Actions that already
                ran stand; nothing is committed.
        """
        outcome = TurnOutcome()
        try:
            yield self._enter(TurnState.GROUNDING)
            lines = self.executor.roster()
            campaign = self.executor.campaign()
            state = state_block(
                request.policy,
                lines,
                self.executor.rulings(),
                campaign,
                self.executor.refused(),
            )
            results: list[str] = list(request.known_results)
            if request.plan:
                reading = yield from self._plan(
                    request, state, lines, campaign.open_thread_ids
                )
                if reading.ok:
                    outcome.actions = reading.actions
                    yield from self._execute(reading.actions, outcome)
                    results.extend(result.result for result in outcome.results)
                else:
                    outcome.plan_unreadable = True
                    results.append(PLAN_UNREADABLE)
            narration = yield from self._narrate(request, results)
            # The model sometimes answers with the plan it wanted to run next
            # instead of planning it -- most often after seeing results it
            # wants to act on. Running it here is what stops the turn
            # recording nothing (issue #603). Once only: a second pass is a
            # follow-up, a third would be a loop.
            # Read again after the first plan ran: a character it started or
            # a thread it opened is one the follow-up may name, and a thread
            # it closed is not (issue #834).
            followup = self._leaked_plan(
                request,
                self.executor.roster(),
                self.executor.campaign().open_thread_ids,
            )
            if followup:
                outcome.actions = (*outcome.actions, *followup)
                yield from self._execute(followup, outcome)
                # What was known before the turn stays in what the second
                # narration is told, beside everything that ran (issue #782).
                results = [
                    *request.known_results,
                    *(result.result for result in outcome.results),
                ]
                narration = yield from self._narrate(request, results)
            # A reply that was asked for a figure the records hold must
            # carry it. The model states one about two turns in three and
            # otherwise answers in prose with no number in it, so the server
            # quotes the record (issue #622). Both manners: the aide is
            # asked for these figures more often than the game is. Before
            # the open-choices line, so the answer and the record it comes
            # from stay together and the promise about the menu ends the
            # reply, where a player looking at the menu will read it.
            # The roster and the people this session has recorded settle
            # whose record a question is about -- and whether it is about a
            # record at all: "level" and "body" name the dungeon and a
            # corpse as readily (issue #649).
            asked_about = self.executor.roster()
            met = self.executor.campaign().person_names
            raised = figures_raised(
                request.latest,
                (action.action for action in outcome.actions),
                names=[
                    word for line in asked_about for word in (line.name, line.label)
                ],
            )
            if raised:
                narration = with_record_figure(
                    narration,
                    topics=raised,
                    lines=asked_about,
                    message=request.latest,
                    others=met,
                )
            # The guidelines promise a player that a numbered list is never
            # the whole of what they may do, and the model forgets about one
            # turn in three (issues #342, #372).
            if not request.policy.is_aide:
                narration = with_open_choices(narration)
            outcome.narration = narration.strip()
            yield self._emit(
                TurnEvent(events.NARRATION, {"text": outcome.narration}, visible=True)
            )
            yield self._enter(TurnState.COMMITTING)
            outcome.session_id = request.commit(outcome.narration)
            yield self._enter(TurnState.DONE, session_id=outcome.session_id)
            return outcome
        except TurnBudgetExceeded:
            self._emit_failure("the turn ran out of time")
            raise
        except Exception as error:
            self._emit_failure(str(error) or error.__class__.__name__)
            raise

    def _emit_failure(self, error: str) -> None:
        """Record why the turn stopped; the caller reports it to the player."""
        try:
            self._emit(
                TurnEvent(events.FAILED, {"state": self.state.value, "error": error})
            )
            self.state = TurnState.FAILED
            self._emit(TurnEvent(events.STATE, {"state": TurnState.FAILED.value}))
        except Exception:
            logger.exception("Could not record why a GM turn failed")
