"""The character and user list pages carry markup only (#338 items 18 and 19),
and every template fits the Content-Security-Policy (issue #178).

Their behaviour used to sit in inline <script> blocks and onclick/onchange
attributes, where Jest could not reach it. It lives in static/js now; these
tests keep it there. Since #178 the policy's script-src admits no inline
script at all except by nonce, so the sweep below holds every template of this
repository to it: an inline <script> block carries ``nonce="{{ csp_nonce }}"``
(a ``json_script`` data block needs none, since it never runs), no element
carries an ``on*=`` handler, and no link runs ``javascript:``. A template that
broke the rule would render a page the browser refuses to run.

The templates are read the way a browser reads them, with the standard
library's HTML parser, rather than matched by regular expression: the parser
lowercases tag and attribute names, takes quoted and unquoted values alike,
and skips HTML comments, so ``<SCRIPT>``, ``onclick=go()`` and
``href=" JavaScript:x"`` are all seen for what they are.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from common.test_helpers import SOURCE_ATTRIBUTE, PolicyFindings, findings_in

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = [
    "characters/templates/characters/character_list.html",
    "characters/templates/characters/deleted_characters.html",
    "accounts/templates/accounts/users_list.html",
]

PROJECT_TEMPLATES = sorted(
    path
    for path in Path(settings.BASE_DIR).rglob("templates/**/*.html")
    if ".venv" not in path.parts and "node_modules" not in path.parts
)
#: What the browser never sees: a template comment or an HTML comment, which
#: may mention a tag by name in explaining it.
COMMENTS = re.compile(
    r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}|\{#.*?#\}|<!--.*?-->", re.S
)
#: A template tag inside an element's start tag (``<a {% if x %}href="..."
#: {% endif %}>``) would otherwise reach the parser glued to the attribute
#: name beside it. Variables (``{{ csp_nonce }}``) are values and stay.
TEMPLATE_TAGS = re.compile(r"\{%.*?%\}", re.S)


def markup_of(path: Path) -> str:
    """The template's text less its comments and template tags."""
    return TEMPLATE_TAGS.sub(" ", COMMENTS.sub("", path.read_text()))


def findings_of(path: Path) -> PolicyFindings:
    """What the policy cares about in the template at ``path``."""
    return findings_in(markup_of(path))


class PolicyCollectorTest(SimpleTestCase):
    """The collector sees markup as a browser does, not as it is spelled."""

    def test_the_collector_catches_each_spelling(self):
        findings = findings_in(
            "<SCRIPT>alert(1)</SCRIPT>\n"
            "<div onclick=go()>x</div>\n"
            '<a href=" JavaScript:x">y</a>\n'
            '<!-- <script> onload=ignored href="javascript:ignored" -->\n'
        )
        self.assertEqual([tag.text for tag in findings.script_tags], ["<SCRIPT>"])
        self.assertTrue(findings.script_tags[0].runs_inline)
        self.assertEqual(findings.inline_handlers, ["onclick"])
        self.assertEqual(findings.javascript_urls, ['href=" JavaScript:x"'])

    def test_a_sourced_block_a_data_block_and_a_nonced_block_pass(self):
        findings = findings_in(
            '<script src="/static/js/chat.js"></script>\n'
            '<script id="data" type="application/json">{}</script>\n'
            '<script nonce="{{ csp_nonce }}">menu();</script>\n'
        )
        self.assertEqual([tag.runs_inline for tag in findings.script_tags], [False] * 3)


class EveryTemplateFitsThePolicyTest(SimpleTestCase):
    """The sweep over every template in the repository (issue #178)."""

    def test_the_corpus_is_not_empty(self):
        self.assertGreater(len(PROJECT_TEMPLATES), 20)

    def test_every_inline_script_block_carries_the_nonce(self):
        offenders = [
            f"{path.relative_to(ROOT)}: {tag.text}"
            for path in PROJECT_TEMPLATES
            for tag in findings_of(path).script_tags
            if tag.runs_inline
        ]
        self.assertEqual(offenders, [])

    def test_no_template_carries_an_inline_event_handler(self):
        offenders = [
            f"{path.relative_to(ROOT)}: {handler}"
            for path in PROJECT_TEMPLATES
            for handler in findings_of(path).inline_handlers
        ]
        self.assertEqual(offenders, [])

    def test_no_template_links_to_javascript(self):
        offenders = [
            f"{path.relative_to(ROOT)}: {url}"
            for path in PROJECT_TEMPLATES
            for url in findings_of(path).javascript_urls
        ]
        self.assertEqual(offenders, [])


class NoInlineScriptTest(SimpleTestCase):
    def test_no_inline_script_blocks(self):
        for template in TEMPLATES:
            with self.subTest(template=template):
                findings = findings_in((ROOT / template).read_text())
                inline = [
                    tag.text
                    for tag in findings.script_tags
                    if SOURCE_ATTRIBUTE not in tag.attributes
                ]
                self.assertEqual(inline, [])

    def test_no_inline_event_handlers(self):
        for template in TEMPLATES:
            with self.subTest(template=template):
                findings = findings_in((ROOT / template).read_text())
                self.assertEqual(findings.inline_handlers, [])
