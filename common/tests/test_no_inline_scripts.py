"""The character and user list pages carry markup only (#338 items 18 and 19).

Their behaviour used to sit in inline <script> blocks and onclick/onchange
attributes, where Jest could not reach it. It lives in static/js now; these
tests keep it there.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = [
    "characters/templates/characters/character_list.html",
    "characters/templates/characters/deleted_characters.html",
    "accounts/templates/accounts/users_list.html",
]


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
