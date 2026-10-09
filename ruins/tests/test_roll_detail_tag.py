"""{% roll_detail %}: the die-and-face trace after a generated result (#338 item 8)."""

from types import SimpleNamespace

from django.template import Context, Template
from django.test import SimpleTestCase


def render(roll) -> str:
    template = Template("{% load roll_detail %}{% roll_detail roll %}")
    return template.render(Context({"roll": roll}))


class RollDetailTagTest(SimpleTestCase):
    def test_a_roll_mapping_renders_the_die_and_the_face(self):
        self.assertEqual(
            render({"die": "d8", "face": 5}),
            '<span class="roll-detail">d8 &rarr; 5</span>',
        )

    def test_a_roll_object_renders_the_same_way(self):
        self.assertEqual(
            render(SimpleNamespace(die="d20", face=17)),
            '<span class="roll-detail">d20 &rarr; 17</span>',
        )

    def test_it_matches_the_markup_it_replaced(self):
        roll = {"die": "d6", "face": 3}
        by_hand = Template(
            '<span class="roll-detail">{{ roll.die }} &rarr; {{ roll.face }}</span>'
        ).render(Context({"roll": roll}))

        self.assertEqual(render(roll), by_hand)

    def test_values_are_escaped(self):
        self.assertIn("&lt;b&gt;", render({"die": "<b>", "face": 1}))
