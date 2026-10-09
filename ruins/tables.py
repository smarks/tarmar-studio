"""Parse the Dungeons & Ruins dice tables out of the reference markdown.

The markdown page at ``dm-rules/gm-tools/dungeons-ruins.md`` in the canon
directory (``reference.paths.DM_RULES_DIR``, which may live outside this
repository, issue #917) is the single source of truth for the tables (issue
#194): this module reads that file and turns each ``| dN | Title |`` table into
a :class:`DiceTable`, so an edit to the page changes the generator with no code
change. The drift guard lives in ``ruins/tests/test_tables.py``: it parses the
real file and asserts the table set the generator expects is still published.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

from reference.markdown_tables import split_table_row
from reference.paths import GM_TOOLS_DIR

TABLES_MARKDOWN_PATH = GM_TOOLS_DIR / "dungeons-ruins.md"

_DIE_HEADER_PATTERN = re.compile(r"^d(\d+)$")
_SEPARATOR_CELL_PATTERN = re.compile(r"^:?-+:?$")


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
            face 1).
    """

    section: str
    title: str
    die_sides: int
    rows: tuple[str, ...]

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


def parse_tables(markdown_text: str) -> dict[str, DiceTable]:
    """Parse every dice table in a markdown document, keyed by title.

    A dice table starts at a header row whose first cell is a die size
    (``| d8 | Built by... |``); its body rows map face numbers to results.
    Tables whose header is not a die size are ignored, as is all prose.

    Raises:
        ValueError: If a dice table is malformed — faces that do not run
            exactly 1..N for a dN header, or two tables sharing a title.
    """
    tables: dict[str, DiceTable] = {}
    section = ""
    pending: dict | None = None

    def finalize() -> None:
        nonlocal pending
        if pending is None:
            return
        faces = sorted(pending["rows"])
        expected_faces = list(range(1, pending["die_sides"] + 1))
        if faces != expected_faces:
            raise ValueError(
                f"Table “{pending['title']}” is misnumbered: a d{pending['die_sides']} "
                f"needs faces {expected_faces[0]}–{expected_faces[-1]}, got {faces}"
            )
        if pending["title"] in tables:
            raise ValueError(f"Duplicate table title “{pending['title']}”")
        tables[pending["title"]] = DiceTable(
            section=pending["section"],
            title=pending["title"],
            die_sides=pending["die_sides"],
            rows=tuple(
                pending["rows"][face] for face in range(1, pending["die_sides"] + 1)
            ),
        )
        pending = None

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
        die_match = _DIE_HEADER_PATTERN.fullmatch(first_cell)
        if die_match:
            finalize()
            pending = {
                "section": section,
                "title": cells[1],
                "die_sides": int(die_match.group(1)),
                "rows": {},
            }
            continue
        if _SEPARATOR_CELL_PATTERN.fullmatch(first_cell):
            continue
        try:
            face = int(first_cell)
        except ValueError:
            # A table that is not a dice table (non-numeric first column);
            # nothing of ours to collect.
            finalize()
            continue
        if pending is not None:
            if face in pending["rows"]:
                raise ValueError(f"Table “{pending['title']}” repeats face {face}")
            pending["rows"][face] = cells[1]
    finalize()
    return tables


@lru_cache(maxsize=1)
def load_tables() -> dict[str, DiceTable]:
    """Parse and cache the tables from the reference markdown file."""
    return parse_tables(TABLES_MARKDOWN_PATH.read_text(encoding="utf-8"))
