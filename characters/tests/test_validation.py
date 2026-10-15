"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

import re

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.test import TestCase

from characters.models import (
    CHARACTER_INT_FIELD_MAX,
    CHARACTER_INT_FIELD_MIN,
    Character,
    character_int_field_maximum,
    character_int_field_minimum,
)
from characters.rules import character_rules
from common.test_helpers import BaseTestCase


class AttributeMaxValidationTest(BaseTestCase):
    """Tests for attribute max validation."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user("player")
        # The bounds below are what a Game Master is held to: a player
        # may not set these fields at all (issue #748, test_field_authors.PlayerFieldUpdateTest).
        self.game_master = self.create_dm("validation-gm")
        self.character = Character.objects.create(
            user=self.user,
            name="Test Character",
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
        )

    def test_attribute_over_30_rejected(self):
        """Attribute values over 30 are rejected."""
        self.login_as(self.game_master)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/update-field/",
            {"field": "strength", "value": 31},
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)
        self.assertIn("30", data["error"])
        self.character.refresh_from_db()
        self.assertEqual(self.character.strength, 10)

    def test_attribute_at_30_accepted(self):
        """Attribute value of exactly 30 is accepted."""
        self.login_as(self.game_master)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/update-field/",
            {"field": "strength", "value": 30},
        )
        self.assertEqual(response.status_code, 200)
        self.character.refresh_from_db()
        self.assertEqual(self.character.strength, 30)

    def test_all_attributes_have_max_validation(self):
        """All six attributes have max validation."""
        self.login_as(self.game_master)
        for attr in [
            "strength",
            "dexterity",
            "intelligence",
            "wisdom",
            "constitution",
            "charisma",
        ]:
            response = self.post_json(
                f"/tarmar/characters/{self.character.pk}/update-field/",
                {"field": attr, "value": 999},
            )
            self.assertEqual(
                response.status_code, 400, f"{attr} should reject value 999"
            )


class IntFieldPerFieldMaxValidationTest(BaseTestCase):
    """Issue #136: total_xp/starting_coin are 32-bit PositiveIntegerFields and
    must not be capped at the PositiveSmallIntegerField limit that genuinely
    smallint fields (fatigue_roll) are bound by."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user("player")
        # The bounds below are what a Game Master is held to: a player
        # may not set these fields at all (issue #748, test_field_authors.PlayerFieldUpdateTest).
        self.game_master = self.create_dm("validation-gm")
        self.character = Character.objects.create(
            user=self.user,
            name="Test Character",
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
            total_xp=1000,
            starting_coin=100,
        )

    def test_dm_can_set_total_xp_to_level_10_value(self):
        """Level 10 requires 512,000 XP -- well past the old 32767 cap."""
        self.login_as(self.game_master)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/update-field/",
            {"field": "total_xp", "value": 512000},
        )
        self.assertEqual(response.status_code, 200, msg=response.content.decode())
        self.character.refresh_from_db()
        self.assertEqual(self.character.total_xp, 512000)

    def test_starting_coin_accepts_value_past_smallint_limit(self):
        self.login_as(self.game_master)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/update-field/",
            {"field": "starting_coin", "value": 40000},
        )
        self.assertEqual(response.status_code, 200, msg=response.content.decode())
        self.character.refresh_from_db()
        self.assertEqual(self.character.starting_coin, 40000)

    def test_fatigue_roll_rejects_a_value_two_dice_cannot_roll(self):
        """The Fatigue roll is 2d6, so 13 and up are refused (issue #653).

        It used to be bounded only by the smallint ceiling, so 30000 saved
        and flowed into Fatigue, Body, the injury thresholds and pools().
        """
        self.login_as(self.game_master)
        for rejected_value in (13, 30000, 32768):
            with self.subTest(value=rejected_value):
                response = self.post_json(
                    f"/tarmar/characters/{self.character.pk}/update-field/",
                    {"field": "fatigue_roll", "value": rejected_value},
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("12", response.json()["error"])
                self.character.refresh_from_db()
                self.assertEqual(self.character.fatigue_roll, 7)

    def test_fatigue_roll_accepts_every_total_two_dice_can_roll(self):
        """2 through 12 all save, so the bound refuses nothing legitimate."""
        self.login_as(self.game_master)
        for accepted_value in range(2, 13):
            with self.subTest(value=accepted_value):
                response = self.post_json(
                    f"/tarmar/characters/{self.character.pk}/update-field/",
                    {"field": "fatigue_roll", "value": accepted_value},
                )
                self.assertEqual(
                    response.status_code, 200, msg=response.content.decode()
                )
                self.character.refresh_from_db()
                self.assertEqual(self.character.fatigue_roll, accepted_value)

    def test_total_xp_accepts_value_at_smallint_boundary_and_above(self):
        """32768 is exactly one past the old flat cap -- must now be accepted
        for total_xp since it's not a smallint field."""
        self.login_as(self.game_master)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/update-field/",
            {"field": "total_xp", "value": 32768},
        )
        self.assertEqual(response.status_code, 200, msg=response.content.decode())
        self.character.refresh_from_db()
        self.assertEqual(self.character.total_xp, 32768)


class IntFieldPerFieldMinValidationTest(BaseTestCase):
    """Issue #164: total_xp/starting_coin/fatigue_roll are plain
    Positive(Small)IntegerFields with no model-level MinValueValidator, so 0
    is a genuinely storable value and must be accepted -- unlike the six core
    attributes, which do carry MinValueValidator(1) on the model and must
    keep rejecting 0."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user("player")
        # The bounds below are what a Game Master is held to: a player
        # may not set these fields at all (issue #748, test_field_authors.PlayerFieldUpdateTest).
        self.game_master = self.create_dm("validation-gm")
        self.character = Character.objects.create(
            user=self.user,
            name="Test Character",
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
            total_xp=1000,
            starting_coin=100,
        )

    def test_total_xp_accepts_zero(self):
        """A fresh character's total_xp is 0 by model default -- the update
        endpoint (and, by the same validation, import) must accept it."""
        self.login_as(self.game_master)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/update-field/",
            {"field": "total_xp", "value": 0},
        )
        self.assertEqual(response.status_code, 200, msg=response.content.decode())
        self.character.refresh_from_db()
        self.assertEqual(self.character.total_xp, 0)

    def test_starting_coin_accepts_zero(self):
        self.login_as(self.game_master)
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/update-field/",
            {"field": "starting_coin", "value": 0},
        )
        self.assertEqual(response.status_code, 200, msg=response.content.decode())
        self.character.refresh_from_db()
        self.assertEqual(self.character.starting_coin, 0)

    def test_fatigue_roll_rejects_values_below_what_two_dice_can_roll(self):
        """0 and 1 are not 2d6 results, so they are refused (issue #653).

        This used to accept 0, which is where the unbounded field came from:
        the floor was the plain PositiveSmallIntegerField's, not the dice's.
        """
        self.login_as(self.game_master)
        for rejected_value in (0, 1):
            with self.subTest(value=rejected_value):
                response = self.post_json(
                    f"/tarmar/characters/{self.character.pk}/update-field/",
                    {"field": "fatigue_roll", "value": rejected_value},
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("2", response.json()["error"])
                self.character.refresh_from_db()
                self.assertEqual(self.character.fatigue_roll, 7)

    def test_attributes_still_reject_zero(self):
        """Attributes carry MinValueValidator(1) on the model -- 0 STR isn't
        a valid character and must keep failing, unlike the other int fields."""
        self.login_as(self.game_master)
        for attr in [
            "strength",
            "dexterity",
            "intelligence",
            "wisdom",
            "constitution",
            "charisma",
        ]:
            response = self.post_json(
                f"/tarmar/characters/{self.character.pk}/update-field/",
                {"field": attr, "value": 0},
            )
            self.assertEqual(response.status_code, 400, f"{attr} should reject value 0")


class IntFieldMaxMatchesModelFieldTypeTest(TestCase):
    """Issue #136 follow-up: CHARACTER_INT_FIELD_MAX (characters/models.py)
    is hand-kept and nothing else checks it against the model, so a future
    column-type change (e.g. widening/narrowing a field) could silently
    leave a stale cap in place. This introspects each field's actual class
    and asserts the configured cap still matches."""

    # Only field classes CHARACTER_INT_FIELDS is expected to contain. If a
    # field is migrated to some other integer type, this test should fail
    # loudly (via assertIn below) rather than silently pass with the wrong
    # bound -- extend this map deliberately, not just to make the test pass.
    EXPECTED_CAP_BY_FIELD_CLASS = {
        models.PositiveSmallIntegerField: 32767,
        models.PositiveIntegerField: 2147483647,
    }

    def test_cap_matches_actual_model_field_type(self):
        for field_name, configured_cap in CHARACTER_INT_FIELD_MAX.items():
            field_class = Character._meta.get_field(field_name).__class__
            self.assertIn(
                field_class,
                self.EXPECTED_CAP_BY_FIELD_CLASS,
                f"{field_name} is a {field_class.__name__}, which this "
                "drift guard doesn't have an expected cap for -- add it to "
                "EXPECTED_CAP_BY_FIELD_CLASS and confirm CHARACTER_INT_FIELD_MAX",
            )
            expected_cap = self.EXPECTED_CAP_BY_FIELD_CLASS[field_class]
            self.assertEqual(
                configured_cap,
                expected_cap,
                f"{field_name} is a {field_class.__name__} (should cap at "
                f"{expected_cap}) but CHARACTER_INT_FIELD_MAX caps it at "
                f"{configured_cap} -- update the dict in characters/models.py",
            )

    def test_a_roll_is_capped_by_its_dice_not_its_column(self):
        """The Fatigue roll is its dice and nothing wider (issue #653): the
        cap the endpoint and the import apply is the canon's, read when asked."""
        self.assertEqual(
            character_int_field_maximum("fatigue_roll"),
            character_rules().fatigue_roll.maximum,
        )
        for field_name in CHARACTER_INT_FIELD_MAX:
            if field_name == "fatigue_roll":
                continue
            with self.subTest(field=field_name):
                self.assertEqual(
                    character_int_field_maximum(field_name),
                    CHARACTER_INT_FIELD_MAX[field_name],
                )


class IntFieldMinMatchesModelValidatorTest(TestCase):
    """Issue #164 follow-up: CHARACTER_INT_FIELD_MIN (characters/models.py) is
    hand-kept like CHARACTER_INT_FIELD_MAX (#136), and nothing else checks it
    against the model. This introspects each field's MinValueValidator (if
    any) and asserts the configured floor still matches -- a field with no
    MinValueValidator must floor at 0, since that's what the app can
    actually store and a stricter hand-kept floor would break its own
    export/import round trip (as happened for total_xp in #164)."""

    def test_min_matches_actual_model_min_validator(self):
        for field_name, configured_min in CHARACTER_INT_FIELD_MIN.items():
            field = Character._meta.get_field(field_name)
            min_validators = [
                validator
                for validator in field.validators
                if isinstance(validator, MinValueValidator)
            ]
            expected_min = min_validators[0].limit_value if min_validators else 0
            self.assertEqual(
                configured_min,
                expected_min,
                f"{field_name}'s model MinValueValidator floors it at "
                f"{expected_min}, but CHARACTER_INT_FIELD_MIN floors it at "
                f"{configured_min} -- update the dict in characters/models.py",
            )


class UniqueCharacterNameTest(BaseTestCase):
    """Character names need not be unique (issue #571)."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user("player")

    def test_duplicate_names_are_allowed(self):
        """Two of a user's characters may share a name (issue #571)."""
        fields = {
            "user": self.user,
            "name": "Existing Name",
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
        }
        first = Character.objects.create(**fields)
        second = Character.objects.create(**fields)
        self.assertNotEqual(first.pk, second.pk)

    def test_same_name_different_users_allowed(self):
        """Same character name for different users is allowed."""
        user2 = self.create_user("player2")
        Character.objects.create(
            user=self.user,
            name="Shared Name",
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
        )
        # Should not raise - different user
        char2 = Character.objects.create(
            user=user2,
            name="Shared Name",
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
        )
        self.assertEqual(char2.name, "Shared Name")


class ParseDemonDieValidationTest(TestCase):
    """Tests for parse_demon_die input validation."""

    def test_valid_single_roll(self):
        """Valid single roll returns correct list."""
        from characters.dice import parse_demon_die

        self.assertEqual(parse_demon_die("3"), [3])
        self.assertEqual(parse_demon_die("6"), [6])

    def test_valid_exploding_roll(self):
        """Valid exploding roll returns correct list."""
        from characters.dice import parse_demon_die

        self.assertEqual(parse_demon_die("1.1.4"), [1, 1, 4])
        self.assertEqual(parse_demon_die("6.6.2"), [6, 6, 2])

    def test_empty_string_returns_default(self):
        """Empty string returns default [3]."""
        from characters.dice import parse_demon_die

        self.assertEqual(parse_demon_die(""), [3])

    def test_none_returns_default(self):
        """None returns default [3]."""
        from characters.dice import parse_demon_die

        self.assertEqual(parse_demon_die(None), [3])

    def test_invalid_die_value_returns_default(self):
        """Die value outside 1-6 returns default."""
        from characters.dice import parse_demon_die

        self.assertEqual(parse_demon_die("7"), [3])
        self.assertEqual(parse_demon_die("0"), [3])
        self.assertEqual(parse_demon_die("1.7.3"), [3])

    def test_non_numeric_returns_default(self):
        """Non-numeric input returns default."""
        from characters.dice import parse_demon_die

        self.assertEqual(parse_demon_die("abc"), [3])
        self.assertEqual(parse_demon_die("1.a.3"), [3])

    def test_non_string_returns_default(self):
        """Non-string input returns default."""
        from characters.dice import parse_demon_die

        self.assertEqual(parse_demon_die(123), [3])
        self.assertEqual(parse_demon_die([1, 2, 3]), [3])


class ModelValidatorTest(BaseTestCase):
    """Tests for model-level attribute validators."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user("player")

    def test_attribute_validators_reject_over_30(self):
        """Attribute values over 30 should fail validation."""
        from django.core.exceptions import ValidationError

        char = Character(
            user=self.user,
            name="Test",
            strength=31,  # Over max
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
            skills={},
            experience_log=[],
        )
        with self.assertRaises(ValidationError):
            char.full_clean(exclude=["skills", "experience_log"])

    def test_attribute_validators_reject_under_1(self):
        """Attribute values under 1 should fail validation."""
        from django.core.exceptions import ValidationError

        char = Character(
            user=self.user,
            name="Test",
            strength=0,  # Under min
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
            skills={},
            experience_log=[],
        )
        with self.assertRaises(ValidationError):
            char.full_clean(exclude=["skills", "experience_log"])

    def test_valid_attributes_pass(self):
        """Valid attribute values pass validation."""
        char = Character(
            user=self.user,
            name="Test",
            strength=18,
            dexterity=12,
            intelligence=15,
            wisdom=10,
            constitution=14,
            charisma=8,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
            skills={},
            experience_log=[],
        )
        # Validate only specific fields (exclude JSONFields which need blank=True)
        char.full_clean(exclude=["skills", "experience_log"])


class ControlCharacterSanitizationTest(BaseTestCase):
    """Free-text fields drop control characters but keep quotes (issue #120).

    Equipment names/notes and skill names are rendered into quoted HTML
    attributes on the character edit page. The browser-side escaper handles
    quotes and angle brackets; the server's job is only to keep control
    characters — which nobody types on purpose and which corrupt embedded JSON,
    audit lines and exports — out of stored data. Apostrophes and double quotes
    must survive: "Bob's 6\" dagger" is a legitimate item name.
    """

    def setUp(self):
        super().setUp()
        self.user = self.create_user("sanitize-user")
        self.character = Character.objects.create(
            user=self.user,
            name="Test Character",
            strength=15,
            dexterity=12,
            intelligence=10,
            wisdom=13,
            constitution=14,
            charisma=8,
            appearance_roll="3",
            height_roll="4",
            weight_roll="3",
            fatigue_roll=7,
            equipment=[],
        )
        self.login_as(self.user)

    def test_strip_control_characters_removes_and_normalizes(self):
        """Control characters go; whitespace collapses; quotes stay."""
        from characters.constants import strip_control_characters

        self.assertEqual(strip_control_characters("a\x00b"), "a b")
        self.assertEqual(
            strip_control_characters("line one\nline two"), "line one line two"
        )
        self.assertEqual(
            strip_control_characters("  padded\t\tvalue  "), "padded value"
        )
        self.assertEqual(strip_control_characters("tab\u2028sep"), "tab sep")
        self.assertEqual(
            strip_control_characters("Bob's 6\" dagger"), "Bob's 6\" dagger"
        )
        self.assertEqual(
            strip_control_characters("<img src=x onerror=1>"), "<img src=x onerror=1>"
        )

    def test_normalize_skill_name_drops_control_characters(self):
        """A skill name with control characters normalizes without them."""
        from characters.skills import normalize_skill_name

        self.assertEqual(normalize_skill_name("Sword\x00play"), "sword play")
        self.assertEqual(normalize_skill_name("wolf's\x1bbane"), "wolf's bane")

    def test_custom_equipment_name_and_notes_sanitized(self):
        """Custom item name/notes lose control characters, keep quotes."""
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {
                "is_custom": True,
                "name": 'Sword\x00 of "Truth"',
                "category": "gear",
                "weight": 1.0,
                "notes": 'sharp\x07 and "shiny"',
            },
        )
        self.assertEqual(response.status_code, 200)
        item = response.json()["equipment"][0]
        self.assertEqual(item["name"], 'Sword of "Truth"')
        self.assertEqual(item["notes"], 'sharp and "shiny"')

    def test_catalog_equipment_notes_sanitized_on_add(self):
        """Notes on a catalog item are sanitized the same way."""
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/add/",
            {"item_id": "broadsword", "notes": "keen\x1f edge"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["equipment"][0]["notes"], "keen edge")

    def test_equipment_update_notes_sanitized(self):
        """Updating notes strips control characters but keeps a double quote."""
        self.character.equipment = [
            {"item_id": "broadsword", "quantity": 1, "in_pack": False, "notes": ""}
        ]
        self.character.save()

        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/equipment/update/",
            {"index": 0, "notes": 'x\x00" autofocus onfocus="alert(1)'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["equipment"][0]["notes"],
            'x " autofocus onfocus="alert(1)',
        )

    def test_allocated_skill_name_sanitized(self):
        """Allocating a point to a control-character skill stores it clean."""
        self.character.free_skill_points = 1
        self.character.save()

        # "Weapon Parry", the glossary's: "Shield Parry" was a name no rule
        # knows, which a player may no longer start (#862).
        response = self.post_json(
            f"/tarmar/characters/{self.character.pk}/allocate-skill/",
            {"skill": "Weapon\x00Parry"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["skill"], "weapon parry")
        self.character.refresh_from_db()
        self.assertIn("weapon parry", self.character.skills)


class CharacterEditTemplateEscapingTest(TestCase):
    """Guard the escaping fix behind the character edit page (issue #120).

    This JavaScript used to live inline in character_edit.html, out of Jest's
    reach, so these assertions read the template source. Issue #63 moved it to
    static/js/character-edit.js -- where character-edit.test.js now exercises
    escapeHtml's behaviour directly -- so the assertions follow it to the
    static file and keep pinning the same two properties: escapeHtml handles
    quotes (its output lands inside double-quoted attributes), and the
    skill-row lookup escapes the skill name as a CSS selector.

    Jest covers the behaviour; this covers the source, and so still fails if
    the file is reverted to an implementation that happens to satisfy a
    narrower test.
    """

    def _script_source(self):
        from pathlib import Path

        from django.conf import settings

        path = Path(settings.BASE_DIR) / "static" / "js" / "character-edit.js"
        return path.read_text(encoding="utf-8")

    def test_escape_html_escapes_both_quote_characters(self):
        """escapeHtml must neutralize " and ' as well as < > &."""
        source = self._script_source()
        self.assertIn("&quot;", source)
        self.assertIn("&#39;", source)
        self.assertNotIn("div.textContent = text;", source)

    def test_skill_row_lookup_escapes_the_selector(self):
        """The data-skill selector is built with CSS.escape, not raw text."""
        source = self._script_source()
        self.assertIn("CSS.escape(data.skill)", source)
        self.assertNotIn('[data-skill="${data.skill}"]', source)

    def test_edit_page_carries_no_inline_script(self):
        """The template's JavaScript is gone, not merely moved around in it.

        A regression that put a <script> block back into this template would
        put logic beyond Jest's reach again, which is the whole of issue #63.
        The json_script config block is `<script type="application/json">`:
        data, not code, and matched separately so it does not mask a real one.
        """
        from pathlib import Path

        import characters

        source = (
            Path(characters.__file__).parent
            / "templates"
            / "characters"
            / "character_edit.html"
        ).read_text(encoding="utf-8")
        executable_blocks = [
            tag
            for tag in re.findall(r"<script[^>]*>", source)
            if "application/json" not in tag and "src=" not in tag
        ]
        self.assertEqual(executable_blocks, [])


class JSONFieldTypeValidatorTest(TestCase):
    """Container-type validation for the JSON fields (issue #122).

    These validators used to be conditional (``if isinstance(...): check
    size``), so a wrong-typed value skipped validation entirely instead of
    being rejected. They must now raise for any non-container value, not just
    over-sized valid containers.
    """

    def test_validate_skills_size_rejects_non_dict(self):
        from django.core.exceptions import ValidationError

        from characters.models import validate_skills_size

        with self.assertRaises(ValidationError):
            validate_skills_size(["not", "a", "dict"])

    def test_validate_skills_size_accepts_dict(self):
        from characters.models import validate_skills_size

        validate_skills_size({"sword": 3})

    def test_validate_equipment_size_rejects_non_list(self):
        from django.core.exceptions import ValidationError

        from characters.models import validate_equipment_size

        with self.assertRaises(ValidationError):
            validate_equipment_size({"also": "wrong"})

    def test_validate_equipment_size_accepts_list(self):
        from characters.models import validate_equipment_size

        validate_equipment_size([{"item_id": "dagger"}])

    def test_validate_experience_log_size_rejects_non_list(self):
        from django.core.exceptions import ValidationError

        from characters.models import validate_experience_log_size

        with self.assertRaises(ValidationError):
            validate_experience_log_size("wrong")

    def test_validate_experience_log_size_accepts_list(self):
        from characters.models import validate_experience_log_size

        validate_experience_log_size([{"track": "laborer"}])

    def test_validate_history_size_rejects_non_list(self):
        from django.core.exceptions import ValidationError

        from characters.models import validate_history_size

        with self.assertRaises(ValidationError):
            validate_history_size({"not": "a list"})


class CanonRuledIntFieldFloorTest(TestCase):
    """The Fatigue roll's and the age's floors are the canon's (issue #59).

    Their columns carry named validators that read the loaded rules, so no
    MinValueValidator states a number and :data:`CHARACTER_INT_FIELD_MIN`
    holds the column's 0; :func:`character_int_field_minimum` applies the
    rule, the one the validators hold the column to.
    """

    def test_the_floor_is_read_from_the_rules(self):
        rules = character_rules()
        self.assertEqual(
            character_int_field_minimum("fatigue_roll"), rules.fatigue_roll.minimum
        )
        self.assertEqual(character_int_field_minimum("age"), rules.age.starting)
        self.assertEqual(character_int_field_minimum("total_xp"), 0)

    def test_the_column_refuses_what_the_floor_refuses(self):
        for field_name in ("fatigue_roll", "age"):
            floor = character_int_field_minimum(field_name)
            field = Character._meta.get_field(field_name)
            with self.subTest(field=field_name):
                field.run_validators(floor)
                with self.assertRaises(ValidationError):
                    field.run_validators(floor - 1)
