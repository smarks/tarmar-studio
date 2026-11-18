"""Django system checks for the characters app (issues #59, #70 and #75).

The canon's ``character-rules.json`` is read the first time any character
code asks for it: constructing a ``Character`` (its age default), rendering a
sheet, building the Games Master's extraction schema, importing a character,
creating or rolling one. Its ``skill-rules.json`` is read the same way, the
first time skills, prior experience or a check need it, and its
``equipment.json`` and ``equipment-rules.json`` the first time the catalog,
encumbrance or movement do. A file the loader
refuses, or a canon without one, would otherwise surface only then, as an
error in the middle of whichever of those came first. Checked here, it fails
``manage.py check`` and the deploy's ``migrate`` (which runs the system
checks) before anyone builds a character.

One check reads two files together (tarmar-studio #208): a skill the canon
leaves to a Game Master to start (``skill-rules.json``'s
``game_master_started``) must be on no list that gives a skill without asking
who may start it, a track's table or an Origin's row
(:func:`check_game_master_started_skills`).
"""

from collections.abc import Callable, Iterator, Sequence
from typing import Any

from django.apps import AppConfig
from django.core.checks import CheckMessage, Error, register

from common.helpers import column_length
from reference.paths import (
    CANON_CHARACTER_RULES_FILE_NAME,
    CANON_EQUIPMENT_CATALOG_FILE_NAME,
    CANON_EQUIPMENT_RULES_FILE_NAME,
    CANON_SKILL_RULES_FILE_NAME,
    CHARACTER_RULES_FILE,
    EQUIPMENT_CATALOG_FILE,
    EQUIPMENT_RULES_FILE,
    SKILL_RULES_FILE,
)
from reference.rules_file import RulesFileError

from .combat import validate_catalog_classes
from .equipment_rules import (
    EquipmentCatalogError,
    EquipmentRulesError,
    equipment_catalog,
    equipment_rules,
    validate_armour_restriction_codes,
    validate_missile_classes,
)
from .models import CHARACTER_TABLE_FIELDS, Character
from .rules import (
    SOCIAL_CLASS_DETAIL_TABLES,
    CharacterRules,
    CharacterRulesError,
    character_rules,
)
from .skill_rules import (
    GAME_MASTER_STARTED_BLOCK,
    SkillRules,
    SkillRulesError,
    skill_rules,
)
from .skills import (
    is_game_master_started,
    normalize_skill_name,
    validate_skill_names,
)

#: The check id a refused ``character-rules.json`` reports under.
CHARACTER_RULES_CHECK_ID = "characters.E001"
#: The check id a refused ``skill-rules.json`` reports under.
SKILL_RULES_CHECK_ID = "characters.E002"
#: The check id a refused ``equipment.json`` reports under.
EQUIPMENT_CATALOG_CHECK_ID = "characters.E003"
#: The check id a refused ``equipment-rules.json`` reports under.
EQUIPMENT_RULES_CHECK_ID = "characters.E004"
#: The check id a canon table label too long for its ``Character`` column
#: reports under (issue #66).
CHARACTER_LABEL_COLUMNS_CHECK_ID = "characters.E005"
#: The check id a canon list that grants a skill only a Game Master starts
#: reports under (tarmar-studio #208).
GAME_MASTER_STARTED_CHECK_ID = "characters.E006"


def _rules_file_refusals(
    load: Callable[[], object],
    error_class: type[RulesFileError],
    file_name: str,
    schema_module: str,
    check_id: str,
) -> list[CheckMessage]:
    """Load one canon rules file, reporting the loader's refusal as an error.

    Args:
        load: The cached loader, e.g. :func:`characters.rules.character_rules`.
        error_class: The refusal that loader raises.
        file_name: The file's name, for the hint.
        schema_module: The module whose docstring gives the schema.
        check_id: The id the error reports under.

    Returns:
        No messages when the rules load; otherwise an :class:`Error` carrying
        the loader's message, which names the file and the field.
    """
    try:
        load()
    except error_class as error:
        return [
            Error(
                str(error),
                hint=(
                    f"Every canon carries {file_name} beside canon.json. "
                    f"The schema is in {schema_module}."
                ),
                id=check_id,
            )
        ]
    return []


@register()
def check_character_rules(
    app_configs: Sequence[AppConfig] | None = None, **kwargs: Any
) -> list[CheckMessage]:
    """Load the canon's character rules.

    Args:
        app_configs: The apps being checked, or ``None`` for all; the canon
            belongs to no one app, so it is checked whichever are named.
        **kwargs: Django's other check arguments, unused.

    Returns:
        No messages when the rules load; otherwise an :class:`Error` carrying
        the loader's message, which names the file and the field.
    """
    return _rules_file_refusals(
        character_rules,
        CharacterRulesError,
        CANON_CHARACTER_RULES_FILE_NAME,
        "characters/rules.py",
        CHARACTER_RULES_CHECK_ID,
    )


def _skill_rules_against_catalog() -> None:
    """Load the skill rules and check their skill names.

    Whether a name is a Weapon skill depends on the catalog's weapons
    (:func:`characters.weapon_skill.trained_weapon_id`), so the names are
    checked only when the catalog loads; a refused catalog is
    ``characters.E003``'s to report (issue #75).

    Raises:
        SkillRulesError: The rules are refused, or name a skill that is not
            a glossary skill in its own right.
    """
    rules = skill_rules()
    try:
        equipment_catalog()
    except EquipmentCatalogError:
        return
    validate_skill_names(rules, SKILL_RULES_FILE)


@register()
def check_skill_rules(
    app_configs: Sequence[AppConfig] | None = None, **kwargs: Any
) -> list[CheckMessage]:
    """Load the canon's skills and experience rules and check their skill names.

    Each skill the checks name must be a glossary skill in its own right
    (:func:`characters.skills.validate_skill_names`), which the loader alone
    cannot see.

    Args:
        app_configs: The apps being checked, or ``None`` for all; the canon
            belongs to no one app, so it is checked whichever are named.
        **kwargs: Django's other check arguments, unused.

    Returns:
        No messages when the rules load; otherwise an :class:`Error` carrying
        the loader's message, which names the file and the field.
    """
    return _rules_file_refusals(
        _skill_rules_against_catalog,
        SkillRulesError,
        CANON_SKILL_RULES_FILE_NAME,
        "characters/skill_rules.py",
        SKILL_RULES_CHECK_ID,
    )


def _catalog_against_rules() -> None:
    """Load the catalog and check its classes, tiers and armour codes.

    Raises:
        EquipmentCatalogError: The catalog is refused, names a weapon class
            or armour tier the combat matrix lacks, or an armour restriction
            code the rules do not give.
    """
    catalog = equipment_catalog()
    validate_catalog_classes(catalog, EQUIPMENT_CATALOG_FILE)
    try:
        rules = equipment_rules()
    except EquipmentRulesError:
        # The rules' own refusal is characters.E004's to report; with no
        # rules there is nothing to check the codes against.
        return
    validate_armour_restriction_codes(catalog, rules, EQUIPMENT_CATALOG_FILE)


def _rules_against_catalog() -> None:
    """Load the equipment rules and check their Missile classes against the
    catalog.

    Raises:
        EquipmentRulesError: The rules are refused, or name a Missile class
            no catalog weapon has.
    """
    rules = equipment_rules()
    try:
        catalog = equipment_catalog()
    except EquipmentCatalogError:
        # The catalog's own refusal is characters.E003's to report.
        return
    validate_missile_classes(rules, catalog, EQUIPMENT_RULES_FILE)


@register()
def check_equipment_catalog(
    app_configs: Sequence[AppConfig] | None = None, **kwargs: Any
) -> list[CheckMessage]:
    """Load the canon's equipment catalog and check it against the code and
    the rules.

    Each weapon class and armour tier must be one the combat matrix has
    (:func:`characters.combat.validate_catalog_classes`), and each armour's
    ``movement_restriction`` a code the equipment rules give
    (:func:`characters.equipment_rules.validate_armour_restriction_codes`),
    which the catalog's loader alone cannot see.

    Args:
        app_configs: The apps being checked, or ``None`` for all; the canon
            belongs to no one app, so it is checked whichever are named.
        **kwargs: Django's other check arguments, unused.

    Returns:
        No messages when the catalog loads and agrees with the rules;
        otherwise an :class:`Error` carrying the refusal, which names the
        file and the field.
    """
    return _rules_file_refusals(
        _catalog_against_rules,
        EquipmentCatalogError,
        CANON_EQUIPMENT_CATALOG_FILE_NAME,
        "characters/equipment_rules.py",
        EQUIPMENT_CATALOG_CHECK_ID,
    )


@register()
def check_equipment_rules(
    app_configs: Sequence[AppConfig] | None = None, **kwargs: Any
) -> list[CheckMessage]:
    """Load the canon's equipment rules and check their Missile classes.

    Each Missile class must be a class some catalog weapon has
    (:func:`characters.equipment_rules.validate_missile_classes`), which the
    rules' loader alone cannot see.

    Args:
        app_configs: The apps being checked, or ``None`` for all; the canon
            belongs to no one app, so it is checked whichever are named.
        **kwargs: Django's other check arguments, unused.

    Returns:
        No messages when the rules load and agree with the catalog; otherwise
        an :class:`Error` carrying the refusal, which names the file and the
        field.
    """
    return _rules_file_refusals(
        _rules_against_catalog,
        EquipmentRulesError,
        CANON_EQUIPMENT_RULES_FILE_NAME,
        "characters/equipment_rules.py",
        EQUIPMENT_RULES_CHECK_ID,
    )


def _storable_labels(rules: CharacterRules) -> Iterator[tuple[str, str, str]]:
    """Each label a ``Character`` column may have to hold, with its field and
    table: the table fields' rows and, for ``provenance_detail``, every detail
    table's rows."""
    for field_name, table_name in CHARACTER_TABLE_FIELDS.items():
        for label in getattr(rules, table_name).labels:
            yield field_name, table_name, label
    for table_name in SOCIAL_CLASS_DETAIL_TABLES:
        for label in rules.detail_table(table_name).labels:
            yield "provenance_detail", table_name, label


@register()
def check_character_labels_fit_their_columns(
    app_configs: Sequence[AppConfig] | None = None, **kwargs: Any
) -> list[CheckMessage]:
    """Every canon table label a character may hold fits its column (issue #66).

    The background fields hold the canon's row labels with no ``choices``
    of their own, so a label longer than the column would be refused only on
    the first save that reached it. A canon whose rules do not load is
    ``characters.E001``'s to report.

    Args:
        app_configs: The apps being checked, or ``None`` for all; the canon
            belongs to no one app, so it is checked whichever are named.
        **kwargs: Django's other check arguments, unused.

    Returns:
        One :class:`Error` per label too long for its column.
    """
    try:
        rules = character_rules()
    except CharacterRulesError:
        return []
    messages: list[CheckMessage] = []
    for field_name, table_name, label in _storable_labels(rules):
        limit = column_length(Character, field_name)
        if len(label) > limit:
            messages.append(
                Error(
                    f"{CHARACTER_RULES_FILE}: {table_name} label {label!r} is "
                    f"{len(label)} characters, but Character.{field_name} holds "
                    f"at most {limit}",
                    hint="Shorten the label; the column widths are the model's.",
                    id=CHARACTER_LABEL_COLUMNS_CHECK_ID,
                )
            )
    return messages


def granted_game_master_started_skills(
    canon_skill_rules: SkillRules, canon_character_rules: CharacterRules
) -> list[str]:
    """Each canon list that grants a skill only a Game Master starts.

    Four paths give a character a skill from a canon list and ask nobody who
    may start it: a year of prior experience (a track's d20 table, and the
    skill its pay rides on), the Origin's grant, the background roll (the
    sheet's and the Games Master chat's ``roll_background`` and
    ``set_background``, which grant through the Origin), and a player's
    import, which grants the Origin's skill. They read two lists between
    them, so holding the lists holds all four.

    Args:
        canon_skill_rules: The canon's skill rules.
        canon_character_rules: The canon's character rules.

    Returns:
        Where each such skill is named, as the two files spell it; empty when
        none is.

    Raises:
        EquipmentCatalogError: The catalog, which says whether a name is a
            Weapon skill, is refused.
    """

    def only_a_game_master_starts(written: str) -> bool:
        return is_game_master_started(normalize_skill_name(written), canon_skill_rules)

    found = []
    for key, track in canon_skill_rules.tracks.tracks.items():
        found.extend(
            f"{CANON_SKILL_RULES_FILE_NAME} tracks.{key}.skills names {written!r}"
            for written in dict.fromkeys(track.skills)
            if only_a_game_master_starts(written)
        )
        if track.money_skill is not None and only_a_game_master_starts(
            track.money_skill
        ):
            found.append(
                f"{CANON_SKILL_RULES_FILE_NAME} tracks.{key}.money_skill names "
                f"{track.money_skill!r}"
            )
    for row in canon_character_rules.location.rows:
        found.extend(
            f"{CANON_CHARACTER_RULES_FILE_NAME} location row {row.label!r} grants "
            f"{written!r}"
            for written in row.skills
            if only_a_game_master_starts(written)
        )
    return found


@register()
def check_game_master_started_skills(
    app_configs: Sequence[AppConfig] | None = None, **kwargs: Any
) -> list[CheckMessage]:
    """No track and no Origin grants a skill only a Game Master starts
    (tarmar-studio #208).

    A player is held to ``game_master_started`` where a free point is spent
    (:func:`characters.services.allocation_refusal`). Prior experience, the
    Origin's grant, the background roll and a player's import give a skill
    from a canon list with no such question, so a canon that put Arcane on a
    track's table would hand players the invitation. A canon whose rules or
    catalog do not load is ``characters.E001``'s, ``E002``'s or ``E003``'s
    to report.

    Args:
        app_configs: The apps being checked, or ``None`` for all; the canon
            belongs to no one app, so it is checked whichever are named.
        **kwargs: Django's other check arguments, unused.

    Returns:
        One :class:`Error` naming every list that grants such a skill, or no
        messages.
    """
    try:
        granted = granted_game_master_started_skills(skill_rules(), character_rules())
    except (SkillRulesError, CharacterRulesError, EquipmentCatalogError):
        return []
    if not granted:
        return []
    return [
        Error(
            f"{'; '.join(granted)}: {GAME_MASTER_STARTED_BLOCK} leaves each to a "
            "Game Master to start, and a track's table or an Origin's row gives "
            "a skill to any character",
            hint=(
                "Take the skill off the track or the Origin, or out of "
                f"{GAME_MASTER_STARTED_BLOCK} in {CANON_SKILL_RULES_FILE_NAME}."
            ),
            id=GAME_MASTER_STARTED_CHECK_ID,
        )
    ]
