"""The canon's skills and experience rules, read from ``skill-rules.json`` (#70).

Stage 3 of rules-as-data: every number the skills and prior-experience code
reads comes from the loaded canon's ``skill-rules.json``
(:data:`reference.paths.SKILL_RULES_FILE`), and the procedures that use them
(the triangular skill cost, the experience simulation, the check resolution)
stay code. The numbers are on the public pages
(``reference/content/public-rules/characters/``: ``attributes.md``,
``prior-experience.md`` and ``prior-experience/``'s ``skill-tracks.md``,
``survivability.md``, ``aging.md`` and ``skills-glossary.md``), so the default
canon's file carries the pages' values; the Tarmar canon carries its own,
free to diverge. This module is the one path that reads them; nothing at the
table writes them. The starting age and the years per aging stage are
``character-rules.json``'s ``age`` (:mod:`characters.rules`), not this file's.

The file is a JSON object::

    {
      "skill_cost": {"multipliers": {str: int, ...}},
      "tracks": {
        str: {"label": str, "xp_per_year": int, "survival_target": int,
              "money_multiplier": int, "money_skill": str | null,
              "skills": [str x 20]},
        ...
      },
      "survival_roll": {"dice": str, "automatic_death_total": int,
                        "automatic_survival_total": int},
      "experience_levels": [{"level": int, "minimum_xp": int,
                             "xp_per_skill_point": int | null}, ...],
      "aging": {
        "first_stage_with_penalties": int,
        "stages_per_mental_step": int,
        "stages": [{"stage": int, "strength": int, "dexterity": int,
                    "intelligence": int, "wisdom": int,
                    "constitution": int}, ...]
      },
      "attribute_modifier": {"lowest_score": int, "modifiers": [int, ...]},
      "skill_check": {"dice": str, "natural_success": int,
                      "natural_failure": int, "focused_field_bonus": int,
                      "governing_attributes": {str: str | null, ...},
                      "implied_governing_attributes": {str: str, ...}},
      "attribute_check": {"dice": str, "game_master_modifier_limit": int},
      "rulings": [{"key": str, "reading": str, ...}]
    }

Every field but ``rulings`` is required, and the reading is the strict one
every canon rules file shares (``reference.rules_file``): an unknown key, a
repeated key, a boolean where an integer goes, or an empty string is refused,
naming the file and the field.

What the values mean, and what else is refused:

- ``skill_cost.multipliers``: skills that cost a multiple of the ordinary
  triangular cost (Arcane costs double), by stored skill name; a skill not
  listed costs the ordinary amount. Each multiplier is at least 1.
- ``tracks``: each prior-experience track by its key, with its label (no two
  alike), the XP a year in it gives (at least 1), the survival roll's target,
  the money a year pays per skill level (at least 1), the skill whose level
  sets that pay (``null`` for the highest skill held), and its d20 skill
  table, one entry per face. Entries may repeat, as the pages' tables do.
- ``survival_roll``: the dice of the survival roll each year in a track
  makes, and the dice totals that kill or save whatever the target. Both lie
  within the dice's totals, the death total below the survival total.
- ``experience_levels``: the ladder, level 1 upward with none skipped, each
  level's ``minimum_xp`` rising from 0, and ``xp_per_skill_point``, the XP
  per skill point earned on the climb to that level from the previous
  level's ``minimum_xp`` (prior-experience.md: at 3,000 XP a character has
  earned two points, at 1,000 and 2,000, while still level 1, at the level-2
  row's rate). Past the top level its rate continues. Level 1 has no climb,
  so its rate is ``null``; every later level's is at least 1 XP.
- ``aging``: the cumulative penalties at each tabulated stage (consecutive
  stages, every penalty 0 or less), the first stage that carries one (within
  the table or one past it, with no penalty in a stage before it), and how
  many stages past the table INT and WIS take to lose a further point
  (STR, DEX and CON lose one every stage).
- ``attribute_modifier``: the modifier for each score from ``lowest_score``
  upward; a score off either end takes the nearest row's.
- ``skill_check``: the dice, the dice totals that always succeed and always
  fail, the bonus a focused Knowledge field adds (0 or more), and the
  attribute each skill rides on by stored skill name: ``governing_attributes``
  as the glossary tables give them (``null`` where a skill is governed by no
  attribute), ``implied_governing_attributes`` for skills no table lists. The
  attributes are the six core attributes' field names (``"strength"``), no
  skill is in both maps, ``governing_attributes`` names every skill the code
  reads by name (:data:`REQUIRED_SKILL_NAMES`), and every skill a track's
  ``money_skill`` or ``skill_cost.multipliers`` names is in one of the maps.
- ``attribute_check``: the dice, and the widest situational modifier a Game
  Master may set on the target, either way (0 or more).

Skill names in ``skill_cost``, ``money_skill`` and the checks are written as
they are stored (:func:`characters.skill_names.normalize_skill_name`: lower
case, no level numeral), so a lookup by a stored name finds them, and each
is a glossary skill in its own right: not a weapon's name, which falls under
Weapon, nor a chosen part such as "maker skill (smith)". That last needs
``characters.skills``, which imports this module, so the ``characters.E002``
system check makes it (:func:`characters.skills.validate_skill_names`). Dice are
written as :mod:`characters.rules` requires, and each roll's lowest and
highest totals are derived from its dice, not stored.
"""

import functools
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from reference.paths import SKILL_RULES_FILE
from reference.rules_file import RulesFileError, RulesFileReader

from .constants import CHARACTER_ATTRIBUTE_FIELDS
from .rules import DiceRollRules, require_at_least
from .skill_names import normalize_skill_name

#: Faces on the d20 each track's skill table is rolled on (skill-tracks.md).
TRACK_TABLE_DIE_FACES = 20
#: Skills the code reads by name, which ``skill_check.governing_attributes``
#: must therefore name:
#:
#: * ``"weapon"``: every Weapon skill, tied to a weapon or not, falls under it
#:   (``characters.weapon_skill.UNRECORDED_WEAPON_SKILL``, read by
#:   ``characters.skills.glossary_skill`` and
#:   ``characters.skill_checks.governing_attribute``).
#: * ``"maker skill"`` and ``"knowledge"``: taken with a chosen part
#:   (``characters.skills.SPECIALISED_SKILLS``), whose attribute
#:   ``skill_checks.governing_attribute`` looks up; the Craft track's pay
#:   reads ``"maker skill"``, and a focused ``"knowledge"`` field adds
#:   ``focused_field_bonus`` (``skill_checks.skill_check``).
#: * ``"literacy"``: the Literacy skill whose check an illiterate character
#:   cannot roll (``characters.skill_checks.LITERACY_SKILL``).
REQUIRED_SKILL_NAMES: tuple[str, ...] = (
    "weapon",
    "maker skill",
    "knowledge",
    "literacy",
)
#: The attributes aging lowers by one every stage past the table.
PHYSICAL_AGING_ATTRIBUTES = ("strength", "dexterity", "constitution")
#: The attributes aging lowers by one every ``stages_per_mental_step`` stages
#: past the table.
MENTAL_AGING_ATTRIBUTES = ("intelligence", "wisdom")
#: Every attribute aging lowers, in the order a stage's penalties are given:
#: the physical and the mental ones together.
AGING_ATTRIBUTES = ("strength", "dexterity", "intelligence", "wisdom", "constitution")

SKILL_COST_FIELDS = ("multipliers",)
TRACK_INTEGER_FIELDS = ("xp_per_year", "survival_target", "money_multiplier")
TRACK_FIELDS = ("label", *TRACK_INTEGER_FIELDS, "money_skill", "skills")
SURVIVAL_ROLL_INTEGER_FIELDS = ("automatic_death_total", "automatic_survival_total")
SURVIVAL_ROLL_FIELDS = ("dice", *SURVIVAL_ROLL_INTEGER_FIELDS)
EXPERIENCE_LEVEL_FIELDS = ("level", "minimum_xp", "xp_per_skill_point")
AGING_INTEGER_FIELDS = ("first_stage_with_penalties", "stages_per_mental_step")
AGING_FIELDS = (*AGING_INTEGER_FIELDS, "stages")
AGING_STAGE_FIELDS = ("stage", *AGING_ATTRIBUTES)
ATTRIBUTE_MODIFIER_FIELDS = ("lowest_score", "modifiers")
SKILL_CHECK_INTEGER_FIELDS = (
    "natural_success",
    "natural_failure",
    "focused_field_bonus",
)
SKILL_CHECK_FIELDS = (
    "dice",
    *SKILL_CHECK_INTEGER_FIELDS,
    "governing_attributes",
    "implied_governing_attributes",
)
ATTRIBUTE_CHECK_FIELDS = ("dice", "game_master_modifier_limit")
TOP_LEVEL_FIELDS = (
    "skill_cost",
    "tracks",
    "survival_roll",
    "experience_levels",
    "aging",
    "attribute_modifier",
    "skill_check",
    "attribute_check",
)


class SkillRulesError(RulesFileError):
    """The canon's ``skill-rules.json`` is missing or malformed.

    The message names the file and the field at fault.
    """


def _check_stored_skill_name(name: str) -> None:
    """Refuse a skill name not written the way skills are stored.

    A lookup by a character's stored skill name would never find a name
    written another way ("Arcane" for ``"arcane"``).

    Raises:
        ValueError: ``name`` is not already in stored form.
    """
    stored = normalize_skill_name(name)
    if name != stored:
        raise ValueError(f"skill {name!r} must be written as stored, {stored!r}")


def _check_within_dice(roll: DiceRollRules, name: str, total: int) -> None:
    """Refuse a dice total the roll's dice cannot give.

    Worded by :meth:`characters.rules.DiceRollRules.refusal`, the one
    statement of a roll's range.

    Raises:
        ValueError: ``total`` lies outside the dice's lowest and highest.
    """
    refusal = roll.refusal(name, total)
    if refusal is not None:
        raise ValueError(refusal)


@dataclass(frozen=True)
class SkillCostRules:
    """The skills that cost a multiple of the ordinary triangular cost.

    Attributes:
        multipliers: The multiple each such skill charges, by stored name.
    """

    multipliers: Mapping[str, int]

    def __post_init__(self) -> None:
        for skill_name, multiplier in self.multipliers.items():
            _check_stored_skill_name(skill_name)
            require_at_least(f"{skill_name}'s multiplier", multiplier, 1)

    def multiplier_for(self, skill_name: str) -> int:
        """How many times the ordinary cost ``skill_name`` charges.

        Args:
            skill_name: A skill name, stored or as written ("Arcane III").

        Returns:
            Its multiplier, or 1 for a skill not listed.
        """
        return self.multipliers.get(normalize_skill_name(skill_name), 1)


@dataclass(frozen=True)
class SkillTrack:
    """One prior-experience track.

    Attributes:
        key: The track's key, as a character's record stores it.
        label: The track's name as the pages print it.
        xp_per_year: XP a year in the track gives.
        survival_target: The total the survival roll must reach.
        money_multiplier: Money a year pays per skill level.
        money_skill: The stored name of the skill whose level sets the pay,
            or ``None`` for the highest skill held.
        skills: The d20 table, the entry for a roll of 1 first.
    """

    key: str
    label: str
    xp_per_year: int
    survival_target: int
    money_multiplier: int
    money_skill: str | None
    skills: tuple[str, ...]

    def __post_init__(self) -> None:
        require_at_least("xp_per_year", self.xp_per_year, 1)
        require_at_least("money_multiplier", self.money_multiplier, 1)
        if self.money_skill is not None:
            _check_stored_skill_name(self.money_skill)
        if len(self.skills) != TRACK_TABLE_DIE_FACES:
            raise ValueError(
                f"skills must give one entry per face of a d20 "
                f"({TRACK_TABLE_DIE_FACES}), got {len(self.skills)}"
            )


@dataclass(frozen=True)
class SkillTrackRules:
    """Every prior-experience track, by key.

    Attributes:
        tracks: The tracks, keyed by :attr:`SkillTrack.key`.
    """

    tracks: Mapping[str, SkillTrack]

    def __post_init__(self) -> None:
        if not self.tracks:
            raise ValueError("names no track")
        for key, track in self.tracks.items():
            if track.key != key:
                raise ValueError(f"track {key!r} carries the key {track.key!r}")
        labels = [track.label for track in self.tracks.values()]
        repeated = sorted({label for label in labels if labels.count(label) > 1})
        if repeated:
            raise ValueError(f"repeats the label {repeated}")


@dataclass(frozen=True)
class SurvivalRollRules(DiceRollRules):
    """The survival roll a year in a track makes.

    Attributes:
        dice: The dice rolled.
        automatic_death_total: The dice total that kills whatever the target.
        automatic_survival_total: The dice total that saves whatever the
            target.
    """

    automatic_death_total: int
    automatic_survival_total: int

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in SURVIVAL_ROLL_INTEGER_FIELDS:
            _check_within_dice(self, name, getattr(self, name))
        if self.automatic_death_total >= self.automatic_survival_total:
            raise ValueError(
                f"automatic_death_total {self.automatic_death_total} must be "
                f"below automatic_survival_total {self.automatic_survival_total}"
            )


@dataclass(frozen=True)
class ExperienceLevel:
    """One rung of the experience ladder.

    Attributes:
        level: The level.
        minimum_xp: The lifetime XP that reaches it.
        xp_per_skill_point: The XP per skill point earned on the climb to
            this level from the previous level's ``minimum_xp`` (and, for the
            top level, past it), or ``None`` for level 1, which has no climb.
    """

    level: int
    minimum_xp: int
    xp_per_skill_point: int | None


@dataclass(frozen=True)
class ExperienceRules:
    """The experience ladder.

    Attributes:
        levels: Level 1 upward, none skipped.
    """

    levels: tuple[ExperienceLevel, ...]

    def __post_init__(self) -> None:
        if not self.levels:
            raise ValueError("has no levels")
        for expected_level, rung in enumerate(self.levels, start=1):
            if rung.level != expected_level:
                raise ValueError(
                    f"levels must run from 1 with none skipped: level "
                    f"{expected_level} is given as {rung.level}"
                )
        first = self.levels[0]
        if first.minimum_xp != 0:
            raise ValueError(f"level 1's minimum_xp must be 0, got {first.minimum_xp}")
        if first.xp_per_skill_point is not None:
            raise ValueError(
                "level 1 has no climb from a level below it, so its "
                f"xp_per_skill_point must be null, got {first.xp_per_skill_point}"
            )
        for lower, higher in pairwise(self.levels):
            if higher.minimum_xp <= lower.minimum_xp:
                raise ValueError(
                    f"minimum_xp must rise: level {lower.level} needs "
                    f"{lower.minimum_xp} and level {higher.level} {higher.minimum_xp}"
                )
        for rung in self.levels[1:]:
            if rung.xp_per_skill_point is None:
                raise ValueError(
                    f"level {rung.level}'s xp_per_skill_point is null, but the "
                    f"climb to it from level {rung.level - 1} earns skill points"
                )
            require_at_least(
                f"level {rung.level}'s xp_per_skill_point", rung.xp_per_skill_point, 1
            )

    @property
    def top_level(self) -> int:
        """The last level the ladder gives."""
        return self.levels[-1].level

    def level_for(self, total_xp: int) -> int:
        """The level ``total_xp`` lifetime XP reaches.

        Args:
            total_xp: Lifetime XP; below 0 is level 1.

        Returns:
            The highest level whose ``minimum_xp`` it reaches.
        """
        reached = [rung.level for rung in self.levels if total_xp >= rung.minimum_xp]
        return max(reached, default=self.levels[0].level)


@dataclass(frozen=True)
class AgingStage:
    """The cumulative aging penalties carried at one stage.

    Attributes:
        stage: The aging stage.
        strength: The STR penalty, 0 or less.
        dexterity: The DEX penalty, 0 or less.
        constitution: The CON penalty, 0 or less.
        intelligence: The INT penalty, 0 or less.
        wisdom: The WIS penalty, 0 or less.
    """

    stage: int
    strength: int
    dexterity: int
    constitution: int
    intelligence: int
    wisdom: int

    def __post_init__(self) -> None:
        for attribute in AGING_ATTRIBUTES:
            penalty = getattr(self, attribute)
            if penalty > 0:
                raise ValueError(f"{attribute} must be 0 or less, got {penalty}")

    @property
    def penalties(self) -> dict[str, int]:
        """The penalties by attribute field name."""
        return {attribute: getattr(self, attribute) for attribute in AGING_ATTRIBUTES}


@dataclass(frozen=True)
class AgingRules:
    """The aging table and how decline continues past it.

    Attributes:
        first_stage_with_penalties: The first stage carrying any penalty.
        stages_per_mental_step: Stages past the table for INT and WIS to lose
            a further point; STR, DEX and CON lose one every stage.
        stages: The tabulated stages, consecutive.
    """

    first_stage_with_penalties: int
    stages_per_mental_step: int
    stages: tuple[AgingStage, ...]

    def __post_init__(self) -> None:
        require_at_least("stages_per_mental_step", self.stages_per_mental_step, 1)
        if not self.stages:
            raise ValueError("has no stages")
        for lower, higher in pairwise(self.stages):
            if higher.stage != lower.stage + 1:
                raise ValueError(
                    f"stages must be consecutive: stage {lower.stage} is "
                    f"followed by {higher.stage}"
                )
        first_tabulated, last_tabulated = self.stages[0].stage, self.stages[-1].stage
        if not first_tabulated <= self.first_stage_with_penalties <= last_tabulated + 1:
            raise ValueError(
                f"first_stage_with_penalties {self.first_stage_with_penalties} "
                f"must be a tabulated stage ({first_tabulated} to "
                f"{last_tabulated}) or the one after"
            )
        for aging_stage in self.stages:
            if aging_stage.stage < self.first_stage_with_penalties and any(
                aging_stage.penalties.values()
            ):
                raise ValueError(
                    f"stage {aging_stage.stage} carries a penalty before "
                    f"first_stage_with_penalties {self.first_stage_with_penalties}"
                )

    def penalties_for(self, stage: int) -> dict[str, int] | None:
        """The cumulative penalties at aging stage ``stage``.

        Before :attr:`first_stage_with_penalties` there is no aging
        (``None``). A tabulated stage gives its row. Past the last tabulated
        stage each attribute keeps falling from its own last tabulated value
        (aging.md: each gets a further -1): STR, DEX and CON by one every
        stage, INT and WIS by one every :attr:`stages_per_mental_step`
        stages, uncapped.

        Args:
            stage: The aging stage.

        Returns:
            The penalties by attribute field name, or ``None``.
        """
        if stage < self.first_stage_with_penalties:
            return None
        last = self.stages[-1]
        if stage <= last.stage:
            return self.stages[stage - self.stages[0].stage].penalties
        stages_past_table = stage - last.stage
        further_loss = {
            **dict.fromkeys(PHYSICAL_AGING_ATTRIBUTES, stages_past_table),
            **dict.fromkeys(
                MENTAL_AGING_ATTRIBUTES,
                stages_past_table // self.stages_per_mental_step,
            ),
        }
        return {
            attribute: penalty - further_loss[attribute]
            for attribute, penalty in last.penalties.items()
        }


@dataclass(frozen=True)
class AttributeModifierRules:
    """The attribute modifier table.

    Attributes:
        lowest_score: The score of the table's first row.
        modifiers: The modifier for each score from ``lowest_score`` up.
    """

    lowest_score: int
    modifiers: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.modifiers:
            raise ValueError("modifiers has no rows")

    @property
    def highest_score(self) -> int:
        """The score of the table's last row."""
        return self.lowest_score + len(self.modifiers) - 1

    def modifier_for(self, score: int) -> int:
        """The modifier for ``score``.

        A score off either end of the table takes the nearest row's modifier
        (issue #806).

        Args:
            score: An attribute score.

        Returns:
            Its modifier.
        """
        row = min(max(score, self.lowest_score), self.highest_score)
        return self.modifiers[row - self.lowest_score]


@dataclass(frozen=True)
class SkillCheckRules(DiceRollRules):
    """The skill check: dice, naturals, and each skill's attribute.

    Attributes:
        dice: The dice rolled.
        natural_success: The dice total that always succeeds.
        natural_failure: The dice total that always fails.
        focused_field_bonus: What a focused Knowledge field adds to a check
            in it.
        governing_attributes: The attribute each glossary skill rides on, by
            stored skill name; ``None`` for a skill governed by none.
        implied_governing_attributes: The attribute the rules imply for a
            skill no glossary table lists.
    """

    natural_success: int
    natural_failure: int
    focused_field_bonus: int
    governing_attributes: Mapping[str, str | None]
    implied_governing_attributes: Mapping[str, str]

    def __post_init__(self) -> None:
        super().__post_init__()
        _check_within_dice(self, "natural_success", self.natural_success)
        _check_within_dice(self, "natural_failure", self.natural_failure)
        if self.natural_failure >= self.natural_success:
            raise ValueError(
                f"natural_failure {self.natural_failure} must be below "
                f"natural_success {self.natural_success}"
            )
        require_at_least("focused_field_bonus", self.focused_field_bonus, 0)
        attributes_by_skill = {
            **self.governing_attributes,
            **self.implied_governing_attributes,
        }
        for skill_name, attribute in attributes_by_skill.items():
            _check_stored_skill_name(skill_name)
            if attribute is not None and attribute not in CHARACTER_ATTRIBUTE_FIELDS:
                raise ValueError(
                    f"{skill_name!r} rides on {attribute!r}, which is not a "
                    f"core attribute {list(CHARACTER_ATTRIBUTE_FIELDS)}"
                )
        in_both = sorted(
            set(self.governing_attributes).intersection(
                self.implied_governing_attributes
            )
        )
        if in_both:
            raise ValueError(
                f"{in_both} is in both governing_attributes and "
                "implied_governing_attributes"
            )
        missing = [
            skill_name
            for skill_name in REQUIRED_SKILL_NAMES
            if skill_name not in self.governing_attributes
        ]
        if missing:
            raise ValueError(
                f"governing_attributes must name {missing}, which the code "
                "reads by name"
            )

    def names_skill(self, skill_name: str) -> bool:
        """Whether either map gives ``skill_name`` an attribute (or none).

        Args:
            skill_name: A stored skill name.
        """
        return (
            skill_name in self.governing_attributes
            or skill_name in self.implied_governing_attributes
        )


@dataclass(frozen=True)
class AttributeCheckRules(DiceRollRules):
    """The attribute check with no skill.

    Attributes:
        dice: The dice rolled.
        game_master_modifier_limit: The widest modifier a Game Master may set
            on the target, either way.
    """

    game_master_modifier_limit: int

    def __post_init__(self) -> None:
        super().__post_init__()
        require_at_least(
            "game_master_modifier_limit", self.game_master_modifier_limit, 0
        )


@dataclass(frozen=True)
class SkillRules:
    """Every number the skills and experience code reads from the canon.

    Attributes:
        skill_cost: The skills that cost a multiple of the ordinary cost.
        tracks: The prior-experience tracks.
        survival_roll: The survival roll's dice and automatic totals.
        experience: The experience ladder.
        aging: The aging table.
        attribute_modifier: The attribute modifier table.
        skill_check: The skill check.
        attribute_check: The attribute check.
    """

    skill_cost: SkillCostRules
    tracks: SkillTrackRules
    survival_roll: SurvivalRollRules
    experience: ExperienceRules
    aging: AgingRules
    attribute_modifier: AttributeModifierRules
    skill_check: SkillCheckRules
    attribute_check: AttributeCheckRules

    def __post_init__(self) -> None:
        unknown = [
            f"tracks.{key}.money_skill {track.money_skill!r}"
            for key, track in self.tracks.tracks.items()
            if track.money_skill is not None
            and not self.skill_check.names_skill(track.money_skill)
        ] + [
            f"skill_cost.multipliers {skill_name!r}"
            for skill_name in self.skill_cost.multipliers
            if not self.skill_check.names_skill(skill_name)
        ]
        if unknown:
            raise ValueError(
                f"{', '.join(unknown)} names a skill neither governing_attributes "
                "nor implied_governing_attributes gives"
            )


class _SkillRulesFileReader(RulesFileReader):
    """Reads one ``skill-rules.json`` into :class:`SkillRules`.

    The shared checks are :class:`reference.rules_file.RulesFileReader`'s;
    this adds the skill blocks. Each method takes the value and its field
    path in the file and returns the dataclass, or raises
    :class:`SkillRulesError`.
    """

    error_class = SkillRulesError

    def skill_cost(self, value: Any, field_path: str) -> SkillCostRules:
        """``skill_cost``."""
        fields = self.json_object(value, field_path, SKILL_COST_FIELDS)
        multipliers = self.mapping(
            fields["multipliers"], f"{field_path}.multipliers", self.integer
        )
        with self.wrapped_value_errors(field_path):
            return SkillCostRules(multipliers=multipliers)

    def track(self, key: str, value: Any, field_path: str) -> SkillTrack:
        """One track of ``tracks``."""
        fields = self.json_object(value, field_path, TRACK_FIELDS)
        label = self.text(fields["label"], f"{field_path}.label")
        integers = self.integers(fields, field_path, TRACK_INTEGER_FIELDS)
        money_skill = self.optional_text(
            fields["money_skill"], f"{field_path}.money_skill"
        )
        # Ordered and repeating: a d20 table gives some skills on several faces.
        skills = self.entries(fields["skills"], f"{field_path}.skills", self.text)
        with self.wrapped_value_errors(field_path):
            return SkillTrack(
                key=key, label=label, money_skill=money_skill, skills=skills, **integers
            )

    def tracks(self, value: Any, field_path: str) -> SkillTrackRules:
        """``tracks``."""
        entries = self.mapping(value, field_path, lambda entry, _entry_path: entry)
        tracks = {
            key: self.track(key, entry, f"{field_path}.{key}")
            for key, entry in entries.items()
        }
        with self.wrapped_value_errors(field_path):
            return SkillTrackRules(tracks=tracks)

    def survival_roll(self, value: Any, field_path: str) -> SurvivalRollRules:
        """``survival_roll``."""
        fields = self.json_object(value, field_path, SURVIVAL_ROLL_FIELDS)
        dice = self.text(fields["dice"], f"{field_path}.dice")
        integers = self.integers(fields, field_path, SURVIVAL_ROLL_INTEGER_FIELDS)
        with self.wrapped_value_errors(field_path):
            return SurvivalRollRules(dice=dice, **integers)

    def experience_level(self, value: Any, field_path: str) -> ExperienceLevel:
        """One rung of ``experience_levels``."""
        fields = self.json_object(value, field_path, EXPERIENCE_LEVEL_FIELDS)
        return ExperienceLevel(
            level=self.integer(fields["level"], f"{field_path}.level"),
            minimum_xp=self.integer(fields["minimum_xp"], f"{field_path}.minimum_xp"),
            xp_per_skill_point=self.optional_integer(
                fields["xp_per_skill_point"], f"{field_path}.xp_per_skill_point"
            ),
        )

    def experience(self, value: Any, field_path: str) -> ExperienceRules:
        """``experience_levels``."""
        levels = self.entries(value, field_path, self.experience_level)
        with self.wrapped_value_errors(field_path):
            return ExperienceRules(levels=levels)

    def aging_stage(self, value: Any, field_path: str) -> AgingStage:
        """One row of ``aging.stages``."""
        fields = self.json_object(value, field_path, AGING_STAGE_FIELDS)
        integers = self.integers(fields, field_path, AGING_STAGE_FIELDS)
        with self.wrapped_value_errors(field_path):
            return AgingStage(**integers)

    def aging(self, value: Any, field_path: str) -> AgingRules:
        """``aging``."""
        fields = self.json_object(value, field_path, AGING_FIELDS)
        integers = self.integers(fields, field_path, AGING_INTEGER_FIELDS)
        stages = self.entries(
            fields["stages"], f"{field_path}.stages", self.aging_stage
        )
        with self.wrapped_value_errors(field_path):
            return AgingRules(stages=stages, **integers)

    def attribute_modifier(self, value: Any, field_path: str) -> AttributeModifierRules:
        """``attribute_modifier``."""
        fields = self.json_object(value, field_path, ATTRIBUTE_MODIFIER_FIELDS)
        lowest_score = self.integer(
            fields["lowest_score"], f"{field_path}.lowest_score"
        )
        modifiers = self.integer_list(fields["modifiers"], f"{field_path}.modifiers")
        with self.wrapped_value_errors(field_path):
            return AttributeModifierRules(
                lowest_score=lowest_score, modifiers=modifiers
            )

    def skill_check(self, value: Any, field_path: str) -> SkillCheckRules:
        """``skill_check``."""
        fields = self.json_object(value, field_path, SKILL_CHECK_FIELDS)
        dice = self.text(fields["dice"], f"{field_path}.dice")
        integers = self.integers(fields, field_path, SKILL_CHECK_INTEGER_FIELDS)
        governing = self.mapping(
            fields["governing_attributes"],
            f"{field_path}.governing_attributes",
            self.optional_text,
        )
        implied = self.mapping(
            fields["implied_governing_attributes"],
            f"{field_path}.implied_governing_attributes",
            self.text,
        )
        with self.wrapped_value_errors(field_path):
            return SkillCheckRules(
                dice=dice,
                governing_attributes=governing,
                implied_governing_attributes=implied,
                **integers,
            )

    def attribute_check(self, value: Any, field_path: str) -> AttributeCheckRules:
        """``attribute_check``."""
        fields = self.json_object(value, field_path, ATTRIBUTE_CHECK_FIELDS)
        dice = self.text(fields["dice"], f"{field_path}.dice")
        limit = self.integer(
            fields["game_master_modifier_limit"],
            f"{field_path}.game_master_modifier_limit",
        )
        with self.wrapped_value_errors(field_path):
            return AttributeCheckRules(dice=dice, game_master_modifier_limit=limit)

    def skill_rules(self, value: Any) -> SkillRules:
        """The whole file."""
        fields = self.file_object(value, TOP_LEVEL_FIELDS)
        skill_cost = self.skill_cost(fields["skill_cost"], "skill_cost")
        tracks = self.tracks(fields["tracks"], "tracks")
        survival_roll = self.survival_roll(fields["survival_roll"], "survival_roll")
        experience = self.experience(fields["experience_levels"], "experience_levels")
        aging = self.aging(fields["aging"], "aging")
        attribute_modifier = self.attribute_modifier(
            fields["attribute_modifier"], "attribute_modifier"
        )
        skill_check = self.skill_check(fields["skill_check"], "skill_check")
        attribute_check = self.attribute_check(
            fields["attribute_check"], "attribute_check"
        )
        # The one check across blocks: every skill money_skill and skill_cost
        # name is one the skill check gives an attribute.
        with self.wrapped_value_errors("skill_check"):
            return SkillRules(
                skill_cost=skill_cost,
                tracks=tracks,
                survival_roll=survival_roll,
                experience=experience,
                aging=aging,
                attribute_modifier=attribute_modifier,
                skill_check=skill_check,
                attribute_check=attribute_check,
            )


def load_skill_rules(path: Path) -> SkillRules:
    """Read a canon's ``skill-rules.json`` into :class:`SkillRules`.

    The schema is in this module's docstring; the file-level checks are
    :meth:`reference.rules_file.RulesFileReader.parsed_file`'s.

    Args:
        path: The ``skill-rules.json`` file.

    Returns:
        The rules.

    Raises:
        SkillRulesError: The file is missing or unreadable, is not JSON,
            repeats a key in one object, or a field is missing, unknown, of
            the wrong type, or refused by the rules' own validation; the
            message names the file and the field.
    """
    reader = _SkillRulesFileReader(path)
    return reader.skill_rules(reader.parsed_file())


@functools.cache
def skill_rules() -> SkillRules:
    """The loaded canon's skills and experience rules, read once per process.

    The canon does not change under a running server; ``runserver`` restarts
    when a canon JSON file changes (``reference.apps``). Tests that need a
    fresh read call ``skill_rules.cache_clear()``.

    Returns:
        The rules read from :data:`reference.paths.SKILL_RULES_FILE`.

    Raises:
        SkillRulesError: The file is missing or malformed.
    """
    return load_skill_rules(SKILL_RULES_FILE)
