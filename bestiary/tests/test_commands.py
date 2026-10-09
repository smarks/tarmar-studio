"""Tests for the ``generate_beast`` management command."""

from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from bestiary.models import Beast
from bestiary.tests.test_generation import make_beast


class GenerateBeastCommandTest(TestCase):
    def setUp(self):
        self.wolf = make_beast()

    def test_generate_by_name(self):
        output = StringIO()
        call_command("generate_beast", name="Test Wolf", stdout=output)
        variant = Beast.objects.get(is_generated=True)
        self.assertEqual(variant.base_beast, self.wolf)
        self.assertIn(variant.name, output.getvalue())
        self.assertIn("p. 37", output.getvalue())

    def test_seed_makes_generation_reproducible(self):
        first_output = StringIO()
        call_command("generate_beast", name="Test Wolf", seed=42, stdout=first_output)
        second_output = StringIO()
        call_command("generate_beast", name="Test Wolf", seed=42, stdout=second_output)
        first, second = Beast.objects.filter(is_generated=True).order_by("pk")
        for stat in (
            "strength",
            "dexterity",
            "intelligence",
            "wisdom",
            "constitution",
            "charisma",
            "movement",
            "stops",
        ):
            self.assertEqual(getattr(first, stat), getattr(second, stat))

    def test_generate_by_category_and_difficulty(self):
        output = StringIO()
        call_command(
            "generate_beast", category="mammals", difficulty="moderate", stdout=output
        )
        self.assertTrue(Beast.objects.filter(is_generated=True).exists())

    def test_no_match_is_a_command_error(self):
        with self.assertRaises(CommandError):
            call_command("generate_beast", name="Nonexistent Horror")
