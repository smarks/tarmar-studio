"""Django system checks for the battle app (issue #960).

The canon's ``magic-rules.json`` is read on a battle's first turn. A file the
loader refuses, or a canon without one, would otherwise surface only then, as
an error in the middle of somebody's fight. Checked here, it fails
``manage.py check`` and the deploy's ``migrate`` (which runs the system
checks) before any battle is seated.
"""

from collections.abc import Sequence
from typing import Any

from django.apps import AppConfig
from django.core.checks import CheckMessage, Error, register

from characters.skill_checks import is_glossary_skill
from characters.skills import glossary_skill
from reference.paths import MAGIC_RULES_FILE

from .magic_rules import MagicRulesError, magic_rules

#: The check id a refused ``magic-rules.json`` reports under.
MAGIC_RULES_CHECK_ID = "battle.E001"
#: The check id a Channel source skill the glossary does not name reports under.
CHANNEL_SOURCE_SKILL_CHECK_ID = "battle.E002"

#: Where the schema is written, for every message's hint.
SCHEMA_HINT = "The schema is in battle/magic_rules.py."


def unknown_channel_source_skills() -> list[str]:
    """The Channel ``source_skill`` names a character's record cannot hold.

    The seat reads each by its glossary name
    (:func:`battle.services._channel_skill_levels`), so a name must be a bare
    glossary skill as the skills code stores it: lower case, no chosen part.
    Any other would read level 0 for every caster without a word.

    Returns:
        The refused names, sorted; empty when the canon bounds no casting by
        Channel.

    Raises:
        MagicRulesError: The canon's file is missing or malformed.
    """
    channel = magic_rules().channel
    if channel is None:
        return []
    named = {
        derivation.source_skill
        for derivation in channel.derivations.values()
        if derivation.source_skill is not None
    }
    return sorted(
        name
        for name in named
        if glossary_skill(name) != name or not is_glossary_skill(name)
    )


@register()
def check_magic_rules(
    app_configs: Sequence[AppConfig] | None = None, **kwargs: Any
) -> list[CheckMessage]:
    """Load the canon's magic rules and check the skills its Channel reads.

    Args:
        app_configs: The apps being checked, or ``None`` for all; the canon
            belongs to no one app, so it is checked whichever are named.
        **kwargs: Django's other check arguments, unused.

    Returns:
        No messages when the rules load and name only glossary skills;
        otherwise an :class:`Error` carrying the loader's message (which
        names the file and the field), or one naming the unknown skills.
    """
    try:
        unknown = unknown_channel_source_skills()
    except MagicRulesError as error:
        return [
            Error(
                str(error),
                hint=(
                    "Every canon carries a magic-rules.json beside canon.json. "
                    f"{SCHEMA_HINT}"
                ),
                id=MAGIC_RULES_CHECK_ID,
            )
        ]
    if unknown:
        return [
            Error(
                f"{MAGIC_RULES_FILE}: channel.derivations names source skills "
                f"the skills glossary does not have: {unknown}",
                hint=(
                    "A source_skill is a glossary skill as the skills code "
                    f"stores it, e.g. lower case with no chosen part. {SCHEMA_HINT}"
                ),
                id=CHANNEL_SOURCE_SKILL_CHECK_ID,
            )
        ]
    return []
