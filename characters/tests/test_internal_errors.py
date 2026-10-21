"""A character endpoint's server failure answers JSON (issue #139).

The battle endpoints answered an internal failure to a script caller in the
fixed sentence since #113; the sheet's endpoints let a ``RuntimeError`` reach
the page as Django's HTML 500, which the sheet's script could only report as a
bare status. They share ``common.decorators.internal_errors_logged`` now.
"""

from unittest import mock

from django.urls import reverse

from common.helpers import SCRIPT_CALLER_HEADERS
from common.test_helpers import (
    BaseTestCase,
    assert_failure_answered_in_json,
    create_character,
    logged_exception,
)

#: The failing service's text: in the log, never on the page.
SECRET = "engine state 4242 at /srv/tarmar/characters/services.py"


class CharacterEndpointFailureTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_player(username="failing-owner")
        self.login_as(self.owner)
        self.character = create_character(self.owner, name="Aldwin")

    def test_a_failing_roll_answers_the_fixed_sentence(self):
        """A bodyless post, as the sheet's Fatigue button sends it."""
        with mock.patch(
            "characters.views.characters.services.roll_fatigue",
            side_effect=RuntimeError(SECRET),
        ):
            assert_failure_answered_in_json(
                self,
                lambda: self.client.post(
                    reverse("characters:roll_fatigue", args=[self.character.pk])
                ),
                logger_name="characters.views.characters",
                secret=SECRET,
            )

    def test_a_failing_catalog_answers_the_fixed_sentence(self):
        """The equipment picker's GET is JSON-only too."""
        with mock.patch(
            "characters.views.equipment.catalog_by_group",
            side_effect=TypeError(SECRET),
        ):
            assert_failure_answered_in_json(
                self,
                lambda: self.client.get(reverse("characters:equipment_catalog")),
                logger_name="characters.views.equipment",
                secret=SECRET,
            )

    def test_a_script_deleting_a_character_hears_the_fixed_sentence(self):
        """The delete serves a form post and a script: a script caller gets
        JSON, while the edit page's form post is left to Django's error page."""
        url = reverse("characters:delete", args=[self.character.pk])
        with mock.patch(
            "characters.views.characters._log_character_event",
            side_effect=RuntimeError(SECRET),
        ):
            assert_failure_answered_in_json(
                self,
                lambda: self.client.post(url, headers=SCRIPT_CALLER_HEADERS),
                logger_name="characters.views.characters",
                secret=SECRET,
            )

            self.client.raise_request_exception = False
            with (
                self.assertNoLogs("characters.views.characters", "ERROR"),
                self.assertLogs("django.request", "ERROR") as request_logs,
            ):
                response = self.client.post(url)

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("application/json", response["Content-Type"])
        self.assertNotIn("4242", response.content.decode())
        self.assertIn(SECRET, str(logged_exception(request_logs.records[0])))
