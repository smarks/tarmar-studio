"""Lobby and remote-player services (#250): seats, claims, readiness,
start, and the pause/replay flow that feeds human choices to the engine."""

from battle.models import Battle, BattleEvent
from battle.persistence import CLASSIC_PROFILE, TARMAR_PROFILE, load_state
from battle.services import (
    advance_turn,
    claim_seat,
    create_battle,
    record_player_action,
    release_seat,
    restart_battle,
    set_seat_ready,
    start_battle,
)
from common.test_helpers import BaseTestCase, create_character


def open_battle(
    dm,
    *,
    seats=1,
    count=1,
    auto_start=False,
    seed=7,
    name="Open Pit",
    rules_profile=TARMAR_PROFILE,
):
    return create_battle(
        dm,
        name=name,
        count=count,
        seed=seed,
        player_seats=seats,
        auto_start=auto_start,
        rules_profile=rules_profile,
    )


class OpenBattleCreationTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()

    def test_player_seats_make_a_lobby_with_open_seats(self):
        battle = open_battle(self.dm, seats=2, count=1)
        self.assertEqual(battle.status, Battle.STATUS_LOBBY)
        self.assertEqual(battle.state_json, {})
        self.assertEqual(battle.initial_state_json, {})
        seats = battle.combatants.filter(is_open_seat=True)
        self.assertEqual(seats.count(), 2)
        self.assertEqual(
            list(seats.values_list("display_name", flat=True)),
            ["Open seat 1", "Open seat 2"],
        )

    def test_generated_roster_rows_carry_snapshots(self):
        battle = open_battle(self.dm, seats=1, count=1)
        generated = battle.combatants.get(is_open_seat=False)
        self.assertEqual(generated.snapshot_json["combatant_id"], generated.pk)
        self.assertIn("weapon", generated.snapshot_json)

    def test_seats_count_toward_the_field_bounds(self):
        with self.assertRaises(ValueError):
            create_battle(self.dm, name="Too big", count=10, player_seats=3, seed=1)

    def test_no_seats_is_the_classic_immediate_battle(self):
        battle = create_battle(self.dm, name="Classic", count=2, seed=3)
        self.assertEqual(battle.status, Battle.STATUS_SETUP)
        self.assertTrue(battle.state_json["combatants"])


class SeatClaimTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.battle = open_battle(self.dm, seats=2, count=1)
        self.seat = self.battle.combatants.filter(is_open_seat=True).first()

    def test_claim_generating_an_archetype(self):
        seat = claim_seat(self.battle.pk, self.seat.pk, self.player, archetype="archer")
        self.assertEqual(seat.player, self.player)
        self.assertEqual(seat.character.user, self.player)
        self.assertFalse(seat.is_ready)
        self.assertEqual(seat.snapshot_json["combatant_id"], seat.pk)
        self.assertEqual(seat.snapshot_json["archetype"], "archer")
        self.assertNotEqual(seat.display_name, "Open seat 1")

    def test_claim_with_own_character(self):
        character = create_character(self.player, name="Brannor")
        seat = claim_seat(
            self.battle.pk, self.seat.pk, self.player, character_id=character.pk
        )
        self.assertEqual(seat.character, character)
        self.assertEqual(seat.display_name, "Brannor")
        self.assertEqual(seat.snapshot_json["name"], "Brannor")

    def test_claim_someone_elses_character_is_refused(self):
        other = self.create_user(username="other")
        character = create_character(other, name="NotYours")
        with self.assertRaisesMessage(ValueError, "not one of your living characters"):
            claim_seat(
                self.battle.pk, self.seat.pk, self.player, character_id=character.pk
            )
        self.seat.refresh_from_db()
        self.assertIsNone(self.seat.player)

    def test_taken_seat_refuses_a_second_claim(self):
        claim_seat(self.battle.pk, self.seat.pk, self.player, archetype="archer")
        rival = self.create_user(username="rival")
        with self.assertRaisesMessage(ValueError, "already taken"):
            claim_seat(self.battle.pk, self.seat.pk, rival, archetype="wizard")

    def test_claim_is_a_conditional_update_not_a_read_then_write(self):
        """The race guard: the seat is taken with one UPDATE filtered on
        ``player__isnull=True``, so of two transactions racing past the
        battle-status check, exactly one row-update wins. Simulated here by
        occupying the seat between the caller's read and the claim."""
        rival = self.create_user(username="rival")
        self.battle.combatants.filter(pk=self.seat.pk).update(player=rival)
        with self.assertRaisesMessage(ValueError, "already taken"):
            claim_seat(self.battle.pk, self.seat.pk, self.player, archetype="archer")

    def test_one_seat_per_user(self):
        seats = list(self.battle.combatants.filter(is_open_seat=True))
        claim_seat(self.battle.pk, seats[0].pk, self.player, archetype="archer")
        with self.assertRaisesMessage(ValueError, "already hold a seat"):
            claim_seat(self.battle.pk, seats[1].pk, self.player, archetype="wizard")

    def test_claim_needs_exactly_one_of_character_or_archetype(self):
        with self.assertRaises(ValueError):
            claim_seat(self.battle.pk, self.seat.pk, self.player)
        character = create_character(self.player, name="Both")
        with self.assertRaises(ValueError):
            claim_seat(
                self.battle.pk,
                self.seat.pk,
                self.player,
                character_id=character.pk,
                archetype="archer",
            )

    def test_duplicate_display_names_get_numbered(self):
        generated = self.battle.combatants.get(is_open_seat=False)
        character = create_character(self.player, name=generated.display_name)
        seat = claim_seat(
            self.battle.pk, self.seat.pk, self.player, character_id=character.pk
        )
        self.assertEqual(seat.display_name, f"{generated.display_name} (2)")

    def test_release_reopens_the_seat(self):
        claim_seat(self.battle.pk, self.seat.pk, self.player, archetype="archer")
        seat = release_seat(self.battle.pk, self.seat.pk, self.player)
        self.assertIsNone(seat.player)
        self.assertIsNone(seat.character)
        self.assertEqual(seat.snapshot_json, {})
        self.assertEqual(seat.display_name, "Open seat 1")

    def test_only_the_holder_or_a_dm_releases(self):
        claim_seat(self.battle.pk, self.seat.pk, self.player, archetype="archer")
        rival = self.create_user(username="rival")
        with self.assertRaises(ValueError):
            release_seat(self.battle.pk, self.seat.pk, rival)
        release_seat(self.battle.pk, self.seat.pk, self.dm)
        self.seat.refresh_from_db()
        self.assertIsNone(self.seat.player)


class LobbyStartTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()

    def test_start_requires_ready_players(self):
        battle = open_battle(self.dm, seats=1, count=1)
        seat = battle.combatants.get(is_open_seat=True)
        claim_seat(battle.pk, seat.pk, self.player, archetype="archer")
        with self.assertRaisesMessage(ValueError, "must be ready"):
            start_battle(battle.pk)

    def test_start_builds_state_and_drops_unclaimed_seats(self):
        battle = open_battle(self.dm, seats=2, count=1)
        seat = battle.combatants.filter(is_open_seat=True).first()
        claim_seat(battle.pk, seat.pk, self.player, archetype="archer")
        set_seat_ready(battle.pk, seat.pk, self.player, ready=True)
        battle = start_battle(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_SETUP)
        self.assertEqual(battle.combatants.count(), 2)  # unclaimed seat gone
        state_ids = {c["combatant_id"] for c in battle.state_json["combatants"]}
        self.assertEqual(state_ids, set(battle.combatants.values_list("pk", flat=True)))
        positions = {(c["q"], c["r"]) for c in battle.state_json["combatants"]}
        self.assertEqual(len(positions), 2)  # everyone placed, distinctly
        self.assertEqual(battle.initial_state_json, battle.state_json)

    def test_start_refuses_a_field_of_one(self):
        battle = open_battle(self.dm, seats=1, count=1)
        with self.assertRaisesMessage(ValueError, "at least 2"):
            start_battle(battle.pk)

    def test_ready_flag_needs_your_own_seat(self):
        battle = open_battle(self.dm, seats=1, count=1)
        seat = battle.combatants.get(is_open_seat=True)
        claim_seat(battle.pk, seat.pk, self.player, archetype="archer")
        rival = self.create_user(username="rival")
        with self.assertRaises(ValueError):
            set_seat_ready(battle.pk, seat.pk, rival, ready=True)

    def test_auto_start_fires_when_everyone_is_ready(self):
        battle = open_battle(self.dm, seats=1, count=1, auto_start=True)
        seat = battle.combatants.get(is_open_seat=True)
        claim_seat(battle.pk, seat.pk, self.player, archetype="archer")
        battle, _seat = set_seat_ready(battle.pk, seat.pk, self.player, ready=True)
        self.assertEqual(battle.status, Battle.STATUS_SETUP)
        self.assertTrue(battle.state_json["combatants"])

    def test_without_auto_start_ready_leaves_the_lobby_open(self):
        battle = open_battle(self.dm, seats=1, count=1)
        seat = battle.combatants.get(is_open_seat=True)
        claim_seat(battle.pk, seat.pk, self.player, archetype="archer")
        battle, seat = set_seat_ready(battle.pk, seat.pk, self.player, ready=True)
        self.assertEqual(battle.status, Battle.STATUS_LOBBY)
        self.assertTrue(seat.is_ready)

    def test_lobby_refuses_advance_and_restart(self):
        battle = open_battle(self.dm, seats=1, count=1)
        with self.assertRaises(ValueError):
            advance_turn(battle.pk)
        with self.assertRaises(ValueError):
            restart_battle(battle.pk)

    def test_claims_close_when_the_battle_starts(self):
        battle = open_battle(self.dm, seats=2, count=1)
        seats = list(battle.combatants.filter(is_open_seat=True))
        claim_seat(battle.pk, seats[0].pk, self.player, archetype="archer")
        set_seat_ready(battle.pk, seats[0].pk, self.player, ready=True)
        start_battle(battle.pk)
        rival = self.create_user(username="rival")
        with self.assertRaisesMessage(ValueError, "no longer open"):
            claim_seat(battle.pk, seats[1].pk, rival, archetype="wizard")


def classic_lobby(test, *, seats=1, count=1, seed=515):
    """A classic-profile lobby with one seat claimed and ready."""
    battle = open_battle(
        test.dm,
        seats=seats,
        count=count,
        seed=seed,
        name="Classic Pit",
        rules_profile=CLASSIC_PROFILE,
    )
    seat = battle.combatants.filter(is_open_seat=True).first()
    seat = claim_seat(battle.pk, seat.pk, test.player, archetype="archer")
    set_seat_ready(battle.pk, seat.pk, test.player, ready=True)
    return battle, seat


class ClassicLobbyTest(BaseTestCase):
    """A classic battle can be opened to players (#251).

    Until this slice ``create_battle`` refused player seats outright for the
    classic profile: the lobby assembled ``state_json`` from Tarmar per-row
    snapshots and classic had no assembly of its own, so no human could be
    handed a classic decision at all. The seam turned out to be one call —
    the crossing already reads the same ``CombatantState`` snapshots the
    lobby stores per row — and the whole of it is *when* it runs: at the
    start, over the settled roster, rather than at creation.
    """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()

    def test_a_classic_battle_opens_player_seats(self):
        battle = open_battle(self.dm, seats=2, count=1, rules_profile=CLASSIC_PROFILE)
        self.assertEqual(battle.status, Battle.STATUS_LOBBY)
        self.assertEqual(battle.rules_profile, CLASSIC_PROFILE)
        self.assertEqual(battle.state_json, {})
        self.assertEqual(battle.combatants.filter(is_open_seat=True).count(), 2)

    def test_nothing_crosses_while_the_roster_is_unsettled(self):
        """The crossing waits for the seats: a lobby has no figures yet.

        Crossing at creation would rebuild a roster that still held open
        seats and characters nobody had brought — and would file its losses
        against combatants that may never exist.
        """
        battle = open_battle(self.dm, seats=2, count=1, rules_profile=CLASSIC_PROFILE)
        self.assertEqual(battle.events.count(), 0)
        self.assertEqual(battle.initial_state_json, {})

    def test_start_crosses_the_settled_roster_into_classic_figures(self):
        battle, _seat = classic_lobby(self, seats=1, count=1)
        battle = start_battle(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_SETUP)
        state = load_state(battle.state_json, CLASSIC_PROFILE)
        self.assertEqual(len(state.figures), battle.combatants.count())
        self.assertEqual(battle.initial_state_json, battle.state_json)

    def test_every_figure_uid_is_its_combatant_row(self):
        """``profiles.CLASSIC_UID_IS_ROW_PK`` holds through the lobby too.

        The claimed seat is the case worth pinning: its figure is built from
        a snapshot written at claim time, by a different code path than the
        generated roster's.
        """
        battle, seat = classic_lobby(self, seats=1, count=1)
        battle = start_battle(battle.pk)
        state = load_state(battle.state_json, CLASSIC_PROFILE)
        self.assertEqual(
            {figure.uid for figure in state.figures},
            {str(pk) for pk in battle.combatants.values_list("pk", flat=True)},
        )
        self.assertIn(str(seat.pk), {figure.uid for figure in state.figures})

    def test_an_unclaimed_seat_never_becomes_a_figure(self):
        battle, _seat = classic_lobby(self, seats=2, count=1)
        battle = start_battle(battle.pk)
        self.assertEqual(battle.combatants.count(), 2)
        state = load_state(battle.state_json, CLASSIC_PROFILE)
        self.assertEqual(len(state.figures), 2)

    def test_the_crossing_is_logged_when_the_lobby_closes(self):
        """What the crossing costs is filed against the settled roster.

        A player's own character is rebuilt by classic's rules like any
        other figure, so the note belongs to the start, not to creation.
        """
        battle, _seat = classic_lobby(self, seats=1, count=2, seed=4242)
        self.assertEqual(battle.events.count(), 0)
        battle = start_battle(battle.pk)
        for event in battle.events.all():
            self.assertTrue(event.message)
        sequences = list(battle.events.values_list("sequence", flat=True))
        self.assertEqual(len(sequences), len(set(sequences)))

    def test_a_seated_player_is_asked_under_classic_rules(self):
        """The point of the slice: a human is asked, not played by the AI.

        The two profiles name a combatant differently — a classic figure
        carries ``str(pk)`` as its uid — so this also pins that the turn
        loop's controller lookup and ``ClassicProfile.actor_id`` agree.
        """
        battle, seat = classic_lobby(self, seats=1, count=1)
        battle = start_battle(battle.pk)
        battle, events = advance_turn(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        self.assertEqual(events, [])
        self.assertEqual(battle.pending_input_json["combatant_id"], seat.pk)
        self.assertTrue(battle.pending_input_json["candidates"])

    def test_a_classic_choice_completes_the_turn_and_credits_the_player(self):
        battle, seat = classic_lobby(self, seats=1, count=1)
        battle = start_battle(battle.pk)
        battle, _events = advance_turn(battle.pk)
        chosen = battle.pending_input_json["candidates"][0]
        battle, events = record_player_action(
            battle.pk,
            self.player,
            combatant_id=battle.pending_input_json["combatant_id"],
            turn=battle.pending_input_json["turn"],
            letter=chosen["letter"],
            target_id=chosen.get("target_id"),
            spell_key=chosen.get("spell_key") or "",
        )
        self.assertEqual(battle.turn_number, 1)
        self.assertTrue(events)
        decision = battle.events.filter(
            event_type="decision", actor=seat.display_name
        ).get()
        self.assertIn(f"chosen by {self.player.username}", decision.message)

    def test_a_classic_lobby_restarts_to_its_crossed_roster(self):
        """Restart rewinds to the frozen classic state, not to the lobby."""
        battle, _seat = classic_lobby(self, seats=1, count=1)
        battle = start_battle(battle.pk)
        frozen = battle.initial_state_json
        battle = restart_battle(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_SETUP)
        self.assertEqual(battle.state_json, frozen)
        self.assertEqual(len(load_state(battle.state_json, CLASSIC_PROFILE).figures), 2)


def started_battle_with_player(test, *, seed=11, archetype="armoured fighter"):
    """One generated AI fighter vs one player-claimed fighter, started."""
    battle = open_battle(test.dm, seats=1, count=1, seed=seed)
    seat = battle.combatants.get(is_open_seat=True)
    seat = claim_seat(battle.pk, seat.pk, test.player, archetype=archetype)
    set_seat_ready(battle.pk, seat.pk, test.player, ready=True)
    return start_battle(battle.pk), seat


class RemotePlayerTurnTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()

    def test_advance_pauses_awaiting_the_players_choice(self):
        battle, seat = started_battle_with_player(self)
        battle, events = advance_turn(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        self.assertEqual(events, [])
        self.assertEqual(battle.events.count(), 0)  # partial turn rolled back
        self.assertEqual(battle.turn_number, 0)
        pending = battle.pending_input_json
        self.assertEqual(pending["combatant_id"], seat.pk)
        self.assertEqual(pending["turn"], 1)
        self.assertTrue(pending["candidates"])
        for candidate in pending["candidates"]:
            self.assertIn("letter", candidate)
            self.assertIn("score", candidate)
            self.assertIn("rationale", candidate)

    def test_recorded_choice_completes_the_turn_and_credits_the_player(self):
        battle, seat = started_battle_with_player(self)
        battle, _events = advance_turn(battle.pk)
        chosen = battle.pending_input_json["candidates"][0]
        battle, events = record_player_action(
            battle.pk,
            self.player,
            combatant_id=battle.pending_input_json["combatant_id"],
            turn=battle.pending_input_json["turn"],
            letter=chosen["letter"],
            target_id=chosen.get("target_id"),
            spell_key=chosen.get("spell_key") or "",
        )
        self.assertEqual(battle.status, Battle.STATUS_PAUSED)
        self.assertEqual(battle.turn_number, 1)
        self.assertEqual(battle.pending_input_json, {})
        self.assertEqual(battle.turn_inputs_json, {})
        self.assertTrue(events)
        decision = battle.events.filter(
            event_type="decision", actor=seat.display_name
        ).get()
        self.assertIn(f"chosen by {self.player.username}", decision.message)
        self.assertEqual(decision.payload["chosen"]["letter"], chosen["letter"])
        self.assertTrue(decision.payload["candidates"])

    def test_replay_reproduces_the_same_menu_and_rolls(self):
        """Determinism: pausing and re-running yields byte-identical
        candidates, and the completed turn's pre-decision rolls match what
        any earlier partial run would have rolled (same seed, same state)."""
        battle, seat = started_battle_with_player(self)
        battle, _events = advance_turn(battle.pk)
        first_menu = battle.pending_input_json["candidates"]
        # A second Advance while awaiting re-runs the partial turn: the
        # replay must land on the identical menu.
        battle, _events = advance_turn(battle.pk)
        self.assertEqual(battle.pending_input_json["candidates"], first_menu)
        chosen = first_menu[0]
        battle, _events = record_player_action(
            battle.pk,
            self.player,
            combatant_id=battle.pending_input_json["combatant_id"],
            turn=battle.pending_input_json["turn"],
            letter=chosen["letter"],
            target_id=chosen.get("target_id"),
            spell_key=chosen.get("spell_key") or "",
        )
        decision = battle.events.filter(
            event_type="decision", actor=seat.display_name
        ).get()
        # The log lists exactly the options offered. The menu also carries
        # each placing option's legal hexes for the picker (#819, #866),
        # which the log leaves out: the hex chosen is on its "chosen".
        offered = [
            {key: value for key, value in entry.items() if key != "destinations"}
            for entry in first_menu
        ]
        self.assertTrue(any("destinations" in entry for entry in first_menu))
        self.assertEqual(decision.payload["candidates"], offered)

    def test_choice_off_the_menu_is_refused(self):
        battle, _seat = started_battle_with_player(self)
        battle, _events = advance_turn(battle.pk)
        with self.assertRaisesMessage(ValueError, "not on the pending menu"):
            record_player_action(
                battle.pk,
                self.player,
                combatant_id=battle.pending_input_json["combatant_id"],
                turn=battle.pending_input_json["turn"],
                letter="zz",
            )

    def test_only_the_seated_player_may_choose(self):
        battle, _seat = started_battle_with_player(self)
        battle, _events = advance_turn(battle.pk)
        chosen = battle.pending_input_json["candidates"][0]
        rival = self.create_user(username="rival")
        with self.assertRaisesMessage(ValueError, "not your combatant"):
            record_player_action(
                battle.pk,
                rival,
                combatant_id=battle.pending_input_json["combatant_id"],
                turn=battle.pending_input_json["turn"],
                letter=chosen["letter"],
                target_id=chosen.get("target_id"),
            )
        with self.assertRaisesMessage(ValueError, "not your combatant"):
            record_player_action(
                battle.pk,
                self.dm,
                combatant_id=battle.pending_input_json["combatant_id"],
                turn=battle.pending_input_json["turn"],
                letter=chosen["letter"],
                target_id=chosen.get("target_id"),
            )

    def test_dm_may_hand_the_choice_to_the_ai(self):
        battle, seat = started_battle_with_player(self)
        battle, _events = advance_turn(battle.pk)
        top = max(
            battle.pending_input_json["candidates"],
            key=lambda candidate: candidate["score"],
        )
        awaited_combatant_id = battle.pending_input_json["combatant_id"]
        awaited_turn = battle.pending_input_json["turn"]
        # The bystander's attempt goes first: once the DM's hand-off has
        # resumed the turn there is no pause left to refuse them for, and
        # the check would pass for the wrong reason.
        rival = self.create_user(username="rival")
        with self.assertRaisesMessage(ValueError, "hand the choice to the AI"):
            record_player_action(
                battle.pk,
                rival,
                combatant_id=awaited_combatant_id,
                turn=awaited_turn,
                letter="",
                as_ai=True,
            )
        battle, _events = record_player_action(
            battle.pk,
            self.dm,
            combatant_id=awaited_combatant_id,
            turn=awaited_turn,
            letter="",
            as_ai=True,
        )
        self.assertEqual(battle.turn_number, 1)
        decision = battle.events.filter(
            event_type="decision", actor=seat.display_name
        ).get()
        self.assertEqual(decision.payload["chosen"]["letter"], top["letter"])

    def test_nothing_pending_refuses_a_choice(self):
        battle, _seat = started_battle_with_player(self)
        with self.assertRaisesMessage(ValueError, "No player input is pending"):
            record_player_action(
                battle.pk,
                self.player,
                combatant_id=battle.combatants.first().pk,
                turn=1,
                letter="a",
            )

    def test_stale_recorded_input_is_dropped_and_reasked(self):
        battle, _seat = started_battle_with_player(self)
        battle, _events = advance_turn(battle.pk)
        seat_key = str(battle.pending_input_json["combatant_id"])
        battle.turn_inputs_json = {
            seat_key: {"letter": "zz", "target_id": None, "spell_key": ""}
        }
        battle.save(update_fields=["turn_inputs_json"])
        battle, events = advance_turn(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        self.assertEqual(events, [])
        self.assertEqual(battle.turn_inputs_json, {})

    def test_restart_clears_pending_input(self):
        battle, _seat = started_battle_with_player(self)
        battle, _events = advance_turn(battle.pk)
        battle = restart_battle(battle.pk)
        self.assertEqual(battle.pending_input_json, {})
        self.assertEqual(battle.turn_inputs_json, {})
        self.assertEqual(battle.status, Battle.STATUS_SETUP)

    def test_ai_only_battles_never_pause(self):
        battle = create_battle(self.dm, name="Pure AI", count=2, seed=5)
        battle, events = advance_turn(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_PAUSED)
        self.assertTrue(events)
        self.assertEqual(BattleEvent.objects.filter(battle=battle).count(), len(events))
