"""How long a turn waits behind other tables' turns for the model (issue #64).

Ollama on the production host answers one request at a time, so a call sent
while other turns are being answered waits in a line, and says nothing until
its turn comes. From inside the call that silence looks exactly like a model
loading or reading a long prompt. What this app does know is its own records:
the turns claimed before this one and not yet finished. While a call has had
no reply yet and some of those are still running, the turn is taken to be
waiting behind them, and the time is counted.

This module knows nothing of Django; the engine is handed a
:class:`ModelQueueWait` whose ``turns_ahead`` asks the records.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

#: The ``waiting`` event's key for how many turns are ahead of this one. It is
#: present only when there are some.
TURNS_AHEAD_KEY = "turns_ahead"


@dataclass
class ModelQueueWait:
    """The time one turn has spent waiting behind other turns.

    The engine calls :meth:`call_started` as each model call is sent,
    :meth:`look` at each silence before that call's first token, and
    :meth:`served` when the first token arrives. A silence after the first
    token is the model working, not the line, and is never counted.

    Attributes:
        turns_ahead: How many other turns are ahead of this one now.
        clock: The clock the waits are measured on; the turn's own.
        waited_seconds: The time counted so far, up to the last look.
        ahead: How many turns were ahead at the last look of the call now
            waiting; ``0`` once it is served, or when none were.
    """

    turns_ahead: Callable[[], int]
    clock: Callable[[], float] = time.monotonic
    waited_seconds: float = 0.0
    ahead: int = 0
    _last_look: float | None = field(default=None, repr=False)

    def call_started(self) -> None:
        """A model call was sent; its wait, if any, starts now."""
        self._last_look = self.clock()
        self.ahead = 0

    def look(self) -> int:
        """Look at the line at a silence before the call's first token.

        The time since the last look is counted as waiting when turns are
        still ahead now: they were ahead for at least the end of it.

        Returns:
            How many turns are ahead of this one.
        """
        now = self.clock()
        self.ahead = self.turns_ahead()
        if self.ahead and self._last_look is not None:
            self.waited_seconds += now - self._last_look
        self._last_look = now
        return self.ahead

    def served(self) -> None:
        """The call's first token arrived; it is no longer waiting."""
        self._last_look = None
        self.ahead = 0

    @property
    def still_waiting(self) -> bool:
        """Whether the call now in flight was behind other turns at last look."""
        return self.ahead > 0

    def waited(self) -> float:
        """Every second this turn has waited behind other turns, up to now.

        A call still behind them at its last look is counted to this moment,
        so a turn the budget ends while it waits has its whole wait counted.
        """
        if self.still_waiting and self._last_look is not None:
            return self.waited_seconds + (self.clock() - self._last_look)
        return self.waited_seconds
