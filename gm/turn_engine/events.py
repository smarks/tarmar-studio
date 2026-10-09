"""What a turn reports as it runs, and where that goes (issue #590).

The engine yields :class:`TurnEvent` objects in order. Each one is handed to
an :class:`EventSink` first: the database sink appends the persistent ones to
``GMEvent`` for the turn, so the log is written as things happen rather than
at the end. The SSE view then relays the ones a page shows.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..models import GMEvent

#: The turn entered a state; ``payload["state"]`` names it.
STATE = "state"
#: A model call was sent; the payload is enough to send it again.
MODEL_REQUEST = "model_request"
#: A model call's raw reply, exactly as the model wrote it.
MODEL_RESPONSE = "model_response"
#: The plan as read: its actions, with any refusals.
PLAN = "plan"
#: A planning reply that was not a plan, with the errors sent back.
PLAN_REJECTED = "plan_rejected"
#: What one action did, or why it did not run.
ACTION = "action"
#: Dice the plan asked for, rolled.
ROLL = "roll"
#: A table ruling recorded or retired (issue #600); ``payload["change"]`` is
#: ``"recorded"`` or ``"retired"``.
RULING = "ruling"
#: The narration, whole, once the model finished it.
NARRATION = "narration"
#: A long session's older turns were folded into a recap before the turn
#: was planned (issue #783): whether it was kept, how far it reaches, and how
#: long it took.
FOLD = "fold"
#: The turn stopped part-way; ``payload["error"]`` says why.
FAILED = "failed"

# Transient kinds, relayed to the page and never stored.
#: A piece of the narration, as it streams.
TOKEN = "token"
#: What the Game Master is doing, in the page's words.
STATUS = "status"
#: Nothing arrived from the model for a whole keep-alive interval.
WAITING = "waiting"


@dataclass(frozen=True)
class TurnEvent:
    """One thing a turn reports.

    Attributes:
        kind: One of the kinds above.
        payload: What happened, as JSON-ready values.
        visible: Whether the player is shown it.
        persist: Whether it belongs in the turn's event log.
    """

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    visible: bool = False
    persist: bool = True


class EventSink(Protocol):
    """Where a turn's events are recorded as they happen."""

    def record(self, event: TurnEvent) -> None:
        """Record ``event``; called before the event is relayed anywhere."""
        ...


@dataclass
class MemorySink:
    """Keeps every event in a list, for tests and for turns with no record."""

    events: list[TurnEvent] = field(default_factory=list)

    def record(self, event: TurnEvent) -> None:
        self.events.append(event)

    def kinds(self) -> list[str]:
        """The kinds recorded, in order."""
        return [event.kind for event in self.events]


@dataclass
class DatabaseSink:
    """Appends a turn's persistent events to ``GMEvent``, numbered in order.

    Called inside whatever transaction the engine or an action has open, so an
    action's event commits or rolls back with the change it reports.
    """

    turn_id: int
    seq: int = 0

    def record(self, event: TurnEvent) -> None:
        if not event.persist:
            return
        self.seq += 1
        GMEvent.objects.create(
            turn_id=self.turn_id,
            seq=self.seq,
            kind=event.kind,
            visible=event.visible,
            payload=event.payload,
        )
