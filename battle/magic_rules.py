"""The canon's magic rules, read into the engine's ``MagicRules`` (issue #960).

The engine (``tarmar_engine.magic``) carries the structure of the casting
success tiers, Push, Channel, Borrowing (tarmar-engine #28) and Runaway's
later turns (tarmar-engine #30) and takes every number from a ``MagicRules``
its caller injects. The numbers are the canon's: the Tarmar canon builds them
from its DM-only magic-mechanics pages, and the default canon ships invented
ones. This module is the one path that sets them, and it reads only the
loaded canon's ``magic-rules.json`` (:data:`reference.paths.MAGIC_RULES_FILE`).
Nothing a player or a Game Master does at the table writes them.

The file is a JSON object::

    {
      "casting_success_tiers": [
        {"key": str, "label": str, "natural_totals": [int, ...],
         "effect_bonus": int, "mana_refund": int}
      ],
      "push": null | {
        "control_dice": str, "control_attribute": str,
        "penalty_per_mana": int, "base_cost_counts_as_invested": bool,
        "failure_bands": [
          {"key": str, "label": str, "is_runaway": bool,
           "lowest_margin": int, "highest_margin": int | null,
           "spell_takes_effect": bool, "push_bonus_applies": bool}
        ],
        "automatic_runaway_totals": [int, ...],
        "max_push_mana": int | null,
        "effect_bonus_per_mana_by_kind": {"damage" | "healing": int},
        "spell_effect_bonus_per_mana": {<engine spell key>: int}
      },
      "channel": null | {
        "derivations": {<kind of magic>: {"source_skill": str | null,
                                          "multiplier": int, "offset": int}},
        "kind_by_spell": {<engine spell key>: <kind of magic>},
        "default_kind": str | null
      },
      "borrowing": null | {
        "debt_dice": str, "debt_attributes": [str, ...],
        "penalty_per_debt": int,
        "failure": {"key": str, "label": str, "is_runaway": bool},
        "automatic_failure_totals": [int, ...],
        "max_debt": int | null,
        "refunds_pay_debt_first": bool
      },
      "runaway": null | {
        "drain_multiplier_of_cost": int, "drain_flat": int,
        "overflow_pool": "body" | "fatigue",
        "death_multiplier_of_max": int | null,
        "self_anchor": {"dice": str, "attribute": str},
        "helper_anchor": {"dice": str, "attribute": str},
        "helper_cost_multiplier_of_cost": int,
        "helper_failure_catches_helper": bool,
        "effect_bonus_per_turn": int
      },
      "rulings": [{"key": str, "reading": str, ...}]
    }

Field names are the engine dataclass fields they fill, and every one of them
is required: a canon states each number rather than inheriting an engine
default. An unknown key at any level is an error, since it is most likely a
misspelling of a known one; so is a key repeated in one object, two tiers or
two failure bands sharing a ``key``, and an attribute named twice in
``borrowing.debt_attributes``. ``rulings`` is optional and is the canon's own
record of the readings its pages left open; it is checked for shape and not
otherwise read.
"""

import functools
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, NoReturn

from django.core.exceptions import ImproperlyConfigured
from tarmar_engine.magic import (
    BorrowingFailure,
    BorrowingRules,
    CastingSuccessTier,
    ChannelDerivation,
    ChannelRules,
    ControlBand,
    MagicRules,
    PushRules,
    RollUnderRule,
    RunawayRules,
    SpellEffectKind,
    spell_effect_kind,
)
from tarmar_engine.spells import SPELLS

from common.helpers import parse_json_text
from reference.paths import MAGIC_RULES_FILE

TIER_FIELDS = ("key", "label", "natural_totals", "effect_bonus", "mana_refund")
BAND_FIELDS = (
    "key",
    "label",
    "is_runaway",
    "lowest_margin",
    "highest_margin",
    "spell_takes_effect",
    "push_bonus_applies",
)
PUSH_FIELDS = (
    "control_dice",
    "control_attribute",
    "penalty_per_mana",
    "base_cost_counts_as_invested",
    "failure_bands",
    "automatic_runaway_totals",
    "max_push_mana",
    "effect_bonus_per_mana_by_kind",
    "spell_effect_bonus_per_mana",
)
CHANNEL_FIELDS = ("derivations", "kind_by_spell", "default_kind")
DERIVATION_FIELDS = ("source_skill", "multiplier", "offset")
BORROWING_FIELDS = (
    "debt_dice",
    "debt_attributes",
    "penalty_per_debt",
    "failure",
    "automatic_failure_totals",
    "max_debt",
    "refunds_pay_debt_first",
)
BORROWING_FAILURE_FIELDS = ("key", "label", "is_runaway")
RUNAWAY_FIELDS = (
    "drain_multiplier_of_cost",
    "drain_flat",
    "overflow_pool",
    "death_multiplier_of_max",
    "self_anchor",
    "helper_anchor",
    "helper_cost_multiplier_of_cost",
    "helper_failure_catches_helper",
    "effect_bonus_per_turn",
)
ROLL_UNDER_FIELDS = ("dice", "attribute")
TOP_LEVEL_FIELDS = (
    "casting_success_tiers",
    "push",
    "channel",
    "borrowing",
    "runaway",
)
OPTIONAL_TOP_LEVEL_FIELDS = ("rulings",)
RULING_FIELDS = ("key", "reading")


class MagicRulesError(ImproperlyConfigured):
    """The canon's ``magic-rules.json`` is missing or malformed.

    The message names the file and the field at fault.
    """


class _RulesFileReader:
    """Checks one ``magic-rules.json``'s values, naming the file in every error.

    Each method takes the value and its field path in the file, e.g.
    ``push.failure_bands[1].highest_margin``, and returns the value in the
    type the engine takes, or raises :class:`MagicRulesError`.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def fail(self, field_path: str, problem: str) -> NoReturn:
        """Raise the error for one field.

        Raises:
            MagicRulesError: Always.
        """
        raise MagicRulesError(f"{self.path}: {field_path} {problem}")

    @contextmanager
    def engine_check(self, field_path: str) -> Iterator[None]:
        """Re-raise an engine validator's ``ValueError`` naming the file.

        Raises:
            MagicRulesError: The engine refused the values built inside.
        """
        try:
            yield
        except ValueError as error:
            raise MagicRulesError(f"{self.path}: {field_path}: {error}") from error

    def json_object(
        self,
        value: Any,
        field_path: str,
        required_fields: tuple[str, ...],
        optional_fields: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """An object carrying every required field and nothing unknown."""
        if not isinstance(value, dict):
            self.fail(field_path, f"must be an object, got {value!r}")
        prefix = f"{field_path}." if field_path else ""
        for name in value:
            if name not in required_fields and name not in optional_fields:
                self.fail(f"{prefix}{name}", "is not a field this file takes")
        for name in required_fields:
            if name not in value:
                self.fail(f"{prefix}{name}", "is missing")
        return value

    def json_list(self, value: Any, field_path: str) -> list[Any]:
        """A list."""
        if not isinstance(value, list):
            self.fail(field_path, f"must be a list, got {value!r}")
        return value

    def integer(self, value: Any, field_path: str) -> int:
        """An integer; ``true`` and ``false`` are not integers here."""
        if not isinstance(value, int) or isinstance(value, bool):
            self.fail(field_path, f"must be an integer, got {value!r}")
        return value

    def optional_integer(self, value: Any, field_path: str) -> int | None:
        """An integer or ``null``."""
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            self.fail(field_path, f"must be an integer or null, got {value!r}")
        return value

    def boolean(self, value: Any, field_path: str) -> bool:
        """``true`` or ``false``."""
        if not isinstance(value, bool):
            self.fail(field_path, f"must be true or false, got {value!r}")
        return value

    def text(self, value: Any, field_path: str) -> str:
        """A non-empty string."""
        if not isinstance(value, str) or not value:
            self.fail(field_path, f"must be a non-empty string, got {value!r}")
        return value

    def optional_text(self, value: Any, field_path: str) -> str | None:
        """A non-empty string or ``null``."""
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            self.fail(field_path, f"must be a non-empty string or null, got {value!r}")
        return value

    def integer_set(self, value: Any, field_path: str) -> frozenset[int]:
        """A list of integers with no repeats."""
        items = self.json_list(value, field_path)
        integers = [
            self.integer(item, f"{field_path}[{index}]")
            for index, item in enumerate(items)
        ]
        if len(set(integers)) != len(integers):
            self.fail(field_path, f"repeats a total: {integers}")
        return frozenset(integers)

    def text_list(self, value: Any, field_path: str) -> tuple[str, ...]:
        """A list of non-empty strings with no repeats."""
        items = self.json_list(value, field_path)
        texts = tuple(
            self.text(item, f"{field_path}[{index}]")
            for index, item in enumerate(items)
        )
        if len(set(texts)) != len(texts):
            self.fail(field_path, f"repeats an entry: {list(texts)}")
        return texts

    def unique_keys(
        self, items: Sequence[CastingSuccessTier | ControlBand], field_path: str
    ) -> None:
        """Tiers or failure bands whose ``key``s are all different."""
        keys = [item.key for item in items]
        repeated = sorted({key for key in keys if keys.count(key) > 1})
        if repeated:
            self.fail(field_path, f"repeats the key {repeated}")

    def mapping[Value](
        self,
        value: Any,
        field_path: str,
        read_entry: Callable[[Any, str], Value],
    ) -> dict[str, Value]:
        """An object of non-empty keys, each value read by ``read_entry``."""
        if not isinstance(value, dict):
            self.fail(field_path, f"must be an object, got {value!r}")
        entries: dict[str, Value] = {}
        for key, entry in value.items():
            if not key:
                self.fail(field_path, "has an empty key")
            entries[key] = read_entry(entry, f"{field_path}.{key}")
        return entries

    def engine_spell_key(self, key: str, field_path: str) -> None:
        """A key the pinned engine's spell catalog has."""
        if key not in SPELLS:
            self.fail(field_path, f"names no engine spell (known: {sorted(SPELLS)})")

    def casting_success_tier(self, value: Any, field_path: str) -> CastingSuccessTier:
        """One entry of ``casting_success_tiers``."""
        fields = self.json_object(value, field_path, TIER_FIELDS)
        with self.engine_check(field_path):
            return CastingSuccessTier(
                key=self.text(fields["key"], f"{field_path}.key"),
                label=self.text(fields["label"], f"{field_path}.label"),
                natural_totals=self.integer_set(
                    fields["natural_totals"], f"{field_path}.natural_totals"
                ),
                effect_bonus=self.integer(
                    fields["effect_bonus"], f"{field_path}.effect_bonus"
                ),
                mana_refund=self.integer(
                    fields["mana_refund"], f"{field_path}.mana_refund"
                ),
            )

    def control_band(self, value: Any, field_path: str) -> ControlBand:
        """One entry of ``push.failure_bands``."""
        fields = self.json_object(value, field_path, BAND_FIELDS)
        return ControlBand(
            key=self.text(fields["key"], f"{field_path}.key"),
            label=self.text(fields["label"], f"{field_path}.label"),
            is_runaway=self.boolean(fields["is_runaway"], f"{field_path}.is_runaway"),
            lowest_margin=self.integer(
                fields["lowest_margin"], f"{field_path}.lowest_margin"
            ),
            highest_margin=self.optional_integer(
                fields["highest_margin"], f"{field_path}.highest_margin"
            ),
            spell_takes_effect=self.boolean(
                fields["spell_takes_effect"], f"{field_path}.spell_takes_effect"
            ),
            push_bonus_applies=self.boolean(
                fields["push_bonus_applies"], f"{field_path}.push_bonus_applies"
            ),
        )

    def push_bonus_by_kind(
        self, value: Any, field_path: str
    ) -> dict[SpellEffectKind, int]:
        """``push.effect_bonus_per_mana_by_kind``, keyed by the engine's kinds."""
        bonuses = self.mapping(value, field_path, self.integer)
        known_kinds = {kind.value for kind in SpellEffectKind}
        for kind in bonuses:
            if kind not in known_kinds:
                self.fail(
                    f"{field_path}.{kind}",
                    f"is not a kind of spell effect (known: {sorted(known_kinds)})",
                )
        return {SpellEffectKind(kind): bonus for kind, bonus in bonuses.items()}

    def push_bonus_by_spell(self, value: Any, field_path: str) -> dict[str, int]:
        """``push.spell_effect_bonus_per_mana``: engine spells with an effect."""
        bonuses = self.mapping(value, field_path, self.integer)
        for spell_key in bonuses:
            self.engine_spell_key(spell_key, f"{field_path}.{spell_key}")
            if spell_effect_kind(SPELLS[spell_key]) is None:
                self.fail(
                    f"{field_path}.{spell_key}",
                    "names a spell with no rolled effect for a push bonus to add to",
                )
        return bonuses

    def push(self, value: Any, field_path: str) -> PushRules | None:
        """``push``, or ``None`` when Push is not offered."""
        if value is None:
            return None
        fields = self.json_object(value, field_path, PUSH_FIELDS)
        bands_path = f"{field_path}.failure_bands"
        bands = tuple(
            self.control_band(band, f"{bands_path}[{index}]")
            for index, band in enumerate(
                self.json_list(fields["failure_bands"], bands_path)
            )
        )
        self.unique_keys(bands, bands_path)
        with self.engine_check(field_path):
            return PushRules(
                control_dice=self.text(
                    fields["control_dice"], f"{field_path}.control_dice"
                ),
                control_attribute=self.text(
                    fields["control_attribute"], f"{field_path}.control_attribute"
                ),
                penalty_per_mana=self.integer(
                    fields["penalty_per_mana"], f"{field_path}.penalty_per_mana"
                ),
                base_cost_counts_as_invested=self.boolean(
                    fields["base_cost_counts_as_invested"],
                    f"{field_path}.base_cost_counts_as_invested",
                ),
                failure_bands=bands,
                automatic_runaway_totals=self.integer_set(
                    fields["automatic_runaway_totals"],
                    f"{field_path}.automatic_runaway_totals",
                ),
                effect_bonus_per_mana_by_kind=self.push_bonus_by_kind(
                    fields["effect_bonus_per_mana_by_kind"],
                    f"{field_path}.effect_bonus_per_mana_by_kind",
                ),
                max_push_mana=self.optional_integer(
                    fields["max_push_mana"], f"{field_path}.max_push_mana"
                ),
                spell_effect_bonus_per_mana=self.push_bonus_by_spell(
                    fields["spell_effect_bonus_per_mana"],
                    f"{field_path}.spell_effect_bonus_per_mana",
                ),
            )

    def channel_derivation(self, value: Any, field_path: str) -> ChannelDerivation:
        """One entry of ``channel.derivations``."""
        fields = self.json_object(value, field_path, DERIVATION_FIELDS)
        with self.engine_check(field_path):
            return ChannelDerivation(
                source_skill=self.optional_text(
                    fields["source_skill"], f"{field_path}.source_skill"
                ),
                multiplier=self.integer(
                    fields["multiplier"], f"{field_path}.multiplier"
                ),
                offset=self.integer(fields["offset"], f"{field_path}.offset"),
            )

    def kind_by_spell(self, value: Any, field_path: str) -> dict[str, str]:
        """``channel.kind_by_spell``: engine spell keys to kinds of magic."""
        kinds = self.mapping(value, field_path, self.text)
        for spell_key in kinds:
            self.engine_spell_key(spell_key, f"{field_path}.{spell_key}")
        return kinds

    def channel(self, value: Any, field_path: str) -> ChannelRules | None:
        """``channel``, or ``None`` when no Channel bounds a casting."""
        if value is None:
            return None
        fields = self.json_object(value, field_path, CHANNEL_FIELDS)
        with self.engine_check(field_path):
            return ChannelRules(
                derivations=self.mapping(
                    fields["derivations"],
                    f"{field_path}.derivations",
                    self.channel_derivation,
                ),
                kind_by_spell=self.kind_by_spell(
                    fields["kind_by_spell"], f"{field_path}.kind_by_spell"
                ),
                default_kind=self.optional_text(
                    fields["default_kind"], f"{field_path}.default_kind"
                ),
            )

    def borrowing_failure(self, value: Any, field_path: str) -> BorrowingFailure:
        """``borrowing.failure``: what a failed debt roll does."""
        fields = self.json_object(value, field_path, BORROWING_FAILURE_FIELDS)
        return BorrowingFailure(
            key=self.text(fields["key"], f"{field_path}.key"),
            label=self.text(fields["label"], f"{field_path}.label"),
            is_runaway=self.boolean(fields["is_runaway"], f"{field_path}.is_runaway"),
        )

    def borrowing(self, value: Any, field_path: str) -> BorrowingRules | None:
        """``borrowing``, or ``None`` when no caster may borrow."""
        if value is None:
            return None
        fields = self.json_object(value, field_path, BORROWING_FIELDS)
        with self.engine_check(field_path):
            return BorrowingRules(
                debt_dice=self.text(fields["debt_dice"], f"{field_path}.debt_dice"),
                debt_attributes=self.text_list(
                    fields["debt_attributes"], f"{field_path}.debt_attributes"
                ),
                penalty_per_debt=self.integer(
                    fields["penalty_per_debt"], f"{field_path}.penalty_per_debt"
                ),
                failure=self.borrowing_failure(
                    fields["failure"], f"{field_path}.failure"
                ),
                automatic_failure_totals=self.integer_set(
                    fields["automatic_failure_totals"],
                    f"{field_path}.automatic_failure_totals",
                ),
                max_debt=self.optional_integer(
                    fields["max_debt"], f"{field_path}.max_debt"
                ),
                refunds_pay_debt_first=self.boolean(
                    fields["refunds_pay_debt_first"],
                    f"{field_path}.refunds_pay_debt_first",
                ),
            )

    def roll_under_rule(self, value: Any, field_path: str) -> RollUnderRule:
        """``runaway.self_anchor`` or ``runaway.helper_anchor``: dice against
        one attribute."""
        fields = self.json_object(value, field_path, ROLL_UNDER_FIELDS)
        with self.engine_check(field_path):
            return RollUnderRule(
                dice=self.text(fields["dice"], f"{field_path}.dice"),
                attribute=self.text(fields["attribute"], f"{field_path}.attribute"),
            )

    def runaway(self, value: Any, field_path: str) -> RunawayRules | None:
        """``runaway``, or ``None`` when a Runaway has no later turns."""
        if value is None:
            return None
        fields = self.json_object(value, field_path, RUNAWAY_FIELDS)
        integers = {
            name: self.integer(fields[name], f"{field_path}.{name}")
            for name in (
                "drain_multiplier_of_cost",
                "drain_flat",
                "helper_cost_multiplier_of_cost",
                "effect_bonus_per_turn",
            )
        }
        with self.engine_check(field_path):
            return RunawayRules(
                **integers,
                overflow_pool=self.text(
                    fields["overflow_pool"], f"{field_path}.overflow_pool"
                ),
                death_multiplier_of_max=self.optional_integer(
                    fields["death_multiplier_of_max"],
                    f"{field_path}.death_multiplier_of_max",
                ),
                self_anchor=self.roll_under_rule(
                    fields["self_anchor"], f"{field_path}.self_anchor"
                ),
                helper_anchor=self.roll_under_rule(
                    fields["helper_anchor"], f"{field_path}.helper_anchor"
                ),
                helper_failure_catches_helper=self.boolean(
                    fields["helper_failure_catches_helper"],
                    f"{field_path}.helper_failure_catches_helper",
                ),
            )

    def rulings(self, value: Any, field_path: str) -> None:
        """``rulings``: a list of objects with string ``key`` and ``reading``."""
        for index, ruling in enumerate(self.json_list(value, field_path)):
            ruling_path = f"{field_path}[{index}]"
            if not isinstance(ruling, dict):
                self.fail(ruling_path, f"must be an object, got {ruling!r}")
            for name in RULING_FIELDS:
                self.text(ruling.get(name), f"{ruling_path}.{name}")

    def magic_rules(self, value: Any) -> MagicRules:
        """The whole file."""
        if not isinstance(value, dict):
            self.fail("the file", f"must hold a JSON object, got {value!r}")
        fields = self.json_object(
            value, "", TOP_LEVEL_FIELDS, OPTIONAL_TOP_LEVEL_FIELDS
        )
        if "rulings" in fields:
            self.rulings(fields["rulings"], "rulings")
        tiers = tuple(
            self.casting_success_tier(tier, f"casting_success_tiers[{index}]")
            for index, tier in enumerate(
                self.json_list(fields["casting_success_tiers"], "casting_success_tiers")
            )
        )
        self.unique_keys(tiers, "casting_success_tiers")
        with self.engine_check("casting_success_tiers"):
            rules = MagicRules(
                casting_success_tiers=tiers,
                push=self.push(fields["push"], "push"),
                channel=self.channel(fields["channel"], "channel"),
            )
        # Added once the rest stands, so the engine's refusal of an uncapped
        # Borrowing with no Channel to bound it names this field.
        borrowing = self.borrowing(fields["borrowing"], "borrowing")
        with self.engine_check("borrowing"):
            rules = replace(rules, borrowing=borrowing)
        return replace(rules, runaway=self.runaway(fields["runaway"], "runaway"))


def _refuse_repeated_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``json.loads``'s object hook: one object, every key once.

    ``json.loads`` alone keeps the last of a repeated key, so a second
    ``"push"`` would replace the first without a word.

    Raises:
        ValueError: A key appears twice in one object.
    """
    keys = [key for key, _value in pairs]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        raise ValueError(f"an object repeats the key {repeated}")
    return dict(pairs)


def load_magic_rules(path: Path) -> MagicRules:
    """Read a canon's ``magic-rules.json`` into the engine's ``MagicRules``.

    The schema is in this module's docstring.

    Args:
        path: The ``magic-rules.json`` file.

    Returns:
        The rules, as the engine's ``TarmarProfile(magic=...)`` takes them.

    Raises:
        MagicRulesError: The file is missing or unreadable, is not JSON,
            repeats a key in one object, or a field is missing, unknown, of
            the wrong type, or refused by the engine's own validation; the
            message names the file and the field.
    """
    reader = _RulesFileReader(path)
    if not path.is_file():
        reader.fail("the file", "is missing; every canon must carry one")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise MagicRulesError(f"{path} could not be read: {error}") from error
    try:
        data = parse_json_text(text, object_pairs_hook=_refuse_repeated_keys)
    except ValueError as error:
        raise MagicRulesError(f"{path} is not valid JSON: {error}") from error
    return reader.magic_rules(data)


@functools.cache
def magic_rules() -> MagicRules:
    """The loaded canon's magic rules, read once per process.

    The canon does not change under a running server; ``runserver`` restarts
    when a canon JSON file changes (``reference.apps``). Tests that need a
    fresh read call ``magic_rules.cache_clear()``.

    Returns:
        The rules read from :data:`reference.paths.MAGIC_RULES_FILE`.

    Raises:
        MagicRulesError: The file is missing or malformed.
    """
    return load_magic_rules(MAGIC_RULES_FILE)
