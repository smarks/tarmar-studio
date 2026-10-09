"""Silhouette plates for the bestiary (issue #193)."""

from django.core.management import call_command

from bestiary.art import (
    _FALLBACK_SILHOUETTE,
    _PALETTES,
    _SILHOUETTES,
    plate_for,
    scale_for_size,
)
from bestiary.models import CATEGORY_CHOICES, Beast
from common.test_helpers import BaseTestCase


class CoverageTest(BaseTestCase):
    """Every category the model offers has art and a palette."""

    def test_every_category_has_a_silhouette_and_a_palette(self):
        for key, label in CATEGORY_CHOICES:
            with self.subTest(category=label):
                self.assertIn(key, _SILHOUETTES)
                self.assertIn(key, _PALETTES)

    def test_no_silhouette_is_drawn_for_a_category_that_does_not_exist(self):
        known = {key for key, _ in CATEGORY_CHOICES}
        self.assertEqual(set(_SILHOUETTES) - known, set())
        self.assertEqual(set(_PALETTES) - known, set())

    def test_every_silhouette_is_closed_shapes_plus_open_details(self):
        """Filled shapes must close; detail lines must not be filled."""
        for category, (shapes, details) in _SILHOUETTES.items():
            with self.subTest(category=category):
                self.assertTrue(shapes)
                for shape in shapes:
                    self.assertTrue(shape.startswith("M"))
                    self.assertTrue(shape.endswith("z"), f"{category} shape is open")
                if details:
                    self.assertNotIn("z", details)


class PlateTest(BaseTestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("loaddata", "beasts", verbosity=0)

    def a_beast(self, **filters) -> Beast:
        beast = Beast.objects.filter(is_generated=False, **filters).first()
        if beast is None:
            self.fail(f"the beast fixture has nothing matching {filters}")
        return beast

    def test_a_plate_is_the_same_on_every_render(self):
        """The tint is identity, so it cannot change between page loads."""
        beast = self.a_beast()
        self.assertEqual(plate_for(beast), plate_for(beast))

    def test_two_beasts_in_a_category_do_not_look_identical(self):
        beasts = list(Beast.objects.filter(is_generated=False, category="mammals")[:8])
        grounds = {plate_for(beast).ground for beast in beasts}
        self.assertGreater(len(grounds), 1)

    def test_a_plate_draws_its_own_category(self):
        beast = self.a_beast(category="dragons")
        self.assertEqual(plate_for(beast).shapes, _SILHOUETTES["dragons"][0])

    def test_an_unknown_category_falls_back_rather_than_drawing_nothing(self):
        beast = self.a_beast()
        beast.category = "cryptids"
        plate = plate_for(beast)
        self.assertEqual(plate.shapes, _FALLBACK_SILHOUETTE[0])
        self.assertTrue(plate.ground.startswith("hsl("))

    def test_the_label_names_the_beast_and_says_it_is_a_silhouette(self):
        beast = self.a_beast(category="dragons")
        label = plate_for(beast).label
        self.assertIn(beast.name, label)
        self.assertIn("silhouette", label)

    def test_a_beast_with_no_slug_still_gets_a_plate(self):
        beast = self.a_beast()
        beast.slug = ""
        self.assertTrue(plate_for(beast).ground)


class ScaleTest(BaseTestCase):
    """A figure's footprint decides how much of the frame it fills."""

    def test_bigger_figures_fill_more_of_the_plate(self):
        scales = [scale_for_size(size) for size in (1, 2, 3, 4, 7)]
        self.assertEqual(scales, sorted(scales))
        self.assertEqual(len(set(scales)), len(scales))

    def test_the_largest_figure_fills_the_frame(self):
        self.assertEqual(scale_for_size(7), 1.0)

    def test_an_unlisted_size_sits_with_the_nearest_listed_one_below(self):
        self.assertEqual(scale_for_size(5), scale_for_size(4))
        self.assertEqual(scale_for_size(9), scale_for_size(7))

    def test_a_nonsense_size_falls_back_to_the_smallest(self):
        self.assertEqual(scale_for_size(0), scale_for_size(1))
        self.assertEqual(scale_for_size(-3), scale_for_size(1))


class TransformTest(BaseTestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("loaddata", "beasts", verbosity=0)

    def test_the_transform_scales_about_the_plate_centre(self):
        beast = Beast.objects.filter(is_generated=False).first()
        assert beast is not None
        transform = plate_for(beast).transform
        self.assertTrue(transform.startswith("translate(50 50) scale("))
        self.assertTrue(transform.endswith("translate(-50 -50)"))

    def test_a_flipped_plate_mirrors_and_an_unflipped_one_does_not(self):
        beasts = list(Beast.objects.filter(is_generated=False)[:40])
        plates = [plate_for(beast) for beast in beasts]
        self.assertTrue(any(plate.flipped for plate in plates))
        self.assertTrue(any(not plate.flipped for plate in plates))
        for plate in plates:
            self.assertEqual("scale(-1 1)" in plate.transform, plate.flipped)
