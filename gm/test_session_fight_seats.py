"""Who plays a character in a Games Master session's fight (issue #853).

Spencer's ruling: a seated character is played by its owner unless the owner
is absent and the Game Master takes it, and the ledger says so. ``/fight``
seats each character to its owner; ``battle.services.take_seat`` and
``return_seat`` move a seat between the owner and a Game Master, each
leaving a line in the battle log.
"""

from battle import control
from battle.models import Battle, BattleEvent
from battle.services import (
    TAKE_SEAT_GAME_MASTER_ONLY,
    advance_turn,
    record_player_action,
    return_seat,
    take_seat,
)
from common.test_helpers import BaseTestCase, create_character
from gm.testing import game_master_sets_the_foes_asked_for

from .fight import start_fight
from .models import GMSession


class SessionFightSeatsTest(BaseTestCase):
    """A Game Master's party of his players' characters."""

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm(username="warden")
        self.alice = self.create_user(username="alice")
        self.session = GMSession.objects.create(
            name="The Wolf Road", user=self.game_master
        )
        self.aldwin = create_character(self.alice, name="Aldwin")
        self.warden_hero = create_character(self.game_master, name="Brand")
        self.session.characters.add(self.aldwin, self.warden_hero)
        self.battle = start_fight(self.session, foes_text="a gray wolf", seed=3).battle

    def seat_of(self, character) -> object:
        return self.battle.combatants.select_related("player").get(character=character)

    def seat_lines(self) -> list[str]:
        return list(
            BattleEvent.objects.filter(
                battle=self.battle, payload__has_key="seat"
            ).values_list("message", flat=True)
        )

    def test_each_character_is_seated_to_its_owner(self):
        self.assertEqual(self.seat_of(self.aldwin).player, self.alice)
        self.assertEqual(self.seat_of(self.warden_hero).player, self.game_master)
        for character in (self.aldwin, self.warden_hero):
            self.assertEqual(self.seat_of(character).control_mode, control.MANUAL)
        self.assertEqual(
            control.mode_line(self.battle, self.seat_of(self.aldwin)),
            "Manual — played by alice",
        )

    def test_the_owner_chooses_for_their_character_and_the_game_master_cannot(
        self,
    ):
        battle = self._await(self.aldwin)
        pending = battle.pending_input_json
        candidate = pending["candidates"][0]
        with self.assertRaisesMessage(ValueError, "not your combatant"):
            record_player_action(
                battle.pk,
                self.game_master,
                combatant_id=pending["combatant_id"],
                turn=pending["turn"],
                letter=candidate["letter"],
                target_id=candidate.get("target_id"),
            )
        record_player_action(
            battle.pk,
            self.alice,
            combatant_id=pending["combatant_id"],
            turn=pending["turn"],
            letter=candidate["letter"],
            target_id=candidate.get("target_id"),
        )

    def test_a_game_master_takes_an_absent_owners_seat_and_the_log_says_so(self):
        seat, event = take_seat(
            self.battle.pk, self.seat_of(self.aldwin).pk, game_master=self.game_master
        )
        self.assertEqual(seat.player, self.game_master)
        assert event is not None
        self.assertEqual(event.payload["seat"], "taken")
        self.assertEqual(
            self.seat_lines(),
            [
                "warden, the Game Master, takes Aldwin's seat: alice, who owns "
                "the character, is absent"
            ],
        )
        battle = self._await(self.aldwin)
        pending = battle.pending_input_json
        candidate = pending["candidates"][0]
        with self.assertRaisesMessage(ValueError, "not your combatant"):
            record_player_action(
                battle.pk,
                self.alice,
                combatant_id=pending["combatant_id"],
                turn=pending["turn"],
                letter=candidate["letter"],
                target_id=candidate.get("target_id"),
            )
        record_player_action(
            battle.pk,
            self.game_master,
            combatant_id=pending["combatant_id"],
            turn=pending["turn"],
            letter=candidate["letter"],
            target_id=candidate.get("target_id"),
        )

    def test_the_owner_takes_the_seat_back_and_the_log_says_so(self):
        seat_id = self.seat_of(self.aldwin).pk
        take_seat(self.battle.pk, seat_id, game_master=self.game_master)
        seat, _event = return_seat(self.battle.pk, seat_id, user=self.alice)
        self.assertEqual(seat.player, self.alice)
        self.assertEqual(
            self.seat_lines()[-1],
            "Aldwin's seat goes back to alice, who owns the character",
        )

    def test_nobody_else_moves_a_seat(self):
        seat_id = self.seat_of(self.aldwin).pk
        bob = self.create_user(username="bob")
        with self.assertRaisesMessage(ValueError, TAKE_SEAT_GAME_MASTER_ONLY):
            take_seat(self.battle.pk, seat_id, game_master=self.alice)
        with self.assertRaisesMessage(ValueError, TAKE_SEAT_GAME_MASTER_ONLY):
            take_seat(self.battle.pk, seat_id, game_master=bob)
        take_seat(self.battle.pk, seat_id, game_master=self.game_master)
        with self.assertRaisesMessage(ValueError, "owner or the Game Master running"):
            return_seat(self.battle.pk, seat_id, user=bob)
        with self.assertRaisesMessage(ValueError, "already play"):
            take_seat(self.battle.pk, seat_id, game_master=self.game_master)
        self.assertEqual(self.seat_of(self.aldwin).player, self.game_master)

    def test_another_game_master_is_a_spectator_here(self):
        # The #853 review: take and return are the fight's runner's -- the
        # session owner -- not any Game Master account's.
        stranger = self.create_dm(username="stranger-gm")
        seat_id = self.seat_of(self.aldwin).pk
        with self.assertRaisesMessage(ValueError, TAKE_SEAT_GAME_MASTER_ONLY):
            take_seat(self.battle.pk, seat_id, game_master=stranger)
        take_seat(self.battle.pk, seat_id, game_master=self.game_master)
        with self.assertRaisesMessage(ValueError, "owner or the Game Master running"):
            return_seat(self.battle.pk, seat_id, user=stranger)
        self.login_as(stranger)
        state = self.client.get(f"/tarmar/battles/{self.battle.pk}/state/").json()
        offers = [
            (seat["take_seat_url"], seat["return_seat_url"])
            for seat in state["seats"]
            if "take_seat_url" in seat
        ]
        self.assertTrue(offers)
        self.assertEqual({offer for offer in offers}, {("", "")})

    def test_no_game_master_takes_a_seat_in_a_players_own_session_fight(self):
        # The reviewer's probe: an unrelated Game Master took alice's seat in
        # alice's own record-writing session fight.
        own = GMSession.objects.create(name="Alice's own", user=self.alice)
        mira = create_character(self.alice, name="Mira")
        own.characters.add(mira)
        # A player's own session: the foes are the Game Master's to set (#841);
        # this test is not about who chooses them, so the request stands.
        with game_master_sets_the_foes_asked_for():
            battle = start_fight(own, foes_text="a gray wolf", seed=4).battle
        self.assertTrue(battle.writes_to_records)
        seat = battle.combatants.get(character=mira)
        for game_master in (self.game_master, self.create_dm(username="other-gm")):
            with self.assertRaisesMessage(ValueError, TAKE_SEAT_GAME_MASTER_ONLY):
                take_seat(battle.pk, seat.pk, game_master=game_master)
        seat.refresh_from_db()
        self.assertEqual(seat.player, self.alice)

    def test_a_finished_fight_moves_no_seat(self):
        Battle.objects.filter(pk=self.battle.pk).update(status=Battle.STATUS_FINISHED)
        with self.assertRaisesMessage(ValueError, "over"):
            take_seat(
                self.battle.pk,
                self.seat_of(self.aldwin).pk,
                game_master=self.game_master,
            )

    def test_a_beast_has_no_owner_to_stand_in_for(self):
        wolf = self.battle.combatants.get(beast__isnull=False)
        with self.assertRaisesMessage(ValueError, "saved character"):
            take_seat(self.battle.pk, wolf.pk, game_master=self.game_master)

    def test_the_arena_offers_each_viewer_only_the_hand_off_they_may_make(self):
        aldwin_seat = self.seat_of(self.aldwin).pk
        take_url = f"/tarmar/battles/{self.battle.pk}/seats/{aldwin_seat}/take/"
        return_url = f"/tarmar/battles/{self.battle.pk}/seats/{aldwin_seat}/return/"

        def offer(user) -> dict:
            self.login_as(user)
            state = self.client.get(f"/tarmar/battles/{self.battle.pk}/state/").json()
            return next(
                seat for seat in state["seats"] if seat["combatant_id"] == aldwin_seat
            )

        self.assertEqual(offer(self.game_master)["take_seat_url"], take_url)
        self.assertEqual(offer(self.game_master)["return_seat_url"], "")
        self.assertEqual(offer(self.alice)["take_seat_url"], "")
        self.assertEqual(offer(self.alice)["return_seat_url"], "")

        # The player cannot post it either: the service holds the rule.
        self.login_as(self.alice)
        refused = self.client.post(take_url)
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["error"], TAKE_SEAT_GAME_MASTER_ONLY)
        self.assertEqual(self.seat_of(self.aldwin).player, self.alice)

        self.login_as(self.game_master)
        taken = self.client.post(take_url)
        self.assertEqual(taken.status_code, 200, taken.content)
        self.assertIn("takes Aldwin's seat", taken.json()["events"][0]["message"])
        self.assertEqual(self.seat_of(self.aldwin).player, self.game_master)
        self.assertEqual(offer(self.alice)["return_seat_url"], return_url)
        self.assertEqual(offer(self.game_master)["take_seat_url"], "")

        self.login_as(self.alice)
        returned = self.client.post(return_url)
        self.assertEqual(returned.status_code, 200, returned.content)
        self.assertEqual(self.seat_of(self.aldwin).player, self.alice)

    def _await(self, character) -> Battle:
        """Advance, handing every other awaited choice to the AI, until
        ``character``'s figure is awaited."""
        seat_id = self.seat_of(character).pk
        battle, _events = advance_turn(self.battle.pk)
        for _step in range(40):
            if battle.status != Battle.STATUS_AWAITING:
                battle, _events = advance_turn(battle.pk)
                continue
            pending = battle.pending_input_json
            if pending["combatant_id"] == seat_id:
                return battle
            battle, _events = record_player_action(
                battle.pk,
                self.game_master,
                combatant_id=pending["combatant_id"],
                turn=pending["turn"],
                letter="",
                as_ai=True,
            )
        self.fail(f"{character.name} was never awaited")
