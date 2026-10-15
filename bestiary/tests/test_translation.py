"""Unit tests for the pure TFT → Tarmar translation layer."""

import json
import math
from pathlib import Path

from django.test import SimpleTestCase

from bestiary.models import CATEGORY_CHOICES
from bestiary.translation import (
    CATEGORY_CHARISMA,
    CATEGORY_WISDOM,
    SECTION_CATEGORY,
    classify_attack,
    derive_pools,
    fixed_fatigue_roll,
    infer_size_hexes,
    parse_armour,
    parse_attacks,
    parse_movement,
    tier_for_stops,
    translate,
)
from characters.combat import MATRIX

SOURCE_STATS = Path(__file__).resolve().parent.parent / "data" / "tft_source_stats.json"


class SectionCategoryTest(SimpleTestCase):
    def test_every_source_section_has_a_category(self):
        entries = json.loads(SOURCE_STATS.read_text())
        sections = {entry["tft_section"] for entry in entries}
        unmapped = sections - set(SECTION_CATEGORY)
        self.assertEqual(unmapped, set())

    def test_categories_are_model_choices(self):
        valid = {key for key, _label in CATEGORY_CHOICES}
        self.assertEqual(set(SECTION_CATEGORY.values()) - valid, set())

    def test_wisdom_and_charisma_cover_every_category(self):
        valid = {key for key, _label in CATEGORY_CHOICES}
        self.assertEqual(set(CATEGORY_WISDOM), valid)
        self.assertEqual(set(CATEGORY_CHARISMA), valid)


class MovementTest(SimpleTestCase):
    def test_plain_value(self):
        self.assertEqual(parse_movement("8"), (8, None))

    def test_compound_value_keeps_ground_and_notes_the_rest(self):
        self.assertEqual(
            parse_movement("8/20 (walking/flying)"),
            (8, "MA 8/20 (walking/flying)"),
        )

    def test_range_collapses_to_midpoint(self):
        self.assertEqual(parse_movement("22-28"), (25, None))

    def test_non_numeric_is_rooted_with_note(self):
        self.assertEqual(parse_movement("water"), (0, "MA water"))

    def test_blank_is_rooted_without_note(self):
        self.assertEqual(parse_movement(""), (0, None))


class AttackClassTest(SimpleTestCase):
    def test_keyword_classes(self):
        self.assertEqual(classify_attack("Bite", 10), "Piercing")
        self.assertEqual(classify_attack("Gore", 10), "Thrusting")
        self.assertEqual(classify_attack("Tentacle grab", 10), "Flexible / Snare")
        self.assertEqual(classify_attack("Fire breath", 10), "Missile — Bows")
        self.assertEqual(classify_attack("Kick", 10), "Striking")

    def test_heavy_upgrade_at_threshold_strength(self):
        self.assertEqual(classify_attack("Kick", 30), "Heavy Striking")
        self.assertEqual(classify_attack("Gore", 30), "Heavy Thrusting")
        # Piercing and missiles do not upgrade: mass does not sharpen a fang.
        self.assertEqual(classify_attack("Bite", 60), "Piercing")

    def test_every_class_is_a_matrix_key(self):
        for name in ("Bite", "Gore", "Kick", "Tentacle", "Breath"):
            for strength in (5, 50):
                self.assertIn(classify_attack(name, strength), MATRIX)


class ParseAttacksTest(SimpleTestCase):
    def test_splits_and_converts_dice(self):
        attacks = parse_attacks("Bite (2d+1); sting (1d-1, rear hexes)", 10)
        self.assertEqual(
            attacks,
            [
                {"name": "Bite", "damage": "2d6+1", "weapon_class": "Piercing"},
                {
                    "name": "sting",
                    "damage": "1d6-1, rear hexes",
                    "weapon_class": "Piercing",
                },
            ],
        )

    def test_entry_without_parenthesis_keeps_name_only(self):
        attacks = parse_attacks("Tentacle grab", 10)
        self.assertEqual(
            attacks,
            [
                {
                    "name": "Tentacle grab",
                    "damage": "",
                    "weapon_class": "Flexible / Snare",
                }
            ],
        )

    def test_damage_followed_by_more_prose_is_still_found(self):
        """The pattern this replaced required the closing parenthesis to be
        the entry's last character, so an entry that said anything after it
        kept its whole text as the name and had no damage at all — which is
        how a pig's gore and an arachne's bare hands ended up fighting
        bare-handed (#645)."""
        self.assertEqual(
            parse_attacks("Gore (1d+2), or 1d-1 for a tuskless domestic pig", 10),
            [
                {
                    "name": "Gore",
                    "damage": "1d6+2, or 1d6-1 for a tuskless domestic pig",
                    "weapon_class": "Thrusting",
                }
            ],
        )
        self.assertEqual(
            parse_attacks("Bare-handed (1d) or by weapon", 10),
            [
                {
                    "name": "Bare-handed",
                    "damage": "1d6 or by weapon",
                    "weapon_class": "Striking",
                }
            ],
        )

    def test_only_the_first_parenthesis_is_unwrapped(self):
        """A second parenthesised aside used to be swallowed into the damage
        with its brackets mangled ("1d6+1) (see below")."""
        self.assertEqual(
            parse_attacks("Seed shot (1d+1) (see below)", 10)[0]["damage"],
            "1d6+1 (see below)",
        )

    def test_an_unclosed_parenthesis_reads_to_the_entrys_own_end(self):
        """The source extract drops a closing bracket here and there (the
        common crocodile's bite); the damage is still the text it opened."""
        attacks = parse_attacks(
            "Bite (2d, one target in front hex only; tail (rear hex only)", 10
        )
        self.assertEqual(attacks[0]["name"], "Bite")
        self.assertTrue(attacks[0]["damage"].startswith("2d6,"))

    def test_none_gives_empty_list(self):
        self.assertEqual(parse_attacks(None, 10), [])

    def test_trailing_separator_is_ignored(self):
        self.assertEqual(len(parse_attacks("Bite (1d);", 10)), 1)


class ArmourTest(SimpleTestCase):
    def test_tier_banding(self):
        self.assertEqual(tier_for_stops(0), "None")
        self.assertEqual(tier_for_stops(1), "Light")
        self.assertEqual(tier_for_stops(2), "Light")
        self.assertEqual(tier_for_stops(3), "Medium")
        self.assertEqual(tier_for_stops(4), "Medium")
        self.assertEqual(tier_for_stops(5), "Heavy")
        self.assertEqual(tier_for_stops(7), "Heavy")

    def test_parse_armour_text(self):
        self.assertEqual(parse_armour("Scales stop 3 hits"), ("Medium", 3))
        self.assertEqual(parse_armour("Chitin stops 1 hit"), ("Light", 1))
        self.assertEqual(parse_armour(None), ("None", 0))


class SizeTest(SimpleTestCase):
    def test_bands(self):
        self.assertEqual(infer_size_hexes(10), 1)
        self.assertEqual(infer_size_hexes(20), 1)
        self.assertEqual(infer_size_hexes(21), 2)
        self.assertEqual(infer_size_hexes(45), 3)
        self.assertEqual(infer_size_hexes(60), 4)
        self.assertEqual(infer_size_hexes(150), 7)

    def test_explicit_size_wins(self):
        self.assertEqual(infer_size_hexes(12, explicit=3), 3)


class PoolsTest(SimpleTestCase):
    def test_matches_character_formula_with_midpoint_roll(self):
        fatigue, body = derive_pools(
            {
                "strength": 16,
                "dexterity": 12,
                "intelligence": 5,
                "wisdom": 5,
                "constitution": 16,
                "charisma": 3,
            }
        )
        expected_fatigue = 16 + 5 + 5 + max(12, 16) + fixed_fatigue_roll()
        self.assertEqual(fatigue, expected_fatigue)
        self.assertEqual(body, math.ceil(expected_fatigue * 2 / 3))


class TranslateTest(SimpleTestCase):
    def setUp(self):
        self.entry = {
            "name": "Dire Wolf",
            "tft_section": "Canines",
            "page": 37,
            "st": 16,
            "st_range": None,
            "dx": 12,
            "iq": 5,
            "ma": "12",
            "attacks": "Bite (2d)",
            "abilities": None,
            "armor": "Fur stops 1 hit",
            "size_hexes_text": None,
        }

    def test_full_translation(self):
        fields = translate(self.entry, description="A big wolf.")
        self.assertEqual(fields["slug"], "dire-wolf")
        self.assertEqual(fields["category"], "mammals")
        self.assertEqual(fields["strength"], 16)
        self.assertEqual(fields["constitution"], 16)
        self.assertEqual(fields["wisdom"], CATEGORY_WISDOM["mammals"])
        self.assertEqual(fields["charisma"], CATEGORY_CHARISMA["mammals"])
        self.assertEqual(fields["armour_tier"], "Light")
        self.assertEqual(fields["stops"], 1)
        self.assertEqual(fields["movement"], 12)
        self.assertEqual(fields["size_hexes"], 1)
        self.assertEqual(fields["source_page"], 37)
        self.assertFalse(fields["is_generated"])
        self.assertEqual(fields["description"], "A big wolf.")
        self.assertEqual(
            fields["attacks"],
            [{"name": "Bite", "damage": "2d6", "weapon_class": "Piercing"}],
        )

    def test_st_range_and_movement_note_recorded_in_abilities(self):
        entry = dict(self.entry, st=45, st_range="30-60", ma="8/20 (walking/flying)")
        fields = translate(entry)
        self.assertIn("Source ST 30-60", fields["special_abilities"])
        self.assertIn("MA 8/20 (walking/flying)", fields["special_abilities"])
        self.assertEqual(fields["movement"], 8)

    def test_stats_clamped_to_one(self):
        entry = dict(self.entry, st=0, dx=0, iq=0)
        fields = translate(entry)
        self.assertEqual(fields["strength"], 1)
        self.assertEqual(fields["dexterity"], 1)
        self.assertEqual(fields["intelligence"], 1)
