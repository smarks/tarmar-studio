"""The characters app's refusals and its Game Master's call are configuration
(issue #85).

The field table's reasons for the values only a Game Master sets, the service
refusals that share them, and the phrase that marks a Game Master's override
(on a history line, a check's line, the skill check's options and the line the
sheet's script writes) are read from ``config/characters-refusals.toml``
(``settings.CHARACTERS_REFUSAL_WORDS``). The shipped file is Spencer's to edit,
so these tests pin none of its sentences: they run under fixed words in which
each sentence is its own table and key. Changing the words must not change who
is refused, so both sides are checked under the fixed words too.
"""

import ast
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.urls import reverse

from characters import services
from characters.constants import unnamed_character
from characters.purse import PurseError, format_purse, given_by_game_master, move_coin
from characters.refusal_words import load_refusal_words
from characters.services import CharacterChangeError
from characters.skill_checks import skill_check
from characters.tests.fixed_refusals import with_fixed_refusals
from characters.tests.test_skill_checks import FacesRoller
from characters.views.characters import skill_check_options_context
from characters.views.mechanics import GAME_MASTER_CALL_NOTE
from common.test_helpers import BaseTestCase, create_character
from common.tests.refusal_scan import fixed_words, literal_refusals, literal_returns

ROOT = Path(settings.BASE_DIR)

#: Where the Game Master's call used to be written out (issue #85), and must
#: not be again: the string constants of every module of the app (docstrings
#: aside, so #18's ``characters/magic.py`` is held to it at the merge), and the
#: script and template text.
PHRASE = "Game Master's call"
PHRASE_SOURCES = tuple(
    path.relative_to(ROOT).as_posix()
    for path in sorted((ROOT / "characters").rglob("*.py"))
    if "tests" not in path.parts
    and "migrations" not in path.parts
    and not path.name.startswith("test")
)
PHRASE_PAGES = (
    "characters/templates/characters/_skill_check_options.html",
    "static/js/mechanics.js",
)


def _docstrings(tree: ast.Module) -> set[ast.AST]:
    """The docstring constants of a module and its classes and functions."""
    found: set[ast.AST] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
            ):
                found.add(body[0].value)
    return found


class ShippedRefusalWordsTest(SimpleTestCase):
    def test_the_shipped_file_loads_and_settings_hold_the_named_file(self):
        load_refusal_words(ROOT / "config" / "characters-refusals.toml")
        self.assertEqual(
            load_refusal_words(settings.CHARACTERS_REFUSAL_WORDS_FILE),
            settings.CHARACTERS_REFUSAL_WORDS,
        )


class NothingWrittenInCodeTest(SimpleTestCase):
    def test_the_purse_and_the_constants_write_no_sentence_of_their_own(self):
        """The purse's lines and refusals, the point-buy note, the unnamed
        character and a missing one are configuration (#136)."""
        self.assertEqual(
            literal_refusals(
                ROOT / "characters/purse.py", {"PurseError", "too_long", "move_coin"}
            ),
            [],
        )
        self.assertEqual(
            literal_returns(
                ROOT / "characters/purse.py",
                {
                    "format_purse",
                    "move_coin",
                    "purchase_before_wealth_note",
                    "given_by_game_master",
                    "charge_for_catalog_item",
                    "charge_for_custom_item",
                },
            ),
            [],
        )
        self.assertEqual(
            literal_returns(
                ROOT / "characters/constants.py",
                {"too_long", "point_buy_budget_note", "unnamed_character"},
            ),
            [],
        )
        self.assertEqual(
            literal_refusals(
                ROOT / "characters/views/access.py", {"_json_error", "Http404"}
            ),
            [],
        )

    def test_no_field_reason_is_a_literal(self):
        self.assertEqual(
            literal_refusals(
                ROOT / "characters/services.py", {"_game_master", "_said"}
            ),
            [],
        )

    def test_the_rolled_choices_and_the_fallback_read_the_configuration(self):
        """``ROLL_FIXED_REASONS`` holds readers, not sentences, and
        ``field_refusal`` returns no words of its own."""
        tree = ast.parse((ROOT / "characters/services.py").read_text())
        (table,) = [
            node
            for node in tree.body
            if isinstance(node, ast.AnnAssign)
            and getattr(node.target, "id", None) == "ROLL_FIXED_REASONS"
        ]
        assert table.value is not None
        (mapping,) = [
            node for node in ast.walk(table.value) if isinstance(node, ast.Dict)
        ]
        self.assertFalse(
            [value for value in mapping.values if fixed_words(value, set())]
        )
        (refusal,) = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "field_refusal"
        ]
        self.assertFalse(
            [
                node.lineno
                for node in ast.walk(refusal)
                if isinstance(node, ast.Return)
                and node.value is not None
                and fixed_words(node.value, set())
            ]
        )

    def test_the_phrase_is_written_nowhere_but_the_configuration(self):
        for name in PHRASE_SOURCES:
            tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
            docstrings = _docstrings(tree)
            written = [
                node.lineno
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and PHRASE in node.value
                and node not in docstrings
            ]
            with self.subTest(source=name):
                self.assertEqual(written, [])
        for name in PHRASE_PAGES:
            with self.subTest(page=name):
                self.assertNotIn(PHRASE, (ROOT / name).read_text(encoding="utf-8"))


@with_fixed_refusals
class TheWordsFollowTheConfigurationTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.player = self.create_user(username="words-player")
        self.game_master = self.create_dm(username="words-gm")

    def test_the_purse_prints_and_refuses_in_the_configured_words(self):
        """The purse's line, its refusal and a gift read the configuration
        (#136); who is refused is unchanged."""
        character = create_character(self.player, name="Wren")
        line = move_coin(character, Decimal(5), reason="a find")
        self.assertEqual(line, f"purse.received 5 {format_purse(character)} a find")
        self.assertTrue(format_purse(character).startswith("purse."))
        with self.assertRaisesMessage(PurseError, "purse.not_enough "):
            move_coin(character, Decimal(-1_000_000), reason="a castle")
        self.assertEqual(
            given_by_game_master(2, "Rope"),
            "purse.given_by_game_master purse.quantity_of Rope 2",
        )
        self.assertEqual(unnamed_character(), "constants.unnamed_character")

    def test_a_field_reason_is_the_configured_one(self):
        self.assertEqual(
            services.field_refusal("is_dragonborn"),
            "services.dragonborn_game_master_only",
        )
        self.assertEqual(
            services.field_refusal("provenance_class"), "services.field_provenance"
        )
        self.assertEqual(
            services.field_refusal("height_roll"), "services.roll_fixed_height"
        )
        self.assertEqual(
            services.field_refusal("import_notice"),
            "services.nobody_sets import_notice",
        )

    def test_a_player_is_refused_in_the_configured_words_and_a_game_master_is_not(
        self,
    ):
        character = create_character(self.player, name="Aldwin")
        with self.assertRaisesMessage(
            CharacterChangeError, "services.pools_game_master_only"
        ):
            services.set_current_pools(character.pk, fatigue=10)
        done = services.set_current_pools(
            character.pk, fatigue=10, is_game_master=True, actor=self.game_master
        )
        self.assertEqual(done.character.current_fatigue, 10)

    def test_a_history_note_marks_the_game_masters_call(self):
        note = services.check_attribute("strength", 22, is_game_master=True)
        self.assertIn("common.the_game_masters_call", note)
        self.assertEqual(services.check_attribute("strength", 12, False), "")

    def test_a_check_line_and_its_answer_mark_the_call(self):
        reader = create_character(
            self.player,
            name="Dain",
            intelligence=10,
            skills={"literacy": 3},
            literacy_roll=18,
            literacy_rolled=True,
        )
        check = skill_check(
            reader, "literacy", is_game_master=True, roller=FacesRoller(3, 3)
        )
        self.assertTrue(
            check.line.endswith(" (common.a_game_masters_call: not known to read)")
        )
        self.login_as(self.game_master)
        response = self.post_json(
            reverse("characters:skill_check", args=[reader.pk]), {"skill": "Literacy"}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response.json()["check"][GAME_MASTER_CALL_NOTE],
            " (common.a_game_masters_call: not known to read)",
        )
        options = render_to_string(
            "characters/_skill_check_options.html",
            skill_check_options_context(reader),
        )
        self.assertIn("not known to read; common.a_game_masters_call", options)
