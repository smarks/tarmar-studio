"""The canon's character-creation rules file (issue #59).

Most cases read an invented file, so they pin the loader and the dataclasses'
validation rather than any canon's numbers. The default canon's real file and
the loaded canon's are read at the end; that the default file states what the
public character pages state is the drift tests' business
(``characters/tests/test_rules_consistency.py``), and the Tarmar canon's
values are its own ``tests/expectations.json``'s.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

from django.core.checks import Error, run_checks
from django.test import SimpleTestCase

from characters.checks import CHARACTER_RULES_CHECK_ID, check_character_rules
from characters.rules import (
    AgeRules,
    AttributeRollRules,
    AttributeRules,
    CharacterRules,
    CharacterRulesError,
    CoinRoll,
    FatigueRollRules,
    HeightRules,
    LiteracyRollRules,
    LocationRow,
    LocationTable,
    PointBuyRules,
    RollTable,
    TableRow,
    WealthRow,
    WealthTable,
    WeightRules,
    character_rules,
    load_character_rules,
    scores_by_short_name,
)
from common.test_helpers import CANON_EXPECTATIONS, skip_unless_canon_expects
from reference.paths import (
    CANON_CHARACTER_RULES_FILE_NAME,
    CANON_DIR_ENV,
    CHARACTER_RULES_FILE,
    DEFAULT_CANON_DIR,
    REPO_ROOT,
)
from reference.rules_file import RulesFileError

#: A complete file with invented numbers.
INVENTED_RULES: dict[str, Any] = {
    "attributes": {
        "maximum": 25,
        "point_buy": {"total": 50, "minimum": 4, "maximum": 15},
    },
    "age": {"starting": 14, "years_per_stage": 5},
    "attribute_roll": {"dice": "4d4"},
    "fatigue_roll": {"dice": "1d8+1", "attributes": ["CON"], "best_of": []},
    "literacy_roll": {"dice": "2d10"},
    "height": {
        "base_inches_by_face": [50, 54, 58, 62, 66, 70],
        "inches_per_extra_one": -3,
        "inches_per_extra_six": 3,
        "minimum_inches": 30,
    },
    "weight": {
        "stones_by_face": [7, 8, 9, 10, 11, 12],
        "pounds_per_stone": 10,
        "strength_divisor": 3,
    },
    "social_class": [
        {"up_to": 20, "label": "nobility"},
        {"up_to": 50, "label": "merchant"},
        {"up_to": 100, "label": "commoner"},
    ],
    "nobility": [{"up_to": 40, "label": "Lord"}, {"up_to": 100, "label": "Squire"}],
    "merchant": [{"up_to": 100, "label": "Trader"}],
    "commoner": [{"up_to": 60, "label": "Farmer"}, {"up_to": 100, "label": "Smith"}],
    "location": {
        "skill_points": 2,
        "rows": [
            {
                "up_to": 50,
                "label": "Port",
                "skills": ["Sailing", "Swimming"],
                "literacy_modifier": 1,
            },
            {
                "up_to": 100,
                "label": "Ask the GM",
                "skills": [],
                "literacy_modifier": None,
            },
        ],
    },
    "wealth": [
        {"up_to": 30, "label": "Poor", "coin": 5},
        {"up_to": 90, "label": "Comfortable", "coin": {"base": 50, "dice": "2d6"}},
        {"up_to": 100, "label": "Wealthy", "coin": None},
    ],
    "rulings": [{"key": "invented", "reading": "Invented for the test."}],
}

INVENTED_CHARACTER_RULES = CharacterRules(
    attributes=AttributeRules(
        maximum=25, point_buy=PointBuyRules(total=50, minimum=4, maximum=15)
    ),
    age=AgeRules(starting=14, years_per_stage=5),
    attribute_roll=AttributeRollRules(dice="4d4"),
    fatigue_roll=FatigueRollRules(dice="1d8+1", attributes=("CON",), best_of=()),
    literacy_roll=LiteracyRollRules(dice="2d10"),
    height=HeightRules(
        base_inches_by_face=(50, 54, 58, 62, 66, 70),
        inches_per_extra_one=-3,
        inches_per_extra_six=3,
        minimum_inches=30,
    ),
    weight=WeightRules(
        stones_by_face=(7, 8, 9, 10, 11, 12), pounds_per_stone=10, strength_divisor=3
    ),
    social_class=RollTable(
        rows=(
            TableRow(20, "nobility"),
            TableRow(50, "merchant"),
            TableRow(100, "commoner"),
        )
    ),
    nobility=RollTable(rows=(TableRow(40, "Lord"), TableRow(100, "Squire"))),
    merchant=RollTable(rows=(TableRow(100, "Trader"),)),
    commoner=RollTable(rows=(TableRow(60, "Farmer"), TableRow(100, "Smith"))),
    location=LocationTable(
        rows=(
            LocationRow(50, "Port", ("Sailing", "Swimming"), 1),
            LocationRow(100, "Ask the GM", (), None),
        ),
        skill_points=2,
    ),
    wealth=WealthTable(
        rows=(
            WealthRow(30, "Poor", 5),
            WealthRow(90, "Comfortable", CoinRoll(base=50, dice="2d6")),
            WealthRow(100, "Wealthy", None),
        )
    ),
)


def invented_rules() -> dict[str, Any]:
    """A deep copy of :data:`INVENTED_RULES` a test may change."""
    return json.loads(json.dumps(INVENTED_RULES))


def rules_summary(rules: CharacterRules) -> dict[str, Any]:
    """The rules in the shape a canon's ``character_rules`` expectation takes.

    Args:
        rules: Loaded rules.

    Returns:
        Every number and name in the rules, as JSON would hold them.
    """

    def rows(table: RollTable | LocationTable | WealthTable) -> list[list[Any]]:
        return [[row.up_to, row.label] for row in table.rows]

    def coin(value: int | CoinRoll | None) -> Any:
        if isinstance(value, CoinRoll):
            return [value.base, value.dice]
        return value

    point_buy = rules.attributes.point_buy
    return {
        "attribute_maximum": rules.attributes.maximum,
        "point_buy": [point_buy.total, point_buy.minimum, point_buy.maximum],
        "age": [rules.age.starting, rules.age.years_per_stage],
        "attribute_dice": rules.attribute_roll.dice,
        "fatigue_roll": [
            rules.fatigue_roll.dice,
            list(rules.fatigue_roll.attributes),
            list(rules.fatigue_roll.best_of),
        ],
        "literacy_dice": rules.literacy_roll.dice,
        "height": [
            list(rules.height.base_inches_by_face),
            rules.height.inches_per_extra_one,
            rules.height.inches_per_extra_six,
            rules.height.minimum_inches,
        ],
        "weight": [
            list(rules.weight.stones_by_face),
            rules.weight.pounds_per_stone,
            rules.weight.strength_divisor,
        ],
        "tables": {
            "social_class": rows(rules.social_class),
            "nobility": rows(rules.nobility),
            "merchant": rows(rules.merchant),
            "commoner": rows(rules.commoner),
            "location": rows(rules.location),
            "wealth": rows(rules.wealth),
        },
        "location_skill_points": rules.location.skill_points,
        "location_effects": {
            row.label: [list(row.skills), row.literacy_modifier]
            for row in rules.location.rows
        },
        "wealth_coin": {row.label: coin(row.coin) for row in rules.wealth.rows},
    }


class CharacterRulesFileTest(SimpleTestCase):
    """The loader reads the file strictly, naming the file and the field."""

    def setUp(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.rules_file = directory / CANON_CHARACTER_RULES_FILE_NAME

    def write_text(self, text: str) -> Path:
        """Write ``text`` as the file and return its path."""
        self.rules_file.write_text(text, encoding="utf-8")
        return self.rules_file

    def load(self, rules: dict[str, Any]) -> CharacterRules:
        """Load ``rules`` written as the file."""
        return load_character_rules(self.write_text(json.dumps(rules)))

    def assert_refused(self, rules: dict[str, Any], *expected_texts: str) -> None:
        """Loading ``rules`` raises :class:`CharacterRulesError` naming the
        file and each of ``expected_texts``."""
        with self.assertRaises(CharacterRulesError) as caught:
            self.load(rules)
        message = str(caught.exception)
        self.assertIn(str(self.rules_file), message)
        for expected_text in expected_texts:
            self.assertIn(expected_text, message)

    def assert_changed_refused(
        self, change: Callable[[dict[str, Any]], Any], *expected_texts: str
    ) -> None:
        """The invented file with ``change`` applied is refused, naming each of
        ``expected_texts``."""
        rules = invented_rules()
        change(rules)
        self.assert_refused(rules, *expected_texts)

    def test_a_full_file_loads_into_the_dataclasses(self) -> None:
        self.assertEqual(self.load(INVENTED_RULES), INVENTED_CHARACTER_RULES)

    def test_rulings_are_optional(self) -> None:
        rules = invented_rules()
        del rules["rulings"]
        self.assertEqual(self.load(rules), INVENTED_CHARACTER_RULES)

    def test_the_error_is_a_rules_file_error(self) -> None:
        self.assertTrue(issubclass(CharacterRulesError, RulesFileError))

    def test_a_missing_file_is_refused(self) -> None:
        with self.assertRaises(CharacterRulesError) as caught:
            load_character_rules(self.rules_file)
        self.assertIn(str(self.rules_file), str(caught.exception))
        self.assertIn("every canon must carry one", str(caught.exception))

    def test_a_key_repeated_in_one_object_is_refused(self) -> None:
        # json.loads alone would keep the second "age" and drop the first.
        text = '{"age": {}, ' + json.dumps(INVENTED_RULES)[1:]
        with self.assertRaises(CharacterRulesError) as caught:
            load_character_rules(self.write_text(text))
        self.assertIn("is not valid JSON", str(caught.exception))
        self.assertIn("['age']", str(caught.exception))

    def test_a_missing_key_is_refused(self) -> None:
        self.assert_changed_refused(
            lambda rules: rules.pop("attribute_roll"), "attribute_roll is missing"
        )
        self.assert_changed_refused(
            lambda rules: rules["height"].pop("minimum_inches"),
            "height.minimum_inches is missing",
        )
        self.assert_changed_refused(
            lambda rules: rules.pop("wealth"), "wealth is missing"
        )

    def test_an_unknown_key_is_refused(self) -> None:
        self.assert_changed_refused(
            lambda rules: rules["age"].update(starting_age=16),
            "age.starting_age",
            "not a field",
        )
        self.assert_changed_refused(
            lambda rules: rules["wealth"][0].update(coins=5),
            "wealth[0].coins",
            "not a field",
        )

    def test_a_boolean_is_not_an_integer(self) -> None:
        self.assert_changed_refused(
            lambda rules: rules["attributes"]["point_buy"].update(total=True),
            "attributes.point_buy.total",
            "an integer",
        )
        self.assert_changed_refused(
            lambda rules: rules["location"]["rows"][0].update(literacy_modifier=False),
            "location.rows[0].literacy_modifier",
            "an integer or null",
        )
        self.assert_changed_refused(
            lambda rules: rules["wealth"][0].update(coin=True),
            "wealth[0].coin",
            "must be an integer",
        )

    def test_a_skill_named_twice_is_refused(self) -> None:
        self.assert_changed_refused(
            lambda rules: rules["location"]["rows"][0].update(skills=["Sail", "Sail"]),
            "location.rows[0].skills",
            "repeats the entry ['Sail']",
        )

    def test_a_malformed_ruling_is_refused(self) -> None:
        self.assert_changed_refused(
            lambda rules: rules.update(rulings=["An unkeyed reading."]),
            "rulings[0]",
            "must be an object",
        )

    def test_each_refusal_of_the_rules_names_its_block(self) -> None:
        cases: tuple[tuple[Callable[[dict[str, Any]], Any], str, str], ...] = (
            (
                lambda rules: rules["social_class"][1].update(up_to=20),
                "social_class:",
                "rows must rise in up_to",
            ),
            (
                lambda rules: rules["nobility"][0].update(up_to=0),
                "nobility:",
                "first row's up_to must be at least 1",
            ),
            (
                lambda rules: rules["merchant"][0].update(up_to=99),
                "merchant:",
                "last row's up_to must be 100",
            ),
            (
                lambda rules: rules["commoner"][0].update(label="Smith"),
                "commoner:",
                "repeats the label ['Smith']",
            ),
            (lambda rules: rules.update(merchant=[]), "merchant:", "has no rows"),
            (
                lambda rules: rules["location"]["rows"][0].update(up_to=100),
                "location:",
                "rows must rise in up_to",
            ),
            (
                lambda rules: rules["wealth"][2].update(up_to=99),
                "wealth:",
                "last row's up_to must be 100",
            ),
            (
                lambda rules: rules["height"]["base_inches_by_face"].pop(),
                "height:",
                "one value per face of a d6 (6), got 5",
            ),
            (
                lambda rules: rules["weight"]["stones_by_face"].append(13),
                "weight:",
                "one value per face of a d6 (6), got 7",
            ),
            (
                lambda rules: rules["weight"].update(
                    stones_by_face=[0, 8, 9, 10, 11, 12]
                ),
                "weight:",
                "each of stones_by_face must be at least 1, got 0",
            ),
            (
                lambda rules: rules["weight"].update(strength_divisor=0),
                "weight:",
                "strength_divisor must be at least 1, got 0",
            ),
            (
                lambda rules: rules["height"].update(minimum_inches=0),
                "height:",
                "minimum_inches must be at least 1",
            ),
            (
                lambda rules: rules["fatigue_roll"].update(attributes=["CON", "LUCK"]),
                "fatigue_roll:",
                "names ['LUCK'], which are not core attributes",
            ),
            (
                lambda rules: rules["fatigue_roll"].update(best_of=["con"]),
                "fatigue_roll:",
                "names ['con']",
            ),
            (
                lambda rules: rules["fatigue_roll"].update(best_of=["CON", "DEX"]),
                "fatigue_roll:",
                "['CON'] is in both attributes and best_of",
            ),
            (
                lambda rules: rules["fatigue_roll"].update(attributes=[]),
                "fatigue_roll:",
                "attributes names no attribute",
            ),
            (
                lambda rules: rules["fatigue_roll"].update(dice="2x6"),
                "fatigue_roll:",
                "Invalid dice expression: 2x6",
            ),
            (
                lambda rules: rules["literacy_roll"].update(dice="3d6 + 1"),
                "literacy_roll:",
                "Invalid dice expression",
            ),
            (
                lambda rules: rules["attribute_roll"].update(dice="3d"),
                "attribute_roll:",
                "Invalid dice expression: 3d",
            ),
            (
                lambda rules: rules["attribute_roll"].update(dice="3D6"),
                "attribute_roll:",
                "dice '3D6' must be written '3d6'",
            ),
            (
                lambda rules: rules["attribute_roll"].update(sides=6),
                "attribute_roll.sides",
                "not a field",
            ),
            (
                lambda rules: rules["fatigue_roll"].update(dice=" 1D8+1 "),
                "fatigue_roll:",
                "dice ' 1D8+1 ' must be written '1d8+1'",
            ),
            (
                lambda rules: rules["literacy_roll"].update(dice="02d010"),
                "literacy_roll:",
                "dice '02d010' must be written '2d10'",
            ),
            (
                lambda rules: rules["wealth"][1]["coin"].update(dice="2d6+0"),
                "wealth[1].coin:",
                "dice '2d6+0' must be written '2d6'",
            ),
            (
                lambda rules: rules["wealth"][1]["coin"].update(dice="d"),
                "wealth[1].coin:",
                "Invalid dice expression",
            ),
            (
                lambda rules: rules["wealth"][1]["coin"].update(base=-1),
                "wealth[1].coin:",
                "base must be at least 0",
            ),
            (
                lambda rules: rules["wealth"][0].update(coin=-5),
                "wealth[0]:",
                "coin must be at least 0, got -5",
            ),
            (
                lambda rules: rules["attributes"]["point_buy"].update(minimum=0),
                "attributes.point_buy:",
                "minimum must be at least 1",
            ),
            (
                lambda rules: rules["attributes"]["point_buy"].update(maximum=3),
                "attributes.point_buy:",
                "maximum 3 is below minimum 4",
            ),
            (
                lambda rules: rules["attributes"]["point_buy"].update(total=100),
                "attributes.point_buy:",
                "total 100 cannot be spent on 6 attributes of 4 to 15 (24 to 90)",
            ),
            (
                lambda rules: rules["attributes"]["point_buy"].update(total=23),
                "attributes.point_buy:",
                "total 23 cannot be spent",
            ),
            (
                lambda rules: rules["attributes"].update(maximum=14),
                "attributes:",
                "point_buy.maximum 15 is above the attribute maximum 14",
            ),
            (
                lambda rules: rules["age"].update(years_per_stage=0),
                "age:",
                "years_per_stage must be at least 1",
            ),
            (
                lambda rules: rules["location"].update(skill_points=-1),
                "location:",
                "skill_points must be at least 0, got -1",
            ),
            (
                lambda rules: rules["social_class"][0].update(label="gentry"),
                "social_class:",
                "must be the detail tables",
            ),
        )
        for change, field_path, problem in cases:
            with self.subTest(field_path=field_path, problem=problem):
                self.assert_changed_refused(change, field_path, problem)


class CharacterRulesValuesTest(SimpleTestCase):
    """What the loaded rules derive and look up."""

    def test_a_roll_s_range_comes_from_its_dice(self) -> None:
        fatigue_roll = INVENTED_CHARACTER_RULES.fatigue_roll
        self.assertEqual((fatigue_roll.minimum, fatigue_roll.maximum), (2, 9))
        literacy_roll = INVENTED_CHARACTER_RULES.literacy_roll
        self.assertEqual((literacy_roll.minimum, literacy_roll.maximum), (2, 20))

    def test_a_roll_lands_on_the_first_row_it_does_not_pass(self) -> None:
        social_class = INVENTED_CHARACTER_RULES.social_class
        for roll, label in (
            (1, "nobility"),
            (20, "nobility"),
            (21, "merchant"),
            (50, "merchant"),
            (51, "commoner"),
            (100, "commoner"),
        ):
            with self.subTest(roll=roll):
                self.assertEqual(social_class.lookup(roll), label)
        self.assertEqual(
            INVENTED_CHARACTER_RULES.wealth.row_for(60).coin,
            CoinRoll(base=50, dice="2d6"),
        )

    def test_a_roll_past_every_row_takes_the_last_row(self) -> None:
        self.assertEqual(INVENTED_CHARACTER_RULES.commoner.lookup(101), "Smith")

    def test_an_origin_grants_its_first_skill(self) -> None:
        port, ask_the_gm = INVENTED_CHARACTER_RULES.location.rows
        self.assertEqual(port.granted_skill, "Sailing")
        self.assertIsNone(ask_the_gm.granted_skill)


class CharacterRulesHelpersTest(SimpleTestCase):
    """The procedures the rules carry, on invented values."""

    def test_a_roll_refuses_only_what_its_dice_cannot_give(self) -> None:
        fatigue_roll = INVENTED_CHARACTER_RULES.fatigue_roll  # 1d8+1: 2 to 9
        self.assertIsNone(fatigue_roll.refusal("The Fatigue roll", 2))
        self.assertIsNone(fatigue_roll.refusal("The Fatigue roll", 9))
        self.assertEqual(
            fatigue_roll.refusal("The Fatigue roll", 1),
            "The Fatigue roll is 1d8+1, so it must be between 2 and 9; 1 was given",
        )
        self.assertEqual(
            fatigue_roll.refusal("The Fatigue roll", 10),
            "The Fatigue roll is 1d8+1, so it must be between 2 and 9; 10 was given",
        )

    def test_the_fatigue_sum_adds_the_attributes_the_best_of_and_the_roll(
        self,
    ) -> None:
        scores = {"STR": 9, "DEX": 14, "INT": 11, "WIS": 12, "CON": 13, "CHR": 8}
        only_con = FatigueRollRules(dice="2d6", attributes=("CON",), best_of=())
        self.assertEqual(only_con.total(scores, 7), 13 + 7)
        public = FatigueRollRules(
            dice="2d6", attributes=("CON", "WIS", "INT"), best_of=("DEX", "STR")
        )
        self.assertEqual(public.total(scores, 7), 13 + 12 + 11 + 14 + 7)
        self.assertEqual(public.total({**scores, "STR": 16}, 7), 13 + 12 + 11 + 16 + 7)

    def test_a_row_is_found_by_its_label(self) -> None:
        location = INVENTED_CHARACTER_RULES.location
        port = location.row_labelled("Port")
        assert port is not None
        self.assertEqual(port.skills, ("Sailing", "Swimming"))
        self.assertIsNone(location.row_labelled("Village"))

    def test_a_social_class_names_its_detail_table(self) -> None:
        self.assertIs(
            INVENTED_CHARACTER_RULES.detail_table("merchant"),
            INVENTED_CHARACTER_RULES.merchant,
        )
        with self.assertRaises(KeyError):
            INVENTED_CHARACTER_RULES.detail_table("gentry")

    def test_the_literacy_check_reads_as_a_penalty(self) -> None:
        literacy_roll = INVENTED_CHARACTER_RULES.literacy_roll
        self.assertEqual(literacy_roll.check_text(0), "2d10 ≤ INT")
        self.assertEqual(literacy_roll.check_text(3), "2d10 ≤ INT − 3")

    def test_coin_dice_are_written_as_the_log_and_the_rules_say_them(self) -> None:
        percentile = CoinRoll(base=100, dice="1d100")
        self.assertTrue(percentile.is_percentile)
        self.assertEqual(
            (percentile.dice_label, percentile.dice_in_words),
            ("d100", "percentile dice"),
        )
        other = CoinRoll(base=50, dice="2d6")
        self.assertFalse(other.is_percentile)
        self.assertEqual((other.dice_label, other.dice_in_words), ("2d6", "2d6"))

    def test_scores_are_keyed_by_the_files_short_names(self) -> None:
        scores = {
            "strength": 1,
            "dexterity": 2,
            "intelligence": 3,
            "wisdom": 4,
            "constitution": 5,
            "charisma": 6,
            "movement": 9,
        }
        self.assertEqual(
            scores_by_short_name(scores),
            {"STR": 1, "DEX": 2, "INT": 3, "WIS": 4, "CON": 5, "CHR": 6},
        )


class CanonCharacterRulesTest(SimpleTestCase):
    """The canons' own files load."""

    def tearDown(self) -> None:
        character_rules.cache_clear()

    def test_the_default_canon_file_loads(self) -> None:
        rules = load_character_rules(
            DEFAULT_CANON_DIR / CANON_CHARACTER_RULES_FILE_NAME
        )
        # The public pages' rolls: Fatigue's 2d6 and the literacy roll's 3d6.
        self.assertEqual(
            (rules.fatigue_roll.minimum, rules.fatigue_roll.maximum), (2, 12)
        )
        self.assertEqual(
            (rules.literacy_roll.minimum, rules.literacy_roll.maximum), (3, 18)
        )

    def test_the_loaded_canon_file_loads_once_per_process(self) -> None:
        character_rules.cache_clear()
        loaded = character_rules()
        self.assertEqual(loaded, load_character_rules(CHARACTER_RULES_FILE))
        self.assertIs(character_rules(), loaded)


class CharacterRulesCheckTest(SimpleTestCase):
    """``manage.py check`` refuses a canon whose file the loader refuses."""

    def test_the_loaded_canon_passes(self) -> None:
        self.assertEqual(check_character_rules(), [])

    def test_a_refused_file_is_a_registered_error(self) -> None:
        refusal = CharacterRulesError("character-rules.json: age is missing")
        with patch("characters.checks.character_rules", side_effect=refusal):
            messages = [
                message
                for message in run_checks()
                if message.id == CHARACTER_RULES_CHECK_ID
            ]
        self.assertEqual(len(messages), 1)
        self.assertIsInstance(messages[0], Error)
        self.assertEqual(messages[0].msg, str(refusal))


class TarmarCharacterRulesTest(SimpleTestCase):
    """The Tarmar canon's file holds the numbers its expectations pin.

    The values are the canon's: its ``tests/expectations.json`` carries them
    under ``character_rules``, and none appears here.
    """

    @skip_unless_canon_expects("character_rules")
    def test_the_file_matches_the_canon_expectations(self) -> None:
        self.assertEqual(
            rules_summary(load_character_rules(CHARACTER_RULES_FILE)),
            CANON_EXPECTATIONS["character_rules"],
        )


class CanonReadOnlyWhenAskedTest(SimpleTestCase):
    """No import reads the canon's character rules (issue #59).

    A module-level read would load the file while Django imports the URL
    configuration, so a canon without the file would crash ``manage.py
    check`` with a traceback instead of reporting ``characters.E001``. Run
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
            "from characters.rules import character_rules\n"
            "print('misses', character_rules.cache_info().misses)\n"
        )
        self.assertIn("misses 0", self.run_python("-c", script))

    def test_a_canon_without_the_file_is_a_check_error_not_a_crash(self) -> None:
        canon_dir = Path(self.enterContext(tempfile.TemporaryDirectory())) / "canon"
        shutil.copytree(DEFAULT_CANON_DIR, canon_dir)
        (canon_dir / CANON_CHARACTER_RULES_FILE_NAME).unlink()
        output = self.run_python("manage.py", "check", canon_dir=canon_dir)
        self.assertIn(CHARACTER_RULES_CHECK_ID, output)
        self.assertIn("is missing; every canon must carry one", output)
        self.assertNotIn("Traceback", output)
