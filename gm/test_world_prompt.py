"""The world section of the GM prompt is read from the canon (issue #919).

``SECTION_WORLD`` used to be a string in ``gm.engine``. It is canon, so it now
lives in the canon directory as ``gm-prompt/world.md`` and leaves this
repository with the rest of the canon. These tests pin that the move changed
no byte of the Tarmar world section, that a canon without the file still gets a
world section, that the default canon's digest has the shape every digest
has, and that the file is prompt text, never a reference page.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.test import SimpleTestCase

from common.test_helpers import (
    CANON_EXPECTATIONS,
    BaseTestCase,
    skip_unless_canon_expects,
)
from reference import search_index
from reference.paths import (
    CANON_CHARACTER_RULES_FILE_NAME,
    CANON_DIR_ENV,
    CANON_EQUIPMENT_CATALOG_FILE_NAME,
    CANON_EQUIPMENT_RULES_FILE_NAME,
    CANON_NAME,
    CANON_PROMPT_DIR_NAME,
    CANON_SKILL_RULES_FILE_NAME,
    CONTENT_DIR,
    DEFAULT_CANON_DIR,
    DEFAULT_CANON_NAME,
    DM_RULES_DIR,
    REPO_ROOT,
    TARMAR_CANON_NAME,
    WORLD_PROMPT_FILE,
    iter_content_markdown,
)

from .engine import (
    FALLBACK_WORLD_SECTION,
    SECTION_WORLD,
    load_world_section,
)
from .retrieval import iter_corpus_files
from .turn_record import prompt_hash

# ``prompt_hash`` of the old ``SECTION_WORLD`` constant, as it stood in
# ``gm.engine`` on main at 84ba6c0 before it moved into the canon. Kept until
# #921 moves the Tarmar canon out of this repository.
WORLD_SECTION_HASH_BEFORE_THE_MOVE = (
    "de171e51bd3263729c66a8c7ff4a90ed28d7eea43fe6c89e52e31a2a7a84c249"
)

DEFAULT_WORLD_PROMPT_FILE = DEFAULT_CANON_DIR / CANON_PROMPT_DIR_NAME / "world.md"

# Names from the Tarmar canon's digest that must not leak into the default one.
# Each is on a public page; the canon's tests/expectations.json adds those that
# are not (issue #921).
TARMAR_PROPER_NOUNS = (
    "Tarmar",
    "Navel",
    "God King",
    "Splinter",
    "Maja",
    "Solberg",
    "Union",
    "Kalingarne",
    "Emporium",
    "Imperium",
    "Dragon Isles",
    "Northurn",
    "Wayfarer",
    "Pillars",
    "Kanu",
    "Qasha",
    "Eldfaxi",
    "Hurhelm",
    "Kingfort",
    "Stephen's World",
    "Endarkenment",
)


@skipUnless(CANON_NAME == TARMAR_CANON_NAME, "pins the Tarmar canon's digest")
class TarmarWorldSectionUnchangedTest(SimpleTestCase):
    """The Tarmar canon's ``world.md`` is the old constant, byte for byte.

    With the prompt still the sections joined (``gm.tests``) and the section
    still the canon's file (below), this says the move changed nothing the
    model reads in the world section. It is not the real gate: the real one
    is ``GMPromptSnapshot`` after deploy, where the first turn's prompt hash
    shows whether production sent what was expected.
    """

    def test_the_world_section_is_the_old_constant(self) -> None:
        self.assertEqual(prompt_hash(SECTION_WORLD), WORLD_SECTION_HASH_BEFORE_THE_MOVE)


class WorldSectionFromCanonTest(SimpleTestCase):
    """Where the world section comes from, and what a canon without one gets."""

    def setUp(self) -> None:
        self.temporary_directory = Path(
            self.enterContext(tempfile.TemporaryDirectory())
        )

    def test_the_section_is_the_loaded_canons_file(self) -> None:
        self.assertEqual(WORLD_PROMPT_FILE, DM_RULES_DIR / "gm-prompt" / "world.md")
        self.assertTrue(WORLD_PROMPT_FILE.is_file())
        self.assertEqual(
            SECTION_WORLD, WORLD_PROMPT_FILE.read_text(encoding="utf-8").rstrip("\n")
        )

    def test_the_files_trailing_newlines_do_not_reach_the_prompt(self) -> None:
        world_file = self.temporary_directory / "world.md"
        world_file.write_text("## The World of Elsewhere\nText.\n\n", encoding="utf-8")
        self.assertEqual(
            load_world_section(world_file), "## The World of Elsewhere\nText."
        )

    def test_a_canon_without_the_file_gets_the_fallback(self) -> None:
        missing = self.temporary_directory / "world.md"
        with self.assertLogs("gm.engine", level="WARNING") as logged:
            self.assertEqual(
                load_world_section(missing, canon_name=DEFAULT_CANON_NAME),
                FALLBACK_WORLD_SECTION,
            )
        self.assertIn(str(missing), logged.output[0])
        self.assertTrue(FALLBACK_WORLD_SECTION.startswith("## The World\n"))
        self.assertNotIn("\n\n", FALLBACK_WORLD_SECTION)

    def test_the_tarmar_canon_without_the_file_is_an_error(self) -> None:
        """No fallback: it would quietly change the production prompt."""
        missing = self.temporary_directory / "world.md"
        with self.assertRaisesMessage(
            FileNotFoundError, f"The Tarmar canon has no world digest at {missing}"
        ):
            load_world_section(missing, canon_name=TARMAR_CANON_NAME)

    def test_a_directory_where_the_file_should_be_is_an_error(self) -> None:
        directory = self.temporary_directory / "world.md"
        directory.mkdir()
        with self.assertRaisesMessage(IsADirectoryError, "is not a file"):
            load_world_section(directory)

    def import_the_engine_on_a_canon_without_prompt_files(
        self, script: str, canon_json: str | None
    ) -> subprocess.CompletedProcess[str]:
        """Run ``script`` in a fresh process on a canon with no ``gm-prompt/``.

        The canon carries the default canon's character, skill and equipment
        rules files, which every canon must and the prompt sections are built
        from (issues #68, #70).

        Args:
            script: Python source that imports ``gm.engine``.
            canon_json: The canon's ``canon.json``, or None for none (which
                makes an outside canon the Tarmar canon).

        Returns:
            The finished process.
        """
        canon_directory = self.temporary_directory / "canon"
        canon_directory.mkdir()
        (canon_directory / "scenario-seeds.md").write_text(
            "# Scenario Seeds\n", encoding="utf-8"
        )
        if canon_json is not None:
            (canon_directory / "canon.json").write_text(canon_json, encoding="utf-8")
        for rules_file_name in (
            CANON_CHARACTER_RULES_FILE_NAME,
            CANON_SKILL_RULES_FILE_NAME,
            CANON_EQUIPMENT_CATALOG_FILE_NAME,
            CANON_EQUIPMENT_RULES_FILE_NAME,
        ):
            shutil.copy(
                DEFAULT_CANON_DIR / rules_file_name, canon_directory / rules_file_name
            )
        environment = {
            name: value
            for name, value in os.environ.items()
            if name != "PYTEST_CURRENT_TEST"
        }
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env={**environment, CANON_DIR_ENV: str(canon_directory.resolve())},
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

    def test_a_process_on_a_canon_without_the_file_starts_with_the_fallback(
        self,
    ) -> None:
        completed = self.import_the_engine_on_a_canon_without_prompt_files(
            "from gm import engine\n"
            "assert engine.SECTION_WORLD == engine.FALLBACK_WORLD_SECTION\n"
            "assert engine.FALLBACK_WORLD_SECTION in engine.system_prompt()\n",
            canon_json='{"name": "default"}',
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_a_process_on_a_tarmar_canon_without_the_file_does_not_start(
        self,
    ) -> None:
        completed = self.import_the_engine_on_a_canon_without_prompt_files(
            "from gm import engine\n", canon_json=None
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("FileNotFoundError: The Tarmar canon has no", completed.stderr)


class DefaultCanonWorldDigestTest(SimpleTestCase):
    """The default canon's digest, checked whichever canon is loaded."""

    def setUp(self) -> None:
        self.digest = load_world_section(DEFAULT_WORLD_PROMPT_FILE)

    def test_the_default_canon_has_a_digest(self) -> None:
        self.assertTrue(DEFAULT_WORLD_PROMPT_FILE.is_file())
        self.assertNotEqual(self.digest, FALLBACK_WORLD_SECTION)

    def test_it_has_the_subsections_every_digest_has_in_order(self) -> None:
        headings = (
            "## The World of the Corran Reach",
            "### The Lands",
            "### The Powers",
            "### Peoples",
            "### GM-Only Background (never state to players",
        )
        positions = [self.digest.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertTrue(self.digest.startswith(headings[0]))

    def test_it_names_nothing_from_the_tarmar_canon(self) -> None:
        for name in TARMAR_PROPER_NOUNS:
            with self.subTest(name=name):
                self.assertNotIn(name.lower(), self.digest.lower())

    @skip_unless_canon_expects("proper_nouns")
    def test_it_names_none_of_the_tarmar_canons_unpublished_names(self) -> None:
        for name in CANON_EXPECTATIONS["proper_nouns"]:
            with self.subTest(name=name):
                self.assertNotIn(name.lower(), self.digest.lower())


class PromptTextIsNotAPageTest(BaseTestCase):
    """``gm-prompt/`` is prompt text: not indexed, retrieved or served."""

    PAGE_PATH = "dm-rules/gm-prompt/world"

    def test_content_iteration_leaves_the_prompt_directory_out(self) -> None:
        content_root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        canon_directory = content_root / "dm-rules"
        (canon_directory / "gm-prompt").mkdir(parents=True)
        (canon_directory / "gm-prompt" / "world.md").write_text(
            "## The World\n", encoding="utf-8"
        )
        (canon_directory / "scenario-seeds.md").write_text(
            "# Scenario Seeds\n", encoding="utf-8"
        )
        sources = [
            path.as_posix() for path, _file in iter_content_markdown(content_root)
        ]
        self.assertEqual(sources, ["dm-rules/scenario-seeds.md"])

    def test_the_loaded_canons_prompt_is_in_no_corpus_or_index(self) -> None:
        sources = [source for source, _file in iter_corpus_files(CONTENT_DIR)]
        self.assertTrue(any(source.startswith("dm-rules/") for source in sources))
        self.assertFalse(
            [source for source in sources if source.startswith("dm-rules/gm-prompt/")]
        )
        self.assertNotIn(self.PAGE_PATH, search_index.build_index())

    def test_the_reference_view_does_not_serve_it(self) -> None:
        self.login_as(self.create_dm())
        response = self.client.get(f"/tarmar/reference/{self.PAGE_PATH}/")
        self.assertContains(response, "Content not found")
        self.assertNotContains(response, "GM-Only Background")
