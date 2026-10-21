"""An account endpoint's server failure answers JSON (issue #139).

The profile autosave and the users list's delete let a ``RuntimeError`` reach
their scripts as Django's HTML 500; they answer in the fixed sentence now, as
the battle endpoints have since #113, through
``common.decorators.internal_errors_logged``.
"""

from unittest import mock

from django.urls import reverse

from common.test_helpers import BaseTestCase, assert_failure_answered_in_json

#: The failing service's text: in the log, never on the page.
SECRET = "account row 4242 at /srv/tarmar/origami_auth/views.py"


class AccountEndpointFailureTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.create_admin(username="failing-admin")
        self.login_as(self.admin)

    def test_a_failing_profile_autosave_answers_the_fixed_sentence(self):
        with mock.patch(
            "accounts.views.auth_views.profile_update_field",
            side_effect=RuntimeError(SECRET),
        ):
            assert_failure_answered_in_json(
                self,
                lambda: self.post_json(
                    reverse("profile_update_field"),
                    {"field": "real_name", "value": "Spencer"},
                ),
                logger_name="accounts.views",
                secret=SECRET,
            )

    def test_a_failing_delete_answers_the_fixed_sentence(self):
        """A bodyless post, as the users list sends it."""
        player = self.create_player(username="doomed-player")
        with mock.patch(
            "accounts.views.auth_views.delete_user",
            side_effect=RuntimeError(SECRET),
        ):
            assert_failure_answered_in_json(
                self,
                lambda: self.client.post(reverse("delete_user", args=[player.pk])),
                logger_name="accounts.views",
                secret=SECRET,
            )
