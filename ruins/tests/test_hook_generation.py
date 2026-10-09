"""Matching-rule and determinism tests for the hook generator (issue #276)."""

from django.test import SimpleTestCase

from ruins.hook_generation import (
    NoHooksForLevel,
    eligible_hooks,
    generate_hook,
    matched_level,
    published_levels,
)
from ruins.hooks import load_hooks


class MatchedLevelTests(SimpleTestCase):
    """The rule: average level rounded half-up, floor 1."""

    def test_average_rounds_half_up(self):
        self.assertEqual(matched_level(1.0), 1)
        self.assertEqual(matched_level(1.4), 1)
        self.assertEqual(matched_level(1.5), 2)
        self.assertEqual(matched_level(2.4), 2)
        self.assertEqual(matched_level(2.5), 3)

    def test_floor_is_level_one(self):
        self.assertEqual(matched_level(0.0), 1)


class EligibilityTests(SimpleTestCase):
    def test_published_levels_are_ascending_and_nonempty(self):
        levels = published_levels()
        self.assertEqual(list(levels), sorted(levels))
        self.assertIn(1, levels)

    def _titles_banded(self, levels: tuple[int, ...]) -> set[str]:
        """Titles of the loaded canon's hooks banded for exactly ``levels``."""
        titles = {hook.title for hook in load_hooks() if hook.levels == levels}
        self.assertTrue(titles, f"the canon publishes no hook banded {levels}")
        return titles

    def test_level_two_filters_out_level_one_only_hooks(self):
        level_two_titles = {hook.title for hook in eligible_hooks(2)}
        self.assertFalse(self._titles_banded((1,)) & level_two_titles)
        self.assertTrue(self._titles_banded((2,)) <= level_two_titles)
        self.assertTrue(self._titles_banded((1, 2)) <= level_two_titles)

    def test_level_one_filters_out_level_two_only_hooks(self):
        level_one_titles = {hook.title for hook in eligible_hooks(1)}
        self.assertTrue(self._titles_banded((1,)) <= level_one_titles)
        self.assertFalse(self._titles_banded((2,)) & level_one_titles)
        self.assertTrue(self._titles_banded((1, 2)) <= level_one_titles)

    def test_unpublished_level_has_no_hooks(self):
        self.assertEqual(eligible_hooks(7), ())


class GenerateHookTests(SimpleTestCase):
    def test_same_seed_and_level_reproduce_the_same_hook(self):
        first = generate_hook(level=1, seed=42)
        second = generate_hook(level=1, seed=42)
        self.assertEqual(first.hook, second.hook)
        self.assertEqual(first.roll.face, second.roll.face)

    def test_different_levels_may_roll_different_pools(self):
        generated = generate_hook(level=2, seed=7)
        self.assertEqual(generated.eligible_count, len(eligible_hooks(2)))
        self.assertEqual(generated.roll.die, f"d{generated.eligible_count}")
        self.assertIn(2, generated.hook.levels)

    def test_rolled_hook_is_always_banded_for_the_level(self):
        for seed in range(1, 30):
            generated = generate_hook(level=1, seed=seed)
            self.assertIn(1, generated.hook.levels, generated.hook.title)

    def test_roll_record_is_kept_whole(self):
        generated = generate_hook(level=1, seed=3)
        self.assertEqual(generated.roll.record.purpose, "Hook (level 1)")
        self.assertEqual(generated.roll.record.faces, (generated.roll.face,))
        self.assertEqual(generated.roll.result, generated.hook.title)

    def test_unpublished_level_raises_with_the_published_bands(self):
        with self.assertRaises(NoHooksForLevel) as caught:
            generate_hook(level=5, seed=1)
        self.assertEqual(caught.exception.level, 5)
        self.assertEqual(caught.exception.published_levels, tuple(published_levels()))
        self.assertIn("levels 1, 2", str(caught.exception))
