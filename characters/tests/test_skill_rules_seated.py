"""Skills and prior experience read the loaded canon's rules (issue #70).

Under the default canon the code behaves exactly as before, which the rest of
the suite holds. These tests seat invented rules (``test_skill_rules``'s,
whose every number differs from the pages') and check that each caller
follows them, so a number left behind in Python would fail here rather than
agree with the default canon by coincidence.
"""

import json
import re
from typing import Any
from unittest.mock import patch

from characters.constants import CHARACTER_ATTRIBUTE_FIELDS
from characters.dice import roll_survival_dice
from characters.models import Character
from characters.services import CharacterChangeError, apply_experience_years
from characters.skill_checks import (
    attribute_check,
    governing_attribute,
    is_glossary_skill,
    skill_check,
)
from characters.skills import (
    calculate_attribute_modifier,
    calculate_skill_points_from_xp,
    get_aging_penalties_for_age,
    get_random_track,
    get_skill_for_year,
    granted_skill_names,
    next_aging_change_after_age,
    points_for_level,
    selectable_tracks,
    simulate_experience_years,
    skill_cost_multiplier,
    survival_rule_description,
    survival_targets,
    track_keys,
    track_label,
    track_labels,
)
from characters.templatetags.dice_display import in_aging_order, survival_roll
from common.test_helpers import BaseTestCase, FacesRoller
from gm import engine
from gm.engine import MODE_AIDE
from gm.rules_lookup import look_up
from gm.turn_engine.outcomes import outcomes_from_run
from gm.turn_engine.plan import read_plan
from gm.turn_engine.policy import TurnPolicy, _check_words, dice_actions_asked_for
from reference.rules_file import (
    caches_derived_from_rules,
    forget_values_derived_from_rules,
)

from .rules_fixtures import seated_skill_rules
from .test_skill_rules import INVENTED_SKILL_RULES, SAILOR_SKILLS, SMITH_SKILLS

#: Six scores whose invented modifier is +2 (the table tops out at 6). Typed
#: loosely: they are spread into keyword arguments no checker matches by key.
STRONG: dict[str, Any] = dict.fromkeys(CHARACTER_ATTRIBUTE_FIELDS, 12)


class SeatedSkillRulesTestCase(BaseTestCase):
    """Runs each test under the invented skill rules."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_skill_rules(INVENTED_SKILL_RULES))


def play_one_year(track: str, faces: tuple[int, ...]) -> dict:
    """One year on ``track`` with the survival dice and the d20 pinned to 1."""
    with (
        patch("characters.skills.roll_survival_dice", return_value=faces),
        patch("characters.skills.roll_d20", return_value=1),
    ):
        return simulate_experience_years(
            years_requested=1,
            years_completed=0,
            current_age=16,
            track=track,
            **STRONG,
        )


class TracksTest(SeatedSkillRulesTestCase):
    def test_the_tracks_and_their_labels_are_the_canons(self) -> None:
        self.assertEqual(track_keys(), ("sailor", "smith"))
        self.assertEqual(selectable_tracks(), ("sailor", "smith", "random"))
        self.assertEqual(
            track_labels(), {"random": "Random", "sailor": "Sailor", "smith": "Smith"}
        )
        self.assertEqual(track_label("smith"), "Smith")
        self.assertEqual(track_label("civil_service"), "Civil Service")
        self.assertEqual(survival_targets(), {"sailor": 1, "smith": 25})
        self.assertIn(get_random_track(), ("sailor", "smith"))

    def test_the_d20_tables_are_the_canons(self) -> None:
        self.assertEqual(get_skill_for_year("sailor", 1), SAILOR_SKILLS[0])
        self.assertEqual(get_skill_for_year("smith", 20), SMITH_SKILLS[-1])
        with self.assertRaisesMessage(ValueError, "Unknown track: merchant"):
            get_skill_for_year("merchant", 1)
        self.assertEqual(
            granted_skill_names(),
            sorted({name.lower() for name in (*SAILOR_SKILLS, *SMITH_SKILLS)}),
        )

    def test_a_year_pays_the_tracks_xp_and_money(self) -> None:
        # The smith's pay rides on its tongs, which the d20's 1 grants: level 1.
        smith = play_one_year("smith", (10, 10, 20))
        self.assertEqual(smith["xp_awarded"], 400)
        self.assertEqual(smith["money_earned"], 30)
        self.assertEqual(smith["skills_gained"], {"tongs": 1})
        # The sailor's pay rides on the highest skill held.
        with patch("characters.skills.roll_d20", return_value=1):
            sailor = simulate_experience_years(
                years_requested=1,
                years_completed=0,
                current_age=16,
                track="sailor",
                starting_skills={"rope 4": 6},  # level 3
                **STRONG,
            )
        self.assertEqual(sailor["xp_awarded"], 300)
        self.assertEqual(sailor["money_earned"], 30)

    def test_the_money_skill_is_the_one_the_canon_names(self) -> None:
        # Tongs held at level 2 pay the smith 60, though Bellows is level 6.
        with (
            patch("characters.skills.roll_survival_dice", return_value=(10, 10, 20)),
            patch("characters.skills.roll_d20", return_value=11),  # Bellows
        ):
            smith = simulate_experience_years(
                years_requested=1,
                years_completed=0,
                current_age=16,
                track="smith",
                starting_skills={"tongs": 3, "bellows": 21},
                **STRONG,
            )
        self.assertEqual(smith["money_earned"], 60)

    def test_the_service_and_the_plan_take_only_the_canons_tracks(self) -> None:
        character = Character.objects.create(
            user=self.create_user("sailor"), name="Wren", **STRONG
        )
        with self.assertRaisesMessage(CharacterChangeError, "Unknown track: merchant"):
            apply_experience_years(character, "merchant", 1)
        with patch("characters.skills.roll_survival_dice", return_value=(10, 10, 20)):
            _note, run = apply_experience_years(character, "smith", 1)
        self.assertEqual(run["track"], "smith")

        def read(track: str) -> object:
            return read_plan(
                json.dumps(
                    {
                        "actions": [
                            {
                                "action": "play_years",
                                "character_id": 41,
                                "track": track,
                                "years": 1,
                            }
                        ]
                    }
                ),
                roster_ids={41},
                policy=TurnPolicy.for_turn(MODE_AIDE, is_game_master=True),
            )

        self.assertFalse(read("smith").errors)
        self.assertFalse(read("random").errors)
        refused = " ".join(str(error) for error in read("merchant").errors)
        self.assertIn("track must be one of sailor, smith, random", refused)


class SurvivalRollTest(SeatedSkillRulesTestCase):
    def test_the_survival_dice_are_the_canons(self) -> None:
        with patch("characters.dice.random.randint", side_effect=[7, 9]):
            self.assertEqual(roll_survival_dice(), (7, 9, 16))

    def test_the_naturals_and_target_are_the_canons(self) -> None:
        # 2d10's 20 survives the smith's unreachable 25; 2 kills the sailor's 1.
        survived = play_one_year("smith", (10, 10, 20))["survival_check"]
        self.assertTrue(survived["success"])
        self.assertTrue(survived["auto_success"])
        self.assertEqual(survived["target"], 25)
        self.assertEqual(survived["dice"], [10, 10])
        self.assertEqual(survived["dice_expression"], "2d10")
        died = play_one_year("sailor", (1, 1, 2))
        self.assertFalse(died["alive"])
        self.assertTrue(died["survival_check"]["auto_death"])
        # 19 + 2 against 25 is a death; 18 was 3d6's natural survival.
        self.assertFalse(play_one_year("smith", (9, 10, 19))["alive"])

    def test_the_rule_in_words_and_the_records_are_the_canons(self) -> None:
        described = survival_rule_description()
        self.assertIn("roll 2d10, add", described)
        self.assertIn("A natural 20 on the dice", described)
        self.assertIn("a natural 2 is an automatic death", described)
        run = play_one_year("smith", (10, 10, 20))
        run["track"] = "smith"
        (outcome,) = outcomes_from_run(run)
        self.assertIn("survival: 2d10 [10, 10] = 20", outcome.line)
        self.assertIn("2d10 rolled 10, 10 = 20", outcome.detail)
        self.assertIn(described, outcome.detail)
        self.assertEqual(
            survival_roll(run["survival_check"]),
            "2d10 [10, 10] + 2 = 22 vs 25 (a natural 20 — always survives)",
        )


class LadderAndCostTest(SeatedSkillRulesTestCase):
    def test_the_ladder_is_the_canons(self) -> None:
        # 50 XP a point on the climb to 100, 70 a point from there on.
        for total_xp, points in ((0, 0), (99, 1), (100, 2), (300, 4), (440, 6)):
            with self.subTest(total_xp=total_xp):
                self.assertEqual(calculate_skill_points_from_xp(total_xp), points)
        self.assertEqual(Character(total_xp=99).level, 1)
        self.assertEqual(Character(total_xp=300).level, 3)
        self.assertEqual(Character(total_xp=10**6).level, 3)

    def test_the_cost_multipliers_are_the_canons(self) -> None:
        self.assertEqual(skill_cost_multiplier("Sorcery II"), 3)
        self.assertEqual(skill_cost_multiplier("Arcane"), 1)
        self.assertEqual(skill_cost_multiplier(None), 1)
        self.assertEqual(points_for_level(2, "sorcery"), 9)
        self.assertEqual(points_for_level(2, "arcane"), 3)


class AgingAndModifierTest(SeatedSkillRulesTestCase):
    def test_the_aging_table_is_the_canons(self) -> None:
        # Stage 3 (age 28) is the first with a penalty; stage 4 is the table's
        # last; past it STR, DEX and CON fall a point a stage and INT and WIS
        # a point every third.
        self.assertIsNone(get_aging_penalties_for_age(27))
        self.assertEqual(
            get_aging_penalties_for_age(28),
            {
                "strength": -1,
                "dexterity": 0,
                "intelligence": 0,
                "wisdom": 0,
                "constitution": 0,
            },
        )
        self.assertEqual(
            get_aging_penalties_for_age(36),
            {
                "strength": -3,
                "dexterity": -2,
                "intelligence": -1,
                "wisdom": 0,
                "constitution": -4,
            },
        )
        self.assertEqual(next_aging_change_after_age(16, 10), (28, {"strength": -1}))
        change = next_aging_change_after_age(28, 10)
        assert change is not None
        self.assertEqual(
            list(change[1]), ["strength", "dexterity", "intelligence", "constitution"]
        )

    def test_the_modifier_table_is_the_canons(self) -> None:
        for score, modifier in ((0, -2), (2, -2), (4, 0), (6, 2), (30, 2)):
            with self.subTest(score=score):
                self.assertEqual(calculate_attribute_modifier(score), modifier)
        # Every score of 12 is past the table's top, so the best modifier is +2.
        self.assertEqual(
            play_one_year("smith", (10, 10, 20))["survival_check"]["modifier"], 2
        )


class GamesMasterTest(SeatedSkillRulesTestCase):
    def test_the_prompt_is_built_from_the_canons_rules(self) -> None:
        prompt = engine.system_prompt()
        self.assertIn(
            "Modifiers: 2=−2, 3=−1, 4=0, 5=+1, 6=+2. Below 2 the modifier stays "
            "−2 and above 6 it stays +2.",
            prompt,
        )
        self.assertIn(
            "Level 1 = 0 XP, Level 2 = 100, Level 3 = 300. Skill points accrue at "
            "regular XP intervals on the climb to each level (every 50 XP to "
            "level 2, every 70 XP to level 3), and the level-3 rate keeps running "
            "past 300 XP.",
            prompt,
        )
        self.assertIn("sailor, smith, random", engine.section_plan_schema())

    def test_the_rules_lookups_are_the_canons(self) -> None:
        aging = look_up("aging")
        self.assertIn("28 STR −1; 32 STR −2, DEX −1, INT −1, CON −3", aging)
        self.assertIn("INT and WIS every 12", aging)
        self.assertIn("target is 25, so on 2d10", look_up("survival", "smith"))
        self.assertIn("sailor 1, smith 25", look_up("survival"))


class PromptCachesTest(BaseTestCase):
    def test_seated_rules_build_their_own_prompt_and_leave_none_behind(self) -> None:
        loaded = engine.system_prompt()
        with seated_skill_rules(INVENTED_SKILL_RULES):
            seated = engine.system_prompt()
        self.assertNotEqual(seated, loaded)
        self.assertEqual(engine.system_prompt(), loaded)

    def test_clearing_forgets_every_value_built_from_the_rules(self) -> None:
        registered = caches_derived_from_rules()
        for builder in (engine.system_prompt, _check_words):
            self.assertIn(builder, registered)
        for cached in registered:
            cached()
            self.assertEqual(cached.cache_info().currsize, 1)
        forget_values_derived_from_rules()
        for cached in registered:
            with self.subTest(cached=cached):
                self.assertEqual(cached.cache_info().currsize, 0)


class SheetTest(SeatedSkillRulesTestCase):
    def test_the_edit_page_offers_the_canons_tracks_and_dice(self) -> None:
        user = self.create_user("player")
        character = Character.objects.create(user=user, name="Wren", **STRONG)
        self.login_as(user)
        html = self.client.get(f"/tarmar/characters/{character.pk}/").content.decode()
        match = re.search(
            r'<script id="character-edit-config" type="application/json">'
            r"(.*?)</script>",
            html,
            re.DOTALL,
        )
        assert match is not None
        config = json.loads(match.group(1))
        self.assertEqual(
            config["trackLabels"],
            {"random": "Random", "sailor": "Sailor", "smith": "Smith"},
        )
        self.assertEqual(config["survivalTargets"], {"sailor": 1, "smith": 25})
        self.assertEqual(config["survivalDice"], "2d10")
        self.assertEqual(config["agingByAge"]["28"], {"str": -1})
        self.assertIn('<option value="smith"', html)
        self.assertNotIn('<option value="merchant"', html)

    def test_history_prints_aging_in_the_tables_order(self) -> None:
        # A run stored before #70 past the table kept CON before INT.
        stored = {"strength": -3, "constitution": -3, "intelligence": -1}
        self.assertEqual(
            in_aging_order(stored),
            [("strength", -3), ("intelligence", -1), ("constitution", -3)],
        )


class SkillAndAttributeChecksTest(SeatedSkillRulesTestCase):
    """The checks follow the invented file: 3d4 naturals 12 and 3, a +2 focus,
    the invented governing pairs, and a 2d8 attribute check limited to 4."""

    def setUp(self) -> None:
        super().setUp()
        self.game_master = self.create_dm(username="checks-gm")
        self.character = Character.objects.create(
            user=self.game_master,
            name="Wren",
            skills={"rope": 3, "knowledge (tides)": 1},
            **STRONG,
        )

    def test_each_skill_rides_on_the_canons_attribute(self) -> None:
        self.assertEqual(governing_attribute("rope"), "dexterity")
        self.assertEqual(governing_attribute("weapon (broadsword)"), "strength")
        self.assertEqual(governing_attribute("knowledge (tides)"), "charisma")
        self.assertEqual(governing_attribute("sorcery"), "wisdom")
        self.assertIsNone(governing_attribute("stealth"))
        self.assertTrue(is_glossary_skill("sorcery"))
        self.assertFalse(is_glossary_skill("stealth"))
        with self.assertRaisesMessage(CharacterChangeError, "Unknown skill: stealth"):
            skill_check(self.character, "stealth")

    def test_a_skill_check_rolls_the_canons_dice_and_naturals(self) -> None:
        rolled = skill_check(self.character, "rope", roller=FacesRoller(1, 4, 4))
        self.assertEqual(
            (rolled.dice, rolled.faces, rolled.dice_total), ("3d4", (1, 4, 4), 9)
        )
        self.assertTrue(rolled.line.startswith("rope check: 3d4 [1, 4, 4] = 9 +2 DEX"))
        # The canon's natural success is 11: 4+4+3 succeeds against 40, and
        # 4+4+4, the dice's highest, is no natural and fails against it.
        high = skill_check(
            self.character,
            "rope",
            difficulty=40,
            is_game_master=True,
            roller=FacesRoller(4, 4, 3),
        )
        self.assertEqual((high.natural, high.outcome), ("natural 11", "success"))
        highest = skill_check(
            self.character,
            "rope",
            difficulty=40,
            is_game_master=True,
            roller=FacesRoller(4, 4, 4),
        )
        self.assertEqual((highest.natural, highest.outcome), ("", "failure"))
        low = skill_check(
            self.character,
            "rope",
            difficulty=0,
            is_game_master=True,
            roller=FacesRoller(1, 1, 1),
        )
        self.assertEqual((low.natural, low.outcome), ("natural 3", "failure"))
        # 2d6's 2 is no natural on 3d4, and no 3d4 roll shows it.
        with self.assertRaisesMessage(
            CharacterChangeError, "A 3d4 total is between 3 and 12"
        ):
            skill_check(self.character, "rope", stated_total=2, is_game_master=True)
        stated = skill_check(
            self.character, "rope", stated_total=3, is_game_master=True
        )
        self.assertIn("3d4 stated by a Game Master = 3", stated.line)

    def test_a_focused_field_adds_the_canons_bonus(self) -> None:
        focused = skill_check(
            self.character, "knowledge (tides)", roller=FacesRoller(2, 2, 2)
        )
        self.assertEqual(focused.focus_bonus, 2)
        self.assertIn("+2 focus", focused.line)

    def test_an_attribute_check_rolls_the_canons_dice_within_its_limit(self) -> None:
        rolled = attribute_check(self.character, "wisdom", roller=FacesRoller(7, 9))
        self.assertIn("wisdom check, 2d8: [7, 9] = 16 > 12", rolled.line)
        with self.assertRaisesMessage(
            CharacterChangeError, "A 2d8 total is between 2 and 16"
        ):
            attribute_check(
                self.character, "wisdom", stated_total=17, is_game_master=True
            )
        stated = attribute_check(
            self.character, "wisdom", stated_total=16, modifier=4, is_game_master=True
        )
        self.assertIn("2d8 stated by a Game Master = 16 ≤ 16", stated.line)
        with self.assertRaisesMessage(
            CharacterChangeError, "modifier must be a whole number from -4 to 4"
        ):
            attribute_check(self.character, "wisdom", modifier=5, is_game_master=True)
        # Players are held to the rules: no modifier at all, within the limit
        # or not, as before.
        with self.assertRaisesMessage(
            CharacterChangeError, "Only a Game Master can set a modifier"
        ):
            attribute_check(self.character, "wisdom", modifier=1)

    def test_the_plan_holds_stated_totals_and_modifiers_to_the_canon(self) -> None:
        def errors(action: dict[str, Any]) -> str:
            reading = read_plan(
                json.dumps({"actions": [{"character_id": 41, **action}]}),
                roster_ids={41},
                policy=TurnPolicy.for_turn(MODE_AIDE, is_game_master=True),
            )
            return " ".join(str(error) for error in reading.errors)

        skill = {"action": "skill_check", "skill": "rope"}
        self.assertIn(
            "total must be a whole number from 3 to 12", errors({**skill, "total": 2})
        )
        self.assertEqual(errors({**skill, "total": 3}), "")
        attribute = {"action": "attribute_check", "attribute": "wisdom"}
        self.assertIn(
            "total must be a whole number from 2 to 16",
            errors({**attribute, "total": 17}),
        )
        self.assertIn(
            "modifier must be a whole number from -4 to 4",
            errors({**attribute, "modifier": 5}),
        )
        self.assertEqual(errors({**attribute, "total": 16, "modifier": -4}), "")

    def test_a_check_asked_for_by_name_is_one_the_canon_governs(self) -> None:
        self.assertIn("skill_check", dice_actions_asked_for("his rope check"))
        self.assertNotIn("skill_check", dice_actions_asked_for("his stealth check"))

    def test_the_rules_lookups_are_the_canons(self) -> None:
        said = look_up("skill_check")
        self.assertIn("Skill check: roll 3d4, add", said)
        self.assertIn("natural 11 always succeeds and a natural 3 always fails", said)
        self.assertIn("rope dexterity", said)
        self.assertNotIn("stealth", said)
        attribute = look_up("attribute_check")
        self.assertIn("roll 2d8; it passes", attribute)
        self.assertIn("may state a total (2 to 16)", attribute)
        self.assertIn("set a modifier (−4 to +4)", attribute)

    def test_the_sheet_bounds_and_labels_the_stated_total_by_the_canon(self) -> None:
        self.login_as(self.game_master)
        html = self.client.get(
            f"/tarmar/characters/{self.character.pk}/"
        ).content.decode()
        self.assertIn("3d4 rolled at the table (Game Master)", html)
        self.assertRegex(html, r'name="total" min="3" max="12"')
        self.assertIn('<option value="rope"', html)
        self.assertNotIn('<option value="stealth"', html)


class SeatedChecksLeaveNothingBehindTest(BaseTestCase):
    def test_the_check_pattern_is_the_loaded_canons_again_afterwards(self) -> None:
        self.assertIn("skill_check", dice_actions_asked_for("his stealth check"))
        with seated_skill_rules(INVENTED_SKILL_RULES):
            self.assertNotIn("skill_check", dice_actions_asked_for("his stealth check"))
        self.assertIn("skill_check", dice_actions_asked_for("his stealth check"))
