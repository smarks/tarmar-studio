"""|signed: a modifier as the character sheet's tiles show it (#338 item 22)."""

from django.template import Context, Template
from django.test import SimpleTestCase

from characters.templatetags.dice_display import signed


class SignedFilterTest(SimpleTestCase):
    def test_it_signs_positive_zero_and_negative_values(self):
        self.assertEqual(signed(3), "+3")
        self.assertEqual(signed(0), "+0")
        self.assertEqual(signed(-2), "-2")

    def test_it_renders_what_the_hand_written_template_expression_did(self):
        by_hand = Template("{% if value >= 0 %}+{% endif %}{{ value }}")
        through_filter = Template("{% load dice_display %}{{ value|signed }}")
        for value in (-12, -6, -1, 0, 1, 2, 12):
            with self.subTest(value=value):
                context = Context({"value": value})
                self.assertEqual(
                    through_filter.render(context), by_hand.render(context)
                )
