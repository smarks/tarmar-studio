"""Placed destinations (#819, #866), on tarmar-engine v0.9.10.

Spencer's ruling of 2026-10-09: the engine takes a hex for the options that
place a figure (``Candidate.destination``, tarmar-engine #20), and the
studio's turn menu lets a player pick it.
"""

from django.urls import reverse
from tarmar_engine import movement

from battle import turn_menu
from battle.engine.state import BattleState
from battle.models import Battle
from battle.policy import Candidate
from battle.services import (
    advance_turn,
    create_battle,
    record_player_action,
    set_combatant_control_mode,
)
from battle.tests.fixed_refusals import with_fixed_refusals
from common.test_helpers import BaseTestCase

from .test_lobby import started_battle_with_player
from .test_turn_menu import figure


class DestinationMenuTest(BaseTestCase):
    """The menu carries each placing option's legal hexes, from the engine."""

    def test_an_engaged_option_offers_its_shift_hexes(self):
        chooser = figure(1, "Aldwin", (0, 0))
        state = BattleState(
            arena_radius=4, combatants=[chooser, figure(2, "Foe", (1, 0))]
        )
        described = turn_menu.describe(
            state,
            chooser,
            Candidate("j", "ATTACK", 0.0, "", target_id=2),
            ai_choice=False,
        )
        self.assertEqual(
            set(described.destinations),
            set(movement.legal_destinations(state, chooser, "j")),
        )
        self.assertTrue(described.destinations)
        self.assertNotIn("destinations", described.to_payload())
        self.assertEqual(
            described.menu_payload()["destinations"],
            [list(cell) for cell in described.destinations],
        )

    def test_a_plain_candidate_keeps_its_own_payload(self):
        plain = Candidate("k", "DEFEND", 0.3, "advice")
        self.assertEqual(turn_menu.menu_payload(plain), plain.to_payload())

    def test_a_charge_takes_no_hex(self):
        chooser = figure(1, "Aldwin", (0, 0))
        state = BattleState(
            arena_radius=6, combatants=[chooser, figure(2, "Foe", (4, 0))]
        )
        described = turn_menu.describe(
            state,
            chooser,
            Candidate("b", "CHARGE", 0.0, "", target_id=2),
            ai_choice=False,
        )
        self.assertEqual(described.destinations, ())
        self.assertNotIn("destinations", described.menu_payload())


class PickedDestinationTest(BaseTestCase):
    """A picked hex goes through the pause, the replay and the engine."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()

    def pending_move(self):
        battle, seat = started_battle_with_player(self)
        battle, _events = advance_turn(battle.pk)
        menu = battle.pending_input_json["candidates"]
        move = next(
            entry
            for entry in menu
            # A move to a hex: MOVE, or DODGE or DROP at the jog when the
            # figure's armour bars the Run.
            if entry.get("destinations") and entry["letter"] in ("a", "c", "d")
        )
        return battle, seat, move

    def act(self, battle, move, destination):
        return record_player_action(
            battle.pk,
            self.player,
            combatant_id=battle.pending_input_json["combatant_id"],
            turn=battle.pending_input_json["turn"],
            letter=move["letter"],
            target_id=move.get("target_id"),
            spell_key=move.get("spell_key") or "",
            destination=destination,
        )

    def test_the_figure_moves_to_the_hex_it_was_given(self):
        battle, seat, move = self.pending_move()
        hex_picked = tuple(move["destinations"][0])
        battle, _events = self.act(battle, move, hex_picked)
        decision = battle.events.get(event_type="decision", actor=seat.display_name)
        self.assertEqual(decision.payload["chosen"]["destination"], list(hex_picked))
        moved = battle.events.filter(
            event_type="movement", actor=seat.display_name
        ).first()
        assert moved is not None
        self.assertEqual(moved.payload["destination"], list(hex_picked))
        self.assertEqual(moved.payload["to"], list(hex_picked))

    @with_fixed_refusals
    def test_a_hex_the_option_cannot_take_is_refused(self):
        battle, _seat, move = self.pending_move()
        with self.assertRaisesMessage(ValueError, "services.destination_not_marked"):
            self.act(battle, move, (99, 99))
        defend = next(
            entry
            for entry in battle.pending_input_json["candidates"]
            if not entry.get("destinations")
        )
        with self.assertRaisesMessage(ValueError, "services.destination_not_marked"):
            self.act(battle, defend, tuple(move["destinations"][0]))

    def test_the_endpoint_reads_the_hex(self):
        battle, seat, move = self.pending_move()
        self.login_as(self.player)
        url = reverse("battle:act", args=[battle.pk])
        body = {
            "combatant_id": battle.pending_input_json["combatant_id"],
            "turn": battle.pending_input_json["turn"],
            "letter": move["letter"],
            "target_id": move.get("target_id"),
        }
        for bad in ("x", [1], [1, "q"]):
            with self.subTest(destination=bad):
                response = self.post_json(url, {**body, "destination": bad})
                self.assertEqual(response.status_code, 400)
        response = self.post_json(url, {**body, "destination": move["destinations"][0]})
        self.assertEqual(response.status_code, 200)

    def test_an_ai_hand_off_takes_no_hex_the_ai_did_not_name(self):
        battle, _seat, _move = self.pending_move()
        battle, _events = record_player_action(
            battle.pk,
            self.dm,
            combatant_id=battle.pending_input_json["combatant_id"],
            turn=battle.pending_input_json["turn"],
            letter="",
            as_ai=True,
        )
        self.assertNotEqual(battle.status, Battle.STATUS_AWAITING)


class ManualRunnerFigureTest(BaseTestCase):
    """A manual figure nobody sits in is decided by the battle's runner."""

    def test_the_runner_picks_a_hex_too(self):
        dm = self.create_dm()
        battle = create_battle(dm, name="Pit", count=2, seed=9)
        first = battle.combatants.order_by("pk").first()
        assert first is not None
        set_combatant_control_mode(battle.pk, first.pk, "manual")
        battle, _events = advance_turn(battle.pk)
        menu = battle.pending_input_json["candidates"]
        placing = [entry for entry in menu if entry.get("destinations")]
        self.assertTrue(placing)
