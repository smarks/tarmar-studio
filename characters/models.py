import datetime
import math
import re
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.functional import cached_property

from common.helpers import whole_number

from .constants import (
    ATTRIBUTE_BONUS_FIELDS,
    CHARACTER_ATTRIBUTE_FIELDS,
    CHARACTER_NAME_PATTERN,
    FATIGUE_ROLL_MAXIMUM,
    FATIGUE_ROLL_MINIMUM,
    LITERACY_ROLL_MAXIMUM,
    LITERACY_ROLL_MINIMUM,
    MAX_ATTRIBUTE_VALUE,
    MAX_CHARACTER_NAME_LENGTH,
    SEVERE_INJURY_BAND_TOP,
    STARTING_AGE,
)
from .dice import (
    calculate_final_weight_lbs,
    demon_die_to_height_inches,
    format_height,
    parse_dice_expression,
)
from .equipment import (
    ENCUMBRANCE_LEVELS,
    calculate_encumbrance_level,
    combine_movement_restrictions,
    describe_movement_restrictions,
    entry_category,
    get_armor_movement_restrictions,
    get_item,
    get_movement_penalty,
    get_movement_restrictions,
    get_restrictive_armor_names,
    get_walk_limit,
    is_worn_armour,
)
from .movement import GAIT_ORDER, compute_gaits
from .movement import movement_modifier as _movement_modifier
from .skills import specialised_skill_for_granted
from .weapon_skill import weapon_skill_for_granted


@dataclass(frozen=True)
class HistoryEntry:
    """One row of the sheet's Recent Changes table, ready to render.

    The stored history is JSON, so its ``ts`` is an ISO-8601 string. The
    editor printed that string straight into the table --
    ``2026-09-25T15:36:31.856045+00:00``, UTC and microseconds and all --
    while every other date on the same page was local and readable (issue
    #700). Parsing it here gives the template a real instant, which is what
    ``{% local_time %}`` needs to hand the reader their own timezone.

    Attributes:
        when: The instant of the change, or None when the stored value is not
            a timestamp this can read. The template falls back to the raw
            string in that case rather than dropping the row.
        raw_timestamp: Exactly what was stored, for that fallback.
        field: The character field that changed.
        old_value: Its value before.
        new_value: Its value after.
        changed_by: The username recorded with the change.
    """

    when: datetime.datetime | None
    raw_timestamp: str
    field: str
    old_value: str
    new_value: str
    changed_by: str

    @classmethod
    def from_stored(cls, entry: dict) -> "HistoryEntry":
        """Read one stored history dict.

        Args:
            entry: A dict as ``Character.record_change`` writes it.

        Returns:
            The entry with its timestamp parsed where that is possible.
        """
        raw_timestamp = str(entry.get("ts", ""))
        try:
            when = datetime.datetime.fromisoformat(raw_timestamp)
        except ValueError:
            # A row written before this shape existed, or by hand. Keeping the
            # raw string is strictly better than losing the row; the template
            # prints it unchanged.
            when = None
        return cls(
            when=when,
            raw_timestamp=raw_timestamp,
            field=str(entry.get("field", "")),
            old_value=str(entry.get("old", "")),
            new_value=str(entry.get("new", "")),
            changed_by=str(entry.get("by", "")),
        )


# Validators for character attributes (1-30 range)
ATTRIBUTE_VALIDATORS = [MinValueValidator(1), MaxValueValidator(30)]

# Per-character JSON growth bounds. Prevents runaway writes from bloating rows.
MAX_SKILL_ENTRIES = 200
MAX_EQUIPMENT_ITEMS = 500
MAX_EXPERIENCE_LOG_ENTRIES = 200
MAX_HISTORY_ENTRIES = 50
MAX_COMBAT_LOG_ENTRIES = 20


def validate_skills_size(value):
    # Type-check first: every reader of this field (total_allocated_skill_points,
    # highest_skill_level, the templates, the exporters) assumes a dict and has no
    # fallback. A wrong type here must never reach save() silently — see issue #122,
    # where a conditional (isinstance-guarded) check let a wrong-typed value skip
    # validation entirely, save, and permanently 500 the character page.
    if not isinstance(value, dict):
        raise ValidationError("Skills must be an object mapping skill name to points.")
    if len(value) > MAX_SKILL_ENTRIES:
        raise ValidationError(
            f"Too many skills ({len(value)}); max is {MAX_SKILL_ENTRIES}."
        )


def validate_spells(value):
    """A spell record is ``{catalog spell key: skill points}`` (issue #774).

    The keys are the combat spell catalog's (``tarmar_engine.spells``), the
    points whole and within the skill ceiling, so an import cannot seat a
    spell the battle cannot cast.
    """
    from tarmar_engine.spells import SPELLS

    from .skills import MAX_SKILL_POINTS

    if not isinstance(value, dict):
        raise ValidationError("Spells must be an object mapping spell to points")
    for key, points in value.items():
        if key not in SPELLS:
            raise ValidationError(f"Unknown spell: {key}")
        if isinstance(points, bool) or not isinstance(points, int):
            raise ValidationError(f"Points for {key} must be a whole number")
        if not 0 <= points <= MAX_SKILL_POINTS:
            raise ValidationError(
                f"Points for {key} must be between 0 and {MAX_SKILL_POINTS}"
            )


def validate_equipment_size(value):
    if not isinstance(value, list):
        raise ValidationError("Equipment must be a list of item objects.")
    if len(value) > MAX_EQUIPMENT_ITEMS:
        raise ValidationError(
            f"Too many equipment items ({len(value)}); max is {MAX_EQUIPMENT_ITEMS}."
        )


def validate_experience_log_size(value):
    if not isinstance(value, list):
        raise ValidationError("Experience log must be a list of term entries.")
    if len(value) > MAX_EXPERIENCE_LOG_ENTRIES:
        raise ValidationError(
            f"Too many experience entries ({len(value)}); "
            f"max is {MAX_EXPERIENCE_LOG_ENTRIES}."
        )


def validate_history_size(value):
    if not isinstance(value, list):
        raise ValidationError("History must be a list of change entries.")
    if len(value) > MAX_HISTORY_ENTRIES:
        raise ValidationError(
            f"Too many history entries ({len(value)}); max is {MAX_HISTORY_ENTRIES}."
        )


def validate_combat_log_size(value):
    if not isinstance(value, list):
        raise ValidationError("Combat log must be a list of resolved-attack entries.")
    if len(value) > MAX_COMBAT_LOG_ENTRIES:
        raise ValidationError(
            f"Too many combat log entries ({len(value)}); "
            f"max is {MAX_COMBAT_LOG_ENTRIES}."
        )


@dataclass(frozen=True)
class EquipmentRow:
    """One line of a character's equipment, resolved against the catalog.

    A catalog entry stores only its ``item_id`` and how it is carried, so its
    name, weight and category have to be looked up before anything can print
    them; a custom item carries its own. ``Character.equipment_rows`` builds
    these so no reader has to know which kind it is holding (issue #633).
    """

    name: str
    quantity: int
    unit_weight: float
    total_weight: float
    in_pack: bool
    equipped: bool
    #: The catalog category, or "" when the item names none.
    category: str
    notes: str


def survival_save_penalty(pool_maximum: int, current_value: int) -> int:
    """The penalty on a survival save at ``current_value`` (issue #685).

    injury-thresholds-death.md: "**Survival Save Penalty:** Once past
    negative full starting value, the penalty equals how far past the
    threshold you've gone", worked as "A character with Body 30 ... At -30,
    saves continue but are now penalized. At -35, the penalty is -5 (3d6 <=
    CON - 5)". So the penalty is how far below ``-pool_maximum`` the pool has
    fallen, and it is 0 at the threshold itself, where nothing is yet past
    it. The thresholds themselves are a property of the character
    (``injury_thresholds``); the penalty needs the current damage, which the
    sheet does not track, so it is a function rather than a field.

    Args:
        pool_maximum: The pool's starting (undamaged) value.
        current_value: Where the pool stands now, negative when past 0.

    Returns:
        The penalty as a negative number, or 0 while the save is unpenalized.
    """
    past_the_threshold = -pool_maximum - current_value
    return -past_the_threshold if past_the_threshold > 0 else 0


@dataclass(frozen=True)
class PoolReading:
    """One pool's current value beside its injury thresholds (issue #751).

    Attributes:
        current: Where the pool stands now; negative once past 0.
        thresholds: The pool's breakpoints, as
            ``Character._pool_injury_thresholds`` builds them.
    """

    current: int
    thresholds: dict

    @property
    def maximum(self) -> int:
        """The pool's starting (undamaged) value."""
        return int(self.thresholds["pool"])

    @property
    def roll_penalty(self) -> int:
        """The table's -1 or -2 to all rolls, or 0; 0 again once at or below 0."""
        if self.current <= 0:
            return 0
        if self.current <= self.thresholds["minus_two_at"]:
            return -2
        minus_one_at = self.thresholds["minus_one_at"]
        if minus_one_at is not None and self.current <= minus_one_at:
            return -1
        return 0

    @property
    def save_penalty(self) -> int | None:
        """The survival save's penalty (0 while unpenalized), or ``None``.

        ``None`` means no save is due from this pool: it is above minus half
        its starting value.
        """
        if self.current > self.thresholds["survival_save_at"]:
            return None
        return survival_save_penalty(self.maximum, self.current)


@dataclass(frozen=True)
class CurrentCondition:
    """A character's pools now, and where the injury table puts them (#751).

    injury-thresholds-death.md: the bands "apply to both Fatigue and Body
    (whichever is worse)", so every figure here is the worse pool's.

    Attributes:
        fatigue: Current Fatigue.
        fatigue_maximum: Fatigue's starting value.
        body: Current Body.
        body_maximum: Body's starting value.
        conscious: Both pools above 0.
        roll_penalty: -1 or -2 to all rolls, or 0 (0 as well while
            unconscious, when the character takes no rolls for it to touch).
        survival_save_penalty: ``None`` when no survival save is due, else
            its penalty (0 while unpenalized).
        survival_save_target: The CON the save rolls 3d6 against.
        alive: The character is not dead. The dead have pools on the record
            (what killed them) but no band: they read "dead", not
            "unconscious; survival save each turn".
    """

    fatigue: int
    fatigue_maximum: int
    body: int
    body_maximum: int
    conscious: bool
    roll_penalty: int
    survival_save_penalty: int | None
    survival_save_target: int | None
    alive: bool = True

    @classmethod
    def from_pools(
        cls, readings: tuple["PoolReading", "PoolReading"], *, alive: bool = True
    ) -> "CurrentCondition":
        """Read the table for ``(fatigue, body)``, the worse pool governing."""
        fatigue, body = readings
        save_penalties = [
            reading.save_penalty
            for reading in readings
            if reading.save_penalty is not None
        ]
        conscious = alive and fatigue.current > 0 and body.current > 0
        return cls(
            fatigue=fatigue.current,
            fatigue_maximum=fatigue.maximum,
            body=body.current,
            body_maximum=body.maximum,
            conscious=conscious,
            # An unconscious character takes no rolls for the band to touch.
            roll_penalty=(
                min(reading.roll_penalty for reading in readings) if conscious else 0
            ),
            survival_save_penalty=(
                min(save_penalties) if save_penalties and alive else None
            ),
            survival_save_target=fatigue.thresholds["survival_save_target"],
            alive=alive,
        )

    @property
    def unhurt(self) -> bool:
        """Both pools at their starting values."""
        return self.fatigue >= self.fatigue_maximum and self.body >= self.body_maximum

    @staticmethod
    def pools_text(
        fatigue: int, fatigue_maximum: int, body: int, body_maximum: int
    ) -> str:
        """``Fatigue 27/44, Body 30/30``, for any pair of pools."""
        return f"Fatigue {fatigue}/{fatigue_maximum}, Body {body}/{body_maximum}"

    @property
    def pools(self) -> str:
        """``Fatigue 27/44, Body 30/30``."""
        return self.pools_text(
            self.fatigue, self.fatigue_maximum, self.body, self.body_maximum
        )

    @property
    def state(self) -> str:
        """The injury table's row for these pools, in words."""
        if not self.alive:
            return "dead"
        if self.survival_save_penalty is not None:
            penalty = (
                f" {self.survival_save_penalty}" if self.survival_save_penalty else ""
            )
            return (
                "unconscious; survival save each turn "
                f"(3d6 ≤ CON {self.survival_save_target}{penalty})"
            )
        if not self.conscious:
            return "unconscious"
        if self.roll_penalty:
            return f"{self.roll_penalty} to all rolls"
        return "unhurt" if self.unhurt else "hurt, no penalty yet"

    @property
    def summary(self) -> str:
        """``Fatigue 27/44, Body 30/30 (-1 to all rolls)``."""
        return f"{self.pools} ({self.state})"


#: The marks an incomplete character carries wherever it is listed (#571).
NEEDS_ATTRIBUTES = "needs attributes"
NEEDS_FATIGUE_ROLL = "needs Fatigue roll"


class CharacterQuerySet(models.QuerySet):
    """Character queries, with completeness as a filter (issue #571)."""

    def playable(self):
        """Characters the rules can use: all six attributes and the Fatigue roll.

        Battle seating, party muster and the Games Master's attach picker
        offer only these. Completeness is derived, never declared, so there is
        no flag for anyone to forget to set.
        """
        return self.filter(
            fatigue_roll__isnull=False,
            **{f"{name}__isnull": False for name in CHARACTER_ATTRIBUTE_FIELDS},
        )


def board_can_seat_weapon(item: dict) -> bool:
    """Whether the battle board can seat this equipment entry as a weapon.

    A catalog weapon with a class and a rollable damage expression; a custom
    weapon, or "varies" / "by STR" damage, the board passes over. The one
    answer ``battle.services._seated_arms`` and the sheet's default weapon
    in hand share (review of #846).
    """
    item_data = get_item(item.get("item_id", ""))
    if not item_data or item_data.get("category") != "weapons":
        return False
    damage = item_data.get("damage")
    if not item_data.get("weapon_class") or not isinstance(damage, str):
        return False
    try:
        parse_dice_expression(damage)
    except ValueError:
        return False
    return True


def carries_a_weapon(equipment: list | None) -> bool:
    """Whether any of this equipment is a weapon the board can seat (#844).

    Without one a character is seated bare-handed
    (``battle.services._seated_arms``); the sheet, the Games Master's
    Characters tab and a session fight's Battle tab all say so from this.
    """
    return any(
        isinstance(item, dict) and board_can_seat_weapon(item)
        for item in equipment or []
    )


#: The die-roll text fields and what the sheet calls them (issue #805).
DIE_ROLL_FIELDS: dict[str, str] = {"height_roll": "Height", "weight_roll": "Weight"}

#: A die roll as the sheet stores it: one face, or a demon die's chain -- a
#: run of exploding 1s and 6s, each followed by the next roll ("6.6.2").
_DEMON_DIE_CHAIN = re.compile(r"^(?:[16]\.)*[1-6]$")


def is_demon_die_chain(text: str) -> bool:
    """Whether ``text`` is a die roll the height and weight tables can read.

    attributes.md: height is "Choose or roll 1d6", rolled again on a 1 or a
    6; weight is a 1d6 face (older records keep a chain, whose last face is
    read). A face outside 1 to 6, a 2 to 5 anywhere but last, or anything
    else is not a roll (issue #805).
    """
    return bool(_DEMON_DIE_CHAIN.match(text.strip()))


class Character(models.Model):
    """A player character with rolled attributes."""

    objects = CharacterQuerySet.as_manager()

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="characters",
        db_index=True,
    )
    name = models.CharField(max_length=100)

    # Core attributes (3-18 from 3d6, validated 1-30 for aging). Null until
    # recorded: a character built in a Games Master session is a row from its
    # first change, before its attributes are known (issue #571).
    strength = models.PositiveSmallIntegerField(
        null=True, validators=ATTRIBUTE_VALIDATORS
    )
    dexterity = models.PositiveSmallIntegerField(
        null=True, validators=ATTRIBUTE_VALIDATORS
    )
    intelligence = models.PositiveSmallIntegerField(
        null=True, validators=ATTRIBUTE_VALIDATORS
    )
    wisdom = models.PositiveSmallIntegerField(
        null=True, validators=ATTRIBUTE_VALIDATORS
    )
    constitution = models.PositiveSmallIntegerField(
        null=True, validators=ATTRIBUTE_VALIDATORS
    )
    charisma = models.PositiveSmallIntegerField(
        null=True, validators=ATTRIBUTE_VALIDATORS
    )

    # Demon die results stored as strings (e.g., "3", "1.1.4", "6.6.2").
    # blank=True because "" is the field's own default and its readers all
    # handle it (height_inches and weight_lbs return None), and because the
    # sheet renders these as free-text boxes a player may clear. Without it
    # the write path and import disagreed: clearing the box saved, and the
    # character's own export would not import (issue #637).
    appearance_roll = models.CharField(
        max_length=50, blank=True, default="", db_default=""
    )
    height_roll = models.CharField(max_length=50, blank=True, default="", db_default="")
    weight_roll = models.CharField(max_length=50, blank=True, default="", db_default="")

    # Fatigue roll (2d6 component, 2-12); null until rolled (issue #571).
    # The bounds are on the column because they are what the dice can produce,
    # not a presentation choice: without them the field took anything up to the
    # smallint ceiling and a stated 30000 reached Fatigue, Body, the injury
    # thresholds and battle seating (issue #653).
    fatigue_roll = models.PositiveSmallIntegerField(
        null=True,
        validators=[
            MinValueValidator(FATIGUE_ROLL_MINIMUM),
            MaxValueValidator(FATIGUE_ROLL_MAXIMUM),
        ],
    )

    # Provenance (social class)
    PROVENANCE_CLASSES = [
        ("nobility", "Nobility"),
        ("merchant", "Merchant"),
        ("commoner", "Commoner"),
    ]
    provenance_class = models.CharField(
        max_length=20, choices=PROVENANCE_CLASSES, default="commoner"
    )
    # Free text on the sheet, so a player may clear it: blank=True for the
    # same reason as the roll fields above (issue #637).
    provenance_detail = models.CharField(max_length=50, blank=True, default="Laborer")
    # Whether the provenance is on the record: rolled on the table, or set by a
    # Game Master (issue #749). A player rolls it once and may not choose it
    # (Spencer's ruling, 2026-09-28); the roll control and the field-update
    # refusal both hang on this. True by default so that no path which forgets
    # to say otherwise hands a player a roll; a character nobody rolled (one
    # started in a Games Master session) is created with it False.
    provenance_recorded = models.BooleanField(default=True, db_default=True)

    # Location
    LOCATION_CHOICES = [
        ("City", "City"),
        ("Village", "Village"),
        ("Rural", "Rural"),
        ("Consult GM", "Consult GM"),
    ]
    location = models.CharField(
        max_length=20, choices=LOCATION_CHOICES, default="Village"
    )
    # Whether the Origin is on the record: rolled on the table, or chosen
    # (location.md: "Roll percentile dice or choose") (issue #754). A character
    # nobody rolled or chose an Origin for holds the model's Village, with no
    # skill granted for it; the chat's roll_background rolls it. Same default
    # as provenance_recorded, for the same reason.
    location_recorded = models.BooleanField(default=True, db_default=True)
    # The skill holding the one point the Origin grants (location.md: "City:
    # Street smarts ... Village: Street smarts OR survival ... Rural:
    # Survival"), normalized; empty when no grant is on the record (issue
    # #754). An Origin change moves exactly this point
    # (services.apply_origin_skill), so a point the player bought in the same
    # skill is never taken as the grant, and none is made twice.
    origin_skill = models.CharField(
        max_length=50, blank=True, default="", db_default=""
    )
    # The 3d6 rolled for the literacy check, null until rolled (issue #741).
    # Only the dice are stored: whether they pass is read against the check the
    # current origin and INT give, so a corrected INT re-reads the same roll.
    # Once it is rolled a player may not change the origin, and a Game Master
    # who does rolls it again (services.apply_origin_to_literacy). Bounded to
    # what 3d6 can produce, like the Fatigue roll (issue #653).
    literacy_roll = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[
            MinValueValidator(LITERACY_ROLL_MINIMUM),
            MaxValueValidator(LITERACY_ROLL_MAXIMUM),
        ],
    )
    # Whether literacy has ever been rolled for this character (#741). The
    # player's Origin lock and their one roll hang on this, not on a roll being
    # stored now: a Game Master who moves a rolled character to "Consult GM"
    # clears the stored roll, and that must not hand the player a fresh origin
    # and a second roll. Set by the roll, never cleared.
    literacy_rolled = models.BooleanField(default=False, db_default=False)
    # The Game Master's ruling on whether the character can read, for an
    # origin of "Consult GM", which publishes no check to roll (issue #743):
    # True literate, False illiterate, None not ruled. Kept apart from the
    # roll so the sheet can say which it was. Set only through
    # services.rule_literacy, which a Game Master alone may call, and only at
    # "Consult GM"; withdrawn when a Game Master moves the Origin to one with
    # a check (services.apply_origin_to_literacy). A ruling fixes a player's
    # Origin as a roll does (services.can_change_origin).
    literacy_ruling = models.BooleanField(null=True, blank=True, default=None)
    # What the Game Master rules an origin of "Consult GM" is (issue #801):
    # location.md's 100 row defers to him, and the Origin field holds only the
    # table's four rows. Free text, the Game Master's alone, and only while
    # the Origin is "Consult GM" (services.change_field); withdrawn, with a
    # history line, when a Game Master moves the Origin to a row of the table.
    origin_ruling = models.CharField(
        max_length=200, blank=True, default="", db_default=""
    )
    # The character's people (issue #810): the two the canon opens to players,
    # humankind and the Kanu. A player chooses one of these; nobody rolls it.
    PEOPLES = [
        ("Human", "Human"),
        ("Kanu", "Kanu"),
    ]
    people = models.CharField(
        max_length=20, choices=PEOPLES, default="Human", db_default="Human"
    )
    # Whether the character is Dragonborn: the Game Master's secret (#810).
    # Set only by a Game Master, never shown on a player's sheet, and never
    # exported (a player's export would tell them). The player sees
    # ``people``.
    is_dragonborn = models.BooleanField(
        default=False, db_default=False, choices=[(False, "No"), (True, "Yes")]
    )
    # When the character entered play (issue #803): its first battle seat, or
    # the first completed Games Master turn it was attached to while complete
    # (services.record_entry_into_play). Written once and never cleared, so
    # detaching it from a session does not take it out of play. A player adds
    # prior-experience years only before it.
    entered_play_at = models.DateTimeField(null=True, blank=True, default=None)
    # Skill points a lowered XP could not take back because they were spent
    # (issue #769): the next XP the character earns pays this off before any
    # new point is granted, so lowering and raising XP never makes points.
    skill_points_owed = models.PositiveIntegerField(default=0, db_default=0)
    # What a player's import dropped and why (issue #744): a player's import
    # arrives as a new character, its rolled, earned and ruled values at a
    # fresh character's, and the sheet says so until somebody dismisses it. Empty when there is nothing to say. Not
    # exported: it is about this copy's arrival, not about the character.
    import_notice = models.TextField(blank=True, default="", db_default="")

    # Wealth
    WEALTH_CHOICES = [
        ("Subsistence", "Subsistence"),
        ("Moderate", "Moderate"),
        ("Merchant", "Merchant"),
        ("Rich", "Rich"),
    ]
    wealth_level = models.CharField(
        max_length=20, choices=WEALTH_CHOICES, default="Moderate"
    )
    # Null when the wealth table gives no number: Rich is "Consult GM"
    # (wealth.md), so its coin waits for a Game Master to set it rather than
    # reading as 0 (issue #749).
    starting_coin = models.PositiveIntegerField(null=True, blank=True, default=100)
    # Whether the wealth level is on the record, rolled or set by a Game
    # Master (issue #749). The coin is rolled once, when the level is first
    # recorded; re-selecting a level never rolls it again. Same default as
    # provenance_recorded, for the same reason.
    wealth_recorded = models.BooleanField(default=True, db_default=True)

    # Log of all dice rolls made during character creation
    dice_log = models.TextField(blank=True, default="")

    # Skills data
    skills = models.JSONField(
        default=dict, validators=[validate_skills_size]
    )  # {"sword": 3, "navigation": 1, ...}
    free_skill_points = models.PositiveSmallIntegerField(default=0)
    # The Mana Pool (issue #774): the Magic track's "Spell Point" grants,
    # tracked as their own pool rather than folded into skill points, plus
    # whatever a Game Master grants (``characters.magic.grant_mana``). The
    # battle seats it as the caster's mana.
    free_spell_points = models.PositiveSmallIntegerField(default=0)
    # The spells the character knows (issue #774): ``{spell key: skill
    # points}``, keyed by the combat spell catalog (``tarmar_engine.spells``).
    # spell-mastery.md: "Multiple skill points in a spell grant additional
    # abilities", level 1 being "Can cast the spell"; the level comes from the
    # same ladder as every skill. Written only by ``characters.magic``.
    # db_default as well as default, on both new columns: the colour still
    # serving during a deploy inserts rows without them (issue #575).
    spells = models.JSONField(
        default=dict, db_default={}, blank=True, validators=[validate_spells]
    )
    # Coin gained (+) or spent (-) since creation (issue #757): purchases from
    # the catalog (never refunded) and Game Master grants. The purse
    # is starting coin plus prior-experience earnings plus this; see
    # ``characters.purse.move_coin``, the one writer.
    coin_adjustment = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, db_default=0
    )

    # Equipment inventory
    equipment = models.JSONField(
        default=list, blank=True, validators=[validate_equipment_size]
    )
    pack_dropped = models.BooleanField(default=False)  # True if pack is dropped

    # Experience tracking
    total_xp = models.PositiveIntegerField(default=0)
    age = models.PositiveSmallIntegerField(default=STARTING_AGE)
    # One per prior-experience run recorded in the log. Runs used to be fixed
    # 4-year terms, which is why the field is named this way; since issue #221
    # a run is however many years the player asked for, so the sheet counts
    # years (see :attr:`experience_years`) and this stays the log-entry count.
    experience_terms = models.PositiveSmallIntegerField(default=0)
    experience_log = models.JSONField(
        default=list, validators=[validate_experience_log_size]
    )  # Audit trail of experience runs
    money_earned = models.PositiveIntegerField(default=0)  # From prior experience

    # Aging penalties (cumulative totals)
    aging_str_penalty = models.SmallIntegerField(default=0)
    aging_dex_penalty = models.SmallIntegerField(default=0)
    aging_int_penalty = models.SmallIntegerField(default=0)
    aging_wis_penalty = models.SmallIntegerField(default=0)
    aging_con_penalty = models.SmallIntegerField(default=0)

    # Character status
    is_alive = models.BooleanField(default=True)
    death_cause = models.CharField(max_length=200, blank=True, default="")

    # Wounds carried between fights (issue #751): how far each pool sits
    # below its maximum. Stored as the loss rather than as a current value so
    # that 0 means unhurt with no special case, and a maximum that moves
    # (aging, a CON gain) moves the current value with it rather than
    # leaving a stored number above the new ceiling. The rules keep damage
    # until it is healed ("Survival saves continue each turn until the
    # character is healed above the threshold", injury-thresholds-death.md)
    # and publish no rate of recovery, so nothing lowers these on a timer:
    # a fight writes them (``services.record_fight``) and a Game Master sets
    # them (``services.set_current_pools``).
    fatigue_damage = models.PositiveSmallIntegerField(default=0, db_default=0)
    body_damage = models.PositiveSmallIntegerField(default=0, db_default=0)
    # Mana spent and not yet restored, beside the wounds (coordinator's
    # ruling under the standing rule; Spencer may overrule): the rules
    # publish no mana recovery either, so what a fight spends stays spent
    # until a Game Master restores it. Stored as the amount spent for the
    # same reasons as the damage columns.
    mana_spent = models.PositiveSmallIntegerField(default=0, db_default=0)
    is_played = models.BooleanField(default=False)  # True if user has manually edited

    # Visibility (issue #273, the parties chain's foundation). Private -- the
    # default, and what every pre-existing character migrated as -- means only
    # the owner and DM/admin users can see it at all. Public means every
    # registered user may READ it (sheet, list, exports); editing stays
    # owner/DM-only regardless (`_can_edit_object` never consults this flag).
    is_public = models.BooleanField(default=False)

    # Per-field change history (lightweight; issue #27 Option A). Each entry:
    # {ts: ISO-8601 string, field: str, old: str, new: str, by: username}.
    history = models.JSONField(
        default=list, blank=True, validators=[validate_history_size]
    )

    # Resolved attacks from the sheet's Attack Resolution panel, oldest first
    # and capped at MAX_COMBAT_LOG_ENTRIES (issue #101). Each entry is the
    # server's own summary of one roll — see characters/attack.py's
    # `log_entry`. It exists because a result that vanished on reload would be
    # transient UI, which this project's rules forbid.
    #
    # Deliberately NOT in character_export's EXPORTABLE_FIELDS, for the same
    # reason `history` is not: it is per-session audit noise about one table's
    # play, not part of the character an import should reconstitute.
    combat_log = models.JSONField(
        default=list, blank=True, validators=[validate_combat_log_size]
    )

    # Soft delete
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        # Intentionally retained (issue #108): Character used to live in the
        # accounts app and the table kept that name when the model moved to
        # characters/. An AlterModelTable rename looks tempting but is unsafe
        # under this repo's blue-green deploy (deploy.sh): migrate runs against
        # the shared Postgres DB *before* the still-serving old-color process is
        # cut over, so a table rename would 404/500 every character query on the
        # old color for the whole verify+switch window. Leave the historical
        # name in place rather than risk a production outage for a cosmetic fix.
        db_table = "accounts_character"
        ordering = ["-created_at"]
        # No unique name per user (issue #571): a character's identity is its
        # id, and two characters may share a name.
        indexes = [
            # Serves the character-list query: ORDER BY is_deleted, -created_at.
            models.Index(
                fields=["is_deleted", "-created_at"],
                name="char_deleted_created_idx",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.user.username})"

    # Computed properties that scan the skills/equipment JSON are memoized with
    # @cached_property (they are read several times per request). The JSON fields
    # are mutable, so drop the caches on save() — after persisting a change the
    # next read recomputes from the new data.
    _CACHED_DERIVED = (
        "total_allocated_skill_points",
        "total_equipment_weight",
        "pack_weight",
        "carried_weight",
    )

    #: The five denormalised aging columns, in the order the schedule names
    #: them, paired with the key ``get_aging_penalties_for_age`` returns.
    _AGING_PENALTY_FIELDS: tuple[tuple[str, str], ...] = (
        ("aging_str_penalty", "strength"),
        ("aging_dex_penalty", "dexterity"),
        ("aging_int_penalty", "intelligence"),
        ("aging_wis_penalty", "wisdom"),
        ("aging_con_penalty", "constitution"),
    )

    def reconcile_aging_penalties(self) -> list[str]:
        """Set the five aging columns from this character's age (issue #680).

        Aging is a pure function of age -- aging.md: "Aging is keyed to
        **age**, not to how experience was bought ... Every number in the
        table below is a **cumulative lifetime total**" -- so the columns are
        a cache of ``get_aging_penalties_for_age``, not a second source of
        truth. They used to be written only by ``apply_experience_run``, and
        only when a run crossed a four-year boundary, so an import could
        produce a character of 71 carrying no penalties at all and a run that
        ended in death advanced the age while skipping the aging those years
        earned (issue #654). ``save()`` calls this, which is what makes the
        invariant hold however the age was set.

        Returns:
            The names of the columns this changed, so ``save()`` can add them
            to an ``update_fields`` it was given.
        """
        from .skills import get_aging_penalties_for_age

        if self.age is None:  # pragma: no cover - age is non-null with a default
            return []
        totals = get_aging_penalties_for_age(self.age) or {}
        changed = []
        for column, attribute in self._AGING_PENALTY_FIELDS:
            reconciled = abs(totals.get(attribute, 0))
            if getattr(self, column) != reconciled:
                setattr(self, column, reconciled)
                changed.append(column)
        return changed

    #: The JSON collections whose growth bounds must hold on every write.
    #: Django does not run field validators on ``save()`` (only ``full_clean``
    #: does), so before issue #655 these caps only ever ran on the import
    #: path: a character could be grown past them through the editing
    #: endpoints and was then refused by its own export/import round trip.
    _SIZE_VALIDATED_COLLECTIONS = (
        "skills",
        "equipment",
        "experience_log",
        "history",
        "combat_log",
    )

    def validate_collection_sizes(self) -> None:
        """Run the JSON growth-bound validators (issue #655).

        Raises:
            ValidationError: A collection is past its cap, or is not the type
                its readers assume.
        """
        for field_name in self._SIZE_VALIDATED_COLLECTIONS:
            for validator in self._meta.get_field(field_name).validators:
                validator(getattr(self, field_name))

    def save(self, *args, **kwargs):
        self.validate_collection_sizes()
        reconciled_fields = self.reconcile_aging_penalties()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and reconciled_fields:
            kwargs["update_fields"] = [*update_fields, *reconciled_fields]
        super().save(*args, **kwargs)
        for attribute in self._CACHED_DERIVED:
            self.__dict__.pop(attribute, None)

    @property
    def missing_parts(self) -> list[str]:
        """What this character still needs before the rules can use it (#571).

        Empty for a complete character; otherwise the marks lists show, such
        as "needs attributes" and "needs Fatigue roll".
        """
        missing = []
        if any(getattr(self, name) is None for name in CHARACTER_ATTRIBUTE_FIELDS):
            missing.append(NEEDS_ATTRIBUTES)
        if self.fatigue_roll is None:
            missing.append(NEEDS_FATIGUE_ROLL)
        return missing

    def pools(self) -> tuple[int, int]:
        """``(fatigue, body)`` for a character the rules can use.

        Raises:
            ValueError: The character is not complete, so its pools are
                unknown (issue #571). Battle seating and the Games Master's
                sheets only ever take complete characters.
        """
        fatigue, body = self.fatigue, self.body
        if fatigue is None or body is None:
            raise ValueError(
                f"{self.name} is not complete: {', '.join(self.missing_parts)}"
            )
        return fatigue, body

    def _any_missing(self, *names: str) -> bool:
        """Whether any of the named attributes is not recorded yet."""
        return any(getattr(self, name) is None for name in names)

    @property
    def is_complete(self) -> bool:
        """All six attributes and the Fatigue roll are recorded (issue #571)."""
        return not self.missing_parts

    def mark_played_by(self, user) -> None:
        """Flag this character as played, but only when `user` is its owner.

        An admin/DM editing someone else's character does not mean the owner
        played it. ``character_update_field`` has always drawn this line;
        the equipment, skill, and experience-term endpoints didn't -- each
        set ``is_played`` unconditionally, so a DM fixing up someone else's
        sheet silently flipped their status from "Ready" to "Played" (issue
        #91). This is the one place that decides it now, called uniformly
        from every mutating endpoint.
        """
        if self.user_id == user.id and not self.is_played:
            self.is_played = True

    def save_equipment(self, equipment, *, user) -> None:
        """Persist a new equipment list: assign, mark played, and save.

        Bundles the three-line write every equipment-mutating endpoint
        repeated (issue #91). Callers still do their own audit logging
        (``_log_equipment_change``) since that's request-shaped, not
        something a model method should reach for.
        """
        self.equipment = equipment
        self.mark_played_by(user)
        self.save()

    def record_field_change(
        self, field: str, old_value, new_value, username: str
    ) -> None:
        """Append a per-field change entry to ``history``, capped at the last N.

        Values are stringified so the JSON stays serializable for any field
        type (ints, strings, dicts) without invoking custom encoders. A no-op
        change (old == new) records nothing.
        """
        if old_value == new_value:
            return
        entries = list(self.history or [])
        entries.append(
            {
                "ts": timezone.now().isoformat(),
                "field": field,
                "old": str(old_value),
                "new": str(new_value),
                "by": username,
            }
        )
        # Keep only the most recent N entries.
        self.history = entries[-MAX_HISTORY_ENTRIES:]

    def apply_field_change(self, field: str, value):
        """Set `field` to `value`, applying the derived recompute it needs.

        Returns the field's prior value, for the caller to pass to
        :meth:`record_field_change`. Does not validate (see
        :meth:`clean_field_value`), save, or mark played -- the caller owns
        those, same as every other mutating endpoint.
        """
        old_value = getattr(self, field)
        setattr(self, field, value)
        # wealth_level used to reroll starting_coin on every write, so
        # re-selecting Merchant was a free reroll of the coin (issue #749).
        # The coin now follows the wealth level only when the level is first
        # recorded, in services.change_field.
        return old_value

    @classmethod
    def clean_field_value(cls, field, value):
        """Validate and coerce `value` for `field`.

        Returns the coerced value on success, or raises ``ValidationError``
        (the same exception ``full_clean()`` raises, so a caller already
        translating that -- ``character_import`` -- handles this identically)
        on failure. Handles the name rules, string max-lengths, choice
        membership, and integer conversion/bounds.

        A classmethod, not an instance method: ``character_import`` validates
        raw field values before a ``Character`` exists to hold them, which is
        exactly the reuse issue #89 named this logic as needing. It shares
        the whitelist and per-field bounds below with
        ``character_update_field``, so import can't grant a value the
        single-field edit endpoint would refuse (issue #136).
        """
        if field == "name":
            value = str(value).strip()
            if not value:
                raise ValidationError("Name cannot be empty")
            if len(value) > MAX_CHARACTER_NAME_LENGTH:
                raise ValidationError(
                    f"Name must be {MAX_CHARACTER_NAME_LENGTH} characters or fewer"
                )
            if not CHARACTER_NAME_PATTERN.match(value):
                raise ValidationError(
                    "Name may contain letters, numbers, spaces, "
                    "hyphens, underscores, periods, and apostrophes only"
                )

        if field in CHARACTER_BOOLEAN_FIELDS:
            if isinstance(value, bool):
                return value
            if str(value).strip().lower() in ("true", "1", "yes", "on"):
                return True
            if str(value).strip().lower() in ("false", "0", "no", "off", ""):
                return False
            raise ValidationError(f"{field} must be yes or no")

        if field in CHARACTER_STRING_MAX_LENGTHS:
            max_len = CHARACTER_STRING_MAX_LENGTHS[field]
            if len(str(value)) > max_len:
                raise ValidationError(f"{field} must be {max_len} characters or fewer")

        if field in CHARACTER_CHOICE_FIELDS:
            valid_values = CHARACTER_CHOICE_FIELDS[field]
            # A list is unhashable, so ``in`` a dict raised TypeError (#767).
            if not isinstance(value, str) or value not in valid_values:
                options = ", ".join(valid_values)
                raise ValidationError(f"{field} must be one of: {options}")

        if field in DIE_ROLL_FIELDS and value is None:
            # No roll is a blank cell; None reached the column and failed
            # there (review of #805). A player is refused it earlier, by the
            # one-face rule (services.check_player_choice).
            value = ""
        if field in DIE_ROLL_FIELDS and value != "":
            # A height or weight that does not parse used to read as a real
            # one: "tall" showed 5'4" (issue #805). Refused here, the one
            # check the sheet, the import and every edit path share.
            if not is_demon_die_chain(str(value)):
                raise ValidationError(
                    f"{DIE_ROLL_FIELDS[field]} must be die faces 1 to 6, joined "
                    "by dots for a chain of 1s and 6s: 4, or 6.6.2"
                )

        if field in CHARACTER_INT_FIELDS:
            if value is None and field in CHARACTER_NULLABLE_INT_FIELDS:
                return None
            if value is None:
                raise ValidationError("Value is required")
            try:
                value = whole_number(value)
            except ValueError:
                raise ValidationError("Invalid number") from None
            min_value = CHARACTER_INT_FIELD_MIN[field]
            if value < min_value:
                # Every field here but fatigue_roll has a floor of 0 or 1, and
                # "positive" says that plainly; the Fatigue roll's floor is the
                # lowest 2d6 can produce, which needs naming (issue #653).
                raise ValidationError(
                    "Value must be positive"
                    if min_value <= 1
                    else f"Value must be {min_value} or more"
                )
            if field in CHARACTER_ATTRIBUTE_FIELDS and value > MAX_ATTRIBUTE_VALUE:
                raise ValidationError(f"Attribute max is {MAX_ATTRIBUTE_VALUE}")
            field_max = CHARACTER_INT_FIELD_MAX[field]
            if value > field_max:
                raise ValidationError(f"Value must be {field_max} or fewer")

        return value

    def _apply_attribute_bonus(self, attribute: str, points: int = 1) -> None:
        """Apply "+1 STR/DEX/CON" experience results, capped at the attribute max.

        Args:
            attribute: The short name the result carries -- "str", "dex" or
                "con".
            points: How many times the run rolled that result. A run's
                results reach here aggregated, so three "+1 STR" years arrive
                as one ``("+1 str", 3)`` pair and must raise Strength by
                three; raising it once was issue #629, and it made four
                one-year runs disagree with one four-year run, which
                survivability.md states they must not.
        """
        attribute_field = ATTRIBUTE_BONUS_FIELDS.get(attribute)
        if attribute_field is None:
            return
        current_score = getattr(self, attribute_field)
        setattr(self, attribute_field, min(MAX_ATTRIBUTE_VALUE, current_score + points))

    def apply_experience_run(self, run_result, track: str, skill_points_from_xp: int):
        """Apply a simulated run's results to this character (caller saves).

        Updates age/XP/money, distributes the run's skills (handling the "+1
        STR", "skill point", and "spell point" specials), records how many
        free skill points the run granted, applies aging penalties and
        death, and appends the run to the experience log. Does not mark
        played or save -- the caller does, uniformly with every other
        mutating endpoint (issue #91).

        The core game rules this used to hide (aging tables, survival
        checks, skill-point thresholds) already live in ``skills.py`` as of
        issues #221/#235; this method is what's left that genuinely mutates
        a character in place, per issue #89.
        """
        free_skill_points_before = self.free_skill_points
        # A run that ends in death stops early (see simulate_experience_years), so
        # `years` may hold fewer entries than were asked for -- its last entry's
        # age is the character's age when the run ended, whether that's the full
        # advance or the year they died (issue #147).
        self.age = run_result["years"][-1]["age"]
        self.total_xp += run_result["xp_awarded"]
        self.free_skill_points += skill_points_from_xp
        self.money_earned += run_result["money_earned"]
        self.experience_terms += 1

        current_skills = self.skills or {}
        for skill, points in run_result["skills_gained"].items():
            if skill.startswith("+1 "):
                self._apply_attribute_bonus(skill[3:].lower(), points)
            elif skill == "skill point":
                self.free_skill_points += points
            elif skill == "spell point":
                self.free_spell_points += points
            else:
                skill = specialised_skill_for_granted(
                    current_skills, weapon_skill_for_granted(current_skills, skill)
                )
                current_skills[skill] = current_skills.get(skill, 0) + points
        self.skills = current_skills

        # Free skill points this run granted (XP-threshold points plus any "skill
        # point" results), for display in the history entry.
        run_result["free_points_awarded"] = (
            self.free_skill_points - free_skill_points_before
        )

        # The aging columns are not written here any more: save() reconciles
        # them from the age this run reached, so a run that ended in death --
        # which returns before the simulator works out its aging totals -- no
        # longer advances the age while skipping the penalties those years
        # earned (issues #654, #680). run_result["aging"] stays as the log
        # line's "aging penalties applied" note.

        if not run_result["alive"]:
            self.is_alive = False
            self.death_cause = (
                f"Failed survival check as a {track.replace('_', ' ')} "
                f"at age {self.age}"
            )

        # Capped like combat_log and history are, rather than growing without
        # bound until the row is too big for its own importer (issue #655).
        # The oldest runs fall off the front; the log is a history, and the
        # numbers a run produced are already on the sheet.
        log_entries = (self.experience_log or []) + [run_result]
        self.experience_log = log_entries[-MAX_EXPERIENCE_LOG_ENTRIES:]

    @property
    def fatigue(self):
        """Calculate fatigue: CON + WIS + INT + max(DEX, STR) + 2d6 roll.

        Reads effective (aged) scores, per the ruling on #773: aging lowers an
        attribute everywhere except the creation-time checks. The 2d6 Fatigue
        roll itself is made once, at creation, and aging never touches it.
        """
        constitution = self.effective_constitution
        wisdom = self.effective_wisdom
        intelligence = self.effective_intelligence
        dexterity = self.effective_dexterity
        strength = self.effective_strength
        if (
            constitution is None
            or wisdom is None
            or intelligence is None
            or dexterity is None
            or strength is None
            or self.fatigue_roll is None
        ):
            return None
        return (
            constitution
            + wisdom
            + intelligence
            + max(dexterity, strength)
            + self.fatigue_roll
        )

    @property
    def body(self):
        """Calculate body points: 2/3 of fatigue, rounded up; None if incomplete."""
        fatigue = self.fatigue
        return None if fatigue is None else math.ceil(fatigue * 2 / 3)

    def _pool_injury_thresholds(self, pool_maximum: int) -> dict:
        """Injury and death thresholds for one pool, per the injury rules.

        Source: ``reference/content/public-rules/characters/injury-thresholds-death.md``.
        A pool (Fatigue or Body) at or below half its starting value costs −1 on
        all rolls while it is still above 5; from 5 down to 1 the penalty is −2;
        0 is unconscious; at minus half the starting value the character makes a
        survival save (3d6 ≤ CON) every turn; past minus the full starting value
        those saves are penalized by how far past the threshold they are.

        Rounding (the md gives only the even example Body 30): each threshold is
        the first whole number reached as the pool falls past the exact
        fractional value. For an odd pool of 31 the −1 penalty starts at 15
        (15.5 rounds down, since 16 is still above half) and the survival saves
        start at −16 (−15 has not yet passed −15.5). Both collapse to the
        obvious value for even pools, which is the only case the rules show.

        Args:
            pool_maximum: The pool's starting (undamaged) value.

        Returns:
            A dict of threshold values. ``minus_one_at`` is ``None`` when the
            pool is small enough that half of it already sits inside the 1–5
            band, where the −2 penalty applies instead and there is no −1 range.
        """
        half_rounded_down = pool_maximum // 2
        return {
            "pool": pool_maximum,
            "minus_one_at": (
                half_rounded_down
                if half_rounded_down > SEVERE_INJURY_BAND_TOP
                else None
            ),
            "minus_two_at": SEVERE_INJURY_BAND_TOP,
            "unconscious_at": 0,
            "survival_save_at": -math.ceil(pool_maximum / 2),
            "penalized_save_at": -pool_maximum,
            # Effective (aged) CON: a check made in play (ruling on #773).
            "survival_save_target": self.effective_constitution,
        }

    @property
    def current_fatigue(self) -> int | None:
        """Fatigue now: the maximum less the damage carried (issue #751)."""
        fatigue = self.fatigue
        return None if fatigue is None else fatigue - self.fatigue_damage

    @property
    def current_body(self) -> int | None:
        """Body now: the maximum less the damage carried (issue #751)."""
        body = self.body
        return None if body is None else body - self.body_damage

    @property
    def carries_weapon(self) -> bool:
        """Whether the board would seat a weapon for this character (#844)."""
        return carries_a_weapon(self.equipment)

    @property
    def current_mana(self) -> int:
        """The Mana Pool now: the pool less the mana spent, never below 0.

        The pool is ``free_spell_points`` (the Spell Points earned through the
        Magic track, as the battle seats it); the floor covers a pool that
        shrank after the mana was spent.
        """
        return max(0, self.free_spell_points - self.mana_spent)

    @property
    def mana_text(self) -> str:
        """``Mana 3/5``, or ``""`` for a character with no Mana Pool."""
        if not self.free_spell_points:
            return ""
        return f"Mana {self.current_mana}/{self.free_spell_points}"

    @property
    def condition(self) -> "CurrentCondition | None":
        """Where the injury table puts this character now (issue #751).

        ``None`` for a character whose pools are not known yet.
        """
        thresholds = self.injury_thresholds
        current_fatigue, current_body = self.current_fatigue, self.current_body
        if thresholds is None or current_fatigue is None or current_body is None:
            return None
        return CurrentCondition.from_pools(
            (
                PoolReading(current_fatigue, thresholds["fatigue"]),
                PoolReading(current_body, thresholds["body"]),
            ),
            alive=self.is_alive,
        )

    @property
    def injury_thresholds(self):
        """Injury/death thresholds for both pools, keyed ``fatigue``/``body``.

        The breakpoints to compare a pool against; where the character's own
        pools sit against them now is :attr:`condition`.
        """
        fatigue, body = self.fatigue, self.body
        if fatigue is None or body is None:
            return None
        return {
            "fatigue": self._pool_injury_thresholds(fatigue),
            "body": self._pool_injury_thresholds(body),
        }

    @property
    def height_inches(self):
        """Calculate height in inches from demon die roll.

        None for a stored value that does not parse (issue #805): it used to
        read as a 3, a height nobody rolled.
        """
        if not self.height_roll or not is_demon_die_chain(self.height_roll):
            return None
        return demon_die_to_height_inches(self.height_roll)

    @property
    def height_display(self):
        """Format height as feet'inches" string."""
        inches = self.height_inches
        return "" if inches is None else format_height(inches)

    @property
    def weight_lbs(self):
        """Weight in pounds from the demon die roll and Strength.

        Reads base STR. attributes.md: "Final weight = Base weight + (STR / 2)
        stones", among the secondary attributes a character is made with. The
        ruling on #773 (2026-09-28) lowers an attribute everywhere in play
        except what is decided when the character is made, which reads the
        base score; weight is decided then, as height is, so it stays with the
        score the character was made with (#691: it used to pass
        effective_strength, and a character lost seven pounds on their
        thirty-sixth birthday). That weight falls on the creation side of the
        ruling is the driver's reading, for Spencer to confirm.
        """
        if self.strength is None or not self.weight_roll:
            return None
        if not is_demon_die_chain(self.weight_roll):
            return None  # nonsense is not a weight (issue #805)
        return calculate_final_weight_lbs(self.weight_roll, self.strength)

    # Effective attributes (after aging penalties)

    #
    # An attribute nobody has recorded yet (issue #571) is unknown, not zero:
    # every one of these answers None for it, the way `fatigue`, `body`,
    # `physical`, `mental`, `injury_thresholds` and `weight_lbs` already do.
    # They used to read `max(0, (self.strength or 0) - penalty)`, which priced
    # an unrecorded DEX at 0 and let the rules quote a DEX Combat Mod of -5 for
    # a character whose attributes nobody has entered (issue #630).
    def _effective_attribute(self, base: int | None, aging_penalty: int) -> int | None:
        """One attribute after its aging penalty, floored at 0, or None.

        Args:
            base: The recorded attribute, or None when it is not recorded.
            aging_penalty: The accumulated aging penalty for that attribute.

        Returns:
            The post-aging attribute, never below 0; None when ``base`` is None.
        """
        return None if base is None else max(0, base - aging_penalty)

    @property
    def effective_strength(self) -> int | None:
        """Strength after aging penalties (floor 0), or None if unrecorded."""
        return self._effective_attribute(self.strength, self.aging_str_penalty)

    @property
    def effective_dexterity(self) -> int | None:
        """Dexterity after aging penalties (floor 0), or None if unrecorded.

        Does NOT include armour's DEX penalty — this is the "raw" effective
        attribute used for fatigue, the physical stat, and experience-term
        simulation. For aim/dodge (which the rules say DO fold in armour),
        use :attr:`combat_dexterity` instead.
        """
        return self._effective_attribute(self.dexterity, self.aging_dex_penalty)

    @property
    def combat_dexterity(self) -> int | None:
        """Effective DEX with the equipped armour's DEX penalty folded in (floor 0).

        ``attack-rolls.md``: "Effective DEX already has your armour's DEX
        penalty baked in, so heavy armour drags down your aim as well as
        protecting you." ``armor_dex_penalty`` is stored as a
        negative-or-zero number, so adding it here reduces DEX.

        None when DEX is not recorded: armour cannot reduce a DEX that is
        unknown into a number the rules would price.
        """
        effective = self.effective_dexterity
        return None if effective is None else max(0, effective + self.armor_dex_penalty)

    @property
    def dex_modifier(self) -> int | None:
        """d20 to-hit bonus from combat DEX, or None if DEX is unrecorded."""
        from .combat import dex_modifier

        combat_dex = self.combat_dexterity
        return None if combat_dex is None else dex_modifier(combat_dex)

    @property
    def dodge_modifier(self) -> int | None:
        """What this character adds to an attacker's TN, or None if DEX is unrecorded."""
        from .combat import dodge_modifier

        combat_dex = self.combat_dexterity
        return None if combat_dex is None else dodge_modifier(combat_dex)

    @property
    def effective_intelligence(self) -> int | None:
        """Intelligence after aging penalties (floor 0), or None if unrecorded."""
        return self._effective_attribute(self.intelligence, self.aging_int_penalty)

    @property
    def effective_wisdom(self) -> int | None:
        """Wisdom after aging penalties (floor 0), or None if unrecorded."""
        return self._effective_attribute(self.wisdom, self.aging_wis_penalty)

    @property
    def starting_coin_known(self) -> bool:
        """False while a Rich character's starting coin is "Consult GM".

        wealth.md's Rich row reads "Consult GM"; the sheet batch (#749)
        stores that as a null ``starting_coin`` until a Game Master records
        one.
        """
        return self.starting_coin is not None

    @property
    def purse(self) -> Decimal:
        """The coin the character holds (issue #757).

        Starting coin (wealth.md) plus what prior experience paid ("Earn
        money based on skill level", prior-experience.md) plus every coin
        movement since: purchases and Game Master grants. A starting coin
        that is still "Consult GM" counts as none (coordinator's ruling on the
        #757 review): the character has only what prior experience paid and
        what a Game Master gives until one is recorded.
        """
        return (
            Decimal(self.starting_coin or 0)
            + Decimal(self.money_earned)
            + Decimal(self.coin_adjustment)
        )

    @property
    def effective_constitution(self) -> int | None:
        """Constitution after aging penalties (floor 0), or None if unrecorded."""
        return self._effective_attribute(self.constitution, self.aging_con_penalty)

    @property
    def physical(self):
        """Physical: half the sum of effective STR, DEX, and CON, rounded up.

        Reads effective (aged) scores: derived-pools.md says "Effective", and
        the ruling on #773 makes that the rule everywhere but creation-time
        checks. Armour's DEX penalty is not in it (derived-pools.md).
        """
        if self._any_missing("strength", "dexterity", "constitution"):
            return None
        return math.ceil(
            (
                self.effective_strength
                + self.effective_dexterity
                + self.effective_constitution
            )
            / 2
        )

    @property
    def mental(self):
        """Mental: half the sum of effective WIS, INT, and CHA, rounded up.

        Reads effective (aged) scores (ruling on #773). Charisma has no aging
        penalty, so its base value is its effective one.
        """
        if self._any_missing("wisdom", "intelligence", "charisma"):
            return None
        return math.ceil(
            (self.effective_wisdom + self.effective_intelligence + (self.charisma or 0))
            / 2
        )

    @property
    def location_literacy_modifier(self):
        """Penalty on the INT check to be literate, from this character's origin.

        City 0, Village 2, Rural 4 (see
        reference/content/public-rules/characters/location.md). The number is a
        penalty: added to the 3d6 roll, equivalently subtracted from INT.
        Returns None for "Consult GM", for which the rules publish no effects.

        Derived from the current location rather than stored, so a GM who edits
        the origin on the sheet gets the matching check without a second edit.
        """
        from .dice import LOCATION_LITERACY_MODIFIER

        return LOCATION_LITERACY_MODIFIER.get(self.location)

    @property
    def location_literacy_check(self):
        """The literacy check as the sheet prints it, e.g. "3d6 ≤ INT − 2".

        Written in the rulebook's form for a penalized check — the survival save
        prints as "3d6 ≤ CON − 5" — so the penalty reads in the direction it
        cuts rather than looking like a bonus.
        """
        modifier = self.location_literacy_modifier
        if modifier is None:
            return "Consult GM"
        if modifier == 0:
            return "3d6 ≤ INT"
        return f"3d6 ≤ INT − {modifier}"

    @property
    def literacy_threshold(self) -> int | None:
        """The highest 3d6 that passes this character's literacy check (#741).

        INT less the origin's penalty. Reads base INT: Spencer's ruling on
        #773 (2026-09-28) makes the creation-time checks, the literacy check
        among them, the one place aging does not reach. The check is "to be
        literate at all", made when the character is made; reading it against
        aged INT would let a character forget how to read.

        Returns:
            The threshold, or None when the origin is "Consult GM" (the rules
            publish no check for it) or INT is not recorded yet.
        """
        modifier = self.location_literacy_modifier
        if modifier is None or self.intelligence is None:
            return None
        return self.intelligence - modifier

    @property
    def is_literate(self) -> bool | None:
        """Whether the character can read: the roll against the check (#741),
        or at "Consult GM" the Game Master's ruling (#743).

        The ruling on #772 (coordinator's, under the standing rule; Spencer
        may overrule). location.md: "This is the check to be literate at all,
        and is separate from the Literacy skill." The glossary used to say of
        the Literacy skill "Without it, you cannot read at all", which the
        check contradicts. The check is the rule for *whether* a character
        reads; the skill is how well: a literate character with no Literacy
        skill reads untrained (the glossary's "Untrained, you roll with the
        attribute modifier alone"), and the skill adds its level to
        deciphering and composing. The glossary now says so, and a drift test
        holds the two pages to this reading.

        Read, never stored: the roll is the record, and this is that roll
        against today's threshold, so a corrected INT re-reads the same dice.
        The origin is different: a player may not change it once the roll is
        made, and a Game Master who changes it -- on the sheet or through the
        Games Master's plan, both of which go through
        ``services.change_field`` -- has the check rolled again against the
        new origin, or cleared for "Consult GM". So no roll is read against an
        origin it was not rolled for.

        "Consult GM" has no check, so there the answer is the Game Master's
        ruling, which is None until he makes one.

        Returns:
            None until the roll is made, and while the threshold is unknown;
            at "Consult GM", None until the Game Master rules.
        """
        if self.location_literacy_modifier is None:
            return self.literacy_ruling
        threshold = self.literacy_threshold
        if self.literacy_roll is None or threshold is None:
            return None
        return self.literacy_roll <= threshold

    @property
    def literacy_result(self) -> str:
        """The literacy row's outcome as the sheet prints it (#741).

        Beside the check itself ("3d6 ≤ INT − 2"), with the numbers:
        "Literate (rolled 9 ≤ 12 − 2)", "Illiterate (rolled 14 > 12 − 2)", or
        against a City check with no penalty "Literate (rolled 9 ≤ 12)".
        "Not rolled" while no roll is stored, except "Roll cleared; the Game
        Master decides" at a "Consult GM" origin once literacy was rolled, and
        "Rolled 9" alone when INT is not recorded. At "Consult GM" a Game
        Master's ruling reads with where it came from, "Literate (Game
        Master's ruling)" (#743). The one rendering: the editor's repaint sets
        this text as the server sends it.
        """
        if self.location_literacy_modifier is None and self.literacy_ruling is not None:
            outcome = "Literate" if self.literacy_ruling else "Illiterate"
            return f"{outcome} (Game Master's ruling)"
        if self.literacy_roll is None:
            # Rolled once, then cleared by a Game Master's move to "Consult
            # GM", which publishes no check (#741). Anywhere else a missing
            # roll reads as not rolled, though the flag still holds the lock.
            if self.literacy_rolled and self.location_literacy_modifier is None:
                return "Roll cleared; the Game Master decides"
            return "Not rolled"
        modifier = self.location_literacy_modifier
        if self.is_literate is None or modifier is None:
            return f"Rolled {self.literacy_roll}"
        against = (
            f"{self.intelligence} − {modifier}" if modifier else f"{self.intelligence}"
        )
        if self.is_literate:
            return f"Literate (rolled {self.literacy_roll} ≤ {against})"
        return f"Illiterate (rolled {self.literacy_roll} > {against})"

    @property
    def literacy_summary(self) -> str:
        """The check and its outcome on one line, for the export and the Games
        Master's sheet (#741), e.g. "3d6 ≤ INT − 2, Literate (rolled 9 ≤ 12 − 2)"."""
        return f"{self.location_literacy_check}, {self.literacy_result}"

    @property
    def background_summary(self) -> str:
        """Origin, provenance and wealth on one line (issue #754).

        Each part nobody rolled or set is marked "(not rolled)", read off the
        ``*_recorded`` flags: "origin Village (not rolled); provenance Commoner
        (Laborer) (not rolled); wealth Moderate, 100 starting coin (not
        rolled)". The Games Master's Characters pane, its state block and the
        note a new session character is introduced with all read this.
        """
        coin = (
            "Consult GM for starting coin"
            if self.starting_coin is None
            else f"{self.starting_coin} starting coin"
        )
        # wealth.md's Rich row is "Consult GM", and only a Game Master sets
        # the coin (FIELD_RULES), so a Rich character's figure is his (#801).
        if self.wealth_level == "Rich" and self.starting_coin is not None:
            coin = f"{coin} (Game Master's figure)"
        origin = f"origin {self.location}"
        if self.origin_ruling:
            origin = f"{origin} ({self.origin_ruling}, the Game Master's ruling)"
        parts = (
            (origin, self.location_recorded),
            (
                f"provenance {self.get_provenance_class_display()} "
                f"({self.provenance_detail})",
                self.provenance_recorded,
            ),
            (f"wealth {self.wealth_level}, {coin}", self.wealth_recorded),
        )
        return "; ".join(
            part if recorded else f"{part} (not rolled)" for part, recorded in parts
        )

    @cached_property
    def total_allocated_skill_points(self):
        """Sum of all allocated skill points."""
        if not self.skills:
            return 0
        return sum(self.skills.values())

    @property
    def level(self):
        """Experience level (1-10) derived from total XP."""
        from .skills import get_experience_level

        return get_experience_level(self.total_xp)

    @property
    def highest_skill_level(self):
        """Highest skill level among all skills. Uncapped (issue #146).

        The level is read per skill rather than per point total, because Arcane
        buys levels at half the rate (issue #154).
        """
        from .skills import get_skill_level

        if not self.skills:
            return 0
        return max(get_skill_level(pts, name) for name, pts in self.skills.items())

    @property
    def maker_skill_level(self):
        """The Maker Skill's level, for the Craft track's income.

        Whatever craft it was taken for: "maker skill (smith)" is the Maker
        Skill (issue #808).
        """
        from .skills import glossary_skill_level

        return glossary_skill_level(self.skills, "maker skill")

    @property
    def status(self):
        """Return character status string for display."""
        if self.is_deleted and not self.is_alive:
            return "Deleted & Deceased"
        if self.is_deleted:
            return "Deleted"
        if not self.is_alive:
            return "Deceased"
        if not self.is_complete:
            return "Incomplete"
        if self.is_played:
            return "Played"
        return "Ready"

    @property
    def experience_years(self):
        """Years of prior experience this character has actually played.

        Counted from the log rather than stored, so it is right for legacy
        4-year term entries and for the variable-length runs issue #221
        introduced alike -- including a run cut short by death. Entries with a
        malformed ``years`` value contribute nothing rather than raising.
        """
        total = 0
        for entry in self.experience_log or []:
            if not isinstance(entry, dict):
                continue
            years = entry.get("years")
            if isinstance(years, list):
                total += len(years)
        return total

    @property
    def fatal_survival_check(self):
        """The survival check that killed this character, or ``None``.

        Only an experience-term death has one: the last term of the
        ``experience_log`` ended with ``alive: False``, and its final year's
        ``survival_check`` is the fatal roll (issue #175). A DM-marked death,
        a living character, or an empty log all return ``None``.
        """
        if self.is_alive or not self.experience_log:
            return None
        last_term = self.experience_log[-1]
        if last_term.get("alive", True):
            return None
        years = last_term.get("years") or []
        if not years:
            return None
        return years[-1].get("survival_check")

    @property
    def status_css_class(self):
        """Return CSS class for status badge."""
        if self.is_deleted:
            return "status-deleted"
        if not self.is_alive:
            return "status-dead"
        if not self.is_complete:
            return "incomplete-mark"
        if self.is_played:
            return "status-played"
        return "status-ready"

    # Equipment and encumbrance properties
    @staticmethod
    def _catalog_entry(item: dict) -> dict | None:
        """The catalog record behind a stored equipment entry, if it has one.

        A catalog entry stores only ``item_id``/``quantity``/``in_pack``/
        ``notes`` (see ``equipment_service.build_catalog_equipment_item``);
        its name, weight and category live in the catalog. A custom item
        carries its own and has no record here.
        """
        item_id = item.get("item_id", "")
        if not item_id or item_id == "custom":
            return None
        return get_item(str(item_id))

    def _get_item_weight(self, item):
        """Get weight for an item (catalog or custom)."""
        catalog_item = self._catalog_entry(item)
        if catalog_item:
            catalog_weight = catalog_item.get("weight", 0)
            if catalog_weight > 0:
                return catalog_weight
        # Custom item - use stored weight
        return item.get("weight", 0)

    @property
    def equipment_rows(self) -> list[EquipmentRow]:
        """This character's equipment as display rows, resolved once (issue #633).

        The one place a stored equipment entry becomes something readable.
        The Markdown and PDF exports used to read ``name``/``weight``/
        ``category`` straight off the stored entry, where a catalog item does
        not carry them, and printed every one of them as
        ``Unknown | 0 lbs | -`` -- contradicting the Total Equipment Weight
        two sections above, which was resolved through the catalog all along.
        """
        rows = []
        for item in self.equipment or []:
            catalog_item = self._catalog_entry(item) or {}
            try:
                quantity = int(item.get("quantity", 1))
            except (TypeError, ValueError):
                quantity = 1
            unit_weight = self._get_item_weight(item)
            rows.append(
                EquipmentRow(
                    name=(
                        catalog_item.get("name")
                        or item.get("name")
                        or str(item.get("item_id", ""))
                    ),
                    quantity=quantity,
                    unit_weight=unit_weight,
                    total_weight=unit_weight * quantity,
                    in_pack=bool(item.get("in_pack", False)),
                    equipped=bool(item.get("equipped", False)),
                    category=catalog_item.get("category") or item.get("category") or "",
                    notes=str(item.get("notes", "")),
                )
            )
        return rows

    @cached_property
    def total_equipment_weight(self):
        """Total weight of all equipment."""
        if not self.equipment:
            return 0.0
        total = 0.0
        for item in self.equipment:
            item_weight = self._get_item_weight(item)
            quantity = item.get("quantity", 1)
            total += item_weight * quantity
        return round(total, 1)

    @cached_property
    def pack_weight(self):
        """Weight of items in pack only."""
        if not self.equipment:
            return 0.0
        total = 0.0
        for item in self.equipment:
            if item.get("in_pack", False):
                item_weight = self._get_item_weight(item)
                quantity = item.get("quantity", 1)
                total += item_weight * quantity
        return round(total, 1)

    @cached_property
    def carried_weight(self):
        """Weight currently carried (excludes dropped pack items)."""
        if self.pack_dropped:
            return round(self.total_equipment_weight - self.pack_weight, 1)
        return self.total_equipment_weight

    @property
    def encumbrance_level(self) -> str | None:
        """Encumbrance from carried weight and effective STR, or None (issue #630).

        Effective (aged) STR, per the ruling on #773.

        What a load weighs on a character is priced against their STR, so an
        unrecorded STR leaves the level unknown rather than "unencumbered".
        """
        strength = self.effective_strength
        if strength is None:
            return None
        return calculate_encumbrance_level(self.carried_weight, strength)

    @property
    def movement_penalty(self):
        """Movement penalty from encumbrance (0, -1, -2, or -4)."""
        return get_movement_penalty(self.encumbrance_level)

    @property
    def movement_restrictions(self):
        """List of restricted movement modes from encumbrance and worn armor.

        The most restrictive combination applies: a mode is restricted if
        either encumbrance or an equipped armor piece forbids it.
        """
        return combine_movement_restrictions(
            get_movement_restrictions(self.encumbrance_level),
            get_armor_movement_restrictions(self.equipment),
        )

    @property
    def movement_restriction_label(self):
        """Human-readable movement restriction, e.g. 'Cannot Run or Sprint (Chainmail)'.

        Empty string when nothing is restricted.
        """
        restrictions = self.movement_restrictions
        if not restrictions:
            return ""
        label = describe_movement_restrictions(restrictions)
        armor_names = get_restrictive_armor_names(self.equipment)
        if armor_names:
            label += f" ({', '.join(armor_names)})"
        return label

    @property
    def movement_modifier(self) -> int | None:
        """Movement Modifier: CON modifier + STR modifier + DEX modifier.

        Uses effective (post-aging) attributes, per the ruling on #773, and the
        same attribute-modifier table as everything else
        (``skills.calculate_attribute_modifier``). Armour's DEX penalty is not
        in it: movement.md names only the three modifiers, and armour limits
        movement through the modes it forbids (:attr:`movement_restrictions`).
        This is the base modifier the gait formulas add on top of -- it is
        distinct from :attr:`movement_penalty`, the further reduction from
        carrying too much weight.

        None when any of CON, STR or DEX is not recorded yet (issue #630):
        a movement rate derived from attributes nobody has entered would be
        an invented number, not a rules number.
        """
        from .skills import calculate_attribute_modifier

        constitution = self.effective_constitution
        strength = self.effective_strength
        dexterity = self.effective_dexterity
        if constitution is None or strength is None or dexterity is None:
            return None
        return _movement_modifier(
            calculate_attribute_modifier(constitution),
            calculate_attribute_modifier(strength),
            calculate_attribute_modifier(dexterity),
        )

    @property
    def gait_distances(self):
        """Map gait ("sprint"/"run"/"jog"/"walk") to hex distance, or None.

        None means the gait is unusable: forbidden by encumbrance or worn
        armor (:attr:`movement_restrictions`), or -- for Walk under
        Overloaded -- replaced by the "Walk only, 1 hex max" cap rather than
        the normal formula.

        Every gait is None when :attr:`movement_modifier` is (issue #630) --
        the attributes the formulas need are not recorded, so no gait has a
        distance to state.
        """
        modifier = self.movement_modifier
        if modifier is None:
            return {gait: None for gait in GAIT_ORDER}
        walk_limit = None
        if self.encumbrance_level == "overloaded":
            walk_limit = get_walk_limit(self.encumbrance_level)
        return compute_gaits(
            modifier,
            self.movement_penalty,
            self.movement_restrictions,
            walk_limit=walk_limit,
        )

    @property
    def sprint_distance(self):
        """Sprint gait distance in hexes, or None if sprinting is restricted."""
        return self.gait_distances["sprint"]

    @property
    def run_distance(self):
        """Run gait distance in hexes, or None if running is restricted."""
        return self.gait_distances["run"]

    @property
    def jog_distance(self):
        """Jog gait distance in hexes, or None if jogging is restricted."""
        return self.gait_distances["jog"]

    @property
    def walk_distance(self):
        """Walk gait distance in hexes (capped at 1 when Overloaded)."""
        return self.gait_distances["walk"]

    @property
    def gait_readout(self):
        """Gaits as a list of sheet-ready dicts, fastest to slowest.

        Each entry is ``{"key", "label", "distance", "na"}`` -- the template
        loops over this rather than calling the four distance properties
        individually and re-deriving the restricted/label state itself
        (following the server-computed-label convention used by
        :attr:`movement_restriction_label`, issue #128).
        """
        distances = self.gait_distances
        return [
            {
                "key": gait,
                "label": gait.title(),
                "distance": distances[gait],
                "na": distances[gait] is None,
            }
            for gait in GAIT_ORDER
        ]

    def _item_category(self, item: dict) -> str:
        """An equipment entry's category: the catalog's, or a custom item's own."""
        return entry_category(item)

    def _held_index(self, category: str) -> int | None:
        """The entry of ``category`` in hand: the one chosen, else the first carried.

        Only an item carried outside the pack can be in hand -- a packed one is
        slung away, and a dropped pack is not with the character at all. Among
        those, the one whose ``equipped`` flag the player set is in hand;
        without a choice, the first carried. READY WEAPON in action-options.md
        ("Re-sling current, ready new weapon/shield") is the rule: one weapon
        and one shield at a time (issues #804, #846).
        """
        carried = self._carried_indices(category)
        for index in carried:
            if (self.equipment or [])[index].get("equipped"):
                return index
        return carried[0] if carried else None

    def _carried_indices(self, category: str) -> list[int]:
        """Indices of the entries of ``category`` carried outside the pack."""
        return [
            index
            for index, item in enumerate(self.equipment or [])
            if self._item_category(item) == category and not item.get("in_pack")
        ]

    def _is_two_handed(self, index: int) -> bool:
        """Whether the entry at ``index`` takes both hands (the catalog's flag)."""
        item = (self.equipment or [])[index]
        return bool((get_item(item.get("item_id", "")) or {}).get("two_handed"))

    @property
    def weapon_in_hand_index(self) -> int | None:
        """The equipment index of the weapon in hand, or None (issue #846).

        attack-rolls.md builds the to-hit bonus for one weapon, the one
        swung; the sheet's weapon tiles read this one, the Attack panel
        offers it first, and the battle board seats it.

        With none readied, a character carrying a shield holds the first
        one-handed weapon the battle board can seat, so a bow listed first
        does not take the shield's hand; without a shield, or with no such
        weapon, the first weapon carried (coordinator's ruling on the review).
        A custom weapon is not one the board can seat (it seats catalog
        weapons with rollable damage, :func:`board_can_seat_weapon`), so the
        default never names a weapon the board would pass over.
        """
        chosen = self._held_index("weapons")
        if chosen is None or (self.equipment or [])[chosen].get("equipped"):
            return chosen
        if self._carried_indices("shield"):
            for index in self._carried_indices("weapons"):
                item = (self.equipment or [])[index]
                if not self._is_two_handed(index) and board_can_seat_weapon(item):
                    return index
        return chosen

    @property
    def weapon_in_hand_is_two_handed(self) -> bool:
        """Whether the weapon in hand takes both hands (the catalog's flag)."""
        index = self.weapon_in_hand_index
        return index is not None and self._is_two_handed(index)

    @property
    def shield_in_use_index(self) -> int | None:
        """The equipment index of the shield in use, or None (issue #804).

        One shield at a time, carried outside the pack, and none beside a
        two-handed weapon. attack-rolls.md's "Shield bonus" is a shield in
        use: every shield in the inventory used to count, stacked, even in a
        dropped pack.
        """
        if self.weapon_in_hand_is_two_handed:
            return None
        return self._held_index("shield")

    #: Why a carried shield is not the one in use, when the hand is the reason.
    SHIELD_BESIDE_TWO_HANDED = (
        "Not in use: the weapon in hand takes both hands, so no shield counts"
    )

    @property
    def shield_idle_reason(self) -> str:
        """Why no carried shield counts, or "" (issue #804).

        Readying a two-handed weapon puts the shield out of use without a
        refusal, so the shield's row says why its stops went away.
        """
        if self.weapon_in_hand_is_two_handed and self._carried_indices("shield"):
            return self.SHIELD_BESIDE_TWO_HANDED
        return ""

    def _sum_armor_field(
        self, base_field: str, mod_field: str, *, include_modifiers: bool = True
    ) -> int:
        """Sum base+mod for a field across equipped armor and the shield in use.

        Armour counts only when worn, equipped and out of the pack
        (:func:`characters.equipment.is_worn_armour`, issue #869); a shield
        only when it is the one in use (:attr:`shield_in_use_index`, issue #804). Custom items (no
        catalog entry) use their stored base value.

        Args:
            base_field: The item's own number, e.g. ``"stops"``.
            mod_field: The per-item modifier a Game Master may grant, e.g.
                ``"stops_mod"`` (issue #752).
            include_modifiers: False sums the base numbers alone, which is
                what the battle board seats (see :attr:`armor_stops_without_modifiers`).
        """
        if not self.equipment:
            return 0
        shield_index = self.shield_in_use_index
        total = 0
        for index, item in enumerate(self.equipment):
            modifier = item.get(mod_field, 0) if include_modifiers else 0
            item_data = get_item(item.get("item_id", ""))
            category = item_data.get("category") if item_data else item.get("category")
            if category == "armor" and not is_worn_armour(item):
                continue
            if category == "shield" and index != shield_index:
                continue
            if category not in ("armor", "shield"):
                continue
            # A custom item carries its own numbers.
            base = (item_data or item).get(base_field, 0)
            total += base + modifier
        return total

    @property
    def armor_stops_without_modifiers(self) -> int:
        """Stops from equipped armour and shields, without item modifiers.

        The battle board reads the items' own numbers and no modifier at all
        (issue #752): the engine has no place for a weapon's to-hit or damage
        modifier, so reading the armour's and not the weapon's made one
        character fight half-modified. The rules give equipment no modifiers;
        they are a Game Master's grant, which the sheet shows and applies.
        "The items' own numbers" include a custom item's, which its owner
        typed (#845).
        """
        return self._sum_armor_field("stops", "stops_mod", include_modifiers=False)

    @property
    def combat_dexterity_without_modifiers(self) -> int | None:
        """:attr:`combat_dexterity` with the armour's own DEX penalty only.

        The battle board's DEX, for the reason
        :attr:`armor_stops_without_modifiers` gives (issue #752).
        """
        effective = self.effective_dexterity
        if effective is None:
            return None
        penalty = self._sum_armor_field(
            "dex_penalty", "dex_mod", include_modifiers=False
        )
        return max(0, effective + penalty)

    @property
    def armor_dex_penalty(self):
        """Total DEX penalty from equipped armor and shields."""
        return self._sum_armor_field("dex_penalty", "dex_mod")

    @property
    def armor_stops(self):
        """Total stops from equipped armor and shields."""
        return self._sum_armor_field("stops", "stops_mod")

    def _sum_weapon_mod(self, mod_field: str) -> int:
        """A modifier of the weapon in hand, or 0 with none (issue #846).

        attack-rolls.md builds a swing's bonus from the one weapon swung, so
        the tiles read the weapon in hand (:attr:`weapon_in_hand_index`).
        They used to add up every weapon carried, a figure no swing gets.
        """
        index = self.weapon_in_hand_index
        if index is None:
            return 0
        return int((self.equipment or [])[index].get(mod_field, 0) or 0)

    @property
    def weapon_to_hit_mod(self):
        """The to-hit modifier of the weapon in hand."""
        return self._sum_weapon_mod("to_hit_mod")

    @property
    def weapon_damage_mod(self):
        """The damage modifier of the weapon in hand."""
        return self._sum_weapon_mod("damage_mod")

    @property
    def weapon_critical_mod(self):
        """The critical modifier of the weapon in hand."""
        return self._sum_weapon_mod("critical_mod")

    @property
    def encumbrance_thresholds(self):
        """Return weight thresholds for each encumbrance level, or None.

        None when STR is not recorded (issue #630): every threshold is a
        multiple of STR, so there is nothing to state. Effective (aged) STR,
        per the ruling on #773.
        """
        str_val = self.effective_strength
        if str_val is None:
            return None
        return {
            "unencumbered": str_val,
            "light": round(str_val * ENCUMBRANCE_LEVELS["light"]["max_multiplier"], 1),
            "medium": round(
                str_val * ENCUMBRANCE_LEVELS["medium"]["max_multiplier"], 1
            ),
            "heavy": round(str_val * ENCUMBRANCE_LEVELS["heavy"]["max_multiplier"], 1),
        }


# Editable character fields - all fields except user, created_at, and dice_log.
# Lives here (not views.py) because character_import shares it too, and
# because it and clean_field_value() above are the single source of truth
# for what a single-field edit or an import may write (issue #89).
CHARACTER_FIELDS = {
    "name",
    # A player's people and the Game Master's Dragonborn secret (#810), and
    # his ruling at a "Consult GM" origin (#801).
    "people",
    "is_dragonborn",
    "origin_ruling",
    "strength",
    "dexterity",
    "intelligence",
    "wisdom",
    "constitution",
    "charisma",
    "appearance_roll",
    "height_roll",
    "weight_roll",
    "fatigue_roll",
    "provenance_class",
    "provenance_detail",
    "location",
    "wealth_level",
    "starting_coin",
    "total_xp",
}


def character_has_field(name: str) -> bool:
    """Whether ``Character`` has a model field called ``name``."""
    return any(field.name == name for field in Character._meta.local_fields)


#: The cells the sheetrules batch's fields bring (#801, #810): the Game
#: Master's ruling on a "Consult GM" origin, the character's people, and the
#: Game Master's secret that a character is Dragonborn. Named here, additive,
#: and joined to CHARACTER_FIELDS only where the model carries the field, so
#: this holds with and without that batch's migration 0022.
SHEET_RULES_CELL_FIELDS: tuple[str, ...] = ("origin_ruling", "people", "is_dragonborn")
CHARACTER_FIELDS |= {
    name for name in SHEET_RULES_CELL_FIELDS if character_has_field(name)
}
# Fields that must be integers, and their upper bounds. Each bound matches
# that field's actual model type rather than one flat number — attributes and
# fatigue_roll are PositiveSmallIntegerFields (32767, the DB's smallint
# limit), while starting_coin and total_xp are 32-bit PositiveIntegerFields
# (2147483647, Postgres's int4 limit). Applying the smallint cap to the
# latter two used to make manual XP correction impossible past level 6-7
# (512,000 XP at level 10) once add-term pushed total_xp over 32,767 (#136).
# This is the single source of truth for both the single-field edit endpoint
# and character_import's validation.
MAX_SMALLINT_FIELD_VALUE = 32767
MAX_INT_FIELD_VALUE = 2147483647
CHARACTER_INT_FIELD_MAX = {
    "strength": MAX_SMALLINT_FIELD_VALUE,
    "dexterity": MAX_SMALLINT_FIELD_VALUE,
    "intelligence": MAX_SMALLINT_FIELD_VALUE,
    "wisdom": MAX_SMALLINT_FIELD_VALUE,
    "constitution": MAX_SMALLINT_FIELD_VALUE,
    "charisma": MAX_SMALLINT_FIELD_VALUE,
    # The 2d6 the roll is made of, not the column's ceiling: a stated 30000
    # used to reach Fatigue, Body and the injury thresholds intact (#653).
    "fatigue_roll": FATIGUE_ROLL_MAXIMUM,
    "starting_coin": MAX_INT_FIELD_VALUE,
    "total_xp": MAX_INT_FIELD_VALUE,
    # The wounds carried (issue #751). Not in CHARACTER_FIELDS, so the
    # single-field editor never takes them; the bounds are for an import,
    # which carries them so a round trip keeps a hurt character hurt.
    "fatigue_damage": MAX_SMALLINT_FIELD_VALUE,
    "body_damage": MAX_SMALLINT_FIELD_VALUE,
    "mana_spent": MAX_SMALLINT_FIELD_VALUE,
}
CHARACTER_INT_FIELDS = set(CHARACTER_INT_FIELD_MAX)
# Integer fields whose empty value means something: a Rich character's coin is
# "Consult GM" until a Game Master sets it (issue #749), so it exports as null
# and must import as null.
CHARACTER_NULLABLE_INT_FIELDS = {"starting_coin"}
# Minimum values for CHARACTER_INT_FIELDS, keyed per-field like
# CHARACTER_INT_FIELD_MAX above -- not every field's floor is the same. The
# six core attributes carry an explicit MinValueValidator(1) in
# ATTRIBUTE_VALIDATORS above: 0 STR isn't a valid character. Every other int
# field here is a plain Positive(Small)IntegerField with no such validator,
# so 0 is genuinely storable and must round-trip. This used to be a flat
# "everything but starting_coin needs >=1" rule, which rejected a
# freshly-created character's total_xp=0 on its own export->import round trip
# (#164) even though 0 is the field's default.
MIN_ATTRIBUTE_VALUE = 1
CHARACTER_INT_FIELD_MIN = {
    "strength": MIN_ATTRIBUTE_VALUE,
    "dexterity": MIN_ATTRIBUTE_VALUE,
    "intelligence": MIN_ATTRIBUTE_VALUE,
    "wisdom": MIN_ATTRIBUTE_VALUE,
    "constitution": MIN_ATTRIBUTE_VALUE,
    "charisma": MIN_ATTRIBUTE_VALUE,
    "fatigue_roll": FATIGUE_ROLL_MINIMUM,
    "starting_coin": 0,
    "total_xp": 0,
    "fatigue_damage": 0,
    "body_damage": 0,
    "mana_spent": 0,
}
# Max lengths for string fields (matching model constraints)
CHARACTER_STRING_MAX_LENGTHS = {
    "name": 100,
    "appearance_roll": 50,
    "height_roll": 50,
    "weight_roll": 50,
    "provenance_class": 20,
    "provenance_detail": 50,
    "location": 20,
    "wealth_level": 20,
    "origin_ruling": 200,
    "people": 20,
}
# Fields restricted to the model's declared choices. Values pulled from the
# model itself (rather than duplicated here) so the two can't drift.
CHARACTER_CHOICE_FIELDS = {
    "provenance_class": dict(Character.PROVENANCE_CLASSES),
    "location": dict(Character.LOCATION_CHOICES),
    "wealth_level": dict(Character.WEALTH_CHOICES),
    "people": dict(Character.PEOPLES),
    "is_dragonborn": {"False": "No", "True": "Yes"},
}
# Yes-or-no fields, coerced from the strings and booleans a request or an
# import carries (#810).
CHARACTER_BOOLEAN_FIELDS = {"is_dragonborn"}


def characters_visible_to(user):
    """The Character rows ``user`` may READ: their own, any public one, or
    all of them for a DM/admin.

    The one statement of the app's character-visibility rule (the editor
    view, the character list, the exports, and the battle setup's
    saved-character picker all ask through it). Soft-deleted rows are out
    for everyone — the deleted list and restore endpoints are the
    deliberate exceptions and query for themselves.

    Visibility is a READ scope only. A public character (issue #273) is
    readable by every registered user, but writing to one still goes
    through ``common.helpers._can_edit_object`` (owner or DM/admin), which
    this widening never touches.

    "Registered" is now load-bearing rather than incidental: since issue
    #305 an unauthenticated visitor can reach the arena, so this function
    can be asked about ``AnonymousUser``, and the answer is nothing at all.
    Public means public to accounts, not to the internet — and the naive
    fall-through would have been worse than wrong, since ``Q(user=user)``
    with an anonymous user is not a query this rule ever meant to run.
    """
    if not getattr(user, "is_authenticated", False):
        return Character.objects.none()
    queryset = Character.objects.filter(is_deleted=False)
    if not user.can_manage_users:
        queryset = queryset.filter(models.Q(user=user) | models.Q(is_public=True))
    return queryset
