"""Another account's profile refuses a stranger before reading the body (#182).

``profile_update_field`` parsed the JSON body first, so a player posting a
bad body at another account's profile was told about the body (400) rather
than refused (403). The manager check -- ``can_manage_users``, the same line
the library draws -- now comes first when a ``user_id`` names another account;
a player's own profile still reads the body, since it is theirs to edit.
"""

from django.urls import reverse

from common.test_helpers import BaseTestCase

BAD_BODY = b"{this is not json"
#: A body the view reads and refuses: no such field (the library's 400).
UNKNOWN_FIELD_BODY = b'{"field": "no-such-field", "value": "x"}'


class ProfileUpdateOrderTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.player = self.create_player(username="player182")
        self.other = self.create_user(username="other182")
        self.game_master = self.create_dm(username="gamemaster182")

    def _post(self, user, user_id=None, body: bytes = BAD_BODY):
        self.switch_to(user)
        url = (
            reverse("user_profile_update_field", args=[user_id])
            if user_id is not None
            else reverse("profile_update_field")
        )
        return self.client.post(url, body, content_type="application/json")

    def test_a_player_is_refused_another_account_before_the_body_is_read(self):
        self.assertEqual(self._post(self.player, self.other.pk).status_code, 403)

    def test_a_manager_and_an_owner_reach_the_body(self):
        """Past the gate the body is read, and an unknown field is refused as
        the library refuses it."""
        self.assertEqual(
            self._post(self.game_master, self.other.pk, UNKNOWN_FIELD_BODY).status_code,
            400,
        )
        self.assertEqual(
            self._post(self.player, body=UNKNOWN_FIELD_BODY).status_code, 400
        )
