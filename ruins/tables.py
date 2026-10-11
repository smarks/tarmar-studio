"""Parse the Dungeons & Ruins dice tables out of the reference markdown.

The markdown page at ``dm-rules/gm-tools/dungeons-ruins.md`` in the canon
directory (``reference.paths.DM_RULES_DIR``, which may live outside this
repository, issue #917) is the single source of truth for the tables (issue
#194): this module reads that file and turns each ``| dN | Title |`` table into
a :class:`DiceTable`, so an edit to the page changes the generator with no code
change. The drift guard lives in ``ruins/tests/test_tables.py``: it parses the
real file and asserts the table set the generator expects is still published.

The same parser reads every other GM-tools table page for the generic table
roller (:mod:`ruins.table_rolls`, issue #809), which is why it also takes the
shapes those pages use: dice side by side in one pipe table, ranged faces
(``1−2``), and extra result columns read off the same roll.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

from reference.markdown_tables import split_table_row
from reference.paths import GM_TOOLS_DIR

TABLES_MARKDOWN_PATH = GM_TOOLS_DIR / "dungeons-ruins.md"

_DIE_HEADER_PATTERN = re.compile(r"^d(\d+)$")
_SEPARATOR_CELL_PATTERN = re.compile(r"^:?-+:?$")
# A face cell: one face (``3``) or an inclusive range (``1-2``). Canon pages
# write ranges with a hyphen, an en dash or the minus sign (U+2212).
_FACE_PATTERN = re.compile(r"^(\d+)(?:\s*[-–−]\s*(\d+))?$")


@dataclass(frozen=True)
class DiceTable:
    """One rollable table: a die size and one result row per face.

    Attributes:
        section: The ``##`` heading the table appeared under, e.g.
            ``"Ruin Origin"``.
        title: The table's own header label, e.g. ``"Built by..."`` — the
            key the generator looks tables up by.
        die_sides: The die size from the header's first cell (``d8`` → 8).
        rows: The result text for each face, in face order (``rows[0]`` is
            face 1). A ranged row (``1−2``) fills every face it covers.
        extra_columns: The labels of any further result columns the same
            roll reads, e.g. ``("Hindered by",)`` beside ``Wants``.
        extra_rows: For each face, the cells under ``extra_columns``.
    """

    section: str
    title: str
    die_sides: int
    rows: tuple[str, ...]
    extra_columns: tuple[str, ...] = ()
    extra_rows: tuple[tuple[str, ...], ...] = ()

    @property
    def die(self) -> str:
        """The die notation, e.g. ``"d8"``."""
        return f"d{self.die_sides}"

    def row_for_face(self, face: int) -> str:
        """Return the result row a rolled face lands on.

        Raises:
            ValueError: If ``face`` is outside ``1..die_sides``.
        """
        if not 1 <= face <= self.die_sides:
            raise ValueError(
                f"Face {face} is outside {self.die} for table “{self.title}”"
            )
        return self.rows[face - 1]

    def cells_for_face(self, face: int) -> tuple[tuple[str, str], ...]:
        """Every column a rolled face reads, as ``(label, text)`` pairs.

        The table's own title comes first, then each extra column, so a
        ``| d8 | Wants | Hindered by |`` row answers both from one roll.

        Raises:
            ValueError: If ``face`` is outside ``1..die_sides``.
        """
        first = (self.title, self.row_for_face(face))
        extras = self.extra_rows[face - 1] if self.extra_rows else ()
        return (first, *zip(self.extra_columns, extras, strict=True))


@dataclass
class _PendingTable:
    """A dice table being collected: one die column group of a pipe table.

    A pipe table may set several dice side by side
    (``| d10 | Someone needs... | d6 | The catch... |``): each die cell starts
    a group that runs up to the next die cell, and each group is its own
    table, rolled separately.
    """

    section: str
    die_sides: int
    first_column: int
    end_column: int
    labels: tuple[str, ...]
    rows: dict[int, tuple[str, ...]]

    @property
    def title(self) -> str:
        return self.labels[0]

    def add_row(self, cells: list[str]) -> None:
        """Record this group's slice of one body row.

        A group shorter than its neighbours leaves its cells blank on the
        rows it does not reach; those rows are skipped.

        Raises:
            ValueError: A face cell that is not a face or range, a reversed
                range, results with no face, an empty result cell on a face,
                or a face given twice.
        """
        group_cells = cells[self.first_column : self.end_column]
        group_cells += [""] * (self.end_column - self.first_column - len(group_cells))
        face_cell, results = group_cells[0], tuple(group_cells[1:])
        if not face_cell:
            if any(results):
                raise ValueError(f"Table “{self.title}” has a row with no face")
            return
        face_match = _FACE_PATTERN.fullmatch(face_cell)
        if face_match is None:
            raise ValueError(
                f"Table “{self.title}” has a face “{face_cell}” that is not a "
                "number or a range"
            )
        low = int(face_match.group(1))
        high = int(face_match.group(2) or low)
        if high < low:
            raise ValueError(
                f"Table “{self.title}” has a reversed range “{face_cell}”: "
                f"write it low to high, {high}–{low}"
            )
        for column_index, cell in enumerate(results):
            if not cell:
                raise ValueError(
                    f"Table “{self.title}” has an empty “{self.labels[column_index]}” "
                    f"cell on face “{face_cell}”"
                )
        for face in range(low, high + 1):
            if face in self.rows:
                raise ValueError(f"Table “{self.title}” repeats face {face}")
            self.rows[face] = results

    def build(self) -> DiceTable:
        """The finished table.

        Raises:
            ValueError: If the faces do not run exactly 1..N for a dN header.
        """
        faces = sorted(self.rows)
        expected_faces = list(range(1, self.die_sides + 1))
        if faces != expected_faces:
            raise ValueError(
                f"Table “{self.title}” is misnumbered: a d{self.die_sides} "
                f"needs faces {expected_faces[0]}–{expected_faces[-1]}, got {faces}"
            )
        ordered = [self.rows[face] for face in expected_faces]
        has_extra_columns = len(self.labels) > 1
        return DiceTable(
            section=self.section,
            title=self.title,
            die_sides=self.die_sides,
            rows=tuple(cells[0] for cells in ordered),
            extra_columns=self.labels[1:],
            extra_rows=(
                tuple(cells[1:] for cells in ordered) if has_extra_columns else ()
            ),
        )


def _header_groups(cells: list[str], section: str) -> list[_PendingTable]:
    """Split a dice header row into its die column groups.

    Raises:
        ValueError: If a die cell has no label beside it.
    """
    die_columns = [
        index for index, cell in enumerate(cells) if _DIE_HEADER_PATTERN.fullmatch(cell)
    ]
    groups = []
    for position, first_column in enumerate(die_columns):
        end_column = (
            die_columns[position + 1] if position + 1 < len(die_columns) else len(cells)
        )
        labels = tuple(cells[first_column + 1 : end_column])
        if not labels or not labels[0]:
            raise ValueError(
                f"The {cells[first_column]} column under “{section}” has no "
                "table title beside it"
            )
        groups.append(
            _PendingTable(
                section=section,
                die_sides=int(cells[first_column][1:]),
                first_column=first_column,
                end_column=end_column,
                labels=labels,
                rows={},
            )
        )
    return groups


def parse_tables(markdown_text: str) -> dict[str, DiceTable]:
    """Parse every dice table in a markdown document, keyed by title.

    A dice table starts at a header row whose first cell is a die size
    (``| d8 | Built by... |``); its body rows map face numbers, or ranges
    such as ``1−2``, to results. A header may set several dice side by side
    (``| d10 | Needs | d6 | Catch |``): each die and the columns up to the
    next die is a separate table. Further columns after a die's title are
    read off the same roll (``| d8 | Wants | Hindered by |``). Tables whose
    header is not a die size are ignored, as is all prose. Tables come back
    in page order, left to right within a row.

    Raises:
        ValueError: If a dice table is malformed — faces that do not run
            exactly 1..N for a dN header, a face that is not a number or
            range, or two tables sharing a title.
    """
    tables: dict[str, DiceTable] = {}
    section = ""
    pending: list[_PendingTable] = []

    def finalize() -> None:
        nonlocal pending
        for group in pending:
            if group.title in tables:
                raise ValueError(f"Duplicate table title “{group.title}”")
            tables[group.title] = group.build()
        pending = []

    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            finalize()
            section = line[3:].strip()
            continue
        if not line.startswith("|"):
            finalize()
            continue
        cells = split_table_row(line)
        if len(cells) < 2:
            finalize()
            continue
        first_cell = cells[0]
        if _DIE_HEADER_PATTERN.fullmatch(first_cell):
            finalize()
            pending = _header_groups(cells, section)
            continue
        if _SEPARATOR_CELL_PATTERN.fullmatch(first_cell):
            continue
        if first_cell and _FACE_PATTERN.fullmatch(first_cell) is None:
            # A table that is not a dice table (non-numeric first column);
            # nothing of ours to collect.
            finalize()
            continue
        for group in pending:
            group.add_row(cells)
    finalize()
    return tables


@lru_cache(maxsize=1)
def load_tables() -> dict[str, DiceTable]:
    """Parse and cache the tables from the reference markdown file."""
    return parse_tables(TABLES_MARKDOWN_PATH.read_text(encoding="utf-8"))
