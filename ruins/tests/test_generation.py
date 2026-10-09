"""Generator tests: structure, roll records, and seed determinism."""

from django.test import SimpleTestCase

from ruins.generation import (
    HAZARD_TABLE,
    MAX_ROOMS,
    MIN_ROOMS,
    ROOM_CONTENTS_TABLE,
    generate_ruin,
)
from ruins.tables import load_tables


class GenerateRuinTests(SimpleTestCase):
    def test_same_seed_reproduces_the_same_ruin(self):
        self.assertEqual(generate_ruin(room_count=4, seed=42), generate_ruin(4, 42))

    def test_rolls_every_table_in_order(self):
        ruin = generate_ruin(room_count=3, seed=7)
        self.assertEqual(len(ruin.rooms), 3)
        # 4 site rolls + (contents + hazard) per room + 2 treasure rolls.
        self.assertEqual(len(ruin.all_rolls), 4 + 3 * 2 + 2)
        self.assertEqual(ruin.rooms[0].number, 1)
        self.assertEqual(ruin.rooms[2].number, 3)
        self.assertEqual(ruin.rooms[1].contents.table_title, ROOM_CONTENTS_TABLE)
        self.assertEqual(ruin.rooms[1].hazard.table_title, HAZARD_TABLE)

    def test_each_roll_matches_its_table_row_and_keeps_its_record(self):
        tables = load_tables()
        ruin = generate_ruin(room_count=2, seed=99)
        for roll in ruin.all_rolls:
            table = tables[roll.table_title]
            self.assertEqual(roll.die, table.die)
            self.assertTrue(1 <= roll.face <= table.die_sides)
            self.assertEqual(roll.result, table.row_for_face(roll.face))
            self.assertEqual(roll.record.specification, f"1d{table.die_sides}")
            self.assertEqual(roll.record.faces, (roll.face,))
            self.assertEqual(roll.record.total, roll.face)
            self.assertEqual(roll.record.purpose, roll.table_title)

    def test_unseeded_generation_still_produces_a_full_ruin(self):
        ruin = generate_ruin(room_count=MIN_ROOMS)
        self.assertIsNone(ruin.seed)
        self.assertEqual(len(ruin.rooms), MIN_ROOMS)

    def test_room_count_bounds_are_enforced(self):
        with self.assertRaisesRegex(ValueError, "Room count"):
            generate_ruin(room_count=MIN_ROOMS - 1, seed=1)
        with self.assertRaisesRegex(ValueError, "Room count"):
            generate_ruin(room_count=MAX_ROOMS + 1, seed=1)
