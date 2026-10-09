"""Tests for the location effects applied at character creation (issue #131).

The rules (reference/content/public-rules/characters/location.md) give each
origin a starting skill and a penalty on the INT check to be literate. Before
this, the rolled location was stored as a bare string and neither effect reached
the character.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from characters.dice import (
    LOCATION_LITERACY_MODIFIER,
    LOCATION_SKILL_POINTS,
    build_point_buy_character_stats,
    location_effects,
    roll_all_character_stats,
    roll_secondary_character_stats,
)
from characters.help_text import HELP_TEXT
from characters.models import Character
from common.test_helpers import BaseTestCase

CREATE_URL = "/tarmar/characters/create/"

# A legal 65-point buy, matching the one in test_point_buy.
VALID_BUY = {
    "strength": 14,
    "dexterity": 12,
    "intelligence": 12,
    "wisdom": 11,
    "constitution": 10,
    "charisma": 6,
}


def fixed_location(location):
    """Patch the location roll so a test can pin the origin it exercises."""
    return patch(
        "characters.dice.roll_location_logged",
        return_value=(location, f"Location: d100=1 -> {location}"),
    )


class LocationEffectsTableTest(SimpleTestCase):
    """`location_effects` turns an origin into a skill grant and a log line."""

    def test_city_grants_street_smarts_and_an_unmodified_literacy_check(self):
        skills, log = location_effects("City")

        self.assertEqual(skills, {"street smarts": LOCATION_SKILL_POINTS})
        self.assertIn("Street Smarts", log)
        self.assertIn("Literacy check INT", log)
        # No modifier for a City upbringing, so nothing is taken off INT.
        self.assertNotIn("INT -", log)

    def test_village_grants_street_smarts_and_plus_two(self):
        skills, log = location_effects("Village")

        self.assertEqual(skills, {"street smarts": LOCATION_SKILL_POINTS})
        self.assertIn("Literacy check INT - 2", log)

    def test_rural_grants_survival_and_plus_four(self):
        skills, log = location_effects("Rural")

        self.assertEqual(skills, {"survival": LOCATION_SKILL_POINTS})
        self.assertIn("Survival", log)
        self.assertIn("Literacy check INT - 4", log)

    def test_consult_gm_grants_nothing(self):
        skills, log = location_effects("Consult GM")

        self.assertEqual(skills, {})
        self.assertIn("Consult GM", log)

    def test_granted_skill_names_are_stored_normalized(self):
        """Grants must use the same keys the skills system already writes."""
        from characters.skills import normalize_skill_name

        for location in LOCATION_LITERACY_MODIFIER:
            skills, _log = location_effects(location)
            for name in skills:
                self.assertEqual(name, normalize_skill_name(name))


class SecondaryStatsCarryLocationSkillsTest(SimpleTestCase):
    """Both creation paths share `roll_secondary_character_stats`."""

    def test_secondary_stats_include_the_location_grant(self):
        with fixed_location("Rural"):
            stats, log_entries = roll_secondary_character_stats()

        self.assertEqual(stats["location"], "Rural")
        self.assertEqual(stats["skills"], {"survival": LOCATION_SKILL_POINTS})
        self.assertTrue(
            any("Location effects" in entry for entry in log_entries),
            msg="the dice log should record the location's effects",
        )

    def test_rolled_path_carries_the_grant(self):
        with fixed_location("City"):
            stats = roll_all_character_stats()

        self.assertEqual(stats["skills"], {"street smarts": LOCATION_SKILL_POINTS})
        self.assertIn("Location effects", stats["dice_log"])

    def test_point_buy_path_carries_the_grant(self):
        with fixed_location("City"):
            stats = build_point_buy_character_stats(VALID_BUY)

        self.assertEqual(stats["skills"], {"street smarts": LOCATION_SKILL_POINTS})


class LocationGrantOnCreationTest(BaseTestCase):
    """Creating a character through the page lands the grant on the row."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user("location-user")
        self.login_as(self.user)

    def _create(self, body):
        # A player names the character on creation (issue #786).
        response = self.client.post(CREATE_URL, {"name": "Located", **body})
        self.assertEqual(response.status_code, 302)
        return Character.objects.filter(user=self.user).latest("created_at")

    def test_rolled_character_gets_the_location_skill(self):
        with fixed_location("Village"):
            character = self._create({"method": "roll"})

        self.assertEqual(character.location, "Village")
        self.assertEqual(character.skills, {"street smarts": LOCATION_SKILL_POINTS})

    def test_point_buy_character_gets_the_location_skill(self):
        with fixed_location("Rural"):
            character = self._create({"method": "point-buy", **VALID_BUY})

        self.assertEqual(character.location, "Rural")
        self.assertEqual(character.skills, {"survival": LOCATION_SKILL_POINTS})

    def test_consult_gm_character_gets_no_skill(self):
        with fixed_location("Consult GM"):
            character = self._create({"method": "roll"})

        self.assertEqual(character.skills, {})

    def test_the_grant_counts_as_an_allocated_skill_point(self):
        with fixed_location("City"):
            character = self._create({"method": "roll"})

        self.assertEqual(character.total_allocated_skill_points, LOCATION_SKILL_POINTS)

    def test_the_granted_skill_is_on_the_sheet_immediately_after_creation(self):
        """The redirect target itself must show the grant, not just the row.

        Issue #226 asked specifically for the display to catch up immediately
        on creation; every other test in this class only checks the database
        row, so this is the one exercising the actual GET the player lands on.
        """
        with fixed_location("Rural"):
            character = self._create({"method": "roll"})

        sheet = self.client.get(f"/tarmar/characters/{character.pk}/").content.decode()

        self.assertIn("Survival", sheet)


class ExistingCharactersUntouchedTest(BaseTestCase):
    """Nothing retrofits older rows; the grant follows an Origin that moves."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user("legacy-user")

    def _legacy_character(self, **overrides):
        fields = {
            "user": self.user,
            "name": "Legacy",
            "strength": 10,
            "dexterity": 10,
            "intelligence": 10,
            "wisdom": 10,
            "constitution": 10,
            "charisma": 10,
            "appearance_roll": "3",
            "height_roll": "3",
            "weight_roll": "3",
            "fatigue_roll": 7,
            "location": "Rural",
        }
        fields.update(overrides)
        return Character.objects.create(**fields)

    def test_a_character_created_before_the_grant_keeps_empty_skills(self):
        character = self._legacy_character()

        self.assertEqual(character.skills, {})

    def test_loading_the_sheet_does_not_add_the_skill(self):
        character = self._legacy_character(skills={"sword": 3})
        self.login_as(self.user)

        response = self.client.get(f"/tarmar/characters/{character.pk}/")

        self.assertEqual(response.status_code, 200)
        character.refresh_from_db()
        self.assertEqual(character.skills, {"sword": 3})

    def test_changing_the_origin_grants_the_new_origins_skill(self):
        """This asserted the grant did not follow the Origin, which was the
        #754 defect: a City character moved to Rural had no Survival. The
        grant is now the new Origin's; there was no City grant to take back."""
        character = self._legacy_character(location="City")
        self.login_as(self.user)

        response = self.client.post(
            f"/tarmar/characters/{character.pk}/update-field/",
            data='{"field": "location", "value": "Rural"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        character.refresh_from_db()
        self.assertEqual(character.location, "Rural")
        self.assertEqual(character.skills, {"survival": LOCATION_SKILL_POINTS})


class LiteracyCheckDisplayTest(BaseTestCase):
    """The literacy modifier is derived from the origin and shown on the sheet."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user("literacy-user")
        self.character = Character.objects.create(
            user=self.user,
            name="Literacy Test",
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
            location="City",
        )

    def test_modifier_per_origin(self):
        expected = {"City": 0, "Village": 2, "Rural": 4, "Consult GM": None}
        for location, modifier in expected.items():
            self.character.location = location
            self.assertEqual(self.character.location_literacy_modifier, modifier)

    def test_check_reads_the_way_the_rules_print_it(self):
        expected = {
            "City": "3d6 ≤ INT",
            "Village": "3d6 ≤ INT − 2",
            "Rural": "3d6 ≤ INT − 4",
            "Consult GM": "Consult GM",
        }
        for location, text in expected.items():
            self.character.location = location
            self.assertEqual(self.character.location_literacy_check, text)

    def _sheet(self):
        self.login_as(self.user)
        return self.client.get(
            f"/tarmar/characters/{self.character.pk}/"
        ).content.decode()

    def test_sheet_shows_the_literacy_check(self):
        self.character.location = "Village"
        self.character.save(update_fields=["location"])

        body = self._sheet()

        self.assertIn("Literacy check", body)
        self.assertIn("3d6 ≤ INT − 2", body)

    def test_sheet_explains_the_location_effects(self):
        self.assertIn(HELP_TEXT["location"], self._sheet())
