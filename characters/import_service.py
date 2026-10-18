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
override, so a Game Master's import is kept as written, held only to the
bounds the sheet holds a Game Master to (issue #940; he edits on the sheet
afterwards, where it is recorded), and every player's import arrives as a
new character: :data:`PLAYER_KEPT_FIELDS` kept,
everything rolled, awarded or ruled at a fresh character's value, and a
notice naming each value dropped and why.
"""

import copy
import math
from collections.abc import Callable, Mapping, Sequence
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
    ITEM_MODIFIER_FIELDS,
    MAX_EQUIPMENT_DAMAGE_LENGTH,
    MAX_EQUIPMENT_MODIFIER,
    MAX_EQUIPMENT_NAME_LENGTH,
    MAX_EQUIPMENT_NOTES_LENGTH,
    MAX_EQUIPMENT_QUANTITY,
    MAX_EQUIPMENT_WEIGHT_LBS,
)
from .dice import location_effects, location_literacy_modifier
from .equipment import get_item, settle_worn_armour
from .equipment_service import custom_equipment_item
from .models import CHARACTER_INT_FIELDS, DIE_ROLL_FIELDS, Character
from .skills import MAX_SKILL_POINTS, normalize_skill_name

#: The numbers an item stores as whole numbers and its readers add up with no
#: type check: the Game Master's modifiers and a custom armour's or shield's
#: own Stops and DEX penalty (``Character._sum_armor_field``).
_WHOLE_NUMBER_ITEM_FIELDS: tuple[str, ...] = (
    *ITEM_MODIFIER_FIELDS,
    "stops",
    "dex_penalty",
)

#: The numbers an item stores as a weight or a price, either int or float.
_AMOUNT_ITEM_FIELDS: tuple[str, ...] = ("weight", "cost")

#: The flags an item stores as booleans.
_FLAG_ITEM_FIELDS: tuple[str, ...] = ("equipped", "in_pack")

#: The fields the sheet's single-field editor rules on that the import runs
#: through the same rule (``Character.clean_field_value``): the integers
#: (#136) and the height and weight die rolls (#805, #939). The rest of the
#: sheet's fields are held by the model's own checks in ``full_clean``.
_SHEET_RULED_FIELDS: frozenset[str] = frozenset(
    {*CHARACTER_INT_FIELDS, *DIE_ROLL_FIELDS}
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


def _is_whole_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_amount(value: object) -> bool:
    """A weight or a price: a finite number, as the builder stores one."""
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _validate_equipment(equipment) -> None:
    """Structurally validate imported equipment, mirroring the add/update bounds.

    The add/update endpoints carefully bound quantity/weight/modifiers; import
    must not be a bypass for those. A non-list is left to the caller's
    container check.

    A custom item -- any entry the catalog does not know, which its readers
    take at its own numbers -- is held to the sheet's builder itself
    (:func:`characters.equipment_service.custom_equipment_item`, issue
    #939): its name, category, weight, cost, damage dice, Stops, DEX penalty
    and modifiers, in the builder's words. The checks above it hold each
    number to the type the builder stores, since the builder reads a form and
    coerces what it is given. A player's import brings in no gear
    (:func:`held_to_player`), so this mostly holds a Game Master's import,
    whose custom items the sheet holds to the same rule.

    Raises:
        CharacterImportError: An item the sheet would not store.
    """
    if not isinstance(equipment, list):
        return
    for index, item in enumerate(equipment):
        if not isinstance(item, dict):
            raise CharacterImportError(f"Equipment item {index} is not an object")
        # The add/update endpoints cap these and drop control characters
        # from them; an import file must not be a way around that (issues
        # #120, #928). Skill names are held by the model's own validator.
        for text_field, max_length in (
            ("name", MAX_EQUIPMENT_NAME_LENGTH),
            ("notes", MAX_EQUIPMENT_NOTES_LENGTH),
            ("damage", MAX_EQUIPMENT_DAMAGE_LENGTH),
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
        # Present means stated: a null reaches the readers' arithmetic as
        # None, so it is refused like any other wrong type.
        for amount_field in _AMOUNT_ITEM_FIELDS:
            if amount_field in item and not _is_amount(item[amount_field]):
                raise CharacterImportError(
                    f"Equipment item {index} has an invalid {amount_field}"
                )
        if "weight" in item and not (0 <= item["weight"] <= MAX_EQUIPMENT_WEIGHT_LBS):
            raise CharacterImportError(f"Equipment item {index} has an invalid weight")
        for number_field in _WHOLE_NUMBER_ITEM_FIELDS:
            if number_field in item and not _is_whole_number(item[number_field]):
                raise CharacterImportError(
                    f"Equipment item {index} has an invalid {number_field}"
                )
        for modifier_field in ITEM_MODIFIER_FIELDS:
            if abs(item.get(modifier_field, 0)) > MAX_EQUIPMENT_MODIFIER:
                raise CharacterImportError(
                    f"Equipment item {index} has an invalid {modifier_field}"
                )
        for flag_field in _FLAG_ITEM_FIELDS:
            if flag_field in item and not isinstance(item[flag_field], bool):
                raise CharacterImportError(
                    f"Equipment item {index} has an invalid {flag_field}"
                )
        if get_item(str(item.get("item_id", ""))) is None:
            _built, refusal = custom_equipment_item(item)
            if refusal:
                raise CharacterImportError(f"Equipment item {index}: {refusal}")


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
    if location_literacy_modifier(location) is not None:
        fields.pop("literacy_ruling", None)

    # Enforce the same per-field integer caps the single-field edit endpoint
    # does (CHARACTER_INT_FIELD_MAX), rather than leaning on full_clean()'s
    # model-level validators. Those validators derive their max from the DB
    # backend's integer range, and SQLite's dynamic typing reports an ~8-byte
    # range for every integer column regardless of field type -- so on the
    # SQLite dev/test database they don't actually enforce PositiveIntegerField
    # vs. PositiveSmallIntegerField bounds the way they would against
    # production Postgres. Validating explicitly here keeps import and the
    # edit endpoint in sync everywhere (#136). The height and weight rolls
    # share the edit endpoint's die-roll rule the same way (#939): the model
    # holds them only to a length, so "tall" used to import.
    for ruled_field in _SHEET_RULED_FIELDS:
        if ruled_field not in fields:
            continue
        # A roll not made yet is null on the row (issue #571), and a player's
        # import leaves the rolls unmade (issue #744), so a file carrying a
        # null there is a character this site holds, not a mistake.
        if fields[ruled_field] is None and Character._meta.get_field(ruled_field).null:
            continue
        try:
            fields[ruled_field] = Character.clean_field_value(
                ruled_field, fields[ruled_field]
            )
        except ValidationError as validation_error:
            # A number's rule does not say which number it is (#940); the
            # die rolls' and the name's do.
            refusal = validation_error.messages[0]
            if ruled_field in CHARACTER_INT_FIELDS:
                refusal = f"Import failed: {ruled_field}: {refusal}"
            raise CharacterImportError(refusal) from None

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
    records only dice this site rolled (a chain kept from the importer's own
    character is recorded there by :func:`_unchosen_faces`, issue #941).

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
    if name in services.player_chosen_face_reasons():
        return (
            f"{name.replace('_', ' ')} {value} (a chain comes only from the dice "
            "or a Game Master, and no character of yours is the record this "
            "file came from)"
        )
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
    site_rolls_kept: Sequence[str] = (),
) -> str:
    """What a player's import dropped and kept, and why (issues #744, #941).

    Names each value the file stated that the character did not keep --
    every field that arrived at a different value, except what
    :data:`_NOT_NAMED` leaves out -- each thing carried that was left out,
    and each roll kept because the file is an export of the importer's own
    character.

    Args:
        fields: The validated fields of the file.
        fresh: What the player's import holds instead.
        left_out: What was carried and not brought in
            (:attr:`ImportedCharacter.left_out`).
        site_rolls_kept: Each roll kept as this site rolled it
            (:func:`_unchosen_faces`).

    Returns:
        The notice, or ``""`` when nothing the file stated was dropped or
        kept by its record.
    """
    dropped = [
        _describe(name, fields[name])
        for name in EXPORTABLE_FIELDS
        if name in fields and name in fresh and name not in _NOT_NAMED
        if _stated(name, fields[name], fresh[name])
    ]
    dropped.extend(left_out)
    sentences = []
    if dropped:
        sentences.append(
            "Imported by a player, so this is a new, living character: what the "
            f"file said was rolled, earned or ruled was not kept: {_listed(dropped)}. "
            "Attributes and rolls are set again on this sheet, and experience is "
            "earned here."
        )
    if site_rolls_kept:
        sentences.append(
            "Kept, since this file is an export of your own character, whose "
            f"record holds it: {_listed(site_rolls_kept)}."
        )
    return " ".join(sentences)


def _listed(items: Sequence[str]) -> str:
    """``items`` joined for the notice: "a", "a; and b", "a; b; and c"."""
    if len(items) == 1:
        return items[0]
    return f"{'; '.join(items[:-1])}; and {items[-1]}"


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


#: What a file must agree on with the importer's own character to be that
#: character's export (issue #941): its name, its site-rolled values and its
#: whole dice log, as the server stores them. A chain belongs to one
#: character: re-importing that character's export keeps it, copying the
#: chain onto another character's file does not (coordinator's ruling on the
#: #941 review, for Spencer to confirm).
_SOURCE_MATCH_FIELDS: tuple[str, ...] = (
    "name",
    *(field_name for field_name, _short in CORE_ATTRIBUTE_FIELDS),
    "fatigue_roll",
    "appearance_roll",
    "height_roll",
    "weight_roll",
    "dice_log",
)


def _source_character(
    importer: User | None, file_fields: Mapping[str, object]
) -> Character | None:
    """The importer's own character this file is a faithful export of (#941).

    A file carries no provenance and every value in it, the dice log
    included, is the player's to edit, so nothing in the file alone can tie
    it to a roll. The server's record can: a character the importer owns
    whose name, site-rolled values and whole dice log
    (:data:`_SOURCE_MATCH_FIELDS`) equal the file's is the character the file
    was exported from. A file that differs in any of them is another
    character's, edited or copied, and matches nothing.

    Args:
        importer: Who imports; ``None`` when unknown, which matches nothing.
        file_fields: The validated fields of the file.

    Returns:
        The first such character, or ``None``.
    """
    if importer is None or any(
        name not in file_fields for name in _SOURCE_MATCH_FIELDS
    ):
        return None
    stated = {
        name: Character._meta.get_field(name).to_python(file_fields[name])
        for name in _SOURCE_MATCH_FIELDS
    }
    owned = Character.objects.filter(user=importer, name=stated["name"])
    for candidate in owned.order_by("pk"):
        if all(
            getattr(candidate, name) == stated[name] for name in _SOURCE_MATCH_FIELDS
        ):
            return candidate
    return None


def _unchosen_faces(
    kept: Mapping[str, object],
    *,
    importer: User | None = None,
    file_fields: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], list[str]]:
    """The "choose or roll" values a player keeps, and which stay rolled.

    A player chooses one face of a d6 for each (#748); a demon die's chain
    comes only from the dice or a Game Master, and a file is not dice
    (#744). Such a value is kept when the file is a faithful export of the
    importer's own character (:func:`_source_character`, issue #941), and the
    new character's dice log says where it came from, so it counts as that
    field's roll (``services.may_roll_physical``) and the copy is no
    character's export but its own. A single face, or an Origin, from such an
    export whose character rolled it and holds it still gets the same line,
    so the roll stays final (:data:`services.ROLL_FIXED_REASONS`, issues
    #948, #970). Otherwise a chain arrives blank, as a fresh
    character's, and the notice names it, as it does every rolled value
    dropped (#939). The rule is the sheet's own,
    :func:`characters.services.check_player_choice`.

    Args:
        kept: The fields the player's import keeps.
        importer: Who imports.
        file_fields: The validated fields of the file.

    Returns:
        ``(changes, site_rolls_kept)``: each field to set -- a blanked value
        at the model's default, or the dice log recording a kept chain -- and
        a line for the notice for each chain kept.
    """
    changes: dict[str, object] = {}
    site_rolls_kept: list[str] = []
    kept_lines: list[str] = []
    source: Character | None = None
    looked_up = False

    def source_character() -> Character | None:
        """The importer's character this file is an export of, looked up once."""
        nonlocal source, looked_up
        if not looked_up:
            source = _source_character(importer, file_fields or {})
            looked_up = True
        return source

    for rolled_field in services.ROLL_FIXED_REASONS:
        if rolled_field not in kept:
            continue
        value = kept[rolled_field]
        try:
            services.check_player_choice(rolled_field, value, is_game_master=False)
        except services.CharacterChangeError:
            matched = source_character()
            if matched is None:
                changes[rolled_field] = Character._meta.get_field(
                    rolled_field
                ).get_default()
                continue
            site_rolls_kept.append(
                f"{rolled_field.replace('_', ' ')} {value} (on your character "
                f"{matched.name})"
            )
        else:
            # A value a player could have chosen comes in as it stands. It was
            # a roll, and a roll is final for a player (#948, #970), when the
            # file is the export of the player's own character, that character
            # still holds the value, and its dice log records the roll: the
            # copy records it too, so it is fixed as the original is. (The
            # height and weight are among the fields a file must match to be
            # that export; the Origin is not, so it is compared here.)
            # Coordinator's ruling under the standing rule; Spencer may
            # overrule.
            if not str(value).strip():
                continue
            matched = source_character()
            if (
                matched is None
                or getattr(matched, rolled_field) != value
                or not services.roll_lines(matched.dice_log or "", rolled_field)
            ):
                continue
        kept_lines.append(
            f"{services.ROLL_LOG_LABELS[rolled_field]}: kept from your character "
            f"{matched.name} on import = {value}"
        )
    if kept_lines:
        changes["dice_log"] = "\n".join(kept_lines)
    return changes, site_rolls_kept


def held_to_player(
    imported: ImportedCharacter,
    *,
    is_game_master: bool,
    importer: User | None = None,
) -> ImportedCharacter:
    """Hold a player's import to the rules (issues #744, #757): the one place.

    A Game Master's import comes in as written, to be held to the sheet's
    bounds when saved (:func:`save_imported_character`). Every player's import is a new
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
    * a height or weight no player could choose arrives blank, unless the
      file is a faithful export of the importer's own character
      (:func:`_unchosen_faces`);
    * the background arrives unrolled, so nothing is bought: each thing the
      file carries is left out and named (:func:`_gear_left_out`);
    * the notice names each value dropped and why
      (:func:`player_import_notice`).

    Args:
        imported: The validated file.
        is_game_master: Whether a Game Master imports.
        importer: Who imports, among whose own characters the file's source
            is looked for; ``None`` keeps no chain.

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
    face_changes, site_rolls_kept = _unchosen_faces(
        fields, importer=importer, file_fields=imported.fields
    )
    fields.update(face_changes)
    fields["equipment"] = []
    left_out = _gear_left_out(imported.fields.get("equipment") or [])
    fields["import_notice"] = player_import_notice(
        imported.fields, fields, left_out, site_rolls_kept
    )
    return ImportedCharacter(name=imported.name, fields=fields, left_out=left_out)


def _held_to_sheet_bounds(character: Character) -> None:
    """Hold the pools only a Game Master's import keeps to the sheet (#940).

    Spencer's ruling on #940: a Game Master's import is held to the bounds
    the sheet holds him to, refusing what the sheet would refuse; he edits on
    the sheet afterwards, where it is recorded. Each wound and the spent mana
    (``services.POOL_LOSS_FIELDS``) goes through the sheet's own pools
    setting, ``services.apply_current_pools``, as the pool it leaves --
    the pool's maximum (``services.pool_maxima``) less what is gone -- so no
    pool sits where it may not be set: Mana below 0, or a wound on a
    character whose pools are not known yet. It runs on a copy, so the
    character is not changed by it.

    A player's import holds these at a fresh character's values
    (:func:`fresh_player_fields`), which pass. The aging penalties are not
    checked: ``Character.save`` sets them from the age, whatever the file
    says (``reconcile_aging_penalties``, issue #680). The purse is held to
    its column only: a stored purse may sit below nothing after a Game
    Master's purchase before the wealth roll (#851), which the sheet keeps.

    Raises:
        CharacterImportError: The sheet's refusal, naming the field.
    """
    scratch = copy.copy(character)
    # What save() will store, so the pools' maxima read the age's aging.
    scratch.reconcile_aging_penalties()
    maxima = services.pool_maxima(scratch)
    for label, loss_field in services.POOL_LOSS_FIELDS.items():
        gone = getattr(scratch, loss_field)
        if not gone:
            continue
        now = (maxima[label] or 0) - gone
        try:
            services.apply_current_pools(
                scratch, is_game_master=True, **{label.lower(): now}
            )
        except services.CharacterChangeError as refusal:
            raise CharacterImportError(
                f"Import failed: {loss_field}: {refusal}"
            ) from None


def _clean_to_sheet(character: Character) -> None:
    """The model's own validation, then the sheet's bounds (issue #940)."""
    _clean(character)
    _held_to_sheet_bounds(character)


def save_imported_character(user: User, imported: ImportedCharacter) -> Character:
    """Create ``user``'s new character from a validated payload.

    The payload's name is kept verbatim; names are not unique per user
    (issue #571). What a player may bring in is decided before this, by
    :func:`held_to_player`; what is brought in is held to the model's rules
    and to the sheet's bounds on its pools (:func:`_held_to_sheet_bounds`).

    Raises:
        CharacterImportError: When the model's own validation or the sheet's
            bounds reject the instance.
    """

    def _create() -> Character:
        return services.create_character(
            user, imported.name, imported.fields, validate=_clean_to_sheet
        )

    return _saving(_create)
