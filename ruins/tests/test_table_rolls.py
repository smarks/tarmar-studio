"""The GM-tools table roller: drift guards, seeding and gating (issue #809).

Each roller page's table set is pinned per canon. The default canon's pin is
spelled out here, since that canon lives in this repository; the Tarmar
canon's is supplied by the canon itself in ``tests/expectations.json`` under
``gm_tools_table_rolls`` (issue #921), so this repository does not restate
its table names. Either way, a canon edit that drops, renames or resizes a
table fails here rather than silently changing what the roller rolls.
"""

import tempfile
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils.html import escape

from common.test_helpers import (
    CANON_EXPECTATIONS,
    BaseTestCase,
    skip_unless_canon_expects,
)
from reference.paths import DEFAULT_CANON_DIR, GM_TOOLS_DIR, PathOutsideRoot
from ruins.table_rolls import (
    TABLE_PAGE_SLUGS,
    TablePage,
    TablePageNotInCanon,
    UnknownTablePage,
    load_table_page,
    roll_table_page,
    table_page_path,
    table_page_slug_for_reference_path,
)
from ruins.tables import DiceTable, parse_tables

#: One pinned table: its title, its die, and any extra columns its roll reads.
PinnedTable = tuple[str, str, tuple[str, ...]]

DEFAULT_CANON_PINS: dict[str, tuple[PinnedTable, ...]] = {
    "npc-tables": (
        ("Wants", "d8", ("Held back by",)),
        ("Meets strangers with...", "d6", ()),
        ("They are hiding...", "d6", ()),
    ),
    "job-generator": (
        ("Employer", "d8", ()),
        ("The work", "d8", ()),
        ("Complication", "d6", ()),
    ),
    "urban-intrigue": (
        ("Situation", "d8", ()),
        ("The characters are drawn in because...", "d6", ()),
    ),
    "faction-generator": (
        ("Kind", "d8", ()),
        ("Goal", "d6", ()),
        ("Weak point", "d6", ()),
    ),
    "trade-economics": (
        ("Cargo", "d8", ()),
        ("This season...", "d6", ()),
    ),
    "hook-introduction": (("Why hire outsiders", "d6", ()),),
}

#: The expectations key the Tarmar canon supplies its pins under, shaped
#: ``{slug: [[title, die, [extra column, ...]], ...]}``.
CANON_PINS_KEY = "gm_tools_table_rolls"


def _pinned(tables: tuple[DiceTable, ...] | list[DiceTable]) -> list[PinnedTable]:
    return [(table.title, table.die, table.extra_columns) for table in tables]


def _canon_pins(raw: dict) -> dict[str, list[PinnedTable]]:
    return {
        slug: [(title, die, tuple(extra)) for title, die, extra in tables]
        for slug, tables in raw.items()
    }


class DefaultCanonDriftGuardTests(SimpleTestCase):
    """Read straight from the default canon, so this runs in both modes."""

    def test_every_page_publishes_exactly_its_pinned_tables_in_order(self):
        self.assertEqual(set(DEFAULT_CANON_PINS), set(TABLE_PAGE_SLUGS))
        for slug, pinned in DEFAULT_CANON_PINS.items():
            markdown = (DEFAULT_CANON_DIR / "gm-tools" / f"{slug}.md").read_text(
                encoding="utf-8"
            )
            with self.subTest(slug=slug):
                self.assertEqual(
                    _pinned(list(parse_tables(markdown).values())),
                    list(pinned),
                    f"default canon gm-tools/{slug}.md drifted from its pin",
                )


class LoadedCanonDriftGuardTests(SimpleTestCase):
    """The canon the app is running, which is the Tarmar canon in production."""

    def test_every_page_parses_into_complete_rollable_tables(self):
        for slug in TABLE_PAGE_SLUGS:
            page = load_table_page(slug)
            with self.subTest(slug=slug):
                self.assertTrue(page.tables, f"{slug} has no dice tables")
                for table in page.tables:
                    self.assertGreaterEqual(table.die_sides, 2)
                    self.assertEqual(len(table.rows), table.die_sides)
                    self.assertTrue(all(table.rows), table.title)
                    for extra in table.extra_rows:
                        self.assertEqual(len(extra), len(table.extra_columns))
                        self.assertTrue(all(extra), table.title)

    @skip_unless_canon_expects(CANON_PINS_KEY)
    def test_tarmar_pages_publish_exactly_the_tables_the_canon_pins(self):
        pins = _canon_pins(CANON_EXPECTATIONS[CANON_PINS_KEY])
        self.assertEqual(set(pins), set(TABLE_PAGE_SLUGS))
        for slug, pinned in pins.items():
            with self.subTest(slug=slug):
                self.assertEqual(
                    _pinned(load_table_page(slug).tables),
                    pinned,
                    f"gm-tools/{slug}.md drifted from the canon's pin",
                )


class TablePageServiceTests(SimpleTestCase):
    def test_pages_are_read_from_the_loaded_canon(self):
        self.assertEqual(
            table_page_path("job-generator"), GM_TOOLS_DIR / "job-generator.md"
        )

    def test_unknown_and_generator_backed_slugs_are_refused(self):
        for slug in ("dungeons-ruins", "low-experience-hooks", "../secrets", ""):
            with self.subTest(slug=slug), self.assertRaises(UnknownTablePage):
                table_page_path(slug)

    def test_a_listed_page_the_canon_lacks_is_refused(self):
        with (
            mock.patch("ruins.table_rolls.GM_TOOLS_DIR", Path("/nonexistent-canon")),
            self.assertRaises(TablePageNotInCanon),
        ):
            load_table_page("job-generator")

    def test_a_page_that_is_a_symlink_out_of_gm_tools_is_refused(self):
        temporary_directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        gm_tools = temporary_directory / "gm-tools"
        gm_tools.mkdir()
        outside_page = temporary_directory / "outside.md"
        outside_page.write_text("# Outside\n", encoding="utf-8")
        (gm_tools / "job-generator.md").symlink_to(outside_page)
        with (
            mock.patch("ruins.table_rolls.GM_TOOLS_DIR", gm_tools),
            self.assertRaises(PathOutsideRoot),
        ):
            load_table_page("job-generator")

    def test_reference_paths_map_to_their_roller(self):
        self.assertEqual(
            table_page_slug_for_reference_path("dm-rules/gm-tools/npc-tables"),
            "npc-tables",
        )
        for path in (
            "dm-rules/gm-tools/dungeons-ruins",
            "dm-rules/gm-tools",
            "public-rules/npc-tables",
        ):
            with self.subTest(path=path):
                self.assertIsNone(table_page_slug_for_reference_path(path))

    def test_page_title_is_the_pages_heading(self):
        page = load_table_page("job-generator")
        heading = table_page_path("job-generator").read_text().splitlines()[0]
        self.assertEqual(f"# {page.title}", heading)
        self.assertEqual(page.reference_path, "dm-rules/gm-tools/job-generator")

    def test_one_roll_per_table_in_page_order_landing_on_the_rolled_row(self):
        for slug in TABLE_PAGE_SLUGS:
            page = load_table_page(slug)
            rolled = roll_table_page(page, seed=11)
            with self.subTest(slug=slug):
                self.assertEqual(
                    [entry.table for entry in rolled.rolls], list(page.tables)
                )
                for entry in rolled.rolls:
                    self.assertEqual(entry.roll.die, entry.table.die)
                    self.assertTrue(1 <= entry.roll.face <= entry.table.die_sides)
                    self.assertEqual(
                        entry.roll.result, entry.table.row_for_face(entry.roll.face)
                    )
                    self.assertEqual(
                        entry.cells[0], (entry.table.title, entry.roll.result)
                    )

    def test_a_seed_reproduces_every_roll(self):
        page = load_table_page("trade-economics")

        def faces(seed: int) -> list[int]:
            return [entry.roll.face for entry in roll_table_page(page, seed).rolls]

        self.assertEqual(faces(42), faces(42))
        self.assertEqual(roll_table_page(page, 42).seed, 42)
        self.assertGreater(len({tuple(faces(seed)) for seed in range(1, 30)}), 1)

    def test_a_page_with_no_tables_rolls_nothing(self):
        empty = TablePage(slug="npc-tables", title="NPC Tables", tables=())
        self.assertEqual(roll_table_page(empty, seed=3).rolls, ())


class RollerViewGatingTests(BaseTestCase):
    URL = "/tarmar/gm/tools/job-generator/roll/"

    def test_url_is_the_one_the_page_links(self):
        self.assertEqual(reverse("table_rolls:roll", args=["job-generator"]), self.URL)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.URL, {"seed": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_player_is_refused(self):
        self.login_as(self.create_player())
        response = self.client.get(self.URL, {"seed": "1"})
        self.assertEqual(response.status_code, 403)

    def test_game_master_rolls(self):
        self.login_dm()
        response = self.client.get(self.URL, {"seed": "1"})
        self.assertEqual(response.status_code, 200)

    def test_unknown_and_generator_backed_pages_are_not_found(self):
        self.login_dm()
        for slug in ("dungeons-ruins", "low-experience-hooks", "no-such-page"):
            with self.subTest(slug=slug):
                response = self.client.get(f"/tarmar/gm/tools/{slug}/roll/")
                self.assertEqual(response.status_code, 404)


class RollerViewMissingPageTests(BaseTestCase):
    def test_a_listed_page_missing_from_the_canon_is_a_logged_404(self):
        self.login_dm()
        with (
            mock.patch("ruins.table_rolls.GM_TOOLS_DIR", Path("/nonexistent-canon")),
            self.assertLogs("ruins.views", level="WARNING") as captured,
        ):
            response = self.client.get(
                reverse("table_rolls:roll", args=["job-generator"]), {"seed": "1"}
            )
        self.assertEqual(response.status_code, 404)
        self.assertIn("job-generator", captured.output[0])


class RollerViewOutsidePageTests(BaseTestCase):
    def test_a_page_symlinked_out_of_gm_tools_is_a_logged_404(self):
        temporary_directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        gm_tools = temporary_directory / "gm-tools"
        gm_tools.mkdir()
        outside_page = temporary_directory / "outside.md"
        outside_page.write_text("# Outside\n", encoding="utf-8")
        (gm_tools / "job-generator.md").symlink_to(outside_page)
        self.login_dm()
        with (
            mock.patch("ruins.table_rolls.GM_TOOLS_DIR", gm_tools),
            self.assertLogs("ruins.views", level="WARNING") as captured,
        ):
            response = self.client.get(
                reverse("table_rolls:roll", args=["job-generator"]), {"seed": "1"}
            )
        self.assertEqual(response.status_code, 404)
        self.assertIn("outside", captured.output[0])


class RollerViewFlowTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.login_dm()

    @staticmethod
    def url(slug: str) -> str:
        return reverse("table_rolls:roll", args=[slug])

    def test_rolling_without_a_seed_redirects_to_a_seeded_url(self):
        response = self.client.get(self.url("npc-tables"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response.url).path, self.url("npc-tables"))
        seed = int(parse_qs(urlparse(response.url).query)["seed"][0])
        follow_up = self.client.get(response.url)
        self.assertContains(follow_up, f"seed {seed}")

    def test_seeded_url_shows_every_table_its_row_and_its_dice(self):
        for slug in TABLE_PAGE_SLUGS:
            expected = roll_table_page(load_table_page(slug), seed=42)
            response = self.client.get(self.url(slug), {"seed": "42"})
            with self.subTest(slug=slug):
                self.assertContains(response, 'id="table-roll-result"')
                self.assertContains(response, "seed 42")
                for entry in expected.rolls:
                    for label, text in entry.cells:
                        self.assertContains(
                            response, f"<strong>{escape(label)}</strong>"
                        )
                        self.assertContains(response, escape(text))
                    self.assertContains(
                        response, f"{entry.roll.die} &rarr; {entry.roll.face}"
                    )

    def test_seeded_url_is_deterministic_across_requests(self):
        def result_section(response) -> str:
            content = response.content.decode()
            return content[content.index('id="table-roll-result"') :]

        first = self.client.get(self.url("urban-intrigue"), {"seed": "7"})
        second = self.client.get(self.url("urban-intrigue"), {"seed": "7"})
        self.assertEqual(result_section(first), result_section(second))

    def test_result_links_back_to_the_page_and_keeps_the_form(self):
        response = self.client.get(self.url("faction-generator"), {"seed": "5"})
        self.assertContains(
            response,
            reverse("reference:view", args=["dm-rules/gm-tools/faction-generator"]),
        )
        self.assertContains(response, 'id="table-roll-form"')
        # Empty, so "Roll again" rolls a fresh seed; the shown one is above.
        self.assertContains(response, 'name="seed" inputmode="numeric" value=""')
        self.assertContains(response, "Roll again")

    def test_non_numeric_seed_renders_an_error_and_keeps_the_value(self):
        response = self.client.get(self.url("job-generator"), {"seed": "lucky"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "notice notice-error")
        self.assertContains(response, "Seed must be a whole number")
        self.assertContains(response, 'value="lucky"')
        self.assertNotContains(response, 'id="table-roll-result"')

    def test_a_page_with_no_dice_tables_says_so(self):
        empty = TablePage(slug="trade-economics", title="Trade & Economics", tables=())
        with mock.patch("ruins.views.load_table_page", return_value=empty):
            response = self.client.get(self.url("trade-economics"), {"seed": "2"})
        self.assertContains(response, 'id="table-roll-none"')
        self.assertContains(response, "has no dice tables")


class ReferencePageRollControlTests(BaseTestCase):
    def test_each_roller_page_carries_a_roll_link_for_the_game_master(self):
        self.login_dm()
        for slug in TABLE_PAGE_SLUGS:
            response = self.client.get(
                reverse("reference:view", args=[f"dm-rules/gm-tools/{slug}"])
            )
            with self.subTest(slug=slug):
                self.assertContains(response, 'id="table-roller-link"')
                self.assertContains(
                    response, f'href="{reverse("table_rolls:roll", args=[slug])}"'
                )

    def test_pages_without_a_roller_carry_no_roll_link(self):
        self.login_dm()
        for path in ("dm-rules/gm-tools/dungeons-ruins", "dm-rules/gm-tools"):
            with self.subTest(path=path):
                response = self.client.get(reverse("reference:view", args=[path]))
                self.assertNotContains(response, 'id="table-roller-link"')

    def test_player_sees_neither_the_page_nor_the_link(self):
        self.login_as(self.create_player())
        response = self.client.get(
            reverse("reference:view", args=["dm-rules/gm-tools/job-generator"])
        )
        self.assertContains(response, "Access Denied")
        self.assertNotContains(response, 'id="table-roller-link"')
