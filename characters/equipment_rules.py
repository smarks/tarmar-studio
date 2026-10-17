"""The canon's equipment catalog and equipment rules (issue #75).

Stage 4 of rules-as-data: the items a character may buy come from the loaded
canon's ``equipment.json`` (:data:`reference.paths.EQUIPMENT_CATALOG_FILE`),
and the numbers encumbrance and movement read from its
``equipment-rules.json`` (:data:`reference.paths.EQUIPMENT_RULES_FILE`). The
procedures that use them (the encumbrance lookup, the gait arithmetic, the
movement modifier's shape, the catalog lookups, the worn-armour settlement)
stay code. The items and numbers are on the public pages
(``reference/content/public-rules/equipment/`` and
``combat/action-options/movement.md``), so the default canon's files carry
the pages' values; the Tarmar canon carries its own, free to diverge. This
module is the one path that reads them; nothing at the table writes them.

``equipment.json`` is a JSON object of four groups, each an object keyed by
item id::

    {
      "weapons": {str: {"name": str, "category": str, "cost": number,
                        "weight": number, "subcategory": str,
                        "damage": str | null, "str_req": int | null,
                        "throwable": bool, "two_handed": bool,
                        "weapon_class": str (optional),
                        "alt_class": str (optional), "notes": str (optional)},
                  ...},
      "armor": {str: {"name", "category", "cost", "weight",
                      "stops": int, "dex_penalty": int,
                      "movement_restriction": str | null,
                      "armour_tier": str}, ...},
      "shields": {str: {"name", "category", "cost", "weight",
                        "stops": int, "dex_penalty": int,
                        "shield_bonus": int}, ...},
      "gear": {str: {"name", "category", "cost", "weight",
                     "subcategory": str, "notes": str (optional)}, ...},
      "rulings": [{"key": str, "reading": str, ...}]
    }

What the catalog's values mean, and what else is refused:

- ``category`` is kept as stored, and must be its group's: ``"weapons"``,
  ``"armor"``, ``"gear"``, and ``"shield"`` for an item of the ``shields``
  group (the custom-item form's spelling,
  :data:`characters.equipment.ITEM_CATEGORIES`), since the lookups by
  category read it rather than the group.
- ``cost`` (in gold pieces, fractions allowed: a torch costs 0.01) and
  ``weight`` (in pounds) are numbers, 0 or more.
- ``damage`` is the weapon's damage as the pages print it, or ``null`` for
  ammunition and the bola. It is not parsed here: "varies", "by STR" and
  "1d6 fire" are the pages' words for damage the Game Master decides, and
  whether a damage can be rolled is ``characters.attack.damage_is_rollable``'s
  question at the table.
- ``weapon_class`` is the class the weapon-against-armour matrix reads;
  ammunition has none and leaves the key out. It, ``alt_class`` and
  ``armour_tier`` must each be a class or tier the matrix has, which the
  ``characters.E003`` system check makes
  (:func:`characters.combat.validate_catalog_classes`).
- ``stops``, ``shield_bonus`` are 0 or more and ``dex_penalty`` is 0 or less.
- ``movement_restriction`` is ``null`` or a code ``equipment-rules.json``'s
  ``armour_restriction_codes`` gives; that check spans the two files, so the
  ``characters.E003`` system check makes it
  (:func:`validate_armour_restriction_codes`).
- No id appears in two groups: the lookups read the four as one catalog,
  where a repeat would hide the other item.

``equipment-rules.json`` is a JSON object::

    {
      "encumbrance": {
        "levels": [{"key": str, "max_multiplier": number | null,
                    "penalty": int, "forbidden_gaits": [str, ...],
                    "walk_limit": int | null}, ...]
      },
      "armour_restriction_codes": {str: [str, ...], ...},
      "gaits": {"order": [str, ...], "base_distances": {str: int, ...}},
      "missile_classes": [str, ...],
      "defender_dodge": {"minimum": int, "maximum": int},
      "rulings": [{"key": str, "reading": str, ...}]
    }

What the rules' values mean, and what else is refused:

- ``encumbrance.levels``: lightest to heaviest, keys unique. A character's
  level is the first whose ``max_multiplier`` times their Strength the carried
  weight does not exceed; the multipliers are above 0 and rise strictly, and
  only the last may be ``null``, catching every weight the others do not. Each level's
  movement ``penalty`` (0 or less), the gaits it forbids, and the hexes Walk
  is capped at (``null`` for no cap, else 0 or more).
- ``armour_restriction_codes``: each armour ``movement_restriction`` code and
  the gaits it forbids.
- ``gaits``: every gait, fastest first and unrepeated, and each one's base
  distance in hexes (0 or more), keyed by exactly the gaits of ``order``.
  Every gait a level or a code forbids is one of them, and not the slowest,
  which is never forbidden, only capped (:attr:`GaitRules.restrictable_gaits`).
- ``missile_classes``: the weapon classes Archery covers, at least one; each
  must be a class some catalog weapon has, which the ``characters.E004``
  system check makes (:func:`validate_missile_classes`).
- ``defender_dodge``: the least and most dodge the Game Master may give a
  defender on the attack panel, the minimum no more than the maximum.

The level keys and the gait names are the canon's to choose: the loader
requires only that they be unique, and the code reads a level or a gait by
its place and role, never by its name (:attr:`EncumbranceRules.keys`,
:attr:`EncumbranceRules.heaviest`, :attr:`GaitRules.order`,
:attr:`GaitRules.slowest`). Two places name what the code itself decides:
``battle.generation``'s ``HEAVIEST_GENERATED_LOAD``, the generator's own
choice of level, which a test holds every canon to naming; and the battle
engine's four gaits (``battle.adaptation.ENGINE_GAITS``), matched to the
canon's by place, so a canon must name exactly four, which the
``battle.E003`` system check holds it to until stage 6 seats the engine's
own rules.

Both files are read the strict way every canon rules file shares
(``reference.rules_file``): every field above is required unless marked
optional, ``rulings`` is optional, and an unknown key, a repeated key, a
boolean where a number goes, or an empty string is refused, naming the file
and the field.
"""

import functools
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from functools import cached_property
from itertools import pairwise
from pathlib import Path
from typing import Any, ClassVar

from reference.paths import (
    CANON_EQUIPMENT_RULES_FILE_NAME,
    EQUIPMENT_CATALOG_FILE,
    EQUIPMENT_RULES_FILE,
)
from reference.rules_file import RulesFileError, RulesFileReader

from .rules import require_at_least, require_at_most

#: The catalog's groups, in the order the picker lists them, with the
#: picker's labels. The keys are ``equipment.json``'s top-level keys (#338
#: item 21); the labels are the app's, not the canon's.
CATALOG_GROUPS: tuple[tuple[str, str], ...] = (
    ("weapons", "Weapons"),
    ("armor", "Armor"),
    ("shields", "Shields"),
    ("gear", "Gear"),
)

ENCUMBRANCE_FIELDS = ("levels",)
ENCUMBRANCE_LEVEL_FIELDS = (
    "key",
    "max_multiplier",
    "penalty",
    "forbidden_gaits",
    "walk_limit",
)
GAIT_FIELDS = ("order", "base_distances")
DEFENDER_DODGE_FIELDS = ("minimum", "maximum")
RULES_TOP_LEVEL_FIELDS = (
    "encumbrance",
    "armour_restriction_codes",
    "gaits",
    "missile_classes",
    "defender_dodge",
)


class EquipmentCatalogError(RulesFileError):
    """The canon's ``equipment.json`` is missing or malformed.

    The message names the file and the field at fault.
    """


class EquipmentRulesError(RulesFileError):
    """The canon's ``equipment-rules.json`` is missing or malformed.

    The message names the file and the field at fault.
    """


@dataclass(frozen=True)
class CatalogItem:
    """What every catalog item carries.

    Attributes:
        identifier: The item's id, its key in the file.
        name: The name the sheet and the picker show.
        category: The item's category as stored (see the module docstring).
        cost: Its price in gold pieces.
        weight: Its weight in pounds.
    """

    #: Fields the file may leave out; ``None`` here stands for an absent key.
    OPTIONAL_FIELDS: ClassVar[tuple[str, ...]] = ()
    #: The ``category`` every item of the subclass's group stores.
    CATEGORY: ClassVar[str]

    identifier: str
    name: str
    category: str
    cost: float
    weight: float

    def __post_init__(self) -> None:
        if self.category != self.CATEGORY:
            raise ValueError(
                f"category must be {self.CATEGORY!r} for its group, got "
                f"{self.category!r}"
            )
        require_at_least("cost", self.cost, 0)
        require_at_least("weight", self.weight, 0)

    @classmethod
    def file_fields(cls) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """The item's fields as the file writes them.

        Returns:
            The required fields and the optional ones, each in declaration
            order; :attr:`identifier` is the item's key, not a field.
        """
        names = [field.name for field in fields(cls) if field.name != "identifier"]
        required = tuple(name for name in names if name not in cls.OPTIONAL_FIELDS)
        return required, cls.OPTIONAL_FIELDS

    def as_dict(self) -> dict[str, Any]:
        """The item as the file holds it: every field but the id, and an
        optional field only when present, in the dataclass's declaration
        order (the common fields first) rather than the file's.

        Returns:
            A new dict, which the caller may change.
        """
        required, optional = self.file_fields()
        stored = {name: getattr(self, name) for name in required}
        stored.update(
            {
                name: getattr(self, name)
                for name in optional
                if getattr(self, name) is not None
            }
        )
        return stored


@dataclass(frozen=True)
class Weapon(CatalogItem):
    """A weapon, or ammunition, which the weapons group also holds.

    Attributes:
        subcategory: The weapons page's group (``"unusual"`` for one).
        damage: The damage as the pages print it, or ``None``; not parsed.
        str_req: The Strength it needs, or ``None`` for none.
        throwable: Whether it may be thrown.
        two_handed: Whether it takes both hands.
        weapon_class: Its class for the weapon-against-armour matrix, or
            ``None`` for ammunition.
        alt_class: A second class it may attack as, or ``None``.
        notes: The pages' note on it, or ``None``.
    """

    OPTIONAL_FIELDS: ClassVar[tuple[str, ...]] = ("weapon_class", "alt_class", "notes")
    CATEGORY: ClassVar[str] = "weapons"

    subcategory: str
    damage: str | None
    str_req: int | None
    throwable: bool
    two_handed: bool
    weapon_class: str | None = None
    alt_class: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class Armour(CatalogItem):
    """A suit of armour.

    Attributes:
        stops: The damage it stops, 0 or more.
        dex_penalty: What it takes from DEX, 0 or less.
        movement_restriction: The code of the gaits it forbids, or ``None``.
        armour_tier: Its tier for the weapon-against-armour matrix.
    """

    CATEGORY: ClassVar[str] = "armor"

    stops: int
    dex_penalty: int
    movement_restriction: str | None
    armour_tier: str

    def __post_init__(self) -> None:
        super().__post_init__()
        require_at_least("stops", self.stops, 0)
        require_at_most("dex_penalty", self.dex_penalty, 0)


@dataclass(frozen=True)
class Shield(CatalogItem):
    """A shield, or a parrying weapon used as one.

    Attributes:
        stops: The damage it stops, 0 or more.
        dex_penalty: What it takes from DEX, 0 or less.
        shield_bonus: What it adds to the defender's Target Number, 0 or more.
    """

    CATEGORY: ClassVar[str] = "shield"

    stops: int
    dex_penalty: int
    shield_bonus: int

    def __post_init__(self) -> None:
        super().__post_init__()
        require_at_least("stops", self.stops, 0)
        require_at_most("dex_penalty", self.dex_penalty, 0)
        require_at_least("shield_bonus", self.shield_bonus, 0)


@dataclass(frozen=True)
class Gear(CatalogItem):
    """Anything else a character may buy.

    Attributes:
        subcategory: The gear page's group.
        notes: The pages' note on it, or ``None``.
    """

    OPTIONAL_FIELDS: ClassVar[tuple[str, ...]] = ("notes",)
    CATEGORY: ClassVar[str] = "gear"

    subcategory: str
    notes: str | None = None


@dataclass(frozen=True)
class EquipmentCatalog:
    """Every item a character may buy, by group.

    The lookups mirror ``characters.equipment``'s module functions, each
    answering for an unknown id as they do.

    Attributes:
        weapons: The weapons group, by id.
        armour: The armor group, by id.
        shields: The shields group, by id.
        gear: The gear group, by id.
    """

    weapons: Mapping[str, Weapon]
    armour: Mapping[str, Armour]
    shields: Mapping[str, Shield]
    gear: Mapping[str, Gear]

    def __post_init__(self) -> None:
        seen_in: dict[str, str] = {}
        for group, items in self.groups.items():
            for identifier, catalog_item in items.items():
                if catalog_item.identifier != identifier:
                    raise ValueError(
                        f"{group}.{identifier} carries the id "
                        f"{catalog_item.identifier!r}"
                    )
                if identifier in seen_in:
                    raise ValueError(
                        f"{identifier!r} is in both {seen_in[identifier]} and "
                        f"{group}; an id names one item"
                    )
                seen_in[identifier] = group

    @property
    def groups(self) -> dict[str, Mapping[str, CatalogItem]]:
        """The four groups by their file keys, in :data:`CATALOG_GROUPS`'s
        order; the labels are :data:`CATALOG_GROUPS`'s."""
        by_key: dict[str, Mapping[str, CatalogItem]] = {
            "weapons": self.weapons,
            "armor": self.armour,
            "shields": self.shields,
            "gear": self.gear,
        }
        return {group: by_key[group] for group, _label in CATALOG_GROUPS}

    @cached_property
    def all_items(self) -> Mapping[str, CatalogItem]:
        """Every item by id, the groups merged in order."""
        merged: dict[str, CatalogItem] = {}
        for items in self.groups.values():
            merged.update(items)
        return merged

    def as_dict(self) -> dict[str, dict[str, dict[str, Any]]]:
        """The catalog as the file holds it, less any ``rulings``.

        Returns:
            Each group's items by id, each as :meth:`CatalogItem.as_dict`
            writes it, by group key in :data:`CATALOG_GROUPS`' order; new
            dicts, which the caller may change.
        """
        return {
            group: {identifier: item.as_dict() for identifier, item in items.items()}
            for group, items in self.groups.items()
        }

    def item_dicts(self) -> dict[str, dict[str, Any]]:
        """Every item by id as the file holds it, the groups merged in order.

        Returns:
            New dicts, as :meth:`CatalogItem.as_dict` writes each item.
        """
        return {
            identifier: item.as_dict() for identifier, item in self.all_items.items()
        }

    def item(self, identifier: str) -> CatalogItem | None:
        """The item with id ``identifier``, or ``None``."""
        return self.all_items.get(identifier)

    def weight(self, identifier: str) -> float:
        """The item's weight, or 0 for an unknown id."""
        catalog_item = self.item(identifier)
        return catalog_item.weight if catalog_item else 0

    def name(self, identifier: str) -> str:
        """The item's name, or ``identifier`` itself for an unknown id."""
        catalog_item = self.item(identifier)
        return catalog_item.name if catalog_item else identifier

    def items_by_category(self, category: str) -> dict[str, CatalogItem]:
        """Every item whose stored ``category`` is ``category``, by id."""
        return {
            identifier: catalog_item
            for identifier, catalog_item in self.all_items.items()
            if catalog_item.category == category
        }

    def weapon_class(self, identifier: str) -> str | None:
        """A weapon's combat class, or ``None`` for ammunition, a non-weapon
        or an unknown id."""
        catalog_item = self.item(identifier)
        return catalog_item.weapon_class if isinstance(catalog_item, Weapon) else None

    def is_unusual_weapon(self, identifier: str) -> bool:
        """Whether the id is a weapon of the Unusual group (subcategory
        ``"unusual"``), whose training transfers to no other weapon."""
        catalog_item = self.item(identifier)
        return isinstance(catalog_item, Weapon) and catalog_item.subcategory == (
            "unusual"
        )

    def armour_tier(self, identifier: str) -> str | None:
        """An armour's tier, or ``None`` for anything else or an unknown id."""
        catalog_item = self.item(identifier)
        return catalog_item.armour_tier if isinstance(catalog_item, Armour) else None

    def shield_bonus(self, identifier: str) -> int:
        """A shield's bonus to the defender's Target Number, or 0 for
        anything else or an unknown id."""
        catalog_item = self.item(identifier)
        return catalog_item.shield_bonus if isinstance(catalog_item, Shield) else 0

    @property
    def weapon_classes(self) -> frozenset[str]:
        """Every ``weapon_class`` some weapon has."""
        return frozenset(
            weapon.weapon_class
            for weapon in self.weapons.values()
            if weapon.weapon_class is not None
        )


@dataclass(frozen=True)
class EncumbranceLevel:
    """One encumbrance level.

    Attributes:
        key: The level's key, as the sheet stores it.
        max_multiplier: The most a character carries at this level, as a
            multiple of Strength, or ``None`` for the heaviest level, which
            catches every weight.
        penalty: What the level takes from every gait's distance, 0 or less.
        forbidden_gaits: The gaits the level forbids.
        walk_limit: The hexes Walk is capped at, or ``None`` for no cap.
    """

    key: str
    max_multiplier: float | None
    penalty: int
    forbidden_gaits: tuple[str, ...]
    walk_limit: int | None

    def __post_init__(self) -> None:
        if self.max_multiplier is not None and self.max_multiplier <= 0:
            raise ValueError(
                f"max_multiplier must be above 0, got {self.max_multiplier}"
            )
        require_at_most("penalty", self.penalty, 0)
        if self.walk_limit is not None:
            require_at_least("walk_limit", self.walk_limit, 0)


@dataclass(frozen=True)
class EncumbranceRules:
    """The encumbrance levels, lightest to heaviest.

    Attributes:
        levels: The levels.
    """

    levels: tuple[EncumbranceLevel, ...]

    def __post_init__(self) -> None:
        if not self.levels:
            raise ValueError("has no levels")
        keys = self.keys
        repeated = sorted({key for key in keys if keys.count(key) > 1})
        if repeated:
            raise ValueError(f"repeats the key {repeated}")
        for level in self.levels[:-1]:
            if level.max_multiplier is None:
                raise ValueError(
                    f"{level.key}'s max_multiplier is null, but only the "
                    "heaviest level may catch every weight"
                )
        for lighter, heavier in pairwise(self.levels):
            if (
                lighter.max_multiplier is not None
                and heavier.max_multiplier is not None
                and heavier.max_multiplier <= lighter.max_multiplier
            ):
                raise ValueError(
                    f"max_multiplier must rise: {lighter.key} allows "
                    f"{lighter.max_multiplier} and {heavier.key} "
                    f"{heavier.max_multiplier}"
                )

    @property
    def lightest(self) -> EncumbranceLevel:
        """The first level, a character's when carrying nothing."""
        return self.levels[0]

    @property
    def heaviest(self) -> EncumbranceLevel:
        """The last level, which catches every weight the others do not."""
        return self.levels[-1]

    @property
    def keys(self) -> tuple[str, ...]:
        """The levels' keys, lightest to heaviest."""
        return tuple(level.key for level in self.levels)

    def _level(self, key: str | None) -> EncumbranceLevel | None:
        """The level ``key`` names, or ``None``."""
        return next((level for level in self.levels if level.key == key), None)

    def level_for(self, carried_weight: float, strength: int) -> str:
        """The key of the level ``carried_weight`` pounds puts a character at.

        The first level whose ``max_multiplier`` times ``strength`` the
        weight does not exceed; the heaviest level catches the rest.

        Args:
            carried_weight: The weight carried, in pounds.
            strength: The character's effective Strength.

        Returns:
            The level's key.
        """
        for level in self.levels:
            if (
                level.max_multiplier is None
                or carried_weight <= strength * level.max_multiplier
            ):
                return level.key
        return self.levels[-1].key

    def penalty(self, key: str | None) -> int:
        """The level's movement penalty; 0 for no level or an unknown key."""
        level = self._level(key)
        return level.penalty if level else 0

    def restrictions(self, key: str | None) -> list[str]:
        """The gaits the level forbids; none for no level or an unknown key."""
        level = self._level(key)
        return list(level.forbidden_gaits) if level else []

    def walk_limit(self, key: str | None) -> int | None:
        """The level's cap on Walk; ``None`` for no cap or an unknown key."""
        level = self._level(key)
        return level.walk_limit if level else None


@dataclass(frozen=True)
class GaitRules:
    """The gaits and their base distances.

    Attributes:
        order: Every gait, fastest first.
        base_distances: Each gait's distance in hexes before the movement
            modifier, keyed by exactly the gaits of :attr:`order`.
    """

    order: tuple[str, ...]
    base_distances: Mapping[str, int]

    def __post_init__(self) -> None:
        if not self.order:
            raise ValueError("order names no gait")
        if len(set(self.order)) != len(self.order):
            raise ValueError(f"order repeats a gait: {list(self.order)}")
        if set(self.base_distances) != set(self.order):
            raise ValueError(
                f"base_distances must give exactly the gaits of order "
                f"{list(self.order)}, got {sorted(self.base_distances)}"
            )
        for gait, distance in self.base_distances.items():
            require_at_least(f"{gait}'s base distance", distance, 0)

    @property
    def slowest(self) -> str:
        """The slowest gait: the one a walk limit caps and none forbids."""
        return self.order[-1]

    @property
    def restrictable_gaits(self) -> tuple[str, ...]:
        """The gaits a restriction may forbid, slowest first.

        Derived from :attr:`order`: every gait but the slowest, which is
        never forbidden, only capped (movement.md's Overloaded is "Walk only,
        1 hex max"), reversed so the slowest restrictable gait comes first,
        the order the sheet names restrictions in ("Cannot Jog, Run, or
        Sprint"). For the pages' gaits that is ``("jog", "run", "sprint")``.
        """
        return tuple(reversed(self.order[:-1]))


@dataclass(frozen=True)
class DefenderDodgeRules:
    """The bounds on the dodge the Game Master gives a defender.

    Attributes:
        minimum: The least dodge.
        maximum: The most dodge, at least :attr:`minimum`.
    """

    minimum: int
    maximum: int

    def __post_init__(self) -> None:
        if self.minimum > self.maximum:
            raise ValueError(
                f"minimum {self.minimum} must be no more than maximum {self.maximum}"
            )


@dataclass(frozen=True)
class EquipmentRules:
    """Every number encumbrance and movement read from the canon.

    Attributes:
        encumbrance: The encumbrance levels.
        armour_restriction_codes: The gaits each armour restriction code
            forbids, by code.
        gaits: The gaits and their base distances.
        missile_classes: The weapon classes Archery covers.
        defender_dodge: The bounds on a defender's dodge.
    """

    encumbrance: EncumbranceRules
    armour_restriction_codes: Mapping[str, tuple[str, ...]]
    gaits: GaitRules
    missile_classes: frozenset[str]
    defender_dodge: DefenderDodgeRules

    def __post_init__(self) -> None:
        if not self.missile_classes:
            raise ValueError("missile_classes names no class")
        restrictable = self.gaits.restrictable_gaits
        refused = [
            f"encumbrance.levels {level.key!r} forbids {gait!r}"
            for level in self.encumbrance.levels
            for gait in level.forbidden_gaits
            if gait not in restrictable
        ] + [
            f"armour_restriction_codes {code!r} forbids {gait!r}"
            for code, gaits in self.armour_restriction_codes.items()
            for gait in gaits
            if gait not in restrictable
        ]
        if refused:
            raise ValueError(
                f"{', '.join(refused)}, which is not a gait a restriction may "
                f"forbid: gaits.order but its slowest, {list(restrictable)}"
            )


class _EquipmentCatalogFileReader(RulesFileReader):
    """Reads one ``equipment.json`` into :class:`EquipmentCatalog`.

    The shared checks are :class:`reference.rules_file.RulesFileReader`'s;
    this adds the item fields. Each method takes the value and its field
    path in the file and returns the dataclass, or raises
    :class:`EquipmentCatalogError`.
    """

    error_class = EquipmentCatalogError

    @cached_property
    def field_readers(self) -> dict[str, Callable[[Any, str], Any]]:
        """How each item field is read, by name; one reading per field
        whichever group carries it."""
        return {
            "name": self.text,
            "category": self.text,
            "cost": self.number,
            "weight": self.number,
            "subcategory": self.text,
            "damage": self.optional_text,
            "str_req": self.optional_integer,
            "throwable": self.boolean,
            "two_handed": self.boolean,
            "weapon_class": self.text,
            "alt_class": self.text,
            "notes": self.text,
            "stops": self.integer,
            "dex_penalty": self.integer,
            "movement_restriction": self.optional_text,
            "armour_tier": self.text,
            "shield_bonus": self.integer,
        }

    def catalog_item[Item: CatalogItem](
        self, item_class: type[Item], identifier: str, value: Any, field_path: str
    ) -> Item:
        """One item of a group, read as ``item_class``."""
        required, optional = item_class.file_fields()
        stored = self.json_object(value, field_path, required, optional)
        values = {
            name: self.field_readers[name](stored[name], f"{field_path}.{name}")
            for name in (*required, *optional)
            if name in stored
        }
        with self.wrapped_value_errors(field_path):
            return item_class(identifier=identifier, **values)

    def group[Item: CatalogItem](
        self, item_class: type[Item], value: Any, field_path: str
    ) -> dict[str, Item]:
        """One group: its items by id."""
        entries = self.mapping(value, field_path, lambda entry, _entry_path: entry)
        return {
            identifier: self.catalog_item(
                item_class, identifier, entry, f"{field_path}.{identifier}"
            )
            for identifier, entry in entries.items()
        }

    def equipment_catalog(self, value: Any) -> EquipmentCatalog:
        """The whole file."""
        stored = self.file_object(value, tuple(group for group, _ in CATALOG_GROUPS))
        weapons = self.group(Weapon, stored["weapons"], "weapons")
        armour = self.group(Armour, stored["armor"], "armor")
        shields = self.group(Shield, stored["shields"], "shields")
        gear = self.group(Gear, stored["gear"], "gear")
        # The one check across groups: no id names two items.
        with self.wrapped_value_errors("the groups"):
            return EquipmentCatalog(
                weapons=weapons, armour=armour, shields=shields, gear=gear
            )


class _EquipmentRulesFileReader(RulesFileReader):
    """Reads one ``equipment-rules.json`` into :class:`EquipmentRules`.

    Each method takes the value and its field path in the file and returns
    the dataclass, or raises :class:`EquipmentRulesError`.
    """

    error_class = EquipmentRulesError

    def encumbrance_level(self, value: Any, field_path: str) -> EncumbranceLevel:
        """One level of ``encumbrance.levels``."""
        stored = self.json_object(value, field_path, ENCUMBRANCE_LEVEL_FIELDS)
        key = self.text(stored["key"], f"{field_path}.key")
        max_multiplier = self.optional_number(
            stored["max_multiplier"], f"{field_path}.max_multiplier"
        )
        penalty = self.integer(stored["penalty"], f"{field_path}.penalty")
        forbidden_gaits = self.text_list(
            stored["forbidden_gaits"], f"{field_path}.forbidden_gaits"
        )
        walk_limit = self.optional_integer(
            stored["walk_limit"], f"{field_path}.walk_limit"
        )
        with self.wrapped_value_errors(field_path):
            return EncumbranceLevel(
                key=key,
                max_multiplier=max_multiplier,
                penalty=penalty,
                forbidden_gaits=forbidden_gaits,
                walk_limit=walk_limit,
            )

    def encumbrance(self, value: Any, field_path: str) -> EncumbranceRules:
        """``encumbrance``."""
        stored = self.json_object(value, field_path, ENCUMBRANCE_FIELDS)
        levels = self.entries(
            stored["levels"], f"{field_path}.levels", self.encumbrance_level
        )
        with self.wrapped_value_errors(f"{field_path}.levels"):
            return EncumbranceRules(levels=levels)

    def gaits(self, value: Any, field_path: str) -> GaitRules:
        """``gaits``."""
        stored = self.json_object(value, field_path, GAIT_FIELDS)
        order = self.text_list(stored["order"], f"{field_path}.order")
        base_distances = self.mapping(
            stored["base_distances"], f"{field_path}.base_distances", self.integer
        )
        with self.wrapped_value_errors(field_path):
            return GaitRules(order=order, base_distances=base_distances)

    def defender_dodge(self, value: Any, field_path: str) -> DefenderDodgeRules:
        """``defender_dodge``."""
        stored = self.json_object(value, field_path, DEFENDER_DODGE_FIELDS)
        integers = self.integers(stored, field_path, DEFENDER_DODGE_FIELDS)
        with self.wrapped_value_errors(field_path):
            return DefenderDodgeRules(**integers)

    def equipment_rules(self, value: Any) -> EquipmentRules:
        """The whole file."""
        stored = self.file_object(value, RULES_TOP_LEVEL_FIELDS)
        encumbrance = self.encumbrance(stored["encumbrance"], "encumbrance")
        armour_restriction_codes = self.mapping(
            stored["armour_restriction_codes"],
            "armour_restriction_codes",
            self.text_list,
        )
        gaits = self.gaits(stored["gaits"], "gaits")
        missile_classes = frozenset(
            self.text_list(stored["missile_classes"], "missile_classes")
        )
        defender_dodge = self.defender_dodge(stored["defender_dodge"], "defender_dodge")
        # The checks across blocks: every gait forbidden is one a restriction
        # may forbid, and some class is a Missile class.
        with self.wrapped_value_errors("the rules"):
            return EquipmentRules(
                encumbrance=encumbrance,
                armour_restriction_codes=armour_restriction_codes,
                gaits=gaits,
                missile_classes=missile_classes,
                defender_dodge=defender_dodge,
            )


def load_equipment_catalog(path: Path) -> EquipmentCatalog:
    """Read a canon's ``equipment.json`` into :class:`EquipmentCatalog`.

    The schema is in this module's docstring; the file-level checks are
    :meth:`reference.rules_file.RulesFileReader.parsed_file`'s.

    Args:
        path: The ``equipment.json`` file.

    Returns:
        The catalog.

    Raises:
        EquipmentCatalogError: The file is missing or unreadable, is not
            JSON, repeats a key in one object, or a field is missing,
            unknown, of the wrong type, or refused by the items' own
            validation; the message names the file and the field.
    """
    reader = _EquipmentCatalogFileReader(path)
    return reader.equipment_catalog(reader.parsed_file())


def load_equipment_rules(path: Path) -> EquipmentRules:
    """Read a canon's ``equipment-rules.json`` into :class:`EquipmentRules`.

    Args:
        path: The ``equipment-rules.json`` file.

    Returns:
        The rules.

    Raises:
        EquipmentRulesError: As :func:`load_equipment_catalog` refuses its
            file.
    """
    reader = _EquipmentRulesFileReader(path)
    return reader.equipment_rules(reader.parsed_file())


@functools.cache
def equipment_catalog() -> EquipmentCatalog:
    """The loaded canon's equipment catalog, read once per process.

    The canon does not change under a running server; ``runserver`` restarts
    when a canon JSON file changes (``reference.apps``). Tests that need a
    fresh read call ``equipment_catalog.cache_clear()``.

    Returns:
        The catalog read from :data:`reference.paths.EQUIPMENT_CATALOG_FILE`.

    Raises:
        EquipmentCatalogError: The file is missing or malformed.
    """
    return load_equipment_catalog(EQUIPMENT_CATALOG_FILE)


@functools.cache
def equipment_rules() -> EquipmentRules:
    """The loaded canon's equipment rules, read once per process.

    As :func:`equipment_catalog`; tests call ``equipment_rules.cache_clear()``.

    Returns:
        The rules read from :data:`reference.paths.EQUIPMENT_RULES_FILE`.

    Raises:
        EquipmentRulesError: The file is missing or malformed.
    """
    return load_equipment_rules(EQUIPMENT_RULES_FILE)


def validate_armour_restriction_codes(
    catalog: EquipmentCatalog, rules: EquipmentRules, path: Path
) -> None:
    """Refuse an armour whose ``movement_restriction`` the rules do not give.

    The code is the catalog's and its meaning the rules', so neither loader
    alone can see a code with no meaning: the armour would restrict nothing
    without a word. :func:`characters.checks.check_equipment_catalog` calls
    this, so a refusal is ``characters.E003``.

    Args:
        catalog: The catalog read from ``path``.
        rules: The loaded equipment rules.
        path: The ``equipment.json`` the catalog was read from.

    Raises:
        EquipmentCatalogError: Some armour names an unknown code.
    """
    unknown = [
        f"armor.{identifier}.movement_restriction {armour.movement_restriction!r}"
        for identifier, armour in catalog.armour.items()
        if armour.movement_restriction is not None
        and armour.movement_restriction not in rules.armour_restriction_codes
    ]
    if unknown:
        raise EquipmentCatalogError(
            f"{path}: {', '.join(unknown)} is not a code "
            f"{CANON_EQUIPMENT_RULES_FILE_NAME}'s armour_restriction_codes gives "
            f"{sorted(rules.armour_restriction_codes)}"
        )


def validate_missile_classes(
    rules: EquipmentRules, catalog: EquipmentCatalog, path: Path
) -> None:
    """Refuse a Missile class no catalog weapon has.

    Archery would then cover a class nothing attacks as, and a misspelt class
    would leave the real one uncovered without a word.
    :func:`characters.checks.check_equipment_rules` calls this, so a refusal
    is ``characters.E004``.

    Args:
        rules: The rules read from ``path``.
        catalog: The loaded equipment catalog.
        path: The ``equipment-rules.json`` the rules were read from.

    Raises:
        EquipmentRulesError: Some Missile class is no weapon's class.
    """
    unknown = sorted(rules.missile_classes - catalog.weapon_classes)
    if unknown:
        raise EquipmentRulesError(
            f"{path}: missile_classes {unknown} is no class a catalog weapon "
            f"has {sorted(catalog.weapon_classes)}"
        )
