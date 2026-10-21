"""The parties app's refusals are configuration, not code (issue #136).

The party pages' refusals and the refusal of a disbanded party chosen by hand
are read from ``config/parties-refusals.toml``
(``settings.PARTIES_REFUSAL_WORDS``). The shipped file is Spencer's to edit, so
these tests pin none of its sentences: they run under fixed words in which each
sentence is its own table and key.
"""

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from common.test_helpers import BaseTestCase
from common.tests.refusal_scan import literal_refusals, literal_returns
from common.wording import fixed_wording
from parties.models import Party, disbanded_refusal
from parties.refusal_words import PartiesRefusalWords, load_refusal_words

ROOT = Path(settings.BASE_DIR)

#: Run under words in which each sentence is its own table and key.
with_fixed_refusals = override_settings(
    PARTIES_REFUSAL_WORDS=fixed_wording(PartiesRefusalWords)
)


class ShippedRefusalWordsTest(SimpleTestCase):
    def test_the_shipped_file_loads_and_settings_hold_the_named_file(self):
        load_refusal_words(ROOT / "config" / "parties-refusals.toml")
        self.assertEqual(
            load_refusal_words(settings.PARTIES_REFUSAL_WORDS_FILE),
            settings.PARTIES_REFUSAL_WORDS,
        )


class NothingWrittenInCodeTest(SimpleTestCase):
    def test_no_refusal_is_a_literal(self):
        self.assertEqual(
            literal_refusals(
                ROOT / "parties/views.py",
                {"_field_refusal", "_detail_context", "PermissionDenied"},
            ),
            [],
        )
        self.assertEqual(
            literal_returns(ROOT / "parties/models.py", {"disbanded_refusal"}), []
        )


@with_fixed_refusals
class TheWordsFollowTheConfigurationTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_player(username="words-owner")
        self.party = Party.objects.create(name="The Grey Company", owner=self.owner)

    def test_a_disbanded_party_is_refused_in_the_configured_words(self):
        self.assertEqual(
            disbanded_refusal(self.party), "models.disbanded The Grey Company"
        )

    def test_the_autosave_refuses_in_the_configured_words(self):
        self.login_as(self.owner)
        url = reverse("parties:update_field", kwargs={"pk": self.party.pk})
        for body, refusal in (
            ({"field": "owner", "value": "x"}, "views.field_not_editable"),
            ({"field": "name", "value": 7}, "views.text_value_required"),
            ([], "views.request_not_json"),
        ):
            with self.subTest(refusal=refusal):
                response = self.post_json(url, body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], refusal)
