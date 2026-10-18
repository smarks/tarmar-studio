"""The canon's character-creation rules, read from ``character-rules.json`` (#59).

Stage 2 of rules-as-data: every number character creation reads comes from
the loaded canon's ``character-rules.json``
(:data:`reference.paths.CHARACTER_RULES_FILE`), and the procedures that use
them (the point buy, the rolls, the table lookups, the shape of the Fatigue
sum) stay code. The numbers are on the public rules pages
(``reference/content/public-rules/characters/``), so the default canon's file
carries the pages' values; the Tarmar canon carries its own, free to diverge.
This module is the one path that reads them; nothing at the table writes them.

The file is a JSON object::

    {
      "attributes": {
        "maximum": int,
        "point_buy": {"total": int, "minimum": int, "maximum": int}
      },
      "age": {"starting": int, "years_per_stage": int},
      "attribute_roll": {"dice": str},
      "fatigue_roll": {"dice": str, "attributes": [str, ...],
                       "best_of": [str, ...]},
      "literacy_roll": {"dice": str},
      "height": {"base_inches_by_face": [int x 6], "inches_per_extra_one": int,
                 "inches_per_extra_six": int, "minimum_inches": int},
      "weight": {"stones_by_face": [int x 6], "pounds_per_stone": int,
                 "strength_divisor": int},
      "social_class": [{"up_to": int, "label": str}, ...],
      "nobility": [{"up_to": int, "label": str}, ...],
      "merchant": [{"up_to": int, "label": str}, ...],
      "commoner": [{"up_to": int, "label": str}, ...],
      "location": {
        "skill_points": int,
        "rows": [{"up_to": int, "label": str, "skills": [str, ...],
                  "literacy_modifier": int | null}, ...]
      },
      "wealth": [{"up_to": int, "label": str,
                  "coin": int | {"base": int, "dice": str} | null}, ...],
      "rulings": [{"key": str, "reading": str, ...}]
    }

Every field but ``rulings`` is required, and the reading is the strict one
every canon rules file shares (``reference.rules_file``): an unknown key, a
repeated key, a boolean where an integer goes, an empty string, or a name
repeated in a list is refused, naming the file and the field.

What the values mean, and what else is refused:

- ``attributes``: ``maximum`` is the most any core attribute may reach (by
  experience, past creation's range); ``point_buy`` is the budget the six
  core attributes are bought from and the range each must end in. The range
  must lie inside ``1..maximum``, and the budget must be reachable from it.
- ``age``: the age a character starts at, and the years between stages of
  the aging table.
- ``attribute_roll``: the dice each core attribute is rolled on, when a
  player rolls rather than buys.
- ``fatigue_roll``: the dice added to the Fatigue sum, the attributes the
  sum adds (by the sheet's short names, ``STR`` ... ``CHR``), and the
  attributes of which only the highest is added (may be empty). An attribute
  in both lists is refused, since it would count twice.
- ``literacy_roll``: the dice the roll to be literate at all is made on.
- ``height``: the base height for each face of the first d6, and the inches
  each further 1 or 6 in the demon-die chain adds, floored at
  ``minimum_inches``.
- ``weight``: the base stones for each face of a d6, the pounds in a stone,
  and the divisor of STR added as stones.
- ``social_class``, ``nobility``, ``merchant``, ``commoner``, ``location``
  and ``wealth`` are percentile tables: a d100 roll takes the first row whose
  ``up_to`` it does not pass. Rows rise strictly, the first ``up_to`` is at
  least 1 and the last is 100, and no label repeats. ``social_class``'s
  labels are exactly ``nobility``, ``merchant`` and ``commoner``, the tables
  its result is rolled on next.
- ``location``: each origin's ``skills`` are those its grant may be held in,
  the first being the one granted, at ``skill_points``; its
  ``literacy_modifier`` is the penalty on the literacy roll, ``null`` where
  the page leaves the result to the Game Master.
- ``wealth``: each level's starting coin, a fixed number, a base plus dice,
  or ``null`` where the Game Master sets it.

Dice are ``NdS`` or ``NdS±M`` in ``common.rolling``'s standard form (no
spaces, a lower-case ``d``, no leading zeros, no ``+0``); the lowest and
highest totals each roll can give are derived from its dice, not stored.
"""

import functools
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from common.rolling import dice_total_range, standard_dice_specification
from reference.paths import CHARACTER_RULES_FILE
from reference.rules_file import RulesFileError, RulesFileReader

from .constants import CORE_ATTRIBUTE_FIELDS

#: The core attributes' short names, as the file names them.
ATTRIBUTE_SHORT_NAMES: tuple[str, ...] = tuple(
    short_name for _field_name, short_name in CORE_ATTRIBUTE_FIELDS
)


def scores_by_short_name[Score](
    scores_by_field: Mapping[str, Score],
) -> dict[str, Score]:
    """The six core attributes' scores keyed by short name, as the file names
    them.

    Args:
        scores_by_field: Scores by model field name (``"strength"``); other
            keys are ignored.

    Returns:
        The same scores by short name (``"STR"``).
    """
    return {
        short_name: scores_by_field[field_name]
        for field_name, short_name in CORE_ATTRIBUTE_FIELDS
    }


#: Faces on the d6 the height and weight tables are rolled on.
D6_FACES = 6
#: The highest percentile roll, which every table's last row must reach.
PERCENTILE_TOP = 100
#: The tables a ``social_class`` result is rolled on next, by its label.
SOCIAL_CLASS_DETAIL_TABLES = ("nobility", "merchant", "commoner")

POINT_BUY_FIELDS = ("total", "minimum", "maximum")
ATTRIBUTES_FIELDS = ("maximum", "point_buy")
AGE_FIELDS = ("starting", "years_per_stage")
FATIGUE_ROLL_FIELDS = ("dice", "attributes", "best_of")
#: A roll that is its dice and nothing else (``attribute_roll``,
#: ``literacy_roll``).
DICE_ROLL_FIELDS = ("dice",)
HEIGHT_INTEGER_FIELDS = (
    "inches_per_extra_one",
    "inches_per_extra_six",
    "minimum_inches",
)
HEIGHT_FIELDS = ("base_inches_by_face", *HEIGHT_INTEGER_FIELDS)
WEIGHT_INTEGER_FIELDS = ("pounds_per_stone", "strength_divisor")
WEIGHT_FIELDS = ("stones_by_face", *WEIGHT_INTEGER_FIELDS)
TABLE_ROW_FIELDS = ("up_to", "label")
LOCATION_FIELDS = ("skill_points", "rows")
LOCATION_ROW_FIELDS = (*TABLE_ROW_FIELDS, "skills", "literacy_modifier")
WEALTH_ROW_FIELDS = (*TABLE_ROW_FIELDS, "coin")
COIN_ROLL_FIELDS = ("base", "dice")
TOP_LEVEL_FIELDS = (
    "attributes",
    "age",
    "attribute_roll",
    "fatigue_roll",
    "literacy_roll",
    "height",
    "weight",
    "social_class",
    *SOCIAL_CLASS_DETAIL_TABLES,
    "location",
    "wealth",
)


class CharacterRulesError(RulesFileError):
    """The canon's ``character-rules.json`` is missing or malformed.

    The message names the file and the field at fault.
    """


def _check_dice(dice: str) -> None:
    """Refuse dice that do not parse or are not in standard form.

    Stored dice reach dice logs and the canon's expectations as written, so
    the file writes them one way only (``common.rolling``'s standard form).

    Raises:
        ValueError: ``dice`` does not parse, or is spelled another way.
    """
    standard = standard_dice_specification(dice)
    if dice != standard:
        raise ValueError(f"dice {dice!r} must be written {standard!r}")


def require_at_least(name: str, value: float, least: float) -> None:
    """Refuse ``value`` below ``least``.

    Raises:
        ValueError: ``value`` is below ``least``.
    """
    if value < least:
        raise ValueError(f"{name} must be at least {least}, got {value}")


def require_at_most(name: str, value: float, most: float) -> None:
    """Refuse ``value`` above ``most``.

    Raises:
        ValueError: ``value`` is above ``most``.
    """
    if value > most:
        raise ValueError(f"{name} must be at most {most}, got {value}")


@dataclass(frozen=True)
class PointBuyRules:
    """The point buy: a budget split between the six core attributes.

    Attributes:
        total: The points the six attributes are bought from.
        minimum: The least each attribute may end at.
        maximum: The most each attribute may end at.
    """

    total: int
    minimum: int
    maximum: int

    def __post_init__(self) -> None:
        require_at_least("minimum", self.minimum, 1)
        if self.maximum < self.minimum:
            raise ValueError(f"maximum {self.maximum} is below minimum {self.minimum}")
        attribute_count = len(ATTRIBUTE_SHORT_NAMES)
        lowest, highest = attribute_count * self.minimum, attribute_count * self.maximum
        if not lowest <= self.total <= highest:
            raise ValueError(
                f"total {self.total} cannot be spent on {attribute_count} "
                f"attributes of {self.minimum} to {self.maximum} "
                f"({lowest} to {highest})"
            )


@dataclass(frozen=True)
class AttributeRules:
    """The core attributes' ceiling and how they are bought.

    Attributes:
        maximum: The most any core attribute may reach, by experience too.
        point_buy: The point buy creation offers beside rolling.
    """

    maximum: int
    point_buy: PointBuyRules

    def __post_init__(self) -> None:
        if self.point_buy.maximum > self.maximum:
            raise ValueError(
                f"point_buy.maximum {self.point_buy.maximum} is above the "
                f"attribute maximum {self.maximum}"
            )


@dataclass(frozen=True)
class AgeRules:
    """The starting age and the aging table's stage length.

    Attributes:
        starting: The age every character starts at.
        years_per_stage: Years of life between stages of the aging table.
    """

    starting: int
    years_per_stage: int

    def __post_init__(self) -> None:
        require_at_least("starting", self.starting, 1)
        require_at_least("years_per_stage", self.years_per_stage, 1)


@dataclass(frozen=True)
class DiceRollRules:
    """A roll made on one dice expression, with the totals it can give.

    Attributes:
        dice: ``NdS`` or ``NdS±M``.
    """

    dice: str

    def __post_init__(self) -> None:
        _check_dice(self.dice)

    @property
    def minimum(self) -> int:
        """The lowest total the dice can roll."""
        return dice_total_range(self.dice)[0]

    @property
    def maximum(self) -> int:
        """The highest total the dice can roll."""
        return dice_total_range(self.dice)[1]

    def refusal(self, roll_name: str, value: int) -> str | None:
        """Why ``value`` cannot be this roll's total, or ``None`` when it can.

        The one wording of the refusal, for the model's validator and the
        services that record a stated roll alike. A roll is dice, not a build
        choice, so no Game Master override reaches past it (issue #653).

        Args:
            roll_name: The roll as the sentence names it, e.g. ``"The Fatigue
                roll"``.
            value: The stated total.

        Returns:
            The sentence, or ``None`` when the dice can produce ``value``.
        """
        if self.minimum <= value <= self.maximum:
            return None
        return (
            f"{roll_name} is {self.dice}, so it must be between {self.minimum} "
            f"and {self.maximum}; {value} was given"
        )


@dataclass(frozen=True)
class FatigueRollRules(DiceRollRules):
    """The dice in the Fatigue sum, and the attributes the sum adds.

    Attributes:
        dice: The dice added to the sum.
        attributes: Attributes added in full, by short name.
        best_of: Attributes of which only the highest is added; may be empty.
    """

    attributes: tuple[str, ...]
    best_of: tuple[str, ...]

    def total(self, scores: Mapping[str, int], roll: int) -> int:
        """The Fatigue sum: the attributes, the best of ``best_of``, the roll.

        The one statement of the sum's shape; the Character sheet and the
        bestiary's pools both come through here.

        Args:
            scores: Every core attribute's score, by short name (``"STR"``).
            roll: The Fatigue roll's total.

        Returns:
            The Fatigue pool.
        """
        best = max((scores[name] for name in self.best_of), default=0)
        return sum(scores[name] for name in self.attributes) + best + roll

    def formula(self, roll_term: str) -> str:
        """The sum written out: "CON + WIS + INT + max(DEX, STR) + roll".

        In :meth:`total`'s shape: the attributes, the best of ``best_of``
        (plainly when it names one), then the roll. The one rendering of the
        sum, for the sheet, its help and the Games Master's prompt (issue #68).

        Args:
            roll_term: What the roll is called at the end: ``"roll"``,
                ``"fatigue roll"``, or the dice.

        Returns:
            The formula.
        """
        terms = list(self.attributes)
        if len(self.best_of) > 1:
            terms.append(f"max({', '.join(self.best_of)})")
        else:
            terms.extend(self.best_of)
        terms.append(roll_term)
        return " + ".join(terms)

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.attributes:
            raise ValueError("attributes names no attribute")
        unknown = sorted(
            {*self.attributes, *self.best_of}.difference(ATTRIBUTE_SHORT_NAMES)
        )
        if unknown:
            raise ValueError(
                f"names {unknown}, which are not core attributes "
                f"{list(ATTRIBUTE_SHORT_NAMES)}"
            )
        counted_twice = sorted(set(self.attributes).intersection(self.best_of))
        if counted_twice:
            raise ValueError(
                f"{counted_twice} is in both attributes and best_of, so it "
                "would count twice"
            )


@dataclass(frozen=True)
class AttributeRollRules(DiceRollRules):
    """The roll each core attribute is made on, when it is rolled.

    Attributes:
        dice: The dice one attribute is rolled on.
    """


@dataclass(frozen=True)
class LiteracyRollRules(DiceRollRules):
    """The roll to be literate at all.

    Attributes:
        dice: The dice the roll is made on.
    """

    def check_text(self, modifier: int) -> str:
        """The check as the sheet and the rules print it, e.g. "3d6 ≤ INT − 2".

        Written in the rulebook's form for a penalized check, so the penalty
        reads in the direction it cuts rather than looking like a bonus.

        Args:
            modifier: An origin's literacy modifier.
        """
        check = f"{self.dice} ≤ INT"
        return f"{check} − {modifier}" if modifier else check


def _check_faces(name: str, values: tuple[int, ...]) -> None:
    """Refuse a d6 table that is not six positive numbers.

    Raises:
        ValueError: ``values`` is not six numbers, or one is below 1.
    """
    if len(values) != D6_FACES:
        raise ValueError(
            f"{name} must give one value per face of a d6 ({D6_FACES}), "
            f"got {len(values)}"
        )
    for value in values:
        require_at_least(f"each of {name}", value, 1)


@dataclass(frozen=True)
class HeightRules:
    """The height table and its demon-die chain.

    Attributes:
        base_inches_by_face: Base height for each face of the first d6.
        inches_per_extra_one: Inches each further 1 in the chain adds.
        inches_per_extra_six: Inches each further 6 in the chain adds.
        minimum_inches: The floor a long chain of 1s cannot go below.
    """

    base_inches_by_face: tuple[int, ...]
    inches_per_extra_one: int
    inches_per_extra_six: int
    minimum_inches: int

    def __post_init__(self) -> None:
        _check_faces("base_inches_by_face", self.base_inches_by_face)
        require_at_least("minimum_inches", self.minimum_inches, 1)


@dataclass(frozen=True)
class WeightRules:
    """The weight table and the STR bonus.

    Attributes:
        stones_by_face: Base weight in stones for each face of a d6.
        pounds_per_stone: Pounds in a stone.
        strength_divisor: STR is divided by this and added as stones.
    """

    stones_by_face: tuple[int, ...]
    pounds_per_stone: int
    strength_divisor: int

    def __post_init__(self) -> None:
        _check_faces("stones_by_face", self.stones_by_face)
        require_at_least("pounds_per_stone", self.pounds_per_stone, 1)
        require_at_least("strength_divisor", self.strength_divisor, 1)


@dataclass(frozen=True)
class TableRow:
    """One row of a percentile table.

    Attributes:
        up_to: The highest d100 roll that lands on this row.
        label: What the row gives.
    """

    up_to: int
    label: str


@dataclass(frozen=True)
class PercentileTable[Row: TableRow]:
    """Rows a d100 roll is looked up in.

    Attributes:
        rows: Rising strictly in ``up_to``, the first at least 1 and the last
            100, with no label twice.
    """

    rows: tuple[Row, ...]

    def __post_init__(self) -> None:
        if not self.rows:
            raise ValueError("has no rows")
        if self.rows[0].up_to < 1:
            raise ValueError(
                f"the first row's up_to must be at least 1, got {self.rows[0].up_to}"
            )
        for lower, higher in pairwise(self.rows):
            if higher.up_to <= lower.up_to:
                raise ValueError(
                    f"rows must rise in up_to: {lower.label!r} reaches "
                    f"{lower.up_to} and {higher.label!r} {higher.up_to}"
                )
        if self.rows[-1].up_to != PERCENTILE_TOP:
            raise ValueError(
                f"the last row's up_to must be {PERCENTILE_TOP}, "
                f"got {self.rows[-1].up_to}"
            )
        labels = [row.label for row in self.rows]
        repeated = sorted({label for label in labels if labels.count(label) > 1})
        if repeated:
            raise ValueError(f"repeats the label {repeated}")

    @property
    def labels(self) -> tuple[str, ...]:
        """Every row's label, in order."""
        return tuple(row.label for row in self.rows)

    def row_for(self, roll: int) -> Row:
        """The row a roll lands on.

        The first row whose ``up_to`` the roll does not pass; a roll past
        every row takes the last.

        Args:
            roll: The d100 roll.

        Returns:
            The row.
        """
        for row in self.rows:
            if roll <= row.up_to:
                return row
        return self.rows[-1]

    def row_labelled(self, label: str) -> Row | None:
        """The row carrying ``label``, or ``None`` when no row does.

        A character's record holds the label it rolled or was given, so the
        row is found again by it (its skills, its coin).

        Args:
            label: A row's label, e.g. ``"Village"``.

        Returns:
            The row, or ``None`` for a label the table does not have.
        """
        for row in self.rows:
            if row.label == label:
                return row
        return None


@dataclass(frozen=True)
class RollTable(PercentileTable[TableRow]):
    """A percentile table that gives a label."""

    def lookup(self, roll: int) -> str:
        """The label a roll lands on.

        Args:
            roll: The d100 roll.

        Returns:
            The row's label.
        """
        return self.row_for(roll).label


@dataclass(frozen=True)
class LocationRow(TableRow):
    """One origin of the location table.

    Attributes:
        skills: The skills the origin's grant may be held in, the first being
            the one granted; empty when it grants none.
        literacy_modifier: The penalty on the literacy roll, or ``None`` when
            the Game Master decides.
    """

    skills: tuple[str, ...]
    literacy_modifier: int | None

    @property
    def granted_skill(self) -> str | None:
        """The skill the origin grants, or ``None`` when it grants none."""
        return self.skills[0] if self.skills else None


@dataclass(frozen=True)
class LocationTable(PercentileTable[LocationRow]):
    """The location table and the points its granted skill starts at.

    Attributes:
        skill_points: Points the granted skill starts at.
    """

    skill_points: int

    def __post_init__(self) -> None:
        super().__post_init__()
        require_at_least("skill_points", self.skill_points, 0)


#: Percentile dice, which the logs write ``d100`` and the rules "percentile
#: dice".
PERCENTILE_DICE = "1d100"


@dataclass(frozen=True)
class CoinRoll:
    """Starting coin rolled on top of a base.

    Attributes:
        base: The coin before the roll.
        dice: The dice added to it.
    """

    base: int
    dice: str

    @property
    def is_percentile(self) -> bool:
        """Whether the dice are percentile dice (``1d100``)."""
        return self.dice == PERCENTILE_DICE

    @property
    def dice_label(self) -> str:
        """The dice as the dice log writes them: ``d100`` for percentile."""
        return "d100" if self.is_percentile else self.dice

    @property
    def dice_in_words(self) -> str:
        """The dice as a rules answer says them: "percentile dice"."""
        return "percentile dice" if self.is_percentile else self.dice

    def __post_init__(self) -> None:
        require_at_least("base", self.base, 0)
        _check_dice(self.dice)


@dataclass(frozen=True)
class WealthRow(TableRow):
    """One level of the wealth table.

    Attributes:
        coin: The starting coin: a fixed number, a :class:`CoinRoll`, or
            ``None`` when the Game Master sets it.
    """

    coin: int | CoinRoll | None

    @property
    def coin_text(self) -> str:
        """The starting coin as the sheet writes it: "100", "100 + d100" or
        "by GM" (issue #68)."""
        if self.coin is None:
            return "by GM"
        if isinstance(self.coin, CoinRoll):
            return f"{self.coin.base} + {self.coin.dice_label}"
        return str(self.coin)

    def __post_init__(self) -> None:
        if isinstance(self.coin, int):
            require_at_least("coin", self.coin, 0)


@dataclass(frozen=True)
class WealthTable(PercentileTable[WealthRow]):
    """The wealth table with each level's starting coin."""


@dataclass(frozen=True)
class CharacterRules:
    """Every number character creation reads from the canon.

    Attributes:
        attributes: The attribute ceiling and the point buy.
        age: The starting age and the aging stage length.
        attribute_roll: The dice each core attribute is rolled on.
        fatigue_roll: The Fatigue sum's dice and attributes.
        literacy_roll: The literacy roll's dice.
        height: The height table.
        weight: The weight table.
        social_class: The first provenance roll.
        nobility: The provenance detail for nobility.
        merchant: The provenance detail for a merchant.
        commoner: The provenance detail for a commoner.
        location: The origin table and its effects.
        wealth: The wealth table and starting coin.
    """

    attributes: AttributeRules
    age: AgeRules
    attribute_roll: AttributeRollRules
    fatigue_roll: FatigueRollRules
    literacy_roll: LiteracyRollRules
    height: HeightRules
    weight: WeightRules
    social_class: RollTable
    nobility: RollTable
    merchant: RollTable
    commoner: RollTable
    location: LocationTable
    wealth: WealthTable

    def detail_table(self, social_class: str) -> RollTable:
        """The table a ``social_class`` result is rolled on next.

        Args:
            social_class: A label of ``social_class``, e.g. ``"merchant"``.

        Raises:
            KeyError: ``social_class`` names no detail table.
        """
        if social_class not in SOCIAL_CLASS_DETAIL_TABLES:
            raise KeyError(social_class)
        # The detail tables are the fields named for them.
        table: RollTable = getattr(self, social_class)
        return table

    def __post_init__(self) -> None:
        if sorted(self.social_class.labels) != sorted(SOCIAL_CLASS_DETAIL_TABLES):
            raise ValueError(
                f"social_class labels {list(self.social_class.labels)} must be "
                f"the detail tables {list(SOCIAL_CLASS_DETAIL_TABLES)}"
            )


class _CharacterRulesFileReader(RulesFileReader):
    """Reads one ``character-rules.json`` into :class:`CharacterRules`.

    The shared checks are :class:`reference.rules_file.RulesFileReader`'s;
    this adds the character blocks. Each method takes the value and its field
    path in the file and returns the dataclass, or raises
    :class:`CharacterRulesError`.
    """

    error_class = CharacterRulesError

    def point_buy(self, value: Any, field_path: str) -> PointBuyRules:
        """``attributes.point_buy``."""
        fields = self.json_object(value, field_path, POINT_BUY_FIELDS)
        integers = self.integers(fields, field_path, POINT_BUY_FIELDS)
        with self.wrapped_value_errors(field_path):
            return PointBuyRules(**integers)

    def attributes(self, value: Any, field_path: str) -> AttributeRules:
        """``attributes``."""
        fields = self.json_object(value, field_path, ATTRIBUTES_FIELDS)
        maximum = self.integer(fields["maximum"], f"{field_path}.maximum")
        point_buy = self.point_buy(fields["point_buy"], f"{field_path}.point_buy")
        with self.wrapped_value_errors(field_path):
            return AttributeRules(maximum=maximum, point_buy=point_buy)

    def age(self, value: Any, field_path: str) -> AgeRules:
        """``age``."""
        fields = self.json_object(value, field_path, AGE_FIELDS)
        integers = self.integers(fields, field_path, AGE_FIELDS)
        with self.wrapped_value_errors(field_path):
            return AgeRules(**integers)

    def fatigue_roll(self, value: Any, field_path: str) -> FatigueRollRules:
        """``fatigue_roll``."""
        fields = self.json_object(value, field_path, FATIGUE_ROLL_FIELDS)
        dice = self.text(fields["dice"], f"{field_path}.dice")
        attributes = self.text_list(fields["attributes"], f"{field_path}.attributes")
        best_of = self.text_list(fields["best_of"], f"{field_path}.best_of")
        with self.wrapped_value_errors(field_path):
            return FatigueRollRules(dice=dice, attributes=attributes, best_of=best_of)

    def dice_roll[Roll: DiceRollRules](
        self, value: Any, field_path: str, roll_class: type[Roll]
    ) -> Roll:
        """``attribute_roll`` or ``literacy_roll``: a roll that is its dice."""
        fields = self.json_object(value, field_path, DICE_ROLL_FIELDS)
        dice = self.text(fields["dice"], f"{field_path}.dice")
        with self.wrapped_value_errors(field_path):
            return roll_class(dice=dice)

    def height(self, value: Any, field_path: str) -> HeightRules:
        """``height``."""
        fields = self.json_object(value, field_path, HEIGHT_FIELDS)
        faces = self.integer_list(
            fields["base_inches_by_face"], f"{field_path}.base_inches_by_face"
        )
        integers = self.integers(fields, field_path, HEIGHT_INTEGER_FIELDS)
        with self.wrapped_value_errors(field_path):
            return HeightRules(base_inches_by_face=faces, **integers)

    def weight(self, value: Any, field_path: str) -> WeightRules:
        """``weight``."""
        fields = self.json_object(value, field_path, WEIGHT_FIELDS)
        faces = self.integer_list(
            fields["stones_by_face"], f"{field_path}.stones_by_face"
        )
        integers = self.integers(fields, field_path, WEIGHT_INTEGER_FIELDS)
        with self.wrapped_value_errors(field_path):
            return WeightRules(stones_by_face=faces, **integers)

    def table_row(self, value: Any, field_path: str) -> TableRow:
        """One row of a label table."""
        fields = self.json_object(value, field_path, TABLE_ROW_FIELDS)
        return TableRow(
            up_to=self.integer(fields["up_to"], f"{field_path}.up_to"),
            label=self.text(fields["label"], f"{field_path}.label"),
        )

    def roll_table(self, value: Any, field_path: str) -> RollTable:
        """``social_class``, ``nobility``, ``merchant`` or ``commoner``."""
        rows = self.entries(value, field_path, self.table_row)
        with self.wrapped_value_errors(field_path):
            return RollTable(rows=rows)

    def location_row(self, value: Any, field_path: str) -> LocationRow:
        """One row of ``location.rows``."""
        fields = self.json_object(value, field_path, LOCATION_ROW_FIELDS)
        return LocationRow(
            up_to=self.integer(fields["up_to"], f"{field_path}.up_to"),
            label=self.text(fields["label"], f"{field_path}.label"),
            skills=self.text_list(fields["skills"], f"{field_path}.skills"),
            literacy_modifier=self.optional_integer(
                fields["literacy_modifier"], f"{field_path}.literacy_modifier"
            ),
        )

    def location(self, value: Any, field_path: str) -> LocationTable:
        """``location``."""
        fields = self.json_object(value, field_path, LOCATION_FIELDS)
        skill_points = self.integer(
            fields["skill_points"], f"{field_path}.skill_points"
        )
        rows = self.entries(fields["rows"], f"{field_path}.rows", self.location_row)
        with self.wrapped_value_errors(field_path):
            return LocationTable(rows=rows, skill_points=skill_points)

    def coin(self, value: Any, field_path: str) -> int | CoinRoll | None:
        """A wealth row's ``coin``: a number, a base plus dice, or ``null``."""
        if value is None:
            return None
        if isinstance(value, dict):
            fields = self.json_object(value, field_path, COIN_ROLL_FIELDS)
            base = self.integer(fields["base"], f"{field_path}.base")
            dice = self.text(fields["dice"], f"{field_path}.dice")
            with self.wrapped_value_errors(field_path):
                return CoinRoll(base=base, dice=dice)
        if not isinstance(value, int) or isinstance(value, bool):
            self.fail(
                field_path,
                'must be an integer, {"base": int, "dice": str} or null, '
                f"got {value!r}",
            )
        return value

    def wealth_row(self, value: Any, field_path: str) -> WealthRow:
        """One row of ``wealth``."""
        fields = self.json_object(value, field_path, WEALTH_ROW_FIELDS)
        up_to = self.integer(fields["up_to"], f"{field_path}.up_to")
        label = self.text(fields["label"], f"{field_path}.label")
        coin = self.coin(fields["coin"], f"{field_path}.coin")
        with self.wrapped_value_errors(field_path):
            return WealthRow(up_to=up_to, label=label, coin=coin)

    def wealth(self, value: Any, field_path: str) -> WealthTable:
        """``wealth``."""
        rows = self.entries(value, field_path, self.wealth_row)
        with self.wrapped_value_errors(field_path):
            return WealthTable(rows=rows)

    def character_rules(self, value: Any) -> CharacterRules:
        """The whole file."""
        fields = self.file_object(value, TOP_LEVEL_FIELDS)
        attributes = self.attributes(fields["attributes"], "attributes")
        age = self.age(fields["age"], "age")
        attribute_roll = self.dice_roll(
            fields["attribute_roll"], "attribute_roll", AttributeRollRules
        )
        fatigue_roll = self.fatigue_roll(fields["fatigue_roll"], "fatigue_roll")
        literacy_roll = self.dice_roll(
            fields["literacy_roll"], "literacy_roll", LiteracyRollRules
        )
        height = self.height(fields["height"], "height")
        weight = self.weight(fields["weight"], "weight")
        tables = {
            name: self.roll_table(fields[name], name)
            for name in ("social_class", *SOCIAL_CLASS_DETAIL_TABLES)
        }
        location = self.location(fields["location"], "location")
        wealth = self.wealth(fields["wealth"], "wealth")
        # The one check across blocks: social_class's labels name the tables.
        with self.wrapped_value_errors("social_class"):
            return CharacterRules(
                attributes=attributes,
                age=age,
                attribute_roll=attribute_roll,
                fatigue_roll=fatigue_roll,
                literacy_roll=literacy_roll,
                height=height,
                weight=weight,
                location=location,
                wealth=wealth,
                **tables,
            )


def load_character_rules(path: Path) -> CharacterRules:
    """Read a canon's ``character-rules.json`` into :class:`CharacterRules`.

    The schema is in this module's docstring; the file-level checks are
    :meth:`reference.rules_file.RulesFileReader.parsed_file`'s.

    Args:
        path: The ``character-rules.json`` file.

    Returns:
        The rules.

    Raises:
        CharacterRulesError: The file is missing or unreadable, is not JSON,
            repeats a key in one object, or a field is missing, unknown, of
            the wrong type, or refused by the rules' own validation; the
            message names the file and the field.
    """
    reader = _CharacterRulesFileReader(path)
    return reader.character_rules(reader.parsed_file())


@functools.cache
def character_rules() -> CharacterRules:
    """The loaded canon's character rules, read once per process.

    The canon does not change under a running server; ``runserver`` restarts
    when a canon JSON file changes (``reference.apps``). Tests that need a
    fresh read call ``character_rules.cache_clear()``.

    Returns:
        The rules read from :data:`reference.paths.CHARACTER_RULES_FILE`.

    Raises:
        CharacterRulesError: The file is missing or malformed.
    """
    return load_character_rules(CHARACTER_RULES_FILE)
