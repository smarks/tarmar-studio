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
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

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
SCRIPT_TAG = re.compile(r"<script\b[^>]*>")
INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=")
JAVASCRIPT_URL = re.compile(r"""(?:href|src|action)\s*=\s*["']\s*javascript:""", re.I)
#: What the browser never sees: a template comment or an HTML comment, which
#: may mention a tag by name in explaining it.
COMMENTS = re.compile(
    r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}|\{#.*?#\}|<!--.*?-->", re.S
)
NONCE_ATTRIBUTE = 'nonce="{{ csp_nonce }}"'
DATA_BLOCK_TYPE = 'type="application/json"'


def markup_of(path: Path) -> str:
    """The template's text less its comments."""
    return COMMENTS.sub("", path.read_text())


class EveryTemplateFitsThePolicyTest(SimpleTestCase):
    """The sweep over every template in the repository (issue #178)."""

    def test_the_corpus_is_not_empty(self):
        self.assertGreater(len(PROJECT_TEMPLATES), 20)

    def test_every_inline_script_block_carries_the_nonce(self):
        offenders = []
        for path in PROJECT_TEMPLATES:
            for tag in SCRIPT_TAG.findall(markup_of(path)):
                if "src=" in tag or DATA_BLOCK_TYPE in tag or NONCE_ATTRIBUTE in tag:
                    continue
                offenders.append(f"{path.relative_to(ROOT)}: {tag}")
        self.assertEqual(offenders, [])

    def test_no_template_carries_an_inline_event_handler(self):
        offenders = [
            f"{path.relative_to(ROOT)}: {handler.strip()}"
            for path in PROJECT_TEMPLATES
            for handler in INLINE_HANDLER.findall(markup_of(path))
        ]
        self.assertEqual(offenders, [])

    def test_no_template_links_to_javascript(self):
        offenders = [
            str(path.relative_to(ROOT))
            for path in PROJECT_TEMPLATES
            if JAVASCRIPT_URL.search(markup_of(path))
        ]
        self.assertEqual(offenders, [])


class NoInlineScriptTest(SimpleTestCase):
    def test_no_inline_script_blocks(self):
        for template in TEMPLATES:
            with self.subTest(template=template):
                html = (ROOT / template).read_text()
                inline = [
                    tag
                    for tag in re.findall(r"<script\b[^>]*>", html)
                    if "src=" not in tag
                ]
                self.assertEqual(inline, [])

    def test_no_inline_event_handlers(self):
        for template in TEMPLATES:
            with self.subTest(template=template):
                html = (ROOT / template).read_text()
                self.assertEqual(re.findall(r"\son[a-z]+=", html), [])
