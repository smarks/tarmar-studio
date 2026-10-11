"""Roll every dice table on a GM-tools page (issue #809).

The ruin and hook generators each roll one page into a structured result.
The other GM-tools pages (jobs, NPCs, trade, factions, urban intrigue and
hook introductions) are lists of independent tables, so one generic roller
serves them all: parse the page's dice tables with the shared
:func:`ruins.tables.parse_tables`, roll each once in page order through one
seeded :class:`common.rolling.Roller`, and keep every roll for display. The
canon page stays the single source of truth; an edit there changes the
roller with no code change, and the per-page drift guard in
``ruins/tests/test_table_rolls.py`` catches a table that disappears.
"""

from dataclasses import dataclass
from pathlib import Path

from common.rolling import Roller
from reference.paths import GM_TOOLS_DIR, file_inside
from ruins.generation import TableRoll, roll_table
from ruins.tables import DiceTable, parse_tables

#: The GM-tools pages the roller serves, in the order the GM Tools index
#: lists them. Dungeons & Ruins and the new-character hooks have their own
#: generators (``ruins:generate``, ``hooks:generate``) and are not here.
TABLE_PAGE_SLUGS = (
    "npc-tables",
    "job-generator",
    "urban-intrigue",
    "faction-generator",
    "trade-economics",
    "hook-introduction",
)

#: The reference URL path prefix of a GM-tools page.
GM_TOOLS_REFERENCE_PREFIX = "dm-rules/gm-tools/"


class UnknownTablePage(LookupError):
    """A slug that is not one of :data:`TABLE_PAGE_SLUGS`."""


class TablePageNotInCanon(LookupError):
    """A listed roller page that the loaded canon does not carry."""


@dataclass(frozen=True)
class TablePage:
    """A GM-tools page and the dice tables it publishes.

    Attributes:
        slug: The page's file name without ``.md``, e.g. ``"job-generator"``.
        title: The page's ``#`` heading, e.g. ``"Job Generator"``.
        tables: Every dice table on the page, in page order. Empty when the
            page carries no rollable table.
    """

    slug: str
    title: str
    tables: tuple[DiceTable, ...]

    @property
    def reference_path(self) -> str:
        """The page's path under the reference app, for linking back to it."""
        return f"{GM_TOOLS_REFERENCE_PREFIX}{self.slug}"


@dataclass(frozen=True)
class RolledTable:
    """One table on the page and the roll made on it."""

    table: DiceTable
    roll: TableRoll

    @property
    def cells(self) -> tuple[tuple[str, str], ...]:
        """Every column the rolled face reads, as ``(label, text)`` pairs."""
        return self.table.cells_for_face(self.roll.face)


@dataclass(frozen=True)
class TablePageRoll:
    """A page rolled once per table, reproducible from its seed."""

    page: TablePage
    seed: int | None
    rolls: tuple[RolledTable, ...]


def table_page_path(slug: str) -> Path:
    """The markdown file of a roller page in the loaded canon.

    Raises:
        UnknownTablePage: If ``slug`` is not a roller page. Only listed slugs
            reach the filesystem, so a request cannot name another file.
    """
    if slug not in TABLE_PAGE_SLUGS:
        raise UnknownTablePage(slug)
    return GM_TOOLS_DIR / f"{slug}.md"


def table_page_slug_for_reference_path(reference_path: str) -> str | None:
    """The roller slug a reference page path names, if it has a roller.

    Args:
        reference_path: A reference URL path such as
            ``"dm-rules/gm-tools/job-generator"``.

    Returns:
        The slug, or ``None`` when the page has no table roller.
    """
    if not reference_path.startswith(GM_TOOLS_REFERENCE_PREFIX):
        return None
    slug = reference_path.removeprefix(GM_TOOLS_REFERENCE_PREFIX)
    return slug if slug in TABLE_PAGE_SLUGS else None


def _page_title(markdown_text: str, slug: str) -> str:
    """The page's first ``#`` heading, or the slug when it has none."""
    for line in markdown_text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return slug


def load_table_page(slug: str) -> TablePage:
    """Read and parse one roller page from the loaded canon.

    Read on every call rather than cached, so a canon edit shows on the next
    roll without a restart.

    The slug comes from the request, so the file it names is held to the
    GM-tools directory by :func:`reference.paths.file_inside` as well as by
    the slug list: a canon whose page is a symlink out of that directory is
    refused rather than followed.

    Raises:
        UnknownTablePage: If ``slug`` is not a roller page.
        TablePageNotInCanon: If the loaded canon has no file for the page.
        PathOutsideRoot: If the page's file resolves outside the GM-tools
            directory.
        ValueError: If a dice table on the page is malformed.
    """
    # The root is the GM-tools folder, not the whole canon the reference view
    # holds pages to: a GM-tools page symlinked to another place inside the
    # canon is refused here by design, as the roller reads only its own folder.
    markdown_path = file_inside(GM_TOOLS_DIR, table_page_path(slug))
    if not markdown_path.is_file():
        raise TablePageNotInCanon(f"The loaded canon has no {markdown_path}")
    markdown_text = markdown_path.read_text(encoding="utf-8")
    return TablePage(
        slug=slug,
        title=_page_title(markdown_text, slug),
        tables=tuple(parse_tables(markdown_text).values()),
    )


def roll_table_page(page: TablePage, seed: int | None = None) -> TablePageRoll:
    """Roll each dice table on a page once, in page order.

    Args:
        page: The page, as :func:`load_table_page` read it.
        seed: When given, the same seed reproduces every roll.

    Returns:
        The page with one roll per table; no rolls when it has no tables.
    """
    roller = Roller(seed=seed)
    rolls = tuple(
        RolledTable(table=table, roll=roll_table(table, roller))
        for table in page.tables
    )
    return TablePageRoll(page=page, seed=seed, rolls=rolls)
