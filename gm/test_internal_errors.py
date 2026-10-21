"""A Games Master endpoint's server failure answers JSON (issue #139).

``gm.views_records``, ``gm.views`` and ``gm.luck`` let a ``RuntimeError``
reach the GM page's script as Django's HTML 500; they answer in the fixed
sentence now, as the battle endpoints have since #113, through
``common.decorators.internal_errors_logged``.
"""

from unittest import mock

from django.urls import reverse

from common.helpers import SCRIPT_CALLER_HEADERS
from common.refusal_words import refusal_words as common_refusal_words
from common.test_helpers import (
    BaseTestCase,
    assert_failure_answered_in_json,
    logged_exception,
)

from .models import GMSession, GMTurnRecord
from .turn_record import running_turn_for

#: The failing service's text: in the log, never on the page.
SECRET = "ledger row 4242 at /srv/tarmar/gm/hand_records.py"


class RecordsEndpointFailureTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm(username="failing-gm")
        self.login_as(self.game_master)
        self.session = GMSession.objects.create(
            name="The Wolf Road", user=self.game_master
        )

    def test_a_failing_verdict_lookup_answers_the_fixed_sentence(self):
        with mock.patch(
            "gm.views_records.filed_verdict", side_effect=RuntimeError(SECRET)
        ):
            assert_failure_answered_in_json(
                self,
                lambda: self.client.get(
                    reverse("gm:fight_verdict"), {"session_id": self.session.pk}
                ),
                logger_name="gm.views_records",
                secret=SECRET,
            )

    def test_a_failing_hand_edit_answers_the_fixed_sentence(self):
        with mock.patch(
            "gm.views_records.hand_records.correct_recap",
            side_effect=RuntimeError(SECRET),
        ):
            assert_failure_answered_in_json(
                self,
                lambda: self.post_json(
                    reverse("gm:recap", args=[self.session.pk]),
                    {"change": "edit", "text": "They met a wolf."},
                ),
                logger_name="gm.views_records",
                secret=SECRET,
            )


class SessionEndpointFailureTest(BaseTestCase):
    """``gm.views`` and ``gm.luck``'s own JSON endpoints, the same way."""

    def setUp(self):
        super().setUp()
        self.player = self.create_player(username="failing-player")
        self.login_as(self.player)
        self.session = GMSession.objects.create(name="The Wolf Road", user=self.player)

    def test_a_failing_rename_answers_the_fixed_sentence(self):
        with mock.patch("gm.views._get_gm_session", side_effect=RuntimeError(SECRET)):
            assert_failure_answered_in_json(
                self,
                lambda: self.post_json(
                    reverse("gm:save"),
                    {"session_id": self.session.pk, "name": "The Wolf Road"},
                ),
                logger_name="gm.views",
                secret=SECRET,
            )

    def test_a_failing_luck_spend_answers_the_fixed_sentence(self):
        with mock.patch("gm.luck.spend_luck", side_effect=RuntimeError(SECRET)):
            assert_failure_answered_in_json(
                self,
                lambda: self.post_json(
                    reverse("gm:spend_luck", args=[self.session.pk]),
                    {"character_id": 1},
                ),
                logger_name="gm.luck",
                secret=SECRET,
            )

    def test_the_mode_switch_answers_its_script_in_json_and_its_form_with_the_page(
        self,
    ):
        """The mode switch serves the page's script and a plain form post."""
        url = reverse("gm:mode")
        body = {"session_id": self.session.pk, "mode": "play"}
        with mock.patch("gm.views._get_gm_session", side_effect=RuntimeError(SECRET)):
            assert_failure_answered_in_json(
                self,
                lambda: self.client.post(url, body, headers=SCRIPT_CALLER_HEADERS),
                logger_name="gm.views",
                secret=SECRET,
            )

            self.client.raise_request_exception = False
            with (
                self.assertNoLogs("gm.views", "ERROR"),
                self.assertLogs("django.request", "ERROR") as request_logs,
            ):
                response = self.client.post(url, body)

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("application/json", response["Content-Type"])
        self.assertNotIn("4242", response.content.decode())
        self.assertIn(SECRET, str(logged_exception(request_logs.records[0])))


class StreamFailureTest(BaseTestCase):
    """The chat's stream: what fails before the stream is returned (#139)."""

    def setUp(self):
        super().setUp()
        self.player = self.create_player(username="failing-streamer")
        self.login_as(self.player)
        self.session = GMSession.objects.create(name="The Wolf Road", user=self.player)

    def send(self):
        return self.post_json(
            reverse("gm:stream"),
            {"session_id": self.session.pk, "message": "We open the door."},
        )

    def test_a_failure_before_the_claim_answers_the_fixed_sentence(self):
        with mock.patch("gm.views._get_gm_session", side_effect=RuntimeError(SECRET)):
            assert_failure_answered_in_json(
                self, self.send, logger_name="gm.views", secret=SECRET
            )
        self.assertFalse(GMTurnRecord.objects.exists())

    def test_a_failure_after_the_claim_releases_it(self):
        """The claim is not left running until it goes stale: the record says
        what the page was told, and the session takes the next message."""
        with mock.patch("gm.views._plan_turn", side_effect=RuntimeError(SECRET)):
            assert_failure_answered_in_json(
                self, self.send, logger_name="gm.views", secret=SECRET
            )

        record = GMTurnRecord.objects.get(session=self.session)
        self.assertEqual(record.outcome, GMTurnRecord.OUTCOME_ERROR)
        self.assertEqual(record.error, common_refusal_words().decorators.request_failed)
        self.assertIsNone(running_turn_for(self.player, self.session.pk))
