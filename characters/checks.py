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
"""

from collections.abc import Callable, Sequence
from typing import Any

from django.apps import AppConfig
from django.core.checks import CheckMessage, Error, register

from reference.paths import (
    CANON_CHARACTER_RULES_FILE_NAME,
    CANON_EQUIPMENT_CATALOG_FILE_NAME,
    CANON_EQUIPMENT_RULES_FILE_NAME,
    CANON_SKILL_RULES_FILE_NAME,
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
from .rules import CharacterRulesError, character_rules
from .skill_rules import SkillRulesError, skill_rules
from .skills import validate_skill_names

#: The check id a refused ``character-rules.json`` reports under.
CHARACTER_RULES_CHECK_ID = "characters.E001"
#: The check id a refused ``skill-rules.json`` reports under.
SKILL_RULES_CHECK_ID = "characters.E002"
#: The check id a refused ``equipment.json`` reports under.
EQUIPMENT_CATALOG_CHECK_ID = "characters.E003"
#: The check id a refused ``equipment-rules.json`` reports under.
EQUIPMENT_RULES_CHECK_ID = "characters.E004"


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
        lambda: validate_skill_names(skill_rules(), SKILL_RULES_FILE),
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
