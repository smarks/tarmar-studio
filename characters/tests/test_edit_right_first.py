"""A stranger is refused before the body is read, on every character endpoint (#182).

Every ``<pk>`` endpoint that writes a character used to parse and validate its
body first and check the edit right inside the change afterwards, so a caller
with no right to the character was answered 400 for a bad body and learned
what a good one looks like before the 403. ``characters.views.access.
edit_right_first`` now stands in front of each, and this module holds every
such endpoint to the order by reading them off ``characters.urls`` rather than
listing them: an endpoint added without the decorator that validates its body
first answers a stranger 400, and fails here.

The rule itself is unchanged and sits where it did: a player writes their own
character, a Game Master (``can_manage_users``) writes anyone's.
"""

from django.urls import URLPattern, reverse

from characters import urls as character_urls
from characters.views.characters import CharacterEditView
from common.test_helpers import BaseTestCase, create_character

#: A body no endpoint accepts: not JSON, not a form.
BAD_BODY = b"{this is not json"
#: What the roll endpoint's ``kind`` argument is filled with.
ROLL_KIND = "attributes"
#: The one endpoint that wants a soft-deleted character.
DELETED_ROW_ENDPOINTS = frozenset({"restore"})
#: The endpoints only a Game Master may use at all (``can_manage_users``): a
#: death, a revival, the pools, coin and mana. A caller who is not one is
#: refused before the row is read (``edit_right_first(game_master_only=True)``),
#: so the answer is the same 403 for an existing character and a missing one.
GAME_MASTER_ONLY_ENDPOINTS = frozenset(
    {"mark_dead", "revive", "set_pools", "move_coin", "grant_mana"}
)


def pk_endpoints() -> list[str]:
    """The URL names of every ``<int:pk>`` endpoint in ``characters.urls``
    except the editor page, which is a GET."""
    names = []
    for pattern in character_urls.urlpatterns:
        if not isinstance(pattern, URLPattern) or "<int:pk>" not in str(
            pattern.pattern
        ):
            continue
        if getattr(pattern.callback, "view_class", None) is CharacterEditView:
            continue
        names.append(pattern.name)
    return names


def endpoint_url(name: str, pk: int) -> str:
    kwargs: dict[str, object] = {"pk": pk}
    if name == "roll":
        kwargs["kind"] = ROLL_KIND
    return reverse(f"characters:{name}", kwargs=kwargs)


class EditRightComesFirstTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user(username="owner182")
        self.stranger = self.create_user(username="stranger182")
        self.game_master = self.create_dm(username="gamemaster182")

    def _targets(self):
        live = create_character(self.owner, name="Live")
        deleted = create_character(self.owner, name="Gone", is_deleted=True)
        return live, deleted

    def _post(self, name: str, pk: int):
        return self.client.post(
            endpoint_url(name, pk), BAD_BODY, content_type="application/json"
        )

    def test_the_corpus_is_the_whole_url_module(self):
        names = pk_endpoints()
        self.assertGreaterEqual(len(names), 29)
        self.assertIn("update_field", names)
        self.assertIn("restore", names)

    def test_a_stranger_is_refused_with_403_before_any_body_is_read(self):
        live, deleted = self._targets()
        self.switch_to(self.stranger)
        for name in pk_endpoints():
            with self.subTest(endpoint=name):
                target = deleted if name in DELETED_ROW_ENDPOINTS else live
                response = self._post(name, target.pk)
                self.assertEqual(response.status_code, 403, response.content[:200])

    def test_a_missing_character_is_404_before_any_body_is_read(self):
        """Except at a Game Master's own endpoints, where a player is refused
        before the lookup and so learns nothing from the status."""
        self.switch_to(self.stranger)
        for name in pk_endpoints():
            with self.subTest(endpoint=name):
                expected = 403 if name in GAME_MASTER_ONLY_ENDPOINTS else 404
                self.assertEqual(self._post(name, 987654321).status_code, expected)

    def test_a_game_master_s_endpoint_is_no_existence_oracle(self):
        """A player gets the one answer for their own character, another's
        and none at all; a Game Master is told which is missing."""
        live, _deleted = self._targets()
        for name in GAME_MASTER_ONLY_ENDPOINTS:
            with self.subTest(endpoint=name):
                self.switch_to(self.owner)
                self.assertEqual(self._post(name, live.pk).status_code, 403)
                self.assertEqual(self._post(name, 987654321).status_code, 403)
                self.switch_to(self.game_master)
                self.assertEqual(self._post(name, 987654321).status_code, 404)

    def test_the_owner_and_a_game_master_reach_the_body(self):
        """Past the gate, the bad body is theirs to be told about: never 403,
        except at the Game Master's own endpoints, where an owner who is not
        one is still refused by the view."""
        for user in (self.owner, self.game_master):
            self.switch_to(user)
            for name in pk_endpoints():
                with self.subTest(user=user.username, endpoint=name):
                    live, deleted = self._targets()
                    target = deleted if name in DELETED_ROW_ENDPOINTS else live
                    response = self._post(name, target.pk)
                    game_master_only_refusal = (
                        name in GAME_MASTER_ONLY_ENDPOINTS and user is self.owner
                    )
                    if game_master_only_refusal:
                        self.assertEqual(response.status_code, 403)
                        continue
                    self.assertNotEqual(response.status_code, 403, response.content)
                    self.assertNotEqual(response.status_code, 404)
                    self.assertNotIn(b'name="passphrase"', response.content)
