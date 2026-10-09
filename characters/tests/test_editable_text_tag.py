"""{% editable_text %}: the sheet's short free-text cells (#338 item 5)."""

from django.template import Context, Template

from characters.models import Character
from common.test_helpers import BaseTestCase, create_character


def render(character, field, can_edit):
    template = Template(
        "{% load character_fields %}{% editable_text character field can_edit %}"
    )
    return template.render(
        Context({"character": character, "field": field, "can_edit": can_edit})
    )


class EditableTextTagTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.character = create_character(
            self.create_user("text-owner"),
            height_roll="5' 11\"",
            provenance_detail="<Fisher>",
        )

    def test_an_editor_gets_an_autosaving_input_bounded_by_the_model(self):
        html = render(self.character, "height_roll", True).strip()

        max_length = Character._meta.get_field("height_roll").max_length
        self.assertEqual(
            html,
            '<div class="editable-field" data-field="height_roll">'
            '<input type="text" aria-label="Height roll" '
            f'value="5&#x27; 11&quot;" maxlength="{max_length}" '
            'class="autosave"></div>',
        )

    def test_a_reader_gets_the_text_alone_escaped(self):
        self.assertEqual(
            render(self.character, "provenance_detail", False).strip(), "&lt;Fisher&gt;"
        )

    def test_it_matches_the_cell_it_replaced(self):
        # The aria-label is the one thing the by-hand cell never had: the
        # input's accessible name, which axe counted as a critical violation
        # sixteen times over on this sheet (issue #696).
        by_hand = Template(
            '{% if can_edit %}<div class="editable-field" data-field="weight_roll">'
            '<input type="text" aria-label="Weight roll" '
            'value="{{ character.weight_roll }}" maxlength="50" '
            'class="autosave"></div>{% else %}{{ character.weight_roll }}{% endif %}'
        )
        for can_edit in (True, False):
            with self.subTest(can_edit=can_edit):
                context = Context({"character": self.character, "can_edit": can_edit})
                self.assertEqual(
                    render(self.character, "weight_roll", can_edit).strip(),
                    by_hand.render(context),
                )
