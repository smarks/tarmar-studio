"""Issue #948: a rolled height or weight is fixed for a player.

Spencer's ruling, 2026-10-09: "a rolled height or weight is fixed for a
player once rolled, as the Fatigue roll and attributes are (#590); a player
chooses OR rolls, and a roll is final. A Game Master may change it at any
time. The one-roll record applies at creation too."

The one-roll record is the dice log (``services.roll_lines``), which
only code writes: creation, the Games Master's roster, the sheet's roll, and
a chain an import kept from the importer's own character.
"""

import re

from django.urls import reverse

from characters import services
from characters.models import Character
from common.test_helpers import BaseTestCase, create_character

ROLLED_HEIGHT_LINE = "Height: Demon d6: 2 = 2"
ROLLED_WEIGHT_LINE = "Weight: 1d6: 5 = 5"


def _input_cell(page: str, field: str) -> bool:
    """Whether the sheet renders ``field`` as an editable input."""
    return f'class="editable-field" data-field="{field}"' in page


def _locked_reason(page: str, field: str) -> str:
    """The persistent reason the sheet prints beside a locked ``field``."""
    found = re.search(rf'data-locked-field="{field}">([^<]*)<', page)
    return found.group(1) if found else ""


class RolledPhysicalFixedTest(BaseTestCase):
    """A player may not type over a rolled height or weight; a GM may."""

    def setUp(self):
        super().setUp()
        self.player = self.create_user("physical-player")
        self.character = create_character(
            self.player,
            height_roll="2",
            weight_roll="5",
            dice_log=f"{ROLLED_HEIGHT_LINE}\n{ROLLED_WEIGHT_LINE}",
        )

    def update(self, field: str, value: object):
        return self.post_json(
            reverse("characters:update_field", args=[self.character.pk]),
            {"field": field, "value": value},
        )

    def edit_page(self) -> str:
        return self.client.get(
            reverse("characters:edit", args=[self.character.pk])
        ).content.decode()

    def test_a_player_may_not_type_a_face_over_a_rolled_value(self):
        self.login_as(self.player)
        for field, value in (("height_roll", "6"), ("weight_roll", "1")):
            with self.subTest(field=field):
                response = self.update(field, value)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(
                    response.json()["error"],
                    services.roll_fixed_reason(field),
                )
        self.character.refresh_from_db()
        self.assertEqual(
            (self.character.height_roll, self.character.weight_roll), ("2", "5")
        )

    def test_the_service_refuses_a_player_on_every_path_through_it(self):
        with self.assertRaisesMessage(
            services.CharacterChangeError,
            services.roll_fixed_reason("height_roll"),
        ):
            services.update_field(self.character.pk, "height_roll", "6")
        self.character.refresh_from_db()
        self.assertEqual(self.character.height_roll, "2")

    def test_clearing_the_cell_does_not_reopen_the_choice(self):
        self.character.height_roll = ""
        self.character.save()
        self.login_as(self.player)

        refused = self.update("height_roll", "6")

        self.assertEqual(refused.status_code, 400)
        self.character.refresh_from_db()
        self.assertEqual(self.character.height_roll, "")

    def test_the_sheet_renders_a_rolled_value_as_text_for_a_player(self):
        self.login_as(self.player)
        page = self.edit_page()
        for field in ("height_roll", "weight_roll"):
            with self.subTest(field=field):
                self.assertFalse(_input_cell(page, field))
                self.assertEqual(
                    _locked_reason(page, field),
                    services.roll_fixed_reason(field),
                )

    def test_a_game_master_changes_a_rolled_value_any_time(self):
        self.login_as(self.create_dm("physical-gm"))
        page = self.edit_page()
        self.assertTrue(_input_cell(page, "height_roll"))
        self.assertEqual(_locked_reason(page, "height_roll"), "")

        response = self.update("height_roll", "6.6.3")

        self.assertEqual(response.status_code, 200, response.content)
        self.character.refresh_from_db()
        self.assertEqual(self.character.height_roll, "6.6.3")

    def test_a_player_with_no_roll_recorded_still_chooses_a_face(self):
        unrolled = create_character(
            self.player, name="Unrolled", height_roll="", weight_roll="", dice_log=""
        )
        self.login_as(self.player)
        page = self.client.get(
            reverse("characters:edit", args=[unrolled.pk])
        ).content.decode()
        self.assertTrue(_input_cell(page, "height_roll"))

        response = self.post_json(
            reverse("characters:update_field", args=[unrolled.pk]),
            {"field": "weight_roll", "value": "6"},
        )

        self.assertEqual(response.status_code, 200, response.content)
        unrolled.refresh_from_db()
        self.assertEqual(unrolled.weight_roll, "6")

    def test_a_chosen_face_is_not_a_roll_and_stays_the_players(self):
        """Choosing writes no dice-log line, so the choice stays open."""
        unrolled = create_character(
            self.player, name="Chooser", height_roll="", weight_roll="", dice_log=""
        )
        for value in ("3", "4"):
            services.update_field(unrolled.pk, "height_roll", value)
        unrolled.refresh_from_db()
        self.assertEqual(unrolled.height_roll, "4")
        self.assertFalse(services.field_rolled(unrolled, "height_roll"))


class RolledAtCreationTest(BaseTestCase):
    """The creation page rolls height and weight, so both are fixed at once."""

    def test_a_created_characters_height_and_weight_are_fixed(self):
        player = self.create_user("creation-player")
        self.login_as(player)

        response = self.client.post(
            reverse("characters:create"), {"method": "roll", "name": "Rolled"}
        )

        self.assertEqual(response.status_code, 302)
        character = Character.objects.get(user=player, name="Rolled")
        page = self.client.get(response.url).content.decode()
        for field in ("height_roll", "weight_roll"):
            with self.subTest(field=field):
                self.assertTrue(services.field_rolled(character, field))
                self.assertFalse(_input_cell(page, field))
                refused = self.post_json(
                    reverse("characters:update_field", args=[character.pk]),
                    {"field": field, "value": "6"},
                )
                self.assertEqual(refused.status_code, 400)

    def test_a_sheet_roll_fixes_a_blank_value(self):
        player = self.create_user("sheet-roll-player")
        character = create_character(
            player, height_roll="", weight_roll="", dice_log=""
        )
        self.login_as(player)

        rolled = self.client.post(
            reverse("characters:roll", args=[character.pk, "weight"])
        )

        self.assertEqual(rolled.status_code, 200, rolled.content)
        page = self.client.get(
            reverse("characters:edit", args=[character.pk])
        ).content.decode()
        self.assertFalse(_input_cell(page, "weight_roll"))
        self.assertTrue(_input_cell(page, "height_roll"))
