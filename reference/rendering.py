"""The one markdown-to-safe-HTML pipeline for reference content.

Extracted from ``reference.views`` (issue #276) so the hook generator can
render hook prose exactly the way the reference pages render it: markdown
with tables, sanitized by bleach against the same allowlists. The reference
view layers ``reference.tables.annotate_tables`` on top for its responsive
tables; callers without tables don't need it.
"""

import bleach
import markdown

# Allowed HTML tags and attributes for sanitizing markdown output
ALLOWED_TAGS = [
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "br",
    "hr",
    "strong",
    "em",
    "u",
    "s",
    "code",
    "pre",
    "a",
    "img",
    "ul",
    "ol",
    "li",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "blockquote",
]
# Safe protocols for URLs (prevents javascript: XSS)
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title"],
    "img": ["src", "alt", "title"],
    "th": ["colspan", "rowspan"],
    "td": ["colspan", "rowspan"],
}


def render_reference_markdown(markdown_text: str) -> str:
    """Render markdown to HTML sanitized against the reference allowlists."""
    return bleach.clean(
        markdown.markdown(markdown_text, extensions=["tables"]),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
    )
