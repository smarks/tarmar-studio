"""Every reader of the equipment rules follows the loaded canon (issue #75).

The equipment rules are the loaded canon's ``equipment-rules.json``, read
through :func:`characters.equipment_rules.equipment_rules`. Each case seats
invented rules whose level keys, gait names, distances, penalties, codes,
Missile classes and dodge bounds the default canon does not have, so a reader
still holding one of the deleted constants, a name, or a value it built at
import would answer with the default canon's and fail. The gaits are four, as
the battle engine needs (``battle.E003``).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from django.test import SimpleTestCase

from battle.adaptation import beast_gaits, beast_movement_modifier
from battle.checks import (
    ENGINE_GAITS_CHECK_ID,
    GENERATED_LOAD_CHECK_ID,
    check_engine_gaits,
    check_generated_load,
)
from battle.generation import _lighter_weapons
from battle.services import _catalog_weapon, snapshot_character
from character_export.export_utils import format_pounds, generate_character_markdown
from characters import attack
from characters.equipment import combine_movement_restrictions
from characters.equipment_rules import (
    DefenderDodgeRules,
    EncumbranceLevel,
    EncumbranceRules,
    EquipmentRules,
    GaitRules,
)
from characters.models import Character
from characters.skills import points_for_level
from characters.tests.rules_fixtures import (
    DEFAULT_EQUIPMENT_RULES,
    seated_equipment_rules,
)
from characters.tests.test_attack_service import ScriptedRoller
from characters.views.characters import character_number_limits
from characters.weapon_skill import resolve_weapon_skill
from common.test_helpers import BaseTestCase, create_character
from reference.paths import (
    CANON_DIR_ENV,
    CANON_EQUIPMENT_RULES_FILE_NAME,
    DEFAULT_CANON_DIR,
    REPO_ROOT,
)

#: Invented rules: four renamed gaits and four renamed levels, none of whose
#: names or numbers are the default canon's.
INVENTED_RULES = EquipmentRules(
    encumbrance=EncumbranceRules(
        levels=(
            EncumbranceLevel(
                key="unburdened",
                max_multiplier=2.0,
                penalty=0,
                forbidden_gaits=(),
                walk_limit=None,
            ),
            EncumbranceLevel(
                key="laden",
                max_multiplier=3.0,
                penalty=-3,
                forbidden_gaits=("dash",),
                walk_limit=None,
            ),
            EncumbranceLevel(
                key="straining",
                max_multiplier=4.5,
                penalty=-1,
                forbidden_gaits=("lope", "dash"),
                walk_limit=None,
            ),
            EncumbranceLevel(
                key="crushed",
                max_multiplier=None,
                penalty=-2,
                forbidden_gaits=("trot", "lope", "dash"),
                walk_limit=2,
            ),
        )
    ),
    armour_restriction_codes={"stiff": ("lope", "dash")},
    gaits=GaitRules(
        order=("dash", "lope", "trot", "stroll"),
        base_distances={"dash": 20, "lope": 11, "trot": 6, "stroll": 3},
    ),
    missile_classes=frozenset({"Thrusting"}),
    defender_dodge=DefenderDodgeRules(minimum=3, maximum=7),
)

#: Average attributes: a movement modifier of 0, so a gait's distance is its
#: base plus the level's penalty.
AVERAGE_BODY = {"strength": 10, "constitution": 10, "dexterity": 10}


def carrying(pounds: float, *armour: dict) -> list[dict]:
    """A load of ``pounds`` carried, beside any armour worn."""
    load = {
        "item_id": "custom",
        "name": "Load",
        "category": "gear",
        "quantity": 1,
        "weight": pounds,
        "in_pack": False,
    }
    return [load, *armour]


#: A custom suit whose code the invented rules give: custom armour carries
#: its own fields.
BOARD_COAT = {
    "item_id": "custom",
    "name": "Board Coat",
    "category": "armor",
    "equipped": True,
    "movement_restriction": "stiff",
    "weight": 0,
}


class SeatedRulesTest(SimpleTestCase):
    """Seats :data:`INVENTED_RULES` for every test of the class."""

    def setUp(self) -> None:
        self.enterContext(seated_equipment_rules(INVENTED_RULES))

    def test_nothing_invented_is_the_default_canon_s(self) -> None:
        default = DEFAULT_EQUIPMENT_RULES
        self.assertFalse(
            set(INVENTED_RULES.encumbrance.keys) & set(default.encumbrance.keys)
        )
        self.assertFalse(set(INVENTED_RULES.gaits.order) & set(default.gaits.order))
        self.assertFalse(
            set(INVENTED_RULES.armour_restriction_codes)
            & set(default.armour_restriction_codes)
        )
        self.assertFalse(INVENTED_RULES.missile_classes & default.missile_classes)
        self.assertNotEqual(INVENTED_RULES.defender_dodge, default.defender_dodge)


class TheSheetFollowsTheSeatedRulesTest(SeatedRulesTest):
    def test_a_level_s_penalty_forbids_its_gaits_by_name(self) -> None:
        # STR 10: unburdened to 20 lb, laden to 30.
        character = Character(**AVERAGE_BODY, equipment=carrying(25))
        self.assertEqual(character.encumbrance_level, "laden")
        self.assertEqual(character.encumbrance_label, "Laden")
        self.assertEqual(character.encumbrance_badge_classes, "load-2")
        self.assertEqual(character.movement_penalty, -3)
        self.assertEqual(character.movement_restrictions, ["dash"])
        self.assertEqual(character.movement_restriction_label, "Cannot Dash")
        self.assertEqual(
            character.gait_distances,
            {"dash": None, "lope": 8, "trot": 3, "stroll": 0},
        )
        self.assertEqual(
            [(gait["key"], gait["label"]) for gait in character.gait_readout],
            [
                ("dash", "Dash"),
                ("lope", "Lope"),
                ("trot", "Trot"),
                ("stroll", "Stroll"),
            ],
        )

    def test_the_heaviest_level_caps_the_slowest_gait(self) -> None:
        character = Character(**AVERAGE_BODY, equipment=carrying(100))
        self.assertEqual(character.encumbrance_level, "crushed")
        self.assertEqual(character.encumbrance_badge_classes, "load-4 load-heaviest")
        self.assertEqual(
            character.movement_restriction_label, "Cannot Trot, Lope, or Dash"
        )
        self.assertEqual(
            character.gait_distances,
            {"dash": None, "lope": None, "trot": None, "stroll": 2},
        )

    def test_an_armour_code_forbids_the_seated_gaits(self) -> None:
        character = Character(**AVERAGE_BODY, equipment=carrying(0, BOARD_COAT))
        self.assertEqual(character.encumbrance_level, "unburdened")
        self.assertEqual(character.encumbrance_badge_classes, "load-1")
        self.assertEqual(character.movement_restrictions, ["lope", "dash"])
        self.assertEqual(
            character.movement_restriction_label, "Cannot Lope or Dash (Board Coat)"
        )
        self.assertEqual(
            combine_movement_restrictions(["dash"], ["trot", "dash"]),
            ["trot", "dash"],
        )

    def test_the_carrying_capacity_is_the_lightest_seated_level_s(self) -> None:
        # STR 10 at the lightest level's multiplier of 2.0, not STR itself.
        self.assertEqual(Character(**AVERAGE_BODY).lightest_load_limit, "20")

    def test_the_thresholds_are_the_seated_levels(self) -> None:
        character = Character(**AVERAGE_BODY)
        self.assertEqual(
            character.encumbrance_thresholds,
            {"unburdened": 20.0, "laden": 30.0, "straining": 45.0},
        )
        self.assertEqual(format_pounds(45.0), "45")
        self.assertEqual(format_pounds(12.5), "12.5")

    def test_the_dodge_input_is_held_to_the_seated_bounds(self) -> None:
        limits = character_number_limits()
        self.assertEqual(
            (limits["defender_dodge_minimum"], limits["defender_dodge_maximum"]),
            (3, 7),
        )

    def test_archery_covers_the_seated_missile_classes(self) -> None:
        skills = {"archery": points_for_level(3)}
        self.assertEqual(
            resolve_weapon_skill(skills, "javelin").rule,
            "full: Archery, a Missile weapon",
        )
        self.assertEqual(resolve_weapon_skill(skills, "longbow").bonus, 0)

    def test_the_battle_seats_the_seated_missile_classes_as_missiles(self) -> None:
        # A Thrusting javelin is the seated rules' missile, and a longbow is
        # not. A missile is never stepped down: the one-handed spear, which
        # under the default canon steps down to the trident and the javelin,
        # keeps its place.
        self.assertTrue(_catalog_weapon("javelin").is_missile)
        self.assertFalse(_catalog_weapon("longbow").is_missile)
        self.assertEqual(_lighter_weapons("spear_1h", 18), [])

    def test_a_beast_s_gaits_take_the_seated_proportions_by_place(self) -> None:
        # Stroll and trot to lope, 3 and 6 to 11: a full move of 22 walks 6
        # and jogs 12; lope is the run, so a move of 15 is lope's 11 plus 4.
        self.assertEqual(beast_gaits(22), (6, 12, 22))
        self.assertEqual(beast_movement_modifier(15), 4)

    def test_the_engine_needs_exactly_four_gaits(self) -> None:
        self.assertEqual(check_engine_gaits(), [])
        three_gaits = replace(
            INVENTED_RULES,
            encumbrance=EncumbranceRules(
                levels=(
                    replace(INVENTED_RULES.encumbrance.levels[0]),
                    replace(
                        INVENTED_RULES.encumbrance.levels[3],
                        forbidden_gaits=("lope", "dash"),
                    ),
                )
            ),
            gaits=GaitRules(
                order=("dash", "lope", "stroll"),
                base_distances={"dash": 20, "lope": 11, "stroll": 3},
            ),
        )
        with seated_equipment_rules(three_gaits):
            (message,) = check_engine_gaits()
        self.assertEqual(message.id, ENGINE_GAITS_CHECK_ID)
        self.assertIn("gaits.order names 3 gaits", message.msg)
        self.assertIn("stage 6", message.hint or "")

    def test_the_gait_seated_as_the_run_must_go_some_distance(self) -> None:
        standing_lope = replace(
            INVENTED_RULES,
            gaits=GaitRules(
                order=INVENTED_RULES.gaits.order,
                base_distances={"dash": 20, "lope": 0, "trot": 6, "stroll": 3},
            ),
        )
        with seated_equipment_rules(standing_lope):
            (message,) = check_engine_gaits()
        self.assertEqual(message.id, ENGINE_GAITS_CHECK_ID)
        self.assertIn("gaits.base_distances.lope is 0", message.msg)

    def test_the_generator_s_heaviest_load_must_be_a_seated_level(self) -> None:
        (message,) = check_generated_load()
        self.assertEqual(message.id, GENERATED_LOAD_CHECK_ID)
        self.assertIn("'medium'", message.msg)
        self.assertIn("'unburdened'", message.msg)


class TheSeatAndTheExportFollowTheSeatedRulesTest(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_equipment_rules(INVENTED_RULES))
        self.character = create_character(
            self.create_user("seated-rules-mover"),
            **AVERAGE_BODY,
            equipment=carrying(25),
            skills={"weapon (saber)": 6},
        )

    def test_the_battle_seats_the_canon_s_gaits_by_place(self) -> None:
        seated = snapshot_character(
            self.character, 1, display_name="Seated", archetype="", spells=(), mana=0
        )
        # Laden: dash forbidden; lope 8, trot 3, stroll 0 (floored at 1 hex).
        self.assertEqual(
            (seated.move_sprint, seated.move_run, seated.move_jog, seated.move_walk),
            (0, 8, 3, 1),
        )

    def test_the_sheet_prints_the_lightest_level_s_limit(self) -> None:
        self.login_as(self.character.user)
        page = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        self.assertIn('<span id="max-unencumbered">20</span>', page.content.decode())

    def test_the_export_names_the_canon_s_levels(self) -> None:
        markdown = generate_character_markdown(self.character)
        self.assertIn("- **Encumbrance Level:** Laden", markdown)
        self.assertIn("| Unburdened | 20 lbs |", markdown)
        self.assertIn("| Straining | 45 lbs |", markdown)
        self.assertNotIn("Crushed |", markdown)
        self.assertIn("Dash n/a | Lope 8 | Trot 3 | Stroll 0", markdown)

    def test_an_attack_is_refused_outside_the_seated_dodge_bounds(self) -> None:
        self.character.equipment = [{"item_id": "saber", "quantity": 1}]
        self.character.save()
        accepted = attack.resolve(
            self.character,
            {"weapon_index": 0, "armour_tier": "None", "defender_dodge": 7},
            roller=ScriptedRoller(d20s=[10]),
        )
        self.assertEqual(accepted["target_number"]["defender_dodge"], 7)
        # No dodge in the request is the canon's least, not 0.
        unstated = attack.resolve(
            self.character,
            {"weapon_index": 0, "armour_tier": "None"},
            roller=ScriptedRoller(d20s=[10]),
        )
        self.assertEqual(unstated["target_number"]["defender_dodge"], 3)
        # 2 and 8 lie inside the default canon's 0 to 10.
        for dodge in (2, 8):
            with self.subTest(dodge=dodge):
                with self.assertRaises(attack.AttackValidationError):
                    attack.resolve(
                        self.character,
                        {
                            "weapon_index": 0,
                            "armour_tier": "None",
                            "defender_dodge": dodge,
                        },
                        roller=ScriptedRoller(),
                    )


class ThreeGaitCanonCheckTest(SimpleTestCase):
    """A canon of three gaits fails ``manage.py check`` once, under
    ``battle.E003``, and crashes nothing (issue #75).

    Run out of process, as the characters checks' are, so the check sees the
    canon a fresh server would load.
    """

    def test_three_gaits_are_one_check_error_not_a_crash(self) -> None:
        canon_dir = Path(self.enterContext(tempfile.TemporaryDirectory())) / "canon"
        shutil.copytree(DEFAULT_CANON_DIR, canon_dir)
        rules_file = canon_dir / CANON_EQUIPMENT_RULES_FILE_NAME
        stored = json.loads(rules_file.read_text(encoding="utf-8"))
        # Sprint goes: from the order, its distance, and whatever forbade it.
        stored["gaits"]["order"].remove("sprint")
        del stored["gaits"]["base_distances"]["sprint"]
        for level in stored["encumbrance"]["levels"]:
            level["forbidden_gaits"] = [
                gait for gait in level["forbidden_gaits"] if gait != "sprint"
            ]
        stored["armour_restriction_codes"] = {
            code: ["run"] for code in stored["armour_restriction_codes"]
        }
        rules_file.write_text(json.dumps(stored), encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, "manage.py", "check"],
            cwd=REPO_ROOT,
            env={**os.environ, "DEBUG": "true", CANON_DIR_ENV: str(canon_dir)},
            capture_output=True,
            text=True,
            check=False,
        )
        output = completed.stdout + completed.stderr
        self.assertEqual(output.count(ENGINE_GAITS_CHECK_ID), 1, output)
        self.assertIn("gaits.order names 3 gaits", output)
        self.assertNotIn("Traceback", output)
