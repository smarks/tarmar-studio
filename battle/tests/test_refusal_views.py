"""Battle views show a refusal's words and hide an internal error's (#39).

Every battle view that calls a service used to catch ``ValueError`` and
answer ``str(error)``, so an internal check's text -- "no combatant 4242 in
this battle's state", "unknown rules profile ..." -- reached the player word
for word. A refusal is now a :class:`battle.refusals.BattleRefusal`, the only
type a view shows; any other ``ValueError`` is logged with its traceback and
answered with a fixed message (as #13 settled for the advance view).

Each endpoint is driven with its service made to fail: with internal
failures carrying a recognisable string, and with a refusal. Since #113 an
internal failure is any exception, not only a ``ValueError``: a
``RuntimeError`` or ``KeyError`` from the engine gave a JSON caller Django's
HTML 500 page, which ``battle.js`` could only report as a bare status.
"""

import json
from dataclasses import dataclass, field
from unittest import mock

from django.core.exceptions import BadRequest, PermissionDenied, SuspiciousOperation
from django.http import Http404
from django.http.multipartparser import MultiPartParserError
from django.test import override_settings
from django.urls import reverse

from battle import control, views
from battle.models import Battle, BattleDraft
from battle.refusal_words import refusal_words
from battle.refusals import BattleRefusal
from battle.services import advance_turn, create_battle
from common.helpers import SCRIPT_CALLER_HEADERS
from common.refusal_words import refusal_words as common_refusal_words
from common.test_helpers import BaseTestCase, logged_exception

#: An internal check's text, as the issue quoted it: ids and state, not words
#: for a player.
INTERNAL_TEXT = "no combatant 4242 in this battle's state"

#: A refusal's words, which the player is meant to read.
REFUSAL_TEXT = "The muster has no room for that"

#: The internal failures every endpoint keeps off the page: an internal
#: check's ``ValueError`` (#39), and the engine's other errors (#113).
INTERNAL_FAILURES = (ValueError, RuntimeError, KeyError)


@dataclass(frozen=True)
class Endpoint:
    """One battle endpoint, the service it calls, and how a client posts it.

    Attributes:
        url_name: The ``battle:`` URL name.
        service: The name the view calls its service by, in ``battle.views``.
        on_draft: The URL takes the draft's pk rather than the battle's.
        with_combatant: The URL also takes a combatant's pk.
        answers_json: The answer is JSON; otherwise an HTML page.
        posts_json: The client posts a JSON body, as ``battle.js`` does;
            otherwise a form post, or none at all for a JSON-only endpoint
            that ``postJson`` calls without a body.
        body: The post's fields; ``"combatant"`` stands for the
            combatant's pk.
    """

    url_name: str
    service: str
    on_draft: bool = False
    with_combatant: bool = False
    answers_json: bool = True
    posts_json: bool = True
    body: dict = field(default_factory=dict)


ENDPOINTS = (
    Endpoint(
        "setup-update",
        "_apply_draft_terms",
        on_draft=True,
        body={"field": "name", "value": "Pit"},
    ),
    Endpoint(
        "setup-add",
        "add_draft_entry",
        on_draft=True,
        answers_json=False,
        body={"kind": "fighter", "archetype": "brawler"},
    ),
    Endpoint(
        "setup-remove",
        "remove_draft_entry",
        on_draft=True,
        answers_json=False,
        body={"index": "0"},
    ),
    Endpoint(
        "setup-import-party",
        "import_party_into_draft",
        on_draft=True,
        answers_json=False,
        body={"party_id": "1"},
    ),
    Endpoint(
        "setup-team-add",
        "add_draft_team",
        on_draft=True,
        answers_json=False,
        body={"team_name": "Red"},
    ),
    Endpoint(
        "setup-team-remove",
        "remove_draft_team",
        on_draft=True,
        answers_json=False,
        body={"team_name": "Red"},
    ),
    Endpoint(
        "setup-entry-team",
        "set_entry_team",
        on_draft=True,
        answers_json=False,
        body={"index": "0", "team": ""},
    ),
    Endpoint(
        "setup-start",
        "start_draft",
        on_draft=True,
        answers_json=False,
        body={"name": "Pit"},
    ),
    Endpoint("control", "set_battle_control_mode", body={"control_mode": "manual"}),
    Endpoint(
        "hth-agreement",
        "set_hth_agreement",
        body={"scope": "battle", "agreed": True},
    ),
    Endpoint(
        "combatant-control",
        "set_combatant_control_mode",
        with_combatant=True,
        body={"control_mode": "manual"},
    ),
    Endpoint("restart", "restart_battle", posts_json=False),
    Endpoint("resnapshot", "resnapshot_pools"),
    Endpoint(
        "rename",
        "rename_combatant",
        body={"combatant_id": "combatant", "name": "Bob"},
    ),
    Endpoint("start", "start_battle", answers_json=False),
    Endpoint(
        "seat-claim",
        "claim_seat",
        with_combatant=True,
        answers_json=False,
        body={"archetype": "brawler"},
    ),
    Endpoint("seat-release", "release_seat", with_combatant=True, answers_json=False),
    Endpoint(
        "seat-ready",
        "set_seat_ready",
        with_combatant=True,
        answers_json=False,
        body={"ready": "on"},
    ),
    Endpoint("seat-take", "take_seat", with_combatant=True, posts_json=False),
    Endpoint("seat-return", "return_seat", with_combatant=True, posts_json=False),
    Endpoint(
        "act",
        "record_player_action",
        body={"combatant_id": "combatant", "turn": 1},
    ),
)


class BattleViewRefusalTest(BaseTestCase):
    """Every battle endpoint around a service, failing both ways (#39)."""

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm()
        self.login_as(self.game_master)
        self.battle = create_battle(self.game_master, name="Pit", count=2, seed=3)
        self.combatant = self.battle.combatants.first()
        self.draft = BattleDraft.objects.create(created_by=self.game_master, name="Pit")

    def post(self, endpoint: Endpoint, failure: Exception, headers: dict | None = None):
        """Post ``endpoint`` while its service raises ``failure``.

        ``headers`` go with the post, as a script caller's would.
        """
        owner = self.draft if endpoint.on_draft else self.battle
        args = [owner.pk]
        if endpoint.with_combatant:
            args.append(self.combatant.pk)
        body = {
            key: self.combatant.pk if value == "combatant" else value
            for key, value in endpoint.body.items()
        }
        url = reverse(f"battle:{endpoint.url_name}", args=args)
        with mock.patch.object(views, endpoint.service, side_effect=failure):
            if endpoint.answers_json and endpoint.posts_json:
                return self.client.post(
                    url,
                    json.dumps(body),
                    content_type="application/json",
                    headers=headers,
                )
            return self.client.post(url, body, headers=headers)

    def test_an_internal_error_is_logged_and_not_shown(self):
        # A form post's error is raised on to Django, which logs it and
        # renders the site's 500 page; the client answers with that page
        # rather than raising it into the test.
        self.client.raise_request_exception = False
        for endpoint in ENDPOINTS:
            for failure in INTERNAL_FAILURES:
                with self.subTest(endpoint=endpoint.url_name, failure=failure.__name__):
                    self.assert_internal_failure_hidden(
                        endpoint, failure(INTERNAL_TEXT)
                    )

    def assert_internal_failure_hidden(
        self, endpoint: Endpoint, failure: Exception
    ) -> None:
        """``endpoint`` failing with ``failure`` is logged and not shown."""
        with self.assertLogs("django.request", "ERROR") as request_logs:
            if endpoint.answers_json:
                with self.assertLogs("battle.views", "ERROR") as view_logs:
                    response = self.post(endpoint, failure)
                logged = view_logs.records[0]
            else:
                response = self.post(endpoint, failure)
                logged = request_logs.records[0]

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("4242", response.content.decode())
        self.assertIn(INTERNAL_TEXT, str(logged_exception(logged)))
        if endpoint.answers_json:
            self.assertEqual(
                response.json()["error"],
                common_refusal_words().decorators.request_failed,
            )

    def test_what_django_answers_itself_is_left_to_django(self):
        """What Django answers with a 4xx of its own -- a page not there, a
        permission refused, a request it cannot read -- is not the server
        failing (#113): it is answered as Django answers it, logged where
        Django logs it, and not logged here."""
        answered_by_django = (
            (Http404(), 404, "django.request"),
            (PermissionDenied(), 403, "django.request"),
            (BadRequest(), 400, "django.request"),
            (MultiPartParserError(), 400, "django.request"),
            # Django's security log, not the request log, takes this one.
            (SuspiciousOperation(), 400, "django.security"),
        )
        for endpoint in ENDPOINTS:
            for failure, status, django_logger in answered_by_django:
                with self.subTest(
                    endpoint=endpoint.url_name, failure=type(failure).__name__
                ):
                    with (
                        self.assertNoLogs("battle.views", "ERROR"),
                        self.assertLogs(django_logger),
                    ):
                        response = self.post(endpoint, failure)

                    self.assertEqual(response.status_code, status)

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=100)
    def test_an_oversized_choice_is_djangos_400(self):
        """Not stood in (#113): a body over Django's limit raises
        ``RequestDataTooBig`` as the act endpoint reads it, which Django
        answers with a 400 rather than the decorator with a logged 500."""
        battle, choice = self._paused_manual_battle()
        choice["spell_key"] = "x" * 200

        with (
            self.assertNoLogs("battle.views", "ERROR"),
            self.assertLogs("django.security", "ERROR"),
        ):
            response = self._post_choice(battle, choice)

        self.assertEqual(response.status_code, 400)

    def test_a_draft_gone_under_a_script_caller_is_a_404(self):
        """The draft-gone 404 (#832) is raised inside the logging decorator,
        so a script caller hears it too rather than a logged 500 (#113)."""
        # The muster's endpoints; the autosave and the start answer a draft
        # that went in their own ways (#465, #832).
        wearing_the_404 = {
            "setup-add",
            "setup-remove",
            "setup-import-party",
            "setup-team-add",
            "setup-team-remove",
            "setup-entry-team",
        }
        for endpoint in ENDPOINTS:
            if endpoint.url_name not in wearing_the_404:
                continue
            with self.subTest(endpoint=endpoint.url_name):
                with self.assertNoLogs("battle.views", "ERROR"):
                    response = self.post(
                        endpoint,
                        BattleDraft.DoesNotExist(),
                        headers=SCRIPT_CALLER_HEADERS,
                    )

                self.assertEqual(response.status_code, 404)

    def test_a_failing_poll_answers_json(self):
        """The poll is JSON-only too (#113): an error building the payload is
        the fixed sentence, with the text in the log."""
        with (
            mock.patch.object(
                views, "battle_payload", side_effect=KeyError(INTERNAL_TEXT)
            ),
            self.assertLogs("battle.views", "ERROR") as logs,
            self.assertLogs("django.request", "ERROR"),
        ):
            response = self.client.get(reverse("battle:state", args=[self.battle.pk]))

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json()["error"], common_refusal_words().decorators.request_failed
        )
        self.assertNotIn("4242", response.content.decode())
        self.assertIn(INTERNAL_TEXT, str(logged_exception(logs.records[0])))

    def _paused_manual_battle(self) -> tuple[Battle, dict]:
        """A manual battle paused on a choice, and the choice's body.

        Returns:
            The battle, awaiting, and the JSON body of its first option as
            ``battle.js`` would post it.
        """
        battle = create_battle(
            self.game_master,
            name="Whose move",
            count=3,
            seed=7,
            control_mode=control.MANUAL,
        )
        battle, _events = advance_turn(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        pending = battle.pending_input_json
        option = pending["candidates"][0]
        choice = {
            "combatant_id": pending["combatant_id"],
            "turn": pending["turn"],
            "letter": option["letter"],
            "target_id": option.get("target_id"),
            "spell_key": option.get("spell_key") or "",
        }
        return battle, choice

    def _post_choice(self, battle: Battle, choice: dict):
        """Post ``choice`` to ``battle``'s act endpoint, as ``battle.js`` does."""
        return self.client.post(
            reverse("battle:act", args=[battle.pk]),
            json.dumps(choice),
            content_type="application/json",
        )

    def _failing_choice(self, battle: Battle, choice: dict):
        """Post a choice the server fails on, capturing both error logs.

        Returns:
            The response and the exception ``battle.views`` logged.
        """
        with (
            self.assertLogs("battle.views", "ERROR") as logs,
            self.assertLogs("django.request", "ERROR"),
        ):
            response = self._post_choice(battle, choice)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json()["error"], common_refusal_words().decorators.request_failed
        )
        return response, logged_exception(logs.records[0])

    def test_an_engine_error_on_a_players_choice_stays_in_the_log(self):
        """The issue's own path, nothing stood in: battle_act records the
        choice, the resumed turn asks for the battle's rules profile, and
        ``profiles.for_battle`` raises its internal text."""
        battle, choice = self._paused_manual_battle()
        Battle.objects.filter(pk=battle.pk).update(rules_profile="no-such-rules")

        response, logged = self._failing_choice(battle, choice)

        self.assertNotIn("no-such-rules", response.content.decode())
        self.assertIn("unknown rules profile 'no-such-rules'", str(logged))

    def test_an_engine_failure_that_is_not_a_value_error_stays_in_the_log(self):
        """The issue's own path, nothing stood in (#113): battle_act records
        the choice, the resumed turn reads a stored figure with no Fatigue,
        and the engine's comparison raises a ``TypeError``."""
        battle, choice = self._paused_manual_battle()
        state = battle.state_json
        broken = [{**state["combatants"][0], "fatigue": None}, *state["combatants"][1:]]
        Battle.objects.filter(pk=battle.pk).update(
            state_json={**state, "combatants": broken}
        )

        response, logged = self._failing_choice(battle, choice)

        self.assertNotIn("NoneType", response.content.decode())
        self.assertIsInstance(logged, TypeError)
        self.assertNotIsInstance(logged, ValueError)

    def test_a_refusal_is_shown_in_its_own_words_and_not_logged(self):
        for endpoint in ENDPOINTS:
            with self.subTest(endpoint=endpoint.url_name):
                with self.assertNoLogs("battle.views", "ERROR"):
                    response = self.post(endpoint, BattleRefusal(REFUSAL_TEXT))

                self.assertIn(response.status_code, (400, 403))
                if endpoint.answers_json:
                    self.assertEqual(response.json()["error"], REFUSAL_TEXT)
                else:
                    self.assertIn(REFUSAL_TEXT, response.content.decode())

    def test_the_advance_shows_any_refusal_and_hides_the_rest(self):
        """The advance view (#13) catches the shared refusal type too."""
        with self.assertNoLogs("battle.views", "ERROR"):
            with mock.patch.object(
                views, "advance_turn", side_effect=BattleRefusal(REFUSAL_TEXT)
            ):
                response = self.client.post(
                    reverse("battle:advance", args=[self.battle.pk])
                )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], REFUSAL_TEXT)

    def test_a_real_refusal_still_reaches_the_player(self):
        """Not a stand-in: claim_seat's own refusal, through the real view."""
        lobby = create_battle(
            self.game_master, name="Lobby", count=1, seed=4, player_seats=2
        )
        self.assertEqual(lobby.status, Battle.STATUS_LOBBY)
        first, second = lobby.combatants.filter(is_open_seat=True)[:2]
        claim = reverse("battle:seat-claim", args=[lobby.pk, first.pk])
        self.client.post(claim, {"archetype": "brawler"})

        again = reverse("battle:seat-claim", args=[lobby.pk, second.pk])
        with self.assertNoLogs("battle.views", "ERROR"):
            response = self.client.post(again, {"archetype": "brawler"})

        self.assertContains(
            response, refusal_words().services.already_hold_a_seat, status_code=400
        )
