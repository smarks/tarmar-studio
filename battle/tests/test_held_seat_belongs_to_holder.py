"""A held seat belongs to its holder (issue #180), and a lobby seat can be renamed (#181).

Spencer's decision of 2026-11-10 on #180: a battle's maker may not take over,
rename or otherwise act for a figure another player is seated in; only a Game
Master account may override. ``battle.access.decides_for`` is the one
statement of it, and every path that writes a seated figure asks it: the
control mode (``combatant_control``), the name (``battle_rename``), a paused
turn's choice and handing it to the AI (``record_player_action``), and the
maker's "set them all" (``set_battle_control_mode``), which now leaves held
seats as their players chose. The page offers the click on exactly those
figures (``seat_summaries``), and the awaited menu goes to whoever acts for the
figure and nobody else (``pending_payload``).

Which side of the player/Game Master line each check sits on: a player (the
maker included) is held to their own seat; a Game Master account overrides.

#181: renaming a seat while the battle is still a lobby raised on the empty
state and answered 500. The row is the whole rename there; the started battle
carries the new name.
"""

import json

from django.urls import reverse

from battle import control
from battle.access import Actor, decides_for
from battle.models import Battle
from battle.refusal_words import refusal_words
from battle.services import (
    advance_turn,
    claim_seat,
    create_battle,
    record_player_action,
    set_battle_control_mode,
    set_combatant_control_mode,
    set_seat_ready,
    start_battle,
)
from battle.tests.fixed_refusals import with_fixed_refusals
from battle.views import pending_payload, seat_summaries
from common.test_helpers import BaseTestCase

#: A seed whose first turn does not end the fight (as test_control_modes uses).
STEADY_SEED = 11


class HeldSeatTestCase(BaseTestCase):
    """A player-made lobby with one fighter and one seat another player holds."""

    def setUp(self):
        super().setUp()
        self.maker = self.create_player(username="maker180")
        self.holder = self.create_user(username="holder180")
        self.game_master = self.create_dm(username="gamemaster180")
        self.bystander = self.create_user(username="bystander180")
        self.battle = create_battle(
            self.maker, name="Held", count=1, seed=STEADY_SEED, player_seats=1
        )
        seat = self.battle.combatants.get(is_open_seat=True)
        self.seat = claim_seat(self.battle.pk, seat.pk, self.holder, archetype="archer")
        self.fighter = self.battle.combatants.exclude(pk=self.seat.pk).get()

    def actor(self, user) -> Actor:
        return Actor.for_user_or_guest(user, "")

    def started(self) -> Battle:
        set_seat_ready(self.battle.pk, self.seat.pk, self.holder, ready=True)
        return start_battle(self.battle.pk)

    def post_json(self, user, url_name: str, args: list, body: dict):
        self.switch_to(user)
        return self.client.post(
            reverse(url_name, args=args),
            json.dumps(body),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )


class DecidesForIsTheOneRuleTest(HeldSeatTestCase):
    """The authority function itself, both sides of the line."""

    def test_a_held_seat_is_its_holder_s_and_a_game_master_s_only(self):
        self.assertTrue(decides_for(self.actor(self.holder), self.battle, self.seat))
        self.assertTrue(
            decides_for(self.actor(self.game_master), self.battle, self.seat)
        )
        self.assertFalse(decides_for(self.actor(self.maker), self.battle, self.seat))
        self.assertFalse(
            decides_for(self.actor(self.bystander), self.battle, self.seat)
        )

    def test_an_unseated_figure_is_the_runner_s(self):
        self.assertTrue(decides_for(self.actor(self.maker), self.battle, self.fighter))
        self.assertTrue(
            decides_for(self.actor(self.game_master), self.battle, self.fighter)
        )
        self.assertFalse(
            decides_for(self.actor(self.holder), self.battle, self.fighter)
        )


@with_fixed_refusals
class ControlModeOfAHeldSeatTest(HeldSeatTestCase):
    def control(self, user, combatant, mode=control.AUTOMATIC):
        return self.post_json(
            user,
            "battle:combatant-control",
            [self.battle.pk, combatant.pk],
            {"control_mode": mode},
        )

    def test_the_maker_may_not_set_a_held_seat_s_mode(self):
        """The #180 evidence: the maker set the holder's figure to automatic."""
        response = self.control(self.maker, self.seat)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["error"], refusal_words().views.seat_belongs_to_holder
        )
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.control_mode, control.MANUAL)

    def test_the_holder_and_a_game_master_may(self):
        self.assertEqual(self.control(self.holder, self.seat).status_code, 200)
        self.assertEqual(
            self.control(self.game_master, self.seat, control.MANUAL).status_code,
            200,
        )

    def test_the_maker_still_sets_an_unseated_figure_s_mode(self):
        self.assertEqual(self.control(self.maker, self.fighter).status_code, 200)

    def test_set_them_all_leaves_a_held_seat_as_its_player_chose(self):
        """The maker's "set them all" clears the overrides on the figures the
        maker acts for and no others; a Game Master's clears every row."""
        set_combatant_control_mode(self.battle.pk, self.fighter.pk, control.MANUAL)
        set_battle_control_mode(
            self.battle.pk,
            control.AUTOMATIC,
            actor=self.actor(self.maker),
            apply_to_all=True,
        )
        self.seat.refresh_from_db()
        self.fighter.refresh_from_db()
        self.assertEqual(self.seat.control_mode, control.MANUAL)
        self.assertEqual(self.fighter.control_mode, control.INHERIT)

        set_battle_control_mode(
            self.battle.pk,
            control.AUTOMATIC,
            actor=self.actor(self.game_master),
            apply_to_all=True,
        )
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.control_mode, control.INHERIT)

    def test_the_page_offers_the_controls_to_whoever_acts_for_the_figure(self):
        by_id = {
            seat["combatant_id"]: seat
            for seat in seat_summaries(self.battle, self.actor(self.maker))
        }
        self.assertFalse(by_id[self.seat.pk]["can_set_control"])
        self.assertFalse(by_id[self.seat.pk]["can_rename"])
        self.assertTrue(by_id[self.fighter.pk]["can_set_control"])
        by_id = {
            seat["combatant_id"]: seat
            for seat in seat_summaries(self.battle, self.actor(self.holder))
        }
        self.assertTrue(by_id[self.seat.pk]["can_rename"])
        self.assertFalse(by_id[self.fighter.pk]["can_rename"])


@with_fixed_refusals
class RenamingAHeldSeatTest(HeldSeatTestCase):
    def rename(self, user, combatant, name: str):
        return self.post_json(
            user,
            "battle:rename",
            [self.battle.pk],
            {"combatant_id": combatant.pk, "name": name},
        )

    def test_the_maker_may_not_rename_a_held_seat(self):
        self.started()
        response = self.rename(self.maker, self.seat, "Taken Over")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["error"], refusal_words().views.seat_belongs_to_holder
        )

    def test_the_holder_renames_their_own_seat_whether_or_not_they_run_the_battle(
        self,
    ):
        self.started()
        response = self.rename(self.holder, self.seat, "Mine")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "Mine")

    def test_a_game_master_overrides(self):
        self.started()
        self.assertEqual(
            self.rename(self.game_master, self.seat, "Ruled").status_code, 200
        )

    def test_the_holder_may_not_rename_the_maker_s_fighter(self):
        response = self.rename(self.holder, self.fighter, "Not Mine")
        self.assertEqual(response.status_code, 403)

    def test_a_lobby_seat_is_renamed_without_a_500_and_starts_with_its_new_name(
        self,
    ):
        """Issue #181: the lobby has no state to rename yet; the row is the
        whole rename, and the started battle is named from the rows."""
        self.assertEqual(self.battle.status, Battle.STATUS_LOBBY)
        response = self.rename(self.holder, self.seat, "Lobby Archer")
        self.assertEqual(response.status_code, 200)
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.display_name, "Lobby Archer")
        open_seat_response = self.rename(self.maker, self.fighter, "Our Fighter")
        self.assertEqual(open_seat_response.status_code, 200)

        battle = self.started()
        names = {figure["name"] for figure in battle.state_json["combatants"]}
        self.assertIn("Lobby Archer", names)
        self.assertIn("Our Fighter", names)


class ActingOnAHeldSeatsTurnTest(HeldSeatTestCase):
    """The paused turn: the choice, and handing it to the AI."""

    def awaiting(self) -> Battle:
        battle = self.started()
        battle, _events = advance_turn(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        return battle

    def test_the_maker_may_not_hand_the_holder_s_choice_to_the_ai(self):
        battle = self.awaiting()
        with self.assertRaisesMessage(
            ValueError, refusal_words().services.hand_choice_to_ai_denied
        ):
            record_player_action(
                battle.pk,
                self.maker,
                combatant_id=battle.pending_input_json["combatant_id"],
                turn=battle.pending_input_json["turn"],
                letter="",
                as_ai=True,
            )

    def test_a_game_master_may_choose_or_hand_to_the_ai(self):
        battle = self.awaiting()
        battle, _events = record_player_action(
            battle.pk,
            self.game_master,
            combatant_id=battle.pending_input_json["combatant_id"],
            turn=battle.pending_input_json["turn"],
            letter="",
            as_ai=True,
        )
        self.assertNotEqual(battle.status, Battle.STATUS_AWAITING)

    def test_the_menu_reaches_the_holder_and_a_game_master_not_the_maker(self):
        battle = self.awaiting()
        holder_view = pending_payload(battle, self.actor(self.holder))
        game_master_view = pending_payload(battle, self.actor(self.game_master))
        maker_view = pending_payload(battle, self.actor(self.maker))
        assert holder_view and game_master_view and maker_view
        self.assertTrue(holder_view["candidates"])
        self.assertTrue(game_master_view["candidates"])
        self.assertFalse(maker_view["is_you"])
        self.assertEqual(maker_view["candidates"], [])

    def test_the_act_endpoint_refuses_the_maker_before_reading_the_body(self):
        """Issue #182 for this endpoint: a stranger to the seat is answered
        403 whatever they posted, and never sees a note on the body."""
        battle = self.awaiting()
        response = self.post_json(
            self.maker, "battle:act", [battle.pk], {"this is": "not a choice"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["error"], refusal_words().services.not_your_combatant
        )
