"""The character's dice log keeps every roll the sheet makes (issue #147).

``Character.dice_log`` is where ``services.append_dice_log`` says every roll
is kept, and the skill check, the attribute check, the sheet attack, the
sheet's Fatigue roll and its literacy roll each rolled and kept their dice
only in the history line or the response. Each now appends one line per roll,
"Label: NdS: [faces] = total", for a player and a Game Master alike; a total
a Game Master states from his table stays in the history only, as a stated
cast's does.
"""

from unittest.mock import patch

from django.urls import reverse

from characters import services
from characters.dice import (
    ATTACK_CONFIRM_PART,
    ATTACK_FUMBLE_PART,
    ATTACK_LOG_LABEL,
    ATTACK_TO_HIT_PART,
    ATTRIBUTE_CHECK_LOG_LABEL,
    D20_DICE,
    DICE_IN_TEXT,
    FATIGUE_LOG_LABEL,
    LITERACY_LOG_LABEL,
    SKILL_CHECK_LOG_LABEL,
)
from characters.models import Character
from characters.skill_checks import attribute_check
from characters.skills import points_for_level
from common.test_helpers import BaseTestCase, FacesRoller, create_character

from .test_attack_endpoint import CHARACTER_DEFAULTS, VALID_ATTACK
from .test_point_buy import CREATE_URL, buy_post


def log_lines(character: Character) -> list[str]:
    """The character's saved dice log, line by line."""
    character.refresh_from_db()
    return str(character.dice_log or "").splitlines()


class SkillCheckDiceLogTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("log-checker")
        self.character = create_character(
            self.owner, name="Logger", skills={"stealth": points_for_level(2)}
        )
        self.url = reverse("characters:skill_check", args=[self.character.pk])

    def assert_check_logged(self, response) -> None:
        self.assertEqual(response.status_code, 200, response.content)
        check = response.json()["check"]
        faces = ", ".join(str(face) for face in check["faces"])
        label = SKILL_CHECK_LOG_LABEL.format(skill="stealth")
        expected = f"{label}: 2d6: [{faces}] = {check['dice_total']}"
        lines = log_lines(self.character)
        self.assertEqual(lines[-1], expected)
        # The sheet's Dice Log repaints from the answer, without a reload.
        self.assertEqual(response.json()["dice_log"], self.character.dice_log)
        self.assertTrue(DICE_IN_TEXT.search(lines[-1]))

    def test_a_players_check_is_kept_in_the_dice_log(self):
        self.login_as(self.owner)
        before = len(log_lines(self.character))

        self.assert_check_logged(self.post_json(self.url, {"skill": "Stealth"}))
        self.assertEqual(len(log_lines(self.character)), before + 1)

    def test_a_game_masters_check_is_kept_too(self):
        self.login_as(self.create_dm("log-gm"))

        self.assert_check_logged(
            self.post_json(self.url, {"skill": "stealth", "difficulty": "9"})
        )

    def test_a_total_stated_from_the_table_is_in_the_history_only(self):
        self.login_as(self.create_dm("log-stating-gm"))
        before = log_lines(self.character)

        response = self.post_json(self.url, {"skill": "stealth", "total": "9"})

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(log_lines(self.character), before)
        self.assertIn("stated by a Game Master", self.character.history[-1]["new"])

    def test_a_skills_name_in_the_log_never_reads_as_a_point_buy(self):
        """A Maker Skill's craft is the player's words, and the skill check's
        line carries the name: only a bought score's own line is a buy, the
        sheet's (under the field name) and the creation page's (under the
        short label) alike."""
        label = SKILL_CHECK_LOG_LABEL.format(skill="maker skill (point-buy = 3)")
        self.character.dice_log = f"{label}: 2d6: [1, 1] = 2"
        self.assertFalse(services.bought_scores(self.character))
        for bought in (
            "strength: point-buy = 12 (typed on the sheet)",
            "STR: point-buy = 14",
        ):
            with self.subTest(line=bought):
                self.character.dice_log = f"{label}: 2d6: [1, 1] = 2\n{bought}"
                self.assertTrue(services.bought_scores(self.character))


class BoughtAtCreationTest(BaseTestCase):
    """The review's reproduction: a character bought on the creation page is
    held to the buy, as one bought on the sheet is."""

    def test_a_player_may_not_roll_a_score_of_a_character_bought_at_creation(self):
        owner = self.create_user("log-buyer")
        self.login_as(owner)
        self.client.post(CREATE_URL, buy_post())
        character = Character.objects.get(user=owner)
        self.assertTrue(services.bought_scores(character))
        Character.objects.filter(pk=character.pk).update(strength=None)

        response = self.client.post(
            reverse("characters:roll", args=[character.pk, "attributes"])
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["error"], services.bought_not_rolled_reason())
        character.refresh_from_db()
        self.assertIsNone(character.strength)


class AttributeCheckDiceLogTest(BaseTestCase):
    def test_the_check_is_saved_with_the_change_that_rolled_it(self):
        character = create_character(self.create_user("log-attribute"), strength=12)

        with services.changing(character.pk) as change:
            attribute_check(change.character, "strength", roller=FacesRoller(2, 3, 4))

        label = ATTRIBUTE_CHECK_LOG_LABEL.format(attribute="strength")
        self.assertEqual(log_lines(character)[-1], f"{label}: 3d6: [2, 3, 4] = 9")

    def test_a_stated_total_is_not_logged(self):
        character = create_character(self.create_user("log-stated"), strength=12)

        with services.changing(character.pk) as change:
            attribute_check(
                change.character, "strength", stated_total=9, is_game_master=True
            )

        label = ATTRIBUTE_CHECK_LOG_LABEL.format(attribute="strength")
        self.assertNotIn(label, "\n".join(log_lines(character)))


class SheetAttackDiceLogTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("log-duellist")
        self.character = Character.objects.create(
            user=self.owner,
            name="Log Duellist",
            skills={"weapon (saber)": 10},
            equipment=[{"item_id": "saber", "quantity": 1}],
            **CHARACTER_DEFAULTS,
        )
        self.url = f"/tarmar/characters/{self.character.pk}/attack/"

    def label(self, weapon: str, part: str) -> str:
        return ATTACK_LOG_LABEL.format(weapon=weapon, part=part)

    def attack(self, d20_faces: list[int]):
        with patch("characters.dice.roll_d20", side_effect=d20_faces):
            return self.post_json(self.url, dict(VALID_ATTACK))

    def test_a_hit_keeps_the_d20_and_the_damage_dice(self):
        self.login_as(self.owner)

        response = self.attack([12])

        self.assertEqual(response.status_code, 200, response.content)
        attack = response.json()["attack"]
        self.assertTrue(attack["attack_roll"]["hit"])
        weapon = attack["weapon"]["name"]
        damage = ", ".join(str(face) for face in attack["damage"]["rolls"])
        expression = attack["weapon"]["damage_expression"].lower()
        lines = log_lines(self.character)
        self.assertEqual(
            lines[-2:],
            [
                f"{self.label(weapon, ATTACK_TO_HIT_PART)}: 1d20: [12] = 12",
                f"{self.label(weapon, 'damage')}: {expression}: [{damage}] = "
                f"{attack['damage']['raw_total']}",
            ],
        )
        self.assertEqual(response.json()["dice_log"], self.character.dice_log)
        # The panel reads the die as its face; the stored log keeps its shape.
        self.assertTrue(
            attack["dice_log"][0].startswith(f"attack {D20_DICE}: 12 "),
            attack["dice_log"][0],
        )

    def test_a_natural_twenty_keeps_its_confirm_roll(self):
        self.login_as(self.create_dm("log-attack-gm"))

        response = self.attack([20, 15])

        self.assertEqual(response.status_code, 200, response.content)
        weapon = response.json()["attack"]["weapon"]["name"]
        lines = log_lines(self.character)
        self.assertIn(
            f"{self.label(weapon, ATTACK_TO_HIT_PART)}: 1d20: [20] = 20", lines
        )
        self.assertIn(
            f"{self.label(weapon, ATTACK_CONFIRM_PART)}: 1d20: [15] = 15", lines
        )

    def test_a_natural_one_keeps_its_fumble_roll(self):
        self.login_as(self.owner)

        with patch("characters.dice.roll_d6", return_value=4):
            response = self.attack([1])

        self.assertEqual(response.status_code, 200, response.content)
        weapon = response.json()["attack"]["weapon"]["name"]
        self.assertEqual(
            log_lines(self.character)[-2:],
            [
                f"{self.label(weapon, ATTACK_TO_HIT_PART)}: 1d20: [1] = 1",
                f"{self.label(weapon, ATTACK_FUMBLE_PART)}: 1d6: [4] = 4",
            ],
        )


class SheetBackgroundRollsDiceLogTest(BaseTestCase):
    """The sweep: the sheet's Fatigue and literacy rolls skipped the log too."""

    def setUp(self):
        super().setUp()
        self.character = create_character(
            self.create_user("log-background"), location="Village"
        )

    @patch("characters.services.roll_expression", return_value=([3, 4], 7))
    def test_the_fatigue_roll_is_kept(self, _roll):
        services.roll_fatigue(self.character.pk, is_game_master=True)

        self.assertEqual(
            log_lines(self.character)[-1], f"{FATIGUE_LOG_LABEL}: 2d6: [3, 4] = 7"
        )

    def test_a_stated_fatigue_roll_is_not(self):
        before = log_lines(self.character)

        services.roll_fatigue(self.character.pk, value=9, is_game_master=True)

        self.assertEqual(log_lines(self.character), before)

    def test_the_literacy_roll_is_kept(self):
        Character.objects.filter(pk=self.character.pk).update(
            literacy_roll=None, literacy_rolled=False, location_recorded=True
        )
        with patch(
            "characters.services.roll_literacy_logged",
            return_value=(9, "3d6: [3, 3, 3] = 9"),
        ):
            services.roll_literacy(self.character.pk)

        self.assertEqual(
            log_lines(self.character)[-1], f"{LITERACY_LOG_LABEL}: 3d6: [3, 3, 3] = 9"
        )
