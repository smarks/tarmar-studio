"""A player withdraws their character from a Game Master's session (#871).

Ruling: a player's withdrawal leaves the session's records intact. Only the
``GMSession.characters`` link goes; the history, the ``SessionCharacter``
row and any fight stay.
"""

from django.urls import reverse

from battle.models import Battle, BattleEvent
from battle.profiles import for_battle
from battle.refusal_words import refusal_words
from battle.services import restart_battle, take_seat, withdraw_from_fight
from common.test_helpers import BaseTestCase, create_character

from .fight import start_fight
from .models import GMSession, SessionCharacter
from .retrieval import (
    READER_ONLY_KINDS,
    SESSION_NOTICE_KIND,
    is_played_entry,
    model_messages,
)
from .roster import roster
from .summary import entries_to_fold
from .test_campaign_continue import played_session
from .test_fight import play_turn
from .turn_engine.history import model_history
from .turn_engine.state import continue_campaign, previous_campaign, recap_pending
from .views import _unplayed_continuation
from .withdraw import holding_sessions, withdraw

HOLDING_LIST = 'id="sessions-holding"'


class WithdrawTest(BaseTestCase):
    """The sheet lists the sessions holding the character; the owner withdraws."""

    def setUp(self) -> None:
        super().setUp()
        self.game_master = self.create_dm(username="warden")
        self.player = self.create_user(username="alice")
        self.character = create_character(self.player, name="Aldwin")
        self.session = GMSession.objects.create(
            user=self.game_master, name="The Barrow"
        )
        self.session.characters.add(self.character)
        self.session.history = [{"role": "assistant", "content": "Aldwin enters."}]
        self.session.save()
        SessionCharacter.objects.create(
            session=self.session,
            character=self.character,
            label="the knight",
            game_master_skill_points=3,
        )

    def sheet(self, user) -> str:
        self.logout()
        self.login_as(user)
        response = self.client.get(reverse("characters:edit", args=[self.character.pk]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def post_withdraw(self, user, session_id: object):
        self.logout()
        self.login_as(user)
        return self.client.post(
            reverse("gm:character_withdraw", args=[self.character.pk]),
            {"session_id": session_id},
        )

    def test_the_owner_sees_the_session_and_its_game_master(self) -> None:
        html = self.sheet(self.player)
        self.assertIn(HOLDING_LIST, html)
        self.assertIn("<strong>The Barrow</strong>, run by warden", html)
        self.assertIn("Withdraw from this session", html)

    def test_nobody_else_sees_the_list(self) -> None:
        self.assertNotIn(HOLDING_LIST, self.sheet(self.game_master))
        self.assertEqual(holding_sessions(self.character, self.game_master), [])

    def test_a_players_own_session_is_not_listed(self) -> None:
        own = GMSession.objects.create(user=self.player, name="Mine")
        own.characters.add(self.character)
        listed = holding_sessions(self.character, self.player)
        self.assertEqual([entry.session for entry in listed], [self.session])

    def test_withdrawing_removes_the_link_and_keeps_the_records(self) -> None:
        response = self.post_withdraw(self.player, self.session.pk)
        self.assertRedirects(
            response,
            reverse("characters:edit", args=[self.character.pk]),
            fetch_redirect_response=False,
        )
        self.assertFalse(self.session.characters.filter(pk=self.character.pk).exists())
        self.session.refresh_from_db()
        # Nothing recorded is taken away; the withdrawal is added after it.
        self.assertEqual(
            self.session.history[0], {"role": "assistant", "content": "Aldwin enters."}
        )
        self.assertEqual(len(self.session.history), 2)
        link = SessionCharacter.objects.get(
            session=self.session, character=self.character
        )
        self.assertEqual((link.label, link.game_master_skill_points), ("the knight", 3))
        self.assertNotIn(HOLDING_LIST, self.sheet(self.player))

    def test_a_game_master_reattaching_brings_the_label_back(self) -> None:
        withdraw(self.character, self.session.pk, self.player)
        self.session.characters.add(self.character)
        (entry,) = roster(self.session)
        self.assertEqual(entry.label, "the knight")

    def test_only_the_owner_withdraws(self) -> None:
        stranger = self.create_user(username="bob")
        self.assertEqual(self.post_withdraw(stranger, self.session.pk).status_code, 404)
        self.assertEqual(
            self.post_withdraw(self.game_master, self.session.pk).status_code, 404
        )
        self.assertTrue(self.session.characters.filter(pk=self.character.pk).exists())
        with self.assertRaises(PermissionError):
            withdraw(self.character, self.session.pk, stranger)

    def test_a_session_that_does_not_hold_it_changes_nothing(self) -> None:
        other = GMSession.objects.create(user=self.game_master, name="Elsewhere")
        self.assertFalse(withdraw(self.character, other.pk, self.player))
        self.assertEqual(self.post_withdraw(self.player, "nine").status_code, 400)
        self.assertTrue(self.session.characters.filter(pk=self.character.pk).exists())

    def test_the_sheet_links_the_owner_to_the_fight_they_sit_in(self) -> None:
        battle = start_fight(self.session, foes_text="a gray wolf", seed=2).battle
        html = self.sheet(self.player)
        self.assertIn(
            f'<a class="sessions-holding-fight" href="/tarmar/battles/{battle.pk}/">',
            html,
        )
        (entry,) = holding_sessions(self.character, self.player)
        self.assertEqual(entry.battle, battle)


class WithdrawMidFightTest(BaseTestCase):
    """The withdrawal wins (#871 review; the reviewer's probed sequence).

    Coordinator's ruling under the standing rule, Spencer may overrule: the
    figure leaves the live fight, logged; the fight writes nothing more to
    the record; a Game Master's hold on the seat is released; and no Game
    Master takes the seat of a character withdrawn from the session.
    """

    def setUp(self) -> None:
        super().setUp()
        self.game_master = self.create_dm(username="warden")
        self.player = self.create_user(username="alice")
        self.aldwin = create_character(self.player, name="Aldwin")
        self.brand = create_character(self.game_master, name="Brand")
        self.session = GMSession.objects.create(user=self.game_master, name="Road")
        self.session.characters.add(self.aldwin, self.brand)
        self.battle = start_fight(self.session, foes_text="a gray wolf", seed=3).battle
        self.seat = self.battle.combatants.get(character=self.aldwin)

    def on_board(self) -> set[int]:
        battle = Battle.objects.get(pk=self.battle.pk)
        state = for_battle(battle).load(battle.state_json)
        return {figure.combatant_id for figure in state.combatants}

    def test_withdraw_then_advance_then_take(self) -> None:
        take_seat(self.battle.pk, self.seat.pk, game_master=self.game_master)
        self.aldwin.refresh_from_db()
        record_before = (
            self.aldwin.fatigue_damage,
            self.aldwin.body_damage,
            self.aldwin.is_alive,
        )
        self.assertTrue(withdraw(self.aldwin, self.session.pk, self.player))

        # Off the board, logged, and the hold back with the owner.
        self.assertNotIn(self.seat.pk, self.on_board())
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.player, self.player)
        (line,) = BattleEvent.objects.filter(
            battle=self.battle, payload__has_key="withdrawn"
        )
        self.assertEqual(
            line.message,
            "Aldwin leaves the fight: alice, who owns the character, withdrew "
            "them from the session",
        )

        # The fight goes on without him, to its end, and writes nothing more.
        battle = Battle.objects.get(pk=self.battle.pk)
        for _turn in range(60):
            if battle.is_over:
                break
            battle = play_turn(battle, self.game_master)
        self.aldwin.refresh_from_db()
        self.assertEqual(
            (self.aldwin.fatigue_damage, self.aldwin.body_damage, self.aldwin.is_alive),
            record_before,
        )
        if not battle.is_over:
            with self.assertRaisesMessage(
                ValueError,
                refusal_words().services.withdrawn_seat.format(
                    name=self.seat.display_name
                ),
            ):
                take_seat(self.battle.pk, self.seat.pk, game_master=self.game_master)

    def test_the_game_master_cannot_take_the_withdrawn_seat(self) -> None:
        withdraw(self.aldwin, self.session.pk, self.player)
        with self.assertRaisesMessage(
            ValueError,
            refusal_words().services.withdrawn_seat.format(name=self.seat.display_name),
        ):
            take_seat(self.battle.pk, self.seat.pk, game_master=self.game_master)
        self.login_as(self.game_master)
        state = self.client.get(f"/tarmar/battles/{self.battle.pk}/state/").json()
        (offer,) = [
            seat for seat in state["seats"] if seat["combatant_id"] == self.seat.pk
        ]
        self.assertEqual(offer["take_seat_url"], "")

    def test_an_awaited_choice_for_the_withdrawn_figure_is_let_go(self) -> None:
        Battle.objects.filter(pk=self.battle.pk).update(
            status=Battle.STATUS_AWAITING,
            pending_input_json={"combatant_id": self.seat.pk, "turn": 1},
            turn_inputs_json={str(self.seat.pk): {"letter": "a"}},
        )
        withdraw(self.aldwin, self.session.pk, self.player)
        battle = Battle.objects.get(pk=self.battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_PAUSED)
        self.assertEqual(battle.pending_input_json, {})
        self.assertEqual(battle.turn_inputs_json, {})

    def test_a_restart_keeps_the_withdrawn_off_the_board(self) -> None:
        withdraw(self.aldwin, self.session.pk, self.player)
        restart_battle(self.battle.pk, is_game_master=True)
        self.assertNotIn(self.seat.pk, self.on_board())
        self.assertTrue(
            BattleEvent.objects.filter(
                battle=self.battle, payload__has_key="withdrawn_kept_off"
            ).exists()
        )

    def test_the_withdrawal_is_recorded_on_the_character_and_in_the_session(
        self,
    ) -> None:
        withdraw(self.aldwin, self.session.pk, self.player)
        self.aldwin.refresh_from_db()
        entry = self.aldwin.history[-1]
        self.assertEqual(
            (entry["field"], entry["old"], entry["new"], entry["by"]),
            ("Games Master session", "Road", "withdrawn", "alice"),
        )
        self.session.refresh_from_db()
        self.assertEqual(
            self.session.history[-1],
            {
                "role": "assistant",
                "kind": SESSION_NOTICE_KIND,
                "content": "alice withdrew Aldwin from this session; Aldwin's "
                "sheet no longer comes to the table.",
            },
        )

    def test_the_withdrawal_notice_never_reaches_the_model(self) -> None:
        # Second review of #871: filed as the assistant with no kind, the
        # line went to the model as its own words and showed as a GM reply.
        self.session.history = [
            {"role": "user", "content": "We ride on."},
            {"role": "assistant", "content": "The road bends north."},
        ]
        self.session.save()
        withdraw(self.aldwin, self.session.pk, self.player)
        self.session.refresh_from_db()
        line = "alice withdrew Aldwin from this session"
        self.assertIn(line, self.session.history[-1]["content"])
        for wire in (
            model_history(self.session.history),
            model_messages(self.session.history, num_ctx=65536),
            entries_to_fold(self.session.history, already_folded=0),
        ):
            self.assertFalse(
                any(line in str(entry.get("content", "")) for entry in wire), wire
            )
        # The Game Master reading the session sees it, as a notice.
        self.login_as(self.game_master)
        page = self.client.get(reverse("gm:chat") + f"?session={self.session.pk}")
        html = page.content.decode()
        self.assertIn('<div class="message assistant notice">', html)
        self.assertIn(line, html)

    def test_a_finished_fight_is_left_as_it_ended(self) -> None:
        Battle.objects.filter(pk=self.battle.pk).update(status=Battle.STATUS_FINISHED)
        withdraw(self.aldwin, self.session.pk, self.player)
        self.assertIn(self.seat.pk, self.on_board())
        self.assertFalse(
            withdraw_from_fight(self.battle.pk, self.aldwin, by=self.player)
        )


class WithdrawalIsNotPlayTest(BaseTestCase):
    """A reader-only notice never counts as play (third review of #871).

    The reviewer's probe: a continuation, a character attached and then
    withdrawn before any turn. The notice used to count as play, so the
    recap stopped being pending (the model would start with no story) and a
    second "Continue" opened yet another continuation.
    """

    def setUp(self) -> None:
        super().setUp()
        self.game_master = self.create_dm(username="warden")
        self.player = self.create_user(username="alice")
        self.aldwin = create_character(self.player, name="Aldwin")
        self.previous = played_session(self.game_master)
        self.continuation = continue_campaign(self.previous, None)
        self.continuation.characters.add(self.aldwin)

    def test_a_continuation_with_only_a_withdrawal_notice_is_unplayed(self) -> None:
        self.assertTrue(recap_pending(self.continuation))
        self.assertTrue(withdraw(self.aldwin, self.continuation.pk, self.player))
        self.continuation.refresh_from_db()
        self.assertEqual(
            [entry.get("kind") for entry in self.continuation.history],
            [SESSION_NOTICE_KIND],
        )
        self.assertTrue(recap_pending(self.continuation))
        self.assertEqual(_unplayed_continuation(self.previous), self.continuation)
        carry = previous_campaign(self.game_master)
        assert carry is not None
        self.assertEqual(carry.session, self.previous)

    def test_every_reader_only_kind_is_not_play(self) -> None:
        # After the rebase onto batch D2 its fight-verdict kind joins
        # READER_ONLY_KINDS, and this covers it with no change here.
        for kind in READER_ONLY_KINDS:
            self.assertFalse(
                is_played_entry({"role": "assistant", "kind": kind, "content": "x"}),
                kind,
            )
        self.assertTrue(is_played_entry({"role": "assistant", "content": "x"}))
        self.assertFalse(
            is_played_entry({"role": "user", "kind": "canon", "content": "x"})
        )
