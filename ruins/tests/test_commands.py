"""``generate_ruin`` management command tests."""

from io import StringIO

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase

from common.test_helpers import BaseTestCase, create_character
from parties.models import Party, PartyMembership
from ruins.generation import DEFAULT_ROOMS, generate_ruin
from ruins.hook_generation import generate_hook


class GenerateRuinCommandTests(SimpleTestCase):
    def run_command(self, *arguments) -> str:
        output = StringIO()
        call_command("generate_ruin", *arguments, stdout=output)
        return output.getvalue()

    def test_seeded_run_prints_the_same_ruin_the_generator_rolls(self):
        expected = generate_ruin(room_count=3, seed=42)
        output = self.run_command("--rooms", "3", "--seed", "42")
        self.assertIn("The Site (seed 42)", output)
        self.assertIn("Room 3", output)
        self.assertIn("Treasure", output)
        for roll in expected.all_rolls:
            self.assertIn(roll.result, output)
            self.assertIn(f"[{roll.die} → {roll.face}]", output)

    def test_seeded_runs_are_reproducible(self):
        self.assertEqual(
            self.run_command("--seed", "7"), self.run_command("--seed", "7")
        )

    def test_default_run_rolls_the_default_room_count(self):
        output = self.run_command()
        self.assertIn(f"Room {DEFAULT_ROOMS}", output)
        self.assertNotIn("seed", output)

    def test_out_of_bounds_rooms_is_a_command_error(self):
        with self.assertRaisesRegex(CommandError, "Room count"):
            self.run_command("--rooms", "0")


class GenerateHookCommandTests(SimpleTestCase):
    """``generate_hook`` with --band: pure roll, no database needed."""

    def run_command(self, *arguments) -> str:
        output = StringIO()
        call_command("generate_hook", *arguments, stdout=output)
        return output.getvalue()

    def test_seeded_band_run_prints_the_same_hook_the_generator_rolls(self):
        expected = generate_hook(level=1, seed=42)
        output = self.run_command("--band", "1", "--seed", "42")
        self.assertIn("Level 1 hook (seed 42)", output)
        self.assertIn(expected.hook.title, output)
        self.assertIn(f"[{expected.roll.die} → {expected.roll.face}]", output)
        self.assertIn("Region:", output)

    def test_seeded_runs_are_reproducible(self):
        self.assertEqual(
            self.run_command("--band", "2", "--seed", "7"),
            self.run_command("--band", "2", "--seed", "7"),
        )

    def test_unpublished_band_is_a_command_error_pointing_at_scenario_seeds(self):
        with self.assertRaisesRegex(CommandError, "Scenario Seeds"):
            self.run_command("--band", "5")

    def test_party_and_band_together_are_refused(self):
        with self.assertRaises(CommandError):
            self.run_command("--party", "1", "--band", "1")

    def test_neither_party_nor_band_is_refused(self):
        with self.assertRaises(CommandError):
            self.run_command()


class GenerateHookPartyCommandTests(BaseTestCase):
    """``generate_hook`` with --party: the matching rule end to end."""

    def run_command(self, *arguments) -> str:
        output = StringIO()
        call_command("generate_hook", *arguments, stdout=output)
        return output.getvalue()

    def setUp(self):
        super().setUp()
        self.owner = self.create_dm()
        self.party = Party.objects.create(name="The Torchbearers", owner=self.owner)
        for index, xp in enumerate((0, 2000)):
            character = create_character(
                self.owner, name=f"Member {index}", total_xp=xp
            )
            PartyMembership.objects.create(
                party=self.party, character=character, added_by=self.owner
            )

    def test_party_by_name_matches_its_band(self):
        # Levels 1 and 2 average 1.5, which rounds up to level 2.
        expected = generate_hook(level=2, seed=9)
        output = self.run_command("--party", "The Torchbearers", "--seed", "9")
        self.assertIn("rolls as level 2", output)
        self.assertIn(expected.hook.title, output)

    def test_party_by_id_works_too(self):
        output = self.run_command("--party", str(self.party.pk), "--seed", "9")
        self.assertIn("rolls as level 2", output)

    def test_unknown_party_is_a_command_error(self):
        with self.assertRaisesRegex(CommandError, "No party matches"):
            self.run_command("--party", "The Missing")

    def test_empty_party_is_a_command_error(self):
        empty = Party.objects.create(name="The Unmanned", owner=self.owner)
        with self.assertRaisesRegex(CommandError, "no members"):
            self.run_command("--party", str(empty.pk))

    def test_a_disbanded_party_is_refused_with_the_reason(self):
        """Issue #971: the command read every party, disbanded or not."""
        Party.objects.filter(pk=self.party.pk).update(is_deleted=True)
        for argument in ("The Torchbearers", str(self.party.pk)):
            with self.subTest(argument=argument):
                with self.assertRaisesRegex(CommandError, "was disbanded"):
                    self.run_command("--party", argument, "--seed", "9")

    def test_a_live_party_wins_over_a_disbanded_one_by_the_same_name(self):
        Party.objects.create(name="The Torchbearers", owner=self.owner, is_deleted=True)
        output = self.run_command("--party", "The Torchbearers", "--seed", "9")
        self.assertIn("rolls as level 2", output)

    def test_two_live_parties_by_one_name_ask_for_the_id(self):
        Party.objects.create(name="The Torchbearers", owner=self.owner)
        with self.assertRaisesRegex(CommandError, "pass the party's id"):
            self.run_command("--party", "The Torchbearers")
