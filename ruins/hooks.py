"""Parse the adventure hooks out of the reference markdown (issue #276).

The markdown page at ``dm-rules/gm-tools/low-experience-hooks.md`` in the canon
directory (``reference.paths.DM_RULES_DIR``, which may live outside this
repository, issue #917) is the single source of truth for the hooks, the same
way ``dungeons-ruins.md`` is for the ruin tables (issue #194): this module
reads that file and turns each ``###`` hook section into a :class:`HookEntry`,
so an edit to the page changes the generator with no code change. Unlike the
ruin tables the page is prose with sections, not dice tables, so the structure
parsed here is:

- an index table (``| Hook | Region | Type |``) whose rows anchor-link the
  sections — the hook's *type* lives only there;
- one ``###`` section per hook under a ``##`` grouping heading, carrying a
  ``**Region:**`` line (markdown links to region pages included), a
  ``**Levels:**`` band line, and the hook's prose.

The parser is strict on purpose: a hook missing its band or region, an index
row without a matching section (or the reverse), or a malformed band raises
``ValueError``. The drift guard in ``ruins/tests/test_hooks.py`` parses the
real file, so drift there fails tests instead of silently changing output.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

from reference.markdown_tables import split_table_row
from reference.paths import GM_TOOLS_DIR

HOOKS_MARKDOWN_PATH = GM_TOOLS_DIR / "low-experience-hooks.md"

_INDEX_ROW_PATTERN = re.compile(r"^\[(?P<title>[^\]]+)\]\(#(?P<anchor>[^)]+)\)$")
# Bands are written with a hyphen, an en dash or the minus sign (U+2212), as
# the dice-table ranges in ruins.tables are.
_LEVEL_BAND_PATTERN = re.compile(
    r"^(?P<low>\d+)(?:\s*[-\u2013\u2212]\s*(?P<high>\d+))?$"
)
_MARKDOWN_LINK_PATTERN = re.compile(r"\[(?P<label>[^\]]+)\]\((?P<url>[^)]+)\)")
_REGION_MARKER = "**Region:**"
_LEVELS_MARKER = "**Levels:**"


@dataclass(frozen=True)
class HookEntry:
    """One adventure hook parsed from the reference page.

    Attributes:
        title: The ``###`` heading text, e.g. ``"The Miller's Missing Ledger"``.
        anchor: The heading's URL fragment, matching the index table's link.
        section: The ``##`` grouping the hook sits under, e.g.
            ``"Jobs & Debts"``.
        hook_type: The Type column from the index table, e.g. ``"Job"``.
        levels: The experience levels the hook is sized for, from its
            ``**Levels:**`` band — ``(1, 2)`` for ``1–2``.
        region_markdown: The ``**Region:**`` value, markdown intact, so
            region-page links survive into the rendered result.
        region_links: Every ``(label, url)`` link in the region line —
            the cross-references to the regional lore pages.
        body_markdown: The hook's prose, markdown intact.
    """

    title: str
    anchor: str
    section: str
    hook_type: str
    levels: tuple[int, ...]
    region_markdown: str
    region_links: tuple[tuple[str, str], ...]
    body_markdown: str

    @property
    def band_label(self) -> str:
        """The band as the page writes it: ``"1"`` or ``"1–2"``."""
        if len(self.levels) == 1:
            return str(self.levels[0])
        return f"{self.levels[0]}–{self.levels[-1]}"


def _heading_anchor(title: str) -> str:
    """The URL fragment a markdown heading renders with (GitHub-style)."""
    kept = re.sub(r"[^\w\s-]", "", title.lower())
    return re.sub(r"\s+", "-", kept.strip())


def _parse_level_band(band_text: str, title: str) -> tuple[int, ...]:
    """Parse a ``**Levels:**`` value like ``1`` or ``1–2`` into level tuple."""
    match = _LEVEL_BAND_PATTERN.fullmatch(band_text.strip())
    if not match:
        raise ValueError(f"Hook “{title}” has an unparseable band “{band_text}”")
    low = int(match.group("low"))
    high = int(match.group("high") or low)
    if high < low:
        raise ValueError(f"Hook “{title}” has an inverted band “{band_text}”")
    return tuple(range(low, high + 1))


def parse_hooks(markdown_text: str) -> tuple[HookEntry, ...]:
    """Parse every hook section, cross-checked against the index table.

    Returns the hooks in page order.

    Raises:
        ValueError: If a hook section is missing its region or band, a band
            does not parse, two hooks share an anchor, or the index table and
            the sections disagree about which hooks the page publishes.
    """
    index_types: dict[str, str] = {}
    sections: list[dict] = []
    section_heading = ""
    lines = markdown_text.splitlines()
    position = 0

    def consume_block(start: int) -> tuple[list[str], int]:
        """Collect lines from ``start`` until a blank line; return (lines, next)."""
        block: list[str] = []
        cursor = start
        while cursor < len(lines) and lines[cursor].strip():
            block.append(lines[cursor].strip())
            cursor += 1
        return block, cursor

    while position < len(lines):
        line = lines[position].strip()
        if line.startswith("## ") and not line.startswith("### "):
            section_heading = line[3:].strip()
            position += 1
            continue
        if line.startswith("|"):
            cells = split_table_row(line)
            if cells:
                index_match = _INDEX_ROW_PATTERN.fullmatch(cells[0])
                if index_match and len(cells) >= 3:
                    index_types[index_match.group("anchor")] = cells[2]
            position += 1
            continue
        if line.startswith("### "):
            title = line[4:].strip()
            sections.append(
                {
                    "title": title,
                    "anchor": _heading_anchor(title),
                    "section": section_heading,
                    "region": None,
                    "levels": None,
                    "body": [],
                }
            )
            position += 1
            continue
        if sections and line.startswith(_REGION_MARKER):
            block, position = consume_block(position)
            block[0] = block[0][len(_REGION_MARKER) :].strip()
            sections[-1]["region"] = " ".join(block)
            continue
        if sections and line.startswith(_LEVELS_MARKER):
            band_text = line[len(_LEVELS_MARKER) :].strip()
            sections[-1]["levels"] = _parse_level_band(band_text, sections[-1]["title"])
            position += 1
            continue
        if sections and line:
            block, position = consume_block(position)
            sections[-1]["body"].append(" ".join(block))
            continue
        position += 1

    hooks: list[HookEntry] = []
    seen_anchors: set[str] = set()
    for parsed in sections:
        title = parsed["title"]
        if parsed["anchor"] in seen_anchors:
            raise ValueError(f"Duplicate hook anchor “{parsed['anchor']}”")
        seen_anchors.add(parsed["anchor"])
        if parsed["region"] is None:
            raise ValueError(f"Hook “{title}” has no **Region:** line")
        if parsed["levels"] is None:
            raise ValueError(f"Hook “{title}” has no **Levels:** band")
        if parsed["anchor"] not in index_types:
            raise ValueError(f"Hook “{title}” is missing from the index table")
        region_markdown = parsed["region"]
        hooks.append(
            HookEntry(
                title=title,
                anchor=parsed["anchor"],
                section=parsed["section"],
                hook_type=index_types[parsed["anchor"]],
                levels=parsed["levels"],
                region_markdown=region_markdown,
                region_links=tuple(
                    (match.group("label"), match.group("url"))
                    for match in _MARKDOWN_LINK_PATTERN.finditer(region_markdown)
                ),
                body_markdown="\n\n".join(parsed["body"]),
            )
        )

    orphaned_index_rows = set(index_types) - seen_anchors
    if orphaned_index_rows:
        raise ValueError(
            f"Index table links hooks with no section: {sorted(orphaned_index_rows)}"
        )
    return tuple(hooks)


@lru_cache(maxsize=1)
def load_hooks() -> tuple[HookEntry, ...]:
    """Parse and cache the hooks from the reference markdown file."""
    return parse_hooks(HOOKS_MARKDOWN_PATH.read_text(encoding="utf-8"))
