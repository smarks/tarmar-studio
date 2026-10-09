"""Page widths come from base.css's utilities (#338 item 24).

Nine pages set their column width nine ways: bare ``main {}`` rules in their
own sheets or inline <style> blocks, which only won by loading after base.css,
and per-page classes. A page names one of base.css's ``main.page-width-*``
classes in ``{% block main_attrs %}`` now; these tests keep it that way.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
CSS_DIRECTORY = ROOT / "static" / "css"
TEMPLATE_ROOTS = [ROOT / "templates", *ROOT.glob("*/templates")]


def _without_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _rules(css: str):
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", _without_comments(css)):
        for selector in match.group(1).split(","):
            yield " ".join(selector.split()), match.group(2)


class PageWidthUtilitiesTest(SimpleTestCase):
    def test_no_sheet_but_base_sets_a_main_width(self):
        for sheet in sorted(CSS_DIRECTORY.glob("*.css")):
            if sheet.name == "base.css":
                continue
            with self.subTest(sheet=sheet.name):
                offenders = [
                    selector
                    for selector, body in _rules(sheet.read_text())
                    if "max-width" in body
                    and re.search(r"(^|\s)(main|\.[\w-]+-main)$", selector)
                ]
                self.assertEqual(offenders, [])

    def test_no_template_styles_main_inline(self):
        templates = [t for root in TEMPLATE_ROOTS for t in root.rglob("*.html")]
        self.assertGreater(len(templates), 20)
        offenders = [
            str(template.relative_to(ROOT))
            for template in templates
            if re.search(r"^\s*main\s*\{", template.read_text(), re.MULTILINE)
        ]
        self.assertEqual(offenders, [])

    def test_every_page_width_a_template_names_is_defined(self):
        base = (CSS_DIRECTORY / "base.css").read_text()
        defined = set(re.findall(r"main\.(page-width-[\w]+)\s*\{", base))
        used = {
            name
            for root in TEMPLATE_ROOTS
            for template in root.rglob("*.html")
            for name in re.findall(r"page-width-[\w]+", template.read_text())
        }
        self.assertTrue(used)
        self.assertEqual(used - defined, set())
