"""Encounter tables and generation service (issue #213).

The tables are data, so most of what can rot is referential: a beast slug
that no longer exists in the canonical fixture, a subtable name nothing
defines, a count specification the dice parser rejects. Each of those gets a
drift guard here, pure-Python against the fixture file — no database needed.
"""

import json
from pathlib import Path
from unittest import TestCase

from common.rolling import Roller, parse_dice_specification
from encounters.generation import (
    MAIN_TABLE_SIZE,
    MAIN_TABLES,
    SUBTABLE_SIZE,
    SUBTABLES,
    TERRAINS,
    generate_encounter,
    main_entry,
    subtable_entry,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "bestiary"
    / "fixtures"
    / "beasts.json"
)


def catalog_slugs() -> set[str]:
    rows = json.loads(FIXTURE_PATH.read_text())
    return {row["fields"]["slug"] for row in rows if not row["fields"]["is_generated"]}


def every_entry():
    for table in MAIN_TABLES.values():
        yield from table
    for per_terrain in SUBTABLES.values():
        for table in per_terrain.values():
            yield from table


class TableShapeTest(TestCase):
    def test_every_terrain_has_a_full_main_table(self):
        self.assertEqual(set(MAIN_TABLES), set(TERRAINS))
        for terrain, table in MAIN_TABLES.items():
            self.assertEqual(len(table), MAIN_TABLE_SIZE, terrain)

    def test_every_subtable_covers_every_terrain(self):
        for name, per_terrain in SUBTABLES.items():
            self.assertEqual(set(per_terrain), set(TERRAINS), name)
            for terrain, table in per_terrain.items():
                self.assertEqual(len(table), SUBTABLE_SIZE, f"{name}/{terrain}")

    def test_every_referenced_subtable_is_defined(self):
        for entry in every_entry():
            if entry.subtable is not None:
                self.assertIn(entry.subtable, SUBTABLES, entry.name)

    def test_subtable_entries_do_not_nest_further_than_special(self):
        # main -> humans/animals -> special is the deepest chain the rules
        # describe; the special tables themselves must be concrete.
        for per_terrain in SUBTABLES["special"].values():
            for entry in per_terrain:
                self.assertIsNone(entry.subtable, entry.name)


class DriftGuardTest(TestCase):
    def test_every_beast_slug_exists_in_the_canonical_fixture(self):
        known = catalog_slugs()
        for entry in every_entry():
            if entry.beast_slug is not None:
                self.assertIn(entry.beast_slug, known, entry.name)

    def test_every_count_specification_parses(self):
        for entry in every_entry():
            if entry.count is not None:
                parse_dice_specification(entry.count)

    def test_entries_are_either_concrete_or_subtable_references(self):
        for entry in every_entry():
            if entry.subtable is not None:
                self.assertIsNone(entry.beast_slug, entry.name)
                self.assertIsNone(entry.count, entry.name)


class TableLookupTest(TestCase):
    def test_main_entry_maps_roll_to_row(self):
        self.assertEqual(main_entry("plains", 1).name, "Wolf pack")
        self.assertEqual(main_entry("desert", 20).subtable, "special")

    def test_subtable_entry_maps_roll_to_row(self):
        self.assertEqual(subtable_entry("humans", "forest", 1).name, "Bandit band")
        self.assertEqual(subtable_entry("animals", "desert", 6).name, "Camels")

    def test_unknown_terrain_raises(self):
        with self.assertRaises(ValueError):
            main_entry("swamp", 1)


class GenerateEncounterTest(TestCase):
    def test_unknown_terrain_raises(self):
        with self.assertRaises(ValueError):
            generate_encounter("swamp", seed=1)

    def test_same_seed_reproduces_the_same_encounter(self):
        first = generate_encounter("forest", seed=42)
        second = generate_encounter("forest", seed=42)
        self.assertEqual(first, second)

    def test_result_is_always_concrete_with_recorded_rolls(self):
        for terrain in TERRAINS:
            for seed in range(60):
                result = generate_encounter(terrain, seed=seed)
                self.assertIsNone(result.entry.subtable)
                self.assertEqual(result.terrain, terrain)
                # One d20 step first, then a d10 per subtable followed.
                self.assertEqual(result.steps[0].table, "encounter")
                self.assertEqual(result.rolls[0].specification, "1d20")
                for step in result.steps[1:]:
                    self.assertIn(step.table, SUBTABLES)
                # A count arrives exactly when the entry has count dice.
                if result.entry.count is None:
                    self.assertIsNone(result.count)
                else:
                    count, sides, modifier = parse_dice_specification(
                        result.entry.count
                    )
                    assert result.count is not None  # narrows for the checks below
                    self.assertGreaterEqual(result.count, count + modifier)
                    self.assertLessEqual(result.count, count * sides + modifier)

    def test_caller_supplied_roller_captures_every_roll(self):
        records = []
        roller = Roller(seed=7, on_roll=records.append)
        result = generate_encounter("plains", roller=roller)
        self.assertEqual(tuple(records), result.rolls)
