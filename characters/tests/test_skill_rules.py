"""The canon's skills and experience rules file (issue #70).

Most cases read an invented file, so they pin the loader and the dataclasses'
validation rather than any canon's numbers. The default canon's real file and
the loaded canon's are read at the end; that the default file states what the
public pages state is the drift tests' business, and the Tarmar canon's values
are its own ``tests/expectations.json``'s.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from django.core.checks import Error, run_checks
from django.test import SimpleTestCase

from characters import skill_checks
from characters.checks import SKILL_RULES_CHECK_ID, check_skill_rules
from characters.skill_rules import (
    AGING_ATTRIBUTES,
    MENTAL_AGING_ATTRIBUTES,
    PHYSICAL_AGING_ATTRIBUTES,
    REQUIRED_SKILL_NAMES,
    AgingRules,
    AgingStage,
    AttributeCheckRules,
    AttributeModifierRules,
    ExperienceLevel,
    ExperienceRules,
    SkillCheckRules,
    SkillCostRules,
    SkillRules,
    SkillRulesError,
    SkillTrack,
    SkillTrackRules,
    SurvivalRollRules,
    load_skill_rules,
    skill_rules,
)
from characters.tests.rules_fixtures import DEFAULT_SKILL_RULES, seated_skill_rules
from common.test_helpers import CANON_EXPECTATIONS, skip_unless_canon_expects
from reference.paths import (
    CANON_DIR_ENV,
    CANON_SKILL_RULES_FILE_NAME,
    DEFAULT_CANON_DIR,
    REPO_ROOT,
    SKILL_RULES_FILE,
)
from reference.rules_file import RulesFileError

#: An invented d20 table: one entry per face, some repeated.
SAILOR_SKILLS = [f"Rope {face % 7}" for face in range(1, 21)]
SMITH_SKILLS = ["Tongs"] * 10 + ["Bellows"] * 10

#: The skills the code reads by name, each on an invented attribute.
INVENTED_REQUIRED_SKILLS: dict[str, str | None] = {
    "weapon": "strength",
    "maker skill": "wisdom",
    "knowledge": "charisma",
    "literacy": None,
}
assert sorted(INVENTED_REQUIRED_SKILLS) == sorted(REQUIRED_SKILL_NAMES)

#: A complete file with invented numbers.
INVENTED_RULES: dict[str, Any] = {
    "skill_cost": {"multipliers": {"sorcery": 3}},
    "tracks": {
        "sailor": {
            "label": "Sailor",
            "xp_per_year": 300,
            "survival_target": 1,
            "money_multiplier": 10,
            "money_skill": None,
            "skills": SAILOR_SKILLS,
        },
        "smith": {
            "label": "Smith",
            "xp_per_year": 400,
            "survival_target": 25,
            "money_multiplier": 30,
            "money_skill": "tongs",
            "skills": SMITH_SKILLS,
        },
    },
    "survival_roll": {
        "dice": "2d10",
        "automatic_death_total": 2,
        "automatic_survival_total": 20,
    },
    "experience_levels": [
        {"level": 1, "minimum_xp": 0, "xp_per_skill_point": None},
        {"level": 2, "minimum_xp": 100, "xp_per_skill_point": 50},
        {"level": 3, "minimum_xp": 300, "xp_per_skill_point": 70},
    ],
    "aging": {
        "first_stage_with_penalties": 3,
        "stages_per_mental_step": 3,
        "stages": [
            {
                "stage": 2,
                "strength": 0,
                "dexterity": 0,
                "intelligence": 0,
                "wisdom": 0,
                "constitution": 0,
            },
            {
                "stage": 3,
                "strength": -1,
                "dexterity": 0,
                "intelligence": 0,
                "wisdom": 0,
                "constitution": 0,
            },
            {
                "stage": 4,
                "strength": -2,
                "dexterity": -1,
                "intelligence": -1,
                "wisdom": 0,
                "constitution": -3,
            },
        ],
    },
    "attribute_modifier": {"lowest_score": 2, "modifiers": [-2, -1, 0, 1, 2]},
    "skill_check": {
        "dice": "3d4",
        "natural_success": 12,
        "natural_failure": 3,
        "focused_field_bonus": 2,
        "governing_attributes": {
            "rope": "dexterity",
            "fate": None,
            "tongs": "strength",
            **INVENTED_REQUIRED_SKILLS,
        },
        "implied_governing_attributes": {"sorcery": "wisdom"},
    },
    "attribute_check": {"dice": "2d8", "game_master_modifier_limit": 4},
    "rulings": [{"key": "invented", "reading": "Invented for the test."}],
}

INVENTED_SKILL_RULES = SkillRules(
    skill_cost=SkillCostRules(multipliers={"sorcery": 3}),
    tracks=SkillTrackRules(
        tracks={
            "sailor": SkillTrack(
                key="sailor",
                label="Sailor",
                xp_per_year=300,
                survival_target=1,
                money_multiplier=10,
                money_skill=None,
                skills=tuple(SAILOR_SKILLS),
            ),
            "smith": SkillTrack(
                key="smith",
                label="Smith",
                xp_per_year=400,
                survival_target=25,
                money_multiplier=30,
                money_skill="tongs",
                skills=tuple(SMITH_SKILLS),
            ),
        }
    ),
    survival_roll=SurvivalRollRules(
        dice="2d10", automatic_death_total=2, automatic_survival_total=20
    ),
    experience=ExperienceRules(
        levels=(
            ExperienceLevel(level=1, minimum_xp=0, xp_per_skill_point=None),
            ExperienceLevel(level=2, minimum_xp=100, xp_per_skill_point=50),
            ExperienceLevel(level=3, minimum_xp=300, xp_per_skill_point=70),
        )
    ),
    aging=AgingRules(
        first_stage_with_penalties=3,
        stages_per_mental_step=3,
        stages=(
            AgingStage(
                stage=2,
                strength=0,
                dexterity=0,
                constitution=0,
                intelligence=0,
                wisdom=0,
            ),
            AgingStage(
                stage=3,
                strength=-1,
                dexterity=0,
                constitution=0,
                intelligence=0,
                wisdom=0,
            ),
            AgingStage(
                stage=4,
                strength=-2,
                dexterity=-1,
                constitution=-3,
                intelligence=-1,
                wisdom=0,
            ),
        ),
    ),
    attribute_modifier=AttributeModifierRules(
        lowest_score=2, modifiers=(-2, -1, 0, 1, 2)
    ),
    skill_check=SkillCheckRules(
        dice="3d4",
        natural_success=12,
        natural_failure=3,
        focused_field_bonus=2,
        governing_attributes={
            "rope": "dexterity",
            "fate": None,
            "tongs": "strength",
            **INVENTED_REQUIRED_SKILLS,
        },
        implied_governing_attributes={"sorcery": "wisdom"},
    ),
    attribute_check=AttributeCheckRules(dice="2d8", game_master_modifier_limit=4),
)


def invented_rules() -> dict[str, Any]:
    """A deep copy of :data:`INVENTED_RULES` a test may change."""
    return json.loads(json.dumps(INVENTED_RULES))


def skill_rules_summary(rules: SkillRules) -> dict[str, Any]:
    """The rules in the shape a canon's ``skill_rules`` expectation takes.

    Args:
        rules: Loaded rules.

    Returns:
        The numbers the expectation pins, as JSON would hold them.
    """
    last_stage = rules.aging.stages[-1]
    return {
        "tracks": {
            key: [track.xp_per_year, track.survival_target]
            for key, track in rules.tracks.tracks.items()
        },
        "experience_levels": [
            [rung.level, rung.minimum_xp, rung.xp_per_skill_point]
            for rung in rules.experience.levels
        ],
        "skill_cost_multipliers": dict(rules.skill_cost.multipliers),
        "survival_totals": [
            rules.survival_roll.automatic_death_total,
            rules.survival_roll.automatic_survival_total,
        ],
        "naturals": [
            rules.skill_check.natural_failure,
            rules.skill_check.natural_success,
        ],
        "attribute_check_modifier_limit": (
            rules.attribute_check.game_master_modifier_limit
        ),
        "aging_first_stage_with_penalties": rules.aging.first_stage_with_penalties,
        "aging_last_stage": [last_stage.stage, last_stage.penalties],
        "governing_pairs": len(rules.skill_check.governing_attributes),
        "implied_governing_pairs": len(rules.skill_check.implied_governing_attributes),
    }


class SkillRulesFileTest(SimpleTestCase):
    """The loader reads the file strictly, naming the file and the field."""

    def setUp(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.rules_file = directory / CANON_SKILL_RULES_FILE_NAME

    def write_text(self, text: str) -> Path:
        """Write ``text`` as the file and return its path."""
        self.rules_file.write_text(text, encoding="utf-8")
        return self.rules_file

    def load(self, rules: dict[str, Any]) -> SkillRules:
        """Load ``rules`` written as the file."""
        return load_skill_rules(self.write_text(json.dumps(rules)))

    def assert_changed_refused(
        self, change: Callable[[dict[str, Any]], Any], *expected_texts: str
    ) -> None:
        """The invented file with ``change`` applied is refused, naming the
        file and each of ``expected_texts``."""
        rules = invented_rules()
        change(rules)
        with self.assertRaises(SkillRulesError) as caught:
            self.load(rules)
        message = str(caught.exception)
        self.assertIn(str(self.rules_file), message)
        for expected_text in expected_texts:
            self.assertIn(expected_text, message)

    def test_a_full_file_loads_into_the_dataclasses(self) -> None:
        self.assertEqual(self.load(INVENTED_RULES), INVENTED_SKILL_RULES)

    def test_rulings_are_optional(self) -> None:
        rules = invented_rules()
        del rules["rulings"]
        self.assertEqual(self.load(rules), INVENTED_SKILL_RULES)

    def test_the_error_is_a_rules_file_error(self) -> None:
        self.assertTrue(issubclass(SkillRulesError, RulesFileError))

    def test_a_missing_file_is_refused(self) -> None:
        with self.assertRaises(SkillRulesError) as caught:
            load_skill_rules(self.rules_file)
        self.assertIn(str(self.rules_file), str(caught.exception))
        self.assertIn("every canon must carry one", str(caught.exception))

    def test_a_key_repeated_in_one_object_is_refused(self) -> None:
        # json.loads alone would keep the second "aging" and drop the first.
        text = '{"aging": {}, ' + json.dumps(INVENTED_RULES)[1:]
        with self.assertRaises(SkillRulesError) as caught:
            load_skill_rules(self.write_text(text))
        self.assertIn("is not valid JSON", str(caught.exception))
        self.assertIn("['aging']", str(caught.exception))

    def test_a_missing_key_is_refused(self) -> None:
        cases: tuple[tuple[Callable[[dict[str, Any]], Any], str], ...] = (
            (lambda rules: rules.pop("experience_levels"), "experience_levels"),
            (
                lambda rules: rules["tracks"]["smith"].pop("money_skill"),
                "tracks.smith.money_skill",
            ),
            (
                lambda rules: rules["aging"]["stages"][1].pop("wisdom"),
                "aging.stages[1].wisdom",
            ),
            (
                lambda rules: rules["skill_check"].pop("implied_governing_attributes"),
                "skill_check.implied_governing_attributes",
            ),
        )
        for change, field_path in cases:
            with self.subTest(field_path=field_path):
                self.assert_changed_refused(change, f"{field_path} is missing")

    def test_an_unknown_key_is_refused(self) -> None:
        cases: tuple[tuple[Callable[[dict[str, Any]], Any], str], ...] = (
            (lambda rules: rules.update(skill_tracks={}), "skill_tracks"),
            (
                lambda rules: rules["tracks"]["sailor"].update(survivability=4),
                "tracks.sailor.survivability",
            ),
            (
                lambda rules: rules["experience_levels"][0].update(xp=0),
                "experience_levels[0].xp",
            ),
            (
                lambda rules: rules["aging"]["stages"][0].update(charisma=0),
                "aging.stages[0].charisma",
            ),
            (
                lambda rules: rules["attribute_check"].update(sides=6),
                "attribute_check.sides",
            ),
        )
        for change, field_path in cases:
            with self.subTest(field_path=field_path):
                self.assert_changed_refused(
                    change, field_path, "is not a field this file takes"
                )

    def test_a_wrong_type_is_refused(self) -> None:
        cases: tuple[tuple[Callable[[dict[str, Any]], Any], str, str], ...] = (
            (
                lambda rules: rules["tracks"]["sailor"].update(xp_per_year=True),
                "tracks.sailor.xp_per_year",
                "must be an integer",
            ),
            (
                lambda rules: rules["experience_levels"][1].update(
                    xp_per_skill_point=False
                ),
                "experience_levels[1].xp_per_skill_point",
                "an integer or null",
            ),
            (
                lambda rules: rules["skill_cost"]["multipliers"].update(sorcery="3"),
                "skill_cost.multipliers.sorcery",
                "must be an integer",
            ),
            (
                lambda rules: rules["tracks"]["smith"].update(money_skill=""),
                "tracks.smith.money_skill",
                "a non-empty string or null",
            ),
            (
                lambda rules: rules["tracks"]["smith"]["skills"].__setitem__(0, 7),
                "tracks.smith.skills[0]",
                "a non-empty string",
            ),
            (
                lambda rules: rules["skill_check"]["governing_attributes"].update(
                    rope=1
                ),
                "skill_check.governing_attributes.rope",
                "a non-empty string or null",
            ),
            (
                lambda rules: rules["skill_check"][
                    "implied_governing_attributes"
                ].update(sorcery=None),
                "skill_check.implied_governing_attributes.sorcery",
                "a non-empty string",
            ),
            (
                lambda rules: rules.update(tracks=[]),
                "tracks",
                "must be an object",
            ),
            (
                lambda rules: rules["tracks"].update({"": {}}),
                "tracks",
                "has an empty key",
            ),
            (
                lambda rules: rules["attribute_modifier"].update(modifiers=0),
                "attribute_modifier.modifiers",
                "must be a list",
            ),
        )
        for change, field_path, problem in cases:
            with self.subTest(field_path=field_path, problem=problem):
                self.assert_changed_refused(change, field_path, problem)

    def test_a_malformed_ruling_is_refused(self) -> None:
        self.assert_changed_refused(
            lambda rules: rules.update(rulings=["An unkeyed reading."]),
            "rulings[0]",
            "must be an object",
        )

    def test_each_refusal_of_the_rules_names_its_block(self) -> None:
        cases: tuple[tuple[Callable[[dict[str, Any]], Any], str, str], ...] = (
            (
                lambda rules: rules["skill_cost"]["multipliers"].update(sorcery=0),
                "skill_cost:",
                "sorcery's multiplier must be at least 1, got 0",
            ),
            (
                lambda rules: rules["skill_cost"].update(multipliers={"Sorcery": 3}),
                "skill_cost:",
                "skill 'Sorcery' must be written as stored, 'sorcery'",
            ),
            (
                lambda rules: rules["tracks"]["sailor"]["skills"].pop(),
                "tracks.sailor:",
                "one entry per face of a d20 (20), got 19",
            ),
            (
                lambda rules: rules["tracks"]["sailor"]["skills"].append("Rope"),
                "tracks.sailor:",
                "one entry per face of a d20 (20), got 21",
            ),
            (
                lambda rules: rules["tracks"]["sailor"].update(xp_per_year=0),
                "tracks.sailor:",
                "xp_per_year must be at least 1, got 0",
            ),
            (
                lambda rules: rules["tracks"]["smith"].update(money_multiplier=0),
                "tracks.smith:",
                "money_multiplier must be at least 1, got 0",
            ),
            (
                lambda rules: rules["tracks"]["smith"].update(money_skill="Tongs"),
                "tracks.smith:",
                "skill 'Tongs' must be written as stored, 'tongs'",
            ),
            (
                lambda rules: rules["tracks"]["smith"].update(label="Sailor"),
                "tracks:",
                "repeats the label ['Sailor']",
            ),
            (
                lambda rules: rules.update(tracks={}),
                "tracks:",
                "names no track",
            ),
            (
                lambda rules: rules["survival_roll"].update(dice="2D10"),
                "survival_roll:",
                "dice '2D10' must be written '2d10'",
            ),
            (
                lambda rules: rules["survival_roll"].update(automatic_death_total=1),
                "survival_roll:",
                "automatic_death_total is 2d10, so it must be between 2 and 20; "
                "1 was given",
            ),
            (
                lambda rules: rules["survival_roll"].update(
                    automatic_survival_total=21
                ),
                "survival_roll:",
                "automatic_survival_total is 2d10, so it must be between 2 and "
                "20; 21 was given",
            ),
            (
                lambda rules: rules["survival_roll"].update(automatic_death_total=20),
                "survival_roll:",
                "automatic_death_total 20 must be below automatic_survival_total 20",
            ),
            (
                lambda rules: rules.update(experience_levels=[]),
                "experience_levels:",
                "has no levels",
            ),
            (
                lambda rules: rules["experience_levels"][2].update(level=4),
                "experience_levels:",
                "level 3 is given as 4",
            ),
            (
                lambda rules: rules["experience_levels"][0].update(minimum_xp=5),
                "experience_levels:",
                "level 1's minimum_xp must be 0, got 5",
            ),
            (
                lambda rules: rules["experience_levels"][0].update(
                    xp_per_skill_point=10
                ),
                "experience_levels:",
                "level 1 has no climb from a level below it",
            ),
            (
                lambda rules: rules["experience_levels"][2].update(minimum_xp=100),
                "experience_levels:",
                "minimum_xp must rise: level 2 needs 100 and level 3 100",
            ),
            (
                lambda rules: rules["experience_levels"][1].update(
                    xp_per_skill_point=None
                ),
                "experience_levels:",
                "level 2's xp_per_skill_point is null, but the climb to it from "
                "level 1 earns skill points",
            ),
            (
                lambda rules: rules["experience_levels"][2].update(
                    xp_per_skill_point=0
                ),
                "experience_levels:",
                "level 3's xp_per_skill_point must be at least 1, got 0",
            ),
            (
                lambda rules: rules["aging"]["stages"][1].update(wisdom=1),
                "aging.stages[1]:",
                "wisdom must be 0 or less, got 1",
            ),
            (
                lambda rules: rules["aging"]["stages"][2].update(stage=5),
                "aging:",
                "stages must be consecutive: stage 3 is followed by 5",
            ),
            (
                lambda rules: rules["aging"].update(stages=[]),
                "aging:",
                "has no stages",
            ),
            (
                lambda rules: rules["aging"].update(stages_per_mental_step=0),
                "aging:",
                "stages_per_mental_step must be at least 1, got 0",
            ),
            (
                lambda rules: rules["aging"].update(first_stage_with_penalties=1),
                "aging:",
                "first_stage_with_penalties 1 must be a tabulated stage (2 to 4) "
                "or the one after",
            ),
            (
                lambda rules: rules["aging"].update(first_stage_with_penalties=6),
                "aging:",
                "first_stage_with_penalties 6 must be a tabulated stage",
            ),
            (
                lambda rules: rules["aging"].update(first_stage_with_penalties=4),
                "aging:",
                "stage 3 carries a penalty before first_stage_with_penalties 4",
            ),
            (
                lambda rules: rules["attribute_modifier"].update(modifiers=[]),
                "attribute_modifier:",
                "modifiers has no rows",
            ),
            (
                lambda rules: rules["skill_check"].update(dice="3d"),
                "skill_check:",
                "Invalid dice expression: 3d",
            ),
            (
                lambda rules: rules["skill_check"].update(natural_success=13),
                "skill_check:",
                "natural_success is 3d4, so it must be between 3 and 12; 13 was given",
            ),
            (
                lambda rules: rules["skill_check"].update(natural_failure=2),
                "skill_check:",
                "natural_failure is 3d4, so it must be between 3 and 12; 2 was given",
            ),
            (
                lambda rules: rules["skill_check"].update(natural_failure=12),
                "skill_check:",
                "natural_failure 12 must be below natural_success 12",
            ),
            (
                lambda rules: rules["skill_check"].update(focused_field_bonus=-1),
                "skill_check:",
                "focused_field_bonus must be at least 0, got -1",
            ),
            (
                lambda rules: rules["skill_check"]["governing_attributes"].update(
                    rope="DEX"
                ),
                "skill_check:",
                "'rope' rides on 'DEX', which is not a core attribute",
            ),
            (
                lambda rules: rules["skill_check"][
                    "implied_governing_attributes"
                ].update(sorcery="luck"),
                "skill_check:",
                "'sorcery' rides on 'luck', which is not a core attribute",
            ),
            (
                lambda rules: rules["skill_check"]["governing_attributes"].update(
                    {"Rope II": "dexterity"}
                ),
                "skill_check:",
                "skill 'Rope II' must be written as stored, 'rope'",
            ),
            (
                lambda rules: rules["skill_check"]["governing_attributes"].update(
                    sorcery="intelligence"
                ),
                "skill_check:",
                "['sorcery'] is in both governing_attributes and "
                "implied_governing_attributes",
            ),
            (
                lambda rules: rules["tracks"]["smith"].update(money_skill="anvil"),
                "skill_check:",
                "tracks.smith.money_skill 'anvil' names a skill neither "
                "governing_attributes nor implied_governing_attributes gives",
            ),
            (
                lambda rules: rules["skill_cost"]["multipliers"].update(bellows=2),
                "skill_check:",
                "skill_cost.multipliers 'bellows' names a skill neither",
            ),
            (
                lambda rules: rules["attribute_check"].update(dice="2d8+0"),
                "attribute_check:",
                "dice '2d8+0' must be written '2d8'",
            ),
            (
                lambda rules: rules["attribute_check"].update(
                    game_master_modifier_limit=-1
                ),
                "attribute_check:",
                "game_master_modifier_limit must be at least 0, got -1",
            ),
        )
        for change, field_path, problem in cases:
            with self.subTest(field_path=field_path, problem=problem):
                self.assert_changed_refused(change, field_path, problem)

    def test_each_skill_the_code_reads_by_name_must_be_governed(self) -> None:
        for skill_name in REQUIRED_SKILL_NAMES:
            with self.subTest(skill_name=skill_name):
                self.assert_changed_refused(
                    lambda rules, skill_name=skill_name: rules["skill_check"][
                        "governing_attributes"
                    ].pop(skill_name),
                    "skill_check:",
                    f"governing_attributes must name ['{skill_name}'], which the "
                    "code reads by name",
                )

    def test_a_money_skill_or_multiplier_may_be_an_implied_skill(self) -> None:
        rules = invented_rules()
        rules["tracks"]["smith"]["money_skill"] = "sorcery"
        self.assertEqual(self.load(rules).tracks.tracks["smith"].money_skill, "sorcery")

    def test_a_survival_target_has_no_range_of_its_own(self) -> None:
        # The check adds the best modifier, which may be negative, so a target
        # below what the dice total (the pages' Laborer 2+ on 3d6) is a rule.
        rules = invented_rules()
        rules["tracks"]["sailor"]["survival_target"] = -4
        self.assertEqual(self.load(rules).tracks.tracks["sailor"].survival_target, -4)


class SkillRulesDataclassTest(SimpleTestCase):
    """Checks a dataclass makes that a file cannot reach through the loader."""

    def test_a_track_is_filed_under_its_own_key(self) -> None:
        sailor = INVENTED_SKILL_RULES.tracks.tracks["sailor"]
        with self.assertRaisesMessage(
            ValueError, "track 'deckhand' carries the key 'sailor'"
        ):
            SkillTrackRules(tracks={"deckhand": sailor})

    def test_aging_s_physical_and_mental_attributes_are_every_aged_one(
        self,
    ) -> None:
        self.assertEqual(
            sorted((*PHYSICAL_AGING_ATTRIBUTES, *MENTAL_AGING_ATTRIBUTES)),
            sorted(AGING_ATTRIBUTES),
        )


class SkillRulesValuesTest(SimpleTestCase):
    """What the loaded rules derive and look up, on invented values."""

    def test_a_roll_s_range_comes_from_its_dice(self) -> None:
        rules = INVENTED_SKILL_RULES
        self.assertEqual(
            (rules.survival_roll.minimum, rules.survival_roll.maximum), (2, 20)
        )
        self.assertEqual(
            (rules.skill_check.minimum, rules.skill_check.maximum), (3, 12)
        )
        self.assertEqual(
            (rules.attribute_check.minimum, rules.attribute_check.maximum), (2, 16)
        )

    def test_an_unlisted_skill_costs_the_ordinary_amount(self) -> None:
        skill_cost = INVENTED_SKILL_RULES.skill_cost
        self.assertEqual(skill_cost.multiplier_for("sorcery"), 3)
        self.assertEqual(skill_cost.multiplier_for("Sorcery II"), 3)
        self.assertEqual(skill_cost.multiplier_for("sorcery lore"), 1)
        self.assertEqual(skill_cost.multiplier_for("rope"), 1)

    def test_xp_reaches_the_highest_level_whose_minimum_it_passes(self) -> None:
        experience = INVENTED_SKILL_RULES.experience
        self.assertEqual(experience.top_level, 3)
        for total_xp, level in ((-10, 1), (0, 1), (99, 1), (100, 2), (299, 2)):
            with self.subTest(total_xp=total_xp):
                self.assertEqual(experience.level_for(total_xp), level)
        self.assertEqual(experience.level_for(300), 3)
        self.assertEqual(experience.level_for(10**9), 3)

    def test_aging_is_none_before_the_first_stage_and_the_table_within_it(
        self,
    ) -> None:
        aging = INVENTED_SKILL_RULES.aging
        self.assertIsNone(aging.penalties_for(-1))
        self.assertIsNone(aging.penalties_for(2))
        self.assertEqual(
            aging.penalties_for(3),
            {
                "strength": -1,
                "dexterity": 0,
                "intelligence": 0,
                "wisdom": 0,
                "constitution": 0,
            },
        )

    def test_past_the_table_each_attribute_falls_from_its_own_last_value(
        self,
    ) -> None:
        aging = INVENTED_SKILL_RULES.aging  # Last row: STR -2 DEX -1 INT -1 WIS 0
        # CON -3; INT and WIS lose one every third stage past it.
        expected = {
            5: {
                "strength": -3,
                "dexterity": -2,
                "intelligence": -1,
                "wisdom": 0,
                "constitution": -4,
            },
            7: {
                "strength": -5,
                "dexterity": -4,
                "intelligence": -2,
                "wisdom": -1,
                "constitution": -6,
            },
            10: {
                "strength": -8,
                "dexterity": -7,
                "intelligence": -3,
                "wisdom": -2,
                "constitution": -9,
            },
        }
        for stage, penalties in expected.items():
            with self.subTest(stage=stage):
                self.assertEqual(aging.penalties_for(stage), penalties)

    def test_a_first_stage_one_past_the_table_ages_only_past_it(self) -> None:
        aging = AgingRules(
            first_stage_with_penalties=3,
            stages_per_mental_step=1,
            stages=tuple(
                AgingStage(
                    stage=stage,
                    strength=0,
                    dexterity=0,
                    constitution=0,
                    intelligence=0,
                    wisdom=0,
                )
                for stage in (1, 2)
            ),
        )
        self.assertIsNone(aging.penalties_for(2))
        self.assertEqual(set((aging.penalties_for(3) or {}).values()), {-1})

    def test_a_score_off_the_table_takes_the_nearest_row(self) -> None:
        modifier = INVENTED_SKILL_RULES.attribute_modifier  # 2..6: -2..+2
        self.assertEqual(modifier.highest_score, 6)
        for score, expected in ((-3, -2), (2, -2), (4, 0), (6, 2), (30, 2)):
            with self.subTest(score=score):
                self.assertEqual(modifier.modifier_for(score), expected)


class CanonSkillRulesTest(SimpleTestCase):
    """The canons' own files load."""

    def tearDown(self) -> None:
        skill_rules.cache_clear()

    def test_the_default_canon_file_loads(self) -> None:
        rules = load_skill_rules(DEFAULT_CANON_DIR / CANON_SKILL_RULES_FILE_NAME)
        self.assertEqual(rules, DEFAULT_SKILL_RULES)
        # The public pages' dice: 3d6 survival and attribute checks, 2d6 skill
        # checks, and a d20 table for every track.
        self.assertEqual(rules.survival_roll.dice, "3d6")
        self.assertEqual(rules.skill_check.dice, "2d6")
        self.assertEqual(rules.attribute_check.dice, "3d6")
        self.assertEqual(rules.tracks.tracks["craft"].money_skill, "maker skill")

    def test_the_loaded_canon_file_loads_once_per_process(self) -> None:
        skill_rules.cache_clear()
        loaded = skill_rules()
        self.assertEqual(loaded, load_skill_rules(SKILL_RULES_FILE))
        self.assertIs(skill_rules(), loaded)

    def test_seated_rules_answer_every_caller_then_go(self) -> None:
        with seated_skill_rules(INVENTED_SKILL_RULES):
            self.assertIs(skill_rules(), INVENTED_SKILL_RULES)
        self.assertEqual(skill_rules(), load_skill_rules(SKILL_RULES_FILE))


class DefaultSkillRulesMatchTheCodeTablesTest(SimpleTestCase):
    """The default canon's checks state what ``characters.skill_checks`` does.

    Slice A of issue #70 proved the whole default file equal to the Python
    tables. Slice B seated the tracks, the ladder, the skill cost, the
    survival roll, the aging table and the modifier table and deleted their
    tables; the drift tests in ``characters/tests/test_rules_consistency.py``
    and ``test_dice_fidelity.py`` now hold those parts of the default file to
    the pages. The checks are seated next, and this holds them until then.
    """

    rules = DEFAULT_SKILL_RULES

    def test_the_checks_are_the_code_s(self) -> None:
        skill_check = self.rules.skill_check
        self.assertEqual(
            (skill_check.minimum, skill_check.maximum),
            (skill_checks.SKILL_CHECK_MINIMUM, skill_checks.SKILL_CHECK_MAXIMUM),
        )
        self.assertEqual(skill_check.natural_success, skill_checks.NATURAL_SUCCESS)
        self.assertEqual(skill_check.natural_failure, skill_checks.NATURAL_FAILURE)
        self.assertEqual(
            skill_check.focused_field_bonus, skill_checks.FOCUSED_FIELD_BONUS
        )
        self.assertEqual(
            list(skill_check.governing_attributes.items()),
            list(skill_checks.GOVERNING_ATTRIBUTES.items()),
        )
        self.assertEqual(
            dict(skill_check.implied_governing_attributes),
            skill_checks.IMPLIED_GOVERNING_ATTRIBUTES,
        )
        attribute_check = self.rules.attribute_check
        self.assertEqual(
            (attribute_check.minimum, attribute_check.maximum),
            (
                skill_checks.ATTRIBUTE_CHECK_MINIMUM,
                skill_checks.ATTRIBUTE_CHECK_MAXIMUM,
            ),
        )
        self.assertEqual(
            attribute_check.game_master_modifier_limit,
            skill_checks.MAX_ATTRIBUTE_CHECK_MODIFIER,
        )


class SkillRulesCheckTest(SimpleTestCase):
    """``manage.py check`` refuses a canon whose file the loader refuses."""

    def test_the_loaded_canon_passes(self) -> None:
        self.assertEqual(check_skill_rules(), [])

    def test_a_refused_file_is_a_registered_error(self) -> None:
        refusal = SkillRulesError("skill-rules.json: aging is missing")
        with patch("characters.checks.skill_rules", side_effect=refusal):
            messages = [
                message
                for message in run_checks()
                if message.id == SKILL_RULES_CHECK_ID
            ]
        self.assertEqual(len(messages), 1)
        self.assertIsInstance(messages[0], Error)
        self.assertEqual(messages[0].msg, str(refusal))
        self.assertIn("characters/skill_rules.py", messages[0].hint or "")

    def checked_with(self, **skill_check_changes: Any) -> list[str]:
        """The E002 messages for the invented rules with the checks changed."""
        rules = replace(
            INVENTED_SKILL_RULES,
            skill_check=replace(
                INVENTED_SKILL_RULES.skill_check, **skill_check_changes
            ),
        )
        with patch("characters.checks.skill_rules", return_value=rules):
            return [
                message.msg
                for message in check_skill_rules()
                if message.id == SKILL_RULES_CHECK_ID
            ]

    def test_a_weapons_name_is_refused(self) -> None:
        # "hammer" is a weapon the catalog sells: a stored "hammer" is the
        # Weapon skill, so a lookup by it would never find this entry.
        governing = {**INVENTED_SKILL_RULES.skill_check.governing_attributes}
        (message,) = self.checked_with(
            governing_attributes={**governing, "hammer": "strength"}
        )
        self.assertIn(str(SKILL_RULES_FILE), message)
        self.assertIn(
            "skill_check.governing_attributes.hammer falls under 'weapon'", message
        )
        self.assertIn("each must be a glossary skill in its own right", message)

    def test_a_chosen_part_is_refused(self) -> None:
        (message,) = self.checked_with(
            implied_governing_attributes={
                "sorcery": "wisdom",
                "maker skill (smith)": "intelligence",
            }
        )
        self.assertIn(
            "skill_check.implied_governing_attributes.maker skill (smith) falls "
            "under 'maker skill'",
            message,
        )

    def test_the_invented_names_pass(self) -> None:
        self.assertEqual(self.checked_with(), [])


class TarmarSkillRulesTest(SimpleTestCase):
    """The Tarmar canon's file holds the numbers its expectations pin.

    The values are the canon's: its ``tests/expectations.json`` carries them
    under ``skill_rules``, and none appears here.
    """

    @skip_unless_canon_expects("skill_rules")
    def test_the_file_matches_the_canon_expectations(self) -> None:
        self.assertEqual(
            skill_rules_summary(load_skill_rules(SKILL_RULES_FILE)),
            CANON_EXPECTATIONS["skill_rules"],
        )


class SkillRulesReadOnlyWhenAskedTest(SimpleTestCase):
    """No import reads the canon's skill rules (issue #70).

    A module-level read would load the file while Django imports the URL
    configuration, so a canon without the file would crash ``manage.py
    check`` with a traceback instead of reporting ``characters.E002``. Run
    out of process: this test runner has every module imported already.
    """

    def run_python(self, *arguments: str, canon_dir: Path | None = None) -> str:
        """Run ``python *arguments`` from the repository root; its output."""
        environment = {**os.environ, "DEBUG": "true"}
        if canon_dir is not None:
            environment[CANON_DIR_ENV] = str(canon_dir)
        completed = subprocess.run(
            [sys.executable, *arguments],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.stdout + completed.stderr

    def test_importing_the_url_configuration_reads_no_rules(self) -> None:
        script = (
            "import os, django\n"
            "os.environ.setdefault('DJANGO_SETTINGS_MODULE', "
            "'tarmar_studio.settings')\n"
            "django.setup()\n"
            "import tarmar_studio.urls, characters.urls\n"
            "from characters.skill_rules import skill_rules\n"
            "print('misses', skill_rules.cache_info().misses)\n"
        )
        self.assertIn("misses 0", self.run_python("-c", script))

    def test_a_canon_without_the_file_is_a_check_error_not_a_crash(self) -> None:
        canon_dir = Path(self.enterContext(tempfile.TemporaryDirectory())) / "canon"
        shutil.copytree(DEFAULT_CANON_DIR, canon_dir)
        (canon_dir / CANON_SKILL_RULES_FILE_NAME).unlink()
        output = self.run_python("manage.py", "check", canon_dir=canon_dir)
        self.assertIn(SKILL_RULES_CHECK_ID, output)
        self.assertIn("is missing; every canon must carry one", output)
        self.assertNotIn("Traceback", output)
