"""Issue #753: one answer to "which skill applies to this weapon".

The sheet's attack panel and the battle board read the same resolver, so
the fixture below is swung on both and must price the same bonus on each.
"""

import importlib

from django.apps import apps as django_apps
from django.urls import reverse

from battle.services import _seated_arms, create_battle
from characters import attack
from characters.services import RENAME_REFUSED_REASON, CharacterChangeError
from characters.skill_checks import governing_attribute
from characters.skills import points_for_level
from characters.weapon_skill import (
    UNTRAINED,
    adds_to_attacks,
    record_weapon_skill,
    resolve_weapon_skill,
    trained_weapon_id,
    weapon_skill_for_granted,
    weapon_skill_name,
)
from common.test_helpers import BaseTestCase, create_character

from .test_attack_service import ScriptedRoller

LEVEL_THREE = points_for_level(3)


class ResolverRuleTest(BaseTestCase):
    def test_the_skill_taken_for_the_weapon_gives_its_full_level(self):
        skill = resolve_weapon_skill({"weapon (broadsword)": LEVEL_THREE}, "broadsword")
        self.assertEqual((skill.skill_name, skill.bonus), ("weapon (broadsword)", 3))

    def test_another_weapon_of_the_class_gets_half_rounded_down(self):
        skill = resolve_weapon_skill({"weapon (broadsword)": LEVEL_THREE}, "mace")
        self.assertEqual(skill.bonus, 1)
        self.assertEqual(skill.effective_level, 1)

    def test_another_class_gets_nothing(self):
        self.assertEqual(
            resolve_weapon_skill({"weapon (broadsword)": LEVEL_THREE}, "spear_2h"),
            UNTRAINED,
        )

    def test_an_unusual_weapon_shares_no_training(self):
        """Quarterstaff is Striking but Unusual: nothing transfers either way."""
        self.assertEqual(
            resolve_weapon_skill({"weapon (quarterstaff)": LEVEL_THREE}, "mace").bonus,
            0,
        )
        self.assertEqual(
            resolve_weapon_skill({"weapon (mace)": LEVEL_THREE}, "quarterstaff").bonus,
            0,
        )

    def test_no_other_skill_adds_to_an_attack(self):
        """The issue's evidence: Knowledge V added +5 to a broadsword."""
        skills = {"knowledge": points_for_level(5), "weapon parry": LEVEL_THREE}
        self.assertEqual(resolve_weapon_skill(skills, "broadsword"), UNTRAINED)

    def test_an_untied_weapon_skill_adds_nothing_until_tied(self):
        self.assertEqual(
            resolve_weapon_skill({"weapon": LEVEL_THREE}, "broadsword"), UNTRAINED
        )

    def test_archery_covers_both_missile_classes(self):
        for weapon_id in ("longbow", "light_crossbow", "sling"):
            with self.subTest(weapon_id):
                skill = resolve_weapon_skill({"archery": LEVEL_THREE}, weapon_id)
                self.assertEqual((skill.skill_name, skill.bonus), ("archery", 3))
        self.assertEqual(
            resolve_weapon_skill({"archery": LEVEL_THREE}, "broadsword"), UNTRAINED
        )
        # The boomerang sits in a Missile class but is Unusual.
        self.assertEqual(
            resolve_weapon_skill({"archery": LEVEL_THREE}, "boomerang"), UNTRAINED
        )

    def test_the_best_skill_applies_and_skills_do_not_stack(self):
        skills = {"weapon (mace)": points_for_level(5), "weapon (saber)": LEVEL_THREE}
        skill = resolve_weapon_skill(skills, "saber")
        self.assertEqual((skill.skill_name, skill.bonus), ("weapon (saber)", 3))

    def test_older_spellings_are_read_as_the_weapon(self):
        """The battle's generated fighters used to carry "broadsword"."""
        for spelling in ("broadsword", "weapon (broadsword)"):
            self.assertEqual(trained_weapon_id(spelling), "broadsword")
        self.assertEqual(
            trained_weapon_id("weapon (bastard sword 1h)"), "bastard_sword_1h"
        )
        self.assertEqual(
            weapon_skill_name("bastard_sword_1h"), "weapon (bastard sword 1h)"
        )
        self.assertIsNone(trained_weapon_id("weapon parry"))
        self.assertIsNone(trained_weapon_id("weapon"))


class SheetAndBoardAgreeTest(BaseTestCase):
    """The same character, the same weapon, the same bonus on both surfaces."""

    FIXTURES = (
        # (skills, weapon in hand, expected bonus)
        ({"weapon (broadsword)": LEVEL_THREE}, "broadsword", 3),
        ({"weapon (broadsword)": LEVEL_THREE}, "saber", 1),
        ({"weapon": LEVEL_THREE, "knowledge": points_for_level(5)}, "broadsword", 0),
        ({"archery": LEVEL_THREE}, "longbow", 3),
    )

    def test_the_attack_panel_and_the_battle_price_the_same_skill(self):
        owner = self.create_user("agreer")
        for skills, weapon_id, expected in self.FIXTURES:
            with self.subTest(skills=skills, weapon=weapon_id):
                character = create_character(
                    owner,
                    name="Agreer",
                    skills=skills,
                    equipment=[{"item_id": weapon_id, "quantity": 1}],
                )
                sheet = attack.resolve(
                    character,
                    {"weapon_index": 0, "armour_tier": "None"},
                    roller=ScriptedRoller(d20s=[10]),
                )
                board_level = _seated_arms(character).skill_level
                self.assertEqual(sheet["to_hit"]["skill_bonus"], expected)
                self.assertEqual(board_level, expected)

    def test_a_seated_character_carries_the_resolved_level(self):
        dm = self.create_dm()
        character = create_character(
            dm,
            name="Seated Swordsman",
            skills={"weapon (broadsword)": LEVEL_THREE},
            equipment=[{"item_id": "broadsword", "quantity": 1}],
        )
        battle = create_battle(
            dm, name="Agreement", count=1, character_ids=(character.pk,), seed=3
        )
        seated = next(
            combatant
            for combatant in battle.state_json["combatants"]
            if combatant["name"] == "Seated Swordsman"
        )
        self.assertEqual(seated["weapon_skill_level"], 3)


class RecordWeaponSkillTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("tier")
        self.character = create_character(
            self.owner,
            name="Tier",
            skills={"weapon": LEVEL_THREE},
            equipment=[{"item_id": "broadsword", "quantity": 1}],
        )
        self.url = reverse("characters:record_weapon_skill", args=[self.character.pk])

    def test_a_player_ties_an_untied_skill_once_and_it_is_in_the_history(self):
        self.login_as(self.owner)
        response = self.post_json(
            self.url, {"skill": "weapon", "item_id": "broadsword"}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.character.refresh_from_db()
        self.assertEqual(self.character.skills, {"weapon (broadsword)": LEVEL_THREE})
        self.assertIn("tied to Broadsword", self.character.history[-1]["new"])
        moved = self.post_json(
            self.url, {"skill": "weapon (broadsword)", "item_id": "mace"}
        )
        self.assertEqual(moved.status_code, 400)
        self.assertIn("only a Game Master", moved.json()["error"])

    def test_a_game_master_may_move_a_tied_skill(self):
        self.character.skills = {"weapon (broadsword)": LEVEL_THREE}
        record_weapon_skill(
            self.character, "weapon (broadsword)", "mace", is_game_master=True
        )
        self.assertEqual(self.character.skills, {"weapon (mace)": LEVEL_THREE})

    def test_points_join_a_skill_already_held_for_that_weapon(self):
        self.character.skills = {"weapon": 1, "weapon (broadsword)": LEVEL_THREE}
        record_weapon_skill(
            self.character, "weapon", "broadsword", is_game_master=False
        )
        self.assertEqual(
            self.character.skills, {"weapon (broadsword)": LEVEL_THREE + 1}
        )

    def test_refuses_what_is_not_a_weapon_skill_or_not_a_weapon(self):
        self.character.skills = {"weapon": 1, "knowledge": 1}
        with self.assertRaises(CharacterChangeError):
            record_weapon_skill(
                self.character, "knowledge", "mace", is_game_master=True
            )
        with self.assertRaises(CharacterChangeError):
            record_weapon_skill(
                self.character, "weapon", "backpack", is_game_master=True
            )
        with self.assertRaises(CharacterChangeError):
            record_weapon_skill(
                self.character, "weapon", "arrows_20", is_game_master=True
            )

    def test_a_player_cannot_tie_or_move_one_by_renaming(self):
        """The rename endpoint is the sibling path that writes the same name."""
        self.login_as(self.owner)
        url = reverse("characters:rename_skill", args=[self.character.pk])
        response = self.post_json(
            url, {"old_name": "weapon", "new_name": "Weapon (Great Sword)"}
        )
        self.assertEqual(response.status_code, 400)
        self.character.refresh_from_db()
        self.assertEqual(self.character.skills, {"weapon": LEVEL_THREE})

    def test_no_skill_is_renamed_into_one_an_attack_reads(self):
        """The #753 review: Knowledge V renamed to "weapon" and tied, or to
        "archery", was +5 to hit."""
        self.character.skills = {"knowledge": points_for_level(5), "archery": 1}
        self.character.save()
        self.login_as(self.owner)
        url = reverse("characters:rename_skill", args=[self.character.pk])
        # "Brawling" was a fourth name here while the board read it; the
        # rules have no such skill and nothing reads it now (#867), so a tied
        # Weapon skill stands in, and BrawlingIsNoAttackSkillTest covers it.
        for old_name, new_name in (
            ("knowledge", "weapon"),
            ("knowledge", "weapon (broadsword)"),
            ("archery", "knowledge lore"),
        ):
            with self.subTest(new_name):
                response = self.post_json(
                    url, {"old_name": old_name, "new_name": new_name}
                )
                self.assertEqual(response.status_code, 400)
        self.post_json(url, {"old_name": "archery", "new_name": "stealth"})
        self.character.refresh_from_db()
        self.assertIn("archery", self.character.skills)
        # A rename within Knowledge still works, by the Game Master who may
        # focus it (#768); renaming it to "Lore" moved its points out of
        # Knowledge, the bug this line used to pin as the owner's.
        self.logout()
        self.login_as(self.create_dm())
        renamed = self.post_json(
            url, {"old_name": "knowledge", "new_name": "Knowledge (Lore)"}
        )
        self.assertEqual(renamed.status_code, 200)

    def test_a_repeated_weapon_grant_goes_to_the_one_tied_skill(self):
        """The #753 review: prior experience's bare "Weapon" is the same skill
        when exactly one is tied; with two, the player chooses."""
        self.assertEqual(
            weapon_skill_for_granted({"weapon (saber)": 3}, "weapon"), "weapon (saber)"
        )
        # With two tied, the higher one (the #753 second review).
        self.assertEqual(
            weapon_skill_for_granted(
                {"weapon (saber)": 3, "weapon (mace)": 6}, "weapon"
            ),
            "weapon (mace)",
        )
        self.assertEqual(weapon_skill_for_granted({"archery": 3}, "weapon"), "weapon")
        self.assertEqual(
            weapon_skill_for_granted({"weapon (saber)": 3}, "coins"), "coins"
        )
        self.character.skills = {"weapon (saber)": 3}
        self.character.apply_experience_run(
            {
                "years": [{"age": 17}],
                "xp_awarded": 0,
                "money_earned": 0,
                "skills_gained": {"weapon": 1},
                "alive": True,
                "aging": None,
            },
            "merchant",
            0,
        )
        self.assertEqual(self.character.skills, {"weapon (saber)": 4})

    def test_the_data_migration_names_older_weapon_skills_and_reverses(self):
        migration = importlib.import_module(
            "characters.migrations.0017_weapon_skills_named_for_their_weapon"
        )
        self.character.skills = {
            "broadsword": 3,
            "weapon (broadsword)": 1,
            "great sword": 2,
            "bastard sword (1 hand)": 1,
            "sword": 2,
            "weapon": 1,
            "knowledge": 5,
        }
        self.character.save()
        migration.name_weapon_skills(django_apps, None)
        self.character.refresh_from_db()
        self.assertEqual(
            self.character.skills,
            {
                "weapon (broadsword)": 4,
                "weapon (great sword)": 2,
                "weapon (bastard sword 1h)": 1,
                "weapon": 3,
                "knowledge": 5,
            },
        )
        migration.restore_weapon_names(django_apps, None)
        self.character.refresh_from_db()
        self.assertEqual(
            self.character.skills,
            {
                "broadsword": 4,
                "great_sword": 2,
                "bastard_sword_1h": 1,
                "weapon": 3,
                "knowledge": 5,
            },
        )

    def test_any_untied_weapon_skill_is_offered_the_control(self):
        """ "weapon (nonsense)" is a Weapon skill the resolver cannot read."""
        self.character.skills = {"weapon (spork)": LEVEL_THREE}
        self.character.save()
        self.login_as(self.owner)
        page = self.client.get(reverse("characters:edit", args=[self.character.pk]))
        self.assertContains(page, 'class="weapon-skill-tie"')
        response = self.post_json(
            self.url, {"skill": "weapon (spork)", "item_id": "dagger"}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.character.refresh_from_db()
        self.assertEqual(self.character.skills, {"weapon (dagger)": LEVEL_THREE})

    def test_the_sheet_offers_the_control_on_an_untied_skill(self):
        self.login_as(self.owner)
        page = self.client.get(reverse("characters:edit", args=[self.character.pk]))
        self.assertContains(page, 'class="weapon-skill-tie"')
        self.assertNotContains(page, 'id="attack-skill"')


class BrawlingIsNoAttackSkillTest(BaseTestCase):
    """#867: the rules have no bare-handed skill, so nothing reads "brawling".

    The battle's generated brawler used to train one, the board seated bare
    hands at its level, and the rename guard and skill checks treated it as
    an attack skill. A record that still carries one (a brawler saved before
    the change) keeps it as an ordinary skill.
    """

    def test_no_attack_reads_it(self):
        self.assertFalse(adds_to_attacks("brawling"))
        self.assertTrue(adds_to_attacks("archery"))
        self.assertTrue(adds_to_attacks("weapon (dagger)"))

    def test_a_players_rename_into_it_is_refused_as_a_move_not_an_attack(self):
        """No attack guard refuses it any more: a player's rename of Knowledge
        into Brawling is refused as moving points to another skill (#768),
        not as a skill that adds to attacks. This test used to expect the
        owner's rename to pass; #768 refuses that move for everyone but a
        Game Master, whose case follows."""
        owner = self.create_user("player-renamer")
        character = create_character(
            owner, name="Renamer", skills={"knowledge": points_for_level(5)}
        )
        self.login_as(owner)
        response = self.post_json(
            reverse("characters:rename_skill", args=[character.pk]),
            {"old_name": "knowledge", "new_name": "Brawling"},
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["error"], RENAME_REFUSED_REASON)
        self.assertNotIn("adds to attacks", response.json()["error"])

    def test_a_game_master_may_rename_into_it(self):
        owner = self.create_user("renamer")
        character = create_character(
            owner, name="Renamer", skills={"knowledge": points_for_level(5)}
        )
        self.login_as(self.create_dm("renamer-gm"))
        response = self.post_json(
            reverse("characters:rename_skill", args=[character.pk]),
            {"old_name": "knowledge", "new_name": "Brawling"},
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_no_rule_names_its_attribute(self):
        self.assertIsNone(governing_attribute("brawling"))

    def test_a_saved_brawler_fights_bare_handed_at_level_zero(self):
        character = create_character(
            self.create_user("old-brawler"),
            name="Old Brawler",
            skills={"brawling": points_for_level(4)},
        )
        arms = _seated_arms(character)
        self.assertEqual(
            (arms.weapon.item_id, arms.weapon.name, arms.skill_level),
            ("", "bare hands", 0),
        )
