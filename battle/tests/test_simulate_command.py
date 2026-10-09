"""The `simulate` management command and its `tarmar simulate` wrapper."""

import random
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.models import User
from battle import services
from battle.management.commands.simulate import SIMULATOR_USERNAME
from battle.models import Battle
from bestiary.models import Beast
from common.test_helpers import BaseTestCase


class SimulateCommandTest(BaseTestCase):
    def run_command(self, *args):
        output = StringIO()
        call_command("simulate", *args, stdout=output)
        return output.getvalue()

    def test_runs_a_seeded_battle_to_completion(self):
        random.seed(99)  # pin the roster so the run finishes quickly
        output = self.run_command("--n", "2", "--seed", "42")
        self.assertIn("2 combatants, seed 42", output)
        self.assertIn("Phase 1: Initiative", output)
        self.assertIn("initiative 1d6", output)
        self.assertIn("chooses", output)
        self.assertIn("finished after", output)
        battle = Battle.objects.get()
        self.assertEqual(battle.status, Battle.STATUS_FINISHED)
        self.assertTrue(User.objects.filter(username=SIMULATOR_USERNAME).exists())

    def test_rejects_out_of_range_count(self):
        with self.assertRaises(CommandError):
            call_command("simulate", "--n", "1")

    def test_turn_cap_stops_a_stalemate(self):
        random.seed(99)
        with patch("battle.management.commands.simulate.MAX_TURNS", 2):
            output = self.run_command("--n", "2", "--seed", "42")
        self.assertIn("Stopped after 2 turns", output)
        # The battle stopped at the cap; it did not finish (issue #877).
        battle = Battle.objects.get()
        self.assertNotIn(
            battle.status, (Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE)
        )
        self.assertNotIn("finished after", output)
        self.assertIn(f"{battle.name} stopped at the turn cap after 2 turn(s)", output)

    def test_a_stalemate_says_so(self):
        random.seed(99)
        real_advance = services.advance_turn

        def stalemated(battle_id):
            battle, events = real_advance(battle_id)
            battle.status = Battle.STATUS_STALEMATE
            return battle, events

        with patch(
            "battle.management.commands.simulate.advance_turn", side_effect=stalemated
        ):
            output = self.run_command("--n", "2", "--seed", "42")
        self.assertIn("ended in stalemate after 1 turn(s)", output)
        self.assertNotIn("finished after", output)

    def test_beasts_join_the_simulation(self):
        from .test_services import make_beast

        make_beast()
        make_beast(slug="cave-bear", name="Cave Bear", strength=30, size_hexes=3)
        random.seed(99)
        with patch("battle.management.commands.simulate.MAX_TURNS", 3):
            output = self.run_command(
                "--n",
                "2",
                "--seed",
                "42",
                "--beasts",
                "2",
                "--beast-category",
                "mammals",
            )
        self.assertIn("4 combatants, seed 42", output)
        self.assertIn("beast", output)
        battle = Battle.objects.get()
        self.assertEqual(battle.combatants.exclude(beast=None).count(), 2)

    def test_unmatchable_beast_draft_is_a_command_error(self):
        # The migration-loaded catalog has real dragons; empty it so the
        # dragons draft below is genuinely unmatchable (#183).
        Beast.objects.all().delete()
        with self.assertRaises(CommandError):
            call_command(
                "simulate",
                "--n",
                "2",
                "--beasts",
                "1",
                "--beast-category",
                "dragons",
            )


class TarmarSubcommandTest(BaseTestCase):
    def test_simulate_subcommand_wraps_the_management_command(self):
        from cli import tarmar

        with patch("django.core.management.call_command") as call_mock:
            with patch("django.setup"):
                with patch(
                    "sys.argv",
                    ["tarmar", "simulate", "--n", "3", "--seed", "9", "--step"],
                ):
                    tarmar.main()
        call_mock.assert_called_once_with(
            "simulate", "--n", "3", "--seed", "9", "--step"
        )

    def test_bare_simulate_leaves_the_defaults_to_the_command(self):
        """No wrapper defaults of its own (issue #337, item 13).

        It used to send --n 8 when none was given, while the command's
        default is DEFAULT_COMBATANTS.
        """
        from cli import tarmar

        with patch("django.core.management.call_command") as call_mock:
            with patch("django.setup"):
                with patch("sys.argv", ["tarmar", "simulate"]):
                    tarmar.main()
        call_mock.assert_called_once_with("simulate")

    def test_simulate_subcommand_passes_beast_options_through(self):
        from cli import tarmar

        with patch("django.core.management.call_command") as call_mock:
            with patch("django.setup"):
                with patch(
                    "sys.argv",
                    [
                        "tarmar",
                        "simulate",
                        "--n",
                        "2",
                        "--seed",
                        "9",
                        "--beasts",
                        "2",
                        "--beast-category",
                        "mammals",
                        "--beast-difficulty",
                        "moderate",
                    ],
                ):
                    tarmar.main()
        call_mock.assert_called_once_with(
            "simulate",
            "--n",
            "2",
            "--seed",
            "9",
            "--beasts",
            "2",
            "--beast-category",
            "mammals",
            "--beast-difficulty",
            "moderate",
        )
