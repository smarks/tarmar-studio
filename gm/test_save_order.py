"""A session that is not yours is a 404 before anything else in the body (#182).

``gm_save`` validated the name, and ``gm_stream`` the message, before looking
the session up, so a bad name or message aimed at another user's session was
answered 400 and a good one 404. The id has to come from the body, but nothing
else in it is judged until the session is known to be the caller's.
"""

import json

from common.test_helpers import BaseTestCase

from .models import GMSession
from .refusal_words import refusal_words

SAVE_URL = "/tarmar/gm/save/"
STREAM_URL = "/tarmar/gm/stream/"


class SaveOrderTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user(username="owner182")
        self.stranger = self.create_user(username="stranger182")
        self.session = GMSession.objects.create(
            user=self.owner, name="Keep", mode="play"
        )

    def _save(self, user, body: dict):
        self.switch_to(user)
        return self.client.post(
            SAVE_URL, json.dumps(body), content_type="application/json"
        )

    def test_a_stranger_is_answered_404_before_the_name_is_judged(self):
        for name in ("", "x" * 10_000, 42):
            with self.subTest(name=name):
                response = self._save(
                    self.stranger, {"session_id": self.session.pk, "name": name}
                )
                self.assertEqual(response.status_code, 404)
                self.assertEqual(
                    response.json()["error"], refusal_words().views.session_not_found
                )

    def test_the_owner_is_told_about_the_name(self):
        response = self._save(self.owner, {"session_id": self.session.pk, "name": ""})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"], refusal_words().views.session_needs_name
        )


class StreamOrderTest(BaseTestCase):
    """A message to a session that is not yours is a 404 whatever the message (#182).

    ``gm_stream`` judged the message -- text or not, too long -- before looking
    the session up, so a stranger was told about their message (400) rather
    than that the session is not theirs (404). No model is called: the
    refusal comes before any turn is claimed.
    """

    def setUp(self):
        super().setUp()
        self.owner = self.create_user(username="owner182s")
        self.stranger = self.create_user(username="stranger182s")
        self.session = GMSession.objects.create(
            user=self.owner, name="Keep", mode="play"
        )

    def _send(self, user, body: dict):
        self.switch_to(user)
        return self.client.post(
            STREAM_URL, json.dumps(body), content_type="application/json"
        )

    def test_a_stranger_is_answered_404_before_the_message_is_judged(self):
        for message in (42, "x" * 100_000):
            with self.subTest(message=message):
                response = self._send(
                    self.stranger, {"session_id": self.session.pk, "message": message}
                )
                self.assertEqual(response.status_code, 404)
                self.assertEqual(
                    response.json()["error"], refusal_words().views.session_not_found
                )

    def test_the_owner_is_told_about_the_message(self):
        response = self._send(
            self.owner, {"session_id": self.session.pk, "message": 42}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"], refusal_words().views.message_not_text
        )
