"""Turning a ``tarmar-character`` export payload into a saved ``Character``.

The one validator for imported character data. The upload view
(``characters.views.characters.character_import``) and the GM session's
``/character`` command (issue #344) both hand a ``{"_format":
"tarmar-character", "data": {...}}`` object to :func:`validate_import_payload`
and then save through :func:`save_imported_character`, which keeps no
rules of its own, so a bound tightened here holds for every way a character
enters the database.

Names are not unique (issue #571): a character's identity is its id, so an
imported character keeps the name it was exported with.

Who imports decides what is kept (issue #744). A file is not dice: a player
could export a character, edit its numbers and import it with every roll at
its best, with any XP, skills, coin or age, or with a dead character alive.
Nor is a file this site wrote the importer's character as it stands: it may
be someone else's public character, or the player's own from before a bad
roll. Spencer's house rule holds players to the rules and lets a Game Master
override, so a Game Master's import is kept verbatim, and every player's
import arrives as a new character: :data:`PLAYER_KEPT_FIELDS` kept,
everything rolled, awarded or ruled at a fresh character's value, and a
notice naming each value dropped and why.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError

from accounts.models import User
from character_export.export_utils import (
    EXPORTABLE_FIELDS,
)

from . import services
from .constants import (
    CHARACTER_EXPORT_FORMAT,
    CONTROL_CHARACTERS_PATTERN,
    CORE_ATTRIBUTE_FIELDS,
    MAX_EQUIPMENT_MODIFIER,
    MAX_EQUIPMENT_NAME_LENGTH,
    MAX_EQUIPMENT_NOTES_LENGTH,
    MAX_EQUIPMENT_QUANTITY,
    MAX_EQUIPMENT_WEIGHT_LBS,
)
from .dice import LOCATION_LITERACY_MODIFIER, location_effects
from .equipment import get_item, settle_worn_armour
from .models import CHARACTER_INT_FIELDS, Character
from .skills import MAX_SKILL_POINTS, normalize_skill_name

_EQUIPMENT_MODIFIER_FIELDS = (
    "to_hit_mod",
    "damage_mod",
    "critical_mod",
    "stops_mod",
    "dex_mod",
)

# The JSON collections a saved character carries. ``full_clean`` skips their
# blank check (an empty list or dict is legitimate) and their size validators
# run explicitly instead.
_COLLECTION_FIELDS = ("skills", "equipment", "experience_log")


#: The key the import response carries the notice under (issue #744): the
#: text stored on the character as ``import_notice``, or ``""``.
IMPORT_NOTICE_KEY = "import_notice"

#: Exported fields a player's import does not carry: the Mana Pool, the
#: spells known and the purse's movements are granted or bought in play, and
#: a hand-edited file must not grant them (coordinator's ruling on the #757
#: review, the #744 rule). A Game Master's import keeps them as written.
GAME_MASTER_IMPORT_FIELDS: tuple[str, ...] = (
    "spells",
    "coin_adjustment",
    "free_spell_points",
)

#: Coin a player's import sets to a fresh character's, whatever the file
#: says: they feed the purse (the #757 reviews). The background is unrolled
#: on such an import (:data:`PLAYER_UNROLLED_BACKGROUND`), so the starting
#: coin is the model's unrolled value -- what a character started in a Games
#: Master session holds before its wealth roll -- and nothing is earned yet.
#: Nothing is rolled on import; the wealth roll on the sheet sets the coin.
PLAYER_FRESH_COIN_FIELDS: tuple[str, ...] = ("starting_coin", "money_earned")

#: What a player's import never keeps,
#: whatever the list below says -- written apart so that when the kept list
#: is built from the sheet's table of who may set each field, these cannot
#: come back through it (issues #744, #757):
#:
#: * ``skills``: only the skill the Origin grants a new character;
#: * ``is_alive``, ``death_cause``, ``is_played``: a player's import is a new
#:   character, alive and unplayed, since an edited file cannot be told from
#:   a resurrection. A character who died on the site stays dead there;
#:   importing a copy of them makes a new one (coordinator's ruling, for
#:   Spencer to confirm);
#: * :data:`GAME_MASTER_IMPORT_FIELDS` and :data:`PLAYER_FRESH_COIN_FIELDS`.
#:
#: Equipment is left out whatever the table says: nothing is bought before
#: the wealth roll (:data:`UNROLLED_WEALTH_BUYS_NOTHING`).
PLAYER_IMPORT_NEVER_KEPT: frozenset[str] = frozenset(
    {
        "skills",
        "is_alive",
        "death_cause",
        "is_played",
        *GAME_MASTER_IMPORT_FIELDS,
        *PLAYER_FRESH_COIN_FIELDS,
    }
)

#: What a player's import keeps (issue #744):
#: the exported fields the sheet's table (``services.FIELD_RULES``) lets a
#: player set, less :data:`PLAYER_IMPORT_NEVER_KEPT`. Built from the table, so
#: it is an allowlist: a field added to the export later is dropped for
#: players until the table says a player may set it.
PLAYER_KEPT_FIELDS: frozenset[str] = (
    frozenset(
        name
        for name in EXPORTABLE_FIELDS
        if services.FIELD_RULES[name].author is services.FieldAuthor.PLAYER
    )
    - PLAYER_IMPORT_NEVER_KEPT
)

#: A player's import arrives with its background unrolled, whatever the flags' defaults (True, for characters
#: that predate #749), so the sheet offers the player their provenance and
#: wealth rolls.
PLAYER_UNROLLED_BACKGROUND: dict[str, bool] = {
    "provenance_recorded": False,
    "wealth_recorded": False,
}


class CharacterImportError(Exception):
    """A payload that must not become a character; ``str()`` says why."""


@dataclass(frozen=True)
class ImportedCharacter:
    """A validated payload, ready to save.

    ``fields`` holds every exportable field the payload carried, coerced;
    ``name`` is the requested name, which is what the character is saved
    with. Names need not be unique (issue #571), so nothing renames it.
    """

    name: str
    fields: dict = field(default_factory=dict)
    #: What a player's import left out and why, for the history and the
    #: import's answer (issue #757).
    left_out: list[str] = field(default_factory=list)


def _validate_equipment(equipment) -> None:
    """Structurally validate imported equipment, mirroring the add/update bounds.

    The add/update endpoints carefully bound quantity/weight/modifiers; import
    must not be a bypass for those. A non-list is left to the caller's
    container check.
    """
    if not isinstance(equipment, list):
        return
    for index, item in enumerate(equipment):
        if not isinstance(item, dict):
            raise CharacterImportError(f"Equipment item {index} is not an object")
        # The add/update endpoints cap these two and drop control characters
        # from them; an import file must not be a way around that (issue #120).
        for text_field, max_length in (
            ("name", MAX_EQUIPMENT_NAME_LENGTH),
            ("notes", MAX_EQUIPMENT_NOTES_LENGTH),
        ):
            text_value = item.get(text_field)
            if text_value is None:
                continue
            if not isinstance(text_value, str) or len(text_value) > max_length:
                raise CharacterImportError(
                    f"Equipment item {index} has an invalid {text_field}"
                )
            if CONTROL_CHARACTERS_PATTERN.search(text_value):
                raise CharacterImportError(
                    f"Equipment item {index} has control characters in its {text_field}"
                )
        quantity = item.get("quantity")
        if quantity is not None and (
            not isinstance(quantity, int)
            or isinstance(quantity, bool)
            or not (1 <= quantity <= MAX_EQUIPMENT_QUANTITY)
        ):
            raise CharacterImportError(
                f"Equipment item {index} has an invalid quantity"
            )
        weight = item.get("weight")
        if weight is not None and (
            isinstance(weight, bool)
            or not isinstance(weight, int | float)
            or not (0 <= weight <= MAX_EQUIPMENT_WEIGHT_LBS)
        ):
            raise CharacterImportError(f"Equipment item {index} has an invalid weight")
        for modifier_field in _EQUIPMENT_MODIFIER_FIELDS:
            modifier = item.get(modifier_field)
            if modifier is not None and (
                not isinstance(modifier, int)
                or isinstance(modifier, bool)
                or abs(modifier) > MAX_EQUIPMENT_MODIFIER
            ):
                raise CharacterImportError(
                    f"Equipment item {index} has an invalid {modifier_field}"
                )


def _validate_experience_log(experience_log: list) -> None:
    """Structurally validate an imported experience log.

    Every entry must be an object; the fields the exporters and templates
    read without a defensive type check (``track``, ``xp_awarded``,
    ``skills_gained``) are validated so a bad import can't crash
    markdown/PDF export later (issue #122).
    """
    for index, entry in enumerate(experience_log):
        if not isinstance(entry, dict):
            raise CharacterImportError(f"Experience log entry {index} is not an object")
        track = entry.get("track")
        if track is not None and not isinstance(track, str):
            raise CharacterImportError(
                f"Experience log entry {index} has a non-string track"
            )
        for numeric_field in ("xp_awarded", "money_earned"):
            numeric_value = entry.get(numeric_field)
            if numeric_value is not None and (
                not isinstance(numeric_value, int | float)
                or isinstance(numeric_value, bool)
            ):
                raise CharacterImportError(
                    f"Experience log entry {index} has a non-numeric {numeric_field}"
                )
        skills_gained = entry.get("skills_gained")
        if skills_gained is not None:
            if not isinstance(skills_gained, dict):
                raise CharacterImportError(
                    f"Experience log entry {index} has an invalid skills_gained"
                )
            for skill_name, points in skills_gained.items():
                if not isinstance(skill_name, str) or (
                    not isinstance(points, int | float) or isinstance(points, bool)
                ):
                    raise CharacterImportError(
                        f"Experience log entry {index} has an invalid "
                        f"skills_gained entry"
                    )


def _validate_skills(skills: dict) -> None:
    """Enforce the per-skill bound the allocate-skill endpoint protects.

    That bound is a storage sanity ceiling, not a level cap: skill levels are
    uncapped, so a term-built character with a 21-point skill re-imports
    (issue #146).
    """
    for skill_name, points in skills.items():
        # Skill names reach the page as a data-skill attribute value; the
        # allocate/rename endpoints normalize control characters out of
        # them, so import has to refuse them too (issue #120).
        if isinstance(skill_name, str) and CONTROL_CHARACTERS_PATTERN.search(
            skill_name
        ):
            raise CharacterImportError(
                "Imported skill name contains disallowed control characters"
            )
        if (
            not isinstance(points, int)
            or isinstance(points, bool)
            or not (0 <= points <= MAX_SKILL_POINTS)
        ):
            raise CharacterImportError(
                f"Imported skill '{skill_name}' has invalid points "
                f"(must be 0-{MAX_SKILL_POINTS})"
            )


def validate_import_payload(payload: object) -> ImportedCharacter:
    """Check a ``tarmar-character`` payload and coerce its fields.

    Args:
        payload: The decoded JSON document (any type; a non-object is
            rejected here rather than assumed).

    Returns:
        The validated fields, projected onto the exportable subset -- extra
        keys the document carries are dropped (forward-compat).

    Raises:
        CharacterImportError: On any shape, name, bound or type problem.
    """
    if not isinstance(payload, dict):
        raise CharacterImportError("Import file must be a JSON object")
    if payload.get("_format") != CHARACTER_EXPORT_FORMAT:
        raise CharacterImportError("File is not a Tarmar character export")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise CharacterImportError("Import file is missing the 'data' object")

    # The name rule the sheet's rename and the creation page apply, in their
    # words: this used to keep its own copy of the three checks (#786 review).
    try:
        raw_name = str(Character.clean_field_value("name", data.get("name", "")))
    except ValidationError as validation_error:
        raise CharacterImportError(validation_error.messages[0]) from None

    fields = {key: data[key] for key in EXPORTABLE_FIELDS if key in data}
    # The name is validated explicitly above (presence, length, pattern), so
    # full_clean later excludes it. There is nothing else for it to check:
    # a character's identity is its id and two may share a name (issue #571).
    fields["name"] = raw_name
    # A stored literacy roll was rolled (#741): a file that carries one but
    # not the flag, hand-edited or otherwise, must not reopen the player's
    # Origin and their one roll.
    if fields.get("literacy_roll") is not None:
        fields["literacy_rolled"] = True
    # A ruling belongs to "Consult GM", the one origin with no check (#743);
    # at any other origin the check is rolled, so a ruling there is dropped,
    # whoever imports it.
    location = fields.get("location", Character._meta.get_field("location").default)
    if LOCATION_LITERACY_MODIFIER.get(location) is not None:
        fields.pop("literacy_ruling", None)

    # Enforce the same per-field integer caps the single-field edit endpoint
    # does (CHARACTER_INT_FIELD_MAX), rather than leaning on full_clean()'s
    # model-level validators. Those validators derive their max from the DB
    # backend's integer range, and SQLite's dynamic typing reports an ~8-byte
    # range for every integer column regardless of field type -- so on the
    # SQLite dev/test database they don't actually enforce PositiveIntegerField
    # vs. PositiveSmallIntegerField bounds the way they would against
    # production Postgres. Validating explicitly here keeps import and the
    # edit endpoint in sync everywhere (#136).
    for int_field in CHARACTER_INT_FIELDS:
        if int_field not in fields:
            continue
        # A roll not made yet is null on the row (issue #571), and a player's
        # import leaves the rolls unmade (issue #744), so a file carrying a
        # null there is a character this site holds, not a mistake.
        if fields[int_field] is None and Character._meta.get_field(int_field).null:
            continue
        try:
            fields[int_field] = Character.clean_field_value(
                int_field, fields[int_field]
            )
        except ValidationError as validation_error:
            raise CharacterImportError(validation_error.messages[0]) from None

    # Reject wrong-typed containers outright rather than silently skipping their
    # validation -- every downstream reader (total_allocated_skill_points, the
    # equipment/experience templates, the markdown/PDF exporters) assumes the
    # correct container type with no fallback. A wrong type here used to save
    # successfully and then permanently 500 the character page (issue #122).
    if "skills" in fields and not isinstance(fields["skills"], dict):
        raise CharacterImportError(
            "Imported skills must be an object mapping skill to points"
        )
    if "equipment" in fields and not isinstance(fields["equipment"], list):
        raise CharacterImportError("Imported equipment must be a list of item objects")
    if "experience_log" in fields and not isinstance(fields["experience_log"], list):
        raise CharacterImportError(
            "Imported experience log must be a list of term entries"
        )

    if "skills" in fields:
        _validate_skills(fields["skills"])
        # Stored as the allocate and rename endpoints store them, so the
        # grant below and an Origin change find the skill a file wrote as
        # "Street Smarts" (issue #754). Names that meet add their points,
        # held to the same bound.
        normalized: dict[str, int] = {}
        for skill_name, points in fields["skills"].items():
            key = normalize_skill_name(str(skill_name))
            normalized[key] = normalized.get(key, 0) + points
        _validate_skills(normalized)
        fields["skills"] = normalized
    # A file written before #754 names no skill holding the Origin's grant;
    # read it off the skills as the migration did, or the next Origin change
    # would grant the point a second time.
    if "origin_skill" not in fields:
        fields["origin_skill"] = services.held_origin_skill(
            location, fields.get("skills")
        )
    _validate_equipment(fields.get("equipment"))
    # Armour a file marks both worn and packed arrives worn (#869), as the
    # sheet stores it: the two flags never both hold.
    for item in fields.get("equipment") or []:
        settle_worn_armour(item)
    if "experience_log" in fields:
        _validate_experience_log(fields["experience_log"])

    return ImportedCharacter(name=raw_name, fields=fields)


def fresh_player_fields(fields: dict) -> dict:
    """A player's import: every field not kept, at a fresh character's value.

    The six attributes, the Fatigue roll and the literacy record are unset;
    XP, experience, age, aging, free points, money earned, the starting coin
    and the appearance roll start at a new character's values, alive and
    unplayed; the background is marked unrolled, so the sheet offers its
    rolls; the skills are the one the Origin grants a new character. Each
    field's fresh value is the model's own default, so nothing here restates
    one; nothing is rolled, and nothing is written to the dice log, which
    records only dice this site rolled.

    Args:
        fields: The validated fields of the file.

    Returns:
        The value of every exportable field a player does not keep.
    """
    values = {
        name: Character._meta.get_field(name).get_default()
        for name in EXPORTABLE_FIELDS
        if name not in PLAYER_KEPT_FIELDS
    }
    location = fields.get("location", Character._meta.get_field("location").default)
    values["skills"], _grant_log = location_effects(location)
    # Which skill holds that grant, so an Origin change moves it (#754).
    values["origin_skill"] = next(iter(values["skills"]), "")
    values.update(PLAYER_UNROLLED_BACKGROUND)
    return values


def _signed(number: object) -> str:
    return f"{number:+d}" if isinstance(number, int) else str(number)


def _describe(name: str, value: object) -> str:
    """One dropped value, as the notice names it."""
    for field_name, short in CORE_ATTRIBUTE_FIELDS:
        if name == field_name:
            return f"{short} {value}"
    if name == "fatigue_roll":
        return f"the Fatigue roll {value}"
    if name == "literacy_roll":
        return f"the literacy roll {value}"
    if name == "literacy_rolled":
        return "the record that literacy was rolled"
    if name == "literacy_ruling":
        return (
            "the Game Master's literacy ruling "
            f"({'literate' if value else 'illiterate'})"
        )
    if name in ("skills", "spells") and isinstance(value, dict):
        listed = ", ".join(f"{known} {points}" for known, points in value.items())
        return f"the {name} ({listed})"
    if name == "experience_log" and isinstance(value, list):
        return f"the experience log ({len(value)} entries)"
    if name == "dice_log":
        return "the dice log"
    if name == "total_xp":
        return f"{value} XP"
    if name == "is_alive":
        return "that the character is alive" if value else "the character's death"
    if name == "is_played":
        return "that the character has been played" if value else "unplayed"
    if name.startswith("aging_") and name.endswith("_penalty"):
        return f"the aging penalty on {name.split('_')[1].upper()} ({_signed(value)})"
    return f"{name.replace('_', ' ')} {value}"


#: What the notice does not name as dropped: the equipment, named item by
#: item instead; the dice log, a record of rolls and not a value; the death
#: cause, which the death names; and the background flags, which the
#: provenance and wealth they stand for name.
_NOT_NAMED = frozenset(
    {
        "equipment",
        "dice_log",
        "death_cause",
        "provenance_recorded",
        "location_recorded",
        "origin_skill",
        "wealth_recorded",
    }
)


def _stated(name: str, value: object, fresh: object) -> bool:
    """Whether the file stated something for ``name`` the import did not keep.

    Compared as the field reads the value, so a file's ``"0.00"`` is the
    ``Decimal(0)`` a new character holds; and a value at the field's own
    default states nothing -- a file with no skills drops none, though the
    Origin's grant is added.
    """
    model_field = Character._meta.get_field(name)
    read = model_field.to_python(value)
    return read != fresh and read != model_field.get_default()


def player_import_notice(
    fields: dict,
    fresh: dict,
    left_out: Sequence[str] = (),
) -> str:
    """What a player's import dropped, and why, for the sheet (issue #744).

    Names each value the file stated that the character did not keep --
    every field that arrived at a different value, except what
    :data:`_NOT_NAMED` leaves out -- and each thing carried that was left
    out.

    Args:
        fields: The validated fields of the file.
        fresh: What the player's import holds instead.
        left_out: What was carried and not brought in
            (:attr:`ImportedCharacter.left_out`).

    Returns:
        The notice, or ``""`` when nothing the file stated was dropped.
    """
    dropped = [
        _describe(name, fields[name])
        for name in EXPORTABLE_FIELDS
        if name in fields and name in fresh and name not in _NOT_NAMED
        if _stated(name, fields[name], fresh[name])
    ]
    dropped.extend(left_out)
    if not dropped:
        return ""
    listed = (
        dropped[0]
        if len(dropped) == 1
        else f"{'; '.join(dropped[:-1])}; and {dropped[-1]}"
    )
    return (
        "Imported by a player, so this is a new, living character: what the "
        f"file said was rolled, earned or ruled was not kept: {listed}. "
        "Attributes and rolls are set again on this sheet, and experience is "
        "earned here."
    )


def _clean(character: Character, *, owned: bool = True) -> None:
    """Run the model's own validation on an unsaved or updated instance.

    Skips the JSONField blank-check on collections that legitimately default
    to empty (matches the create-character flow, which never calls
    full_clean either); the size-cap validators on those fields still run.

    Args:
        character: The instance.
        owned: Whether it has its owner yet; a file checked before anyone
            owns it (:func:`held_to_player`) has none.
    """
    # A roll not made yet is null on the row (#571); model validation calls
    # that blank, so a null there is left out of it rather than refused.
    unmade = [
        model_field.name
        for model_field in character._meta.concrete_fields
        if model_field.null and getattr(character, model_field.attname) is None
    ]
    owner = [] if owned else ["user"]
    character.full_clean(exclude=["name", *_COLLECTION_FIELDS, *unmade, *owner])
    for validator_field in _COLLECTION_FIELDS:
        for validator in character._meta.get_field(validator_field).validators:
            validator(getattr(character, validator_field))
    # Death invariant (mark-dead enforces this; import must too). Checked on
    # the cleaned instance so a string "false" is coerced first.
    if not character.is_alive and not character.death_cause.strip():
        raise CharacterImportError("Imported dead character is missing a death cause")


def _saving[Result](save: Callable[[], Result]) -> Result:
    """Run ``save`` and turn the model's rejections into import errors."""
    try:
        return save()
    except (TypeError, ValueError) as error:
        raise CharacterImportError(f"Import failed: {error}") from error
    except ValidationError as error:
        if hasattr(error, "message_dict") and error.message_dict:
            field_name, messages = next(iter(error.message_dict.items()))
            raise CharacterImportError(
                f"Import failed: {field_name}: {messages[0]}"
            ) from error
        raise CharacterImportError(f"Import failed: {error.messages[0]}") from error


#: Why a player's import brings in no gear (the coordinator's ruling on the
#: #744 review): the background arrives unrolled, and gear bought before the
#: wealth roll can leave the purse below zero once the roll sets the coin
#: (an 80 gp sword, then Subsistence's 10 gp, read -70).
UNROLLED_WEALTH_BUYS_NOTHING = "bought on the sheet once wealth is rolled"


def _gear_left_out(equipment: Sequence[dict]) -> list[str]:
    """A line for each thing a player's file carries, none of it brought in.

    A custom item is a Game Master's to add (#752); a catalog item is the
    player's to buy on the sheet, after the wealth roll.
    """
    left_out = []
    for entry in equipment:
        item_id = str(entry.get("item_id", ""))
        item = get_item(item_id)
        quantity = int(entry.get("quantity", 1) or 1)
        called = (item or {}).get("name") or entry.get("name") or item_id
        if item is None:
            left_out.append(f"{called} (a custom item is a Game Master's to add)")
        else:
            left_out.append(f"{quantity} × {called} ({UNROLLED_WEALTH_BUYS_NOTHING})")
    return left_out


def held_to_player(
    imported: ImportedCharacter, *, is_game_master: bool
) -> ImportedCharacter:
    """Hold a player's import to the rules (issues #744, #757): the one place.

    A Game Master's import comes in verbatim. Every player's import is a new
    character held to what a player may set, whatever file it came from --
    one this site wrote is still someone's character as it once stood, not
    the importer's as it stands (the coordinator's ruling on the #744
    review):

    * the file is checked as written first, so a value no dice can make is
      refused rather than quietly dropped;
    * :data:`PLAYER_KEPT_FIELDS` (built from ``services.FIELD_RULES``) are
      kept, and every other exportable field arrives at a fresh character's
      value (:func:`fresh_player_fields`), the explicit exceptions in
      :data:`PLAYER_IMPORT_NEVER_KEPT` included whatever the table says --
      so the character is alive, unplayed and unrolled;
    * the background arrives unrolled, so nothing is bought: each thing the
      file carries is left out and named (:func:`_gear_left_out`);
    * the notice names each value dropped and why
      (:func:`player_import_notice`).

    Raises:
        CharacterImportError: The file as written breaks the model's rules.
    """
    if is_game_master:
        return imported
    _saving(lambda: _clean(Character(**imported.fields), owned=False))
    fields = {
        name: value
        for name, value in imported.fields.items()
        if name in PLAYER_KEPT_FIELDS
    }
    fields.update(fresh_player_fields(imported.fields))
    fields["equipment"] = []
    left_out = _gear_left_out(imported.fields.get("equipment") or [])
    fields["import_notice"] = player_import_notice(imported.fields, fields, left_out)
    return ImportedCharacter(name=imported.name, fields=fields, left_out=left_out)


def save_imported_character(user: User, imported: ImportedCharacter) -> Character:
    """Create ``user``'s new character from a validated payload.

    The payload's name is kept verbatim; names are not unique per user
    (issue #571). What a player may bring in is decided before this, by
    :func:`held_to_player`.

    Raises:
        CharacterImportError: When the model's own validation rejects the
            instance.
    """

    def _create() -> Character:
        return services.create_character(
            user, imported.name, imported.fields, validate=_clean
        )

    return _saving(_create)
