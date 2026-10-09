"""Whether a battle counts is chosen and shown (#750, #751, second review).

Coordinator's ruling under the standing rule (Spencer may overrule): a Game
Master's battle writing to players' records must be visible and chosen. The
setup page offers a Game Master "This battle counts", on by default; a player
is not offered it and their battle never counts; the lobby and the battle page
say so whenever it is on; and the history names who made the battle.
"""

from django.urls import reverse

from battle.models import Battle, BattleDraft
from battle.services import advance_turn, claim_seat, create_battle, set_seat_ready
from characters.models import Character
from common.test_helpers import BaseTestCase, create_character

COUNTS_LINE = "This battle counts:"
TURN_LIMIT = 60


class SetupChoiceTest(BaseTestCase):
    def start(self, maker, **terms):
        draft = BattleDraft.objects.create(created_by=maker, name="")
        self.login_as(maker)
        page = self.client.get(reverse("battle:setup", args=[draft.pk]))
        self.client.post(
            reverse("battle:setup-add", args=[draft.pk]),
            {"kind": "fighter", "archetype": "archer"},
        )
        self.client.post(
            reverse("battle:setup-add", args=[draft.pk]),
            {"kind": "fighter", "archetype": "archer"},
        )
        self.client.post(
            reverse("battle:setup-start", args=[draft.pk]), {"name": "Pit", **terms}
        )
        return page, Battle.objects.get(name="Pit", created_by=maker)

    def test_a_game_master_is_offered_it_checked_and_it_follows_the_box(self):
        dm = self.create_dm()
        page, battle = self.start(dm, writes_to_records="true")
        self.assertContains(page, 'name="writes_to_records"')
        self.assertContains(page, "This battle counts: wounds and deaths go on records")
        self.assertIn(
            "checked", page.content.decode().split('name="writes_to_records"')[1][:200]
        )
        self.assertTrue(battle.writes_to_records)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertContains(response, COUNTS_LINE)

    def test_a_game_master_who_unticks_it_gets_a_practice_fight(self):
        _page, battle = self.start(self.create_dm())
        self.assertFalse(battle.writes_to_records)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertNotContains(response, COUNTS_LINE)

    def test_a_player_is_not_offered_it_and_cannot_post_it(self):
        page, battle = self.start(self.create_user("player"), writes_to_records="true")
        self.assertNotContains(page, 'name="writes_to_records"')
        self.assertFalse(battle.writes_to_records)


class CountsLineAndMakerTest(BaseTestCase):
    def test_the_lobby_says_so_and_the_history_names_the_maker(self):
        dm = self.create_dm("grimwald")
        player = self.create_user("player")
        character = create_character(player, name="Aldwin")
        battle = create_battle(
            dm, name="Held", count=1, seed=3, character_ids=(character.pk,)
        )
        lobby = create_battle(dm, name="Open", count=1, seed=4, player_seats=1)
        self.login_as(player)
        for shown in (battle, lobby):
            response = self.client.get(reverse("battle:detail", args=[shown.pk]))
            self.assertContains(response, COUNTS_LINE)
        for _ in range(TURN_LIMIT):
            battle, _events = advance_turn(battle.pk)
            if battle.status in (Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE):
                break
        character.refresh_from_db()
        self.assertTrue(character.history)
        self.assertEqual({entry["by"] for entry in character.history}, {"grimwald"})


class DeadSeatHoldsTheStartTest(BaseTestCase):
    def test_the_last_ready_saves_and_the_lobby_names_the_seat(self):
        dm = self.create_dm()
        player, other = self.create_user("player"), self.create_user("other")
        doomed = create_character(player, name="Doomed")
        steady = create_character(other, name="Steady")
        battle = create_battle(
            dm, name="Open", count=1, seed=4, player_seats=2, auto_start=True
        )
        first, second = battle.combatants.filter(is_open_seat=True).order_by("pk")
        claim_seat(battle.pk, first.pk, player, character_id=doomed.pk)
        claim_seat(battle.pk, second.pk, other, character_id=steady.pk)
        set_seat_ready(battle.pk, first.pk, player, ready=True)
        Character.objects.filter(pk=doomed.pk).update(
            is_alive=False, death_cause="a fall"
        )
        # The last player's ready is theirs to give; it saves.
        battle, seat = set_seat_ready(battle.pk, second.pk, other, ready=True)
        seat.refresh_from_db()
        self.assertTrue(seat.is_ready)
        battle.refresh_from_db()
        self.assertEqual(battle.status, Battle.STATUS_LOBBY)
        self.login_as(other)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertContains(response, 'id="lobby-blocked"')
        self.assertContains(response, "<strong>Doomed</strong>")
