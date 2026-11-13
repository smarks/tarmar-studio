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
from django.db import connection, transaction
from django.db.models import QuerySet
from django.utils import timezone

from accounts.models import User
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


#: Why :func:`claim_turn` refused a turn. The session already has one running
#: (issue #515); the account has as many running as it may (issue #174); the
#: site has as many running as it serves at once (issue #174).
REFUSED_SESSION_BUSY = "session_busy"
REFUSED_ACCOUNT_CAP = "account_cap"
REFUSED_SITE_CAP = "site_cap"

#: The key of the one advisory lock every claim takes on PostgreSQL, so the
#: site-wide count and the start it guards cannot interleave across gunicorn's
#: worker processes. Any fixed 64-bit number does; this one is arbitrary.
TURN_CLAIM_LOCK_KEY = 174_515_064


@dataclass(frozen=True)
class TurnRefusal:
    """Why a turn was not started, for the view to answer in its own words.

    Attributes:
        reason: One of ``REFUSED_SESSION_BUSY``, ``REFUSED_ACCOUNT_CAP`` and
            ``REFUSED_SITE_CAP``.
        running: The session's turn still running, when that is the reason;
            the page asks after it by id. ``None`` for the two caps, which
            name no single turn.
    """

    reason: str
    running: GMTurnRecord | None = None


def running_turn_cap(*, is_game_master: bool) -> int:
    """How many turns an account may have running at once (issue #174).

    Args:
        is_game_master: Whether the account carries Game Master authority,
            read off the account (``battle.access.is_dm``), never the request.

    Returns:
        ``settings.GM_MAX_RUNNING_TURNS_PER_GAME_MASTER`` for a Game Master,
        who runs several tables, else ``GM_MAX_RUNNING_TURNS_PER_PLAYER``.
    """
    if is_game_master:
        return int(settings.GM_MAX_RUNNING_TURNS_PER_GAME_MASTER)
    return int(settings.GM_MAX_RUNNING_TURNS_PER_PLAYER)


def _running_turns() -> QuerySet[GMTurnRecord]:
    """The turns running now, as far as the records can tell (issue #174).

    A record left running by a worker that died keeps its row until
    :func:`expire_stale_running_turns` marks it, so the count reads only the
    rows a live turn could still own: those started within the recap and
    stream budgets plus the grace, the same bound :func:`turns_ahead_of`
    applies. A slot a dead worker left is therefore held for exactly that
    bound -- 300 + 300 + 180 s, 780 s, on the default settings -- and no
    longer. It cannot be freed sooner, because nothing but time tells a dead
    worker's row from a slow turn's: a turn writes its record only when it
    starts and when it ends. Holding a slot for the longest a turn can live
    is the price of never counting a live turn as dead, and it is the same
    bound a session already waits under before it takes a new message (#515).
    """
    return GMTurnRecord.objects.filter(
        outcome=GMTurnRecord.OUTCOME_RUNNING,
        started_at__gte=_running_turns_alive_since(),
    )


def running_turns_of(user: User) -> int:
    """How many turns ``user`` has running now, every session's and sessionless.

    Args:
        user: The account.

    Returns:
        The count of its live running records.
    """
    return _running_turns().filter(user=user).count()


def running_turns_site_wide() -> int:
    """How many turns are running now across every account."""
    return _running_turns().count()


#: The statement that takes the claim lock on PostgreSQL. The key is cast, so
#: the driver's parameter is read as the ``bigint`` the one-argument form
#: takes rather than left for PostgreSQL to resolve against both overloads.
TURN_CLAIM_LOCK_SQL = "SELECT pg_advisory_xact_lock(%s::bigint)"


def _lock_turn_claims() -> None:
    """Serialize every claim across processes while this transaction lasts.

    The per-account count is already serialized by the lock on the user's
    row (issue #515), but the site-wide count (issue #174) spans accounts, and
    two accounts' claims read on two gunicorn workers at once would each see
    a count one short and both start. On PostgreSQL, production's database,
    a transaction-scoped advisory lock keyed on :data:`TURN_CLAIM_LOCK_KEY`
    holds the second claim until the first has started or been refused; it
    costs no table and is released with the transaction. SQLite, the test and
    dev database, has no advisory locks and serializes its writers itself, so
    there the step is skipped, as ``select_for_update`` already is.

    Taken after the user's row and the account's count, never before: a user
    row can be held for seconds elsewhere (an Admin's address change holds it
    across a mail send, ``accounts.views._admin_sets_email``), and a claim
    waiting on that row must not hold every other account's claim with it.
    """
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(TURN_CLAIM_LOCK_SQL, [TURN_CLAIM_LOCK_KEY])


def claim_turn(recorder: TurnRecorder) -> TurnRefusal | None:
    """Start ``recorder``'s turn unless the session, the account or the site is full.

    The checks and the start happen under a lock on the user's row and the
    claim lock (:func:`_lock_turn_claims`), so two messages sent at the same
    moment cannot both start past a limit (issues #515, #174). Three refusals,
    in this order:

    * the session already has a turn running (``REFUSED_SESSION_BUSY``); a
      turn with no session yet is never refused on this count, since it will
      make a session of its own;
    * the account has :func:`running_turn_cap` turns running
      (``REFUSED_ACCOUNT_CAP``), the sessionless ones included -- a message
      sent with no session used to start a fresh turn every time, without
      bound;
    * the site has ``settings.GM_MAX_RUNNING_TURNS_SITE_WIDE`` running
      (``REFUSED_SITE_CAP``), which keeps request threads free for every
      other page.

    The locks are taken in this order, and only as far as needed: the user's
    row, then the session and account checks under it; the claim lock
    (:func:`_lock_turn_claims`) only once those pass, so a claim waiting on a
    busy user row never holds the site's lock while it waits; then the site
    count and the start under both.

    Returns:
        The refusal, or ``None`` when this turn started.
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
                return TurnRefusal(REFUSED_SESSION_BUSY, running)
        else:
            # running_turn_for expired the stale rows for a session turn;
            # a sessionless one has to, or a dead worker's rows would count
            # against its account until something else asked.
            expire_stale_running_turns()
        cap = running_turn_cap(is_game_master=recorder.is_game_master)
        if running_turns_of(recorder.user) >= cap:
            return TurnRefusal(REFUSED_ACCOUNT_CAP)
        _lock_turn_claims()
        if running_turns_site_wide() >= int(settings.GM_MAX_RUNNING_TURNS_SITE_WIDE):
            return TurnRefusal(REFUSED_SITE_CAP)
        recorder.start()
    return None
