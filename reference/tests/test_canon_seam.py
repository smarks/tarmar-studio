"""The canon seam: every dm-rules reader resolves through one root (issue #917).

``TARMAR_CANON_DIR`` points the DM-only canon at a directory outside the
content tree. These tests copy ``dm-rules`` somewhere else, mark the copy so a
read from it can be told from a read of the in-repo tree, and check each
reader: the reference page view, search, the GM map registry, the ruins
tables and hooks, and the retrieval corpus.

Two levels, because the readers capture the root at two different times.
The view, search and retrieval look :data:`reference.paths.DM_RULES_DIR` up
when they run, so the in-process tests patch that one name to what
:func:`reference.paths.resolve_canon_dir` makes of the variable. ``gm.maps``
and ``ruins`` bind their paths when imported, as production does, so a
subprocess started with the variable set imports them cold and reports where
they read from. Reloading those modules in the test process instead would
leave every module that imported their names by value holding the old ones.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from common.test_helpers import BaseTestCase
from gm import retrieval
from gm.maps import GAZETTEER_DIR, GAZETTEER_HUB, MAP_AREAS, MapScope
from reference import nav, paths, search_index
from reference.apps import watch_canon_files
from reference.paths import (
    CANON_DIR_ENV,
    DEFAULT_CANON_DIR,
    DEFAULT_CANON_NAME,
    REPO_ROOT,
    TARMAR_CANON_NAME,
    PathOutsideRoot,
    file_inside,
    page_markdown_file,
    resolve_canon,
    resolve_canon_dir,
)
from ruins.hooks import load_hooks
from ruins.tables import load_tables

# Appended to one page of the copy: found only if that copy was read.
SENTINEL = "Quorvenkal the outside canon keeps this line"
SENTINEL_PAGE = Path("scenario-seeds.md")
# Deleted from the copy: still in the repo, so finding it means the in-repo
# tree was read as well as (or instead of) the outside one.
REMOVED_PAGE = Path("nobility-titles.md")


def make_outside_canon(parent: Path) -> Path:
    """Copy the current canon under ``parent`` and mark the copy.

    Args:
        parent: A temporary directory to hold the copy.

    Returns:
        The copied canon directory.
    """
    canon_copy = parent / "outside-canon"
    shutil.copytree(paths.DM_RULES_DIR, canon_copy)
    sentinel_page = canon_copy / SENTINEL_PAGE
    sentinel_page.write_text(
        f"{sentinel_page.read_text(encoding='utf-8')}\n\n{SENTINEL}\n",
        encoding="utf-8",
    )
    (canon_copy / REMOVED_PAGE).unlink()
    return canon_copy


class ResolveCanonDirTest(SimpleTestCase):
    """The three branches of resolve_canon, read from an explicit environment."""

    def setUp(self) -> None:
        self.temporary_directory = Path(
            self.enterContext(tempfile.TemporaryDirectory())
        )

    def test_variable_names_an_existing_directory(self) -> None:
        outside = self.temporary_directory / "canon"
        outside.mkdir()
        self.assertEqual(
            resolve_canon({CANON_DIR_ENV: str(outside)}),
            (outside.resolve(), TARMAR_CANON_NAME),
        )
        self.assertEqual(
            resolve_canon_dir({CANON_DIR_ENV: str(outside)}), outside.resolve()
        )

    def test_variable_naming_a_missing_directory_is_improperly_configured(self) -> None:
        missing = self.temporary_directory / "absent"
        with self.assertRaisesMessage(ImproperlyConfigured, CANON_DIR_ENV):
            resolve_canon_dir({CANON_DIR_ENV: str(missing)})

    def test_relative_variable_is_improperly_configured(self) -> None:
        relative = Path(os.path.relpath(self.temporary_directory))
        self.assertTrue(relative.resolve().is_dir())
        with self.assertRaisesMessage(ImproperlyConfigured, "absolute"):
            resolve_canon_dir({CANON_DIR_ENV: str(relative)})

    def test_variable_naming_a_file_is_improperly_configured(self) -> None:
        a_file = self.temporary_directory / "canon.md"
        a_file.write_text("# Not a directory\n", encoding="utf-8")
        with self.assertRaises(ImproperlyConfigured):
            resolve_canon_dir({CANON_DIR_ENV: str(a_file)})

    def test_unset_or_blank_variable_falls_back_to_the_in_tree_canon(self) -> None:
        content_dir = self.temporary_directory / "content"
        (content_dir / "dm-rules").mkdir(parents=True)
        for environ in ({}, {CANON_DIR_ENV: "  "}):
            with self.subTest(environ=environ):
                self.assertEqual(
                    resolve_canon(environ, content_dir),
                    (content_dir / "dm-rules", TARMAR_CANON_NAME),
                )

    def test_no_canon_anywhere_falls_back_to_the_default_canon(self) -> None:
        content_dir = self.temporary_directory / "content"
        content_dir.mkdir()
        self.assertEqual(
            resolve_canon({}, content_dir), (DEFAULT_CANON_DIR, DEFAULT_CANON_NAME)
        )

    def test_this_checkout_resolves_to_its_own_canon_without_the_variable(
        self,
    ) -> None:
        # The in-repo dm-rules when this checkout has it, the default canon
        # when it does not (issue #918: the suite runs both ways).
        in_tree_canon = paths.CONTENT_DIR / "dm-rules"
        expected = (
            (in_tree_canon, TARMAR_CANON_NAME)
            if in_tree_canon.is_dir()
            else (DEFAULT_CANON_DIR, DEFAULT_CANON_NAME)
        )
        self.assertEqual(resolve_canon({}), expected)


class PageMarkdownFileTest(SimpleTestCase):
    """Page paths map by prefix, and a ".." segment never reaches the split."""

    def test_dm_rules_prefix_maps_to_the_canon(self) -> None:
        self.assertEqual(
            page_markdown_file("dm-rules/gm-tools"),
            (paths.DM_RULES_DIR / "gm-tools.md", paths.DM_RULES_DIR),
        )
        self.assertEqual(
            page_markdown_file("dm-rules"),
            (paths.CONTENT_DIR / "dm-rules.md", paths.CONTENT_DIR),
        )

    def test_a_dot_dot_segment_is_refused_before_the_prefix_is_read(self) -> None:
        for page_path in (
            "public-rules/../dm-rules/scenario-seeds",
            "dm-rules/../public-rules/combat",
            "..",
        ):
            with self.subTest(page_path=page_path), self.assertRaises(ValueError):
                page_markdown_file(page_path)


class FileInsideTest(SimpleTestCase):
    """A requested file resolves inside its root, or is refused."""

    def setUp(self) -> None:
        super().setUp()
        temporary_directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = temporary_directory / "content"
        self.root.mkdir()
        (self.root / "page.md").write_text("# Page\n", encoding="utf-8")
        self.outside_file = temporary_directory / "outside.md"
        self.outside_file.write_text("# Outside\n", encoding="utf-8")
        # A sibling whose name starts with the root's: a bare prefix test on
        # the strings would take it for a file inside the root.
        (temporary_directory / "content-old").mkdir()

    def test_a_file_inside_resolves_to_its_real_path(self) -> None:
        for requested in ("page.md", self.root / "page.md", "missing.md"):
            with self.subTest(requested=str(requested)):
                self.assertEqual(
                    file_inside(self.root, requested),
                    self.root.resolve() / Path(requested).name,
                )

    def test_files_reaching_outside_the_root_are_refused(self) -> None:
        (self.root / "escape.md").symlink_to(self.outside_file)
        for requested in (
            "../outside.md",
            self.outside_file,
            "escape.md",
            "../content-old/page.md",
            "",
            ".",
        ):
            with (
                self.subTest(requested=str(requested)),
                self.assertRaises(PathOutsideRoot),
            ):
                file_inside(self.root, requested)


class OutsideCanonReadersTest(BaseTestCase):
    """The view, search and retrieval read dm-rules from the outside canon."""

    def setUp(self) -> None:
        super().setUp()
        temporary_directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.canon_copy = make_outside_canon(temporary_directory)
        self.resolved_canon = resolve_canon_dir({CANON_DIR_ENV: str(self.canon_copy)})
        # Cleanups run last-registered first: the patch comes off before the
        # index is rebuilt from the real tree for the tests that follow.
        self.addCleanup(search_index.build_index)
        self.enterContext(patch.object(paths, "DM_RULES_DIR", self.resolved_canon))
        search_index.build_index()
        self.login_as(self.create_dm())

    def test_dm_rules_page_renders_from_the_outside_canon(self) -> None:
        response = self.client.get("/tarmar/reference/dm-rules/scenario-seeds/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, SENTINEL)
        self.assertNotContains(response, "Content not found")

    def test_a_page_missing_from_the_outside_canon_is_not_served_from_the_repo(
        self,
    ) -> None:
        response = self.client.get("/tarmar/reference/dm-rules/nobility-titles/")
        self.assertContains(response, "Content not found")

    def test_the_landing_page_and_public_pages_still_come_from_content(self) -> None:
        for url, text in (
            ("/tarmar/reference/dm-rules/", "DM Rules"),
            ("/tarmar/reference/public-rules/combat/", "five seconds"),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, text)
                self.assertNotContains(response, "Content not found")

    def test_traversal_out_of_the_outside_canon_is_rejected(self) -> None:
        with self.assertLogs("reference.views", level="WARNING"):
            response = self.client.get(
                "/tarmar/reference/dm-rules/..%2F..%2F..%2Fsecret/"
            )
        self.assertContains(response, "Content not found")

    def test_content_path_climbing_into_the_in_repo_canon_is_rejected(self) -> None:
        with self.assertLogs("reference.views", level="WARNING"):
            response = self.client.get(
                "/tarmar/reference/public-rules%2F..%2Fdm-rules%2Fnobility-titles/"
            )
        self.assertContains(response, "Content not found")

    def test_a_symlink_out_of_the_outside_canon_is_rejected(self) -> None:
        outside_file = self.canon_copy.parent / "outside.md"
        outside_file.write_text(f"# Outside\n\n{SENTINEL}\n", encoding="utf-8")
        (self.canon_copy / "escape.md").symlink_to(outside_file)
        with self.assertLogs("reference.views", level="WARNING"):
            response = self.client.get("/tarmar/reference/dm-rules/escape/")
        self.assertContains(response, "Content not found")
        self.assertNotContains(response, SENTINEL)

    def test_search_finds_the_outside_canon_page(self) -> None:
        response = self.client.get("/tarmar/reference/search/?q=Quorvenkal")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "dm-rules/scenario-seeds")
        index = search_index.get_index()
        self.assertIn("dm-rules/scenario-seeds", index)
        self.assertNotIn("dm-rules/nobility-titles", index)
        self.assertIn("public-rules/combat", index)

    def test_retrieval_chunks_carry_dm_rules_sources_from_the_outside_canon(
        self,
    ) -> None:
        corpus_files = dict(retrieval.iter_corpus_files(retrieval.DEFAULT_CORPUS_ROOT))
        canon_files = {
            source: path
            for source, path in corpus_files.items()
            if source.startswith("dm-rules/")
        }
        self.assertTrue(canon_files)
        self.assertTrue(
            all(
                path.is_relative_to(self.resolved_canon)
                for path in canon_files.values()
            )
        )
        self.assertNotIn("dm-rules/nobility-titles.md", corpus_files)
        self.assertIn("public-rules/magic.md", corpus_files)

        chunks = list(retrieval.iter_corpus_chunks(retrieval.DEFAULT_CORPUS_ROOT))
        sentinel_sources = {chunk.source for chunk in chunks if SENTINEL in chunk.text}
        self.assertEqual(sentinel_sources, {"dm-rules/scenario-seeds.md"})
        sources = [chunk.source for chunk in chunks]
        self.assertEqual(sources, sorted(sources, key=lambda source: source.split("/")))


# Run in a fresh interpreter with TARMAR_CANON_DIR set: every reader resolves
# its root at import there, as a production process started with it would.
READERS_REPORT_SCRIPT = """
import json

import django

django.setup()

from gm import maps, retrieval
from reference import paths, search_index
from ruins import hooks, tables

chunks = list(retrieval.iter_corpus_chunks(retrieval.DEFAULT_CORPUS_ROOT))
index = search_index.build_index()
print(json.dumps({
    "dm_rules_dir": str(paths.DM_RULES_DIR),
    "gm_tools_dir": str(paths.GM_TOOLS_DIR),
    "canon_name": paths.CANON_NAME,
    "page_file": str(paths.page_markdown_file("dm-rules/scenario-seeds")[0]),
    "gazetteer_dir": str(maps.GAZETTEER_DIR),
    "map_area_count": len(maps.MAP_AREAS),
    "region_place_count": len(
        next(area for area in maps.MAP_AREAS if area.scope is maps.MapScope.REGION)
        .place_names
    ),
    "tables_path": str(tables.TABLES_MARKDOWN_PATH),
    "table_count": len(tables.load_tables()),
    "hooks_path": str(hooks.HOOKS_MARKDOWN_PATH),
    "hook_count": len(hooks.load_hooks()),
    "sentinel_sources": sorted(
        {chunk.source for chunk in chunks if %(sentinel)r in chunk.text}
    ),
    "chunk_sources": sorted({chunk.source for chunk in chunks}),
    "indexed_paths": sorted(index),
}))
"""


class OutsideCanonAtImportTest(SimpleTestCase):
    """A process started with TARMAR_CANON_DIR reads dm-rules only from there."""

    def setUp(self) -> None:
        self.temporary_directory = Path(
            self.enterContext(tempfile.TemporaryDirectory())
        )
        empty_dotenv = self.temporary_directory / "empty.env"
        empty_dotenv.write_text("", encoding="utf-8")
        self.environment = {
            name: value
            for name, value in os.environ.items()
            if name != "PYTEST_CURRENT_TEST"
        }
        self.environment.update(
            DJANGO_SETTINGS_MODULE="tarmar_studio.settings",
            DEBUG="true",
            TARMAR_DOTENV_PATH=str(empty_dotenv),
        )

    def run_python(self, script: str, canon_dir: Path) -> subprocess.CompletedProcess:
        """Run ``script`` in a fresh interpreter with the canon at ``canon_dir``."""
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env={**self.environment, CANON_DIR_ENV: str(canon_dir)},
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

    def test_every_reader_resolves_through_the_outside_canon(self) -> None:
        canon_copy = make_outside_canon(self.temporary_directory).resolve()
        completed = self.run_python(
            READERS_REPORT_SCRIPT % {"sentinel": SENTINEL}, canon_copy
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout.strip().splitlines()[-1])

        self.assertEqual(report["dm_rules_dir"], str(canon_copy))
        self.assertEqual(report["gm_tools_dir"], str(canon_copy / "gm-tools"))
        # The copy keeps the canon.json of whichever canon it was copied from.
        self.assertEqual(report["canon_name"], paths.CANON_NAME)
        self.assertEqual(report["page_file"], str(canon_copy / "scenario-seeds.md"))
        assert GAZETTEER_DIR is not None and GAZETTEER_HUB is not None
        gazetteer = GAZETTEER_DIR.relative_to(paths.DM_RULES_DIR)
        self.assertEqual(report["gazetteer_dir"], str(canon_copy / gazetteer))
        self.assertEqual(report["map_area_count"], len(MAP_AREAS))
        region = next(area for area in MAP_AREAS if area.scope is MapScope.REGION)
        self.assertEqual(report["region_place_count"], len(region.place_names))
        self.assertEqual(
            report["tables_path"], str(canon_copy / "gm-tools" / "dungeons-ruins.md")
        )
        self.assertEqual(report["table_count"], len(load_tables()))
        self.assertEqual(
            report["hooks_path"],
            str(canon_copy / "gm-tools" / "low-experience-hooks.md"),
        )
        self.assertEqual(report["hook_count"], len(load_hooks()))
        self.assertEqual(report["sentinel_sources"], ["dm-rules/scenario-seeds.md"])
        self.assertNotIn("dm-rules/nobility-titles.md", report["chunk_sources"])
        hub_source = (
            f"dm-rules/{GAZETTEER_HUB.relative_to(paths.DM_RULES_DIR).as_posix()}"
        )
        self.assertIn(hub_source, report["chunk_sources"])
        self.assertIn("dm-rules/scenario-seeds", report["indexed_paths"])
        self.assertNotIn("dm-rules/nobility-titles", report["indexed_paths"])

    def test_a_missing_canon_directory_stops_the_import(self) -> None:
        completed = self.run_python(
            "import reference.paths", self.temporary_directory / "absent"
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("ImproperlyConfigured", completed.stderr)
        self.assertIn(CANON_DIR_ENV, completed.stderr)


class CanonLineTest(BaseTestCase):
    """The GM chat page and the reference pages name the loaded canon (#918)."""

    def test_each_canon_has_a_label(self) -> None:
        self.assertEqual(
            paths.CANON_LABELS,
            {TARMAR_CANON_NAME: "Tarmar canon", DEFAULT_CANON_NAME: "default canon"},
        )
        self.assertEqual(paths.CANON_LABEL, paths.CANON_LABELS[paths.CANON_NAME])

    def test_the_reference_index_names_the_canon(self) -> None:
        response = self.client.get("/tarmar/reference/")
        self.assertContains(
            response, f'<p class="canon-line">World: {paths.CANON_LABEL}</p>', html=True
        )

    def test_the_gm_chat_page_names_the_canon(self) -> None:
        self.login_as(self.create_user())
        response = self.client.get("/tarmar/gm/")
        self.assertContains(
            response, f'<p class="canon-line">World: {paths.CANON_LABEL}</p>', html=True
        )


class CanonNameTest(SimpleTestCase):
    """A canon names itself; the default canon is "default" however reached."""

    def setUp(self) -> None:
        self.temporary_directory = Path(
            self.enterContext(tempfile.TemporaryDirectory())
        )

    def test_the_variable_pointing_at_the_default_canon_reports_default(self) -> None:
        self.assertEqual(
            resolve_canon({CANON_DIR_ENV: str(DEFAULT_CANON_DIR)}),
            (DEFAULT_CANON_DIR.resolve(), DEFAULT_CANON_NAME),
        )

    def test_a_canon_json_names_the_canon(self) -> None:
        outside = self.temporary_directory / "canon"
        outside.mkdir()
        (outside / "canon.json").write_text('{"name": "default"}', encoding="utf-8")
        self.assertEqual(
            resolve_canon({CANON_DIR_ENV: str(outside)}),
            (outside.resolve(), DEFAULT_CANON_NAME),
        )

    def test_without_canon_json_an_outside_canon_is_tarmar(self) -> None:
        outside = self.temporary_directory / "canon"
        outside.mkdir()
        self.assertEqual(
            paths.canon_name_of(outside, TARMAR_CANON_NAME), TARMAR_CANON_NAME
        )

    def test_a_malformed_canon_json_is_improperly_configured(self) -> None:
        outside = self.temporary_directory / "canon"
        outside.mkdir()
        for body in ('{"name": "atlantis"}', "[]", "{not json"):
            with self.subTest(body=body):
                (outside / "canon.json").write_text(body, encoding="utf-8")
                with self.assertRaisesMessage(ImproperlyConfigured, "canon.json"):
                    paths.canon_name_of(outside, TARMAR_CANON_NAME)


class CanonNavFileTest(SimpleTestCase):
    """reference.nav reads the DM Rules pages from the canon's nav.json."""

    def setUp(self) -> None:
        self.nav_file = (
            Path(self.enterContext(tempfile.TemporaryDirectory())) / "nav.json"
        )

    def test_a_missing_nav_file_publishes_nothing(self) -> None:
        self.assertEqual(nav._load_canon_nav(self.nav_file), [])

    def test_a_valid_nav_file_is_returned(self) -> None:
        items = [
            {
                "title": "Vale",
                "slug": "vale",
                "children": [{"title": "Ford", "slug": "ford", "dm_only": True}],
            }
        ]
        self.nav_file.write_text(json.dumps(items), encoding="utf-8")
        self.assertEqual(nav._load_canon_nav(self.nav_file), items)

    def test_malformed_nav_files_raise_naming_the_file_and_item(self) -> None:
        for body, message in (
            ('{"title": "Vale"}', "must be a JSON list"),
            ("[{not json", "is not valid JSON"),
            ('[{"title": "Vale"}]', "[0].slug must be a string"),
            ('[{"title": "Vale", "slug": "vale", "children": {}}]', "[0].children"),
            (
                '[{"title": "V", "slug": "v", "children": [{"slug": "f"}]}]',
                "[0].children[0].title must be a string",
            ),
            ('[{"title": "V", "slug": "v", "dm_only": "yes"}]', "[0].dm_only"),
        ):
            with self.subTest(body=body):
                self.nav_file.write_text(body, encoding="utf-8")
                with self.assertRaisesMessage(ValueError, message):
                    nav._load_canon_nav(self.nav_file)
                with self.assertRaisesMessage(ValueError, str(self.nav_file)):
                    nav._load_canon_nav(self.nav_file)

    def test_both_canons_nav_files_are_valid(self) -> None:
        for canon_directory in (paths.DM_RULES_DIR, DEFAULT_CANON_DIR):
            with self.subTest(canon=str(canon_directory)):
                self.assertTrue(nav._load_canon_nav(canon_directory / "nav.json"))


class CanonFileWatchTest(SimpleTestCase):
    """runserver restarts when the canon's import-time files change."""

    def test_the_reloader_watches_the_canon_json_files(self) -> None:
        reloader = Mock()
        watch_canon_files(reloader)
        reloader.watch_dir.assert_any_call(paths.DM_RULES_DIR, "*.json")

    def test_the_reloader_watches_the_world_digest(self) -> None:
        # gm.engine reads gm-prompt/world.md at import (issue #919).
        reloader = Mock()
        watch_canon_files(reloader)
        reloader.watch_dir.assert_any_call(paths.DM_RULES_DIR / "gm-prompt", "*.md")
        self.assertEqual(
            paths.WORLD_PROMPT_FILE.parent, paths.DM_RULES_DIR / "gm-prompt"
        )
        self.assertEqual(reloader.watch_dir.call_count, 2)

    def test_the_search_fingerprint_covers_the_canon_json_files(self) -> None:
        canon_directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name in ("canon.json", "nav.json", "maps.json"):
            (canon_directory / name).write_text("{}", encoding="utf-8")
        with patch.object(search_index, "DM_RULES_DIR", canon_directory):
            fingerprinted = {path for path, _mtime in search_index._fingerprint()}
        for name in ("canon.json", "nav.json", "maps.json"):
            with self.subTest(name=name):
                self.assertIn(str(canon_directory / name), fingerprinted)


class CanonRepositoryLayoutTest(SimpleTestCase):
    """The canon lives in its own repository, beside documents that are not pages.

    Issue #921 moved the Tarmar canon to the private ``smarks/tarmar-canon``,
    whose root is the canon directory and also holds ``author/``, ``guides/``
    and a ``README.md``.
    """

    def test_documents_beside_the_pages_are_not_pages(self) -> None:
        content_root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        canon_directory = content_root / "dm-rules"
        for relative in (
            "the-world.md",
            "gm-tools/hooks.md",
            "README.md",
            "author/tarmar_author_bible.md",
            "guides/tarmar_gms_guide.md",
            "tests/notes.md",
            "gm-prompt/world.md",
        ):
            page = canon_directory / relative
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text("# Page\n", encoding="utf-8")
        (content_root / "public-rules.md").write_text("# Public\n", encoding="utf-8")

        yielded = [
            relative_path.as_posix()
            for relative_path, _file in paths.iter_content_markdown(content_root)
        ]

        self.assertEqual(
            yielded,
            ["dm-rules/gm-tools/hooks.md", "dm-rules/the-world.md", "public-rules.md"],
        )

    def test_this_repository_tracks_none_of_the_private_canon(self) -> None:
        tracked = subprocess.run(
            [
                "git",
                "ls-files",
                "--",
                "reference/content/dm-rules",
                "docs/tarmar_author_bible.md",
                "docs/tarmar_canon_for_training.md",
                "docs/cosmology-notes.md",
                "docs/houses-of-the-union.md",
                "docs/restructure-task.md",
                "build/tarmar_gms_guide.md",
                "build/tarmar_cosmology_notes_private.md",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertEqual(tracked, "")

    def test_a_copy_dropped_back_into_the_tree_is_ignored_by_git(self) -> None:
        ignored = subprocess.run(
            ["git", "check-ignore", "--no-index", "reference/content/dm-rules/x.md"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ignored.returncode, 0, ignored.stderr)


class ProductionCanonWarningTest(SimpleTestCase):
    """A production process on the default canon says so in its log (#921)."""

    def test_an_unset_variable_is_warned_about_as_unset(self) -> None:
        with self.assertLogs("reference.paths", level="WARNING") as captured:
            self.assertTrue(
                paths.warn_if_production_lacks_its_canon(DEFAULT_CANON_NAME, {})
            )
        self.assertIn(f"{CANON_DIR_ENV} is not set", captured.output[0])
        self.assertIn(str(DEFAULT_CANON_DIR), captured.output[0])

    def test_a_variable_naming_the_default_canon_is_warned_about_as_such(
        self,
    ) -> None:
        environ = {CANON_DIR_ENV: str(DEFAULT_CANON_DIR)}
        with self.assertLogs("reference.paths", level="WARNING") as captured:
            self.assertTrue(
                paths.warn_if_production_lacks_its_canon(DEFAULT_CANON_NAME, environ)
            )
        self.assertIn(
            f"{CANON_DIR_ENV} names the default canon ({DEFAULT_CANON_DIR})",
            captured.output[0],
        )
        self.assertNotIn("is not set", captured.output[0])

    def test_the_tarmar_canon_is_not(self) -> None:
        with self.assertNoLogs("reference.paths", level="WARNING"):
            self.assertFalse(
                paths.warn_if_production_lacks_its_canon(TARMAR_CANON_NAME)
            )
