"""A player's free point goes into a skill the rules name (issue #862).

The sheet's allocate endpoint took any typed name, so a player with a free
point could start "brawling" (which the battle's bare-handed fighter used to
read, #815) or a name no rule knows. A player now starts only a skill the
skills glossary names, or adds to one the character holds; a Game Master may
name any skill.
"""

from django.urls import reverse

from characters import services
from characters.skill_checks import is_glossary_skill
from common.test_helpers import BaseTestCase, create_character


class AllocationNamesTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("allocating-player")
        self.game_master = self.create_dm("allocating-gm")
        self.character = create_character(
            self.owner, free_skill_points=3, skills={"sword": 2}
        )
        self.url = reverse("characters:allocate_skill", args=[self.character.pk])
        self.login_as(self.owner)

    def allocate(self, skill: str):
        return self.post_json(self.url, {"skill": skill})

    def test_a_player_cannot_start_brawling_or_a_name_no_rule_knows(self):
        for name in ("Brawling", "Navigation", "Shield Parry"):
            with self.subTest(name=name):
                response = self.allocate(name)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json()["error"], services.ALLOCATION_REFUSED_REASON
                )
        self.character.refresh_from_db()
        self.assertEqual(self.character.skills, {"sword": 2})
        self.assertEqual(self.character.free_skill_points, 3)

    def test_a_player_cannot_focus_knowledge_but_may_start_it(self):
        response = self.allocate("Knowledge (History)")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"], services.KNOWLEDGE_FIELD_REFUSED_REASON
        )
        self.assertEqual(self.allocate("Knowledge").status_code, 200)

    def test_a_player_starts_what_the_glossary_names(self):
        for name, stored in (
            ("Perception", "perception"),
            ("Maker Skill (Smith)", "maker skill (smith)"),
            ("Weapon (Broadsword)", "weapon (broadsword)"),
        ):
            with self.subTest(name=name):
                response = self.allocate(name)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.json()["skill"], stored)

    def test_a_player_adds_to_a_skill_already_held_whatever_its_name(self):
        response = self.allocate("Sword")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["points"], 3)

    def test_a_game_master_may_name_any_skill(self):
        self.logout()
        self.login_as(self.game_master)
        for name in ("Brawling", "Knowledge (History)"):
            with self.subTest(name=name):
                self.assertEqual(self.allocate(name).status_code, 200)
        self.character.refresh_from_db()
        self.assertIn("brawling", self.character.skills)


class AllocationRefusalTest(BaseTestCase):
    """The rule itself, as the sheet asks it."""

    def test_arcane_and_luck_are_the_glossarys(self):
        self.assertTrue(is_glossary_skill("arcane"))
        self.assertTrue(is_glossary_skill("luck"))
        self.assertIsNone(services.allocation_refusal({}, "arcane"))

    def test_a_bare_maker_skill_lands_on_the_craft_held(self):
        held = {"maker skill (smith)": 1}
        self.assertEqual(
            services.allocation_target(held, "maker skill"), "maker skill (smith)"
        )
        self.assertIsNone(services.allocation_refusal(held, "maker skill"))

    def test_brawling_is_refused(self):
        self.assertFalse(is_glossary_skill("brawling"))
        self.assertEqual(
            services.allocation_refusal({}, "brawling"),
            services.ALLOCATION_REFUSED_REASON,
        )
