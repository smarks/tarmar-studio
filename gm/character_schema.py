"""The JSON the GM is asked for when a player saves a character (issue #344).

Django-free on purpose: the CLI's ``/character`` command imports this module
without a settings file, exactly as it does ``gm.engine``.

The GM walks a player through creation in prose. Rather than parse that
prose, ``/character`` asks the model for the character as JSON constrained
by :func:`character_json_schema` (Ollama's structured outputs: the schema
goes in the ``/api/chat`` request's ``format`` field). The schema covers
what the player decided -- attributes, background, skills, gear, prior
experience -- and nothing the rules derive: Fatigue, Body, modifiers and
the pools are computed by ``characters.models.Character`` from those inputs,
so a number the model misremembers cannot reach the sheet.

:func:`extraction_to_import_payload` turns the model's answer into the
``tarmar-character`` document ``characters.import_service`` validates; this
module never validates anything itself, so there is one validator.
"""

import functools
import json
from dataclasses import dataclass
from typing import Any

from characters.constants import (
    CHARACTER_EXPORT_FORMAT,
    CORE_ATTRIBUTE_FIELDS,
)
from characters.equipment_rules import equipment_catalog
from characters.rules import (
    CharacterRules,
    DiceRollRules,
    character_rules,
    game_master_origins_in_words,
)
from characters.skills import normalize_skill_name
from common.services.sse import strip_thinking
from reference.rules_file import derived_from_rules


def _roll_text(name: str, roll: DiceRollRules) -> str:
    """A roll as the ask and the schema name it: "the 2d6 rolled for
    Fatigue (2-12)" on the public pages, the canon's dice and range."""
    return f"the {roll.dice} rolled for {name} ({roll.minimum}-{roll.maximum})"


def _literacy_ruling_rule(rules: CharacterRules) -> str:
    """What the extraction prompt says ``literacy_ruling`` is, naming the
    canon's origins left to the Game Master (#128).

    Args:
        rules: The canon's character rules.
    """
    origins = game_master_origins_in_words(rules)
    if not origins:
        return "literacy_ruling is always null: every Origin has a check to roll"
    return (
        "literacy_ruling is the Game Master's ruling at an Origin of "
        f"{origins}, or null"
    )


def _literacy_ruling_description(rules: CharacterRules) -> str:
    """The schema's description of ``literacy_ruling``, naming the canon's
    origins left to the Game Master (#128).

    Args:
        rules: The canon's character rules.
    """
    origins = game_master_origins_in_words(rules)
    if not origins:
        return "Always null: every Origin has a literacy check to roll"
    return (
        f"Only for an Origin of {origins}: true if the Game Master ruled the "
        "character literate, false if illiterate; null if he did not rule"
    )


def extraction_prompt(*, for_game_master: bool = False) -> str:
    """What the model is told when the player runs /character.

    The numbers it must not produce (Fatigue, Body) are named so it does not
    fold them into the fields it does produce. The rolls' dice and ranges are
    the loaded canon's (issue #59), read when asked.

    Args:
        for_game_master: True when the person at the table is a DM or admin
            (issue #352). Their character may hold values no player could
            roll or buy, and the extraction must carry those through rather
            than tidy them back into range, so a note says so.
    """
    rules = character_rules()
    prompt = (
        "The player wants to save the character you have been creating together "
        "in this conversation. Report that character as JSON matching the schema "
        "you were given, using exactly the values the conversation settled on. "
        f"Rules: fatigue_roll is only {_roll_text('Fatigue', rules.fatigue_roll)}, "
        "never the Fatigue total; literacy_roll is only "
        f"{_roll_text('the literacy check', rules.literacy_roll)}, or null when "
        f"it was never rolled; {_literacy_ruling_rule(rules)}; do not report "
        "Fatigue, "
        "Body, modifiers or whether the character can read -- they are "
        "computed from the attributes. Attributes are the values before any "
        "aging. Skills are the skills the character has with their points; "
        "equipment is what they carry; age, total_xp and money_earned reflect "
        "any prior experience bought. Where the conversation never decided a "
        "field, use the schema's sensible default rather than inventing a value."
    )
    if not for_game_master:
        return prompt
    return prompt + (
        " This character was built by the Game Master, who may set any value "
        "they like. Report exactly the numbers the conversation settled on, "
        f"including attributes above {rules.attributes.point_buy.maximum} and "
        "skills no experience paid for; do not cap, round or normalise them."
    )


def _starting_coin_description(rules: CharacterRules) -> str:
    """The starting coin field's description, with the default level's coin.

    "Coin the character starts with (100 for Moderate if unrolled)": the
    canon's default wealth row (``defaults.wealth``), the level an unrolled
    character has, and its coin (issues #68, #66).

    Args:
        rules: The loaded canon's creation rules.
    """
    row = rules.default_wealth_row
    return (
        f"Coin the character starts with ({row.coin_text} for {row.label} if unrolled)"
    )


# Extraction wants the one right answer, not a creative one.
EXTRACTION_TEMPERATURE = 0

# The provenance, location and wealth choices, the creation bounds, the rolls'
# ranges and the starting age are the loaded canon's (``characters.rules``,
# issue #59), read when a schema is built: this module is imported by the
# offline CLI, and a canon that will not load is reported by the system
# check, not by an import.
#
# Creation bounds: the point buy's range, the values a character can be
# *created* with, which is what the model is describing. The importer
# enforces the storage bounds. What a DM or admin may set (issue #352) is the
# storage floor below and the canon's attribute ceiling, which is what
# ``characters.models.ATTRIBUTE_VALIDATORS`` enforces on save; narrowing the
# schema below these would cap the Game Master's own numbers before the
# importer ever saw them.
GAME_MASTER_ATTRIBUTE_MINIMUM = 1


@dataclass(frozen=True)
class SchemaField:
    """One property of the extraction schema.

    ``json_type`` is the JSON-schema type; ``items`` carries the schema of
    an array's elements. ``enum``, ``minimum`` and ``maximum`` are emitted
    when set. Every field is required -- Ollama's structured outputs fill
    every required property, which is how a field the conversation never
    settled gets the default the description names. A ``nullable`` field's
    type admits ``null`` as well, for a record that stays empty until it is
    made, such as a literacy roll nobody rolled (issue #742): no default
    number is honest there.
    """

    name: str
    json_type: str
    description: str
    enum: tuple[str, ...] | None = None
    minimum: int | None = None
    maximum: int | None = None
    items: dict[str, Any] | None = None
    nullable: bool = False

    def as_json_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "type": [self.json_type, "null"] if self.nullable else self.json_type,
            "description": self.description,
        }
        if self.enum is not None:
            schema["enum"] = list(self.enum)
        if self.minimum is not None:
            schema["minimum"] = self.minimum
        if self.maximum is not None:
            schema["maximum"] = self.maximum
        if self.items is not None:
            schema["items"] = self.items
        return schema


def _attribute_field(name: str, label: str, minimum: int, maximum: int) -> SchemaField:
    """One core-attribute property, bounded by the range its author may use."""
    return SchemaField(
        name,
        "integer",
        f"{label} as rolled or bought, {minimum}-{maximum}",
        minimum=minimum,
        maximum=maximum,
    )


def _object_schema(fields: tuple[SchemaField, ...]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {field.name: field.as_json_schema() for field in fields},
        "required": [field.name for field in fields],
    }


SKILL_FIELDS: tuple[SchemaField, ...] = (
    SchemaField("name", "string", "The skill's name, e.g. sword"),
    SchemaField("points", "integer", "Skill points held in it", minimum=0),
)

EQUIPMENT_FIELDS: tuple[SchemaField, ...] = (
    SchemaField("name", "string", "The item's name, e.g. Dagger"),
    SchemaField("quantity", "integer", "How many; 1 if a single item", minimum=1),
    SchemaField("equipped", "boolean", "Worn or wielded right now"),
    SchemaField("in_pack", "boolean", "Carried in the pack rather than to hand"),
)


def _character_fields(
    rules: CharacterRules, attribute_minimum: int, attribute_maximum: int
) -> tuple[SchemaField, ...]:
    """Every property of the extraction schema, with the given attribute bounds.

    One statement of the field list; the two bound sets
    (:func:`character_fields`) are the only difference between what a
    player's character and the Game Master's may hold (issue #352).
    """
    return (
        SchemaField("name", "string", "The character's name"),
        *(
            _attribute_field(field_name, label, attribute_minimum, attribute_maximum)
            for field_name, label in CORE_ATTRIBUTE_FIELDS
        ),
        SchemaField(
            "appearance_roll",
            "string",
            "The Appearance die as rolled (1 pretty to 6 ugly), e.g. '3'; '3' if unrolled",
        ),
        SchemaField(
            "height_roll",
            "string",
            "The Height die as rolled (1 to 6), e.g. '4'; '3' if unrolled",
        ),
        SchemaField(
            "weight_roll",
            "string",
            "The Weight die as rolled (1 to 6), e.g. '2'; '3' if unrolled",
        ),
        SchemaField(
            "fatigue_roll",
            "integer",
            f"Only {_roll_text('Fatigue', rules.fatigue_roll)} -- NOT the "
            "Fatigue total",
            minimum=rules.fatigue_roll.minimum,
            maximum=rules.fatigue_roll.maximum,
        ),
        SchemaField(
            "provenance_class",
            "string",
            "Social class of birth",
            enum=rules.social_class.labels,
        ),
        SchemaField(
            "provenance_detail",
            "string",
            "The family's trade or station, e.g. Laborer, Minor noble house",
        ),
        SchemaField(
            "location",
            "string",
            "Where the character grew up",
            enum=rules.location.labels,
        ),
        # Named as the export names it, because the importer reads export
        # field names: the one record of the literacy check (issue #742).
        # Whether it passes is read from the origin and INT, never reported.
        SchemaField(
            "literacy_roll",
            "integer",
            f"Only {_roll_text('the literacy check', rules.literacy_roll)}; null "
            "if it was never rolled",
            minimum=rules.literacy_roll.minimum,
            maximum=rules.literacy_roll.maximum,
            nullable=True,
        ),
        # A Game Master's ruling at "Consult GM", which has no check (#743):
        # kept by his import, dropped by a player's and at any other origin.
        SchemaField(
            "literacy_ruling",
            "boolean",
            _literacy_ruling_description(rules),
            nullable=True,
        ),
        SchemaField(
            "wealth_level", "string", "Family wealth", enum=rules.wealth.labels
        ),
        SchemaField(
            "starting_coin",
            "integer",
            _starting_coin_description(rules),
            minimum=0,
        ),
        SchemaField(
            "skills",
            "array",
            "Every skill the character has, with points; empty if none",
            items=_object_schema(SKILL_FIELDS),
        ),
        SchemaField(
            "free_skill_points",
            "integer",
            "Skill points not yet spent; 0 if none",
            minimum=0,
        ),
        SchemaField(
            "equipment",
            "array",
            "Everything the character carries; empty if nothing",
            items=_object_schema(EQUIPMENT_FIELDS),
        ),
        SchemaField(
            "age",
            "integer",
            f"Current age; {rules.age.starting} with no prior experience",
            minimum=rules.age.starting,
        ),
        SchemaField(
            "total_xp",
            "integer",
            "Experience points from prior experience; 0 if none",
            minimum=0,
        ),
        SchemaField(
            "money_earned",
            "integer",
            "Coin earned through prior experience; 0 if none",
            minimum=0,
        ),
    )


def character_fields(*, for_game_master: bool = False) -> tuple[SchemaField, ...]:
    """The extraction schema's properties, bounded for whoever is saving.

    Built from the loaded canon's rules when asked (issue #59), never at
    import; the same rules give the same fields, so the schema a model sees
    does not change between turns.

    Args:
        for_game_master: True when a DM or admin is saving: attributes are
            bounded by storage and the attribute ceiling rather than by the
            point buy's range (issue #352).
    """
    rules = character_rules()
    if for_game_master:
        return _character_fields(
            rules, GAME_MASTER_ATTRIBUTE_MINIMUM, rules.attributes.maximum
        )
    point_buy = rules.attributes.point_buy
    return _character_fields(rules, point_buy.minimum, point_buy.maximum)


def character_json_schema(*, for_game_master: bool = False) -> dict[str, Any]:
    """The JSON schema sent as Ollama's ``format`` for a ``/character`` turn.

    Args:
        for_game_master: True when a DM or admin is saving. Their schema
            bounds attributes by what storage allows rather than by what
            creation allows, so the numbers they set survive extraction
            (issue #352). Everything else about the two schemas is identical.
    """
    return _object_schema(character_fields(for_game_master=for_game_master))


# Appended to the ask when the runner cannot constrain the output itself
# (Ollama's MLX runner answers 501 "structured output is unavailable" to any
# ``format``, as of 0.33): the schema travels in the prompt instead and the
# reply is parsed by :func:`parse_extraction_reply`.
SCHEMA_IN_PROMPT_PREAMBLE = (
    "Answer with one JSON object only -- no prose, no code fence -- matching "
    "this JSON schema exactly:"
)


def extraction_messages(
    model_messages: list[dict[str, str]],
    *,
    schema_in_prompt: bool = False,
    for_game_master: bool = False,
) -> list[dict[str, str]]:
    """The messages of the extraction call: the session so far, then the ask.

    The two runner paths are kept in step deliberately: whichever way the
    schema travels, ``for_game_master`` selects the same bounds and the same
    ask, so a runner that refuses ``format`` does not quietly re-impose the
    player's range on a DM's character (issue #352).

    Args:
        model_messages: The conversation as the model sees it.
        schema_in_prompt: Spell the schema out in the ask, for a runner that
            does not take it as ``format``.
        for_game_master: True when a DM or admin is saving.
    """
    ask = extraction_prompt(for_game_master=for_game_master)
    if schema_in_prompt:
        ask += (
            f"\n\n{SCHEMA_IN_PROMPT_PREAMBLE}\n"
            f"{json.dumps(character_json_schema(for_game_master=for_game_master), indent=1)}"
        )
    return [*model_messages, {"role": "user", "content": ask}]


def parse_extraction_reply(reply: str) -> Any:
    """The JSON document in ``reply``.

    The reading half of every structured ask this project makes -- the
    ``/character`` extraction, the ledger's per-turn intent read (issue #356)
    and ``/fight``'s foe list (issue #368) all come through here, so a reply
    that arrived by the schema-in-prompt fallback is read the same way
    wherever it was asked for.

    A constrained reply is the document itself. An unconstrained one may wrap
    it in a code fence or a sentence, so the text between the first ``{``
    and the last ``}`` is what gets parsed. Reasoning is dropped first: a
    thinking model that answers anyway puts braces inside its ``<think>``
    block, and the first one there is not the document's.

    Args:
        reply: The model's reply, exactly as Ollama returned it.

    Returns:
        The parsed JSON value.

    Raises:
        json.JSONDecodeError: When no JSON object can be read out of it.
    """
    visible = strip_thinking(reply)
    start = visible.find("{")
    end = visible.rfind("}")
    candidate = visible[start : end + 1] if 0 <= start < end else visible
    return json.loads(candidate)


@derived_from_rules
@functools.cache
def _catalog_ids_by_name() -> dict[str, str]:
    """The loaded catalog's ids by display name, lower-cased.

    So an item the model names the way the rules do ("Dagger") is stored as
    the catalog entry (weight, cost and combat class follow from the id)
    rather than as a custom item with none of those. Built the first time an
    extraction asks, never at import: the catalog is the loaded canon's
    (issue #75).
    """
    return {
        item.name.lower(): item_id
        for item_id, item in equipment_catalog().all_items.items()
    }


def _equipment_item(entry: Any) -> Any:
    """One equipment entry in the shape ``Character.equipment`` stores."""
    if not isinstance(entry, dict):
        return entry
    name = str(entry.get("name", "")).strip()
    item: dict[str, Any] = {
        "quantity": entry.get("quantity", 1),
        "equipped": bool(entry.get("equipped", False)),
        "in_pack": bool(entry.get("in_pack", False)),
        "notes": "",
    }
    catalog_id = _catalog_ids_by_name().get(name.lower())
    if catalog_id is not None:
        catalog_item = equipment_catalog().all_items[catalog_id]
        item["item_id"] = catalog_id
        item["name"] = catalog_item.name
        item["category"] = catalog_item.category
    else:
        item["item_id"] = "custom"
        item["name"] = name
        item["category"] = "gear"
        item["weight"] = 0
        item["cost"] = 0
    return item


def extraction_to_import_payload(extracted: Any) -> dict[str, Any]:
    """Reshape the model's answer into the document the importer accepts.

    Only shape changes happen here: skills become the ``{name: points}``
    mapping the model stores, equipment entries take the stored item shape
    (catalog id when the name is a catalog item), and the rest is copied
    through for the importer to check. Nothing is validated -- a value the
    model got wrong is reported by ``characters.import_service``, with the
    same message the file importer would give.

    Args:
        extracted: The parsed JSON. Anything but an object yields a payload
            with an empty ``data`` object, which the importer rejects for
            having no name.
    """
    source: dict[str, Any] = extracted if isinstance(extracted, dict) else {}
    data: dict[str, Any] = {
        field.name: source[field.name]
        for field in character_fields()
        if field.name in source
    }
    skills = source.get("skills")
    if isinstance(skills, list):
        data["skills"] = {
            normalize_skill_name(str(entry.get("name", ""))): entry.get("points")
            for entry in skills
            if isinstance(entry, dict) and str(entry.get("name", "")).strip()
        }
    equipment = source.get("equipment")
    if isinstance(equipment, list):
        data["equipment"] = [_equipment_item(entry) for entry in equipment]
    return {"_format": CHARACTER_EXPORT_FORMAT, "_version": 1, "data": data}
