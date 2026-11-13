"""The one markdown-to-safe-HTML pipeline for reference content.

Extracted from ``reference.views`` (issue #276) so the hook generator can
render hook prose exactly the way the reference pages render it: markdown
with tables, sanitized by bleach against the same allowlists. The reference
view layers ``reference.tables.annotate_tables`` on top for its responsive
tables; callers without tables don't need it.
"""

from html.parser import HTMLParser
from typing import Any

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


#: What model-written markdown may carry (issue #177): the reference
#: allowlist less images, and a link only to this site.
MODEL_ALLOWED_TAGS = [tag for tag in ALLOWED_TAGS if tag != "img"]


#: What a browser drops from an href before resolving it, so a check has to
#: drop it first: ``/<TAB>/evil.example`` is ``//evil.example`` to the browser.
_HREF_IGNORED_CHARACTERS = str.maketrans("", "", "\t\n\r")


def _link_stays_on_this_site(tag: str, name: str, value: str) -> bool:
    """bleach's attribute filter for a model-written ``<a>``: ``title``
    always, ``href`` only when it is a path on this site.

    Read as a browser reads it: tabs and newlines dropped, then a backslash
    anywhere refused (browsers turn ``/\\evil.example`` into a host), then one
    leading slash and never two.
    """
    if name == "title":
        return True
    if name != "href":
        return False
    href = value.translate(_HREF_IGNORED_CHARACTERS).strip()
    if "\\" in href:
        return False
    return href.startswith("/") and not href.startswith("//")


MODEL_ALLOWED_ATTRIBUTES: dict[str, Any] = {
    **{
        tag: attributes
        for tag, attributes in ALLOWED_ATTRIBUTES.items()
        if tag != "img"
    },
    "a": _link_stays_on_this_site,
}


#: The elements the chat page's sanitizer lets through -- the ALLOWED_TAGS of
#: MARKDOWN_SANITIZE_CONFIG in ``static/js/chat.js`` (DOMPurify), spelled once
#: on this side and held equal to that list by ``gm/test_prompt_injection.py``.
#: Text reaches the screen only inside these; anything else is dropped whole,
#: contents included, so the reader of a proposal fails closed: an element
#: nobody thought of hides its text here as it does there.
CHAT_ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "p",
        "br",
        "hr",
        "strong",
        "em",
        "b",
        "i",
        "u",
        "s",
        "del",
        "code",
        "pre",
        "blockquote",
        "ul",
        "ol",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "a",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "span",
        "input",
    }
)

#: HTML's void elements: a ``<br/>`` is complete, while a self-closing
#: ``<script/>`` is, to a browser, an open script that swallows the rest.
VOID_ELEMENTS: frozenset[str] = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)


class _VisibleTextParser(HTMLParser):
    """Collect the text a reader of the chat would see of an HTML fragment.

    Text counts only while every open element above it is in
    :data:`CHAT_ALLOWED_TAGS`; an element outside the list hides its whole
    subtree, and one left unclosed hides everything after it, as a browser's
    ``<script>`` or ``<plaintext>`` does. A self-closing tag that is not a
    void element opens, as in a browser. Tag names arrive lowercased;
    comments and attributes carry no text; entities are decoded by the parser
    (``convert_charrefs``), so the result is the characters on screen.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._open: list[str] = []
        self._hidden_depth = 0

    def _visible(self) -> bool:
        return self._hidden_depth == 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in VOID_ELEMENTS:
            self.parts.append(" ")
            return
        self._open.append(tag)
        if tag not in CHAT_ALLOWED_TAGS:
            self._hidden_depth += 1
        self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # ``<br/>`` is complete; ``<script/>`` opens and eats what follows.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag not in self._open:
            return
        while self._open:
            closed = self._open.pop()
            if closed not in CHAT_ALLOWED_TAGS:
                self._hidden_depth -= 1
            if closed == tag:
                break
        self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._visible():
            self.parts.append(data)


def visible_text_of_markdown(markdown_text: str) -> str:
    """What a reader sees of ``markdown_text``, and nothing they do not (#177).

    The one statement of what a reply "says": the markdown rendered as the
    page renders it, then read as the chat shows it: only text inside the
    elements the chat's sanitizer allows (:data:`CHAT_ALLOWED_TAGS`) counts,
    so a link's title, a reference definition, an HTML comment, and everything
    inside any other element -- a ``<script>``, an ``<svg>``, an ``<iframe>``,
    a made-up tag -- text a Game Master confirming a proposal never saw, are
    not in it. ``TurnPolicy.addresses`` reads a proposal
    through this when a bare affirmative answers it.

    Args:
        markdown_text: The reply as the model wrote it.

    Returns:
        The visible text, whitespace collapsed.
    """
    parser = _VisibleTextParser()
    parser.feed(markdown.markdown(markdown_text, extensions=["tables"]))
    parser.close()
    return " ".join("".join(parser.parts).split())


def render_model_markdown(markdown_text: str) -> str:
    """Render markdown a model wrote, keeping its images and links off other hosts.

    A model's reply can be steered by text a player wrote into a record
    (issue #177), and an image or a link in it is fetched or followed by the
    reader's browser from wherever its author pointed it: the image reports
    the reader's address to that server as it loads, the link carries the
    reader there. So no image at all, and a link only to a path on this site;
    the text of a dropped link stays. The page's Content-Security-Policy
    (``img-src 'self'``, issue #178) is the second wall behind this one.

    Args:
        markdown_text: The model's markdown, as it wrote it.

    Returns:
        Sanitized HTML.
    """
    # strip=True takes a disallowed tag out rather than showing it escaped,
    # so an image's address is not even printed as text.
    return bleach.clean(
        markdown.markdown(markdown_text, extensions=["tables"]),
        tags=MODEL_ALLOWED_TAGS,
        attributes=MODEL_ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )
