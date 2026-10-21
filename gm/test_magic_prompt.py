"""The magic section of the GM prompt is read from the canon (#922, #48, #127).

``SECTION_MAGIC`` used to be a string in ``gm.engine``. The Tarmar version
carries the GM-side numbers the public magic pages leave out, so it moved into
the canon directory as ``gm-prompt/magic.md`` (#922). Its Special Casting
Results and Borrowing (#48), and its Push, Channel, Runaway and Counterspell
(#127), are now built from the canon's ``magic-rules.json`` around the file,
so the file leaves them empty and ``gm.engine.section_magic`` assembles them. These tests pin the assembled
Tarmar section, that a canon without the file gets the default canon's
section, that the assembler refuses a body under a heading the engine builds
or a missing one, and that the default canon's section keeps to the public
rules and the headings every magic section has.
"""

import tempfile
from pathlib import Path
from unittest import skipUnless

from django.test import SimpleTestCase

from battle.magic_rules import magic_rules
from battle.tests.magic_fixtures import DEFAULT_MAGIC_RULES
from common.test_helpers import CANON_EXPECTATIONS, skip_unless_canon_expects
from reference.paths import (
    CANON_NAME,
    DEFAULT_CANON_NAME,
    DEFAULT_MAGIC_PROMPT_FILE,
    DM_RULES_DIR,
    MAGIC_PROMPT_FILE,
    TARMAR_CANON_NAME,
)

from .engine import (
    GENERATED_MAGIC_HEADINGS,
    MagicSectionError,
    assemble_magic_section,
    checked_magic_section,
    load_magic_section,
    section_magic,
    system_prompt,
)
from .test_world_prompt import (
    DEFAULT_WORLD_PROMPT_FILE,
    import_the_engine_on_a_test_canon,
)
from .turn_record import prompt_hash

# ``prompt_hash`` of the assembled Tarmar ``section_magic()``. It was
# 5cb0048b89a637f26c95041afe21b8990bbda1fc3d7bd548f766cd5c18ce17d1, the old
# ``SECTION_MAGIC`` constant byte for byte (#922), until #48 built the Special
# Casting Results and Borrowing from the canon's ``magic-rules.json``: the
# table now states the critical success's +2 and 1 mana back and that a 16
# keeps its mana, and Borrowing states the natural 18 that always fails, that
# a failure is a Runaway, and that refunds pay the Debt first. That was
# d13c58b26bbfe092f3d704371fbd3ae991d4f3d97c06d27219a1772d40662741 until #127
# built Push, Channel, Runaway and Counterspell from the same file: Push's
# penalty now counts the "mana pushed" (the canon's base cost does not count)
# and each failure band says whether the spell takes effect; Channel says the
# cap covers the cost and the pushed mana and that Arcane costs 2× to advance;
# Runaway's growth states the +1 a turn it ran that the board banks and adds
# once to the anchorer's next cast of that spell (a continuing spell's growth
# stays the GM's, ruling runaway_effect_growth), and
# the helper pays "the original casting cost"; Counterspell states the least
# commitment (3), the opposed dice (3d6 + INT) and that the bonus is each
# side's.
TARMAR_MAGIC_SECTION_HASH = (
    "88512526e0974102379bef1acb65aa38494176cfd2f6ad701e2907defd4e386c"
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
class TarmarMagicSectionTest(SimpleTestCase):
    """The Tarmar canon's assembled magic section is the pinned text.

    A change to it changes the production prompt, and with it the prompt
    snapshot every later turn records (``GMPromptSnapshot``), so it is made
    on purpose: update the hash and say why beside it.
    """

    def test_the_magic_section_is_the_pinned_text(self) -> None:
        self.assertEqual(prompt_hash(section_magic()), TARMAR_MAGIC_SECTION_HASH)


class MagicSectionFromCanonTest(SimpleTestCase):
    """Where the magic section comes from, and what a canon without one gets."""

    def setUp(self) -> None:
        self.temporary_directory = Path(
            self.enterContext(tempfile.TemporaryDirectory())
        )

    def test_the_section_is_the_loaded_canons_file_and_rules(self) -> None:
        self.assertEqual(MAGIC_PROMPT_FILE, DM_RULES_DIR / "gm-prompt" / "magic.md")
        self.assertTrue(MAGIC_PROMPT_FILE.is_file())
        self.assertEqual(
            section_magic(),
            assemble_magic_section(
                MAGIC_PROMPT_FILE.read_text(encoding="utf-8").rstrip("\n"),
                magic_rules(),
            ),
        )
        self.assertIn(section_magic(), system_prompt())

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


(
    _SPECIAL_CASTING_RESULTS,
    _PUSH,
    _CHANNEL,
    _BORROWING,
    _RUNAWAY,
    _COUNTERSPELL,
) = GENERATED_MAGIC_HEADINGS

#: A magic section with every generated heading empty, a subsection of prose
#: between two of them, generated headings one after another, and the last
#: generated heading at the end of the file.
INVENTED_MAGIC_FILE = (
    "## Magic System\nOpening.\n\n"
    f"{_SPECIAL_CASTING_RESULTS}\n\n"
    "### Schools of Magic\nSchools.\n  Indented, kept as written.\n\n"
    f"{_PUSH}\n\n{_CHANNEL}\n\n{_BORROWING}\n\n{_RUNAWAY}\n\n{_COUNTERSPELL}"
)


class AssembleMagicSectionTest(SimpleTestCase):
    """The engine's subsections go in under their empty headings (#48, #127)."""

    def test_each_generated_heading_takes_its_body_and_the_rest_is_kept(self) -> None:
        assembled = assemble_magic_section(INVENTED_MAGIC_FILE, DEFAULT_MAGIC_RULES)
        self.assertTrue(
            assembled.startswith(
                f"## Magic System\nOpening.\n\n{_SPECIAL_CASTING_RESULTS}\n3: "
            )
        )
        self.assertIn(
            ".\n\n### Schools of Magic\nSchools.\n  Indented, kept as written.\n\n"
            f"{_PUSH}\nInvest extra mana",
            assembled,
        )
        for heading, next_heading in (
            (_PUSH, _CHANNEL),
            (_CHANNEL, _BORROWING),
            (_BORROWING, _RUNAWAY),
            (_RUNAWAY, _COUNTERSPELL),
        ):
            with self.subTest(heading=heading):
                body_start = assembled.index(f"{heading}\n") + len(heading) + 1
                self.assertEqual(
                    assembled.index("\n\n", body_start),
                    assembled.index(f"\n\n{next_heading}\n"),
                )
        self.assertTrue(
            assembled.endswith(
                f"{_COUNTERSPELL}\nThe defender commits 1 to 5 mana and declares a "
                "counter (on their action); opposed 2d10 + INT rolls, each side +1 per "
                "2 mana invested (the caster's whole casting, the defender's "
                "commitment); ties go to the defender. Defender wins: spell "
                "countered. Caster wins: spell goes off. Only the caster can escalate "
                "on later turns by paying again; the defender gets one shot per "
                "casting and may oppose again (paying again) or let it through. A "
                "failed Control Roll during escalation can trigger Runaway."
            )
        )

    def test_a_body_under_a_generated_heading_is_refused(self) -> None:
        for heading in GENERATED_MAGIC_HEADINGS:
            with self.subTest(heading=heading):
                written = INVENTED_MAGIC_FILE.replace(
                    heading, f"{heading}\nHand-written numbers."
                )
                for check in (
                    checked_magic_section,
                    lambda text: assemble_magic_section(text, DEFAULT_MAGIC_RULES),
                ):
                    with self.assertRaisesMessage(
                        MagicSectionError, f"{heading!r} has a body"
                    ):
                        check(written)

    def test_a_missing_generated_heading_is_refused(self) -> None:
        for heading in GENERATED_MAGIC_HEADINGS:
            with self.subTest(heading=heading):
                without = INVENTED_MAGIC_FILE.replace(heading, "### Something Else")
                with self.assertRaisesMessage(MagicSectionError, f"no {heading!r}"):
                    checked_magic_section(without)

    def test_a_generated_heading_written_twice_is_refused(self) -> None:
        for heading in GENERATED_MAGIC_HEADINGS:
            with self.subTest(heading=heading):
                twice = f"{INVENTED_MAGIC_FILE}\n\n{heading}"
                with self.assertRaisesMessage(
                    MagicSectionError, f"{heading!r} 2 times"
                ):
                    checked_magic_section(twice)

    def test_a_heading_with_extra_words_is_not_the_generated_one(self) -> None:
        renamed = INVENTED_MAGIC_FILE.replace(_BORROWING, f"{_BORROWING} (Debt)")
        with self.assertRaisesMessage(MagicSectionError, f"no {_BORROWING!r}"):
            checked_magic_section(renamed)

    def test_a_checked_file_comes_back_unchanged(self) -> None:
        self.assertIs(checked_magic_section(INVENTED_MAGIC_FILE), INVENTED_MAGIC_FILE)


class MagicSectionAtStartupTest(SimpleTestCase):
    """A canon's magic file is read and checked as ``gm.engine`` imports (#48),
    so a broken one stops the process rather than failing the first turn."""

    def setUp(self) -> None:
        self.temporary_directory = Path(
            self.enterContext(tempfile.TemporaryDirectory())
        )
        self.world_text = DEFAULT_WORLD_PROMPT_FILE.read_text(encoding="utf-8")

    def test_a_tarmar_canon_without_the_magic_file_does_not_start(self) -> None:
        completed = import_the_engine_on_a_test_canon(
            self.temporary_directory,
            "from gm import engine\n",
            canon_json=None,
            prompt_files={DEFAULT_WORLD_PROMPT_FILE.name: self.world_text},
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(
            "FileNotFoundError: The Tarmar canon has no magic section",
            completed.stderr,
        )

    def test_a_magic_file_with_a_generated_body_does_not_start(self) -> None:
        written = DEFAULT_MAGIC_PROMPT_FILE.read_text(encoding="utf-8").replace(
            f"{_BORROWING}\n", f"{_BORROWING}\nHand-written numbers.\n"
        )
        completed = import_the_engine_on_a_test_canon(
            self.temporary_directory,
            "from gm import engine\n",
            canon_json='{"name": "default"}',
            prompt_files={
                DEFAULT_WORLD_PROMPT_FILE.name: self.world_text,
                DEFAULT_MAGIC_PROMPT_FILE.name: written,
            },
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(
            f"MagicSectionError: The magic section's {_BORROWING!r} has a body",
            completed.stderr,
        )


class DefaultCanonMagicSectionTest(SimpleTestCase):
    """The default canon's section, checked whichever canon is loaded."""

    def setUp(self) -> None:
        self.section = assemble_magic_section(
            load_magic_section(DEFAULT_MAGIC_PROMPT_FILE), DEFAULT_MAGIC_RULES
        )

    def test_it_has_the_subsections_every_magic_section_has_in_order(self) -> None:
        positions = [self.section.index(heading) for heading in MAGIC_HEADINGS]
        self.assertEqual(positions, sorted(positions))
        self.assertTrue(self.section.startswith(MAGIC_HEADINGS[0]))

    def test_one_blank_line_comes_before_every_subsection(self) -> None:
        for heading in MAGIC_HEADINGS[1:]:
            with self.subTest(heading=heading):
                self.assertIn(f".\n\n{heading}\n", self.section)
        self.assertNotIn("\n\n\n", self.section)

    @skip_unless_canon_expects("gm_side_magic_phrases")
    def test_it_states_none_of_the_tarmar_canons_gm_side_numbers(self) -> None:
        for phrase in CANON_EXPECTATIONS["gm_side_magic_phrases"]:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase.lower(), self.section.lower())

    @skip_unless_canon_expects("gm_side_magic_phrases")
    def test_the_tarmar_section_states_every_gm_side_number(self) -> None:
        """The phrases are real: each is in the section they are kept out of."""
        tarmar_section = section_magic()
        for phrase in CANON_EXPECTATIONS["gm_side_magic_phrases"]:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase.lower(), tarmar_section.lower())
