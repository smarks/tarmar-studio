"""Battle-royale simulator persistence (issue #176).

A ``Battle`` is a deterministic, replayable simulation: its ``seed`` plus the
turn number seed every turn's dice, its ``state_json`` snapshots the pure
engine state between turns, and its ``BattleEvent`` rows are the complete
audit trail — every roll, every AI decision, every phase marker. Reloading the
detail page reproduces the whole battle from these rows alone.

Mutable per-turn combatant state (fatigue, position, facing, ...) lives in
``Battle.state_json``, not in columns: ``BattleCombatant`` exists for
provenance (which Character rows fought) and list display.
"""

from django.core.exceptions import ValidationError
from django.db import models

from .control import (
    AUTOMATIC,
    COMBATANT_MODE_CHOICES,
    INHERIT,
    MODE_CHOICES,
)
from .persistence import PROFILE_CHOICES, TARMAR_PROFILE, roster_entries

#: Every battle seed is a whole number in ``range(SEED_RANGE)``: what the
#: setup form accepts, what an unseeded battle draws, and what the seeds
#: derived from one (a fighter's, a session fight's stand-ins) are drawn
#: from. Named once (review of batch h).
SEED_RANGE = 2**31

# Bound the serialized engine state so a runaway writer cannot bloat rows.
MAX_STATE_JSON_COMBATANTS = 100


def validate_state_json(value):
    if not isinstance(value, dict):
        raise ValidationError("Battle state must be an object.")
    # Shape-agnostic: a field validator sees the value, never the row, so
    # it cannot dispatch on the battle's profile (battle.persistence).
    combatants = roster_entries(value)
    if not isinstance(combatants, list):
        raise ValidationError("Battle state combatants must be a list.")
    if len(combatants) > MAX_STATE_JSON_COMBATANTS:
        raise ValidationError(
            f"Too many combatants ({len(combatants)}); "
            f"max is {MAX_STATE_JSON_COMBATANTS}."
        )


class Battle(models.Model):
    """One battle-royale simulation.

    Two creation shapes since issue #250: the classic DM-only simulation
    (status starts at ``setup`` with the engine state snapshotted
    immediately), and an *open* battle that begins life as a ``lobby`` —
    player seats wait to be claimed, and the engine state is only built when
    the lobby closes and the battle starts.
    """

    STATUS_LOBBY = "lobby"
    STATUS_SETUP = "setup"
    STATUS_PAUSED = "paused"
    # Mid-turn: the engine reached a player-controlled combatant's decision
    # and is waiting for that player's chosen option before the turn can
    # finish (services.advance_turn's replay-on-input flow).
    STATUS_AWAITING = "awaiting"
    STATUS_FINISHED = "finished"
    # Terminal like ``finished``, but nobody won: the battle went
    # ``services.STALEMATE_TURNS`` consecutive full turns without hurting
    # anyone and was declared unwinnable rather than advanced forever
    # (#289 — some matchups cannot end by construction).
    STATUS_STALEMATE = "stalemate"
    STATUS_CHOICES = [
        (STATUS_LOBBY, "Lobby"),
        (STATUS_SETUP, "Setup"),
        # "Paused" read as though a person had stopped the battle; in
        # automatic mode it is simply the state between two turns
        # (#716, low finding). The stored value is untouched -- only the
        # word shown changes.
        (STATUS_PAUSED, "Between turns"),
        (STATUS_AWAITING, "Awaiting players"),
        (STATUS_FINISHED, "Finished"),
        (STATUS_STALEMATE, "Stalemate"),
    ]

    name = models.CharField(max_length=100)
    seed = models.IntegerField()
    # Which rules profile governs this battle — and therefore which state
    # type ``state_json`` holds, since the engine's seam runs each profile
    # over its own state (see ``battle.persistence``). Every battle stored
    # before the classic profile arrived is Tarmar, which is the default.
    rules_profile = models.CharField(
        max_length=20, choices=PROFILE_CHOICES, default=TARMAR_PROFILE
    )
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_SETUP
    )
    # Lobby rule (DM's choice at creation): start the battle automatically
    # the moment every seat is claimed and every claimed player is ready.
    auto_start = models.BooleanField(default=False)
    # Who decides, as a setting rather than an inference (#308). This is the
    # *default* every combatant inherits; a combatant carrying a mode of its
    # own overrides it (``battle.control.effective_mode`` is the one place
    # that rule is written). Automatic by default, which is what a battle
    # with nobody seated has always done.
    control_mode = models.CharField(
        max_length=10, choices=MODE_CHOICES, default=AUTOMATIC
    )
    # A fight a player's Games Master session put on the board (#762): the
    # side nobody sits in belongs to the Game Master, not to the player who
    # made the battle. Their maker still advances it and plays their own
    # seats; restarting it, changing who decides, and choosing a foe's move
    # need a Game Master account (``battle.access.commands_foes``). Recorded
    # on the row when the fight starts, rather than read off the session,
    # because a session's link moves to its next fight and its mode can be
    # switched, and neither should hand an old fight back to the player.
    foes_belong_to_game_master = models.BooleanField(default=False, db_default=False)
    # Whether this fight's wounds and deaths are written to the records of
    # the saved characters in it (issues #750, #751). Only a Games Master
    # session's fight and a battle a Game Master runs do: a player's own
    # standalone or practice battle, lobby or classic crossing writes nothing
    # to any record, as before. Coordinator's ruling under the standing rule
    # (players held to the rules, the Game Master decides); Spencer may
    # overrule. Recorded when the battle is made, for the same reason as the
    # flag above.
    writes_to_records = models.BooleanField(default=False, db_default=False)
    # A Game Master's "HTH by agreement" ruling (issue #867): the whole
    # battle, or pairs of combatants, may enter hand-to-hand by agreement.
    # ``{"whole_battle": bool, "pairs": [[low_id, high_id], ...]}``, ``{}``
    # for none. The one source of truth; ``battle.agreement`` reads it and
    # writes it onto the engine state every turn, and only a Game Master
    # changes it (``battle.services.set_hth_agreement``).
    hth_agreement_json = models.JSONField(default=dict, blank=True, db_default={})
    # While ``status`` is ``awaiting``: which combatant the turn is paused
    # on, plus the legal candidates (policy scores included, as advice) that
    # were computed when the engine paused — so the player's page can render
    # the choice without re-running the engine. Shape:
    # ``{"combatant_id": int, "turn": int, "candidates": [payload, ...]}``.
    # Empty when no input is pending. Display-only: a submitted choice is
    # validated against a deterministic replay, never against this cache.
    pending_input_json = models.JSONField(default=dict, blank=True)
    # Player choices recorded for the in-progress turn, keyed by combatant id
    # (as a string — JSON object keys): ``{"7": {"letter": "j",
    # "target_id": 3, "spell_key": ""}}``. Consumed by the deterministic
    # turn replay and cleared when the turn commits.
    turn_inputs_json = models.JSONField(default=dict, blank=True)
    turn_number = models.PositiveIntegerField(default=0)
    # How many consecutive completed turns hurt nobody — no combatant ended
    # a turn with lower pools than it began, and nobody died or dropped.
    # Reset to 0 by any turn that does hurt someone, and by restart. When it
    # reaches ``services.STALEMATE_TURNS`` the battle ends as a stalemate
    # (#289). Derived deterministically from the turn states, so a replay
    # counts the same turns and stalls at the same place.
    turns_without_progress = models.PositiveIntegerField(default=0)
    # Full serialized engine state between turns, in the shape written by
    # ``rules_profile``'s state type — ``battle.persistence`` dispatches.
    state_json = models.JSONField(
        default=dict, blank=True, validators=[validate_state_json]
    )
    # The turn-0 snapshot, frozen at creation and never advanced. Restart
    # (issue #188) rewinds ``state_json`` to this, which is why the roster is
    # not regenerated from the seed: replaying generation would duplicate
    # ``create_battle``'s logic, and the snapshot already *is* that result.
    # Empty for battles created before the field existed; the migration
    # backfills the ones still sitting at turn 0, and restart refuses the rest.
    initial_state_json = models.JSONField(
        default=dict, blank=True, validators=[validate_state_json]
    )
    # Null for a battle a guest made (issue #305). The arena is playable
    # without an account, so a battle needs an owner slot that can be
    # empty; ``guest_key`` below carries who that guest is meanwhile, and
    # ``battle.adoption`` fills this in if they register afterwards.
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="battles",
        null=True,
        blank=True,
        db_index=True,
    )
    # The ``common.guest`` token of the visitor who made this battle, while
    # it has no account behind it. Empty for every owned battle — the two
    # are exclusive, and adoption clears this as it sets ``created_by``.
    # Control only: it decides who may advance or restart this battle, and
    # never who may read it (``battle.access.readable_battles`` decides
    # that from the roster's contents alone).
    guest_key = models.CharField(max_length=64, blank=True, default="", db_index=True)
    # Soft delete (#339, item 5), deliberately the same shape ``Character``
    # already uses: a flag, the moment it was set, and a restore path. A
    # battle is a replayable record — its event log is the whole fight, and
    # a GM session may point at it — so "clear this off my list" must not be
    # the same act as destroying it. Deleting hides the battle from every
    # list; the row, its combatants and its log all survive, and whoever
    # runs the battle can put it back.
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} (seed {self.seed})"

    @property
    def is_over(self) -> bool:
        """Finished or stalemated: nothing more happens on this board."""
        return self.status in (self.STATUS_FINISHED, self.STATUS_STALEMATE)


class BattleDraft(models.Model):
    """One DM's in-progress battle setup (issues #270/#271).

    The staged setup page edits this row, not browser memory: every field
    change and every roster addition is saved server-side, so leaving for
    the character editor (create or edit a combatant mid-setup) and coming
    back finds the muster exactly as it was. Starting the battle compiles
    the draft into :func:`battle.services.create_battle`'s arguments and
    deletes the row; discarding just deletes it.

    ``roster_json`` is an ordered list of entry dicts — the muster roll as
    the DM assembled it. Entry shapes (``battle.services.add_draft_entry``
    validates them on the way in):

    - ``{"kind": "fighter", "archetype": <ARCHETYPES member>}``
    - ``{"kind": "beast", "slug": <catalog slug>}``
    - ``{"kind": "random_beast", "category": "", "difficulty": ""}``
    - ``{"kind": "character", "character_id": <pk>}``
    - ``{"kind": "open_seat"}``

    Any entry may additionally carry ``"team": <name>`` (#275) — a name
    from ``teams_json``, the draft's ordered list of team names. An entry
    without one fights free-for-all, exactly as every entry did before
    teams existed. Teams are born by importing a party
    (:func:`battle.services.import_party_into_draft`) or by naming one
    directly; disbanding a team returns its entries to the free-for-all
    pool.

    Character entries hold only the id: the snapshot is taken when the
    battle is created (#252's seat-time semantics), so edits made through
    the setup round-trip are in the battle, and the source row never is.
    """

    name = models.CharField(max_length=100, blank=True, default="")
    seed = models.IntegerField(null=True, blank=True)
    rules_profile = models.CharField(
        max_length=20, choices=PROFILE_CHOICES, default=TARMAR_PROFILE
    )
    auto_start = models.BooleanField(default=False)
    # The battle-level control mode this draft will be created with (#308).
    control_mode = models.CharField(
        max_length=10, choices=MODE_CHOICES, default=AUTOMATIC
    )
    # A Game Master's choice that the battle counts (issues #750, #751):
    # its wounds and deaths go on the saved characters' records
    # (``Battle.writes_to_records``). On unless they turn it off; a player's
    # draft never offers it, and ``draft_create_kwargs`` ignores it for one.
    writes_to_records = models.BooleanField(default=True, db_default=True)
    roster_json = models.JSONField(default=list, blank=True)
    # Ordered team names (#275). A team may sit empty — the DM adds to it —
    # and the list's order is the muster roll's display order.
    teams_json = models.JSONField(default=list, blank=True)
    # Nullable for the same reason as ``Battle.created_by`` (#305): a
    # guest assembles their muster on this row before there is any account
    # to hang it on.
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="battle_drafts",
        null=True,
        blank=True,
        db_index=True,
    )
    guest_key = models.CharField(max_length=64, blank=True, default="", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Draft: {self.name or 'unnamed battle'}"


class BattleCombatant(models.Model):
    """Provenance row linking a battle to the Character or Beast that fought.

    A combatant is exactly one of the two: :meth:`clean` enforces it at
    creation, and the DB constraint below enforces the half a database can
    hold forever — never both. (A strict exactly-one constraint would make
    both FKs undeletable: hard-deleting a fought Character or Beast —
    including via a User-cascade — must be able to SET_NULL its side. Such a
    row survives as a tombstone: ``display_name`` and the battle's events
    keep the audit trail.)

    Since issue #250 a row can also be a *seat* in an open battle's lobby:
    ``is_open_seat`` rows start with neither character nor beast (the one
    exception :meth:`clean` allows) until a player claims them, choosing or
    generating a character. ``player`` names the controlling human — during
    the battle that combatant's turn decisions come from them, not the AI —
    and ``snapshot_json`` holds the position-free engine snapshot taken at
    generation or claim time, assembled into ``Battle.state_json`` when the
    lobby closes and positions are dealt.

    A guest's battle (issue #305) produces a third shape: a fighter with
    *no* provenance at all. A guest cannot save characters, so nothing
    rolls a ``Character`` row for them to point at — the figure exists only
    as its engine snapshot, which is what actually fights. That is the same
    row shape a tombstoned combatant already has (both FKs ``SET_NULL``),
    which is why the database constraint has always permitted it; ``clean``
    now says so too, for a row that carries a snapshot or a guest's claim.
    """

    battle = models.ForeignKey(
        Battle, on_delete=models.CASCADE, related_name="combatants"
    )
    character = models.ForeignKey(
        "characters.Character",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="battle_combatants",
    )
    beast = models.ForeignKey(
        "bestiary.Beast",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="battle_combatants",
    )
    display_name = models.CharField(max_length=120)
    # Free-for-all battles leave this empty; a team battle would tag sides.
    team = models.CharField(max_length=50, blank=True, default="")
    # Provenance for the muster's labels (#270): True for a combatant seated
    # from the saved-characters list rather than generated for this battle.
    # (``Character.is_played`` cannot answer this — a saved character nobody
    # has edited yet still reads as generated by that flag.)
    is_saved_character = models.BooleanField(default=False)
    # Lobby seats (#250). ``is_open_seat`` marks a row born as a claimable
    # player seat; ``player`` is the human who claimed it (and thereafter
    # controls the combatant in play); ``is_ready`` is the player's lobby
    # ready flag. ``player`` SET_NULLs like the other FKs: a deleted account
    # hands the seat's combatant back to the AI rather than breaking rows.
    is_open_seat = models.BooleanField(default=False)
    player = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="battle_seats",
    )
    # A guest in the seat (#305): ``player`` stays null and this carries
    # the ``common.guest`` token of the visitor holding it, so their own
    # turn prompts reach them and nobody else's do. Exclusive with
    # ``player``, and cleared when adoption sets one.
    guest_key = models.CharField(max_length=64, blank=True, default="", db_index=True)
    is_ready = models.BooleanField(default=False)
    # This combatant's own answer to "who decides?" (#308), or empty to
    # follow the battle's. Three values rather than two on purpose: without
    # an explicit "inherit" a row could not tell "the DM set me automatic"
    # from "nobody has said", and changing the battle default would either
    # move everybody or nobody. Claiming a seat writes ``manual`` here, so
    # #250's seated play keeps working without depending on ``player`` being
    # read as a control flag.
    control_mode = models.CharField(
        max_length=10, choices=COMBATANT_MODE_CHOICES, default=INHERIT, blank=True
    )
    # Position-free engine snapshot (``asdict(CombatantState)`` with q/r/
    # facing placeholders) for combatants of a lobby battle, frozen at
    # generation or claim time. Empty for classic battles, which snapshot
    # straight into ``Battle.state_json`` at creation.
    snapshot_json = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(character__isnull=False, beast__isnull=False),
                name="combatant_not_both_character_and_beast",
            )
        ]

    def clean(self):
        if self.character_id and self.beast_id:
            raise ValidationError("A combatant is exactly one of character or beast.")
        if self.character_id or self.beast_id or self.is_open_seat:
            return
        # Neither FK, and not a seat: legal only for a figure that has no
        # Character row to point at because nobody could have saved it —
        # a guest's fighter in a guest's battle (#305).
        battle = self.battle if self.battle_id else None
        if battle is not None and battle.created_by_id is None:
            return
        raise ValidationError("A combatant is exactly one of character or beast.")

    def __str__(self):
        return f"{self.display_name} in {self.battle.name}"


class BattleEvent(models.Model):
    """One engine event: a roll, an AI decision, an action, a phase marker...

    ``sequence`` is the battle-global event index the engine assigns — unique
    within a battle and monotonic, so other events (a death's fatal roll
    chain) can reference events by sequence number and the UI can anchor them.
    ``message`` is the server-rendered human line (the repo convention: the
    server computes display, the browser only shows it).
    """

    EVENT_TYPES = [
        ("roll", "Roll"),
        ("decision", "Decision"),
        ("action", "Action"),
        ("movement", "Movement"),
        ("damage", "Damage"),
        ("status", "Status"),
        ("death", "Death"),
        ("phase", "Phase"),
        ("info", "Info"),
    ]

    battle = models.ForeignKey(Battle, on_delete=models.CASCADE, related_name="events")
    turn = models.PositiveIntegerField()
    phase = models.PositiveSmallIntegerField()
    sequence = models.PositiveIntegerField()
    event_type = models.CharField(max_length=10, choices=EVENT_TYPES)
    actor = models.CharField(max_length=120, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    message = models.TextField()

    class Meta:
        ordering = ["battle_id", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["battle", "sequence"],
                name="unique_event_sequence_per_battle",
            )
        ]
        indexes = [
            models.Index(fields=["battle", "turn"], name="event_battle_turn_idx"),
        ]

    def __str__(self):
        return f"[{self.battle_id}:{self.sequence}] {self.event_type}: {self.message}"
