"""The character, skills, experience and equipment rules a test runs under.

Character creation reads its numbers from the loaded canon through
:func:`characters.rules.character_rules` (#59), skills and prior experience
through :func:`characters.skill_rules.skill_rules` (#70), and the equipment
catalog and rules through :func:`characters.equipment_rules.equipment_catalog`
and :func:`characters.equipment_rules.equipment_rules` (#75). The drift tests
hold the default canon's files to the public pages whichever canon is loaded,
so they read the ``DEFAULT_`` values below; a test that needs the code to run
under particular rules seats them with the matching ``seated_`` context.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

from characters.equipment_rules import (
    EquipmentCatalog,
    EquipmentRules,
    equipment_catalog,
    equipment_rules,
    load_equipment_catalog,
    load_equipment_rules,
)
from characters.rules import CharacterRules, character_rules, load_character_rules
from characters.skill_rules import SkillRules, load_skill_rules, skill_rules
from reference.paths import (
    CANON_CHARACTER_RULES_FILE_NAME,
    CANON_EQUIPMENT_CATALOG_FILE_NAME,
    CANON_EQUIPMENT_RULES_FILE_NAME,
    CANON_SKILL_RULES_FILE_NAME,
    DEFAULT_CANON_DIR,
)
from reference.rules_file import forget_values_derived_from_rules

#: The default canon's rules: the numbers the public character pages state.
DEFAULT_CHARACTER_RULES: CharacterRules = load_character_rules(
    DEFAULT_CANON_DIR / CANON_CHARACTER_RULES_FILE_NAME
)

#: The default canon's skills and experience rules: the numbers the public
#: attributes, prior experience and skills pages state.
DEFAULT_SKILL_RULES: SkillRules = load_skill_rules(
    DEFAULT_CANON_DIR / CANON_SKILL_RULES_FILE_NAME
)

#: The default canon's equipment catalog: the items the public equipment pages
#: list.
DEFAULT_EQUIPMENT_CATALOG: EquipmentCatalog = load_equipment_catalog(
    DEFAULT_CANON_DIR / CANON_EQUIPMENT_CATALOG_FILE_NAME
)

#: The default canon's equipment rules: the numbers the public movement and
#: armour pages state.
DEFAULT_EQUIPMENT_RULES: EquipmentRules = load_equipment_rules(
    DEFAULT_CANON_DIR / CANON_EQUIPMENT_RULES_FILE_NAME
)


def _forget_loaded_rules() -> None:
    """Forget every rules file and every value built from them.

    The Games Master's prompts, and the patterns naming the canon's skills and
    tracks, are built from the loaded rules once and held for the process
    (:func:`reference.rules_file.derived_from_rules`), so a seated test reads
    values built from the seated rules, and no later test reads those.
    """
    character_rules.cache_clear()
    skill_rules.cache_clear()
    equipment_catalog.cache_clear()
    equipment_rules.cache_clear()
    forget_values_derived_from_rules()


@contextmanager
def _seated(loader: str, loaded: object) -> Iterator[None]:
    """Patch the loader ``loader`` names to answer ``loaded``, with every
    loaded rules file and derived value forgotten before and after.

    Args:
        loader: The dotted path of a ``load_*`` function whose cached caller
            every reader goes through.
        loaded: What that loader returns while seated.
    """
    _forget_loaded_rules()
    try:
        with patch(loader, return_value=loaded):
            yield
    finally:
        _forget_loaded_rules()


@contextmanager
def seated_character_rules(
    rules: CharacterRules = DEFAULT_CHARACTER_RULES,
) -> Iterator[None]:
    """Seat ``rules`` as the loaded canon's for every caller, then forget them.

    Args:
        rules: The rules :func:`characters.rules.character_rules` answers with.
    """
    with _seated("characters.rules.load_character_rules", rules):
        yield


@contextmanager
def seated_skill_rules(rules: SkillRules = DEFAULT_SKILL_RULES) -> Iterator[None]:
    """Seat ``rules`` as the loaded canon's for every caller, then forget them.

    Args:
        rules: The rules :func:`characters.skill_rules.skill_rules` answers
            with.
    """
    with _seated("characters.skill_rules.load_skill_rules", rules):
        yield


@contextmanager
def seated_equipment_catalog(
    catalog: EquipmentCatalog = DEFAULT_EQUIPMENT_CATALOG,
) -> Iterator[None]:
    """Seat ``catalog`` as the loaded canon's for every caller, then forget it.

    Args:
        catalog: The catalog
            :func:`characters.equipment_rules.equipment_catalog` answers with.
    """
    with _seated("characters.equipment_rules.load_equipment_catalog", catalog):
        yield


@contextmanager
def seated_equipment_rules(
    rules: EquipmentRules = DEFAULT_EQUIPMENT_RULES,
) -> Iterator[None]:
    """Seat ``rules`` as the loaded canon's for every caller, then forget them.

    Args:
        rules: The rules :func:`characters.equipment_rules.equipment_rules`
            answers with.
    """
    with _seated("characters.equipment_rules.load_equipment_rules", rules):
        yield
