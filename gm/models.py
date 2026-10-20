import json
from datetime import timedelta

from django.conf import settings
from django.db import models, transaction
from django.template.defaultfilters import pluralize
from django.utils import timezone

from .engine import GM_MODES, MODE_AIDE, MODE_PLAY, TIMES_OF_DAY
from .messages import as_typed, build_mode_switch_message
from .retrieval import ChatMessage, is_hidden_message

#: What each mode is called on the page, and the one line saying what it
#: does. The mode indicator (issue #353) reads both from here, so the badge,
#: the control's options and the prompt cannot name different things.
MODE_LABELS: dict[str, str] = {
    MODE_PLAY: "Game Master",
    MODE_AIDE: "GM aide",
}
MODE_DESCRIPTIONS: dict[str, str] = {
    MODE_PLAY: "Runs the game for you: sets the scene, plays the world, and asks what you do.",
    MODE_AIDE: "Answers what you ask and stops. You are running the game; it is not.",
}
MODE_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (mode, MODE_LABELS[mode]) for mode in GM_MODES
)

#: What a session is called before anything has been said in it. Every
#: session used to keep this name forever, so a sidebar of a player's whole
#: history read "New Session" twenty times over and picking one out of it
#: was guesswork (issue #339, item 12). One spelling, so the placeholder and
#: the test for "still unnamed" cannot drift apart.
PLACEHOLDER_SESSION_NAME = "New Session"

#: How much of the opening line a session is named after. Long enough to
#: tell two games apart in a narrow sidebar, short enough not to wrap.
SESSION_NAME_MAX_LENGTH = 48


def name_from_history(history: list[ChatMessage]) -> str:
    """Name a session after the first thing the player said in it.

    A session's own opening line is what its owner remembers it by, so it
    makes a better label than a timestamp and costs nothing to derive. The
    text is squeezed onto one line and cut at
    :data:`SESSION_NAME_MAX_LENGTH`, on a word boundary where there is one.

    Args:
        history: The session's messages, oldest first.

    Returns:
        The derived name, or :data:`PLACEHOLDER_SESSION_NAME` when nothing
        the player wrote is in the history yet.
    """
    for message in history:
        if message.get("role") != "user" or is_hidden_message(message):
            continue
        # What the player typed, where the model was sent something else for
        # it: a /roll is named "/roll 3d6", not its adjudication prompt (#838).
        text = " ".join(as_typed(message).split())
        if not text:
            continue
        if len(text) <= SESSION_NAME_MAX_LENGTH:
            return text
        cut = text[:SESSION_NAME_MAX_LENGTH]
        head, _space, tail = cut.rpartition(" ")
        # Keep a whole word where the cut left a usable one; a single very
        # long word is truncated as it stands rather than thrown away.
        trimmed = head if head and len(tail) < SESSION_NAME_MAX_LENGTH // 2 else cut
        return f"{trimmed.rstrip()}…"
    return PLACEHOLDER_SESSION_NAME


class GMSession(models.Model):
    """Persists a Game Master conversation history."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="gm_sessions",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=200)
    # How the person at this session wants the model to behave (issue #372):
    # the Game Master who runs a game, or the aide who helps someone else run
    # one. Deliberately NOT the same question as what they are allowed to do
    # -- authority is read off the account on every turn and is never stored
    # here (issue #352). The column default is the neutral one; both creation
    # paths pass the mode this account's role opens in, so a DM still starts
    # in the aide they had before this field existed.
    mode = models.CharField(max_length=8, choices=MODE_CHOICES, default=MODE_PLAY)
    history_json = models.TextField(default="[]")
    # The recap of this session's older turns, and how many history entries
    # it covers (issue #579). On the session rather than its own table: one
    # session has exactly one recap, it is rewritten in place, and nothing
    # else points at it. Empty until the session is long enough to fold.
    # db_default as well as default: during a blue/green deploy the colour
    # still serving writes rows without these columns, and a NOT NULL column
    # with no database default would refuse them (issue #575).
    summary = models.TextField(blank=True, default="", db_default="")
    summary_covers = models.PositiveIntegerField(default=0, db_default=0)
    # The player's characters in this session (issue #341). Which rows may be
    # attached is decided by the attach endpoint (the session owner's own
    # characters, or any a DM/admin can edit); the model only holds the link.
    characters = models.ManyToManyField(
        "characters.Character", related_name="gm_sessions", blank=True
    )
    # Retired (issue #571): a character built in a session is a
    # characters.Character row from its first change, attached here and
    # labelled by SessionCharacter. No code reads or writes this column. It
    # stays until a release that no longer needs to roll back past #571,
    # because dropping it would break the previous release's code.
    character_draft = models.JSONField(default=dict, blank=True)
    # The battle a /fight put on the hex board (issue #345): the session's
    # active fight while it runs, and the last one afterwards until the next
    # /fight replaces it. SET_NULL so deleting a battle leaves the session.
    battle = models.ForeignKey(
        "battle.Battle",
        on_delete=models.SET_NULL,
        related_name="gm_sessions",
        null=True,
        blank=True,
    )
    # The session this one continues the campaign of (issue #761), set only
    # by the "continue the campaign" control, which copied that session's
    # records into this one. The page says so from this column. SET_NULL so
    # deleting the older session leaves this one, and its copies, whole.
    # Nullable, so a colour still serving the previous release can write
    # rows without it during a deploy.
    continued_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="continuations",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Memoized parse of history_json; reset by the setter so it stays in sync.
    _history_cache: list[ChatMessage] | None = None

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            # Serves the per-user session list: filter user, ORDER BY -updated_at.
            models.Index(
                fields=["user", "-updated_at"], name="gmsession_user_updated_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.message_count} messages)"

    def refresh_from_db(self, *args, **kwargs) -> None:
        super().refresh_from_db(*args, **kwargs)
        self._history_cache = None  # drop the memoized parse after a reload

    @property
    def history(self) -> list[ChatMessage]:
        cache = self._history_cache
        if cache is None:
            cache = json.loads(self.history_json)
            self._history_cache = cache
        return cache

    @history.setter
    def history(self, value: list[ChatMessage]) -> None:
        self._history_cache = value
        self.history_json = json.dumps(value)

    @property
    def message_count(self) -> int:
        """Turns the reader sees: neither the system prompt nor hidden blocks.

        Hidden blocks are the canon excerpts (#331), character sheets
        (#341) and battle log entries (#345) filed for the model.
        """
        return sum(
            1
            for m in self.history
            if m.get("role") != "system" and not is_hidden_message(m)
        )

    @property
    def message_count_label(self) -> str:
        """The count as the page says it: "1 msg", "2 msgs" (issue #62).

        The rail's row and the done frame both say it from here, so the page
        never words a count of its own.
        """
        count = self.message_count
        return f"{count} msg{pluralize(count)}"

    def switch_mode(self, mode: str) -> bool:
        """Change this session's mode, keeping everything said in it so far.

        The conversation is never thrown away: it is the game, and a person
        changing how they want to be answered has not asked to lose it. What
        is added is one hidden note at the end of the history saying the mode
        changed (see :func:`gm.messages.build_mode_switch_message`), so the
        model reads the switch as an instruction instead of finding its own
        earlier manner contradicting the system prompt.

        The note is appended, never inserted, so every byte the model has
        already seen stays where it was and the prompt cache is not
        invalidated by the switch.

        The row is re-fetched under a lock before it is written, for the same
        reason ``gm.views._persist_turns`` does it: this writes the history
        column, and a turn streaming in the next tab may be appending to it.
        Building the new history from the loaded snapshot instead would lose
        whatever landed between the page render and the switch.

        Args:
            mode: The mode to change to, already normalised to one of
                :data:`gm.engine.GM_MODES`.

        Returns:
            True when the mode changed, False when it was already ``mode``
            (in which case nothing is written and no note is filed).
        """
        if self.mode == mode:
            return False
        with transaction.atomic():
            locked = GMSession.objects.select_for_update().get(pk=self.pk)
            if locked.mode == mode:
                return False
            locked.mode = mode
            locked.history = [*locked.history, build_mode_switch_message(mode)]
            locked.save(update_fields=["mode", "history_json", "updated_at"])
        self.refresh_from_db()
        return True

    def attached_characters(self):
        """The attached characters still in play, in a stable order (by id).

        A character soft-deleted since it was attached drops out of the panel
        and the model's sheet block (its link row stays, so restoring the
        character brings it back). The order matters beyond the panel: the
        sheet block is hashed to decide whether it changed, so the same set of
        characters must always render in the same order.
        """
        return self.characters.filter(is_deleted=False).order_by("pk")


class SessionCharacter(models.Model):
    """What a session's conversation calls one of its characters (issue #571).

    The session's roster is its attached characters (``GMSession.characters``).
    This row adds what only the conversation knows: the label the Game Master
    used before the character had a name ("the brother"), and the skill
    points a Game Master granted past those earned (issue #404). A marker's
    character is resolved against the attached rows' names and these labels,
    never against every character the user owns.
    """

    session = models.ForeignKey(
        GMSession, on_delete=models.CASCADE, related_name="session_characters"
    )
    character = models.ForeignKey(
        "characters.Character",
        on_delete=models.CASCADE,
        related_name="session_characters",
    )
    #: What the conversation calls the character, cut to this rather than
    #: refused: it is a handle the model picked, not a name anyone gave.
    MAX_LABEL_CHARS = 100

    label = models.CharField(max_length=MAX_LABEL_CHARS, blank=True, default="")
    game_master_skill_points = models.PositiveIntegerField(default=0)
    # Luck spent in this session (issue #861): "once per session per level"
    # (skills glossary), so the count is the session's, not the character's.
    # db_default so the colour still serving the previous release can write
    # rows without it during a deploy (issue #575).
    luck_spent = models.PositiveIntegerField(default=0, db_default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "character"], name="one_label_per_session_character"
            )
        ]

    def __str__(self) -> str:
        return f"{self.character_id} in session {self.session_id} ({self.label})"


class GMPromptSnapshot(models.Model):
    """One system prompt, byte for byte as turns sent it (issue #389).

    The prompt is built from code, and the code changes: a record that only
    named the prompt would say nothing once the next deploy rewrote it. Each
    distinct prompt is kept once, under its hash, so a turn from last week can
    still be read against the exact instructions the model was given.
    """

    prompt_hash = models.CharField(max_length=64, unique=True)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"prompt {self.prompt_hash[:16]}"


class GMTurnRecord(models.Model):
    """What happened on one GM turn, for whoever has to debug it (issue #389).

    ``GMSession.history`` is what the model reads next turn, so it can only
    hold messages. Everything else a turn decided -- which mode and prompt it
    ran with, the model and options, how long it took, what the ledger applied
    or refused, and whether it failed -- lives here instead, beside the
    history rather than inside it, so recording it never changes the model's
    prompt prefix.

    A turn that failed or ran out of time is recorded too. Those persist no
    history at all, which made them the hardest turns to debug.
    """

    OUTCOME_OK = "ok"
    OUTCOME_TOO_SLOW = "too_slow"
    OUTCOME_ERROR = "error"
    OUTCOME_REJECTED = "rejected"
    # A turn that has started and not yet finished (issue #515). It is what
    # tells a reloaded page that its reply is still coming, and what refuses a
    # second message to the same session until it lands.
    OUTCOME_RUNNING = "running"
    OUTCOME_CHOICES = [
        (OUTCOME_RUNNING, "Running"),
        (OUTCOME_OK, "Completed"),
        (OUTCOME_TOO_SLOW, "Ran out of time"),
        (OUTCOME_ERROR, "Failed"),
        (OUTCOME_REJECTED, "Rejected"),
    ]

    session = models.ForeignKey(
        GMSession,
        on_delete=models.CASCADE,
        related_name="turn_records",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="gm_turn_records",
        null=True,
        blank=True,
    )
    started_at = models.DateTimeField(default=timezone.now)
    duration_ms = models.PositiveIntegerField(default=0)
    outcome = models.CharField(max_length=10, choices=OUTCOME_CHOICES)
    #: The column's length; a longer command is cut to it when recorded,
    #: since this is a label for the record, not text anyone reads back.
    MAX_COMMAND_CHARS = 16
    # "" for an ordinary message; otherwise the slash command it was.
    command = models.CharField(max_length=MAX_COMMAND_CHARS, blank=True)
    mode = models.CharField(max_length=8, choices=MODE_CHOICES)
    is_game_master = models.BooleanField(default=False)
    model = models.CharField(max_length=200, blank=True)
    options = models.JSONField(default=dict, blank=True)
    prompt = models.ForeignKey(
        GMPromptSnapshot,
        on_delete=models.SET_NULL,
        related_name="turns",
        null=True,
        blank=True,
    )
    app_version = models.CharField(max_length=40, blank=True)
    user_message = models.TextField(blank=True)
    reply = models.TextField(blank=True)
    # The reply as the model wrote it, when the server removed machinery
    # narration from an aide turn (issue #392); "" when nothing was removed.
    original_reply = models.TextField(blank=True)
    # The sentences removed from it, in order.
    removed_narration = models.JSONField(default=list, blank=True)
    # The slice of ``GMSession.history`` this turn wrote: entries from
    # ``history_start`` up to, not including, ``history_end``. ``history_end``
    # is None for a turn that saved nothing.
    history_start = models.PositiveIntegerField(default=0)
    history_end = models.PositiveIntegerField(null=True, blank=True)
    gm_rolls = models.JSONField(default=list, blank=True)
    procedures = models.JSONField(default=list, blank=True)
    ledger_applied = models.JSONField(default=list, blank=True)
    ledger_refused = models.JSONField(default=list, blank=True)
    notices = models.JSONField(default=list, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["started_at", "pk"]
        indexes = [
            models.Index(
                fields=["session", "started_at"], name="gmturn_session_started_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"turn {self.pk} of session {self.session_id} ({self.outcome})"


class GMEvent(models.Model):
    """One thing that happened during a turn, in order (issue #590).

    The plan → run → narrate turn engine (:mod:`gm.turn_engine`) writes an
    event the moment something happens: each state it enters, every model
    request and raw response, the plan it read, each action's result, the
    narration. An action's event is written in the same transaction as the
    character change it reports, so the log and the record cannot disagree.

    Append-only: an event is never updated. ``GMTurnRecord`` stays the turn's
    one-row summary; the events are how the turn got there, and are enough to
    replay it.
    """

    turn = models.ForeignKey(
        GMTurnRecord, on_delete=models.CASCADE, related_name="events"
    )
    # The event's position in its turn, from 1.
    seq = models.PositiveIntegerField()
    kind = models.CharField(max_length=32)
    # Whether the player saw it: an action's result or the narration, as
    # opposed to a model request kept for replay.
    visible = models.BooleanField(default=False)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["turn", "seq"]
        constraints = [
            models.UniqueConstraint(
                fields=["turn", "seq"], name="one_event_per_turn_seq"
            )
        ]

    def __str__(self) -> str:
        return f"event {self.seq} of turn {self.turn_id} ({self.kind})"

    def save(self, *args, **kwargs) -> None:
        """Write a new event; an existing one is never rewritten."""
        if not self._state.adding:
            raise ValueError("GM events are append-only")
        super().save(*args, **kwargs)


class TableRuling(models.Model):
    """A house rule the Game Master stated for this session's table (issue #600).

    Recorded by the ``record_house_rule`` plan action and listed as a fact in
    the state block of every later model call, so a ruling outweighs the
    book's text in the system prompt instead of sitting in the history as
    prose. A ruling is never edited: a later one on the same topic retires it
    and a new row is written, so what the table played by stays readable.
    """

    #: The longest topic and rule a plan may record.
    MAX_TOPIC_CHARS = 60
    MAX_RULE_CHARS = 400

    session = models.ForeignKey(
        GMSession, on_delete=models.CASCADE, related_name="rulings"
    )
    # What the rule is about, short: "critical hits".
    topic = models.CharField(max_length=MAX_TOPIC_CHARS)
    # The rule in the Game Master's words, trimmed.
    text = models.CharField(max_length=MAX_RULE_CHARS)
    # The turn that recorded it; kept when the turn record is pruned.
    turn = models.ForeignKey(
        GMTurnRecord,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rulings",
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["session", "created_at", "pk"]
        indexes = [models.Index(fields=["session", "active"])]

    def __str__(self) -> str:
        return f"{self.topic}: {self.text}"

    @property
    def line(self) -> str:
        """``- critical hits (ruling 3): a natural 20 just crits.``"""
        return f"- {self.topic} (ruling {self.pk}): {self.text}"

    @staticmethod
    def same_topic(first: str, second: str) -> bool:
        """Whether two topics name the same thing, ignoring case and spacing."""
        return " ".join(first.casefold().split()) == " ".join(second.casefold().split())


class GMReviewRequest(models.Model):
    """A Game Master's request for Claude to review a session (issue #402).

    The session is frozen into ``snapshot`` the moment it is sent -- the
    whole transcript payload, turn records and drafts included -- so the
    review reads exactly what the Game Master was looking at, however the
    session carries on afterwards.

    A reviewer on the LLM host claims a pending request, analyses it, and
    completes or fails it. A claim left behind by a reviewer that died is
    claimable again after :data:`STALE_CLAIM_MINUTES`.
    """

    STATUS_PENDING = "pending"
    STATUS_CLAIMED = "claimed"
    STATUS_DONE = "done"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Waiting for Claude"),
        (STATUS_CLAIMED, "Claude is reviewing it"),
        (STATUS_DONE, "Reviewed"),
        (STATUS_FAILED, "The review failed"),
    ]
    STALE_CLAIM_MINUTES = 60

    #: The reviews list's status filter (issue #572): what each choice in the
    #: query string is called and which statuses it takes in. ``all`` is the
    #: default and filters nothing.
    FILTER_ALL = "all"
    FILTER_CHOICES: dict[str, tuple[str, tuple[str, ...]]] = {
        FILTER_ALL: ("All", ()),
        "open": ("Waiting or in progress", (STATUS_PENDING, STATUS_CLAIMED)),
        "done": ("Reviewed", (STATUS_DONE,)),
        "failed": ("Failed", (STATUS_FAILED,)),
    }

    session = models.ForeignKey(
        GMSession,
        on_delete=models.SET_NULL,
        related_name="review_requests",
        null=True,
        blank=True,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="gm_review_requests",
        null=True,
        blank=True,
    )
    note = models.TextField(blank=True)
    snapshot = models.JSONField()
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True
    )
    #: The column's length; a runner's self-description is cut to it (#938).
    MAX_RUNNER_CHARS = 200
    runner = models.CharField(max_length=MAX_RUNNER_CHARS, blank=True)
    findings = models.TextField(blank=True)
    failure = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        return f"review {self.pk} ({self.status})"

    @classmethod
    def claimable(cls) -> models.QuerySet["GMReviewRequest"]:
        """Reviews a reviewer may take: pending, or claimed and abandoned."""
        stale = timezone.now() - timedelta(minutes=cls.STALE_CLAIM_MINUTES)
        return cls.objects.filter(
            models.Q(status=cls.STATUS_PENDING)
            | models.Q(status=cls.STATUS_CLAIMED, claimed_at__lt=stale)
        ).order_by("created_at", "pk")

    @classmethod
    def filtered(cls, choice: str) -> models.QuerySet["GMReviewRequest"]:
        """Reviews matching one of :data:`FILTER_CHOICES`, newest first.

        An unknown choice is treated as ``all``, so a mangled shared link
        still shows the list rather than an error.
        """
        reviews = cls.objects.all()
        _label, statuses = cls.FILTER_CHOICES.get(choice, cls.FILTER_CHOICES["all"])
        if statuses:
            reviews = reviews.filter(status__in=statuses)
        return reviews

    @property
    def is_open(self) -> bool:
        """True while the review is waiting for, or with, a reviewer."""
        return self.status in (self.STATUS_PENDING, self.STATUS_CLAIMED)

    @property
    def review_duration(self) -> timedelta | None:
        """How long the reviewer spent on it, from claim to outcome.

        None until it is both claimed and finished.
        """
        if self.claimed_at is None or self.completed_at is None:
            return None
        return max(self.completed_at - self.claimed_at, timedelta(0))

    @property
    def review_duration_text(self) -> str:
        """:attr:`review_duration` in words, e.g. ``4 min 12 s``; empty if none."""
        duration = self.review_duration
        if duration is None:
            return ""
        total_seconds = int(duration.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours} h {minutes} min"
        if minutes:
            return f"{minutes} min {seconds} s"
        return f"{seconds} s"

    @classmethod
    def claim(cls, review_id: int, runner: str) -> bool:
        """Take ``review_id`` for ``runner``, atomically.

        Returns:
            True when this call claimed it; False when it was not claimable,
            so two reviewers polling at once never review the same session.
        """
        updated = (
            cls.claimable()
            .filter(pk=review_id)
            .update(
                status=cls.STATUS_CLAIMED,
                runner=runner[: cls.MAX_RUNNER_CHARS],
                claimed_at=timezone.now(),
            )
        )
        return updated == 1

    def complete(self, findings: str) -> None:
        """Record Claude's findings and mark the review done."""
        self.findings = findings
        self.failure = ""
        self.status = self.STATUS_DONE
        self.completed_at = timezone.now()
        self.save(update_fields=["findings", "failure", "status", "completed_at"])

    def fail(self, reason: str) -> None:
        """Record why the review could not be done."""
        self.failure = reason
        self.status = self.STATUS_FAILED
        self.completed_at = timezone.now()
        self.save(update_fields=["failure", "status", "completed_at"])


class CampaignState(models.Model):
    """Where the party is, and when, for one session (issue #579).

    One row per session, changed only by the ``move_party`` and
    ``advance_time`` plan actions, and read back into the state block of
    every model call. Before this, where the party stood and what hour it was
    lived in the transcript and nowhere else, so the narration reinvented
    both whenever the older turns fell out of the window.

    What the party carries is not here: it is one row per thing, in
    :class:`SupplyItem`.
    """

    #: The longest a plan may make the place.
    MAX_PLACE_CHARS = 120

    MORNING = "morning"
    #: From :data:`gm.engine.TIMES_OF_DAY`, which the plan schema also reads,
    #: so what a plan may set and what a record may hold are one list.
    TIMES_OF_DAY = list(TIMES_OF_DAY)

    session = models.OneToOneField(
        GMSession, on_delete=models.CASCADE, related_name="campaign"
    )
    # Where the party is, in the words the table used.
    place = models.CharField(max_length=MAX_PLACE_CHARS, blank=True)
    # The key in gm.maps' registry when the place is mapped; "" when it is
    # not, which is the honest answer for somewhere the canon never named.
    place_key = models.CharField(max_length=60, blank=True)
    day = models.PositiveIntegerField(default=1)
    time_of_day = models.CharField(max_length=16, choices=TIMES_OF_DAY, default=MORNING)
    updated_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"{self.place or 'nowhere recorded'} ({self.when})"

    @property
    def when(self) -> str:
        """``day 3, evening``."""
        return f"day {self.day}, {self.get_time_of_day_display().lower()}"

    @property
    def where(self) -> str:
        """The place, saying plainly when it is not on any map."""
        if not self.place:
            return "not recorded"
        if self.place_key:
            return self.place
        return f"{self.place} (not a mapped place)"

    @property
    def lines(self) -> list[str]:
        """How the state block says where and when the party is."""
        return [f"- where: {self.where}", f"- when: {self.when}"]


class SupplyItem(models.Model):
    """One thing the party carries between them, and how much of it (#579).

    Supplies were a single free-text line on :class:`CampaignState` until
    this, which meant a turn that spent two torches rewrote the whole
    sentence and could drop the rope while it was at it. One row per thing
    is the same shape the rest of #579 settled on: a change names what it
    changes and leaves the others alone.

    Written only by the ``adjust_supplies`` plan action. Recording a name the
    session already holds updates that row, so there is one line of rations
    and not four; ``spent`` takes the row away when the last of it is gone.

    The rules name gear rather than a party's stores -- the equipment tables
    price rations, torches, oil and a waterskin, and no rule counts them down
    -- so the name is whatever the table calls the thing, and the amount is
    free text because "4 days", "half a skin" and "3" are all answers a table
    gives.
    """

    MAX_NAME_CHARS = 60
    MAX_AMOUNT_CHARS = 40
    MAX_NOTE_CHARS = 160

    session = models.ForeignKey(
        GMSession, on_delete=models.CASCADE, related_name="supplies"
    )
    name = models.CharField(max_length=MAX_NAME_CHARS)
    # How much there is, or what state it is in: "3", "4 days", "half full".
    amount = models.CharField(max_length=MAX_AMOUNT_CHARS, blank=True)
    # Anything else the table said about it.
    note = models.CharField(max_length=MAX_NOTE_CHARS, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["session", "created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                models.functions.Lower("name"),
                "session",
                name="one_row_per_named_supply_in_a_session",
            )
        ]

    def __str__(self) -> str:
        return self.line.removeprefix("- ")

    @property
    def line(self) -> str:
        """How the state block and the panel list it."""
        said = self.name
        if self.amount:
            said = f"{said}: {self.amount}"
        if self.note:
            said = f"{said} ({self.note})"
        return f"- {said}"


class SceneEntity(models.Model):
    """A person or a thing in play, and the state the table left it in (#579).

    People and objects share one table because they failed the same way. The
    Game Master's session 36 had an iron box resting on a ledge in one turn,
    chained in the next and fused to the rock two turns later, with no event
    between that would have changed it -- the narration reinvented the thing
    every turn because nothing held its state. An NPC's attitude drifts the
    same way for the same reason.

    Recorded by ``record_npc`` and ``record_object``. Recording a name the
    session already holds updates that row rather than adding a second, so
    the table has one iron box, not four.
    """

    MAX_NAME_CHARS = 80
    MAX_DETAIL_CHARS = 200

    PERSON = "person"
    THING = "thing"
    KINDS = [(PERSON, "Person"), (THING, "Thing")]

    session = models.ForeignKey(
        GMSession, on_delete=models.CASCADE, related_name="entities"
    )
    kind = models.CharField(max_length=8, choices=KINDS)
    name = models.CharField(max_length=MAX_NAME_CHARS)
    # Where they were met, or where the thing is.
    where = models.CharField(max_length=MAX_DETAIL_CHARS, blank=True)
    # What a person wants; left blank for a thing.
    wants = models.CharField(max_length=MAX_DETAIL_CHARS, blank=True)
    # A person's attitude to the party, or the state a thing is in -- open,
    # locked, fused to the rock.
    condition = models.CharField(max_length=MAX_DETAIL_CHARS, blank=True)
    # Anything else the table established.
    notes = models.CharField(max_length=MAX_DETAIL_CHARS, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["session", "created_at", "pk"]
        indexes = [models.Index(fields=["session", "kind"])]
        constraints = [
            models.UniqueConstraint(
                models.functions.Lower("name"),
                "session",
                name="one_row_per_named_entity_in_a_session",
            )
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def line(self) -> str:
        """How the state block lists it, with only the parts that are known."""
        said = [
            part
            for part in (
                f"at {self.where}" if self.where else "",
                f"wants {self.wants}" if self.wants else "",
                self.condition,
                self.notes,
            )
            if part
        ]
        detail = "; ".join(said) if said else "nothing else recorded"
        return f"- {self.kind} {self.pk}: {self.name} -- {detail}"


class StoryThread(models.Model):
    """Something left hanging that the table may come back to (issue #579).

    A hook, a promise, a debt or a mystery, opened by ``open_thread`` and
    closed by ``resolve_thread``. Kept after it is resolved: what the party
    did is part of the campaign, and a resolved thread stops the narration
    reopening it.
    """

    MAX_TEXT_CHARS = 300

    session = models.ForeignKey(
        GMSession, on_delete=models.CASCADE, related_name="threads"
    )
    text = models.CharField(max_length=MAX_TEXT_CHARS)
    opened_turn = models.ForeignKey(
        GMTurnRecord,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="threads_opened",
    )
    resolved = models.BooleanField(default=False)
    resolution = models.CharField(max_length=MAX_TEXT_CHARS, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["session", "created_at", "pk"]
        indexes = [models.Index(fields=["session", "resolved"])]

    def __str__(self) -> str:
        return self.text

    @property
    def line(self) -> str:
        """``- thread 2: the Sleeper must not wake``."""
        if self.resolved:
            ending = (
                f" (resolved: {self.resolution})" if self.resolution else " (resolved)"
            )
            return f"- thread {self.pk}: {self.text}{ending}"
        return f"- thread {self.pk}: {self.text}"


class GMRecordEdit(models.Model):
    """A campaign record the Game Master set by hand (issues #789, #790, #791).

    House rules, the people and things in play, the open threads and the
    fold's recap were written only when the model decided to write them, and
    corrected only by asking it and hoping. The Game Master may now add,
    correct, retire, resolve or remove them himself from the page. Each such
    change is recorded here -- who made it, with what authority, and the
    record before and after -- so a hand edit is never mistaken for one the
    model made, and the transcript shows it beside the turns.

    Append-only, like :class:`GMEvent`: an edit is never rewritten.
    """

    RULING = "ruling"
    PERSON = "person"
    THING = "thing"
    THREAD = "thread"
    RECAP = "recap"
    RECORD_KINDS = [
        (RULING, "House rule"),
        (PERSON, "Person"),
        (THING, "Thing"),
        (THREAD, "Thread"),
        (RECAP, "Recap"),
    ]

    ADDED = "added"
    EDITED = "edited"
    RETIRED = "retired"
    REMOVED = "removed"
    RESOLVED = "resolved"
    REOPENED = "reopened"
    CHANGES = [
        (ADDED, "added"),
        (EDITED, "corrected"),
        (RETIRED, "retired"),
        (REMOVED, "removed"),
        (RESOLVED, "resolved"),
        (REOPENED, "reopened"),
    ]

    session = models.ForeignKey(
        GMSession, on_delete=models.CASCADE, related_name="hand_edits"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gm_record_edits",
    )
    # The account's authority when the edit was made; only a Game Master may.
    is_game_master = models.BooleanField(default=False)
    record_kind = models.CharField(max_length=8, choices=RECORD_KINDS)
    # The row changed, or None for the session's recap.
    record_id = models.PositiveIntegerField(null=True, blank=True)
    change = models.CharField(max_length=8, choices=CHANGES)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["session", "created_at", "pk"]
        indexes = [models.Index(fields=["session", "record_kind", "record_id"])]

    def __str__(self) -> str:
        return self.line

    def save(self, *args, **kwargs) -> None:
        """Write a new edit; an existing one is never rewritten."""
        if not self._state.adding:
            raise ValueError("hand edits are append-only")
        super().save(*args, **kwargs)

    @property
    def who(self) -> str:
        """The account that made the edit, as the page and transcript name it."""
        return self.user.get_username() if self.user is not None else "(deleted)"

    @property
    def line(self) -> str:
        """``Set by hand by gm: person 4 corrected``."""
        target = self.get_record_kind_display().lower()
        if self.record_id is not None:
            target = f"{target} {self.record_id}"
        return f"Set by hand by {self.who}: {target} {self.get_change_display()}"
