"""Off-board rolls take the injury penalty (#840); the illiterate read nothing (#863).

injury-thresholds-death.md: "Down to 1/2 (but above 5) — −1 to all rolls;
Between 1 and 5 — −2 to all rolls", the worse pool governing. The board
applied it; the sheet's attack panel and the skill check rolled as if the
character were unhurt.
"""

from django.urls import reverse
from django.utils.html import escape
from tarmar_engine.reactions import TarmarReactions

from battle.services import snapshot_character
from characters import attack
from characters.models import Character
from characters.refusal_words import game_master_call_note, refusal_words
from characters.services import CharacterChangeError
from characters.skill_checks import (
    ILLITERATE_REASON,
    LITERACY_UNSETTLED_REASON,
    injury_penalty,
    skill_check,
    unconscious_refusal,
)
from characters.tests.test_attack_service import AttackServiceTestBase, ScriptedRoller
from characters.tests.test_skill_checks import FacesRoller
from characters.views.characters import skill_check_skills
from common.test_helpers import BaseTestCase, create_character


def fatigue_of(character) -> int:
    """The character's Fatigue pool, which every character here has."""
    fatigue = character.fatigue
    assert fatigue is not None
    return fatigue


def hurt_to(character, *, fatigue_left: int) -> None:
    """Leave ``character`` with ``fatigue_left`` Fatigue and Body untouched."""
    character.fatigue_damage = fatigue_of(character) - fatigue_left
    character.save()


class InjuryPenaltyTest(BaseTestCase):
    """The penalty itself, read off the record's pools."""

    def setUp(self) -> None:
        super().setUp()
        self.character = create_character(self.create_user(), name="Dain")

    def test_the_bands(self) -> None:
        self.assertEqual(injury_penalty(self.character), 0)
        hurt_to(self.character, fatigue_left=fatigue_of(self.character) // 2)
        self.assertEqual(injury_penalty(self.character), -1)
        hurt_to(self.character, fatigue_left=5)
        self.assertEqual(injury_penalty(self.character), -2)
        # Unconscious takes no rolls for the band to touch.
        hurt_to(self.character, fatigue_left=0)
        self.assertEqual(injury_penalty(self.character), 0)

    def test_a_character_with_no_pools_yet_has_none(self) -> None:
        incomplete = create_character(self.create_user("other"), fatigue_roll=None)
        self.assertEqual(injury_penalty(incomplete), 0)

    def test_the_skill_check_takes_it_and_says_so(self) -> None:
        self.character.skills = {"stealth": 3}
        self.character.save()
        unhurt = skill_check(self.character, "stealth", roller=FacesRoller(3, 4))
        hurt_to(self.character, fatigue_left=4)
        hurt = skill_check(self.character, "stealth", roller=FacesRoller(3, 4))
        self.assertEqual(unhurt.injury_penalty, 0)
        self.assertEqual(hurt.injury_penalty, -2)
        self.assertEqual(hurt.total, unhurt.total - 2)
        self.assertIn(" -2 injured = ", hurt.line)
        self.assertNotIn("injured", unhurt.line)

    def test_a_natural_still_decides_through_the_penalty(self) -> None:
        self.character.skills = {"stealth": 1}
        hurt_to(self.character, fatigue_left=4)
        check = skill_check(
            self.character,
            "stealth",
            difficulty=40,
            is_game_master=True,
            roller=FacesRoller(6, 6),
        )
        self.assertEqual(check.outcome, "success")


class AttackPanelInjuryTest(AttackServiceTestBase):
    """The sheet's attack panel, as the board's attack roll takes it."""

    def test_the_to_hit_carries_the_penalty_and_the_dice_line_names_it(self) -> None:
        unhurt = self.resolve(ScriptedRoller(d20s=[10]))
        hurt_to(self.character, fatigue_left=fatigue_of(self.character) // 2)
        hurt = attack.resolve(
            self.character,
            {"weapon_index": 0, "armour_tier": "None"},
            roller=ScriptedRoller(d20s=[10]),
        )
        self.assertEqual(unhurt["to_hit"]["injury_penalty"], 0)
        self.assertEqual(hurt["to_hit"]["injury_penalty"], -1)
        self.assertEqual(hurt["to_hit"]["total"], unhurt["to_hit"]["total"] - 1)
        self.assertEqual(
            hurt["attack_roll"]["total"], unhurt["attack_roll"]["total"] - 1
        )
        self.assertIn("(includes -1 injured)", hurt["dice_log"][0])
        self.assertNotIn("injured", unhurt["dice_log"][0])


class IlliterateLiteracyCheckTest(BaseTestCase):
    """A Literacy check for a character who failed the literacy check (#863).

    The #772 ruling: the origin's literacy check decides whether a character
    can read at all; the Literacy skill adds to deciphering and composing.
    """

    def setUp(self) -> None:
        super().setUp()
        self.player = self.create_user(username="alice")
        self.character = create_character(
            self.player,
            name="Dain",
            intelligence=10,
            skills={"literacy": 3},
            literacy_roll=18,
            literacy_rolled=True,
        )
        self.assertIs(self.character.is_literate, False)

    def test_a_player_is_refused_and_told_why(self) -> None:
        with self.assertRaisesMessage(CharacterChangeError, ILLITERATE_REASON):
            skill_check(self.character, "literacy")

    def test_a_game_master_may_still_call_for_it(self) -> None:
        check = skill_check(
            self.character, "literacy", is_game_master=True, roller=FacesRoller(3, 3)
        )
        self.assertEqual(check.skill, "literacy")
        self.assertEqual(check.game_master_call, "not known to read")
        self.assertTrue(check.line.endswith(game_master_call_note("not known to read")))

    def test_a_literate_character_rolls_and_one_not_yet_rolled_does_not(
        self,
    ) -> None:
        self.character.literacy_roll = 3
        self.assertIs(self.character.is_literate, True)
        skill_check(self.character, "literacy", roller=FacesRoller(3, 3))
        # The #863 review: nothing says an unrolled character can read.
        self.character.literacy_roll = None
        self.character.literacy_rolled = False
        self.assertIsNone(self.character.is_literate)
        with self.assertRaisesMessage(CharacterChangeError, LITERACY_UNSETTLED_REASON):
            skill_check(self.character, "literacy", roller=FacesRoller(3, 3))
        skill_check(
            self.character, "literacy", is_game_master=True, roller=FacesRoller(3, 3)
        )
        self.assertTrue(
            {option["name"]: option for option in skill_check_skills(self.character)}[
                "literacy"
            ]["game_master_only"]
        )

    def test_the_endpoint_refuses_the_player_in_words(self) -> None:
        self.login_as(self.player)
        response = self.post_json(
            reverse("characters:skill_check", args=[self.character.pk]),
            {"skill": "Literacy"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], ILLITERATE_REASON)

    def test_the_sheets_option_says_it_is_a_game_masters_call(self) -> None:
        options = {
            option["name"]: option for option in skill_check_skills(self.character)
        }
        self.assertTrue(options["literacy"]["game_master_only"])
        self.assertFalse(options["stealth"]["game_master_only"])
        self.login_as(self.player)
        html = self.client.get(
            reverse("characters:edit", args=[self.character.pk])
        ).content.decode()
        self.assertIn(
            "Literacy (INT) — not known to read; "
            + escape(refusal_words().common.a_game_masters_call),
            html,
        )


class UnconsciousRollsNothingTest(BaseTestCase):
    """The #840 review: a pool at 0 or below is unconscious, and the sheet's
    rolls agree with the board's wherever the character is conscious."""

    def setUp(self) -> None:
        super().setUp()
        self.player = self.create_user(username="alice")
        self.character = create_character(
            self.player,
            name="Dain",
            skills={"stealth": 3},
            equipment=[{"item_id": "dagger", "quantity": 1}],
        )

    def test_the_sheet_agrees_with_the_board_above_zero_and_refuses_below(
        self,
    ) -> None:
        # The reviewer's probe, as a test: every state of both pools, for
        # four constitutions. Where the character is conscious the sheet's
        # penalty is the board's; where not, the sheet refuses the roll.
        reactions = TarmarReactions()
        checked = 0
        for constitution in (6, 9, 12, 15):
            character = Character(
                name="Probe",
                strength=12,
                dexterity=12,
                intelligence=11,
                wisdom=9,
                constitution=constitution,
                charisma=10,
                fatigue_roll=7,
                height_roll="3",
                weight_roll="3",
            )
            fatigue, body = character.pools()
            for fatigue_damage in range(0, fatigue + 3):
                for body_damage in range(0, body + 3, 2):
                    character.fatigue_damage = fatigue_damage
                    character.body_damage = body_damage
                    checked += 1
                    if fatigue - fatigue_damage > 0 and body - body_damage > 0:
                        figure = snapshot_character(
                            character,
                            1,
                            display_name="P",
                            archetype="fighter",
                            spells=(),
                            mana=0,
                        )
                        self.assertEqual(
                            injury_penalty(character),
                            -reactions.injury_penalty(figure),
                            (fatigue - fatigue_damage, body - body_damage),
                        )
                        self.assertIsNone(unconscious_refusal(character))
                    else:
                        self.assertIn(
                            "unconscious", unconscious_refusal(character) or ""
                        )
        self.assertGreater(checked, 1000)

    def test_an_unconscious_character_is_refused_a_check_and_an_attack(self) -> None:
        self.character.body_damage = self.character.body
        self.character.save()
        with self.assertRaisesMessage(CharacterChangeError, "is unconscious"):
            skill_check(self.character, "stealth")
        # A Game Master may call it (the second review, under the standing
        # rule), and the line says it was his call; the attack stays refused.
        called = skill_check(
            self.character, "stealth", is_game_master=True, stated_total=7
        )
        self.assertEqual(called.game_master_call, "unconscious")
        self.assertTrue(called.line.endswith(game_master_call_note("unconscious")))
        self.login_as(self.create_dm(username="warden"))
        response = self.post_json(
            reverse("characters:skill_check", args=[self.character.pk]),
            {"skill": "stealth", "total": 7},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["check"]["game_master_call"], "unconscious")
        self.logout()
        with self.assertRaises(attack.AttackValidationError) as refused:
            attack.resolve(
                self.character,
                {"weapon_index": 0, "armour_tier": "None"},
                roller=ScriptedRoller(d20s=[10]),
            )
        self.assertIn("is unconscious", str(refused.exception))
        self.login_as(self.player)
        response = self.post_json(
            reverse("characters:attack", args=[self.character.pk]),
            {"weapon_index": 0, "armour_tier": "None"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("is unconscious", response.json()["error"])

    def test_the_dead_roll_nothing(self) -> None:
        self.character.is_alive = False
        self.character.save()
        with self.assertRaisesMessage(CharacterChangeError, "is dead"):
            skill_check(self.character, "stealth")
        with self.assertRaisesMessage(CharacterChangeError, "is dead"):
            skill_check(self.character, "stealth", is_game_master=True)
