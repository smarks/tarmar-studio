"""The battle pages show the state and offer the controls a user expects.

Issue #339, items 1-8 as they land in the battle app: the nav link, the
refusal that renders as a page rather than as JSON, the seated player's own
control, the DM's release of somebody else's seat, soft delete and its way
back, the ownership verb, the rules profile, and the seat card that says who
actually decides.
"""

from django.urls import reverse
from django.utils.html import escape

from battle.access import Actor
from battle.models import Battle
from battle.persistence import CLASSIC_PROFILE
from battle.refusal_words import refusal_words
from battle.services import claim_seat, create_battle, set_seat_ready, start_battle
from battle.views import seat_summaries
from common.helpers import SCRIPT_CALLER_HEADERS

from .test_lobby_views import LobbyViewTestMixin


class NavigationTest(LobbyViewTestMixin):
    """Item 1: Battles is reachable without the admin menu."""

    def test_a_player_finds_battles_in_the_main_menu(self):
        self.login_as(self.player)
        response = self.client.get(reverse("home"))
        self.assertContains(response, reverse("battle:list"))

    def test_the_home_page_links_to_battles(self):
        self.login_as(self.player)
        response = self.client.get(reverse("home"))
        self.assertContains(response, f'href="{reverse("battle:list")}"')

    def test_a_dm_still_has_the_link(self):
        self.login_as(self.dm)
        response = self.client.get(reverse("home"))
        self.assertContains(response, reverse("battle:list"))


class LobbyRefusalTest(LobbyViewTestMixin):
    """Item 2: a refused lobby post comes back as the page, with the reason."""

    def test_a_refused_ready_re_renders_the_muster(self):
        battle = self.open_battle(seats=2)
        seat = self.open_seat(battle)
        self.login_as(self.player)
        # Nobody is sitting in this seat, so readying it is refused.
        response = self.client.post(
            reverse("battle:seat-ready", args=[battle.pk, seat.pk]),
            {"ready": "true"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, "battle/battle_detail.html")
        self.assertContains(
            response, escape(refusal_words().services.not_your_seat), status_code=400
        )
        # The muster is still there: the reason joined the page rather than
        # replacing it.
        self.assertContains(response, "Muster", status_code=400)

    def test_a_refused_release_re_renders_the_muster(self):
        battle = self.open_battle()
        seat = self.open_seat(battle)
        self.login_as(self.player)
        response = self.client.post(
            reverse("battle:seat-release", args=[battle.pk, seat.pk])
        )
        self.assertContains(
            response,
            escape(refusal_words().services.not_an_occupied_seat),
            status_code=400,
        )
        self.assertContains(response, "Muster", status_code=400)

    def test_an_xhr_caller_still_gets_json(self):
        battle = self.open_battle()
        self.login_as(self.player)
        response = self.client.post(
            reverse("battle:control", args=[battle.pk]),
            {"control_mode": "manual"},
            headers=SCRIPT_CALLER_HEADERS,
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn(refusal_words().views.control_denied, response.json()["error"])


class SeatedPlayerControlTest(LobbyViewTestMixin):
    """Item 3: a seated player may hand their own figure to the AI."""

    def started_with_a_seated_player(self):
        battle = self.open_battle(count=2)
        seat = self.open_seat(battle)
        seat = claim_seat(battle.pk, seat.pk, self.player, archetype="archer")
        set_seat_ready(battle.pk, seat.pk, self.player, ready=True)
        start_battle(battle.pk)
        battle.refresh_from_db()
        return battle, seat

    def test_the_seat_summary_says_its_holder_may_set_control(self):
        battle, seat = self.started_with_a_seated_player()
        summaries = seat_summaries(battle, Actor(user=self.player, guest_key=""))
        mine = next(row for row in summaries if row["combatant_id"] == seat.pk)
        self.assertTrue(mine["is_you"])
        self.assertTrue(mine["can_set_control"])

    def test_a_watcher_is_not_offered_another_players_control(self):
        battle, seat = self.started_with_a_seated_player()
        watcher = self.create_user(username="bystander")
        summaries = seat_summaries(battle, Actor(user=watcher, guest_key=""))
        mine = next(row for row in summaries if row["combatant_id"] == seat.pk)
        self.assertFalse(mine["can_set_control"])

    def test_the_started_page_offers_the_seated_player_their_own_figure(self):
        battle, seat = self.started_with_a_seated_player()
        self.login_as(self.player)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertContains(response, "Your figure")
        self.assertContains(
            response,
            reverse("battle:combatant-control", args=[battle.pk, seat.pk]),
        )
        # The DM's whole desk is still the DM's. (The per-combatant select
        # carries a "Who decides for <name>" label, so the heading is what
        # distinguishes the desk from this one card.)
        self.assertNotContains(response, "<h2>Who decides</h2>")

    def test_the_seated_player_can_hand_the_figure_to_the_ai(self):
        battle, seat = self.started_with_a_seated_player()
        self.login_as(self.player)
        response = self.client.post(
            reverse("battle:combatant-control", args=[battle.pk, seat.pk]),
            {"control_mode": "automatic"},
        )
        self.assertRedirects(response, reverse("battle:detail", args=[battle.pk]))
        seat.refresh_from_db()
        self.assertEqual(seat.control_mode, "automatic")

    def test_a_watcher_sees_no_control_panel_at_all(self):
        battle, _seat = self.started_with_a_seated_player()
        watcher = self.create_user(username="bystander")
        self.login_as(watcher)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertNotContains(response, "Your figure")
        self.assertNotContains(response, "<h2>Who decides</h2>")


class DmReleasesASeatTest(LobbyViewTestMixin):
    """Item 4: the DM can free a seat its holder walked away from."""

    def claimed(self):
        battle = self.open_battle()
        seat = self.open_seat(battle)
        return battle, claim_seat(battle.pk, seat.pk, self.player, archetype="archer")

    def test_the_lobby_offers_the_dm_a_release_for_somebody_elses_seat(self):
        battle, seat = self.claimed()
        self.login_as(self.dm)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertContains(response, "Release this seat")
        self.assertContains(
            response, reverse("battle:seat-release", args=[battle.pk, seat.pk])
        )
        # It asks first: the player loses the figure they brought.
        self.assertContains(response, "loses the figure they brought")

    def test_the_dm_release_frees_the_seat(self):
        battle, seat = self.claimed()
        self.login_as(self.dm)
        response = self.client.post(
            reverse("battle:seat-release", args=[battle.pk, seat.pk])
        )
        self.assertRedirects(response, reverse("battle:detail", args=[battle.pk]))
        seat.refresh_from_db()
        self.assertIsNone(seat.player)

    def test_another_player_is_offered_no_such_thing(self):
        battle, _seat = self.claimed()
        rival = self.create_user(username="rival")
        self.login_as(rival)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertNotContains(response, "Release this seat")

    def test_the_ready_toggle_stays_the_holders_alone(self):
        battle, seat = self.claimed()
        self.login_as(self.dm)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertNotContains(
            response, reverse("battle:seat-ready", args=[battle.pk, seat.pk])
        )


class BattleDeleteTest(LobbyViewTestMixin):
    """Item 5: a battle can be deleted, and the delete can be undone."""

    def test_the_dm_deletes_a_battle_and_it_leaves_the_list(self):
        battle = self.open_battle()
        self.login_as(self.dm)
        response = self.client.post(reverse("battle:delete", args=[battle.pk]))
        self.assertRedirects(response, reverse("battle:list"))
        battle.refresh_from_db()
        self.assertTrue(battle.is_deleted)
        self.assertIsNotNone(battle.deleted_at)
        listed = self.client.get(reverse("battle:list"))
        self.assertNotContains(listed, "Open Arena")

    def test_the_delete_is_soft_the_log_and_the_page_survive(self):
        battle = self.open_battle()
        self.login_as(self.dm)
        self.client.post(reverse("battle:delete", args=[battle.pk]))
        page = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertContains(page, "This battle is deleted")
        self.assertContains(page, "Restore this battle")
        self.assertTrue(Battle.objects.filter(pk=battle.pk).exists())

    def test_the_deleted_bucket_lists_it_with_a_way_back(self):
        battle = self.open_battle()
        self.login_as(self.dm)
        self.client.post(reverse("battle:delete", args=[battle.pk]))
        listed = self.client.get(reverse("battle:list"), {"show": "deleted"})
        self.assertContains(listed, "Open Arena")
        self.assertContains(listed, "Restore")

    def test_restoring_puts_it_back(self):
        battle = self.open_battle()
        self.login_as(self.dm)
        self.client.post(reverse("battle:delete", args=[battle.pk]))
        response = self.client.post(reverse("battle:restore", args=[battle.pk]))
        self.assertRedirects(response, reverse("battle:detail", args=[battle.pk]))
        battle.refresh_from_db()
        self.assertFalse(battle.is_deleted)
        self.assertIsNone(battle.deleted_at)
        self.assertContains(self.client.get(reverse("battle:list")), "Open Arena")

    def test_somebody_elses_battle_is_not_yours_to_delete(self):
        battle = self.open_battle()
        self.login_as(self.player)
        response = self.client.post(reverse("battle:delete", args=[battle.pk]))
        self.assertEqual(response.status_code, 404)
        battle.refresh_from_db()
        self.assertFalse(battle.is_deleted)

    def test_deleting_twice_is_a_404_not_a_second_write(self):
        battle = self.open_battle()
        self.login_as(self.dm)
        self.client.post(reverse("battle:delete", args=[battle.pk]))
        battle.refresh_from_db()
        stamped = battle.deleted_at
        again = self.client.post(reverse("battle:delete", args=[battle.pk]))
        self.assertEqual(again.status_code, 404)
        battle.refresh_from_db()
        self.assertEqual(battle.deleted_at, stamped)

    def test_a_player_deletes_and_restores_the_battle_they_made(self):
        battle = create_battle(self.player, name="My Own Pit", count=2, seed=3)
        self.login_as(self.player)
        self.client.post(reverse("battle:delete", args=[battle.pk]))
        battle.refresh_from_db()
        self.assertTrue(battle.is_deleted)
        self.client.post(reverse("battle:restore", args=[battle.pk]))
        battle.refresh_from_db()
        self.assertFalse(battle.is_deleted)

    def test_the_status_filter_narrows_the_list(self):
        self.open_battle()
        create_battle(self.dm, name="Fought Already", count=2, seed=4)
        self.login_as(self.dm)
        lobbies = self.client.get(reverse("battle:list"), {"show": "lobby"})
        self.assertContains(lobbies, "Open Arena")
        self.assertNotContains(lobbies, "Fought Already")

    def test_a_junk_filter_falls_back_to_every_battle(self):
        self.open_battle()
        self.login_as(self.dm)
        listed = self.client.get(reverse("battle:list"), {"show": "nonsense"})
        self.assertContains(listed, "Open Arena")


class ListVerbTest(LobbyViewTestMixin):
    """Item 6: the row's verb knows who runs the battle."""

    def test_a_player_opens_the_battle_they_made(self):
        create_battle(self.player, name="My Own Pit", count=2, seed=3)
        self.login_as(self.player)
        response = self.client.get(reverse("battle:list"))
        self.assertContains(response, ">Open<")
        self.assertNotContains(response, ">Watch<")

    def test_somebody_elses_finished_battle_is_still_a_watch(self):
        create_battle(self.dm, name="Their Pit", count=2, seed=3)
        self.login_as(self.player)
        response = self.client.get(reverse("battle:list"))
        self.assertContains(response, ">Watch<")

    def test_a_dm_opens_everything(self):
        self.open_battle()
        self.login_as(self.dm)
        response = self.client.get(reverse("battle:list"))
        self.assertContains(response, ">Open<")


class RulesProfileVisibleTest(LobbyViewTestMixin):
    """Item 7: which rules a fight runs under, on both shapes of the page."""

    def test_the_started_page_names_the_rules(self):
        battle = self.open_battle(count=2, rules_profile=CLASSIC_PROFILE)
        start_battle(battle.pk)
        self.login_as(self.dm)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        battle.refresh_from_db()
        self.assertContains(response, 'id="battle-rules"')
        self.assertContains(response, battle.get_rules_profile_display())

    def test_the_list_carries_a_rules_column(self):
        battle = self.open_battle(rules_profile=CLASSIC_PROFILE)
        self.login_as(self.dm)
        response = self.client.get(reverse("battle:list"))
        self.assertContains(response, "<th>Rules</th>")
        self.assertContains(response, battle.get_rules_profile_display())


class SeatControlCopyTest(LobbyViewTestMixin):
    """Item 8: the lobby says who decides, rather than assuming the AI."""

    def test_a_manual_battle_does_not_claim_the_ai_fights_it(self):
        battle = self.open_battle(count=2)
        battle.control_mode = "manual"
        battle.save(update_fields=["control_mode"])
        self.login_as(self.dm)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertNotContains(response, "the AI fights this one")
        self.assertContains(response, "Manual — played by")

    def test_an_automatic_battle_says_so_in_the_lede(self):
        battle = self.open_battle(count=2)
        self.login_as(self.player)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertContains(response, "Unless a combatant says otherwise")
        self.assertContains(response, battle.get_control_mode_display())

    def test_a_seat_card_carries_the_servers_control_line(self):
        battle = self.open_battle(count=2)
        self.login_as(self.dm)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertContains(response, "lobby-seat-control")
        self.assertContains(response, "Automatic — the AI plays it")
