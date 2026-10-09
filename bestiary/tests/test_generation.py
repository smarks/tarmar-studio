"""Tests for GM beast generation: base picks and bounded variants."""

import math
import random

from django.test import TestCase

from bestiary.generation import (
    DIFFICULTY_BANDS,
    VARIATION,
    generate_variant,
    pick_base,
)
from bestiary.models import Beast
from common.test_helpers import make_beast as shared_make_beast

TEST_WOLF_STATS = {
    "strength": 16,
    "dexterity": 12,
    "constitution": 16,
    "fatigue_pool": 49,
    "body_pool": 33,
    "armour_tier": "Light",
    "stops": 1,
    "attacks": [{"name": "Bite", "damage": "2d6", "weapon_class": "Piercing"}],
    "special_abilities": "Fur stops 1 hit",
    "size_hexes": 1,
    "source_page": 37,
}


def make_beast(**overrides) -> Beast:
    """The generation tests' wolf, from the shared beast factory."""
    return shared_make_beast(**{**TEST_WOLF_STATS, **overrides})


class PickBaseTest(TestCase):
    def setUp(self):
        # These tests reason about a catalog containing exactly the beasts
        # built below, so clear the migration-loaded catalog first (#183).
        Beast.objects.all().delete()
        self.wolf = make_beast()
        self.dragon = make_beast(
            name="Test Dragon",
            slug="test-dragon",
            category="dragons",
            strength=60,
            constitution=60,
        )

    def test_exact_name_case_insensitive(self):
        self.assertEqual(pick_base(name="test wolf"), self.wolf)

    def test_slug_also_matches(self):
        self.assertEqual(pick_base(name="test-dragon"), self.dragon)

    def test_random_within_category(self):
        self.assertEqual(pick_base(category="dragons"), self.dragon)

    def test_random_within_difficulty_band(self):
        self.assertEqual(pick_base(difficulty="deadly"), self.dragon)
        self.assertEqual(pick_base(difficulty="moderate"), self.wolf)

    def test_unknown_difficulty_raises(self):
        with self.assertRaises(ValueError):
            pick_base(difficulty="apocalyptic")

    def test_no_match_raises(self):
        with self.assertRaises(Beast.DoesNotExist):
            pick_base(category="plants")

    def test_generated_variants_are_never_picked(self):
        variant = generate_variant(self.wolf, rng=random.Random(1))
        self.assertTrue(variant.is_generated)
        for _attempt in range(10):
            self.assertFalse(pick_base(category="mammals").is_generated)

    def test_difficulty_bands_cover_all_strengths(self):
        lower_bounds = sorted(
            minimum for minimum, _maximum in DIFFICULTY_BANDS.values()
        )
        self.assertEqual(lower_bounds[0], 1)


class GenerateVariantTest(TestCase):
    def setUp(self):
        self.base = make_beast()

    def test_str_is_the_name(self):
        self.assertEqual(str(self.base), "Test Wolf")

    def test_variant_is_flagged_and_linked(self):
        variant = generate_variant(self.base, rng=random.Random(42))
        self.assertTrue(variant.is_generated)
        self.assertEqual(variant.base_beast, self.base)
        self.assertEqual(variant.category, self.base.category)
        self.assertEqual(variant.attacks, self.base.attacks)
        self.assertEqual(variant.source_page, self.base.source_page)
        self.assertIn("variant", variant.name)
        self.assertNotEqual(variant.slug, self.base.slug)

    def test_stats_stay_within_variation_bounds(self):
        for seed in range(25):
            variant = generate_variant(self.base, rng=random.Random(seed))
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
                base_value = getattr(self.base, stat)
                varied_value = getattr(variant, stat)
                low = max(1, round(base_value * (1 - VARIATION)))
                high = round(base_value * (1 + VARIATION))
                self.assertGreaterEqual(varied_value, min(low, base_value), stat)
                self.assertLessEqual(varied_value, max(high, base_value), stat)

    def test_pools_rederived_from_varied_stats(self):
        variant = generate_variant(self.base, rng=random.Random(7))
        expected_fatigue = (
            variant.constitution
            + variant.wisdom
            + variant.intelligence
            + max(variant.dexterity, variant.strength)
            + 7
        )
        self.assertEqual(variant.fatigue_pool, expected_fatigue)
        self.assertEqual(variant.body_pool, math.ceil(expected_fatigue * 2 / 3))

    def test_zero_stays_zero(self):
        rooted = make_beast(name="Test Plant", slug="test-plant", movement=0, stops=0)
        variant = generate_variant(rooted, rng=random.Random(3))
        self.assertEqual(variant.movement, 0)
        self.assertEqual(variant.stops, 0)
        self.assertEqual(variant.armour_tier, "None")

    def test_variant_slugs_stay_unique(self):
        first = generate_variant(self.base, rng=random.Random(1))
        second = generate_variant(self.base, rng=random.Random(1))
        self.assertNotEqual(first.slug, second.slug)
