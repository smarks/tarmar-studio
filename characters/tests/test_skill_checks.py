"""Issue #756: the skill check, by the skills glossary's mechanic.

Spencer's ruling (2026-09-28): 2d6 + the governing attribute's modifier +
the skill level against a difficulty the Game Master sets; 3d6 ≤ attribute
stays the mechanic for a raw attribute check with no skill.
"""

import re
from pathlib import Path

from django.conf import settings
from django.urls import reverse

from characters.services import CharacterChangeError
from characters.skill_checks import (
    LITERACY_SKILL,
    governing_attribute,
    skill_check,
)
from characters.skill_rules import REQUIRED_SKILL_NAMES
from characters.skills import (
    SPECIALISED_SKILLS,
    granted_skill_names,
    normalize_skill_name,
    points_for_level,
)
from characters.weapon_skill import UNRECORDED_WEAPON_SKILL
from common.test_helpers import BaseTestCase, FacesRoller, create_character
from gm import engine

from .rules_fixtures import DEFAULT_SKILL_RULES, seated_skill_rules

RULES = Path(settings.BASE_DIR) / "reference" / "content" / "public-rules"
GLOSSARY = RULES / "characters" / "prior-experience" / "skills-glossary.md"
ATTACK_ROLLS = RULES / "combat" / "action-options" / "attack-rolls.md"

ABBREVIATIONS = {
    "STR": "strength",
    "DEX": "dexterity",
    "INT": "intelligence",
    "WIS": "wisdom",
    "CON": "constitution",
    "CHA": "charisma",
    "—": None,
}


class GlossaryDriftTest(BaseTestCase):
    """The default canon's skill and attribute checks are the pages' (#70)."""

    def setUp(self):
        super().setUp()
        self.enterContext(seated_skill_rules())  # the default canon's rules

    def test_the_map_is_the_glossarys_governs_column(self):
        """Every row of the glossary's skill tables, read against the map."""
        rows = re.findall(
            r"^\| ([^|]+?) \| (STR|DEX|INT|WIS|CON|CHA|—) \|",
            GLOSSARY.read_text(),
            flags=re.MULTILINE,
        )
        self.assertGreater(len(rows), 35)
        from_page = {
            normalize_skill_name(skill): ABBREVIATIONS[governs]
            for skill, governs in rows
        }
        self.assertEqual(
            from_page, DEFAULT_SKILL_RULES.skill_check.governing_attributes
        )

    def test_the_names_read_without_asking_are_the_ones_the_loader_requires(self):
        """``governing_attribute`` reads these without checking first (#70).

        The loader refuses a canon whose map lacks any of
        ``REQUIRED_SKILL_NAMES``; the code's own names for them must be that
        list, or a canon could pass the loader and still fail a check.
        """
        self.assertEqual(
            set(SPECIALISED_SKILLS) | {UNRECORDED_WEAPON_SKILL, LITERACY_SKILL},
            set(REQUIRED_SKILL_NAMES),
        )

    def test_every_skill_a_track_grants_has_an_entry(self):
        governed = DEFAULT_SKILL_RULES.skill_check.governing_attributes
        for skill_name in granted_skill_names():
            self.assertIn(skill_name, governed)

    def test_the_checks_dice_and_naturals_are_the_pages(self):
        """The glossary's mechanic and attack-rolls.md's attribute check."""
        glossary = " ".join(GLOSSARY.read_text().replace("\n>", "\n").split())
        checks = DEFAULT_SKILL_RULES.skill_check
        self.assertIn(
            f"roll **{checks.dice} + the governing attribute's modifier",
            glossary,
        )
        self.assertIn(
            f"A natural {checks.natural_success} always succeeds; a natural "
            f"{checks.natural_failure} always fails.",
            glossary,
        )
        self.assertIn(
            f"focused field gives is **{checks.focused_field_bonus:+d}** to "
            "checks in that field",
            glossary,
        )
        attack_rolls = " ".join(ATTACK_ROLLS.read_text().split())
        self.assertIn(
            f"still roll **{DEFAULT_SKILL_RULES.attribute_check.dice} ≤ attribute**",
            attack_rolls,
        )

    def test_the_pages_and_the_prompt_teach_the_glossary_mechanic(self):
        self.assertIn(
            "2d6 + the governing attribute's modifier + your skill level",
            " ".join(GLOSSARY.read_text().replace("\n>", "\n").split()),
        )
        attack_rolls = " ".join(ATTACK_ROLLS.read_text().split())
        self.assertIn(
            "**2d6 + the governing attribute's modifier + your skill level**",
            attack_rolls,
        )
        self.assertNotIn("General skill checks, attribute checks,", attack_rolls)
        prompt = engine.section_core() + engine.section_combat()
        self.assertIn(
            "A skill check rolls 2d6 + the governing attribute's modifier + the "
            "skill level",
            prompt,
        )
        self.assertNotIn("skill and attribute checks, saves, and spells still", prompt)


class SkillCheckRuleTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("checker")
        # DEX 16 is +3; age 16, so no aging.
        self.character = create_character(
            self.owner,
            name="Checker",
            dexterity=16,
            skills={"stealth": points_for_level(2), "luck": 1, "juggling": 3},
        )

    def test_two_d6_plus_modifier_plus_level_against_the_difficulty(self):
        check = skill_check(
            self.character,
            "stealth",
            difficulty=12,
            is_game_master=True,
            roller=FacesRoller(3, 4),
        )
        self.assertEqual((check.attribute, check.attribute_modifier), ("dexterity", 3))
        self.assertEqual((check.faces, check.level, check.total), ((3, 4), 2, 12))
        self.assertEqual(check.outcome, "success")
        fails = skill_check(
            self.character,
            "stealth",
            difficulty=13,
            is_game_master=True,
            roller=FacesRoller(3, 4),
        )
        self.assertEqual(fails.outcome, "failure")

    def test_the_naturals_decide_whatever_the_total(self):
        high = skill_check(
            self.character,
            "stealth",
            difficulty=40,
            is_game_master=True,
            roller=FacesRoller(6, 6),
        )
        self.assertEqual((high.outcome, high.natural), ("success", "natural 12"))
        low = skill_check(
            self.character,
            "stealth",
            difficulty=0,
            is_game_master=True,
            roller=FacesRoller(1, 1),
        )
        self.assertEqual((low.outcome, low.natural), ("failure", "natural 2"))

    def test_a_players_roll_has_no_difficulty_and_no_outcome(self):
        """The GM sets the difficulty; a player cannot (the #756 review)."""
        with self.assertRaises(CharacterChangeError):
            skill_check(self.character, "stealth", difficulty=5)
        plain = skill_check(self.character, "stealth", roller=FacesRoller(3, 4))
        self.assertEqual((plain.total, plain.outcome, plain.difficulty), (12, "", None))
        self.assertEqual(
            plain.line, "stealth check: 2d6 [3, 4] = 7 +3 DEX +2 level = 12"
        )
        # A player may roll as often as they like; the Game Master's asking
        # is what makes a roll count, so even a natural carries no outcome.
        natural = skill_check(self.character, "stealth", roller=FacesRoller(6, 6))
        self.assertEqual((natural.outcome, natural.natural), ("", "natural 12"))
        self.assertTrue(natural.line.endswith("= 17 (natural 12)"))

    def test_untrained_rolls_the_modifier_alone(self):
        check = skill_check(self.character, "sailor", roller=FacesRoller(2, 3))
        self.assertEqual((check.level, check.total, check.outcome), (0, 8, ""))

    def test_the_score_is_the_aged_one(self):
        """Ruling on #773: checks use effective (aged) scores."""
        self.character.aging_dex_penalty = 2  # 16 -> 14, +1
        check = skill_check(self.character, "stealth", roller=FacesRoller(3, 3))
        self.assertEqual((check.attribute_score, check.attribute_modifier), (14, 1))

    def test_luck_is_not_a_check(self):
        with self.assertRaises(CharacterChangeError):
            skill_check(self.character, "luck")

    def test_an_unknown_skill_is_refused(self):
        with self.assertRaises(CharacterChangeError):
            skill_check(self.character, "basket weaving")

    def test_a_player_cannot_substitute_or_state_the_dice(self):
        with self.assertRaises(CharacterChangeError):
            skill_check(self.character, "stealth", attribute="strength")
        with self.assertRaises(CharacterChangeError):
            skill_check(self.character, "stealth", stated_total=12)

    def test_a_game_master_may_substitute_state_and_govern_a_custom_skill(self):
        substituted = skill_check(
            self.character,
            "stealth",
            attribute="wisdom",
            stated_total=10,
            is_game_master=True,
        )
        self.assertTrue(substituted.substituted and substituted.stated)
        self.assertEqual(substituted.total, 10 + 0 + 2)
        with self.assertRaises(CharacterChangeError):
            skill_check(self.character, "juggling")
        custom = skill_check(
            self.character,
            "juggling",
            attribute="dexterity",
            is_game_master=True,
            roller=FacesRoller(4, 4),
        )
        self.assertEqual(custom.total, 8 + 3 + 2)

    def test_a_tied_weapon_skill_rides_on_dex(self):
        self.assertEqual(governing_attribute("weapon (broadsword)"), "dexterity")
        self.assertEqual(governing_attribute("arcane"), "intelligence")


class SkillCheckEndpointTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("roller")
        self.character = create_character(
            self.owner, name="Roller", skills={"stealth": points_for_level(2)}
        )
        self.url = reverse("characters:skill_check", args=[self.character.pk])

    def test_a_player_rolls_one_check_and_it_is_in_the_history(self):
        self.login_as(self.owner)
        refused = self.post_json(self.url, {"skill": "stealth", "difficulty": "9"})
        self.assertEqual(refused.status_code, 400)
        # The name is normalised the way every skill write is.
        response = self.post_json(self.url, {"skill": "  Stealth "})
        self.assertEqual(response.status_code, 200, response.content)
        check = response.json()["check"]
        self.assertEqual(len(check["faces"]), 2)
        self.assertEqual(
            check["total"], check["dice_total"] + check["attribute_modifier"] + 2
        )
        self.character.refresh_from_db()
        entry = self.character.history[-1]
        self.assertEqual((entry["field"], entry["by"]), ("skill_check", "roller"))
        self.assertIn("stealth check: 2d6", entry["new"])

    def test_a_player_override_is_refused_and_a_game_masters_is_taken(self):
        self.login_as(self.owner)
        refused = self.post_json(self.url, {"skill": "stealth", "total": "12"})
        self.assertEqual(refused.status_code, 400)
        self.logout()
        self.login_as(self.create_dm())
        taken = self.post_json(
            self.url, {"skill": "stealth", "total": "12", "difficulty": "30"}
        )
        self.assertEqual(taken.json()["check"]["outcome"], "success")

    def test_someone_elses_character_is_refused(self):
        self.login_as(self.create_user("stranger"))
        self.assertEqual(
            self.post_json(self.url, {"skill": "stealth"}).status_code, 403
        )

    def test_the_sheet_offers_the_control(self):
        self.login_as(self.owner)
        page = self.client.get(reverse("characters:edit", args=[self.character.pk]))
        self.assertContains(page, 'id="skill-check-btn"')
        self.assertNotContains(page, "Attribute (Game Master)")
