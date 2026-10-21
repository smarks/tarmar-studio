"""The board writes wounds and deaths to saved characters as it goes (#750, #751).

A battle used to change no character at all: the Games Master's chat wrote a
session fight's deaths on the player's next message, and every other battle
wrote nothing. Now a fight that writes to records -- a Games Master session's
fight, or a battle a Game Master runs -- writes each wound and death to the
record in the transaction of the turn that dealt it, through
``characters.services``; a character enters the next fight as hurt as the
last one left them; and a restart hands the abandoned run's losses back
without undoing a death. A player's own battle writes nothing (coordinator's
ruling under the standing rule; Spencer may overrule).
"""

import json

from django.urls import reverse

from battle import control, profiles
from battle.engine.state import BattleState
from battle.models import Battle
from battle.persistence import CLASSIC_PROFILE, TARMAR_PROFILE, load_state
from battle.refusal_words import refusal_words
from battle.services import (
    StalePools,
    advance_turn,
    claim_seat,
    create_battle,
    fight_reports,
    resnapshot_pools,
    restart_battle,
    results_recorded,
    set_combatant_control_mode,
    set_seat_ready,
    stale_pools,
    start_battle,
    withheld_characters,
)
from battle.tests.fixed_refusals import with_fixed_refusals
from battle.wording import words
from characters import services as character_services
from characters.models import Character
from characters.services import FightReport
from common.test_helpers import BaseTestCase, create_character, says, sentence_parts
from gm.models import GMSession

#: Enough turns for a one-on-one fight to be decided.
TURN_LIMIT = 60

#: Where a fight is over.
OVER = (Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE)


def tarmar_state(battle: Battle) -> BattleState:
    """The battle's current engine state."""
    state = load_state(Battle.objects.get(pk=battle.pk).state_json, TARMAR_PROFILE)
    assert isinstance(state, BattleState)
    return state


class FightWriteBackTest(BaseTestCase):
    """A Game Master's battle: a player's saved character against a generated fighter."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("player")
        self.game_master = self.create_dm()
        self.character = create_character(self.player, name="Aldwin")

    def make_battle(self, *, seed: int = 3, maker=None) -> Battle:
        return create_battle(
            maker or self.game_master,
            name="Duel",
            count=1,
            seed=seed,
            character_ids=(self.character.pk,),
        )

    def seat(self, battle: Battle):
        return battle.combatants.get(character=self.character)

    def figure(self, battle: Battle):
        return tarmar_state(battle).by_id(self.seat(battle).pk)

    def advance_until_hurt(self, battle: Battle) -> Battle:
        """Advance until Aldwin's figure has lost something, or the fight ends."""
        for _ in range(TURN_LIMIT):
            battle, _events = advance_turn(battle.pk)
            figure = self.figure(battle)
            if figure.fatigue < figure.max_fatigue or battle.status in OVER:
                return battle
        self.fail("Aldwin was never hurt")

    def play_out(self, battle: Battle) -> Battle:
        for _ in range(TURN_LIMIT):
            battle, _events = advance_turn(battle.pk)
            if battle.status in OVER:
                return battle
        return battle

    def kill_on_the_board(self, battle: Battle) -> Battle:
        """Put Aldwin past any survival save; the engine's phase 6 kills him."""
        state = Battle.objects.get(pk=battle.pk).state_json
        seat_id = self.seat(battle).pk
        for combatant in state["combatants"]:
            if combatant["combatant_id"] == seat_id:
                combatant["fatigue"] = -3
                combatant["body"] = -100
                combatant["conscious"] = False
        Battle.objects.filter(pk=battle.pk).update(state_json=state)
        # Those pools stand for turns the board would have written to the
        # record (#750), so the record is put in step, without history, or
        # the fight is refused as fighting on stale pools (#842).
        fatigue, body = self.character.pools()
        Character.objects.filter(pk=self.character.pk).update(
            fatigue_damage=fatigue + 3, body_damage=body + 100
        )
        battle, _events = advance_turn(battle.pk)
        self.assertFalse(self.figure(battle).alive)
        return battle

    def test_a_character_comes_onto_the_board_as_hurt_as_the_record_says(self):
        self.character.fatigue_damage = 20
        self.character.body_damage = 3
        self.character.save()
        figure = self.figure(self.make_battle())
        self.assertEqual((figure.fatigue, figure.max_fatigue), (39, 59))
        self.assertEqual((figure.body, figure.max_body), (37, 40))
        self.assertTrue(figure.conscious)

    def test_a_caster_comes_on_with_the_mana_the_record_has_left(self):
        self.character.free_spell_points = 5
        self.character.mana_spent = 3
        self.character.save()
        battle = create_battle(
            self.game_master,
            name="Duel",
            count=1,
            seed=3,
            character_ids=(self.character.pk,),
        )
        # create_battle seats a saved character with its Mana Pool.
        figure = self.figure(battle)
        self.assertEqual((figure.mana, figure.max_mana), (2, 5))

    def test_mana_spent_on_the_board_is_reported_and_handed_back(self):
        battle = self.make_battle()
        seat_id = self.seat(battle).pk
        full = {seat_id: profiles.FigureVitals(alive=True, fatigue=59, body=40, mana=5)}
        spent = {
            seat_id: profiles.FigureVitals(alive=True, fatigue=59, body=40, mana=1)
        }
        [report] = fight_reports(battle, full, spent, turn=2)
        self.assertEqual(report.mana_spent, 4)
        [back] = fight_reports(battle, spent, full, turn=2)
        self.assertEqual(back.mana_spent, -4)

    def test_a_caster_comes_on_owing_what_the_record_owes(self):
        # Borrowing (tarmar-engine #28): the debt a fight leaves persists.
        self.character.free_spell_points = 5
        self.character.mana_owed = 2
        self.character.save()
        self.assertEqual(self.figure(self.make_battle()).debt, 2)

    def test_a_debt_run_up_on_the_board_is_reported_and_handed_back(self):
        battle = self.make_battle()
        seat_id = self.seat(battle).pk
        owing_one = {
            seat_id: profiles.FigureVitals(
                alive=True, fatigue=59, body=40, mana=2, debt=1
            )
        }
        borrowed = {
            seat_id: profiles.FigureVitals(
                alive=True, fatigue=59, body=40, mana=0, debt=4
            )
        }
        [report] = fight_reports(battle, owing_one, borrowed, turn=2, started=owing_one)
        self.assertEqual((report.mana_spent, report.debt_incurred), (2, 3))
        self.assertEqual(report.started_debt, 1)
        [back] = fight_reports(battle, borrowed, owing_one, turn=2)
        self.assertEqual(back.debt_incurred, -3)

    def test_the_tarmar_vitals_carry_the_debt(self):
        battle = self.make_battle()
        state = tarmar_state(battle)
        figure = state.by_id(self.seat(battle).pk)
        figure.debt = 3
        vitals = profiles.for_battle(battle).figure_vitals(state)
        self.assertEqual(vitals[figure.combatant_id].debt, 3)

    def test_a_character_already_down_comes_on_unconscious(self):
        self.character.fatigue_damage = 59
        self.character.save()
        figure = self.figure(self.make_battle())
        self.assertEqual(figure.fatigue, 0)
        self.assertFalse(figure.conscious)

    def test_each_committed_turn_writes_the_pools_and_the_end_writes_one_line(self):
        battle = self.make_battle()
        self.assertTrue(battle.writes_to_records)
        generated = battle.combatants.get(is_saved_character=False)
        for _ in range(TURN_LIMIT):
            battle, _events = advance_turn(battle.pk)
            figure = self.figure(battle)
            self.character.refresh_from_db()
            # The record matches the board after every turn, not at the end.
            self.assertEqual(self.character.current_fatigue, figure.fatigue)
            self.assertEqual(self.character.current_body, figure.body)
            self.assertEqual(self.character.is_alive, figure.alive)
            if battle.status in OVER:
                break
            if self.character.is_alive:
                # No history line mid-fight: a long fight would push the
                # character's other history out of its fifty lines.
                self.assertEqual(self.character.history, [])
        self.assertTrue(self.character.fatigue_damage or not self.character.is_alive)
        lines = [e for e in self.character.history if e["field"] == "current pools"]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["old"], "Fatigue 59/59, Body 40/40")
        # A fighter generated for the battle has no life beyond it.
        generated_row = Character.objects.get(pk=generated.character_id)
        self.assertEqual(generated_row.fatigue_damage, 0)
        self.assertEqual(generated_row.history, [])

    def test_a_long_fight_leaves_the_characters_other_history_alone(self):
        self.character.record_field_change("strength", 10, 14, "player")
        self.character.save()
        for seed in (7, 8, 9):
            self.play_out(self.make_battle(seed=seed))
            self.character.refresh_from_db()
            if not self.character.is_alive:
                break
        fields = [entry["field"] for entry in self.character.history]
        self.assertIn("strength", fields)
        self.assertLessEqual(fields.count("current pools"), 3)

    def test_a_death_is_written_by_the_turn_that_dealt_it(self):
        battle = self.kill_on_the_board(self.make_battle())
        self.character.refresh_from_db()
        self.assertFalse(self.character.is_alive)
        self.assertEqual(
            self.character.death_cause, f"Killed in battle — {battle.name}"
        )
        self.assertEqual(
            [entry["field"] for entry in self.character.history],
            ["current pools", "is_alive"],
        )
        self.assertEqual(self.character.history[0]["old"], "Fatigue 59/59, Body 40/40")
        self.assertIn("(dead), killed in Duel", self.character.history[0]["new"])

    def test_losses_add_to_the_record_rather_than_overwrite_it(self):
        # Hurt in this fight, then (between its turns) hurt somewhere else:
        # the next turn here adds its own loss and keeps the other wound.
        # Since #842 the fight first refuses to go on from pools the record
        # no longer holds, and a Game Master takes them from the record; this
        # test used to advance straight on, which is what #842 stopped.
        battle = self.advance_until_hurt(self.make_battle())
        self.character.refresh_from_db()
        from_this_fight = self.character.fatigue_damage
        character_services.record_fight(
            FightReport(
                character_id=self.character.pk, fight="Elsewhere", turn=1, body_lost=7
            )
        )
        with self.assertRaises(StalePools):
            advance_turn(battle.pk)
        battle = resnapshot_pools(battle.pk, is_game_master=True)
        before = self.figure(battle)
        self.assertEqual(before.body, before.max_body - 7, "the board took the wound")
        battle, _events = advance_turn(battle.pk)
        after = self.figure(battle)
        self.character.refresh_from_db()
        self.assertEqual(
            self.character.fatigue_damage,
            from_this_fight + before.fatigue - after.fatigue,
        )
        # The board took the other wound with the pools, so the record and
        # the board agree: the 7 is kept, and this turn's loss added to it.
        self.assertEqual(self.character.body_damage, after.max_body - after.body)
        self.assertGreaterEqual(self.character.body_damage, 7)

    def test_a_figure_missing_from_either_snapshot_reports_nothing(self):
        battle = self.make_battle()
        seat_id = self.seat(battle).pk
        hurt = {seat_id: profiles.FigureVitals(alive=False, fatigue=1, body=1)}
        self.assertEqual(fight_reports(battle, {}, hurt, turn=1), [])
        self.assertEqual(fight_reports(battle, hurt, {}, turn=1), [])

    def test_only_a_game_master_may_restart_a_fight_that_has_marked_the_record(self):
        battle = self.advance_until_hurt(self.make_battle())
        self.assertTrue(results_recorded(battle))
        self.character.refresh_from_db()
        damage = self.character.fatigue_damage
        with self.assertRaises(ValueError) as caught:
            restart_battle(battle.pk)
        self.assertEqual(
            str(caught.exception), refusal_words().services.results_recorded
        )
        self.character.refresh_from_db()
        self.assertEqual(self.character.fatigue_damage, damage)

    def test_a_game_masters_restart_hands_the_losses_back_and_keeps_a_death(self):
        self.character.fatigue_damage = 4
        self.character.save()
        battle = self.advance_until_hurt(self.make_battle())
        restart_battle(battle.pk, is_game_master=True)
        self.character.refresh_from_db()
        # Back where the fight found him, which is where the replay starts.
        self.assertEqual(self.character.fatigue_damage, 4)
        self.assertEqual(self.character.body_damage, 0)
        self.assertEqual(self.figure(battle).fatigue, 55)
        self.assertIn("Duel restarted", self.character.history[-1]["new"])

        battle = self.kill_on_the_board(battle)
        restart_battle(battle.pk, is_game_master=True)
        self.character.refresh_from_db()
        self.assertFalse(self.character.is_alive)
        # The replayed figure stays dead on the board, as the record says
        # (#854; this used to assert it stood up again, which was the bug),
        # and the record takes nothing more from it.
        self.assertFalse(self.figure(battle).alive)
        history = list(self.character.history)
        advance_turn(battle.pk)
        self.character.refresh_from_db()
        self.assertEqual(self.character.history, history)
        self.assertFalse(self.character.is_alive)


class PlayersOwnBattleTest(BaseTestCase):
    """A player's own battle writes nothing to any record, as before (#750)."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("player")
        self.character = create_character(self.player, name="Aldwin")

    def test_practice_neither_wounds_nor_kills_nor_heals_and_restarts_freely(self):
        self.character.fatigue_damage = 10
        self.character.save()
        battle = create_battle(
            self.player,
            name="Practice",
            count=3,
            seed=7,
            character_ids=(self.character.pk,),
        )
        self.assertFalse(battle.writes_to_records)
        for _ in range(TURN_LIMIT):
            battle, _events = advance_turn(battle.pk)
            if battle.status in OVER:
                break
        self.character.refresh_from_db()
        self.assertTrue(self.character.is_alive)
        self.assertEqual(
            (self.character.fatigue_damage, self.character.body_damage), (10, 0)
        )
        self.assertEqual(self.character.history, [])
        self.assertFalse(results_recorded(battle))
        self.login_as(self.player)
        response = self.client.get(f"/tarmar/battles/{battle.pk}/")
        self.assertContains(response, 'id="restart-battle"')
        response = self.client.post(f"/tarmar/battles/{battle.pk}/restart/")
        self.assertEqual(response.status_code, 200, response.content)

    def test_another_players_public_character_fights_only_as_a_copy(self):
        stranger = self.create_user("stranger")
        self.character.is_public = True
        self.character.save()
        battle = create_battle(
            stranger, name="Ambush", count=4, seed=7, character_ids=(self.character.pk,)
        )
        seat = battle.combatants.get(character=self.character)
        self.assertFalse(seat.is_saved_character)
        # Even in a battle that did write, a copy is not the record.
        Battle.objects.filter(pk=battle.pk).update(writes_to_records=True)
        for _ in range(TURN_LIMIT):
            battle, _events = advance_turn(battle.pk)
            if battle.status in OVER:
                break
        self.character.refresh_from_db()
        self.assertTrue(self.character.is_alive)
        self.assertEqual(
            (self.character.fatigue_damage, self.character.body_damage), (0, 0)
        )

    def test_a_game_masters_restart_offer_follows_the_record(self):
        # The page offers Restart only where the endpoint would take it.
        game_master = self.create_dm()
        battle = create_battle(
            game_master,
            name="Held",
            count=1,
            seed=3,
            character_ids=(self.character.pk,),
        )
        Battle.objects.filter(pk=battle.pk).update(created_by=self.player)
        # A record-writing fight a player made and controls is a Games Master
        # session's fight; a battle outside one counts only while its maker
        # is a Game Master (review of #837), so the setup says which it is.
        GMSession.objects.create(user=self.player, name="Held", battle=battle)
        for _ in range(TURN_LIMIT):
            battle, _events = advance_turn(battle.pk)
            if results_recorded(battle) or battle.status in OVER:
                break
        self.assertTrue(results_recorded(battle))
        self.login_as(self.player)
        response = self.client.get(f"/tarmar/battles/{battle.pk}/")
        self.assertContains(response, 'id="advance-turn"')
        self.assertNotContains(response, 'id="restart-battle"')
        response = self.client.post(f"/tarmar/battles/{battle.pk}/restart/")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"], refusal_words().services.results_recorded
        )
        self.login_as(game_master)
        response = self.client.get(f"/tarmar/battles/{battle.pk}/")
        self.assertContains(response, 'id="restart-battle"')


class LobbyStartTest(BaseTestCase):
    """A lobby seat is snapshotted at the claim; the start re-reads the record."""

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm()
        self.player = self.create_user("player")
        self.character = create_character(
            self.player, name="Aldwin", free_spell_points=5
        )
        self.battle = create_battle(
            self.game_master, name="Lobby", count=1, seed=4, player_seats=1
        )
        self.seat = self.battle.combatants.get(is_open_seat=True)
        claim_seat(
            self.battle.pk, self.seat.pk, self.player, character_id=self.character.pk
        )
        set_seat_ready(self.battle.pk, self.seat.pk, self.player, ready=True)

    def test_wounds_taken_after_the_claim_come_onto_the_board(self):
        Character.objects.filter(pk=self.character.pk).update(
            fatigue_damage=30, mana_spent=2
        )
        battle = start_battle(self.battle.pk)
        figure = tarmar_state(battle).by_id(self.seat.pk)
        self.assertEqual((figure.fatigue, figure.max_fatigue), (29, 59))
        self.assertEqual((figure.mana, figure.max_mana), (3, 5))

    def test_a_debt_run_up_after_the_claim_comes_onto_the_board(self):
        Character.objects.filter(pk=self.character.pk).update(mana_owed=2)
        battle = start_battle(self.battle.pk)
        self.assertEqual(tarmar_state(battle).by_id(self.seat.pk).debt, 2)

    def test_a_debt_settled_after_the_claim_is_not_brought_on(self):
        # Claimed owing 2, as the snapshot then said; settled before the start.
        seat = self.battle.combatants.get(pk=self.seat.pk)
        seat.snapshot_json = {**seat.snapshot_json, "debt": 2}
        seat.save(update_fields=["snapshot_json"])
        battle = start_battle(self.battle.pk)
        self.assertEqual(tarmar_state(battle).by_id(self.seat.pk).debt, 0)

    @with_fixed_refusals
    def test_a_character_who_died_after_the_claim_cannot_start(self):
        Character.objects.filter(pk=self.character.pk).update(
            is_alive=False, death_cause="a fall"
        )
        with self.assertRaises(ValueError) as caught:
            start_battle(self.battle.pk)
        self.assertIn("services.died_since_claiming", str(caught.exception))


class ClassicFightWriteBackTest(BaseTestCase):
    """Classic battles write deaths; ST is not the record's Fatigue or Body."""

    def test_a_death_under_classic_rules_is_written_and_no_pool_is(self):
        dm = self.create_dm()
        deaths = 0
        for seed in range(1, 25):
            character = create_character(dm, name=f"Hero {seed}")
            battle = create_battle(
                dm,
                name=f"Classic {seed}",
                count=1,
                seed=seed,
                character_ids=(character.pk,),
                rules_profile=CLASSIC_PROFILE,
            )
            seat = battle.combatants.get(character=character)
            for _ in range(TURN_LIMIT):
                battle, _events = advance_turn(battle.pk)
                if battle.status in OVER:
                    break
            state = load_state(battle.state_json, CLASSIC_PROFILE)
            figure = next(one for one in state.figures if one.uid == str(seat.pk))
            character.refresh_from_db()
            self.assertEqual(character.is_alive, not figure.is_dead)
            self.assertEqual((character.fatigue_damage, character.body_damage), (0, 0))
            deaths += not character.is_alive
        self.assertGreater(deaths, 0, "no seed killed the saved character")


class StalePoolsTest(BaseTestCase):
    """An unfinished record-writing fight goes on only from the records (#842).

    Coordinator's ruling under the standing rule (Spencer may overrule): a
    fight whose seated characters' pools moved on the record since it last
    wrote them is refused, and a Game Master takes the pools from the records.
    The three ways the records moved under a fight: healing while it sat
    unfinished, a turn of another unfinished fight, and a restart's hand-back
    floored at unhurt.
    """

    def setUp(self):
        super().setUp()
        self.player = self.create_user("player")
        self.game_master = self.create_dm()
        self.character = create_character(self.player, name="Aldwin")

    def make_battle(self, name: str = "Duel", seed: int = 3) -> Battle:
        return create_battle(
            self.game_master,
            name=name,
            count=1,
            seed=seed,
            character_ids=(self.character.pk,),
        )

    def hurt(self, battle: Battle) -> Battle:
        for _ in range(TURN_LIMIT):
            battle, _events = advance_turn(battle.pk)
            state = tarmar_state(battle)
            figure = state.by_id(battle.combatants.get(character=self.character).pk)
            if figure.fatigue < figure.max_fatigue or battle.status in OVER:
                return battle
        self.fail("Aldwin was never hurt")

    def heal(self) -> None:
        Character.objects.filter(pk=self.character.pk).update(
            fatigue_damage=0, body_damage=0
        )

    @with_fixed_refusals
    def test_a_fight_healed_while_it_sat_unfinished_is_refused(self):
        battle = self.hurt(self.make_battle())
        if battle.status in OVER:
            self.skipTest("the fight ended on the turn that hurt him")
        self.heal()
        turn = Battle.objects.get(pk=battle.pk).turn_number

        with self.assertRaisesMessage(StalePools, "services.stale_pools Aldwin"):
            advance_turn(battle.pk)

        self.assertEqual(Battle.objects.get(pk=battle.pk).turn_number, turn)
        self.assertEqual(stale_pools(Battle.objects.get(pk=battle.pk)), ["Aldwin"])

    def test_one_character_in_two_unfinished_fights_fights_in_one(self):
        first = self.make_battle("First")
        second = self.make_battle("Second", seed=5)
        self.hurt(second)
        if Battle.objects.get(pk=second.pk).status in OVER:
            self.skipTest("the second fight ended on its first blow")

        with self.assertRaises(StalePools):
            advance_turn(first.pk)

    def test_a_restart_whose_hand_back_was_floored_is_refused(self):
        self.character.fatigue_damage = 4
        self.character.save()
        battle = self.hurt(self.make_battle())
        self.heal()

        restart_battle(battle.pk, is_game_master=True)

        with self.assertRaises(StalePools):
            advance_turn(battle.pk)

    def test_the_game_master_takes_the_pools_and_the_fight_goes_on(self):
        battle = self.hurt(self.make_battle())
        if battle.status in OVER:
            self.skipTest("the fight ended on the turn that hurt him")
        self.heal()

        battle = resnapshot_pools(battle.pk, is_game_master=True)

        state = tarmar_state(battle)
        figure = state.by_id(battle.combatants.get(character=self.character).pk)
        self.assertEqual(
            (figure.fatigue, figure.body), (figure.max_fatigue, figure.max_body)
        )
        self.assertEqual(stale_pools(battle), [])
        note = battle.events.order_by("-sequence").first()
        assert note is not None
        self.assertTrue(says(note.message, words().services.pools_taken))
        # Nobody owes anything here, so the line says nothing of a debt.
        self.assertNotIn(sentence_parts(words().services.figure_owed)[0], note.message)
        advance_turn(battle.pk)

    @with_fixed_refusals
    def test_a_debt_the_game_master_settles_mid_fight_is_what_the_board_owes(self):
        # Borrowing (tarmar-engine #28): the board must not go on rolling
        # against, capping by or paying back a debt the record forgave.
        self.character.free_spell_points = 5
        self.character.mana_owed = 2
        self.character.save()
        battle = self.make_battle()
        seat_id = battle.combatants.get(character=self.character).pk
        self.assertEqual(tarmar_state(battle).by_id(seat_id).debt, 2)
        character_services.set_current_pools(
            self.character.pk, owed=0, is_game_master=True
        )

        with self.assertRaisesMessage(StalePools, "services.stale_pools Aldwin"):
            advance_turn(battle.pk)

        battle = resnapshot_pools(battle.pk, is_game_master=True)
        self.assertEqual(tarmar_state(battle).by_id(seat_id).debt, 0)
        note = battle.events.order_by("-sequence").first()
        assert note is not None
        self.assertIn("owed 2→0", note.message)
        self.assertEqual(stale_pools(battle), [])
        advance_turn(battle.pk)

    def test_only_a_game_master_takes_the_pools(self):
        battle = self.hurt(self.make_battle())
        self.heal()

        with self.assertRaisesMessage(
            ValueError, refusal_words().services.resnapshot_game_master_only
        ):
            resnapshot_pools(battle.pk, is_game_master=False)

    def test_nothing_to_take_is_said(self):
        battle = self.make_battle()

        with self.assertRaisesMessage(
            ValueError, refusal_words().services.pools_already_held
        ):
            resnapshot_pools(battle.pk, is_game_master=True)

    def test_a_fight_nobody_touched_goes_on(self):
        """The neighbouring case: the board's own writes are never stale."""
        battle = self.make_battle()
        for _ in range(3):
            battle, _events = advance_turn(battle.pk)
            if battle.status in OVER:
                break
            self.assertEqual(stale_pools(battle), [])

    def test_a_players_own_battle_is_never_stale(self):
        battle = create_battle(
            self.player,
            name="Practice",
            count=1,
            seed=3,
            character_ids=(self.character.pk,),
        )
        self.assertFalse(battle.writes_to_records)
        advance_turn(battle.pk)
        self.heal()

        advance_turn(battle.pk)

    @with_fixed_refusals
    def test_the_page_says_so_and_offers_the_game_master_the_control(self):
        battle = self.hurt(self.make_battle())
        if battle.status in OVER:
            self.skipTest("the fight ended on the turn that hurt him")
        self.heal()
        self.login_as(self.game_master)

        page = self.client.get(reverse("battle:detail", args=[battle.pk]))
        refused = self.client.post(
            reverse("battle:advance", args=[battle.pk]),
            HTTP_ACCEPT="application/json",
        )
        taken = self.client.post(
            reverse("battle:resnapshot", args=[battle.pk]),
            {"next": reverse("battle:detail", args=[battle.pk])},
        )

        self.assertContains(page, 'id="battle-stale-pools"')
        self.assertContains(page, 'id="resnapshot-pools"')
        self.assertContains(page, "Pools changed on the records")
        self.assertEqual(refused.status_code, 409)
        self.assertIn("services.stale_pools Aldwin", refused.json()["error"])
        # The notice and the control come with the refusal, for a page that
        # was open when the records moved (review of #842).
        self.assertIn('id="resnapshot-pools"', refused.json()["stale_html"])
        self.assertIn('id="battle-stale-pools"', refused.json()["stale_html"])
        self.assertFalse(refused.json()["advance"]["enabled"])
        self.assertRedirects(
            taken,
            reverse("battle:detail", args=[battle.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(stale_pools(Battle.objects.get(pk=battle.pk)), [])


@with_fixed_refusals
class StalePoolsAnsweredAlikeTest(BaseTestCase):
    """The advance and a player's choice refuse stale pools alike (#112).

    A choice resumes the paused turn, which reaches the same #842 refusal the
    advance does. The act endpoint answered it as a plain 400 with its text,
    so a Game Master choosing for a figure got no readout and no control to
    take the pools from the records; both now answer the 409 with both.
    """

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm()
        self.character = create_character(self.create_user("player"), name="Aldwin")
        battle = create_battle(
            self.game_master,
            name="Whose move",
            count=1,
            seed=3,
            character_ids=(self.character.pk,),
            control_mode=control.MANUAL,
        )
        self.battle, _events = advance_turn(battle.pk)
        self.assertEqual(self.battle.status, Battle.STATUS_AWAITING)
        # The record moves under the paused fight: a wound from elsewhere.
        Character.objects.filter(pk=self.character.pk).update(fatigue_damage=1)
        self.assertEqual(stale_pools(self.battle), ["Aldwin"])
        self.login_as(self.game_master)

    def assert_refused_with_the_readout(self, response) -> None:
        self.assertEqual(response.status_code, 409, response.content)
        payload = response.json()
        self.assertIn("services.stale_pools Aldwin", payload["error"])
        self.assertFalse(payload["advance"]["enabled"])
        self.assertEqual(payload["advance"]["label"], "Pools changed on the records")
        self.assertIn('id="battle-stale-pools"', payload["stale_html"])
        self.assertIn('id="resnapshot-pools"', payload["stale_html"])

    def test_the_advance_answers_with_the_readout(self):
        response = self.client.post(
            reverse("battle:advance", args=[self.battle.pk]),
            HTTP_ACCEPT="application/json",
        )

        self.assert_refused_with_the_readout(response)

    def test_a_choice_answers_with_the_readout(self):
        pending = self.battle.pending_input_json
        option = pending["candidates"][0]
        choice = {
            "combatant_id": pending["combatant_id"],
            "turn": pending["turn"],
            "letter": option["letter"],
            "target_id": option.get("target_id"),
            "spell_key": option.get("spell_key") or "",
        }

        response = self.client.post(
            reverse("battle:act", args=[self.battle.pk]),
            json.dumps(choice),
            content_type="application/json",
        )

        self.assert_refused_with_the_readout(response)
        self.assertEqual(
            Battle.objects.get(pk=self.battle.pk).turn_number, self.battle.turn_number
        )

    def test_a_seated_player_is_refused_with_neither_readout_nor_control(self):
        """The refusal's words alone: the readout and the control that takes
        the pools from the records are a Game Master's (#842), and the take
        is refused to anyone else."""
        player = self.character.user
        battle = create_battle(
            self.game_master,
            name="Her move",
            count=1,
            seed=3,
            character_ids=(self.character.pk,),
        )
        seat = battle.combatants.get(character=self.character)
        battle.combatants.filter(pk=seat.pk).update(player=player)
        set_combatant_control_mode(battle.pk, seat.pk, control.MANUAL)
        battle, _events = advance_turn(battle.pk)
        self.assertEqual(battle.pending_input_json["combatant_id"], seat.pk)
        Character.objects.filter(pk=self.character.pk).update(fatigue_damage=2)
        pending = battle.pending_input_json
        option = pending["candidates"][0]
        self.login_as(player)

        refused = self.client.post(
            reverse("battle:act", args=[battle.pk]),
            json.dumps(
                {
                    "combatant_id": pending["combatant_id"],
                    "turn": pending["turn"],
                    "letter": option["letter"],
                    "target_id": option.get("target_id"),
                    "spell_key": option.get("spell_key") or "",
                }
            ),
            content_type="application/json",
        )
        taken = self.client.post(reverse("battle:resnapshot", args=[battle.pk]))

        self.assertEqual(refused.status_code, 409, refused.content)
        payload = refused.json()
        self.assertIn("services.stale_pools Aldwin", payload["error"])
        self.assertIsNone(payload["advance"])
        self.assertEqual(payload["stale_html"], "")
        self.assertEqual(taken.status_code, 403)
        self.assertEqual(stale_pools(Battle.objects.get(pk=battle.pk)), ["Aldwin"])


class DemotedMakerStopsWritingTest(BaseTestCase):
    """A record-writing fight re-reads its maker's role on every write-back.

    Review of #837: ``writes_to_records`` is fixed when the battle is made,
    so a Game Master demoted mid-fight went on writing wounds and deaths to
    other players' characters, and a battle that counted only because he ran
    it went on counting.
    """

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm()
        self.player = self.create_user("player")
        self.theirs = create_character(self.player, name="Brenna")
        self.mine = create_character(self.game_master, name="Gorm")

    def demote(self) -> None:
        self.game_master.is_dm = False
        self.game_master.save(update_fields=["is_dm"])

    def play(self, battle: Battle) -> Battle:
        for _ in range(TURN_LIMIT):
            battle, _events = advance_turn(battle.pk)
            if battle.status in OVER:
                break
        return battle

    def test_a_battle_he_ran_stops_counting(self):
        battle = create_battle(
            self.game_master,
            name="Run",
            count=1,
            seed=3,
            character_ids=(self.theirs.pk,),
        )
        self.assertTrue(battle.writes_to_records)
        self.demote()

        battle = self.play(battle)

        self.theirs.refresh_from_db()
        self.assertEqual(
            (self.theirs.fatigue_damage, self.theirs.body_damage, self.theirs.is_alive),
            (0, 0, True),
        )
        self.assertEqual(
            withheld_characters(battle),
            {self.theirs.pk: words().services.withheld_not_game_master},
        )
        notes = battle.events.filter(payload__records_withheld=self.theirs.pk)
        self.assertEqual(notes.count(), 1, "said once, however many turns")
        self.assertTrue(says(notes.get().message, words().services.records_withheld))
        self.assertEqual(stale_pools(battle), [], "a withheld record is never stale")

    def test_a_session_fight_keeps_writing_his_own_character_only(self):
        battle = create_battle(
            self.game_master,
            name="Session",
            count=1,
            seed=3,
            character_ids=(self.theirs.pk, self.mine.pk),
        )
        GMSession.objects.create(user=self.game_master, name="s", battle=battle)
        self.demote()

        self.assertEqual(
            withheld_characters(battle),
            {self.theirs.pk: words().services.withheld_other_players},
        )
        battle = self.play(battle)

        self.theirs.refresh_from_db()
        self.assertEqual((self.theirs.fatigue_damage, self.theirs.body_damage), (0, 0))

    def test_a_game_master_who_keeps_the_role_keeps_writing(self):
        """The neighbouring case: nothing withheld while the role holds."""
        battle = create_battle(
            self.game_master,
            name="Run",
            count=1,
            seed=3,
            character_ids=(self.theirs.pk,),
        )

        self.assertEqual(withheld_characters(battle), {})
        battle = self.play(battle)

        self.theirs.refresh_from_db()
        self.assertTrue(
            self.theirs.fatigue_damage
            or self.theirs.body_damage
            or not self.theirs.is_alive
        )
        self.assertFalse(
            battle.events.filter(payload__has_key="records_withheld").exists()
        )


class StaleNoticeOnEveryPayloadTest(BaseTestCase):
    """Second review of #842: the notice rides on every battle response.

    While a turn waits on a player's move the Game Master's page polls, and
    its button reads "Waiting on ...": with the notice only on a refused
    Advance, nothing on that page ever said the records had moved.
    """

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm()
        self.player = self.create_user("player")
        self.character = create_character(self.player, name="Aldwin")
        self.battle = create_battle(
            self.game_master,
            name="Duel",
            count=1,
            seed=3,
            character_ids=(self.character.pk,),
        )
        self.login_as(self.game_master)

    def poll(self):
        return self.client.get(reverse("battle:state", args=[self.battle.pk])).json()

    def test_a_fight_nobody_touched_carries_an_empty_notice(self):
        self.assertEqual(self.poll()["stale_html"], "")

    def test_a_poll_after_the_records_moved_carries_the_notice_and_the_control(self):
        # The record moves under the fight: a wound from elsewhere.
        character_services.record_fight(
            FightReport(
                character_id=self.character.pk, fight="Elsewhere", turn=1, body_lost=5
            )
        )
        Battle.objects.filter(pk=self.battle.pk).update(status=Battle.STATUS_AWAITING)

        payload = self.poll()

        self.assertIn('id="battle-stale-pools"', payload["stale_html"])
        self.assertIn('id="resnapshot-pools"', payload["stale_html"])
        self.assertEqual(payload["advance"]["label"], "Pools changed on the records")
