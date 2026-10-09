"""The data migration that carries session fights across the deploy (#750, #762)."""

from importlib import import_module

from django.apps import apps as installed_apps

from battle.models import Battle
from battle.services import advance_turn
from common.test_helpers import BaseTestCase, create_character
from gm.battle_report import pending_battle_entries
from gm.fight import start_fight
from gm.models import GMSession
from gm.testing import game_master_sets_the_foes_asked_for

MIGRATION = import_module("battle.migrations.0015_session_fights_at_deploy")


class SessionFightsAtDeployTest(BaseTestCase):
    def fight_before_the_deploy(self, owner) -> tuple[GMSession, Battle]:
        """A session fight as the previous release left it: no flags, no writes."""
        aldwin = create_character(owner, name="Aldwin")
        session = GMSession.objects.create(name="Quest", user=owner)
        session.characters.add(aldwin)
        with game_master_sets_the_foes_asked_for():
            battle = start_fight(session, foes_text="gray wolf", seed=11).battle
        Battle.objects.filter(pk=battle.pk).update(
            foes_belong_to_game_master=False, writes_to_records=False
        )
        session.refresh_from_db()
        return session, Battle.objects.get(pk=battle.pk)

    def hurt_aldwin(self, battle: Battle, *, dead: bool) -> None:
        """Put the board where a pre-deploy fight left it: hurt, 3 Mana spent."""
        state, initial = battle.state_json, battle.initial_state_json
        for snapshot, mana in ((initial, 5), (state, 2)):
            for combatant in snapshot["combatants"]:
                if combatant["name"] == "Aldwin":
                    combatant["max_mana"], combatant["mana"] = 5, mana
        for combatant in state["combatants"]:
            if combatant["name"] == "Aldwin":
                combatant["fatigue"] -= 20
                combatant["body"] -= 3
                combatant["alive"] = not dead
        Battle.objects.filter(pk=battle.pk).update(
            initial_state_json=initial,
            state_json=state,
            status=Battle.STATUS_FINISHED if dead else Battle.STATUS_PAUSED,
        )

    def run_migration(self) -> None:
        MIGRATION.forwards(installed_apps, None)

    def test_a_fight_under_way_is_given_to_the_game_master_and_caught_up(self):
        session, battle = self.fight_before_the_deploy(self.create_user("player"))
        self.hurt_aldwin(battle, dead=False)
        self.run_migration()
        battle.refresh_from_db()
        self.assertTrue(battle.foes_belong_to_game_master)
        self.assertTrue(battle.writes_to_records)
        aldwin = session.characters.get()
        self.assertEqual((aldwin.fatigue_damage, aldwin.body_damage), (20, 3))
        self.assertEqual(aldwin.mana_spent, 3)
        self.assertTrue(aldwin.is_alive)
        # One line for the fight, from the pools it found him at, by its maker.
        self.assertEqual(len(aldwin.history), 1)
        entry = aldwin.history[0]
        self.assertEqual(entry["field"], "current pools")
        fatigue, body = aldwin.pools()
        self.assertEqual(
            entry["old"], f"Fatigue {fatigue}/{fatigue}, Body {body}/{body}; Mana 5/5"
        )
        self.assertTrue(
            entry["new"].startswith(
                f"Fatigue {fatigue - 20}/{fatigue}, Body {body - 3}/{body}; Mana 2/5"
            )
        )
        self.assertIn("when the board began recording fights", entry["new"])
        # The Game Master's side dealt it, not the player who started it.
        self.assertEqual(entry["by"], "Games Master")
        # From here the board writes as any new fight does.
        before = aldwin.fatigue_damage
        advance_turn(battle.pk)
        aldwin.refresh_from_db()
        self.assertGreaterEqual(aldwin.fatigue_damage + aldwin.body_damage, before)

    def test_a_finished_fight_with_no_summary_filed_has_its_death_written(self):
        session, battle = self.fight_before_the_deploy(self.create_user("player"))
        self.hurt_aldwin(battle, dead=True)
        self.run_migration()
        aldwin = session.characters.get()
        self.assertFalse(aldwin.is_alive)
        self.assertEqual(aldwin.death_cause, f"Killed in battle — {battle.name}")
        self.assertEqual(
            [entry["field"] for entry in aldwin.history], ["current pools", "is_alive"]
        )
        self.assertIn(f"killed in {battle.name}", aldwin.history[0]["new"])

    def test_a_fight_whose_summary_was_filed_is_left_as_it_was(self):
        session, battle = self.fight_before_the_deploy(self.create_user("player"))
        self.hurt_aldwin(battle, dead=False)
        Battle.objects.filter(pk=battle.pk).update(status=Battle.STATUS_FINISHED)
        session.refresh_from_db()
        session.history = session.history + pending_battle_entries(
            session.history, session
        )
        session.save()
        self.run_migration()
        battle.refresh_from_db()
        self.assertFalse(battle.writes_to_records)
        self.assertTrue(battle.foes_belong_to_game_master)
        aldwin = session.characters.get()
        self.assertEqual((aldwin.fatigue_damage, aldwin.body_damage), (0, 0))

    def test_a_game_masters_session_keeps_the_foes(self):
        _session, battle = self.fight_before_the_deploy(self.create_dm())
        self.run_migration()
        battle.refresh_from_db()
        self.assertFalse(battle.foes_belong_to_game_master)
        self.assertTrue(battle.writes_to_records)
