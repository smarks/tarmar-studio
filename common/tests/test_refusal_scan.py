"""The refusal scan finds every way a refusal's words can be fixed in code (#97)."""

import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from common.tests.refusal_scan import literal_refusals

SOURCE = """
WORDS = "a sentence written in code"
NOT_WORDS = 3


def refuse(said, name, status):
    raise Refusal("a literal")
    raise Refusal(f"an f-string for {name}")
    raise Refusal(WORDS)
    raise Refusal(message=WORDS)
    raise Refusal(WORDS.format(name=name))
    raise Refusal("{name} refused".format(name=name))
    raise Refusal(WORDS + name)
    raise Refusal("refused: %s" % name)
    raise Refusal(said.refused)
    raise Refusal(said.refused.format(name=name))
    raise Refusal(said.refused, field="name")
    raise Refusal(NOT_WORDS, status=status)
"""


class LiteralRefusalsTest(SimpleTestCase):
    def test_each_fixed_shape_is_found_and_configured_words_are_not(self):
        with tempfile.TemporaryDirectory() as scratch:
            module = Path(scratch) / "refusing.py"
            module.write_text(SOURCE, encoding="utf-8")
            found = literal_refusals(
                module, {"Refusal"}, structure_keywords=frozenset({"field"})
            )
        self.assertEqual(found, [f"refusing.py:{line}" for line in range(7, 15)])
