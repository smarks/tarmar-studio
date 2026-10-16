"""Django system checks for the characters app (issues #59 and #70).

The canon's ``character-rules.json`` is read the first time any character
code asks for it: constructing a ``Character`` (its age default), rendering a
sheet, building the Games Master's extraction schema, importing a character,
creating or rolling one. Its ``skill-rules.json`` is read the same way, the
first time skills, prior experience or a check need it. A file the loader
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
    CANON_SKILL_RULES_FILE_NAME,
    SKILL_RULES_FILE,
)
from reference.rules_file import RulesFileError

from .rules import CharacterRulesError, character_rules
from .skill_rules import SkillRulesError, skill_rules
from .skills import validate_skill_names

#: The check id a refused ``character-rules.json`` reports under.
CHARACTER_RULES_CHECK_ID = "characters.E001"
#: The check id a refused ``skill-rules.json`` reports under.
SKILL_RULES_CHECK_ID = "characters.E002"


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
                    f"Every canon carries a {file_name} beside canon.json. "
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
