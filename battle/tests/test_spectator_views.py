"""Spectator flow (#250): any signed-in user may watch, none may touch.

The permissions matrix these tests pin down:

==========  =======================  ==============================
Viewer      May read                 May write
==========  =======================  ==============================
DM          everything               everything (advance, restart,
                                     rename, start, AI hand-off)
Seated      everything but other     their own combatant's pending
player      players' pending menus   choice; their seat's flags
Spectator   arena, roster, log,      nothing
            who is awaited
Anonymous   nothing (login redirect) nothing
==========  =======================  ==============================

"Read" never includes another combatant's pending candidate menu or the
policy's advice scores: ``pending_payload`` strips ``candidates`` for
everyone but the awaited player and the DM, and these tests assert that
shape per role against the shared state endpoint.
"""

import json

from django.urls import reverse

from battle.models import Battle
from battle.services import (
    advance_turn,
    claim_seat,
    create_battle,
    record_player_action,
    set_seat_ready,
    start_battle,
)
from common.test_helpers import BaseTestCase


class SpectatorTestMixin(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.watcher = self.create_user(username="watcher")

    def classic_battle(self, **overrides):
        """A DM-only simulation, running from creation (no lobby, no seats)."""
        params = {"name": "Closed Pit", "count": 2, "seed": 5}
        params.update(overrides)
        return create_battle(self.dm, **params)

    def awaiting_battle(self):
        """A started battle parked on the seated player's pending choice."""
        battle = create_battle(
            self.dm, name="Open Arena", count=1, seed=9, player_seats=1
        )
        seat = battle.combatants.filter(is_open_seat=True).first()
        seat = claim_seat(battle.pk, seat.pk, self.player, archetype="archer")
        set_seat_ready(battle.pk, seat.pk, self.player, ready=True)
        start_battle(battle.pk)
        battle, _events = advance_turn(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        return battle, seat

    def switch_to(self, user):
        """Change users without losing the passphrase gate (see lobby tests)."""
        self.logout()
        self.login_as(user)


class SpectatorReadTest(SpectatorTestMixin):
    def test_anonymous_viewers_cannot_read_a_battle_of_private_characters(self):
        """The read widening of #305 stops where #273 does.

        A DM's battle rolls real ``Character`` rows, private by default,
        and the arena shows a combatant's attributes, weapon and wounds. So
        an anonymous viewer never gets the page: the page sends them to sign
        in, since any signed-in user may read it (issue #339, item 32), and
        the JSON state stays a 404. What they *can* open is covered in
        ``battle.tests.test_guest_play``."""
        battle = self.classic_battle()
        detail_url = reverse("battle:detail", args=[battle.pk])
        response = self.client.get(detail_url)
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={detail_url}",
            fetch_redirect_response=False,
        )
        self.assertNotContains(response, battle.name, status_code=302)
        state = self.client.get(reverse("battle:state", args=[battle.pk]))
        self.assertEqual(state.status_code, 404)

    def test_signing_in_from_the_redirect_lands_on_the_battle(self):
        battle = self.classic_battle()
        detail_url = reverse("battle:detail", args=[battle.pk])
        self.login_as(self.watcher)
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)

    def test_an_unknown_battle_is_still_a_404_when_signed_out(self):
        response = self.client.get(reverse("battle:detail", args=[999999]))
        self.assertEqual(response.status_code, 404)

    def test_spectator_opens_a_running_battle_without_controls(self):
        battle = self.classic_battle()
        self.login_as(self.watcher)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Watching")
        self.assertNotContains(response, "Advance turn")
        self.assertNotContains(response, "data-rename-url")
        self.assertNotContains(response, "battle-start-form")
        self.assertNotContains(response, 'id="restart-battle"')

    def test_seated_player_and_dm_see_no_watching_chip(self):
        battle, _seat = self.awaiting_battle()
        self.login_as(self.player)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertNotContains(response, "Watching")
        self.switch_to(self.dm)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertNotContains(response, "Watching")

    def test_finished_battle_replays_its_log_for_a_spectator(self):
        """A finished battle's whole event log renders from the database —
        the replay is free, so spectators get it (documented in the view).

        Two real turns build the log; the status flips to finished directly,
        because how the engine ends a battle is its own tests' business —
        this one pins down that a finished battle's page still hands a
        spectator every logged turn.
        """
        battle = self.classic_battle(count=2, seed=3)
        battle, _events = advance_turn(battle.pk)
        battle, _events = advance_turn(battle.pk)
        Battle.objects.filter(pk=battle.pk).update(status=Battle.STATUS_FINISHED)
        self.login_as(self.watcher)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Watching")
        self.assertContains(response, 'class="turn-group"')
        self.assertContains(response, 'data-turn="2"')

    def test_spectator_polls_the_state_endpoint_incrementally(self):
        battle, _seat = self.awaiting_battle()
        # The player's recorded choice completes the turn; the spectator's
        # next poll picks the whole turn up through ``?after``.
        chosen = battle.pending_input_json["candidates"][0]
        record_player_action(
            battle.pk,
            self.player,
            combatant_id=battle.pending_input_json["combatant_id"],
            turn=battle.pending_input_json["turn"],
            letter=chosen["letter"],
            target_id=chosen.get("target_id"),
            spell_key=chosen.get("spell_key") or "",
        )
        self.login_as(self.watcher)
        response = self.client.get(
            reverse("battle:state", args=[battle.pk]), {"after": "0"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["turn"], 1)
        self.assertTrue(data["events"])
        top = max(event["sequence"] for event in data["events"])
        response = self.client.get(
            reverse("battle:state", args=[battle.pk]), {"after": str(top)}
        )
        self.assertEqual(response.json()["events"], [])


class ResponseShapePerRoleTest(SpectatorTestMixin):
    """The shared endpoint answers every role, shaped to what they may see."""

    def pending_for(self, battle, user):
        self.switch_to(user)
        response = self.client.get(reverse("battle:state", args=[battle.pk]))
        self.assertEqual(response.status_code, 200)
        return response.json()["pending"]

    def test_candidates_and_advice_reach_only_the_actor_and_the_dm(self):
        battle, seat = self.awaiting_battle()
        spectator_pending = self.pending_for(battle, self.watcher)
        self.assertEqual(spectator_pending["combatant_id"], seat.pk)
        self.assertFalse(spectator_pending["is_you"])
        self.assertEqual(spectator_pending["candidates"], [])

        player_pending = self.pending_for(battle, self.player)
        self.assertTrue(player_pending["is_you"])
        self.assertTrue(player_pending["candidates"])
        # The advice scores ride inside the candidates — present here...
        self.assertIn("score", player_pending["candidates"][0])

        dm_pending = self.pending_for(battle, self.dm)
        self.assertFalse(dm_pending["is_you"])
        self.assertTrue(dm_pending["candidates"])

    def test_spectator_seats_carry_no_is_you_flags(self):
        battle, _seat = self.awaiting_battle()
        self.login_as(self.watcher)
        response = self.client.get(reverse("battle:state", args=[battle.pk]))
        seats = response.json()["seats"]
        self.assertTrue(seats)
        self.assertFalse(any(entry["is_you"] for entry in seats))


class SpectatorWriteRefusalTest(SpectatorTestMixin):
    """Read-only is server-enforced: every mutation refuses a spectator."""

    def test_dm_only_endpoints_refuse_a_spectator(self):
        battle, _seat = self.awaiting_battle()
        self.login_as(self.watcher)
        for route in ("battle:advance", "battle:restart", "battle:start"):
            response = self.client.post(reverse(route, args=[battle.pk]))
            self.assertEqual(response.status_code, 403, route)
        response = self.client.post(
            reverse("battle:rename", args=[battle.pk]),
            json.dumps({"combatant_id": 1, "name": "Nope"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_spectator_may_not_act_for_the_awaited_player(self):
        battle, _seat = self.awaiting_battle()
        chosen = battle.pending_input_json["candidates"][0]
        self.login_as(self.watcher)
        response = self.client.post(
            reverse("battle:act", args=[battle.pk]),
            json.dumps(
                {
                    "combatant_id": battle.pending_input_json["combatant_id"],
                    "turn": battle.pending_input_json["turn"],
                    "letter": chosen["letter"],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("not your combatant", response.json()["error"])

    def test_no_seat_business_once_the_battle_started(self):
        battle, seat = self.awaiting_battle()
        self.login_as(self.watcher)
        claim = self.client.post(
            reverse("battle:seat-claim", args=[battle.pk, seat.pk]),
            {"archetype": "wizard"},
        )
        # A refused form post comes back as the page with the reason on it
        # rather than raw JSON (#339, item 2).
        self.assertContains(claim, "no longer open", status_code=400)
        ready = self.client.post(
            reverse("battle:seat-ready", args=[battle.pk, seat.pk]),
            {"ready": "true"},
        )
        self.assertEqual(ready.status_code, 400)
        release = self.client.post(
            reverse("battle:seat-release", args=[battle.pk, seat.pk])
        )
        self.assertEqual(release.status_code, 400)


class WatchableListTest(SpectatorTestMixin):
    def test_players_find_watchable_battles_with_a_watch_action(self):
        self.classic_battle(name="Running Pit")
        self.login_as(self.watcher)
        response = self.client.get(reverse("battle:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Running Pit")
        self.assertContains(response, ">Watch<")
        self.assertNotContains(response, "New Battle")

    def test_lobbies_show_starting_soon_with_seats_filled(self):
        battle = create_battle(
            self.dm, name="Filling Up", count=1, seed=7, player_seats=2
        )
        seat = battle.combatants.filter(is_open_seat=True).first()
        claim_seat(battle.pk, seat.pk, self.player, archetype="archer")
        self.login_as(self.watcher)
        response = self.client.get(reverse("battle:list"))
        self.assertContains(response, "starting soon")
        self.assertContains(response, "1 of 2 seats claimed")
        self.assertContains(response, ">Join<")

    def test_the_action_verb_follows_the_viewers_stake(self):
        lobby = create_battle(
            self.dm, name="Seated Lobby", count=1, seed=8, player_seats=1
        )
        seat = lobby.combatants.filter(is_open_seat=True).first()
        claim_seat(lobby.pk, seat.pk, self.player, archetype="archer")
        self.login_as(self.player)
        response = self.client.get(reverse("battle:list"))
        self.assertContains(response, ">Play<")
        self.switch_to(self.dm)
        response = self.client.get(reverse("battle:list"))
        self.assertContains(response, ">Open<")
        self.assertNotContains(response, ">Watch<")
