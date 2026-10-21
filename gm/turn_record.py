"""A detailed record of every GM turn, kept for debugging (issue #389).

The session's history is what the model reads, so it can only hold messages.
What a turn decided besides its messages -- the mode and exact prompt it ran
with, the model and options, the time it took, what the ledger did, and
whether it failed -- is recorded here instead, on :class:`gm.models.GMTurnRecord`.

A :class:`TurnRecorder` is opened when a turn starts, before anything can go
wrong, and finished once on whichever path the turn leaves by.
"""

import hashlib
import logging
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from characters.services import record_entry_into_play
from common.context_processors import git_info
from common.refusal_words import refusal_words as common_refusal_words

from .models import GMPromptSnapshot, GMTurnRecord
from .turn_engine.policy import CAMPAIGN_ACTIONS

#: The actions that begin play (the coordinator's ruling on #803, revised):
#: the campaign actions and a house ruling recorded. Years, the other
#: creation actions, narration and questions never do.
PLAY_ACTIONS: frozenset[str] = CAMPAIGN_ACTIONS | {"record_house_rule"}


def turn_is_play(procedures: Sequence[dict]) -> bool:
    """Whether a completed turn began play (issue #803).

    The coordinator's ruling under the standing rule (Spencer may overrule):
    play begins only with a campaign action (``move_party``, ``advance_time``,
    the supply, entity and thread actions) or a house ruling recorded, in
    either session mode -- the aide's as much as play's -- or with a fight,
    through the battle it starts. Narration and questions never count, and
    prior-experience years are part of creation. A turn that both plays years
    and runs one of these enters play after its years apply.

    Args:
        procedures: The turn's procedure frames; each names its action as
            ``kind``.
    """
    return any(
        frame.get("ran") and str(frame.get("kind", "")) in PLAY_ACTIONS
        for frame in procedures
    )


logger = logging.getLogger(__name__)


def prompt_hash(text: str) -> str:
    """The key a system prompt is stored under: its full SHA-256."""
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class TurnRecorder:
    """Everything known about a turn when it starts, and the clock since.

    Attributes:
        user: Who sent the turn.
        session_id: The session it belongs to, or ``None`` when this turn is
            the one that creates it; :meth:`finish` takes the new id.
        command: The slash command, or ``""`` for an ordinary message.
        mode: The mode the turn ran in.
        is_game_master: Whether the account carried Game Master authority.
        user_message: What the player sent.
        history_start: How many history entries the session held before it.
        system_prompt: The system prompt exactly as sent.
        model: The Ollama model.
        options: The options object sent with it.
        finished: Whether :meth:`finish` has already written the record.
    """

    user: Any
    session_id: int | None
    command: str
    mode: str
    is_game_master: bool
    user_message: str
    history_start: int
    system_prompt: str
    model: str
    options: dict[str, Any]
    started_at: datetime = field(default_factory=timezone.now)
    started_monotonic: float = field(default_factory=time.monotonic)
    finished: bool = False
    # The running record :meth:`start` wrote, which :meth:`finish` completes.
    record_id: int | None = None

    def _snapshot(self) -> GMPromptSnapshot | None:
        # A call made with no system prompt -- the foe call of a /fight
        # (issue #836) -- has none to keep.
        if not self.system_prompt:
            return None
        snapshot, _created = GMPromptSnapshot.objects.get_or_create(
            prompt_hash=prompt_hash(self.system_prompt),
            defaults={"text": self.system_prompt},
        )
        return snapshot

    def start(self) -> None:
        """Write this turn's record as running (issue #515).

        Unlike :meth:`finish`, a failure here is not swallowed: the running
        record is what refuses a second message to the session while this one
        is under way, so a turn that cannot write it must not start.
        """
        record = GMTurnRecord.objects.create(
            session_id=self.session_id,
            user=self.user if getattr(self.user, "pk", None) else None,
            started_at=self.started_at,
            outcome=GMTurnRecord.OUTCOME_RUNNING,
            command=self.command,
            mode=self.mode,
            is_game_master=self.is_game_master,
            model=self.model,
            options=self.options,
            prompt=self._snapshot(),
            app_version=git_info(None)["git_sha_short"],
            user_message=self.user_message,
            history_start=self.history_start,
        )
        self.record_id = record.pk

    def attach_session(self, session_id: int) -> None:
        """Name the session a running turn created part-way (issue #515).

        A procedure on the first message creates the session mid-turn and the
        page moves to it; the running record has to say so, or a reload of
        that session would not know its turn is still coming.
        """
        self.session_id = session_id
        if self.record_id is not None:
            GMTurnRecord.objects.filter(pk=self.record_id).update(session_id=session_id)

    def finish(
        self,
        outcome: str,
        *,
        session_id: int | None = None,
        reply: str = "",
        history_end: int | None = None,
        gm_rolls: Sequence[dict] = (),
        procedures: Sequence[dict] = (),
        ledger_applied: Sequence[str] = (),
        ledger_refused: Sequence[str] = (),
        notices: Sequence[str] = (),
        error: str = "",
    ) -> None:
        """Write the record for this turn.

        The record exists to debug the turn, so it must never be what breaks
        it: a failure to write it is logged with its traceback and the turn
        carries on. It is logged rather than shown to the player because the
        player has nothing to do about it.

        Args:
            outcome: One of the ``GMTurnRecord.OUTCOME_*`` values.
            session_id: The session's id, when the turn created it.
            reply: The GM's reply as persisted.
            history_end: The history length after the turn saved, or ``None``
                when it saved nothing.
            gm_rolls: The roll frames the turn produced.
            procedures: The procedure frames the turn produced.
            ledger_applied: What the ledger applied after the reply.
            ledger_refused: What the ledger refused after the reply.
            notices: Grounding notices the player was shown.
            error: Why the turn failed, when it did.
        """
        # One record per turn. A page that disconnects after the done frame
        # was already recorded would otherwise add a second, contradictory one.
        if self.finished:
            return
        self.finished = True
        duration_ms = int((time.monotonic() - self.started_monotonic) * 1000)
        outcome_fields = {
            "session_id": session_id if session_id is not None else self.session_id,
            "duration_ms": duration_ms,
            "outcome": outcome,
            "reply": reply,
            "history_end": history_end,
            "gm_rolls": list(gm_rolls),
            "procedures": list(procedures),
            "ledger_applied": list(ledger_applied),
            "ledger_refused": list(ledger_refused),
            "notices": list(notices),
            "error": error,
        }
        self._write(outcome, outcome_fields)
        session_id = outcome_fields["session_id"]
        if (
            outcome == GMTurnRecord.OUTCOME_OK
            and session_id
            and turn_is_play(procedures)
        ):
            # A completed turn of play puts the session's complete characters
            # in play, once (issue #803). Guarded on its own, after the
            # record is written, so a failure here never costs the record.
            try:
                record_entry_into_play(session_id=session_id)
            except Exception:
                logger.exception(
                    "Could not record entry into play for session %s; the turn "
                    "record is kept",
                    session_id,
                )

    def _write(self, outcome: str, outcome_fields: dict[str, Any]) -> None:
        """Write the finished record: the running one updated, or a new one."""
        try:
            if self.record_id is not None:
                # The running record :meth:`start` wrote becomes the finished
                # one, so a turn still has exactly one record (issue #515).
                GMTurnRecord.objects.filter(pk=self.record_id).update(**outcome_fields)
                return
            record = GMTurnRecord.objects.create(
                user=self.user if getattr(self.user, "pk", None) else None,
                started_at=self.started_at,
                command=self.command,
                mode=self.mode,
                is_game_master=self.is_game_master,
                model=self.model,
                options=self.options,
                prompt=self._snapshot(),
                app_version=git_info(None)["git_sha_short"],
                user_message=self.user_message,
                history_start=self.history_start,
                **outcome_fields,
            )
            self.record_id = record.pk
        except Exception:
            logger.exception(
                "Could not record GM turn (outcome %s); the turn is unaffected",
                outcome,
            )


# How long past its budget a running turn may still be saving -- the reply is
# followed by the ledger's own model call -- before it is taken to have died
# with its worker, say in a deploy (issue #515). Past this, its session takes
# new messages again and a waiting page is told the turn did not finish.
RUNNING_TURN_GRACE_SECONDS = 180.0

STALE_RUNNING_TURN_ERROR = "the server stopped before this turn finished"


def _running_turns_alive_since() -> datetime:
    """The start time before which a running turn is taken to have died.

    A continued session's first turn writes its recap, within
    ``GM_RECAP_BUDGET_SECONDS``, before its own budget starts (issue #936),
    so a turn is given both before it is taken to have died.
    """
    limit = (
        float(settings.GM_RECAP_BUDGET_SECONDS)
        + float(settings.GM_STREAM_BUDGET_SECONDS)
        + RUNNING_TURN_GRACE_SECONDS
    )
    return timezone.now() - timedelta(seconds=limit)


def expire_stale_running_turns() -> None:
    """Mark running turns that can no longer be running as failed (issue #515)."""
    GMTurnRecord.objects.filter(
        outcome=GMTurnRecord.OUTCOME_RUNNING,
        started_at__lt=_running_turns_alive_since(),
    ).update(outcome=GMTurnRecord.OUTCOME_ERROR, error=STALE_RUNNING_TURN_ERROR)


def turns_ahead_of(record_id: int) -> int:
    """How many turns are ahead of this one for the model (issue #64).

    The model answers one request at a time, and this app's own view of who
    is waiting for it is the turns claimed and not yet finished: every
    player's, every session's, fights included. Those claimed before this
    one, and still within the time a live turn can take, are counted as ahead
    of it. A turn that died with its worker is not, though nothing has marked
    it yet; this is asked every few seconds while a turn waits, so it only
    reads.

    It is an estimate of the model's own line, which this app cannot see: a
    turn claimed earlier may be between its calls just then, and anything
    that asks the model outside a claimed turn is not counted.

    Args:
        record_id: The waiting turn's record.

    Returns:
        The number of running turns claimed before it.
    """
    return GMTurnRecord.objects.filter(
        outcome=GMTurnRecord.OUTCOME_RUNNING,
        pk__lt=record_id,
        started_at__gte=_running_turns_alive_since(),
    ).count()


def running_turn_for(user: Any, session_id: int) -> GMTurnRecord | None:
    """The turn still running for ``user`` in this session, if there is one.

    Args:
        user: The session's owner.
        session_id: The session. A turn that has not created its session yet
            is deliberately not findable this way, and the parameter will not
            take ``None``: such a turn names no session, so the query would
            match on nothing but "this player has one running" and hand a
            blank page whatever turn another tab had started (issue #650).
            The page that sent it remembers it for itself and asks after it
            by id through :func:`gm.views.gm_turn`.

    Returns:
        The newest running record, or ``None``.
    """
    expire_stale_running_turns()
    return (
        GMTurnRecord.objects.filter(
            user=user,
            session_id=session_id,
            outcome=GMTurnRecord.OUTCOME_RUNNING,
        )
        .order_by("-started_at", "-pk")
        .first()
    )


@contextmanager
def released_on_failure(recorder: TurnRecorder) -> Iterator[None]:
    """Release a claimed turn when its request fails before answering (#139).

    A claim (:func:`claim_turn`) holds the session's one turn until the
    record is finished. A failure the view does not answer itself -- a
    ``RuntimeError`` from the rules lookup, the session, the fight -- is
    raised on to ``common.decorators.internal_errors_logged``, which logs it
    and answers the page; on its way out this finishes the record as an
    error in the sentence the page is answered in, so the session is free
    for the next message rather than refused until the claim goes stale. A
    record the view already finished is left as it is.

    Args:
        recorder: The claimed turn.

    Yields:
        Nothing; the view's work runs inside.
    """
    try:
        yield
    except Exception:
        recorder.finish(
            GMTurnRecord.OUTCOME_ERROR,
            error=common_refusal_words().decorators.request_failed,
        )
        raise


def claim_turn(recorder: TurnRecorder) -> GMTurnRecord | None:
    """Start ``recorder``'s turn unless its session already has one running.

    The check and the start happen under a lock on the user's row, so two
    messages sent to one session at the same moment cannot both start
    (issue #515). A turn with no session yet is never refused: it will make a
    session of its own, so there is nothing to interleave with.

    Returns:
        The turn already running, when this one was refused; ``None`` when
        this one started.
    """
    with transaction.atomic():
        # Evaluated in full, so the FOR UPDATE really is sent.
        list(
            get_user_model()
            .objects.select_for_update()
            .filter(pk=recorder.user.pk)
            .values_list("pk", flat=True)
        )
        if recorder.session_id is not None:
            running = running_turn_for(recorder.user, recorder.session_id)
            if running is not None:
                return running
        recorder.start()
    return None
