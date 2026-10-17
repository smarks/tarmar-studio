"""Django system checks for the battle app (issues #960 and #75).

The canon's ``magic-rules.json`` is read on a battle's first turn. A file the
loader refuses, or a canon without one, would otherwise surface only then, as
an error in the middle of somebody's fight. Checked here, it fails
``manage.py check`` and the deploy's ``migrate`` (which runs the system
checks) before any battle is seated. The same goes for a canon whose gaits
the engine cannot seat (:func:`check_engine_gaits`), that lacks the
encumbrance level the battle generator deals up to
(:func:`check_generated_load`), or that lacks an item the battle names
(:func:`check_generator_catalog_ids`).
"""

from collections.abc import Sequence
from typing import Any

from django.apps import AppConfig
from django.core.checks import CheckMessage, Error, register

from characters.equipment_rules import (
    EquipmentCatalogError,
    EquipmentRulesError,
    equipment_catalog,
    equipment_rules,
)
from characters.skill_checks import is_glossary_skill
from characters.skills import glossary_skill
from reference.paths import (
    EQUIPMENT_CATALOG_FILE,
    EQUIPMENT_RULES_FILE,
    MAGIC_RULES_FILE,
)

from .adaptation import ENGINE_GAITS, engine_gait_names
from .generation import HEAVIEST_GENERATED_LOAD, catalog_ids_refused
from .magic_rules import MagicRulesError, magic_rules

#: The check id a refused ``magic-rules.json`` reports under.
MAGIC_RULES_CHECK_ID = "battle.E001"
#: The check id a Channel source skill the glossary does not name reports under.
CHANNEL_SOURCE_SKILL_CHECK_ID = "battle.E002"
#: The check id a canon whose gaits the engine cannot seat reports under.
ENGINE_GAITS_CHECK_ID = "battle.E003"
#: The check id a canon without the generator's heaviest load reports under.
GENERATED_LOAD_CHECK_ID = "battle.E004"
#: The check id a canon without an item the battle names reports under.
GENERATOR_CATALOG_CHECK_ID = "battle.E005"

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


@register()
def check_engine_gaits(
    app_configs: Sequence[AppConfig] | None = None, **kwargs: Any
) -> list[CheckMessage]:
    """Require the canon to name as many gaits as the battle engine seats.

    The canon names its own gaits and the sheet reads any number of them, but
    the battle engine's are fixed: four, sprint, run, jog and walk
    (:data:`battle.adaptation.ENGINE_GAITS`), each carrying the engine's own
    rules (a charge's reach is its jog, for one). A
    battle seats the canon's gaits onto them by place, fastest first
    (:func:`battle.adaptation.engine_gait_names`), which needs exactly four.
    This is the code's constraint, not a reading of the rules: stage 6 of
    rules-as-data, which seats the engine's own rules from the canon, lifts
    it (issue #75).

    Args:
        app_configs: The apps being checked, or ``None`` for all; the canon
            belongs to no one app, so it is checked whichever are named.
        **kwargs: Django's other check arguments, unused.

    The gait seated as the engine's run must also go some distance: a
    beast's walk and jog are fractions of it
    (:func:`battle.adaptation.gait_proportions`), and a beast's movement
    modifier is measured from it.

    Returns:
        No messages when the canon names four gaits and its run goes some
        distance, or when its rules file is refused (``characters.E004``
        reports that); otherwise an :class:`Error` naming the gaits.
    """
    try:
        gaits = equipment_rules().gaits
    except EquipmentRulesError:
        # The file's own refusal is characters.E004's to report.
        return []
    hint = (
        "The engine's gaits are fixed until stage 6 of rules-as-data seats its "
        "rules from the canon. The schema is in characters/equipment_rules.py."
    )
    if len(gaits.order) != len(ENGINE_GAITS):
        return [
            Error(
                f"{EQUIPMENT_RULES_FILE}: gaits.order names {len(gaits.order)} "
                f"gaits {list(gaits.order)}, but the battle engine seats exactly "
                f"{len(ENGINE_GAITS)}, matched by place, fastest first, to its "
                f"{list(ENGINE_GAITS)}",
                hint=hint,
                id=ENGINE_GAITS_CHECK_ID,
            )
        ]
    run = engine_gait_names()["run"]
    if gaits.base_distances[run] == 0:
        return [
            Error(
                f"{EQUIPMENT_RULES_FILE}: gaits.base_distances.{run} is 0, but "
                f"{run!r} is the gait the battle engine seats as its run, from "
                "which a beast's walk and jog are measured, so it must go some "
                "distance",
                hint=hint,
                id=ENGINE_GAITS_CHECK_ID,
            )
        ]
    return []


@register()
def check_generated_load(
    app_configs: Sequence[AppConfig] | None = None, **kwargs: Any
) -> list[CheckMessage]:
    """Require the canon to name the level the battle generator deals up to.

    :data:`battle.generation.HEAVIEST_GENERATED_LOAD` is the generator's own
    choice of encumbrance level, read by its key, so a canon that names its
    levels otherwise would fail the first battle a Game Master generates
    (issue #75).

    Args:
        app_configs: The apps being checked, or ``None`` for all; the canon
            belongs to no one app, so it is checked whichever are named.
        **kwargs: Django's other check arguments, unused.

    Returns:
        No messages when the canon names the level, or when its rules file is
        refused (``characters.E004`` reports that); otherwise an
        :class:`Error` naming the levels.
    """
    try:
        keys = equipment_rules().encumbrance.keys
    except EquipmentRulesError:
        # The file's own refusal is characters.E004's to report.
        return []
    if HEAVIEST_GENERATED_LOAD in keys:
        return []
    return [
        Error(
            f"{EQUIPMENT_RULES_FILE}: encumbrance.levels names {list(keys)}, "
            f"but the battle generator deals loads up to the level "
            f"{HEAVIEST_GENERATED_LOAD!r}",
            hint=(
                "HEAVIEST_GENERATED_LOAD in battle/generation.py is the "
                "generator's choice; a canon names that level, or the choice "
                "changes with it."
            ),
            id=GENERATED_LOAD_CHECK_ID,
        )
    ]


@register()
def check_generator_catalog_ids(
    app_configs: Sequence[AppConfig] | None = None, **kwargs: Any
) -> list[CheckMessage]:
    """Require the canon to carry every item the battle names by id.

    The archetypes' kits, their step-down tables, the arrows and the bows'
    class, and the hand-to-hand weapon are the battle's own choices
    (:func:`battle.generation.catalog_ids_refused`), read by id, so a canon
    without one would fail the first battle a Game Master generates (issue
    #75).

    Args:
        app_configs: The apps being checked, or ``None`` for all; the canon
            belongs to no one app, so it is checked whichever are named.
        **kwargs: Django's other check arguments, unused.

    Returns:
        No messages when the canon carries them all, or when its catalog or
        rules file is refused (``characters.E003`` and ``characters.E004``
        report those); otherwise one :class:`Error` naming each id.
    """
    try:
        equipment_catalog()
        equipment_rules()
    except (EquipmentCatalogError, EquipmentRulesError):
        # The files' own refusals are characters.E003's and E004's to report.
        return []
    refused = catalog_ids_refused()
    if not refused:
        return []
    return [
        Error(
            f"{EQUIPMENT_CATALOG_FILE}: the battle names items the canon does "
            f"not carry: {'; '.join(refused)}",
            hint=(
                "The kits, step-downs, arrows and hand-to-hand weapon in "
                "battle/generation.py and battle/services.py are the battle's "
                "choices; a canon carries them, or the choices change with it."
            ),
            id=GENERATOR_CATALOG_CHECK_ID,
        )
    ]
