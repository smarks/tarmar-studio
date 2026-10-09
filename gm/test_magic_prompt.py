"""The magic section of the GM prompt is read from the canon (issue #922).

``SECTION_MAGIC`` used to be a string in ``gm.engine``. The Tarmar version
carries the GM-side numbers the public magic pages leave out, so it moved into
the canon directory as ``gm-prompt/magic.md``. These tests pin that the move
changed no byte of the Tarmar section, that a canon without the file gets the
default canon's section, and that the default canon's section keeps to the
public rules and the headings every magic section has.
"""

import tempfile
from pathlib import Path
from unittest import skipUnless

from django.test import SimpleTestCase

from common.test_helpers import CANON_EXPECTATIONS, skip_unless_canon_expects
from reference.paths import (
    CANON_NAME,
    DEFAULT_CANON_NAME,
    DEFAULT_MAGIC_PROMPT_FILE,
    DM_RULES_DIR,
    MAGIC_PROMPT_FILE,
    TARMAR_CANON_NAME,
)

from .engine import SECTION_MAGIC, SYSTEM_PROMPT, load_magic_section
from .turn_record import prompt_hash

# ``prompt_hash`` of the old ``SECTION_MAGIC`` constant, as it stood in
# ``gm.engine`` at 024df6b before it moved into the canon.
MAGIC_SECTION_HASH_BEFORE_THE_MOVE = (
    "5cb0048b89a637f26c95041afe21b8990bbda1fc3d7bd548f766cd5c18ce17d1"
)

# The subsections every magic section carries, in this order: checked here
# file by file, and inside the whole prompt by ``gm.tests``, whose
# ``REQUIRED_HEADINGS`` takes them from here.
MAGIC_HEADINGS = (
    "## Magic System",
    "### Special Casting Results",
    "### Schools of Magic",
    "### Spell Mastery",
    "### Push (Boosting)",
    "### Channel",
    "### Borrowing",
    "### Runaway",
    "### Counterspell",
)


@skipUnless(CANON_NAME == TARMAR_CANON_NAME, "pins the Tarmar canon's section")
class TarmarMagicSectionUnchangedTest(SimpleTestCase):
    """The Tarmar canon's ``magic.md`` is the old constant, byte for byte.

    The real gate is ``GMPromptSnapshot`` after deploy: the first turn must
    reuse the latest snapshot rather than create a new one.
    """

    def test_the_magic_section_is_the_old_constant(self) -> None:
        self.assertEqual(prompt_hash(SECTION_MAGIC), MAGIC_SECTION_HASH_BEFORE_THE_MOVE)


class MagicSectionFromCanonTest(SimpleTestCase):
    """Where the magic section comes from, and what a canon without one gets."""

    def setUp(self) -> None:
        self.temporary_directory = Path(
            self.enterContext(tempfile.TemporaryDirectory())
        )

    def test_the_section_is_the_loaded_canons_file(self) -> None:
        self.assertEqual(MAGIC_PROMPT_FILE, DM_RULES_DIR / "gm-prompt" / "magic.md")
        self.assertTrue(MAGIC_PROMPT_FILE.is_file())
        self.assertEqual(
            SECTION_MAGIC, MAGIC_PROMPT_FILE.read_text(encoding="utf-8").rstrip("\n")
        )
        self.assertIn(SECTION_MAGIC, SYSTEM_PROMPT)

    def test_the_files_trailing_newlines_do_not_reach_the_prompt(self) -> None:
        magic_file = self.temporary_directory / "magic.md"
        magic_file.write_text("## Magic System\nText.\n\n", encoding="utf-8")
        self.assertEqual(load_magic_section(magic_file), "## Magic System\nText.")

    def test_a_canon_without_the_file_gets_the_default_canons(self) -> None:
        missing = self.temporary_directory / "magic.md"
        with self.assertLogs("gm.engine", level="WARNING") as logged:
            section = load_magic_section(missing, canon_name=DEFAULT_CANON_NAME)
        self.assertEqual(
            section,
            DEFAULT_MAGIC_PROMPT_FILE.read_text(encoding="utf-8").rstrip("\n"),
        )
        self.assertIn(str(missing), logged.output[0])

    def test_no_section_anywhere_is_an_error(self) -> None:
        with self.assertLogs("gm.engine", level="WARNING"):
            with self.assertRaisesMessage(FileNotFoundError, "No magic section"):
                load_magic_section(
                    self.temporary_directory / "magic.md",
                    self.temporary_directory / "fallback.md",
                    canon_name=DEFAULT_CANON_NAME,
                )

    def test_the_tarmar_canon_without_the_file_is_an_error(self) -> None:
        """No fallback: it would quietly change the production prompt."""
        missing = self.temporary_directory / "magic.md"
        with self.assertRaisesMessage(
            FileNotFoundError, f"The Tarmar canon has no magic section at {missing}"
        ):
            load_magic_section(missing, canon_name=TARMAR_CANON_NAME)

    def test_a_directory_where_the_file_should_be_is_an_error(self) -> None:
        directory = self.temporary_directory / "magic.md"
        directory.mkdir()
        with self.assertRaisesMessage(IsADirectoryError, "is not a file"):
            load_magic_section(directory)


class DefaultCanonMagicSectionTest(SimpleTestCase):
    """The default canon's section, checked whichever canon is loaded."""

    def setUp(self) -> None:
        self.section = load_magic_section(DEFAULT_MAGIC_PROMPT_FILE)

    def test_it_has_the_subsections_every_magic_section_has_in_order(self) -> None:
        positions = [self.section.index(heading) for heading in MAGIC_HEADINGS]
        self.assertEqual(positions, sorted(positions))
        self.assertTrue(self.section.startswith(MAGIC_HEADINGS[0]))

    @skip_unless_canon_expects("gm_side_magic_phrases")
    def test_it_states_none_of_the_tarmar_canons_gm_side_numbers(self) -> None:
        for phrase in CANON_EXPECTATIONS["gm_side_magic_phrases"]:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase.lower(), self.section.lower())

    @skip_unless_canon_expects("gm_side_magic_phrases")
    def test_the_tarmar_section_states_every_gm_side_number(self) -> None:
        """The phrases are real: each is in the section they are kept out of."""
        tarmar_section = load_magic_section(MAGIC_PROMPT_FILE)
        for phrase in CANON_EXPECTATIONS["gm_side_magic_phrases"]:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase.lower(), tarmar_section.lower())
