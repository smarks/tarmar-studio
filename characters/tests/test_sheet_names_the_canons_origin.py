"""The sheet names the origins as the loaded canon's location table does
(issue #151).

#128 made the Games Master's prompt, schema and refusals read the origin the
rules leave to the Game Master (the location row with no literacy check) off
the canon's table. The sheet still typed it: the Origin's help named "Consult
GM" and every origin's grant, and the refusal a player's ruling on the origin
gets named "Consult GM". Both now read the rows, so a canon that names them
otherwise is named as it says, and one that leaves no origin to the Game
Master says nothing of one.
"""

from dataclasses import replace

from django.test import SimpleTestCase
from django.urls import reverse

from characters import services
from characters.help_text import help_text
from characters.refusal_words import refusal_words
from characters.rules import LocationRow, LocationTable
from common.test_helpers import BaseTestCase, create_character

from .rules_fixtures import DEFAULT_CHARACTER_RULES, seated_character_rules
from .test_rules import INVENTED_CHARACTER_RULES

#: :data:`INVENTED_CHARACTER_RULES` with every origin given a literacy check,
#: and one that grants nothing.
NO_ORIGIN_LEFT_TO_THE_GAME_MASTER = replace(
    INVENTED_CHARACTER_RULES,
    location=LocationTable(
        rows=(
            LocationRow(60, "Port", ("Sailing",), 1),
            LocationRow(100, "Wilds", (), 3),
        ),
        skill_points=2,
    ),
)


def location_help() -> str:
    """The Origin's help, as the sheet's help popover shows it."""
    return help_text()["location"]


def origin_ruling_refusal(origin: str) -> str:
    """The configured refusal of a player's ruling, naming ``origin``."""
    return refusal_words().services.origin_ruling_game_master_only.format(origin=origin)


class OriginHelpTest(SimpleTestCase):
    def test_under_the_public_pages_the_words_are_the_typed_ones(self):
        """The default canon's rows give what was typed, byte for byte, but
        for the hardest origin: "out in the country" now names the row with
        the largest penalty, Rural."""
        with seated_character_rules(DEFAULT_CHARACTER_RULES):
            text = location_help()
            reason = services.field_refusal("origin_ruling")
        self.assertTrue(
            text.startswith(
                "Where your character grew up. City grants Street Smarts; Village "
                "grants Street Smarts or Survival — your choice, rename the "
                "granted skill to switch; Rural grants Survival. Changing the "
                "origin moves the grant: "
            ),
            text,
        )
        self.assertTrue(
            text.endswith(
                "rolls literacy again against the new one. Consult GM has no check "
                "to roll: the Game Master rules whether the character can read, "
                "and that ruling fixes the origin the way a roll does."
            ),
            text,
        )
        self.assertIn(
            "taken off INT, so the Rural origin makes letters hardest to come by. ",
            text,
        )
        self.assertEqual(reason, origin_ruling_refusal("Consult GM"))

    def test_the_canons_own_rows(self):
        with seated_character_rules(INVENTED_CHARACTER_RULES):
            text = location_help()
            reason = services.field_refusal("origin_ruling")
        self.assertIn(
            "Where your character grew up. Port grants Sailing or Swimming — your "
            "choice, rename the granted skill to switch. Changing the origin",
            text,
        )
        self.assertIn(" Ask the GM has no check to roll: the Game Master rules", text)
        self.assertEqual(reason, origin_ruling_refusal("Ask the GM"))
        # One origin has a check, so none is hardest of several.
        self.assertIn("taken off INT. Roll it once", text)
        for words in (text, reason):
            with self.subTest(words=words[:40]):
                for row in ("Consult GM", "City", "Village", "Rural"):
                    self.assertNotIn(row, words)

    def test_a_canon_that_leaves_no_origin_to_the_game_master(self):
        with seated_character_rules(NO_ORIGIN_LEFT_TO_THE_GAME_MASTER):
            text = location_help()
            reason = services.field_refusal("origin_ruling")
        # An origin that grants nothing is not listed as granting anything;
        # the one with the largest penalty is the hardest place to learn.
        self.assertIn("grew up. Port grants Sailing. Changing the origin", text)
        self.assertNotIn("Wilds grants", text)
        self.assertIn(
            "taken off INT, so the Wilds origin makes letters hardest to come by. ",
            text,
        )
        self.assertNotIn("has no check to roll", text)
        self.assertTrue(text.endswith("against the new one."), text)
        self.assertEqual(reason, refusal_words().services.origin_ruling_no_origin)

    def test_no_origin_is_hardest_when_the_largest_penalty_is_shared(self):
        tied = replace(
            NO_ORIGIN_LEFT_TO_THE_GAME_MASTER,
            location=LocationTable(
                rows=(
                    LocationRow(30, "Port", ("Sailing",), 1),
                    LocationRow(60, "Wilds", (), 3),
                    LocationRow(100, "Marsh", (), 3),
                ),
                skill_points=2,
            ),
        )
        with seated_character_rules(tied):
            text = location_help()
        self.assertIn("taken off INT. Roll it once", text)
        self.assertNotIn("hardest", text)

    def test_a_canon_whose_origins_grant_nothing(self):
        granting_nothing = replace(
            NO_ORIGIN_LEFT_TO_THE_GAME_MASTER,
            location=LocationTable(
                rows=(LocationRow(100, "Port", (), 3),), skill_points=0
            ),
        )
        with seated_character_rules(granting_nothing):
            text = location_help()
        self.assertTrue(
            text.startswith("Where your character grew up. Changing the origin"), text
        )


class OriginRulingRefusalOnTheSheetTest(BaseTestCase):
    """A player's update of the ruling is refused in the canon's words."""

    def test_a_player_is_refused_naming_the_canons_origin(self):
        owner = self.create_user("origin-words")
        character = create_character(owner)
        self.login_as(owner)
        url = reverse("characters:update_field", args=[character.pk])

        with seated_character_rules(INVENTED_CHARACTER_RULES):
            response = self.post_json(
                url, {"field": "origin_ruling", "value": "a harbour town"}
            )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["error"], origin_ruling_refusal("Ask the GM"))
