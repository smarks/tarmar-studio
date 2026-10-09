"""A Games Master session's fight: the foes belong to the Game Master (#762).

Coordinator's ruling under the standing rule (players held to the rules, the
Game Master decides); Spencer may overrule. The player who started the fight
made the battle, and the maker's powers used to reach the side that is not
theirs: restart it, set the foes to manual and choose their moves. The player
still advances the fight and plays their own seats.
"""

from battle import control
from battle.access import Actor, can_control, commands_foes, decides_for
from battle.models import Battle
from battle.services import create_battle
from common.test_helpers import BaseTestCase, create_character


class GameMasterFoesTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.player = self.create_user("player")
        self.dm = self.create_dm()
        character = create_character(self.player, name="Aldwin")
        self.battle = create_battle(
            self.player,
            name="Quest: fight",
            count=1,
            seed=5,
            character_ids=(character.pk,),
            character_teams=("Party",),
            fighter_teams=("Foes",),
        )
        self.seat = self.battle.combatants.get(character=character)
        self.battle.combatants.filter(pk=self.seat.pk).update(
            player=self.player, control_mode=control.MANUAL
        )
        self.seat.refresh_from_db()
        self.foe = self.battle.combatants.get(is_saved_character=False)
        Battle.objects.filter(pk=self.battle.pk).update(foes_belong_to_game_master=True)
        self.battle.refresh_from_db()
        self.login_as(self.player)

    def test_the_rule_in_one_place(self):
        player, dm = Actor(self.player, ""), Actor(self.dm, "")
        self.assertTrue(can_control(player, self.battle))
        self.assertFalse(commands_foes(player, self.battle))
        self.assertFalse(decides_for(player, self.battle, self.foe))
        self.assertTrue(decides_for(player, self.battle, self.seat))
        self.assertTrue(commands_foes(dm, self.battle))
        self.assertTrue(decides_for(dm, self.battle, self.foe))
        # A battle the player built for themself keeps the maker's powers.
        Battle.objects.filter(pk=self.battle.pk).update(
            foes_belong_to_game_master=False
        )
        self.battle.refresh_from_db()
        self.assertTrue(commands_foes(player, self.battle))
        self.assertTrue(decides_for(player, self.battle, self.foe))

    def test_the_player_cannot_take_the_foes_over(self):
        response = self.client.post(
            f"/tarmar/battles/{self.battle.pk}/combatants/{self.foe.pk}/control/",
            {"control_mode": control.MANUAL},
        )
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "belong to the Game Master", status_code=403)
        self.foe.refresh_from_db()
        self.assertEqual(self.foe.control_mode, control.INHERIT)

    def test_the_player_cannot_change_the_battles_default(self):
        response = self.client.post(
            f"/tarmar/battles/{self.battle.pk}/control/",
            {"control_mode": control.MANUAL, "apply_to_all": "true"},
        )
        self.assertEqual(response.status_code, 403)
        self.battle.refresh_from_db()
        self.assertEqual(self.battle.control_mode, control.AUTOMATIC)

    def test_the_player_cannot_restart_it(self):
        response = self.client.post(f"/tarmar/battles/{self.battle.pk}/restart/")
        self.assertEqual(response.status_code, 403)

    def test_the_player_still_advances_it_and_hands_their_own_seat_to_the_ai(self):
        response = self.client.post(f"/tarmar/battles/{self.battle.pk}/advance/")
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            f"/tarmar/battles/{self.battle.pk}/combatants/{self.seat.pk}/control/",
            {"control_mode": control.AUTOMATIC},
        )
        self.assertEqual(response.status_code, 302)
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.control_mode, control.AUTOMATIC)

    def test_the_page_offers_the_player_no_foe_controls(self):
        response = self.client.get(f"/tarmar/battles/{self.battle.pk}/")
        self.assertContains(response, 'id="advance-turn"')
        self.assertNotContains(response, 'id="restart-battle"')
        self.assertNotContains(response, 'id="battle-control"')
        seats = {seat["combatant_id"]: seat for seat in response.context["seats"]}
        self.assertFalse(seats[self.foe.pk]["can_set_control"])
        self.assertFalse(seats[self.foe.pk]["you_decide"])
        self.assertTrue(seats[self.seat.pk]["can_set_control"])

    def test_a_game_master_commands_them(self):
        self.login_as(self.dm)
        response = self.client.post(
            f"/tarmar/battles/{self.battle.pk}/combatants/{self.foe.pk}/control/",
            {"control_mode": control.MANUAL},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(f"/tarmar/battles/{self.battle.pk}/restart/")
        self.assertEqual(response.status_code, 200)
        response = self.client.get(f"/tarmar/battles/{self.battle.pk}/")
        self.assertContains(response, 'id="battle-control"')
        self.assertContains(response, 'id="restart-battle"')


class GameMasterFoesRenameAndCardsTest(BaseTestCase):
    """Renaming, and one card per seated figure, in a session's fight (#762)."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("player")
        party = [
            create_character(self.player, name=name) for name in ("Aldwin", "Mira")
        ]
        self.battle = create_battle(
            self.player,
            name="Quest: fight",
            count=1,
            seed=5,
            character_ids=tuple(character.pk for character in party),
            character_teams=("Party", "Party"),
            fighter_teams=("Foes",),
        )
        self.battle.combatants.filter(character__in=party).update(
            player=self.player, control_mode=control.MANUAL
        )
        Battle.objects.filter(pk=self.battle.pk).update(foes_belong_to_game_master=True)
        self.foe = self.battle.combatants.get(is_saved_character=False)
        self.aldwin = self.battle.combatants.get(character=party[0])
        self.login_as(self.player)

    def rename(self, combatant_id: int, name: str):
        return self.post_json(
            f"/tarmar/battles/{self.battle.pk}/rename/",
            {"combatant_id": combatant_id, "name": name},
        )

    def test_the_player_renames_their_own_figure_and_not_a_foe(self):
        response = self.rename(self.foe.pk, "Pup")
        self.assertEqual(response.status_code, 403)
        self.foe.refresh_from_db()
        self.assertNotEqual(self.foe.display_name, "Pup")
        response = self.rename(self.aldwin.pk, "Aldwin the Bold")
        self.assertEqual(response.status_code, 200, response.content)
        seats = {
            seat["combatant_id"]: seat
            for seat in self.client.get(f"/tarmar/battles/{self.battle.pk}/").context[
                "seats"
            ]
        }
        self.assertFalse(seats[self.foe.pk]["can_rename"])
        self.assertTrue(seats[self.aldwin.pk]["can_rename"])

    def test_every_party_figure_gets_its_card(self):
        response = self.client.get(f"/tarmar/battles/{self.battle.pk}/")
        self.assertContains(response, "Your figures")
        self.assertEqual(len(response.context["my_seat_summaries"]), 2)
        self.assertContains(
            response, 'class="control-combatant" data-combatant=', count=2
        )
