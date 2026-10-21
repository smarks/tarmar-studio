"""The sentence every app's JSON endpoints answer a server failure in is
configuration (issues #113, #139).

It is read from ``config/common-refusals.toml``
(``settings.COMMON_REFUSAL_WORDS``) at startup. The shipped file is Spencer's
to edit, so no test pins its sentence; the endpoint tests compare against the
configured words.
"""

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from common.refusal_words import load_refusal_words
from common.tests.refusal_scan import literal_refusals

ROOT = Path(settings.BASE_DIR)


class ShippedRefusalWordsTest(SimpleTestCase):
    def test_the_shipped_file_loads_and_settings_hold_the_named_file(self):
        """The shipped file passes every check; the settings hold whatever file
        ``COMMON_REFUSAL_WORDS_FILE`` names, which a deployment may change."""
        load_refusal_words(ROOT / "config" / "common-refusals.toml")
        self.assertEqual(
            load_refusal_words(settings.COMMON_REFUSAL_WORDS_FILE),
            settings.COMMON_REFUSAL_WORDS,
        )

    def test_the_decorator_writes_no_sentence_of_its_own(self):
        self.assertEqual(
            literal_refusals(ROOT / "common" / "decorators.py", {"_json_error"}), []
        )
