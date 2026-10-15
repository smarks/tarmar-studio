"""The shared strict reader for canon rules files (issue #59).

Read through a tiny invented schema, so these tests pin the reader itself
rather than any one canon file. ``battle/tests/test_magic_rules.py`` and
``characters/tests/test_rules.py`` cover the same primitives again through
``magic-rules.json`` and ``character-rules.json``.
"""

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

from django.test import SimpleTestCase

from reference.rules_file import RulesFileError, RulesFileReader


class InventedRulesError(RulesFileError):
    """The invented rules file's own refusal."""


@dataclass(frozen=True)
class InventedStage:
    """One entry of the invented file's ``stages``."""

    key: str
    years: int

    def __post_init__(self) -> None:
        if self.years < 1:
            raise ValueError(f"a stage lasts at least a year, got {self.years}")


@dataclass(frozen=True)
class InventedRules:
    """What the invented file reads into."""

    ceiling: int
    cap: int | None
    title: str
    motto: str | None
    rounds_up: bool
    lucky_totals: frozenset[int]
    faces: tuple[int, ...]
    attributes: tuple[str, ...]
    bonuses: dict[str, int]
    stages: tuple[InventedStage, ...]


STAGE_FIELDS = ("key", "years")
TOP_LEVEL_FIELDS = (
    "ceiling",
    "cap",
    "title",
    "motto",
    "rounds_up",
    "lucky_totals",
    "faces",
    "attributes",
    "bonuses",
    "stages",
)
OPTIONAL_TOP_LEVEL_FIELDS = ("notes",)


class InventedRulesReader(RulesFileReader):
    """Reads the invented file with nothing but the shared primitives."""

    error_class = InventedRulesError

    def stage(self, value: Any, field_path: str) -> InventedStage:
        """One entry of ``stages``."""
        fields = self.json_object(value, field_path, STAGE_FIELDS)
        with self.wrapped_value_errors(field_path):
            return InventedStage(
                key=self.text(fields["key"], f"{field_path}.key"),
                years=self.integer(fields["years"], f"{field_path}.years"),
            )

    def invented_rules(self, value: Any) -> InventedRules:
        """The whole file."""
        fields = self.file_object(value, TOP_LEVEL_FIELDS, OPTIONAL_TOP_LEVEL_FIELDS)
        stages = self.entries(fields["stages"], "stages", self.stage)
        self.unique_keys(stages, "stages")
        return InventedRules(
            ceiling=self.integer(fields["ceiling"], "ceiling"),
            cap=self.optional_integer(fields["cap"], "cap"),
            title=self.text(fields["title"], "title"),
            motto=self.optional_text(fields["motto"], "motto"),
            rounds_up=self.boolean(fields["rounds_up"], "rounds_up"),
            lucky_totals=self.integer_set(fields["lucky_totals"], "lucky_totals"),
            faces=self.integer_list(fields["faces"], "faces"),
            attributes=self.text_list(fields["attributes"], "attributes"),
            bonuses=self.mapping(fields["bonuses"], "bonuses", self.integer),
            stages=stages,
        )


INVENTED_FILE: dict[str, Any] = {
    "ceiling": 18,
    "cap": None,
    "title": "Invented",
    "motto": None,
    "rounds_up": True,
    "lucky_totals": [3, 18],
    "faces": [4, 4, 6],
    "attributes": ["STR", "DEX"],
    "bonuses": {"damage": 2},
    "stages": [{"key": "young", "years": 10}, {"key": "old", "years": 20}],
    "notes": "optional, and not read",
    "rulings": [
        {"key": "ceiling", "reading": "Ruled 18.", "page": "invented.md"},
    ],
}

INVENTED_LOADED = InventedRules(
    ceiling=18,
    cap=None,
    title="Invented",
    motto=None,
    rounds_up=True,
    lucky_totals=frozenset({3, 18}),
    faces=(4, 4, 6),
    attributes=("STR", "DEX"),
    bonuses={"damage": 2},
    stages=(InventedStage("young", 10), InventedStage("old", 20)),
)


def invented_file(**changes: Any) -> dict[str, Any]:
    """:data:`INVENTED_FILE` with top-level fields replaced by ``changes``."""
    return json.loads(json.dumps(INVENTED_FILE)) | changes


class RulesFileReaderTest(SimpleTestCase):
    """Each primitive, refusing with the file, the field path and its own error."""

    def setUp(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.rules_file = directory / "invented-rules.json"

    def write_text(self, text: str) -> Path:
        """Write ``text`` as the file and return its path."""
        self.rules_file.write_text(text, encoding="utf-8")
        return self.rules_file

    def load(self, path: Path) -> InventedRules:
        """Read ``path`` through the invented reader."""
        reader = InventedRulesReader(path)
        return reader.invented_rules(reader.parsed_file())

    def assert_refused(self, contents: Any, *expected_texts: str) -> None:
        """Loading ``contents`` raises the reader's error naming the file
        and each of ``expected_texts``."""
        with self.assertRaises(InventedRulesError) as caught:
            self.load(self.write_text(json.dumps(contents)))
        message = str(caught.exception)
        self.assertIn(str(self.rules_file), message)
        for expected_text in expected_texts:
            self.assertIn(expected_text, message)

    def test_a_full_file_reads(self) -> None:
        self.assertEqual(
            self.load(self.write_text(json.dumps(INVENTED_FILE))), INVENTED_LOADED
        )

    def assert_file_refused(self, *expected_texts: str) -> InventedRulesError:
        """Loading the file as it stands raises the reader's error naming the
        file and each of ``expected_texts``."""
        with self.assertRaises(InventedRulesError) as caught:
            self.load(self.rules_file)
        message = str(caught.exception)
        self.assertIn(str(self.rules_file), message)
        for expected_text in expected_texts:
            self.assertIn(expected_text, message)
        return caught.exception

    def test_the_error_is_a_plain_exception(self) -> None:
        # Not Django's ImproperlyConfigured, so this module loads no Django and
        # the offline CLI's gm.engine chain can reach it (gm/tests.py's
        # test_engine_imports_without_django); not a ValueError, which
        # wrapped_value_errors would wrap a second time.
        self.assertEqual(RulesFileError.__bases__, (Exception,))

    def test_a_reader_without_its_own_error_raises_the_base_error(self) -> None:
        reader = RulesFileReader(self.rules_file)
        with self.assertRaises(RulesFileError) as caught:
            reader.integer("seven", "ceiling")
        self.assertIs(type(caught.exception), RulesFileError)
        self.assertIn(str(self.rules_file), str(caught.exception))

    def test_a_missing_file_is_refused(self) -> None:
        self.assert_file_refused("is missing; every canon must carry one")

    def test_an_unreadable_file_is_refused(self) -> None:
        self.write_text(json.dumps(INVENTED_FILE))
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            refusal = self.assert_file_refused("could not be read")
        self.assertIsInstance(refusal.__cause__, PermissionError)

    def test_invalid_json_is_refused(self) -> None:
        self.write_text("{not json")
        self.assert_file_refused("is not valid JSON")

    def test_a_key_repeated_in_one_object_is_refused(self) -> None:
        # json.loads alone would keep the second "ceiling" and drop the first.
        self.write_text('{"ceiling": 1, ' + json.dumps(INVENTED_FILE)[1:])
        self.assert_file_refused("is not valid JSON", "['ceiling']")

    def test_a_file_that_is_not_an_object_is_refused(self) -> None:
        self.assert_refused([], "the file", "must hold a JSON object")

    def test_an_unknown_key_is_refused(self) -> None:
        self.assert_refused(invented_file(cieling=18), "cieling", "not a field")

    def test_a_missing_field_is_refused(self) -> None:
        contents = invented_file()
        del contents["stages"][1]["years"]
        self.assert_refused(contents, "stages[1].years is missing")

    def test_a_boolean_is_not_an_integer(self) -> None:
        self.assert_refused(invented_file(ceiling=True), "ceiling", "an integer")
        self.assert_refused(invented_file(cap=False), "cap", "an integer or null")

    def test_a_boolean_must_be_true_or_false(self) -> None:
        self.assert_refused(invented_file(rounds_up=1), "rounds_up", "true or false")

    def test_an_empty_text_is_refused(self) -> None:
        self.assert_refused(invented_file(title=""), "title", "non-empty string")
        self.assert_refused(invented_file(motto=""), "motto", "string or null")

    def test_a_set_that_repeats_an_entry_is_refused(self) -> None:
        self.assert_refused(
            invented_file(lucky_totals=[3, 3]), "lucky_totals", "repeats a total"
        )
        self.assert_refused(
            invented_file(attributes=["STR", "STR"]), "attributes", "repeats an entry"
        )

    def test_an_ordered_list_keeps_its_repeats_and_refuses_a_non_integer(
        self,
    ) -> None:
        self.assertEqual(
            self.load(self.write_text(json.dumps(invented_file(faces=[2, 2])))).faces,
            (2, 2),
        )
        self.assert_refused(invented_file(faces=[1, "2"]), "faces[1]", "an integer")

    def test_rulings_are_optional_on_every_file(self) -> None:
        contents = invented_file()
        del contents["rulings"]
        self.assertEqual(
            self.load(self.write_text(json.dumps(contents))), INVENTED_LOADED
        )

    def test_a_malformed_ruling_is_refused(self) -> None:
        cases = (
            ({}, "rulings", "must be a list"),
            (["Ruled 18."], "rulings[0]", "must be an object"),
            ([{"key": "ceiling"}], "rulings[0].reading", "non-empty string"),
            ([{"key": "", "reading": "r"}], "rulings[0].key", "non-empty string"),
        )
        for rulings, field_path, problem in cases:
            with self.subTest(field_path):
                self.assert_refused(invented_file(rulings=rulings), field_path, problem)

    def test_a_nested_value_that_is_not_an_object_is_refused(self) -> None:
        self.assert_refused(invented_file(stages=[5]), "stages[0]", "must be an object")

    def test_a_list_that_is_not_a_list_is_refused(self) -> None:
        self.assert_refused(invented_file(stages={}), "stages", "must be a list")

    def test_a_mapping_with_an_empty_key_is_refused(self) -> None:
        self.assert_refused(invented_file(bonuses={"": 1}), "bonuses", "empty key")
        self.assert_refused(invented_file(bonuses=[]), "bonuses", "an object")

    def test_a_mapping_entry_is_named_by_its_key(self) -> None:
        self.assert_refused(
            invented_file(bonuses={"damage": True}), "bonuses.damage", "an integer"
        )

    def test_entries_sharing_a_key_are_refused(self) -> None:
        stages = [{"key": "young", "years": 1}, {"key": "young", "years": 2}]
        self.assert_refused(invented_file(stages=stages), "stages", "['young']")

    def test_a_value_error_is_named_by_its_field_path(self) -> None:
        stages = [{"key": "young", "years": 0}]
        self.assert_refused(
            invented_file(stages=stages), "stages[0]:", "at least a year, got 0"
        )
