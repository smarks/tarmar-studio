"""The one place a character changes (issue #571).

Every change to a saved character goes through here: the character editor's
endpoints, the importer and the Games Master. Each call locks the row
(``SELECT ... FOR UPDATE``), re-reads it, applies the change through the
rules, saves it and appends a line to its change history, all in one
transaction.

The rules themselves are the ``apply_*`` functions. They change a
``Character`` instance in memory and never save, so a caller holding a
character that is not a row yet (the Games Master's JSON draft, until it is
retired) prices a change by the same arithmetic as the editor.

Who may make a change is the caller's to decide and pass in:

* ``authorize`` is called with the locked row before anything changes, and
  raises :class:`~django.core.exceptions.PermissionDenied` to refuse;
* ``is_game_master`` lifts the limits a player is held to (issue #404);
* ``actor`` is who made the change, for the history and the "played" flag.
"""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from django.db import transaction
from django.db.models import Q

from accounts.models import User

from .constants import (
    FATIGUE_ROLL_MAXIMUM,
    FATIGUE_ROLL_MINIMUM,
    MAX_ATTRIBUTE_VALUE,
    MAX_EQUIPMENT_QUANTITY,
    POINT_BUY_MAX_ATTRIBUTE,
    POINT_BUY_MIN_ATTRIBUTE,
    POINT_BUY_TOTAL,
)
from .dice import (
    LOCATION_SKILL_CHOICES,
    LOCATION_SKILL_POINTS,
    Roller,
    literacy_check_text,
    location_effects,
    roll_3d6_logged,
    roll_d6_logged,
    roll_expression,
    roll_location_logged,
    roll_provenance_logged,
    roll_wealth_logged,
    starting_coin_for_wealth,
    wealth_gives_coin,
)
from .equipment import EQUIPMENT_CATALOG, get_item_name
from .models import (
    CHARACTER_FIELDS,
    CHARACTER_INT_FIELD_MAX,
    Character,
    CurrentCondition,
)
from .purse import (
    PurseError,
    catalog_price,
    charge_for_catalog_item,
    hold_purchase_to_wealth,
)
from .skills import (
    ALL_TRACKS,
    MAX_EXPERIENCE_YEARS_PER_RUN,
    MAX_SKILL_POINTS,
    calculate_skill_points_from_xp,
    calculate_skill_points_in_range,
    format_skill_level,
    get_random_track,
    glossary_skill,
    normalize_skill_name,
    simulate_experience_years,
    specialised_skill_for_granted,
    split_specialised_skill,
)
from .weapon_skill import adds_to_attacks

#: The six core attributes, in sheet order.
ATTRIBUTE_NAMES: tuple[str, ...] = (
    "strength",
    "dexterity",
    "intelligence",
    "wisdom",
    "constitution",
    "charisma",
)

#: The refusal the Games Master can put right itself, in the same turn, by
#: recording the attributes it was given before the years (issue #457).
REFUSAL_ATTRIBUTES_NOT_RECORDED = "attributes_not_recorded"

#: Who a history line names when no user made the change.
UNATTRIBUTED = "system"


# ---------------------------------------------------------------------------
# Who may set each field (issue #748). One table, read by the field-update
# service, the sheet's cell tags and the equipment endpoints, so the page and
# the server cannot disagree about what a player may type.
# ---------------------------------------------------------------------------


class FieldAuthor(StrEnum):
    """Who may set a character's value as an edit.

    The standing rule (#404, Spencer's rulings of 2026-09-28): players are held
    to the rules exactly, and a Game Master (``can_manage_users``) may override.

    * ``PLAYER``: the rules let the player choose it (within their limits);
      a Game Master may too.
    * ``GAME_MASTER``: the dice or play decide it. A player reaches it only
      through a roll control or a rules service (years of experience, the
      skill workbench); only a Game Master sets it by hand.
    * ``NOBODY``: bookkeeping or derived from other values; no one sets it
      as an edit.
    """

    PLAYER = "player"
    GAME_MASTER = "game_master"
    NOBODY = "nobody"


@dataclass(frozen=True)
class FieldRule:
    """Who may set one value, and what a refused player is told.

    Attributes:
        author: Who may set it.
        reason: For a Game-Master-only value, the persistent note the sheet
            prints beside it for a player, and the refusal a player's
            field update gets. Empty for the other two kinds.
    """

    author: FieldAuthor
    reason: str = ""


def _player() -> FieldRule:
    return FieldRule(FieldAuthor.PLAYER)


def _game_master(reason: str) -> FieldRule:
    return FieldRule(FieldAuthor.GAME_MASTER, reason)


def _nobody() -> FieldRule:
    return FieldRule(FieldAuthor.NOBODY)


#: Why a player's literacy ruling is refused (issue #743), wherever it is
#: asked for: the field table's reason and the service's refusal.
LITERACY_RULING_GAME_MASTER_ONLY = (
    "Only a Game Master rules on whether a character can read; a player's "
    "literacy is rolled"
)

#: Why a player's origin ruling is refused (issue #801).
ORIGIN_RULING_GAME_MASTER_ONLY = "Only a Game Master rules what a Consult GM origin is"

#: Why a player cannot set the Dragonborn secret (issue #810).
DRAGONBORN_GAME_MASTER_ONLY = "Only a Game Master can set this."

#: Fields whose value a player must never read, in the history either (#810).
SECRET_FIELDS: frozenset[str] = frozenset({"is_dragonborn"})
#: What the history records for a change to one of them.
SECRET_HISTORY_FIELD = "game_master_note"
SECRET_HISTORY_NOTE = "a Game Master's note"

#: Why a player cannot set current Fatigue and Body (issue #751).
POOLS_GAME_MASTER_ONLY = (
    "Only a Game Master can set current Fatigue, Body and Mana; a fight on the "
    "board records them on its own"
)

_ATTRIBUTE_REASON = (
    "Rolled (3d6) or bought (65 points); only a Game Master can change a "
    "recorded score."
)

#: Every stored field of ``Character`` and who may set it (issue #748). A test
#: walks the model and holds this table to it, so a new field has to be decided
#: here before the suite passes.
FIELD_RULES: Mapping[str, FieldRule] = MappingProxyType(
    {
        # Identity and bookkeeping.
        "id": _nobody(),
        "user": _nobody(),
        "created_at": _nobody(),
        "is_played": _nobody(),
        "history": _nobody(),
        "combat_log": _nobody(),
        "dice_log": _nobody(),
        # What a player's import dropped (#744): written by the import,
        # cleared by the dismiss control.
        "import_notice": _nobody(),
        # Set by the delete and restore endpoints, not as an edit of the sheet.
        "is_deleted": _nobody(),
        "deleted_at": _nobody(),
        # The player's own choices.
        "name": _player(),
        "is_public": _player(),
        # attributes.md: "Height: Choose or roll 1d6"; "Base weight is
        # player's choice between 8 and 13 stones (or roll 1d6)".
        "height_roll": _player(),
        "weight_roll": _player(),
        # location.md: "Roll percentile dice or choose". Fixed for a player
        # once literacy is rolled (#741, can_change_origin).
        "location": _player(),
        # Where free points go, through the skill workbench (allocation holds
        # a player to the points earned and to the glossary's skills, #862).
        "skills": _player(),
        # Picking catalog items, packing them, dropping the pack. An item's
        # modifiers are the Game Master's (ITEM_MODIFIER_RULE, issue #752).
        "equipment": _player(),
        "pack_dropped": _player(),
        # Rolled or awarded.
        "strength": _game_master(_ATTRIBUTE_REASON),
        "dexterity": _game_master(_ATTRIBUTE_REASON),
        "intelligence": _game_master(_ATTRIBUTE_REASON),
        "wisdom": _game_master(_ATTRIBUTE_REASON),
        "constitution": _game_master(_ATTRIBUTE_REASON),
        "charisma": _game_master(_ATTRIBUTE_REASON),
        "appearance_roll": _game_master(
            "Rolled (1d6); only a Game Master can change it or qualify a 1 or a 6."
        ),
        "fatigue_roll": _game_master(
            "Rolled (2d6) once; only a Game Master can change it."
        ),
        "literacy_roll": _game_master(
            "Rolled (3d6) once; only a Game Master can have it rolled again."
        ),
        # The Game Master's word at "Consult GM", which has no check (#743).
        "literacy_ruling": _game_master(LITERACY_RULING_GAME_MASTER_ONLY),
        # What a "Consult GM" origin is (#801): the Game Master's word again.
        "origin_ruling": _game_master(ORIGIN_RULING_GAME_MASTER_ONLY),
        # A people is the player's choice; nobody rolls one (#810).
        "people": _player(),
        # The Game Master's secret (#810), never the player's to set or see.
        "is_dragonborn": _game_master(DRAGONBORN_GAME_MASTER_ONLY),
        # Written by the first battle seat or played turn, never by hand (#803).
        "entered_play_at": _nobody(),
        # What a lowered XP could not take back; the next XP pays it (#769).
        "skill_points_owed": _nobody(),
        "provenance_class": _game_master(
            "Rolled on the provenance table; only a Game Master can change it."
        ),
        "provenance_detail": _game_master(
            "Rolled on the provenance table; only a Game Master can change it."
        ),
        "wealth_level": _game_master(
            "Rolled on the wealth table; only a Game Master can change it."
        ),
        "starting_coin": _game_master(
            "Set once by the wealth roll; only a Game Master can change it."
        ),
        "total_xp": _game_master(
            "Earned in play or prior experience; only a Game Master can award it."
        ),
        "age": _game_master(
            "Moves with years of prior experience; only a Game Master can change it."
        ),
        "free_skill_points": _game_master(
            "Earned with XP; only a Game Master can grant more."
        ),
        "is_alive": _game_master("Only a Game Master can mark a death or revive."),
        "death_cause": _game_master("Only a Game Master can mark a death or revive."),
        # What fights left the character (issue #751, and spent mana by the
        # coordinator's ruling): written by a fight that writes to records
        # (record_fight) and set by a Game Master (set_current_pools); not a
        # sheet field edit for anyone, so they are not in CHARACTER_FIELDS.
        "fatigue_damage": _game_master(POOLS_GAME_MASTER_ONLY),
        "body_damage": _game_master(POOLS_GAME_MASTER_ONLY),
        "mana_spent": _game_master(POOLS_GAME_MASTER_ONLY),
        # Written by the rules as a consequence of something else.
        "provenance_recorded": _nobody(),
        "location_recorded": _nobody(),
        "origin_skill": _nobody(),
        "wealth_recorded": _nobody(),
        "literacy_rolled": _nobody(),
        # The Mana Pool's maximum: the Magic track's Spell Points, and what a
        # Game Master grants through characters.magic.grant_mana (#774).
        "free_spell_points": _game_master(
            "Spell Points from the Magic track; only a Game Master grants mana."
        ),
        # The spells known: a player puts free skill points into one through
        # characters.magic.learn_spell, as into a skill; never a field update
        # (spells is not in CHARACTER_FIELDS). #774.
        "spells": _player(),
        # Moves only through characters.purse.move_coin: purchases, and a
        # Game Master giving or taking coin (#757).
        "coin_adjustment": _game_master(
            "Moves with purchases; only a Game Master gives or takes coin."
        ),
        "experience_terms": _nobody(),
        "experience_log": _nobody(),
        "money_earned": _nobody(),
        "aging_str_penalty": _nobody(),
        "aging_dex_penalty": _nobody(),
        "aging_int_penalty": _nobody(),
        "aging_wis_penalty": _nobody(),
        "aging_con_penalty": _nobody(),
    }
)

#: What the sheet's field update takes from a player: the fields it edits
#: (``CHARACTER_FIELDS``) that a player may set.
PLAYER_UPDATE_FIELDS: frozenset[str] = frozenset(
    name for name in CHARACTER_FIELDS if FIELD_RULES[name].author is FieldAuthor.PLAYER
)

#: What it takes from a Game Master: every field it edits, as before #748.
GAME_MASTER_UPDATE_FIELDS: frozenset[str] = frozenset(CHARACTER_FIELDS)


def update_fields_for(*, is_game_master: bool) -> frozenset[str]:
    """The fields this asker may set through the sheet's field update.

    The view refuses anything else and the sheet renders anything else
    read-only (``characters.templatetags.character_fields``), from this one
    answer.

    Args:
        is_game_master: Whether the asker is a Game Master (``can_manage_users``).
    """
    return GAME_MASTER_UPDATE_FIELDS if is_game_master else PLAYER_UPDATE_FIELDS


def field_refusal(field_name: str) -> str:
    """What a player is told about a field they may not set.

    Args:
        field_name: A field of ``Character``.

    Returns:
        The field's reason, or a plain one for a field nobody sets by hand.
    """
    rule = FIELD_RULES.get(field_name)
    if rule is not None and rule.reason:
        return rule.reason
    return f"{field_name} is not something anyone sets by hand."


def can_set_field(
    character: Character, field_name: str, *, is_game_master: bool
) -> bool:
    """Whether this asker may set this field on this character now.

    The role's list (:func:`update_fields_for`), plus one exception the rules
    make for a player: an attribute nobody has recorded yet (a character
    started in a Games Master session, #571) may be bought, held to the
    65-point buy (:func:`check_point_buy`), and locks once recorded.
    attributes.md: "Players can roll 3d6 to determine each attribute OR
    divide 65 points between the six core attributes".

    Args:
        character: The character, as it stands.
        field_name: A field of ``Character``.
        is_game_master: Whether the asker is a Game Master.
    """
    if field_name in update_fields_for(is_game_master=is_game_master):
        return True
    return field_name in ATTRIBUTE_NAMES and getattr(character, field_name) is None


def check_field_author(
    character: Character, field_name: str, *, is_game_master: bool
) -> None:
    """Refuse a field update this asker may not make (issue #748).

    Raises:
        CharacterChangeError: The field is not this asker's to set now.
    """
    if not can_set_field(character, field_name, is_game_master=is_game_master):
        raise CharacterChangeError(field_refusal(field_name))


#: The dice-log words a bought score is recorded with, on the creation page
#: ("Strength: point-buy = 12") and on the sheet.
POINT_BUY_LOG_MARKER = "point-buy = "

#: Why a player who has typed a score may not roll the rest (the
#: coordinator's ruling on the #748 review, applying the standing rule).
BOUGHT_NOT_ROLLED_REASON = (
    "Typed scores committed this character to the 65-point buy; type the rest "
    "(the six must total 65), or ask a Game Master"
)


def bought_scores(character: Character) -> bool:
    """Whether any of the character's scores were bought rather than rolled.

    attributes.md gives a player the roll "OR" the 65-point buy, not a mix: a
    typed score commits the character to the buy, and the roll is refused for
    the rest (a Game Master may still roll).

    This reads the :data:`POINT_BUY_LOG_MARKER` words out of ``dice_log``, so
    whatever a player can write into ``dice_log`` decides whether they are
    held to the buy. A player's import must never set ``dice_log`` (the #744
    branch drops it); keep it that way, or a player could clear the marker by
    importing and roll the scores they had committed to buying.
    """
    return POINT_BUY_LOG_MARKER in (character.dice_log or "")


def check_point_buy(character: Character, name: str, value: int) -> str:
    """Hold a player's typed attribute to the 65-point buy (issue #748).

    A player types only a score nobody has recorded (:func:`can_set_field`).
    Typed scores are a point buy, so each is 3 to 18 and the six must be able
    to total exactly 65: the ones recorded, this one, and at least 3 and at
    most 18 for each still missing. The last one must make exactly 65.

    Args:
        character: The character, before the change.
        name: The attribute being typed.
        value: The score.

    Returns:
        The dice-log line recording the purchase.

    Raises:
        CharacterChangeError: A score outside what the buy leaves room for.
    """
    check_attribute(name, value, is_game_master=False)
    others = [getattr(character, other) for other in ATTRIBUTE_NAMES if other != name]
    recorded = sum(score for score in others if score is not None)
    missing_after = sum(1 for score in others if score is None)
    lowest = max(
        POINT_BUY_MIN_ATTRIBUTE,
        POINT_BUY_TOTAL - recorded - POINT_BUY_MAX_ATTRIBUTE * missing_after,
    )
    highest = min(
        POINT_BUY_MAX_ATTRIBUTE,
        POINT_BUY_TOTAL - recorded - POINT_BUY_MIN_ATTRIBUTE * missing_after,
    )
    if lowest > highest:
        # A buyer may not roll the rest (bought_scores), so only a Game Master
        # can finish this character.
        instead = (
            "ask a Game Master to record the rest"
            if bought_scores(character)
            else "roll the missing ones instead"
        )
        raise CharacterChangeError(
            f"The scores already recorded leave no {POINT_BUY_TOTAL}-point buy "
            f"for the rest; {instead}"
        )
    if not lowest <= value <= highest:
        raise CharacterChangeError(
            f"Typed scores are a {POINT_BUY_TOTAL}-point buy: with the scores "
            f"recorded, {name} can be {lowest} to {highest}; {value} was given"
        )
    return f"{name}: {POINT_BUY_LOG_MARKER}{value} (typed on the sheet)"


#: attributes.md: "Height: Choose or roll 1d6" and "Base weight is player's
#: choice between 8 and 13 stones (or roll 1d6)". A player chooses one face;
#: the chain an exploding height roll can make comes only from the dice or a
#: Game Master (issue #748).
PLAYER_D6_FACES: frozenset[str] = frozenset({"", "1", "2", "3", "4", "5", "6"})
PLAYER_CHOSEN_FACE_FIELDS: Mapping[str, str] = MappingProxyType(
    {
        "height_roll": "A player chooses one height face, 1 to 6; a longer "
        "chain comes only from a roll or a Game Master.",
        "weight_roll": "A player chooses one base-weight face, 1 to 6 (8 to 13 "
        "stones).",
    }
)


def check_player_choice(
    field_name: str, value: object, *, is_game_master: bool
) -> None:
    """Hold a player's free choice to what the rules let them choose.

    Raises:
        CharacterChangeError: A player's height or weight outside one d6 face.
    """
    reason = PLAYER_CHOSEN_FACE_FIELDS.get(field_name)
    if reason and not is_game_master and str(value).strip() not in PLAYER_D6_FACES:
        raise CharacterChangeError(reason)


#: The per-item modifiers an equipment entry may carry (issue #752).
ITEM_MODIFIER_FIELDS: tuple[str, ...] = (
    "to_hit_mod",
    "damage_mod",
    "critical_mod",
    "stops_mod",
    "dex_mod",
)

#: The rules give equipment no modifiers: attack-rolls.md names the to-hit
#: sources (DEX, weapon skill, under-strength, situational) and the catalog
#: publishes each item's own numbers. A modifier is a Game Master's grant, a
#: magic item placed as loot (dm-rules/gm-tools/dungeons-ruins.md).
ITEM_MODIFIER_RULE = _game_master(
    "Item modifiers are a Game Master's grant; the rules give equipment none."
)


def can_set_item_modifiers(*, is_game_master: bool) -> bool:
    """Whether this asker may set an equipment item's modifiers (issue #752)."""
    return is_game_master or ITEM_MODIFIER_RULE.author is FieldAuthor.PLAYER


class CharacterChangeError(Exception):
    """A change the rules do not allow; ``str()`` says why.

    Attributes:
        reason: A machine-readable kind of refusal, such as
            :data:`REFUSAL_ATTRIBUTES_NOT_RECORDED`, or ``""`` for a refusal
            only a person can resolve.
    """

    def __init__(self, message: str, reason: str = "") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class Change:
    """A change in progress on a locked character.

    Attributes:
        character: The locked row, re-read inside the transaction.
        notes: The history lines this change appends, as ``(field, old, new)``.
    """

    character: Character
    notes: list[tuple[str, str, str]] = field(default_factory=list)
    discarded: bool = False

    def discard(self) -> None:
        """Leave the row as it was: nothing is saved when the block ends."""
        self.discarded = True

    def note(self, field_name: str, old: object, new: object) -> None:
        """Record a history line for this change."""
        self.notes.append((field_name, str(old), str(new)))


@dataclass(frozen=True)
class ChangeResult:
    """What a change did.

    Attributes:
        character: The saved character.
        note: One line saying what the rules did.
        run_result: The simulator's result, for a run of experience years.
        granted: Skill points a Games Master spent past those earned.
    """

    character: Character
    note: str
    run_result: dict[str, Any] | None = None
    granted: int = 0


@contextmanager
def changing(
    character_id: int,
    *,
    actor: User | None = None,
    authorize: Callable[[Character], None] | None = None,
    is_deleted: bool = False,
    mark_played: bool = True,
    by: str = "",
) -> Iterator[Change]:
    """Lock a character, let the caller change it, then save it with history.

    Anything raised inside the block rolls the whole change back.

    Args:
        character_id: The character's primary key.
        actor: Who is making the change; ``None`` for the system.
        authorize: Called with the locked row first; raises
            ``PermissionDenied`` to refuse.
        is_deleted: The soft-delete state the row must be in.
        mark_played: Whether an owner's change marks the character played.
        by: Whom the history names, when that is not a user acting (a fight
            on the board, #750); empty names the actor, or the system.

    Yields:
        The change, holding the locked character.

    Raises:
        Character.DoesNotExist: No character with that id and state.
    """
    with transaction.atomic():
        character = Character.objects.select_for_update().get(
            pk=character_id, is_deleted=is_deleted
        )
        if authorize is not None:
            authorize(character)
        change = Change(character)
        yield change
        if change.discarded:
            return
        if not by:
            by = actor.username if actor is not None else UNATTRIBUTED
        for field_name, old, new in change.notes:
            character.record_field_change(field_name, old, new, by)
        if mark_played and actor is not None:
            character.mark_played_by(actor)
        character.save()


# ---------------------------------------------------------------------------
# The rules. Each changes ``character`` in memory and returns what it did.
# ---------------------------------------------------------------------------


def check_attribute(name: str, value: int, is_game_master: bool) -> str:
    """Hold a stated score to the rules for its author (issue #404).

    A player is held to the workbench's own limits; a Game Master may set any
    score a character can hold, and the note says it is their call.

    Returns:
        "" within the player's limits, or the Game Master's note beyond them.

    Raises:
        CharacterChangeError: A player outside the limits, or anyone past
            what a character can hold.
    """
    if name not in ATTRIBUTE_NAMES:
        raise CharacterChangeError(f"{name} is not a core attribute")
    if not 1 <= value <= MAX_ATTRIBUTE_VALUE:
        raise CharacterChangeError(
            f"{name} must be between 1 and {MAX_ATTRIBUTE_VALUE}, the most a "
            f"character can hold; {value} was given"
        )
    if POINT_BUY_MIN_ATTRIBUTE <= value <= POINT_BUY_MAX_ATTRIBUTE:
        return ""
    if not is_game_master:
        raise CharacterChangeError(
            f"{name} must be between {POINT_BUY_MIN_ATTRIBUTE} and "
            f"{POINT_BUY_MAX_ATTRIBUTE} for a player; {value} was given"
        )
    return (
        f" (outside the {POINT_BUY_MIN_ATTRIBUTE}-{POINT_BUY_MAX_ATTRIBUTE} a player "
        "may set: the Game Master's call)"
    )


def attributes_known(character: Character) -> bool:
    """Whether all six core attributes are recorded."""
    return all(getattr(character, name) is not None for name in ATTRIBUTE_NAMES)


def apply_attribute(
    character: Character, name: str, value: int, *, is_game_master: bool = False
) -> str:
    """Set one attribute, held to the rules for its author."""
    beyond = check_attribute(name, value, is_game_master)
    setattr(character, name, value)
    return f"{name} set to {value}{beyond}"


def apply_recorded_attributes(
    character: Character, name: str, value: int, *, is_game_master: bool = False
) -> str:
    """Record stated attribute scores: all six (``name="all"``) or one (#391)."""
    if name == "all":
        beyond = check_attribute("strength", value, is_game_master)
        for attribute in ATTRIBUTE_NAMES:
            setattr(character, attribute, value)
        return f"all six attributes recorded as {value}{beyond}"
    beyond = check_attribute(name, value, is_game_master)
    setattr(character, name, value)
    return f"{name} recorded as {value}{beyond}"


def check_first_roll(already_rolled: bool, what: str, is_game_master: bool) -> None:
    """Hold a player to one roll of dice already rolled (issue #590).

    A player whose attributes or Fatigue roll are recorded does not get to
    roll them again for a better number; a Game Master may.

    Raises:
        CharacterChangeError: A player asking for dice already rolled.
    """
    if already_rolled and not is_game_master:
        raise CharacterChangeError(
            f"{what} already recorded; only a Game Master can roll again"
        )


def apply_rolled_attributes(
    character: Character, *, is_game_master: bool = False
) -> str:
    """Roll 3d6 for each of the six attributes and record them (issue #590).

    Code owns the dice: the faces go into the character's dice log.
    """
    check_first_roll(
        any(getattr(character, name) is not None for name in ATTRIBUTE_NAMES),
        "The attributes are",
        is_game_master,
    )
    rolled = []
    logs = []
    for name in ATTRIBUTE_NAMES:
        total, log = roll_3d6_logged()
        setattr(character, name, total)
        rolled.append(f"{name} {log}")
        logs.append(f"{name}: {log}")
    character.dice_log = "\n".join(part for part in (character.dice_log, *logs) if part)
    return "rolled the six attributes: " + "; ".join(rolled)


def apply_fatigue_roll(character: Character, value: int | None = None) -> str:
    """Record the 2d6 Fatigue roll: rolled by the server, or as stated.

    Args:
        character: The character.
        value: A stated roll; ``None`` rolls it here (issue #391), because
            code owns the dice.

    Raises:
        CharacterChangeError: A stated roll outside what 2d6 can produce
            (issue #653). There is no Game Master override here, unlike
            ``check_attribute``: this is a die roll, not a build choice.
    """
    if value is not None:
        if not FATIGUE_ROLL_MINIMUM <= value <= FATIGUE_ROLL_MAXIMUM:
            raise CharacterChangeError(
                f"The Fatigue roll is 2d6, so it must be between "
                f"{FATIGUE_ROLL_MINIMUM} and {FATIGUE_ROLL_MAXIMUM}; "
                f"{value} was given"
            )
        character.fatigue_roll = value
        return f"Fatigue roll (2d6) set to {value}"
    rolls, total = roll_expression("2d6")
    character.fatigue_roll = total
    faces = " and ".join(str(face) for face in rolls)
    return f"Fatigue roll (2d6) rolled {faces} = {total}"


def can_roll_literacy(character: Character, *, is_game_master: bool) -> bool:
    """Whether the literacy roll may be made now, by this asker (issue #741).

    The rule is quoted on :func:`apply_literacy_roll`, which enforces the same
    limits; this answers the question for the page, which offers the
    control only when the server would take it. "Consult GM" publishes no
    modifier, so there is no check to roll against, and nobody rolls it until
    the origin is one that has one. Otherwise a player rolls once, and only
    against an Origin somebody rolled or chose (#754), and a Game Master may
    roll again (issues #590, #636).
    """
    if character.location_literacy_modifier is None:
        return False
    if is_game_master:
        return True
    return character.location_recorded and not character.literacy_rolled


#: Why a player may not roll literacy before the Origin (the coordinator's
#: ruling on the #754 review): the roll fixes the Origin
#: (:func:`can_change_origin`), so rolled against one nobody rolled or chose
#: it would fix the model's default Village for good, with no skill granted.
LITERACY_BEFORE_ORIGIN_REASON = (
    "The Origin is not rolled or chosen yet; roll or choose the Origin first, "
    "since the literacy check is made against it"
)


#: Why a player's Origin stops being editable (Spencer's ruling on #741). The
#: field-update refusal says it, and the sheet's note beside the Origin says it.
ORIGIN_FIXED_REASON = (
    "The Origin is fixed once literacy is rolled or ruled; only a Game Master "
    "can change it."
)


def can_change_origin(character: Character, *, is_game_master: bool) -> bool:
    """Whether this asker may change the character's Origin (issue #741).

    The literacy roll is made against the origin's check, so once it is
    rolled a player may no longer move the origin under it: they would be
    choosing the check after seeing the dice. A Game Master still may, the
    same Game Master who may roll the check again (``can_manage_users``).
    Before any roll the Origin is as editable as it always was. "Rolled"
    means ever rolled (``Character.literacy_rolled``): a roll a Game Master
    cleared by moving the character to "Consult GM" keeps the lock. A Game
    Master's ruling at "Consult GM" (#743) locks it too: otherwise a player
    ruled illiterate could move to an origin with a check and roll it.
    """
    decided = character.literacy_rolled or character.literacy_ruling is not None
    return not decided or is_game_master


def can_rule_literacy(character: Character, *, is_game_master: bool) -> bool:
    """Whether this asker may record a literacy ruling now (issue #743).

    A Game Master, at an origin of "Consult GM": the one origin the rules
    publish no check for, so the one where there is nothing to roll and the
    table decides. Everywhere else literacy is rolled.
    """
    return is_game_master and character.location_literacy_modifier is None


def apply_literacy_ruling(
    character: Character, literate: bool | None, *, is_game_master: bool
) -> str:
    """Record, change or withdraw the Game Master's literacy ruling (#743).

    Spencer's house rule: players are held to the rules and a Game Master
    overrides. Here the rules have nothing to hold anyone to -- location.md
    publishes no modifier for "Consult GM" -- so the Game Master's word is
    the record, stored as a ruling rather than as a roll, and shown as one.

    Args:
        character: The character.
        literate: ``True`` or ``False``; ``None`` withdraws a ruling.
        is_game_master: Whether a Game Master asked.

    Raises:
        CharacterChangeError: A player asking, or an origin that has a check
            to roll.
    """
    if not is_game_master:
        raise CharacterChangeError(LITERACY_RULING_GAME_MASTER_ONLY)
    if character.location_literacy_modifier is not None:
        raise CharacterChangeError(
            f"The {character.location} origin has a literacy check to roll; a "
            "Game Master's ruling is for an origin of Consult GM"
        )
    character.literacy_ruling = literate
    if literate is None:
        return "the Game Master's literacy ruling withdrawn"
    return f"ruled {'literate' if literate else 'illiterate'} by the Game Master"


def apply_literacy_roll(character: Character, *, is_game_master: bool = False) -> str:
    """Roll the 3d6 literacy check and record the dice (issue #741).

    The rule, from reference/content/public-rules/characters/location.md:

        "The literacy modifier is a penalty — letters are harder to come by
        the further you live from a city. Add it to the 3d6 literacy roll, or
        equivalently subtract it from INT. City literacy is 3d6 ≤ INT; Village
        literacy is 3d6 ≤ INT − 2; Rural literacy is 3d6 ≤ INT − 4. This is
        the check to be literate at all, and is separate from the Literacy
        skill."

    Code owns the dice. Only the roll is stored; whether it passes is read
    from the current origin and INT (``Character.is_literate``), so a
    corrected INT re-reads these dice. A changed origin does not: once the
    roll is made only a Game Master may change the origin, and that rolls the
    check again against the new one (:func:`apply_origin_to_literacy`).

    Args:
        character: The character.
        is_game_master: Whether a Game Master asked, who may roll again.

    Raises:
        CharacterChangeError: The origin is "Consult GM", which publishes no
            check; a player whose Origin nobody has rolled or chosen
            (:data:`LITERACY_BEFORE_ORIGIN_REASON`); or a player asking for
            dice already rolled.
    """
    if character.location_literacy_modifier is None:
        raise CharacterChangeError(
            "The rules publish no literacy check for an origin of Consult GM; "
            "the Game Master decides whether this character can read"
        )
    if not is_game_master and not character.location_recorded:
        raise CharacterChangeError(LITERACY_BEFORE_ORIGIN_REASON)
    check_first_roll(character.literacy_rolled, "The literacy roll is", is_game_master)
    # A Game Master's roll against an Origin nobody rolled or chose makes it
    # the Origin, as migration 0021 does for a check already rolled: the roll
    # fixes it for the player (can_change_origin), who must not be left
    # waiting on an Origin roll they can no longer make, or without its skill.
    adopted = ""
    if not character.location_recorded:
        character.location_recorded = True
        granted = [line for *_, line in apply_origin_skill(character, None)]
        adopted = "; ".join(
            [
                f"the {character.location} origin recorded, as the check was "
                "rolled against it",
                *granted,
            ]
        )
    total, log = roll_3d6_logged()
    character.literacy_roll = total
    character.literacy_rolled = True
    note = f"rolled the literacy check ({character.location_literacy_check}), {log}"
    if adopted:
        note = f"{note}; {adopted}"
    threshold = character.literacy_threshold
    if threshold is None:
        return f"{note}; INT is not recorded yet, so it reads once INT is"
    passed = total <= threshold
    comparison = "≤" if passed else ">"
    outcome = "literate" if passed else "illiterate"
    return f"{note}; {total} {comparison} {threshold}, {outcome}"


def apply_death(character: Character, cause: str) -> str:
    """Mark a living character dead, with the cause (a Game Master's call).

    The sheet's mark-dead control and the Games Master chat's ``mark_dead``
    action both write a death through here (issue #802); a fight on the board
    writes its own through :func:`apply_fight_report`. Undone by
    :func:`apply_revive`, which keeps the cause in the log.

    Raises:
        CharacterChangeError: The character is already dead.
    """
    if not character.is_alive:
        raise CharacterChangeError("Character is already dead")
    character.is_alive = False
    character.death_cause = cause[: Character._meta.get_field("death_cause").max_length]
    return f"marked dead: {character.death_cause}"


def apply_revive(character: Character) -> str:
    """Bring a dead character back, keeping how they died in the log (#403).

    Raises:
        CharacterChangeError: The character is alive.
    """
    if character.is_alive:
        raise CharacterChangeError("is alive; there is nothing to bring back")
    cause = character.death_cause
    character.is_alive = True
    character.death_cause = ""
    return (
        f"brought back to life (had died: {cause})" if cause else "brought back to life"
    )


#: The most damage a pool column holds.
MAX_POOL_DAMAGE = CHARACTER_INT_FIELD_MAX["fatigue_damage"]


#: Whom the history names for what a Games Master session's fight did: the
#: side that dealt it belongs to the Game Master (#762), not to the player who
#: started the fight.
GAMES_MASTER_AUTHOR = "Games Master"

#: Why a fight's one history line is written for a character: it ended.
FIGHT_ENDED = "ended"
#: ...or it was restarted, and what the abandoned run took was handed back.
FIGHT_RESTARTED = "restarted"


@dataclass(frozen=True)
class FightReport:
    """What a fight on the hex board tells a character's record (#750, #751).

    The one contract between the battle app and the character record. The
    battle sends it at two moments, always inside the transaction that
    changed the board, so the record never lags what the board shows and no
    later step (a chat message, a detach, a restart) stands between the two:

    * **a committed turn** -- for each saved character whose figure the turn
      hurt, healed or killed: what the turn took from each pool, and whether
      the figure died. The turn that ends the fight reports every saved
      character, ``closes`` set to :data:`FIGHT_ENDED`;
    * **a restart** -- the negative of everything the fight has taken from
      each pool so far, because the replay takes it again from the same
      starting snapshot, ``closes`` set to :data:`FIGHT_RESTARTED`. A restart
      never reports a revival: a death stays recorded, and bringing a
      character back is a Game Master's act (:func:`revive`).

    Only a Games Master session's fight and a battle a Game Master runs send
    these (``Battle.writes_to_records``); a player's own battle writes
    nothing to any record.

    Changes rather than totals, because a character can leave one fight
    unfinished, be hurt in another and come back: a total from the first
    board would write its stale pools over the second fight's wounds.

    The pools are written on every turn; the history gets one line per
    fight -- when it ends, kills the character, or is restarted -- carrying
    the pools the fight found them at, so a long fight does not push the
    character's other history out of its fifty lines.

    Attributes:
        character_id: The ``Character`` the figure is.
        fight: The battle's name, for the history and the cause of death.
        turn: The turn just committed, or the turn a restart abandoned.
        fatigue_lost: Fatigue the turn took (negative when it restored some).
        body_lost: Body the turn took (negative when it restored some).
        mana_spent: Mana the turn spent (negative when a restart hands it
            back). Spent mana stays spent on the record, like a wound, until
            a Game Master restores it: the rules publish no recovery.
        died: The figure died this turn.
        closes: ``""`` mid-fight, else why the fight's history line is due.
        started_fatigue: The figure's Fatigue when the fight began, the
            history line's old value; ``None`` to use the record's own.
        started_body: The figure's Body when the fight began, likewise.
        started_mana: The figure's Mana when the fight began, likewise.
        author: Whom the fight's history lines name: the Game Master
            (:data:`GAMES_MASTER_AUTHOR`) for a fight whose foes are theirs,
            so a player is never credited with marking their own death; the
            battle's maker for a battle a Game Master chose to count.
    """

    character_id: int
    fight: str
    turn: int
    fatigue_lost: int = 0
    body_lost: int = 0
    mana_spent: int = 0
    died: bool = False
    closes: str = ""
    started_fatigue: int | None = None
    started_body: int | None = None
    started_mana: int | None = None
    author: str = ""

    @property
    def changes_anything(self) -> bool:
        """Whether there is anything for the record to take or say."""
        return bool(
            self.fatigue_lost
            or self.body_lost
            or self.mana_spent
            or self.died
            or self.closes
        )


def _add_damage(damage: int, lost: int) -> int:
    """``damage`` plus ``lost``, floored at unhurt and capped at the column.

    The floor matters when a Game Master healed the record mid-fight and the
    fight then gives back what it had taken: nobody goes above full.
    """
    return min(MAX_POOL_DAMAGE, max(0, damage + lost))


def _fight_line(report: FightReport) -> str:
    """How the fight's history line names the moment it was written."""
    if report.died:
        return f"killed in {report.fight}, turn {report.turn}"
    if report.closes == FIGHT_RESTARTED:
        return f"{report.fight} restarted"
    return f"at the end of {report.fight}, turn {report.turn}"


def apply_fight_report(
    character: Character, report: FightReport
) -> tuple[str, str] | None:
    """Take one fight report onto the record, in memory (#750, #751).

    A character already dead on the record takes nothing: a restarted fight
    may put its figure back on the board, but the record's death stands.

    Returns:
        ``(old, new)`` for the fight's history line when one is due and the
        fight changed the character, else ``None``.
    """
    if not character.is_alive:
        return None
    before = character.condition
    mana_before = character.mana_text
    character.fatigue_damage = _add_damage(
        character.fatigue_damage, report.fatigue_lost
    )
    character.body_damage = _add_damage(character.body_damage, report.body_lost)
    character.mana_spent = _add_damage(character.mana_spent, report.mana_spent)
    if report.died:
        character.is_alive = False
        character.death_cause = f"Killed in battle — {report.fight}"[:200]
    after = character.condition
    if not (report.closes or report.died) or before is None or after is None:
        return None
    pool = character.free_spell_points
    if report.started_fatigue is not None and report.started_body is not None:
        old = CurrentCondition.pools_text(
            report.started_fatigue,
            after.fatigue_maximum,
            report.started_body,
            after.body_maximum,
        )
        if pool and report.started_mana is not None:
            old += f"; Mana {report.started_mana}/{pool}"
    else:
        old = before.pools + (f"; {mana_before}" if mana_before else "")
    now = after.pools + (f"; {character.mana_text}" if character.mana_text else "")
    if old == now and not report.died:
        return None
    mana = f"; {character.mana_text}" if character.mana_text else ""
    return old, f"{after.summary}{mana}, {_fight_line(report)}"


def record_fight(report: FightReport) -> ChangeResult | None:
    """Write one fight report to the character's record (#750, #751).

    Through :func:`changing`, like every other change: the row is locked and
    nobody is marked as having played it. The pools are written every turn;
    the history gets the fight's one line when it ends, kills the character
    (beside the ``is_alive`` line the Game Master's mark-dead writes), or is
    restarted (see :class:`FightReport`).

    Returns:
        The change, or ``None`` when the record had nothing to take.
    """
    if not report.changes_anything:
        return None
    with changing(report.character_id, by=report.author, mark_played=False) as change:
        character = change.character
        was = (
            character.fatigue_damage,
            character.body_damage,
            character.mana_spent,
            character.is_alive,
        )
        line = apply_fight_report(character, report)
        now = (
            character.fatigue_damage,
            character.body_damage,
            character.mana_spent,
            character.is_alive,
        )
        if was == now and line is None:
            change.discard()
            return None
        if line is not None:
            change.note("current pools", *line)
        if was[3] and not character.is_alive:
            change.note("is_alive", True, False)
    return ChangeResult(change.character, line[1] if line else "")


def apply_current_pools(
    character: Character,
    *,
    fatigue: int | None = None,
    body: int | None = None,
    mana: int | None = None,
    is_game_master: bool = False,
) -> str:
    """Set current Fatigue, Body and/or Mana, for a Game Master only (#751).

    A player's pools are what the fights did to them; healing, rest, mana
    restored and a blow off the board are the Game Master's to rule, and the
    rules publish no recovery rate to apply for any of them.

    Args:
        character: The character.
        fatigue: Current Fatigue, or ``None`` to leave it.
        body: Current Body, or ``None`` to leave it.
        mana: Current Mana, or ``None`` to leave it.
        is_game_master: Whether a Game Master asked.

    Raises:
        CharacterChangeError: A player asking, pools not known yet, a value
            above the pool's maximum, Mana below 0, or a pool further below 0
            than the record can hold.
    """
    if not is_game_master:
        raise CharacterChangeError(POOLS_GAME_MASTER_ONLY)
    if fatigue is None and body is None and mana is None:
        raise CharacterChangeError("Give a current Fatigue, Body or Mana to set")
    maxima = {"Fatigue": character.fatigue, "Body": character.body}
    if (fatigue is not None or body is not None) and (
        maxima["Fatigue"] is None or maxima["Body"] is None
    ):
        raise CharacterChangeError(
            "Fatigue and Body are not known until the attributes and the Fatigue "
            "roll are recorded"
        )
    spent: dict[str, int] = {}
    for label, value, maximum, floor in (
        ("Fatigue", fatigue, maxima["Fatigue"], -MAX_POOL_DAMAGE),
        ("Body", body, maxima["Body"], -MAX_POOL_DAMAGE),
        ("Mana", mana, character.free_spell_points, 0),
    ):
        if value is None:
            continue
        top = int(maximum or 0)
        if value > top:
            raise CharacterChangeError(
                f"{label} cannot be above its maximum of {top}; {value} was given"
            )
        if top - value > MAX_POOL_DAMAGE or value < floor:
            below = "below 0" if floor == 0 else "further down than a record holds"
            raise CharacterChangeError(f"{label} {value} is {below}")
        spent[label] = top - value
    character.fatigue_damage = spent.get("Fatigue", character.fatigue_damage)
    character.body_damage = spent.get("Body", character.body_damage)
    character.mana_spent = spent.get("Mana", character.mana_spent)
    condition = character.condition
    now = [condition.summary] if condition is not None else []
    if character.mana_text:
        now.append(character.mana_text)
    return f"set by the Game Master to {'; '.join(now)}"


def set_current_pools(
    character_id: int,
    *,
    fatigue: int | None = None,
    body: int | None = None,
    mana: int | None = None,
    actor: User | None = None,
    authorize: Callable[[Character], None] | None = None,
    is_game_master: bool = False,
) -> ChangeResult:
    """Set a saved character's current Fatigue, Body and/or Mana (issue #751).

    See :func:`apply_current_pools`: a Game Master only.
    """
    with changing(
        character_id, actor=actor, authorize=authorize, mark_played=False
    ) as change:
        character = change.character
        before = character.condition
        was = [before.pools] if before is not None else []
        if character.mana_text:
            was.append(character.mana_text)
        note = apply_current_pools(
            character,
            fatigue=fatigue,
            body=body,
            mana=mana,
            is_game_master=is_game_master,
        )
        change.note("current pools", "; ".join(was), note)
    return ChangeResult(change.character, note)


def allocation_target(skills: Mapping[str, int] | None, skill: str) -> str:
    """The stored name an allocation to ``skill`` lands on (issue #808).

    A bare "maker skill" beside "maker skill (smith)" is that skill, as a
    track's grant is. :func:`apply_skill_points` writes here, and the sheet's
    allocate endpoint builds the row it answers from the same name.

    Args:
        skills: The character's stored skills.
        skill: The name as asked for.
    """
    return specialised_skill_for_granted(
        dict(skills or {}), normalize_skill_name(skill)
    )


#: Why a player may not start a skill the rules do not name (issue #862).
ALLOCATION_REFUSED_REASON = (
    "A free point goes into a skill the skills glossary names, or one the "
    "character already holds; only a Game Master may start another skill"
)

#: Why a player may not start a Knowledge field (issue #862), as a rename
#: may not focus one (:data:`RENAME_REFUSED_REASON`).
KNOWLEDGE_FIELD_REFUSED_REASON = (
    "Only a Game Master may focus Knowledge on a field; a player's free point "
    "goes into Knowledge itself"
)


def allocation_refusal(skills: Mapping[str, int] | None, skill: str) -> str | None:
    """Why a player may not put a free point into ``skill``, or None (#862).

    A player spends a free point on a skill the character already holds, or
    starts one the skills glossary names
    (:func:`characters.skill_checks.is_glossary_skill`): a Weapon skill, the
    Maker Skill with its craft (the choice a rename also leaves a player,
    :data:`PLAYER_CHOSEN_PARTS`), Arcane, and so on. A name no rule knows,
    "brawling" among them, and a Knowledge field are the Game Master's to
    allow. The standing rule applied (players held to the rules, a Game
    Master overrides): coordinator's ruling, Spencer may overrule.

    Args:
        skills: The character's stored skills.
        skill: The name as asked for.
    """
    from .skill_checks import is_glossary_skill

    target = allocation_target(skills, skill)
    if target in (skills or {}):
        return None
    under, chosen_part = split_specialised_skill(target)
    if chosen_part and under not in PLAYER_CHOSEN_PARTS:
        return KNOWLEDGE_FIELD_REFUSED_REASON
    if not is_glossary_skill(target):
        return ALLOCATION_REFUSED_REASON
    return None


def apply_skill_points(
    character: Character,
    skill: str,
    points: int,
    *,
    is_game_master: bool = False,
    glossary_only: bool = False,
) -> tuple[str, int]:
    """Spend free skill points on one skill.

    Args:
        character: The character, changed in memory.
        skill: The skill's name as asked for.
        points: How many points to spend.
        is_game_master: Whether a Game Master asked; he may grant points
            past those earned.
        glossary_only: Hold the name to :func:`allocation_refusal`, as the
            sheet does for a player (issue #862).

    Returns:
        ``(note, granted)``: what was done, and how many points a Game Master
        spent past those earned (issue #404).

    Raises:
        CharacterChangeError: Too few free points for a player, a name
            ``glossary_only`` refuses, or the skill would pass the storage
            ceiling.
    """
    if points < 1:
        raise CharacterChangeError("A skill purchase must be at least one point")
    granted = 0
    if character.free_skill_points < points:
        if not is_game_master and not character.free_skill_points:
            raise CharacterChangeError(
                f"No free skill points available (0 free skill point(s); "
                f"{points} asked for)"
            )
        if not is_game_master:
            raise CharacterChangeError(
                f"Only {character.free_skill_points} free skill point(s) available; "
                f"{points} were asked for"
            )
        granted = points - character.free_skill_points
        character.free_skill_points = points
    if glossary_only:
        refusal = allocation_refusal(character.skills, skill)
        if refusal is not None:
            raise CharacterChangeError(refusal)
    skills = dict(character.skills or {})
    name = allocation_target(skills, skill)
    current = int(skills.get(name, 0))
    if current + points > MAX_SKILL_POINTS:
        raise CharacterChangeError(
            f"Skill would pass the maximum storable points ({MAX_SKILL_POINTS})"
        )
    skills[name] = current + points
    character.skills = skills
    character.free_skill_points -= points
    # Both figures are named, and the one that matters next is given rather
    # than left to be worked out. "(now I (+1), 2 pts)" put the skill's own
    # points beside the words "skill point(s)" and never said how many free
    # points were left, so the GM subtracted for itself and sometimes told a
    # player 0 when the record held 1 (issue #356).
    left = character.free_skill_points
    total = skills[name]
    note = (
        f"spent {points} skill point(s) on {name} "
        f"({name} is now {format_skill_level(total, name)}, "
        f"{total} point{'' if total == 1 else 's'} in it); "
        f"{left} free skill point{'' if left == 1 else 's'} left"
    )
    if granted:
        note += f"; {granted} past the points earned, granted by the Game Master"
    return note, granted


#: Why a player may not add prior experience to a character in play (#803).
IN_PLAY_REASON = (
    "This character has entered play (seated in a battle, or played in a "
    "Games Master session); prior experience comes before play, so only a "
    "Game Master can add years now"
)


def character_in_play(character: Character) -> bool:
    """Whether the character has entered play (issue #803).

    The coordinator's ruling under the standing rule (Spencer may overrule):
    in play means seated in a battle, or attached to a Games Master session
    that has played a turn -- not ``is_played``, which any owner's edit sets.
    It is stored once, when it first happens (``entered_play_at``,
    :func:`record_entry_into_play`, which states which turns are play), and
    never cleared, so detaching a character from its session does not take it
    out of play.

    Args:
        character: The character.
    """
    return character.entered_play_at is not None


def _complete_rows():
    """The saved characters complete enough to play: six scores and Fatigue."""
    return Character.objects.filter(
        entered_play_at__isnull=True,
        fatigue_roll__isnull=False,
        **{f"{name}__isnull": False for name in ATTRIBUTE_NAMES},
    )


def record_entry_into_play(
    *, session_id: int | None = None, battle_id: int | None = None
) -> int:
    """Mark characters as entered play, once (issue #803).

    The coordinator's ruling on #803 under the standing rule (Spencer may
    overrule): play begins only with a campaign action -- ``move_party``,
    ``advance_time``, the supply, entity and thread actions, or a house
    ruling recorded -- or with a fight, in either session mode. Narration and
    questions never count, so a player may ask which track to take and then
    play years. A turn that both plays years and runs a campaign action
    enters play after that turn's years apply. Called when such a Games
    Master turn completes (``gm.turn_record.turn_is_play``), for every
    complete character attached to its session, and when a battle starts
    (``battle.services``), for every seat holding a complete character. A
    character still being built -- no scores or no Fatigue roll -- is not in
    play.

    Args:
        session_id: A Games Master session whose turn of play completed.
        battle_id: A battle that started.

    Returns:
        How many characters entered play now.
    """
    from django.utils import timezone

    rows = _complete_rows()
    if session_id is not None:
        rows = rows.filter(gm_sessions__pk=session_id)
    elif battle_id is not None:
        rows = rows.filter(battle_combatants__battle_id=battle_id)
    else:
        return 0
    return Character.objects.filter(
        pk__in=list(rows.values_list("pk", flat=True))
    ).update(entered_play_at=timezone.now())


def apply_experience_years(
    character: Character, track: str, years: int, *, is_game_master: bool = False
) -> tuple[str, dict[str, Any]]:
    """Play ``years`` of prior experience through the simulator.

    Simulates the run from the character's base attributes, prices the XP
    thresholds crossed, and hands both to ``Character.apply_experience_run``.

    prior-experience.md: "Characters start at 16 years of age and level 1.
    They can begin playing immediately or acquire additional skills through
    prior experience." So a player adds years before play only
    (:func:`character_in_play`, issue #803); a Game Master may after, and the
    note says it was his call.

    Returns:
        ``(note, run_result)``.

    Raises:
        CharacterChangeError: A dead character, missing attributes, a bad
            number of years, an unknown track, or a player's character that
            has entered play.
    """
    if not character.is_alive:
        if is_game_master:
            raise CharacterChangeError(
                f"{character.name or 'The character'} is dead; bring them back "
                "to life first"
            )
        raise CharacterChangeError(f"{character.name or 'The character'} is dead")
    if not attributes_known(character):
        raise CharacterChangeError(
            "Roll or buy the six core attributes before taking years of experience",
            reason=REFUSAL_ATTRIBUTES_NOT_RECORDED,
        )
    if years < 1 or years > MAX_EXPERIENCE_YEARS_PER_RUN:
        raise CharacterChangeError(
            f"Years must be between 1 and {MAX_EXPERIENCE_YEARS_PER_RUN}"
        )
    chosen = get_random_track() if track in ("", "random") else track
    if chosen not in ALL_TRACKS:
        raise CharacterChangeError(f"Unknown track: {track}")
    in_play = character_in_play(character)
    if in_play and not is_game_master:
        raise CharacterChangeError(IN_PLAY_REASON)
    run_result = simulate_experience_years(
        years_requested=years,
        years_completed=character.experience_years,
        current_age=character.age,
        track=chosen,
        strength=character.strength,
        dexterity=character.dexterity,
        intelligence=character.intelligence,
        wisdom=character.wisdom,
        constitution=character.constitution,
        charisma=character.charisma,
        starting_skills=character.skills,
    )
    xp_before = character.total_xp
    # What a lowered XP left owed is paid first (#769).
    skill_points_from_xp = pay_owed_skill_points(
        character,
        calculate_skill_points_in_range(
            xp_before, xp_before + run_result["xp_awarded"]
        ),
    )
    run_result["track"] = chosen
    character.apply_experience_run(run_result, chosen, skill_points_from_xp)
    played = len(run_result["years"])
    outcome = "" if run_result["alive"] else f", and died: {character.death_cause}"
    note = (
        f"played {played} of {years} year(s) on the {chosen.replace('_', ' ')} "
        f"track (+{run_result['xp_awarded']} XP, "
        f"+{run_result['money_earned']} coin, "
        f"+{run_result['free_points_awarded']} free skill point(s)){outcome}"
    )
    if in_play:
        note += "; after the character entered play: the Game Master's call"
    return note, run_result


def pay_owed_skill_points(character: Character, earned: int) -> int:
    """Pay what a lowered XP left owed out of newly earned points (#769).

    Returns:
        The points left to grant after the debt is paid.
    """
    paid = min(character.skill_points_owed, earned)
    character.skill_points_owed -= paid
    return earned - paid


def apply_xp_change(character: Character, new_xp: int) -> str:
    """Set lifetime XP and grant the skill points it earns (issue #769).

    prior-experience.md: "Skill points are granted cumulatively as XP is
    earned. For example, at 3,000 XP you would have earned 2 skill points."
    The same arithmetic a run of years is priced by
    (``calculate_skill_points_in_range``), into the same free points. Only a
    Game Master sets XP by hand (``FIELD_RULES``).

    Lowering XP takes back the points the lower total no longer earns, from
    the free points; what was already spent stays where it is and is owed
    (``skill_points_owed``), paid from the next points XP earns, so lowering
    and raising XP never makes points. Coordinator's ruling under the
    standing rule (a Game Master's correction; no refunds of what was
    bought); Spencer may overrule.

    Args:
        character: The character, changed in memory.
        new_xp: The new lifetime XP, already validated.

    Returns:
        What happened to the free points, or ``""`` when nothing did.
    """
    old_xp = character.total_xp
    character.total_xp = new_xp
    if new_xp >= old_xp:
        earned = calculate_skill_points_in_range(old_xp, new_xp)
        if not earned:
            return ""
        owed_before = character.skill_points_owed
        granted = pay_owed_skill_points(character, earned)
        character.free_skill_points += granted
        note = (
            f"+{granted} free skill point(s) earned by the XP "
            f"({character.free_skill_points} free)"
        )
        if owed_before != character.skill_points_owed:
            note += (
                f"; {owed_before - character.skill_points_owed} paid what a "
                "lowered XP left owed"
            )
        return note
    lost = calculate_skill_points_from_xp(old_xp) - calculate_skill_points_from_xp(
        new_xp
    )
    if not lost:
        return ""
    taken = min(lost, character.free_skill_points)
    character.free_skill_points -= taken
    character.skill_points_owed += lost - taken
    note = (
        f"-{taken} free skill point(s) the lower XP no longer earns "
        f"({character.free_skill_points} free)"
    )
    if taken < lost:
        note += (
            f"; {lost - taken} already spent stay where they are, owed from "
            "the next XP earned"
        )
    return note


#: A player's rename that would move points to another rule (issue #768).
RENAME_REFUSED_REASON = (
    "A rename may not move points to another skill. A player may name a "
    "Maker Skill's craft once, or move a Village origin's point between "
    "Street Smarts and Survival; only a Game Master may focus Knowledge or "
    "move points between skills"
)


def _village_choice(character: Character, old_skill: str, new_skill: str) -> bool:
    """Whether this rename is the Village origin's choice (location.md).

    "Village: Street smarts OR survival (choose)": the one point the Origin
    granted (``origin_skill``) moves to the other of the two, and nothing
    else rides with it.
    """
    choices = _grant_skills("Village")
    return (
        character.location == "Village"
        and character.origin_skill == old_skill
        and old_skill in choices
        and new_skill in choices
        and old_skill != new_skill
        and int((character.skills or {}).get(old_skill, 0)) == LOCATION_SKILL_POINTS
    )


#: The chosen parts a player names: Maker Skill is "A **chosen craft**"
#: (skills glossary). Knowledge's field is not here: "the GM may let you
#: focus a field", so focusing it is the Game Master's.
PLAYER_CHOSEN_PARTS: tuple[str, ...] = ("maker skill",)


def _names_chosen_part(old_skill: str, new_skill: str) -> bool:
    """Whether this rename is a player naming the Maker Skill's craft.

    The choice is made once, from the bare skill, the way a Weapon skill is
    tied to its weapon once (#753); moving it afterwards is the Game Master's.
    """
    skill, detail = split_specialised_skill(new_skill)
    return old_skill in PLAYER_CHOSEN_PARTS and skill == old_skill and bool(detail)


def apply_skill_rename(
    character: Character, old_skill: str, new_skill: str, *, is_game_master: bool
) -> tuple[str, int]:
    """Rename one of the character's skills (issue #768).

    The rule: a rename never changes which rule a skill falls under
    (:func:`~characters.skills.glossary_skill`). A player may make the two
    choices the rules leave them -- a Village origin's Street Smarts or
    Survival (joining one already held), and the Maker Skill's craft; a
    Knowledge field is the Game Master's to allow -- and a
    Weapon skill is tied through its own control (#753). Anything else moves
    points to another skill (15 points of a track's Coins into a weapon, a
    skill out of Arcane's double cost), which only a Game Master may do.

    Args:
        character: The character, changed in memory.
        old_skill: The stored (normalised) name.
        new_skill: The new name, normalised.
        is_game_master: Whether a Game Master asked.

    Returns:
        ``(what the history records as the new name, points)``: the new name,
        with a note when a Game Master moved points to another skill.

    Raises:
        CharacterChangeError: No such skill, a name already held, or a player
            moving points to another skill.
    """
    skills = dict(character.skills or {})
    if old_skill not in skills:
        raise CharacterChangeError(f"Skill '{old_skill}' not found")
    if new_skill in skills and _village_choice(character, old_skill, new_skill):
        # The Origin's point joins a Survival or Street Smarts the character
        # already holds from a track (location.md: "choose").
        del skills[old_skill]
        skills[new_skill] += LOCATION_SKILL_POINTS
        character.skills = skills
        character.origin_skill = new_skill
        return new_skill, skills[new_skill]
    if new_skill in skills:
        raise CharacterChangeError(f"Skill '{new_skill}' already exists")
    if not is_game_master:
        if adds_to_attacks(old_skill) or adds_to_attacks(new_skill):
            # Every name an attack reads (issue #753).
            raise CharacterChangeError(
                "A skill that adds to attacks (Weapon or Archery) cannot "
                "be renamed, nor another skill renamed into one; only a Game "
                "Master can"
            )
        if not (
            _village_choice(character, old_skill, new_skill)
            or _names_chosen_part(old_skill, new_skill)
        ):
            raise CharacterChangeError(RENAME_REFUSED_REASON)
    points = skills.pop(old_skill)
    skills[new_skill] = points
    character.skills = skills
    # The Origin's point goes with the skill that holds it, so an Origin
    # change then moves that point and no other (issue #754).
    if character.origin_skill == old_skill:
        character.origin_skill = new_skill
    recorded = new_skill
    if glossary_skill(old_skill) != glossary_skill(new_skill):
        recorded += " (points moved from another skill: the Game Master's call)"
    return recorded, points


def catalog_item_id(name: str) -> str:
    """The catalog id this name means, or ``""`` for a custom item (#719).

    Matched on the catalog's own name, ignoring case, so "broadsword" and
    "Broadsword" are the same thing the sheet sells.

    Args:
        name: What the item was called.
    """
    wanted = name.strip().casefold()
    for item_id, item in EQUIPMENT_CATALOG.items():
        if str(item.get("name", "")).casefold() == wanted:
            return item_id
    return ""


def apply_equipment(
    character: Character, name: str, quantity: int, *, purchase: bool = False
) -> str:
    """Add ``quantity`` of a named item, stacking onto one already carried.

    With ``purchase``, a catalog item is bought at the catalog's price
    through ``characters.purse`` and refused when the purse is short: a
    player's Games Master session gets gear the way the sheet does
    (coordinator's ruling on the #757 review). A Game Master's session gives
    it free; an item the catalog does not sell has no price and carries no
    combat numbers, so it is added as it is.

    The name is resolved against the catalog first. It used to be compared
    against each stored entry's ``name``, which a catalog entry does not
    carry -- it stores only ``item_id``, ``quantity``, ``in_pack`` and
    ``notes`` -- so the comparison was always false against one. A character
    who bought a broadsword on the sheet and was then handed a broadsword by
    the Game Master ended up with two lines, the second of them with no
    ``item_id``, resolving no weight, damage or category anywhere (#719).
    """
    if quantity < 1:
        raise CharacterChangeError("An item's quantity must be at least one")
    equipment = list(character.equipment or [])
    item_id = catalog_item_id(name)
    carried_item = None
    for item in equipment:
        stored_id = str(item.get("item_id", ""))
        carried = (
            stored_id == item_id
            if item_id and stored_id
            else str(item.get("name", "")).casefold() == name.casefold()
        )
        if carried:
            carried_item = item
            break
    # One line holds at most what the sheet and the importer accept, so a
    # character always survives its own export (issue #800): two gifts of 999
    # used to stack to 1,998, which the importer refuses.
    held = int(carried_item.get("quantity", 1)) if carried_item is not None else 0
    if held + quantity > MAX_EQUIPMENT_QUANTITY:
        raise CharacterChangeError(
            f"One line of equipment holds at most {MAX_EQUIPMENT_QUANTITY}; "
            f"{held} are carried and {quantity} more would make {held + quantity}"
        )
    paid = ""
    if purchase and item_id:
        try:
            # A player's session buys nothing before the wealth roll, as the
            # sheet does not (issue #851); a purchase is a player's.
            if catalog_price(item_id) > 0:
                hold_purchase_to_wealth(character, is_game_master=False)
            paid = f"; {charge_for_catalog_item(character, item_id, quantity)}"
        except PurseError as refusal:
            raise CharacterChangeError(str(refusal)) from refusal
    if carried_item is not None:
        carried_item["quantity"] = held + quantity
        character.equipment = equipment
        stored_id = str(carried_item.get("item_id", ""))
        called = (
            get_item_name(stored_id) if stored_id else carried_item.get("name", name)
        )
        return f"now carrying {carried_item['quantity']} × {called}{paid}"
    # A catalog item is stored the way the sheet stores one, so what the
    # Game Master hands over weighs and fights like the thing it is named
    # after; anything the catalog does not sell stays a custom entry.
    if item_id:
        equipment.append({"item_id": item_id, "quantity": quantity})
        name = get_item_name(item_id)
    else:
        equipment.append({"name": name, "quantity": quantity})
    character.equipment = equipment
    return f"picked up {quantity} × {name}{paid}"


#: The background fields a Games Master records as changes (issue #571).
BACKGROUND_FIELDS: tuple[str, ...] = ("provenance_class", "location", "wealth_level")


def apply_name(character: Character, name: str) -> str:
    """Rename the character. The record keeps its id.

    Held to the one name rule, ``Character.clean_field_value("name", ...)``,
    as the sheet's rename, the creation page and the import are (issue #870).

    Raises:
        ValidationError: The name breaks the rule.
    """
    character.name = str(Character.clean_field_value("name", name))
    return f"named {character.name}"


# ---------------------------------------------------------------------------
# The locked calls: lock, apply, save, append to history.
# ---------------------------------------------------------------------------


def _single(
    character_id: int,
    field_name: str,
    apply: Callable[[Character], str],
    *,
    actor: User | None,
    authorize: Callable[[Character], None] | None,
) -> ChangeResult:
    with changing(character_id, actor=actor, authorize=authorize) as change:
        note = apply(change.character)
        change.note(field_name, "", note)
    return ChangeResult(change.character, note)


def set_attribute(
    character_id: int,
    name: str,
    value: int,
    *,
    actor: User | None = None,
    is_game_master: bool = False,
    authorize: Callable[[Character], None] | None = None,
) -> ChangeResult:
    """Record one attribute, or all six with ``name="all"``."""
    return _single(
        character_id,
        name,
        lambda character: apply_recorded_attributes(
            character, name, value, is_game_master=is_game_master
        ),
        actor=actor,
        authorize=authorize,
    )


def roll_fatigue(
    character_id: int,
    *,
    value: int | None = None,
    actor: User | None = None,
    authorize: Callable[[Character], None] | None = None,
    once_for_players: bool = True,
    is_game_master: bool = False,
) -> ChangeResult:
    """Roll (or record a stated) Fatigue roll.

    Args:
        once_for_players: Refuse a player a second roll (issue #590). On by
            default (issue #748), so a new caller is held unless it says
            otherwise.
        is_game_master: Whether a Game Master asked, who may roll again.
    """

    def apply(character: Character) -> str:
        if once_for_players:
            check_first_roll(
                character.fatigue_roll is not None,
                "The Fatigue roll is",
                is_game_master,
            )
        return apply_fatigue_roll(character, value)

    return _single(
        character_id, "fatigue_roll", apply, actor=actor, authorize=authorize
    )


def apply_origin_to_literacy(
    character: Character, old_origin: str
) -> list[tuple[str, object, str]]:
    """Follow an Origin change through to the literacy record (#741, #743).

    Spencer's ruling: when a Game Master changes the Origin of a character
    whose literacy has been decided, literacy is rolled again against the new
    origin's check, through :func:`apply_literacy_roll`, and the player's
    Origin lock survives. "Decided" is a roll ever made, or a Game Master's
    ruling at "Consult GM" (#743): a ruling belongs to "Consult GM", so a move
    to an origin with a check withdraws it and rolls the check. A new origin
    of "Consult GM" publishes no check, so there is nothing to roll: a stored
    roll is cleared and the Game Master decides, while ``literacy_rolled``
    stays set so the player is still held to the roll they had. Nothing
    happens before any roll or ruling, or when the origin did not actually
    change. A player never reaches here with a decided check:
    :func:`change_field` refuses them first.

    Args:
        character: The character, its ``location`` already changed.
        old_origin: The origin it had before the change.

    Returns:
        The history lines, as ``(field, old value, line)``: the ruling's
        withdrawal under ``literacy_ruling``, the roll's under
        ``literacy_roll``.
    """
    new_origin = character.location
    if new_origin == old_origin:
        return []
    because = f"because Origin changed from {old_origin} to {new_origin}"
    has_check = character.location_literacy_modifier is not None
    lines: list[tuple[str, object, str]] = []
    ruling = character.literacy_ruling
    if has_check and ruling is not None:
        character.literacy_ruling = None
        lines.append(
            (
                "literacy_ruling",
                ruling,
                f"the Game Master's ruling "
                f"({'literate' if ruling else 'illiterate'}) withdrawn {because}, "
                "which has a check to roll",
            )
        )
    roll_before = character.literacy_roll
    if has_check and (character.literacy_rolled or ruling is not None):
        note = apply_literacy_roll(character, is_game_master=True)
        lines.append(
            ("literacy_roll", roll_before, f"literacy rerolled {because}: {note}")
        )
    elif not has_check and character.literacy_roll is not None:
        character.literacy_roll = None
        lines.append(
            (
                "literacy_roll",
                roll_before,
                f"literacy roll {roll_before} cleared {because}, which publishes "
                "no check; the Game Master decides whether this character can "
                "read",
            )
        )
    return lines


def _grant_skills(origin: str) -> tuple[str, ...]:
    """The skills ``origin``'s grant may be held in, the granted one first."""
    return tuple(
        normalize_skill_name(name) for name in LOCATION_SKILL_CHOICES.get(origin, ())
    )


def held_origin_skill(origin: str, skills: object) -> str:
    """The skill ``skills`` holds the ``origin``'s grant in, or ``""``.

    For a character whose grant was never recorded (``origin_skill``): the
    first skill its Origin's grant may be held in that it has, the rule
    migration ``0021`` applies to existing characters (it keeps a frozen copy
    of it). A Game Master's import of a file written before #754 comes here,
    so its grant is not given a second time on the next Origin change.

    Args:
        origin: The Origin (``location``).
        skills: The character's skills; anything but a mapping holds nothing.
    """
    if not isinstance(skills, Mapping):
        return ""
    held = {normalize_skill_name(str(name)) for name in skills}
    for choice in _grant_skills(origin):
        if choice in held:
            return choice
    return ""


def apply_origin_skill(
    character: Character, old_origin: str | None
) -> list[tuple[str, object, str]]:
    """Move the point the Origin grants to follow the Origin (issue #754).

    location.md: "City: Street smarts ... Village: Street smarts OR survival
    (choose) ... Rural: Survival", one point each (``LOCATION_SKILL_POINTS``).
    The grant was made at creation and never again, so a City character moved
    to Rural kept Street Smarts and got no Survival.

    The grant is on the record (``Character.origin_skill``), so exactly that
    point moves: it is taken off the skill holding it, and the new Origin's
    point is added, to a skill the character may already have bought points
    in. A grant the new Origin gives too stays where it is: City and Village
    share Street Smarts, and a Village character who took Survival holds the
    Rural grant already.

    The rule holds for whoever may move the Origin: a player before literacy
    is rolled (``can_change_origin``), a Game Master at any time.

    Args:
        character: The character, its ``location`` already changed.
        old_origin: The Origin before the change, or ``None`` when nobody had
            rolled or chosen one (``location_recorded`` was off), which is the
            first time an Origin is set even when it names the same one.

    Returns:
        The history lines, as ``(field, old value, line)``, all under
        ``skills``.
    """
    new_origin = character.location
    if old_origin == new_origin:
        return []
    new_choices = _grant_skills(new_origin)
    held = character.origin_skill
    skills = dict(character.skills or {})
    if held and held in new_choices and held in skills:
        return []
    lines: list[tuple[str, object, str]] = []
    if held and held in skills:
        before = dict(skills)
        skills[held] -= LOCATION_SKILL_POINTS
        if skills[held] <= 0:
            del skills[held]
        lines.append(
            (
                "skills",
                before,
                f"{held} {LOCATION_SKILL_POINTS} taken back: the "
                f"{old_origin or 'earlier'} origin's grant, which the "
                f"{new_origin} origin does not give",
            )
        )
    character.origin_skill = ""
    if new_choices:
        before = dict(skills)
        granted = new_choices[0]
        skills[granted] = skills.get(granted, 0) + LOCATION_SKILL_POINTS
        character.origin_skill = granted
        lines.append(
            (
                "skills",
                before,
                f"{granted} {LOCATION_SKILL_POINTS} granted by the {new_origin} origin",
            )
        )
    character.skills = skills
    return lines


def follow_origin(
    character: Character, old_origin: str, *, was_recorded: bool
) -> list[tuple[str, object, str]]:
    """Follow an Origin change to what hangs on it: the skill and literacy.

    The one place all three are followed, so the editor, the chat's
    ``set_background`` and the Origin roll cannot follow one and not the
    other (issues #741, #754), and the Game Master's origin ruling (#801).

    Args:
        character: The character, its ``location`` already changed.
        old_origin: The Origin before the change.
        was_recorded: Whether anybody had rolled or chosen that Origin.

    Returns:
        The history lines, as ``(field, old value, line)``.
    """
    return [
        *apply_origin_skill(character, old_origin if was_recorded else None),
        *apply_origin_to_literacy(character, old_origin),
        *withdraw_origin_ruling(character),
    ]


def withdraw_origin_ruling(character: Character) -> list[tuple[str, object, str]]:
    """Drop the Game Master's origin ruling off "Consult GM" (issue #801).

    The ruling says what a Consult GM origin is, so it belongs to that row,
    as the literacy ruling does (#743); an Origin moved to a row of the
    table, chosen or rolled, withdraws it.

    Returns:
        The history line, as ``(field, old value, line)``, or nothing.
    """
    ruling = character.origin_ruling
    if not ruling or character.location == "Consult GM":
        return []
    character.origin_ruling = ""
    return [
        (
            "origin_ruling",
            ruling,
            f"the Game Master's origin ruling ({ruling}) withdrawn because "
            f"Origin changed to {character.location}",
        )
    ]


def change_field(
    change: Change, field_name: str, value: object, *, is_game_master: bool
) -> list[str]:
    """Set one validated field on a locked row, with its history (#741).

    The one place the Origin lock and a Game Master's literacy reroll are
    enforced, so every way the Origin changes is held to them: the editor's
    :func:`update_field` and the Games Master's :func:`set_background` both
    come through here.

    Args:
        change: The change in progress.
        field_name: A field ``Character.clean_field_value`` has validated.
        value: Its cleaned value.
        is_game_master: Whether a Game Master asked. A player may not change
            the Origin once literacy is rolled (:func:`can_change_origin`); a
            Game Master's change rolls it again (:func:`apply_origin_to_literacy`)
            as its own history line.

    Returns:
        The history lines, one for the field and one for each change to the
        literacy record.

    Raises:
        CharacterChangeError: A field this asker may not set
            (:func:`check_field_author`, issue #748), or a player changing a
            rolled character's Origin.
    """
    character = change.character
    check_field_author(character, field_name, is_game_master=is_game_master)
    check_player_choice(field_name, value, is_game_master=is_game_master)
    if field_name == "location" and not can_change_origin(
        character, is_game_master=is_game_master
    ):
        raise CharacterChangeError(ORIGIN_FIXED_REASON)
    if field_name == "origin_ruling" and value and character.location != "Consult GM":
        raise CharacterChangeError(
            f"The {character.location} origin is a row of the table; a Game "
            "Master's origin ruling is for an origin of Consult GM"
        )
    if field_name == "total_xp":
        old_xp = character.total_xp
        points_before = character.free_skill_points
        granted = apply_xp_change(character, int(str(value)))
        change.note(field_name, old_xp, value)
        notes = [f"{field_name} set to {value}"]
        if granted:
            change.note("free_skill_points", points_before, character.free_skill_points)
            notes.append(granted)
        return notes
    beyond = ""
    if field_name in ATTRIBUTE_NAMES and is_game_master:
        # The same limits the Games Master's stated scores are held to.
        beyond = check_attribute(field_name, int(str(value)), is_game_master)
    elif field_name in ATTRIBUTE_NAMES:
        _append_dice_log(
            character, check_point_buy(character, field_name, int(str(value)))
        )
    origin_was_recorded = character.location_recorded
    old = character.apply_field_change(field_name, value)
    if field_name in SECRET_FIELDS:
        # The history is the player's to read (Recent Changes): it records
        # that the Game Master wrote something, never what (#810).
        change.note(SECRET_HISTORY_FIELD, "", SECRET_HISTORY_NOTE)
        return [SECRET_HISTORY_NOTE]
    change.note(field_name, old, value)
    notes = [f"{field_name} set to {value}{beyond}"]
    if field_name == "location":
        # Chosen is on the record as much as rolled (location.md: "Roll
        # percentile dice or choose").
        character.location_recorded = True
        for followed_field, before, line in follow_origin(
            character, str(old), was_recorded=origin_was_recorded
        ):
            change.note(followed_field, before, line)
            notes.append(line)
    if field_name in ("provenance_class", "provenance_detail"):
        character.provenance_recorded = True
    if field_name == "wealth_level":
        coin_before = character.starting_coin
        coin_note = apply_wealth_to_coin(character)
        change.note("starting_coin", coin_before, character.starting_coin)
        notes.append(coin_note)
    return notes


def _append_dice_log(character: Character, line: str) -> None:
    """Add a line to the character's dice log, where every roll is kept."""
    character.dice_log = "\n".join(part for part in (character.dice_log, line) if part)


def _format_coin(coin: int | None) -> str:
    return "Consult GM" if coin is None else str(coin)


def apply_wealth_to_coin(character: Character) -> str:
    """Follow a Game Master's wealth setting through to the coin (issue #749).

    Spencer's ruling: starting coin is rolled once, when wealth is first
    recorded, and is never rolled again by re-selecting the same or another
    level; a Game Master may reroll it explicitly
    (:func:`apply_starting_coin_roll`) or type it. So the first recorded level
    sets the coin from the wealth table (a Merchant's d100 into the dice log,
    a Rich character's "Consult GM" as no number), and any later change keeps
    the coin as it is.

    Args:
        character: The character, its ``wealth_level`` already changed.

    Returns:
        The history line saying what happened to the coin.
    """
    if character.wealth_recorded:
        return (
            f"starting coin kept at {_format_coin(character.starting_coin)}; "
            "a Game Master may reroll it"
        )
    coin, detail = starting_coin_for_wealth(character.wealth_level)
    character.starting_coin = coin
    character.wealth_recorded = True
    _append_dice_log(
        character,
        f"Wealth: set to {character.wealth_level}, starting coin = {detail}",
    )
    return f"starting coin set to {detail}"


def apply_provenance_roll(character: Character, *, is_game_master: bool = False) -> str:
    """Roll the provenance tables and record the result (issue #749).

    provenance.md: "Roll percentile dice or choose Merchant/Commoner. You must
    roll for Nobility". Spencer's ruling (2026-09-28) closes the choice: a
    player rolls provenance once and cannot choose it; a Game Master may set it
    or roll again.

    Raises:
        CharacterChangeError: A player asking for a provenance already recorded.
    """
    check_first_roll(character.provenance_recorded, "Provenance is", is_game_master)
    social_class, detail, log = roll_provenance_logged()
    character.provenance_class = social_class
    character.provenance_detail = detail
    character.provenance_recorded = True
    _append_dice_log(character, log)
    return f"rolled provenance: {log}"


def apply_wealth_roll(character: Character, *, is_game_master: bool = False) -> str:
    """Roll the wealth table, and the coin with it, once (issue #749).

    wealth.md: "Roll percentile dice (or choose if you don't want to be
    rich)"; Merchant coin is 100 + percentile dice and Rich is "Consult GM".
    Spencer's ruling: a player rolls wealth and cannot choose it; the coin is
    rolled with it and never again by re-selecting; a Game Master may roll
    again.

    Raises:
        CharacterChangeError: A player asking for a wealth already recorded.
    """
    check_first_roll(character.wealth_recorded, "Wealth is", is_game_master)
    level, coin, log = roll_wealth_logged()
    character.wealth_level = level
    character.starting_coin = coin
    character.wealth_recorded = True
    _append_dice_log(character, log)
    return f"rolled wealth: {log}"


def apply_origin_roll(character: Character, *, is_game_master: bool = False) -> str:
    """Roll the location table for the Origin and follow it (issue #754).

    location.md: "Roll percentile dice or choose". The dice log gets the
    creation page's two lines, the roll and the Origin's effects; the skill
    and literacy follow as they do for a chosen Origin (:func:`follow_origin`).

    A player rolls an Origin nobody has recorded, once (the #636 rule, as
    for provenance and wealth); one already rolled or chosen they may choose
    again on the sheet, before literacy fixes it. A Game Master may roll again.

    Raises:
        CharacterChangeError: A player whose Origin is fixed by a literacy
            roll or ruling (:func:`can_change_origin`), or already recorded.
    """
    if not can_change_origin(character, is_game_master=is_game_master):
        raise CharacterChangeError(ORIGIN_FIXED_REASON)
    check_first_roll(character.location_recorded, "The origin is", is_game_master)
    old_origin = character.location
    was_recorded = character.location_recorded
    location, log = roll_location_logged()
    character.location = location
    character.location_recorded = True
    _append_dice_log(character, log)
    followed = follow_origin(character, old_origin, was_recorded=was_recorded)
    granted = any(
        line.endswith(f"granted by the {location} origin") for *_, line in followed
    )
    # The creation page's effects line when the grant was made here; when the
    # Origin's grant was already held (a reroll onto the same Origin, or one
    # sharing it), the log says the point stayed rather than claiming a new one.
    if granted or not character.origin_skill:
        _append_dice_log(character, location_effects(location)[1])
    else:
        _append_dice_log(
            character,
            f"Location effects: {character.origin_skill} {LOCATION_SKILL_POINTS} "
            f"point kept from before, {literacy_check_text(location)}",
        )
    return "; ".join([f"rolled the origin: {log}", *(line for *_, line in followed)])


#: Why a player's second background roll is refused (issue #754, the #636
#: one-roll rule, and Spencer's rulings on #749).
BACKGROUND_ALREADY_ROLLED = (
    "The background (origin, provenance and wealth) is already recorded; only "
    "a Game Master can roll it again"
)


def apply_background_roll(
    character: Character, *, is_game_master: bool = False
) -> list[str]:
    """Roll origin, provenance and wealth, as the creation page does (#754).

    The same table functions and dice-log lines as
    ``dice.roll_secondary_character_stats``, in its order: provenance, origin
    (with its effects and skill), wealth (with its coin). A player rolls each
    part nobody has recorded, once; an Origin fixed by literacy is left for a
    Game Master. A Game Master rolls all three again.

    Args:
        character: The character.
        is_game_master: Whether a Game Master asked.

    Returns:
        One note per roll made.

    Raises:
        CharacterChangeError: A player with nothing left to roll.
    """
    roll_provenance = is_game_master or not character.provenance_recorded
    roll_origin = is_game_master or not character.location_recorded
    roll_wealth = is_game_master or not character.wealth_recorded
    origin_fixed = roll_origin and not can_change_origin(
        character, is_game_master=is_game_master
    )
    roll_origin = roll_origin and not origin_fixed
    if not (roll_provenance or roll_origin or roll_wealth):
        raise CharacterChangeError(
            ORIGIN_FIXED_REASON if origin_fixed else BACKGROUND_ALREADY_ROLLED
        )
    notes = []
    if roll_provenance:
        notes.append(apply_provenance_roll(character, is_game_master=is_game_master))
    if roll_origin:
        notes.append(apply_origin_roll(character, is_game_master=is_game_master))
    if roll_wealth:
        notes.append(apply_wealth_roll(character, is_game_master=is_game_master))
    return notes


def apply_starting_coin_roll(
    character: Character, *, is_game_master: bool = False
) -> str:
    """Reroll the starting coin for the recorded wealth level (issue #749).

    Only a Game Master may: the ruling rolls the coin once, and this is the
    explicit reroll it leaves them.

    Raises:
        CharacterChangeError: A player, or a level whose coin the table does
            not give (Rich, "Consult GM"), which a Game Master types instead.
    """
    if not is_game_master:
        raise CharacterChangeError(
            "The starting coin is rolled once with the wealth; only a Game "
            "Master can have it rolled again"
        )
    if not wealth_gives_coin(character.wealth_level):
        raise CharacterChangeError(
            f"The wealth table gives {character.wealth_level} no coin to roll "
            "(Consult GM); type the Game Master's figure instead"
        )
    coin, detail = starting_coin_for_wealth(character.wealth_level)
    character.starting_coin = coin
    _append_dice_log(character, f"Starting coin rerolled: {detail}")
    return f"starting coin rerolled: {detail}"


def apply_appearance_roll(character: Character, *, is_game_master: bool = False) -> str:
    """Roll the 1d6 appearance and record it (issue #748).

    attributes.md: "Roll 1d6: 1 is pretty, 6 is ugly. If you roll a 1 or 6,
    ask the GM to further qualify." A player rolls once; a Game Master may
    roll again, or qualify the result by typing it.

    Raises:
        CharacterChangeError: A player asking for an appearance already rolled.
    """
    check_first_roll(bool(character.appearance_roll), "Appearance is", is_game_master)
    value, log = roll_d6_logged()
    character.appearance_roll = str(value)
    _append_dice_log(character, f"Appearance: {log}")
    note = f"rolled appearance, {log}"
    if value in (1, 6):
        note += "; a 1 or a 6 is for the Game Master to qualify"
    return note


def apply_sheet_attribute_roll(
    character: Character, *, is_game_master: bool = False
) -> str:
    """The sheet's attribute roll: the missing scores, or all six again.

    A character with scores missing (#571) rolls 3d6 for each missing one:
    the rules roll a score nobody has recorded. A player who has bought any
    score is held to the buy instead (:func:`bought_scores`). With all six
    recorded it is :func:`apply_rolled_attributes`, which only a Game Master
    may ask for. The chat's roll keeps its own all-or-nothing rule.
    """
    missing = [name for name in ATTRIBUTE_NAMES if getattr(character, name) is None]
    if not missing:
        return apply_rolled_attributes(character, is_game_master=is_game_master)
    if bought_scores(character) and not is_game_master:
        raise CharacterChangeError(BOUGHT_NOT_ROLLED_REASON)
    rolled = []
    for name in missing:
        total, log = roll_3d6_logged()
        setattr(character, name, total)
        rolled.append(f"{name} {log}")
        _append_dice_log(character, f"{name}: {log}")
    return "rolled the missing attributes: " + "; ".join(rolled)


@dataclass(frozen=True)
class SheetRoll:
    """One roll the character sheet offers (issues #748, #749).

    Attributes:
        fields: The fields the roll writes; each one's old and new value goes
            into the history, so a Game Master's reroll keeps what it replaced.
        apply: The rule, as ``apply(character, is_game_master=...)``.
        may_roll: Whether the page should offer it to this asker now; the
            rule enforces the same limit, so a hidden control is never the
            only guard.
        rerolls: Whether rolling now replaces a recorded value, which the page
            asks a Game Master to confirm first.
        confirm: What the page asks when it does.
    """

    fields: tuple[str, ...]
    apply: Callable[..., str]
    may_roll: Callable[[Character, bool], bool]
    rerolls: Callable[[Character], bool]
    confirm: str


@dataclass(frozen=True)
class RollOffer:
    """What the page offers for one roll.

    Attributes:
        available: Render the control.
        confirm: The question to ask before rolling, when the roll would
            replace a recorded value; empty otherwise.
    """

    available: bool
    confirm: str


#: The rolls the sheet offers besides Fatigue and literacy, which keep their
#: own endpoints (#636, #741). A player rolls each once; a Game Master may
#: roll again.
SHEET_ROLLS: Mapping[str, SheetRoll] = MappingProxyType(
    {
        "attributes": SheetRoll(
            ATTRIBUTE_NAMES,
            apply_sheet_attribute_roll,
            lambda character, is_game_master: is_game_master
            or not (attributes_known(character) or bought_scores(character)),
            attributes_known,
            "Roll all six attributes again? The recorded scores are replaced; "
            "the history keeps them.",
        ),
        "appearance": SheetRoll(
            ("appearance_roll",),
            apply_appearance_roll,
            lambda character, is_game_master: is_game_master
            or not character.appearance_roll,
            lambda character: bool(character.appearance_roll),
            "Roll appearance again? The recorded appearance is replaced; "
            "the history keeps it.",
        ),
        "provenance": SheetRoll(
            ("provenance_class", "provenance_detail"),
            apply_provenance_roll,
            lambda character, is_game_master: is_game_master
            or not character.provenance_recorded,
            lambda character: character.provenance_recorded,
            "Roll provenance again? The recorded class and detail are replaced; "
            "the history keeps them.",
        ),
        # location.md: "Roll percentile dice or choose" (issue #754). A player
        # rolls an Origin nobody rolled or chose, unless literacy fixes it; a
        # Game Master may roll again. The skill and literacy follow.
        "origin": SheetRoll(
            ("location", "skills", "literacy_roll", "literacy_ruling", "origin_ruling"),
            apply_origin_roll,
            lambda character, is_game_master: is_game_master
            or (
                not character.location_recorded
                and can_change_origin(character, is_game_master=False)
            ),
            lambda character: character.location_recorded,
            "Roll the origin again? The recorded origin is replaced, its skill "
            "moves, and a rolled literacy check is rolled again; the history "
            "keeps them.",
        ),
        "wealth": SheetRoll(
            ("wealth_level", "starting_coin"),
            apply_wealth_roll,
            lambda character, is_game_master: is_game_master
            or not character.wealth_recorded,
            lambda character: character.wealth_recorded,
            "Roll wealth again? The recorded level and coin are replaced; "
            "the history keeps them.",
        ),
        "starting_coin": SheetRoll(
            ("starting_coin",),
            apply_starting_coin_roll,
            lambda character, is_game_master: is_game_master
            and wealth_gives_coin(character.wealth_level),
            lambda character: True,
            "Roll the starting coin again? The recorded coin is replaced; "
            "the history keeps it.",
        ),
    }
)


def sheet_rolls_for(
    character: Character, *, is_game_master: bool
) -> dict[str, RollOffer]:
    """What the page offers this asker for each of :data:`SHEET_ROLLS` now."""
    return {
        kind: RollOffer(
            available=roll.may_roll(character, is_game_master),
            confirm=roll.confirm if roll.rerolls(character) else "",
        )
        for kind, roll in SHEET_ROLLS.items()
    }


def _history_value(value: object) -> object:
    """A value as the history prints it: an unset one is empty, not "None"."""
    return "" if value is None else value


def roll_on_sheet(
    character_id: int,
    kind: str,
    *,
    actor: User | None = None,
    authorize: Callable[[Character], None] | None = None,
    is_game_master: bool = False,
) -> ChangeResult:
    """Make one of the sheet's rolls on a saved character.

    Raises:
        CharacterChangeError: An unknown roll, or one the rules refuse.
    """
    roll = SHEET_ROLLS.get(kind)
    if roll is None:
        raise CharacterChangeError(f"There is no {kind} roll on the sheet")
    with changing(character_id, actor=actor, authorize=authorize) as change:
        character = change.character
        before = {name: getattr(character, name) for name in roll.fields}
        note = roll.apply(character, is_game_master=is_game_master)
        # One line per value the roll moved, old and new, so a Game Master's
        # reroll of a recorded value keeps what it replaced; the dice are in
        # the dice log.
        for name in roll.fields:
            change.note(
                name,
                _history_value(before[name]),
                _history_value(getattr(character, name)),
            )
    return ChangeResult(change.character, note)


#: What a background roll can move, each kept in the history old and new.
BACKGROUND_ROLL_FIELDS: tuple[str, ...] = (
    "provenance_class",
    "provenance_detail",
    "location",
    "skills",
    "literacy_roll",
    "literacy_ruling",
    "origin_ruling",
    "wealth_level",
    "starting_coin",
)


def roll_background(
    character_id: int,
    *,
    actor: User | None = None,
    authorize: Callable[[Character], None] | None = None,
    is_game_master: bool = False,
) -> ChangeResult:
    """Roll a saved character's background (issue #754).

    See :func:`apply_background_roll` for the rule. The Games Master's
    ``roll_background`` action comes here.
    """
    with changing(character_id, actor=actor, authorize=authorize) as change:
        character = change.character
        before = {name: getattr(character, name) for name in BACKGROUND_ROLL_FIELDS}
        notes = apply_background_roll(character, is_game_master=is_game_master)
        for name in BACKGROUND_ROLL_FIELDS:
            change.note(
                name,
                _history_value(before[name]),
                _history_value(getattr(character, name)),
            )
    return ChangeResult(change.character, "; ".join(notes))


def roll_literacy(
    character_id: int,
    *,
    actor: User | None = None,
    authorize: Callable[[Character], None] | None = None,
    is_game_master: bool = False,
) -> ChangeResult:
    """Roll the literacy check on a saved character (issue #741).

    See :func:`apply_literacy_roll` for the rule. A player rolls once; a Game
    Master may roll again.
    """
    return _single(
        character_id,
        "literacy_roll",
        lambda character: apply_literacy_roll(character, is_game_master=is_game_master),
        actor=actor,
        authorize=authorize,
    )


def rule_literacy(
    character_id: int,
    literate: bool | None,
    *,
    actor: User | None = None,
    authorize: Callable[[Character], None] | None = None,
    is_game_master: bool = False,
) -> ChangeResult:
    """Record the Game Master's literacy ruling on a saved character (#743).

    See :func:`apply_literacy_ruling` for the rule. The editor's ruling
    control and the Games Master's ``rule_literacy`` action both come here.
    """
    return _single(
        character_id,
        "literacy_ruling",
        lambda character: apply_literacy_ruling(
            character, literate, is_game_master=is_game_master
        ),
        actor=actor,
        authorize=authorize,
    )


#: Why there is no survival save to roll (issue #839).
NO_SURVIVAL_SAVE_DUE = (
    "No survival save is due: that is rolled only while a pool is at or below "
    "minus half its starting value"
)

#: Why a player rolls no second save against the same wounds (#839 review).
SURVIVAL_SAVE_ALREADY_ROLLED = (
    "The survival save for these wounds is already rolled; the next is rolled "
    "when the pools change, or when a Game Master calls for one"
)

#: Why the sheet rolls no save for a character fighting on the board.
SURVIVAL_SAVE_ON_THE_BOARD = (
    "is fighting on the board, which rolls the survival save each turn itself"
)

#: The history field a survival save is kept under.
SURVIVAL_SAVE_FIELD = "survival save"


def _seated_in_a_live_fight(character: Character) -> bool:
    """Whether a live record-writing fight has this character on its board.

    Read through the reverse relation, so this module needs no import of the
    battle app. A lobby has no board yet; a character its owner withdrew from
    the fight's session has left the board (#871 review).
    """
    live = character.battle_combatants.filter(
        battle__writes_to_records=True, battle__is_deleted=False
    ).exclude(battle__status__in=("lobby", "finished", "stalemate"))
    held = Q(battle__gm_sessions__isnull=True) | Q(
        battle__gm_sessions__characters=character
    )
    return live.filter(held).exists()


def _last_save_against(character: Character) -> str | None:
    """The pools the last recorded survival save was rolled against, or None."""
    for entry in reversed(character.history or []):
        if entry.get("field") == SURVIVAL_SAVE_FIELD:
            return entry.get("old")
    return None


def survival_save_refusal(character: Character, *, is_game_master: bool) -> str | None:
    """Why the sheet may not roll a survival save now, or None (#839 review).

    The review's ruling under the standing rule (Spencer may overrule): a
    player rolls one save for each state of the pools, refused until the
    pools change; a Game Master may call further saves; and nobody rolls one
    from the sheet while the character is on a live record-writing board,
    whose own phase 6 rolls it.

    Returns:
        The refusal, or None when a save may be rolled.
    """
    condition = character.condition
    if (
        not character.is_alive
        or condition is None
        or condition.survival_save_penalty is None
        or condition.survival_save_target is None
    ):
        return NO_SURVIVAL_SAVE_DUE
    if _seated_in_a_live_fight(character):
        return f"{character.name} {SURVIVAL_SAVE_ON_THE_BOARD}"
    if not is_game_master and _last_save_against(character) == condition.pools:
        return SURVIVAL_SAVE_ALREADY_ROLLED
    return None


def apply_survival_save(
    character: Character, roller: Roller | None = None, *, is_game_master: bool = False
) -> tuple[str, str]:
    """Roll one survival save off the board (issue #839).

    The rule, from
    ``reference/content/public-rules/characters/injury-thresholds-death.md``:
    at minus half a pool's starting value the character "makes a survival
    save (3d6 ≤ CON) every turn", penalized past minus the full starting
    value by how far past it is, and "Survival saves continue each turn until
    the character is healed above the threshold or fails a save and dies".
    The board rolls it in its own phase 6; this is the same save for a
    character the record says is dying off the board.

    Code owns the dice (Spencer's ruling: a survival save is rolled once, by
    code, and recorded). Each call is one save, rolled once, into the dice
    log; a failure marks the character dead, which only a Game Master's
    revive undoes. What limits the calls is :func:`survival_save_refusal`.

    Args:
        character: The locked character.
        roller: Roll through this roller instead of the dice module default.
        is_game_master: Whether a Game Master called the save.

    Returns:
        ``(pools, line)``: the pools the save was rolled against, and the
        save's line, dice, target and outcome.

    Raises:
        CharacterChangeError: :func:`survival_save_refusal`'s reason.
    """
    refusal = survival_save_refusal(character, is_game_master=is_game_master)
    if refusal is not None:
        raise CharacterChangeError(refusal)
    condition = character.condition
    assert condition is not None and condition.survival_save_target is not None
    penalty = condition.survival_save_penalty or 0
    target = condition.survival_save_target + penalty
    total, log = roll_3d6_logged(roller)
    against = f"CON {condition.survival_save_target}" + (
        f" {penalty}" if penalty else ""
    )
    survived = total <= target
    line = f"survival save, {log} {'≤' if survived else '>'} {target} ({against}): " + (
        "survives this turn" if survived else "fails, and dies"
    )
    _append_dice_log(character, f"Survival save at {condition.pools}: {log}")
    if not survived:
        character.is_alive = False
        character.death_cause = f"Failed a survival save ({total} > {target})"
    return condition.pools, line


def roll_survival_save(
    character_id: int,
    *,
    actor: User | None = None,
    authorize: Callable[[Character], None] | None = None,
    roller: Roller | None = None,
    is_game_master: bool = False,
) -> ChangeResult:
    """Roll a dying character's survival save off the board (issue #839).

    See :func:`apply_survival_save`. Whoever may edit the character rolls it:
    the owner for their own dying character, and a Game Master for any. The
    history line keeps the pools it was rolled against (its old value), which
    is what holds a player to one save for each state of them. Not marked as
    played, like a fight's wounds: the save is the rules acting.
    """
    with changing(
        character_id, actor=actor, authorize=authorize, mark_played=False
    ) as change:
        pools, line = apply_survival_save(
            change.character, roller, is_game_master=is_game_master
        )
        change.note(SURVIVAL_SAVE_FIELD, pools, line)
        if not change.character.is_alive:
            change.note("is_alive", True, False)
    return ChangeResult(change.character, line)


def dismiss_import_notice(
    character_id: int,
    *,
    actor: User | None = None,
    authorize: Callable[[Character], None] | None = None,
) -> ChangeResult:
    """Clear what a player's import said it dropped (issue #744).

    Reading a notice is not playing the character, so this does not mark it
    played; the history keeps the notice that was dismissed.
    """
    with changing(
        character_id, actor=actor, authorize=authorize, mark_played=False
    ) as change:
        old = change.character.import_notice
        change.character.import_notice = ""
        change.note("import_notice", old, "dismissed")
    return ChangeResult(change.character, "import notice dismissed")


def record_attributes(
    character_id: int,
    scores: Mapping[str, int],
    *,
    actor: User | None = None,
    is_game_master: bool = False,
    authorize: Callable[[Character], None] | None = None,
) -> ChangeResult:
    """Record stated scores in one change, all or none (issue #590).

    Args:
        scores: Attribute names to scores; ``"all"`` sets all six.
    """
    with changing(character_id, actor=actor, authorize=authorize) as change:
        note = "; ".join(
            apply_recorded_attributes(
                change.character, name, value, is_game_master=is_game_master
            )
            for name, value in scores.items()
        )
        change.note("attributes", "", note)
    return ChangeResult(change.character, note)


def roll_attributes(
    character_id: int,
    *,
    actor: User | None = None,
    is_game_master: bool = False,
    authorize: Callable[[Character], None] | None = None,
) -> ChangeResult:
    """Roll and record all six attributes (issue #590)."""
    return _single(
        character_id,
        "attributes",
        lambda character: apply_rolled_attributes(
            character, is_game_master=is_game_master
        ),
        actor=actor,
        authorize=authorize,
    )


def revive(
    character_id: int,
    *,
    actor: User | None = None,
    authorize: Callable[[Character], None] | None = None,
) -> ChangeResult:
    """Bring a dead character back to life."""
    return _single(
        character_id, "is_alive", apply_revive, actor=actor, authorize=authorize
    )


def mark_dead(
    character_id: int,
    cause: str,
    *,
    actor: User | None = None,
    authorize: Callable[[Character], None] | None = None,
    is_game_master: bool = False,
) -> ChangeResult:
    """Record a character's death outside a fight (issue #802).

    Only a Game Master marks a death (``FIELD_RULES``); nobody is marked as
    having played the character, as on the sheet.

    Raises:
        CharacterChangeError: A player asking, or a character already dead.
    """
    with changing(
        character_id, actor=actor, authorize=authorize, mark_played=False
    ) as change:
        # A death is not a field the sheet's editor sets, so it is held to
        # its FIELD_RULES row here rather than through check_field_author.
        if not is_game_master:
            raise CharacterChangeError(field_refusal("is_alive"))
        note = apply_death(change.character, cause)
        change.note("is_alive", True, False)
    return ChangeResult(change.character, note)


def award_xp(
    character_id: int,
    xp: int,
    *,
    actor: User | None = None,
    authorize: Callable[[Character], None] | None = None,
    is_game_master: bool = False,
) -> ChangeResult:
    """Add XP earned in play to a character's lifetime total (issue #802).

    Through :func:`change_field`, the path the sheet's XP field takes, so
    whatever a change of XP carries with it there -- the skill points the XP
    earns (issue #769) -- it carries here too. Only a Game Master awards XP
    (``FIELD_RULES``).

    Raises:
        CharacterChangeError: A player asking.
        ValidationError: A total past what the field holds.
    """
    with changing(character_id, actor=actor, authorize=authorize) as change:
        total = Character.clean_field_value("total_xp", change.character.total_xp + xp)
        notes = change_field(change, "total_xp", total, is_game_master=is_game_master)
    return ChangeResult(change.character, f"awarded {xp} XP; " + "; ".join(notes))


def rename(
    character_id: int,
    name: str,
    *,
    actor: User | None = None,
    authorize: Callable[[Character], None] | None = None,
) -> ChangeResult:
    """Rename a character; its id, and so its identity, stay the same."""
    with changing(character_id, actor=actor, authorize=authorize) as change:
        old = change.character.name
        note = apply_name(change.character, name)
        change.note("name", old, change.character.name)
    return ChangeResult(change.character, note)


def add_equipment(
    character_id: int,
    name: str,
    quantity: int,
    *,
    actor: User | None = None,
    authorize: Callable[[Character], None] | None = None,
    purchase: bool = False,
) -> ChangeResult:
    """Add a named item to what the character carries; see ``apply_equipment``."""
    return _single(
        character_id,
        "equipment",
        lambda character: apply_equipment(character, name, quantity, purchase=purchase),
        actor=actor,
        authorize=authorize,
    )


def allocate_skill_points(
    character_id: int,
    skill: str,
    points: int = 1,
    *,
    actor: User | None = None,
    is_game_master: bool = False,
    authorize: Callable[[Character], None] | None = None,
) -> ChangeResult:
    """Spend free skill points on a skill."""
    with changing(character_id, actor=actor, authorize=authorize) as change:
        note, granted = apply_skill_points(
            change.character, skill, points, is_game_master=is_game_master
        )
        change.note("skills", "", note)
    return ChangeResult(change.character, note, granted=granted)


def add_experience_years(
    character_id: int,
    track: str,
    years: int,
    *,
    actor: User | None = None,
    is_game_master: bool = False,
    authorize: Callable[[Character], None] | None = None,
) -> ChangeResult:
    """Play a run of prior-experience years."""
    with changing(character_id, actor=actor, authorize=authorize) as change:
        note, run_result = apply_experience_years(
            change.character, track, years, is_game_master=is_game_master
        )
        change.note("experience", "", note)
    return ChangeResult(change.character, note, run_result=run_result)


def update_field(
    character_id: int,
    field_name: str,
    value: object,
    *,
    actor: User | None = None,
    authorize: Callable[[Character], None] | None = None,
    is_game_master: bool = False,
) -> ChangeResult:
    """Set one already-validated editable field (a background field, say).

    ``Character.clean_field_value`` validates; this applies the derived
    recompute the field needs and keeps the history.

    Args:
        is_game_master: Whether a Game Master asked; see :func:`change_field`
            for the Origin lock and the literacy reroll it decides.

    Raises:
        CharacterChangeError: A player changing a rolled character's Origin.
    """
    with changing(character_id, actor=actor, authorize=authorize) as change:
        notes = change_field(change, field_name, value, is_game_master=is_game_master)
    return ChangeResult(change.character, "; ".join(notes))


def set_background(
    character_id: int,
    values: Mapping[str, object],
    *,
    actor: User | None = None,
    authorize: Callable[[Character], None] | None = None,
    is_game_master: bool = False,
) -> ChangeResult:
    """Record background fields (provenance, location, wealth) in one change.

    Args:
        is_game_master: Whether a Game Master asked; the Origin is held to
            the same lock and reroll as the editor's (:func:`change_field`).

    Raises:
        CharacterChangeError: A player changing a rolled character's Origin;
            nothing in the change is saved.
    """
    with changing(character_id, actor=actor, authorize=authorize) as change:
        notes = []
        for field_name, value in values.items():
            cleaned = Character.clean_field_value(field_name, value)
            unrecorded_origin = (
                field_name == "location" and not change.character.location_recorded
            )
            if getattr(change.character, field_name) == cleaned and not (
                unrecorded_origin
            ):
                # Restating a value is not changing it: an unchanged wealth
                # beside a new Origin is not a player choosing wealth. An
                # Origin nobody rolled or chose is the exception: naming it
                # is the first time it is set, and grants its skill (#754).
                continue
            notes.extend(
                change_field(change, field_name, cleaned, is_game_master=is_game_master)
            )
    return ChangeResult(change.character, "; ".join(notes))


# ---------------------------------------------------------------------------
# Creating a character, and replacing one wholesale (the importer).
# ---------------------------------------------------------------------------


def create_character(
    user: User,
    name: str,
    fields: Mapping[str, Any],
    *,
    validate: Callable[[Character], None] | None = None,
) -> Character:
    """Create ``user``'s character from already-checked field values.

    Args:
        user: The owner.
        name: The name. Names need not be unique (issue #571).
        fields: Every other field value.
        validate: Called on the unsaved instance before it is saved; raises
            to refuse.

    Returns:
        The saved character.
    """
    character = Character(user=user, **{**fields, "name": name})
    if validate is not None:
        validate(character)
    character.save()
    return character
