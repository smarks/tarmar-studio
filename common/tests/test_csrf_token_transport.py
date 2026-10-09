"""The CSRF token takes exactly one route from Django to JavaScript.

Before issue #338 it took five: interpolated into a script literal by four
templates, shipped as a ``json_script`` config key by four views, hung on
elements as ``data-csrf`` by four more templates, read back out of a rendered
form's hidden input by one module, and aliased to a module constant by
another. A page that used one route owed nothing to the others, so a page
could easily grow JavaScript that posts with no token at all -- or keep
working only because a form happened to be on it.

base.html now carries the token once, in a meta tag, and ``api.js``'s
``csrfToken()`` is the only thing that reads it. These tests are the guard on
that: they fail the day a sixth route appears.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from common.test_helpers import BaseTestCase

# Where a template could smuggle the token into JavaScript again. Each pattern
# is the shape the audit found, not a guess.
FORBIDDEN_TRANSPORTS = (
    # data-csrf="{{ csrf_token }}" on an element for JS to read back off.
    re.compile(r"data-csrf\s*="),
    # 'csrfToken': '{{ csrf_token }}' or const x = '{{ csrf_token }}' --
    # the token interpolated into a JavaScript string literal. The {% csrf_token %}
    # *tag*, which renders a form's hidden input, is a different thing and stays.
    re.compile(r"""['"]\{\{\s*csrf_token\s*\}\}['"]"""),
)

PROJECT_TEMPLATES = sorted(
    path
    for path in Path(settings.BASE_DIR).rglob("templates/**/*.html")
    if ".venv" not in path.parts and "node_modules" not in path.parts
)


class NoTemplateShipsTheTokenToJavaScriptItselfTest(SimpleTestCase):
    def test_the_corpus_is_not_empty(self):
        """A sweep over no files is not a pass."""
        self.assertGreater(len(PROJECT_TEMPLATES), 20)

    def test_no_template_hangs_the_token_on_an_element_or_a_literal(self):
        offenders = []
        for path in PROJECT_TEMPLATES:
            if (
                path.name == "base.html"
                and path.parent == Path(settings.BASE_DIR) / "templates"
            ):
                continue  # the one meta tag, checked below
            text = path.read_text()
            for pattern in FORBIDDEN_TRANSPORTS:
                if pattern.search(text):
                    offenders.append(
                        f"{path.relative_to(settings.BASE_DIR)}: {pattern.pattern}"
                    )
        self.assertEqual(
            offenders,
            [],
            "The CSRF token has one route to JavaScript: base.html's meta tag, "
            "read by api.js's csrfToken(). These templates take another:\n  "
            + "\n  ".join(offenders),
        )

    def test_no_module_reads_the_token_out_of_a_rendered_form(self):
        """character-edit.js used to; api.js's fallback is the only place left."""
        js_root = Path(settings.BASE_DIR) / "static" / "js"
        offenders = [
            str(path.relative_to(settings.BASE_DIR))
            for path in sorted(js_root.glob("*.js"))
            if path.name != "api.js" and "csrfmiddlewaretoken" in path.read_text()
        ]
        self.assertEqual(offenders, [])


class EveryPageCarriesTheTokenExactlyOnceTest(BaseTestCase):
    """Pages that extend base.html get the meta tag, and only one of them."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user("tokenbearer")
        self.login_as(self.user)

    def test_a_page_carries_one_meta_tag_with_a_real_token(self):
        html = self.client.get("/tarmar/characters/").content.decode()
        tags = re.findall(r'<meta name="csrf-token" content="([^"]*)">', html)
        self.assertEqual(len(tags), 1, "expected exactly one csrf-token meta tag")
        self.assertTrue(tags[0], "the meta tag carries no token")

    def test_the_meta_tag_precedes_api_js(self):
        """csrfToken() reads the tag, so it must already be in the document."""
        html = self.client.get("/tarmar/characters/").content.decode()
        self.assertLess(
            html.index('name="csrf-token"'),
            html.index("js/api.js"),
            "the token must be in the document before api.js's consumers run",
        )
