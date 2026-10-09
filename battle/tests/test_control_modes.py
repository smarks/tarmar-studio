"""Control as an explicit mode, not an inference (#308).

Four things are pinned here, and the order is the order they matter in.

**Precedence**, because the whole feature rests on it: the battle's mode is
a default every combatant inherits, and a combatant's own mode overrides it.
A row that merely *agrees* with the battle is not the same as a row that
follows it, and the tests say so.

**The mixed advance**, because that is where a mode stops being a setting
and becomes behaviour: with some combatants automatic and some manual, one
press of Advance must run the automatic ones and stop at the first manual
one, and the readout must name who it stopped for — from stored state, on a
reload, not as a flash when it happens.

**Switching mid-battle**, including the case that would otherwise wedge a
battle: handing the *awaited* combatant to the AI while the turn is paused
on it.

**Ownership**, because manual means "its owner decides" and since #305 an
owner may have no account — and because the old inference (``player_id is
not None``) silently missed a guest's seat entirely.
"""

import json

from django.test import Client
from django.urls import reverse

from battle import control
from battle.access import Actor, decides_for
from battle.engine.state import BattleState
from battle.models import Battle, BattleEvent
from battle.persistence import dump_state
from battle.services import (
    advance_turn,
    claim_seat,
    create_battle,
    manual_controllers,
    record_player_action,
    set_battle_control_mode,
    set_combatant_control_mode,
    set_seat_ready,
    start_battle,
)
from battle.views import advance_control
from common.guest import GUEST_KEY_SESSION_FIELD
from common.test_helpers import BaseTestCase

# A seed whose first turn does not end the fight, so a battle has room to
# be paused, resumed, and re-advanced inside one test.
STEADY_SEED = 11


# ------------------------------------------------------------- precedence


class PrecedenceTest(BaseTestCase):
    """The battle sets a default; a combatant may overrule it. Nothing else."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.battle = create_battle(
            self.dm, name="Precedence", count=2, seed=STEADY_SEED
        )
        self.combatant = self.battle.combatants.first()

    def test_a_combatant_with_no_mode_of_its_own_follows_the_battle(self):
        self.assertEqual(self.combatant.control_mode, control.INHERIT)
        self.assertEqual(
            control.effective_mode(self.battle, self.combatant), control.AUTOMATIC
        )
        self.battle.control_mode = control.MANUAL
        self.assertEqual(
            control.effective_mode(self.battle, self.combatant), control.MANUAL
        )

    def test_a_combatants_own_mode_overrides_an_automatic_battle(self):
        self.combatant.control_mode = control.MANUAL
        self.assertTrue(control.is_manual(self.battle, self.combatant))

    def test_a_combatants_own_mode_overrides_a_manual_battle(self):
        self.battle.control_mode = control.MANUAL
        self.combatant.control_mode = control.AUTOMATIC
        self.assertFalse(control.is_manual(self.battle, self.combatant))

    def test_inherit_is_not_a_battle_level_mode(self):
        """A battle has nothing to inherit from, so the empty string is not
        a mode it may hold — storing one would leave its default undefined."""
        with self.assertRaises(ValueError):
            control.clean_mode(control.INHERIT)
        with self.assertRaises(ValueError):
            control.clean_mode("semi-automatic")
        self.assertEqual(control.clean_mode(control.MANUAL), control.MANUAL)

    def test_a_combatant_may_hold_inherit(self):
        self.assertEqual(control.clean_combatant_mode(control.INHERIT), control.INHERIT)
        self.assertEqual(control.clean_combatant_mode(None), control.INHERIT)
        with self.assertRaises(ValueError):
            control.clean_combatant_mode("sometimes")

    def test_setting_all_clears_the_overrides_rather_than_stamping_them(self):
        """ "Set them all to X" leaves a roster that still *follows* the
        battle — not one that happens to agree with it today and would
        ignore it tomorrow."""
        set_combatant_control_mode(self.battle.pk, self.combatant.pk, control.MANUAL)
        battle, _event = set_battle_control_mode(
            self.battle.pk, control.AUTOMATIC, apply_to_all=True
        )
        self.assertEqual(battle.control_mode, control.AUTOMATIC)
        self.assertEqual(
            list(battle.combatants.values_list("control_mode", flat=True)),
            [control.INHERIT] * battle.combatants.count(),
        )

    def test_setting_the_default_alone_leaves_overrides_standing(self):
        set_combatant_control_mode(self.battle.pk, self.combatant.pk, control.AUTOMATIC)
        battle, _event = set_battle_control_mode(self.battle.pk, control.MANUAL)
        self.assertEqual(battle.control_mode, control.MANUAL)
        kept = battle.combatants.get(pk=self.combatant.pk)
        self.assertEqual(kept.control_mode, control.AUTOMATIC)
        self.assertFalse(control.is_manual(battle, kept))


# --------------------------------------------------------- the advance


class AutomaticBattleTest(BaseTestCase):
    """ "Play this whole battle for me, I just want to watch." """

    def test_an_automatic_battle_never_stops_for_anybody(self):
        dm = self.create_dm()
        battle = create_battle(dm, name="Watch", count=3, seed=STEADY_SEED)
        self.assertEqual(battle.control_mode, control.AUTOMATIC)
        self.assertEqual(manual_controllers(battle), {})
        for _press in range(4):
            battle, events = advance_turn(battle.pk)
            self.assertNotEqual(battle.status, Battle.STATUS_AWAITING)
            if battle.status in (Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE):
                break
            self.assertTrue(events)


class ManualBattleTest(BaseTestCase):
    """A manual battle stops for its owner before anything happens."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.battle = create_battle(
            self.dm,
            name="By hand",
            count=3,
            seed=STEADY_SEED,
            control_mode=control.MANUAL,
        )

    def test_every_combatant_is_manual_and_owned_by_whoever_made_it(self):
        controllers = manual_controllers(self.battle)
        self.assertEqual(
            set(controllers),
            set(self.battle.combatants.values_list("pk", flat=True)),
        )
        self.assertEqual(set(controllers.values()), {self.dm.username})

    def test_the_first_advance_parks_on_a_combatant_and_commits_nothing(self):
        battle, events = advance_turn(self.battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        self.assertEqual(events, [])
        self.assertEqual(battle.turn_number, 0)
        awaited = battle.pending_input_json["combatant_id"]
        self.assertIn(awaited, set(battle.combatants.values_list("pk", flat=True)))
        self.assertTrue(battle.pending_input_json["candidates"])

    def test_the_owner_decides_for_a_combatant_nobody_is_seated_in(self):
        """Manual on an unseated figure would name an owner who does not
        exist, unless "owner" reaches past the seat to whoever runs the
        battle. This is the test that says it does."""
        battle, _events = advance_turn(self.battle.pk)
        awaited = battle.combatants.get(pk=battle.pending_input_json["combatant_id"])
        self.assertTrue(decides_for(Actor(user=self.dm, guest_key=""), battle, awaited))
        top = battle.pending_input_json["candidates"][0]
        battle, _events = record_player_action(
            battle.pk,
            self.dm,
            combatant_id=battle.pending_input_json["combatant_id"],
            turn=battle.pending_input_json["turn"],
            letter=top["letter"],
            target_id=top.get("target_id"),
            spell_key=top.get("spell_key") or "",
        )
        # Either the turn moved on to the next manual combatant, or it
        # committed — never "nothing happened".
        self.assertIn(
            battle.status,
            (
                Battle.STATUS_AWAITING,
                Battle.STATUS_PAUSED,
                Battle.STATUS_FINISHED,
                Battle.STATUS_STALEMATE,
            ),
        )
        if battle.status == Battle.STATUS_AWAITING:
            self.assertNotEqual(battle.pending_input_json["combatant_id"], awaited.pk)


class MixedBattleTest(BaseTestCase):
    """ "I'll drive my own fighter, let the AI run the rest." """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.battle = create_battle(self.dm, name="Mixed", count=3, seed=STEADY_SEED)
        self.mine = self.battle.combatants.all()[1]
        set_combatant_control_mode(self.battle.pk, self.mine.pk, control.MANUAL)
        self.battle.refresh_from_db()
        # The mode change files its own log line; a turn's events are
        # counted against that baseline, not against an empty log.
        self.logged_before = self.battle.events.count()

    def test_only_the_manual_combatant_is_awaited(self):
        self.assertEqual(set(manual_controllers(self.battle)), {self.mine.pk})

    def test_advance_runs_the_ai_ones_and_stops_at_the_manual_one(self):
        battle, events = advance_turn(self.battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        self.assertEqual(battle.pending_input_json["combatant_id"], self.mine.pk)
        # The partial turn rolled back: the AI combatants' decisions are
        # replayed after the choice, not half-committed before it.
        self.assertEqual(events, [])
        self.assertEqual(battle.events.count(), self.logged_before)

    def test_the_readout_names_who_the_battle_is_waiting_on(self):
        battle, _events = advance_turn(self.battle.pk)
        readout = advance_control(battle, Actor(user=self.dm, guest_key=""))
        assert readout is not None
        self.assertFalse(readout["enabled"])
        self.assertEqual(readout["waiting_on"], self.mine.display_name)
        self.assertEqual(
            readout["label"], f"Waiting on {self.mine.display_name}'s move"
        )

    def test_the_readout_is_state_driven_and_survives_a_reload(self):
        advance_turn(self.battle.pk)
        self.login_as(self.dm)
        response = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        self.assertContains(
            response, f"Waiting on {self.mine.display_name}&#x27;s move"
        )

    def test_choosing_resumes_the_turn_and_commits_the_ai_decisions_too(self):
        battle, _events = advance_turn(self.battle.pk)
        top = battle.pending_input_json["candidates"][0]
        battle, events = record_player_action(
            battle.pk,
            self.dm,
            combatant_id=battle.pending_input_json["combatant_id"],
            turn=battle.pending_input_json["turn"],
            letter=top["letter"],
            target_id=top.get("target_id"),
            spell_key=top.get("spell_key") or "",
        )
        self.assertNotEqual(battle.status, Battle.STATUS_AWAITING)
        self.assertEqual(battle.turn_number, 1)
        self.assertTrue(events)
        credited = [
            event
            for event in events
            if event.event_type == "decision"
            and f"chosen by {self.dm.username}" in json.dumps(event.payload)
        ]
        self.assertTrue(credited, "the manual combatant's decision credits its owner")

    def test_an_idle_readout_offers_the_plain_advance(self):
        readout = advance_control(self.battle, Actor(user=self.dm, guest_key=""))
        assert readout is not None
        self.assertTrue(readout["enabled"])
        self.assertEqual(readout["label"], "Advance turn")

    def test_a_spectator_gets_no_advance_readout_at_all(self):
        watcher = self.create_player()
        self.assertIsNone(
            advance_control(self.battle, Actor(user=watcher, guest_key=""))
        )


# ------------------------------------------------------- switching modes


class SwitchingMidBattleTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.battle = create_battle(self.dm, name="Switch", count=3, seed=STEADY_SEED)
        self.mine = self.battle.combatants.all()[1]

    def test_handing_the_awaited_combatant_to_the_ai_unsticks_the_battle(self):
        """The wedge this guards: a battle paused on a figure whose owner
        has gone. Without clearing the pause, Advance stays disabled and
        nobody is ever coming."""
        set_combatant_control_mode(self.battle.pk, self.mine.pk, control.MANUAL)
        battle, _events = advance_turn(self.battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        battle, _combatant, _event = set_combatant_control_mode(
            battle.pk, self.mine.pk, control.AUTOMATIC
        )
        self.assertEqual(battle.status, Battle.STATUS_PAUSED)
        self.assertEqual(battle.pending_input_json, {})
        battle, events = advance_turn(battle.pk)
        self.assertNotEqual(battle.status, Battle.STATUS_AWAITING)
        self.assertEqual(battle.turn_number, 1)
        self.assertTrue(events)

    def test_taking_a_figure_back_stops_the_next_advance_for_it(self):
        battle, _events = advance_turn(self.battle.pk)
        self.assertEqual(battle.turn_number, 1)
        battle, _combatant, _event = set_combatant_control_mode(
            battle.pk, self.mine.pk, control.MANUAL
        )
        battle, events = advance_turn(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        self.assertEqual(battle.pending_input_json["combatant_id"], self.mine.pk)
        self.assertEqual(events, [])

    def test_a_recorded_choice_is_dropped_when_its_combatant_goes_automatic(self):
        """The stale-input case: the choice was the person's, and the
        person is no longer the one deciding."""
        set_battle_control_mode(self.battle.pk, control.MANUAL, apply_to_all=True)
        battle, _events = advance_turn(self.battle.pk)
        top = battle.pending_input_json["candidates"][0]
        awaited = battle.pending_input_json["combatant_id"]
        record_player_action(
            battle.pk,
            self.dm,
            combatant_id=battle.pending_input_json["combatant_id"],
            turn=battle.pending_input_json["turn"],
            letter=top["letter"],
            target_id=top.get("target_id"),
            spell_key=top.get("spell_key") or "",
        )
        battle = Battle.objects.get(pk=battle.pk)
        self.assertIn(str(awaited), battle.turn_inputs_json)
        battle, _event = set_battle_control_mode(
            battle.pk, control.AUTOMATIC, apply_to_all=True
        )
        self.assertEqual(battle.turn_inputs_json, {})

    def test_switching_a_combatant_that_is_not_in_this_battle_is_refused(self):
        other = create_battle(self.dm, name="Other", count=2, seed=3)
        stranger = other.combatants.first()
        with self.assertRaises(ValueError):
            set_combatant_control_mode(self.battle.pk, stranger.pk, control.MANUAL)


# ---------------------------------------------------------------- the log


class ControlChangesAreLoggedTest(BaseTestCase):
    """A figure that starts behaving differently mid-fight owes the log a
    reason. Without these lines the log stops explaining itself."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.battle = create_battle(self.dm, name="Logged", count=2, seed=STEADY_SEED)
        self.combatant = self.battle.combatants.first()

    def test_a_battle_level_change_is_filed(self):
        _battle, event = set_battle_control_mode(self.battle.pk, control.MANUAL)
        assert event is not None
        self.assertEqual(event.event_type, "status")
        self.assertIn("the battle's default is now manual", event.message)
        self.assertEqual(event.payload["scope"], "battle")
        self.assertFalse(event.payload["applied_to_all"])

    def test_setting_them_all_says_so(self):
        _battle, event = set_battle_control_mode(
            self.battle.pk, control.MANUAL, apply_to_all=True
        )
        assert event is not None
        self.assertIn("every combatant now follows the battle", event.message)
        self.assertTrue(event.payload["applied_to_all"])

    def test_a_combatant_change_names_the_figure_and_who_decides_it(self):
        _battle, _combatant, event = set_combatant_control_mode(
            self.battle.pk, self.combatant.pk, control.MANUAL
        )
        assert event is not None
        self.assertIn(self.combatant.display_name, event.message)
        self.assertIn("played by hand", event.message)
        self.assertIn(self.dm.username, event.message)
        self.assertEqual(event.payload["combatant_id"], self.combatant.pk)
        self.assertEqual(event.payload["effective"], control.MANUAL)

    def test_handing_one_back_says_what_it_now_follows(self):
        set_combatant_control_mode(self.battle.pk, self.combatant.pk, control.MANUAL)
        _battle, _combatant, event = set_combatant_control_mode(
            self.battle.pk, self.combatant.pk, control.INHERIT
        )
        assert event is not None
        self.assertIn("follows the battle again", event.message)
        self.assertIn("automatic", event.message)

    def test_the_line_takes_the_next_sequence_and_the_turn_after_it_agrees(self):
        """The numbering trap: a control line minted outside the engine's
        counter would collide with the next turn's events."""
        _battle, event = set_battle_control_mode(self.battle.pk, control.AUTOMATIC)
        assert event is not None
        battle, events = advance_turn(self.battle.pk)
        self.assertTrue(events)
        self.assertTrue(all(other.sequence > event.sequence for other in events))
        sequences = list(battle.events.values_list("sequence", flat=True))
        self.assertEqual(len(sequences), len(set(sequences)))

    def test_a_lobby_has_no_log_to_write_to_yet(self):
        lobby = create_battle(self.dm, name="Lobby", count=2, seed=5, player_seats=1)
        self.assertEqual(lobby.status, Battle.STATUS_LOBBY)
        battle, event = set_battle_control_mode(lobby.pk, control.MANUAL)
        self.assertIsNone(event)
        self.assertEqual(battle.control_mode, control.MANUAL)
        self.assertEqual(BattleEvent.objects.filter(battle=battle).count(), 0)


# ------------------------------------------------------- seats and guests


class SeatsCarryTheModeTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.battle = create_battle(
            self.dm, name="Seated", count=2, seed=STEADY_SEED, player_seats=1
        )
        self.seat = self.battle.combatants.filter(is_open_seat=True).first()

    def test_claiming_a_seat_writes_manual_down_rather_than_implying_it(self):
        seat = claim_seat(self.battle.pk, self.seat.pk, self.player, archetype="archer")
        self.assertEqual(seat.control_mode, control.MANUAL)

    def test_releasing_a_seat_hands_it_back_to_the_battle(self):
        from battle.services import release_seat

        claim_seat(self.battle.pk, self.seat.pk, self.player, archetype="archer")
        seat = release_seat(self.battle.pk, self.seat.pk, self.player)
        self.assertEqual(seat.control_mode, control.INHERIT)

    def test_a_seated_player_is_awaited_in_an_otherwise_automatic_battle(self):
        seat = claim_seat(self.battle.pk, self.seat.pk, self.player, archetype="archer")
        set_seat_ready(self.battle.pk, seat.pk, self.player, ready=True)
        start_battle(self.battle.pk)
        battle, _events = advance_turn(self.battle.pk)
        self.assertEqual(battle.control_mode, control.AUTOMATIC)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        self.assertEqual(battle.pending_input_json["combatant_id"], seat.pk)

    def test_a_player_may_hand_their_own_fighter_to_the_ai_and_keep_the_seat(self):
        seat = claim_seat(self.battle.pk, self.seat.pk, self.player, archetype="archer")
        set_seat_ready(self.battle.pk, seat.pk, self.player, ready=True)
        start_battle(self.battle.pk)
        battle, _combatant, _event = set_combatant_control_mode(
            self.battle.pk, seat.pk, control.AUTOMATIC
        )
        self.assertEqual(manual_controllers(battle), {})
        seat.refresh_from_db()
        self.assertEqual(seat.player_id, self.player.pk)
        battle, events = advance_turn(battle.pk)
        self.assertNotEqual(battle.status, Battle.STATUS_AWAITING)
        self.assertTrue(events)


class GuestSeatsAreControlledToneTest(BaseTestCase):
    """The gap the old inference left (#305 + #308).

    Control was ``player_id is not None``. A guest's claim lands on
    ``guest_key``, not on ``player``, so a guest who claimed a seat was
    quietly played by the AI — the one thing claiming a seat is for.
    """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.client = Client()
        self.battle = create_battle(
            self.dm, name="Guest seat", count=2, seed=STEADY_SEED, player_seats=1
        )
        self.seat = self.battle.combatants.filter(is_open_seat=True).first()

    def guest_key(self):
        # A claim mints the key; peek at it the way the seat endpoints do.
        self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        session = self.client.session
        session[GUEST_KEY_SESSION_FIELD] = "guest-token-308"
        session.save()
        return "guest-token-308"

    def test_a_guest_who_claims_a_seat_actually_plays_it(self):
        key = self.guest_key()
        seat = claim_seat(
            self.battle.pk, self.seat.pk, None, guest_key=key, archetype="archer"
        )
        self.assertEqual(seat.control_mode, control.MANUAL)
        set_seat_ready(self.battle.pk, seat.pk, None, guest_key=key, ready=True)
        start_battle(self.battle.pk)
        battle, _events = advance_turn(self.battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        self.assertEqual(battle.pending_input_json["combatant_id"], seat.pk)
        self.assertEqual(manual_controllers(battle)[seat.pk], control.GUEST_OWNER)

    def test_the_guest_who_holds_the_seat_is_the_one_who_decides(self):
        key = self.guest_key()
        seat = claim_seat(
            self.battle.pk, self.seat.pk, None, guest_key=key, archetype="archer"
        )
        holder = Actor(user=None, guest_key=key)
        stranger = Actor(user=None, guest_key="somebody-else")
        self.assertTrue(decides_for(holder, self.battle, seat))
        self.assertFalse(decides_for(stranger, self.battle, seat))
        # A DM may not choose *for* a seated player; only hand it to the AI.
        self.assertFalse(
            decides_for(Actor(user=self.dm, guest_key=""), self.battle, seat)
        )


# ------------------------------------------------------------- endpoints


class ControlEndpointTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.owner = self.create_player()
        self.battle = create_battle(
            self.owner, name="Endpoints", count=3, seed=STEADY_SEED
        )
        self.combatant = self.battle.combatants.first()

    def test_the_owner_sets_the_battle_default(self):
        self.login_as(self.owner)
        response = self.client.post(
            reverse("battle:control", args=[self.battle.pk]),
            {"control_mode": control.MANUAL},
        )
        self.assertEqual(response.status_code, 302)
        self.battle.refresh_from_db()
        self.assertEqual(self.battle.control_mode, control.MANUAL)

    def test_set_them_all_clears_the_overrides(self):
        set_combatant_control_mode(self.battle.pk, self.combatant.pk, control.MANUAL)
        self.login_as(self.owner)
        self.client.post(
            reverse("battle:control", args=[self.battle.pk]),
            {"control_mode": control.AUTOMATIC, "apply_to_all": "true"},
        )
        self.combatant.refresh_from_db()
        self.assertEqual(self.combatant.control_mode, control.INHERIT)

    def test_a_stranger_may_not_set_anything(self):
        stranger = self.create_user(username="stranger308")
        self.login_as(stranger)
        response = self.client.post(
            reverse("battle:control", args=[self.battle.pk]),
            {"control_mode": control.MANUAL},
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            reverse(
                "battle:combatant-control", args=[self.battle.pk, self.combatant.pk]
            ),
            {"control_mode": control.MANUAL},
        )
        self.assertEqual(response.status_code, 403)

    def test_a_junk_mode_is_refused_rather_than_stored(self):
        self.login_as(self.owner)
        response = self.client.post(
            reverse("battle:control", args=[self.battle.pk]),
            {"control_mode": "whenever"},
        )
        self.assertEqual(response.status_code, 400)
        self.battle.refresh_from_db()
        self.assertEqual(self.battle.control_mode, control.AUTOMATIC)

    def test_a_seated_player_sets_their_own_combatants_mode(self):
        lobby = create_battle(
            self.owner, name="Lobby ctl", count=2, seed=5, player_seats=1
        )
        seat = lobby.combatants.filter(is_open_seat=True).first()
        player = self.create_user(username="seated308")
        seat = claim_seat(lobby.pk, seat.pk, player, archetype="archer")
        self.login_as(player)
        response = self.client.post(
            reverse("battle:combatant-control", args=[lobby.pk, seat.pk]),
            {"control_mode": control.AUTOMATIC},
        )
        self.assertEqual(response.status_code, 302)
        seat.refresh_from_db()
        self.assertEqual(seat.control_mode, control.AUTOMATIC)

    def test_an_unknown_combatant_is_a_404_not_a_silent_no_op(self):
        self.login_as(self.owner)
        other = create_battle(self.owner, name="Elsewhere", count=2, seed=9)
        response = self.client.post(
            reverse(
                "battle:combatant-control",
                args=[self.battle.pk, other.combatants.first().pk],
            ),
            {"control_mode": control.MANUAL},
        )
        self.assertEqual(response.status_code, 404)

    def test_the_payload_carries_the_mode_and_the_advance_readout(self):
        self.login_as(self.owner)
        response = self.client.get(reverse("battle:state", args=[self.battle.pk]))
        payload = json.loads(response.content)
        self.assertEqual(payload["control_mode"], control.AUTOMATIC)
        self.assertEqual(payload["advance"]["label"], "Advance turn")
        self.assertTrue(payload["advance"]["enabled"])
        seat = payload["seats"][0]
        self.assertEqual(seat["effective_control_mode"], control.AUTOMATIC)
        self.assertEqual(seat["controller"], control.AI_OWNER)
        self.assertIn("Automatic", seat["control_line"])

    def test_the_panel_is_offered_to_whoever_runs_the_battle_only(self):
        self.login_as(self.owner)
        response = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        self.assertContains(response, "Who decides")
        watcher = self.create_user(username="watcher308")
        self.login_as(watcher)
        response = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        self.assertNotContains(response, "Who decides")


class AiStandsInForAnAbsentPlayerTest(BaseTestCase):
    """Handing an awaited choice to the AI used to be DM-only, which left a
    guest-run battle — one with no DM anywhere near it — with no way past
    an absent player (#305/#308)."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_player()
        self.player = self.create_user(username="absent308")
        self.battle = create_battle(
            self.owner, name="Absent", count=2, seed=STEADY_SEED, player_seats=1
        )
        seat = self.battle.combatants.filter(is_open_seat=True).first()
        self.seat = claim_seat(self.battle.pk, seat.pk, self.player, archetype="archer")
        set_seat_ready(self.battle.pk, self.seat.pk, self.player, ready=True)
        start_battle(self.battle.pk)

    def test_the_battles_owner_may_hand_the_choice_to_the_ai(self):
        battle, _events = advance_turn(self.battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        battle, _events = record_player_action(
            battle.pk,
            self.owner,
            combatant_id=battle.pending_input_json["combatant_id"],
            turn=battle.pending_input_json["turn"],
            letter="",
            as_ai=True,
        )
        self.assertNotEqual(battle.status, Battle.STATUS_AWAITING)

    def test_a_bystander_may_not(self):
        battle, _events = advance_turn(self.battle.pk)
        bystander = self.create_user(username="bystander308")
        with self.assertRaisesMessage(ValueError, "hand the choice to the AI"):
            record_player_action(
                self.battle.pk,
                bystander,
                combatant_id=battle.pending_input_json["combatant_id"],
                turn=battle.pending_input_json["turn"],
                letter="",
                as_ai=True,
            )


class ControlOverJsonTest(BaseTestCase):
    """The same endpoints, driven from script rather than from a form.

    The page itself posts forms — the control desk renders from stored
    state, so the reload a post causes *is* the update, and no toast or
    in-place flash is involved. The JSON shape exists so anything driving
    the battle programmatically gets back the same payload an advance
    returns, rather than a redirect it would have to follow.
    """

    def setUp(self):
        super().setUp()
        self.owner = self.create_player()
        self.battle = create_battle(
            self.owner, name="Json ctl", count=3, seed=STEADY_SEED
        )
        self.combatant = self.battle.combatants.first()
        self.login_as(self.owner)

    def post_json(self, url, body):
        return self.client.post(url, json.dumps(body), content_type="application/json")

    def test_a_json_battle_change_answers_in_the_battle_payload(self):
        response = self.post_json(
            reverse("battle:control", args=[self.battle.pk]),
            {"control_mode": control.MANUAL},
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["control_mode"], control.MANUAL)
        self.assertTrue(payload["advance"]["enabled"])

    def test_a_json_combatant_change_answers_in_the_battle_payload(self):
        response = self.post_json(
            reverse(
                "battle:combatant-control", args=[self.battle.pk, self.combatant.pk]
            ),
            {"control_mode": control.MANUAL},
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        seats = {seat["combatant_id"]: seat for seat in payload["seats"]}
        self.assertEqual(seats[self.combatant.pk]["control_mode"], control.MANUAL)
        self.assertEqual(seats[self.combatant.pk]["controller"], self.owner.username)

    def test_a_junk_combatant_mode_is_refused(self):
        response = self.client.post(
            reverse(
                "battle:combatant-control", args=[self.battle.pk, self.combatant.pk]
            ),
            {"control_mode": "occasionally"},
        )
        self.assertEqual(response.status_code, 400)
        self.combatant.refresh_from_db()
        self.assertEqual(self.combatant.control_mode, control.INHERIT)

    def test_unparseable_json_is_refused_rather_than_guessed_at(self):
        for url in (
            reverse("battle:control", args=[self.battle.pk]),
            reverse(
                "battle:combatant-control", args=[self.battle.pk, self.combatant.pk]
            ),
        ):
            response = self.client.post(
                url, "{not json", content_type="application/json"
            )
            self.assertEqual(response.status_code, 400)


class GuestRunsAManualBattleTest(BaseTestCase):
    """A manual battle with no account behind it anywhere (#305 + #308).

    Manual means "its owner decides", and a guest's battle has an owner who
    has no username to print. This pins the one spelling the whole app uses
    for that person, so a guest is not shown a figure owned by nobody.
    """

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_the_guest_who_made_the_battle_owns_every_figure_in_it(self):
        session = self.client.session
        session[GUEST_KEY_SESSION_FIELD] = "guest-owner-308"
        session.save()
        battle = create_battle(
            None,
            guest_key="guest-owner-308",
            name="Guest manual",
            count=2,
            seed=STEADY_SEED,
            control_mode=control.MANUAL,
        )
        combatant = battle.combatants.first()
        self.assertEqual(control.owner_label(battle, combatant), control.GUEST_OWNER)
        self.assertEqual(
            set(manual_controllers(battle).values()), {control.GUEST_OWNER}
        )
        self.assertTrue(
            decides_for(
                Actor(user=None, guest_key="guest-owner-308"), battle, combatant
            )
        )
        self.assertFalse(
            decides_for(Actor(user=None, guest_key="somebody-else"), battle, combatant)
        )

    def test_the_guest_may_set_the_mode_from_the_page(self):
        response = self.client.post(reverse("battle:setup-create"))
        self.assertEqual(response.status_code, 302)
        draft_pk = int(response.url.rstrip("/").split("/")[-1])
        for archetype in ("armoured fighter", "archer"):
            self.client.post(
                reverse("battle:setup-add", args=[draft_pk]),
                {"kind": "fighter", "archetype": archetype},
            )
        response = self.client.post(
            reverse("battle:setup-start", args=[draft_pk]),
            {"name": "Guest control", "control_mode": control.MANUAL},
        )
        self.assertEqual(response.status_code, 302)
        battle = Battle.objects.get(name="Guest control")
        self.assertEqual(battle.control_mode, control.MANUAL)
        response = self.client.post(
            reverse("battle:control", args=[battle.pk]),
            {"control_mode": control.AUTOMATIC, "apply_to_all": "true"},
        )
        self.assertEqual(response.status_code, 302)
        battle.refresh_from_db()
        self.assertEqual(battle.control_mode, control.AUTOMATIC)


class RefusedGrappleReachesThePlayersLogTest(BaseTestCase):
    """A player's grapple that cannot happen still writes a line (#686).

    The player-facing half of the defect. ``policy`` puts ATTEMPT HTH on
    the menu whenever the actor is engaged with an adjacent enemy, without
    asking whether that enemy is already in somebody else's grip, and
    ``_human_decider`` serves that menu as the player's legal choices. Up
    to tarmar-engine v0.9.3 the engine then refused the attempt in Phase 5
    with a bare ``return``: the player saw their combatant's Forecast line
    and no outcome at all for the turn.
    """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.battle = create_battle(self.dm, name="Refused grip", count=3, seed=4242)
        state = BattleState.from_dict(self.battle.state_json)
        grabber, target, rival = state.combatants
        # All three in reach, with the target already in the rival's grip.
        grabber.position = (0, 0)
        target.position = (1, 0)
        rival.position = (1, 0)
        rival.grappling = target.combatant_id
        target.grappled_by = rival.combatant_id
        # An "Entering Hand-to-Hand" condition, or o is not offered at all
        # (tarmar-engine 0.9.5, #823): the target is the slower figure.
        target.movement_modifier = grabber.movement_modifier - 1
        self.grabber_id = grabber.combatant_id
        self.target_name = target.name
        self.grabber_name = grabber.name
        self.battle.state_json = dump_state(state)
        self.battle.save(update_fields=["state_json"])
        set_combatant_control_mode(self.battle.pk, self.grabber_id, control.MANUAL)

    def test_the_menu_still_offers_the_attempt(self):
        """Pinned because the refusal only matters while the option is
        offered: the menu does not check the target's hold, which is why
        the engine has to say something when it turns the attempt down."""
        battle, _events = advance_turn(self.battle.pk)
        self.assertEqual(battle.pending_input_json["combatant_id"], self.grabber_id)
        letters = {
            candidate["letter"] for candidate in battle.pending_input_json["candidates"]
        }
        self.assertIn("o", letters)

    def test_the_refusal_is_written_to_the_battle_log(self):
        battle, _events = advance_turn(self.battle.pk)
        attempt = next(
            candidate
            for candidate in battle.pending_input_json["candidates"]
            if candidate["letter"] == "o"
        )
        record_player_action(
            battle.pk,
            self.dm,
            combatant_id=battle.pending_input_json["combatant_id"],
            turn=battle.pending_input_json["turn"],
            letter="o",
            target_id=attempt["target_id"],
            spell_key="",
        )
        battle, _events = advance_turn(battle.pk)
        refusals = [
            event
            for event in BattleEvent.objects.filter(battle=battle, event_type="info")
            if event.payload.get("grapple_refused")
        ]
        self.assertEqual(len(refusals), 1)
        self.assertEqual(refusals[0].payload["grapple_refused"], "target_held")
        self.assertIn(self.target_name, refusals[0].message)
        self.assertIn(self.grabber_name, refusals[0].message)


# ------------------------------------------------- the choice names its pause


class ChoiceNamesItsPauseTest(BaseTestCase):
    """A submitted choice belongs to one figure on one turn (#644).

    The service used to read *which* combatant a submission was for out of
    the server's own ``pending_input_json``, so a submission composed while
    the battle awaited A and landing after it had moved to B was recorded as
    B's move whenever the option was legal for B too. The generic options
    (DODGE, DEFEND, DISENGAGE) carry no target and sit on almost every menu,
    so that overlap is the ordinary case — and the battle then logged
    "chosen by <owner>" for a decision the owner never made for that figure.
    """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.battle = create_battle(
            self.dm,
            name="Whose move",
            count=3,
            seed=7,
            control_mode=control.MANUAL,
        )

    def awaiting(self):
        """Advance to the first pause; return (battle, awaited pk, turn)."""
        battle, _events = advance_turn(self.battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        pending = battle.pending_input_json
        return battle, pending["combatant_id"], pending["turn"]

    @staticmethod
    def generic_option(battle):
        """A menu option with no target — the kind that overlaps two menus."""
        candidates = battle.pending_input_json["candidates"]
        return next(
            (one for one in candidates if one.get("target_id") is None),
            candidates[0],
        )

    def test_a_choice_composed_for_another_combatant_is_refused(self):
        battle, awaited_id, turn = self.awaiting()
        other = battle.combatants.exclude(pk=awaited_id).first()
        option = self.generic_option(battle)
        with self.assertRaisesMessage(ValueError, "the battle is waiting on"):
            record_player_action(
                battle.pk,
                self.dm,
                combatant_id=other.pk,
                turn=turn,
                letter=option["letter"],
                target_id=option.get("target_id"),
                spell_key=option.get("spell_key") or "",
            )
        battle.refresh_from_db()
        self.assertEqual(battle.turn_inputs_json, {})
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        self.assertEqual(battle.pending_input_json["combatant_id"], awaited_id)

    def test_a_choice_composed_for_another_turn_is_refused(self):
        battle, awaited_id, turn = self.awaiting()
        option = self.generic_option(battle)
        with self.assertRaisesMessage(ValueError, f"turn {turn}"):
            record_player_action(
                battle.pk,
                self.dm,
                combatant_id=awaited_id,
                turn=turn - 1,
                letter=option["letter"],
                target_id=option.get("target_id"),
                spell_key=option.get("spell_key") or "",
            )
        battle.refresh_from_db()
        self.assertEqual(battle.turn_inputs_json, {})

    def test_the_refusal_names_both_figures_so_the_reader_can_place_it(self):
        battle, awaited_id, turn = self.awaiting()
        awaited = battle.combatants.get(pk=awaited_id)
        other = battle.combatants.exclude(pk=awaited_id).first()
        option = self.generic_option(battle)
        with self.assertRaises(ValueError) as refusal:
            record_player_action(
                battle.pk,
                self.dm,
                combatant_id=other.pk,
                turn=turn,
                letter=option["letter"],
                target_id=option.get("target_id"),
                spell_key=option.get("spell_key") or "",
            )
        message = str(refusal.exception)
        self.assertIn(other.display_name, message)
        self.assertIn(awaited.display_name, message)

    def test_the_ai_hand_off_is_bound_to_its_pause_too(self):
        """Handing a decision to the AI names a figure as much as picking
        an option does, so a stale hand-off is refused on the same terms."""
        battle, awaited_id, turn = self.awaiting()
        other = battle.combatants.exclude(pk=awaited_id).first()
        with self.assertRaisesMessage(ValueError, "the battle is waiting on"):
            record_player_action(
                battle.pk,
                self.dm,
                combatant_id=other.pk,
                turn=turn,
                letter="",
                as_ai=True,
            )
        battle.refresh_from_db()
        self.assertEqual(battle.turn_inputs_json, {})

    def test_the_matching_choice_is_still_recorded(self):
        battle, awaited_id, turn = self.awaiting()
        option = self.generic_option(battle)
        battle, _events = record_player_action(
            battle.pk,
            self.dm,
            combatant_id=awaited_id,
            turn=turn,
            letter=option["letter"],
            target_id=option.get("target_id"),
            spell_key=option.get("spell_key") or "",
        )
        self.assertNotEqual(
            (battle.status, battle.pending_input_json.get("combatant_id")),
            (Battle.STATUS_AWAITING, awaited_id),
        )
