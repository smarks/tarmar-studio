"""One name rule on every path that names a character (issue #870).

``Character.clean_field_value("name", ...)`` says what a name may be, and the
sheet's rename, the creation page and the import asked it. The Games Master
chat, a Game Master's blank name on the creation page and battle generation
stored names without asking, so a character could hold a name its own export
could not bring back in. Each path now holds to the rule: a name somebody gave
is refused when it breaks it, and a name made from other text keeps only what
the rule accepts.
"""

import json

from django.core.exceptions import ValidationError

from battle.generation import generate_character
from character_export.export_utils import generate_character_json
from characters import services
from characters.constants import (
    CHARACTER_NAME_PATTERN,
    name_within_rule,
    unnamed_character,
)
from characters.import_service import validate_import_payload
from characters.models import Character
from common.test_helpers import BaseTestCase, create_character
from gm import roster
from gm.models import GMSession, SessionCharacter
from gm.testing import run_actions

CREATE_URL = "/tarmar/characters/create/"


def imports_again(character: Character) -> bool:
    """Whether the character's own export passes the importer's checks."""
    validate_import_payload(json.loads(generate_character_json(character)))
    return True


class NameWithinRuleTest(BaseTestCase):
    def test_it_keeps_only_what_the_rule_accepts(self):
        for text, expected in (
            ("Aldwin, the Grey", "Aldwin the Grey"),
            ("probe.gm+1@x", "probe.gm1x"),
            ("Kestra Duskmere (2)", "Kestra Duskmere 2"),
            ("  O'Brien-Smith_the.3rd  ", "O'Brien-Smith_the.3rd"),
            ("tab\there", "tab here"),
        ):
            with self.subTest(text=text):
                self.assertEqual(name_within_rule(text, fallback="x"), expected)

    def test_nothing_left_is_the_fallback(self):
        self.assertEqual(
            name_within_rule("!!! ,,, @@@", fallback=unnamed_character()),
            unnamed_character(),
        )

    def test_a_long_text_is_capped_and_still_a_name(self):
        name = name_within_rule("word, " * 40, fallback="x")
        self.assertLessEqual(len(name), 100)
        self.assertRegex(name, CHARACTER_NAME_PATTERN)
        self.assertEqual(name, name.strip())


class ChatNamesTest(BaseTestCase):
    """The Games Master chat: a start, and a rename."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user("chat-namer")
        self.session = GMSession.objects.create(user=self.owner, name="Names")

    def test_a_name_the_rule_refuses_refuses_the_start(self):
        with self.assertRaises(ValidationError):
            roster.start_character(
                self.session, "the grey monk", name="Aldwin, the Grey"
            )
        self.assertFalse(Character.objects.filter(user=self.owner).exists())

    def test_the_plan_step_fails_with_the_rules_reason(self):
        game_master = self.create_dm("chat-naming-gm")
        session = GMSession.objects.create(user=game_master, name="GM names")
        (result,) = run_actions(
            session,
            {
                "action": "record_attributes",
                "new_character": {"name": "Aldwin, the Grey"},
                "all": 12,
            },
        )
        self.assertFalse(result.ran)
        self.assertIn("apostrophes only", result.error)
        self.assertFalse(Character.objects.filter(user=game_master).exists())

    def test_a_good_name_is_kept_as_given(self):
        character = roster.start_character(self.session, "the monk", name="Aldwin")
        self.assertEqual(character.name, "Aldwin")

    def test_a_label_becomes_a_name_the_rule_accepts(self):
        character = roster.start_character(self.session, "Aldwin, the Grey")
        self.assertEqual(character.name, "Aldwin the Grey")
        self.assertEqual(
            SessionCharacter.objects.get(character=character).label,
            "Aldwin, the Grey",
        )
        self.assertTrue(imports_again(character))

    def test_a_label_with_nothing_the_rule_accepts_is_unnamed(self):
        character = roster.start_character(self.session, "???")
        self.assertEqual(character.name, unnamed_character())

    def test_a_rename_is_held_to_the_rule_on_the_service_too(self):
        character = create_character(self.owner, name="Aldwin")
        with self.assertRaises(ValidationError):
            services.rename(character.pk, "Aldwin, the Grey")
        character.refresh_from_db()
        self.assertEqual(character.name, "Aldwin")
        services.rename(character.pk, "Aldwin the Grey")
        character.refresh_from_db()
        self.assertEqual(character.name, "Aldwin the Grey")


class GameMasterBlankNameTest(BaseTestCase):
    """The creation page's Game Master fallback to the account name."""

    def test_an_account_name_the_rule_refuses_is_made_one_it_accepts(self):
        game_master = self.create_dm("probe.gm+1@x")
        self.login_as(game_master)
        response = self.client.post(CREATE_URL, {"method": "roll"})
        self.assertEqual(response.status_code, 302)
        character = Character.objects.get(user=game_master)
        self.assertEqual(character.name, "probe.gm1x")
        self.assertTrue(imports_again(character))


class BattleGenerationNamesTest(BaseTestCase):
    """A battle's free-text name no longer lands in its fighters' names."""

    def test_a_battle_name_with_punctuation_names_importable_fighters(self):
        owner = self.create_user("battle-namer")
        generated = generate_character(owner, "Fight at dawn, bring torches!", 0)
        character = generated.character
        self.assertRegex(character.name, CHARACTER_NAME_PATTERN)
        self.assertTrue(character.name.startswith("Fight at dawn bring torches "))
        self.assertTrue(imports_again(character))
