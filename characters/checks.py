"""Django system checks for the characters app (issue #59).

The canon's ``character-rules.json`` is read the first time any character
code asks for it: constructing a ``Character`` (its age default), rendering a
sheet, building the Games Master's extraction schema, importing a character,
creating or rolling one. A file the loader refuses, or a canon without one,
would otherwise surface only then, as an error in the middle of whichever of
those came first. Checked here, it fails ``manage.py check`` and the
deploy's ``migrate`` (which runs the system checks) before anyone builds a
character.
"""

from collections.abc import Sequence
from typing import Any

from django.apps import AppConfig
from django.core.checks import CheckMessage, Error, register

from .rules import CharacterRulesError, character_rules

#: The check id a refused ``character-rules.json`` reports under.
CHARACTER_RULES_CHECK_ID = "characters.E001"


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
    try:
        character_rules()
    except CharacterRulesError as error:
        return [
            Error(
                str(error),
                hint=(
                    "Every canon carries a character-rules.json beside "
                    "canon.json. The schema is in characters/rules.py."
                ),
                id=CHARACTER_RULES_CHECK_ID,
            )
        ]
    return []
