"""A restart never stands the dead back up (issue #854).

Spencer's ruling: death on the board is a record, and only a Game Master
reverses it, by an explicit action the ledger names, never by a restart. A
Game Master's restart used to put a dead character's figure back at its
turn-0 snapshot, alive, while the record said dead.
"""

from django.utils.html import escape

from battle import profiles
from battle.models import Battle, BattleEvent
from battle.persistence import CLASSIC_PROFILE, load_state
from battle.services import (
    advance_turn,
    create_battle,
    record_player_action,
    restart_battle,
)
from battle.tests.test_profiles import classic_battle
from battle.wording import words
from characters import services as character_services
from characters.models import Character
from common.test_helpers import BaseTestCase, create_character


def play_turn(battle: Battle, user) -> Battle:
    """Advance one turn, handing every awaited choice to the AI."""
    battle, _events = advance_turn(battle.pk)
    while battle.status == Battle.STATUS_AWAITING:
        battle, _events = record_player_action(
            battle.pk,
            user,
            combatant_id=battle.pending_input_json["combatant_id"],
            turn=battle.pending_input_json["turn"],
            letter="",
            as_ai=True,
        )
    return battle


class RestartKeepsTheDeadTest(BaseTestCase):
    """A fight that writes to records, where one of two characters died."""

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm()
        self.aldwin = create_character(self.game_master, name="Aldwin")
        self.mira = create_character(self.game_master, name="Mira")
        self.battle = create_battle(
            self.game_master,
            name="Wolf Road",
            count=0,
            seed=5,
            beast_slugs=("gray-wolf",),
            character_ids=(self.aldwin.pk, self.mira.pk),
            character_teams=("Party", "Party"),
            beast_teams=("Foes",),
            writes_to_records=True,
        )
        self.aldwin_seat = self.battle.combatants.get(character=self.aldwin)
        self.mira_seat = self.battle.combatants.get(character=self.mira)
        # Aldwin past any survival save: the engine's phase 6 kills him on
        # the first turn, and Mira fights on.
        state = Battle.objects.get(pk=self.battle.pk).state_json
        for combatant in state["combatants"]:
            if combatant["combatant_id"] == self.aldwin_seat.pk:
                combatant["fatigue"] = -3
                combatant["body"] = -100
                combatant["conscious"] = False
        Battle.objects.filter(pk=self.battle.pk).update(state_json=state)
        # Those pools stand for turns the board would have written to the
        # record (#750), so the record is put in step, without history, or
        # the fight is refused as fighting on stale pools (#842).
        fatigue, body = self.aldwin.pools()
        Character.objects.filter(pk=self.aldwin.pk).update(
            fatigue_damage=fatigue + 3, body_damage=body + 100
        )
        play_turn(self.battle, self.game_master)
        self.aldwin.refresh_from_db()
        self.assertFalse(self.aldwin.is_alive)

    def figures(self, battle: Battle) -> dict:
        profile = profiles.for_battle(battle)
        return profile.figure_vitals(profile.load(battle.state_json))

    def test_the_dead_go_back_on_the_board_dead_and_the_living_stand(self):
        battle = restart_battle(self.battle.pk, is_game_master=True)
        figures = self.figures(battle)
        self.assertFalse(figures[self.aldwin_seat.pk].alive)
        self.assertTrue(figures[self.mira_seat.pk].alive)
        # The replay goes on without him, and the record stays dead.
        for _turn in range(3):
            battle = play_turn(battle, self.game_master)
            self.assertFalse(self.figures(battle)[self.aldwin_seat.pk].alive)
        self.aldwin.refresh_from_db()
        self.assertFalse(self.aldwin.is_alive)

    def test_the_log_says_who_stays_dead_and_why(self):
        restart_battle(self.battle.pk, is_game_master=True)
        (line,) = BattleEvent.objects.filter(
            battle=self.battle, payload__has_key="laid_dead"
        )
        self.assertEqual(line.payload["laid_dead"], ["Aldwin"])
        self.assertEqual(
            line.message, words().services.stays_dead.format(names="Aldwin")
        )

    def test_the_restart_answer_carries_the_line_for_the_log(self):
        self.login_as(self.game_master)
        response = self.client.post(f"/tarmar/battles/{self.battle.pk}/restart/")
        self.assertEqual(response.status_code, 200, response.content)
        (event,) = response.json()["events"]
        line = words().services.stays_dead.format(names="Aldwin")
        self.assertEqual(event["message"], line)
        self.assertIn(escape(line), response.json()["turns_html"][0]["html"])

    def test_a_game_masters_revive_then_restart_brings_the_figure_back(self):
        # The explicit action the ledger names: revive, then the restart
        # puts the figure back at its turn-0 snapshot.
        character_services.revive(self.aldwin.pk, actor=self.game_master)
        battle = restart_battle(self.battle.pk, is_game_master=True)
        self.assertTrue(self.figures(battle)[self.aldwin_seat.pk].alive)
        self.assertFalse(
            BattleEvent.objects.filter(
                battle=self.battle, payload__has_key="laid_dead"
            ).exists()
        )

    def test_a_battle_that_writes_nothing_restarts_as_it_always_has(self):
        Battle.objects.filter(pk=self.battle.pk).update(writes_to_records=False)
        battle = restart_battle(self.battle.pk, is_game_master=True)
        self.assertTrue(self.figures(battle)[self.aldwin_seat.pk].alive)


class ClassicLayDeadTest(BaseTestCase):
    """Classic has no ``alive`` flag: dead is ST -1 or below (p.3)."""

    def test_a_classic_figure_is_laid_dead_by_its_hits(self):
        battle = classic_battle(self.create_dm())
        profile = profiles.for_battle(battle)
        state = load_state(battle.state_json, CLASSIC_PROFILE)
        figure = state.figures[0]
        profile.lay_dead(state, int(figure.uid))
        self.assertTrue(figure.is_dead)
        self.assertFalse(profile.figure_vitals(state)[int(figure.uid)].alive)
        with self.assertRaisesMessage(ValueError, "no combatant 0"):
            profile.lay_dead(state, 0)
