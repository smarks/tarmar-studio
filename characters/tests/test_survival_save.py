"""The survival save off the board, for a dying character (issue #839).

injury-thresholds-death.md: at minus half a pool's starting value the
character makes a survival save (3d6 ≤ CON) every turn, penalized past minus
the full starting value by how far past it is; a failed save is death.
Ruling: a survival save is rolled once, by code, and recorded.
"""

import math
from dataclasses import replace

from django.urls import reverse

from battle.models import Battle
from battle.services import create_battle
from characters import services
from characters.models import Character
from characters.services import (
    NO_SURVIVAL_SAVE_DUE,
    SURVIVAL_SAVE_ALREADY_ROLLED,
    CharacterChangeError,
    roll_survival_save,
    survival_save_refusal,
)
from common.rolling import Roller
from common.test_helpers import BaseTestCase, create_character

SAVE_BUTTON = 'id="roll-survival-save-btn"'


class ThreeFacesRoller(Roller):
    """A dice-module roller whose 3d6 lands on the faces given."""

    def __init__(self, *faces: int) -> None:
        super().__init__(seed=0)
        self.faces_to_give = faces

    def roll(self, specification, *, purpose, **kwargs):
        record = super().roll(specification, purpose=purpose, **kwargs)
        return replace(
            record, faces=tuple(self.faces_to_give), total=sum(self.faces_to_give)
        )


class SurvivalSaveTest(BaseTestCase):
    """The rule, as ``services.roll_survival_save`` rolls it."""

    def setUp(self) -> None:
        super().setUp()
        self.player = self.create_user(username="alice")
        self.character = create_character(self.player, name="Dain", constitution=12)
        body = self.character.body
        assert body is not None
        self.body = body
        # Body at minus half its starting value: the save is due, unpenalized.
        self.dying_at = math.ceil(self.body / 2)
        self.character.body_damage = self.body + self.dying_at
        self.character.save()

    def test_a_save_at_or_under_con_survives_and_is_recorded(self) -> None:
        result = roll_survival_save(
            self.character.pk, actor=self.player, roller=ThreeFacesRoller(4, 4, 4)
        )
        character = result.character
        self.assertTrue(character.is_alive)
        self.assertEqual(
            result.note,
            "survival save, 3d6: [4, 4, 4] = 12 ≤ 12 (CON 12): survives this turn",
        )
        condition = character.condition
        assert condition is not None
        pools = condition.pools
        self.assertIn(
            f"Survival save at {pools}: 3d6: [4, 4, 4] = 12", character.dice_log
        )
        (line,) = [
            entry for entry in character.history if entry["field"] == "survival save"
        ]
        # The pools it was rolled against are kept with it (#839 review).
        self.assertEqual((line["old"], line["new"]), (pools, result.note))
        self.assertEqual(line["by"], "alice")
        self.assertFalse(character.is_played)

    def test_a_failed_save_is_death_on_the_record(self) -> None:
        result = roll_survival_save(
            self.character.pk, actor=self.player, roller=ThreeFacesRoller(6, 4, 3)
        )
        character = result.character
        self.assertFalse(character.is_alive)
        self.assertEqual(character.death_cause, "Failed a survival save (13 > 12)")
        self.assertTrue(result.note.endswith("13 > 12 (CON 12): fails, and dies"))
        death = [entry for entry in character.history if entry["field"] == "is_alive"]
        self.assertEqual(
            [(entry["old"], entry["new"]) for entry in death], [("True", "False")]
        )
        # The dead roll no more saves; only a Game Master's revive brings
        # them back.
        with self.assertRaisesMessage(CharacterChangeError, NO_SURVIVAL_SAVE_DUE):
            roll_survival_save(self.character.pk, actor=self.player)

    def test_past_minus_the_full_pool_the_save_is_penalized(self) -> None:
        self.character.body_damage = self.body * 2 + 3
        self.character.save()
        result = roll_survival_save(
            self.character.pk, actor=self.player, roller=ThreeFacesRoller(3, 3, 3)
        )
        self.assertTrue(result.character.is_alive)
        self.assertIn("9 ≤ 9 (CON 12 -3)", result.note)
        # A second save against the same wounds is the Game Master's to call.
        result = roll_survival_save(
            self.character.pk,
            actor=self.player,
            roller=ThreeFacesRoller(4, 3, 3),
            is_game_master=True,
        )
        self.assertFalse(result.character.is_alive)

    def test_a_player_rolls_once_for_each_state_of_the_pools(self) -> None:
        # The reviewer's probe: three presses were three saves.
        roll_survival_save(
            self.character.pk, actor=self.player, roller=ThreeFacesRoller(1, 1, 1)
        )
        for _press in range(2):
            with self.assertRaisesMessage(
                CharacterChangeError, SURVIVAL_SAVE_ALREADY_ROLLED
            ):
                roll_survival_save(self.character.pk, actor=self.player)
        # A wound taken since (the pools changed) is a new state: a new save.
        Character.objects.filter(pk=self.character.pk).update(
            body_damage=self.body + self.dying_at + 1
        )
        roll_survival_save(
            self.character.pk, actor=self.player, roller=ThreeFacesRoller(1, 1, 1)
        )
        self.character.refresh_from_db()
        saves = [e for e in self.character.history if e["field"] == "survival save"]
        self.assertEqual(len(saves), 2)

    def test_a_game_master_may_call_further_saves(self) -> None:
        for _call in range(2):
            roll_survival_save(
                self.character.pk,
                actor=self.player,
                roller=ThreeFacesRoller(1, 1, 1),
                is_game_master=True,
            )
        self.assertIsNone(
            survival_save_refusal(
                Character.objects.get(pk=self.character.pk), is_game_master=True
            )
        )

    def test_no_save_from_the_sheet_while_the_board_rolls_it(self) -> None:
        battle = create_battle(
            self.create_dm(),
            name="Road",
            count=0,
            seed=1,
            beast_slugs=("gray-wolf",),
            character_ids=(self.character.pk,),
            writes_to_records=True,
        )
        with self.assertRaisesMessage(CharacterChangeError, "is fighting on the board"):
            roll_survival_save(
                self.character.pk, actor=self.player, is_game_master=True
            )
        # Once that fight is over, the sheet rolls it again.
        Battle.objects.filter(pk=battle.pk).update(status=Battle.STATUS_FINISHED)
        roll_survival_save(
            self.character.pk, actor=self.player, roller=ThreeFacesRoller(1, 1, 1)
        )

    def test_a_board_that_writes_nothing_does_not_hold_it(self) -> None:
        create_battle(
            self.player,
            name="Practice",
            count=1,
            seed=1,
            character_ids=(self.character.pk,),
        )
        roll_survival_save(
            self.character.pk, actor=self.player, roller=ThreeFacesRoller(1, 1, 1)
        )

    def test_no_save_is_due_above_minus_half_a_pool(self) -> None:
        self.character.body_damage = self.body
        self.character.save()
        with self.assertRaisesMessage(CharacterChangeError, NO_SURVIVAL_SAVE_DUE):
            roll_survival_save(self.character.pk, actor=self.player)
        incomplete = create_character(self.player, name="Nobody", fatigue_roll=None)
        with self.assertRaisesMessage(CharacterChangeError, NO_SURVIVAL_SAVE_DUE):
            services.apply_survival_save(incomplete)


class SurvivalSaveEndpointTest(BaseTestCase):
    """Who rolls it, and where the sheet offers it."""

    def setUp(self) -> None:
        super().setUp()
        self.player = self.create_user(username="alice")
        self.character = create_character(self.player, name="Dain", constitution=12)
        body = self.character.body
        assert body is not None
        self.character.body_damage = body * 2
        self.character.save()
        self.url = reverse("characters:roll_survival_save", args=[self.character.pk])
        self.sheet_url = reverse("characters:edit", args=[self.character.pk])

    def post_as(self, user):
        self.logout()
        self.login_as(user)
        return self.client.post(self.url, "{}", content_type="application/json")

    def test_the_owner_and_a_game_master_roll_it_and_nobody_else(self) -> None:
        stranger = self.create_user(username="bob")
        refused = self.post_as(stranger)
        self.assertEqual(refused.status_code, 403)
        self.character.refresh_from_db()
        self.assertEqual(self.character.dice_log.count("Survival save"), 0)
        response = self.post_as(self.player)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["success"])
        self.character.refresh_from_db()
        if self.character.is_alive:
            # The player's second press against the same wounds is refused
            # in words; the Game Master may call another.
            again = self.post_as(self.player)
            self.assertEqual(again.status_code, 400)
            self.assertEqual(again.json()["error"], SURVIVAL_SAVE_ALREADY_ROLLED)
            self.assertIn(
                "survival-save-refused",
                self.client.get(self.sheet_url).content.decode(),
            )
            self.assertEqual(self.post_as(self.create_dm()).status_code, 200)
        self.character.refresh_from_db()
        self.assertGreaterEqual(self.character.dice_log.count("Survival save"), 1)

    def test_a_save_not_due_is_refused_in_words(self) -> None:
        self.character.body_damage = 0
        self.character.save()
        response = self.post_as(self.player)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], NO_SURVIVAL_SAVE_DUE)

    def test_the_sheet_offers_it_only_while_it_is_due_and_to_an_editor(self) -> None:
        self.login_as(self.player)
        html = self.client.get(self.sheet_url).content.decode()
        self.assertIn(SAVE_BUTTON, html)
        self.assertIn("Roll the survival save (3d6 &le; 12)", html)
        # A public character's sheet is readable by anyone signed in; its
        # save is not theirs to roll.
        self.character.is_public = True
        self.character.save()
        self.logout()
        self.login_as(self.create_user(username="bob"))
        response = self.client.get(self.sheet_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(SAVE_BUTTON, response.content.decode())
        self.character.body_damage = 0
        self.character.save()
        self.logout()
        self.login_as(self.player)
        self.assertNotIn(SAVE_BUTTON, self.client.get(self.sheet_url).content.decode())
