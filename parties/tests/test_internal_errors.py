"""A party endpoint's server failure answers JSON (issue #139).

The party editor's autosave let a ``RuntimeError`` reach ``autosave.js`` as
Django's HTML 500; it answers in the fixed sentence now, as the battle
endpoints have since #113, through ``common.decorators.internal_errors_logged``.
"""

from unittest import mock

from django.urls import reverse

from common.test_helpers import BaseTestCase, assert_failure_answered_in_json
from parties.models import Party

#: The failing service's text: in the log, never on the page.
SECRET = "party row 4242 at /srv/tarmar/parties/forms.py"


class PartyEndpointFailureTest(BaseTestCase):
    def test_a_failing_autosave_answers_the_fixed_sentence(self):
        owner = self.create_player(username="failing-bandleader")
        self.login_as(owner)
        party = Party.objects.create(name="The Grey Company", owner=owner)
        with mock.patch(
            "parties.views.PartyForm.save", side_effect=RuntimeError(SECRET)
        ):
            assert_failure_answered_in_json(
                self,
                lambda: self.post_json(
                    reverse("parties:update_field", kwargs={"pk": party.pk}),
                    {"field": "name", "value": "The Greyer Company"},
                ),
                logger_name="parties.views",
                secret=SECRET,
            )
