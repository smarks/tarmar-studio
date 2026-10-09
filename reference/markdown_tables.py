"""Plain-text parsing of markdown pipe tables, defined once (issue #337).

The ruins generators parse dice tables and hook lists out of the canon at
runtime, and the rules drift guards read tables out of the same pages to pin
code to the documents. Each used to carry its own copy of the row splitter, so
a fix to one (an escaped pipe, say) would have left the others behind.

Free of Django, like ``reference.paths``, so the offline tools can import it.
"""

SEPARATOR_CHARACTERS = frozenset("-: ")


def split_table_row(line: str) -> list[str]:
    """Split a ``| a | b |`` markdown row into its stripped cell texts.

    Args:
        line: One line of a markdown pipe table, with or without surrounding
            whitespace.

    Returns:
        The cell texts in column order, each stripped of whitespace.
    """
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def is_separator_row(cells: list[str]) -> bool:
    """Whether split cells form a ``|---|:--:|`` header separator row.

    Args:
        cells: The output of :func:`split_table_row` for one line.

    Returns:
        True when every cell holds only dashes, colons, and spaces.
    """
    return all(set(cell) <= SEPARATOR_CHARACTERS for cell in cells)


def table_rows_under_heading(markdown: str, heading: str) -> list[list[str]]:
    """The cell texts of the first pipe table after ``heading``.

    The header row is included and separator rows are dropped. The table ends
    at the first non-pipe line once a row with any non-empty cell was read.

    Args:
        markdown: The whole markdown document.
        heading: Text that appears once before the table, such as
            ``"# Turn Sequence"``.

    Returns:
        One list of stripped cell texts per table row.

    Raises:
        IndexError: If ``heading`` does not appear in ``markdown``.
    """
    section = markdown.split(heading, 1)[1]
    rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if any(cell for row in rows for cell in row):
                break
            continue
        cells = split_table_row(stripped)
        if is_separator_row(cells):
            continue
        rows.append(cells)
    return rows
