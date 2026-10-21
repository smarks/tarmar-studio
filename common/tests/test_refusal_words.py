"""The sentence every app's JSON endpoints answer a server failure in is
configuration (issues #113, #139).

It is read from ``config/common-refusals.toml``
(``settings.COMMON_REFUSAL_WORDS``) at startup. The shipped file is Spencer's
to edit, so no test pins its sentence; the endpoint tests compare against the
configured words.
"""

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from characters.constants import too_long
from common.refusal_words import CommonRefusalWords, load_refusal_words
from common.seeds import InvalidSeed, parse_seed
from common.tests.refusal_scan import literal_refusals, literal_returns
from common.wording import fixed_wording

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

    def test_the_shared_refusals_write_no_sentence_of_their_own(self):
        """The length refusal and the seed's are configuration (#136)."""
        self.assertEqual(
            literal_refusals(ROOT / "common" / "seeds.py", {"InvalidSeed"}), []
        )
        self.assertEqual(
            literal_returns(ROOT / "characters" / "constants.py", {"too_long"}), []
        )


@override_settings(COMMON_REFUSAL_WORDS=fixed_wording(CommonRefusalWords))
class TheWordsFollowTheConfigurationTest(SimpleTestCase):
    def test_the_shared_refusals_are_the_configured_ones(self):
        self.assertEqual(too_long("Name", "abcd", 3), "text_limits.too_long Name 3")
        self.assertEqual(too_long("Name", "abc", 3), "")
        with self.assertRaisesMessage(InvalidSeed, "seeds.not_whole_number abc"):
            parse_seed("abc")
