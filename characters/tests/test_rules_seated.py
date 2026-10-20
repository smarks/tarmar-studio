"""Character creation reads the loaded canon's rules, not numbers of its own.

Issue #59 moved every creation number into the canon's
``character-rules.json``. Under the default canon the code behaves exactly as
before, which the rest of the suite holds; these tests seat invented rules
(``characters/tests/test_rules.py``'s) and check that each caller follows
them, so a number left behind in Python would fail here rather than agree
with the default canon by coincidence.
"""

import json
import math
from dataclasses import replace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from bestiary.translation import derive_pools, fixed_fatigue_roll
from characters import services
from characters.constants import point_buy_budget_note
from characters.dice import (
    DICE_IN_TEXT,
    calculate_final_weight_lbs,
    demon_die_to_height_inches,
    literacy_check_text,
    location_effects,
    roll_all_character_stats,
    roll_attribute_logged,
    roll_fatigue_logged,
    roll_literacy_logged,
    roll_location_logged,
    roll_provenance_logged,
    roll_wealth_logged,
    starting_coin_for_wealth,
    wealth_gives_coin,
)
from characters.models import (
    Character,
    character_int_field_maximum,
    character_int_field_minimum,
    starting_age,
    validate_attribute_maximum,
    validate_fatigue_roll,
    validate_literacy_roll,
    validate_starting_age,
)
from characters.rules import LocationRow, LocationTable
from characters.services import (
    CharacterChangeError,
    apply_fatigue_roll,
    apply_origin_roll,
    apply_origin_skill,
    apply_rolled_attributes,
    apply_sheet_attribute_roll,
    apply_skill_rename,
    check_attribute,
    check_point_buy,
)
from characters.skills import (
    _effective_attributes_at_age,
    age_for_aging_stage,
    aging_stage_for_age,
    simulate_experience_years,
)
from characters.templatetags.character_fields import editable_attribute, editable_number
from characters.views.characters import (
    _character_create_context,
    _parse_point_buy_attributes,
    _point_buy_defaults,
)
from common.rolling import Roller
from common.test_helpers import BaseTestCase, create_character
from gm.character_schema import character_json_schema, extraction_prompt
from gm.engine import MODE_AIDE
from gm.rules_lookup import look_up
from gm.turn_engine.plan import read_plan
from gm.turn_engine.policy import TurnPolicy

from .rules_fixtures import DEFAULT_CHARACTER_RULES, seated_character_rules
from .test_rules import INVENTED_CHARACTER_RULES


class SeatedRulesTestCase(SimpleTestCase):
    """Runs each test under the invented rules."""

    def setUp(self) -> None:
        self.enterContext(seated_character_rules(INVENTED_CHARACTER_RULES))


class AttributesAndAgeTest(SeatedRulesTestCase):
    def test_the_point_buy_is_the_canons(self) -> None:
        within = dict.fromkeys(
            ("strength", "dexterity", "intelligence", "wisdom", "constitution"), "8"
        )
        attributes, error = _parse_point_buy_attributes({**within, "charisma": "10"})
        self.assertIsNone(error)
        self.assertEqual(sum((attributes or {}).values()), 50)
        _attributes, error = _parse_point_buy_attributes({**within, "charisma": "16"})
        self.assertEqual(error, "CHR must be between 4 and 15; you entered 16.")
        self.assertIsNone(point_buy_budget_note(50))
        self.assertEqual(
            point_buy_budget_note(52),
            "Point-buy total: 52 — 2 over the 50-point budget.",
        )

    def test_the_attribute_ceiling_is_the_canons(self) -> None:
        validate_attribute_maximum(25)
        with self.assertRaisesMessage(ValidationError, "Attribute max is 25"):
            validate_attribute_maximum(26)
        self.assertIn(
            "outside the 4-15 a player may set",
            check_attribute("strength", 3, is_game_master=True),
        )
        with self.assertRaises(CharacterChangeError):
            check_attribute("strength", 3, is_game_master=False)

    def test_the_starting_age_and_stage_are_the_canons(self) -> None:
        self.assertEqual(starting_age(), 14)
        self.assertEqual(Character().age, 14)
        validate_starting_age(14)
        with self.assertRaises(ValidationError):
            validate_starting_age(13)
        self.assertEqual(character_int_field_minimum("age"), 14)
        self.assertEqual(aging_stage_for_age(24), 2)
        self.assertEqual(age_for_aging_stage(2), 24)


class RollsTest(SeatedRulesTestCase):
    def test_the_attributes_are_rolled_on_the_canons_dice(self) -> None:
        # The invented dice are 4d4: four faces of 1 to 4, totals 4 to 16.
        total, log = roll_attribute_logged(Roller(seed=59))
        self.assertRegex(log, r"^4d4: \[[1-4], [1-4], [1-4], [1-4]\] = \d+$")
        self.assertTrue(4 <= total <= 16, total)
        stats = roll_all_character_stats(Roller(seed=59))
        attribute_lines = stats["dice_log"].splitlines()[:6]
        for label, line in zip(
            ("STR", "DEX", "INT", "WIS", "CON", "CHR"), attribute_lines, strict=True
        ):
            with self.subTest(label=label):
                self.assertTrue(line.startswith(f"{label}: 4d4: ["), line)
        character = Character()
        note = apply_rolled_attributes(character)
        self.assertIn("strength 4d4: [", note)
        self.assertIn("charisma: 4d4: [", character.dice_log)
        character = Character(strength=None)
        self.assertIn("4d4: [", apply_sheet_attribute_roll(character))

    def test_the_fatigue_roll_is_the_canons_dice(self) -> None:
        total, log = roll_fatigue_logged(Roller(seed=59))
        self.assertTrue(log.startswith("1d8+1: ["), log)
        self.assertTrue(2 <= total <= 9, total)
        self.assertEqual(
            (
                character_int_field_minimum("fatigue_roll"),
                character_int_field_maximum("fatigue_roll"),
            ),
            (2, 9),
        )
        validate_fatigue_roll(9)
        with self.assertRaisesMessage(
            ValidationError,
            "The Fatigue roll is 1d8+1, so it must be between 2 and 9; 10 was given",
        ):
            validate_fatigue_roll(10)
        character = Character()
        self.assertEqual(
            apply_fatigue_roll(character, 9), "Fatigue roll (1d8+1) set to 9"
        )

    def test_the_fatigue_sum_reads_the_canons_attributes(self) -> None:
        character = Character(
            strength=3,
            dexterity=3,
            intelligence=3,
            wisdom=3,
            constitution=12,
            charisma=3,
            fatigue_roll=5,
            age=14,
        )
        # The invented sum is CON + the roll, nothing else.
        self.assertEqual(character.fatigue, 17)

    def test_the_literacy_roll_is_the_canons_dice(self) -> None:
        total, log = roll_literacy_logged(Roller(seed=59))
        self.assertTrue(log.startswith("2d10: ["), log)
        self.assertTrue(2 <= total <= 20, total)
        validate_literacy_roll(20)
        with self.assertRaises(ValidationError):
            validate_literacy_roll(1)
        character = Character(location="Port", intelligence=10)
        self.assertEqual(character.location_literacy_check, "2d10 ≤ INT − 1")


class SeatedLiteracyServiceTest(BaseTestCase):
    """``services.roll_literacy`` rolls the canon's dice end to end (#93).

    ``RollsTest`` seats the function the service calls; this seats the
    service itself, on a saved character, so a service that rolled dice of
    its own, or a note that named them, would fail here.
    """

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_character_rules(INVENTED_CHARACTER_RULES))
        # Port's invented penalty is 1, so INT 10 reads on 9 or less.
        self.character = create_character(
            self.create_user("seated-literacy-owner"),
            location="Port",
            intelligence=10,
        )

    def test_the_service_rolls_and_records_the_canons_dice(self) -> None:
        result = services.roll_literacy(self.character.pk)

        found = DICE_IN_TEXT.findall(result.note)
        self.assertEqual(len(found), 1, result.note)
        expression, faces_text, total_text = found[0]
        faces = [int(face) for face in faces_text.split(",")]
        total = int(total_text)
        self.assertEqual(expression, "2d10")
        self.assertEqual(len(faces), 2, faces)
        self.assertTrue(all(1 <= face <= 10 for face in faces), faces)
        self.assertEqual(sum(faces), total)
        self.assertIn("rolled the literacy check (2d10 ≤ INT − 1)", result.note)
        outcome = f"{total} ≤ 9, literate" if total <= 9 else f"{total} > 9, illiterate"
        self.assertIn(outcome, result.note)
        self.character.refresh_from_db()
        self.assertEqual(self.character.literacy_roll, total)
        self.assertTrue(self.character.literacy_rolled)
        self.assertEqual(self.character.is_literate, total <= 9)
        validate_literacy_roll(self.character.literacy_roll)


class HeightAndWeightTest(SeatedRulesTestCase):
    def test_the_height_table_and_chain_are_the_canons(self) -> None:
        self.assertEqual(demon_die_to_height_inches("2"), 54)
        self.assertEqual(demon_die_to_height_inches("1.1.3"), 50 - 3)
        self.assertEqual(demon_die_to_height_inches("6.6.4"), 70 + 3)
        self.assertEqual(demon_die_to_height_inches("1." * 10 + "3"), 30)

    def test_the_weight_table_and_strength_bonus_are_the_canons(self) -> None:
        # Face 2 is 8 stones; STR 10 // 3 adds 3; 10 pounds a stone.
        self.assertEqual(calculate_final_weight_lbs("2", 10), (8 + 3) * 10)


class TablesTest(SeatedRulesTestCase):
    def test_provenance_rolls_on_the_canons_tables(self) -> None:
        roller = Roller(seed=59)
        social_class, detail, _log = roll_provenance_logged(roller)
        detail_table = getattr(INVENTED_CHARACTER_RULES, social_class)
        self.assertIn(detail, detail_table.labels)

    def test_the_origin_grant_and_literacy_are_the_canons(self) -> None:
        location, _log = roll_location_logged(Roller(seed=59))
        self.assertIn(location, ("Port", "Ask the GM"))
        skills, log = location_effects("Port")
        self.assertEqual(skills, {"sailing": 2})
        self.assertEqual(
            log, "Location effects: Sailing 2 point, Literacy check INT - 1"
        )
        self.assertEqual(location_effects("Ask the GM")[0], {})
        self.assertEqual(literacy_check_text("Port"), "Literacy check INT - 1")

    def test_the_starting_coin_is_the_canons(self) -> None:
        self.assertEqual(starting_coin_for_wealth("Poor"), (5, "5"))
        self.assertEqual(starting_coin_for_wealth("Wealthy"), (None, "Consult GM"))
        self.assertFalse(wealth_gives_coin("Wealthy"))
        self.assertTrue(wealth_gives_coin("Comfortable"))
        bonus = Roller(seed=59).roll("2d6", purpose="2d6").total
        coin, detail = starting_coin_for_wealth("Comfortable", Roller(seed=59))
        self.assertEqual(coin, 50 + bonus)
        self.assertEqual(detail, f"50 + 2d6({bonus}) = {50 + bonus}")
        level, _coin, _log = roll_wealth_logged(Roller(seed=59))
        self.assertIn(level, INVENTED_CHARACTER_RULES.wealth.labels)


class GamesMasterTest(SeatedRulesTestCase):
    def test_the_extraction_schema_is_the_canons(self) -> None:
        properties = character_json_schema()["properties"]
        self.assertEqual(properties["location"]["enum"], ["Port", "Ask the GM"])
        self.assertEqual(
            properties["wealth_level"]["enum"], ["Poor", "Comfortable", "Wealthy"]
        )
        self.assertEqual(
            (properties["strength"]["minimum"], properties["strength"]["maximum"]),
            (4, 15),
        )
        self.assertEqual(
            character_json_schema(for_game_master=True)["properties"]["strength"][
                "maximum"
            ],
            25,
        )
        self.assertEqual(
            (
                properties["fatigue_roll"]["minimum"],
                properties["fatigue_roll"]["maximum"],
            ),
            (2, 9),
        )
        self.assertEqual(properties["age"]["minimum"], 14)

    def test_the_extraction_ask_and_schema_name_the_canons_dice(self) -> None:
        for ask in (extraction_prompt(), extraction_prompt(for_game_master=True)):
            with self.subTest(ask=ask[-40:]):
                self.assertIn("the 1d8+1 rolled for Fatigue (2-9)", ask)
                self.assertIn("the 2d10 rolled for the literacy check (2-20)", ask)
                self.assertNotIn("2d6", ask)
                self.assertNotIn("3d6", ask)
        self.assertIn("attributes above 15", extraction_prompt(for_game_master=True))
        properties = character_json_schema()["properties"]
        self.assertIn(
            "the 1d8+1 rolled for Fatigue (2-9)",
            properties["fatigue_roll"]["description"],
        )
        self.assertIn(
            "the 2d10 rolled for the literacy check (2-20)",
            properties["literacy_roll"]["description"],
        )

    def test_the_rules_lookups_are_the_canons(self) -> None:
        origins = look_up("origins")
        self.assertIn(
            "Port: Sailing or Swimming, literacy check 2d10 ≤ INT − 1", origins
        )
        self.assertIn("Ask the GM: the Game Master decides", origins)
        coin = look_up("starting_coin")
        self.assertIn(
            "Poor 5; Comfortable 50 + 2d6; Wealthy: the Game Master sets it", coin
        )
        self.assertIn("keyed to age from 14", look_up("aging"))


#: The invented rules with a one-skill origin beside the two-skill "Port", for
#: the rename rule: only an origin whose row names two skills offers a choice.
ONE_SKILL_ORIGIN_RULES = replace(
    INVENTED_CHARACTER_RULES,
    location=LocationTable(
        rows=(
            LocationRow(50, "Port", ("Sailing", "Swimming"), 1),
            LocationRow(99, "Inland", ("Hunting",), 3),
            LocationRow(100, "Ask the GM", (), None),
        ),
        skill_points=2,
    ),
)


class PointBuyServiceTest(SeatedRulesTestCase):
    """``services.check_point_buy`` holds a typed score to the canon's buy."""

    def test_a_typed_score_is_held_to_the_canons_range(self) -> None:
        character = Character()
        self.assertIn("point-buy", check_point_buy(character, "strength", 4))
        self.assertIn("point-buy", check_point_buy(character, "strength", 15))
        for refused in (3, 16):
            with self.subTest(score=refused):
                with self.assertRaisesMessage(
                    CharacterChangeError, "between 4 and 15 for a player"
                ):
                    check_point_buy(character, "strength", refused)

    def test_the_last_score_must_make_the_canons_total(self) -> None:
        character = Character(
            strength=8, dexterity=8, intelligence=8, wisdom=8, constitution=8
        )
        self.assertIn("point-buy", check_point_buy(character, "charisma", 10))
        with self.assertRaisesMessage(
            CharacterChangeError,
            "Typed scores are a 50-point buy: with the scores recorded, charisma "
            "can be 10 to 10; 9 was given",
        ):
            check_point_buy(character, "charisma", 9)


class OriginSkillTest(SimpleTestCase):
    """The origin's grant follows the canon's location table."""

    def setUp(self) -> None:
        self.enterContext(seated_character_rules(ONE_SKILL_ORIGIN_RULES))

    def test_a_two_skill_origin_lets_the_player_choose(self) -> None:
        character = Character(
            location="Port", origin_skill="sailing", skills={"sailing": 2}
        )
        recorded, points = apply_skill_rename(
            character, "sailing", "swimming", is_game_master=False
        )
        # The choice is the player's by the row (#67): the history records
        # the new name with no Game Master's call; the point is the origin's two.
        self.assertEqual(recorded, "swimming")
        self.assertEqual(points, 2)
        self.assertEqual(character.skills, {"swimming": 2})
        self.assertEqual(character.origin_skill, "swimming")

    def test_a_game_master_making_the_rows_choice_is_not_a_call(self) -> None:
        character = Character(
            location="Port", origin_skill="sailing", skills={"sailing": 2}
        )
        recorded, _points = apply_skill_rename(
            character, "sailing", "swimming", is_game_master=True
        )
        self.assertEqual(recorded, "swimming")

    def test_a_skill_the_row_does_not_list_stays_the_game_masters(self) -> None:
        for location, origin_skill, new_skill in (
            ("Port", "sailing", "hunting"),
            ("Inland", "hunting", "sailing"),
        ):
            with self.subTest(location):
                character = Character(
                    location=location,
                    origin_skill=origin_skill,
                    skills={origin_skill: 2},
                )
                with self.assertRaises(CharacterChangeError):
                    apply_skill_rename(
                        character, origin_skill, new_skill, is_game_master=False
                    )
                recorded, points = apply_skill_rename(
                    character, origin_skill, new_skill, is_game_master=True
                )
                self.assertEqual(
                    recorded,
                    f"{new_skill} (points moved from another skill: the Game "
                    "Master's call)",
                )
                self.assertEqual((points, character.skills), (2, {new_skill: 2}))

    def test_a_one_skill_origin_offers_no_choice(self) -> None:
        character = Character(
            location="Inland", origin_skill="hunting", skills={"hunting": 2}
        )
        with self.assertRaises(CharacterChangeError):
            apply_skill_rename(character, "hunting", "sailing", is_game_master=False)
        self.assertEqual(character.skills, {"hunting": 2})

    def test_the_grant_moves_at_the_canons_points(self) -> None:
        character = Character(location="Inland", origin_skill="", skills={})
        lines = apply_origin_skill(character, None)
        self.assertEqual(character.skills, {"hunting": 2})
        self.assertEqual(
            [line for *_, line in lines], ["hunting 2 granted by the Inland origin"]
        )
        character.location = "Port"
        lines = apply_origin_skill(character, "Inland")
        self.assertEqual(character.skills, {"sailing": 2})
        self.assertEqual(
            [line for *_, line in lines],
            [
                "hunting 2 taken back: the Inland origin's grant, which the Port "
                "origin does not give",
                "sailing 2 granted by the Port origin",
            ],
        )

    def test_a_reroll_onto_the_same_origin_logs_the_points_kept(self) -> None:
        character = Character(
            location="Port",
            location_recorded=True,
            origin_skill="sailing",
            skills={"sailing": 2},
            dice_log="",
        )
        with patch("characters.dice.roll_d100", return_value=10):
            apply_origin_roll(character, is_game_master=True)
        self.assertIn(
            "Location effects: sailing 2 point kept from before, Literacy check "
            "INT - 1",
            character.dice_log,
        )


class AttributeCeilingTest(SeatedRulesTestCase):
    """Experience raises a score no further than the canon's ceiling, 25."""

    def test_an_attribute_bonus_stops_at_the_ceiling(self) -> None:
        character = Character(strength=24)
        character._apply_attribute_bonus("str", 3)
        self.assertEqual(character.strength, 25)

    def test_the_simulated_run_stops_its_running_score_at_the_ceiling(self) -> None:
        seen_strength: list[int] = []

        def record(attributes: dict[str, int], age: int) -> dict[str, int]:
            seen_strength.append(attributes["strength"])
            return _effective_attributes_at_age(attributes, age)

        with (
            patch("characters.skills._effective_attributes_at_age", side_effect=record),
            patch("characters.skills.roll_survival_dice", return_value=(6, 6, 5, 17)),
            patch("characters.skills.roll_d20", return_value=10),
        ):
            result = simulate_experience_years(
                years_requested=3,
                years_completed=0,
                current_age=14,
                track="laborer",
                strength=24,
                dexterity=10,
                intelligence=10,
                wisdom=10,
                constitution=10,
                charisma=10,
            )
        self.assertTrue(result["alive"])
        self.assertEqual(seen_strength, [24, 25, 25])


class GamesMasterPlanTest(SeatedRulesTestCase):
    def test_a_stated_fatigue_roll_is_held_to_the_canons_dice(self) -> None:
        def read(value: int) -> object:
            return read_plan(
                json.dumps(
                    {
                        "actions": [
                            {
                                "action": "roll_fatigue",
                                "character_id": 41,
                                "value": value,
                            }
                        ]
                    }
                ),
                roster_ids={41},
                policy=TurnPolicy.for_turn(MODE_AIDE, is_game_master=True),
            )

        self.assertEqual(read(9).actions[0].fatigue_value, 9)
        self.assertTrue(read(10).errors)
        self.assertTrue(read(1).errors)


class SheetAndCreationPageTest(SeatedRulesTestCase):
    def test_the_attribute_inputs_take_the_canons_bounds(self) -> None:
        character = Character()
        player = editable_attribute({}, character, "strength", True)
        self.assertEqual((player["minimum"], player["maximum"]), (4, 15))
        game_master = editable_attribute(
            {"is_game_master": True}, character, "strength", True
        )
        self.assertEqual((game_master["minimum"], game_master["maximum"]), (1, 25))

    def test_a_number_input_takes_the_canons_bounds(self) -> None:
        cell = editable_number({}, Character(), "fatigue_roll", True)
        self.assertEqual((cell["minimum"], cell["maximum"]), (2, 9))

    def test_the_creation_page_offers_the_canons_buy(self) -> None:
        defaults = _point_buy_defaults()
        self.assertEqual(sum(defaults), 50)
        self.assertEqual(defaults, [9, 9, 8, 8, 8, 8])
        context = _character_create_context()
        self.assertEqual(
            (
                context["point_buy_total"],
                context["point_buy_min"],
                context["point_buy_max"],
            ),
            (50, 4, 15),
        )
        self.assertEqual(
            [attribute["value"] for attribute in context["core_attributes"]],
            defaults,
        )


class BestiaryTest(SeatedRulesTestCase):
    def test_a_beasts_pools_use_the_canons_sum_and_midpoint(self) -> None:
        # 1d8+1 gives 2 to 9, so the midpoint is 5 (the public 2d6's is 7).
        self.assertEqual(fixed_fatigue_roll(), 5)
        scores = {
            "strength": 16,
            "dexterity": 12,
            "intelligence": 5,
            "wisdom": 5,
            "constitution": 14,
            "charisma": 3,
        }
        # The invented sum is CON alone.
        self.assertEqual(derive_pools(scores), (14 + 5, math.ceil((14 + 5) * 2 / 3)))


class ProvenanceTableTest(SimpleTestCase):
    def test_the_same_roll_lands_on_each_canons_own_row(self) -> None:
        for rules, social_class in (
            (INVENTED_CHARACTER_RULES, "nobility"),
            (DEFAULT_CHARACTER_RULES, "merchant"),
        ):
            with (
                self.subTest(social_class=social_class),
                seated_character_rules(rules),
                patch("characters.dice.roll_d100", side_effect=[15, 1]),
            ):
                self.assertEqual(roll_provenance_logged()[0], social_class)
