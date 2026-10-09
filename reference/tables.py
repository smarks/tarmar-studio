"""Post-processing for markdown-rendered rules tables.

Reference tables are wide (the prior-experience skill-track grid is nine
columns), and the old treatment put them behind a horizontal scrollbar. A
scrollbar hides columns, so rules that should be readable at a glance are not.
Instead, every cell is tagged with the name of its column and every table is
tagged with its column count; the stylesheet uses those to restack a table into
labelled blocks once it is too wide for the viewport. Nothing is ever clipped.

Runs after ``bleach.clean`` — the attributes added here are ours, not user
content, so sanitizing them again would only strip them back off.
"""

from html import escape, unescape
from html.parser import HTMLParser


def _render_attributes(attributes: list[tuple[str, str | None]]) -> str:
    """Render parsed attributes back into an HTML attribute string."""
    rendered = ""
    for attribute_name, attribute_value in attributes:
        if attribute_value is None:
            rendered += f" {attribute_name}"
        else:
            rendered += f' {attribute_name}="{escape(attribute_value, quote=True)}"'
    return rendered


class _ColumnLabelParser(HTMLParser):
    """Re-emit HTML, annotating tables and their body cells.

    Each ``<td>`` gains ``data-label`` holding the text of its column's header,
    and each ``<table>`` gains ``data-columns`` holding its header count.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.output_parts: list[str] = []
        self.open_tables: list[dict] = []

    @property
    def current_table(self) -> dict | None:
        return self.open_tables[-1] if self.open_tables else None

    def _record_header_text(self, text: str) -> None:
        table = self.current_table
        if table is not None and table["capturing_header"]:
            table["header_buffer"].append(text)

    def handle_starttag(self, tag: str, attributes: list) -> None:
        if tag == "table":
            self.open_tables.append(
                {
                    "headers": [],
                    "column_index": 0,
                    "seen_body_cell": False,
                    "capturing_header": False,
                    "header_buffer": [],
                    "output_index": len(self.output_parts),
                }
            )
        elif tag == "tr" and self.current_table is not None:
            self.current_table["column_index"] = 0
        elif tag == "th" and self.current_table is not None:
            table = self.current_table
            if not table["seen_body_cell"]:
                table["capturing_header"] = True
                table["header_buffer"] = []
        elif tag == "td" and self.current_table is not None:
            table = self.current_table
            table["seen_body_cell"] = True
            column_index = table["column_index"]
            table["column_index"] = column_index + 1
            has_label = any(name == "data-label" for name, _ in attributes)
            if not has_label and column_index < len(table["headers"]):
                column_name = table["headers"][column_index]
                if column_name:
                    attributes = [*attributes, ("data-label", column_name)]

        self.output_parts.append(f"<{tag}{_render_attributes(attributes)}>")

    def handle_startendtag(self, tag: str, attributes: list) -> None:
        self.output_parts.append(f"<{tag}{_render_attributes(attributes)} />")

    def handle_endtag(self, tag: str) -> None:
        table = self.current_table
        if tag == "th" and table is not None and table["capturing_header"]:
            table["capturing_header"] = False
            table["headers"].append("".join(table["header_buffer"]).strip())
            table["header_buffer"] = []

        self.output_parts.append(f"</{tag}>")

        if tag == "table" and table is not None:
            self.open_tables.pop()
            opening_tag = self.output_parts[table["output_index"]]
            column_count = len(table["headers"])
            self.output_parts[table["output_index"]] = (
                f'<table data-columns="{column_count}"' + opening_tag[len("<table") :]
            )

    def handle_data(self, data: str) -> None:
        self._record_header_text(data)
        self.output_parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self._record_header_text(unescape(f"&{name};"))
        self.output_parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._record_header_text(unescape(f"&#{name};"))
        self.output_parts.append(f"&#{name};")

    def result(self) -> str:
        return "".join(self.output_parts)


def annotate_tables(html_content: str) -> str:
    """Tag tables with their column count and cells with their column name."""
    parser = _ColumnLabelParser()
    parser.feed(html_content)
    parser.close()
    return parser.result()
