"""Lobby and remote-play endpoints (#250): the who-may-do-what matrix."""

import json

from django.urls import reverse

from battle.models import Battle
from battle.services import (
    advance_turn,
    claim_seat,
    create_battle,
    set_seat_ready,
    start_battle,
)
from common.test_helpers import BaseTestCase


class LobbyViewTestMixin(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()

    def open_battle(
        self, *, seats=1, count=1, auto_start=False, seed=9, rules_profile=None
    ):
        return create_battle(
            self.dm,
            name="Open Arena",
            count=count,
            seed=seed,
            player_seats=seats,
            auto_start=auto_start,
            **({"rules_profile": rules_profile} if rules_profile else {}),
        )

    def open_seat(self, battle):
        return battle.combatants.filter(is_open_seat=True, player=None).first()

    def switch_to(self, user):
        """Change the logged-in user without losing the passphrase gate.

        Logging a *different* user into the same client flushes the session
        (Django's fixation guard), passphrase flag included; ``logout``
        re-seeds the flag first.
        """
        self.logout()
        self.login_as(user)


class LobbyPageTest(LobbyViewTestMixin):
    def test_open_lobby_is_visible_to_any_player(self):
        battle = self.open_battle()
        self.login_as(self.player)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Open seat 1")
        self.assertContains(response, "Claim this seat")

    def test_the_lede_invites_a_claim_while_a_seat_is_open(self):
        battle = self.open_battle()
        self.login_as(self.player)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertContains(response, "Claim an open seat")

    def test_the_lede_says_every_seat_is_taken_once_none_is_open(self):
        """It used to invite a claim with no seat left (issue #339, item 25)."""
        battle = self.open_battle()
        claim_seat(
            battle.pk, self.open_seat(battle).pk, self.player, archetype="archer"
        )
        outsider = self.create_user(username="latecomer")
        self.login_as(outsider)

        response = self.client.get(reverse("battle:detail", args=[battle.pk]))

        self.assertContains(response, "Every seat is taken")
        self.assertNotContains(response, "Claim an open seat")

    def test_a_game_master_is_offered_an_open_seat(self):
        """The claim form was hidden from a GM though the server allowed it (issue #339, item 26)."""
        other_dm = self.create_dm(username="visiting_gm")
        battle = self.open_battle()
        self.login_as(other_dm)

        response = self.client.get(reverse("battle:detail", args=[battle.pk]))

        self.assertContains(response, "Claim this seat")

    def test_a_game_master_can_claim_the_seat(self):
        other_dm = self.create_dm(username="visiting_gm")
        battle = self.open_battle()
        seat = self.open_seat(battle)
        self.login_as(other_dm)

        response = self.client.post(
            reverse("battle:seat-claim", args=[battle.pk, seat.pk]),
            {"archetype": "archer"},
        )

        self.assertEqual(response.status_code, 302)
        seat.refresh_from_db()
        self.assertEqual(seat.player, other_dm)

    def test_open_lobby_appears_on_a_players_list(self):
        battle = self.open_battle()
        self.login_as(self.player)
        response = self.client.get(reverse("battle:list"))
        self.assertContains(response, battle.name)

    def test_seated_player_keeps_access_after_start(self):
        battle = self.open_battle()
        seat = self.open_seat(battle)
        seat = claim_seat(battle.pk, seat.pk, self.player, archetype="archer")
        set_seat_ready(battle.pk, seat.pk, self.player, ready=True)
        start_battle(battle.pk)
        self.login_as(self.player)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertEqual(response.status_code, 200)
        # An outsider gets in too — as a spectator, watching read-only.
        outsider = self.create_user(username="outsider")
        self.switch_to(outsider)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Watching")

    def test_dm_creates_an_open_battle_through_the_setup_flow(self):
        from battle.models import BattleDraft

        self.login_as(self.dm)
        draft = BattleDraft.objects.create(created_by=self.dm)
        add_url = reverse("battle:setup-add", args=[draft.pk])
        self.client.post(add_url, {"kind": "fighter", "archetype": "archer"})
        for _seat in range(2):
            self.client.post(add_url, {"kind": "open_seat"})
        response = self.client.post(
            reverse("battle:setup-start", args=[draft.pk]),
            {"name": "Form Lobby", "seed": "4", "auto_start": "on"},
        )
        battle = Battle.objects.get(name="Form Lobby")
        self.assertRedirects(response, reverse("battle:detail", args=[battle.pk]))
        self.assertEqual(battle.status, Battle.STATUS_LOBBY)
        self.assertTrue(battle.auto_start)
        self.assertEqual(battle.combatants.filter(is_open_seat=True).count(), 2)


class LobbyRulesNoticeTest(LobbyViewTestMixin):
    """The muster names the rules being joined (#251).

    A lobby is where a player chooses what to bring, and under classic rules
    what they bring is rebuilt when the battle starts. The profile is
    therefore part of the offer, not a detail discoverable afterwards from
    the crossing log.
    """

    def test_a_classic_lobby_says_what_the_crossing_will_do(self):
        from battle.persistence import CLASSIC_PROFILE

        battle = self.open_battle(rules_profile=CLASSIC_PROFILE)
        self.login_as(self.player)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertContains(response, "Classic Melee")
        self.assertContains(response, "crosses into classic figures")

    def test_a_tarmar_lobby_names_its_own_rules_and_promises_no_crossing(self):
        battle = self.open_battle()
        self.login_as(self.player)
        response = self.client.get(reverse("battle:detail", args=[battle.pk]))
        self.assertContains(response, "Tarmar")
        self.assertNotContains(response, "crosses into classic figures")


class SeatEndpointTest(LobbyViewTestMixin):
    def test_player_claims_readies_and_releases(self):
        battle = self.open_battle(seats=2)
        seat = self.open_seat(battle)
        self.login_as(self.player)
        response = self.client.post(
            reverse("battle:seat-claim", args=[battle.pk, seat.pk]),
            {"archetype": "archer"},
        )
        self.assertRedirects(response, reverse("battle:detail", args=[battle.pk]))
        seat.refresh_from_db()
        self.assertEqual(seat.player, self.player)

        response = self.client.post(
            reverse("battle:seat-ready", args=[battle.pk, seat.pk]),
            {"ready": "true"},
        )
        self.assertRedirects(response, reverse("battle:detail", args=[battle.pk]))
        seat.refresh_from_db()
        self.assertTrue(seat.is_ready)

        response = self.client.post(
            reverse("battle:seat-release", args=[battle.pk, seat.pk])
        )
        self.assertRedirects(response, reverse("battle:detail", args=[battle.pk]))
        seat.refresh_from_db()
        self.assertIsNone(seat.player)

    def test_claiming_a_taken_seat_reports_the_error(self):
        battle = self.open_battle()
        seat = self.open_seat(battle)
        claim_seat(battle.pk, seat.pk, self.player, archetype="archer")
        rival = self.create_user(username="rival")
        self.login_as(rival)
        response = self.client.post(
            reverse("battle:seat-claim", args=[battle.pk, seat.pk]),
            {"archetype": "wizard"},
        )
        # The lobby's controls are plain forms, so a refusal comes back as
        # the page with the reason on it, never as raw JSON (#339, item 2).
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "already taken", status_code=400)
        self.assertTemplateUsed(response, "battle/battle_detail.html")

    def test_only_a_dm_starts_the_battle(self):
        battle = self.open_battle()
        seat = self.open_seat(battle)
        seat = claim_seat(battle.pk, seat.pk, self.player, archetype="archer")
        set_seat_ready(battle.pk, seat.pk, self.player, ready=True)
        self.login_as(self.player)
        response = self.client.post(reverse("battle:start", args=[battle.pk]))
        self.assertContains(response, "can start it", status_code=403)
        self.switch_to(self.dm)
        response = self.client.post(reverse("battle:start", args=[battle.pk]))
        self.assertRedirects(response, reverse("battle:detail", args=[battle.pk]))
        battle.refresh_from_db()
        self.assertEqual(battle.status, Battle.STATUS_SETUP)

    def test_a_dms_lobby_is_not_readable_to_a_guest(self):
        """A guest cannot claim a seat in a battle they cannot even open —
        this one holds the DM's private generated characters (#273/#305).
        The guest's own lobby is exercised in ``test_guest_play``."""
        battle = self.open_battle()
        seat = self.open_seat(battle)
        response = self.client.post(
            reverse("battle:seat-claim", args=[battle.pk, seat.pk]),
            {"archetype": "archer"},
        )
        self.assertEqual(response.status_code, 404)


class ActAndStateEndpointTest(LobbyViewTestMixin):
    def awaiting_battle(self):
        battle = self.open_battle()
        seat = self.open_seat(battle)
        seat = claim_seat(battle.pk, seat.pk, self.player, archetype="archer")
        set_seat_ready(battle.pk, seat.pk, self.player, ready=True)
        start_battle(battle.pk)
        battle, _events = advance_turn(battle.pk)
        return battle, seat

    def act(self, battle, payload):
        """POST one action, bound to the pause the battle is actually on.

        ``combatant_id``/``turn`` are required of every client since #644;
        a case that wants to submit an unbound or stale choice overrides
        them in its own payload.
        """
        pending = battle.pending_input_json or {}
        body = {
            "combatant_id": pending.get("combatant_id"),
            "turn": pending.get("turn"),
        }
        body.update(payload)
        return self.client.post(
            reverse("battle:act", args=[battle.pk]),
            json.dumps(body),
            content_type="application/json",
        )

    def test_the_awaited_player_acts_and_the_turn_completes(self):
        battle, _seat = self.awaiting_battle()
        chosen = battle.pending_input_json["candidates"][0]
        self.login_as(self.player)
        response = self.act(
            battle,
            {
                "letter": chosen["letter"],
                "target_id": chosen.get("target_id"),
                "spell_key": chosen.get("spell_key") or "",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["turn"], 1)
        self.assertIsNone(data["pending"])
        self.assertTrue(data["events"])
        self.assertTrue(
            any(event["event_type"] == "decision" for event in data["events"])
        )

    def test_an_unbound_submission_is_refused(self):
        """Every client names the pause it answered (#644). A body with no
        ``combatant_id``/``turn`` is the old shape, and the old shape is what
        let one figure's choice land on another."""
        battle, _seat = self.awaiting_battle()
        chosen = battle.pending_input_json["candidates"][0]
        self.login_as(self.player)
        response = self.client.post(
            reverse("battle:act", args=[battle.pk]),
            json.dumps({"letter": chosen["letter"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("combatant_id is required", response.json()["error"])

    def test_a_submission_naming_another_combatant_is_refused(self):
        battle, seat = self.awaiting_battle()
        chosen = battle.pending_input_json["candidates"][0]
        other = battle.combatants.exclude(pk=seat.pk).first()
        self.login_as(self.player)
        response = self.act(
            battle,
            {
                "combatant_id": other.pk,
                "letter": chosen["letter"],
                "target_id": chosen.get("target_id"),
                "spell_key": chosen.get("spell_key") or "",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("the battle is waiting on", response.json()["error"])
        battle.refresh_from_db()
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        self.assertEqual(battle.turn_inputs_json, {})

    def test_a_submission_naming_another_turn_is_refused(self):
        battle, _seat = self.awaiting_battle()
        chosen = battle.pending_input_json["candidates"][0]
        self.login_as(self.player)
        response = self.act(
            battle,
            {
                "turn": battle.pending_input_json["turn"] + 1,
                "letter": chosen["letter"],
                "target_id": chosen.get("target_id"),
                "spell_key": chosen.get("spell_key") or "",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("the battle is waiting on", response.json()["error"])

    def test_another_player_may_not_act(self):
        battle, _seat = self.awaiting_battle()
        chosen = battle.pending_input_json["candidates"][0]
        rival = self.create_user(username="rival")
        self.login_as(rival)
        response = self.act(battle, {"letter": chosen["letter"]})
        # Watchable since the spectator flow — but never theirs to act in.
        self.assertEqual(response.status_code, 400)
        self.assertIn("not your combatant", response.json()["error"])

    def test_a_bystander_may_not_hand_the_choice_to_the_ai(self):
        """Narrowed to bystanders by #308.

        This used to read "the DM may, players may not" — a rule that made
        sense while the DM was the only person who could run a battle. Two
        things moved it. Ownership replaced role (#305), so whoever made a
        battle runs it and a guest-made battle has no DM to unstick it; and
        control became a mode the seat's own player may change (#308), of
        which "let the AI take this one decision" is the smallest version.
        So the seat's player and the battle's owner may both stand the AI
        in — and somebody with no part in the battle still may not.
        """
        battle, _seat = self.awaiting_battle()
        rival = self.create_user(username="ai-rival")
        self.login_as(rival)
        response = self.act(battle, {"ai": True})
        self.assertEqual(response.status_code, 400)
        self.assertIn("hand the choice to the AI", response.json()["error"])

    def test_the_seats_own_player_may_stand_the_ai_in_for_one_decision(self):
        battle, _seat = self.awaiting_battle()
        self.login_as(self.player)
        response = self.act(battle, {"ai": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["turn"], 1)

    def test_the_dm_may_hand_the_choice_to_the_ai(self):
        battle, _seat = self.awaiting_battle()
        self.login_as(self.dm)
        response = self.act(battle, {"ai": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["turn"], 1)

    def test_state_poll_shows_pending_and_filters_events(self):
        battle, seat = self.awaiting_battle()
        self.login_as(self.player)
        response = self.client.get(reverse("battle:state", args=[battle.pk]))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "awaiting")
        self.assertEqual(data["pending"]["combatant_id"], seat.pk)
        self.assertTrue(data["pending"]["is_you"])
        self.assertTrue(data["pending"]["candidates"])
        self.assertEqual(data["events"], [])
        seats_by_id = {s["combatant_id"]: s for s in data["seats"]}
        self.assertTrue(seats_by_id[seat.pk]["is_you"])

        chosen = data["pending"]["candidates"][0]
        self.act(
            battle,
            {
                "letter": chosen["letter"],
                "target_id": chosen.get("target_id"),
                "spell_key": chosen.get("spell_key") or "",
            },
        )
        response = self.client.get(
            reverse("battle:state", args=[battle.pk]), {"after": "0"}
        )
        data = response.json()
        self.assertEqual(data["turn"], 1)
        self.assertTrue(data["events"])
        top = max(event["sequence"] for event in data["events"])
        response = self.client.get(
            reverse("battle:state", args=[battle.pk]), {"after": str(top)}
        )
        self.assertEqual(response.json()["events"], [])

    def test_candidates_hide_from_bystanders(self):
        """A second seated player sees who is awaited, not their options."""
        battle = self.open_battle(seats=2, count=0, seed=12)
        seats = list(battle.combatants.filter(is_open_seat=True))
        seat_one = claim_seat(battle.pk, seats[0].pk, self.player, archetype="archer")
        other = self.create_player(username="secondplayer")
        claim_seat(battle.pk, seats[1].pk, other, archetype="wizard")
        set_seat_ready(battle.pk, seat_one.pk, self.player, ready=True)
        set_seat_ready(battle.pk, seats[1].pk, other, ready=True)
        start_battle(battle.pk)
        battle, _events = advance_turn(battle.pk)
        awaited_id = battle.pending_input_json["combatant_id"]
        bystander = other if awaited_id == seat_one.pk else self.player
        self.login_as(bystander)
        response = self.client.get(reverse("battle:state", args=[battle.pk]))
        pending = response.json()["pending"]
        self.assertFalse(pending["is_you"])
        self.assertEqual(pending["candidates"], [])
        self.switch_to(self.dm)
        response = self.client.get(reverse("battle:state", args=[battle.pk]))
        self.assertTrue(response.json()["pending"]["candidates"])
